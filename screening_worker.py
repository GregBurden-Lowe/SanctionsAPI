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
import time
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("screening_worker")

def main():
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from utils import perform_opensanctions_check, _normalize_text

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    poll_interval = max(2, int(os.environ.get("SCREENING_WORKER_POLL_SECONDS", "5")))
    retention_days = max(1, int(os.environ.get("SCREENING_JOBS_RETENTION_DAYS", "7")))
    screened_retention_months = int(os.environ.get("SCREENED_ENTITIES_RETENTION_MONTHS", "0"))
    cleanup_every_n = max(1, int(os.environ.get("SCREENING_CLEANUP_EVERY_N_LOOPS", "50")))
    logger.info("Starting worker (poll every %s s, job retention %s days, screened_entities retention %s months, cleanup every %s loops)",
                poll_interval, retention_days, screened_retention_months or "off", cleanup_every_n)

    loop_count = 0
    while True:
        try:
            conn = psycopg2.connect(url)
            conn.autocommit = False
            try:
                # Claim one pending job
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT job_id, entity_key, name, date_of_birth, entity_type, requestor, reason, refresh_run_id, force_rescreen
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
                entity_type = row["entity_type"] or "Person"
                requestor = row["requestor"] or ""
                reason = row.get("reason") or "manual"
                refresh_run_id = row.get("refresh_run_id")
                force_rescreen = bool(row.get("force_rescreen"))

                # Mark running
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE screening_jobs SET status = 'running', started_at = NOW() WHERE job_id = %s",
                        (job_id,),
                    )
                conn.commit()

                # Previous result status for transition tracking
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT status, result_json FROM screened_entities WHERE entity_key = %s",
                        (entity_key,),
                    )
                    existing_any = cur.fetchone()
                previous_status = (existing_any or {}).get("status")

                # Idempotent reuse for non-forced jobs
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT result_json FROM screened_entities WHERE entity_key = %s AND screening_valid_until > NOW()",
                        (entity_key,),
                    )
                    existing_valid = cur.fetchone()
                if existing_valid and not force_rescreen:
                    existing_result = existing_valid.get("result_json") or {}
                    if isinstance(existing_result, str):
                        try:
                            existing_result = json.loads(existing_result)
                        except Exception:
                            existing_result = {}
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
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE screening_jobs
                            SET status = 'completed',
                                finished_at = NOW(),
                                previous_status = %s,
                                result_status = %s,
                                transition = %s
                            WHERE job_id = %s
                            """,
                            (previous_status, result_status, transition, job_id),
                        )
                    conn.commit()
                    logger.info("Job %s: reused existing valid screening", job_id)
                    continue

                # Run screening (sync)
                logger.info("Job %s: running check for %s", job_id, name[:50])
                result = perform_opensanctions_check(
                    name=name,
                    dob=dob,
                    entity_type=entity_type,
                    requestor=requestor,
                )

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
                        dob_date = date.fromisoformat(str(dob).strip()[:10])
                    except Exception:
                        pass

                screened_against_uk_hash = None
                screened_against_refresh_run_id = None
                try:
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        if refresh_run_id:
                            cur.execute(
                                "SELECT refresh_run_id, uk_hash FROM watchlist_refresh_runs WHERE refresh_run_id = %s::uuid",
                                (str(refresh_run_id),),
                            )
                        else:
                            cur.execute(
                                """
                                SELECT refresh_run_id, uk_hash
                                FROM watchlist_refresh_runs
                                ORDER BY ran_at DESC
                                LIMIT 1
                                """
                            )
                        run_row = cur.fetchone()
                    if run_row:
                        screened_against_refresh_run_id = str(run_row.get("refresh_run_id"))
                        screened_against_uk_hash = run_row.get("uk_hash")
                except Exception:
                    screened_against_refresh_run_id = str(refresh_run_id) if refresh_run_id else None

                # Upsert screened_entities
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO screened_entities (
                            entity_key, display_name, normalized_name, date_of_birth, entity_type,
                            last_screened_at, screening_valid_until,
                            status, risk_level, confidence, score, uk_sanctions_flag, pep_flag,
                            result_json, last_requestor, updated_at,
                            screened_against_uk_hash, screened_against_refresh_run_id,
                            manual_override_uk_hash, manual_override_stale
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, NULL, FALSE)
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
                            now,
                            screened_against_uk_hash,
                            screened_against_refresh_run_id,
                        ),
                    )
                    cur.execute(
                        """
                        UPDATE screening_jobs
                        SET status = 'completed',
                            finished_at = NOW(),
                            previous_status = %s,
                            result_status = %s,
                            transition = %s
                        WHERE job_id = %s
                        """,
                        (previous_status, result_status, transition, job_id),
                    )
                conn.commit()
                logger.info("Job %s: completed reason=%s transition=%s", job_id, reason, transition)

            except Exception as e:
                conn.rollback()
                if "job_id" in dir() and job_id:
                    try:
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
    main()
