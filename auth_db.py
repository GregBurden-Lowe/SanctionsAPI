# auth_db.py — User storage for GUI auth. Uses same PostgreSQL pool as screening_db.
# Requires DATABASE_URL. Seed user: Greg.Burden-Lowe@Legalprotectiongroup.co.uk / Admin (must change at first logon).

from __future__ import annotations

import hashlib
import logging
import math
import secrets
import time
from typing import Optional, List, Any

from security import hash_password, verify_password

logger = logging.getLogger(__name__)

DEFAULT_USER_EMAIL = "Greg.Burden-Lowe@Legalprotectiongroup.co.uk"
DEFAULT_USER_PASSWORD = "Admin"


async def ensure_auth_schema(conn) -> None:
    """Create users table if not exist (idempotent)."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email               TEXT UNIQUE NOT NULL,
            password_hash       TEXT NOT NULL,
            must_change_password BOOLEAN NOT NULL DEFAULT true,
            is_admin            BOOLEAN NOT NULL DEFAULT false,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_email ON users (email)
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS access_requests (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email        TEXT NOT NULL,
            requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_access_requests_email ON access_requests (email)
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_login_attempts (
            id           BIGSERIAL PRIMARY KEY,
            email        TEXT NOT NULL,
            attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            success      BOOLEAN NOT NULL DEFAULT false,
            client_ip    TEXT
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_auth_login_attempts_email_time
        ON auth_login_attempts (email, attempted_at DESC)
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name         TEXT NOT NULL,
            key_hash     TEXT NOT NULL UNIQUE,
            role         TEXT NOT NULL,
            active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_used_at TIMESTAMPTZ
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys (active)
    """)


async def seed_default_user(conn) -> None:
    """Insert default admin user if not already present (ON CONFLICT DO NOTHING so we never overwrite)."""
    password_hash = hash_password("Admin")
    await conn.execute(
        """
        INSERT INTO users (email, password_hash, must_change_password, is_admin)
        VALUES ($1, $2, true, true)
        ON CONFLICT (email) DO NOTHING
        """,
        DEFAULT_USER_EMAIL.lower(),
        password_hash,
    )
    logger.info("auth_db: default user ensured (email=%s)", DEFAULT_USER_EMAIL)


async def get_user_by_email(conn, email: str) -> Optional[dict]:
    """Return user row as dict or None. Keys: id, email, password_hash, must_change_password, is_admin, created_at."""
    row = await conn.fetchrow(
        "SELECT id, email, password_hash, must_change_password, is_admin, created_at FROM users WHERE email = $1",
        email.strip().lower(),
    )
    if row is None:
        return None
    return dict(row)


async def verify_user(conn, email: str, password: str) -> Optional[dict]:
    """If email/password match, return user dict (without password_hash). Else None."""
    user = await get_user_by_email(conn, email)
    if user is None:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    out = {k: v for k, v in user.items() if k != "password_hash"}
    out["id"] = str(out["id"])
    return out


def _login_backoff_seconds_for_failures(failed_count: int) -> int:
    """Soft backoff policy for repeated failed logins (no hard lockout)."""
    if failed_count >= 10:
        return 600
    if failed_count >= 8:
        return 120
    if failed_count >= 5:
        return 30
    return 0


async def get_login_backoff_remaining_seconds(conn, email: str) -> int:
    """
    Return remaining backoff in seconds for this account based on failed attempts
    in the last 15 minutes. 0 means no delay.
    """
    row = await conn.fetchrow(
        """
        SELECT
            COUNT(*)::int AS failed_count,
            MAX(attempted_at) AS last_failed_at
        FROM auth_login_attempts
        WHERE email = $1
          AND success = false
          AND attempted_at > NOW() - INTERVAL '15 minutes'
        """,
        email.strip().lower(),
    )
    if not row:
        return 0
    failed_count = int(row.get("failed_count") or 0)
    delay = _login_backoff_seconds_for_failures(failed_count)
    last_failed_at = row.get("last_failed_at")
    if delay <= 0 or last_failed_at is None:
        return 0
    remaining = math.ceil((last_failed_at.timestamp() + delay) - time.time())
    return max(0, remaining)


async def record_login_attempt(conn, email: str, success: bool, client_ip: Optional[str] = None) -> None:
    """Record login attempt outcome and prune old rows for this account."""
    email_norm = email.strip().lower()
    await conn.execute(
        """
        INSERT INTO auth_login_attempts (email, success, client_ip)
        VALUES ($1, $2, $3)
        """,
        email_norm,
        bool(success),
        (client_ip or "").strip() or None,
    )
    # Keep table size bounded for each account.
    await conn.execute(
        """
        DELETE FROM auth_login_attempts
        WHERE email = $1
          AND attempted_at < NOW() - INTERVAL '30 days'
        """,
        email_norm,
    )


async def update_password(conn, user_id: str, new_password: str) -> None:
    """Set password and must_change_password = false."""
    new_hash = hash_password(new_password)
    await conn.execute(
        "UPDATE users SET password_hash = $1, must_change_password = false WHERE id = $2",
        new_hash,
        user_id,
    )


async def update_user(
    conn,
    user_id: str,
    *,
    is_admin: Optional[bool] = None,
    new_password: Optional[str] = None,
) -> None:
    """Update user: set is_admin and/or set a new password (with must_change_password=true)."""
    updates = []
    args = []
    n = 1
    if is_admin is not None:
        updates.append(f"is_admin = ${n}")
        args.append(is_admin)
        n += 1
    if new_password is not None:
        updates.append(f"password_hash = ${n}")
        args.append(hash_password(new_password))
        n += 1
        updates.append("must_change_password = true")
    if not updates:
        return
    args.append(user_id)
    await conn.execute(
        f"UPDATE users SET {', '.join(updates)} WHERE id = ${n}",
        *args,
    )


