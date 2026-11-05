"""Synthetic expected outcomes and real backend retrieval parity, not accuracy claims."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from uuid import uuid4

import asyncpg
import pandas as pd
from screening_policy import run_postgres_check
from utils import _normalize_text, ScreeningUnavailableError, perform_postgres_watchlist_check

CASES = json.loads((Path(__file__).parent / 'fixtures/policy_cases.json').read_text())


def fixture(case):
    return [{**dict(id='target', schema='Person', source_type='sanctions', dataset='OFAC'), **case['row']},
            dict(id='coverage-sanctions', name='Zzyzx Remote Subject', schema='Person', source_type='sanctions', dataset='OFAC')]


def arguments(case):
    return dict(name=case['query'], dob=case.get('dob'), country=case.get('country'), entity_type=case.get('type','Person'))


def assert_outcome(test, result, case):
    test.assertEqual(result['Match Found'], case['match'], case['label'])
    for key in ('dob_assessment','country_assessment'):
        if key in case:
            test.assertEqual(result['Candidate Evidence'][0][key], case[key], case['label'])


@unittest.skipUnless(os.getenv('SANCTIONS_TEST_ADMIN_DSN'), 'Requires isolated local PostgreSQL')
class PostgresRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.admin = await asyncpg.connect(os.environ['SANCTIONS_TEST_ADMIN_DSN'])
        self.database = 'sanctions_parity_' + uuid4().hex
        await self.admin.execute(f'CREATE DATABASE "{self.database}"')
        self.conn = await asyncpg.connect(os.environ['SANCTIONS_TEST_ADMIN_DSN'], database=self.database)
        await self.conn.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        await self.conn.execute('''CREATE TABLE watchlist_entities(source_type text, entity_id text, entity_schema text,
            name text, name_norm text, birth_date date, dataset text, regime text, raw_json jsonb)''')

    async def asyncTearDown(self):
        await self.conn.close()
        await self.admin.execute(f'DROP DATABASE "{self.database}"')
        await self.admin.close()

    async def test_real_retrieval_meets_the_policy_corpus(self):
        for case in CASES:
            with self.subTest(case=case['label']):
                rows = fixture(case)
                await self.conn.execute('TRUNCATE watchlist_entities')
                await self.conn.executemany('''INSERT INTO watchlist_entities
                    (source_type,entity_id,entity_schema,name,name_norm,dataset,raw_json) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)''',
                    [(r['source_type'],r['id'],r['schema'],r['name'],_normalize_text(r['name']),r['dataset'],json.dumps(r)) for r in rows])
                pg = await run_postgres_check(self.conn, **arguments(case))
                assert_outcome(self, pg, case)

    async def test_statement_timeout_propagates_as_incomplete(self):
        locker = await asyncpg.connect(os.environ['SANCTIONS_TEST_ADMIN_DSN'], database=self.database)
        tx = locker.transaction()
        await tx.start()
        try:
            await locker.execute('LOCK TABLE watchlist_entities IN ACCESS EXCLUSIVE MODE')
            await self.conn.execute("SET statement_timeout='20ms'")
            with self.assertRaises(asyncpg.QueryCanceledError):
                await run_postgres_check(self.conn, name='Jane Smith', dob=None, country=None, entity_type='Person')
        finally:
            await self.conn.execute('RESET statement_timeout')
            await tx.rollback()
            await locker.close()

    async def test_two_shared_tokens_retrieve_hyphenated_candidate(self):
        rows = [dict(id='primary', name='Ho Gyu Kim', schema='Person', source_type='sanctions', dataset='Fixture'),
                dict(id='other', name='Kim Tong-Ho', schema='Person', source_type='sanctions', dataset='Fixture'),
                dict(id='alias', name='Unrelated Display', aliases='Kim Tong-Ho', schema='Person', source_type='sanctions', dataset='Fixture')]
        await self.conn.executemany('''INSERT INTO watchlist_entities
            (source_type,entity_id,entity_schema,name,name_norm,dataset,raw_json) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)''',
            [(r['source_type'], r['id'], r['schema'], r['name'], _normalize_text(r['name']), r['dataset'], json.dumps(r)) for r in rows])
        args = dict(name='Ho Gyu Kim', dob=None, country=None, entity_type='Person')
        pg = await run_postgres_check(self.conn, **args)
        self.assertEqual({c['record_id'] for c in pg['Candidate Evidence']}, {'primary', 'other', 'alias'})
        with self.assertRaises(ScreeningUnavailableError):
            await perform_postgres_watchlist_check(self.conn, 'Ho Gyu Kim', None, candidate_limit=1)

    async def test_default_candidate_limit_completes_large_sets_and_still_fails_closed(self):
        async def add(start, count):
            await self.conn.executemany('''INSERT INTO watchlist_entities
                (source_type,entity_id,entity_schema,name,name_norm,dataset,raw_json) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)''',
                [('sanctions', f'row-{i}', 'Person', 'Jane Smith', 'jane smith', 'Fixture', '{}') for i in range(start, start + count)])
        await add(0, 401)
        result = await perform_postgres_watchlist_check(self.conn, 'Jane Smith', None, log_search=False)
        self.assertTrue(result['Is Sanctioned'])
        self.assertEqual(result['Candidate Evidence Summary']['potential_matches'], 401)
        await add(401, 1600)
        with self.assertRaises(ScreeningUnavailableError):
            await perform_postgres_watchlist_check(self.conn, 'Jane Smith', None, log_search=False)

    async def test_missing_postgres_sources_never_clear(self):
        with self.assertRaises(ScreeningUnavailableError):
            await run_postgres_check(self.conn, name='Jane Smith', dob=None, country=None, entity_type='Person')
