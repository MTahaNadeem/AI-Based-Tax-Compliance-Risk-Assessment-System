# TaxNet Graph
### Graph AI for Broadening the National Tax Net

Links fragmented Pakistani civic datasets (FBR, Excise, DISCO, Property Registry) with **no shared CNIC**, builds a knowledge graph of every citizen's financial footprint, scores tax-compliance deviation 0–100 with a **GraphSAGE GNN + interpretable lifestyle-gap model**, and explains every flag with a legal-grade audit trail in an auditor dashboard.

---

## Quick start

```bash
pip install -r requirements.txt

# 1. Generate synthetic data (skip once the official CSVs arrive — see below)
python pipeline/generate_data.py

# 2. Run the full pipeline  (ER -> graph -> GNN -> scoring -> audit trails)
python pipeline/run_pipeline.py

# 3. Launch the dashboard
uvicorn app.main:app --port 8005
# open http://localhost:8000
```

Runs fully **offline** — d3 and all fonts are vendored. No CDN, no venue-WiFi risk.

### Swapping in the official dataset
Drop the four official CSVs into `data/` with the announced names
(`fbr_tax_records.csv`, `excise_vehicles.csv`, `disco_consumption.csv`,
`property_transfers.csv`) and re-run step 2. If a `ground_truth.csv`
(or a hand-labelled sample) is present, ER precision/recall is computed
automatically; otherwise that stage is skipped gracefully.

---

## Architecture

```
4 CSVs ──► Normalisation ──► Blocking ──► Pair scoring ──► Union-Find ──► Entities
            (Urdu→Roman        (composite    (name/addr/      clustering
             transliteration,   phonetic     phone signals,
             honorific strip)   keys)        ambiguity-aware)
                                                  │
Entities ──► NetworkX Knowledge Graph (Person·Vehicle·Property·Meter·TaxReturn)
                                                  │
              ┌───────────────────────────────────┴────────────────────┐
              ▼                                                        ▼
   Interpretable lifestyle-gap model (70%)            GraphSAGE autoencoder (30%)
   bill/vehicle/property-implied income                reconstruction-error
   vs declared income                                  anomaly percentile (PyG)
              └───────────────────┬────────────────────────────────────┘
                                  ▼
            Deviation Score 0–100  +  decomposable audit trail (XAI)
                                  ▼
                FastAPI  ──►  Auditor dashboard (risk register, case files,
                              evidence tables, per-citizen footprint graph)
```

### Design decisions that map to the rubric

**Technical Rigor & ML** — Initial-aware multilingual name matching (handles
"M. Ahmed" ↔ "Muhammad Ahmed" ↔ "چوہدری محمد احمد"), generic Urdu→Roman
transliteration (no per-person lookup tables), and a custom GraphSAGE
autoencoder trained unsupervised on the citizen graph.

**Data Engineering & Scalability** — Composite phonetic blocking prunes
**99.1%** of the naive O(n²) comparison space while remaining safe for
ultra-common surnames (Khan, Malik). The 30M-citizen answer: blocking keeps
candidate generation near-linear; embeddings can move to an ANN index
(FAISS), the graph to Neo4j with sharding, and the GNN trains on
neighbourhood samples by construction (GraphSAGE was designed for
billion-edge graphs).

**Explainability & XAI** — The score is 70% a fully decomposable
lifestyle-gap signal; the GNN contributes 30% and is reported separately,
never hidden. Every flag carries the exact source records (department,
record ID, finding) plus a plain-language narrative. **Ambiguity awareness:**
the matcher refuses to merge people on a common name alone — those cases
route to human review instead of false accusation, which is what
"legal-grade" actually requires.

**Validation & Reliability** — `eval/evaluate_er.py` computes pairwise
precision / recall / F1 against ground truth. The match threshold (0.75) was
selected by sweep; current synthetic-data results: **P 86.8% · R 73.3% · F1 0.795** core mode; **P 90.0% · R 85.3% · F1 0.876** with plug-ins (more corroborating records tighten the net). Recall is deliberately traded for precision: in a system that
accuses citizens, false positives are the costly error.

