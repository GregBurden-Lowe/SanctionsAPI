"""Score independently labelled replay cases; never infer truth from historical dispositions."""
from collections import Counter
from datetime import date
import math

VALID_OUTCOMES = {'no_potential_match', 'potential_sanctions_match'}


def score_cases(cases, backend='postgres'):
    counts = Counter()
    times = []
    assessments = []
    for case in cases:
        actual = case.get(backend)
        if not actual:
            continue
        counts['executed'] += 1
        if actual.get('outcome') == 'incomplete':
            counts['incomplete'] += 1
        if isinstance(actual.get('seconds'), (int,float)):
            times.append(actual['seconds'])
        labels = case.get('acceptance_fields', {})
        expected = labels.get('expected_outcome', '').strip()
        labelled = expected in VALID_OUTCOMES and all(labels.get(k, '').strip() for k in ('acceptance_reviewer','acceptance_reason','acceptance_date'))
        try:
            date.fromisoformat(labels.get('acceptance_date', ''))
        except (ValueError, TypeError):
            labelled = False
        wanted = {v.strip() for v in labels.get('expected_watchlist_entry','').split(';') if v.strip()}
        if expected != 'no_potential_match' and not wanted:
            labelled = False
        if not labelled:
            counts['unlabelled'] += 1
            continue
        counts['labelled'] += 1
        positive = expected != 'no_potential_match'
        counts['expected_positive' if positive else 'expected_negative'] += 1
        if actual.get('outcome') == 'incomplete':
            counts['incomplete_positive' if positive else 'incomplete_negative'] += 1
            assessment = 'incomplete'
        elif positive:
            result = actual.get('result', {})
            ids = {str(c.get('record_id')) for c in result.get('Candidate Evidence', []) if c.get('disposition') == 'potential_match'}
            if wanted <= ids and actual.get('outcome') == expected:
                counts['true_positive'] += 1
                assessment = 'expected_candidates_detected'
            elif result.get('Candidate Evidence Summary', {}).get('display_truncated'):
                counts['unassessable_positive'] += 1
                assessment = 'candidate_evidence_truncated'
            else:
                counts['false_negative'] += 1
                assessment = 'expected_candidate_or_risk_type_missing'
        elif actual.get('outcome') == 'no_potential_match':
            counts['true_negative'] += 1
            assessment = 'correct_no_match'
        else:
            counts['false_positive'] += 1
            assessment = 'unexpected_potential_match'
        assessments.append({'case_id':case['case_id'],'assessment':assessment})
    def ratio(n, d):
        return round(n/d, 4) if d else None
    times.sort()
    return {'status':'labelled_sample_only' if counts['labelled'] else 'awaiting_independent_labels',
            'counts':dict(counts), 'candidate_detection_rate':ratio(counts['true_positive'], counts['expected_positive']),
            'false_alert_rate_on_completed_negatives':ratio(counts['false_positive'], counts['true_negative']+counts['false_positive']),
            'incomplete_rate':ratio(counts['incomplete'],counts['executed']),
            'p95_seconds':times[max(0,math.ceil(len(times)*.95)-1)] if times else None,
            'case_assessments':assessments,
            'limitations':['Sample results are not a population accuracy claim.',
                'Incomplete and truncated positive cases count as not demonstrated in candidate detection rate.',
                'Operational review time requires separately recorded investigator timings.']}
