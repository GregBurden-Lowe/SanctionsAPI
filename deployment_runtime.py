"""Explicit schema contract for compatible rolling releases (no startup DDL)."""
import hashlib
from pathlib import Path

PROTOCOL = 'history-leases-v1'


def schema_contract():
    digest = hashlib.sha256(PROTOCOL.encode())
    for path in sorted(Path(__file__).with_name('migrations').glob('*.sql')):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


async def verify_schema(conn):
    installed = await conn.fetchval("SELECT contract FROM deployment_schema WHERE singleton=true")
    if installed != schema_contract():
        raise RuntimeError('Schema contract does not match this release; coordinated migration required')
    # Verify the main application/auth relations as well as the recorded contract.
    await conn.fetch("""SELECT q.request_id, q.expires_at, q.retry_of, r.run_id,
        e.review_decision, j.lease_token, j.attempt_count, d.decision_id, u.id
        FROM screening_requests q, screening_runs r, screened_entities e,
        screening_jobs j, screening_review_decisions d, users u LIMIT 0""")


async def install_schema(conn):
    """Only invoked explicitly during a coordinated baseline/migration window."""
    import screening_db
    import auth_db
    async with conn.transaction():
        await conn.execute("SET LOCAL lock_timeout='5s'")
        await conn.execute("SET LOCAL statement_timeout='15min'")
        if not await conn.fetchval('SELECT pg_try_advisory_xact_lock(720031004)'):
            raise RuntimeError('Another schema installer is running')
        await screening_db.ensure_schema(conn)
        await auth_db.ensure_auth_schema(conn)
        await conn.execute('''CREATE TABLE IF NOT EXISTS deployment_schema (
            singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), contract text NOT NULL)''')
        await conn.execute('''INSERT INTO deployment_schema(singleton, contract) VALUES(true,$1)
            ON CONFLICT(singleton) DO UPDATE SET contract=EXCLUDED.contract''', schema_contract())


async def main():
    import os
    import asyncpg
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    try:
        await install_schema(conn)
    finally:
        await conn.close()


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
