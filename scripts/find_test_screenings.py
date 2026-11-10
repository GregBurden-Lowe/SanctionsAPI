#!/usr/bin/env python3
"""Read-only list of saved screenings that a benchmark or test run created.

Selects recorded requests by when they were made and by who or what made them,
then reports the saved screenings those requests produced. It writes nothing
to the database. The report contains personal data: it is created mode 0600,
never replaces a file, and must stay outside the repository.

A screening is only `eligible` for bulk flagging when every recorded request
for it falls inside the selection. One that anyone else has also searched is
`needs_review`, because the same name may belong to a real customer. A name is
never enough on its own: --listed-names-only only narrows a selection.

Monitoring re-screens are recorded as `background_job` requests with a
`system:` requestor. They are the system re-checking a record that already
exists, so they are never selected and never count as someone else searching.
A background job with any other requestor is a real submission and does count.
"""
import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import re
import sys

import asyncpg
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.plan_release_rescreen import database_identity, decode, parse_time, write_private

INVENTORY_KIND = 'test_screening_candidates'
# References an earlier monitoring generation wrote onto the records it re-screened.
SYSTEM_REFERENCE_PREFIXES = ('MONITOR-', 'UK-DELTA-')


def is_system_request(request):
    """The system re-screening an existing record, as opposed to someone searching for it."""
    if request['source'] == 'release_rescreen':
        return True
    if request['source'] != 'background_job':
        return False
    requestor = str(decode(request['inputs']).get('requestor') or '')
    return requestor.startswith('system:') or (request['business_reference'] or '').startswith(SYSTEM_REFERENCE_PREFIXES)


def selection_from(args):
    selection = {
        'start': parse_time(args.start).isoformat(), 'end': parse_time(args.end).isoformat(),
        'actors': sorted(set(args.actor or [])), 'sources': sorted(set(args.source or [])),
        'requestors': sorted({r.strip().lower() for r in (args.requestor or []) if r.strip()}),
        'reference_prefixes': sorted(set(args.reference_prefix or [])),
        'reference_regex': args.reference_regex or None,
        'listed_names_only': bool(args.listed_names_only), 'window_only': bool(args.window_only),
    }
    if selection['start'] >= selection['end']:
        raise ValueError('The start must be before the end')
    narrowed = any(selection[key] for key in ('actors', 'sources', 'requestors', 'reference_prefixes', 'reference_regex'))
    if not narrowed and not selection['window_only']:
        raise ValueError('Give at least one of --actor, --source, --requestor, --reference-prefix or --reference-regex, '
                         'or pass --window-only to select every request in the window')
    if selection['reference_regex']:
        re.compile(selection['reference_regex'])
    return selection


def is_selected(request, selection):
    """Whether one recorded request falls inside the selection (the time window is applied in SQL)."""
    inputs = decode(request['inputs'])
    reference = request['business_reference'] or ''
    if selection['actors'] and (request['actor'] or '') not in selection['actors']:
        return False
    if selection['sources'] and request['source'] not in selection['sources']:
        return False
    if selection['requestors'] and str(inputs.get('requestor') or '').strip().lower() not in selection['requestors']:
        return False
    if selection['reference_prefixes'] and not any(reference.startswith(p) for p in selection['reference_prefixes']):
        return False
    if selection['reference_regex'] and not re.search(selection['reference_regex'], reference):
        return False
    return True


async def listed_names(conn):
    """Normalised listed names and aliases, when the watchlist index is present."""
    if await conn.fetchval("SELECT to_regclass('public.watchlist_names') IS NOT NULL") and \
            await conn.fetchval('SELECT EXISTS(SELECT 1 FROM watchlist_names)'):
        return {r['name_norm'] for r in await conn.fetch('SELECT DISTINCT name_norm FROM watchlist_names')}
    if await conn.fetchval("SELECT to_regclass('public.watchlist_entities') IS NOT NULL"):
        return {r['name_norm'] for r in await conn.fetch('SELECT DISTINCT name_norm FROM watchlist_entities')}
    return None


