"""
TaxNet Graph — API + dashboard server.
Run:  uvicorn app.main:app --port 8005   ->  http://localhost:8005

Two surfaces on one process:
  /          → Auditor dashboard (existing)
  /portal    → Citizens' People's Portal (new)
"""
import csv
import io
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "..", "outputs", "api_data.json")


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Startup: initialise portal DB and prime the matching index."""
    from .portal_db import init_db
    from .portal_data import get_store
    from .portal_match import init_matching
    init_db()
    store = get_store()   # loads api_data.json into memory
    profiles = list(store._by_entity.values()) if store.is_loaded() else []
    init_matching(profiles)
    yield
    # (shutdown — nothing to clean up)


app = FastAPI(title="TaxNet Graph API", version="2.0", lifespan=lifespan)

from fastapi import Request
from fastapi.responses import JSONResponse
from .portal_auth import verify_token
from .portal_routes import COOKIE_NAME

@app.middleware("http")
async def require_admin_auditor(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
        p = verify_token(token)
        if not p or p.get("role") not in ("auditor", "admin"):
            return JSONResponse(status_code=403, content={"detail": "Admin/Auditor access required"})
    return await call_next(request)

def load():
    if not os.path.exists(DATA_PATH):
        raise HTTPException(503, "Pipeline output missing — run pipeline/run_pipeline.py first")
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)

SLIM = ("entity_id", "name", "score", "tier", "tier_ur", "filer", "declared_income",
        "lifestyle_income", "n_vehicles", "n_properties", "avg_bill", "n_sources",
        "household_members", "n_accounts", "n_intl_trips")

@app.get("/api/summary")
def summary():
    d = load()
    return {k: d[k] for k in ("generated_at", "pipeline_seconds", "timings", "plugins",
                              "llm_active", "er_stats", "er_metrics", "tiers",
                              "n_households", "graph_stats")}

@app.get("/api/profiles")
def profiles(tier: str | None = None, limit: int = 300):
    d = load()
    rows = d["profiles"]
    if tier:
        rows = [p for p in rows if p["tier"] == tier.upper()]
    return dict(total=len(rows),
                profiles=[{k: p[k] for k in SLIM} for p in rows[:limit]])

@app.get("/api/profile/{entity_id}")
def profile(entity_id: str):
    for p in load()["profiles"]:
        if p["entity_id"] == entity_id:
            return p
    raise HTTPException(404, "entity not found")

@app.get("/api/review-pairs")
def review_pairs():
    d = load()
    return dict(total=len(d["review_pairs"]), pairs=d["review_pairs"])

@app.get("/api/export/csv")
def export_csv(tier: str | None = None, top: int = 0):
    d = load()
    rows = d["profiles"]
    if tier:
        rows = [p for p in rows if p["tier"] == tier.upper()]
    if top:
        rows = rows[:top]
    buf = io.StringIO()
    cols = ["entity_id", "name", "tier", "score"] + \
           [f"score_{k}" for k in "ABCDEF"] + \
           ["declared_income_pkr", "lifestyle_income_pkr", "household_id",
            "household_members", "filer_status", "n_sources", "n_vehicles",
            "n_properties", "n_accounts", "n_intl_trips"]
    w = csv.writer(buf)
    w.writerow(cols)
    for p in rows:
        w.writerow([p["entity_id"], p["name"], p["tier"], p["score"]]
                   + [p["components"].get(k, "") for k in "ABCDEF"]
                   + [p["declared_income"], p["lifestyle_income"], p["household_id"],
                      p["household_members"], p["filer"], p["n_sources"],
                      p["n_vehicles"], p["n_properties"], p["n_accounts"],
                      p["n_intl_trips"]])
    buf.seek(0)
    name = f"taxnet_{tier or 'all'}{'_top'+str(top) if top else ''}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={name}"})

@app.get("/api/export/audit-report")
def audit_report(top: int = 20):
    d = load()
    rows = d["profiles"][:top]
    parts = [f"TAXNET GRAPH — BULK AUDIT REPORT (top {top}) — generated {d['generated_at']}\n"
             + "=" * 78]
    for i, p in enumerate(rows, 1):
        parts.append(f"\n{i}. {p['name']}  [{p['entity_id']}]  "
                     f"TIER {p['tier']}  SCORE {p['score']}\n" + "-" * 78
                     + f"\n{p['audit']}\n\nEvidence:")
        for e in p["evidence"]:
            parts.append(f"  - [{e['source']}] {e['finding']}  "
                         f"({e['source_file']} row {e['row_number']})")
        parts.append(f"\n{p['defensibility']}\n")
    text = "\n".join(parts)
    return StreamingResponse(iter([text]), media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename=taxnet_audit_top{top}.txt"})

# ---- additive endpoints (frontend support; existing endpoints unchanged) ----

CITY_NAMES = [("Islamabad", "اسلام آباد"), ("Rawalpindi", "راولپنڈی"),
              ("Lahore", "لاہور"), ("Karachi", "کراچی"), ("Faisalabad", "فیصل آباد"),
              ("Multan", "ملتان"), ("Peshawar", "پشاور")]

@app.get("/api/geo")
def geo():
    """Per-city aggregates derived from address-bearing labels in each entity's graph."""
    d = load()
    cities = {en: dict(city=en, city_ur=ur, entities=0, tiers={}, gap=0, top=[])
              for en, ur in CITY_NAMES}
    located = 0
    for p in d["profiles"]:
        city = None
        for n in p["graph"]["nodes"]:
            lbl = str(n.get("label", ""))
            for en, ur in CITY_NAMES:
                if en in lbl or ur in lbl:
                    city = en
                    break
            if city:
                break
        if not city:
            continue
        located += 1
        c = cities[city]
        c["entities"] += 1
        c["tiers"][p["tier"]] = c["tiers"].get(p["tier"], 0) + 1
        c["gap"] += max(0, p["lifestyle_income"] - p["declared_income"])
        c["top"].append(dict(entity_id=p["entity_id"], name=p["name"],
                             score=p["score"], tier=p["tier"]))
        c["top"] = sorted(c["top"], key=lambda t: -t["score"])[:3]
    return dict(located=located, total=len(d["profiles"]),
                cities=list(cities.values()))

