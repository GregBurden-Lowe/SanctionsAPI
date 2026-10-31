"""Input integrity and interpretation safeguards for private diagnostic replays."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from scripts.validate_acceptance import load_pack, inputs_for, outcome


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
        self.assertEqual(outcome({'Is Sanctioned':True,'Is PEP':True}),'potential_sanctions_match')
        self.assertEqual(outcome({'Is Sanctioned':False,'Is PEP':True}),'potential_pep_match')
        self.assertEqual(outcome({'Is Sanctioned':False,'Is PEP':False}),'no_potential_match')
