"""Additive analyst guidance. Legacy finding fields remain unchanged."""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def enrich_result(result):
    candidates = result.get('Candidate Evidence', [])
    potential = [item for item in candidates if item.get('disposition') == 'potential_match']
    actions = []
    inputs = result.get('Screening Inputs', {})
    if potential:
        actions.append('Review each potential candidate and record identity evidence and a reason for the decision.')
        if inputs.get('entity_type', '').lower() == 'person' and not inputs.get('dob'):
            actions.append('Obtain a date of birth; a name match alone does not establish identity.')
        elif any(item.get('dob_assessment') in ('compatible', 'unknown') for item in potential if item.get('entity_type') == 'Person'):
            actions.append('Resolve missing or partial date-of-birth evidence where available.')
        if inputs.get('entity_type', '').lower() == 'organization':
            actions.append('Confirm the company registration number and review relevant directors and ownership relationships.')
        if result.get('Is Sanctioned'):
            actions.append('Escalate unresolved sanctions candidates to Compliance before making a clearance decision.')
        if result.get('Is PEP'):
            actions.append('Verify the public role and relevant relationships; a PEP match is not a sanctions finding.')
    else:
        actions.append('No potential match under these rules. Apply your normal business controls and retain this screening record.')
    if not result.get('PEP Check', {}).get('checked', True):
        actions.append('PEP screening was not performed for this submitted entity; screen connected individuals separately.')
    result['Screening Guidance'] = {
        'finding': 'potential_sanctions_match' if result.get('Is Sanctioned') else 'potential_pep_match' if result.get('Is PEP') else 'no_potential_match',
        'identity_confirmed': False, 'next_actions': actions,
        'limitations': ['Name similarity is not an identity probability.', 'Missing information is not conflicting evidence.'],
    }
    # Business reference is supplied at persistence, not inferred from a name.
    from utils import _normalize_dob, _normalize_country, _normalize_text
    identity = {'name': _normalize_text(inputs.get('name', '')),
                'dob': _normalize_dob(inputs.get('dob')), 'country': _normalize_country(inputs.get('country')),
                'entity_type': inputs.get('entity_type', '').strip().lower()}
    result['Review Subject Signature'] = digest({'identity': identity,
        'rules': result.get('Matching Rules Version'), 'config': result.get('Matching Configuration')})
    for item in candidates:
        item['candidate_signature'] = digest({k: v for k, v in item.items() if k != 'candidate_signature'})
    result['Review Reuse Eligible'] = bool(reusable_candidates(result))
    return result


def reusable_candidates(result):
    """No name-only suppressions and no decisions over hidden/truncated candidates."""
    inputs = result.get('Screening Inputs', {})
    identity_known = bool(inputs.get('dob')) if inputs.get('entity_type', '').lower() == 'person' else bool(inputs.get('country'))
    summary = result.get('Candidate Evidence Summary', {})
    candidates = [c for c in result.get('Candidate Evidence', []) if c.get('disposition') == 'potential_match']
    if (not identity_known or not result.get('Review Subject Signature') or summary.get('display_truncated')
        or len(candidates) != summary.get('potential_matches') or not candidates
        or any(not c.get('record_id') or not c.get('candidate_signature') or not c.get('source_evidence_hash') for c in candidates)):
        return []
    return candidates


def describe_change(result, previous, previous_run_id=None):
    """Explain candidate changes without overwriting the earlier run or review."""
    if isinstance(previous, str):
        previous = json.loads(previous)
    def index(value):
        return {str(c.get('record_id') or c.get('name'))+'|'+str(c.get('source_type')): c
                for c in (value or {}).get('Candidate Evidence', []) if c.get('disposition') == 'potential_match'}
    old, new = index(previous), index(result)
    added = sorted(new.keys()-old.keys())
    removed = sorted(old.keys()-new.keys())
    changed = sorted(k for k in old.keys() & new.keys() if old[k].get('source_evidence_hash') != new[k].get('source_evidence_hash'))
    result['Monitoring Change'] = {'previous_run_id':str(previous_run_id) if previous_run_id else None,
        'added_candidates':added, 'removed_candidates':removed, 'changed_candidates':changed,
        'message':'Candidate evidence changed; review the current finding.' if added or removed or changed else 'No change in the displayed candidate evidence.',
        'evidence_truncated':bool(result.get('Candidate Evidence Summary',{}).get('display_truncated') or (previous or {}).get('Candidate Evidence Summary',{}).get('display_truncated'))}
    return result
