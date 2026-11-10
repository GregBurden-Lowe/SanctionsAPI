"""Evidence grades in the live matcher and the date-of-birth-required workflow.

These check the wiring and the fail-safe handling of the new outcome. They do
not certify matching recall.
"""
import os
import unittest
from unittest.mock import AsyncMock, patch

import ai_triage
import screening_db
import utils
from evidence_grading import apply_shadow_policy, recording
from name_matching import TokenStats
from screening_guidance import describe_change, enrich_result
from screening_policy import _merge_dual_type_results
from tests import test_internal_improvements as fixtures
from utils import (DOB_REQUIRED_STATUS, ScreeningUnavailableError, is_dob_required_result,
                   rules_version_for, stamp_screening_result)

WEAK_QUERY = 'James Martin Carter'          # against "James Martin Brown": one search word unmatched


def person(name, record_id, **extra):
    return dict(name=name, schema='Person', source_type='sanctions', dataset='fixture', id=record_id, **extra)


def grades(mode):
    return patch.dict(os.environ, {'SCREENING_EVIDENCE_GRADES': mode, 'SCREENING_MATCHER_VERSION': 'v2'})


def screen(query, dob, rows, *, submitted='Person', entity_type='Person', stats=True):
    return utils._evaluate_watchlist_rows(query, dob, None, entity_type, rows, 'fixture',
                                          token_stats=TokenStats({}, 100) if stats else None,
                                          submitted_entity_type=submitted)


def decision_fields(result):
    """Everything that drives an outcome, without the informational grade fields."""
    out = {key: value for key, value in result.items() if key != 'Candidate Evidence'}
    out['Check Summary'] = {k: v for k, v in result['Check Summary'].items() if k != 'Date'}
    out['Candidate Evidence'] = [{k: v for k, v in item.items() if not k.startswith('evidence_grade')}
                                 for item in result['Candidate Evidence']]
    return out


class SettingTests(unittest.TestCase):
    ROWS = [person('James Martin Brown', 'weak-1'), person('Alexander PETROV', 'strong-1')]

    def test_grades_are_off_unless_configured(self):
        with patch.dict(os.environ):
            os.environ.pop('SCREENING_EVIDENCE_GRADES', None)
            self.assertEqual(utils.evidence_grades_mode(), 'off')
            result = screen(WEAK_QUERY, None, self.ROWS)
        self.assertNotIn('evidence_grade', result['Candidate Evidence'][0])
        self.assertEqual(result['Check Summary']['Status'], 'Fail Sanction')
        self.assertNotIn('dob_required', result['Candidate Evidence Summary'])

    def test_an_unknown_value_refuses_to_screen(self):
        with grades('enforced'), self.assertRaises(ScreeningUnavailableError):
            screen(WEAK_QUERY, None, self.ROWS)

    def test_annotate_records_the_grade_and_changes_no_decision(self):
        for query, dob in ((WEAK_QUERY, None), (WEAK_QUERY, '1980-01-01'), ('Alexander Petrov', None)):
            with grades('off'):
                plain = screen(query, dob, self.ROWS)
            with grades('annotate'):
                annotated = screen(query, dob, self.ROWS)
            self.assertEqual(decision_fields(plain), decision_fields(annotated))
            self.assertTrue(all('evidence_grade' in item for item in annotated['Candidate Evidence']))
        self.assertEqual(annotated['Candidate Evidence'][0]['evidence_grade'], 'strong')
        self.assertEqual(annotated['Candidate Evidence'][0]['evidence_grade_detail']['graded_name'], 'Alexander PETROV')

    def test_grades_need_the_v2_name_rules(self):
        with grades('enforce'):
            result = screen(WEAK_QUERY, None, self.ROWS, stats=False)
        self.assertTrue(all('evidence_grade' not in item for item in result['Candidate Evidence']))

    def test_only_enforce_changes_the_rules_version(self):
        with grades('off'):
            off = rules_version_for('postgres_beta')
        with grades('annotate'):
            self.assertEqual(rules_version_for('postgres_beta'), off)
        with grades('enforce'):
            self.assertEqual(rules_version_for('postgres_beta'), off + '-evidence-grades-v2')


