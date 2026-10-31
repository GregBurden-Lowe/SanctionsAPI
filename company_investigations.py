"""Connected company reviews reuse the existing request-ledger screening path."""
import asyncio
import json
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from services.companies_house import get_company_screening_data, connected_subjects


async def investigate(number, reference, reason, actor):
    import api_server
    import screening_db
    bundle = await asyncio.to_thread(get_company_screening_data, number)
    subjects = connected_subjects(bundle)
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(503, 'Investigation recording unavailable')
    async with pool.acquire() as conn:
        identifier = await conn.fetchval('''INSERT INTO company_screening_investigations
            (actor,company_number,business_reference,relationship_snapshot) VALUES($1,$2,$3,$4::jsonb)
            RETURNING investigation_id''', actor,number,reference,json.dumps(bundle))
    checks = []
    status = 'incomplete'
    try:
        for subject in subjects[:100]:
            data = api_server.OpCheckRequest(name=subject['name'],dob=subject.get('dob'),country=subject.get('country'),
                entity_type=subject['entity_type'],requestor=actor,business_reference=reference,reason_for_check=reason)
            result = await api_server._check_opensanctions_impl(data, actor=actor, source='company_investigation')
            body = json.loads(result.body) if isinstance(result, JSONResponse) else result
            checks.append({'subject':subject, 'result':body})
            async with pool.acquire() as conn:
                await conn.execute('UPDATE company_screening_investigations SET checks=$2::jsonb WHERE investigation_id=$1',identifier,json.dumps(checks))
        complete = len(subjects)==len(checks) and all(c['result'].get('Check Summary') for c in checks)
        status = 'screened' if complete and bundle.get('ownership',{}).get('status') == 'retrieved' else 'incomplete'
    finally:
        async with pool.acquire() as conn:
            await conn.execute('UPDATE company_screening_investigations SET status=$2 WHERE investigation_id=$1',identifier,status)
    return {'investigation_id':str(identifier), 'status':status, 'checks':checks, 'relationships':bundle,
            'unprocessed_relationships':len(subjects)-len(checks),
            'message':'Review each result and ownership limitations. This is not an overall company clearance.'}
