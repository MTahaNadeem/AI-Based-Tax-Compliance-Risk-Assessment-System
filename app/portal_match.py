"""
People's Portal — identity-claim matching.

Reuses the existing entity-resolution pipeline functions verbatim:
  normalize.normalize_name / normalize_address / normalize_phone
  entity_resolution.blocking_keys / pair_score / THRESHOLD / GRAY_LO

The matching result is one of three outcomes defined in the design doc §2.4:
  'match'     — exactly one candidate ≥ THRESHOLD (0.75)
  'ambiguous' — multiple candidates ≥ THRESHOLD, or any candidate in
                [GRAY_LO, THRESHOLD), or matching entity is an ER review pair
  'no_match'  — no candidate above GRAY_LO

IMPORTANT: The caller (portal_routes.py) must wrap every call to match_claim()
in portal_auth.timing_floor() to enforce an 800 ms minimum response time
across all three outcomes (enumeration mitigation).

The FBR CSV is read once at match startup (same process start as data store).
Phone numbers from the FBR CSV are used as a blocking/scoring signal but
are NEVER persisted anywhere in the portal database.
"""
import os
import sys
from dataclasses import dataclass
from typing import Literal, Optional

# Make pipeline importable when called from app/
HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.join(HERE, "..", "pipeline")
if PIPELINE not in sys.path:
    sys.path.insert(0, PIPELINE)

import pandas as pd
from normalize import normalize_name, normalize_address, normalize_phone, address_numbers
from entity_resolution import blocking_keys, pair_score, THRESHOLD, GRAY_LO, extract_city

# ------------------------------------------------------------------ types
MatchOutcome = Literal["match", "ambiguous", "no_match"]

@dataclass
class MatchResult:
    outcome: MatchOutcome
    entity_id: Optional[str] = None   # only set on 'match'
    candidates: Optional[list] = None  # [{entity_id, score}] for 'ambiguous'
    reason: Optional[str] = None       # 'multi_match' | 'ambiguous_score' | 'er_review'


# ------------------------------------------------------------------ record index
_records: list[dict] = []
_block_index: dict[str, list[int]] = {}
_er_review_entity_ids: set[str] = set()
_loaded = False


def _make_record(entity_id: str, raw_name: str, raw_addr: str, raw_phone: str,
                 name_freq: int = 1) -> dict:
    nname = normalize_name(raw_name)
    naddr = normalize_address(raw_addr)
    return dict(
        source="fbr",
        record_id=entity_id,
        entity_id=entity_id,
        raw_name=raw_name,
        raw_addr=raw_addr,
        nname=nname,
        naddr=naddr,
        anums=address_numbers(raw_addr),
        city=extract_city(normalize_address(raw_addr)),
        nphone=normalize_phone(raw_phone) if raw_phone else "",
        name_freq=name_freq,
    )


def _load_fbr_records() -> None:
    global _records, _block_index, _loaded
    if _loaded:
        return

    data_dir = os.path.join(HERE, "..", "data")
    fbr_path = os.path.join(data_dir, "fbr_tax_records.csv")
    if not os.path.exists(fbr_path):
        _loaded = True
        return

    df = pd.read_csv(fbr_path)

    # Name frequency across FBR register (mirrors entity_resolution.py logic)
    from collections import Counter
    freq = Counter(normalize_name(str(n)) for n in df["full_name"])

    recs = []
    for _, row in df.iterrows():
        raw_name = str(row.get("full_name", ""))
        raw_addr = str(row.get("reported_address", ""))
        raw_phone = str(row.get("phone_number", ""))
        # entity_id — we need to map fbr_id to resolved entity_id.
        # The api_data.json contains the resolved name per entity but not the
        # raw fbr_id.  We use raw_name+raw_addr for matching, and store a
        # synthetic record_id from the FBR row.  The actual entity_id mapping
        # is done by build_block_index using the api_data profiles.
        nname = normalize_name(raw_name)
        recs.append(dict(
            source="fbr",
            record_id=str(row["fbr_id"]),
            entity_id=None,   # filled by _apply_entity_mapping
            raw_name=raw_name,
            raw_addr=raw_addr,
            nname=nname,
            naddr=normalize_address(raw_addr),
            anums=address_numbers(raw_addr),
            city=extract_city(normalize_address(raw_addr)),
            nphone=normalize_phone(raw_phone),
            name_freq=freq.get(nname, 1),
        ))

    _records = recs
    _loaded = True


