#!/usr/bin/env python3
"""Execute a frozen release inventory. No cache reuse; failures never clear.

Run from the verified release image, after reviewing the inventory and release
acceptance. Results and individual request links commit atomically per group.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from uuid import UUID

import asyncpg
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screening_db
from screening_guidance import describe_change
from screening_policy import run_postgres_check
from scripts.plan_release_rescreen import (TEST_DATA_ISSUE, database_identity, make_plan, parse_time,
                                           review_fingerprint, write_private)
from utils import _empty_no_match_result, _normalize_text, stamp_screening_result

ACTOR = 'system:release-rescreen'
SOURCE = 'release_rescreen'
BLOCKING_ISSUES = {'request_not_completed', 'legacy_snapshot_not_original_request',
                   'missing_run_or_entity_key', 'missing_or_unsupported_inputs',
                   'invalid_dob', 'backend_not_recorded', 'backend_mismatch', 'missing_business_reference'}


FLAGGED_SINCE_INVENTORY = 'A record was flagged as test data after the inventory was taken; regenerate the inventory'


class GuardFailure(RuntimeError):
    """A safe, non-sensitive operator-facing reason for not publishing."""


def key_for(request_id, rules):
    return 'release:' + rules + ':' + str(request_id)


def valid_plan(plan):
    if plan.get('version') != 1 or not isinstance(plan.get('cases'), list) or not isinstance(plan.get('database_identity'),dict):
        raise ValueError('Unsupported inventory')
    parse_time(plan['start']); parse_time(plan['cutoff'])
    ids = [c['request_id'] for c in plan['cases']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate original request IDs in inventory')
    for request_id in ids:
        UUID(request_id)
    start, cutoff = parse_time(plan['start']), parse_time(plan['cutoff'])
    if start >= cutoff:
        raise ValueError('Invalid inventory time window')
    for case in plan['cases']:
        if not all(k in case for k in ('current_run_id','run_inputs','source','request_status','review_fingerprint')):
            raise ValueError('Inventory predates execution safeguards; regenerate it')
        if not start <= parse_time(case['requested_at']) < cutoff:
            raise ValueError('Inventory request is outside the frozen window')
    return True


async def screen(conn, inputs, rules):
    args = {k:inputs.get(k) for k in ('name','dob','country','entity_type')}
    # Requests first screened on the retired Parquet route re-screen on PostgreSQL.
    if inputs['backend'] not in ('postgres_beta', 'original'):
        raise ValueError('Unknown backend')
    result = await run_postgres_check(conn, **args, requestor=ACTOR, log_search=False, require_assurance=True)
    backend = 'postgres_beta'
    stamp_screening_result(result, **args, backend=backend)
    if result.get('Matching Rules Version') != rules:
        raise RuntimeError('Executed matching version does not match the requested release version')
    # Release re-screenings require fresh review, preserving the old decision in history.
    result['Review Reuse Eligible'] = False
    return result


def identity(case):
    return (case['request_id'],case['source'],case['request_status'],case['run_id'],case['entity_key'],case['requested_at'],
            case['business_reference'],case['inputs'],case['run_inputs'],case['group_id'])


def matches_current_run(case):
    """A cached request must not change current state if its inputs differ."""
    recorded = case['run_inputs']
    submitted = case['inputs']
    for key in ('name', 'dob', 'country', 'entity_type'):
        left = recorded.get(key)
        right = submitted.get(key)
        if (str(left).strip() if left is not None else None) != (str(right).strip() if right is not None else None):
            return False
    backend = {'worker-original':'original'}.get(recorded.get('backend'), recorded.get('backend'))
    return backend == submitted['backend']


async def load_cases(conn, cases, plan):
    ids = [UUID(c['request_id']) for c in cases]
    rows = await conn.fetch('''SELECT q.request_id,q.requested_at,q.source,q.status,q.inputs,q.business_reference,
               q.run_id,r.entity_key,r.inputs AS run_inputs,e.current_run_id,
               e.status AS current_status,e.review_status,e.review_decision,e.review_claimed_by,e.review_claimed_at,
               e.review_claimed_run_id,e.review_completed_at,e.review_outcome,e.review_notes,e.manual_override_stale,
               EXISTS(SELECT 1 FROM screening_jobs j WHERE j.entity_key=r.entity_key
                      AND j.status IN ('pending','running')) AS active_jobs,
               COALESCE(screening_is_test(e), false) AS test_flagged
        FROM screening_requests q LEFT JOIN screening_runs r ON r.run_id=q.run_id
        LEFT JOIN screened_entities e ON e.entity_key=r.entity_key
        WHERE q.request_id=ANY($1::uuid[]) ORDER BY q.requested_at,q.request_id''',ids)
    current = make_plan(rows,parse_time(plan['start']),parse_time(plan['cutoff']))
    return {c['request_id']:c for c in current['cases']}


async def already_done(conn, cases, rules):
    keys=[key_for(c['request_id'],rules) for c in cases]
    found=await conn.fetch('''SELECT idempotency_key,request_id,run_id,status,inputs FROM screening_requests
        WHERE actor=$1 AND source=$2 AND idempotency_key=ANY($3::text[])''',ACTOR,SOURCE,keys)
    return {r['idempotency_key']:{'status':r['status'],'request_id':str(r['request_id']),
             'run_id':str(r['run_id']) if r['run_id'] else None,
             'inputs':json.loads(r['inputs']) if isinstance(r['inputs'], str) else r['inputs']} for r in found}


def completed(records):
    return all(r['status'] == 'completed' for r in records.values())


async def publish_group(conn, plan, cases, result, rules):
    """Publish immutable runs and links in one transaction, guarded against races."""
    async with conn.transaction():
        await conn.execute("SET LOCAL lock_timeout='3s'")
        # Prevent enqueue/claim from racing the active-job check and publication.
        await conn.execute('LOCK TABLE screening_jobs IN SHARE MODE')
        await conn.fetch('''SELECT request_id FROM screening_requests WHERE request_id=ANY($1::uuid[])
                            ORDER BY request_id FOR SHARE''',[UUID(c['request_id']) for c in cases])
        fresh=await load_cases(conn,cases,plan)
        if len(fresh)!=len(cases) or any(identity(c)!=identity(fresh.get(c['request_id'],{})) for c in cases):
            raise GuardFailure('Original request changed or is missing')
        if any(BLOCKING_ISSUES.intersection(fresh[c['request_id']]['issues']) for c in cases):
            raise GuardFailure('Original request is no longer eligible')
        if any(fresh[c['request_id']]['issues'] and 'active_job_for_entity' in fresh[c['request_id']]['issues'] for c in cases):
            raise GuardFailure('An active screening job exists')
        if any(TEST_DATA_ISSUE in fresh[c['request_id']]['issues'] for c in cases):
            raise GuardFailure(FLAGGED_SINCE_INVENTORY)
        existing=await already_done(conn,cases,rules)
        if existing:
            if len(existing)==len(cases) and completed(existing):
                return {'completed':0,'already_completed':len(cases),
                        'history_only':sum(not r['inputs'].get('current_state_updated') for r in existing.values()), 'details':[]}
            raise GuardFailure('Only part of this group was already processed; inspect before resuming')
        by_entity={}
        for c in cases:
            by_entity.setdefault(c['entity_key'],[]).append(c)
        rows={}
        # Stable lock order and a short lock timeout avoid interfering with review activity.
        for entity in sorted(by_entity):
            row=await conn.fetchrow('SELECT * FROM screened_entities WHERE entity_key=$1 FOR UPDATE',entity)
            if not row:
                raise GuardFailure('Current entity record is missing')
            rows[entity]=row
        history_only=0
        details=[]
        for entity, originals in by_entity.items():
            row=rows[entity]
            planned_current = {c.get('current_run_id') for c in originals}
            current_id = str(row['current_run_id']) if row['current_run_id'] else None
            # A rerun may update current state only if that exact run was part
            # of this group and has not changed since the inventory was taken.
            update_current = (len(planned_current)==1 and current_id in planned_current
                              and any(c['run_id'] == current_id and matches_current_run(c) for c in originals)
                              and len({c['review_fingerprint'] for c in originals}) == 1
                              and review_fingerprint(row) == originals[0]['review_fingerprint']
                              and row['review_status'] != 'IN_REVIEW'
                              and bool(row['business_reference'])
                              and row['reason_for_check'] in screening_db._REASON_FOR_CHECK_ALLOWED)
            engine=json.loads(json.dumps(result))
            old_result=row['result_json']
            if update_current:
                describe_change(engine,old_result,current_id)
                await screening_db.upsert_screening(conn, entity_key=entity, display_name=originals[0]['inputs']['name'],
                    normalized_name=_normalize_text(originals[0]['inputs']['name']), date_of_birth=originals[0]['inputs']['dob'],
                    country_input=originals[0]['inputs']['country'], entity_type=originals[0]['inputs']['entity_type'],
                    requestor=row['last_requestor'] or ACTOR, business_reference=row['business_reference'],
                    reason_for_check=row['reason_for_check'], result=engine)
                run_id=UUID(engine['run_id'])
                screened_at=engine['screened_at']
            else:
                subject_id=await conn.fetchval('SELECT subject_id FROM screening_subjects WHERE entity_key=$1',entity)
                if not subject_id:
                    raise GuardFailure('Immutable subject record is missing')
                screened_at=datetime.now(timezone.utc).isoformat()
                run_id=await conn.fetchval('''INSERT INTO screening_runs(subject_id,entity_key,screened_at,snapshot_kind,inputs,result_json)
                    VALUES($1,$2,$3::timestamptz,'screening',$4::jsonb,$5::jsonb) RETURNING run_id''',
                    subject_id,entity,datetime.fromisoformat(screened_at),json.dumps(engine['Screening Inputs']),json.dumps(engine))
                history_only += len(originals)
            for c in originals:
                original_id=c['request_id']
                payload={**c['inputs'],'business_reference':c['business_reference'],
                         'original_request_id':original_id,'original_run_id':c['run_id'],
                         'release_rescreen_rules':rules,'current_state_updated':update_current}
                request_id=await conn.fetchval('''INSERT INTO screening_requests(actor,source,inputs,business_reference,status,
                    run_id,reused,finished_at,response_status,idempotency_key,payload_hash)
                    VALUES($1,$2,$3::jsonb,$4,'completed',$5,false,clock_timestamp(),200,$6,$7) RETURNING request_id''',
                    ACTOR,SOURCE,json.dumps(payload),c['business_reference'],run_id,key_for(original_id,rules),
                    hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest())
                response={**engine,'run_id':str(run_id),'entity_key':entity,'entity_id':entity,
                          'request_id':str(request_id),'screened_at':screened_at,'reused':False}
                response['Review Business Reference']=c['business_reference']
                original_result=await conn.fetchval('SELECT result_json FROM screening_runs WHERE run_id=$1',UUID(c['run_id']))
                describe_change(response,original_result,c['run_id'])
                await conn.execute('UPDATE screening_requests SET response_json=$2::jsonb WHERE request_id=$1',
                                   request_id,json.dumps(response))
                previous_engine=json.loads(original_result) if isinstance(original_result,str) else (original_result or {})
                details.append({'request_id':original_id,'new_request_id':str(request_id),'new_run_id':str(run_id),
                                'status':'current_updated' if update_current else 'history_only',
                                'previous_status':previous_engine.get('Check Summary',{}).get('Status'),
                                'current_status_at_publication':str(row['status']),
                                'new_status':engine.get('Check Summary',{}).get('Status')})
        return {'completed':len(cases),'already_completed':0,'history_only':history_only,'details':details}


async def execute(conn, plan, rules):
    valid_plan(plan)
    # Rules versions name every rule set in force, so they run well past 60
    # characters. The limit only bounds the idempotency key and the report.
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,200}',rules):
        raise ValueError('Invalid rules version')
    probe=_empty_no_match_result('version check')
    stamp_screening_result(probe,'Version Check',None,None,'Person','postgres_beta')
    if probe['Matching Rules Version'] != rules:
        raise ValueError('Runtime matching version does not match the requested release version')
    if await conn.fetchval('SELECT current_database()') != plan.get('database'):
        raise ValueError('Inventory database does not match connected database')
    if await database_identity(conn) != plan['database_identity']:
        raise ValueError('Inventory database endpoint or identity does not match the current connection')
    # Session lock serializes release rescreen executors; other writers are
    # fenced separately during publication. Lock is released on disconnect.
    if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtext('sanctions:release-rescreen'))"):
        raise RuntimeError('Another release rescreen executor is running')
    try:
        totals=Counter(); details=[]; groups={}
        for case in plan['cases']:
            if TEST_DATA_ISSUE in case.get('issues',[]):
                # Test data is left exactly as it is; this is not a failure to resolve.
                totals['skipped_test_data']+=1
                details.append({'request_id':case['request_id'],'status':'skipped_test_data'})
                continue
            blockers=BLOCKING_ISSUES.intersection(case.get('issues',[]))
            if not case.get('group_id') or blockers:
                totals['unresolved']+=1
                details.append({'request_id':case['request_id'],'status':'unresolved',
                                'reason':', '.join(sorted(blockers)) or 'missing matching input group'})
                continue
            groups.setdefault(case['group_id'],[]).append(case)
        for cases in groups.values():
            try:
                previous=await already_done(conn,cases,rules)
                if len(previous)==len(cases) and completed(previous):
                    totals['already_completed']+=len(cases)
                    totals['history_only']+=sum(not r['inputs'].get('current_state_updated') for r in previous.values())
                    for c in cases:
                        prior=previous[key_for(c['request_id'],rules)]
                        details.append({'request_id':c['request_id'],'new_request_id':prior['request_id'],
                                        'new_run_id':prior['run_id'],
                                        'status':'already_completed_current' if prior['inputs'].get('current_state_updated') else 'already_completed_history_only'})
                    continue
                if previous:
                    raise GuardFailure('Partially processed group requires review')
                fresh=await load_cases(conn,cases,plan)
                if len(fresh)!=len(cases) or any(identity(c)!=identity(fresh.get(c['request_id'],{})) for c in cases):
                    raise GuardFailure('Original request changed or is missing')
                if any(BLOCKING_ISSUES.intersection(fresh[c['request_id']]['issues']) for c in cases):
                    raise GuardFailure('Original request is no longer eligible')
                if any('active_job_for_entity' in fresh[c['request_id']]['issues'] for c in cases):
                    raise GuardFailure('An active screening job exists')
                if any(TEST_DATA_ISSUE in fresh[c['request_id']]['issues'] for c in cases):
                    raise GuardFailure(FLAGGED_SINCE_INVENTORY)
                result=await screen(conn,cases[0]['inputs'],rules)
                published=await publish_group(conn,plan,cases,result,rules)
                totals.update({k:v for k,v in published.items() if k != 'details'})
                details.extend(published['details'])
            except Exception as exc:
                totals['unresolved']+=len(cases)
                reason = str(exc) if isinstance(exc, GuardFailure) else type(exc).__name__
                details.extend({'request_id':c['request_id'],'status':'unresolved','reason':reason}
                               for c in cases)
        return {'rules_version':rules,'counts':dict(totals),'details':details,
                'status':'needs_attention' if totals['unresolved'] or totals['history_only'] else 'complete'}
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtext('sanctions:release-rescreen'))")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory',type=Path,required=True)
    parser.add_argument('--inventory-sha256',required=True)
    parser.add_argument('--rules-version',required=True)
    parser.add_argument('--release-id',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--old-writers-drained',action='store_true',required=True,
                        help='Attest the previous release API and worker writers have fully drained')
    parser.add_argument('--execute',action='store_true',required=True)
    args=parser.parse_args()
    if not os.environ.get('RELEASE_ID') or os.environ['RELEASE_ID'] != args.release_id:
        parser.error('RELEASE_ID in the verified release runtime must match --release-id')
    content=args.inventory.read_bytes()
    if hashlib.sha256(content).hexdigest()!=args.inventory_sha256.lower():
        parser.error('Inventory SHA-256 does not match')
    if args.output.exists():
        parser.error('Output must be a new path')
    plan=json.loads(content)
    valid_plan(plan)
    async def run():
        conn=await asyncpg.connect(os.environ['DATABASE_URL'],timeout=10)
        try:
            return await execute(conn,plan,args.rules_version)
        finally:
            await conn.close()
    # Reserve a private report before any database work, so a path/permission
    # error cannot occur only after publishing results. A crash leaves an
    # explicit interrupted marker; database idempotency makes a retry safe.
    write_private(args.output,{'status':'started_or_interrupted','inventory_sha256':args.inventory_sha256.lower(),
                               'release_id':args.release_id})
    report=asyncio.run(run())
    report['inventory_sha256']=args.inventory_sha256.lower()
    report['release_id']=args.release_id
    fd, tempname=tempfile.mkstemp(prefix='.rescreen-',dir=args.output.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(report,stream,indent=2,ensure_ascii=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tempname,args.output)
    finally:
        if os.path.exists(tempname):
            os.unlink(tempname)
    print(json.dumps({'status':report['status'],'counts':report['counts'],'report':str(args.output)}))
    if report['status'] != 'complete':
        raise SystemExit(2)


if __name__=='__main__':
    main()
