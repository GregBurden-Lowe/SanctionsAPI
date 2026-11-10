"""Evidence grades for person name candidates.

Without grades every surviving candidate produces the same alert, whether the
name is exact or one search word found nothing to match. This module grades
how much of a person name is actually explained:

    strong    every search word is explained, with at most one difference
    possible  every search word is explained, with several differences; or the
              listed name is wholly contained in a longer search name
    weak      a search word is not explained and the listed name is not
              wholly accounted for either

The live matcher uses the grades only when SCREENING_EVIDENCE_GRADES is set:
`annotate` records the grade on each candidate and changes no decision;
`enforce` also applies the policy below. With the setting absent or `off`,
this module is not imported by the API or worker. The same policy can be
replayed as a shadow over unchanged results (scripts/shadow_grade_replay.py).

Policy, version 2. It changes only weak person candidates on searches
submitted as a person:

    date of birth agrees (exact or compatible)   stays an alert
    no date of birth was supplied                `dob_required`: not cleared and
                                                 not yet an analyst alert; the
                                                 search needs a date of birth
    date of birth supplied, listing has none
    that can be compared                         `below_alert_threshold`: kept
                                                 as evidence, not an alert

Strong and possible candidates, date-of-birth exclusions, every candidate on a
search submitted as an organisation, and all organisation, vessel and aircraft
candidates are unchanged. Version 1 demoted every uncorroborated weak
candidate; on the captured review queue that removed the alert from 14 of 31
cases a reviewer had marked as needing corroboration, all of them searches
with no date of birth or submitted as an organisation.

Grades are heuristics, not probabilities of identity, and `dob_required` is
never a clearance. Nothing here is validated on independently labelled cases.
"""
from contextlib import contextmanager
import copy
import re

from shadow_matching import align, words

STRONG, POSSIBLE, WEAK = 'strong', 'possible', 'weak'
DOB_REQUIRED = 'dob_required'
BELOW_ALERT_THRESHOLD = 'below_alert_threshold'
SHADOW_RULES_VERSION = 'offline-shadow-evidence-grades-v2'
NO_DOB_SUPPLIED = 'weak_name_no_date_of_birth_supplied'
LISTED_DOB_NOT_COMPARABLE = 'weak_name_listed_date_of_birth_not_comparable'
_RANK = {WEAK: 0, POSSIBLE: 1, STRONG: 2}
_CORROBORATING_DOB = ('exact', 'compatible')
REASON_TEXT = {
    NO_DOB_SUPPLIED: 'Part of the searched name is unmatched and no date of birth was supplied; '
                     'a date of birth is required before this candidate can be assessed.',
    LISTED_DOB_NOT_COMPARABLE: 'Part of the searched name is unmatched and the listing has no date of birth '
                               'that can be compared; kept as evidence, not an alert.',
}
FALLBACK_DETAIL = {'reason': 'single_word_with_exact_date_of_birth'}

# A listed word with one of these endings is a patronymic, not a family name.
_PATRONYMIC = re.compile(r'(ovich|evich|ovna|evna|ichna|ovych|ivna|ogly|oglu|kyzy)$')
# Looser: a search word ending like this may itself be a (misspelt) patronymic.
_PATRONYMIC_LIKE = re.compile(r'(ich|vna|chna|vych|vic|vitj|ogly|oglu|uly|kyzy|qizi)$')


