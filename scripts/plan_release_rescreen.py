#!/usr/bin/env python3
"""Read-only inventory of historic screening requests for a release rescreen.

The report contains personal data. It does not enqueue or update any record.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

import asyncpg
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils import _normalize_dob, _normalize_text


TEST_DATA_ISSUE = 'flagged_as_test_data'


def parse_time(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('The start must include a timezone offset')
    return parsed.astimezone(timezone.utc)


def decode(value):
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def review_fingerprint(row):
    fields=('review_status','review_decision','review_claimed_by','review_claimed_at',
            'review_claimed_run_id','review_completed_at','review_outcome','review_notes','manual_override_stale')
    payload={key:row.get(key) for key in fields}
    payload['current_status']=row.get('current_status',row.get('status'))
    if isinstance(payload['review_decision'], str):
        payload['review_decision']=json.loads(payload['review_decision'])
    return hashlib.sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest()


async def database_identity(conn):
    row=await conn.fetchrow('''SELECT current_database() AS name, inet_server_addr()::text AS address,
        inet_server_port() AS port, (SELECT oid::text FROM pg_database WHERE datname=current_database()) AS oid''')
    return dict(row)


def make_plan(rows, start, cutoff):
    cases = []
    counts = Counter()
    groups = {}
    for row in rows:
        inputs = decode(row['inputs'])
        run_inputs = decode(row['run_inputs'])
        name = inputs.get('name') or run_inputs.get('name')
        dob = inputs.get('dob') if 'dob' in inputs else run_inputs.get('dob')
        country = inputs.get('country') if 'country' in inputs else run_inputs.get('country')
        kind = inputs.get('entity_type') or run_inputs.get('entity_type')
        backend = inputs.get('search_backend') or run_inputs.get('backend')
        backend = {'worker-original': 'original'}.get(backend, backend)
        run_backend = {'worker-original': 'original'}.get(run_inputs.get('backend'), run_inputs.get('backend'))
        issues = []
        if row['status'] != 'completed' or not row['run_id']:
            issues.append('request_not_completed')
        if row['source'] == 'legacy_snapshot':
            issues.append('legacy_snapshot_not_original_request')
        if not row['entity_key']:
            issues.append('missing_run_or_entity_key')
        elif row['current_run_id'] != row['run_id']:
            issues.append('historical_run_not_current')
        if not name or not _normalize_text(name) or kind not in ('Person', 'Organization'):
            issues.append('missing_or_unsupported_inputs')
        if dob and not _normalize_dob(dob):
            issues.append('invalid_dob')
        if backend not in ('original', 'postgres_beta'):
            issues.append('backend_not_recorded')
        if run_backend and backend != run_backend:
            issues.append('backend_mismatch')
        if not (row['business_reference'] or '').strip():
            issues.append('missing_business_reference')
        if row['active_jobs']:
            issues.append('active_job_for_entity')
        if row.get('test_flagged'):
            # Marked as test data: the executor leaves it out, as monitoring does.
            issues.append(TEST_DATA_ISSUE)
        counts.update(issues or ['eligible'])
        group_id = None
        if (name and _normalize_text(name) and (not dob or _normalize_dob(dob))
                and kind in ('Person', 'Organization') and backend in ('original', 'postgres_beta')):
            # Group only identical supplied values. The screening fingerprint
            # normalizes punctuation differently from person token matching,
            # so using it here could conflate distinct searches.
            values = {'name':name.strip(), 'dob':str(dob).strip() if dob else None,
                      'country':str(country).strip() if country else None,
                      'entity_type':kind, 'backend':backend}
            group_id = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            groups.setdefault(group_id, []).append(str(row['request_id']))
        cases.append({'request_id':str(row['request_id']), 'source':row['source'], 'request_status':row['status'],
                      'run_id':str(row['run_id']) if row['run_id'] else None,
                      'current_run_id':str(row['current_run_id']) if row['current_run_id'] else None,
                      'review_fingerprint':review_fingerprint(row),
                      'entity_key':row['entity_key'], 'requested_at':row['requested_at'].isoformat(),
                      'business_reference':row['business_reference'], 'run_inputs':run_inputs, 'inputs':{'name':name,'dob':dob,'country':country,
                      'entity_type':kind,'backend':backend}, 'group_id':group_id, 'issues':issues})
    return {'version':1,'start':start.isoformat(),'cutoff':cutoff.isoformat(), 'cases':cases,
            'counts':dict(counts), 'request_count':len(cases), 'matching_input_groups':len(groups),
            'groups':groups,
            'notes':['Read-only inventory, not a screening or an execution plan.',
                     'Issue counts overlap. Current-run eligibility does not establish safe execution.',
                     'Each original request is retained; grouping does not authorize current-state updates.']}


async def preview(start):
    dsn = os.environ.get('DATABASE_URL', '').strip()
    if not dsn:
        raise ValueError('DATABASE_URL is required')
    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            cutoff = await conn.fetchval('SELECT transaction_timestamp()')
            if start >= cutoff:
                raise ValueError('Start must be before the snapshot cutoff')
            rows = await conn.fetch('''SELECT q.request_id,q.requested_at,q.source,q.status,q.inputs,q.business_reference,
                       q.run_id,r.entity_key,r.inputs AS run_inputs,e.current_run_id,
                       e.status AS current_status,e.review_status,e.review_decision,e.review_claimed_by,e.review_claimed_at,
                       e.review_claimed_run_id,e.review_completed_at,e.review_outcome,e.review_notes,e.manual_override_stale,
                       EXISTS(SELECT 1 FROM screening_jobs j WHERE j.entity_key=r.entity_key
                              AND j.status IN ('pending','running')) AS active_jobs,
                       COALESCE(screening_is_test(e), false) AS test_flagged
                FROM screening_requests q LEFT JOIN screening_runs r ON r.run_id=q.run_id
                LEFT JOIN screened_entities e ON e.entity_key=r.entity_key
                WHERE q.requested_at >= $1 AND q.requested_at < $2 AND q.source <> 'release_rescreen'
                ORDER BY q.requested_at,q.request_id''',start,cutoff)
            result = make_plan(rows,start,cutoff)
            result['database'] = await conn.fetchval('SELECT current_database()')
            result['database_identity'] = await database_identity(conn)
            return result
    finally:
        await conn.close()


def write_private(path, payload):
    # Fail if the file already exists; never replace another inventory.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True, help='Inclusive start, with timezone, e.g. 2026-11-02T00:00:00+00:00')
    parser.add_argument('--output', type=Path, required=True, help='New private report path')
    args = parser.parse_args()
    report = asyncio.run(preview(parse_time(args.start)))
    write_private(args.output, report)
    print(json.dumps({'report':str(args.output),'start':report['start'],'cutoff':report['cutoff'],
                      'request_count':report['request_count'],'matching_input_groups':report['matching_input_groups'],
                      'counts':report['counts']}))


if __name__ == '__main__':
    main()
