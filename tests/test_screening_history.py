"""Regression tests; DB cases create/drop a dedicated database only when explicitly enabled."""
import asyncio
import json
import os
import unittest
import tempfile
from uuid import uuid4
from unittest.mock import AsyncMock, Mock, patch

import pandas as pd
import asyncpg
import api_server
import screening_db
import screening_history
from tests.worker_fixtures import postgres_job
from utils import (ScreeningUnavailableError,
                   screening_fingerprint, stamp_screening_result, _empty_no_match_result)


def sample():
    return _empty_no_match_result('test fixture')


class ScreeningSafetyTests(unittest.TestCase):
    def test_country_default_and_explicit_override_share_fingerprint(self):
        args = ('Jane Smith', None)
        self.assertEqual(screening_fingerprint(*args, None, 'Person', 'postgres_beta'), screening_fingerprint(*args, 'United Kingdom', 'Person', 'postgres_beta'))
        self.assertNotEqual(screening_fingerprint(*args, 'France', 'Person', 'postgres_beta'), screening_fingerprint(*args, None, 'Person', 'postgres_beta'))

    def test_fingerprint_preserves_matching_inputs(self):
        fp = lambda name='Jane A Smith', country='UK', dob='01-02-1980', backend='original': screening_fingerprint(name, dob, country, 'Person', backend)
        self.assertEqual(fp(), fp(dob='1980-02-01'))
        self.assertNotEqual(fp(), fp(name='Jane B Smith'))
        self.assertNotEqual(fp(), fp(country='US'))
        self.assertNotEqual(fp(), fp(backend='postgres_beta'))


class UnsupportedNameApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_entry_points_refuse_before_cache_or_queue(self):
        payload = dict(name='Mr محمد Rashid', entity_type='Person', requestor='Alice',
                       business_reference='TEST-1', reason_for_check='Client Onboarding')
        response = await api_server._execute_screening(api_server.OpCheckRequest(**payload))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(json.loads(response.body)['error'], 'unsupported_name')
        internal = await api_server._internal_screening_execute(None, api_server.InternalScreeningRequest(**payload))
        self.assertEqual(internal['error'], 'unsupported_name')
        self.assertIn('no clearance was issued', internal['message'])


class ReconciliationPauseApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_can_pause_and_resume_with_shared_flag(self):
        import httpx
        api_server.app.dependency_overrides[api_server.require_admin] = lambda: {'sub': 'admin'}
        self.addCleanup(api_server.app.dependency_overrides.clear)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SANCTIONS_DATA_DIR': directory}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app), base_url='http://test') as client:
                self.assertFalse((await client.get('/admin/screening/reconciliation-pause')).json()['paused'])
                self.assertTrue((await client.post('/admin/screening/reconciliation-pause?paused=true')).json()['paused'])
                self.assertTrue((await client.get('/admin/screening/reconciliation-pause')).json()['paused'])
                self.assertFalse((await client.post('/admin/screening/reconciliation-pause?paused=false')).json()['paused'])
                api_server.app.dependency_overrides.clear()
                self.assertIn((await client.post('/admin/screening/reconciliation-pause?paused=true')).status_code, (401, 403))


