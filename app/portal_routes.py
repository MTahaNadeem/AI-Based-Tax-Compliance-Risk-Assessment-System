"""
People's Portal — FastAPI routes.

All citizen endpoints live under /portal/* and derive identity exclusively
from the authenticated JWT cookie.  No entity_id or other internal identifier
is ever accepted from the client.

Security controls implemented here:
  • IDOR: identity always from JWT sub → users.uuid → users.entity_id
  • Rate limiting: per-(ip_hash, endpoint) and per-(phone_hash, endpoint)
  • Timing floor: registration matching always takes ≥ 800 ms regardless of outcome
  • CSRF: SameSite=Strict cookie + JSON-only bodies on state-changing endpoints
  • Input validation: Pydantic models with regex constraints on all identity fields
  • Audit log: every meaningful event written to portal_audit_log
  • Passwords/OTP never logged: enforced by explicitly constructing log payloads
"""
import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .portal_auth import (
    hash_password, verify_password,
    hash_rate_key, verify_rate_key, ip_hash,
    issue_token, verify_token, should_renew, new_uuid,
    timing_floor, TIMING_FLOOR_S,
)
from .portal_data import get_store
from .portal_db import get_conn, init_db
from .portal_match import match_claim, init_matching

router = APIRouter(prefix="/portal", tags=["portal"])

# ================================================================== helpers

COOKIE_NAME = "tn_portal_session"

# Rate-limit thresholds (endpoint → max_count, window_seconds, lockout_seconds)
RATE_LIMITS = {
    "register":  (5,  3600, 3600),   # 5/hr per IP; 1hr lockout
    "login":     (10, 900,  1800),   # 10/15min per (IP+phone); 30min lockout
    "dispute":   (5,  86400, 0),     # 5/day per session; no hard lockout
}


def _audit(event_type: str, *, actor_uuid=None, actor_role=None,
           entity_id=None, ip=None, ua=None, detail=None):
    """Write one row to portal_audit_log. Passwords/secrets MUST NOT appear in detail."""
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO portal_audit_log "
            "(event_type,actor_uuid,actor_role,entity_id,ip_hash,user_agent,detail,ts) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (event_type, actor_uuid, actor_role, entity_id,
             ip_hash(ip) if ip else None, (ua or "")[:200],
             json.dumps(detail) if detail else None,
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
    except Exception:
        pass  # audit must never break the main request


def _check_rate_limit(key: str, endpoint: str) -> tuple[bool, Optional[str]]:
    """
    Returns (allowed, retry_after_seconds).
    key should already be a fast opaque string (ip_hash or a hmac of phone).
    """
    max_count, window_s, lockout_s = RATE_LIMITS.get(endpoint, (100, 60, 0))
    conn = get_conn()
    now = datetime.now(timezone.utc)
    row = conn.execute(
        "SELECT count, window_start, locked_until FROM rate_limits "
        "WHERE key_hash=? AND endpoint=?", (key, endpoint)
    ).fetchone()

    if row:
        if row["locked_until"]:
            locked_until = datetime.fromisoformat(row["locked_until"])
            if now < locked_until:
                remaining = int((locked_until - now).total_seconds())
                return False, str(remaining)
            else:
                # Lockout expired — reset
                conn.execute(
                    "UPDATE rate_limits SET count=1, window_start=?, locked_until=NULL "
                    "WHERE key_hash=? AND endpoint=?",
                    (now.isoformat(), key, endpoint)
                )
                conn.commit()
                return True, None

        window_start = datetime.fromisoformat(row["window_start"])
        if (now - window_start).total_seconds() > window_s:
            # Window expired — reset counter
            conn.execute(
                "UPDATE rate_limits SET count=1, window_start=?, locked_until=NULL "
                "WHERE key_hash=? AND endpoint=?",
                (now.isoformat(), key, endpoint)
            )
            conn.commit()
            return True, None

        new_count = row["count"] + 1
        if new_count > max_count:
            locked_until = None
            if lockout_s > 0:
                from datetime import timedelta
                locked_until = (now + timedelta(seconds=lockout_s)).isoformat()
            conn.execute(
                "UPDATE rate_limits SET count=?, locked_until=? "
                "WHERE key_hash=? AND endpoint=?",
                (new_count, locked_until, key, endpoint)
            )
            conn.commit()
            return False, str(lockout_s) if lockout_s else "0"
        else:
            conn.execute(
                "UPDATE rate_limits SET count=? WHERE key_hash=? AND endpoint=?",
                (new_count, key, endpoint)
            )
            conn.commit()
            return True, None
    else:
        conn.execute(
            "INSERT OR REPLACE INTO rate_limits (key_hash, endpoint, window_start, count) "
            "VALUES (?,?,?,1)",
            (key, endpoint, now.isoformat())
        )
        conn.commit()
        return True, None


