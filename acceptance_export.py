"""Read-only candidate export; historical decisions are not acceptance labels."""
import csv
import io
import json
import zipfile
from datetime import datetime, timezone


CASES_SQL = """
WITH candidates AS (
    SELECT 'decision:' || d.decision_id::text AS case_id, d.entity_key,
        r.run_id, 'saved_review_decision' AS evidence_source, r.snapshot_kind,
        r.inputs, r.result_json, r.screened_at, r.refresh_run_id, r.uk_hash,
        d.outcome, d.notes, d.actor, d.decided_at AS reviewed_at,
        CASE WHEN d.outcome LIKE 'Confirmed Match%' THEN 'confirmed_match'
             ELSE 'false_positive' END AS category
    FROM screening_review_decisions d JOIN screening_runs r USING(run_id)
    WHERE d.decision_type='analyst'
        AND (d.outcome LIKE 'Confirmed Match%' OR d.outcome LIKE 'False Positive%')
    UNION ALL
    SELECT 'current:' || e.entity_key, e.entity_key, e.current_run_id,
        'current_state', COALESCE(r.snapshot_kind, 'unversioned'),
        COALESCE(e.result_json->'Screening Inputs',
            jsonb_build_object('name', e.display_name, 'entity_type', e.entity_type,
                'dob', e.date_of_birth, 'country', e.country_input)),
        e.result_json, e.last_screened_at, e.screened_against_refresh_run_id,
        e.screened_against_uk_hash, e.review_outcome, e.review_notes,
        e.review_completed_by, e.review_completed_at,
        CASE WHEN e.review_outcome LIKE 'Confirmed Match%' THEN 'confirmed_match'
             WHEN e.review_outcome LIKE 'False Positive%' THEN 'false_positive'
             ELSE 'cleared_unreviewed' END
    FROM screened_entities e LEFT JOIN screening_runs r ON r.run_id=e.current_run_id
    WHERE NOT EXISTS (SELECT 1 FROM screening_review_decisions d WHERE d.run_id=e.current_run_id)
      AND ((e.review_status='COMPLETED' AND
            (e.review_outcome LIKE 'Confirmed Match%' OR e.review_outcome LIKE 'False Positive%'))
        OR (e.review_status IS NULL AND e.review_outcome IS NULL AND e.review_decision IS NULL
            AND e.status ILIKE 'Cleared%' AND e.result_json->'Check Summary'->>'Status' ILIKE 'Cleared%'
            AND NOT e.result_json ? 'Manual Override'))
)
SELECT *, count(*) OVER() AS available FROM candidates WHERE category=$1
ORDER BY COALESCE(reviewed_at, screened_at) DESC, case_id LIMIT $2
"""


async def collect(conn, reviewed_limit=50, cleared_limit=25):
    if not 1 <= reviewed_limit <= 250 or not 0 <= cleared_limit <= 250:
        raise ValueError('Invalid sample size')
    cases, counts = [], {}
    async with conn.transaction(isolation='repeatable_read', readonly=True):
        await conn.execute("SET LOCAL statement_timeout='15s'")
        for category in ('confirmed_match', 'false_positive', 'cleared_unreviewed'):
            limit = cleared_limit if category == 'cleared_unreviewed' else reviewed_limit
            rows = await conn.fetch(CASES_SQL, category, max(1, limit))
            counts[category] = {'available': rows[0]['available'] if rows else 0,
                                'exported': min(len(rows), limit)}
            for row in rows[:limit]:
                item = dict(row)
                item.pop('available')
                for field in ('inputs', 'result_json'):
                    if isinstance(item[field], str):
                        item[field] = json.loads(item[field])
                cases.append(item)
    return cases, counts


def csv_text(value):
    """Prevent user-controlled names/notes becoming spreadsheet formulas."""
    text = '' if value is None else str(value)
    if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
        return "'" + text
    return text


def build_archive(cases, counts):
    columns = ['case_id', 'category', 'search_name', 'entity_type', 'dob', 'country',
               'recorded_candidate', 'previous_outcome', 'previous_reason', 'previous_reviewer',
               'reviewed_at', 'screened_at', 'run_id', 'evidence_source', 'snapshot_kind',
               'expected_outcome', 'expected_watchlist_entry', 'acceptance_reason',
               'acceptance_reviewer', 'acceptance_date']
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for case in cases:
        inputs = case['inputs']
        values = {key: case.get(key) for key in columns}
        values.update(search_name=inputs.get('name'), entity_type=inputs.get('entity_type'),
                      dob=inputs.get('dob'), country=inputs.get('country'),
                      recorded_candidate=case['result_json'].get('Sanctions Name'),
                      previous_outcome=case['outcome'], previous_reason=case['notes'],
                      previous_reviewer=case['actor'])
        writer.writerow({key: csv_text(value) for key, value in values.items()})
    manifest = {'generated_at': datetime.now(timezone.utc).isoformat(), 'counts': counts,
                'selection': 'Most recent cases per category; not a random or representative sample.',
                'acceptance_labels': 'Blank: a reviewer must establish expected outcomes independently.'}
    instructions = """Acceptance candidate pack

cases.csv: complete the five blank acceptance columns after investigating each case.
evidence.json: original saved inputs/results and recorded source metadata, keyed by case_id.
manifest.json: available/exported counts and selection method.

Historical outcomes are leads, not independently verified expected answers. Cleared
unreviewed cases have no agreed outcome. Pending external reviews are excluded.
Different reviewed runs of the same entity can appear: do not treat them as independent
identities. Selection favours recent cases, not statistical representativeness.

Current-state/legacy evidence may have overwritten inputs/results or manual overrides.
Missing dates, aliases and original reviewer evidence cannot be reconstructed. Recorded
refresh IDs do not guarantee an exact archived watchlist version. Confirm the expected
watchlist entry and freeze the source version before formal acceptance testing.

The export does not run screening, change records or grant acceptance. It contains
names, DOBs and reviewer notes; handle it within your organisation's approved controls.
CSV cells that could be interpreted as formulas are prefixed with an apostrophe;
evidence.json retains the original values. Import CSV identifier and date columns as
text where necessary to avoid spreadsheet auto-conversion.
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('cases.csv', output.getvalue().encode('utf-8-sig'))
        archive.writestr('evidence.json', json.dumps(cases, default=str, ensure_ascii=False, indent=2))
        archive.writestr('manifest.json', json.dumps(manifest, indent=2))
        archive.writestr('README.txt', instructions)
    return buffer.getvalue()