class EnforcedPolicyTests(unittest.TestCase):
    WEAK_ROW = [person('James Martin Brown', 'weak-1', birth_date='1980-01-01')]

    def test_weak_name_without_a_date_of_birth_is_dob_required_and_never_looks_cleared(self):
        with grades('enforce'):
            result = screen(WEAK_QUERY, None, self.WEAK_ROW)
        self.assertEqual(result['Check Summary']['Status'], DOB_REQUIRED_STATUS)
        self.assertTrue(result['Check Summary']['Status'].startswith('Fail Sanction'))
        self.assertTrue(is_dob_required_result(result))
        # Anything unaware of the new outcome must still see an unresolved alert.
        self.assertTrue(result['Is Sanctioned'] and result['Match Found'])
        self.assertEqual(result['Risk Level'], 'High Risk')
        self.assertIn('not a clearance', result['Decision Explanation'])
        item = result['Candidate Evidence'][0]
        self.assertEqual((item['disposition'], item['disposition_reason']),
                         ('dob_required', 'weak_name_no_date_of_birth_supplied'))
        self.assertEqual(result['Candidate Evidence Summary']['potential_matches'], 0)
        self.assertEqual(result['Candidate Evidence Summary']['dob_required'], 1)

    def test_agreeing_date_of_birth_keeps_the_alert(self):
        for dob in ('1980-01-01', '1980'):
            with grades('enforce'):
                result = screen(WEAK_QUERY, dob, self.WEAK_ROW)
            self.assertEqual(result['Check Summary']['Status'], 'Fail Sanction')
            self.assertEqual(result['Candidate Evidence'][0]['disposition'], 'potential_match')

    def test_supplied_date_of_birth_with_nothing_to_compare_is_below_the_alert_threshold(self):
        with grades('enforce'):
            result = screen(WEAK_QUERY, '1980-01-01', [person('James Martin Brown', 'weak-1')])
        self.assertFalse(result['Is Sanctioned'] or result['Match Found'])
        self.assertEqual(result['Check Summary']['Status'], 'Cleared')
        self.assertEqual(result['Candidate Evidence'][0]['disposition'], 'below_alert_threshold')
        self.assertEqual(result['Candidate Evidence Summary']['below_alert_threshold'], 1)

    def test_conflicting_date_of_birth_is_still_an_exclusion(self):
        with grades('enforce'):
            result = screen(WEAK_QUERY, '1970-01-01', self.WEAK_ROW)
        self.assertEqual(result['Candidate Evidence'][0]['disposition'], 'excluded')

    def test_organisation_submissions_and_strong_names_are_unchanged(self):
        with grades('off'):
            expected_org = screen(WEAK_QUERY, None, self.WEAK_ROW, submitted='Organization')
            expected_strong = screen('Alexander Petrov', None, [person('Alexander PETROV', 'strong-1')])
        with grades('enforce'):
            org = screen(WEAK_QUERY, None, self.WEAK_ROW, submitted='Organization')
            strong = screen('Alexander Petrov', None, [person('Alexander PETROV', 'strong-1')])
        for expected, actual in ((expected_org, org), (expected_strong, strong)):
            self.assertEqual(expected['Check Summary']['Status'], actual['Check Summary']['Status'])
            self.assertEqual([i['disposition'] for i in expected['Candidate Evidence']],
                             [i['disposition'] for i in actual['Candidate Evidence']])
            self.assertEqual(actual['Check Summary']['Status'], 'Fail Sanction')

    def test_a_potential_match_leads_and_keeps_the_ordinary_status(self):
        rows = [person('James Martin Brown', 'weak-1'), person('James Martin CARTER', 'strong-1')]
        with grades('enforce'):
            result = screen(WEAK_QUERY, None, rows)
        self.assertEqual(result['Check Summary']['Status'], 'Fail Sanction')
        self.assertEqual(result['Sanctions Name'], 'James Martin CARTER')
        self.assertEqual([i['disposition'] for i in result['Candidate Evidence']], ['potential_match', 'dob_required'])

    def test_single_word_exact_date_of_birth_fallback_stays_an_alert(self):
        with grades('enforce'):
            result = screen('Ethan Marlow', '1980-01-01', [person('Marlow', 'fallback-1', birth_date='1980-01-01')])
        self.assertEqual(result['Check Summary']['Status'], 'Fail Sanction')
        self.assertEqual(result['Candidate Evidence'][0]['evidence_grade'], 'weak')

    def test_every_candidate_is_graded_before_the_display_limit(self):
        rows = [person('James Martin Brown', f'weak-{n}', aliases=f'J M Brown {n}') for n in range(60)]
        with grades('enforce'):
            result = screen(WEAK_QUERY, None, rows)
        summary = result['Candidate Evidence Summary']
        self.assertEqual((summary['dob_required'], summary['potential_matches'], summary['display_truncated']), (60, 0, True))
        self.assertEqual(result['Check Summary']['Status'], DOB_REQUIRED_STATUS)

    def test_live_enforcement_matches_the_shadow_policy(self):
        rows = [person('James Martin Brown', 'weak-1', birth_date='1980-01-01'), person('James Martin Brown', 'weak-2'),
                person('James Martin CARTER', 'strong-1'), person('Jose Daniel MARTINEZ LOPEZ', 'other-1')]
        for query in (WEAK_QUERY, 'Jose Martinez'):
            for dob in (None, '1980-01-01', '1975'):
                for submitted in ('Person', 'Organization'):
                    with grades('off'), recording() as recorded:
                        plain = screen(query, dob, rows, submitted=submitted)
                    shadow, _ = apply_shadow_policy(plain, recorded, submitted_type=submitted, dob=dob)
                    with grades('enforce'):
                        live = screen(query, dob, rows, submitted=submitted)
                    self.assertEqual({i['record_id']: i['disposition'] for i in shadow['Candidate Evidence']},
                                     {i['record_id']: i['disposition'] for i in live['Candidate Evidence']},
                                     (query, dob, submitted))


