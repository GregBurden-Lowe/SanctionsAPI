"""Shadow hypotheses only; these tests do not certify production recall."""
from shadow_matching import evaluate


def test_organisation_prefix_examples_are_rejected():
    for q, candidate in [('Househaw', 'HOUSE OF SHIPPING INVESTMENT FZCO'),
                         ('Marvinez & Partners Ltd', 'INTERNATIONAL MARINE MANAGEMENT FZE'),
                         ('i4ya Limited', 'Гатэль “Мiнск”')]:
        assert evaluate(q, candidate, 'Organization', 'Organization')[0] is None


def test_organisation_typo_and_legal_form_can_survive():
    score, _ = evaluate('Volgaa Group', 'OOO Volga Group', 'Organization', 'Organization')
    assert score is not None


def test_typo_in_a_long_legal_form_does_not_become_a_distinctive_word():
    for query, candidate in [('cloed joint stock company rsk technologies', 'CLOSED JOINT STOCK COMPANY RSK TECHNOLOGIES'),
                             ('fotis lines incrporated', 'FOTIS LINES INCORPORATED'),
                             ('joint stock comany almaz antey', 'JOINT STOCK COMPANY ALMAZ ANTEY')]:
        assert evaluate(query, candidate, 'Organization', 'Organization')[0] is not None


def test_adjacent_transposition_of_organisation_word_is_supported():
    assert evaluate('ykaera y lane sas', 'YAKERA Y LANE SAS', 'Organization', 'Organization')[0] is not None


def test_legal_form_transposition_and_single_substitution_are_supported():
    for query, candidate, kind in [
        ('lmiited liability company globaltek', 'LIMITED LIABILITY COMPANY GLOBALTEK', 'Organization'),
        ('riavan danilovich kurbanov', 'Rizvan Danilovich KURBANOV', 'Person'),
        ('ataantic diamond group', 'ATLANTIC DIAMOND GROUP', 'Organization'),
    ]:
        assert evaluate(query, candidate, kind, kind)[0] is not None


def test_numeric_identifiers_do_not_use_the_new_substitution_exception():
    assert evaluate('Orion 12345', 'Orion 12346', 'Organization', 'Organization')[0] is None


def test_typo_in_a_shared_generic_word_does_not_hide_a_listing():
    assert evaluate('Good Luck Sihpping Company', 'Good Luck Shipping Company', 'Organization', 'Organization')[0] is not None
    assert evaluate('Good Luck Shapping Company', 'Good Luck Shipping Company', 'Organization', 'Organization')[0] is not None
    assert evaluate('Househaw Sihpping', 'House Shipping', 'Organization', 'Organization')[0] is None


def test_matching_generic_words_are_not_penalised_twice():
    score, _ = evaluate('skahart management services private limited',
                        'SKYHART MANAGEMENT SERVICES PRIVATE LIMITED', 'Organization', 'Organization')
    assert score is not None and score >= 75


def test_nonidentical_organisation_name_never_scores_100():
    score, _ = evaluate('Rapid Heath lmiited', 'RAPID HEATH LIMITED', 'Organization', 'Organization')
    assert score is not None and score < 100


def test_listed_initial_needs_another_exact_substantive_name():
    score, reason = evaluate('Mr Ethan Marlow', 'Marlow E', 'Person', 'Organization')
    assert reason == 'ambiguous_partial_name_review' and score == 75
    score, reason = evaluate('Jonathon Hale', 'Jonathan H. Otley', 'Person', 'Person')
    assert reason == 'ambiguous_name_review' and score <= 69


def test_short_listed_name_can_be_reviewed_when_two_full_words_agree():
    score, reason = evaluate('Daniel Evan Marlow', 'Evan Marlow', 'Person', 'Person')
    assert reason == 'ambiguous_partial_name_review' and score == 67
    score, reason = evaluate('James Martin Carter', 'James Martin Brown', 'Person', 'Person')
    assert reason == 'ambiguous_name_review' and score <= 69


def test_transliterated_person_and_generic_company_are_reviewable():
    for query, candidate, kind in [('Dareem Zanem', 'Darim Mansur AL-ZANIMI', 'Person'),
                                    ('H Vistok', 'Zoran Anis Visteyak HUSSAIN', 'Person'),
                                    ('Norture Group', 'NORTPUR GROUP', 'Organization'),
                                    ('First Move Property Group', 'FIRST GROUP', 'Organization')]:
        score, reason = evaluate(query, candidate, kind, kind)
        assert score is not None and score <= 69 and reason == 'ambiguous_name_review'


def test_short_company_typo_needs_an_exact_shared_business_word():
    assert evaluate('QEK Trading Ltd', 'QET TRADING CO., LIMITED', 'Organization', 'Person')[0] is not None
    assert evaluate('QEK Ltd', 'QET LIMITED', 'Organization', 'Person')[0] is None


def test_query_initial_remains_possible():
    assert evaluate('J Smith', 'John Smith', 'Person', 'Person')[1] == 'name_parts_agree'


def test_cross_type_uses_the_candidate_name_rules():
    assert evaluate('Alan Smith', 'LLC Smith', 'Organization', 'Person')[0] is None
    assert evaluate('Volga', 'Volga', 'Organization', 'Person')[1] == 'name_parts_agree'
    assert evaluate('Mr Ethan Marlow', 'Ethan MARLOW', 'Person', 'Organization')[1] == 'name_parts_agree'
    assert evaluate('Mr James Darrow', 'James DARRON', 'Person', 'Organization')[1] == 'name_parts_agree'
    assert evaluate('Rivertons', 'RIVERTON FZCO', 'Organization', 'Person')[0] is not None
    assert evaluate('Househaw', 'HOUSE OF SHIPPING INVESTMENT FZCO', 'Organization', 'Person')[0] is None


def test_entity_type_does_not_change_name_assessment():
    for query, candidate, kind in [('Mr James Darrow', 'James DARRON', 'Person'),
                                   ('Rivertons', 'RIVERTON FZCO', 'Organization'),
                                   ('Alan Smith', 'LLC Smith', 'Organization')]:
        assert evaluate(query, candidate, kind, 'Person') == evaluate(query, candidate, kind, 'Organization')


def test_integrated_low_score_is_reviewable_and_explained():
    from name_matching import TokenStats
    from utils import _evaluate_watchlist_rows
    item = dict(name='James Martin Brown', schema='Person', source_type='sanctions',
                dataset='fixture', id='fixture-1', birth_date='1980-01-01')
    def run(dob):
        return _evaluate_watchlist_rows('James Martin Carter', dob, None, 'Person',
                                        [item], 'fixture', token_stats=TokenStats({}, 100))
    possible = run(None)
    evidence = possible['Candidate Evidence'][0]
    assert possible['Match Found'] and evidence['name_score'] <= 69
    assert evidence['match_rule'] == 'ambiguous_name_review'
    assert 'other parts differ or are missing' in evidence['reasons'][0]
    conflict = run('1970-01-01')
    assert not conflict['Match Found']
    assert conflict['Candidate Evidence'][0]['dob_assessment'] == 'conflicting'


def test_integrated_organisation_prefix_is_rejected():
    from name_matching import TokenStats
    from utils import _evaluate_watchlist_rows
    item = dict(name='HOUSE OF SHIPPING INVESTMENT FZCO', schema='Organization',
                source_type='sanctions', dataset='fixture', id='fixture-2')
    result = _evaluate_watchlist_rows('Househaw', None, None, 'Organization', [item],
                                      'fixture', token_stats=TokenStats({}, 100))
    assert not result['Match Found']
