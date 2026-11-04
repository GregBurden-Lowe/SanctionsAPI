"""Diagnostic replay of an exported candidate pack against an explicit local snapshot.

Never treats past false-positive dispositions as engine-clear expectations. Creates
only a disposable local PostgreSQL DB; writes private reports outside the repository.
"""
import argparse
import asyncio
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import asyncpg
import pyarrow.parquet as pq
from screening_policy import run_postgres_check, run_parquet_check
from utils import _normalize_text, stamp_screening_result, get_matching_config
from scripts.rehearse_history_migration import local_dsn


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    temporary.chmod(0o600)
    temporary.replace(path)


def load_pack(folder):
    with (folder/'cases.csv').open(encoding='utf-8-sig') as stream:
        cases = list(csv.DictReader(stream))
    evidence = json.loads((folder/'evidence.json').read_text())
    ids = [row['case_id'] for row in cases]
    evidence_ids = [row['case_id'] for row in evidence]
    if len(set(ids)) != len(ids) or len(set(evidence_ids)) != len(evidence_ids) or set(ids) != set(evidence_ids):
        raise ValueError('CSV/evidence case IDs must be unique and match exactly')
    by_id = {row['case_id']:row for row in cases}
    for row in evidence:
        row['acceptance_fields'] = {key:by_id[row['case_id']].get(key, '') for key in
            ('expected_outcome','expected_watchlist_entry','acceptance_reason','acceptance_reviewer','acceptance_date')}
    return evidence


def inputs_for(case):
    raw = case['inputs']
    args = {key:raw.get(key) for key in ('name','dob','country','entity_type')}
    normalizations = []
    if args['entity_type'] == 'Organisation':
        args['entity_type'] = 'Organization'
        normalizations.append('Historical Organisation spelling mapped to API Organization spelling')
    if args['entity_type'] not in ('Person','Organization') or not args['name']:
        raise ValueError('Unsupported or missing exported input')
    return args, normalizations


def outcome(result):
    if result.get('Is Sanctioned'):
        return 'potential_sanctions_match'
    return 'no_potential_match'


