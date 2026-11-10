"""Finding and bulk-flagging benchmark screenings, against disposable PostgreSQL only."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from urllib.parse import urlparse, urlunparse
from uuid import UUID

import screening_db
from scripts.find_test_screenings import find, is_selected, selection_from
from scripts.flag_test_screenings import flag_all
from tests import test_internal_improvements as fixtures
from utils import _empty_no_match_result, stamp_screening_result

DSN = os.environ.get('SANCTIONS_TEST_ADMIN_DSN')
WINDOW = ('2026-11-05T00:00:00+00:00', '2026-11-06T00:00:00+00:00')
IN_WINDOW = datetime.fromisoformat('2026-11-05T12:00:00+00:00')
BEFORE_WINDOW = datetime.fromisoformat('2026-11-04T12:00:00+00:00')
REASON = 'Created by the 5 November matcher benchmark, not a customer.'


def selection(**overrides):
    args = dict(start=WINDOW[0], end=WINDOW[1], actor=None, source=None, requestor=None, reference_prefix=None,
                reference_regex=None, listed_names_only=False, window_only=False)
    args.update(overrides)
    return selection_from(argparse.Namespace(**args))


class SelectionTests(unittest.TestCase):
    def test_a_time_window_alone_must_be_asked_for_explicitly(self):
        with self.assertRaisesRegex(ValueError, 'window-only'):
            selection()
        self.assertTrue(selection(window_only=True)['window_only'])
        with self.assertRaisesRegex(ValueError, 'before the end'):
            selection(actor=['bench'], start=WINDOW[1], end=WINDOW[0])

    def test_every_given_selector_must_match(self):
        request = {'actor': 'bench-key', 'source': '/opcheck', 'business_reference': 'BENCH-0042',
                   'inputs': json.dumps({'requestor': 'Benchmark Runner'})}
        for chosen, expected in ((dict(actor=['bench-key']), True), (dict(actor=['someone-else']), False),
                                 (dict(requestor=['benchmark runner']), True), (dict(reference_prefix=['BENCH-']), True),
                                 (dict(reference_regex=r'^BENCH-\d{4}$'), True), (dict(reference_prefix=['POLICY-']), False),
                                 (dict(actor=['bench-key'], source=['/opcheck/dataverse']), False)):
            self.assertEqual(is_selected(request, selection(**chosen)), expected, chosen)


@unittest.skipUnless(DSN, 'Requires disposable PostgreSQL')
class ToolTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.InternalDatabaseTests.asyncSetUp
    asyncTearDown = fixtures.InternalDatabaseTests.asyncTearDown

    async def screening(self, key, name, reference='BENCH-1'):
        result = _empty_no_match_result('fixture')
        result.update({'Is Sanctioned': True, 'Match Found': True, 'Score': 100})
        result['Check Summary']['Status'] = 'Fail Sanction'
        stamp_screening_result(result, name, None, None, 'Person', 'postgres_beta')
        await screening_db.upsert_screening(self.conn, key, name, name.lower(), None, None, 'Person', 'Benchmark',
                                            reference, 'Ad-Hoc Compliance Review', result)
        return UUID(result['run_id'])

    async def request(self, run, actor='bench-key', when=IN_WINDOW, reference='BENCH-1', source='/opcheck',
                      requestor='Benchmark Runner'):
        await self.conn.execute('''INSERT INTO screening_requests(actor,source,inputs,business_reference,status,run_id,requested_at)
            VALUES($1,$2,$3::jsonb,$4,'completed',$5,$6::timestamptz)''', actor, source,
            json.dumps({'requestor': requestor}), reference, run, when)

    async def monitoring(self, run, when=IN_WINDOW, reference='BENCH-1'):
        """What a monitoring re-screen leaves in the request history."""
        await self.request(run, actor=None, when=when, reference=reference, source='background_job',
                           requestor='system:source-monitoring')

    async def seed(self):
        runs = {}
        for key, name in (('only-bench', 'Alpha One'), ('mixed', 'Bravo Two'), ('real', 'Charlie Three'),
                          ('old-bench', 'Delta Four'), ('flagged', 'Echo Five'), ('busy', 'Foxtrot Six')):
            runs[key] = await self.screening(key, name)
        for key in ('only-bench', 'mixed', 'flagged', 'busy'):
            await self.request(runs[key])
        await self.request(runs['only-bench'])                                   # the benchmark searched it twice
        await self.request(runs['mixed'], actor='alice', reference='POLICY-9')   # so did a real user
        await self.request(runs['real'], actor='alice', reference='POLICY-7')
        await self.request(runs['old-bench'], when=BEFORE_WINDOW)
        async with self.conn.transaction():
            await screening_db.set_test_flag(self.conn, entity_key='flagged', expected_run=runs['flagged'],
                                             active=True, reason=REASON, actor='admin')
        await screening_db.enqueue_job(self.conn, entity_key='busy', name='Foxtrot Six', date_of_birth=None, country=None,
                                       entity_type='Person', requestor='tester', business_reference='BENCH-1',
                                       reason_for_check='Periodic Re-Screen', reason='manual', force_rescreen=True)
        return runs

    async def flags(self):
        return {r['entity_key'] for r in await self.conn.fetch('SELECT entity_key FROM screening_test_flags WHERE active')}

    async def inventory(self, **chosen):
        return await find(self.conn, selection(**(chosen or {'actor': ['bench-key']})))

    async def test_finder_classifies_screenings_and_writes_nothing(self):
        await self.seed()
        report = await self.inventory()
        found = {e['entity_key']: e for e in report['entities']}
        self.assertEqual(set(found), {'only-bench', 'mixed', 'flagged', 'busy'})   # not the real or out-of-window ones
        self.assertEqual(found['only-bench']['status'], 'eligible')
        self.assertEqual(found['only-bench']['selected_requests'], 2)
        self.assertEqual((found['mixed']['status'], found['mixed']['reasons']),
                         ('needs_review', ['also_requested_outside_the_selection']))
        self.assertEqual(found['mixed']['requests_outside_selection'], 1)
        self.assertEqual((found['flagged']['status'], found['flagged']['reasons']), ('skipped', ['already_flagged']))
        self.assertEqual((found['busy']['status'], found['busy']['reasons']), ('needs_review', ['check_queued_or_running']))
        self.assertEqual(report['requests_selected'], 5)
        self.assertEqual(await self.flags(), {'flagged'})
        # Other selectors reach the same benchmark requests; a reference nobody used reaches none.
        self.assertEqual({e['entity_key'] for e in (await self.inventory(reference_prefix=['BENCH-']))['entities']}, set(found))
        self.assertEqual((await self.inventory(reference_prefix=['NOPE-']))['entities'], [])
        # Selecting the whole window makes the real user's request part of the selection too.
        everything = {e['entity_key']: e['status'] for e in (await self.inventory(window_only=True))['entities']}
        self.assertEqual(everything['real'], 'eligible')

    async def test_monitoring_rescreens_are_neither_selected_nor_counted_as_someone_else(self):
        runs = await self.seed()
        for _ in range(3):
            await self.monitoring(runs['only-bench'])
        await self.monitoring(runs['real'], reference='POLICY-7')
        await self.monitoring(runs['old-bench'], reference='UK-DELTA-0a1b2c3d')     # an older monitoring generation
        internal = await self.screening('internal-job', 'Hotel Eight')
        await self.request(internal)
        await self.request(internal, actor=None, source='background_job', requestor='Claims Team', reference='CLAIM-5')
        report = await self.inventory()
        found = {e['entity_key']: e for e in report['entities']}
        self.assertEqual((found['only-bench']['status'], found['only-bench']['system_rescreens_ignored']), ('eligible', 3))
        # A background job somebody submitted is a real request for that record.
        self.assertEqual(found['internal-job']['status'], 'needs_review')
        self.assertEqual(found['internal-job']['outside_selection_by_source_and_login'], {'background_job / no login': 1})
        # Selecting the whole window must not sweep up records that were only monitored in it.
        everything = {e['entity_key'] for e in (await self.inventory(window_only=True))['entities']}
        self.assertNotIn('old-bench', everything)
        self.assertEqual(report['system_rescreens_ignored'], 3)
        # A monitoring request recorded after the report does not block the flag.
        await self.monitoring(runs['only-bench'], when=datetime.fromisoformat('2099-01-01T00:00:00+00:00'))
        done = await flag_all(self.conn, report, REASON, 'greg', execute=True)
        self.assertIn('only-bench', await self.flags())
        self.assertEqual(done['counts']['flagged'], 1)

    async def test_dry_run_changes_nothing_and_execute_flags_only_eligible_records(self):
        await self.seed()
        report = await self.inventory()
        dry = await flag_all(self.conn, report, REASON, 'greg', execute=False)
        self.assertEqual(dry['mode'], 'dry_run')
        self.assertEqual(dry['counts'], {'would_flag': 1, 'not_attempted': 3})
        self.assertEqual(await self.flags(), {'flagged'})
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_test_flag_events'), 1)

        done = await flag_all(self.conn, report, REASON, 'greg', execute=True)
        self.assertEqual(done['counts'], {'flagged': 1, 'not_attempted': 3})
        self.assertEqual(done['status'], 'complete')
        self.assertEqual(await self.flags(), {'flagged', 'only-bench'})
        event = await self.conn.fetchrow("SELECT actor,reason,active FROM screening_test_flag_events WHERE entity_key='only-bench'")
        self.assertEqual((event['actor'], event['reason'], event['active']), ('bulk-test-flag:greg', REASON, True))
        queue = {item['entity_key'] for item in await screening_db.list_review_queue(self.conn)}
        self.assertNotIn('only-bench', queue)                                     # hidden from review
        self.assertIn('mixed', queue)                                             # the shared record is untouched
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screened_entities'), 6)   # nothing deleted

        again = await flag_all(self.conn, report, REASON, 'greg', execute=True)
        self.assertEqual(again['counts'], {'skipped': 1, 'not_attempted': 3})
        self.assertEqual(again['status'], 'needs_attention')

    async def test_records_that_changed_since_the_report_are_skipped(self):
        runs = await self.seed()
        await self.screening('second-bench', 'Golf Seven')
        await self.request(await self.conn.fetchval("SELECT current_run_id FROM screened_entities WHERE entity_key='second-bench'"))
        report = await self.inventory()
        await self.screening('only-bench', 'Alpha One')                           # screened again: a new run
        await self.conn.execute('''INSERT INTO screening_requests(actor,source,inputs,business_reference,status,run_id)
            SELECT 'alice','/opcheck','{}'::jsonb,'POLICY-3','completed',current_run_id
            FROM screened_entities WHERE entity_key='second-bench' ''')           # a real search reused the result
        result = await flag_all(self.conn, report, REASON, 'greg', execute=True)
        reasons = {d['entity_key']: d.get('reason') for d in result['details']}
        self.assertIn('has changed', reasons['only-bench'])
        self.assertEqual(reasons['second-bench'], 'requested_again_since_the_report')
        self.assertEqual(await self.flags(), {'flagged'})
        self.assertEqual(runs['only-bench'] == await self.conn.fetchval(
            "SELECT current_run_id FROM screened_entities WHERE entity_key='only-bench'"), False)

    async def test_wrong_database_bad_reason_and_foreign_reports_are_refused(self):
        await self.seed()
        report = await self.inventory()
        for bad, pattern in (({**report, 'database_identity': {**report['database_identity'], 'oid': '0'}}, 'endpoint'),
                             ({**report, 'database': 'elsewhere'}, 'different database'),
                             ({**report, 'kind': 'release_inventory'}, 'Unsupported report')):
            with self.assertRaisesRegex(ValueError, pattern):
                await flag_all(self.conn, bad, REASON, 'greg', execute=True)
        with self.assertRaisesRegex(ValueError, 'reason'):
            await flag_all(self.conn, report, 'too short', 'greg', execute=True)
        with self.assertRaisesRegex(ValueError, 'responsible'):
            await flag_all(self.conn, report, REASON, ' ', execute=True)
        self.assertEqual(await self.flags(), {'flagged'})

    async def test_command_line_is_a_dry_run_unless_execute_is_given(self):
        await self.seed()
        parsed = urlparse(DSN)
        env = {**os.environ, 'DATABASE_URL': urlunparse(parsed._replace(path='/' + self.database))}
        scripts = Path(__file__).parents[1] / 'scripts'
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            report, dry, done = (Path(folder) / name for name in ('report.json', 'dry.json', 'done.json'))
            found = subprocess.run([sys.executable, str(scripts / 'find_test_screenings.py'), '--start', WINDOW[0],
                                    '--end', WINDOW[1], '--actor', 'bench-key', '--output', str(report)],
                                   env=env, capture_output=True, text=True)
            self.assertEqual(found.returncode, 0, found.stderr)
            self.assertEqual(oct(report.stat().st_mode & 0o777), '0o600')
            digest = hashlib.sha256(report.read_bytes()).hexdigest()
            command = [sys.executable, str(scripts / 'flag_test_screenings.py'), '--inventory', str(report),
                       '--reason', REASON, '--actor', 'greg']
            wrong = subprocess.run(command + ['--inventory-sha256', '0' * 64, '--output', str(dry), '--execute'],
                                   env=env, capture_output=True, text=True)
            self.assertNotEqual(wrong.returncode, 0)
            self.assertFalse(dry.exists())
            rehearsal = subprocess.run(command + ['--inventory-sha256', digest, '--output', str(dry)],
                                       env=env, capture_output=True, text=True)
            self.assertEqual(rehearsal.returncode, 0, rehearsal.stderr)
            self.assertEqual(json.loads(dry.read_text())['counts']['would_flag'], 1)
            self.assertEqual(await self.flags(), {'flagged'})
            real = subprocess.run(command + ['--inventory-sha256', digest, '--output', str(done), '--execute'],
                                  env=env, capture_output=True, text=True)
            self.assertEqual(real.returncode, 0, real.stderr)
            self.assertEqual(await self.flags(), {'flagged', 'only-bench'})


if __name__ == '__main__':
    unittest.main()