async def find(conn, selection):
    """Build the candidate inventory inside one read-only snapshot."""
    async with conn.transaction(isolation='repeatable_read', readonly=True):
        cutoff = await conn.fetchval('SELECT transaction_timestamp()')
        start, end = parse_time(selection['start']), parse_time(selection['end'])
        in_window = await conn.fetch('''SELECT q.request_id,q.requested_at,q.actor,q.source,q.inputs,q.business_reference,
                   q.status,r.entity_key
            FROM screening_requests q LEFT JOIN screening_runs r ON r.run_id=q.run_id
            WHERE q.requested_at >= $1 AND q.requested_at < $2 AND q.source <> 'release_rescreen'
            ORDER BY q.requested_at,q.request_id''', start, end)
        chosen = [row for row in in_window if not is_system_request(row) and is_selected(row, selection)]
        chosen_ids = {row['request_id'] for row in chosen}
        by_entity = {}
        unsaved = 0
        for row in chosen:
            if row['entity_key'] is None:
                unsaved += 1            # failed or still queued: there is no saved screening to flag
                continue
            by_entity.setdefault(row['entity_key'], []).append(row)
        keys = sorted(by_entity)
        # Every recorded request for these screenings, at any time, to see who else searched them.
        everyone = await conn.fetch('''SELECT q.request_id,q.requested_at,q.actor,q.source,q.inputs,
                   q.business_reference,r.entity_key
            FROM screening_requests q JOIN screening_runs r ON r.run_id=q.run_id
            WHERE r.entity_key=ANY($1::text[])''', keys)
        outside, outside_from, system = {}, {}, Counter()
        for row in everyone:
            if row['request_id'] in chosen_ids:
                continue
            if is_system_request(row):
                system[row['entity_key']] += 1
                continue
            outside.setdefault(row['entity_key'], []).append(row['requested_at'])
            outside_from.setdefault(row['entity_key'], Counter())[f"{row['source']} / {row['actor'] or 'no login'}"] += 1
        saved = {row['entity_key']: row for row in await conn.fetch('''SELECT s.entity_key,s.display_name,s.normalized_name,
                   s.entity_type,s.date_of_birth,s.country_input,s.business_reference,s.last_requestor,s.status,
                   s.review_status,s.review_outcome,s.current_run_id,s.last_screened_at,
                   EXISTS(SELECT 1 FROM screening_test_flags f WHERE f.entity_key=s.entity_key AND f.active) AS has_flag,
                   screening_is_test(s) AS flagged,
                   EXISTS(SELECT 1 FROM screening_jobs j WHERE j.entity_key=s.entity_key
                          AND j.status IN ('pending','running')) AS active_job
            FROM screened_entities s WHERE s.entity_key=ANY($1::text[])''', keys)}
        names = await listed_names(conn) if (selection['listed_names_only'] or keys) else None
        if selection['listed_names_only'] and names is None:
            raise ValueError('--listed-names-only needs the watchlist tables, which are not present')
        entities, counts = [], Counter()
        for key in keys:
            requests = by_entity[key]
            row = saved.get(key)
            exact = None if names is None or row is None else (row['normalized_name'] in names)
            if selection['listed_names_only'] and not exact:
                counts['not_a_listed_name'] += 1
                continue
            others = sorted(outside.get(key, []))
            reasons = []
            if row is None:
                status, reasons = 'skipped', ['no_saved_screening']
            elif row['flagged']:
                status, reasons = 'skipped', ['already_flagged']
            else:
                if others:
                    reasons.append('also_requested_outside_the_selection')
                if row['has_flag']:
                    reasons.append('earlier_flag_no_longer_applies')
                if not (row['business_reference'] or '').strip():
                    reasons.append('no_business_reference')
                if row['active_job']:
                    reasons.append('check_queued_or_running')
                status = 'needs_review' if reasons else 'eligible'
            counts[status] += 1
            for reason in reasons:
                counts['reason:' + reason] += 1
            entry = {'entity_key': key, 'status': status, 'reasons': reasons,
                     'selected_requests': len(requests),
                     'first_selected_request': requests[0]['requested_at'].isoformat(),
                     'last_selected_request': requests[-1]['requested_at'].isoformat(),
                     'actors': sorted({r['actor'] or '' for r in requests}),
                     'sources': sorted({r['source'] for r in requests}),
                     'requestors': sorted({str(decode(r['inputs']).get('requestor') or '') for r in requests}),
                     'business_references': sorted({r['business_reference'] or '' for r in requests}),
                     'requests_outside_selection': len(others),
                     'outside_selection_by_source_and_login': dict(outside_from.get(key, {})),
                     'latest_request_outside_selection': others[-1].isoformat() if others else None,
                     'system_rescreens_ignored': system[key],
                     'name_is_exactly_listed': exact}
            if row is not None:
                entry.update({'display_name': row['display_name'], 'entity_type': row['entity_type'],
                              'date_of_birth': row['date_of_birth'].isoformat() if row['date_of_birth'] else None,
                              'country_input': row['country_input'], 'current_business_reference': row['business_reference'],
                              'last_requestor': row['last_requestor'], 'current_status': row['status'],
                              'review_status': row['review_status'], 'review_outcome': row['review_outcome'],
                              'current_run_id': str(row['current_run_id']) if row['current_run_id'] else None,
                              'last_screened_at': row['last_screened_at'].isoformat() if row['last_screened_at'] else None})
            entities.append(entry)
        return {'version': 1, 'kind': INVENTORY_KIND, 'cutoff': cutoff.isoformat(), 'selection': selection,
                'database': await conn.fetchval('SELECT current_database()'),
                'database_identity': await database_identity(conn),
                'requests_in_window': len(in_window), 'requests_selected': len(chosen),
                'selected_requests_without_a_saved_screening': unsaved,
                'system_rescreens_ignored': sum(system.values()),
                'counts': dict(counts), 'entities': entities,
                'notes': ['Read-only inventory. Nothing was flagged, changed or deleted.',
                          'Eligible means every recorded request for the screening is inside the selection; '
                          'it does not prove the record is test data. Check the selection before flagging.',
                          'Reason counts overlap.']}


