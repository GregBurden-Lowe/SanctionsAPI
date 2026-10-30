"""Analyst disposition is separate from the engine's immutable finding."""
import json


class ReviewConflict(ValueError):
    pass


def decode(value):
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def project_result(engine, decision=None, *, stale=False):
    """Compatibility response: effective outcome plus explicit original engine evidence."""
    result = decode(engine)
    decision = decode(decision)
    if not decision:
        return result
    result['Engine Result'] = decode(engine)
    result['Review Decision'] = {**decision, 'stale': bool(stale)}
    if not stale and decision.get('outcome', '').startswith('False Positive'):
        result.update({'Is Sanctioned': False, 'Is PEP': False, 'Match Found': False,
                       'Risk Level': 'Cleared', 'Decision Explanation': 'An analyst resolved this run as a false positive. Original engine evidence is retained.'})
        # Keep original match name, similarity, sources and screening timestamp.
        result['Check Summary'] = {**result.get('Check Summary', {}), 'Status': 'Cleared - False Positive'}
    return result


async def write_decision(conn, row, actor, outcome, notes, decision_type='analyst'):
    record = await conn.fetchrow('''INSERT INTO screening_review_decisions
        (run_id, entity_key, actor, outcome, notes, decision_type)
        VALUES ($1, $2, $3, $4, $5, $6) RETURNING *''',
        row['current_run_id'], row['entity_key'], actor, outcome, notes, decision_type)
    decision = {key: str(value) if key in ('decision_id', 'run_id', 'decided_at') else value for key, value in dict(record).items()}
    false_positive = outcome.startswith('False Positive')
    await conn.execute('''UPDATE screened_entities SET review_decision=$2::jsonb,
        review_status='COMPLETED', review_outcome=$3, review_notes=$4,
        review_completed_by=$5, review_completed_at=now(), updated_at=now(),
        status=CASE WHEN $6 THEN 'Cleared - False Positive' ELSE status END,
        risk_level=CASE WHEN $6 THEN 'Cleared' ELSE risk_level END,
        uk_sanctions_flag=CASE WHEN $6 THEN false ELSE uk_sanctions_flag END,
        pep_flag=CASE WHEN $6 THEN false ELSE pep_flag END,
        manual_override_uk_hash=CASE WHEN $6 THEN screened_against_uk_hash ELSE manual_override_uk_hash END,
        manual_override_stale=false
        WHERE entity_key=$1''', row['entity_key'], json.dumps(decision), outcome, notes, actor, false_positive)
    result = project_result(row['result_json'], decision)
    result.update(run_id=str(row['current_run_id']), screened_at=row['last_screened_at'].isoformat(), entity_key=row['entity_key'])
    return result
