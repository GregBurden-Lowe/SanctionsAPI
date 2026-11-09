"""Synthetic labelled regression corpus: not a production accuracy estimate."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import pandas as pd

from utils import _evaluate_watchlist_rows, _identity_dob_evidence, _normalize_dob, _parse_aliases, ScreeningUnavailableError
from api_server import _merge_dual_type_results


def row(name='Jane Smith', **kwargs):
    return dict(name=name, schema='Person', source_type='sanctions', dataset='OFAC', id='fixture-1', **kwargs)


def evaluate(rows, name='Jane Smith', dob=None, country=None, entity_type='Person'):
    return _evaluate_watchlist_rows(name, dob, country, entity_type, rows, 'fixture', True)


class MatchingEvidenceTests(unittest.TestCase):
    def test_vessel_single_word_for_person_and_full_alias(self):
        vessel = row('CARL', aliases='CARL BAUER'); vessel['schema'] = 'Vessel'
        partial = _evaluate_watchlist_rows('Carl Smith', None, None, 'Organization', [vessel], 'fixture',
                                          submitted_entity_type='Person')
        self.assertFalse(partial['Match Found'])
        exact = _evaluate_watchlist_rows('Carl Bauer', None, None, 'Organization', [vessel], 'fixture',
                                        submitted_entity_type='Person')
        self.assertTrue(exact['Match Found'])
        self.assertEqual(exact['Candidate Evidence'][0]['entity_type'], 'Vessel')
        self.assertEqual(exact['Candidate Evidence'][0]['matched_name'], 'CARL BAUER')
        single = _evaluate_watchlist_rows('Carl', None, None, 'Organization', [vessel], 'fixture',
                                         submitted_entity_type='Person')
        self.assertTrue(single['Match Found'])
        organisation = _evaluate_watchlist_rows('Carl Smith', None, None, 'Organization', [vessel], 'fixture',
                                               submitted_entity_type='Organization')
        self.assertTrue(organisation['Match Found'])

    def test_labelled_name_benchmark(self):
        cases = json.loads((Path(__file__).parent / 'fixtures/matching_cases.json').read_text())
        for case in cases:
            with self.subTest(case=case['label']):
                kind = case.get('type', 'Person')
                item = row(case['candidate']); item['schema'] = kind
                result = evaluate([item], case['query'], entity_type=kind)
                self.assertEqual(result['Match Found'], case['expected'])

    def test_generated_name_variants_and_conflicting_tokens(self):
        # Synthetic metamorphic checks exercise multiple names, but are not
        # independent identity labels or a production recall measurement.
        names = [('Aisha', 'Rahman'), ('Marta', 'Nowak'), ('Wei', 'Zhang'),
                 ('Daniel', 'Okafor'), ('Fatima', 'Hassan'), ('Olga', 'Petrova'),
                 ('Mohamed', 'Khalil'), ('Lucia', 'Romero'), ('Arjun', 'Kapoor'),
                 ('Sofia', 'Andersson'), ('Emeka', 'Nwosu'), ('Yuki', 'Tanaka')]
        for given, family in names:
            query = f'{given} {family}'
            for candidate, expected in ((query, True), (f'{family} {given}', True),
                                        (f'{given} Elizabeth {family}', True),
                                        (f'{given} Unrelatedsurname', False),
                                        (f'Unrelatedgiven {family}', False),
                                        (f'{given} {given}', False)):
                with self.subTest(query=query, candidate=candidate):
                    self.assertEqual(evaluate([row(candidate)], query)['Match Found'], expected)

    def test_dob_precision_and_unknown(self):
        for candidate, query, expected in [
            (None, '1980-02-01', 'unknown'), ('1980', '1980-02-01', 'compatible'),
            ('1980-02', '1980-02-01', 'compatible'), ('1980-02-01', '1980', 'compatible'),
            ('1980-02-01', '1980-02-01', 'exact'), ('1970', '1980', 'conflicting'),
            ('1970;1980', '1980-02-01', 'compatible'), ('1970;unknown', '1980', 'unknown'),
            ('1970;circa 1968', '1980', 'conflicting'), ('1970;1980', '1980', 'compatible'),
            ('circa 1968', '1967', 'compatible'), ('approximately 1968', '1969', 'compatible'),
            ('circa 1968', '1966', 'conflicting'), ('circa 1968', '1970', 'conflicting'),
            ('circa 1968;1970-12-25', '1970-12-25', 'exact'),
            ('circa 1968;unknown', '1980', 'unknown'), ('circa 1968-01-01', '1980', 'unknown'),
            ('03 Mar 1966', '1964-09-01', 'conflicting'), ('03 Mar 1966', '1966-03-03', 'exact'),
            ('03 Mar 1966', '1966', 'compatible'), ('Mar 1966', '1966-03-01', 'compatible'),
            ('Mar 1966', '1966-04-01', 'conflicting'),
            ('1970 to 1972', '1964-09-01', 'conflicting'), ('1970 to 1972', '1970-01-01', 'compatible'),
            ('1970 to 1972', '1972-12-31', 'compatible'), ('1970 to 1972', '1973', 'conflicting'),
            ('01 Mar 1965 to 31 Mar 1965', '1965-03-31', 'compatible'),
            ('01 Mar 1965 to 31 Mar 1965', '1965-04-01', 'conflicting'),
            ('1972 to 1970', '1964', 'unknown'), ('31 Feb 1966', '1964', 'unknown'),
            ('1970 to 1972;03 Mar 1966', '1966-03-03', 'exact'),
            ('1970 to 1972;unparsed', '1964', 'unknown'),
            ('1980-02-30', '1980-02-01', 'unknown'), ('1970-01-01', None, 'unknown'),
        ]:
            with self.subTest(candidate=candidate, query=query):
                self.assertEqual(_identity_dob_evidence(candidate, query), expected)

    def test_missing_dob_keeps_potential_match(self):
        result = evaluate([row()], dob='1980-02-01')
        self.assertTrue(result['Is Sanctioned'])
        self.assertEqual(result['Candidate Evidence'][0]['dob_assessment'], 'unknown')

    def test_ofac_written_dates_disambiguate_without_suppressing_name_only_matches(self):
        rows = [dict(name='Abdul RASHID BALUCH', schema='Person', source_type='sanctions', dataset='OFAC', id='ofac-17765', aliases='Qari Abdul RASHID', birth_date='1970 to 1972'),
                dict(name='Waseem Rauf LOAN', schema='Person', source_type='sanctions', dataset='OFAC', id='ofac-10593', aliases='Abdul Majid RASHID', birth_date='03 Mar 1966')]
        initial = evaluate(rows, name='Mr Abdur Rashid')
        self.assertTrue(initial['Match Found'])
        result = evaluate(rows, name='Mr Abdur Rashid', dob='1964-09-01')
        self.assertFalse(result['Match Found'])
        self.assertEqual({item['record_id'] for item in result['Candidate Evidence']}, {'ofac-17765', 'ofac-10593'})
        self.assertTrue(all(item['dob_assessment'] == 'conflicting' for item in result['Candidate Evidence']))

    def test_reviewed_links_combine_dates_only_for_complete_unchanged_group(self):
        import linked_listings
        first = dict(name='Jane Smith', schema='Person', source_type='sanctions', dataset='A', id='a', birth_date='1970')
        second = dict(name='Jane Smith', schema='Person', source_type='sanctions', dataset='B', id='b', birth_date='')
        base = evaluate([first, second], dob='1980')
        records = {e['record_id']: e['source_evidence_hash'] for e in base['Candidate Evidence']}
        link = {'records': records, 'reviewed_by': 'Compliance', 'review_reference': 'case-1'}
        with patch.object(linked_listings, 'REVIEWED_LINKS', (link,)):
            linked = evaluate([first, second], dob='1980')
            self.assertFalse(linked['Match Found'])
            self.assertTrue(all(e['linked_listing_evidence']['dob_assessment'] == 'conflicting' for e in linked['Candidate Evidence']))
            self.assertTrue(evaluate([second], dob='1980')['Match Found'])
            self.assertTrue(evaluate([first, dict(second, birth_date='1980')], dob='1980')['Match Found'])
            self.assertTrue(evaluate([first, second], dob='1970')['Match Found'])
            unrelated = dict(name='Jane Smith', schema='Person', source_type='sanctions', dataset='C', id='c', birth_date='')
            self.assertTrue(evaluate([first, second, unrelated], dob='1980')['Match Found'])
        with patch.object(linked_listings, 'REVIEWED_LINKS', (link, link)):
            self.assertTrue(evaluate([first, second], dob='1980')['Match Found'])
        self.assertTrue(evaluate([first, second], dob='1980')['Match Found'])

    def test_link_registry_changes_screening_fingerprint(self):
        import linked_listings
        from utils import screening_fingerprint
        args = ('Jane Smith', '1980', None, 'Person', 'postgres_beta')
        before = screening_fingerprint(*args)
        with patch.object(linked_listings, 'REVIEWED_LINKS', ({'records': {'a': '1', 'b': '2'}, 'reviewed_by': 'Compliance', 'review_reference': 'case-1'},)):
            self.assertNotEqual(before, screening_fingerprint(*args))

    def test_linked_approximate_year_uses_one_year_tolerance(self):
        import linked_listings
        first = dict(name='Jane Smith', schema='Person', source_type='sanctions', dataset='A', id='a', birth_date='circa 1970;1971')
        second = dict(name='Jane Smith', schema='Person', source_type='sanctions', dataset='B', id='b', birth_date='')
        evidence = evaluate([first, second], dob='1980')['Candidate Evidence']
        records = {e['record_id']: e['source_evidence_hash'] for e in evidence}
        with patch.object(linked_listings, 'REVIEWED_LINKS', ({'records': records, 'reviewed_by': 'Compliance', 'review_reference': 'case-1'},)):
            self.assertFalse(evaluate([first, second], dob='1980')['Match Found'])
            self.assertTrue(evaluate([first, second], dob='1969')['Match Found'])

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

    def test_incomplete_alias_does_not_override_conflicting_full_name(self):
        item = row('Claire Barlow', aliases='C. Barlow;C.L. Barlow;Barlow;Barlow, C.')
        for dob in (None, '1980-01-01'):
            result = evaluate([item], name='Evan Barlow', dob=dob)
            self.assertFalse(result['Match Found'])
            self.assertEqual(result['Top Matches'], [])
        self.assertTrue(evaluate([item], name='Claire Barlow')['Match Found'])

    def test_complete_alias_is_still_used(self):
        item = row('Claire Barlow', aliases='Barlow;Clara Barlow')
        result = evaluate([item], name='Clara Barlow')
        self.assertTrue(result['Match Found'])
        self.assertEqual(result['Candidate Evidence'][0]['matched_name'], 'Clara Barlow')

    def test_nested_csv_aliases_preserve_quoted_semicolons_and_quotes(self):
        self.assertEqual(_parse_aliases('Other;"Mohammad; Eslami";"John ""Jack"" Smith"'),
                         ['Other','Mohammad; Eslami','John "Jack" Smith'])
        item = row('Unrelated Display', aliases='Other;"Mohammad; Eslami"')
        result = evaluate([item], name='Mohammad Eslami')
        self.assertTrue(result['Match Found'])
        self.assertEqual(result['Candidate Evidence'][0]['matched_name'],'Mohammad; Eslami')
        self.assertFalse(evaluate([item], name='Doctor Mohammad Example')['Match Found'])

    def test_malformed_nested_csv_fails_incomplete(self):
        with self.assertRaises(ScreeningUnavailableError):
            evaluate([row('Unrelated Display',aliases='"unclosed;name')])

    def test_perfect_score_requires_the_same_full_token_multiset(self):
        for name,candidate in [('Jane Smith','Jane Elizabeth Smith'),
                               ('Milo A Milo','Milo Milo'),
                               ('J Smith','John Smith'),
                               ('Jane Smith Group','Jane Smith')]:
            with self.subTest(name=name,candidate=candidate):
                result=evaluate([row(candidate)],name)
                self.assertTrue(result['Match Found'])
                self.assertLess(result['Score'],100)
        self.assertEqual(evaluate([row('Smith Jane')],name='Jane Smith')['Score'],100)
        self.assertFalse(evaluate([row('Omar Al-Zafir')],name='Omar Al-Omar')['Match Found'])
        self.assertTrue(evaluate([row('Omar Omar')],name='Omar Al-Omar')['Match Found'])

    def test_extra_title_and_surname_does_not_suppress_two_name_match(self):
        result=evaluate([row('Aisha Rahman')],name='Mrs Aisha Rahman Hassan')
        self.assertTrue(result['Match Found'])
        self.assertLess(result['Score'],100)

    def test_single_word_name_requires_exact_full_dob_to_support_review(self):
        for candidate in (row('Claire Barlow', aliases='Barlow', birth_date='1980-01-01'),
                          row('Barlow', birth_date='1980-01-01')):
            for dob, expected in ((None, False), ('1980', False), ('1981-01-01', False), ('1980-01-01', True)):
                with self.subTest(candidate=candidate['name'], dob=dob):
                    result = evaluate([candidate], name='Evan Barlow', dob=dob)
                    self.assertEqual(result['Match Found'], expected)
                    if expected:
                        evidence = result['Candidate Evidence'][0]
                        self.assertEqual(evidence['matched_name'], 'Barlow')
                        self.assertEqual(evidence['dob_assessment'], 'exact')
                        self.assertLess(evidence['name_score'], 75)
                        self.assertIn('single-word', evidence['reasons'][0])

    def test_single_word_support_needs_exact_non_initial_token(self):
        for alias in ('Crawfor', 'I'):
            item = row('Claire Barlow', aliases=alias, birth_date='1980-01-01')
            self.assertFalse(evaluate([item], name='Evan Barlow', dob='1980-01-01')['Match Found'])

    def test_repeated_name_does_not_match_unrelated_second_token(self):
        item = row('Milo Milo')
        result = evaluate([item], name='Milo Winters')
        self.assertFalse(result['Match Found'])
        self.assertEqual(result['Top Matches'], [])
        self.assertTrue(evaluate([item], name='Milo Milo')['Match Found'])

    def test_no_top_50_filter_before_identity_decision(self):
        items = [row(birth_date='1970-01-01') for _ in range(55)] + [row(birth_date='1980-02-01')]
        result = evaluate(items, dob='1980-02-01')
        self.assertTrue(result['Match Found'])
        self.assertEqual(result['Candidate Evidence Summary']['excluded'], 55)
        self.assertTrue(result['Candidate Evidence Summary']['display_truncated'])
        self.assertEqual(result['Candidate Evidence'][0]['dob_assessment'], 'exact')

    def test_dual_merge_keeps_person_and_organisation_evidence(self):
        person = evaluate([row()])
        organisation = row('Example Ltd'); organisation['schema'] = 'Organization'
        org = evaluate([organisation], 'Example Ltd', entity_type='Organization')
        result = _merge_dual_type_results(person, org, name='Example Ltd', submitted_entity_type='Person')
        self.assertTrue(result['Is Sanctioned'])
        self.assertEqual(len(result['Candidate Evidence']), 2)
    def test_query_dates_do_not_invent_precision(self):
        for raw in ('0000', '9999', '1980-02', 'today', '31-02-1980'):
            self.assertIsNone(_normalize_dob(raw))
        self.assertEqual(_normalize_dob('15/06/1980'), '1980-06-15')
        self.assertEqual(_normalize_dob('1980'), '1980')
