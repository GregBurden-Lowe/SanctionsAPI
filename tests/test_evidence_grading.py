"""Shadow evidence grades; these tests do not certify production recall."""
import shadow_matching
import utils
from evidence_grading import (BELOW_ALERT_THRESHOLD, DOB_REQUIRED, LISTED_DOB_NOT_COMPARABLE, NO_DOB_SUPPLIED,
                              POSSIBLE, STRONG, WEAK, apply_shadow_policy, grade_person_name, recording,
                              transliteration_variant)
from name_matching import TokenStats


def grade(query, candidate):
    return grade_person_name(query, candidate)[0]


def person(name, record_id, **extra):
    return dict(name=name, schema='Person', source_type='sanctions', dataset='fixture', id=record_id, **extra)


def screen(query, dob, rows, entity_type='Person'):
    with recording() as grades:
        result = utils._evaluate_watchlist_rows(query, dob, None, entity_type, rows, 'fixture',
                                                token_stats=TokenStats({}, 100), submitted_entity_type='Person')
    return result, grades


def shadow_of(query, dob, rows, submitted='Person', entity_type='Person'):
    live, grades = screen(query, dob, rows, entity_type)
    shadow, changed = apply_shadow_policy(live, grades, submitted_type=submitted, dob=dob)
    return live, shadow, changed


def test_explained_names_are_strong_with_at_most_one_difference():
    for query, candidate in [('Alexander Petrov', 'Alexander PETROV'),
                             ('Rizvan Kurbanov', 'Rizvan Danilovich KURBANOV'),      # omitted patronymic
                             ('Riavan Danilovich Kurbanov', 'Rizvan Danilovich KURBANOV'),
                             ('J Smith', 'John Smith'),
                             ('Kurbanov Rizvan', 'Rizvan KURBANOV')]:
        assert grade(query, candidate) == STRONG


def test_several_differences_are_possible_not_weak():
    for query, candidate in [('Jose Martinez', 'Jose Daniel MARTINEZ LOPEZ'),
                             ('Hassan Ahmed', 'Hasan Ahmad Hasan Muhammad AL-KUHLANI'),
                             ('Vialij Zyov', 'Vitalij ZYKOV')]:
        assert grade(query, candidate) == POSSIBLE


def test_unexplained_search_word_is_weak():
    for query, candidate in [('James Martin Carter', 'James Martin Brown'),
                             ('Olga Morozov', 'Oleg Viktorovich MOROZOV'),
                             ('Sana Rahman', 'Abdul Rahman S. Taha'),
                             ('Mr Ethan Marlow', 'Marlow E'),
                             ('Alexander Petrov', 'Alexei Petrovich MAYOROV')]:
        assert grade(query, candidate) == WEAK


def test_transliteration_variant_explains_a_search_word():
    # The matcher's own word test does not pair these, but only a vowel spelling differs.
    for query, candidate in [('Arkadii Rotinberg', 'Arkady Romanovich ROTENBERG'),
                             ('Dmitrii Medvedev', 'Dmitry Anatolyevich MEDVEDEV'),
                             ('Dareem Zanem', 'Darim Mansur AL-ZANIMI')]:
        assert grade(query, candidate) != WEAK
    for a, b in [('arkadii', 'arkady'), ('dmitrii', 'dmitry'), ('mikhail', 'mihail'), ('zanem', 'zanimi')]:
        assert transliteration_variant(a, b) and transliteration_variant(b, a)


def test_different_names_are_not_transliteration_variants():
    # Different consonants, or a short word where a vowel change is usually another name.
    for a, b in [('olga', 'oleg'), ('chen', 'chun'), ('ming', 'mingguo'), ('carter', 'carver'), ('saleh', 'faleh'),
                 ('alexander', 'alexei'), ('hernandez', 'fernandez'), ('orion12', 'orion13')]:
        assert not transliteration_variant(a, b)
    for query, candidate in [('Olga Morozov', 'Oleg Viktorovich MOROZOV'), ('Chen Kim', 'KIM Chun-sam')]:
        assert grade(query, candidate) == WEAK


