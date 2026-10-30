"""Rehearse on synthetic legacy data in a new, disposable LOCAL PostgreSQL DB.

SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres python scripts/rehearse_history_migration.py --rows 10000
Never connects using DATABASE_URL and never modifies an existing application's DB.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlparse, parse_qs
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import screening_db


def local_dsn(value):
    parsed = urlparse(value)
    if parsed.scheme not in ('postgres', 'postgresql'):
        raise ValueError('Use a local PostgreSQL URL in SANCTIONS_TEST_ADMIN_DSN.')
    hosts = [parsed.hostname or '', *parse_qs(parsed.query).get('host', [])]
    if any(host not in ('', 'localhost', '127.0.0.1', '::1') and not host.startswith('/') for host in hosts):
        raise ValueError('Rehearsal only permits a local host or Unix socket.')
    return value


async def counts(conn):
    return {table: await conn.fetchval(f'SELECT count(*) FROM {table}') for table in
            ('screened_entities', 'screening_subjects', 'screening_runs', 'screening_requests', 'screening_review_events', 'screening_review_decisions')}


async def rehearse(rows):
    dsn = local_dsn(os.environ.get('SANCTIONS_TEST_ADMIN_DSN', ''))
    database = 'sanctions_rehearsal_' + uuid4().hex
    admin = await asyncpg.connect(dsn)
    conn = None
    created = False
    report = {'synthetic_rows': rows, 'production_data_used': False, 'checks': []}
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
        created = True
        conn = await asyncpg.connect(dsn, database=database, command_timeout=180)
        report['postgres_version'] = await conn.fetchval('SHOW server_version')
        await conn.execute((ROOT / 'tests/fixtures/pre_history_schema.sql').read_text())
        await conn.execute("""INSERT INTO screened_entities (
            entity_key, display_name, normalized_name, entity_type, last_screened_at,
            screening_valid_until, status, risk_level, confidence, score, result_json,
            business_reference, last_requestor, review_status, review_claimed_by,
            review_claimed_at, review_outcome, review_notes, review_completed_by, review_completed_at)
            SELECT 'legacy-'||i, 'Synthetic Company '||i, 'synthetic company '||i, 'Organization',
                '2026-09-01'::timestamptz, '2026-12-01'::timestamptz,
                CASE WHEN i%3=0 THEN 'Cleared - False Positive' ELSE 'Fail Sanction' END,
                CASE WHEN i%3=0 THEN 'Cleared' ELSE 'High Risk' END, 'High', 93,
                jsonb_build_object('Match Found', i%3<>0, 'Is Sanctioned', i%3<>0, 'Is PEP',false,
                    'Check Summary',jsonb_build_object('Status',CASE WHEN i%3=0 THEN 'Cleared - False Positive' ELSE 'Fail Sanction' END),
                    'fixture_padding', repeat(md5(i::text),64)),
                'SYNTHETIC-'||i, 'fixture-requestor',
                CASE WHEN i%3=0 THEN 'COMPLETED' WHEN i%3=1 THEN 'IN_REVIEW' END,
                CASE WHEN i%3=1 THEN 'fixture-reviewer' END,
                CASE WHEN i%3=1 THEN '2026-09-02'::timestamptz END,
                CASE WHEN i%3=0 THEN 'False Positive - Proceeded' END,
                CASE WHEN i%3=0 THEN 'Legacy synthetic review evidence' END,
                CASE WHEN i%3=0 THEN 'fixture-reviewer' END,
                CASE WHEN i%3=0 THEN '2026-09-02'::timestamptz END
            FROM generate_series(1,$1::int) i""", rows)
        # Keep a byte-for-byte JSON comparison of all original current-state fields.
        await conn.execute('CREATE TEMP TABLE original_state AS SELECT entity_key, to_jsonb(e) AS evidence FROM screened_entities e')
        report['database_bytes_before'] = await conn.fetchval('SELECT pg_database_size(current_database())')
        # Deliberately roll back both migrations, proving interrupted transactions leave no partial history.
        tx = conn.transaction()
        await tx.start()
        try:
            for name in ('001_screening_history.sql', '002_review_decisions.sql', '003_job_recovery.sql', '004_request_recovery.sql'):
                await conn.execute((ROOT / 'migrations' / name).read_text())
        finally:
            await tx.rollback()
        assert await conn.fetchval("SELECT to_regclass('screening_runs')") is None
        report['checks'].append('transaction rollback leaves no partial history schema')
        started = time.perf_counter()
        await screening_db.ensure_schema(conn)
        report['migration_seconds'] = round(time.perf_counter() - started, 3)
        baseline = await counts(conn)
        assert all(baseline[t] == rows for t in ('screened_entities','screening_subjects','screening_runs','screening_requests')), baseline
        assert baseline['screening_review_decisions'] == 0
        assert await conn.fetchval("""SELECT count(*) FROM screened_entities e JOIN original_state o USING(entity_key)
            WHERE (to_jsonb(e) - ARRAY['current_run_id','review_decision','review_claimed_run_id']) <> o.evidence""") == 0
        assert await conn.fetchval("""SELECT count(*) FROM screened_entities e JOIN screening_runs r ON r.run_id=e.current_run_id
            WHERE r.snapshot_kind<>'legacy_snapshot' OR r.result_json<>e.result_json OR r.screened_at<>e.last_screened_at""") == 0
        assert await conn.fetchval("SELECT count(*) FROM screened_entities WHERE review_status='IN_REVIEW' AND review_claimed_run_id IS DISTINCT FROM current_run_id") == 0
        assert await conn.fetchval("SELECT count(*) FROM screening_requests WHERE source<>'legacy_snapshot' OR actor IS NOT NULL") == 0
        report['checks'].extend(['all legacy rows backfilled exactly once', 'all original current-state fields preserved',
            'snapshot JSON and screening timestamps preserved', 'existing claims pinned to preserved runs', 'no invented actors or analyst decisions'])
        started = time.perf_counter()
        await screening_db.ensure_schema(conn)
        report['reentry_seconds'] = round(time.perf_counter() - started, 3)
        assert await counts(conn) == baseline
        report['checks'].append('startup reentry does not duplicate history')
        for table in ('screening_runs', 'screening_requests'):
            await conn.execute(f'ANALYZE {table}')
        started = time.perf_counter()
        import screening_history
        records = await screening_history.list_requests(conn, 'SYNTHETIC-1')
        assert len(records['items']) == 1
        report['reference_lookup_ms'] = round((time.perf_counter() - started)*1000, 3)
        report['database_bytes_after'] = await conn.fetchval('SELECT pg_database_size(current_database())')
        report['counts'] = baseline
        report['status'] = 'passed'
    finally:
        if conn is not None:
            await conn.close()
        try:
            if created:
                await admin.execute(f'DROP DATABASE "{database}"')
                report['disposable_database_removed'] = True
        finally:
            await admin.close()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=10000)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 1 <= args.rows <= 1000000:
        parser.error('--rows must be between 1 and 1000000')
    result = asyncio.run(rehearse(args.rows))
    rendered = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end='')
