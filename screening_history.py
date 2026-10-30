"""Request ledger. Execution snapshots are captured atomically by database triggers."""
import json
import hashlib
import os
from contextvars import ContextVar
from uuid import UUID

active_request = ContextVar('screening_request', default=None)


class RequestConflict(ValueError):
    pass


class RequestExpired(RuntimeError):
    pass


async def recover_expired(conn):
    return await conn.execute("""WITH expired AS (
        SELECT request_id FROM screening_requests WHERE status='requested' AND job_id IS NULL
        AND expires_at<=clock_timestamp() LIMIT 100 FOR UPDATE SKIP LOCKED
    ) UPDATE screening_requests q SET status='failed',response_status=503,finished_at=clock_timestamp(),
        response_json=jsonb_build_object('error','request_interrupted','request_id',q.request_id,
            'message','This screening did not finish within its time limit. No clearance was issued. Start a linked retry.')
        FROM expired e WHERE q.request_id=e.request_id""")


async def begin_submission(conn, inputs, actor, source, key=None, retry_of=None):
    if key is not None and (not 1<=len(key)<=128 or not key.isascii() or any(ord(c)<33 or ord(c)>126 for c in key)):
        raise RequestConflict('Idempotency-Key must contain 1–128 visible ASCII characters.')
    digest=hashlib.sha256(json.dumps(inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    timeout=max(30,int(os.environ.get('SCREENING_REQUEST_TIMEOUT_SECONDS','600')))
    async with conn.transaction():
        await recover_expired(conn)
        if retry_of:
            parent=await conn.fetchrow("SELECT actor,status FROM screening_requests WHERE request_id=$1::uuid",UUID(retry_of))
            if parent is None or parent['actor'] != actor or parent['status']!='failed':
                raise RequestConflict('A retry must refer to your own failed request.')
        row=await conn.fetchrow("""INSERT INTO screening_requests(inputs,actor,source,business_reference,
            idempotency_key,payload_hash,expires_at,retry_of)
            VALUES($1::jsonb,$2,$3,$4,$5,$6,clock_timestamp()+($7 * interval '1 second'),$8::uuid)
            ON CONFLICT DO NOTHING RETURNING *""",json.dumps(inputs),actor,source,inputs.get('business_reference'),
            key,digest,timeout,UUID(retry_of) if retry_of else None)
        if row:
            return str(row['request_id']),None
        existing=await conn.fetchrow("""SELECT * FROM screening_requests WHERE COALESCE(actor,'')=COALESCE($1,'')
            AND source=$2 AND idempotency_key=$3""",actor,source,key)
        if existing['payload_hash']!=digest:
            raise RequestConflict('This idempotency key was used with different inputs. Use a new key for a new submission.')
        return str(existing['request_id']),decode_row(existing)


async def lock_active(conn, request_id=None):
    request_id=request_id or active_request.get()
    if request_id is None:
        return
    row=await conn.fetchrow("""SELECT status FROM screening_requests WHERE request_id=$1::uuid
        AND status='requested' AND (expires_at IS NULL OR expires_at>clock_timestamp()) FOR UPDATE""",UUID(request_id))
    if row is None:
        raise RequestExpired('Request is no longer active; result discarded.')


async def start(conn, inputs, actor, source):
    return str(await conn.fetchval(
        """INSERT INTO screening_requests(inputs, actor, source, business_reference)
           VALUES ($1::jsonb, $2, $3, $4) RETURNING request_id""",
        json.dumps(inputs), actor, source, inputs.get('business_reference'),
    ))


async def finish(conn, request_id, result, failed=False, response_status=None):
    async with conn.transaction():
        row=await conn.fetchrow('SELECT * FROM screening_requests WHERE request_id=$1::uuid FOR UPDATE',UUID(request_id))
        if row['status'] in ('completed','failed'):
            return decode_row(row)
        await lock_active(conn,request_id)
        await conn.execute("""UPDATE screening_requests SET status=$2,response_json=$3::jsonb,
            run_id=$4::uuid,reused=$5,finished_at=clock_timestamp(),response_status=$6
            WHERE request_id=$1::uuid""",request_id,'failed' if failed else 'completed',json.dumps(result),
            result.get('run_id'),bool(result.get('reused')),response_status or (503 if failed else 200))
        return None


async def attach_job(conn, job_id, inputs):
    request_id = active_request.get()
    if request_id is None:
        request_id = await start(conn, inputs, None, 'background_job')
    await lock_active(conn, request_id)
    await conn.execute(
        "UPDATE screening_requests SET job_id=$2::uuid, status='queued' WHERE request_id=$1::uuid",
        request_id, job_id,
    )
    return request_id


async def list_requests(conn, reference=None, limit=50, offset=0):
    rows = await conn.fetch(
        """SELECT request_id, requested_at, actor, source, inputs, business_reference,
           status, reused, run_id, finished_at FROM screening_requests
           WHERE ($1::text IS NULL OR business_reference=$1)
           ORDER BY requested_at DESC, request_id DESC LIMIT $2 OFFSET $3""",
        reference or None, limit + 1, offset,
    )
    return {'items': [decode_row(r) for r in rows[:limit]], 'has_more': len(rows) > limit}


async def get_request(conn, request_id):
    await recover_expired(conn)
    row = await conn.fetchrow(
        """SELECT q.*, r.screened_at, r.snapshot_kind, r.refresh_run_id, r.uk_hash,
           r.subject_id, r.entity_key FROM screening_requests q
           LEFT JOIN screening_runs r USING(run_id) WHERE request_id=$1::uuid""", UUID(request_id),
    )
    if not row:
        return None
    item = decode_row(row)
    decisions = await conn.fetch("SELECT * FROM screening_review_decisions WHERE run_id=$1 ORDER BY decided_at, decision_id", row['run_id'])
    item['review_decisions'] = [dict(decision) for decision in decisions]
    return item


def decode_row(row):
    result = dict(row)
    for key in ('inputs', 'response_json'):
        if isinstance(result.get(key), str):
            result[key] = json.loads(result[key])
    return result
