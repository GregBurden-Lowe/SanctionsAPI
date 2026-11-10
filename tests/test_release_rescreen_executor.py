"""Exercise release rescreen publication against disposable PostgreSQL only."""
import asyncio
import json
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg
import screening_db
from scripts.execute_release_rescreen import execute, screen, ACTOR, SOURCE
from scripts.plan_release_rescreen import preview, parse_time
from utils import _empty_no_match_result, rules_version_for, stamp_screening_result

DSN=os.environ.get('SANCTIONS_TEST_ADMIN_DSN')


def rules():
    """The version the runtime stamps on results, which the executor must be given exactly."""
    return rules_version_for('postgres_beta')


@unittest.skipUnless(DSN,'Requires isolated local PostgreSQL')
class RescreenTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.admin=await asyncpg.connect(DSN)
        self.database='sanctions_release_execute_'+uuid4().hex
        await self.admin.execute(f'CREATE DATABASE "{self.database}"')
        self.conn=await asyncpg.connect(DSN,database=self.database)
        await screening_db.ensure_schema(self.conn)
        from urllib.parse import urlparse,urlunparse
        p=urlparse(DSN)
        self.dsn=urlunparse(p._replace(path='/'+self.database))
        self.env=patch.dict(os.environ,{'DATABASE_URL':self.dsn})
        self.env.start()
        self.addCleanup(self.env.stop)

    async def asyncTearDown(self):
        await self.conn.close()
        await self.admin.execute(f'DROP DATABASE "{self.database}"')
        await self.admin.close()

    def result(self, backend='postgres_beta'):
        result=_empty_no_match_result('fixture')
        stamp_screening_result(result,'Jane Smith',None,None,'Person',backend)
        result['Source Coverage']={'status':'current','version':'fixture'}
        return result

    async def seed(self, *, references=('REF-1','REF-2'), backend='postgres_beta'):
        result=self.result(backend)
        await screening_db.upsert_screening(self.conn,'entity','Jane Smith','jane smith',None,None,'Person',
                'Alice','REF-1','Client Onboarding',result)
        run=UUID(result['run_id'])
        inputs={'name':'Jane Smith','dob':None,'country':None,'entity_type':'Person','search_backend':backend}
        for ref in references:
            await self.conn.execute('''INSERT INTO screening_requests(source,inputs,business_reference,status,run_id,requested_at)
                VALUES('/opcheck',$1::jsonb,$2,'completed',$3,'2026-11-02T12:00:00+00')''',json.dumps(inputs),ref,run)
        plan=await preview(parse_time('2026-11-02T00:00:00+00:00'))
        return plan,run

    async def test_fresh_group_links_each_request_and_is_idempotent(self):
        plan,old_run=await self.seed()
        old=await self.conn.fetchval('SELECT result_json FROM screening_runs WHERE run_id=$1',old_run)
        with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=lambda *a:self.result())) as matcher:
            report=await execute(self.conn,plan,rules())
            self.assertEqual(matcher.await_count,1)
            self.assertEqual(report['counts']['completed'],2)
            self.assertEqual(report['status'],'complete')
            again=await execute(self.conn,plan,rules())
            self.assertEqual(matcher.await_count,1)
            self.assertEqual(again['counts']['already_completed'],2)
        rows=await self.conn.fetch("SELECT * FROM screening_requests WHERE source=$1",SOURCE)
        self.assertEqual(len(rows),2)
        self.assertEqual({r['business_reference'] for r in rows},{'REF-1','REF-2'})
        self.assertEqual(len({r['run_id'] for r in rows}),1)
        self.assertNotEqual(rows[0]['run_id'],old_run)
        self.assertEqual(await self.conn.fetchval('SELECT current_run_id FROM screened_entities WHERE entity_key=$1','entity'),rows[0]['run_id'])
        self.assertEqual(await self.conn.fetchval('SELECT result_json FROM screening_runs WHERE run_id=$1',old_run),old)
        self.assertEqual({json.loads(r['inputs'])['original_request_id'] for r in rows}, {c['request_id'] for c in plan['cases']})
        self.assertTrue(all(not r['reused'] for r in rows))

    async def test_changed_current_is_saved_to_history_without_overwrite_and_remains_attention_on_retry(self):
        plan,old_run=await self.seed(references=('REF-1',))
        newer=self.result()
        await screening_db.upsert_screening(self.conn,'entity','Jane Smith','jane smith',None,None,'Person',
                'Alice','REF-1','Client Onboarding',newer)
        new_current=UUID(newer['run_id'])
        with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=lambda *a:self.result())):
            report=await execute(self.conn,plan,rules())
            again=await execute(self.conn,plan,rules())
        self.assertEqual(report['counts']['history_only'],1)
        self.assertEqual(again['status'],'needs_attention')
        self.assertEqual(await self.conn.fetchval('SELECT current_run_id FROM screened_entities WHERE entity_key=$1','entity'),new_current)
        rescreen=await self.conn.fetchrow('SELECT run_id,inputs FROM screening_requests WHERE source=$1',SOURCE)
        self.assertNotEqual(rescreen['run_id'],new_current)
        self.assertFalse(json.loads(rescreen['inputs'])['current_state_updated'])

    async def test_failure_and_active_job_leave_existing_results_unchanged(self):
        plan,old_run=await self.seed(references=('REF-1',))
        with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=RuntimeError('unavailable'))):
            report=await execute(self.conn,plan,rules())
        self.assertEqual(report['status'],'needs_attention')
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_requests WHERE source=$1',SOURCE),0)
        self.assertEqual(await self.conn.fetchval('SELECT current_run_id FROM screened_entities WHERE entity_key=$1','entity'),old_run)
        await screening_db.enqueue_job(self.conn,'entity','Jane Smith',None,None,'Person','Alice','REF-1','Client Onboarding')
        with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=lambda *a:self.result())) as matcher:
            report=await execute(self.conn,plan,rules())
        self.assertEqual(report['counts']['unresolved'],1)
        self.assertEqual(matcher.await_count,0)

    async def test_active_job_arriving_during_screening_blocks_publication(self):
        plan,old_run=await self.seed(references=('REF-1',))
        async def race(*args):
            await screening_db.enqueue_job(self.conn,'entity','Jane Smith',None,None,'Person','Alice','REF-1','Client Onboarding')
            return self.result()
        with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=race)):
            report=await execute(self.conn,plan,rules())
        self.assertEqual(report['counts']['unresolved'],1)
        self.assertEqual(await self.conn.fetchval('SELECT current_run_id FROM screened_entities WHERE entity_key=$1','entity'),old_run)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_requests WHERE source=$1',SOURCE),0)

    async def test_changed_review_is_not_reset(self):
        plan,old_run=await self.seed(references=('REF-1',))
        await self.conn.execute("UPDATE screened_entities SET review_status='COMPLETED', review_outcome='Reviewed' WHERE entity_key='entity'")
        with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=lambda *a:self.result())):
            report=await execute(self.conn,plan,rules())
        self.assertEqual(report['counts']['history_only'],1)
        row=await self.conn.fetchrow("SELECT current_run_id,review_status,review_outcome FROM screened_entities WHERE entity_key='entity'")
        self.assertEqual(row['current_run_id'],old_run)
        self.assertEqual(row['review_status'],'COMPLETED')
        self.assertEqual(row['review_outcome'],'Reviewed')

    async def test_prior_analyst_decision_is_preserved_but_not_automatically_reused(self):
        from review_decisions import write_decision
        _,old_run=await self.seed(references=('REF-1',))
        row=await self.conn.fetchrow("SELECT * FROM screened_entities WHERE entity_key='entity'")
        await write_decision(self.conn,row,'reviewer','False Positive - Proceeded','Prior reviewed decision')
        plan=await preview(parse_time('2026-11-02T00:00:00+00:00'))
        with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=lambda *a:self.result())):
            report=await execute(self.conn,plan,rules())
        self.assertEqual(report['status'],'complete')
        saved=await self.conn.fetchrow('SELECT outcome,notes,run_id FROM screening_review_decisions WHERE run_id=$1',old_run)
        self.assertEqual(saved['notes'],'Prior reviewed decision')
        self.assertEqual(saved['outcome'],'False Positive - Proceeded')
        self.assertIsNone(await self.conn.fetchval("SELECT review_decision FROM screened_entities WHERE entity_key='entity'"))
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_review_decisions WHERE entity_key=$1','entity'),1)

    async def test_review_in_progress_is_not_reset(self):
        plan,old_run=await self.seed(references=('REF-1',))
        await self.conn.execute("UPDATE screened_entities SET review_status='IN_REVIEW',review_claimed_by='Alice' WHERE entity_key='entity'")
        plan=await preview(parse_time('2026-11-02T00:00:00+00:00'))
        with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=lambda *a:self.result())):
            report=await execute(self.conn,plan,rules())
        self.assertEqual(report['counts']['history_only'],1)
        self.assertEqual(await self.conn.fetchval("SELECT current_run_id FROM screened_entities WHERE entity_key='entity'"),old_run)
        self.assertEqual(await self.conn.fetchval("SELECT review_claimed_by FROM screened_entities WHERE entity_key='entity'"),'Alice')

    async def test_group_failure_rolls_back_current_and_all_request_links(self):
        plan,old_run=await self.seed()
        from screening_guidance import describe_change
        calls=0
        def fail_after_publication_started(*args):
            nonlocal calls
            calls+=1
            if calls==2:
                raise RuntimeError('synthetic failure')
            return describe_change(*args)
        with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=lambda *a:self.result())), \
             patch('scripts.execute_release_rescreen.describe_change',side_effect=fail_after_publication_started):
            report=await execute(self.conn,plan,rules())
        self.assertEqual(report['counts']['unresolved'],2)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM screening_requests WHERE source=$1",SOURCE),0)
        self.assertEqual(await self.conn.fetchval("SELECT current_run_id FROM screened_entities WHERE entity_key='entity'"),old_run)

    async def test_stale_request_and_mismatched_backend_are_not_executed(self):
        plan,old_run=await self.seed(references=('REF-1',))
        await self.conn.execute("UPDATE screening_requests SET status='failed' WHERE request_id=$1",UUID(plan['cases'][0]['request_id']))
        with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=lambda *a:self.result())) as matcher:
            report=await execute(self.conn,plan,rules())
        self.assertEqual(report['counts']['unresolved'],1)
        self.assertEqual(matcher.await_count,0)
        self.assertEqual(await self.conn.fetchval("SELECT current_run_id FROM screened_entities WHERE entity_key='entity'"),old_run)

    async def test_wrong_rules_version_rejected_before_any_screening(self):
        plan,_=await self.seed(references=('REF-1',))
        with self.assertRaisesRegex(ValueError,'matching version'):
            await execute(self.conn,plan,'evidence-v7')
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_requests WHERE source=$1',SOURCE),0)

    async def test_long_rules_version_and_dob_required_result_are_published(self):
        import utils
        from name_matching import TokenStats
        plan,_=await self.seed(references=('REF-1',))
        with patch.dict(os.environ,{'SCREENING_MATCHER_VERSION':'v2','SCREENING_EVIDENCE_GRADES':'enforce'}):
            version=rules()
            self.assertGreater(len(version),60)
            self.assertTrue(version.endswith('-evidence-grades-v2'))
            def waiting(*_):
                row=dict(name='Jane Maria Brown',schema='Person',source_type='sanctions',dataset='fixture',id='listed-1')
                result=utils._evaluate_watchlist_rows('Jane Maria Smith',None,None,'Person',[row],'fixture',
                    token_stats=TokenStats({},100),submitted_entity_type='Person')
                stamp_screening_result(result,'Jane Smith',None,None,'Person','postgres_beta')
                result['Source Coverage']={'status':'current','version':'fixture'}
                return result
            self.assertEqual(waiting()['Check Summary']['Status'],utils.DOB_REQUIRED_STATUS)
            with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=waiting)):
                report=await execute(self.conn,plan,version)
                again=await execute(self.conn,plan,version)
        self.assertEqual(report['status'],'complete')
        self.assertEqual(report['details'][0]['new_status'],utils.DOB_REQUIRED_STATUS)
        self.assertEqual(again['counts']['already_completed'],1)
        row=await self.conn.fetchrow("SELECT status,result_json FROM screened_entities WHERE entity_key='entity'")
        self.assertEqual(row['status'],utils.DOB_REQUIRED_STATUS)
        self.assertTrue(json.loads(row['result_json'])['Is Sanctioned'])
        self.assertEqual([i['entity_key'] for i in await screening_db.list_review_queue(self.conn,queue='awaiting_dob')],['entity'])

    async def flag_as_test(self, run):
        async with self.conn.transaction():
            await screening_db.set_test_flag(self.conn, entity_key='entity', expected_run=run, active=True,
                                             reason='Benchmark record, not a customer.', actor='admin')

    async def test_records_flagged_as_test_data_are_left_out(self):
        _,old_run=await self.seed()
        await self.flag_as_test(old_run)
        plan=await preview(parse_time('2026-11-02T00:00:00+00:00'))
        self.assertEqual(plan['counts']['flagged_as_test_data'],2)
        self.assertTrue(all('flagged_as_test_data' in c['issues'] for c in plan['cases']))
        with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=lambda *a:self.result())) as matcher:
            report=await execute(self.conn,plan,rules())
        self.assertEqual(matcher.await_count,0)
        self.assertEqual(report['counts'],{'skipped_test_data':2})
        self.assertEqual(report['status'],'complete')                     # test data is not something to resolve
        self.assertEqual({d['status'] for d in report['details']},{'skipped_test_data'})
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_requests WHERE source=$1',SOURCE),0)
        self.assertEqual(await self.conn.fetchval("SELECT current_run_id FROM screened_entities WHERE entity_key='entity'"),old_run)

    async def test_record_flagged_after_the_inventory_is_not_rescreened(self):
        plan,old_run=await self.seed(references=('REF-1',))
        await self.flag_as_test(old_run)
        with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=lambda *a:self.result())) as matcher:
            report=await execute(self.conn,plan,rules())
        self.assertEqual(matcher.await_count,0)
        self.assertEqual(report['status'],'needs_attention')
        self.assertIn('flagged as test data',report['details'][0]['reason'])
        self.assertEqual(await self.conn.fetchval("SELECT current_run_id FROM screened_entities WHERE entity_key='entity'"),old_run)

    async def test_malformed_rules_version_is_rejected(self):
        plan,_=await self.seed(references=('REF-1',))
        for bad in ('','evidence v8','evidence-v8;drop','x'*201):
            with self.assertRaisesRegex(ValueError,'Invalid rules version'):
                await execute(self.conn,plan,bad)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_requests WHERE source=$1',SOURCE),0)

    async def test_changed_database_endpoint_is_rejected_before_screening(self):
        plan,_=await self.seed(references=('REF-1',))
        plan['database_identity']['oid']='0'
        with self.assertRaisesRegex(ValueError,'database endpoint'):
            await execute(self.conn,plan,rules())
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_requests WHERE source=$1',SOURCE),0)

    async def test_one_group_can_link_distinct_entity_keys_without_merging_history(self):
        await self.seed(references=('REF-1',))
        result=self.result()
        await screening_db.upsert_screening(self.conn,'other-entity','Jane Smith','jane smith',None,None,'Person',
                'Bob','REF-3','Client Onboarding',result)
        inputs={'name':'Jane Smith','dob':None,'country':None,'entity_type':'Person','search_backend':'postgres_beta'}
        await self.conn.execute('''INSERT INTO screening_requests(source,inputs,business_reference,status,run_id,requested_at)
            VALUES('/opcheck',$1::jsonb,'REF-3','completed',$2,'2026-11-02T12:00:00+00')''',json.dumps(inputs),UUID(result['run_id']))
        plan=await preview(parse_time('2026-11-02T00:00:00+00:00'))
        with patch('scripts.execute_release_rescreen.screen',AsyncMock(side_effect=lambda *a:self.result())) as matcher:
            report=await execute(self.conn,plan,rules())
        self.assertEqual(matcher.await_count,1)
        self.assertEqual(report['counts']['completed'],2)
        rows=await self.conn.fetch('''SELECT r.entity_key,r.run_id FROM screening_requests q
            JOIN screening_runs r USING(run_id) WHERE q.source=$1''',SOURCE)
        self.assertEqual({r['entity_key'] for r in rows},{'entity','other-entity'})
        self.assertEqual(len({r['run_id'] for r in rows}),2)

    async def test_backend_dispatch_uses_selected_backend_without_cache(self):
        inp={'name':'Jane Smith','dob':None,'country':None,'entity_type':'Person','backend':'postgres_beta'}
        with patch('scripts.execute_release_rescreen.run_postgres_check',AsyncMock(return_value=_empty_no_match_result('fixture'))) as pg:
            got=await screen(self.conn,inp,rules())
            self.assertEqual(got['Screening Inputs']['backend'],'postgres_beta')
            self.assertTrue(pg.await_args.kwargs['require_assurance'])
        inp['backend']='original'
        with patch('scripts.execute_release_rescreen.run_postgres_check',AsyncMock(return_value=_empty_no_match_result('fixture'))) as pg:
            got=await screen(self.conn,inp,rules())
            self.assertEqual(got['Screening Inputs']['backend'],'postgres_beta')
            self.assertTrue(pg.await_args.kwargs['require_assurance'])

    async def test_real_postgres_matcher_uses_assured_sources(self):
        plan,_=await self.seed(references=('REF-1',))
        await self.conn.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        await self.conn.execute('''CREATE TABLE watchlist_entities(source_type text,entity_id text,entity_schema text,
            name text,name_norm text,birth_date date,dataset text,regime text,raw_json jsonb)''')
        records=[{'id':'p1','name':'Jane Smith','schema':'Person','source_type':'sanctions','dataset':'Fixture sanctions'},
                 {'id':'s1','name':'Other Subject','schema':'Person','source_type':'sanctions','dataset':'Fixture sanctions'}]
        for item in records:
            await self.conn.execute('''INSERT INTO watchlist_entities(source_type,entity_id,entity_schema,name,name_norm,dataset,raw_json)
                VALUES($1,$2,$3,$4,$5,$6,$7::jsonb)''',item['source_type'],item['id'],item['schema'],item['name'],
                item['name'].lower(),item['dataset'],json.dumps(item))
        manifest={'version':'fixture','ingested_at':datetime.now(timezone.utc).isoformat(),
                  'list_counts':{k:1 for k in ('UK','UN','EU','OFAC')}}
        await self.conn.execute("INSERT INTO watchlist_source_state(singleton,manifest) VALUES(true,$1::jsonb) ON CONFLICT(singleton) DO UPDATE SET manifest=excluded.manifest",json.dumps(manifest))
        args={'name':'Jane Smith','dob':None,'country':None,'entity_type':'Person','backend':'postgres_beta'}
        result=await screen(self.conn,args,rules())
        self.assertTrue(result['Is Sanctioned'])
        self.assertEqual(result['Source Coverage']['version'],'fixture')
        report=await execute(self.conn,plan,rules())
        self.assertEqual(report['status'],'complete')
        self.assertEqual(await self.conn.fetchval("SELECT status FROM screened_entities WHERE entity_key='entity'"),'Fail Sanction')

    async def test_uncorroborated_backend_mismatch_is_reported(self):
        await self.seed(references=('REF-1',),backend='postgres_beta')
        await self.conn.execute("""UPDATE screening_requests SET inputs=jsonb_set(inputs,'{search_backend}','\"original\"')
                                   WHERE business_reference='REF-1' AND source='/opcheck'""")
        plan=await preview(parse_time('2026-11-02T00:00:00+00:00'))
        with patch('scripts.execute_release_rescreen.screen',AsyncMock()) as matcher:
            report=await execute(self.conn,plan,rules())
        self.assertEqual(report['status'],'needs_attention')
        self.assertEqual(report['counts']['unresolved'],1)
        self.assertEqual(matcher.await_count,0)

    async def test_stale_source_never_changes_current_record(self):
        plan,old_run=await self.seed(references=('REF-1',))
        with patch('scripts.execute_release_rescreen.run_postgres_check',AsyncMock(side_effect=RuntimeError('source is stale'))):
            report=await execute(self.conn,plan,rules())
        self.assertEqual(report['counts']['unresolved'],1)
        self.assertEqual(await self.conn.fetchval("SELECT current_run_id FROM screened_entities WHERE entity_key='entity'"),old_run)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_requests WHERE source=$1',SOURCE),0)

    async def test_real_missing_source_never_changes_current_record(self):
        plan,old_run=await self.seed(references=('REF-1',))
        report=await execute(self.conn,plan,rules())
        self.assertEqual(report['counts']['unresolved'],1)
        self.assertEqual(await self.conn.fetchval("SELECT current_run_id FROM screened_entities WHERE entity_key='entity'"),old_run)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_requests WHERE source=$1',SOURCE),0)

    async def test_alias_preflight_detects_malformed_values_read_only(self):
        from scripts.validate_watchlist_aliases import validate
        await self.conn.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        await self.conn.execute('CREATE TABLE watchlist_entities(source_type text,raw_json jsonb)')
        manifest={'version':'fixture','ingested_at':datetime.now(timezone.utc).isoformat(),
                  'list_counts':{k:1 for k in ('UK','UN','EU','OFAC')}}
        await self.conn.execute("INSERT INTO watchlist_source_state(singleton,manifest) VALUES(true,$1::jsonb) ON CONFLICT(singleton) DO UPDATE SET manifest=excluded.manifest",json.dumps(manifest))
        await self.conn.execute("INSERT INTO watchlist_entities VALUES('sanctions',$1::jsonb)",json.dumps({'aliases':'"John; Smith"'}))
        await self.conn.execute("INSERT INTO watchlist_entities VALUES('peps',$1::jsonb)",json.dumps({'aliases':'"unclosed;name'}))
        report=await validate(self.conn)
        self.assertEqual(report['status'],'needs_attention')
        self.assertEqual(report['counts']['malformed'],1)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM watchlist_entities'),2)
        await self.conn.execute("UPDATE watchlist_entities SET raw_json=$1::jsonb WHERE source_type='peps'",json.dumps({'aliases':'Jane Smith'}))
        clean=await validate(self.conn)
        self.assertEqual(clean['status'],'passed')
        self.assertEqual(clean['counts']['malformed'],0)

    async def test_second_executor_cannot_run_concurrently(self):
        plan,_=await self.seed(references=('REF-1',))
        other=await asyncpg.connect(self.dsn)
        try:
            await other.fetchval("SELECT pg_advisory_lock(hashtext('sanctions:release-rescreen'))")
            with self.assertRaisesRegex(RuntimeError,'executor is running'):
                await execute(self.conn,plan,rules())
        finally:
            await other.execute("SELECT pg_advisory_unlock(hashtext('sanctions:release-rescreen'))")
            await other.close()


