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
_REASON_FOR_CHECK_ALLOWED = frozenset({
    "Client Onboarding",
    "Claim Payment",
    "Business Partner Payment",
    "Business Partner Due Diligence",
    "Periodic Re-Screen",
    "Ad-Hoc Compliance Review",
})


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
            business_reference TEXT,
            reason_for_check TEXT,
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
            business_reference TEXT,
            reason_for_check TEXT,
            reason          TEXT NOT NULL DEFAULT 'manual',
            refresh_run_id  UUID,
            force_rescreen  BOOLEAN NOT NULL DEFAULT FALSE,
            status          TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            previous_status TEXT,
            result_status   TEXT,
            transition      TEXT,
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
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_refresh_runs (
            refresh_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ran_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            include_peps BOOLEAN NOT NULL DEFAULT TRUE,
            postgres_synced BOOLEAN NOT NULL DEFAULT FALSE,
            sanctions_rows INTEGER NOT NULL DEFAULT 0,
            peps_rows INTEGER NOT NULL DEFAULT 0,
            uk_hash TEXT,
            prev_uk_hash TEXT,
            uk_changed BOOLEAN NOT NULL DEFAULT FALSE,
            uk_row_count INTEGER NOT NULL DEFAULT 0,
            delta_added INTEGER NOT NULL DEFAULT 0,
            delta_removed INTEGER NOT NULL DEFAULT 0,
            delta_changed INTEGER NOT NULL DEFAULT 0,
            candidate_count INTEGER NOT NULL DEFAULT 0,
            queued_count INTEGER NOT NULL DEFAULT 0,
            already_pending_count INTEGER NOT NULL DEFAULT 0,
            reused_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_uk_snapshot_entries (
            refresh_run_id UUID NOT NULL REFERENCES watchlist_refresh_runs(refresh_run_id) ON DELETE CASCADE,
            fingerprint TEXT NOT NULL,
            entity_id TEXT,
            name_norm TEXT NOT NULL,
            birth_date TEXT,
            dataset TEXT,
            regime TEXT,
            PRIMARY KEY (refresh_run_id, fingerprint)
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_watchlist_snapshot_name_norm
        ON watchlist_uk_snapshot_entries (name_norm)
    """)
    await conn.execute("""
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS screened_against_uk_hash TEXT
    """)
    await conn.execute("""
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS screened_against_refresh_run_id UUID
    """)
    await conn.execute("""
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS manual_override_uk_hash TEXT
    """)
    await conn.execute("""
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS manual_override_stale BOOLEAN NOT NULL DEFAULT FALSE
    """)
    await conn.execute("""
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS business_reference TEXT
    """)
    await conn.execute("""
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS reason_for_check TEXT
    """)
    await conn.execute("""
        ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT 'manual'
    """)
    await conn.execute("""
        ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS business_reference TEXT
    """)
    await conn.execute("""
        ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS reason_for_check TEXT
    """)
    await conn.execute("""
        ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS refresh_run_id UUID
    """)
    await conn.execute("""
        ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS force_rescreen BOOLEAN NOT NULL DEFAULT FALSE
    """)
    await conn.execute("""
        ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS previous_status TEXT
    """)
    await conn.execute("""
        ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS result_status TEXT
    """)
    await conn.execute("""
        ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS transition TEXT
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_screening_jobs_refresh_run
        ON screening_jobs (refresh_run_id)
    """)


async def get_valid_screening(conn, entity_key: str) -> Optional[Dict[str, Any]]:
    """
    If a row exists and screening_valid_until > now(), return result_json.
    Does NOT extend validity; read-only.
    """
    row = await conn.fetchrow(
        """
        SELECT result_json, screening_valid_until, manual_override_stale
        FROM screened_entities
        WHERE entity_key = $1 AND screening_valid_until > NOW()
        """,
        entity_key,
    )
    if row is None:
        return None
    if bool(row["manual_override_stale"]):
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
    business_reference: Optional[str],
    reason_for_check: Optional[str],
    result: Dict[str, Any],
    screened_against_uk_hash: Optional[str] = None,
    screened_against_refresh_run_id: Optional[str] = None,
) -> None:
    """Insert or replace screened_entities row. Sets validity to last_screened_at + 12 months."""
    business_reference_clean = (business_reference or "").strip()
    reason_for_check_clean = (reason_for_check or "").strip()
    if not business_reference_clean:
        raise ValueError("business_reference is required")
    if reason_for_check_clean not in _REASON_FOR_CHECK_ALLOWED:
        raise ValueError("reason_for_check is required and must be a valid enum value")
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
            result_json, last_requestor, business_reference, reason_for_check, updated_at,
            screened_against_uk_hash, screened_against_refresh_run_id,
            manual_override_uk_hash, manual_override_stale
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20::uuid, NULL, FALSE)
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
            business_reference = EXCLUDED.business_reference,
            reason_for_check = EXCLUDED.reason_for_check,
            updated_at = EXCLUDED.updated_at,
            screened_against_uk_hash = EXCLUDED.screened_against_uk_hash,
            screened_against_refresh_run_id = EXCLUDED.screened_against_refresh_run_id,
            manual_override_uk_hash = NULL,
            manual_override_stale = FALSE
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
        business_reference_clean,
        reason_for_check_clean,
        now,
        screened_against_uk_hash,
        screened_against_refresh_run_id,
    )


async def update_cached_screening_metadata(
    conn,
    *,
    entity_key: str,
    requestor: str,
    business_reference: Optional[str],
    reason_for_check: Optional[str],
) -> None:
    """Update request metadata when a cached screening is reused."""
    business_reference_clean = (business_reference or "").strip()
    reason_for_check_clean = (reason_for_check or "").strip()
    if not business_reference_clean:
        raise ValueError("business_reference is required")
    if reason_for_check_clean not in _REASON_FOR_CHECK_ALLOWED:
        raise ValueError("reason_for_check is required and must be a valid enum value")
    await conn.execute(
        """
        UPDATE screened_entities
        SET last_requestor = $2,
            business_reference = $3,
            reason_for_check = $4,
            updated_at = NOW()
        WHERE entity_key = $1
        """,
        entity_key,
        requestor,
        business_reference_clean,
        reason_for_check_clean,
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
    business_reference: Optional[str] = None,
    reason_for_check: Optional[str] = None,
    reason: str = "manual",
    refresh_run_id: Optional[str] = None,
    force_rescreen: bool = False,
) -> str:
    """Insert a pending job; return job_id (UUID string)."""
    business_reference_clean = (business_reference or "").strip()
    reason_for_check_clean = (reason_for_check or "").strip()
    if not business_reference_clean:
        raise ValueError("business_reference is required")
    if reason_for_check_clean not in _REASON_FOR_CHECK_ALLOWED:
        raise ValueError("reason_for_check is required and must be a valid enum value")
    row = await conn.fetchrow(
        """
        INSERT INTO screening_jobs (
            entity_key, name, date_of_birth, entity_type, requestor, business_reference, reason_for_check, reason, refresh_run_id, force_rescreen, status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::uuid, $10, 'pending')
        RETURNING job_id
        """,
        entity_key,
        name,
        date_of_birth,
        entity_type or "Person",
        requestor,
        business_reference_clean,
        reason_for_check_clean,
        reason or "manual",
        refresh_run_id,
        bool(force_rescreen),
    )
    return str(row["job_id"])


async def get_latest_refresh_run(conn) -> Optional[Dict[str, Any]]:
    row = await conn.fetchrow(
        """
        SELECT *
        FROM watchlist_refresh_runs
        ORDER BY ran_at DESC
        LIMIT 1
        """
    )
    return _to_json_safe(dict(row)) if row else None


async def get_latest_uk_hash(conn) -> Dict[str, Optional[str]]:
    row = await conn.fetchrow(
        """
        SELECT refresh_run_id, uk_hash
        FROM watchlist_refresh_runs
        ORDER BY ran_at DESC
        LIMIT 1
        """
    )
    if not row:
        return {"refresh_run_id": None, "uk_hash": None}
    return {"refresh_run_id": str(row["refresh_run_id"]), "uk_hash": row["uk_hash"]}


async def create_refresh_run(
    conn,
    *,
    include_peps: bool,
    postgres_synced: bool,
    sanctions_rows: int,
    peps_rows: int,
    uk_hash: str,
    prev_uk_hash: Optional[str],
    uk_changed: bool,
    uk_row_count: int,
    delta_added: int,
    delta_removed: int,
    delta_changed: int,
) -> str:
    row = await conn.fetchrow(
        """
        INSERT INTO watchlist_refresh_runs (
            include_peps, postgres_synced, sanctions_rows, peps_rows,
            uk_hash, prev_uk_hash, uk_changed, uk_row_count,
            delta_added, delta_removed, delta_changed
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        RETURNING refresh_run_id
        """,
        include_peps,
        postgres_synced,
        int(sanctions_rows or 0),
        int(peps_rows or 0),
        uk_hash or "",
        prev_uk_hash,
        bool(uk_changed),
        int(uk_row_count or 0),
        int(delta_added or 0),
        int(delta_removed or 0),
        int(delta_changed or 0),
    )
    return str(row["refresh_run_id"])


async def finalize_refresh_run(
    conn,
    *,
    refresh_run_id: str,
    candidate_count: int,
    queued_count: int,
    already_pending_count: int,
    reused_count: int,
    failed_count: int,
) -> None:
    await conn.execute(
        """
        UPDATE watchlist_refresh_runs
        SET candidate_count = $2,
            queued_count = $3,
            already_pending_count = $4,
            reused_count = $5,
            failed_count = $6
        WHERE refresh_run_id = $1::uuid
        """,
        refresh_run_id,
        int(candidate_count or 0),
        int(queued_count or 0),
        int(already_pending_count or 0),
        int(reused_count or 0),
        int(failed_count or 0),
    )


async def replace_uk_snapshot_entries(
    conn,
    *,
    refresh_run_id: str,
    entries: List[Dict[str, str]],
) -> None:
    if not entries:
        return
    await conn.executemany(
        """
        INSERT INTO watchlist_uk_snapshot_entries (
            refresh_run_id, fingerprint, entity_id, name_norm, birth_date, dataset, regime
        )
        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7)
        ON CONFLICT DO NOTHING
        """,
        [
            (
                refresh_run_id,
                e.get("fingerprint"),
                e.get("entity_id") or None,
                e.get("name_norm") or "",
                e.get("birth_date") or None,
                e.get("dataset") or None,
                e.get("regime") or None,
            )
            for e in entries
        ],
    )


async def get_uk_snapshot_entries(conn, refresh_run_id: str) -> List[Dict[str, str]]:
    rows = await conn.fetch(
        """
        SELECT fingerprint, entity_id, name_norm, birth_date, dataset, regime
        FROM watchlist_uk_snapshot_entries
        WHERE refresh_run_id = $1::uuid
        """,
        refresh_run_id,
    )
    return [dict(r) for r in rows]


async def shortlist_screened_entities_by_terms(
    conn,
    *,
    terms: List[str],
    max_candidates: int = 10000,
) -> List[Dict[str, Any]]:
    cleaned = sorted({t.strip().lower() for t in terms if t and len(t.strip()) >= 4})
    if not cleaned:
        return []
    max_candidates = max(1, min(500000, int(max_candidates)))
    rows = await conn.fetch(
        """
        WITH t AS (
            SELECT UNNEST($1::text[]) AS term
        )
        SELECT DISTINCT s.entity_key, s.display_name, s.date_of_birth, s.entity_type
        FROM screened_entities s
        JOIN t ON s.normalized_name ILIKE ('%' || t.term || '%')
        ORDER BY s.entity_key
        LIMIT $2
        """,
        cleaned,
        max_candidates,
    )
    return [dict(r) for r in rows]


async def mark_manual_overrides_stale(conn, *, latest_uk_hash: str) -> int:
    result = await conn.execute(
        """
        UPDATE screened_entities
        SET manual_override_stale = TRUE,
            updated_at = NOW()
        WHERE manual_override_uk_hash IS NOT NULL
          AND COALESCE(manual_override_uk_hash, '') <> COALESCE($1, '')
          AND manual_override_stale = FALSE
        """,
        latest_uk_hash or "",
    )
    try:
        return int(result.split()[-1]) if result else 0
    except (ValueError, IndexError):
        return 0


async def get_refresh_run_summary(conn, *, limit: int = 14) -> Dict[str, Any]:
    limit = max(1, min(90, int(limit)))
    latest = await conn.fetchrow(
        """
        SELECT *
        FROM watchlist_refresh_runs
        ORDER BY ran_at DESC
        LIMIT 1
        """
    )
    runs = await conn.fetch(
        """
        SELECT *
        FROM watchlist_refresh_runs
        ORDER BY ran_at DESC
        LIMIT $1
        """,
        limit,
    )
    latest_id = str(latest["refresh_run_id"]) if latest else None
    transitions: List[Dict[str, Any]] = []
    if latest_id:
        trows = await conn.fetch(
            """
            SELECT COALESCE(transition, 'unknown') AS transition, COUNT(*)::int AS n
            FROM screening_jobs
            WHERE refresh_run_id = $1::uuid
              AND reason = 'uk_delta_rescreen'
            GROUP BY COALESCE(transition, 'unknown')
            ORDER BY n DESC
            """,
            latest_id,
        )
        transitions = [dict(r) for r in trows]
    return {
        "latest": _to_json_safe(dict(latest)) if latest else None,
        "runs": _to_json_safe([dict(r) for r in runs]),
        "latest_transitions": _to_json_safe(transitions),
    }


async def search_screened_entities(
    conn,
    name: Optional[str] = None,
    entity_key: Optional[str] = None,
    business_reference: Optional[str] = None,
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
    if business_reference and business_reference.strip():
        n += 1
        conditions.append(f"business_reference = ${n}")
        args.append(business_reference.strip())
    if not conditions:
        return []
    limit = max(1, min(100, limit))
    offset = max(0, offset)
    args.extend([limit, offset])
    where_sql = " AND ".join(conditions)
    query = f"""
        SELECT entity_key, display_name, normalized_name, date_of_birth, entity_type,
               last_screened_at, screening_valid_until, status, risk_level, confidence, score,
               uk_sanctions_flag, pep_flag, result_json, last_requestor, business_reference, reason_for_check, updated_at
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
        "SELECT status, entity_key, error_message, reason, previous_status, result_status, transition FROM screening_jobs WHERE job_id = $1",
        job_id,
    )
    if row is None:
        return None
    out = {"status": row["status"], "job_id": job_id, "entity_key": row["entity_key"]}
    out["reason"] = row["reason"]
    out["previous_status"] = row["previous_status"]
    out["result_status"] = row["result_status"]
    out["transition"] = row["transition"]
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


async def mark_false_positive(
    conn,
    *,
    entity_key: str,
    actor: str,
    reason: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Mark an existing screening record as manually cleared (false positive).
    Keeps an audit trail inside result_json["Manual Override"].
    """
    row = await conn.fetchrow(
        """
        SELECT result_json
        FROM screened_entities
        WHERE entity_key = $1
        """,
        entity_key,
    )
    if row is None:
        return None

    rj = row["result_json"]
    if isinstance(rj, str):
        result = json.loads(rj)
    elif isinstance(rj, dict):
        result = dict(rj)
    else:
        result = dict(rj) if hasattr(rj, "items") else {}

    previous_summary = dict(result.get("Check Summary") or {})
    latest_run = await conn.fetchrow(
        """
        SELECT refresh_run_id, uk_hash
        FROM watchlist_refresh_runs
        ORDER BY ran_at DESC
        LIMIT 1
        """
    )
    latest_hash = (latest_run["uk_hash"] if latest_run else None) or None
    latest_run_id = str(latest_run["refresh_run_id"]) if latest_run else None
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    valid_until = now + timedelta(days=365)

    result["Manual Override"] = {
        "type": "false_positive_clear",
        "actor": actor,
        "reason": (reason or "").strip() or None,
        "overridden_at": now.isoformat(),
        "previous_status": previous_summary.get("Status"),
        "previous_risk_level": result.get("Risk Level"),
        "previous_score": result.get("Score"),
        "previous_sanctions_name": result.get("Sanctions Name"),
    }
    result["Sanctions Name"] = None
    result["Birth Date"] = None
    result["Regime"] = None
    result["Is Sanctioned"] = False
    result["Is PEP"] = False
    result["Match Found"] = False
    result["Risk Level"] = "Cleared"
    result["Confidence"] = "Manual Review"
    result["Score"] = 0
    result["Check Summary"] = {
        "Status": "Cleared - False Positive",
        "Source": f"{previous_summary.get('Source') or 'Manual Review'}; Manual override",
        "Date": now_str,
    }

    await conn.execute(
        """
        UPDATE screened_entities
        SET status = $2,
            risk_level = $3,
            confidence = $4,
            score = $5,
            uk_sanctions_flag = $6,
            pep_flag = $7,
            result_json = $8::jsonb,
            last_requestor = $9,
            last_screened_at = $10,
            screening_valid_until = $11,
            updated_at = $10,
            screened_against_uk_hash = $12,
            screened_against_refresh_run_id = $13::uuid,
            manual_override_uk_hash = $12,
            manual_override_stale = FALSE
        WHERE entity_key = $1
        """,
        entity_key,
        "Cleared - False Positive",
        "Cleared",
        "Manual Review",
        0.0,
        False,
        False,
        json.dumps(result),
        actor,
        now,
        valid_until,
        latest_hash,
        latest_run_id,
    )
    return result


async def list_screening_jobs(
    conn,
    *,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    List screening jobs for operational monitoring.
    Sorted newest-first by created_at.
    """
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    valid_status = {"pending", "running", "completed", "failed"}
    where = ""
    args: List[Any] = []
    if status and status in valid_status:
        where = "WHERE status = $1"
        args.append(status)
    args.extend([limit, offset])

    idx_limit = len(args) - 1
    idx_offset = len(args)
    rows = await conn.fetch(
        f"""
        SELECT
            j.job_id,
            j.entity_key,
            j.name,
            j.date_of_birth,
            j.entity_type,
            j.requestor,
            j.reason_for_check,
            j.reason,
            j.refresh_run_id,
            j.force_rescreen,
            j.status,
            j.previous_status,
            j.result_status,
            j.transition,
            j.created_at,
            j.started_at,
            j.finished_at,
            j.error_message,
            s.status AS screening_status,
            s.risk_level AS screening_risk_level
        FROM screening_jobs j
        LEFT JOIN screened_entities s
          ON s.entity_key = j.entity_key
        {where}
        ORDER BY j.created_at DESC
        LIMIT ${idx_limit} OFFSET ${idx_offset}
        """,
        *args,
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        for key in ("created_at", "started_at", "finished_at"):
            if d.get(key) is not None:
                d[key] = d[key].isoformat()
        out.append(_to_json_safe(d))
    return out
