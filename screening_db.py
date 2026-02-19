# screening_db.py — PostgreSQL persistence for screening state and job queue.
# Optional: set DATABASE_URL to enable. If unset, API behaves as before (no persistence).

from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from typing import Optional, Any, Dict, List
from uuid import UUID

logger = logging.getLogger(__name__)

_pool: Any = None


def _uk_sanctions_from_result(result: Dict[str, Any]) -> bool:
    """Derive UK sanctions flag from Check Summary.Source (display logic only)."""
    src = (result.get("Check Summary") or {}).get("Source") or ""
    s = src.lower()
    return any(p in s for p in ("uk", "hmt", "ofsi", "hm treasury", "uk fcdo", "uk financial sanctions"))


async def get_pool():
    """Return asyncpg pool if DATABASE_URL is set; else None."""
    global _pool
    if _pool is not None:
        return _pool
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    try:
        import asyncpg
        _pool = await asyncpg.create_pool(url, min_size=1, max_size=4, command_timeout=30)
        return _pool
    except Exception as e:
        logger.warning("screening_db: pool create failed: %s", e)
        return None


async def close_pool():
    """Close the global pool (call on app shutdown)."""
    global _pool
    if _pool is None:
        return
    try:
        await _pool.close()
    except Exception as e:
        logger.warning("screening_db: pool close failed: %s", e)
    _pool = None


async def ensure_schema(conn) -> None:
    """Create tables if not exist (idempotent)."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS screened_entities (
            entity_key       TEXT PRIMARY KEY,
            display_name     TEXT NOT NULL,
            normalized_name  TEXT NOT NULL,
            date_of_birth    DATE,
            entity_type      TEXT NOT NULL DEFAULT 'Person',
            last_screened_at     TIMESTAMPTZ NOT NULL,
            screening_valid_until TIMESTAMPTZ NOT NULL,
            status          TEXT NOT NULL,
            risk_level      TEXT NOT NULL,
            confidence      TEXT NOT NULL,
            score           NUMERIC(5,2) NOT NULL,
            uk_sanctions_flag BOOLEAN NOT NULL DEFAULT FALSE,
            pep_flag        BOOLEAN NOT NULL DEFAULT FALSE,
            result_json     JSONB NOT NULL,
            last_requestor  TEXT,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_screened_entities_valid_until
        ON screened_entities (screening_valid_until)
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS screening_jobs (
            job_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_key      TEXT NOT NULL,
            name            TEXT NOT NULL,
            date_of_birth   TEXT,
            entity_type     TEXT NOT NULL DEFAULT 'Person',
            requestor       TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at      TIMESTAMPTZ,
            finished_at     TIMESTAMPTZ,
            error_message   TEXT
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_screening_jobs_pending
        ON screening_jobs (created_at) WHERE status = 'pending'
    """)


async def get_valid_screening(conn, entity_key: str) -> Optional[Dict[str, Any]]:
    """
    If a row exists and screening_valid_until > now(), return result_json.
    Does NOT extend validity; read-only.
    """
    row = await conn.fetchrow(
        """
        SELECT result_json, screening_valid_until
        FROM screened_entities
        WHERE entity_key = $1 AND screening_valid_until > NOW()
        """,
        entity_key,
    )
    if row is None:
        return None
    rj = row["result_json"]
    if isinstance(rj, str):
        return json.loads(rj)
    if isinstance(rj, dict):
        return rj
    return dict(rj) if hasattr(rj, "items") else rj


async def upsert_screening(
    conn,
    entity_key: str,
    display_name: str,
    normalized_name: str,
    date_of_birth: Optional[str],
    entity_type: str,
    requestor: str,
    result: Dict[str, Any],
) -> None:
    """Insert or replace screened_entities row. Sets validity to last_screened_at + 12 months."""
    now = datetime.now(timezone.utc)
    valid_until = now + timedelta(days=365)
    status = (result.get("Check Summary") or {}).get("Status") or "Unknown"
    risk_level = result.get("Risk Level") or ""
    confidence = result.get("Confidence") or ""
    score = float(result.get("Score") or 0)
    pep_flag = bool(result.get("Is PEP"))
    uk_flag = _uk_sanctions_from_result(result)
    dob_date = None
    if date_of_birth:
        try:
            from datetime import date
            dob_date = date.fromisoformat(date_of_birth.strip()[:10])
        except Exception:
            pass

    await conn.execute(
        """
        INSERT INTO screened_entities (
            entity_key, display_name, normalized_name, date_of_birth, entity_type,
            last_screened_at, screening_valid_until,
            status, risk_level, confidence, score, uk_sanctions_flag, pep_flag,
            result_json, last_requestor, updated_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
        ON CONFLICT (entity_key) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            normalized_name = EXCLUDED.normalized_name,
            date_of_birth = EXCLUDED.date_of_birth,
            entity_type = EXCLUDED.entity_type,
            last_screened_at = EXCLUDED.last_screened_at,
            screening_valid_until = EXCLUDED.screening_valid_until,
            status = EXCLUDED.status,
            risk_level = EXCLUDED.risk_level,
            confidence = EXCLUDED.confidence,
            score = EXCLUDED.score,
            uk_sanctions_flag = EXCLUDED.uk_sanctions_flag,
            pep_flag = EXCLUDED.pep_flag,
            result_json = EXCLUDED.result_json,
            last_requestor = EXCLUDED.last_requestor,
            updated_at = EXCLUDED.updated_at
        """,
        entity_key,
        display_name,
        normalized_name,
        dob_date,
        entity_type or "Person",
        now,
        valid_until,
        status,
        risk_level,
        confidence,
        score,
        uk_flag,
        pep_flag,
        json.dumps(result),
        requestor,
        now,
    )