async def run(selection):
    dsn = os.environ.get('DATABASE_URL', '').strip()
    if not dsn:
        raise ValueError('DATABASE_URL is required')
    conn = await asyncpg.connect(dsn, timeout=10)
    try:
        return await find(conn, selection)
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--start', required=True, help='inclusive start with timezone, e.g. 2026-11-05T09:00:00+00:00')
    parser.add_argument('--end', required=True, help='exclusive end with timezone')
    parser.add_argument('--actor', action='append', help='login or API key name recorded on the request; repeatable')
    parser.add_argument('--source', action='append', help='recorded request source, e.g. /opcheck; repeatable')
    parser.add_argument('--requestor', action='append', help='requestor field the benchmark sent; repeatable')
    parser.add_argument('--reference-prefix', action='append', help='business reference prefix; repeatable')
    parser.add_argument('--reference-regex', help='regular expression the business reference must contain')
    parser.add_argument('--listed-names-only', action='store_true',
                        help='keep only screenings whose searched name is exactly a listed name or alias')
    parser.add_argument('--window-only', action='store_true',
                        help='select every request in the window when nothing else identifies the benchmark')
    parser.add_argument('--output', type=Path, required=True, help='new private report path outside the repository')
    args = parser.parse_args()
    try:
        selection = selection_from(args)
    except (ValueError, re.error) as exc:
        parser.error(str(exc))
    if args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error('The report contains personal data and must be written outside the repository')
    if args.output.exists():
        parser.error('Output must be a new path')
    report = asyncio.run(run(selection))
    write_private(args.output, report)
    print(json.dumps({'report': str(args.output), 'requests_in_window': report['requests_in_window'],
                      'requests_selected': report['requests_selected'], 'screenings': len(report['entities']),
                      'counts': report['counts']}))


if __name__ == '__main__':
    main()
