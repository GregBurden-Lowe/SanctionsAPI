"""Durable, paginated full-population reconciliation across all supported sources."""
import json
from datetime import datetime, timezone

from source_assurance import decode, require_current, parquet_manifest, postgres_manifest


async def tick(conn, batch_size=100, force=False):
    import screening_db
    coverage = require_current(await postgres_manifest(conn))
    async with conn.transaction():
        if not await conn.fetchval('SELECT pg_try_advisory_xact_lock(720031005)'):
            return {'status': 'busy'}
        state = await conn.fetchrow('SELECT * FROM screening_monitoring_state WHERE singleton FOR UPDATE')
        cycle = await conn.fetchrow("SELECT * FROM screening_monitoring_cycles WHERE status='running' ORDER BY started_at LIMIT 1 FOR UPDATE")
        if not cycle:
            due = not state['last_completed_at'] or (datetime.now(timezone.utc)-state['last_completed_at']).total_seconds() >= 86400
            if not force and not due and state['last_version'] == coverage['version']:
                return {'status': 'up_to_date'}
            cycle = await conn.fetchrow('INSERT INTO screening_monitoring_cycles(version) VALUES($1) RETURNING *', coverage['version'])
            await conn.execute('''INSERT INTO screening_monitoring_items(cycle_id,entity_key,inputs)
                SELECT $1,entity_key,jsonb_build_object('name',display_name,
                    'dob',COALESCE(result_json->'Screening Inputs'->>'dob',date_of_birth::text),
                    'country',country_input,'entity_type',entity_type,'business_reference',business_reference)
                FROM screened_entities''',cycle['cycle_id'])
        # Pending work stays durable; no truncated term shortlist or dropped overflow.
        items = await conn.fetch('''SELECT * FROM screening_monitoring_items i WHERE cycle_id=$1 AND job_id IS NULL
            AND NOT EXISTS(SELECT 1 FROM screening_jobs j WHERE j.entity_key=i.entity_key AND j.status IN ('pending','running'))
            ORDER BY entity_key LIMIT $2''', cycle['cycle_id'], max(1,min(batch_size,1000)))
        queued = 0
        for item in items:
            inputs = decode(item['inputs'])
            job_id = await screening_db.enqueue_job(conn, entity_key=item['entity_key'], name=inputs['name'],
                date_of_birth=inputs.get('dob'), country=inputs.get('country'), entity_type=inputs['entity_type'],
                requestor='system:source-monitoring', business_reference=inputs.get('business_reference') or 'MONITOR-'+item['entity_key'],
                reason_for_check='Periodic Re-Screen', reason='all_sources_reconciliation', force_rescreen=True)
            await conn.execute('UPDATE screening_monitoring_items SET job_id=$3::uuid WHERE cycle_id=$1 AND entity_key=$2',cycle['cycle_id'],item['entity_key'],job_id)
            queued += 1
        await conn.execute('UPDATE screening_monitoring_cycles SET queued=queued+$2 WHERE cycle_id=$1',cycle['cycle_id'],queued)
        remaining = await conn.fetchval('''SELECT count(*) FROM screening_monitoring_items i LEFT JOIN screening_jobs j USING(job_id)
            WHERE cycle_id=$1 AND (i.job_id IS NULL OR j.status IN ('pending','running') OR j.job_id IS NULL)''',cycle['cycle_id'])
        failed = await conn.fetchval("SELECT count(*) FROM screening_monitoring_items i JOIN screening_jobs j USING(job_id) WHERE cycle_id=$1 AND j.status='failed'", cycle['cycle_id'])
        if not remaining:
            await conn.execute("UPDATE screening_monitoring_cycles SET status=$2,finished_at=now(),error=$3 WHERE cycle_id=$1",cycle['cycle_id'], 'failed' if failed else 'completed', f'{failed} screening jobs failed; inspect and retry.' if failed else None)
            # Back off failed populations until the next scheduled day (or forced retry).
            await conn.execute('UPDATE screening_monitoring_state SET last_completed_at=now(),last_version=$1 WHERE singleton',cycle['version'])
        return {'status': 'running' if remaining else 'failed' if failed else 'completed', 'queued':queued, 'remaining':remaining, 'failed':failed, 'cycle_id':str(cycle['cycle_id'])}


async def summary(conn):
    from source_assurance import postgres_manifest, assess
    return {'postgres': assess(await postgres_manifest(conn)), 'parquet': assess(parquet_manifest()),
            'monitor': dict(await conn.fetchrow('SELECT * FROM screening_monitoring_state WHERE singleton') or {}),
            'refresh': dict(await conn.fetchrow('SELECT * FROM screening_source_health WHERE singleton') or {}),
            'cycles': [dict(r) for r in await conn.fetch('SELECT * FROM screening_monitoring_cycles ORDER BY started_at DESC LIMIT 10')]}


def start_background(stop_event):
    """Existing screening workers also maintain sources; database/file locks coordinate replicas."""
    import asyncio
    import logging
    import os
    import threading
    def run():
        async def loop():
            import asyncpg
            from api_server import refresh_with_lock, RefreshRequest
            from source_assurance import assess, postgres_manifest
            try:
                while not stop_event.is_set():
                    conn = None
                    try:
                        conn = await asyncpg.connect(os.environ['DATABASE_URL'], timeout=10)
                        await conn.execute('UPDATE screening_monitoring_state SET last_tick_at=now(),last_error=NULL WHERE singleton')
                        local = assess(parquet_manifest())
                        database = assess(await postgres_manifest(conn))
                        if local['status'] != 'current' or database['status'] != 'current' or local.get('version') != database.get('version') or (local.get('age_hours') or 0) >= 24:
                            if os.getenv('SCREENING_AUTO_REFRESH_ENABLED', 'true').lower() == 'true':
                                await refresh_with_lock(RefreshRequest())
                        await tick(conn)
                    except Exception:
                        logging.getLogger('screening_monitoring').exception('Monitoring needs attention')
                        if conn and not conn.is_closed():
                            try:
                                await conn.execute("UPDATE screening_monitoring_state SET last_error='Monitoring failed; inspect source coverage and service logs.' WHERE singleton")
                            except Exception:
                                pass
                    finally:
                        if conn:
                            await conn.close()
                    # Cooperative wait also permits shutdown; no event-loop blocking.
                    for _ in range(60):
                        if stop_event.is_set():
                            break
                        await asyncio.sleep(1)
            finally:
                import screening_db
                await screening_db.close_pool()
        asyncio.run(loop())
    if os.getenv('SCREENING_MONITORING_ENABLED', 'true').lower() == 'true':
        thread = threading.Thread(target=run, name='screening-monitoring', daemon=True)
        thread.start()
        return thread
