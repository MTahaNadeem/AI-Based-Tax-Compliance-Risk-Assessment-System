"""
End-to-end pipeline: CSVs -> ER (+LLM adjudication) -> Knowledge Graph
(+households, transfer chains) -> GraphSAGE -> A-F sub-scores -> audit trails
(+LLM EN/UR narration) -> outputs/api_data.json.

Auto-detects plug-in datasets (banking_accounts.csv, travel_logs.csv) in data/.
Run:  python pipeline/run_pipeline.py
"""
import json, os, sys, time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "eval"))
DATA = os.path.join(HERE, "..", "data")
OUT = os.path.join(HERE, "..", "outputs")

import llm
from entity_resolution import resolve
from build_graph import build_graph, find_circular_transfers
from gnn_anomaly import gnn_anomaly_scores
from scoring import (component_scores, audit_narrative, tier_of,
                     COMP_NAMES, DEFENSIBILITY)
from evaluate_er import pairwise_metrics

CORE = {"fbr": "fbr_tax_records.csv", "excise": "excise_vehicles.csv",
        "disco": "disco_consumption.csv", "property": "property_transfers.csv"}
PLUGINS = {"banking": "banking_accounts.csv", "travel": "travel_logs.csv"}

def main():
    timings, t0 = {}, time.time()

    dfs = {k: pd.read_csv(os.path.join(DATA, f)) for k, f in CORE.items()}
    plugins_present = []
    for k, f in PLUGINS.items():
        p = os.path.join(DATA, f)
        if os.path.exists(p):
            dfs[k] = pd.read_csv(p)
            plugins_present.append(k)
    print(f"[0/6] Datasets: {', '.join(CORE)}"
          + (f" + plug-ins: {', '.join(plugins_present)}" if plugins_present else
             "  (no plug-ins detected)"))

    print("[1/6] Entity resolution"
          + (" + LLM adjudication" if llm.available() else
             " (no LLM key: gray zone -> human-review queue)") + " ...")
    t = time.time()
    adjudicator = llm.adjudicate_pair if llm.available() else None
    entities, er_stats, review_pairs = resolve(dfs, adjudicator=adjudicator)
    timings["entity_resolution"] = round(time.time() - t, 2)
    print(f"      {er_stats['n_records']} records -> {er_stats['n_entities']} entities | "
          f"{er_stats['comparisons']:,} vs naive {er_stats['comparisons_naive']:,} "
          f"({er_stats['reduction']} pruned) | gray-zone {er_stats['gray_zone_pairs']} "
          f"| LLM links {er_stats['llm_adjudicated_links']} | review {er_stats['review_queue']}")

    metrics = None
    gt = os.path.join(DATA, "ground_truth.csv")
    if os.path.exists(gt):
        t = time.time()
        metrics = pairwise_metrics(entities, pd.read_csv(gt))
        timings["er_evaluation"] = round(time.time() - t, 2)
        print(f"[2/6] ER eval: P={metrics['precision']} R={metrics['recall']} F1={metrics['f1']}")
    else:
        print("[2/6] No ground truth — ER eval skipped (attach a labelled sample to enable).")

    print("[3/6] Knowledge graph + households + transfer chains ...")
    t = time.time()
    G = build_graph(entities)
    circular = find_circular_transfers(G)
    timings["graph_build"] = round(time.time() - t, 2)
    n_households = len({d.get("household_id") for _, d in G.nodes(data=True)
                        if d.get("kind") == "person"})
    print(f"      {G.number_of_nodes()} nodes / {G.number_of_edges()} edges | "
          f"{n_households} households | {len(circular)} entities in transfer patterns")

    print("[4/6] GraphSAGE autoencoder (PyTorch Geometric) ...")
    t = time.time()
    gnn_pct, loss = gnn_anomaly_scores(G)
    timings["gnn_training"] = round(time.time() - t, 2)
    print(f"      reconstruction loss {loss:.4f} | {timings['gnn_training']}s")

    print("[5/6] Sub-score decomposition (A-F) + audit trails"
          + (" + LLM EN/UR narration" if llm.available() else "") + " ...")
    t = time.time()
    ent_by_id = {e["entity_id"]: e for e in entities}
    profiles = []
    for pid, pct in gnn_pct.items():
        ent = ent_by_id[pid]
        final, comps, weights, evidence, life = component_scores(
            G, pid, ent, pct, circular, plugins_present)
        d = G.nodes[pid]
        tier_en, tier_ur = tier_of(final)
        narrative = audit_narrative(G, pid, final, comps, weights, life, ent)
        urdu = None
        if llm.available() and final >= 61:        # narrate HIGH/CRITICAL tiers
            n = llm.narrate(dict(name=d["label"], tier=tier_en, final=final,
                                 components=comps, weights=weights,
                                 evidence=[e["finding"] for e in evidence][:14]))
            if n:
                narrative, urdu = n["english"], n["urdu"]
        # timeline events (C6)
        events = []
        for nbr in G.successors(pid):
            nd = G.nodes[nbr]
            if nd["kind"] == "vehicle":
                events.append(dict(year=str(nd["year"]), kind="vehicle", label=nd["label"]))
            elif nd["kind"] == "property" and nd.get("date"):
                rel = [e["rel"] for _, _, e in G.out_edges(pid, data=True) if _ == nbr]
                events.append(dict(year=nd["date"][:4], kind="property",
                                   label=("sold " if "sold_by" in rel else "bought ")
                                         + str(nd["label"])[:34]))
            elif nd["kind"] == "account":
                events.append(dict(year=nd["opened"][:4], kind="account", label=nd["label"]))
            elif nd["kind"] == "trip":
                events.append(dict(year=nd["date"][:4], kind="trip", label=nd["label"]))
        events.sort(key=lambda e: e["year"])
        # subgraph for dashboard
        sub_nodes = [dict(id=pid, kind="person", label=d["label"])]
        sub_edges = []
        for _, nbr, edata in G.out_edges(pid, data=True):
            nd = G.nodes[nbr]
            sub_nodes.append(dict(id=nbr, kind=nd["kind"], label=str(nd.get("label", nbr))))
            sub_edges.append(dict(source=pid, target=nbr, rel=edata["rel"]))
        prov = [p for r in ent["records"] for p in r.get("provenance", [])]
        profiles.append(dict(
            entity_id=pid, name=d["label"], score=final, tier=tier_en, tier_ur=tier_ur,
            components=comps, weights=weights,
            comp_names={k: COMP_NAMES[k] for k in comps},
            filer=d["filer"], declared_income=d["declared_income"],
            lifestyle_income=life["total"],
            household_id=d.get("household_id"), household_members=d.get("household_members", 1),
            household_declared=d.get("household_declared", d["declared_income"]),
            n_vehicles=d["n_vehicles"], n_properties=d["n_properties"],
            avg_bill=d["avg_bill"], n_sources=d["n_sources"],
            n_accounts=d.get("n_accounts", 0), annual_deposits=d.get("annual_deposits", 0),
            n_intl_trips=d.get("n_intl_trips", 0), travel_spend=d.get("travel_spend", 0),
            audit=narrative, audit_urdu=urdu, defensibility=DEFENSIBILITY,
            evidence=evidence, timeline=events, match_provenance=prov,
            graph=dict(nodes=sub_nodes, links=sub_edges)))
    profiles.sort(key=lambda p: -p["score"])
    timings["scoring_audit"] = round(time.time() - t, 2)

    tiers = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "MINIMAL": 0}
    for p in profiles:
        tiers[p["tier"]] += 1

    print("[6/6] Writing outputs ...")
    os.makedirs(OUT, exist_ok=True)
    timings["total"] = round(time.time() - t0, 2)
    payload = dict(
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        pipeline_seconds=timings["total"], timings=timings,
        plugins=plugins_present, llm_active=llm.available(),
        er_stats=er_stats, er_metrics=metrics, tiers=tiers,
        n_households=n_households,
        graph_stats=dict(nodes=G.number_of_nodes(), edges=G.number_of_edges()),
        review_pairs=review_pairs[:100],
        profiles=profiles)
    with open(os.path.join(OUT, "api_data.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"\nDone in {timings['total']}s ({len(profiles)} profiles) | stage timings: "
          + ", ".join(f"{k} {v}s" for k, v in timings.items() if k != "total"))

if __name__ == "__main__":
    main()