async def list_users(conn) -> List[dict]:
    """Return all users (no password_hash). id as str, created_at as ISO string."""
    rows = await conn.fetch(
        "SELECT id, email, must_change_password, is_admin, created_at FROM users ORDER BY created_at"
    )
    return [
        {
            "id": str(r["id"]),
            "email": r["email"],
            "must_change_password": r["must_change_password"],
            "is_admin": r["is_admin"],
            "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
        }
        for r in rows
    ]


async def create_user(
    conn,
    email: str,
    password: str,
    *,
    must_change_password: bool = True,
    is_admin: bool = False,
) -> dict:
    """Insert user; return created user (no password_hash). Raises if email exists."""
    email = email.strip().lower()
    if not email:
        raise ValueError("Email required")
    password_hash = hash_password(password)
    row = await conn.fetchrow(
        """
        INSERT INTO users (email, password_hash, must_change_password, is_admin)
        VALUES ($1, $2, $3, $4)
        RETURNING id, email, must_change_password, is_admin, created_at
        """,
        email,
        password_hash,
        must_change_password,
        is_admin,
    )
    if row is None:
        raise ValueError("Insert failed")
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "must_change_password": row["must_change_password"],
        "is_admin": row["is_admin"],
        "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
    }


async def create_access_request(conn, email: str) -> None:
    """Record an access request (email only)."""
    email = email.strip().lower()
    if not email:
        raise ValueError("Email required")
    await conn.execute(
        "INSERT INTO access_requests (email) VALUES ($1)",
        email,
    )


async def list_access_requests(conn) -> List[dict]:
    """Return pending access requests (id, email, requested_at)."""
    rows = await conn.fetch(
        "SELECT id, email, requested_at FROM access_requests ORDER BY requested_at"
    )
    return [
        {
            "id": str(r["id"]),
            "email": r["email"],
            "requested_at": r["requested_at"].isoformat() if hasattr(r["requested_at"], "isoformat") else str(r["requested_at"]),
        }
        for r in rows
    ]


async def grant_access_request(conn, request_id: str, password: str) -> dict:
    """
    Create user from access request (email + temp password, must_change_password=True),
    then delete all access requests for that email. Returns created user. Raises if email already exists.
    """
    row = await conn.fetchrow(
        "SELECT id, email FROM access_requests WHERE id = $1",
        request_id,
    )
    if row is None:
        raise ValueError("Access request not found")
    email = row["email"]
    user = await create_user(conn, email, password, must_change_password=True, is_admin=False)
    await conn.execute("DELETE FROM access_requests WHERE email = $1", email)
    return user


def _hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def create_api_key(conn, *, name: str, role: str = "screening") -> dict:
    """
    Create an API key record and return metadata + plaintext key once.
    Only key_hash is stored in DB.
    """
    key_plain = "sak_" + secrets.token_urlsafe(32)
    key_hash = _hash_api_key(key_plain)
    row = await conn.fetchrow(
        """
        INSERT INTO api_keys (name, key_hash, role, active)
        VALUES ($1, $2, $3, TRUE)
        RETURNING id, name, role, active, created_at, last_used_at
        """,
        name.strip(),
        key_hash,
        role.strip(),
    )
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "role": row["role"],
        "active": bool(row["active"]),
        "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
        "last_used_at": row["last_used_at"].isoformat() if row["last_used_at"] and hasattr(row["last_used_at"], "isoformat") else (str(row["last_used_at"]) if row["last_used_at"] else None),
        "api_key": key_plain,
    }


async def list_api_keys(conn) -> List[dict]:
    rows = await conn.fetch(
        """
        SELECT id, name, role, active, created_at, last_used_at
        FROM api_keys
        ORDER BY created_at DESC
        """
    )
    out: List[dict] = []
    for r in rows:
        out.append(
            {
                "id": str(r["id"]),
                "name": r["name"],
                "role": r["role"],
                "active": bool(r["active"]),
                "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
                "last_used_at": r["last_used_at"].isoformat() if r["last_used_at"] and hasattr(r["last_used_at"], "isoformat") else (str(r["last_used_at"]) if r["last_used_at"] else None),
            }
        )
    return out


async def set_api_key_active(conn, api_key_id: str, *, active: bool) -> bool:
    row = await conn.fetchrow(
        """
        UPDATE api_keys
        SET active = $2
        WHERE id = $1::uuid
        RETURNING id
        """,
        api_key_id,
        bool(active),
    )
    return row is not None


async def delete_api_key(conn, api_key_id: str) -> bool:
    row = await conn.fetchrow(
        """
        DELETE FROM api_keys
        WHERE id = $1::uuid
        RETURNING id
        """,
        api_key_id,
    )
    return row is not None


async def get_active_api_key_by_token(conn, token: str) -> Optional[dict]:
    """
    Resolve active API key by presented bearer token.
    """
    key_hash = _hash_api_key(token)
    row = await conn.fetchrow(
        """
        SELECT id, name, role, active
        FROM api_keys
        WHERE key_hash = $1
          AND active = TRUE
        LIMIT 1
        """,
        key_hash,
    )
    return dict(row) if row is not None else None


async def touch_api_key_last_used(conn, api_key_id: str) -> None:
    await conn.execute(
        """
        UPDATE api_keys
        SET last_used_at = NOW()
        WHERE id = $1::uuid
        """,
        api_key_id,
    )
