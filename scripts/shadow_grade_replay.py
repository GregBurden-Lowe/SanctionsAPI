#!/usr/bin/env python3
"""Replay searches locally and report what the shadow evidence grades would change.

Each search runs through the matcher with evidence grades off, on a disposable
local PostgreSQL database built from a pinned Parquet snapshot. By default the
comparison is that same result with the graded policy applied as a shadow
(see evidence_grading.py). With --live the search is run a second time with
SCREENING_EVIDENCE_GRADES=enforce, so the comparison is what the matcher
itself returns. Nothing is written to production, and no live decision, queue
entry or screening history is created.

Cases come from scripts/shadow_review_replay.py: its constructed controls, and
its queue sample and analyst-reviewed cases when --queue is given. Output has
the same shape, so scripts/shadow_review_report.py can render it. Queue files
and output contain identity information and must stay outside the repository.
Constructed controls are not independently labelled, and a changed candidate
is neither a confirmed false positive nor a clearance. In the report, a search
whose shadow outcome is "DOB required" appears under "shadow does not alert";
its reason code is `weak_name_no_date_of_birth_supplied`.
"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
from uuid import uuid4

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import evidence_grading
import screening_db
import utils
from screening_policy import run_postgres_check
from shadow_review_replay import (control_cases, eligible_hits, holdout_cases, independent_cases,
                                  local_dsn, private_write, reviewed_cases, sample_cases)
from source_assurance import assess, parquet_manifest

CONTROL_SETS = {
    'standard': lambda path: control_cases(path),
    'holdout': lambda path: holdout_cases(path),
    'independent': lambda path: independent_cases(path),
    'none': lambda path: [],
}


def all_queue_cases(snapshot):
    """Every open queue entry with its recorded inputs, for whole-queue alert volume."""
    cases = []
    for row in snapshot['open']:
        result = row['result_json']
        result = json.loads(result) if isinstance(result, str) else result or {}
        recorded = result.get('Screening Inputs') or {}
        cases.append({'kind': 'queue', 'entity_key': row['entity_key'], 'run_id': row.get('current_run_id'),
                      'strata': ['all_open'], 'saved_result': result,
                      'inputs': {'name': recorded.get('name', row['display_name']),
                                 'dob': recorded.get('dob', row.get('date_of_birth')),
                                 'country': recorded.get('country', row.get('country_input')),
                                 'entity_type': recorded.get('entity_type', row['entity_type'])}})
    return cases


def live_comparison(baseline, live):
    """Describe a live enforce result against the grades-off result, in the shadow's terms."""
    changed = [{'matched_name': item.get('matched_name'), 'reason': item['disposition_reason'],
                'disposition': item['disposition'], 'record_id': item.get('record_id'),
                'previous_score': item.get('name_score'), 'entity_type': item.get('entity_type'),
                'dob_assessment': item.get('dob_assessment'), 'match_rule': item.get('match_rule')}
               for item in live.get('Candidate Evidence', []) if item.get('disposition_reason')]
    before = {h.get('record_id') for h in eligible_hits(baseline)}
    counts = Counter(item.get('evidence_grade') for item in live.get('Candidate Evidence', [])
                     if item.get('record_id') in before and item.get('evidence_grade'))
    live_summary = live.get('Candidate Evidence Summary') or {}
    base_summary = baseline.get('Candidate Evidence Summary') or {}
    if utils.is_dob_required_result(live):
        outcome = evidence_grading.DOB_REQUIRED
    elif baseline.get('Match Found') and not live.get('Match Found'):
        outcome = 'no_alert'
    elif live_summary.get('potential_matches', 0) < base_summary.get('potential_matches', 0):
        outcome = 'fewer_candidates'
    else:
        outcome = 'unchanged'
    live['Shadow Evidence Grades'] = {
        'rules': live.get('Matching Rules Version') or 'live-enforce', 'outcome': outcome,
        'dob_required': live_summary.get('dob_required', 0), 'demoted': live_summary.get('below_alert_threshold', 0),
        'person_potential_matches_by_grade': {grade: counts.get(grade, 0) for grade in ('strong', 'possible', 'weak')}}
    return changed


