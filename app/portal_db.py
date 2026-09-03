"""
People's Portal — database layer.

Database location: outputs/portal.db
Intentionally separate from data/ which holds raw pipeline CSVs.
All tables are created idempotently on first access.
"""
import os
import sqlite3
import threading

# ------------------------------------------------------------------ path
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get(
    "PORTAL_DB_PATH",
    os.path.join(HERE, "..", "outputs", "portal.db"),
)

_local = threading.local()


def _conn() -> sqlite3.Connection:
    """Thread-local SQLite connection (autocommit off)."""
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


DDL = """
-- Citizens with confirmed portal accounts.
-- uuid:        random UUID v4 — the ONLY id ever sent to the browser.
-- entity_id:   internal pipeline cluster id; never sent to browser.
-- phone_hash:  bcrypt hash of normalised phone; used for OTP in v2.
--              NOT the raw phone number.
CREATE TABLE IF NOT EXISTS users (
    uuid          TEXT PRIMARY KEY,
    entity_id     TEXT NOT NULL UNIQUE,
    phone         TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Official administrators (auditors and admins).
-- Distinct from citizens; never share credentials or JWT role space.
CREATE TABLE IF NOT EXISTS admins (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid           TEXT NOT NULL UNIQUE,
    username       TEXT NOT NULL UNIQUE,
    full_name      TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'auditor',
    password_hash  TEXT NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    last_login_at  TEXT
);


-- Registration attempts whose identity claim matched ambiguously.
-- Routed to auditor dashboard with tag 'pending_registration'.
CREATE TABLE IF NOT EXISTS pending_registrations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    claimed_name  TEXT NOT NULL,
    claimed_addr  TEXT NOT NULL,
    phone_hash    TEXT NOT NULL,   -- bcrypt(normalised_phone, 10)
    password_hash TEXT NOT NULL,   -- bcrypt(password, 12) — stored for later provisioning
    candidates    TEXT NOT NULL,   -- JSON [{entity_id, score}, ...]
    reason        TEXT NOT NULL,   -- 'ambiguous_score' | 'multi_match' | 'er_review'
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK(status IN ('pending','approved','rejected'))
);
CREATE INDEX IF NOT EXISTS pr_status ON pending_registrations(status);

-- Citizen-submitted dispute tickets.
CREATE TABLE IF NOT EXISTS disputes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_uuid     TEXT NOT NULL REFERENCES users(uuid),
    entity_id     TEXT NOT NULL,
    category      TEXT NOT NULL,   -- 'not_my_record'|'data_incorrect'|'already_corrected'
    finding       TEXT NOT NULL,   -- the evidence item text the citizen is disputing
    source        TEXT NOT NULL,   -- e.g. 'DISCO', 'FBR'
    record_id     TEXT NOT NULL,   -- e.g. 'MTR-97903700'
    explanation   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK(status IN ('pending','accepted','rejected','info_requested')),
    auditor_note  TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT
);
CREATE INDEX IF NOT EXISTS disp_entity ON disputes(entity_id);
CREATE INDEX IF NOT EXISTS disp_status ON disputes(status);

-- Auditor-accepted dispute outcomes (interface to future pipeline re-run).
CREATE TABLE IF NOT EXISTS manual_overrides (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id     TEXT NOT NULL,
    source        TEXT NOT NULL,
    record_id     TEXT NOT NULL,
    override_type TEXT NOT NULL DEFAULT 'note_only',
    resolved_by   TEXT NOT NULL,
    resolved_at   TEXT NOT NULL DEFAULT (datetime('now')),
    dispute_id    INTEGER REFERENCES disputes(id)
);

-- Rate-limit counters (key = hashed identity or IP-hash; pruned daily).
CREATE TABLE IF NOT EXISTS rate_limits (
    key_hash      TEXT NOT NULL,
    endpoint      TEXT NOT NULL,
    window_start  TEXT NOT NULL,
    count         INTEGER NOT NULL DEFAULT 0,
    locked_until  TEXT,
    PRIMARY KEY (key_hash, endpoint)
);

-- Immutable access audit log (90-day retention).
CREATE TABLE IF NOT EXISTS portal_audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type    TEXT NOT NULL,
    actor_uuid    TEXT,
    actor_role    TEXT,
    entity_id     TEXT,
    ip_hash       TEXT,
    user_agent    TEXT,
    detail        TEXT,
    ts            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS pal_ts ON portal_audit_log(ts);
"""


def init_db() -> None:
    """Create all tables if they don't already exist. Safe to call multiple times."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = _conn()
    c.executescript(DDL)
    c.commit()


def get_conn() -> sqlite3.Connection:
    return _conn()
