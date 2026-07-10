"""
Entity Resolution pipeline.

Stage 1  BLOCKING      — candidate pairs only share a phonetic surname key,
                         a phone number, or address numerals+city. Turns the
                         O(n²) comparison space near-linear (the 30M-citizens
                         scalability answer).
Stage 2  SCORING       — weighted blend of name similarity (token-set ratio +
                         Jaro-Winkler), address similarity, numeral overlap,
                         and exact phone match. Initial-aware: "m. ahmed"
                         matches "muhammad ahmed".
Stage 3  CLUSTERING    — union-find over pairs above threshold -> unified
                         entity IDs across all four datasets.
"""
import itertools
from collections import defaultdict

import jellyfish
from rapidfuzz import fuzz

from normalize import (normalize_name, normalize_address, normalize_phone,
                       address_numbers, name_tokens)

THRESHOLD = 0.75   # tuned via precision/recall sweep on labelled data (see eval/)

class UnionFind:
    def __init__(self): self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x
    def union(self, a, b): self.p[self.find(a)] = self.find(b)

def initial_aware_name_sim(a: str, b: str) -> float:
    """Handle 'm. ahmed' vs 'muhammad ahmed': an initial matches any token
    starting with that letter; otherwise fuzzy token similarity."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb: return 0.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    used, hits = set(), 0
    for s in short:
        best, best_j = 0.0, None
        for j, l in enumerate(long_):
            if j in used: continue
            if len(s) == 1 or len(l) == 1:           # initial form
                sim = 1.0 if s[0] == l[0] else 0.0
            else:
                sim = max(jellyfish.jaro_winkler_similarity(s, l),
                          fuzz.ratio(s, l) / 100)
            if sim > best: best, best_j = sim, j
        if best >= 0.85 and best_j is not None:
            used.add(best_j); hits += best
    coverage = hits / len(short)
    surname_bonus = 0.0
    if short[-1] == long_[-1] and len(short[-1]) > 1:
        surname_bonus = 0.05
    return min(1.0, coverage + surname_bonus)

CITIES = {"islamabad","rawalpindi","lahore","karachi","faisalabad","multan","peshawar"}

def extract_city(naddr_full: str) -> str:
    for t in naddr_full.split():
        if t in CITIES: return t
    return ""

def pair_score(r1, r2) -> float:
    """Signal-aware scoring: weights renormalise over the signals both records
    actually carry. Records with no identity address (property buyers) can
    still link, but face a stricter name bar plus a city-consistency check."""
    name = initial_aware_name_sim(r1["nname"], r2["nname"])
    have_addr = bool(r1["naddr"] and r2["naddr"])
    have_phone = bool(r1["nphone"] and r2["nphone"])
    if have_addr:
        addr = fuzz.token_set_ratio(r1["naddr"], r2["naddr"]) / 100
        nums = 0.0
        if r1["anums"] and r2["anums"]:
            inter = len(set(r1["anums"]) & set(r2["anums"]))
            nums = inter / max(len(r1["anums"]), len(r2["anums"]))
        phone = 1.0 if (have_phone and r1["nphone"] == r2["nphone"]) else 0.0
        score = 0.46 * name + 0.27 * addr + 0.17 * nums + 0.10 * phone
        if name < 0.45:          # never merge clearly different names on address alone
            score *= 0.5
        return score
    # name-dominant path (e.g. property registry): only allowed when the name
    # is unambiguous in the population. "Muhammad Khan" appears many times —
    # merging those on name alone is exactly the false accusation a
    # legal-grade system must refuse; ambiguous cases route to human review.
    if r1["city"] and r2["city"] and r1["city"] != r2["city"]:
        return 0.0
    if max(r1["name_freq"], r2["name_freq"]) > 1:
        return 0.0
    t1, t2 = name_tokens(r1["nname"]), name_tokens(r2["nname"])
    full1 = sum(len(t) > 1 for t in t1) >= 2
    full2 = sum(len(t) > 1 for t in t2) >= 2
    if not (full1 and full2):
        return 0.0                      # initials are never enough by themselves
    return name if name >= 0.96 else 0.0

SOURCE_FILES = {"fbr": "fbr_tax_records.csv", "excise": "excise_vehicles.csv",
                "disco": "disco_consumption.csv", "property": "property_transfers.csv",
                "property_seller": "property_transfers.csv",
                "banking": "banking_accounts.csv", "travel": "travel_logs.csv"}

def load_records(dfs):
    """dfs: dict of source -> dataframe (core 4 always; banking/travel when the
    plug-in CSVs are present). Sellers in the property registry are loaded as
    their own records (role=seller) so transfer chains become resolvable.
    Every record carries source_file + 1-based data row_number (C4 traceability)."""
    spec = {"fbr":      ("fbr_id", "full_name", "reported_address", "phone_number"),
            "excise":   ("vehicle_reg_no", "owner_name", "owner_address", None),
            "disco":    ("meter_ref_no", "consumer_name", "installation_address", None),
            "property": ("registry_no", "buyer_name", "property_address", None),
            "banking":  ("account_no", "holder_name", "holder_address", None),
            "travel":   ("trip_id", "passenger_name", "passenger_address", None)}
    records = []
    for src, (idc, namec, addrc, phonec) in spec.items():
        if src not in dfs:                      # plug-in absent: skip silently
            continue
        df = dfs[src]
        for ridx, row in df.iterrows():
            raw_name, raw_addr = str(row[namec]), str(row[addrc])
            # property_address is the *asset's* address, weaker identity signal
            addr_for_identity = "" if src == "property" else raw_addr
            records.append(dict(
                source=src, record_id=str(row[idc]), role="primary",
                source_file=SOURCE_FILES[src], row_number=int(ridx) + 1,
                raw_name=raw_name, raw_addr=raw_addr,
                nname=normalize_name(raw_name),
                naddr=normalize_address(addr_for_identity),
                anums=address_numbers(addr_for_identity),
                city=extract_city(normalize_address(raw_addr)),
                nphone=normalize_phone(row[phonec]) if phonec else "",
                row=row.to_dict()))
            if src == "property":               # C1: seller as resolvable record
                records.append(dict(
                    source="property_seller", record_id=str(row[idc]), role="seller",
                    source_file=SOURCE_FILES[src], row_number=int(ridx) + 1,
                    raw_name=str(row["seller_name"]), raw_addr=raw_addr,
                    nname=normalize_name(str(row["seller_name"])),
                    naddr="", anums=[],
                    city=extract_city(normalize_address(raw_addr)),
                    nphone="", row=row.to_dict()))
    return records

def blocking_keys(rec):
    """Composite keys (surname phonetic + first initial) keep blocks small
    even for ultra-common surnames (Khan, Malik), so no block is ever
    skipped — the property that makes blocking scale."""
    keys = []
    toks = name_tokens(rec["nname"])
    if toks:
        last_sx = jellyfish.soundex(toks[-1])
        last_mp = jellyfish.metaphone(toks[-1]) or last_sx       # C7: 2nd phonetic family
        if len(toks) > 1:
            keys.append(f"nm:{last_sx}:{toks[0][0]}")            # surname + 1st initial
            keys.append(f"mp:{last_mp}:{toks[0][0]}")
            keys.append(f"fn:{jellyfish.soundex(toks[0])}:{toks[-1][0]}")
        else:
            keys.append(f"sx:{last_sx}")
            keys.append(f"mq:{last_mp}")
    if rec["nphone"]:
        keys.append("ph:" + rec["nphone"])
    if rec["anums"]:
        keys.append("ad:" + "-".join(rec["anums"][:2]) + ":" + rec["city"])
    return keys

GRAY_LO = 0.65     # lower bound of the borderline band sent to adjudication
GRAY_CAP = 2000    # adjudicate at most this many, highest-score first

def _review_item(a, b, score, reason):
    return dict(score=score, reason=reason,
                a=dict(source=a["source"], record_id=a["record_id"], name=a["raw_name"],
                       address=a["raw_addr"], file=a["source_file"], row=a["row_number"]),
                b=dict(source=b["source"], record_id=b["record_id"], name=b["raw_name"],
                       address=b["raw_addr"], file=b["source_file"], row=b["row_number"]))

def resolve(dfs, adjudicator=None):
    records = load_records(dfs)
    # population frequency of each normalised full name, estimated from the
    # FBR register (closest to one-record-per-person). Drives the
    # ambiguity-awareness rule in name-only matching.
    freq = defaultdict(int)
    for r in records:
        if r["source"] == "fbr":
            freq[r["nname"]] += 1
    for r in records:
        r["name_freq"] = freq.get(r["nname"], 1)

    blocks = defaultdict(list)
    for i, r in enumerate(records):
        for k in blocking_keys(r):
            blocks[k].append(i)

    seen, uf, n_comp = set(), UnionFind(), 0
    gray = []                                   # C16: borderline pairs for LLM/human review
    for key, idxs in blocks.items():
        if len(idxs) > 250:       # safety valve only; composite keys keep blocks small
            continue
        for i, j in itertools.combinations(idxs, 2):
            if (i, j) in seen: continue
            seen.add((i, j)); n_comp += 1
            s = pair_score(records[i], records[j])
            if s >= THRESHOLD:
                uf.union(i, j)
            elif GRAY_LO <= s < THRESHOLD:
                gray.append((i, j, round(s, 3)))

    gray.sort(key=lambda t: -t[2])
    gray = gray[:GRAY_CAP]
    n_llm_links, review_pairs = 0, []
    if adjudicator is not None and gray:
        for i, j, s in gray:
            verdict, reason = adjudicator(records[i], records[j])
            if verdict == "match":
                uf.union(i, j); n_llm_links += 1
                records[i].setdefault("provenance", []).append(
                    dict(linked_to=records[j]["record_id"], method="llm_adjudicated",
                         score=s, reason=reason))
            elif verdict == "uncertain":
                review_pairs.append(_review_item(records[i], records[j], s, reason))
    else:
        review_pairs = [_review_item(records[i], records[j], s, "below threshold; "
                        "no adjudicator configured") for i, j, s in gray[:200]]

    clusters = defaultdict(list)
    for i in range(len(records)):
        clusters[uf.find(i)].append(i)

    entities = []
    for eid, (root, members) in enumerate(sorted(clusters.items())):
        recs = [records[m] for m in members]
        canon = max((r["raw_name"] for r in recs), key=len)
        entities.append(dict(entity_id=f"E{eid:04d}", canonical_name=canon, records=recs))
    total_pairs = len(records) * (len(records) - 1) // 2
    stats = dict(n_records=len(records), n_entities=len(entities),
                 comparisons=n_comp, comparisons_naive=total_pairs,
                 reduction=f"{100 * (1 - n_comp / total_pairs):.2f}%",
                 gray_zone_pairs=len(gray), llm_adjudicated_links=n_llm_links,
                 review_queue=len(review_pairs))
    return entities, stats, review_pairs
