"""Bounded retry, heartbeat and fenced publication for synchronous workers."""
import logging
import threading

logger = logging.getLogger(__name__)


class LostJobLease(RuntimeError):
    pass


def recover_expired(conn, max_attempts=3):
    """Skip rows held by publishers; terminal failure updates all attached requests."""
    with conn.cursor() as cur:
        cur.execute('''WITH expired AS (
            SELECT job_id FROM screening_jobs WHERE status='running'
              AND lease_expires_at <= clock_timestamp()
            ORDER BY lease_expires_at LIMIT 100 FOR UPDATE SKIP LOCKED
        ) UPDATE screening_jobs j SET
            status=CASE WHEN attempt_count >= %s THEN 'failed' ELSE 'pending' END,
            finished_at=CASE WHEN attempt_count >= %s THEN clock_timestamp() ELSE NULL END,
            error_message=CASE WHEN attempt_count >= %s THEN 'Worker recovery retry limit reached. No clearance issued.'
                ELSE 'Worker lease expired; queued for retry.' END,
            lease_token=NULL, lease_expires_at=NULL
        FROM expired e WHERE j.job_id=e.job_id''', (max_attempts, max_attempts, max_attempts))
        return cur.rowcount


def claim_lease(conn, job_id, token, lease_seconds):
    with conn.cursor() as cur:
        cur.execute('''UPDATE screening_jobs SET status='running', started_at=clock_timestamp(),
            finished_at=NULL, error_message=NULL, attempt_count=attempt_count+1,
            lease_token=%s, lease_expires_at=clock_timestamp()+(%s * interval '1 second')
            WHERE job_id=%s AND status='pending' ''', (token, lease_seconds, job_id))
        if cur.rowcount != 1:
            raise LostJobLease('Job is no longer available to claim')


def renew_lease(conn, job_id, token, lease_seconds, max_runtime):
    with conn.cursor() as cur:
        cur.execute('''UPDATE screening_jobs SET lease_expires_at=LEAST(
            clock_timestamp()+(%s * interval '1 second'), started_at+(%s * interval '1 second'))
            WHERE job_id=%s AND lease_token=%s AND status='running'
              AND lease_expires_at>clock_timestamp()
              AND started_at+(%s * interval '1 second')>clock_timestamp()''',
            (lease_seconds,max_runtime,job_id,token,max_runtime))
        return cur.rowcount == 1


def lock_owned_job(conn, job_id, token):
    """Must be held in the same transaction as entity/run/job publication."""
    with conn.cursor() as cur:
        cur.execute('''SELECT job_id FROM screening_jobs WHERE job_id=%s
            AND lease_token=%s AND status='running' AND lease_expires_at>clock_timestamp()
            FOR UPDATE''', (job_id,token))
        if cur.fetchone() is None:
            raise LostJobLease('Worker no longer owns this job; result discarded')


class Heartbeat:
    def __init__(self, dsn, job_id, token, lease_seconds=60, max_runtime=600):
        self.args = (job_id,token,lease_seconds,max_runtime)
        self.dsn = dsn
        self.interval = max(1, lease_seconds / 3)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=5)

    def _run(self):
        import psycopg2
        while not self.stop_event.wait(self.interval):
            conn = None
            try:
                conn = psycopg2.connect(self.dsn, connect_timeout=3, options='-c statement_timeout=3000')
                with conn:
                    if not renew_lease(conn, *self.args):
                        return
            except Exception:
                logger.warning('Job heartbeat failed; publication remains lease-fenced', exc_info=True)
                return
            finally:
                if conn is not None:
                    conn.close()