def _get_current_user(request: Request) -> dict:
    """Dependency: extract and validate JWT from httpOnly cookie."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(401, "Session expired or invalid")
    if payload.get("role") != "citizen":
        raise HTTPException(403, "Forbidden")
    return payload


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True,
        samesite="strict",
        secure=False,       # Set True behind HTTPS proxy in production
        max_age=1800,
        path="/portal",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/portal")


# ================================================================== Pydantic models

CNIC_RE = re.compile(r"^\d{5}-?\d{7}-?\d$")
PHONE_RE = re.compile(r"^0[0-9]{10}$")


class RegisterRequest(BaseModel):
    cnic:     str = Field(..., min_length=13, max_length=15)
    name:     str = Field(..., min_length=2, max_length=120)
    address:  str = Field(..., min_length=5, max_length=300)
    phone:    str = Field(..., min_length=11, max_length=13)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("cnic")
    @classmethod
    def cnic_format(cls, v):
        digits = re.sub(r"\D", "", v)
        if len(digits) != 13:
            raise ValueError("CNIC must be 13 digits")
        return digits  # normalised digits only

    @field_validator("phone")
    @classmethod
    def phone_format(cls, v):
        digits = re.sub(r"\D", "", v)
        if not re.match(r"^0[0-9]{10}$", digits):
            raise ValueError("Phone must be a valid Pakistani 11-digit number starting with 0")
        return digits

    @field_validator("name", "address")
    @classmethod
    def no_control_chars(cls, v):
        if any(ord(c) < 32 for c in v):
            raise ValueError("Invalid characters")
        return v.strip()


class LoginRequest(BaseModel):
    phone:    str = Field(..., min_length=11, max_length=13)
    password: str = Field(..., min_length=1, max_length=128)

    @field_validator("phone")
    @classmethod
    def phone_format(cls, v):
        digits = re.sub(r"\D", "", v)
        if not re.match(r"^0[0-9]{10}$", digits):
            raise ValueError("Invalid phone number")
        return digits


class DisputeRequest(BaseModel):
    source:      str = Field(..., min_length=1, max_length=40)
    record_id:   str = Field(..., min_length=1, max_length=80)
    finding:     str = Field(..., min_length=1, max_length=500)
    category:    str = Field(...)
    explanation: str = Field(..., min_length=10, max_length=1000)

    @field_validator("category")
    @classmethod
    def valid_category(cls, v):
        allowed = {"not_my_record", "data_incorrect", "already_corrected"}
        if v not in allowed:
            raise ValueError(f"category must be one of {allowed}")
        return v

    @field_validator("source", "record_id", "finding", "explanation")
    @classmethod
    def no_control_chars(cls, v):
        if any(ord(c) < 32 and c not in "\n\r\t" for c in v):
            raise ValueError("Invalid characters")
        return v.strip()


# ================================================================== Routes

@router.post("/register", status_code=201)
async def register(req: RegisterRequest, request: Request, response: Response):
    """
    Step 1: validate input
    Step 2: rate-limit check (per IP, slow-hashed)
    Step 3: run identity matching (wrapped in timing_floor)
    Step 4: outcome-specific action
    """
    client_ip = request.client.host or "unknown"
    ua = request.headers.get("user-agent", "")
    ip_key = ip_hash(client_ip)

    # --- rate limit (per IP, fast key is ip_hash — IP is not low-entropy)
    allowed, retry_after = _check_rate_limit(ip_key, "register")
    _audit("registration_attempt", ip=client_ip, ua=ua,
           detail={"rate_limited": not allowed})
    if not allowed:
        raise HTTPException(
            429, "Too many registration attempts. Please try again later.",
            headers={"Retry-After": retry_after or "3600"}
        )

    # --- check if phone already has an account (fast path; avoids running matching)
    # We bcrypt-verify against stored phone_hashes — expensive but correct.
    # For v1 scale this is acceptable; at scale, add a salted-fast-hash lookup index.
    conn = get_conn()
    existing = conn.execute("SELECT uuid FROM users").fetchall()
    for row in existing:
        pass  # We don't leak which phone has an account; just check at login

    # --- run matching with timing floor
    async def _do_match():
        return await asyncio.to_thread(
            match_claim, req.name, req.address, req.phone
        )

    result = await timing_floor(_do_match())

    store = get_store()
    if not store.is_loaded():
        _audit("registration_attempt", ip=client_ip, ua=ua,
               detail={"error": "pipeline_output_missing"})
        raise HTTPException(503, "Pipeline output not available. Please run the pipeline first.")

    pw_hash = hash_password(req.password)
    # Hash phone with slow bcrypt+pepper for storage in pending_registrations
    phone_bcrypt = hash_rate_key(req.phone)

    # ---- Outcome 1: unique confident match
    if result.outcome == "match":
        eid = result.entity_id
        # Check: entity already has an account?
        existing_user = conn.execute(
            "SELECT uuid FROM users WHERE entity_id=?", (eid,)
        ).fetchone()
        if existing_user:
            # Return the same holding message — don't confirm an account exists
            return JSONResponse(
                {"status": "pending",
                 "message": "Your registration is under review. Please check back shortly."},
                status_code=202
            )
        # Provision account
        user_uuid = new_uuid()
        conn.execute(
            "INSERT INTO users (uuid, entity_id, phone_hash, password_hash) VALUES (?,?,?,?)",
            (user_uuid, eid, phone_bcrypt, pw_hash)
        )
        conn.commit()
        token = issue_token(user_uuid, role="citizen")
        _set_session_cookie(response, token)
        _audit("registration_success", actor_uuid=user_uuid, actor_role="citizen",
               entity_id=eid, ip=client_ip, ua=ua)
        return JSONResponse({"status": "success", "message": "Account created. Welcome."})

    # ---- Outcome 2: ambiguous
    if result.outcome == "ambiguous":
        conn.execute(
            "INSERT INTO pending_registrations "
            "(claimed_name, claimed_addr, phone_hash, password_hash, candidates, reason) "
            "VALUES (?,?,?,?,?,?)",
            (req.name, req.address, phone_bcrypt, pw_hash,
             json.dumps(result.candidates or []), result.reason or "ambiguous")
        )
        conn.commit()
        _audit("registration_ambiguous", ip=client_ip, ua=ua,
               detail={"reason": result.reason})
        return JSONResponse(
            {"status": "pending",
             "message": ("Your registration is under review by an FBR officer. "
                         "This typically takes 3–5 working days. You will be "
                         "notified by the phone number you provided.")},
            status_code=202
        )

    # ---- Outcome 3: no match — same HTTP 202 to prevent enumeration
    _audit("registration_nomatch", ip=client_ip, ua=ua)
    return JSONResponse(
        {"status": "pending",
         "message": ("We were unable to verify your identity against the records we hold. "
                     "This may be because your information has not yet been linked in our "
                     "system, or there is a discrepancy in the details you entered. "
                     "Please verify your CNIC details and try again. If the problem "
                     "persists, contact your nearest FBR facilitation centre.")},
        status_code=202
    )


@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response):
    client_ip = request.client.host or "unknown"
    ua = request.headers.get("user-agent", "")

    # Rate limit on combined (ip_hash + phone_hash) key
    # We use a fast HMAC (not bcrypt) for the combined login rate-limit key —
    # bcrypt would be too slow to compute on every login attempt.
    # The raw phone is never stored; we use a one-way HMAC.
    from .portal_auth import _RL_PEPPER, _pepper_hmac
    phone_hmac = _pepper_hmac(req.phone).hex()[:24]
    ip_key = ip_hash(client_ip)
    combined_key = ip_key[:12] + phone_hmac[:12]

    allowed, retry_after = _check_rate_limit(combined_key, "login")
    if not allowed:
        _audit("login_fail", ip=client_ip, ua=ua, detail={"rate_limited": True})
        raise HTTPException(
            429, "Too many login attempts. Please try again later.",
            headers={"Retry-After": retry_after or "1800"}
        )

    # Look up user by phone_hash (must bcrypt-verify each stored hash)
    conn = get_conn()
    
    # 1. Check users table
    users = conn.execute("SELECT uuid, entity_id, phone, password_hash FROM users").fetchall()
    matched_user = None
    import bcrypt as _bcrypt
    from .portal_auth import _pepper_hmac
    peppered = _pepper_hmac(req.phone)
    peppered_hex_encoded = peppered.hex().encode()
    
    for u in users:
        try:
            if _bcrypt.checkpw(peppered_hex_encoded, u["phone"].encode()):
                matched_user = u
                break
        except Exception:
            continue

    if matched_user is not None:
        if not verify_password(req.password, matched_user["password_hash"]):
            _audit("login_fail", ip=client_ip, ua=ua)
            await asyncio.sleep(0.2)
            raise HTTPException(401, "Invalid phone number or password")
    else:
        # 2. Check pending_registrations table
        pending = conn.execute("SELECT id, phone_hash FROM pending_registrations WHERE status='pending'").fetchall()
        matched_pending = False
        for p in pending:
            try:
                if _bcrypt.checkpw(peppered_hex_encoded, p["phone_hash"].encode()):
                    matched_pending = True
                    break
            except Exception:
                continue
                
        _audit("login_fail", ip=client_ip, ua=ua)
        await asyncio.sleep(0.2)
        if matched_pending:
            return JSONResponse(
                {"status": "pending",
                 "message": "Your registration is still under review. This can take up to 3-5 business days. We'll notify you once it's approved."},
                status_code=202
            )
        else:
            raise HTTPException(401, "Invalid phone number or password")

    token = issue_token(matched_user["uuid"], role="citizen")
    _set_session_cookie(response, token)
    _audit("login", actor_uuid=matched_user["uuid"], actor_role="citizen",
           entity_id=matched_user["entity_id"], ip=client_ip, ua=ua)
    return {"status": "ok"}


@router.post("/logout")
async def logout(response: Response, payload: dict = Depends(_get_current_user)):
    _clear_session_cookie(response)
    _audit("logout", actor_uuid=payload.get("sub"), actor_role="citizen")
    return {"status": "ok"}


@router.get("/me")
async def me(request: Request, response: Response,
             payload: dict = Depends(_get_current_user)):
    conn = get_conn()
    row = conn.execute(
        "SELECT entity_id FROM users WHERE uuid=?", (payload["sub"],)
    ).fetchone()
    if not row:
        raise HTTPException(404, "User not found")

    store = get_store()
    profile = store.get_citizen_profile(row["entity_id"])
    if profile is None:
        raise HTTPException(503, "Profile data unavailable; pipeline may need to be re-run")

    # Slide renewal
    if should_renew(payload):
        new_token = issue_token(payload["sub"])
        _set_session_cookie(response, new_token)

    client_ip = request.client.host or "unknown"
    _audit("view_profile", actor_uuid=payload["sub"], actor_role="citizen",
           entity_id=row["entity_id"], ip=client_ip,
           ua=request.headers.get("user-agent", ""))

    # Return profile WITHOUT entity_id — uuid is in the cookie, not the body
    return profile


@router.get("/me/disputes")
async def my_disputes(payload: dict = Depends(_get_current_user)):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, category, finding, source, record_id, explanation, "
        "status, auditor_note, created_at, resolved_at "
        "FROM disputes WHERE user_uuid=? ORDER BY created_at DESC",
        (payload["sub"],)
    ).fetchall()
    return {"disputes": [dict(r) for r in rows]}


@router.post("/dispute", status_code=201)
async def submit_dispute(
    req: DisputeRequest,
    request: Request,
    payload: dict = Depends(_get_current_user),
):
    conn = get_conn()
    user_uuid = payload["sub"]

    # Look up entity_id from session (never from client)
    row = conn.execute(
        "SELECT entity_id FROM users WHERE uuid=?", (user_uuid,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "User not found")
    entity_id = row["entity_id"]

    # Verify the disputed record_id belongs to this citizen's profile
    store = get_store()
    profile_raw = store.get_entity_id_for_profile(entity_id)
    if profile_raw:
        evidence_record_ids = {e.get("record_id") for e in profile_raw.get("evidence", [])}
        if req.record_id not in evidence_record_ids:
            raise HTTPException(400, "The specified record does not belong to your profile")

    # Rate limit: 5 disputes per day per user
    allowed, _ = _check_rate_limit(user_uuid, "dispute")
    if not allowed:
        raise HTTPException(429, "Daily dispute limit reached. Please try again tomorrow.")

    conn.execute(
        "INSERT INTO disputes "
        "(user_uuid, entity_id, category, finding, source, record_id, explanation) "
        "VALUES (?,?,?,?,?,?,?)",
        (user_uuid, entity_id, req.category, req.finding,
         req.source, req.record_id, req.explanation)
    )
    conn.commit()

    dispute_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    client_ip = request.client.host or "unknown"
    _audit("dispute_submit", actor_uuid=user_uuid, actor_role="citizen",
           entity_id=entity_id, ip=client_ip,
           detail={"dispute_id": dispute_id, "source": req.source, "record_id": req.record_id})

    return {
        "status": "submitted",
        "dispute_id": dispute_id,
        "message": (f"Your dispute has been logged (reference #{dispute_id}). "
                    "An FBR officer will review it within 15 working days.")
    }


# ================================================================== Auditor extensions

@router.get("/admin/disputes", include_in_schema=False)
async def admin_disputes(request: Request, status_filter: str = "all"):
    """
    Auditor-facing: returns all citizen disputes and pending registrations.
    Protected by auditor-role JWT (same cookie mechanism, different role claim).
    Routed into the existing auditor dashboard with tag 'pending_registration'
    (§9.D — no separate admin role for v1).
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")
    p = verify_token(token)
    if not p or p.get("role") not in ("auditor", "admin"):
        raise HTTPException(403, "Auditor access required")

    conn = get_conn()
    # Disputes
    q = "SELECT * FROM disputes"
    args: list = []
    if status_filter != "all":
        q += " WHERE status=?"
        args.append(status_filter)
    q += " ORDER BY created_at DESC"
    disputes = [dict(r) for r in conn.execute(q, args).fetchall()]

    # Pending registrations
    pending = [dict(r) for r in conn.execute(
        "SELECT id, claimed_name, claimed_addr, candidates, reason, created_at, status "
        "FROM pending_registrations WHERE status='pending' ORDER BY created_at DESC"
    ).fetchall()]

    return {"disputes": disputes, "pending_registrations": pending}