def tally(entries):
    """Counts only, so the summary carries no names."""
    done = [e for e in entries if e['status'] == 'complete']
    out = {'completed': len(done), 'incomplete': len(entries) - len(done)}
    for kind in sorted({e['kind'] for e in done}):
        group = [e for e in done if e['kind'] == kind]
        item = {
            'cases': len(group),
            'searches_alerting_now': sum(bool(e['baseline'].get('Match Found')) for e in group),
            # A screening that needs a date of birth is flagged like an alert in live mode,
            # so analyst work is counted without it.
            'searches_alerting_under_shadow': sum(
                bool(e['shadow'].get('Match Found'))
                and e['shadow']['Shadow Evidence Grades']['outcome'] != evidence_grading.DOB_REQUIRED for e in group),
            'searches_needing_dob_under_shadow': sum(
                e['shadow']['Shadow Evidence Grades']['outcome'] == evidence_grading.DOB_REQUIRED for e in group),
            'shadow_outcomes': dict(Counter(e['shadow']['Shadow Evidence Grades']['outcome'] for e in group)),
            'candidates_now': sum(len(eligible_hits(e['baseline'])) for e in group),
            'candidates_under_shadow': sum(len(eligible_hits(e['shadow'])) for e in group),
            'undetermined_truncated': sum(e['shadow']['Shadow Evidence Grades']['outcome'] == 'undetermined_truncated'
                                          for e in group),
            'person_candidates_by_grade': dict(sum(
                (Counter(e['shadow']['Shadow Evidence Grades']['person_potential_matches_by_grade']) for e in group),
                Counter())),
        }
        if any('target_id' in e for e in group):
            item['target_alerting_now'] = sum(bool(e.get('baseline_target_found')) for e in group)
            item['target_alerting_under_shadow'] = sum(bool(e.get('shadow_target_found')) for e in group)
            item['by_control'] = {
                label: {'cases': len(sub), 'target_now': sum(bool(e.get('baseline_target_found')) for e in sub),
                        'target_under_shadow': sum(bool(e.get('shadow_target_found')) for e in sub)}
                for label in sorted({e.get('control') or 'reviewed' for e in group})
                for sub in [[e for e in group if (e.get('control') or 'reviewed') == label]]}
        out[kind] = item
    return out


