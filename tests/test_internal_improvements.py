"""Internal workflow safety: coverage, scoped reuse, monitoring and labelled metrics."""
import asyncio
import copy
import csv
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from uuid import uuid4

import asyncpg
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import screening_db
from source_assurance import assess, require_current, build_manifest, METADATA_KEY
from screening_guidance import reusable_candidates, describe_change
from screening_policy import run_postgres_check
from utils import ScreeningUnavailableError, _evaluate_watchlist_rows, stamp_screening_result
from acceptance_metrics import score_cases
from services import companies_house as ch


def manifest(version='fixture'):
    return {'version':version, 'ingested_at':datetime.now(timezone.utc).isoformat(),
            'list_counts':{name:1 for name in ('UK','UN','EU','OFAC')}}


def result(name='Jane Smith', dob='1980', country='UK', extra=False):
    rows = [dict(id='one',name=name,schema='Person',source_type='sanctions',dataset='OFAC',birth_date='1980')]
    if extra:
        rows.append({**rows[0], 'id':'two'})
    r = _evaluate_watchlist_rows(name,dob,country,'Person',rows,'fixture',True)
    r['Source Coverage'] = assess(manifest())
    return stamp_screening_result(r,name,dob,country,'Person','original')


class PureImprovements(unittest.TestCase):
    def test_coverage_fails_for_each_missing_required_list_and_staleness(self):
        for name in ('UK','UN','EU','OFAC'):
            m=manifest(); del m['list_counts'][name]
            with self.subTest(name=name), self.assertRaises(ScreeningUnavailableError): require_current(m)
        m=manifest(); m['ingested_at']=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
        with self.assertRaises(ScreeningUnavailableError): require_current(m)
        with self.assertRaises(ScreeningUnavailableError): require_current({})
        m=manifest()
        self.assertNotIn('PEP',m['list_counts'])
        self.assertEqual(require_current(m)['status'],'current')
        self.assertNotIn('source_published_at',m)

    def test_manifest_uses_exact_csv_hash_and_recognises_current_uk_label(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'source.csv'
            path.write_text('name,dataset\nTest,UK Sanctions List;United Nations;EU Council;OFAC\n')
            first=build_manifest(path)
            require_current(first)
            path.write_text(path.read_text()+'Another,OFAC\n')
            self.assertNotEqual(first['version'],build_manifest(path)['version'])
            self.assertIsNone(first['source_published_at'])

    def test_name_only_and_truncated_evidence_cannot_be_reused(self):
        self.assertTrue(reusable_candidates(result()))
        self.assertFalse(reusable_candidates(result(dob=None)))
        r=result(); r['Candidate Evidence Summary']['display_truncated']=True
        self.assertFalse(reusable_candidates(r))
        r=result(); r['Candidate Evidence'][0]['record_id']=None
        self.assertFalse(reusable_candidates(r))

    def test_monitoring_explains_added_and_changed_candidates(self):
        before=result(); after=result(extra=True)
        after['Candidate Evidence'][0]['source_evidence_hash']='changed'
        describe_change(after,before,'previous')
        self.assertEqual(after['Monitoring Change']['previous_run_id'],'previous')
        self.assertEqual(after['Monitoring Change']['added_candidates'],['two|sanctions'])
        self.assertEqual(after['Monitoring Change']['changed_candidates'],['one|sanctions'])

    def test_scoring_requires_independent_labels_and_expected_candidate(self):
        label={'expected_outcome':'potential_sanctions_match','expected_watchlist_entry':'one',
               'acceptance_reviewer':'Analyst','acceptance_reason':'Verified source identity','acceptance_date':'2026-10-31'}
        cases=[{'case_id':'hit','acceptance_fields':label,'postgres':{'outcome':'potential_sanctions_match','result':result(),'seconds':1}},
               {'case_id':'missing','acceptance_fields':label,'postgres':{'outcome':'incomplete','seconds':2}},
               {'case_id':'historical','outcome':'False Positive - Proceeded','acceptance_fields':{},'postgres':{'outcome':'no_potential_match'}}]
        scored=score_cases(cases)
        self.assertEqual(scored['candidate_detection_rate'],.5)
        self.assertEqual(scored['counts']['unlabelled'],1)
        self.assertEqual(scored['incomplete_rate'],.3333)
        cases[0]['acceptance_fields']={**label,'expected_watchlist_entry':'other'}
        self.assertEqual(score_cases(cases)['counts']['false_negative'],1)

    def test_registry_pagination_and_unavailable_ownership(self):
        pages=[{'items':[{'name':'A'}],'total_results':2},{'items':[{'name':'B'}],'total_results':2}]
        with patch.object(ch,'_request',side_effect=pages) as request:
            self.assertEqual(len(ch._all_items('/fixture')),2)
            self.assertEqual(request.call_args.kwargs['params']['start_index'],1)
        with patch.object(ch,'_request',return_value={'items':[],'total_results':2}), self.assertRaises(ch.CompaniesHouseUpstreamError):
            ch._all_items('/fixture')
        with patch.object(ch,'_request',side_effect=ch.CompaniesHouseRateLimitError('Rate limited')):
            self.assertEqual(ch.get_ownership('00000000')['status'],'unavailable')

    def test_connected_parties_preserve_corporate_type_and_partial_dob(self):
        bundle={'company':{'company_name':'A Ltd','company_number':'00000000'},
            'officers':[{'name':'Corporate Secretary Ltd','role':'corporate-secretary','status':'active'},
                        {'name':'Jane Smith','role':'director','status':'active','date_of_birth':{'year':1980,'month':2}}],
            'ownership':{'persons':[{'name':'Owner Ltd','entity_type':'Organization','natures_of_control':['ownership-of-shares-75-to-100-percent']}]}}
        subjects=ch.connected_subjects(bundle)
        self.assertEqual([s['entity_type'] for s in subjects],['Organization','Organization','Person','Organization'])
        self.assertEqual(subjects[2]['dob'],'1980')


@unittest.skipUnless(os.getenv('SANCTIONS_TEST_ADMIN_DSN'),'Requires disposable PostgreSQL')
class InternalDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.admin=await asyncpg.connect(os.environ['SANCTIONS_TEST_ADMIN_DSN'])
        self.database='sanctions_internal_test_'+uuid4().hex
        await self.admin.execute(f'CREATE DATABASE "{self.database}"')
        self.conn=await asyncpg.connect(os.environ['SANCTIONS_TEST_ADMIN_DSN'],database=self.database)
        await screening_db.ensure_schema(self.conn)

    async def asyncTearDown(self):
        await self.conn.close()
        await self.admin.execute(f'DROP DATABASE "{self.database}"')
        await self.admin.close()

    async def save(self,r=None,key='subject',reference='POL-1'):
        r=r or result()
        await screening_db.upsert_screening(self.conn,key,'Jane Smith','jane smith','1980','UK','Person','Analyst',reference,'Client Onboarding',r)
        return r

    async def dismiss(self):
        rid=await self.conn.fetchval("SELECT current_run_id FROM screened_entities WHERE entity_key='subject'")
        await screening_db.mark_false_positive(self.conn,entity_key='subject',actor='Reviewer',reason='Documented identity differs from listed person',expected_run_id=str(rid))

    async def test_unchanged_candidates_reuse_but_new_candidate_reopens(self):
        first=await self.save(); await self.dismiss()
        worker_result=result()
        stamp_screening_result(worker_result,'Jane Smith','1980','UK','Person','postgres_beta')
        second=await self.save(worker_result)
        self.assertFalse(second['Is Sanctioned'])
        self.assertTrue(second['Engine Result']['Is Sanctioned'])
        self.assertEqual(second['Review Decision']['decision_type'],'reused_candidate_decisions')
        self.assertNotEqual(first['run_id'],second['run_id'])
        third=await self.save(result(extra=True))
        self.assertTrue(third['Is Sanctioned'])
        self.assertNotIn('Review Decision',third)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_runs'),3)
        original=json.loads(await self.conn.fetchval('SELECT result_json FROM screening_runs WHERE run_id=$1::uuid',first['run_id']))
        self.assertTrue(original['Is Sanctioned'])

    async def test_identity_reference_and_source_changes_prevent_reuse(self):
        await self.save(); await self.dismiss()
        for r,reference in [(result(country='US'),'POL-1'),(result(),'POL-2')]:
            with self.subTest(reference=reference,country=r['Screening Inputs']['country']):
                self.assertNotIn('Review Decision',await self.save(r,reference=reference))
        changed=result(); changed['Candidate Evidence'][0]['source_evidence_hash']='changed'
        stamp_screening_result(changed,'Jane Smith','1980','UK','Person','original')
        self.assertNotIn('Review Decision',await self.save(changed))

    async def test_latest_confirmed_decision_overrides_old_false_positive(self):
        await self.save(); await self.dismiss()
        # A new reviewed run can reverse the prior conclusion.
        changed=result(extra=True); await self.save(changed)
        from review_decisions import write_decision
        row=await self.conn.fetchrow("SELECT * FROM screened_entities WHERE entity_key='subject'")
        async with self.conn.transaction():
            await write_decision(self.conn,row,'Reviewer','Confirmed Match - Escalated to Compliance','Additional evidence confirms the identity')
        self.assertNotIn('Review Decision',await self.save(result()))

    async def test_monitoring_paginates_population_and_retains_pending_work(self):
        from screening_monitoring import tick
        for i in range(3): await self.save(key=f'subject-{i}')
        with patch('screening_monitoring.postgres_manifest',AsyncMock(return_value=manifest())):
            first=await tick(self.conn,batch_size=1)
            self.assertEqual(first['queued'],1)
            self.assertEqual(first['remaining'],3)
            await tick(self.conn,batch_size=1); await tick(self.conn,batch_size=1)
            self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_monitoring_cycles'),1)
            self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_monitoring_items WHERE job_id IS NOT NULL'),3)
            inputs=await self.conn.fetch('SELECT date_of_birth,country FROM screening_jobs')
            self.assertTrue(all(r['date_of_birth']=='1980' and r['country']=='UK' for r in inputs))
            self.assertEqual((await tick(self.conn))['queued'],0)
            # Failure is an explicit terminal cycle state, not an apparent clear.
            await self.conn.execute("UPDATE screening_jobs SET status='failed',finished_at=now()")
            final=await tick(self.conn)
            self.assertEqual(final['status'],'failed')
            self.assertEqual(final['failed'],3)

    async def test_test_flags_are_reference_scoped_audited_and_excluded_from_monitoring(self):
        import httpx
        import api_server
        from screening_monitoring import tick
        first = await self.save(key='test-subject', reference='TEST-001')
        await self.save(key='real-subject', reference='POL-1')
        pool = await asyncpg.create_pool(os.environ['SANCTIONS_TEST_ADMIN_DSN'], database=self.database, min_size=1, max_size=3)
        api_server.app.dependency_overrides[api_server.require_admin] = lambda: {'sub':'administrator','is_admin':True}
        try:
            with patch.object(screening_db, 'get_pool', AsyncMock(return_value=pool)):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app), base_url='http://test') as client:
                    url='/admin/screening/test-records/test-subject'
                    payload={'active':True,'reason':'Synthetic benchmark record','expected_run_id':str(uuid4())}
                    self.assertEqual((await client.put(url,json=payload)).status_code,409)
                    payload['expected_run_id']=first['run_id']
                    self.assertEqual((await client.put(url,json=payload)).status_code,200)
                    self.assertEqual(await screening_db.count_review_queue(self.conn),1)
                    self.assertEqual(len(await screening_db.list_ai_triage_candidates(self.conn)),1)
                    self.assertEqual((await client.get('/admin/screening/test-records')).json()['total'],1)
                    with patch('screening_monitoring.postgres_manifest',AsyncMock(return_value=manifest())):
                        cycle=await tick(self.conn)
                    self.assertEqual(cycle['queued'],1)
                    self.assertIsNone(await self.conn.fetchval('SELECT job_id FROM screening_monitoring_items WHERE entity_key=$1','test-subject'))
                    payload.update(active=False,reason='Restore production monitoring')
                    self.assertEqual((await client.put(url,json=payload)).status_code,200)
                    self.assertEqual(await screening_db.count_review_queue(self.conn),2)
                    self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_test_flag_events'),2)
                    with self.assertRaises(asyncpg.RaiseError):
                        await self.conn.execute('DELETE FROM screening_test_flag_events')
                    await screening_db.enqueue_job(self.conn, entity_key='test-subject', name='Jane Smith', date_of_birth='1980',
                        country='UK', entity_type='Person', requestor='fixture', business_reference='TEST-001', reason_for_check='Periodic Re-Screen')
                    payload.update(active=True,reason='Synthetic benchmark record')
                    self.assertEqual((await client.put(url,json=payload)).status_code,409)
                    await self.conn.execute("UPDATE screening_jobs SET status='failed',finished_at=now()")
                    with patch('screening_monitoring.postgres_manifest',AsyncMock(return_value=manifest())):
                        await tick(self.conn)
                    # The current cycle was snapshotted without the test record; a later cycle will include it.
                    self.assertIsNone(await self.conn.fetchval('SELECT job_id FROM screening_monitoring_items WHERE entity_key=$1','test-subject'))
                    self.assertEqual((await client.put(url,json=payload)).status_code,200)
                    await self.conn.execute('UPDATE screened_entities SET business_reference=$2 WHERE entity_key=$1','test-subject','POL-2')
                    self.assertEqual(await screening_db.count_review_queue(self.conn),2)
                    self.assertFalse((await client.get('/admin/screening/test-records')).json()['items'][0]['scope_matches'])
                    await self.conn.execute('UPDATE screened_entities SET business_reference=$2, country_input=$3 WHERE entity_key=$1','test-subject','TEST-001','France')
                    self.assertEqual(await screening_db.count_review_queue(self.conn),2)
        finally:
            api_server.app.dependency_overrides.clear()
            await pool.close()

    async def test_flagged_unqueued_item_does_not_stall_existing_cycle(self):
        from screening_monitoring import tick
        await self.save(key='a-real'); await self.save(key='b-test', reference='TEST-001')
        with patch('screening_monitoring.postgres_manifest',AsyncMock(return_value=manifest())):
            self.assertEqual((await tick(self.conn,batch_size=1))['remaining'],2)
            await self.conn.execute("""INSERT INTO screening_test_flags(entity_key,business_reference,active,reason,actor,normalized_name,entity_type,date_of_birth,country_input)
                SELECT entity_key,business_reference,true,$2,$3,normalized_name,entity_type,date_of_birth,country_input
                FROM screened_entities WHERE entity_key=$1""",'b-test','Synthetic fixture','admin')
            await self.conn.execute("UPDATE screening_jobs SET status='failed',finished_at=now()")
            result=await tick(self.conn)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['remaining'],0)
            self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_jobs'),1)

    async def test_test_flag_routes_require_admin(self):
        import httpx
        import api_server
        with patch.object(api_server, 'get_current_user', AsyncMock(return_value={'sub':'user','is_admin':False})):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app),base_url='http://test') as client:
                self.assertEqual((await client.get('/admin/screening/test-records')).status_code,403)
                response=await client.put('/admin/screening/test-records/subject',json={
                    'active':True,'reason':'Synthetic benchmark record','expected_run_id':str(uuid4())})
                self.assertEqual(response.status_code,403)

    async def test_postgres_manifest_and_rows_cannot_change_between_type_passes(self):
        from source_assurance import postgres_manifest
        await self.conn.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        await self.conn.execute('''CREATE TABLE watchlist_entities(source_type text,entity_id text,entity_schema text,
            name text,name_norm text,birth_date date,dataset text,regime text,raw_json jsonb)''')
        await self.conn.execute("INSERT INTO watchlist_entities VALUES('sanctions','s','Person','Jane Smith','jane smith',NULL,'OFAC',NULL,'{}'),('peps','p','Person','Other','other',NULL,'PEP',NULL,'{}')")
        await self.conn.execute('INSERT INTO watchlist_source_state VALUES(true,$1::jsonb)',json.dumps(manifest('one')))
        second=await asyncpg.connect(os.environ['SANCTIONS_TEST_ADMIN_DSN'],database=self.database)
        try:
            # Reader pins table while refresh (TRUNCATE + manifest update) waits.
            async with self.conn.transaction():
                await self.conn.execute('LOCK TABLE watchlist_entities IN ACCESS SHARE MODE')
                async def publish():
                    async with second.transaction():
                        await second.execute('TRUNCATE watchlist_entities')
                        await second.execute('UPDATE watchlist_source_state SET manifest=$1::jsonb',json.dumps(manifest('two')))
                task=asyncio.create_task(publish())
                await asyncio.sleep(.03)
                self.assertFalse(task.done())
                r=await run_postgres_check(self.conn,name='Jane Smith',dob=None,country=None,entity_type='Person',require_assurance=True)
                self.assertEqual(r['Source Coverage']['version'],'one')
                self.assertTrue(r['Is Sanctioned'])
            await asyncio.wait_for(task,2)
            self.assertEqual((await postgres_manifest(self.conn))['version'],'two')
        finally:
            await second.close()

    async def test_existing_http_endpoints_accept_same_payload_and_fail_closed_when_stale(self):
        import httpx
        import api_server
        from tests.test_api_contract import OPCHECK_KEYS
        await self.conn.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        await self.conn.execute('''CREATE TABLE watchlist_entities(source_type text,entity_id text,entity_schema text,
            name text,name_norm text,birth_date date,dataset text,regime text,raw_json jsonb)''')
        await self.conn.execute("INSERT INTO watchlist_entities VALUES('sanctions','s','Person','Jane Smith','jane smith',NULL,'OFAC',NULL,'{}'),('peps','p','Person','Other','other',NULL,'PEP',NULL,'{}')")
        await self.conn.execute('INSERT INTO watchlist_source_state VALUES(true,$1::jsonb)',json.dumps(manifest('one')))
        pool=await asyncpg.create_pool(os.environ['SANCTIONS_TEST_ADMIN_DSN'],database=self.database,min_size=1,max_size=3)
        api_server.app.dependency_overrides[api_server.get_current_user]=lambda:{'sub':'fixture','is_admin':True}
        try:
            with patch.object(screening_db,'get_pool',AsyncMock(return_value=pool)):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app),base_url='http://test') as client:
                    payload={'name':'Jane Smith','dob':'1980','country':'UK','entity_type':'Person',
                             'requestor':'fixture','business_reference':'POL-1','reason_for_check':'Client Onboarding'}
                    for endpoint in ('/opcheck','/opcheck/dataverse'):
                        response=await client.post(endpoint,json=payload)
                        self.assertEqual(response.status_code,200,response.text)
                        body=response.json()
                        self.assertFalse(OPCHECK_KEYS-set(body))
                        self.assertEqual(body['Source Coverage']['version'],'one')
                        self.assertIn('Screening Guidance',body)
                        self.assertTrue(body['Is Sanctioned'])
                    expired=manifest('one'); expired['ingested_at']=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
                    await self.conn.execute('UPDATE watchlist_source_state SET manifest=$1::jsonb',json.dumps(expired))
                    response=await client.post('/opcheck',json=payload)
                    self.assertEqual(response.status_code,503,response.text)
                    self.assertEqual(response.json()['error'],'screening_incomplete')
                    request=await self.conn.fetchrow('SELECT status FROM screening_requests WHERE request_id=$1::uuid',response.json()['request_id'])
                    self.assertEqual(request['status'],'failed')
        finally:
            api_server.app.dependency_overrides.clear()
            await pool.close()

    async def test_company_investigation_keeps_relationship_snapshot_and_saved_results(self):
        from company_investigations import investigate
        import api_server
        bundle={'company':{'company_name':'Fixture Ltd','company_number':'00000000'},'officers':[],
                'ownership':{'status':'retrieved','persons':[{'name':'Jane Smith','entity_type':'Person','date_of_birth':{'year':1980},'natures_of_control':['shares']}],'limitations':['Direct relationships only']}}
        pool=await asyncpg.create_pool(os.environ['SANCTIONS_TEST_ADMIN_DSN'],database=self.database,min_size=1,max_size=3)
        try:
            with patch('company_investigations.get_company_screening_data',return_value=bundle), \
                 patch.object(screening_db,'get_pool',AsyncMock(return_value=pool)), \
                 patch.object(api_server,'_run_postgres_dual_check',AsyncMock(side_effect=lambda *a,**k:result())):
                report=await investigate('00000000','POL-1','Business Partner Due Diligence','fixture')
                self.assertEqual(report['status'],'screened')
                self.assertEqual(len(report['checks']),2)
                self.assertTrue(all(c['result'].get('request_id') for c in report['checks']))
                saved=await api_server.company_investigation_detail(report['investigation_id'])
                self.assertEqual(saved['relationships'],bundle)
                self.assertEqual(len(saved['checks']),2)
                self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM screening_requests WHERE source='company_investigation'"),2)
        finally:
            await pool.close()

    async def test_monitoring_waits_for_existing_job_without_losing_entity(self):
        from screening_monitoring import tick
        await self.save()
        job=await screening_db.enqueue_job(self.conn,'subject','Jane Smith','1980','UK','Person','fixture','POL-1','Client Onboarding')
        with patch('screening_monitoring.postgres_manifest',AsyncMock(return_value=manifest())):
            first=await tick(self.conn)
            self.assertEqual(first['queued'],0)
            self.assertEqual(first['remaining'],1)
            await self.conn.execute("UPDATE screening_jobs SET status='failed',finished_at=now() WHERE job_id=$1::uuid",job)
            second=await tick(self.conn)
            self.assertEqual(second['queued'],1)
            self.assertNotEqual(str(await self.conn.fetchval('SELECT job_id FROM screening_monitoring_items')),job)