def test_cli_checks_release_hash_and_drain_before_writing(tmp_path):
    import hashlib
    from pathlib import Path
    import subprocess
    import sys
    script=Path(__file__).parents[1]/'scripts/execute_release_rescreen.py'
    inventory=tmp_path/'inventory.json'
    inventory.write_text(json.dumps({'version':1,'start':'2026-11-02T00:00:00+00:00',
        'cutoff':'2026-11-03T00:00:00+00:00','cases':[],'database':'fixture'}))
    output=tmp_path/'report.json'
    digest=hashlib.sha256(inventory.read_bytes()).hexdigest()
    command=[sys.executable,str(script),'--inventory',str(inventory),'--inventory-sha256',digest,
        '--rules-version','evidence-v8','--release-id','expected','--output',str(output),'--execute']
    env={**os.environ,'RELEASE_ID':'expected','DATABASE_URL':'postgresql:///not-used'}
    missing_drain=subprocess.run(command,env=env,capture_output=True,text=True)
    assert missing_drain.returncode != 0 and not output.exists()
    wrong_release=subprocess.run(command+['--old-writers-drained'],env={**env,'RELEASE_ID':'other'},capture_output=True,text=True)
    assert wrong_release.returncode != 0 and not output.exists()
    bad_hash=command.copy()
    bad_hash[bad_hash.index(digest)]='0'*64
    wrong_hash=subprocess.run(bad_hash+['--old-writers-drained'],env=env,capture_output=True,text=True)
    assert wrong_hash.returncode != 0 and not output.exists()