class DownstreamTests(unittest.TestCase):
    def results(self):
        with grades('enforce'):
            dob_required = screen(WEAK_QUERY, None, [person('James Martin Brown', 'weak-1')])
            clear = screen('Nobody Here', None, [], entity_type='Organization')
            alert = screen('Volga', None, [dict(name='Volga', schema='Organization', source_type='sanctions',
                                                dataset='fixture', id='org-1')], entity_type='Organization')
        return dob_required, clear, alert

    def test_merge_prefers_a_potential_match_over_a_missing_date_of_birth(self):
        dob_required, clear, alert = self.results()
        merged = _merge_dual_type_results(dob_required, clear, name=WEAK_QUERY, submitted_entity_type='Person')
        self.assertTrue(is_dob_required_result(merged))
        self.assertEqual(merged['Candidate Evidence Summary']['dob_required'], 1)
        merged = _merge_dual_type_results(dob_required, alert, name='Volga', submitted_entity_type='Person')
        self.assertEqual(merged['Check Summary']['Status'], 'Fail Sanction')
        self.assertEqual([i['disposition'] for i in merged['Candidate Evidence']], ['potential_match', 'dob_required'])

    def test_guidance_asks_for_the_date_of_birth(self):
        dob_required, _, _ = self.results()
        with grades('enforce'):
            stamp_screening_result(dob_required, WEAK_QUERY, None, None, 'Person', 'postgres_beta')
        guidance = dob_required['Screening Guidance']
        self.assertEqual(guidance['finding'], 'date_of_birth_required')
        self.assertIn('date of birth', guidance['next_actions'][0])
        self.assertTrue(dob_required['Matching Rules Version'].endswith('-evidence-grades-v2'))

    def test_moving_between_open_dispositions_is_not_a_monitoring_change(self):
        dob_required, _, _ = self.results()
        with grades('off'):
            before = screen(WEAK_QUERY, None, [person('James Martin Brown', 'weak-1')])
        change = describe_change(dob_required, before)['Monitoring Change']
        self.assertEqual((change['added_candidates'], change['removed_candidates']), ([], []))

    def test_ai_triage_never_clears_a_screening_awaiting_a_date_of_birth(self):
        dob_required, _, _ = self.results()
        action, overridden, reasons = ai_triage.apply_guardrails(
            {'display_name': WEAK_QUERY, 'result_json': dob_required}, 'CLEAR')
        self.assertEqual((action, overridden), ('INVESTIGATE', True))
        self.assertIn('A date of birth is required before this screening can be cleared', reasons)

    def test_review_queue_filter(self):
        where, _ = screening_db._review_queue_filter(None, None, None, False, 'awaiting_dob')
        self.assertIn("status ILIKE 'Fail Sanction - DOB Required%'", where)
        where, _ = screening_db._review_queue_filter(None, None, None, False, 'analyst')
        self.assertIn("status NOT ILIKE 'Fail Sanction - DOB Required%'", where)
        self.assertNotIn('DOB Required', screening_db._review_queue_filter(None, None, None, False)[0])
        with self.assertRaises(ValueError):
            screening_db._review_queue_filter(None, None, None, False, 'everything')


