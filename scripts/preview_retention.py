"""Read-only aggregate retention inventory; never runs migrations or deletes rows."""
import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import sys

import asyncpg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from retention_report import preview


async def run(before):
    dsn=os.environ.get('SANCTIONS_REPORT_DSN')
    if not dsn:
        raise ValueError('Set SANCTIONS_REPORT_DSN to the database to inspect, preferably using a read-only account.')
    conn=await asyncpg.connect(dsn,server_settings={'default_transaction_read_only':'on'})
    try:
        return await preview(conn,before)
    finally:
        await conn.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',help='Optional hypothetical cutoff, ISO 8601 including timezone. No default retention period.')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    cutoff=datetime.fromisoformat(args.before.replace('Z','+00:00')) if args.before else None
    text=json.dumps(asyncio.run(run(cutoff)),indent=2,default=lambda value:value.isoformat())+'\n'
    if args.output:
        args.output.write_text(text)
    else:
        print(text,end='')
