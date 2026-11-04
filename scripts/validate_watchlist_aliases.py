#!/usr/bin/env python3
"""Read-only validation of nested aliases in the current PostgreSQL snapshot."""
import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import sys

import asyncpg
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from source_assurance import postgres_manifest, assess
from utils import _parse_aliases, ScreeningUnavailableError


async def validate(conn):
    counts=Counter()
    async with conn.transaction(isolation='repeatable_read', readonly=True):
        version=int(await conn.fetchval("SHOW server_version_num"))
        encoding=await conn.fetchval('SHOW server_encoding')
        trigrams=await conn.fetchval("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='pg_trgm')")
        await conn.execute('LOCK TABLE watchlist_entities IN ACCESS SHARE MODE')
        coverage=assess(await postgres_manifest(conn))
        async for row in conn.cursor("SELECT source_type, raw_json->>'aliases' AS aliases FROM watchlist_entities",prefetch=2000):
            counts['rows']+=1
            counts['rows_'+str(row['source_type'])]+=1
            try:
                _parse_aliases(row['aliases'])
            except ScreeningUnavailableError:
                counts['malformed']+=1
        counts.setdefault('malformed',0)
        ok=(counts['rows_sanctions']>0 and counts['malformed']==0
            and coverage.get('status')=='current' and version>=130000 and encoding=='UTF8' and trigrams)
        return {'status':'passed' if ok else 'needs_attention', 'counts':dict(counts),
                'source_version':coverage.get('version'), 'source_status':coverage.get('status'),
                'postgres_version_num':version, 'server_encoding':encoding, 'pg_trgm':trigrams,
                'note':'Read-only check; it does not certify source completeness or matching accuracy.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    if not os.environ.get('DATABASE_URL'):
        parser.error('DATABASE_URL is required')
    async def run():
        conn=await asyncpg.connect(os.environ['DATABASE_URL'],timeout=10)
        try:return await validate(conn)
        finally:await conn.close()
    report=asyncio.run(run())
    print(json.dumps(report))
    if report['status']!='passed':raise SystemExit(2)


if __name__=='__main__':
    main()