class LinkingTests(unittest.IsolatedAsyncioTestCase):
    """A date of birth supplied later resolves the waiting record in place."""

    async def run_request(self, dob, awaiting_key):
        import api_server
        captured = {}

        class Conn:
            pass

        class Acquire:
            async def __aenter__(self):
                return Conn()

            async def __aexit__(self, *args):
                return False

        class Pool:
            def acquire(self):
                return Acquire()

        async def upsert(conn, **kwargs):
            captured['entity_key'] = kwargs['entity_key']

        result = utils._empty_no_match_result('fixture')
        request = api_server.OpCheckRequest(name='James Martin Carter', dob=dob, entity_type='Person',
                                            business_reference='BR-1', reason_for_check='Client Onboarding',
                                            requestor='tester')
        db = api_server.screening_db
        with patch.object(db, 'get_pool', AsyncMock(return_value=Pool())), \
                patch.object(db, 'find_awaiting_dob_entity', AsyncMock(return_value=awaiting_key)) as find, \
                patch.object(db, 'get_valid_screening', AsyncMock(return_value=None)), \
                patch.object(db, 'get_latest_uk_hash', AsyncMock(return_value={})), \
                patch.object(db, 'upsert_screening', AsyncMock(side_effect=upsert)), \
                patch.object(api_server, '_run_postgres_dual_check', AsyncMock(return_value=result)):
            response = await api_server._execute_screening(request)
        return request, response, captured, find

    async def test_date_of_birth_reuses_the_waiting_record(self):
        request, response, captured, find = await self.run_request('1980-01-01', 'waiting-key')
        self.assertEqual(captured['entity_key'], 'waiting-key')
        self.assertEqual(response['entity_key'], 'waiting-key')
        self.assertEqual(request.rerun_entity_key, 'waiting-key')        # so a cleared re-run completes its review
        self.assertEqual(find.await_args.kwargs, {'normalized_name': 'james martin carter', 'business_reference': 'BR-1'})

    async def test_nothing_waiting_or_no_date_of_birth_keeps_the_usual_key(self):
        request, response, captured, _ = await self.run_request('1980-01-01', None)
        self.assertNotEqual(captured['entity_key'], 'waiting-key')
        self.assertIsNone(request.rerun_entity_key)
        request, response, captured, find = await self.run_request(None, 'waiting-key')
        self.assertNotEqual(captured['entity_key'], 'waiting-key')
        find.assert_not_awaited()


@unittest.skipUnless(os.getenv('SANCTIONS_TEST_ADMIN_DSN'), 'Requires disposable PostgreSQL')
class QueueDatabaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.InternalDatabaseTests.asyncSetUp
    asyncTearDown = fixtures.InternalDatabaseTests.asyncTearDown

    async def seed(self):
        with grades('enforce'):
            waiting = screen(WEAK_QUERY, None, [person('James Martin Brown', 'weak-1')])
            alert = screen('Alexander Petrov', None, [person('Alexander PETROV', 'strong-1')])
            clear = screen('Nobody Here', None, [])
            for key, name, result in (('waiting', WEAK_QUERY, waiting), ('alert', 'Alexander Petrov', alert),
                                      ('clear', 'Nobody Here', clear)):
                stamp_screening_result(result, name, None, None, 'Person', 'postgres_beta')
                await screening_db.upsert_screening(self.conn, key, name, utils._normalize_text(name), None, None,
                                                    'Person', 'tester', 'BR-1', 'Client Onboarding', result)

    async def keys(self, **filters):
        return sorted(item['entity_key'] for item in await screening_db.list_review_queue(self.conn, **filters))

    async def test_queue_separates_analyst_work_from_screenings_awaiting_a_date_of_birth(self):
        await self.seed()
        self.assertEqual(await self.keys(), ['alert', 'waiting'])                      # never treated as cleared
        self.assertEqual(await self.keys(queue='analyst'), ['alert'])
        self.assertEqual(await self.keys(queue='awaiting_dob'), ['waiting'])
        self.assertEqual(await screening_db.count_review_queue(self.conn, queue='awaiting_dob'), 1)
        flags = {item['entity_key']: item['dob_required'] for item in await screening_db.list_review_queue(self.conn)}
        self.assertEqual(flags, {'alert': False, 'waiting': True})
        self.assertEqual(await self.conn.fetchval("SELECT status FROM screened_entities WHERE entity_key='waiting'"),
                         DOB_REQUIRED_STATUS)

    async def test_ai_triage_selects_only_analyst_work(self):
        await self.seed()
        selected = await screening_db.list_ai_triage_candidates(self.conn)
        self.assertEqual([row['entity_key'] for row in selected], ['alert'])

    async def test_waiting_record_is_found_until_it_is_resolved(self):
        await self.seed()
        find = screening_db.find_awaiting_dob_entity
        self.assertEqual(await find(self.conn, normalized_name='james martin carter', business_reference='BR-1'), 'waiting')
        self.assertIsNone(await find(self.conn, normalized_name='james martin carter', business_reference='BR-2'))
        self.assertIsNone(await find(self.conn, normalized_name='alexander petrov', business_reference='BR-1'))
        claimed = await screening_db.claim_review(self.conn, entity_key='waiting', claimed_by='analyst')
        self.assertEqual(claimed['status'], 'ok')                                     # an analyst can still take it
        run_id = await self.conn.fetchval("SELECT current_run_id::text FROM screened_entities WHERE entity_key='waiting'")
        done = await screening_db.complete_review(self.conn, entity_key='waiting', completed_by='analyst',
                                                  review_outcome='False Positive - Proceeded',
                                                  review_notes='Date of birth unobtainable; identity checked against passport.',
                                                  expected_run_id=run_id)
        self.assertEqual(done['status'], 'ok')
        self.assertIsNone(await find(self.conn, normalized_name='james martin carter', business_reference='BR-1'))


if __name__ == '__main__':
    unittest.main()
