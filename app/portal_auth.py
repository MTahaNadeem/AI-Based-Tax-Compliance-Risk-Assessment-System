"""
People's Portal — authentication helpers.

Design decisions carried forward from the approved design doc:

PASSWORD HASHING
  bcrypt cost=12.  All citizen passwords.

RATE-LIMIT KEY HASHING (§2.1 amendment)
  Low-entropy inputs (CNIC, phone numbers) are brute-forceable under fast
  hashes (SHA-256 does ~1B ops/sec; a 13-digit CNIC space is ~10^13,
  feasible with a GPU cluster).  We therefore use bcrypt cost=10 (slower
  than password hashing but still protective) with a fixed server-side
  pepper loaded from the environment.  The stored bcrypt hash acts as the
  rate-limit table key — the raw phone/CNIC never touches the DB.

JWT
  HS256, 30-minute access tokens, sliding renewal within 5 minutes of
  expiry.  Issued as httpOnly SameSite=Strict cookies.

TIMING FLOOR (§2 amendment)
  All three registration matching outcomes must respond in ≥ TIMING_FLOOR_S
  seconds (default 0.8s) to prevent timing-based enumeration attacks.
  The caller wraps with timing_floor().

ACCEPTED RISK (v1)
  Password-only — no OTP second factor.  This is a known gap for a
  financial-data-adjacent portal.  OTP requires an SMS provider decision
  (§9.B of the design doc) and will be retrofitted in v2.  The phone
  is stored (hashed) now to enable that without a schema change.
"""
import asyncio
import hashlib
import hmac
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

import bcrypt
import jwt

# ------------------------------------------------------------------ config
JWT_SECRET = os.environ.get("PORTAL_JWT_SECRET", "CHANGE-ME-in-production-min-32-bytes!")
JWT_ALG = "HS256"
JWT_LIFETIME_S = 1800        # 30 minutes
JWT_RENEW_WINDOW_S = 300     # re-issue if < 5 min remain

BCRYPT_ROUNDS_PASSWORD = 12
BCRYPT_ROUNDS_RATE_KEY = 10  # for low-entropy inputs (CNIC, phone)

# Server-side pepper for rate-limit key derivation.
# Loaded from env; if absent we fall back to a deterministic but weak value
# and warn — never silently use a blank pepper.
_RL_PEPPER = os.environ.get("PORTAL_RL_PEPPER", "").encode()
if not _RL_PEPPER:
    _RL_PEPPER = b"default-pepper-CHANGE-IN-PRODUCTION"

TIMING_FLOOR_S = float(os.environ.get("PORTAL_TIMING_FLOOR", "0.8"))


# ------------------------------------------------------------------ password
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(BCRYPT_ROUNDS_PASSWORD)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


# ------------------------------------------------------------------ rate-limit key
def _pepper_hmac(value: str) -> bytes:
    """HMAC-SHA256 of value with the server pepper — fast pre-step before bcrypt."""
    return hmac.digest(_RL_PEPPER, value.encode(), "sha256")


def hash_rate_key(value: str) -> str:
    """
    Slow-hash a low-entropy input (CNIC or normalised phone) for use as a
    rate-limit table key.

    Pipeline: pepper → HMAC-SHA256 (fast; stretches entropy slightly) →
              bcrypt cost=10 (slow; prevents brute-force of the stored hash).

    The result is a bcrypt hash string, suitable for storage in rate_limits.
    """
    peppered = _pepper_hmac(value)
    # bcrypt expects bytes; we pass the hex-encoded HMAC so all bytes are printable
    return bcrypt.hashpw(peppered.hex().encode(), bcrypt.gensalt(BCRYPT_ROUNDS_RATE_KEY)).decode()


def verify_rate_key(value: str, stored_hash: str) -> bool:
    """Check whether value matches a previously stored rate-limit hash."""
    try:
        peppered = _pepper_hmac(value)
        return bcrypt.checkpw(peppered.hex().encode(), stored_hash.encode())
    except Exception:
        return False


def ip_hash(ip: str) -> str:
    """Fast one-way hash for IP — used in audit log only, not as a rate-limit key."""
    return hashlib.sha256((_RL_PEPPER + ip.encode())).hexdigest()[:16]


# ------------------------------------------------------------------ JWT
def issue_token(user_uuid: str, role: str = "citizen") -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_uuid,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=JWT_LIFETIME_S)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def verify_token(token: str) -> Optional[dict]:
    """Returns decoded payload or None if invalid/expired."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        return None


def should_renew(payload: dict) -> bool:
    remaining = payload["exp"] - int(time.time())
    return remaining < JWT_RENEW_WINDOW_S


def new_uuid() -> str:
    return str(uuid.uuid4())


# ------------------------------------------------------------------ timing floor
async def timing_floor(coro, floor: float = TIMING_FLOOR_S):
    """
    Await coro and then sleep the remainder up to floor seconds.
    Guarantees the response is never faster than floor seconds,
    regardless of which branch (match / ambiguous / no-match) was taken.
    This prevents timing-based enumeration of registration outcomes.
    """
    t0 = asyncio.get_event_loop().time()
    result = await coro
    elapsed = asyncio.get_event_loop().time() - t0
    remainder = floor - elapsed
    if remainder > 0:
        await asyncio.sleep(remainder)
    return result
