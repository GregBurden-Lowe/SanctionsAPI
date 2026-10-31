"""Real PostgreSQL recovery, publication fencing and worker restart tests."""
import asyncio
import json
import os
import unittest
from uuid import uuid4
from unittest.mock import patch

import asyncpg
import psycopg2
from psycopg2.extensions import make_dsn
import screening_db
import screening_worker
from job_recovery import claim_lease, renew_lease, recover_expired, lock_owned_job, LostJobLease, Heartbeat
from utils import _empty_no_match_result


@unittest.skipUnless(os.getenv('SANCTIONS_TEST_ADMIN_DSN'), 'Requires disposable PostgreSQL database')
class JobRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from datetime import datetime, timezone
        import pandas as pd
        self.source_manifest = {'version':'test-source', 'ingested_at':datetime.now(timezone.utc).isoformat(),
            'list_counts':{k:1 for k in ('UK','UN','EU','OFAC','PEP')}}
        source_frame = pd.DataFrame()
        source_frame.attrs['source_manifest'] = self.source_manifest
        for mocked in (patch('utils.get_opensanctions_df', return_value=source_frame),
                       patch('source_assurance.parquet_manifest', return_value=self.source_manifest),
                       patch.dict(os.environ, {'SCREENING_MONITORING_ENABLED':'false'})):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.admin = await asyncpg.connect(os.environ['SANCTIONS_TEST_ADMIN_DSN'])
        self.database = 'sanctions_recovery_' + uuid4().hex
        await self.admin.execute(f'CREATE DATABASE "{self.database}"')
        self.conn = await asyncpg.connect(os.environ['SANCTIONS_TEST_ADMIN_DSN'], database=self.database)
        await screening_db.ensure_schema(self.conn)
        self.dsn = make_dsn(os.environ['SANCTIONS_TEST_ADMIN_DSN'], dbname=self.database)
        self.worker = psycopg2.connect(self.dsn)
        self.job = await screening_db.enqueue_job(self.conn, 'recovery-key', 'Fixture Ltd', None, 'UK', 'Organization', 'fixture', 'RECOVERY', 'Client Onboarding')
        self.token = str(uuid4())

    async def asyncTearDown(self):
        self.worker.close()
        await self.conn.close()
        await self.admin.execute(f'DROP DATABASE "{self.database}"')
        await self.admin.close()

    def claim(self, token=None):
        claim_lease(self.worker,self.job,token or self.token,60)
        self.worker.commit()

    async def test_graceful_worker_stop_finishes_current_job_without_claiming_next(self):
        import threading
        stopping = threading.Event()
        other = await screening_db.enqueue_job(self.conn, 'next-job', 'Next Ltd', None, 'UK', 'Organization', 'fixture', 'NEXT', 'Client Onboarding')
        def match(**kwargs):
            stopping.set()
            return _empty_no_match_result('fixture')
        with patch.dict(os.environ, {'DATABASE_URL':self.dsn}), patch('utils.perform_opensanctions_check',side_effect=match), \
             patch('screening_worker.time.sleep'):
            screening_worker.main(stopping)
        self.assertEqual(await self.conn.fetchval('SELECT status FROM screening_jobs WHERE job_id=$1::uuid',self.job),'completed')
        self.assertEqual(await self.conn.fetchval('SELECT status FROM screening_jobs WHERE job_id=$1::uuid',other),'pending')

    async def expire(self):
        await self.conn.execute("UPDATE screening_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=$1::uuid",self.job)

    async def test_active_lease_is_not_recovered_and_heartbeat_renews(self):
        self.claim()
        self.assertEqual(recover_expired(self.worker),0)
        self.worker.commit()
        before = await self.conn.fetchval('SELECT lease_expires_at FROM screening_jobs')
        heartbeat = Heartbeat(self.dsn,self.job,self.token)
        heartbeat.interval = .02
        heartbeat.start()
        try:
            for _ in range(50):
                await asyncio.sleep(.02)
                if await self.conn.fetchval('SELECT lease_expires_at FROM screening_jobs') > before:
                    break
            self.assertGreater(await self.conn.fetchval('SELECT lease_expires_at FROM screening_jobs'), before)
        finally:
            heartbeat.stop()
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_job_events'),1)

    async def test_expiry_requeues_and_fences_old_worker(self):
        self.claim()
        await self.expire()
        self.assertFalse(renew_lease(self.worker,self.job,self.token,60,600))
        self.assertEqual(recover_expired(self.worker),1)
        self.worker.commit()
        next_token=str(uuid4())
        self.claim(next_token)
        with self.assertRaises(LostJobLease):
            lock_owned_job(self.worker,self.job,self.token)
        self.worker.rollback()
        self.assertFalse(renew_lease(self.worker,self.job,self.token,60,600))
        self.worker.commit()
        self.assertEqual(await self.conn.fetchval('SELECT attempt_count FROM screening_jobs'),2)
        self.assertEqual(await self.conn.fetchval('SELECT status FROM screening_requests'),'queued')

    async def test_retry_limit_fails_all_attached_requests(self):
        # Two independent submissions waiting on this same job.
        await self.conn.execute("INSERT INTO screening_requests(source,inputs,status,job_id) VALUES ('fixture','{}','queued',$1::uuid)",self.job)
        for attempt in range(3):
            self.claim(str(uuid4()))
            await self.expire()
            self.assertEqual(recover_expired(self.worker,3),1)
            self.worker.commit()
        self.assertEqual(await self.conn.fetchval('SELECT status FROM screening_jobs'),'failed')
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM screening_requests WHERE status='failed'"),2)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_runs'),0)
        with self.assertRaises(asyncpg.RaiseError):
            await self.conn.execute("DELETE FROM screening_job_events")

    async def test_publisher_lock_blocks_recovery_until_commit(self):
        self.claim()
        lock_owned_job(self.worker,self.job,self.token)
        # Expire inside the publishing transaction; another recovery connection must skip it.
        with self.worker.cursor() as cur:
            cur.execute("UPDATE screening_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(self.job,))
        other=psycopg2.connect(self.dsn)
        try:
            self.assertEqual(recover_expired(other),0)
            other.commit()
        finally:
            other.close()
        self.worker.rollback()

    async def test_attempt_runtime_bounds_heartbeat(self):
        self.claim()
        await self.conn.execute("UPDATE screening_jobs SET started_at=clock_timestamp()-interval '601 seconds'")
        self.assertFalse(renew_lease(self.worker,self.job,self.token,60,600))
        self.worker.commit()

    async def test_interrupted_job_completes_after_restart(self):
        with patch.dict(os.environ,{'DATABASE_URL':self.dsn}), patch('utils.perform_opensanctions_check',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                screening_worker.main()
        self.assertEqual(await self.conn.fetchval('SELECT status FROM screening_jobs'),'running')
        await self.expire()
        with patch.dict(os.environ,{'DATABASE_URL':self.dsn}), patch('utils.perform_opensanctions_check',return_value=_empty_no_match_result('fixture')), patch('screening_worker.time.sleep',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                screening_worker.main()
        result=await screening_db.get_job_status(self.conn,self.job)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(await self.conn.fetchval('SELECT attempt_count FROM screening_jobs'),2)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_runs'),1)
        self.assertEqual(await self.conn.fetchval('SELECT status FROM screening_requests'),'completed')
        await screening_db.ensure_schema(self.conn)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_job_events'),4)

    async def test_worker_discards_result_after_takeover(self):
        def takeover(**kwargs):
            with self.worker.cursor() as cur:
                cur.execute("UPDATE screening_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE job_id=%s",(self.job,))
            recover_expired(self.worker)
            claim_lease(self.worker,self.job,str(uuid4()),60)
            self.worker.commit()
            return _empty_no_match_result('fixture')
        # First type check takes over the job; second returns normally.
        calls=0
        def matcher(**kwargs):
            nonlocal calls
            calls+=1
            return takeover(**kwargs) if calls==1 else _empty_no_match_result('fixture')
        with patch.dict(os.environ,{'DATABASE_URL':self.dsn}), patch('utils.perform_opensanctions_check',side_effect=matcher), patch('screening_worker.time.sleep',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                screening_worker.main()
        self.assertEqual(await self.conn.fetchval('SELECT status FROM screening_jobs'),'running')
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screening_runs'),0)
        self.assertEqual(await self.conn.fetchval('SELECT count(*) FROM screened_entities'),0)

    async def test_legacy_running_job_gets_one_grace_period(self):
        await self.conn.execute("UPDATE screening_jobs SET status='running',lease_token=NULL,lease_expires_at=NULL WHERE job_id=$1::uuid",self.job)
        await screening_db.ensure_schema(self.conn)
        expiry=await self.conn.fetchval('SELECT lease_expires_at FROM screening_jobs')
        self.assertIsNotNone(expiry)
        await screening_db.ensure_schema(self.conn)
        self.assertEqual(await self.conn.fetchval('SELECT lease_expires_at FROM screening_jobs'),expiry)
        await self.expire()
        self.assertEqual(recover_expired(self.worker),1)
        self.worker.commit()
        self.assertEqual(await self.conn.fetchval('SELECT status FROM screening_jobs'),'pending')
