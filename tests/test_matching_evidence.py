"""Synthetic labelled regression corpus: not a production accuracy estimate."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import pandas as pd

from utils import _evaluate_watchlist_rows, _identity_dob_evidence, _normalize_dob, perform_opensanctions_check
from api_server import _merge_dual_type_results


def row(name='Jane Smith', **kwargs):
    return dict(name=name, schema='Person', source_type='sanctions', dataset='OFAC', id='fixture-1', **kwargs)


def evaluate(rows, name='Jane Smith', dob=None, country=None, entity_type='Person'):
    return _evaluate_watchlist_rows(name, dob, country, entity_type, rows, 'fixture', True)


class MatchingEvidenceTests(unittest.TestCase):
    def test_labelled_name_benchmark(self):
        cases = json.loads((Path(__file__).parent / 'fixtures/matching_cases.json').read_text())
        for case in cases:
            with self.subTest(case=case['label']):
                kind = case.get('type', 'Person')
                item = row(case['candidate']); item['schema'] = kind
                result = evaluate([item], case['query'], entity_type=kind)
                self.assertEqual(result['Match Found'], case['expected'])

    def test_dob_precision_and_unknown(self):
        for candidate, query, expected in [
            (None, '1980-02-01', 'unknown'), ('1980', '1980-02-01', 'compatible'),
            ('1980-02', '1980-02-01', 'compatible'), ('1980-02-01', '1980', 'compatible'),
            ('1980-02-01', '1980-02-01', 'exact'), ('1970', '1980', 'conflicting'),
            ('1970;1980', '1980-02-01', 'compatible'), ('1970;unknown', '1980', 'unknown'),
            ('1980-02-30', '1980-02-01', 'unknown'), ('1970-01-01', None, 'unknown'),
        ]:
            with self.subTest(candidate=candidate, query=query):
                self.assertEqual(_identity_dob_evidence(candidate, query), expected)

    def test_missing_dob_keeps_potential_match(self):
        result = evaluate([row()], dob='1980-02-01')
        self.assertTrue(result['Is Sanctioned'])
        self.assertEqual(result['Candidate Evidence'][0]['dob_assessment'], 'unknown')

    def test_conflicting_dob_retained_as_excluded_evidence(self):
        result = evaluate([row(birth_date='1970-01-01')], dob='1980-02-01')
        self.assertFalse(result['Match Found'])
        self.assertEqual(result['Candidate Evidence'][0]['disposition'], 'excluded')
        self.assertEqual(result['Confidence'], 'Not assessed')

    def test_country_does_not_clear_name_match(self):
        item = row('Acme Trading Ltd', countries='US;Canada'); item['schema'] = 'Organization'
        result = evaluate([item], 'Acme Trading Ltd', country='UK', entity_type='Organization')
        self.assertTrue(result['Match Found'])
        self.assertEqual(result['Candidate Evidence'][0]['country_assessment'], 'conflicting')
        compatible = evaluate([item], 'Acme Trading Ltd', country='Canada', entity_type='Organization')
        self.assertEqual(compatible['Candidate Evidence'][0]['country_assessment'], 'compatible')

    def test_alias_and_raw_date_preserved(self):
        item = row('Different Display Name', birth_date='1980-01-01', raw_json=json.dumps({'aliases':'Jane Smith;J Smith', 'birth_date':'1980'}))
        result = evaluate([item], dob='1980-06-15')
        self.assertTrue(result['Match Found'])
        self.assertTrue(result['Candidate Evidence'][0]['matched_alias'])
        self.assertEqual(result['Candidate Evidence'][0]['birth_date'], '1980')

    def test_no_top_50_filter_before_identity_decision(self):
        items = [row(birth_date='1970-01-01') for _ in range(55)] + [row(birth_date='1980-02-01')]
        result = evaluate(items, dob='1980-02-01')
        self.assertTrue(result['Match Found'])
        self.assertEqual(result['Candidate Evidence Summary']['excluded'], 55)
        self.assertTrue(result['Candidate Evidence Summary']['display_truncated'])
        self.assertEqual(result['Candidate Evidence'][0]['dob_assessment'], 'exact')

    def test_dual_merge_keeps_pep_and_sanctions_evidence(self):
        pep = row(); pep['source_type'] = 'peps'
        person = evaluate([pep])
        organisation = row('Example Ltd'); organisation['schema'] = 'Organization'
        org = evaluate([organisation], 'Example Ltd', entity_type='Organization')
        result = _merge_dual_type_results(person, org, name='Example Ltd', submitted_entity_type='Person', pep_checked=True)
        self.assertTrue(result['Is PEP']); self.assertTrue(result['Is Sanctioned'])
        self.assertEqual(len(result['Candidate Evidence']), 2)

    def test_parquet_uses_same_evidence(self):
        rows = [row(birth_date='1980')]
        with patch('utils.get_opensanctions_df', return_value=pd.DataFrame(rows)):
            result = perform_opensanctions_check('Jane Smith', '1980-06-15', include_peps=False, log_search=False)
        self.assertEqual(result['Candidate Evidence'], evaluate(rows, dob='1980-06-15')['Candidate Evidence'])

    def test_query_dates_do_not_invent_precision(self):
        for raw in ('0000', '9999', '1980-02', 'today', '31-02-1980'):
            self.assertIsNone(_normalize_dob(raw))
        self.assertEqual(_normalize_dob('15/06/1980'), '1980-06-15')
        self.assertEqual(_normalize_dob('1980'), '1980')