def test_family_name_is_not_explained_by_a_patronymic():
    result, detail = grade_person_name('Andrei Ivanov', 'Andrei Ivanovich SHVED')
    assert result == WEAK and detail['patronymic_pairs_set_aside'] == [('ivanov', 'ivanovich')]
    # The same word pair stands when no listed family name is left unmatched.
    assert grade('Roman Abramov', 'Roman ABRAMOVICH') == STRONG
    # A misspelt patronymic still matches a patronymic.
    assert grade('Aleksei Aleksivich Naydenko', 'Aleksey Alekseevich NAYDENKO') != WEAK


def test_listed_name_contained_in_a_longer_search_name_is_possible():
    assert grade('Lei Hao Zhang', 'Lei ZHANG') == POSSIBLE
    assert grade('A Ibrahim al Hashimi al Qurashi', 'Abu Ibrahim al-Hashimi al-Qurashi') == POSSIBLE
    # One exact word is not enough, and two extra search words are too many.
    assert grade('Lei Hao Zhang', 'ZHANG') == WEAK
    assert grade('Lei Hao Min Zhang', 'Lei ZHANG') == WEAK


def test_weak_candidate_without_a_supplied_date_of_birth_needs_one():
    rows = [person('James Martin Brown', 'fixture-1', birth_date='1980-01-01')]
    live, shadow, changed = shadow_of('James Martin Carter', None, rows)
    assert live['Match Found'] and live['Candidate Evidence'][0]['disposition'] == 'potential_match'   # input untouched
    assert shadow['Shadow Evidence Grades']['outcome'] == DOB_REQUIRED
    assert shadow['Check Summary']['Status'] == 'DOB Required' and shadow['Risk Level'] == 'Unresolved'
    assert not shadow['Match Found'] and 'not a clearance' in shadow['Decision Explanation']
    assert shadow['Candidate Evidence'][0]['disposition'] == DOB_REQUIRED
    assert [(c['record_id'], c['reason']) for c in changed] == [('fixture-1', NO_DOB_SUPPLIED)]


def test_weak_candidate_stays_an_alert_when_the_date_of_birth_agrees():
    rows = [person('James Martin Brown', 'fixture-1', birth_date='1980-01-01')]
    for dob in ('1980-01-01', '1980'):                                               # exact, then compatible
        _, shadow, changed = shadow_of('James Martin Carter', dob, rows)
        assert shadow['Match Found'] and not changed
        assert shadow['Shadow Evidence Grades']['outcome'] == 'unchanged'


def test_weak_candidate_is_demoted_when_a_supplied_date_of_birth_has_nothing_to_agree_with():
    live, shadow, changed = shadow_of('James Martin Carter', '1980-01-01', [person('James Martin Brown', 'fixture-1')])
    assert live['Candidate Evidence'][0]['dob_assessment'] == 'unknown'
    assert not shadow['Match Found'] and shadow['Shadow Evidence Grades']['outcome'] == 'no_alert'
    assert shadow['Candidate Evidence'][0]['disposition'] == BELOW_ALERT_THRESHOLD
    assert changed[0]['reason'] == LISTED_DOB_NOT_COMPARABLE


def test_searches_not_submitted_as_a_person_are_left_alone():
    rows = [person('James Martin Brown', 'fixture-1')]
    for submitted in ('Organization', 'Organisation', None, ''):
        _, shadow, changed = shadow_of('James Martin Carter', None, rows, submitted=submitted)
        assert shadow['Match Found'] and not changed
        assert shadow['Candidate Evidence'][0]['shadow_evidence_grade'] == WEAK       # graded, not acted on


def test_strong_and_possible_candidates_are_never_changed():
    rows = [person('Alexander PETROV', 'strong-1'), person('Jose Daniel MARTINEZ LOPEZ', 'possible-1')]
    for query, record_id, expected in [('Alexander Petrov', 'strong-1', STRONG), ('Jose Martinez', 'possible-1', POSSIBLE)]:
        for dob in (None, '1980-01-01'):
            live, grades = screen(query, dob, rows)
            assert grades[('person', record_id)]['grade'] == expected
            shadow, changed = apply_shadow_policy(live, grades, submitted_type='Person', dob=dob)
            assert shadow['Match Found'] and not changed
            assert shadow['Candidate Evidence'][0]['shadow_evidence_grade'] == expected


