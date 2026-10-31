#!/usr/bin/env python3
"""
Background worker: claims one screening job at a time, runs the check, upserts screened_entities.
Run 1–2 instances (e.g. systemd or Docker) to keep concurrency low.
Requires: DATABASE_URL, same codebase as API (utils, parquet data optional for first run).
"""
import os
import sys
import json
import logging
from review_decisions import project_result
from screening_policy import run_parquet_check
import time
from uuid import uuid4
from job_recovery import Heartbeat, claim_lease, recover_expired, lock_owned_job, LostJobLease
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("screening_worker")
REASON_FOR_CHECK_ALLOWED = frozenset({
    "Client Onboarding",
    "Claim Payment",
    "Business Partner Payment",
    "Business Partner Due Diligence",
    "Periodic Re-Screen",
    "Ad-Hoc Compliance Review",
})

def main(stop_event=None):
    import threading
    stop_event = stop_event or threading.Event()
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from utils import _normalize_text, _normalize_dob, screening_fingerprint, stamp_screening_result

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    if os.environ.get('SANCTIONS_SCHEMA_MODE') == 'verify':
        import asyncio
        import asyncpg
        from pathlib import Path
        from deployment_runtime import verify_schema
        Path('/tmp/screening-worker-ready').unlink(missing_ok=True)
        async def verify():
            connection = await asyncpg.connect(url, timeout=5)
            try:
                await verify_schema(connection)
            finally:
                await connection.close()
        asyncio.run(verify())
        from utils import get_opensanctions_df
        data = get_opensanctions_df()
        if data.empty or not {'sanctions', 'peps'}.issubset(set(data['source_type'])):
            raise RuntimeError('Worker requires populated sanctions and PEP Parquet data')
        Path('/tmp/screening-worker-ready').write_text(os.environ.get('RELEASE_ID', ''))

    poll_interval = max(2, int(os.environ.get("SCREENING_WORKER_POLL_SECONDS", "5")))
    retention_days = max(1, int(os.environ.get("SCREENING_JOBS_RETENTION_DAYS", "7")))
    screened_retention_months = int(os.environ.get("SCREENED_ENTITIES_RETENTION_MONTHS", "0"))
    cleanup_every_n = max(1, int(os.environ.get("SCREENING_CLEANUP_EVERY_N_LOOPS", "50")))
    logger.info("Starting worker (poll every %s s, job retention %s days, screened_entities retention %s months, cleanup every %s loops)",
                poll_interval, retention_days, screened_retention_months or "off", cleanup_every_n)

    lease_seconds = max(15, int(os.environ.get("SCREENING_JOB_LEASE_SECONDS", "60")))
    max_runtime = max(lease_seconds, int(os.environ.get("SCREENING_JOB_MAX_RUNTIME_SECONDS", "600")))
    max_attempts = max(1, int(os.environ.get("SCREENING_JOB_MAX_ATTEMPTS", "3")))
    from screening_monitoring import start_background
    start_background(stop_event)
    loop_count = 0
    while not stop_event.is_set():
        try:
            conn = psycopg2.connect(url)
            conn.autocommit = False
            job_id = None
            token = None
            heartbeat = None
            try:
                recover_expired(conn, max_attempts)
                conn.commit()
                # Claim one pending job
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT job_id, entity_key, name, date_of_birth, country, entity_type, requestor, business_reference, reason_for_check, reason, refresh_run_id, force_rescreen
                        FROM screening_jobs
                        WHERE status = 'pending'
                        ORDER BY created_at
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                        """
                    )
                    row = cur.fetchone()
                if row is None:
                    conn.rollback()
                    conn.close()
                    time.sleep(poll_interval)
                    continue

                job_id = row["job_id"]
                entity_key = row["entity_key"]
                name = row["name"]
                dob = row["date_of_birth"]
                country = (row.get("country") or "").strip() or None
                entity_type = row["entity_type"] or "Person"
                requestor = row["requestor"] or ""
                business_reference = (row.get("business_reference") or "").strip()
                reason_for_check = (row.get("reason_for_check") or "").strip()
                reason = row.get("reason") or "manual"
                refresh_run_id = row.get("refresh_run_id")
                force_rescreen = bool(row.get("force_rescreen"))

                validation_error = None
                if not business_reference:
                    validation_error = "business_reference is required"
                elif reason_for_check not in REASON_FOR_CHECK_ALLOWED:
                    validation_error = "reason_for_check is required and must be a valid enum value"
                if validation_error:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE screening_jobs SET status = 'failed', finished_at = NOW(), error_message = %s WHERE job_id = %s",
                            (validation_error, job_id),
                        )
                    conn.commit()
                    logger.warning("Job %s failed validation: %s", job_id, validation_error)
                    continue

                token = str(uuid4())
                claim_lease(conn, job_id, token, lease_seconds)
                conn.commit()
                heartbeat = Heartbeat(url, job_id, token, lease_seconds, max_runtime)
                heartbeat.start()

                # Previous result status for transition tracking
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT status, result_json, current_run_id FROM screened_entities WHERE entity_key = %s",
                        (entity_key,),
                    )
                    existing_any = cur.fetchone()
                previous_status = (existing_any or {}).get("status")

                # Idempotent reuse for non-forced jobs
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """SELECT result_json, current_run_id, review_decision FROM screened_entities WHERE entity_key = %s
                        AND screening_valid_until > NOW() AND manual_override_stale = FALSE
                        AND result_json->>'Screening Fingerprint' = %s
                        AND (result_json ? 'Source Coverage' OR screened_against_refresh_run_id IS NOT DISTINCT FROM
                            (SELECT refresh_run_id FROM watchlist_refresh_runs ORDER BY ran_at DESC LIMIT 1))""",
                        (entity_key, screening_fingerprint(name, dob, country, entity_type, 'worker-original')),
                    )
                    existing_valid = cur.fetchone()
                if existing_valid:
                    from source_assurance import parquet_manifest, assess, decode
                    cached_engine = decode(existing_valid['result_json'])
                    coverage = assess(parquet_manifest(), cached_engine.get('PEP Check', {}).get('checked', True))
                    if coverage['status'] != 'current' or coverage.get('version') != cached_engine.get('Source Coverage', {}).get('version'):
                        existing_valid = None
                if existing_valid and not force_rescreen:
                    existing_result = existing_valid.get("result_json") or {}
                    if isinstance(existing_result, str):
                        try:
                            existing_result = json.loads(existing_result)
                        except Exception:
                            existing_result = {}
                    existing_result = project_result(existing_result, existing_valid.get('review_decision'))
                    existing_result.update(run_id=str(existing_valid['current_run_id']), entity_key=entity_key, reused=True)
                    result_status = ((existing_result or {}).get("Check Summary") or {}).get("Status") if isinstance(existing_result, dict) else None
                    transition = "unchanged"
                    if previous_status and result_status and previous_status != result_status:
                        p = previous_status.lower()
                        r = result_status.lower()
                        if p.startswith("cleared") and r.startswith("fail"):
                            transition = "cleared_to_fail"
                        elif p.startswith("fail") and r.startswith("cleared"):
                            transition = "fail_to_cleared"
                        else:
                            transition = "changed"
                    lock_owned_job(conn, job_id, token)
                    with conn.cursor() as cur:
                        cur.execute("UPDATE screening_requests SET reused=true WHERE job_id=%s", (job_id,))
                        cur.execute(
                            """
                            UPDATE screening_jobs
                            SET status = 'completed',
                                run_id = %s,
                                response_json = %s::jsonb,
                                finished_at = NOW(),
                                previous_status = %s,
                                result_status = %s,
                                transition = %s
                            WHERE job_id = %s
                            """,
                            (existing_valid["current_run_id"], json.dumps(existing_result), previous_status, result_status, transition, job_id),
                        )
                    conn.commit()
                    logger.info("Job %s: reused existing valid screening", job_id)
                    continue

                # Run screening (sync)
                logger.info("Job %s: running check for %s", job_id, name[:50])
                result = run_parquet_check(
                    name=name,
                    dob=dob,
                    country=country,
                    entity_type=entity_type,
                    requestor=requestor,
                    log_search=False,
                    require_assurance=True,
                )

                stamp_screening_result(result, name, dob, country, entity_type, 'worker-original')
                result['Review Business Reference'] = business_reference
                if reason in ('all_sources_reconciliation', 'uk_delta_rescreen'):
                    from screening_guidance import describe_change
                    describe_change(result, (existing_any or {}).get('result_json'), (existing_any or {}).get('current_run_id'))

                # Derive UK flag from result
                src = (result.get("Check Summary") or {}).get("Source") or ""
                uk_flag = any(
                    p in src.lower()
                    for p in ("uk", "hmt", "ofsi", "hm treasury", "uk fcdo", "uk financial sanctions")
                )
                now = datetime.now(timezone.utc)
                valid_until = now + timedelta(days=365)
                status = (result.get("Check Summary") or {}).get("Status") or "Unknown"
                risk_level = result.get("Risk Level") or ""
                confidence = result.get("Confidence") or ""
                score = float(result.get("Score") or 0)
                pep_flag = bool(result.get("Is PEP"))
                result_status = (result.get("Check Summary") or {}).get("Status") or "Unknown"
                transition = "new_result"
                if previous_status and result_status:
                    if previous_status == result_status:
                        transition = "unchanged"
                    else:
                        p = previous_status.lower()
                        r = result_status.lower()
                        if p.startswith("cleared") and r.startswith("fail"):
                            transition = "cleared_to_fail"
                        elif p.startswith("fail") and r.startswith("cleared"):
                            transition = "fail_to_cleared"
                        else:
                            transition = "changed"
                display_name = name
                normalized_name = _normalize_text(name)
                dob_date = None
                if dob:
                    try:
                        from datetime import date
                        normalized_dob = _normalize_dob(dob)
                        dob_date = date.fromisoformat(normalized_dob) if normalized_dob and len(normalized_dob) == 10 else None
                    except Exception:
                        pass

                # Exact provenance lives in the manifest embedded in the loaded file.
                # A queued refresh ID describes a trigger, not necessarily the file used.
                screened_against_uk_hash = None
                screened_against_refresh_run_id = None

                # Upsert screened_entities
                lock_owned_job(conn, job_id, token)
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO screened_entities (
                            entity_key, display_name, normalized_name, date_of_birth, country_input, entity_type,
                            last_screened_at, screening_valid_until,
                            status, risk_level, confidence, score, uk_sanctions_flag, pep_flag,
                            result_json, last_requestor, business_reference, reason_for_check, updated_at,
                            screened_against_uk_hash, screened_against_refresh_run_id,
                            manual_override_uk_hash, manual_override_stale
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, NULL, FALSE)
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
                        (
                            entity_key,
                            display_name,
                            normalized_name,
                            dob_date,
                            country,
                            entity_type,
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
                            business_reference,
                            reason_for_check,
                            now,
                            screened_against_uk_hash,
                            screened_against_refresh_run_id,
                        ),
                    )
                    cur.execute('SELECT reuse_candidate_decisions(%s)', (entity_key,))
                    decision = cur.fetchone()[0]
                    effective = project_result(result, decision)
                    cur.execute('SELECT current_run_id FROM screened_entities WHERE entity_key=%s', (entity_key,))
                    effective.update(run_id=str(cur.fetchone()[0]), entity_key=entity_key, reused=False, screened_at=now.isoformat())
                    cur.execute('UPDATE screening_jobs SET response_json=%s::jsonb WHERE job_id=%s', (json.dumps(effective), job_id))
                    result_status = effective['Check Summary']['Status']
                    cur.execute(
                        """
                        UPDATE screening_jobs
                        SET status = 'completed',
                            run_id = (SELECT current_run_id FROM screened_entities WHERE entity_key = %s),
                            finished_at = NOW(),
                            previous_status = %s,
                            result_status = %s,
                            transition = %s
                        WHERE job_id = %s
                        """,
                        (entity_key, previous_status, result_status, transition, job_id),
                    )
                conn.commit()
                # Compatibility CSV is written only after fenced database publication.
                try:
                    from utils import _append_search_to_csv
                    _append_search_to_csv(name, result['Check Summary'])
                except Exception:
                    logger.warning("Job %s: compatibility CSV append failed after completion", job_id)
                logger.info("Job %s: completed reason=%s transition=%s", job_id, reason, transition)

            except LostJobLease:
                conn.rollback()
                logger.warning("Job %s: expired ownership; result discarded", job_id)
            except Exception as e:
                conn.rollback()
                if "job_id" in dir() and job_id:
                    try:
                        lock_owned_job(conn, job_id, token)
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE screening_jobs SET status = 'failed', finished_at = NOW(), error_message = %s WHERE job_id = %s",
                                (str(e)[:1000], job_id),
                            )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                    logger.exception("Job %s failed", job_id)
                else:
                    logger.exception("Worker error")
            finally:
                if heartbeat is not None:
                    heartbeat.stop()
                conn.close()

            # Optional cleanup: delete old completed/failed jobs every N loops (non-blocking, conservative).
            loop_count += 1
            if loop_count >= cleanup_every_n:
                loop_count = 0
                try:
                    cleanup_conn = psycopg2.connect(url)
                    cleanup_conn.autocommit = True
                    with cleanup_conn.cursor() as cur:
                        cur.execute(
                            """
                            DELETE FROM screening_jobs
                            WHERE status IN ('completed', 'failed')
                              AND finished_at IS NOT NULL
                              AND finished_at < NOW() - (%s::text || ' days')::interval
                              AND NOT EXISTS (SELECT 1 FROM screening_monitoring_items i JOIN screening_monitoring_cycles c USING(cycle_id) WHERE i.job_id=screening_jobs.job_id AND c.status='running')
                            """,
                            (retention_days,),
                        )
                        deleted = cur.rowcount
                        if screened_retention_months >= 1:
                            cur.execute(
                                """
                                DELETE FROM screened_entities
                                WHERE last_screened_at < NOW() - (%s::text || ' months')::interval
                                """,
                                (screened_retention_months,),
                            )
                            screened_deleted = cur.rowcount
                            if screened_deleted:
                                logger.info("screened_entities retention deleted %s old row(s)", screened_deleted)
                    cleanup_conn.close()
                    if deleted:
                        logger.info("queue cleanup deleted %s old job(s)", deleted)
                except Exception as e:
                    logger.warning("queue cleanup failed: %s", e)
        except Exception as e:
            logger.exception("Connection/worker error: %s", e)
            time.sleep(poll_interval)
        time.sleep(0.5)


if __name__ == "__main__":
    import signal
    import threading
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    main(stopping)
