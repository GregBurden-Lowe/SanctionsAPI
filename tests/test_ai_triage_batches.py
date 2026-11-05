"""Accumulating AI batches and atomic, explicitly automated CLEAR decisions."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch, AsyncMock, Mock

import asyncpg
import ai_triage
import screening_db
from tests import test_internal_improvements as fixtures
from utils import _empty_no_match_result, stamp_screening_result


def recommendation(action='INVESTIGATE'):
    return dict(raw_recommended_action=action,effective_recommended_action=action,ai_confidence_raw=.85,
        ai_confidence_band='0.80-0.89',rationale_short='Distinctive names differ; no supporting identity agreement.',
        llm_model='test-model',llm_runtime='fixture',guardrail_overridden=False,guardrail_reasons=[])


def many_candidates(count):
    evidence=[{'name':f'Listed Person {i}','matched_name':f'Listed Person {i}','name_score':80,'birth_date':'1970','country':'Elsewhere',
        'dataset':'Fixture','disposition':'potential_match','reasons':['long reason '*50],'source_evidence_hash':'x'*64} for i in range(count)]
    return {'display_name':'Alice Smith','matched_name':'Listed Person 0','score':80,'source_label':'Fixture',
        'result_json':{'Candidate Evidence':evidence,'Candidate Evidence Summary':{'potential_matches':count}}}


class PromptTests(unittest.TestCase):
    def test_prompt_stays_small_however_many_candidates_exist(self):
        prompt=ai_triage._build_prompt(many_candidates(50))
        self.assertLess(len(prompt),4000)
        self.assertEqual(prompt.count('listed_name'),ai_triage.PROMPT_MAX_CANDIDATES)
        self.assertIn('"candidates_not_shown": 45',prompt)

    def test_long_fields_are_clipped(self):
        candidate=many_candidates(5)
        for item in candidate['result_json']['Candidate Evidence']:
            item['name']='x'*5000
        self.assertLess(len(ai_triage._build_prompt(candidate)),4000)

    def test_strongest_candidates_are_shown_first(self):
        candidate=many_candidates(6)
        candidate['result_json']['Candidate Evidence'][5].update(name='Strongest Match',name_score=99)
        self.assertIn('Strongest Match',ai_triage._build_prompt(candidate))

    def test_clear_is_refused_when_a_candidate_was_not_shown(self):
        action,overridden,reasons=ai_triage.apply_guardrails(many_candidates(6),'CLEAR')
        self.assertEqual(action,'INVESTIGATE')
        self.assertTrue(overridden)
        self.assertIn('Not every potential match was shown to the model',reasons)

    def test_clear_is_allowed_when_every_candidate_was_shown(self):
        self.assertEqual(ai_triage.apply_guardrails(many_candidates(5),'CLEAR')[0],'CLEAR')

    def test_candidates_beyond_saved_evidence_count_as_not_shown(self):
        candidate=many_candidates(2)
        candidate['result_json']['Candidate Evidence Summary']['potential_matches']=60
        self.assertEqual(ai_triage.apply_guardrails(candidate,'CLEAR')[0],'INVESTIGATE')

    def test_a_full_context_window_gives_no_recommendation(self):
        response=Mock(status_code=200)
        response.json.return_value={'done':True,'done_reason':'stop','prompt_eval_count':ai_triage.MODEL_CONTEXT_TOKENS,
            'response':json.dumps({'recommended_action':'CLEAR','confidence':.9,'rationale_short':'Names differ.'})}
        with patch('ai_triage.requests.post',return_value=response),self.assertRaisesRegex(ValueError,'context'):
            ai_triage._call_ollama_json('prompt')


class HighRiskListTests(unittest.TestCase):
    CURRENT_LISTS=('UK Sanctions List (FCDO)','UN Security Council Consolidated List',
                   'EU Consolidated Financial Sanctions List','OFAC SDN List','OFAC Consolidated Sanctions List')

    def test_every_current_list_is_high_risk(self):
        for dataset in self.CURRENT_LISTS:
            with self.subTest(dataset=dataset):
                self.assertTrue(ai_triage._is_high_risk_source(dataset))

    def test_a_spelling_variant_cannot_be_cleared_on_any_current_list(self):
        for dataset in self.CURRENT_LISTS:
            evidence=[{'name':'Arkady Romanovich ROTENBERG','matched_name':'Arkady Romanovich ROTENBERG','name_score':88,
                       'dataset':dataset,'disposition':'potential_match'}]
            candidate={'display_name':'Arkadii Rotinberg','matched_name':evidence[0]['name'],'score':88,'source_label':dataset,
                       'result_json':{'Candidate Evidence':evidence,'Candidate Evidence Summary':{'potential_matches':1}}}
            with self.subTest(dataset=dataset):
                self.assertEqual(ai_triage.apply_guardrails(candidate,'CLEAR')[0],'INVESTIGATE')

    def test_a_secondary_candidate_on_a_listed_source_blocks_clear(self):
        candidate=many_candidates(2)
        candidate['result_json']['Candidate Evidence'][1]['dataset']='UK Sanctions List (FCDO)'
        self.assertEqual(ai_triage.apply_guardrails(candidate,'CLEAR')[0],'INVESTIGATE')


class GuardrailTests(unittest.TestCase):
    def test_generation_is_bounded_and_complete_response_is_accepted(self):
        answer = {'recommended_action': 'CLEAR', 'confidence': .85, 'rationale_short': 'Names differ.'}
        with patch('ai_triage.requests.post') as post:
            post.return_value.json.return_value = {'done': True, 'done_reason': 'stop', 'response': json.dumps(answer)}
            self.assertEqual(ai_triage._call_ollama_json('test'), answer)
            self.assertEqual(post.call_args.kwargs['json']['options']['num_predict'], ai_triage.MODEL_MAX_OUTPUT_TOKENS)
            self.assertEqual(post.call_args.kwargs['json']['options']['num_thread'], 2)

    def test_truncated_or_invalid_response_cannot_produce_clearance(self):
        clear = {'recommended_action': 'CLEAR', 'confidence': .85, 'rationale_short': 'Names differ.'}
        payloads = [
            {'done': True, 'done_reason': 'length', 'response': json.dumps(clear)},
            {'done': False, 'response': json.dumps(clear)},
            {'done': True, 'response': '{}'},
            {'done': True, 'response': json.dumps({**clear, 'confidence': float('nan')})},
            {'done': True, 'response': json.dumps({**clear, 'confidence': True})},
            {'done': True, 'response': json.dumps({**clear, 'rationale_short': ''})},
        ]
        for payload in payloads:
            with self.subTest(payload=payload), patch('ai_triage.requests.post') as post:
                post.return_value.json.return_value = payload
                with self.assertRaises(ValueError):
                    ai_triage._call_ollama_json('test')

    def test_secondary_candidate_and_truncation_prevent_clear(self):
        candidate={'display_name':'Alice Smith','matched_name':'Bob Jones','source_label':'PEP','score':60,
            'result_json':{'Candidate Evidence':[{'name':'Alice Smith','matched_name':'Alice Smith',
                'disposition':'potential_match','name_score':99,'dataset':'PEP'}]}}
        self.assertEqual(ai_triage.apply_guardrails(candidate,'CLEAR')[0],'INVESTIGATE')
        candidate['result_json']={'Candidate Evidence Summary':{'display_truncated':True}}
        self.assertEqual(ai_triage.apply_guardrails(candidate,'CLEAR')[0],'INVESTIGATE')


@unittest.skipUnless(os.getenv('SANCTIONS_TEST_ADMIN_DSN'),'Requires disposable PostgreSQL')
class AiBatchTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.InternalDatabaseTests.asyncSetUp
    asyncTearDown=fixtures.InternalDatabaseTests.asyncTearDown

    async def seed(self,key='subject'):
        result=_empty_no_match_result('Sanctions')
        result.update({'Is Sanctioned':True,'Match Found':True,'Sanctions Name':'Bob Jones','Score':60,'Risk Level':'Medium Risk'})
        result['Check Summary']['Status']='Fail Sanction'
        stamp_screening_result(result,'Alice Smith',None,None,'Person','original')
        await screening_db.upsert_screening(self.conn,key,'Alice Smith','alice smith',None,None,'Person','tester',key,'Client Onboarding',result)
        return result

    async def batch(self,limit=25,retry_failed=False):
        return await ai_triage.run_ai_triage_batch(self.conn,screening_db_module=screening_db,
            trigger_type='manual_retry' if retry_failed else 'manual',triggered_by='test',limit=limit,retry_failed=retry_failed)

    async def test_retry_bypasses_cooldown_preserves_error_and_auto_approves(self):
        await self.seed()
        with patch('ai_triage.triage_candidate',AsyncMock(side_effect=TimeoutError('test timeout'))):
            await self.batch()
        before=dict(await self.conn.fetchrow("SELECT * FROM ai_triage_recommendations WHERE status='ERROR'"))
        with patch('ai_triage.triage_candidate',AsyncMock(return_value=recommendation('CLEAR'))) as model:
            self.assertEqual((await self.batch())['selected_count'],0)
            result=await self.batch(retry_failed=True)
            self.assertEqual(result['auto_approved_count'],1)
            self.assertEqual(model.await_count,1)
            self.assertEqual((await self.batch(retry_failed=True))['selected_count'],0)
        after=dict(await self.conn.fetchrow('SELECT * FROM ai_triage_recommendations WHERE triage_id=$1',before['triage_id']))
        self.assertEqual(before,after)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM ai_triage_recommendations WHERE human_decision='AUTO_APPROVED'"),1)
        self.assertEqual(len(await screening_db.list_ai_triage_tasks(self.conn,status='ERROR')),1)
        self.assertEqual(await screening_db.list_ai_triage_tasks(self.conn,status='ERROR',hide_resolved_errors=True),[])
        self.assertIsNotNone(await screening_db.get_ai_triage_task(self.conn,triage_id=str(before['triage_id'])))

    async def test_successful_assessments_hide_errors_before_pagination(self):
        for i in range(4): await self.seed(f'subject-{i}')
        with patch('ai_triage.triage_candidate',AsyncMock(side_effect=TimeoutError('test timeout'))):
            await self.batch()
        with patch('ai_triage.triage_candidate',AsyncMock(return_value=recommendation('INVESTIGATE'))):
            await self.batch(limit=1,retry_failed=True)
        with patch('ai_triage.triage_candidate',AsyncMock(return_value=recommendation('UNSURE'))):
            await self.batch(limit=1,retry_failed=True)
        first=await screening_db.list_ai_triage_tasks(self.conn,status='ERROR',hide_resolved_errors=True,limit=1)
        second=await screening_db.list_ai_triage_tasks(self.conn,status='ERROR',hide_resolved_errors=True,limit=1,offset=1)
        self.assertEqual(len(first),1); self.assertEqual(len(second),1)
        self.assertNotEqual(first[0]['triage_id'],second[0]['triage_id'])
        self.assertEqual(await screening_db.list_ai_triage_tasks(self.conn,status='ERROR',hide_resolved_errors=True,limit=1,offset=2),[])
        self.assertEqual(len(await screening_db.list_ai_triage_tasks(self.conn,status='ERROR')),4)

    async def test_failed_retries_and_success_on_different_run_do_not_hide_errors(self):
        await self.seed()
        with patch('ai_triage.triage_candidate',AsyncMock(side_effect=TimeoutError('test timeout'))):
            await self.batch()
            await self.batch(retry_failed=True)
        self.assertEqual(len(await screening_db.list_ai_triage_tasks(self.conn,status='ERROR',hide_resolved_errors=True)),2)
        await self.seed()
        with patch('ai_triage.triage_candidate',AsyncMock(return_value=recommendation())):
            await self.batch()
        self.assertEqual(len(await screening_db.list_ai_triage_tasks(self.conn,status='ERROR',hide_resolved_errors=True)),2)

    async def test_retry_only_targets_failed_current_runs_without_success(self):
        for key in ('changed','resolved','failed'): await self.seed(key)
        with patch('ai_triage.triage_candidate',AsyncMock(side_effect=TimeoutError('test timeout'))):
            await self.batch()
        # A changed run and a fresh never-triaged record belong to normal batches.
        await self.seed('changed')
        await self.seed('fresh')
        await self.conn.execute("UPDATE screened_entities SET review_status='COMPLETED' WHERE entity_key='resolved'")
        with patch('ai_triage.triage_candidate',AsyncMock(return_value=recommendation())) as model:
            result=await self.batch(retry_failed=True)
            self.assertEqual(result['created_count'],1)
            self.assertEqual(model.await_args.args[0]['entity_key'],'failed')
            self.assertEqual((await self.batch(retry_failed=True))['selected_count'],0)

    async def test_repeated_retry_failures_rotate_and_batch_is_capped(self):
        for i in range(30): await self.seed(f'failure-{i:02d}')
        with patch('ai_triage.triage_candidate',AsyncMock(side_effect=TimeoutError('test timeout'))):
            await self.batch(limit=30)
        await self.conn.execute("UPDATE ai_triage_recommendations SET created_at=now()-interval '1 hour' WHERE status='ERROR'")
        with patch('ai_triage.triage_candidate',AsyncMock(side_effect=TimeoutError('test timeout'))) as model:
            result=await self.batch(limit=100,retry_failed=True)
            self.assertEqual(result['selected_count'],25)
            first={call.args[0]['entity_key'] for call in model.await_args_list}
        with patch('ai_triage.triage_candidate',AsyncMock(return_value=recommendation())) as model:
            result=await self.batch(limit=5,retry_failed=True)
            second={call.args[0]['entity_key'] for call in model.await_args_list}
        self.assertEqual(result['selected_count'],5)
        self.assertFalse(first & second)

    async def test_retry_obeys_existing_batch_lock(self):
        other=await asyncpg.connect(os.environ['SANCTIONS_TEST_ADMIN_DSN'],database=self.database)
        try:
            await other.execute('SELECT pg_advisory_lock(720031006)')
            try:
                self.assertEqual((await self.batch(retry_failed=True))['status'],'busy')
                self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM ai_triage_runs'),0)
            finally:
                await other.execute('SELECT pg_advisory_unlock(720031006)')
        finally:
            await other.close()

    async def test_batches_stack_beyond_25_without_reselecting_existing_suggestions(self):
        for i in range(60): await self.seed(f'subject-{i}')
        with patch('ai_triage.triage_candidate',AsyncMock(return_value=recommendation())) as model:
            runs=[await self.batch() for _ in range(4)]
        self.assertEqual([r['created_count'] for r in runs],[25,25,10,0])
        self.assertEqual(model.await_count,60)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM ai_triage_recommendations WHERE status='PENDING_REVIEW'"),60)
        first=await screening_db.list_ai_triage_tasks(self.conn,limit=25)
        second=await screening_db.list_ai_triage_tasks(self.conn,limit=25,offset=25)
        self.assertEqual(len(first),25); self.assertEqual(len(second),25)
        self.assertFalse({r['triage_id'] for r in first}&{r['triage_id'] for r in second})

    async def test_clear_is_auto_approved_once_with_original_evidence_preserved(self):
        first=await self.seed()
        with patch('ai_triage.triage_candidate',AsyncMock(return_value=recommendation('CLEAR'))):
            batch=await self.batch()
        self.assertEqual(batch['auto_approved_count'],1)
        task=await self.conn.fetchrow('SELECT * FROM ai_triage_recommendations')
        self.assertEqual(task['status'],'APPROVED'); self.assertEqual(task['human_decision'],'AUTO_APPROVED')
        current=await self.conn.fetchrow('SELECT * FROM screened_entities')
        self.assertEqual(current['status'],'Cleared - False Positive')
        self.assertTrue(json.loads(current['result_json'])['Is Sanctioned'])
        self.assertEqual(json.loads(current['review_decision'])['decision_type'],'ai_auto_clear')
        self.assertEqual(str(current['current_run_id']),first['run_id'])
        self.assertFalse(await ai_triage.auto_clear_task(self.conn,str(task['triage_id'])))
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_review_decisions'),1)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_candidate_resolutions'),0)
        # Re-running schema installation must accept existing automatic decisions.
        await screening_db.ensure_schema(self.conn)

    async def pending_clear(self):
        await self.seed()
        with patch.dict(os.environ,{'AI_TRIAGE_AUTO_CLEAR_ENABLED':'false'}), patch('ai_triage.triage_candidate',AsyncMock(return_value=recommendation('CLEAR'))):
            await self.batch()
        return str(await self.conn.fetchval('SELECT triage_id FROM ai_triage_recommendations'))

    async def test_existing_pending_clear_backlog_is_applied_by_next_batch(self):
        await self.pending_clear()
        with patch.dict(os.environ,{'AI_TRIAGE_AUTO_CLEAR_ENABLED':'true'}):
            batch=await self.batch()
        self.assertEqual(batch['auto_approved_count'],1)
        self.assertEqual(batch['existing_clear_processed'],1)
        self.assertEqual(batch['selected_count'],0)

    async def test_changed_run_supersedes_clear_without_clearing(self):
        identifier=await self.pending_clear()
        await self.seed()
        self.assertFalse(await ai_triage.auto_clear_task(self.conn,identifier))
        self.assertEqual(await self.conn.fetchval('SELECT status FROM ai_triage_recommendations'),'SUPERSEDED')
        self.assertEqual(await self.conn.fetchval('SELECT status FROM screened_entities'),'Fail Sanction')

    async def test_approval_and_clear_rollback_together(self):
        identifier=await self.pending_clear()
        from review_decisions import write_decision
        async def fail_after_decision(*args,**kwargs):
            await write_decision(*args,**kwargs)
            raise RuntimeError('injected publication failure')
        with patch('review_decisions.write_decision',side_effect=fail_after_decision):
            with self.assertRaises(RuntimeError): await ai_triage.auto_clear_task(self.conn,identifier)
        self.assertEqual(await self.conn.fetchval('SELECT status FROM screened_entities'),'Fail Sanction')
        self.assertEqual(await self.conn.fetchval('SELECT status FROM ai_triage_recommendations'),'PENDING_REVIEW')
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_review_decisions'),0)

    async def test_simultaneous_manual_and_scheduled_batch_is_busy(self):
        other=await asyncpg.connect(os.environ['SANCTIONS_TEST_ADMIN_DSN'],database=self.database)
        try:
            await other.fetchval('SELECT pg_advisory_lock(720031006)')
            self.assertEqual((await self.batch())['status'],'busy')
            self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM ai_triage_runs'),0)
        finally: await other.close()

    async def test_new_screening_can_receive_new_suggestion_and_old_is_superseded(self):
        await self.seed()
        with patch('ai_triage.triage_candidate',AsyncMock(return_value=recommendation())):
            await self.batch()
            await self.seed()
            await self.batch()
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM ai_triage_recommendations WHERE status='PENDING_REVIEW'"),1)
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM ai_triage_recommendations WHERE status='SUPERSEDED'"),1)
