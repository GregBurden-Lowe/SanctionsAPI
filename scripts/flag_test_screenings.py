#!/usr/bin/env python3
"""Flag the eligible screenings from a find_test_screenings.py report as test data.

Without --execute this is a dry run: every check runs against the database and
is rolled back, so the report shows exactly what would be flagged and nothing
changes. With --execute each screening is flagged in its own transaction using
the same rules and audit events as the admin screen.

Flagging hides a record from the review queue, AI triage and monitoring
re-screens. It deletes nothing and is not a clearance; an administrator can
restore a record from Match Review. Only entries the finder marked `eligible`
are touched. Anything marked `needs_review` must be decided individually.

A screening is skipped if it has changed since the report was made: a new run,
a new recorded request, a queued or running check, or an existing flag.
"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from uuid import UUID

import asyncpg
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import screening_db
from scripts.find_test_screenings import INVENTORY_KIND
from scripts.plan_release_rescreen import database_identity, parse_time, write_private

ACTOR_PREFIX = 'bulk-test-flag:'


def valid_inventory(inventory):
    if inventory.get('version') != 1 or inventory.get('kind') != INVENTORY_KIND \
            or not isinstance(inventory.get('entities'), list) or not isinstance(inventory.get('database_identity'), dict):
        raise ValueError('Unsupported report; create it with find_test_screenings.py')
    parse_time(inventory['cutoff'])
    keys = [entry['entity_key'] for entry in inventory['entities']]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate screenings in the report')
    for entry in inventory['entities']:
        if entry['status'] == 'eligible':
            UUID(entry['current_run_id'])
    return True


class _DryRun(Exception):
    """Raised to roll back a transaction whose checks all passed."""


async def flag_one(conn, entry, cutoff, reason, actor, execute):
    """Flag one screening, or prove it could be flagged and roll back. Returns (status, reason)."""
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout='3s'")
            if await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM screening_test_flags
                    WHERE entity_key=$1 AND active)''', entry['entity_key']):
                return 'skipped', 'already_flagged'
            # A request recorded after the report may be a real search that reused this result.
            if await conn.fetchval('''SELECT EXISTS(SELECT 1 FROM screening_requests q
                    JOIN screening_runs r ON r.run_id=q.run_id
                    WHERE r.entity_key=$1 AND q.requested_at >= $2 AND q.source <> 'release_rescreen')''',
                    entry['entity_key'], cutoff):
                return 'skipped', 'requested_again_since_the_report'
            await screening_db.set_test_flag(conn, entity_key=entry['entity_key'],
                                             expected_run=UUID(entry['current_run_id']), active=True,
                                             reason=reason, actor=actor)
            if not execute:
                raise _DryRun()
        return 'flagged', None
    except _DryRun:
        return 'would_flag', None
    except screening_db.FlagRefused as exc:
        return 'skipped', exc.message
    except asyncpg.exceptions.LockNotAvailableError:
        return 'skipped', 'record is locked by other work; run again later'


async def flag_all(conn, inventory, reason, actor, execute):
    valid_inventory(inventory)
    reason = (reason or '').strip()
    if not 10 <= len(reason) <= 500:
        raise ValueError('The reason must be between 10 and 500 characters')
    actor = (actor or '').strip()
    if not actor:
        raise ValueError('Name the person responsible for this run')
    if await conn.fetchval('SELECT current_database()') != inventory.get('database'):
        raise ValueError('The report was made against a different database')
    if await database_identity(conn) != inventory['database_identity']:
        raise ValueError('The report database endpoint or identity does not match the current connection')
    cutoff = parse_time(inventory['cutoff'])
    counts, details = Counter(), []
    for entry in inventory['entities']:
        if entry['status'] != 'eligible':
            status, why = 'not_attempted', entry['status'] + ': ' + (', '.join(entry.get('reasons') or []) or 'see report')
        else:
            status, why = await flag_one(conn, entry, cutoff, reason, ACTOR_PREFIX + actor, execute)
        counts[status] += 1
        details.append({'entity_key': entry['entity_key'], 'status': status, **({'reason': why} if why else {})})
    return {'mode': 'execute' if execute else 'dry_run', 'actor': ACTOR_PREFIX + actor, 'flag_reason': reason,
            'counts': dict(counts), 'details': details,
            'status': 'complete' if not counts['skipped'] else 'needs_attention'}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--inventory', type=Path, required=True, help='report from find_test_screenings.py')
    parser.add_argument('--inventory-sha256', required=True, help='SHA-256 of the report you reviewed')
    parser.add_argument('--reason', required=True, help='why these are test data; stored on every flag (10-500 characters)')
    parser.add_argument('--actor', required=True, help='who is responsible for this run; stored on every flag')
    parser.add_argument('--output', type=Path, required=True, help='new private result path outside the repository')
    parser.add_argument('--execute', action='store_true', help='write the flags; without this nothing changes')
    args = parser.parse_args()
    content = args.inventory.read_bytes()
    if hashlib.sha256(content).hexdigest() != args.inventory_sha256.lower():
        parser.error('Report SHA-256 does not match')
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error('The result must be written outside the repository')
    if args.output.exists():
        parser.error('Output must be a new path')
    inventory = json.loads(content)
    try:
        valid_inventory(inventory)
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc) or 'Unsupported report')

    async def run():
        conn = await asyncpg.connect(os.environ['DATABASE_URL'], timeout=10)
        try:
            return await flag_all(conn, inventory, args.reason, args.actor, args.execute)
        finally:
            await conn.close()

    # Reserve the result file first, so a path problem cannot appear only after flags are written.
    write_private(args.output, {'status': 'started_or_interrupted', 'inventory_sha256': args.inventory_sha256.lower()})
    try:
        result = asyncio.run(run())
    except ValueError as exc:
        os.unlink(args.output)
        parser.error(str(exc))
    result['inventory_sha256'] = args.inventory_sha256.lower()
    fd, tempname = tempfile.mkstemp(prefix='.test-flags-', dir=args.output.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tempname, args.output)
    finally:
        if os.path.exists(tempname):
            os.unlink(tempname)
    print(json.dumps({'mode': result['mode'], 'status': result['status'], 'counts': result['counts'],
                      'report': str(args.output)}))
    if result['status'] != 'complete':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
