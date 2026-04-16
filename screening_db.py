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
_REVIEW_STATUS_ALLOWED = frozenset({"IN_REVIEW", "COMPLETED"})
_REVIEW_OUTCOME_ALLOWED = frozenset({
    "False Positive - Proceeded",
    "False Positive - Payment Released",
    "Confirmed Match - Payment Blocked",
    "Confirmed Match - Escalated to Compliance",
    "Pending External Review",
    "Cancelled / No Action Required",
})
_AI_TRIAGE_STATUS_ALLOWED = frozenset({"PENDING_REVIEW", "APPROVED", "REJECTED", "SUPERSEDED", "ERROR"})
_AI_TRIAGE_ACTION_ALLOWED = frozenset({"CLEAR", "INVESTIGATE", "UNSURE"})


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
            country_input    TEXT,
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
            review_status TEXT,
            review_claimed_by TEXT,
            review_claimed_at TIMESTAMPTZ,
            review_outcome TEXT,
            review_notes TEXT,
            review_completed_by TEXT,
            review_completed_at TIMESTAMPTZ,
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
            country         TEXT,
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
        CREATE TABLE IF NOT EXISTS ai_triage_runs (
            run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trigger_type TEXT NOT NULL,
            triggered_by TEXT,
            llm_runtime TEXT NOT NULL,
            llm_model TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            selected_count INTEGER NOT NULL DEFAULT 0,
            created_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            superseded_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_triage_recommendations (
            triage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID REFERENCES ai_triage_runs(run_id) ON DELETE SET NULL,
            entity_key TEXT NOT NULL,
            screening_state_hash TEXT NOT NULL,
            submitted_name TEXT NOT NULL,
            submitted_entity_type TEXT,
            matched_name TEXT,
            matched_entity_type TEXT,
            matched_birth_date TEXT,
            matched_country TEXT,
            source_label TEXT,
            screening_status TEXT,
            screening_risk_level TEXT,
            screening_score NUMERIC(5,2),
            llm_runtime TEXT NOT NULL,
            llm_model TEXT NOT NULL,
            raw_recommended_action TEXT NOT NULL,
            effective_recommended_action TEXT NOT NULL,
            ai_confidence_raw NUMERIC(5,4),
            ai_confidence_band TEXT,
            rationale_short TEXT,
            explanation_json JSONB,
            raw_output_json JSONB,
            result_snapshot_json JSONB NOT NULL,
            guardrail_overridden BOOLEAN NOT NULL DEFAULT FALSE,
            guardrail_reasons JSONB,
            status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
            human_decision TEXT,
            reviewer TEXT,
            reviewed_at TIMESTAMPTZ,
            reviewer_notes TEXT,
            final_screening_outcome TEXT,
            agreement_indicator TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_triage_recommendations_status
        ON ai_triage_recommendations (status, created_at DESC)
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_triage_recommendations_entity_key
        ON ai_triage_recommendations (entity_key, created_at DESC)
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
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS country_input TEXT
    """)
    await conn.execute("""
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_status TEXT
    """)
    await conn.execute("""
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_claimed_by TEXT
    """)
    await conn.execute("""
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_claimed_at TIMESTAMPTZ
    """)
    await conn.execute("""
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_outcome TEXT
    """)
    await conn.execute("""
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_notes TEXT
    """)
    await conn.execute("""
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_completed_by TEXT
    """)
    await conn.execute("""
        ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_completed_at TIMESTAMPTZ
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
        ADD COLUMN IF NOT EXISTS country TEXT
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


async def get_screened_entity_identity(conn, entity_key: str) -> Optional[Dict[str, Any]]:
    """Return minimal identity fields for a screened entity, or None if not found."""
    row = await conn.fetchrow(
        """
        SELECT entity_key, normalized_name, entity_type
        FROM screened_entities
        WHERE entity_key = $1
        LIMIT 1
        """,
        entity_key,
    )
    if row is None:
        return None
    return dict(row)


async def upsert_screening(
    conn,
    entity_key: str,
    display_name: str,
    normalized_name: str,
    date_of_birth: Optional[str],
    country_input: Optional[str],
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
    country_input_clean = (country_input or "").strip() or None
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
            entity_key, display_name, normalized_name, date_of_birth, country_input, entity_type,
            last_screened_at, screening_valid_until,
            status, risk_level, confidence, score, uk_sanctions_flag, pep_flag,
            result_json, last_requestor, business_reference, reason_for_check, updated_at,
            screened_against_uk_hash, screened_against_refresh_run_id,
            manual_override_uk_hash, manual_override_stale
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21::uuid, NULL, FALSE)
        ON CONFLICT (entity_key) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            normalized_name = EXCLUDED.normalized_name,
            date_of_birth = EXCLUDED.date_of_birth,
            country_input = EXCLUDED.country_input,
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
        country_input_clean,
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
    country_input: Optional[str],
) -> None:
    """Update request metadata when a cached screening is reused."""
    business_reference_clean = (business_reference or "").strip()
    reason_for_check_clean = (reason_for_check or "").strip()
    country_input_clean = (country_input or "").strip() or None
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
            country_input = $5,
            updated_at = NOW()
        WHERE entity_key = $1
        """,
        entity_key,
        requestor,
        business_reference_clean,
        reason_for_check_clean,
        country_input_clean,
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
    country: Optional[str],
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
            entity_key, name, date_of_birth, country, entity_type, requestor, business_reference, reason_for_check, reason, refresh_run_id, force_rescreen, status
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::uuid, $11, 'pending')
        RETURNING job_id
        """,
        entity_key,
        name,
        date_of_birth,
        (country or "").strip() or None,
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


async def create_ai_triage_run(
    conn,
    *,
    trigger_type: str,
    triggered_by: Optional[str],
    llm_runtime: str,
    llm_model: str,
    selected_count: int = 0,
) -> str:
    row = await conn.fetchrow(
        """
        INSERT INTO ai_triage_runs (
            trigger_type, triggered_by, llm_runtime, llm_model, selected_count, status
        )
        VALUES ($1, $2, $3, $4, $5, 'running')
        RETURNING run_id
        """,
        (trigger_type or "manual").strip() or "manual",
        (triggered_by or "").strip() or None,
        (llm_runtime or "ollama").strip() or "ollama",
        (llm_model or "").strip() or "unknown",
        max(0, int(selected_count or 0)),
    )
    return str(row["run_id"])


async def update_ai_triage_run_selected(conn, run_id: str, selected_count: int) -> None:
    await conn.execute(
        """
        UPDATE ai_triage_runs
        SET selected_count = $2
        WHERE run_id = $1::uuid
        """,
        run_id,
        max(0, int(selected_count or 0)),
    )


async def finalize_ai_triage_run(
    conn,
    *,
    run_id: str,
    status: str,
    created_count: int,
    skipped_count: int,
    superseded_count: int,
    error_count: int,
    error_message: Optional[str] = None,
) -> None:
    await conn.execute(
        """
        UPDATE ai_triage_runs
        SET status = $2,
            created_count = $3,
            skipped_count = $4,
            superseded_count = $5,
            error_count = $6,
            error_message = $7,
            finished_at = NOW()
        WHERE run_id = $1::uuid
        """,
        run_id,
        (status or "completed").strip() or "completed",
        max(0, int(created_count or 0)),
        max(0, int(skipped_count or 0)),
        max(0, int(superseded_count or 0)),
        max(0, int(error_count or 0)),
        (error_message or "").strip() or None,
    )


async def list_ai_triage_runs(conn, *, limit: int = 20) -> List[Dict[str, Any]]:
    limit = max(1, min(100, int(limit)))
    rows = await conn.fetch(
        """
        SELECT *
        FROM ai_triage_runs
        ORDER BY started_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [_to_json_safe(dict(r)) for r in rows]


async def get_latest_ai_triage_run(conn) -> Optional[Dict[str, Any]]:
    row = await conn.fetchrow(
        """
        SELECT *
        FROM ai_triage_runs
        ORDER BY started_at DESC
        LIMIT 1
        """
    )
    return _to_json_safe(dict(row)) if row else None


def _ai_triage_result_snapshot(result_json: Dict[str, Any]) -> Dict[str, Any]:
    result = result_json if isinstance(result_json, dict) else {}
    summary = result.get("Check Summary") if isinstance(result.get("Check Summary"), dict) else {}
    return {
        "Sanctions Name": result.get("Sanctions Name"),
        "Birth Date": result.get("Birth Date"),
        "Regime": result.get("Regime"),
        "Score": result.get("Score"),
        "Risk Level": result.get("Risk Level"),
        "Confidence": result.get("Confidence"),
        "Check Summary": {
            "Status": summary.get("Status"),
            "Source": summary.get("Source"),
            "Date": summary.get("Date"),
        },
        "Top Matches": result.get("Top Matches") or [],
        "Input Classification": result.get("Input Classification") or {},
    }


async def list_ai_triage_candidates(conn, *, limit: int = 25) -> List[Dict[str, Any]]:
    limit = max(1, min(250, int(limit)))
    rows = await conn.fetch(
        """
        SELECT
            entity_key,
            display_name,
            entity_type,
            date_of_birth,
            country_input,
            status,
            risk_level,
            score,
            result_json,
            review_status
        FROM screened_entities
        WHERE status NOT ILIKE 'Cleared%'
          AND (
            status ILIKE 'Fail Sanction%'
            OR status ILIKE 'Fail PEP%'
            OR COALESCE((result_json->>'Is Sanctioned')::boolean, FALSE) = TRUE
            OR COALESCE((result_json->>'Is PEP')::boolean, FALSE) = TRUE
          )
          AND COALESCE(review_status, 'UNREVIEWED') <> 'COMPLETED'
        ORDER BY
          CASE WHEN review_status = 'IN_REVIEW' THEN 0 ELSE 1 END,
          last_screened_at ASC
        LIMIT $1
        """,
        limit,
    )
    out: List[Dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        result_json = d.get("result_json")
        if isinstance(result_json, str):
            result_json = json.loads(result_json)
        elif not isinstance(result_json, dict):
            result_json = dict(result_json) if hasattr(result_json, "items") else {}
        summary = result_json.get("Check Summary") if isinstance(result_json.get("Check Summary"), dict) else {}
        d["result_json"] = result_json
        d["date_of_birth"] = d["date_of_birth"].isoformat() if d.get("date_of_birth") else None
        d["score"] = float(d.get("score") or 0)
        d["matched_name"] = result_json.get("Sanctions Name")
        d["matched_birth_date"] = result_json.get("Birth Date")
        d["matched_country"] = (
            result_json.get("Matched Country")
            or result_json.get("Country")
            or ((result_json.get("Matched Entity") or {}).get("country") if isinstance(result_json.get("Matched Entity"), dict) else None)
        )
        inferred = (result_json.get("Input Classification") or {}).get("inferred_as") if isinstance(result_json.get("Input Classification"), dict) else None
        d["matched_entity_type"] = inferred or d.get("entity_type")
        d["source_label"] = summary.get("Source")
        d["result_snapshot_json"] = _ai_triage_result_snapshot(result_json)
        out.append(_to_json_safe(d))
    return out


async def prepare_ai_triage_recommendation(
    conn,
    *,
    entity_key: str,
    screening_state_hash: str,
) -> str:
    pending_same = await conn.fetchrow(
        """
        SELECT triage_id
        FROM ai_triage_recommendations
        WHERE entity_key = $1
          AND status = 'PENDING_REVIEW'
          AND screening_state_hash = $2
        LIMIT 1
        """,
        entity_key,
        screening_state_hash,
    )
    if pending_same is not None:
        return "skip"

    result = await conn.execute(
        """
        UPDATE ai_triage_recommendations
        SET status = 'SUPERSEDED',
            updated_at = NOW()
        WHERE entity_key = $1
          AND status = 'PENDING_REVIEW'
          AND screening_state_hash <> $2
        """,
        entity_key,
        screening_state_hash,
    )
    try:
        updated = int(result.split()[-1]) if result else 0
    except (ValueError, IndexError):
        updated = 0
    return "superseded" if updated > 0 else "new"


async def insert_ai_triage_recommendation(
    conn,
    *,
    run_id: str,
    entity_key: str,
    screening_state_hash: str,
    candidate: Dict[str, Any],
    triage_result: Dict[str, Any],
) -> str:
    raw_action = str(triage_result.get("raw_recommended_action") or "UNSURE").strip().upper()
    effective_action = str(triage_result.get("effective_recommended_action") or raw_action or "UNSURE").strip().upper()
    if raw_action not in _AI_TRIAGE_ACTION_ALLOWED:
        raw_action = "UNSURE"
    if effective_action not in _AI_TRIAGE_ACTION_ALLOWED:
        effective_action = "UNSURE"
    row = await conn.fetchrow(
        """
        INSERT INTO ai_triage_recommendations (
            run_id,
            entity_key,
            screening_state_hash,
            submitted_name,
            submitted_entity_type,
            matched_name,
            matched_entity_type,
            matched_birth_date,
            matched_country,
            source_label,
            screening_status,
            screening_risk_level,
            screening_score,
            llm_runtime,
            llm_model,
            raw_recommended_action,
            effective_recommended_action,
            ai_confidence_raw,
            ai_confidence_band,
            rationale_short,
            explanation_json,
            raw_output_json,
            result_snapshot_json,
            guardrail_overridden,
            guardrail_reasons,
            status
        )
        VALUES (
            $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
            $14, $15, $16, $17, $18, $19, $20, $21::jsonb, $22::jsonb, $23::jsonb,
            $24, $25::jsonb, 'PENDING_REVIEW'
        )
        RETURNING triage_id
        """,
        run_id,
        entity_key,
        screening_state_hash,
        candidate.get("display_name") or "",
        candidate.get("entity_type") or None,
        candidate.get("matched_name") or None,
        candidate.get("matched_entity_type") or None,
        candidate.get("matched_birth_date") or None,
        candidate.get("matched_country") or None,
        candidate.get("source_label") or None,
        candidate.get("status") or None,
        candidate.get("risk_level") or None,
        float(candidate.get("score") or 0),
        triage_result.get("llm_runtime") or "ollama",
        triage_result.get("llm_model") or "unknown",
        raw_action,
        effective_action,
        float(triage_result.get("ai_confidence_raw") or 0),
        triage_result.get("ai_confidence_band") or None,
        (triage_result.get("rationale_short") or "")[:500],
        json.dumps(triage_result.get("explanation_json") or {}),
        json.dumps(triage_result.get("raw_output_json") or {}),
        json.dumps(candidate.get("result_snapshot_json") or candidate.get("result_json") or {}),
        bool(triage_result.get("guardrail_overridden")),
        json.dumps(triage_result.get("guardrail_reasons") or []),
    )
    return str(row["triage_id"])


async def insert_ai_triage_error(
    conn,
    *,
    run_id: str,
    entity_key: str,
    screening_state_hash: str,
    candidate: Dict[str, Any],
    error_message: str,
    llm_runtime: str,
    llm_model: str,
) -> str:
    row = await conn.fetchrow(
        """
        INSERT INTO ai_triage_recommendations (
            run_id,
            entity_key,
            screening_state_hash,
            submitted_name,
            submitted_entity_type,
            matched_name,
            matched_entity_type,
            matched_birth_date,
            matched_country,
            source_label,
            screening_status,
            screening_risk_level,
            screening_score,
            llm_runtime,
            llm_model,
            raw_recommended_action,
            effective_recommended_action,
            ai_confidence_raw,
            ai_confidence_band,
            rationale_short,
            explanation_json,
            raw_output_json,
            result_snapshot_json,
            guardrail_overridden,
            guardrail_reasons,
            status
        )
        VALUES (
            $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
            $14, $15, 'UNSURE', 'UNSURE', 0, '<0.70', $16, '{}'::jsonb, $17::jsonb, $18::jsonb,
            FALSE, '[]'::jsonb, 'ERROR'
        )
        RETURNING triage_id
        """,
        run_id,
        entity_key,
        screening_state_hash,
        candidate.get("display_name") or "",
        candidate.get("entity_type") or None,
        candidate.get("matched_name") or None,
        candidate.get("matched_entity_type") or None,
        candidate.get("matched_birth_date") or None,
        candidate.get("matched_country") or None,
        candidate.get("source_label") or None,
        candidate.get("status") or None,
        candidate.get("risk_level") or None,
        float(candidate.get("score") or 0),
        llm_runtime,
        llm_model,
        (error_message or "AI triage failed")[:500],
        json.dumps({"error": error_message}),
        json.dumps(candidate.get("result_snapshot_json") or candidate.get("result_json") or {}),
    )
    return str(row["triage_id"])


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


async def get_dashboard_summary(conn) -> Dict[str, Any]:
    """
    High-level operational dashboard summary.
    """
    overview = await conn.fetchrow(
        """
        SELECT
            COUNT(*) FILTER (
                WHERE risk_level ILIKE 'High%'
                  AND COALESCE(review_status, 'UNREVIEWED') <> 'COMPLETED'
                  AND status NOT ILIKE 'Cleared%'
            )::int AS open_high_risk_reviews,
            COUNT(*) FILTER (
                WHERE review_status = 'IN_REVIEW'
                  AND review_claimed_at <= NOW() - INTERVAL '24 hours'
            )::int AS aged_reviews_over_24h,
            COUNT(*) FILTER (
                WHERE review_status = 'IN_REVIEW'
                  AND review_claimed_at <= NOW() - INTERVAL '72 hours'
            )::int AS aged_reviews_over_72h,
            COUNT(*) FILTER (
                WHERE last_screened_at >= NOW() - INTERVAL '24 hours'
                  AND (uk_sanctions_flag = TRUE OR pep_flag = TRUE)
            )::int AS new_matches_24h,
            COUNT(*) FILTER (
                WHERE last_screened_at >= NOW() - INTERVAL '7 days'
                  AND (uk_sanctions_flag = TRUE OR pep_flag = TRUE)
            )::int AS new_matches_7d,
            COUNT(*) FILTER (
                WHERE review_claimed_at >= DATE_TRUNC('day', NOW())
            )::int AS claimed_today,
            COUNT(*) FILTER (
                WHERE review_completed_at >= DATE_TRUNC('day', NOW())
            )::int AS completed_today
        FROM screened_entities
        """
    )
    outcome_rows = await conn.fetch(
        """
        SELECT review_outcome AS outcome, COUNT(*)::int AS count
        FROM screened_entities
        WHERE review_status = 'COMPLETED'
          AND review_completed_at >= NOW() - INTERVAL '30 days'
          AND review_outcome IS NOT NULL
        GROUP BY review_outcome
        ORDER BY count DESC, review_outcome ASC
        """
    )
    latest_refresh = await conn.fetchrow(
        """
        SELECT
            refresh_run_id,
            ran_at,
            uk_changed,
            uk_row_count,
            delta_added,
            delta_removed,
            delta_changed,
            candidate_count,
            queued_count,
            already_pending_count,
            failed_count
        FROM watchlist_refresh_runs
        ORDER BY ran_at DESC
        LIMIT 1
        """
    )
    pending_ai = await conn.fetchrow(
        """
        SELECT COUNT(*)::int AS n
        FROM ai_triage_recommendations
        WHERE status = 'PENDING_REVIEW'
        """
    )
    latest_ai_run = await conn.fetchrow(
        """
        SELECT *
        FROM ai_triage_runs
        ORDER BY started_at DESC
        LIMIT 1
        """
    )

    claimed_today = int((overview or {}).get("claimed_today") or 0)
    completed_today = int((overview or {}).get("completed_today") or 0)
    completion_rate = round((completed_today / claimed_today) * 100, 1) if claimed_today > 0 else 0.0

    last_refresh_at = latest_refresh.get("ran_at") if latest_refresh else None
    hours_since_refresh = None
    if last_refresh_at is not None and isinstance(last_refresh_at, datetime):
        hours_since_refresh = round((datetime.now(timezone.utc) - last_refresh_at).total_seconds() / 3600, 1)

    return _to_json_safe(
        {
            "risk": {
                "open_high_risk_reviews": int((overview or {}).get("open_high_risk_reviews") or 0),
                "aged_reviews_over_24h": int((overview or {}).get("aged_reviews_over_24h") or 0),
                "aged_reviews_over_72h": int((overview or {}).get("aged_reviews_over_72h") or 0),
            },
            "matches": {
                "new_matches_24h": int((overview or {}).get("new_matches_24h") or 0),
                "new_matches_7d": int((overview or {}).get("new_matches_7d") or 0),
            },
            "throughput_today": {
                "claimed": claimed_today,
                "completed": completed_today,
                "completion_rate_percent": completion_rate,
            },
            "outcome_mix_30d": [dict(r) for r in outcome_rows],
            "data_freshness": {
                "last_refresh_at": last_refresh_at,
                "hours_since_refresh": hours_since_refresh,
                "latest_refresh": dict(latest_refresh) if latest_refresh else None,
            },
            "ai_triage": {
                "pending_recommendations": int((pending_ai or {}).get("n") or 0),
                "latest_run": dict(latest_ai_run) if latest_ai_run else None,
            },
        }
    )


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
               uk_sanctions_flag, pep_flag, result_json, last_requestor, business_reference, reason_for_check, country_input,
               review_status, review_claimed_by, review_claimed_at, review_outcome, review_notes, review_completed_by, review_completed_at,
               updated_at
        FROM screened_entities
        WHERE {where_sql}
        ORDER BY last_screened_at DESC
        LIMIT ${n + 1} OFFSET ${n + 2}
    """
    rows = await conn.fetch(query, *args)
    out = []
    for r in rows:
        d = dict(r)
        for key in (
            "last_screened_at",
            "screening_valid_until",
            "updated_at",
            "review_claimed_at",
            "review_completed_at",
        ):
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


async def export_screened_entities_for_mi(
    conn,
    *,
    screened_from: Optional[str] = None,
    screened_to: Optional[str] = None,
    review_status: Optional[str] = None,
    include_cleared: bool = True,
) -> List[Dict[str, Any]]:
    conditions: List[str] = []
    args: List[Any] = []
    n = 0

    if not include_cleared:
        conditions.append("status NOT ILIKE 'Cleared%'")
    if screened_from and screened_from.strip():
        n += 1
        conditions.append(f"last_screened_at >= ${n}::timestamptz")
        args.append(screened_from.strip())
    if screened_to and screened_to.strip():
        n += 1
        conditions.append(f"last_screened_at < ${n}::timestamptz")
        args.append(screened_to.strip())
    review_status_clean = (review_status or "").strip().upper()
    if review_status_clean:
        if review_status_clean == "UNREVIEWED":
            conditions.append("review_status IS NULL")
        elif review_status_clean in _REVIEW_STATUS_ALLOWED:
            n += 1
            conditions.append(f"review_status = ${n}")
            args.append(review_status_clean)
        else:
            raise ValueError("review_status must be UNREVIEWED, IN_REVIEW, or COMPLETED")

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = await conn.fetch(
        f"""
        SELECT
            entity_key,
            display_name,
            normalized_name,
            date_of_birth,
            country_input,
            entity_type,
            last_screened_at,
            screening_valid_until,
            status,
            risk_level,
            confidence,
            score,
            uk_sanctions_flag,
            pep_flag,
            last_requestor,
            business_reference,
            reason_for_check,
            review_status,
            review_claimed_by,
            review_claimed_at,
            review_outcome,
            review_notes,
            review_completed_by,
            review_completed_at,
            updated_at,
            result_json->>'Sanctions Name' AS result_sanctions_name,
            result_json->>'Birth Date' AS result_birth_date,
            result_json->>'Regime' AS result_regime,
            COALESCE((result_json->>'Is Sanctioned')::boolean, FALSE) AS result_is_sanctioned,
            COALESCE((result_json->>'Is PEP')::boolean, FALSE) AS result_is_pep,
            COALESCE((result_json->>'Match Found')::boolean, FALSE) AS result_match_found,
            result_json->>'Risk Level' AS result_risk_level,
            result_json->>'Confidence' AS result_confidence,
            result_json->>'Score' AS result_score,
            result_json->'Check Summary'->>'Status' AS result_check_status,
            result_json->'Check Summary'->>'Source' AS result_check_source,
            result_json->'Check Summary'->>'Date' AS result_check_date,
            result_json->'Entity Type Checks'->'Person'->>'status' AS person_check_status,
            COALESCE((result_json->'Entity Type Checks'->'Person'->>'is_match')::boolean, FALSE) AS person_check_is_match,
            result_json->'Entity Type Checks'->'Person'->>'score' AS person_check_score,
            result_json->'Entity Type Checks'->'Organization'->>'status' AS organization_check_status,
            COALESCE((result_json->'Entity Type Checks'->'Organization'->>'is_match')::boolean, FALSE) AS organization_check_is_match,
            result_json->'Entity Type Checks'->'Organization'->>'score' AS organization_check_score,
            COALESCE((result_json->'PEP Check'->>'checked')::boolean, FALSE) AS pep_check_checked,
            result_json->'PEP Check'->>'status' AS pep_check_status,
            result_json->'PEP Check'->>'reason' AS pep_check_reason,
            result_json->'PEP Check'->>'message' AS pep_check_message,
            result_json->'Input Classification'->>'submitted_as' AS input_submitted_as,
            result_json->'Input Classification'->>'inferred_as' AS input_inferred_as,
            COALESCE((result_json->'Input Classification'->>'likely_misclassified')::boolean, FALSE) AS input_likely_misclassified,
            result_json->'Input Classification'->>'confidence' AS input_classification_confidence,
            result_json->'Input Classification'->'signals' AS input_classification_signals_json,
            result_json->'Top Matches' AS top_matches_json,
            result_json AS result_json
        FROM screened_entities
        {where_sql}
        ORDER BY last_screened_at DESC
        """,
        *args,
    )

    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        for key in (
            "last_screened_at",
            "screening_valid_until",
            "review_claimed_at",
            "review_completed_at",
            "updated_at",
        ):
            if d.get(key) is not None:
                d[key] = d[key].isoformat()
        if d.get("date_of_birth") is not None:
            d["date_of_birth"] = d["date_of_birth"].isoformat()
        if d.get("score") is not None:
            d["score"] = float(d["score"])
        for key in ("result_score", "person_check_score", "organization_check_score"):
            if d.get(key) not in (None, ""):
                try:
                    d[key] = float(d[key])
                except Exception:
                    pass
        for key in ("input_classification_signals_json", "top_matches_json", "result_json"):
            if d.get(key) is not None:
                d[key] = json.dumps(_to_json_safe(d[key]), ensure_ascii=True, sort_keys=True)
        out.append(_to_json_safe(d))
    return out


async def list_review_queue(
    conn,
    *,
    review_status: Optional[str] = None,
    business_reference: Optional[str] = None,
    reason_for_check: Optional[str] = None,
    include_cleared: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """
    Review queue for potential matches.
    By default excludes Cleared decisions unless include_cleared is explicitly true.
    """
    conditions: List[str] = []
    args: List[Any] = []
    n = 0
    if not include_cleared:
        conditions.append("status NOT ILIKE 'Cleared%'")
    review_status_clean = (review_status or "").strip().upper()
    if review_status_clean:
        if review_status_clean == "UNREVIEWED":
            conditions.append("review_status IS NULL")
        elif review_status_clean in _REVIEW_STATUS_ALLOWED:
            n += 1
            conditions.append(f"review_status = ${n}")
            args.append(review_status_clean)
        else:
            raise ValueError("review_status must be UNREVIEWED, IN_REVIEW, or COMPLETED")
    business_reference_clean = (business_reference or "").strip()
    if business_reference_clean:
        n += 1
        conditions.append(f"business_reference = ${n}")
        args.append(business_reference_clean)
    reason_for_check_clean = (reason_for_check or "").strip()
    if reason_for_check_clean:
        if reason_for_check_clean not in _REASON_FOR_CHECK_ALLOWED:
            raise ValueError("reason_for_check must be a valid enum value")
        n += 1
        conditions.append(f"reason_for_check = ${n}")
        args.append(reason_for_check_clean)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    args.extend([limit, offset])
    idx_limit = len(args) - 1
    idx_offset = len(args)
    rows = await conn.fetch(
        f"""
        SELECT
            display_name AS entity_name,
            entity_key,
            entity_type,
            date_of_birth,
            country_input,
            status AS decision,
            business_reference,
            reason_for_check,
            last_requestor AS screening_user,
            last_screened_at AS screening_timestamp,
            COALESCE(review_status, 'UNREVIEWED') AS review_status,
            review_claimed_by,
            result_json->'Input Classification'->>'inferred_as' AS inferred_entity_type,
            COALESCE((result_json->'Input Classification'->>'likely_misclassified')::boolean, FALSE) AS likely_misclassified
        FROM screened_entities
        {where_sql}
        ORDER BY last_screened_at DESC
        LIMIT ${idx_limit} OFFSET ${idx_offset}
        """,
        *args,
    )
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if d.get("screening_timestamp") is not None:
            d["screening_timestamp"] = d["screening_timestamp"].isoformat()
        out.append(_to_json_safe(d))
    return out


async def claim_review(
    conn,
    *,
    entity_key: str,
    claimed_by: str,
) -> Dict[str, Any]:
    """
    Claim an unreviewed match for review.
    Only rows with review_status IS NULL can be claimed.
    """
    row = await conn.fetchrow(
        """
        UPDATE screened_entities
        SET review_status = 'IN_REVIEW',
            review_claimed_by = $2,
            review_claimed_at = NOW(),
            updated_at = NOW()
        WHERE entity_key = $1
          AND review_status IS NULL
          AND status NOT ILIKE 'Cleared%'
        RETURNING entity_key, display_name, status, business_reference, reason_for_check,
                  review_status, review_claimed_by, review_claimed_at
        """,
        entity_key,
        claimed_by,
    )
    if row is not None:
        d = dict(row)
        if d.get("review_claimed_at") is not None:
            d["review_claimed_at"] = d["review_claimed_at"].isoformat()
        return {"status": "ok", "item": _to_json_safe(d)}
    existing = await conn.fetchrow(
        """
        SELECT entity_key, status, review_status, business_reference, reason_for_check
        FROM screened_entities
        WHERE entity_key = $1
        """,
        entity_key,
    )
    if existing is None:
        return {"status": "error", "error": "not_found"}
    if str(existing.get("status") or "").lower().startswith("cleared"):
        return {"status": "error", "error": "not_reviewable"}
    return {"status": "error", "error": "not_unreviewed", "review_status": existing.get("review_status")}


async def complete_review(
    conn,
    *,
    entity_key: str,
    completed_by: str,
    review_outcome: str,
    review_notes: str,
) -> Dict[str, Any]:
    """
    Complete an in-review match.
    Only rows with review_status='IN_REVIEW' can be completed.
    """
    review_outcome_clean = (review_outcome or "").strip()
    review_notes_clean = (review_notes or "").strip()
    if review_outcome_clean not in _REVIEW_OUTCOME_ALLOWED:
        raise ValueError("review_outcome must be a valid enum value")
    if len(review_notes_clean) < 10:
        raise ValueError("review_notes must be at least 10 characters")
    row = await conn.fetchrow(
        """
        UPDATE screened_entities
        SET review_status = 'COMPLETED',
            review_outcome = $3,
            review_notes = $4,
            review_completed_by = $2,
            review_completed_at = NOW(),
            updated_at = NOW()
        WHERE entity_key = $1
          AND review_status = 'IN_REVIEW'
        RETURNING entity_key, display_name, status, business_reference, reason_for_check,
                  review_status, review_outcome, review_notes, review_completed_by, review_completed_at
        """,
        entity_key,
        completed_by,
        review_outcome_clean,
        review_notes_clean,
    )
    if row is not None:
        d = dict(row)
        if d.get("review_completed_at") is not None:
            d["review_completed_at"] = d["review_completed_at"].isoformat()
        return {"status": "ok", "item": _to_json_safe(d)}
    existing = await conn.fetchrow(
        """
        SELECT entity_key, review_status, business_reference, reason_for_check
        FROM screened_entities
        WHERE entity_key = $1
        """,
        entity_key,
    )
    if existing is None:
        return {"status": "error", "error": "not_found"}
    return {"status": "error", "error": "not_in_review", "review_status": existing.get("review_status")}


async def list_ai_triage_tasks(
    conn,
    *,
    status: Optional[str] = "PENDING_REVIEW",
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    limit = max(1, min(500, int(limit)))
    offset = max(0, int(offset))
    conditions: List[str] = []
    args: List[Any] = []
    n = 0
    status_clean = (status or "").strip().upper()
    if status_clean:
        if status_clean not in _AI_TRIAGE_STATUS_ALLOWED:
            raise ValueError("status must be one of PENDING_REVIEW, APPROVED, REJECTED, SUPERSEDED, or ERROR")
        n += 1
        conditions.append(f"r.status = ${n}")
        args.append(status_clean)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    args.extend([limit, offset])
    idx_limit = len(args) - 1
    idx_offset = len(args)
    rows = await conn.fetch(
        f"""
        SELECT
            r.triage_id,
            r.run_id,
            r.entity_key,
            r.submitted_name,
            r.submitted_entity_type,
            r.matched_name,
            r.matched_entity_type,
            r.source_label,
            r.screening_status,
            r.screening_risk_level,
            r.screening_score,
            r.raw_recommended_action,
            r.effective_recommended_action,
            r.ai_confidence_raw,
            r.ai_confidence_band,
            r.rationale_short,
            r.guardrail_overridden,
            r.guardrail_reasons,
            r.status,
            r.human_decision,
            r.reviewer,
            r.reviewed_at,
            r.final_screening_outcome,
            r.agreement_indicator,
            r.created_at,
            se.business_reference,
            se.reason_for_check
        FROM ai_triage_recommendations r
        LEFT JOIN screened_entities se
          ON se.entity_key = r.entity_key
        {where_sql}
        ORDER BY r.created_at DESC
        LIMIT ${idx_limit} OFFSET ${idx_offset}
        """,
        *args,
    )
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for key in ("explanation_json", "raw_output_json", "result_snapshot_json", "guardrail_reasons"):
            if key in item:
                item[key] = _decode_jsonish(item.get(key))
        out.append(_to_json_safe(item))
    return out


async def get_ai_triage_task(conn, *, triage_id: str) -> Optional[Dict[str, Any]]:
    row = await conn.fetchrow(
        """
        SELECT
            r.*,
            se.business_reference,
            se.reason_for_check,
            se.review_status AS screening_review_status
        FROM ai_triage_recommendations r
        LEFT JOIN screened_entities se
          ON se.entity_key = r.entity_key
        WHERE r.triage_id = $1::uuid
        LIMIT 1
        """,
        triage_id,
    )
    if row is None:
        return None
    item = dict(row)
    for key in ("explanation_json", "raw_output_json", "result_snapshot_json", "guardrail_reasons"):
        if key in item:
            item[key] = _decode_jsonish(item.get(key))
    return _to_json_safe(item)


async def approve_ai_triage_task(
    conn,
    *,
    triage_id: str,
    reviewer: str,
    reviewer_notes: Optional[str],
    final_screening_outcome: Optional[str],
) -> Optional[Dict[str, Any]]:
    row = await conn.fetchrow(
        """
        UPDATE ai_triage_recommendations
        SET status = 'APPROVED',
            human_decision = 'APPROVED',
            reviewer = $2,
            reviewed_at = NOW(),
            reviewer_notes = $3,
            final_screening_outcome = $4,
            agreement_indicator = 'AGREED',
            updated_at = NOW()
        WHERE triage_id = $1::uuid
          AND status = 'PENDING_REVIEW'
        RETURNING *
        """,
        triage_id,
        reviewer,
        (reviewer_notes or "").strip() or None,
        (final_screening_outcome or "").strip() or None,
    )
    return _to_json_safe(dict(row)) if row else None


async def reject_ai_triage_task(
    conn,
    *,
    triage_id: str,
    reviewer: str,
    reviewer_notes: Optional[str],
    final_screening_outcome: Optional[str],
) -> Optional[Dict[str, Any]]:
    row = await conn.fetchrow(
        """
        UPDATE ai_triage_recommendations
        SET status = 'REJECTED',
            human_decision = 'REJECTED',
            reviewer = $2,
            reviewed_at = NOW(),
            reviewer_notes = $3,
            final_screening_outcome = $4,
            agreement_indicator = 'DISAGREED',
            updated_at = NOW()
        WHERE triage_id = $1::uuid
          AND status = 'PENDING_REVIEW'
        RETURNING *
        """,
        triage_id,
        reviewer,
        (reviewer_notes or "").strip() or None,
        (final_screening_outcome or "").strip() or None,
    )
    return _to_json_safe(dict(row)) if row else None


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


def _decode_jsonish(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return value
    return dict(value) if hasattr(value, "items") else value


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
        where = "WHERE j.status = $1"
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
            j.country,
            j.entity_type,
            j.requestor,
            j.business_reference,
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