def test_row_grade_is_the_best_of_its_accepted_names():
    rows = [person('James Martin Brown', 'fixture-1', aliases='James Martin Carter')]
    live, grades = screen('James Martin Carter', None, rows)
    assert grades[('person', 'fixture-1')]['grade'] == STRONG
    assert grades[('person', 'fixture-1')]['graded_name'] == 'James Martin Carter'
    assert apply_shadow_policy(live, grades, submitted_type='Person', dob=None)[0]['Match Found']


def test_remaining_candidate_supplies_the_headline_when_another_is_changed():
    rows = [person('James Martin Brown', 'weak-1'), person('James Martin CARTER', 'strong-1')]
    _, shadow, changed = shadow_of('James Martin Carter', None, rows)
    assert [item['record_id'] for item in changed] == ['weak-1']
    assert shadow['Match Found'] and shadow['Sanctions Name'] == 'James Martin CARTER'
    assert shadow['Shadow Evidence Grades']['outcome'] == 'fewer_candidates'
    assert [e['disposition'] for e in shadow['Candidate Evidence']] == ['potential_match', DOB_REQUIRED]


def test_single_word_exact_date_of_birth_fallback_stays_an_alert():
    rows = [person('Marlow', 'fixture-1', birth_date='1980-01-01')]
    live, grades = screen('Ethan Marlow', '1980-01-01', rows)
    assert live['Match Found'] and ('person', 'fixture-1') not in grades
    shadow, changed = apply_shadow_policy(live, grades, submitted_type='Person', dob='1980-01-01')
    assert shadow['Match Found'] and not changed
    assert shadow['Candidate Evidence'][0]['shadow_evidence_grade'] == WEAK


def test_organisation_candidates_are_not_regraded():
    rows = [dict(name='NORTPUR GROUP', schema='Organization', source_type='sanctions', dataset='fixture', id='org-1')]
    live, grades = screen('Norture Group', None, rows, entity_type='Organization')
    assert live['Match Found'] and not grades
    shadow, changed = apply_shadow_policy(live, grades, submitted_type='Person', dob=None)
    assert shadow['Match Found'] and not changed
    assert 'shadow_evidence_grade' not in shadow['Candidate Evidence'][0]


def test_truncated_evidence_is_never_reported_as_resolved():
    for dob in (None, '1980-01-01'):
        live, grades = screen('James Martin Carter', dob, [person('James Martin Brown', 'fixture-1')])
        live['Candidate Evidence Summary']['display_truncated'] = True
        shadow, changed = apply_shadow_policy(live, grades, submitted_type='Person', dob=dob)
        assert changed and shadow['Match Found']
        assert shadow['Shadow Evidence Grades']['outcome'] == 'undetermined_truncated'


def test_recording_does_not_change_live_results_and_restores_the_matcher():
    original_apply, original_evaluate = shadow_matching.apply_rules, utils._evaluate_watchlist_rows
    rows = [person('James Martin Brown', 'fixture-1', birth_date='1980-01-01', aliases='J. M. Brown;"Brown; James"'),
            person('Alexander PETROV', 'fixture-2')]
    plain = utils._evaluate_watchlist_rows('James Martin Carter', None, None, 'Person', rows, 'fixture',
                                           token_stats=TokenStats({}, 100), submitted_entity_type='Person')
    recorded, grades = screen('James Martin Carter', None, rows)
    for result in (plain, recorded):
        result['Check Summary'].pop('Date')
    assert plain == recorded
    assert set(grades) == {('person', 'fixture-1')}       # every graded row maps back to its record
    assert shadow_matching.apply_rules is original_apply and utils._evaluate_watchlist_rows is original_evaluate