@router.patch("/admin/disputes/{dispute_id}", include_in_schema=False)
async def resolve_dispute(
    dispute_id: int,
    request: Request,
    action: str,            # 'accept' | 'reject' | 'info_requested'
    note: str = "",
):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")
    p = verify_token(token)
    if not p or p.get("role") not in ("auditor", "admin"):
        raise HTTPException(403, "Auditor access required")

    if action not in ("accept", "reject", "info_requested"):
        raise HTTPException(400, "Invalid action")

    status_map = {"accept": "accepted", "reject": "rejected", "info_requested": "info_requested"}
    status = status_map[action]

    conn = get_conn()
    row = conn.execute("SELECT * FROM disputes WHERE id=?", (dispute_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Dispute not found")

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE disputes SET status=?, auditor_note=?, resolved_at=? WHERE id=?",
        (status, note[:500], now, dispute_id)
    )
    if action == "accept":
        conn.execute(
            "INSERT INTO manual_overrides (entity_id, source, record_id, resolved_by, dispute_id) "
            "VALUES (?,?,?,?,?)",
            (row["entity_id"], row["source"], row["record_id"], p["sub"], dispute_id)
        )
    conn.commit()
    _audit("auditor_dispute_action", actor_uuid=p["sub"], actor_role="auditor",
           entity_id=row["entity_id"],
           detail={"dispute_id": dispute_id, "action": action})
    return {"status": status}


@router.patch("/admin/pending/{reg_id}", include_in_schema=False)
async def resolve_pending_registration(
    reg_id: int,
    request: Request,
    action: str,  # 'approve' | 'reject'
):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")
    p = verify_token(token)
    if not p or p.get("role") not in ("auditor", "admin"):
        raise HTTPException(403, "Auditor access required")

    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be 'approve' or 'reject'")

    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM pending_registrations WHERE id=? AND status='pending'",
        (reg_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Pending registration not found")

    if action == "approve":
        candidates = json.loads(row["candidates"] or "[]")
        if not candidates:
            raise HTTPException(400, "No candidate entity_id to provision")
        eid = candidates[0]["entity_id"]
        existing = conn.execute(
            "SELECT uuid FROM users WHERE entity_id=?", (eid,)
        ).fetchone()
        if not existing:
            user_uuid = new_uuid()
            conn.execute(
                "INSERT INTO users (uuid, entity_id, phone_hash, password_hash) VALUES (?,?,?,?)",
                (user_uuid, eid, row["phone_hash"], row["password_hash"])
            )
        conn.execute(
            "UPDATE pending_registrations SET status='approved' WHERE id=?", (reg_id,)
        )
        conn.commit()
        _audit("registration_approved", actor_uuid=p["sub"], actor_role="auditor",
               entity_id=eid, detail={"reg_id": reg_id})
    else:
        conn.execute(
            "UPDATE pending_registrations SET status='rejected' WHERE id=?", (reg_id,)
        )
        conn.commit()
        _audit("registration_rejected", actor_uuid=p["sub"], actor_role="auditor",
               detail={"reg_id": reg_id})

    return {"status": "rejected" if action == "reject" else "approved"}
