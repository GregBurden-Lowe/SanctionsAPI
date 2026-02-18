# auth_db.py — User storage for GUI auth. Uses same PostgreSQL pool as screening_db.
# Requires DATABASE_URL. Seed user: Greg.Burden-Lowe@Legalprotectiongroup.co.uk / Admin (must change at first logon).

from __future__ import annotations

import logging
from typing import Optional, List, Any
from passlib.context import CryptContext

logger = logging.getLogger(__name__)
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

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


async def seed_default_user(conn) -> None:
    """Insert default admin user if not already present (ON CONFLICT DO NOTHING so we never overwrite)."""
    # Always hash the literal "Admin" so we never pass a long/env value to bcrypt (72-byte limit)
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


def hash_password(password: str) -> str:
    """Bcrypt limits input to 72 bytes; truncate to avoid ValueError."""
    if not isinstance(password, str):
        password = str(password)
    pwd_bytes = password.encode("utf-8")
    if len(pwd_bytes) > 72:
        password = pwd_bytes[:72].decode("utf-8", errors="replace")
    return _pwd_ctx.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)


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


async def update_password(conn, user_id: str, new_password: str) -> None:
    """Set password and must_change_password = false."""
    new_hash = hash_password(new_password)
    await conn.execute(
        "UPDATE users SET password_hash = $1, must_change_password = false WHERE id = $2",
        new_hash,
        user_id,
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
