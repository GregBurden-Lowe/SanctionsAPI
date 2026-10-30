"""Synthetic search benchmark in a disposable local DB; never uses production data."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch
from uuid import uuid4

import asyncpg
import pandas as pd
from rehearse_history_migration import local_dsn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from screening_policy import run_parquet_check, run_postgres_check
from utils import _normalize_text


async def benchmark(size, repeats, concurrency):
    dsn = local_dsn(os.environ.get('SANCTIONS_TEST_ADMIN_DSN',''))
    db = 'sanctions_benchmark_' + uuid4().hex
    admin = await asyncpg.connect(dsn)
    conn = None
    pool = None
    created = False
    report = {'synthetic_rows':size, 'repeats':repeats, 'concurrency':concurrency, 'production_data_used':False}
    try:
        await admin.execute(f'CREATE DATABASE "{db}"'); created = True
        conn = await asyncpg.connect(dsn, database=db)
        await conn.execute('CREATE EXTENSION pg_trgm')
        await conn.execute('''CREATE TABLE watchlist_entities(source_type text, entity_id text, entity_schema text,
            name text,name_norm text,birth_date date,dataset text,regime text,raw_json jsonb)''')
        rows=[]
        for i in range(size-2):
            token=hashlib.sha256(str(i).encode()).hexdigest()[:24]
            rows.append(dict(id=str(i), name='Fixture '+token, aliases='Alias '+token, schema='Organization' if i%2 else 'Person',
                source_type='peps' if i%5==0 else 'sanctions', dataset='Synthetic'))
        rows.extend([dict(id='target',name='Jane Smith',aliases='Jane Jones',schema='Person',source_type='sanctions',dataset='Synthetic',birth_date='1980'),
            dict(id='company',name='Hyde & Partners Ltd',schema='Organization',source_type='sanctions',dataset='Synthetic')])
        await conn.copy_records_to_table('watchlist_entities',columns=['source_type','entity_id','entity_schema','name','name_norm','dataset','raw_json'],
            records=[(r['source_type'],r['id'],r['schema'],r['name'],_normalize_text(r['name']),r['dataset'],json.dumps(r)) for r in rows])
        await conn.execute('CREATE INDEX benchmark_name_trgm ON watchlist_entities USING gin(name_norm gin_trgm_ops)')
        await conn.execute('CREATE INDEX benchmark_source_schema ON watchlist_entities(source_type,entity_schema)')
        await conn.execute('ANALYZE watchlist_entities')
        report['postgres_version']=await conn.fetchval('SHOW server_version')
        df=pd.DataFrame(rows)
        queries=[('exact', 'Jane Smith','Person', True), ('alias','Jane Jones','Person', True),
                 ('no_match','Vladimir Petrov','Person',False), ('company','Hyde and Partners','Organization',True)]
        samples=[]
        for label,name,kind,expected in queries:
            args=dict(name=name,dob=None,country='UK',entity_type=kind)
            timings={'postgres':[],'parquet_in_memory':[]}
            for _ in range(repeats):
                start=time.perf_counter(); pg=await run_postgres_check(conn,**args)
                timings['postgres'].append((time.perf_counter()-start)*1000)
                with patch('utils.get_opensanctions_df',return_value=df):
                    start=time.perf_counter(); pq=run_parquet_check(**args)
                    timings['parquet_in_memory'].append((time.perf_counter()-start)*1000)
                assert pg['Match Found']==pq['Match Found']==expected, label
                assert pg['Candidate Evidence']==pq['Candidate Evidence'], label
            samples.append({'query':label, **{backend:{'median_ms':round(statistics.median(values),2),'max_ms':round(max(values),2)} for backend,values in timings.items()}})
            print(f'Completed {label}',file=sys.stderr,flush=True)
        report['queries']=samples
        pool=await asyncpg.create_pool(dsn,database=db,min_size=concurrency,max_size=concurrency)
        async def concurrent_query():
            async with pool.acquire() as c:
                start=time.perf_counter()
                value=await run_postgres_check(c,name='Jane Jones',dob=None,country='UK',entity_type='Person')
                assert value['Match Found']
                return (time.perf_counter()-start)*1000
        start=time.perf_counter(); latencies=await asyncio.gather(*(concurrent_query() for _ in range(concurrency)))
        elapsed=time.perf_counter()-start
        report['concurrent_alias']={'wall_seconds':round(elapsed,3),'median_ms':round(statistics.median(latencies),2),'max_ms':round(max(latencies),2)}
        report['status']='passed'
    finally:
        if pool: await pool.close()
        if conn: await conn.close()
        try:
            if created:
                await admin.execute(f'DROP DATABASE "{db}"')
                report['disposable_database_removed']=True
        finally:
            await admin.close()
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows',type=int,default=100000)
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--concurrency',type=int,default=4)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not 10<=args.rows<=1000000 or not 1<=args.repeats<=20 or not 1<=args.concurrency<=16:
        parser.error('rows 10..1000000, repeats 1..20, concurrency 1..16 required')
    output=json.dumps(asyncio.run(benchmark(args.rows,args.repeats,args.concurrency)),indent=2)+'\n'
    if args.output: args.output.write_text(output)
    print(output,end='')
