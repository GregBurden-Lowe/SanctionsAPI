"""Word-level assessment, the opt-in flag and PostgreSQL retrieval for the v2 matcher."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import asyncpg

from name_matching import (MATCHER_RULES_VERSION, TokenStats, assess_organisation, assess_person, normalize_text,
                           organisation_core, refine_hits, retrieval_plan, similar_tokens, tokens)
import utils
from utils import ScreeningUnavailableError, get_best_name_matches, _normalize_text

STATS = TokenStats({'sergei': 200, 'ivanovich': 300, 'daniel': 120, 'volga': 3, 'james': 40}, 33000)


def refined(query, names, entity_type='Person', has_dob=False, remove_disagreeing=False):
    standard = get_best_name_matches(query, names, limit=len(names), threshold=75, has_dob=has_dob, entity_type=entity_type)
    return refine_hits(query, names, standard, has_dob=has_dob, entity_type=entity_type, stats=STATS,
                       remove_disagreeing=remove_disagreeing)


class TokenTests(unittest.TestCase):
    def test_normalisation_matches_the_shared_rules(self):
        for value in ('Émile "Jack" Doe', 'Sa’id Jan ‘Abd-al-Salam', 'Максим', '  A  B ', None):
            self.assertEqual(normalize_text(value), _normalize_text(value))

    def test_hyphens_separate_words_and_legal_forms_are_removed(self):
        self.assertEqual(tokens('Kim Tong-Ho'), ['kim', 'tong', 'ho'])
        self.assertEqual(organisation_core('LIMITED LIABILITY COMPANY GAAN CORPORATION'), ['gaan'])
        self.assertEqual(organisation_core('Limited Company'), ['limited', 'company'])

    def test_rare_words_weigh_more_than_common_words(self):
        self.assertGreater(STATS.weight('volga'), STATS.weight('ivanovich'))


class PersonTests(unittest.TestCase):
    def test_spelling_changes_with_an_omitted_patronymic_are_accepted(self):
        hits, added = refined('Arkadii ROTINBERG', ['Arkady Romanovich ROTENBERG'])
        self.assertEqual(added, {0})
        self.assertLess(hits[0][1], 90)

    def test_standard_hits_are_never_removed_by_default(self):
        self.assertTrue(refined('Sergei Ivanovich Thornton', ['Sergei Ivanovich SKRIBA'])[0])

    def test_optional_filter_removes_a_different_distinctive_surname(self):
        self.assertEqual(refined('Sergei Ivanovich Thornton', ['Sergei Ivanovich SKRIBA'], remove_disagreeing=True)[0], [])

    def test_a_date_of_birth_keeps_the_hit_even_with_the_filter(self):
        self.assertTrue(refined('Sergei Ivanovich Thornton', ['Sergei Ivanovich SKRIBA'], has_dob=True, remove_disagreeing=True)[0])

    def test_single_word_queries_are_not_widened(self):
        self.assertEqual(refined('Rotinberg', ['Arkady Romanovich ROTENBERG'])[1], set())

    def test_an_exact_name_is_never_lost(self):
        self.assertEqual(refined('Jane Smith', ['Jane Smith'])[0][0][1], 100)

    def test_an_added_common_name_does_not_remove_a_match(self):
        assessment = assess_person('Arkady James ROTENBERG', 'Arkady Romanovich ROTENBERG', STATS)
        self.assertLess(assessment.unexplained_share, 0.40)


class OrganisationTests(unittest.TestCase):
    def test_legal_forms_and_hyphens_do_not_block_a_match(self):
        for query, name in (('Volga Group', 'OOO Volga Group'), ('EVICOR', 'EVICOR CORPORATION LIMITED'),
                            ('NOVOSTI TV', 'TV-NOVOSTI'), ('EP IBZ', 'EP-IBZ')):
            with self.subTest(query=query):
                self.assertTrue(refined(query, [name], 'Organization')[0])

    def test_common_single_word_listing_needs_more_than_a_shared_word(self):
        self.assertEqual(refined('Daniel Marlowe', ['DANIEL'], 'Organization', remove_disagreeing=True)[0], [])

    def test_rare_distinctive_word_stays_reviewable(self):
        self.assertTrue(assess_organisation('Volga Bakery', 'Volga Shipping', STATS).rare_word_aligned)


class RetrievalPlanTests(unittest.TestCase):
    def test_initials_and_particles_are_not_required(self):
        plan = retrieval_plan('V AL PETROV', 'Person')
        self.assertEqual([plan.query_tokens[i] for i in plan.informative], ['petrov'])
        self.assertEqual(plan.required_matches, 1)

    def test_short_words_need_an_exact_match(self):
        self.assertEqual(similar_tokens('kim', ['kim', 'kin']), ['kim'])
        self.assertEqual(similar_tokens('rotinberg', ['rotenberg', 'smith']), ['rotenberg'])


class FlagTests(unittest.TestCase):
    def test_default_keeps_the_standard_rules(self):
        with patch.dict(os.environ):
            os.environ.pop('SCREENING_MATCHER_VERSION', None)
            self.assertEqual(utils.active_matcher_version(), 'v1')
            self.assertEqual(utils.rules_version_for('postgres_beta'), 'evidence-v8')

    def test_v2_applies_only_to_postgres_and_changes_the_fingerprint(self):
        args = ('Jane Smith', None, None, 'Person')
        standard = utils.screening_fingerprint(*args, 'postgres_beta')
        with patch.dict(os.environ, {'SCREENING_MATCHER_VERSION': 'v2'}):
            self.assertEqual(utils.rules_version_for('postgres_beta'), MATCHER_RULES_VERSION)
            self.assertEqual(utils.rules_version_for('worker-original'), 'evidence-v8')
            self.assertNotEqual(utils.screening_fingerprint(*args, 'postgres_beta'), standard)

    def test_an_unknown_version_stops_screening(self):
        with patch.dict(os.environ, {'SCREENING_MATCHER_VERSION': 'v3'}), self.assertRaises(ScreeningUnavailableError):
            utils.active_matcher_version()


class ImageContentsTests(unittest.TestCase):
    def test_every_local_module_the_app_imports_is_copied_into_the_image(self):
        root = Path(__file__).resolve().parents[1]
        copied = set(re.findall(r'[\w]+\.py', (root / 'Dockerfile').read_text()))
        local = {path.name for path in root.glob('*.py')}
        imported = set()
        for name in copied & local:
            for module in re.findall(r'^\s*(?:from|import)\s+(\w+)', (root / name).read_text(), re.M):
                imported.add(module + '.py')
        self.assertEqual((imported & local) - copied, set())


@unittest.skipUnless(os.getenv('SANCTIONS_TEST_ADMIN_DSN'), 'Requires disposable PostgreSQL')
class PostgresRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import screening_db
        self.admin = await asyncpg.connect(os.environ['SANCTIONS_TEST_ADMIN_DSN'])
        self.database = 'sanctions_tokens_' + uuid4().hex
        await self.admin.execute(f'CREATE DATABASE "{self.database}"')
        self.conn = await asyncpg.connect(os.environ['SANCTIONS_TEST_ADMIN_DSN'], database=self.database)
        await screening_db.ensure_schema(self.conn)
        await self.conn.execute('''CREATE TABLE watchlist_entities(source_type text, entity_id text, entity_schema text,
            name text, name_norm text, birth_date date, dataset text, regime text, raw_json jsonb)''')

    async def asyncTearDown(self):
        await self.conn.close()
        await self.admin.execute(f'DROP DATABASE "{self.database}"')
        await self.admin.close()

    async def load(self, rows):
        import pyarrow as pa
        import pyarrow.parquet as pq
        manifest = {'version': 'fixture', 'ingested_at': datetime.now(timezone.utc).isoformat(),
                    'list_counts': {k: 1 for k in ('UK', 'UN', 'EU', 'OFAC')}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'snapshot.parquet'
            table = pa.Table.from_pylist(rows)
            pq.write_table(table.replace_schema_metadata({b'screening.source_manifest.v1': json.dumps(manifest).encode()}), path)
            await utils.sync_own_watchlists_postgres(self.conn, watchlists_parquet_path=str(path))

    async def check(self, name, entity_type='Person', dob=None):
        from screening_policy import run_postgres_check
        return await run_postgres_check(self.conn, name=name, dob=dob, country=None, entity_type=entity_type, log_search=False)

    FIXTURE = [
        dict(id='person', name='Arkady Romanovich ROTENBERG', schema='Person', dataset='UK', source_type='sanctions', aliases=''),
        dict(id='greek', name='Μπάμπαρ Χαλσά', schema='Organization', dataset='EU', source_type='sanctions', aliases='Babbar Khalsa'),
        dict(id='company', name='OOO Volga Group', schema='Organization', dataset='EU', source_type='sanctions', aliases=''),
    ]

    async def test_import_builds_the_index_and_v2_finds_what_v1_misses(self):
        await self.load(self.FIXTURE)
        self.assertEqual(await self.conn.fetchval('SELECT doc_frequency FROM watchlist_tokens WHERE token=$1', 'rotenberg'), 1)
        self.assertFalse((await self.check('Arkadii ROTINBERG'))['Match Found'])
        with patch.dict(os.environ, {'SCREENING_MATCHER_VERSION': 'v2'}):
            found = await self.check('Arkadii ROTINBERG')
            self.assertEqual(found['Candidate Evidence'][0]['record_id'], 'person')
            self.assertEqual(found['Candidate Evidence'][0]['match_rule'], 'token_alignment')
            self.assertTrue(any('word by word' in reason for reason in found['Candidate Evidence'][0]['reasons']))
            for name, entity_type, expected in (('Babbar Khalsa', 'Organization', 'greek'), ('Volga Group', 'Organization', 'company')):
                result = await self.check(name, entity_type)
                self.assertIn(expected, [item['record_id'] for item in result['Candidate Evidence']])

    async def test_whole_name_similarity_retrieves_a_shorter_listed_name(self):
        short = dict(self.FIXTURE[2], id='short', name='Zorbex')
        alias = dict(self.FIXTURE[2], id='alias', name='Unrelated Display', aliases='Zorbex Marlow')
        await self.load(self.FIXTURE + [short, alias])
        rows, _ = await utils._retrieve_candidates_v2(self.conn, 'Zorbex Marlowe', 'Organization', None, 2000)
        self.assertEqual({row['entity_id'] for row in rows}, {'short', 'alias'})

    async def test_two_shared_words_retrieve_a_person_even_when_both_are_particles(self):
        listed = dict(self.FIXTURE[0], id='particles', name='Abd Al Kareem')
        await self.load(self.FIXTURE + [listed])
        rows, _ = await utils._retrieve_candidates_v2(self.conn, 'Abd Al Thornton', 'Person', None, 2000)
        self.assertIn('particles', {row['entity_id'] for row in rows})

    async def test_word_matches_beyond_the_limit_do_not_refuse_the_search(self):
        await self.load(self.FIXTURE)
        await self.conn.execute("""INSERT INTO watchlist_name_tokens
            SELECT 'zorbexian','organization','extra-'||g FROM generate_series(1,2001) g""")
        await self.conn.execute("INSERT INTO watchlist_tokens VALUES('zorbexian',2001)")
        rows, _ = await utils._retrieve_candidates_v2(self.conn, 'Zorbexian', 'Organization', None, 2000)
        self.assertEqual(rows, [])

    async def test_a_reload_replaces_the_index(self):
        await self.load(self.FIXTURE)
        await self.load(self.FIXTURE[:1])
        self.assertEqual(await self.conn.fetchval("SELECT count(*) FROM watchlist_name_tokens WHERE entity_id='company'"), 0)

    async def test_an_empty_index_refuses_rather_than_clears(self):
        await self.load(self.FIXTURE)
        await self.conn.execute('TRUNCATE watchlist_name_tokens, watchlist_tokens, watchlist_names')
        with patch.dict(os.environ, {'SCREENING_MATCHER_VERSION': 'v2'}), self.assertRaises(ScreeningUnavailableError):
            await self.check('Arkady ROTENBERG')

    async def test_too_many_candidates_still_refuses(self):
        await self.load(self.FIXTURE)
        await self.conn.execute("INSERT INTO watchlist_names SELECT 'volga','organization','extra-'||g||'' FROM generate_series(1,2001) g ON CONFLICT DO NOTHING")
        await self.conn.execute("INSERT INTO watchlist_names SELECT 'volga '||g,'organization','extra-'||g FROM generate_series(1,2001) g")
        with patch.dict(os.environ, {'SCREENING_MATCHER_VERSION': 'v2'}), self.assertRaises(ScreeningUnavailableError):
            await utils.perform_postgres_watchlist_check(self.conn, 'Volga', None, entity_type='Organization', log_search=False)

    async def test_non_latin_input_gets_an_honest_refusal_under_v2(self):
        await self.load(self.FIXTURE)
        with patch.dict(os.environ, {'SCREENING_MATCHER_VERSION': 'v2'}), self.assertRaisesRegex(ScreeningUnavailableError, 'supported letters'):
            await self.check('Максим')