def grade_person_name(query, candidate):
    """Grade one listed name or alias against the search name: (grade, detail)."""
    q, c = words(query), words(candidate)
    if not q or not c:
        return WEAK, {'reason': 'no_comparable_name_parts'}
    pairs = align(q, c)
    paired_listed = {j for _, j, _ in pairs}
    # "Ivanov" resembles the patronymic "Ivanovich", but when the listed family
    # name is itself left unmatched the search word has not been explained.
    family_name_unmatched = any(
        j not in paired_listed and len(word) > 1 and not _PATRONYMIC.search(word) for j, word in enumerate(c))
    kept, set_aside = [], []
    for i, j, _ in pairs:
        a, b = q[i], c[j]
        if (a != b and min(len(a), len(b)) > 1 and family_name_unmatched
                and _PATRONYMIC.search(b) and not _PATRONYMIC_LIKE.search(a)):
            set_aside.append((a, b))
        else:
            kept.append((i, j))
    kept_query = {i for i, _ in kept}
    kept_listed = {j for _, j in kept}
    unmatched_query = [word for i, word in enumerate(q) if i not in kept_query]
    # Omitted patronymics and stray listed initials are normal, not differences.
    unmatched_listed = [word for j, word in enumerate(c)
                        if j not in kept_listed and len(word) > 1 and not _PATRONYMIC.search(word)]
    spelling = [(q[i], c[j]) for i, j in kept if q[i] != c[j] and min(len(q[i]), len(c[j])) > 1]
    initials = [(q[i], c[j]) for i, j in kept if q[i] != c[j] and min(len(q[i]), len(c[j])) == 1]
    exact = [q[i] for i, j in kept if q[i] == c[j]]
    detail = {'unmatched_search_words': unmatched_query, 'unmatched_listed_words': unmatched_listed,
              'spelling_differences': spelling, 'initials': initials, 'patronymic_pairs_set_aside': set_aside}
    if unmatched_query:
        # The listed name can be wholly contained in a longer search name: a
        # list that records "Lei ZHANG" for a customer "Lei Hao Zhang", or a
        # leading initial whose listed word is a particle ("A Ibrahim
        # al-Hashimi" / "Abu Ibrahim ..."). The listing is fully explained by
        # exact words, so only the extra search word is unaccounted for.
        listed_fully_explained = (not unmatched_listed and not spelling and not initials
                                  and sum(len(word) >= 3 for word in exact) >= 2)
        one_extra = len(unmatched_query) == 1 or all(len(word) == 1 for word in unmatched_query)
        if listed_fully_explained and one_extra:
            return POSSIBLE, detail
        return WEAK, detail
    differences = len(spelling) + len(initials) + len(unmatched_listed)
    return (STRONG if differences <= 1 else POSSIBLE), detail


def best_person_grade(query, names, hits):
    """Best grade among the names the matcher accepted for one listing."""
    best = None
    for _, _, index in hits:
        grade, detail = grade_person_name(query, names[index])
        if best is None or _RANK[grade] > _RANK[best['grade']]:
            best = {'grade': grade, 'graded_name': names[index], 'detail': detail}
    return best


def is_person_submission(submitted_type):
    """Only an input that is exactly "Person" counts; anything else is left alone."""
    return str(submitted_type or '').strip().lower() == 'person'


def graded_disposition(grade, dob_assessment, *, person_submitted, dob_supplied):
    """Policy for one person candidate that is currently a potential match."""
    if grade != WEAK or not person_submitted or dob_assessment in _CORROBORATING_DOB:
        return 'potential_match', None
    if not dob_supplied:
        return DOB_REQUIRED, NO_DOB_SUPPLIED
    return BELOW_ALERT_THRESHOLD, LISTED_DOB_NOT_COMPARABLE


def shadow_disposition(evidence, grade, *, person_submitted, dob_supplied):
    """Disposition under the shadow policy, with the reason code when it changes."""
    if evidence.get('disposition') != 'potential_match' or evidence.get('entity_type') != 'Person':
        return evidence.get('disposition'), None
    # No grade means no name was accepted: the single-word, exact-date-of-birth
    # fallback. It is weak by construction and already corroborated.
    return graded_disposition(grade or WEAK, evidence.get('dob_assessment'),
                              person_submitted=person_submitted, dob_supplied=dob_supplied)