def _apply_entity_mapping(profiles: list[dict]) -> None:
    """
    Map FBR records to resolved entity IDs using canonical name matching.
    The pipeline's canonical_name is the longest raw_name in the cluster;
    we do a best-effort match here because we don't have the full cluster→fbr
    mapping at API serve time.

    Strategy: normalise both sides and accept the highest-score match.
    """
    global _block_index

    # Build normalized name → entity_id map from profiles
    from rapidfuzz import fuzz
    name_to_eid = {}
    for p in profiles:
        nname = normalize_name(p["name"])
        name_to_eid[nname] = p["entity_id"]

    for rec in _records:
        # Direct lookup first
        if rec["nname"] in name_to_eid:
            rec["entity_id"] = name_to_eid[rec["nname"]]
        else:
            # Fuzzy fallback — pick best match above 90 similarity
            best_eid, best_score = None, 0
            for nname, eid in name_to_eid.items():
                s = fuzz.ratio(rec["nname"], nname)
                if s > best_score:
                    best_score, best_eid = s, eid
            rec["entity_id"] = best_eid if best_score >= 90 else None

    # Build blocking index over resolved records only
    idx: dict[str, list[int]] = {}
    for i, rec in enumerate(_records):
        if rec["entity_id"] is None:
            continue
        for k in blocking_keys(rec):
            idx.setdefault(k, []).append(i)
    _block_index = idx


def init_matching(profiles: list[dict], er_review_entity_ids: set[str] | None = None) -> None:
    """
    Called once at server startup with the loaded api_data profiles.
    Loads FBR records and builds the block index.
    """
    global _er_review_entity_ids
    _load_fbr_records()
    _apply_entity_mapping(profiles)
    _er_review_entity_ids = er_review_entity_ids or set()


# ------------------------------------------------------------------ matching
def match_claim(
    claimed_name: str,
    claimed_address: str,
    claimed_phone: str,
) -> MatchResult:
    """
    Match a citizen's registration claim against the resolved entity index.

    Returns a MatchResult with outcome 'match', 'ambiguous', or 'no_match'.
    This function is deliberately synchronous so it can be awaited via
    asyncio.to_thread() in the route handler (it may take 50-200ms on CPU).
    """
    if not _records:
        return MatchResult(outcome="no_match")

    # Build a pseudo-record from the claim using the same normalization stack
    pseudo = dict(
        source="portal_claim",
        record_id="claim",
        entity_id=None,
        raw_name=claimed_name,
        raw_addr=claimed_address,
        nname=normalize_name(claimed_name),
        naddr=normalize_address(claimed_address),
        anums=address_numbers(claimed_address),
        city=extract_city(normalize_address(claimed_address)),
        nphone=normalize_phone(claimed_phone) if claimed_phone else "",
        name_freq=1,  # treat the claim as unique until proven otherwise
    )

    # Candidate retrieval via block index
    candidate_idxs: set[int] = set()
    for k in blocking_keys(pseudo):
        for i in _block_index.get(k, []):
            candidate_idxs.add(i)

    if not candidate_idxs:
        return MatchResult(outcome="no_match")

    # Score each candidate
    scored: list[tuple[float, dict]] = []
    for i in candidate_idxs:
        rec = _records[i]
        s = pair_score(pseudo, rec)
        if s > 0:
            scored.append((s, rec))

    scored.sort(key=lambda t: -t[0])

    # Partition into confident / gray / below
    confident = [(s, r) for s, r in scored if s >= THRESHOLD and r["entity_id"]]
    gray = [(s, r) for s, r in scored if GRAY_LO <= s < THRESHOLD and r["entity_id"]]

    # ---- Outcome 1: unique confident match
    if len(confident) == 1 and not gray:
        eid = confident[0][1]["entity_id"]
        # Check if the entity itself was flagged as ER-ambiguous
        if eid in _er_review_entity_ids:
            return MatchResult(
                outcome="ambiguous",
                candidates=[{"entity_id": eid, "score": round(confident[0][0], 3)}],
                reason="er_review",
            )
        return MatchResult(outcome="match", entity_id=eid)

    # ---- Outcome 2: ambiguous (multi-match or gray-zone)
    if len(confident) > 1:
        return MatchResult(
            outcome="ambiguous",
            candidates=[{"entity_id": r["entity_id"], "score": round(s, 3)}
                        for s, r in confident[:5]],
            reason="multi_match",
        )
    if gray:
        best_s, best_r = gray[0]
        return MatchResult(
            outcome="ambiguous",
            candidates=[{"entity_id": best_r["entity_id"], "score": round(best_s, 3)}],
            reason="ambiguous_score",
        )
    if confident:
        # Single confident but entity is in er_review set — already handled above
        eid = confident[0][1]["entity_id"]
        return MatchResult(outcome="match", entity_id=eid)

    # ---- Outcome 3: no match
    return MatchResult(outcome="no_match")
