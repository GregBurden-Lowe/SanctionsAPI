"""Accumulating AI batches and atomic, explicitly automated CLEAR decisions."""
import asyncio
import json
import os
import unittest
from unittest.mock import patch, AsyncMock

import asyncpg
import ai_triage
import screening_db
from tests import test_internal_improvements as fixtures
from utils import _empty_no_match_result, stamp_screening_result


def recommendation(action='INVESTIGATE'):
    return dict(raw_recommended_action=action,effective_recommended_action=action,ai_confidence_raw=.85,
        ai_confidence_band='0.80-0.89',rationale_short='Distinctive names differ; no supporting identity agreement.',
        llm_model='test-model',llm_runtime='fixture',guardrail_overridden=False,guardrail_reasons=[])


class GuardrailTests(unittest.TestCase):
    def test_generation_is_bounded_and_complete_response_is_accepted(self):
        answer = {'recommended_action': 'CLEAR', 'confidence': .85, 'rationale_short': 'Names differ.'}
        with patch('ai_triage.requests.post') as post:
            post.return_value.json.return_value = {'done': True, 'done_reason': 'stop', 'response': json.dumps(answer)}
            self.assertEqual(ai_triage._call_ollama_json('test'), answer)
            self.assertEqual(post.call_args.kwargs['json']['options']['num_predict'], 384)
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
        result=_empty_no_match_result('PEP')
        result.update({'Is PEP':True,'Match Found':True,'Sanctions Name':'Bob Jones','Score':60,'Risk Level':'Medium Risk'})
        result['Check Summary']['Status']='Fail PEP'
        stamp_screening_result(result,'Alice Smith',None,None,'Person','original')
        await screening_db.upsert_screening(self.conn,key,'Alice Smith','alice smith',None,None,'Person','tester',key,'Client Onboarding',result)
        return result

    async def batch(self,limit=25):
        return await ai_triage.run_ai_triage_batch(self.conn,screening_db_module=screening_db,
            trigger_type='manual',triggered_by='test',limit=limit)

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
        self.assertTrue(json.loads(current['result_json'])['Is PEP'])
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
        self.assertEqual(await self.conn.fetchval('SELECT status FROM screened_entities'),'Fail PEP')

    async def test_approval_and_clear_rollback_together(self):
        identifier=await self.pending_clear()
        from review_decisions import write_decision
        async def fail_after_decision(*args,**kwargs):
            await write_decision(*args,**kwargs)
            raise RuntimeError('injected publication failure')
        with patch('review_decisions.write_decision',side_effect=fail_after_decision):
            with self.assertRaises(RuntimeError): await ai_triage.auto_clear_task(self.conn,identifier)
        self.assertEqual(await self.conn.fetchval('SELECT status FROM screened_entities'),'Fail PEP')
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