async def run(args):
    snapshot_path = args.source.resolve()
    out = args.output.resolve()
    queue_path = args.queue.resolve() if args.queue else None
    if out.is_relative_to(ROOT) or (queue_path and queue_path.is_relative_to(ROOT)):
        raise ValueError('Private data must be outside the repository')
    if out.exists():
        raise ValueError('Output directory already exists')
    manifest = assess(parquet_manifest(str(snapshot_path)))
    if args.source_version and manifest.get('version') != args.source_version:
        raise ValueError('Captured source differs from the pinned source version')
    if manifest.get('status') == 'stale' and args.allow_stale_source:
        # The import and the matcher both refuse a stale source. Lift the age
        # limit for this process only; the summary records the real status.
        os.environ['SCREENING_MAX_SOURCE_AGE_HOURS'] = str(24 * 365 * 100)
    elif manifest.get('status') != 'current':
        raise ValueError(f"Source is {manifest.get('status')}; a stale snapshot needs --allow-stale-source")
    out.mkdir(parents=True, mode=0o700)
    utils.DATA_DIR = str(out)
    utils.MATCHING_CONFIG_PATH = str(out / 'matching_config.json')
    private_write(Path(utils.MATCHING_CONFIG_PATH), {'custom_generic_words': []})
    os.environ['SCREENING_MATCHER_VERSION'] = 'v2'
    os.environ['SCREENING_EVIDENCE_GRADES'] = 'off'

    cases, strata, queue_population = [], {}, 0
    if queue_path:
        snapshot = json.loads(queue_path.read_text())
        cases, strata = (all_queue_cases(snapshot), {}) if args.all_queue else sample_cases(snapshot)
        cases += reviewed_cases(snapshot)
        queue_population = len(snapshot['open'])
    controls = CONTROL_SETS[args.controls](snapshot_path)
    cases += controls
    if args.limit:
        cases = cases[:args.limit]

    summary = {'source_version': manifest.get('version'), 'source_status': manifest.get('status'),
               'source_sha256': hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
               'queue_sha256': hashlib.sha256(queue_path.read_bytes()).hexdigest() if queue_path else None,
               'baseline_rules': utils.rules_version_for('postgres_beta'),
               'shadow_rules': ('live-enforce' + utils.EVIDENCE_GRADES_RULES_SUFFIX if args.live
                                else evidence_grading.SHADOW_RULES_VERSION),
               'mode': 'live' if args.live else 'shadow',
               'grading_code_sha256': hashlib.sha256((ROOT / 'evidence_grading.py').read_bytes()).hexdigest(),
               'queue_population': queue_population, 'strata_sizes': strata, 'control_set': args.controls,
               'cases': len(cases), 'local_python': platform.python_version(), 'status': 'running'}
    private_write(out / 'summary.json', summary)

    admin = await asyncpg.connect(local_dsn(args.admin_dsn))
    db = 'sanctions_shadow_' + uuid4().hex
    conn = None
    created = False
    entries = []
    try:
        await admin.execute(f'CREATE DATABASE "{db}"')
        created = True
        conn = await asyncpg.connect(args.admin_dsn, database=db, command_timeout=180)
        summary['local_postgres'] = await conn.fetchval('SHOW server_version')
        await conn.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        await screening_db.ensure_schema(conn)
        await conn.execute('''CREATE TABLE watchlist_entities(source_type text, entity_id text, entity_schema text,
            name text, name_norm text, birth_date date, dataset text, regime text, raw_json jsonb)''')
        await utils.sync_own_watchlists_postgres(conn, watchlists_parquet_path=str(snapshot_path))
        await conn.execute('ANALYZE watchlist_entities')
        print(f'Imported source; evaluating {len(cases)} cases', flush=True)
        with evidence_grading.recording() as grades:
            for number, case in enumerate(cases, 1):
                entry = {key: value for key, value in case.items() if key != 'saved_result'}
                grades.clear()
                try:
                    baseline = await run_postgres_check(conn, **case['inputs'], log_search=False,
                                                        require_assurance=True)
                    if args.live:
                        os.environ['SCREENING_EVIDENCE_GRADES'] = 'enforce'
                        try:
                            shadow = await run_postgres_check(conn, **case['inputs'], log_search=False,
                                                              require_assurance=True)
                        finally:
                            os.environ['SCREENING_EVIDENCE_GRADES'] = 'off'
                        demoted = live_comparison(baseline, shadow)
                    else:
                        shadow, demoted = evidence_grading.apply_shadow_policy(
                            baseline, grades, submitted_type=case['inputs'].get('entity_type'), dob=case['inputs'].get('dob'))
                    entry.update(status='complete', baseline=baseline, shadow=shadow, removed_candidate_names=demoted,
                                 added_candidate_names=[], ambiguous_candidate_names=[])
                    if case['kind'] == 'queue':
                        entry['saved_result'] = case['saved_result']
                    if 'target_id' in case:
                        for label, result in (('baseline', baseline), ('shadow', shadow)):
                            # Awaiting a date of birth is still open, so the target is not lost.
                            open_ids = {h.get('record_id') for h in result.get('Candidate Evidence', [])
                                        if h.get('source_type') == 'sanctions'
                                        and h.get('disposition') in ('potential_match', evidence_grading.DOB_REQUIRED)}
                            entry[label + '_target_open'] = case['target_id'] in open_ids
                            found = case['target_id'] in {h.get('record_id') for h in eligible_hits(result)}
                            entry[label + '_target_found'] = found
                            entry[label + '_target_unknown'] = not found and bool(
                                (result.get('Candidate Evidence Summary') or {}).get('display_truncated'))
                except Exception as exc:
                    entry.update(status='incomplete', error_type=type(exc).__name__, error=str(exc))
                entries.append(entry)
                if number % 25 == 0 or number == len(cases):
                    private_write(out / 'results.json', entries)
                    print(f'Completed {number}/{len(cases)}', flush=True)
        summary['status'] = 'diagnostic_complete'
        summary['totals'] = tally(entries)
    finally:
        if conn:
            await conn.close()
        if created:
            await admin.execute(f'DROP DATABASE "{db}"')
            summary['disposable_database_removed'] = True
        await admin.close()
        private_write(out / 'summary.json', summary)
    print(json.dumps(summary.get('totals', {}), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, required=True, help='pinned watchlists Parquet snapshot')
    parser.add_argument('--source-version', help='refuse to run unless the snapshot has this version')
    parser.add_argument('--queue', type=Path, help='private captured queue JSON (optional)')
    parser.add_argument('--live', action='store_true',
                        help='compare against the matcher run with SCREENING_EVIDENCE_GRADES=enforce, not the shadow policy')
    parser.add_argument('--all-queue', action='store_true',
                        help='replay every open queue entry instead of the stratified sample')
    parser.add_argument('--output', type=Path, required=True, help='new private directory outside the repository')
    parser.add_argument('--admin-dsn', required=True, help='local PostgreSQL only')
    parser.add_argument('--controls', choices=sorted(CONTROL_SETS), default='standard')
    parser.add_argument('--allow-stale-source', action='store_true',
                        help='run an offline diagnostic on a snapshot older than the freshness limit')
    parser.add_argument('--limit', type=int, help='evaluate only the first N cases')
    asyncio.run(run(parser.parse_args()))
