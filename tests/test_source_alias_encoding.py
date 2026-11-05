"""Round-trip source aliases and preserve the published snapshot on failure."""
import tempfile
import unittest
import os
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pandas as pd

from scripts.alias_encoding import encode_aliases
from scripts.fetch_un_sanctions import _parse_individual
from source_assurance import build_own_manifest
from utils import _parse_aliases, refresh_opensanctions_data, ScreeningUnavailableError


class SourceAliasEncodingTests(unittest.TestCase):
    def test_aliases_round_trip_quotes_semicolons_and_unicode(self):
        values = ['"ZAPCHAST TRADE" LLP', 'Name; with semicolon', 'Émile "Jack" Doe']
        self.assertEqual(_parse_aliases(encode_aliases(values)), values)
        self.assertEqual(_parse_aliases(encode_aliases([])), [])

    def test_un_source_parser_encodes_each_alias(self):
        from defusedxml import ElementTree as ET
        element = ET.fromstring('<INDIVIDUAL><FIRST_NAME>Jane</FIRST_NAME>'
                                '<INDIVIDUAL_ALIAS><ALIAS_NAME>Jane &quot;Jack&quot;; Smith</ALIAS_NAME>'
                                '</INDIVIDUAL_ALIAS></INDIVIDUAL>')
        self.assertEqual(_parse_aliases(_parse_individual(element)['aliases']), ['Jane "Jack"; Smith'])

    def test_malformed_refresh_does_not_replace_published_file(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        self.addCleanup(sys.path.remove, str(Path(__file__).resolve().parents[1] / 'scripts'))
        import fetch_all_watchlists
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'watchlists.parquet'
            target.write_bytes(b'original snapshot')
            data = pd.DataFrame([{'name': 'Jane', 'aliases': '"bad;alias', 'source_type': 'sanctions'}])
            with patch('utils.DATA_DIR', folder), patch('utils.OWN_PARQUET', str(target)), \
                 patch.object(fetch_all_watchlists, 'fetch_all', return_value=data):
                with self.assertRaises(ScreeningUnavailableError):
                    refresh_opensanctions_data()
            self.assertEqual(target.read_bytes(), b'original snapshot')

    def test_valid_refresh_publishes_aliases_and_manifest(self):
        import sys
        from source_assurance import parquet_manifest
        scripts_dir = str(Path(__file__).resolve().parents[1] / 'scripts')
        sys.path.insert(0, scripts_dir)
        self.addCleanup(sys.path.remove, scripts_dir)
        import fetch_all_watchlists
        data = pd.DataFrame([{'name': 'Jane', 'aliases': encode_aliases(['"Jane; Smith"']),
                              'source_type': 'sanctions', 'dataset': label}
                             for label in ('UK Sanctions List', 'UN Security Council', 'EU Consolidated', 'OFAC')])
        def fetch(*, include_peps, output_path):
            data.to_parquet(output_path, index=False)
            return data
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'watchlists.parquet'
            with patch('utils.DATA_DIR', folder), patch('utils.OWN_PARQUET', str(target)), \
                 patch.object(fetch_all_watchlists, 'fetch_all', side_effect=fetch):
                refresh_opensanctions_data()
            self.assertTrue(parquet_manifest(str(target))['version'])
            self.assertEqual(_parse_aliases(pd.read_parquet(target).iloc[0]['aliases']), ['"Jane; Smith"'])

    def test_source_version_changes_when_alias_changes_without_count_change(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.parquet'
            def manifest(alias):
                pd.DataFrame([{'name': 'Jane', 'aliases': alias, 'dataset': 'UK Sanctions List',
                               'source_type': 'sanctions'}]).to_parquet(path, index=False)
                return build_own_manifest(str(path))
            first, second = manifest('Jane A'), manifest('Jane B')
            self.assertEqual(first['list_counts'], second['list_counts'])
            self.assertNotEqual(first['version'], second['version'])


@unittest.skipUnless(os.getenv('SANCTIONS_TEST_ADMIN_DSN'), 'Requires isolated local PostgreSQL')
class SourceImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_preserves_encoded_alias_and_published_version(self):
        import asyncpg
        import pyarrow as pa
        import pyarrow.parquet as pq
        from scripts.rehearse_history_migration import local_dsn
        from utils import sync_own_watchlists_postgres

        dsn = local_dsn(os.environ['SANCTIONS_TEST_ADMIN_DSN'])
        admin = await asyncpg.connect(dsn)
        database = 'sanctions_alias_' + uuid4().hex
        conn = None
        created = False
        try:
            await admin.execute(f'CREATE DATABASE "{database}"')
            created = True
            conn = await asyncpg.connect(dsn, database=database)
            await conn.execute('''CREATE TABLE watchlist_entities(source_type text, entity_id text,
                entity_schema text, name text, name_norm text, birth_date date, dataset text,
                regime text, raw_json jsonb)''')
            source = '"Jane; Jack" Smith'
            row = {'id': 'fixture', 'name': 'Jane Smith', 'schema': 'Person', 'dataset': 'UK',
                   'source_type': 'sanctions', 'aliases': encode_aliases([source])}
            manifest = {'version': 'published-version', 'ingested_at': datetime.now(timezone.utc).isoformat(),
                        'list_counts': {k: 1 for k in ('UK', 'UN', 'EU', 'OFAC')}}
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'snapshot.parquet'
                table = pa.Table.from_pylist([row])
                table = table.replace_schema_metadata({b'screening.source_manifest.v1': json.dumps(manifest).encode()})
                pq.write_table(table, path)
                await sync_own_watchlists_postgres(conn, watchlists_parquet_path=str(path))
                raw = json.loads(await conn.fetchval('SELECT raw_json FROM watchlist_entities'))
                self.assertEqual(_parse_aliases(raw['aliases']), [source])
                saved = json.loads(await conn.fetchval('SELECT manifest FROM watchlist_source_state WHERE singleton'))
                self.assertEqual(saved['version'], manifest['version'])
        finally:
            if conn:
                await conn.close()
            if created:
                await admin.execute(f'DROP DATABASE "{database}"')
            await admin.close()

    async def test_non_latin_primary_name_stays_searchable_by_alias(self):
        import asyncpg
        import pyarrow as pa
        import pyarrow.parquet as pq
        from scripts.rehearse_history_migration import local_dsn
        from screening_policy import run_postgres_check
        from utils import sync_own_watchlists_postgres

        dsn = local_dsn(os.environ['SANCTIONS_TEST_ADMIN_DSN'])
        admin = await asyncpg.connect(dsn)
        database = 'sanctions_alias_' + uuid4().hex
        conn = None
        created = False
        try:
            await admin.execute(f'CREATE DATABASE "{database}"')
            created = True
            conn = await asyncpg.connect(dsn, database=database)
            await conn.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
            await conn.execute('''CREATE TABLE watchlist_entities(source_type text, entity_id text,
                entity_schema text, name text, name_norm text, birth_date date, dataset text,
                regime text, raw_json jsonb)''')
            rows = [{'id': 'greek', 'name': 'Μπάμπαρ Χαλσά', 'schema': 'Organization', 'dataset': 'EU',
                     'source_type': 'sanctions', 'aliases': encode_aliases(['Babbar Khalsa'])},
                    {'id': 'cyrillic', 'name': 'Максим Решетников', 'schema': 'Person', 'dataset': 'EU',
                     'source_type': 'sanctions', 'aliases': encode_aliases(['Maxim Reshetnikov'])}]
            manifest = {'version': 'published-version', 'ingested_at': datetime.now(timezone.utc).isoformat(),
                        'list_counts': {k: 1 for k in ('UK', 'UN', 'EU', 'OFAC')}}
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'snapshot.parquet'
                table = pa.Table.from_pylist(rows)
                table = table.replace_schema_metadata({b'screening.source_manifest.v1': json.dumps(manifest).encode()})
                pq.write_table(table, path)
                counts = await sync_own_watchlists_postgres(conn, watchlists_parquet_path=str(path))
            self.assertEqual(counts['sanctions'], 2)
            for name, entity_type, record_id in (('Babbar Khalsa', 'Organization', 'greek'),
                                                 ('Maxim Reshetnikov', 'Person', 'cyrillic')):
                result = await run_postgres_check(conn, name=name, dob=None, country=None, entity_type=entity_type)
                self.assertTrue(result['Is Sanctioned'], name)
                self.assertIn(record_id, [item['record_id'] for item in result['Candidate Evidence']])
        finally:
            if conn:
                await conn.close()
            if created:
                await admin.execute(f'DROP DATABASE "{database}"')
            await admin.close()