async def replay(pack, snapshot, output, parquet_count, only_new_against=None):
    if output.resolve().is_relative_to(ROOT):
        raise ValueError('Private case reports must be outside the repository')
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    cases = load_pack(pack)
    manifest = json.loads((pack/'manifest.json').read_text())
    expected_counts = {k:v['exported'] for k,v in manifest['counts'].items()}
    actual_counts = Counter(case['category'] for case in cases)
    if any(actual_counts[k] != v for k,v in expected_counts.items()) or set(actual_counts)-set(expected_counts):
        raise ValueError('Manifest counts do not match supplied cases')
    input_pack_cases = len(cases)
    comparison_hashes = None
    if only_new_against:
        previous = {case['case_id']:case for case in load_pack(only_new_against)}
        cases = [case for case in cases if previous.get(case['case_id']) != case]
        actual_counts = Counter(case['category'] for case in cases)
        comparison_hashes = {name:digest(only_new_against/name) for name in ('manifest.json','evidence.json','cases.csv')}
    summary = dict(status='running', acceptance_status='not_signed_off',
        generated_at=datetime.now(timezone.utc).isoformat(), cases=len(cases), categories=dict(actual_counts),
        input_pack_cases=input_pack_cases, unchanged_cases_skipped=input_pack_cases-len(cases),
        comparison_pack_hashes=comparison_hashes,
        supplied_acceptance_labels=sum(bool(c['acceptance_fields']['expected_outcome'].strip()) for c in cases),
        auto_resolved_cases=sum((c.get('notes') or '').lower().startswith('auto-resolved') for c in cases),
        matching_configuration=get_matching_config(),
        input_hashes={name:digest(pack/name) for name in ('manifest.json','evidence.json','cases.csv')},
        snapshot={'path':str(snapshot), 'sha256':digest(snapshot),
                  'file_modified_at':datetime.fromtimestamp(snapshot.stat().st_mtime,timezone.utc).isoformat(),
                  'columns':pq.ParquetFile(snapshot).schema.names,
                  'rows':pq.ParquetFile(snapshot).metadata.num_rows},
        limitations=[
            'Historical false-positive decisions are not engine-clear expected outcomes.',
            'Unreviewed clearances are not ground truth; no accuracy or recall claim is made.',
            'Snapshot file time is not a verified watchlist publication date; no live provider calls made.',
            'Local matching settings may differ from deployed settings.',
            'PostgreSQL is loaded from the same explicit Parquet snapshot; generated row IDs are not provider IDs.',
            'Missing source identifiers/countries in a legacy snapshot cannot be reconstructed.',
        ])
    write_json(output/'summary.json', summary)
    dsn = local_dsn(os.environ.get('SANCTIONS_TEST_ADMIN_DSN',''))
    admin = await asyncpg.connect(dsn)
    database = 'sanctions_acceptance_' + uuid4().hex
    created, conn = False, None
    results = []
    try:
        await admin.execute(f'CREATE DATABASE "{database}"'); created = True
        conn = await asyncpg.connect(dsn, database=database)
        await conn.execute('CREATE EXTENSION pg_trgm')
        await conn.execute('''CREATE TABLE watchlist_entities(source_type text,entity_id text,entity_schema text,
            name text,name_norm text,birth_date date,dataset text,regime text,raw_json jsonb)''')
        counter = 0
        for batch in pq.ParquetFile(snapshot).iter_batches(batch_size=10000):
            records = []
            for row in batch.to_pylist():
                records.append((row['source_type'], str(row.get('id') or f'snapshot-row-{counter}'), row['schema'],row['name'],
                    _normalize_text(row['name']),row.get('dataset'),row.get('program_ids'),json.dumps(row)))
                counter += 1
            await conn.copy_records_to_table('watchlist_entities',columns=['source_type','entity_id','entity_schema',
                'name','name_norm','dataset','regime','raw_json'],records=records)
        await conn.execute('CREATE INDEX acceptance_name ON watchlist_entities USING gin(name_norm gin_trgm_ops)')
        await conn.execute('CREATE INDEX acceptance_type ON watchlist_entities(source_type,lower(entity_schema))')
        await conn.execute('ANALYZE watchlist_entities')
        await conn.execute("SET statement_timeout='90s'")
        summary['postgres_version'] = await conn.fetchval('SHOW server_version')
        print(f'Loaded {counter} watchlist rows into disposable local DB',flush=True)
        for index, case in enumerate(cases, 1):
            entry = {'case_id':case['case_id'], 'category':case['category'], 'inputs':case['inputs'],
                'historical_outcome':case.get('outcome'), 'historical_notes':case.get('notes'),
                'historical_result':case['result_json'], 'acceptance_fields':case['acceptance_fields'],
                'acceptance_assessment':'not_scored_requires_independent_expected_candidate_and_version'}
            started = time.perf_counter()
            try:
                args, changes = inputs_for(case)
                entry['normalizations'] = changes
                result = await run_postgres_check(conn, **args, log_search=False)
                stamp_screening_result(result, **args, backend='postgres_beta')
                entry['postgres'] = {'outcome':outcome(result), 'result':result}
            except Exception as exc:
                entry['postgres'] = {'outcome':'incomplete', 'error_type':type(exc).__name__, 'message':str(exc)}
            entry['postgres']['seconds'] = round(time.perf_counter()-started,3)
            results.append(entry)
            write_json(output/'results.json',results)
            print(f'Postgres {index}/{len(cases)}: {entry["postgres"]["outcome"]}',flush=True)
        # Select discrepancies/incomplete cases first, then balance reviewed and unreviewed cases.
        selected = [r for r in results if r['postgres']['outcome']=='incomplete' or
                    (r['category']=='cleared_unreviewed' and r['postgres']['outcome']!='no_potential_match')]
        remainder = [r for r in results if r not in selected]
        groups = [[r for r in remainder if r['category']==group] for group in ('false_positive','cleared_unreviewed')]
        while any(groups):
            for group in groups:
                if group:
                    selected.append(group.pop(0))
        selected = selected[:parquet_count]
        # Full-snapshot Parquet uses the same public matcher, with logging disabled.
        from functools import partial
        from utils import perform_opensanctions_check
        matcher = partial(perform_opensanctions_check, parquet_path=str(snapshot))
        for index, entry in enumerate(selected,1):
            started = time.perf_counter()
            try:
                args, _ = inputs_for(entry)
                result = run_parquet_check(**args, matcher=matcher, log_search=False)
                stamp_screening_result(result, **args, backend='original')
                entry['parquet'] = {'outcome':outcome(result), 'result':result}
                entry['backend_outcomes_agree'] = entry['postgres']['outcome']==outcome(result)
            except Exception as exc:
                entry['parquet'] = {'outcome':'incomplete','error_type':type(exc).__name__,'message':str(exc)}
            entry['parquet']['seconds'] = round(time.perf_counter()-started,3)
            write_json(output/'results.json',results)
            print(f'Parquet {index}/{len(selected)}: {entry["parquet"]["outcome"]}',flush=True)
        summary['postgres_by_category'] = {category:dict(Counter(r['postgres']['outcome'] for r in results if r['category']==category)) for category in actual_counts}
        summary['parquet_replayed'] = len(selected)
        summary['backend_disagreements'] = sum(r.get('backend_outcomes_agree') is False for r in results)
        from acceptance_metrics import score_cases
        summary['quality_metrics'] = {backend:score_cases(results, backend) for backend in ('postgres','parquet')}
        summary['status'] = 'diagnostic_replay_complete'
    finally:
        if conn: await conn.close()
        try:
            if created:
                await admin.execute(f'DROP DATABASE "{database}"')
                summary['disposable_database_removed'] = True
        finally:
            await admin.close()
            write_json(output/'summary.json',summary)
    print(json.dumps(summary['postgres_by_category']),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack',type=Path,required=True)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--parquet-count',type=int,default=10)
    parser.add_argument('--only-new-against',type=Path,help='Earlier complete pack: skip only unchanged parsed cases including acceptance fields')
    args = parser.parse_args()
    if not 0<=args.parquet_count<=1000:
        parser.error('--parquet-count must be 0..1000')
    asyncio.run(replay(args.pack,args.snapshot,args.output,args.parquet_count,args.only_new_against))