def apply_shadow_policy(result, grades, *, submitted_type, dob):
    """Return (shadow copy of a screening result, the candidates it changes).

    `grades` maps (check kind, record id) to a grade record, as collected by
    `recording()`. `submitted_type` and `dob` are the search inputs. Only an
    input that is exactly "Person" counts as a person submission; anything
    else leaves every candidate as it is. The input result is not modified.
    """
    import utils
    person_submitted = is_person_submission(submitted_type)
    dob_supplied = bool(utils._normalize_dob(dob)) if dob else False
    shadow = copy.deepcopy(result)
    evidence = shadow.get('Candidate Evidence') or []
    changed = []
    counts = {STRONG: 0, POSSIBLE: 0, WEAK: 0}
    for item in evidence:
        if item.get('entity_type') != 'Person':
            continue
        record = grades.get(('person', item.get('record_id')))
        grade = record['grade'] if record else WEAK
        item['shadow_evidence_grade'] = grade
        if record:
            item['shadow_grade_detail'] = {'graded_name': record['graded_name'], **record['detail']}
        if item.get('disposition') != 'potential_match':
            continue
        counts[grade] += 1
        disposition, reason = shadow_disposition(item, grade, person_submitted=person_submitted,
                                                 dob_supplied=dob_supplied)
        if reason:
            item['disposition'] = disposition
            item['reasons'] = list(item.get('reasons') or []) + ['Shadow policy: ' + REASON_TEXT[reason]]
            changed.append({'matched_name': item.get('matched_name'), 'reason': reason, 'disposition': disposition,
                            'record_id': item.get('record_id'), 'previous_score': item.get('name_score'),
                            'entity_type': 'Person', 'dob_assessment': item.get('dob_assessment'),
                            'match_rule': item.get('match_rule')})
    remaining = [e for e in evidence if e.get('disposition') == 'potential_match' and e.get('source_type') == 'sanctions']
    needs_dob = [e for e in evidence if e.get('disposition') == DOB_REQUIRED]
    truncated = bool((shadow.get('Candidate Evidence Summary') or {}).get('display_truncated'))
    outcome = 'unchanged'
    if changed and remaining:
        outcome = 'fewer_candidates'
        best = max(remaining, key=lambda e: e.get('name_score') or 0)
        score = best.get('name_score') or 0
        shadow.update({'Sanctions Name': best.get('name'), 'Birth Date': best.get('birth_date'),
                       'Regime': best.get('regime'), 'Position': best.get('position'), 'Score': score,
                       'Confidence': 'High' if score >= 90 else 'Medium' if score >= 80 else 'Low'})
    elif changed and truncated:
        # Candidates beyond the displayed evidence were never graded, so this
        # search cannot be shown to have no alert left.
        outcome = 'undetermined_truncated'
    elif changed:
        outcome = DOB_REQUIRED if needs_dob else 'no_alert'
        shadow.update({'Sanctions Name': None, 'Birth Date': None, 'Regime': None, 'Position': None,
                       'Is Sanctioned': False, 'Match Found': False, 'Score': 0, 'Confidence': 'Not assessed',
                       'Risk Level': 'Unresolved' if needs_dob else 'Cleared',
                       'Decision Explanation': (
                           'Shadow policy: only weak name evidence was found and no date of birth was supplied. '
                           'Supply a date of birth and screen again. This is not a clearance.' if needs_dob else
                           'Shadow policy: only weak, uncorroborated name evidence remains. '
                           'This is a diagnostic, not a clearance.')})
        if isinstance(shadow.get('Check Summary'), dict):
            shadow['Check Summary']['Status'] = 'DOB Required' if needs_dob else 'Cleared'
    order = {'potential_match': 0, DOB_REQUIRED: 1, BELOW_ALERT_THRESHOLD: 2}
    evidence.sort(key=lambda e: (order.get(e.get('disposition'), 3), -(e.get('name_score') or 0)))
    shadow['Shadow Evidence Grades'] = {
        'rules': SHADOW_RULES_VERSION, 'outcome': outcome, 'person_submitted': person_submitted,
        'dob_supplied': dob_supplied,
        'dob_required': sum(c['disposition'] == DOB_REQUIRED for c in changed),
        'demoted': sum(c['disposition'] == BELOW_ALERT_THRESHOLD for c in changed),
        'person_potential_matches_by_grade': counts}
    return shadow, changed


@contextmanager
def recording():
    """Collect a grade for every person listing the live matcher evaluates.

    Wraps the matcher's own name-rule and row-evaluation functions for the
    duration of the block and restores them afterwards. The wrapped functions
    return exactly what the originals return, so live results are unchanged.
    Yields a dict {(check kind, record id): grade record}; clear it per search.
    """
    import shadow_matching
    import utils
    original_apply = shadow_matching.apply_rules
    original_evaluate = utils._evaluate_watchlist_rows
    grades = {}
    by_names = {}

    def apply_and_grade(query, names, hits, added, entity_type, submitted_type):
        hits, added = original_apply(query, names, hits, added, entity_type, submitted_type)
        if hits and (entity_type or 'Person').strip().lower() != 'organization':
            by_names[tuple(names)] = best_person_grade(query, names, hits)
        return hits, added

    def evaluate_and_map(name, dob, country, entity_type, rows, *args, **kwargs):
        rows = list(rows)
        by_names.clear()
        result = original_evaluate(name, dob, country, entity_type, rows, *args, **kwargs)
        if (entity_type or 'Person').strip().lower() != 'organization':
            for row in rows:
                record = by_names.get(_row_names(utils, row))
                record_id = utils._safe_str(row.get('entity_id') or row.get('id')) or None
                if record and record_id:
                    grades[('person', record_id)] = record
        return result

    shadow_matching.apply_rules = apply_and_grade
    utils._evaluate_watchlist_rows = evaluate_and_map
    try:
        yield grades
    finally:
        shadow_matching.apply_rules = original_apply
        utils._evaluate_watchlist_rows = original_evaluate


def _row_names(utils, row):
    """The name list the evaluator builds for a row; tests check the two agree."""
    import json
    raw = row.get('raw_json') or {}
    if isinstance(raw, str):
        raw = json.loads(raw)
    aliases = utils._safe_str(raw.get('aliases') or row.get('aliases'))
    return tuple(dict.fromkeys([utils._safe_str(row.get('name'))] + utils._parse_aliases(aliases)))
