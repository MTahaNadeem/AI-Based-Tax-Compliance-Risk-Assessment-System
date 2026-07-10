"""
Knowledge Graph engine (NetworkX).

Nodes: Person (resolved entity), Vehicle, Property, Meter, TaxReturn
Edges: owns, registered_at, consumes_via, filed
The graph is the single source of truth for scoring, GNN features and the
dashboard's per-person subgraph view.
"""
import networkx as nx

# crude market value proxy per engine cc band (PKR) — generic, not per-person
CC_VALUE = [(800, 2_500_000), (1300, 5_500_000), (1600, 8_500_000),
            (2000, 15_000_000), (2700, 35_000_000), (10_000, 80_000_000)]

def vehicle_value(cc: int) -> int:
    for band, val in CC_VALUE:
        if cc <= band: return val
    return CC_VALUE[-1][1]

def build_graph(entities):
    G = nx.MultiDiGraph()
    for ent in entities:
        pid = ent["entity_id"]
        G.add_node(pid, kind="person", label=ent["canonical_name"])
        agg = dict(declared_income=0, tax_paid=0, filer=None, n_returns=0,
                   vehicles=[], properties=[], bills=[], sources=set(),
                   deposits=0, n_accounts=0, trips=[], n_sold=0,
                   home_naddr="", surname="")
        for rec in ent["records"]:
            row, src = rec["row"], rec["source"]
            if src != "property_seller":
                agg["sources"].add(src)
            if rec.get("naddr") and not agg["home_naddr"]:
                agg["home_naddr"] = rec["naddr"]          # C2: household key material
            toks = rec["nname"].split()
            if toks and not agg["surname"]:
                agg["surname"] = toks[-1]
            if src == "fbr":
                rid = f"TR:{rec['record_id']}"
                G.add_node(rid, kind="tax_return", label=rec["record_id"],
                           declared=int(row["declared_income_pkr"]),
                           paid=int(row["tax_paid_pkr"]), status=row["filer_status"])
                G.add_edge(pid, rid, rel="filed")
                agg["declared_income"] += int(row["declared_income_pkr"])
                agg["tax_paid"] += int(row["tax_paid_pkr"])
                agg["filer"] = row["filer_status"]
                agg["n_returns"] += 1
            elif src == "excise":
                rid = f"V:{rec['record_id']}"
                cc = int(row["engine_capacity_cc"])
                G.add_node(rid, kind="vehicle", label=row["vehicle_make_model"],
                           cc=cc, year=int(row["registration_year"]),
                           est_value=vehicle_value(cc))
                G.add_edge(pid, rid, rel="owns")
                agg["vehicles"].append((row["vehicle_make_model"], cc, vehicle_value(cc)))
            elif src == "disco":
                rid = f"M:{rec['record_id']}"
                bill = int(row["avg_monthly_bill_pkr"])
                G.add_node(rid, kind="meter", label=rec["record_id"],
                           bill=bill, ctype=row["connection_type"])
                G.add_edge(pid, rid, rel="consumes_via")
                agg["bills"].append(bill)
            elif src == "property":
                rid = f"P:{rec['record_id']}"
                val = int(row["property_value_pkr"])
                if rid not in G:
                    G.add_node(rid, kind="property", label=row["property_address"],
                               value=val, marla=row["area_marla"],
                               ptype=row["property_type"], date=row["transfer_date"])
                G.add_edge(pid, rid, rel="purchased_by")     # C1
                agg["properties"].append((row["property_address"], val))
            elif src == "property_seller":                   # C1: seller side
                rid = f"P:{rec['record_id']}"
                val = int(row["property_value_pkr"])
                if rid not in G:
                    G.add_node(rid, kind="property", label=row["property_address"],
                               value=val, marla=row["area_marla"],
                               ptype=row["property_type"], date=row["transfer_date"])
                G.add_edge(pid, rid, rel="sold_by")
                agg["n_sold"] += 1
            elif src == "banking":                           # C11
                rid = f"B:{rec['record_id']}"
                dep = int(row["avg_monthly_deposit_pkr"])
                G.add_node(rid, kind="account", label=f"{row['bank_name']} {row['account_type']}",
                           deposit=dep, bank=row["bank_name"], opened=row["opened_date"])
                G.add_edge(pid, rid, rel="holds_account")
                agg["deposits"] += dep; agg["n_accounts"] += 1
            elif src == "travel":                            # C12
                rid = f"T:{rec['record_id']}"
                cost = int(row["est_trip_cost_pkr"])
                G.add_node(rid, kind="trip", label=f"{row['destination']} ({row['trip_class']})",
                           cost=cost, tclass=row["trip_class"], date=row["departure_date"],
                           dest=row["destination"])
                G.add_edge(pid, rid, rel="travelled")
                agg["trips"].append((row["destination"], cost, row["trip_class"]))
        G.nodes[pid].update(
            declared_income=agg["declared_income"], tax_paid=agg["tax_paid"],
            filer=agg["filer"] or "Unknown",
            total_vehicle_value=sum(v[2] for v in agg["vehicles"]),
            max_cc=max((v[1] for v in agg["vehicles"]), default=0),
            total_property_value=sum(p[1] for p in agg["properties"]),
            avg_bill=sum(agg["bills"]) // len(agg["bills"]) if agg["bills"] else 0,
            n_vehicles=len(agg["vehicles"]), n_properties=len(agg["properties"]),
            n_sources=len(agg["sources"]),
            annual_deposits=agg["deposits"] * 12, n_accounts=agg["n_accounts"],
            travel_spend=sum(t[1] for t in agg["trips"]),
            n_intl_trips=sum(1 for t in agg["trips"]
                             if t[0] not in ("Karachi", "Lahore", "Skardu")),
            n_sold=agg["n_sold"], home_naddr=agg["home_naddr"], surname=agg["surname"])
    assign_households(G)
    return G

