"""Input integrity and interpretation safeguards for private diagnostic replays."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from scripts.validate_acceptance import load_pack, inputs_for, outcome, compare_backends


class AcceptanceValidationTests(unittest.TestCase):
    def test_export_pair_must_have_exact_unique_case_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            with (path/'cases.csv').open('w') as stream:
                writer = csv.DictWriter(stream,fieldnames=['case_id','expected_outcome'])
                writer.writeheader(); writer.writerow({'case_id':'one','expected_outcome':''})
            (path/'evidence.json').write_text(json.dumps([{'case_id':'one','outcome':'False Positive - Proceeded'}]))
            cases = load_pack(path)
            self.assertEqual(cases[0]['acceptance_fields']['expected_outcome'],'')
            # A previous false-positive disposition must never fill an expected engine label.
            self.assertNotEqual(cases[0]['outcome'],cases[0]['acceptance_fields']['expected_outcome'])
            (path/'evidence.json').write_text(json.dumps([{'case_id':'other'}]))
            with self.assertRaises(ValueError):
                load_pack(path)
            (path/'evidence.json').write_text(json.dumps([{'case_id':'one'},{'case_id':'one'}]))
            with self.assertRaises(ValueError):
                load_pack(path)

    def test_historical_type_alias_is_explicit_not_a_silent_input_change(self):
        raw = {'name':'Fixture Ltd','entity_type':'Organisation','dob':None,'country':'GB'}
        args, changes = inputs_for({'inputs':raw})
        self.assertEqual(args['entity_type'],'Organization')
        self.assertTrue(changes)
        self.assertEqual(raw['entity_type'],'Organisation')
        with self.assertRaises(ValueError):
            inputs_for({'inputs':{'name':'Fixture','entity_type':'Vessel'}})

    def test_result_categories_are_potential_matches_not_confirmed_identity(self):
        self.assertEqual(outcome({'Is Sanctioned':True}),'potential_sanctions_match')
        self.assertEqual(outcome({'Is Sanctioned':False}),'no_potential_match')

    def test_candidate_comparison_finds_misses_even_when_both_flag(self):
        def candidate(source_hash, name):
            return {'source_evidence_hash': source_hash, 'entity_type': 'Person',
                    'disposition': 'potential_match', 'matched_name': name, 'name_score': 90}
        def result(items):
            return {'outcome': 'potential_sanctions_match', 'result': {
                'Candidate Evidence': items, 'Candidate Evidence Summary': {'display_truncated': False}}}
        a, b = candidate('a', 'First Name'), candidate('b', 'Second Name')
        comparison = compare_backends(result([a]), result([a, b]))
        self.assertEqual(comparison['status'], 'different')
        self.assertTrue(comparison['outcomes_agree'])
        self.assertEqual(comparison['parquet_only'][0]['identity'], 'b')
        self.assertEqual(compare_backends(result([a]), result([a]))['status'], 'agree')

    def test_incomplete_and_truncated_results_are_not_parity(self):
        complete = {'outcome': 'no_potential_match', 'result': {'Candidate Evidence': [],
                    'Candidate Evidence Summary': {'display_truncated': False}}}
        self.assertEqual(compare_backends(complete, {'outcome': 'incomplete'})['status'], 'incomplete')
        truncated = {'outcome': 'no_potential_match', 'result': {'Candidate Evidence': [],
                     'Candidate Evidence Summary': {'display_truncated': True}}}
        self.assertEqual(compare_backends(complete, truncated)['status'], 'incomplete')
