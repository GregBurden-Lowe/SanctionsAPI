"""Read-only release inventory: original requests, matching inputs and safety flags."""
import json
from datetime import datetime, timezone
from uuid import uuid4

from scripts.plan_release_rescreen import make_plan, parse_time, write_private


def record(**changes):
    run = uuid4()
    return {'request_id': uuid4(), 'run_id': run, 'current_run_id': run,
            'requested_at': datetime(2026, 11, 2, 12, tzinfo=timezone.utc),
            'source':'/opcheck', 'status':'completed', 'entity_key':'subject',
            'business_reference':'REF-1', 'active_jobs':False,
            'inputs':json.dumps({'name':'Jane Smith','dob':'1980-01-02','country':'UK',
                                 'entity_type':'Person','search_backend':'postgres_beta'}),
            'run_inputs':'{}', **changes}


def test_preserves_requests_and_groups_same_matching_inputs():
    first=record()
    second={**first, 'request_id':uuid4(), 'business_reference':'REF-2'}
    start=parse_time('2026-11-02T00:00:00+00:00')
    plan=make_plan([first,second],start,datetime(2026,11,3,tzinfo=timezone.utc))
    assert plan['request_count']==2
    assert plan['matching_input_groups']==1
    assert len(next(iter(plan['groups'].values())))==2
    assert {x['business_reference'] for x in plan['cases']}=={'REF-1','REF-2'}


def test_flags_historical_and_active_without_discarding_request():
    row=record(current_run_id=uuid4(), active_jobs=True)
    plan=make_plan([row],parse_time('2026-11-02T00:00:00+00:00'),datetime(2026,11,3,tzinfo=timezone.utc))
    assert set(plan['cases'][0]['issues'])=={'historical_run_not_current','active_job_for_entity'}


def test_punctuation_variants_are_not_silently_grouped():
    first=record(inputs=json.dumps({'name':'Mary-Jane Watson','entity_type':'Person','search_backend':'original'}))
    second=record(inputs=json.dumps({'name':'MaryJane Watson','entity_type':'Person','search_backend':'original'}))
    plan=make_plan([first,second],parse_time('2026-11-02T00:00:00+00:00'),datetime(2026,11,3,tzinfo=timezone.utc))
    assert plan['matching_input_groups']==2


def test_invalid_dob_and_unknown_backend_not_grouped():
    row=record(inputs=json.dumps({'name':'Jane Smith','dob':'not-a-date','entity_type':'Person'}))
    case=make_plan([row],parse_time('2026-11-02T00:00:00+00:00'),datetime(2026,11,3,tzinfo=timezone.utc))['cases'][0]
    assert case['group_id'] is None
    assert {'invalid_dob','backend_not_recorded'} <= set(case['issues'])


def test_private_inventory_is_not_overwritten(tmp_path):
    path=tmp_path/'inventory.json'
    write_private(path,{'test':True})
    assert path.stat().st_mode & 0o777 == 0o600
    try:
        write_private(path,{'test':False})
        assert False, 'must not overwrite the first report'
    except FileExistsError:
        pass
    assert json.loads(path.read_text()) == {'test':True}


def test_inventory_against_disposable_postgres(monkeypatch):
    import asyncio
    import os
    import pytest
    import asyncpg
    import screening_db
    from scripts.plan_release_rescreen import preview

    dsn = os.environ.get('SANCTIONS_TEST_ADMIN_DSN')
    if not dsn:
        pytest.skip('Requires isolated local PostgreSQL')

    async def exercise():
        admin = await asyncpg.connect(dsn)
        db = 'sanctions_release_plan_' + uuid4().hex
        conn = None
        try:
            await admin.execute(f'CREATE DATABASE "{db}"')
            conn = await asyncpg.connect(dsn, database=db)
            await screening_db.ensure_schema(conn)
            result={'Check Summary':{'Status':'Cleared'}, 'Risk Level':'Cleared','Confidence':'Not assessed','Score':0}
            await screening_db.upsert_screening(conn, 'subject', 'Jane Smith', 'jane smith', None, None,
                'Person', 'Tester', 'REF-1', 'Client Onboarding', result)
            run_id=await conn.fetchval("SELECT current_run_id FROM screened_entities WHERE entity_key='subject'")
            payload=json.dumps({'name':'Jane Smith','entity_type':'Person','search_backend':'postgres_beta'})
            for reference in ('REF-1','REF-2'):
                await conn.execute("""INSERT INTO screening_requests(source,inputs,business_reference,status,run_id,requested_at)
                    VALUES('/opcheck',$1::jsonb,$2,'completed',$3,'2026-11-02T12:00:00+00')""",payload,reference,run_id)
            await conn.execute("""INSERT INTO screening_requests(source,inputs,business_reference,status,run_id,requested_at)
                VALUES('/opcheck',$1::jsonb,'BEFORE','completed',$2,'2026-11-01T23:59:59+00')""",payload,run_id)
            before=await conn.fetchval('SELECT count(*) FROM screening_requests')
            test_dsn=await conn.fetchval('SELECT current_database()')
            from urllib.parse import urlparse, urlunparse
            parsed=urlparse(dsn)
            monkeypatch.setenv('DATABASE_URL',urlunparse(parsed._replace(path='/'+test_dsn)))
            plan=await preview(parse_time('2026-11-02T00:00:00+00:00'))
            assert plan['request_count']==2
            assert plan['matching_input_groups']==1
            assert await conn.fetchval('SELECT count(*) FROM screening_requests')==before
            assert await conn.fetchval('SELECT current_run_id FROM screened_entities WHERE entity_key=\'subject\'')==run_id
        finally:
            if conn:
                await conn.close()
            await admin.execute(f'DROP DATABASE "{db}"')
            await admin.close()
    asyncio.run(exercise())