SOURCE_META = [
    ("fbr", "FBR Iris", "fbr_tax_records.csv", "core",
     "Declared income, tax paid, filer status", "C"),
    ("excise", "Excise & Taxation", "excise_vehicles.csv", "core",
     "Vehicle registrations and engine capacity", "A"),
    ("disco", "DISCO Billing", "disco_consumption.csv", "core",
     "Electricity consumption and monthly billing", "A"),
    ("registry", "Property Registry", "property_transfers.csv", "core",
     "Property transfers, valuations, benami patterns", "A·B"),
    ("banking", "SBP Banking (plug-in)", os.path.join("plugins", "banking_accounts.csv"),
     "plugin", "Bank accounts and average deposits", "D"),
    ("travel", "FIA Travel (plug-in)", os.path.join("plugins", "travel_logs.csv"),
     "plugin", "International travel logs and trip spend", "E"),
]

@app.get("/api/sources")
def sources():
    """Real status of every data source: rows on disk + whether the last run ingested it."""
    d = load()
    base = os.path.join(HERE, "..", "data")
    active = d.get("plugins") or []
    out = []
    for key, name, rel, kind, desc, comp in SOURCE_META:
        path = os.path.join(base, rel)
        info = dict(key=key, name=name, file=rel.replace(os.sep, "/"), kind=kind,
                    desc=desc, component=comp, on_disk=os.path.exists(path),
                    records=0, size_kb=0.0)
        if info["on_disk"]:
            with open(path, encoding="utf-8", errors="replace") as f:
                info["records"] = max(0, sum(1 for _ in f) - 1)
            info["size_kb"] = round(os.path.getsize(path) / 1024, 1)
        info["loaded"] = kind == "core" or key in active
        out.append(info)
    return dict(sources=out, plugins_active=active,
                llm_active=d.get("llm_active", False),
                generated_at=d["generated_at"], timings=d["timings"],
                er_stats=d["er_stats"], er_metrics=d["er_metrics"])

@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "static", "index.html"))


@app.get("/portal", include_in_schema=False)
@app.get("/portal/", include_in_schema=False)
def portal_index():
    return FileResponse(os.path.join(HERE, "static", "portal.html"))


# Register portal routes
from .portal_routes import router as portal_router  # noqa: E402
app.include_router(portal_router)

app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