async def get_pending_running_count(conn) -> int:
    """Count of jobs with status pending or running (for queue pressure / load protection)."""
    row = await conn.fetchrow(
        "SELECT COUNT(*)::int AS n FROM screening_jobs WHERE status IN ('pending', 'running')"
    )
    return row["n"] if row else 0


async def has_pending_or_running_job(conn, entity_key: str) -> bool:
    """True if there is a job for this entity_key with status pending or running."""
    row = await conn.fetchrow(
        """
        SELECT 1 FROM screening_jobs
        WHERE entity_key = $1 AND status IN ('pending', 'running')
        LIMIT 1
        """,
        entity_key,
    )
    return row is not None


async def enqueue_job(
    conn,
    entity_key: str,
    name: str,
    date_of_birth: Optional[str],
    entity_type: str,
    requestor: str,
) -> str:
    """Insert a pending job; return job_id (UUID string)."""
    row = await conn.fetchrow(
        """
        INSERT INTO screening_jobs (entity_key, name, date_of_birth, entity_type, requestor, status)
        VALUES ($1, $2, $3, $4, $5, 'pending')
        RETURNING job_id
        """,
        entity_key,
        name,
        date_of_birth,
        entity_type or "Person",
        requestor,
    )
    return str(row["job_id"])


async def search_screened_entities(
    conn,
    name: Optional[str] = None,
    entity_key: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Search screened_entities by name (partial ILIKE) and/or entity_key (exact).
    At least one of name or entity_key must be provided by the caller.
    Returns list of row dicts with ISO date/timestamp strings.
    """
    conditions = []
    args: List[Any] = []
    n = 0
    if entity_key:
        n += 1
        conditions.append(f"entity_key = ${n}")
        args.append(entity_key.strip())
    if name and name.strip():
        n += 1
        pattern = f"%{name.strip()}%"
        conditions.append(f"(display_name ILIKE ${n} OR normalized_name ILIKE ${n})")
        args.append(pattern)
    if not conditions:
        return []
    limit = max(1, min(100, limit))
    offset = max(0, offset)
    args.extend([limit, offset])
    where_sql = " AND ".join(conditions)
    query = f"""
        SELECT entity_key, display_name, normalized_name, date_of_birth, entity_type,
               last_screened_at, screening_valid_until, status, risk_level, confidence, score,
               uk_sanctions_flag, pep_flag, result_json, last_requestor, updated_at
        FROM screened_entities
        WHERE {where_sql}
        ORDER BY last_screened_at DESC
        LIMIT ${n + 1} OFFSET ${n + 2}
    """
    rows = await conn.fetch(query, *args)
    out = []
    for r in rows:
        d = dict(r)
        for key in ("last_screened_at", "screening_valid_until", "updated_at"):
            if d.get(key) is not None:
                d[key] = d[key].isoformat()
        if d.get("date_of_birth") is not None:
            d["date_of_birth"] = d["date_of_birth"].isoformat()
        if "score" in d and d["score"] is not None:
            d["score"] = float(d["score"])
        if "result_json" in d and d["result_json"] is not None:
            rj = d["result_json"]
            if isinstance(rj, str):
                rj = json.loads(rj)
            elif not isinstance(rj, dict):
                rj = dict(rj) if hasattr(rj, "items") else rj
            d["result_json"] = _to_json_safe(rj)
        out.append(_to_json_safe(d))
    return out


def _to_json_safe(obj: Any) -> Any:
    """Convert non-JSON-serializable types so FastAPI can serialize the response."""
    if obj is None:
        return None
    if isinstance(obj, (Decimal,)):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(v) for v in obj]
    return obj


async def purge_screened_entities_older_than(conn, months: int) -> int:
    """
    Delete screened_entities rows where last_screened_at is older than the given number of months.
    Returns the number of rows deleted. Use SCREENED_ENTITIES_RETENTION_MONTHS (env) to drive retention.
    """
    if months < 1:
        return 0
    result = await conn.execute(
        """
        DELETE FROM screened_entities
        WHERE last_screened_at < NOW() - ($1::text || ' months')::interval
        """,
        months,
    )
    # asyncpg execute returns "DELETE N"
    try:
        return int(result.split()[-1]) if result else 0
    except (ValueError, IndexError):
        return 0


async def get_job_status(conn, job_id: str) -> Optional[Dict[str, Any]]:
    """
    Return { status, entity_key?, result?, error_message? }.
    If status is 'completed', result is loaded from screened_entities.
    """
    row = await conn.fetchrow(
        "SELECT status, entity_key, error_message FROM screening_jobs WHERE job_id = $1",
        job_id,
    )
    if row is None:
        return None
    out = {"status": row["status"], "job_id": job_id, "entity_key": row["entity_key"]}
    if row["error_message"]:
        out["error_message"] = row["error_message"]
    if row["status"] == "completed":
        entity_row = await conn.fetchrow(
            "SELECT result_json FROM screened_entities WHERE entity_key = $1",
            row["entity_key"],
        )
        if entity_row:
            rj = entity_row["result_json"]
            if isinstance(rj, str):
                out["result"] = json.loads(rj)
            elif isinstance(rj, dict):
                out["result"] = rj
            else:
                out["result"] = dict(rj) if hasattr(rj, "items") else rj
    return out