---

## Repo layout
```
pipeline/   generate_data.py · normalize.py · entity_resolution.py
            build_graph.py · gnn_anomaly.py · scoring.py · run_pipeline.py
eval/       evaluate_er.py            (pairwise P/R/F1 harness)
app/        main.py (FastAPI) · static/index.html (dashboard) · static/vendor/
data/       four CSVs + ground_truth.csv
outputs/    api_data.json             (pipeline output consumed by the API)
```

## 5-minute pitch skeleton
1. **Hook (30s)** — "Declared income: zero. Owns a Land Cruiser, pays Rs 2.8 lakh/month for electricity. Our graph found him in 2.2 seconds." *(open the live dossier)*
2. **Problem (45s)** — tax-to-GDP under 10%, 10M+ potential non-filers, four databases that never talk.
3. **How (90s)** — ER pipeline → knowledge graph → dual-signal scoring; show the blocking-reduction and P/R numbers on the masthead.
4. **Why trust it (60s)** — click the audit trail; emphasise ambiguity-awareness ("we refuse to merge two Muhammad Khans on a name").
5. **Scale (45s)** — the 30M-citizen answer above.
6. **Close (30s)** — every point of deviation score recovered is revenue for schools and hospitals; the data already exists, it just isn't connected.


---

## v2 Upgrades (C1-C16)

**C1 Transfer chains** — sellers resolved as entities; `purchased_by`/`sold_by` edges; circular and intra-household transfer patterns flagged as evidence.
**C2 Household/benami inference** — same residence + shared surname ⇒ household; household-level declared vs lifestyle gap exposes assets parked on kin. No family CSV needed: households are inferred from the data.
**C3 Sub-score decomposition** — A AssetGap (.30) · B Household/Benami (.20) · C FilingBehavior (.15) · D BankingDev (.15*) · E TravelDev (.10*) · F GraphAnomaly (.10); *plug-in components; weights renormalise over present components.
**C4 Row-level traceability** — every evidence item cites source_file + row_number; defensibility statement appended to every audit trail.
**C5 Named risk tiers** — CRITICAL/HIGH/MEDIUM/LOW/MINIMAL with Urdu equivalents, used across register, dossier, exports.
**C6 Asset timeline** — dated acquisitions (vehicles, properties, accounts, trips) per dossier.
**C7 Phonetic upgrade** — Metaphone second key family + extended generic spelling variants.
**C8 Scale evidence** — 2,725 persons / ~12k core records (~20k with plug-ins); per-stage timings persisted and shown in UI. Core pipeline ≈16s, plug-in mode ≈26s on CPU.
**C9 Case-file export** — print-stylesheet PDF per dossier (offline, zero dependencies).
**C10 Bulk exports** — tier CSV + top-N audit report endpoints/buttons.
**C11 Banking plug-in** — drop `banking_accounts.csv` into `data/` → KYC account aggregation, deposits-vs-declared AML signal, structuring flag. Remove the file → component disappears, weights renormalise.
**C12 Travel plug-in** — drop `travel_logs.csv` → luxury-travel-vs-declared signal ("luxury travel logs" is named in the brief's Core Problem).
**C13 Business applicability** — same engine: FBR tax gap (core) · banking KYC dedup + AML networks (demonstrated by C11) · insurance fraud rings · telecom identity resolution.
**C14-C16 LLM edge services** (`pipeline/llm.py`; set `GROQ_API_KEY` or `GEMINI_API_KEY`) — EN+Urdu audit narration constrained to provided evidence; address-parser fallback for rule-resistant records; borderline-match adjudication of the gray zone [0.65, 0.75) with provenance recorded. All cached; all degrade to deterministic paths offline. Without a key the gray zone routes to the human-review queue (`/api/review-pairs`).

### Demo flow (the drop-in moment)
```bash
python pipeline/run_pipeline.py                       # official 4-CSV mode
cp data/plugins/banking_accounts.csv data/plugins/travel_logs.csv data/
python pipeline/run_pipeline.py                       # banking + travel light up live
```