def assign_households(G):
    """C2: infer households — same normalised residential address + shared
    surname token => one household. Adds household_id, household_declared,
    household_lifestyle inputs to every person node."""
    from collections import defaultdict
    groups = defaultdict(list)
    for n, d in G.nodes(data=True):
        if d["kind"] != "person":
            continue
        key = (d.get("home_naddr") or f"__solo_{n}", d.get("surname") or n)
        groups[key].append(n)
    hid = 0
    for key, members in groups.items():
        h = f"H{hid:04d}"; hid += 1
        decl = sum(G.nodes[m]["declared_income"] for m in members)
        for m in members:
            G.nodes[m]["household_id"] = h
            G.nodes[m]["household_members"] = len(members)
            G.nodes[m]["household_declared"] = decl
            G.nodes[m]["household_names"] = [G.nodes[x]["label"] for x in members]

def find_circular_transfers(G, max_hops=4):
    """C1: detect property transfer chains that return to an earlier entity or
    its household within max_hops. Returns {person_node: [chain_descr, ...]}."""
    flags = {}
    # build seller->buyer person edges per property
    prop_edges = []   # (seller, buyer, prop)
    for prop, d in G.nodes(data=True):
        if d["kind"] != "property":
            continue
        sellers = [u for u, _, e in G.in_edges(prop, data=True) if e["rel"] == "sold_by"]
        buyers = [u for u, _, e in G.in_edges(prop, data=True) if e["rel"] == "purchased_by"]
        for s in sellers:
            for b in buyers:
                if s != b:
                    prop_edges.append((s, b, prop))
    import networkx as nx
    H = nx.DiGraph()
    for s, b, p in prop_edges:
        H.add_edge(s, b, prop=p)
    for cyc in nx.simple_cycles(H):
        if len(cyc) <= max_hops:
            descr = " → ".join(G.nodes[n]["label"] for n in cyc) + " → (back)"
            for n in cyc:
                flags.setdefault(n, []).append(descr)
    # same-household transfers (benami signal): seller & buyer share household
    for s, b, p in prop_edges:
        hs, hb = G.nodes[s].get("household_id"), G.nodes[b].get("household_id")
        if hs and hs == hb:
            descr = (f"intra-household transfer of {G.nodes[p]['label']} "
                     f"({G.nodes[s]['label']} → {G.nodes[b]['label']})")
            flags.setdefault(b, []).append(descr)
            flags.setdefault(s, []).append(descr)
    return flags