@unittest.skipUnless(os.getenv('SANCTIONS_TEST_ADMIN_DSN'), 'Set SANCTIONS_TEST_ADMIN_DSN for isolated PostgreSQL integration tests')
class ScreeningHistoryDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from datetime import datetime, timezone
        import pandas as pd
        self.source_manifest = {'version':'test-source', 'ingested_at':datetime.now(timezone.utc).isoformat(),
            'list_counts':{k:1 for k in ('UK','UN','EU','OFAC')}}
        for mocked in (patch('screening_worker.current_postgres_manifest', return_value=self.source_manifest),
                       patch('source_assurance.parquet_manifest', return_value=self.source_manifest),
                       patch.dict(os.environ, {'SCREENING_MONITORING_ENABLED':'false'})):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.admin = await asyncpg.connect(os.environ['SANCTIONS_TEST_ADMIN_DSN'])
        self.database = 'sanctions_history_test_' + uuid4().hex
        await self.admin.execute(f'CREATE DATABASE "{self.database}"')
        self.pool = await asyncpg.create_pool(os.environ['SANCTIONS_TEST_ADMIN_DSN'], database=self.database, min_size=1, max_size=5)
        async with self.pool.acquire() as conn:
            await screening_db.ensure_schema(conn)
        self.pool_patch = patch.object(screening_db, 'get_pool', AsyncMock(return_value=self.pool))
        self.pool_patch.start()
        self.matcher = patch.object(api_server, '_run_postgres_dual_check', AsyncMock(side_effect=lambda *a, **kw: sample()))
        self.match = self.matcher.start()

    async def asyncTearDown(self):
        self.matcher.stop(); self.pool_patch.stop()
        await self.pool.close()
        await self.admin.execute(f'DROP DATABASE "{self.database}"')
        await self.admin.close()

    def request(self, reference='POL-123', **kwargs):
        return api_server.OpCheckRequest(name='Example Ltd', entity_type='Organization', country='UK',
            requestor='Alice', business_reference=reference, reason_for_check='Client Onboarding', **kwargs)

    async def check(self, data):
        result = await api_server._check_opensanctions_impl(data, actor='alice@example.test')
        self.assertIsInstance(result, dict, getattr(result, 'body', None))
        return result

    async def test_reuse_preserves_both_references_and_actor(self):
        first = await self.check(self.request())
        second = await self.check(self.request('CLM-456'))
        self.assertNotEqual(first['request_id'], second['request_id'])
        self.assertEqual(first['run_id'], second['run_id'])
        self.assertTrue(second['reused'])
        self.assertEqual(self.match.await_count, 1)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_runs'), 1)
            for reference in ('POL-123', 'CLM-456'):
                history = await screening_history.list_requests(conn, reference)
                self.assertEqual(len(history['items']), 1)
                self.assertEqual(history['items'][0]['actor'], 'alice@example.test')
                self.assertEqual(len(await screening_db.search_screened_entities(conn, business_reference=reference)), 1)
            self.assertEqual(await conn.fetchval('SELECT business_reference FROM screened_entities'), 'POL-123')

    async def test_changed_country_creates_run_and_original_is_immutable(self):
        first = await self.check(self.request())
        data = self.request('POL-999'); data.country = 'US'
        second = await self.check(data)
        self.assertNotEqual(first['run_id'], second['run_id'])
        async with self.pool.acquire() as conn:
            record = await screening_history.get_request(conn, first['request_id'])
            self.assertEqual(record['inputs']['country'], 'UK')
            self.assertEqual(record['response_json']['run_id'], first['run_id'])
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_runs'), 2)
            with self.assertRaises(asyncpg.RaiseError):
                await conn.execute("UPDATE screening_runs SET result_json='{}'::jsonb")

    async def test_missing_country_defaults_on_person_and_company_and_preserves_explicit_input(self):
        for kind, country in [('Person', None), ('Organization', '  '), ('Person', 'France')]:
            request = self.request(f'{kind}-{country}'); request.name = 'Test ' + kind + str(country); request.entity_type = kind; request.country = country
            result = await self.check(request)
            expected = 'France' if country == 'France' else 'United Kingdom'
            self.assertEqual(self.match.await_args.kwargs['country'], expected)
            self.assertEqual(result['Screening Inputs']['country'], expected)
            async with self.pool.acquire() as conn:
                stored = await conn.fetchrow('SELECT country_input FROM screened_entities WHERE entity_key=$1', result['entity_key'])
                self.assertEqual(stored['country_input'], expected)
                original = await screening_history.get_request(conn, result['request_id'])
                self.assertEqual(original['inputs']['country'], country)

    async def test_invalid_date_and_failed_write_never_return_clear(self):
        data = self.request(); data.dob = '31-02-1980'
        invalid = await api_server._check_opensanctions_impl(data)
        self.assertEqual(invalid.status_code, 400)
        with patch.object(screening_db, 'upsert_screening', AsyncMock(side_effect=RuntimeError('write failed'))):
            failed = await api_server._check_opensanctions_impl(self.request())
        self.assertEqual(failed.status_code, 503)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM screening_requests WHERE status='failed'"), 2)

    async def test_queued_result_does_not_follow_later_entity_changes(self):
        async with self.pool.acquire() as conn:
            job = await screening_db.enqueue_job(conn, 'queued-key', 'Queued Ltd', None, 'UK', 'Organization', 'Alice', 'BATCH-1', 'Client Onboarding')
            result = stamp_screening_result(sample(), 'Queued Ltd', None, 'UK', 'Organization', 'postgres_beta')
            async with conn.transaction():
                await screening_db.upsert_screening(conn, 'queued-key', 'Queued Ltd', 'queued ltd', None, 'UK', 'Organization', 'Alice', 'BATCH-1', 'Client Onboarding', result)
                await conn.execute("UPDATE screening_jobs SET status='completed', finished_at=now(), run_id=$2::uuid WHERE job_id=$1::uuid", job, result['run_id'])
            original_run = result['run_id']
            other = stamp_screening_result(sample(), 'Queued Ltd', None, 'US', 'Organization', 'postgres_beta')
            other['Check Summary']['Status'] = 'Fail Sanction'
            await screening_db.upsert_screening(conn, 'queued-key', 'Queued Ltd', 'queued ltd', None, 'US', 'Organization', 'Bob', 'BATCH-2', 'Client Onboarding', other)
            saved_job = await screening_db.get_job_status(conn, job)
            self.assertEqual(saved_job['result']['Check Summary']['Status'], 'Cleared')
            row = await conn.fetchrow('SELECT run_id, status, response_json FROM screening_requests WHERE job_id=$1::uuid', job)
            self.assertEqual(str(row['run_id']), original_run)
            self.assertEqual(row['status'], 'completed')
            await conn.execute('DELETE FROM screening_jobs WHERE job_id=$1::uuid', job)
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_requests WHERE job_id=$1::uuid', job), 1)

    async def test_internal_queue_and_existing_jobs_use_country_default(self):
        import screening_worker
        from psycopg2.extensions import make_dsn
        worker_dsn = make_dsn(os.environ['SANCTIONS_TEST_ADMIN_DSN'], dbname=self.database)
        item = api_server.InternalScreeningRequest(name='Default Person', entity_type='Person', country=None,
            requestor='Alice', business_reference='BATCH-UK', reason_for_check='Client Onboarding')
        async with self.pool.acquire() as conn:
            submitted = await api_server._internal_screening_outcome(conn, item)
            queued = await conn.fetchrow('SELECT country FROM screening_jobs WHERE job_id=$1::uuid', submitted['job_id'])
            self.assertEqual(queued['country'], 'United Kingdom')
            await conn.execute('UPDATE screening_jobs SET country=NULL WHERE job_id=$1::uuid', submitted['job_id'])
            other = item.model_copy(update={'name': 'Explicit Company', 'entity_type': 'Organization', 'country': 'France', 'business_reference': 'BATCH-FR'})
            explicit = await api_server._internal_screening_outcome(conn, other)
        with patch.dict(os.environ, {'DATABASE_URL': worker_dsn}), patch('screening_worker.run_postgres_job', new=postgres_job(matcher := Mock(return_value=sample()), self.source_manifest)), patch('screening_worker.time.sleep', side_effect=KeyboardInterrupt):
            for _ in range(2):
                with self.assertRaises(KeyboardInterrupt):
                    screening_worker.main()
        self.assertEqual([call.kwargs['country'] for call in matcher.call_args_list], ['United Kingdom', 'United Kingdom', 'France', 'France'])
        async with self.pool.acquire() as conn:
            status = await screening_db.get_job_status(conn, submitted['job_id'])
            self.assertEqual(status['status'], 'completed')
            self.assertEqual(status['result']['Screening Inputs']['country'], 'United Kingdom')
            status = await screening_db.get_job_status(conn, explicit['job_id'])
            self.assertEqual(status['result']['Screening Inputs']['country'], 'France')

    async def test_paused_reconciliation_keeps_manual_jobs_running_and_resumes(self):
        import screening_worker
        from screening_monitoring import set_reconciliation_paused, tick
        from psycopg2.extensions import make_dsn
        worker_dsn = make_dsn(os.environ['SANCTIONS_TEST_ADMIN_DSN'], dbname=self.database)
        async with self.pool.acquire() as conn:
            held = await screening_db.enqueue_job(conn, 'held', 'Held Person', None, None, 'Person', 'Alice', 'HELD', 'Periodic Re-Screen', reason='all_sources_reconciliation', force_rescreen=True)
            manual = await screening_db.enqueue_job(conn, 'manual', 'Manual Person', None, None, 'Person', 'Alice', 'MANUAL', 'Client Onboarding')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'DATABASE_URL': worker_dsn, 'SANCTIONS_DATA_DIR': directory}), \
             patch('screening_worker.run_postgres_job', new=postgres_job(Mock(return_value=sample()), self.source_manifest)), \
             patch('screening_worker.time.sleep', side_effect=KeyboardInterrupt):
            set_reconciliation_paused(True)
            async with self.pool.acquire() as conn:
                self.assertEqual((await tick(conn, force=True))['status'], 'paused')
            with self.assertRaises(KeyboardInterrupt): screening_worker.main()
            async with self.pool.acquire() as conn:
                self.assertEqual((await screening_db.get_job_status(conn, held))['status'], 'pending')
                self.assertEqual((await screening_db.get_job_status(conn, manual))['status'], 'completed')
            set_reconciliation_paused(False)
            with self.assertRaises(KeyboardInterrupt): screening_worker.main()
            async with self.pool.acquire() as conn:
                self.assertEqual((await screening_db.get_job_status(conn, held))['status'], 'completed')

    async def test_migration_reentry_and_legacy_backfill(self):
        first = await self.check(self.request())
        async with self.pool.acquire() as conn:
            # Simulate a pre-migration row in this disposable test database.
            await conn.execute('ALTER TABLE screened_entities DISABLE TRIGGER screening_run_capture')
            await conn.execute('UPDATE screened_entities SET current_run_id=NULL')
            await conn.execute('ALTER TABLE screened_entities ENABLE TRIGGER screening_run_capture')
            await screening_db.ensure_schema(conn)
            await screening_db.ensure_schema(conn)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM screening_runs WHERE snapshot_kind='legacy_snapshot'"), 1)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM screening_requests WHERE source='legacy_snapshot'"), 1)
            self.assertIsNotNone(await screening_history.get_request(conn, first['request_id']))

    async def test_concurrent_upserts_do_not_create_phantom_runs(self):
        await asyncio.gather(self.check(self.request('A')), self.check(self.request('B')))
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_requests'), 2)
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_runs'), self.match.await_count)

    async def test_real_worker_records_completion_and_reuse(self):
        import screening_worker
        from psycopg2.extensions import make_dsn
        worker_dsn = make_dsn(os.environ['SANCTIONS_TEST_ADMIN_DSN'], dbname=self.database)
        async with self.pool.acquire() as conn:
            job = await screening_db.enqueue_job(conn, 'worker-key', 'Worker Ltd', None, 'UK', 'Organization', 'Alice', 'WORKER-1', 'Client Onboarding')
        with patch.dict(os.environ, {'DATABASE_URL': worker_dsn}), patch('screening_worker.run_postgres_job', new=postgres_job(matcher := Mock(return_value=sample()), self.source_manifest)), patch('screening_worker.time.sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                screening_worker.main()
            self.assertEqual(matcher.call_count, 2)
            async with self.pool.acquire() as conn:
                status = await screening_db.get_job_status(conn, job)
                self.assertEqual(status['status'], 'completed', status)
                await screening_db.enqueue_job(conn, 'worker-key', 'Worker Ltd', None, 'UK', 'Organization', 'Bob', 'WORKER-2', 'Client Onboarding')
            with self.assertRaises(KeyboardInterrupt):
                screening_worker.main()
            self.assertEqual(matcher.call_count, 2)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_runs'), 1)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM screening_requests WHERE status='completed'"), 2)
            self.assertTrue(await conn.fetchval("SELECT reused FROM screening_requests WHERE business_reference='WORKER-2'"))

    async def test_worker_refuses_mixed_script_before_reusing_cached_result(self):
        import screening_worker
        from psycopg2.extensions import make_dsn
        worker_dsn = make_dsn(os.environ['SANCTIONS_TEST_ADMIN_DSN'], dbname=self.database)
        async with self.pool.acquire() as conn:
            await screening_db.enqueue_job(conn, 'worker-key', 'Mr Rashid', None, 'UK', 'Person', 'Alice', 'WORKER-1', 'Client Onboarding')
        with patch.dict(os.environ, {'DATABASE_URL': worker_dsn}), patch('screening_worker.run_postgres_job', new=postgres_job(matcher := Mock(return_value=sample()), self.source_manifest)), patch('screening_worker.time.sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                screening_worker.main()
            async with self.pool.acquire() as conn:
                job = await screening_db.enqueue_job(conn, 'worker-key', 'Mr محمد Rashid', None, 'UK', 'Person', 'Bob', 'WORKER-2', 'Client Onboarding')
            with self.assertRaises(KeyboardInterrupt):
                screening_worker.main()
            self.assertEqual(matcher.call_count, 2)
        async with self.pool.acquire() as conn:
            status = await screening_db.get_job_status(conn, job)
            self.assertEqual(status['status'], 'failed')
            self.assertIn('no clearance was issued', status['error_message'])
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_runs'), 1)

    async def test_worker_reuses_exact_reviewed_response(self):
        import screening_worker
        from psycopg2.extensions import make_dsn
        worker_dsn = make_dsn(os.environ['SANCTIONS_TEST_ADMIN_DSN'], dbname=self.database)
        async with self.pool.acquire() as conn:
            job = await screening_db.enqueue_job(conn, 'worker-key', 'Worker Ltd', None, 'UK', 'Organization', 'Alice', 'WORKER-1', 'Client Onboarding')
        with patch.dict(os.environ, {'DATABASE_URL': worker_dsn}), patch('screening_worker.run_postgres_job', new=postgres_job(matcher := Mock(return_value={**sample(), 'Is Sanctioned': True, 'Match Found': True, 'Risk Level': 'High Risk'}), self.source_manifest)), patch('screening_worker.time.sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                screening_worker.main()
            self.assertEqual(matcher.call_count, 2)
            async with self.pool.acquire() as conn:
                status = await screening_db.get_job_status(conn, job)
                self.assertEqual(status['status'], 'completed', status)
                await screening_db.mark_false_positive(conn, entity_key='worker-key', actor='analyst', reason='Different registered company', expected_run_id=str(await conn.fetchval("SELECT current_run_id FROM screened_entities WHERE entity_key='worker-key'")))
                second_job = await screening_db.enqueue_job(conn, 'worker-key', 'Worker Ltd', None, 'UK', 'Organization', 'Bob', 'WORKER-2', 'Client Onboarding')
            with self.assertRaises(KeyboardInterrupt):
                screening_worker.main()
            self.assertEqual(matcher.call_count, 2)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_runs'), 1)
            self.assertEqual(await conn.fetchval("SELECT count(*) FROM screening_requests WHERE status='completed'"), 2)
            self.assertTrue(await conn.fetchval("SELECT reused FROM screening_requests WHERE business_reference='WORKER-2'"))

        async with self.pool.acquire() as conn:
            status = await screening_db.get_job_status(conn, second_job)
            self.assertFalse(status['result']['Is Sanctioned'])
            self.assertTrue(status['result']['Engine Result']['Is Sanctioned'])
            saved = await conn.fetchval("SELECT response_json FROM screening_requests WHERE business_reference='WORKER-2'")
            if isinstance(saved, str): saved = json.loads(saved)
            self.assertEqual(saved['Review Decision']['actor'], 'analyst')

    async def test_http_history_round_trip_and_pending_attachment(self):
        import httpx
        api_server.app.dependency_overrides[api_server.get_current_user] = lambda: {'sub': 'http-user@example.test', 'is_admin': True}
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app), base_url='http://test') as client:
                response = await client.post('/opcheck', json=self.request().model_dump())
                self.assertEqual(response.status_code, 200, response.text)
                saved = response.json()
                history = await client.get('/opcheck/history', params={'business_reference': 'POL-123'})
                self.assertEqual(history.status_code, 200, history.text)
                self.assertEqual(history.json()['items'][0]['actor'], 'http-user@example.test')
                detail = await client.get('/opcheck/history/' + saved['request_id'])
                self.assertEqual(detail.json()['response_json']['run_id'], saved['run_id'])
                self.assertEqual((await client.get('/opcheck/history/not-a-uuid')).status_code, 400)
            async with self.pool.acquire() as conn:
                item = api_server.InternalScreeningRequest(**self.request('BULK-1').model_dump(exclude={'search_backend', 'rerun_entity_key'}))
                first = await api_server._internal_screening_outcome(conn, item)
                item.business_reference = 'BULK-2'
                second = await api_server._internal_screening_outcome(conn, item)
                self.assertEqual(second['status'], 'already_pending')
                self.assertEqual(first['job_id'], second['job_id'])
                self.assertNotEqual(first['request_id'], second['request_id'])
                self.assertEqual(await conn.fetchval("SELECT count(*) FROM screening_requests WHERE status='queued'"), 2)
        finally:
            api_server.app.dependency_overrides.clear()

    async def test_postgres_retrieval_evidence_and_empty_table_refresh(self):
        import csv
        import tempfile
        from pathlib import Path
        from utils import sync_watchlist_entities_postgres, perform_postgres_watchlist_check, ScreeningUnavailableError
        import pandas as pd
        async with self.pool.acquire() as conn:
            await conn.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
            await conn.execute('''CREATE TABLE watchlist_entities (
                source_type text, entity_id text, entity_schema text, name text, name_norm text,
                birth_date date, country text, dataset text, regime text, raw_json jsonb)''')
            rows = [dict(id='subject-1', name='Jane Smith', schema='Person', birth_date='1980', countries='US', aliases='Jane Jones', dataset='OFAC;United Nations;EU Council;UK FCDO')]
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'sanctions.csv'
                with path.open('w') as file:
                    writer = csv.DictWriter(file, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(rows)
                imported = await sync_watchlist_entities_postgres(conn, sanctions_csv_path=str(path), peps_csv_path=None, include_peps=False)
                self.assertEqual(imported['sanctions'], 1)
                pg = await perform_postgres_watchlist_check(conn, 'Jane Jones', '1980-06-15', country='UK', log_search=False, include_peps=False)
                self.assertTrue(pg['Match Found'])
                self.assertEqual(pg['Candidate Evidence'][0]['dob_assessment'], 'compatible')
                self.assertEqual(pg['Candidate Evidence'][0]['country_assessment'], 'conflicting')
                excluded = await perform_postgres_watchlist_check(conn, 'Jane Smith', '1970-01-01', log_search=False, include_peps=False)
                self.assertFalse(excluded['Match Found'])
                self.assertEqual(excluded['Candidate Evidence'][0]['disposition'], 'excluded')
                empty = path.with_name('empty.csv')
                empty.write_text(','.join(rows[0].keys()) + '\n')
                with self.assertRaises(ScreeningUnavailableError):
                    await sync_watchlist_entities_postgres(conn, sanctions_csv_path=str(empty), peps_csv_path=None, include_peps=False)
                self.assertEqual(await conn.fetchval('SELECT count(*) FROM watchlist_entities'), 1)
                malformed = [{**rows[0], 'aliases':'"unclosed;alias'}]
                with path.open('w') as file:
                    writer = csv.DictWriter(file, fieldnames=rows[0].keys()); writer.writeheader(); writer.writerows(malformed)
                with self.assertRaises(ScreeningUnavailableError):
                    await sync_watchlist_entities_postgres(conn, sanctions_csv_path=str(path), peps_csv_path=None, include_peps=False)
                self.assertEqual(await conn.fetchval('SELECT count(*) FROM watchlist_entities'), 1)
                self.assertEqual(await conn.fetchval("SELECT name FROM watchlist_entities"),'Jane Smith')
            # Schema filtering precedes the cap; conflicting DOBs remain visible.
            await conn.execute("INSERT INTO watchlist_entities SELECT source_type, 'subject-2', entity_schema, name, name_norm, birth_date, country, dataset, regime, raw_json FROM watchlist_entities")
            with self.assertRaises(ScreeningUnavailableError):
                await perform_postgres_watchlist_check(conn, 'Jane Smith', None, include_peps=False, candidate_limit=1, log_search=False)

    async def adverse_run(self):
        def adverse(*args, **kwargs):
            result = sample()
            result.update({'Is Sanctioned': True, 'Match Found': True, 'Score': 93, 'Confidence': 'High', 'Risk Level': 'High Risk', 'Sanctions Name': 'Example Ltd'})
            result['Check Summary']['Status'] = 'Fail Sanction'
            return result
        self.match.side_effect = adverse
        return await self.check(self.request())

    async def test_false_positive_keeps_engine_timestamps_and_history(self):
        first = await self.adverse_run()
        async with self.pool.acquire() as conn:
            before = dict(await conn.fetchrow('SELECT * FROM screened_entities'))
            resolved = await screening_db.mark_false_positive(conn, entity_key=first['entity_key'], actor='reviewer@example.test', reason='Verified company registration differs.', expected_run_id=first['run_id'])
            after = dict(await conn.fetchrow('SELECT * FROM screened_entities'))
            for field in ('result_json', 'current_run_id', 'last_screened_at', 'screening_valid_until', 'screened_against_uk_hash', 'screened_against_refresh_run_id', 'last_requestor', 'score'):
                self.assertEqual(before[field], after[field], field)
            self.assertEqual(after['status'], 'Cleared - False Positive')
            self.assertEqual(after['review_status'], 'COMPLETED')
            self.assertEqual(resolved['Score'], 93)
            self.assertFalse(resolved['Is Sanctioned'])
            self.assertTrue(resolved['Engine Result']['Is Sanctioned'])
            self.assertEqual(resolved['Check Summary']['Date'], first['Check Summary']['Date'])
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_runs'), 1)
            original = await screening_history.get_request(conn, first['request_id'])
            self.assertTrue(original['response_json']['Is Sanctioned'])
            self.assertEqual(original['review_decisions'][0]['actor'], 'reviewer@example.test')
            with self.assertRaises(asyncpg.RaiseError):
                await conn.execute("UPDATE screening_review_decisions SET notes='changed'")
        reused = await self.check(self.request('CLAIM-AFTER-REVIEW'))
        self.assertTrue(reused['reused'])
        self.assertFalse(reused['Is Sanctioned'])
        self.assertEqual(reused['run_id'], first['run_id'])
        self.assertTrue(reused['Engine Result']['Is Sanctioned'])

    async def test_fresh_adverse_run_reopens_and_rejects_stale_resolution(self):
        from review_decisions import ReviewConflict
        first = await self.adverse_run()
        async with self.pool.acquire() as conn:
            await screening_db.mark_false_positive(conn, entity_key=first['entity_key'], actor='analyst', reason='Independent identifier checked', expected_run_id=first['run_id'])
        data = self.request(); data.rerun_entity_key = first['entity_key']
        second = await self.check(data)
        self.assertNotEqual(first['run_id'], second['run_id'])
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT * FROM screened_entities')
            self.assertIsNone(row['review_status']); self.assertIsNone(row['review_decision'])
            self.assertEqual(row['status'], 'Fail Sanction')
            self.assertEqual(len(await screening_db.list_review_queue(conn, review_status='UNREVIEWED')), 1)
            with self.assertRaises(ReviewConflict):
                await screening_db.mark_false_positive(conn, entity_key=first['entity_key'], actor='analyst', reason='Old screen submitted', expected_run_id=first['run_id'])
            original = await screening_history.get_request(conn, first['request_id'])
            self.assertEqual(len(original['review_decisions']), 1)

    async def test_only_claimant_can_complete_current_run(self):
        first = await self.adverse_run()
        async with self.pool.acquire() as conn:
            await screening_db.claim_review(conn, entity_key=first['entity_key'], claimed_by='alice')
            other = await screening_db.complete_review(conn, entity_key=first['entity_key'], completed_by='bob', review_outcome='False Positive - Proceeded', review_notes='Registration evidence reviewed.', expected_run_id=first['run_id'])
            self.assertEqual(other['error'], 'claimed_by_other')
            stale = await screening_db.complete_review(conn, entity_key=first['entity_key'], completed_by='alice', review_outcome='False Positive - Proceeded', review_notes='Registration evidence reviewed.', expected_run_id=str(uuid4()))
            self.assertEqual(stale['error'], 'stale_run')
            success = await screening_db.complete_review(conn, entity_key=first['entity_key'], completed_by='alice', review_outcome='False Positive - Proceeded', review_notes='Registration evidence reviewed.', expected_run_id=first['run_id'])
            self.assertEqual(success['status'], 'ok')
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_review_decisions'), 1)
            duplicate = await screening_db.complete_review(conn, entity_key=first['entity_key'], completed_by='alice', review_outcome='False Positive - Proceeded', review_notes='Registration evidence reviewed.')
            self.assertEqual(duplicate['error'], 'not_in_review')

    async def test_clear_rerun_is_system_resolution_not_false_positive(self):
        first = await self.adverse_run()
        self.match.side_effect = lambda *a, **kw: sample()
        data = self.request(); data.rerun_entity_key = first['entity_key']
        second = await self.check(data)
        async with self.pool.acquire() as conn:
            stale = await screening_db.complete_clear_rerun(conn, entity_key=first['entity_key'], actor='alice', expected_run_id=first['run_id'])
            self.assertEqual(stale['error'], 'stale_run')
            result = await screening_db.complete_clear_rerun(conn, entity_key=first['entity_key'], actor='alice', expected_run_id=second['run_id'])
            self.assertEqual(result['status'], 'ok')
            decision = await conn.fetchrow('SELECT * FROM screening_review_decisions')
            self.assertEqual(decision['decision_type'], 'system_no_match')
            self.assertEqual(decision['outcome'], 'No Match - Re-screened')
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_runs'), 2)
            await screening_db.ensure_schema(conn)
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_review_decisions'), 1)

    async def test_http_false_positive_requires_exact_run(self):
        import httpx
        first = await self.adverse_run()
        api_server.app.dependency_overrides[api_server.require_admin] = lambda: {'sub': 'reviewer', 'is_admin': True}
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app), base_url='http://test') as client:
                data = {'entity_key': first['entity_key'], 'reason': 'Company identity differs'}
                self.assertEqual((await client.post('/admin/screening/false-positive', json=data)).status_code, 409)
                data['expected_run_id'] = first['run_id']
                response = await client.post('/admin/screening/false-positive', json=data)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['result']['Review Decision']['actor'], 'reviewer')
        finally:
            api_server.app.dependency_overrides.clear()

    async def test_stale_decision_restores_engine_flags_without_rewriting_evidence(self):
        first = await self.adverse_run()
        async with self.pool.acquire() as conn:
            await screening_db.mark_false_positive(conn, entity_key=first['entity_key'], actor='alice', reason='Different registered company', expected_run_id=first['run_id'])
            await screening_db.mark_manual_overrides_stale(conn, latest_uk_hash='new-watchlist')
            row = await conn.fetchrow('SELECT * FROM screened_entities WHERE entity_key=$1', first['entity_key'])
            self.assertTrue(row['manual_override_stale'])
            self.assertTrue(row['uk_sanctions_flag'])
            self.assertEqual(row['status'], 'Fail Sanction')
            self.assertIsNone(row['review_status'])
            self.assertEqual(str(row['current_run_id']), first['run_id'])
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_runs'), 1)

    async def test_api_timeout_records_failure_without_clearance(self):
        self.match.side_effect = asyncpg.QueryCanceledError('statement timeout')
        response = await api_server._check_opensanctions_impl(self.request(), actor='fixture')
        self.assertEqual(response.status_code, 503)
        async with self.pool.acquire() as conn:
            self.assertEqual(await conn.fetchval('SELECT status FROM screening_requests'), 'failed')
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_runs'), 0)

    async def test_worker_failure_and_interruption_never_return_clearance(self):
        import screening_worker
        from psycopg2.extensions import make_dsn
        worker_dsn = make_dsn(os.environ['SANCTIONS_TEST_ADMIN_DSN'], dbname=self.database)
        for error, expected in [(ScreeningUnavailableError('Missing source'), 'failed'), (TimeoutError('timeout'), 'failed'), (KeyboardInterrupt(), 'running')]:
            async with self.pool.acquire() as conn:
                job = await screening_db.enqueue_job(conn, 'failure-'+str(uuid4()), 'Jane Smith', None, 'UK', 'Person', 'Fixture', 'FAILURE-TEST', 'Client Onboarding')
            with patch.dict(os.environ, {'DATABASE_URL': worker_dsn}), patch('screening_worker.run_postgres_job', side_effect=error), patch('screening_worker.time.sleep', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    screening_worker.main()
            async with self.pool.acquire() as conn:
                result = await screening_db.get_job_status(conn, job)
                self.assertEqual(result['status'], expected)
                self.assertNotIn('result', result)
                self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_runs'), 0)
                request = await conn.fetchrow('SELECT status,response_json FROM screening_requests WHERE job_id=$1::uuid',job)
                self.assertEqual(request['status'], 'queued' if expected=='running' else 'failed')

    async def test_refresh_run_records_without_pep_columns(self):
        async with self.pool.acquire() as conn:
            run_id = await screening_db.create_refresh_run(conn, postgres_synced=True, sanctions_rows=33588, uk_hash='hash',
                prev_uk_hash=None, uk_changed=False, uk_row_count=6420, delta_added=0, delta_removed=0, delta_changed=0)
            saved = await conn.fetchrow('SELECT * FROM watchlist_refresh_runs WHERE refresh_run_id=$1::uuid', run_id)
        self.assertEqual(saved['sanctions_rows'], 33588)
        self.assertFalse({'include_peps', 'peps_rows'} & set(saved.keys()))

    async def test_review_queue_total_and_paging_cover_every_item(self):
        result = {**sample(), 'Is Sanctioned': True, 'Match Found': True}
        result['Check Summary'] = {**result['Check Summary'], 'Status': 'Fail Sanction'}
        async with self.pool.acquire() as conn:
            for index in range(5):
                await screening_db.upsert_screening(conn, f'queue-{index}', f'Queue Subject {index}', f'queue subject {index}', None, None,
                                                    'Person', 'tester', f'REF-{index}', 'Client Onboarding', dict(result))
            await conn.execute("UPDATE screened_entities SET last_screened_at='2026-01-01' WHERE entity_key LIKE 'queue-%'")
            self.assertEqual(await screening_db.count_review_queue(conn), 5)
            keys = []
            for offset in (0, 2, 4):
                keys += [row['entity_key'] for row in await screening_db.list_review_queue(conn, limit=2, offset=offset)]
            self.assertEqual(keys, sorted(f'queue-{index}' for index in range(5)))
            self.assertEqual(await screening_db.count_review_queue(conn, review_status='IN_REVIEW'), 0)
            await conn.execute("UPDATE screened_entities SET status='Cleared' WHERE entity_key='queue-0'")
            self.assertEqual(await screening_db.count_review_queue(conn), 4)
            self.assertEqual(await screening_db.count_review_queue(conn, include_cleared=True), 5)

    async def test_review_queue_indexes_exist(self):
        async with self.pool.acquire() as conn:
            names = {row['indexname'] for row in await conn.fetch('SELECT indexname FROM pg_indexes')}
        self.assertLessEqual({'screening_requests_run_idx', 'screened_entities_review_queue_idx'}, names)

    async def test_real_worker_and_interactive_policy_find_wrong_type_match(self):
        import screening_worker
        from screening_policy import run_postgres_check
        job, env = await self.prepare_postgres_worker(name='Hyde and Partners')
        async with self.pool.acquire() as conn:
            await conn.execute("""INSERT INTO watchlist_entities VALUES('sanctions','company','Organization','Hyde & Partners Ltd',
                'hyde partners ltd',NULL,'OFAC',NULL,'{}'::jsonb)""")
        with patch.dict(os.environ, env), patch('screening_worker.time.sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                await asyncio.to_thread(screening_worker.main)
        async with self.pool.acquire() as conn:
            interactive = await run_postgres_check(conn, name='Hyde and Partners', dob=None, country='UK', entity_type='Person')
            stamp_screening_result(interactive, 'Hyde and Partners', None, 'UK', 'Person', 'postgres_beta')
            result = (await screening_db.get_job_status(conn,job))['result']
        self.assertTrue(result['Is Sanctioned'])
        self.assertTrue(result['Input Classification']['likely_misclassified'])
        for key in ('Is Sanctioned','Candidate Evidence','Entity Type Checks','Input Classification'):
            self.assertEqual(result[key],interactive[key])

    async def prepare_postgres_worker(self, manifest=None, name='Jane Smith'):
        async with self.pool.acquire() as conn:
            await conn.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
            await conn.execute('''CREATE TABLE watchlist_entities(source_type text, entity_id text, entity_schema text,
                name text, name_norm text, birth_date date, dataset text, regime text, raw_json jsonb)''')
            await conn.execute("""INSERT INTO watchlist_entities VALUES('sanctions','listed','Person','Jane Smith','jane smith',
                NULL,'OFAC',NULL,'{}'::jsonb)""")
            await conn.execute('INSERT INTO watchlist_source_state VALUES(true,$1::jsonb) ON CONFLICT(singleton) DO UPDATE SET manifest=EXCLUDED.manifest', json.dumps(manifest or self.source_manifest))
            job = await screening_db.enqueue_job(conn, 'pg-worker', name, None, 'UK', 'Person', 'Fixture', 'PG-1', 'Client Onboarding')
        url = os.environ['SANCTIONS_TEST_ADMIN_DSN'].split('?')[0].rsplit('/', 1)[0] + '/' + self.database
        return job, {'DATABASE_URL': url}

    async def test_worker_matches_interactive_screening(self):
        import screening_worker
        from screening_policy import run_postgres_check
        job, env = await self.prepare_postgres_worker()
        with patch.dict(os.environ, env), patch('screening_worker.time.sleep', side_effect=KeyboardInterrupt), \
             patch('screening_worker.current_postgres_manifest', side_effect=lambda url: self.source_manifest):
            with self.assertRaises(KeyboardInterrupt):
                await asyncio.to_thread(screening_worker.main)
            async with self.pool.acquire() as conn:
                saved = (await screening_db.get_job_status(conn, job))['result']
                interactive = await run_postgres_check(conn, name='Jane Smith', dob=None, country='UK', entity_type='Person')
                stamp_screening_result(interactive, 'Jane Smith', None, 'UK', 'Person', 'postgres_beta')
                self.assertEqual(saved['Screening Inputs']['backend'], 'postgres_beta')
                for key in ('Is Sanctioned', 'Candidate Evidence', 'Entity Type Checks', 'Screening Fingerprint'):
                    self.assertEqual(saved[key], interactive[key])
                self.assertEqual(saved['Source Coverage']['version'], 'test-source')
                fingerprint = screening_fingerprint('Jane Smith', None, 'UK', 'Person', 'postgres_beta')
                self.assertIsNotNone(await screening_db.get_valid_screening(conn, 'pg-worker', fingerprint))

    async def test_postgres_worker_refuses_a_stale_snapshot(self):
        from datetime import datetime, timedelta, timezone
        import screening_worker
        stale = dict(self.source_manifest, ingested_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat())
        job, env = await self.prepare_postgres_worker(stale)
        with patch.dict(os.environ, env), patch('screening_worker.time.sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                await asyncio.to_thread(screening_worker.main)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('SELECT status, error_message FROM screening_jobs WHERE job_id=$1::uuid', job)
            self.assertEqual(row['status'], 'failed')
            self.assertIn('stale', row['error_message'])
            self.assertIsNone(await conn.fetchval("SELECT status FROM screened_entities WHERE entity_key='pg-worker'"))

    async def test_monitoring_uses_the_postgres_snapshot_with_a_postgres_worker(self):
        from screening_monitoring import tick
        _, env = await self.prepare_postgres_worker()
        with patch.dict(os.environ, env), patch('screening_monitoring.parquet_manifest', side_effect=AssertionError('Parquet used')):
            async with self.pool.acquire() as conn:
                self.assertIn((await tick(conn))['status'], {'running', 'completed'})

    async def test_idempotent_concurrent_http_submissions_and_payload_conflict(self):
        import httpx
        api_server.app.dependency_overrides[api_server.get_current_user] = lambda: {'sub':'same-user','is_admin':True}
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app),base_url='http://test') as client:
                headers={'Idempotency-Key':'same-submission'}
                data=self.request().model_dump()
                responses=await asyncio.gather(*(client.post('/opcheck',json=data,headers=headers) for _ in range(2)))
                self.assertTrue(all(r.status_code in (200,202) for r in responses))
                self.assertEqual(responses[0].json()['request_id'],responses[1].json()['request_id'])
                replay=await client.post('/opcheck',json=data,headers=headers)
                self.assertEqual(replay.status_code,200,replay.text)
                self.assertEqual(self.match.await_count,1)
                changed=await client.post('/opcheck',json={**data,'country':'US'},headers=headers)
                self.assertEqual(changed.status_code,409)
                async with self.pool.acquire() as conn:
                    self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_requests'),1)
                    self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_runs'),1)
        finally:
            api_server.app.dependency_overrides.clear()

    async def test_idempotency_is_scoped_to_actor_and_route(self):
        first=await api_server._check_opensanctions_impl(self.request(),actor='alice',source='/opcheck',idempotency_key='one')
        second=await api_server._check_opensanctions_impl(self.request(),actor='bob',source='/opcheck',idempotency_key='one')
        third=await api_server._check_opensanctions_impl(self.request(),actor='alice',source='/opcheck/dataverse',idempotency_key='one')
        self.assertEqual(len({first['request_id'],second['request_id'],third['request_id']}),3)

    async def test_abandoned_submission_recovery_and_linked_retry(self):
        async with self.pool.acquire() as conn:
            original,_=await screening_history.begin_submission(conn,self.request().model_dump(),'alice','screening','lost')
            await conn.execute("UPDATE screening_requests SET expires_at=clock_timestamp()-interval '1 second' WHERE request_id=$1::uuid",original)
            record=await screening_history.get_request(conn,original)
            self.assertEqual(record['status'],'failed')
            self.assertEqual(record['response_json']['error'],'request_interrupted')
            self.assertIsNone(record['run_id'])
        replay=await api_server._check_opensanctions_impl(self.request(),actor='alice',idempotency_key='lost')
        self.assertEqual(replay.status_code,503)
        self.assertEqual(self.match.await_count,0)
        retry=self.request(); retry.retry_of=original
        wrong_actor=await api_server._check_opensanctions_impl(retry,actor='bob',idempotency_key='retry')
        self.assertEqual(wrong_actor.status_code,409)
        result=await api_server._check_opensanctions_impl(retry,actor='alice',idempotency_key='retry')
        self.assertNotEqual(result['request_id'],original)
        async with self.pool.acquire() as conn:
            saved=await screening_history.get_request(conn,result['request_id'])
            self.assertEqual(str(saved['retry_of']),original)
            self.assertEqual((await screening_history.get_request(conn,original))['status'],'failed')

    async def test_expired_execution_cannot_publish_result_or_enqueue(self):
        async with self.pool.acquire() as conn:
            request_id,_=await screening_history.begin_submission(conn,self.request().model_dump(),'alice','screening','expire')
            await conn.execute("UPDATE screening_requests SET expires_at=clock_timestamp()-interval '1 second'")
            token=screening_history.active_request.set(request_id)
            try:
                with self.assertRaises(screening_history.RequestExpired):
                    await screening_db.upsert_screening(conn,'expired','Example Ltd','example ltd',None,'UK','Organization','Alice','REF','Client Onboarding',sample())
                with self.assertRaises(screening_history.RequestExpired):
                    await screening_db.enqueue_job(conn,'expired','Example Ltd',None,'UK','Organization','Alice','REF','Client Onboarding')
            finally:
                screening_history.active_request.reset(token)
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screened_entities'),0)
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_jobs'),0)
            self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_runs'),0)

    async def test_run_and_request_response_commit_together(self):
        async with self.pool.acquire() as conn:
            request_id,_=await screening_history.begin_submission(conn,self.request().model_dump(),'alice','screening','atomic')
            token=screening_history.active_request.set(request_id)
            try:
                result=sample()
                await screening_db.upsert_screening(conn,'atomic','Example Ltd','example ltd',None,'UK','Organization','Alice','REF','Client Onboarding',result)
                # Simulate losing the HTTP response immediately after database publication.
                saved=await screening_history.get_request(conn,request_id)
                self.assertEqual(saved['status'],'completed')
                self.assertEqual(saved['response_json']['run_id'],result['run_id'])
                self.assertEqual(saved['response_json']['request_id'],request_id)
            finally:
                screening_history.active_request.reset(token)

    async def test_duplicate_inflight_returns_pending_without_second_execution(self):
        entered=asyncio.Event(); release=asyncio.Event()
        async def slow_match(*args,**kwargs):
            entered.set()
            await release.wait()
            return sample()
        self.match.side_effect=slow_match
        task=asyncio.create_task(api_server._check_opensanctions_impl(self.request(),actor='alice',idempotency_key='inflight'))
        try:
            await asyncio.wait_for(entered.wait(),2)
            duplicate=await api_server._check_opensanctions_impl(self.request(),actor='alice',idempotency_key='inflight')
            self.assertEqual(duplicate.status_code,202)
            body=json.loads(duplicate.body)
            self.assertEqual(body['status'],'requested')
            self.assertEqual(self.match.await_count,1)
            async with self.pool.acquire() as conn:
                await screening_history.recover_expired(conn)
                self.assertEqual(await conn.fetchval('SELECT status FROM screening_requests'),'requested')
        finally:
            release.set()
            result=await task
        self.assertEqual(result['request_id'],body['request_id'])

    async def test_failed_response_replay_preserves_status_and_body(self):
        data=self.request(); data.dob='31-02-1980'
        first=await api_server._check_opensanctions_impl(data,actor='alice',idempotency_key='invalid-date')
        second=await api_server._check_opensanctions_impl(data,actor='alice',idempotency_key='invalid-date')
        self.assertEqual(first.status_code,400)
        self.assertEqual(second.status_code,400)
        self.assertEqual(json.loads(first.body),json.loads(second.body))
        self.assertEqual(self.match.await_count,0)

    async def test_retention_preview_counts_dependencies_without_changing_records(self):
        from datetime import datetime, timezone
        from retention_report import preview
        async with self.pool.acquire() as conn:
            await conn.execute("""INSERT INTO screened_entities(entity_key,display_name,normalized_name,entity_type,
                last_screened_at,screening_valid_until,status,risk_level,confidence,score,result_json)
                VALUES('old-retention','Synthetic Secret Name','synthetic secret name','Person',
                '2020-01-01','2020-12-31','Fail Sanction','High Risk','High',95,'{"Is Sanctioned":true}')""")
            run=await conn.fetchval("SELECT current_run_id FROM screened_entities WHERE entity_key='old-retention'")
            parent=await conn.fetchval("""INSERT INTO screening_requests(actor,source,inputs,status,requested_at,finished_at,idempotency_key)
                VALUES('fixture','test','{}','failed','2020-01-01','2020-01-02','retain-key') RETURNING request_id""")
            await conn.execute("""INSERT INTO screening_requests(source,inputs,status,run_id,retry_of)
                VALUES('test','{}','completed',$1,$2)""",run,parent)
            await conn.execute("""INSERT INTO screening_requests(source,inputs,status,requested_at,expires_at)
                VALUES('test','{}','requested','2020-01-01','2020-01-02')""")
            await conn.execute("""INSERT INTO screening_requests(source,inputs,status,requested_at,finished_at)
                VALUES('test','{}','failed','2025-01-01','2025-01-01')""")
            await conn.execute("""INSERT INTO screening_review_decisions(run_id,entity_key,actor,outcome,notes,decision_type,decided_at)
                VALUES($1,'old-retention','fixture','Confirmed Match - Escalated to Compliance','Synthetic notes','analyst','2020-02-01')""",run)
            await screening_db.enqueue_job(conn,'old-retention','Synthetic Secret Name',None,'UK','Person','Fixture','REF','Client Onboarding')
            # A complete snapshot checks that even overdue request recovery is not invoked by this read-only report.
            from retention_report import TABLES
            before={table:await conn.fetchval(f"SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]')::text FROM {table} t") for table in TABLES}
            report=await preview(conn,datetime(2025,1,1,tzinfo=timezone.utc))
            after={table:await conn.fetchval(f"SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]')::text FROM {table} t") for table in TABLES}
            self.assertEqual(before,after)
            self.assertFalse(report['deletion_enabled'])
            self.assertEqual(report['policy_status'],'awaiting_organisation_policy')
            totals={row['table']:row for row in report['inventory']}
            self.assertEqual(totals['screening_requests']['older_than_cutoff'],2)
            self.assertEqual(totals['screening_runs']['older_than_cutoff'],1)
            for key in ('old_unfinished_requests','old_requests_with_duplicate_protection','old_retry_parents',
                        'old_runs_used_by_current_state','old_runs_used_by_recent_requests','old_runs_with_review_evidence',
                        'old_current_rows_with_open_reviews','old_current_rows_with_active_jobs'):
                self.assertEqual(report['dependencies'][key],1,key)
            self.assertNotIn('Synthetic Secret Name',json.dumps(report,default=str))
            await conn.execute("UPDATE screened_entities SET review_status='COMPLETED',review_outcome='Pending External Review' WHERE entity_key='old-retention'")
            inventory=await preview(conn)
            self.assertEqual(inventory['active']['open_reviews'],1)
            self.assertIsNone(inventory['dependencies'])
            self.assertTrue(all(item['older_than_cutoff'] is None for item in inventory['inventory']))

    async def test_retention_preview_rejects_ambiguous_and_future_cutoffs(self):
        from retention_report import preview
        from datetime import datetime,timezone,timedelta
        async with self.pool.acquire() as conn:
            for cutoff in (datetime(2020,1,1),datetime.now(timezone.utc)+timedelta(days=1)):
                with self.assertRaises(ValueError):
                    await preview(conn,cutoff)

    async def test_retention_preview_http_is_admin_only(self):
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app),base_url='http://test') as client:
            with patch.object(api_server,'get_current_user',AsyncMock(return_value={'sub':'ordinary','is_admin':False})):
                self.assertEqual((await client.get('/admin/screening/retention-preview')).status_code,403)
            with patch.object(api_server,'get_current_user',AsyncMock(return_value={'sub':'admin','is_admin':True})):
                response=await client.get('/admin/screening/retention-preview')
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['mode'],'preview_only')
                self.assertEqual((await client.get('/admin/screening/retention-preview',params={'before':'2020-01-01T00:00:00'})).status_code,400)

    async def test_retention_command_uses_readonly_connection(self):
        from scripts.preview_retention import run
        from urllib.parse import urlsplit
        dsn=urlsplit(os.environ['SANCTIONS_TEST_ADMIN_DSN'])._replace(path='/'+self.database).geturl()
        with patch.dict(os.environ,{'SANCTIONS_REPORT_DSN':dsn}):
            report=await run(None)
        self.assertEqual(report['mode'],'preview_only')
        self.assertEqual(report['policy_status'],'awaiting_organisation_policy')

    async def test_testing_clear_protects_active_work_and_preserves_history(self):
        import httpx
        first = await self.check(self.request())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app), base_url='http://test') as client:
            with patch.object(api_server, 'get_current_user', AsyncMock(return_value={'sub':'admin','is_admin':True})):
                async with self.pool.acquire() as conn:
                    job = await screening_db.enqueue_job(conn, 'pending-clear', 'Pending Ltd', None, 'UK', 'Organization', 'Alice', 'CLEAR', 'Client Onboarding')
                    response = await client.post('/admin/testing/clear-screening-data')
                    self.assertEqual(response.status_code, 409, response.text)
                    self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_jobs'), 1)
                    await conn.execute("UPDATE screening_jobs SET status='failed', finished_at=now() WHERE job_id=$1::uuid", job)
                    for status, outcome in [('IN_REVIEW', None), ('COMPLETED', 'Pending External Review'), (None, None)]:
                        await conn.execute("UPDATE screened_entities SET review_status=$1, review_outcome=$2, status='Fail Sanction'", status, outcome)
                        self.assertEqual((await client.post('/admin/testing/clear-screening-data')).status_code, 409)
                    await conn.execute("UPDATE screened_entities SET review_status='COMPLETED', review_outcome='False Positive', status='Cleared'")
                    # A concurrent writer must prevent the clear even before its work is committed.
                    async with conn.transaction():
                        await conn.execute('LOCK TABLE screening_requests IN ROW EXCLUSIVE MODE')
                        self.assertEqual((await client.post('/admin/testing/clear-screening-data')).status_code, 409)
                    request_id, _ = await screening_history.begin_submission(conn, {}, 'admin', 'test')
                    self.assertEqual((await client.post('/admin/testing/clear-screening-data')).status_code, 409)
                    await screening_history.finish(conn, request_id, {'error':'test'}, failed=True)
                response = await client.post('/admin/testing/clear-screening-data')
                self.assertEqual(response.status_code, 200, response.text)
                async with self.pool.acquire() as conn:
                    self.assertEqual(await conn.fetchval('SELECT count(*) FROM screened_entities'), 0)
                    self.assertEqual(await conn.fetchval('SELECT count(*) FROM screening_jobs'), 0)
                    self.assertEqual((await screening_history.get_request(conn, first['request_id']))['status'], 'completed')

    async def test_terminal_queued_replay_includes_own_request_id(self):
        async with self.pool.acquire() as conn:
            request_id, _ = await screening_history.begin_submission(conn, {}, 'alice', 'screening', 'queued-key')
            token = screening_history.active_request.set(request_id)
            try:
                job = await screening_db.enqueue_job(conn, 'replay-queue', 'Replay Ltd', None, 'UK', 'Organization', 'Alice', 'REPLAY', 'Client Onboarding')
            finally:
                screening_history.active_request.reset(token)
            await conn.execute("UPDATE screening_jobs SET status='failed',finished_at=now() WHERE job_id=$1::uuid", job)
            _, existing = await screening_history.begin_submission(conn, {}, 'alice', 'screening', 'queued-key')
            response = api_server._saved_submission_response(existing)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(json.loads(response.body)['request_id'], request_id)

    async def test_acceptance_export_selection_evidence_and_readonly(self):
        from acceptance_export import collect, build_archive
        import csv
        import io
        import zipfile
        first = await self.adverse_run()
        async with self.pool.acquire() as conn:
            await screening_db.mark_false_positive(conn, entity_key=first['entity_key'], actor='reviewer',
                reason='=Untrusted formula-like note', expected_run_id=first['run_id'])
            # Later runs must not hide the old decision, or duplicate it via current state.
            self.match.side_effect = lambda *a, **kw: sample()
            later = self.request(); later.country = 'US'
            await self.check(later)
            for name in ('Clear One', 'Clear Two', 'Legacy Match', 'Pending External'):
                data = self.request(); data.name = name
                await self.check(data)
            await conn.execute("""UPDATE screened_entities SET review_status='COMPLETED',
                review_outcome='Confirmed Match - Escalated to Compliance', review_notes='Legacy reviewer notes',
                review_completed_by='legacy', review_completed_at=now() WHERE display_name='Legacy Match'""")
            await conn.execute("""UPDATE screened_entities SET review_status='COMPLETED',
                review_outcome='Pending External Review' WHERE display_name='Pending External'""")
            tables = ('screened_entities', 'screening_requests', 'screening_runs', 'screening_review_decisions', 'screening_review_events')
            async def snapshot():
                return [await conn.fetchval(f'SELECT jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text) FROM {table} t') for table in tables]
            before = await snapshot()
            cases, counts = await collect(conn, 1, 1)
            self.assertEqual(before, await snapshot())
            self.assertEqual(len(cases), 3)
            self.assertEqual(counts['cleared_unreviewed'], {'available':3, 'exported':1})
            reviewed = next(c for c in cases if c['category']=='false_positive')
            self.assertEqual(str(reviewed['run_id']), first['run_id'])
            self.assertTrue(reviewed['result_json']['Is Sanctioned'])
            self.assertEqual(reviewed['evidence_source'], 'saved_review_decision')
            self.assertEqual(next(c for c in cases if c['category']=='confirmed_match')['evidence_source'], 'current_state')
            without_clear, _ = await collect(conn, 50, 0)
            self.assertEqual(len(without_clear), 2)
            with zipfile.ZipFile(io.BytesIO(build_archive(cases, counts))) as archive:
                self.assertEqual(set(archive.namelist()), {'cases.csv','evidence.json','manifest.json','README.txt'})
                rows = list(csv.DictReader(io.StringIO(archive.read('cases.csv').decode('utf-8-sig'))))
                self.assertTrue(all(r['expected_outcome']=='' and r['acceptance_reviewer']=='' for r in rows))
                self.assertEqual(next(r for r in rows if r['category']=='false_positive')['previous_reason'], "'=Untrusted formula-like note")
                evidence = json.loads(archive.read('evidence.json'))
                self.assertEqual(next(r for r in evidence if r['category']=='false_positive')['notes'], '=Untrusted formula-like note')

    async def test_acceptance_export_http_access_limits_empty_and_download(self):
        import httpx
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app),base_url='http://test') as client:
            with patch.object(api_server,'get_current_user',AsyncMock(return_value={'sub':'ordinary','is_admin':False})):
                self.assertEqual((await client.get('/admin/screening/acceptance-export')).status_code,403)
            with patch.object(api_server,'get_current_user',AsyncMock(return_value={'sub':'admin','is_admin':True})):
                self.assertEqual((await client.get('/admin/screening/acceptance-export')).status_code,404)
                self.assertEqual((await client.get('/admin/screening/acceptance-export?reviewed_limit=251')).status_code,422)
                self.assertEqual((await client.get('/admin/screening/acceptance-export?cleared_limit=-1')).status_code,422)
                await self.check(self.request())
                response = await client.get('/admin/screening/acceptance-export')
                self.assertEqual(response.status_code,200,response.text[:100])
                self.assertEqual(response.headers['content-type'],'application/zip')
                self.assertEqual(response.headers['cache-control'],'no-store')
                self.assertIn('attachment;',response.headers['content-disposition'])

    async def test_rollout_readiness_schema_contract_and_verify_only_startup(self):
        from deployment_runtime import install_schema, verify_schema, schema_contract
        import httpx
        async with self.pool.acquire() as conn:
            await install_schema(conn)
            await verify_schema(conn)
            await conn.execute('CREATE TABLE watchlist_entities(source_type text, entity_id text, entity_schema text, name text, name_norm text, dataset text)')
            await conn.executemany("INSERT INTO watchlist_entities(source_type, entity_id, entity_schema, name, name_norm, dataset) VALUES('sanctions',$1,'Person','Fixture','fixture',$2)",
                                   [('uk', 'UK Sanctions List (FCDO)'), ('un', 'UN Security Council Consolidated List'),
                                    ('eu', 'EU Consolidated Financial Sanctions List'), ('ofac', 'OFAC SDN List')])
            await conn.execute('INSERT INTO watchlist_source_state VALUES(true,$1::jsonb)', json.dumps(self.source_manifest))
        with patch.dict(os.environ, {'SANCTIONS_SCHEMA_MODE':'verify', 'RELEASE_ID':'fixture-release','SEED_DEFAULT_ADMIN':'false'}), \
             patch.object(api_server,'_validate_jwt_secret'), patch.object(screening_db,'ensure_schema',AsyncMock()) as ensure, \
             patch.object(screening_db,'close_pool',AsyncMock()):
            async with api_server.lifespan(api_server.app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app),base_url='http://test') as client:
                    response = await client.get('/ready')
                    self.assertEqual(response.status_code,200,response.text)
                    self.assertEqual(response.json()['release'],'fixture-release')
                    self.assertEqual(response.json()['schema_contract'],schema_contract())
                    ensure.assert_not_called()
                    async with self.pool.acquire() as conn:
                        await conn.execute("DELETE FROM watchlist_entities WHERE entity_id='eu'")
                    self.assertEqual((await client.get('/ready')).status_code,503)
                    async with self.pool.acquire() as conn:
                        await conn.execute("DELETE FROM watchlist_entities WHERE source_type='sanctions'")
                    self.assertEqual((await client.get('/ready')).status_code,503)
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE deployment_schema SET contract='incompatible'")
            with self.assertRaisesRegex(RuntimeError,'does not match'):
                await verify_schema(conn)
