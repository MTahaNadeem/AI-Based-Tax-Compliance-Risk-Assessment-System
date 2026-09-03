"""
People's Portal — read-only data accessor.

Loads outputs/api_data.json once at startup and serves per-citizen slices.
The full file is NEVER sent in an HTTP response.

Auditor-only fields stripped from every citizen-facing profile:
  score, components, weights, household_id, household_members,
  household_declared, entity_id, match_provenance, graph,
  audit (raw auditor narrative), audit_urdu, defensibility,
  comp_names.

Tier labels are rewritten to citizen-safe language (§5.2 of design doc).

Plugin fields (n_accounts, annual_deposits, n_intl_trips, travel_spend)
are included automatically when present (defaulting to 0 otherwise) so
no special-casing is needed for plugin vs non-plugin pipeline runs.
"""
import json
import os
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "..", "outputs", "api_data.json")

# §5.2 — tier rewrite map: auditor label → citizen-facing label
TIER_CITIZEN = {
    "CRITICAL": "Your record has been referred for review",
    "HIGH":     "Some information on your record has been flagged for review",
    "MEDIUM":   "Your record contains items that may need clarification",
    "LOW":      "Your record is broadly consistent; minor discrepancies noted",
    "MINIMAL":  "Your record appears consistent with our data",
}

# Auditor-only fields that must NEVER appear in a citizen API response.
_STRIP = frozenset({
    "score", "components", "weights", "comp_names",
    "household_id", "household_members", "household_declared",
    "entity_id",            # internal cluster id
    "match_provenance",     # inter-entity linking evidence
    "graph",                # subgraph nodes/edges (may contain other people)
    "audit",                # accusatory auditor narrative
    "audit_urdu",
    "defensibility",
})

# Evidence fields that are citizen-safe to show
_EVIDENCE_KEEP = ("source", "record_id", "finding", "source_file", "row_number")


def _rewrite_narrative(profile: dict) -> str:
    """
    Convert the auditor narrative into a plain, non-accusatory citizen summary.
    §5.3 — 3-sentence max, no score numbers, no sub-score detail.
    """
    filer = profile.get("filer", "Unknown")
    declared = profile.get("declared_income", 0)
    lifestyle = profile.get("lifestyle_income", 0)

    sentences = []

    if filer == "Non-Filer":
        sentences.append(
            "Based on our records, you are currently registered as a non-filer with FBR."
        )
    elif filer == "Unknown":
        sentences.append(
            "We could not locate an active FBR tax filing linked to your name."
        )
    else:
        if declared > 0:
            sentences.append(
                "Your tax filing details have been located in the FBR records."
            )
        else:
            sentences.append(
                "You are recorded as a filer, but no declared income was found for the current period."
            )

    if lifestyle > 0 and lifestyle > declared * 1.2:
        sentences.append(
            "Based on government records, the lifestyle expenses associated with your "
            "name — including vehicles, electricity bills, and property — appear to be "
            "higher than the income you have declared."
        )
    elif lifestyle > 0:
        sentences.append(
            "Your lifestyle indicators appear broadly consistent with your declared income."
        )

    sentences.append(
        "If any of the records below are incorrect or do not belong to you, "
        "please use the dispute option to notify FBR."
    )

    return " ".join(sentences)


def _citizen_evidence(evidence: list) -> list:
    """Strip auditor-internal fields from evidence items."""
    return [
        {k: e[k] for k in _EVIDENCE_KEEP if k in e}
        for e in evidence
    ]


class PortalDataStore:
    """Thread-safe in-memory store of the pipeline output."""

    def __init__(self):
        self._by_entity: dict[str, dict] = {}
        self._review_entity_ids: set[str] = set()
        self._loaded = False

    def load(self) -> None:
        if not os.path.exists(DATA_PATH):
            return
        with open(DATA_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        idx: dict[str, dict] = {}
        for p in raw.get("profiles", []):
            idx[p["entity_id"]] = p
        # Index entity IDs that appeared in review_pairs (ER-ambiguous identities)
        rp_ids: set[str] = set()
        for pair in raw.get("review_pairs", []):
            # review_pairs items have a/b sub-dicts with record_ids, but the
            # entity_id is not directly present; we track this via the
            # resolved cluster — not available here, so we skip this part.
            pass
        self._by_entity = idx
        self._review_entity_ids = rp_ids
        self._loaded = True

    def is_loaded(self) -> bool:
        return self._loaded

    def all_records_for_matching(self) -> list[dict]:
        """
        Return lightweight records (source, raw_name, raw_addr, nphone, entity_id)
        from every profile for registration matching.  Only FBR-sourced records
        carry the phone signal.
        """
        out = []
        for eid, p in self._by_entity.items():
            out.append({
                "entity_id": eid,
                "name": p.get("name", ""),
                "filer": p.get("filer", "Unknown"),
            })
        return out

    def get_entity_id_for_profile(self, entity_id: str) -> Optional[dict]:
        return self._by_entity.get(entity_id)

    def get_citizen_profile(self, entity_id: str) -> Optional[dict]:
        """
        Return a citizen-safe copy of the profile.
        Strips all auditor-only fields (§5.2).
        Rewrites tier label and narrative.
        Never includes internal entity_id or cluster data.
        """
        raw = self._by_entity.get(entity_id)
        if raw is None:
            return None

        # Build citizen-safe profile
        tier = raw.get("tier", "MINIMAL")
        profile = {
            # Identity fields safe to show
            "name":           raw.get("name"),
            "filer":          raw.get("filer"),
            "declared_income": raw.get("declared_income", 0),
            "lifestyle_income": raw.get("lifestyle_income", 0),
            # Asset counts
            "n_vehicles":     raw.get("n_vehicles", 0),
            "n_properties":   raw.get("n_properties", 0),
            "avg_bill":       raw.get("avg_bill", 0),
            "n_sources":      raw.get("n_sources", 0),
            # Plugin fields (0 when plugin not active)
            "n_accounts":     raw.get("n_accounts", 0),
            "annual_deposits": raw.get("annual_deposits", 0),
            "n_intl_trips":   raw.get("n_intl_trips", 0),
            "travel_spend":   raw.get("travel_spend", 0),
            # Citizen-rewritten tier and narrative
            "tier_label":     TIER_CITIZEN.get(tier, TIER_CITIZEN["MINIMAL"]),
            "summary":        _rewrite_narrative(raw),
            # Timeline (dates of asset events — no other-person data)
            "timeline":       raw.get("timeline", []),
            # Evidence items (safe subset of fields)
            "evidence":       _citizen_evidence(raw.get("evidence", [])),
        }
        return profile


# Module-level singleton — loaded once on server startup
_store: Optional[PortalDataStore] = None


def get_store() -> PortalDataStore:
    global _store
    if _store is None:
        _store = PortalDataStore()
        _store.load()
    return _store
