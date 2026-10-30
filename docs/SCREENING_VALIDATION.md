# Matching, consistency, performance and failure validation

Local validation on 30 October 2026. No production data, provider calls, deployment or production migration.

## Changes and expected outcomes

Interactive and background searches now share the person/organisation and PEP policy in `screening_policy.py`. Both types receive sanctions checks, even when an input was submitted under the wrong type. PEP eligibility follows the existing interactive policy. Country evidence is retained for people as well as organisations; country conflict alone cannot exclude a match. Person hyphens now separate tokens, fixing a missed `Jane Smith Jones` / `Jane Smith-Jones` candidate. Original entity keys remain unchanged.

`evidence-v3` invalidates cached results from the previous rules. Existing historical snapshots remain immutable. API and worker must be deployed together; increased worker coverage can increase runtime and the number of review candidates. Backend fingerprints remain separate, so this change does not enable cross-backend cache reuse.

## Corpus and failure tests

The original 14 name cases are supplemented by 28 policy scenarios: listed and accented aliases, initials, reordered and hyphenated names, apostrophes, typos, middle names, negative controls, company suffixes, generic business words, partial/missing/invalid/conflicting DOB, country evidence, incorrect entity types and PEP skipping.

Each policy scenario checks an expected decision through real PostgreSQL retrieval and the in-memory Parquet evaluator. Tests compare candidate evidence, aggregate counts, type results, PEP processing and input classification. A real worker execution checks that an organisation submitted as a person is still found, with the same evidence as interactive matching.

Failure tests cover:

- Missing required sources: incomplete/failure, never a clearance.
- A real PostgreSQL statement timeout caused by a held table lock: propagated as an incomplete execution.
- API timeout handling: HTTP 503, failed request history, no saved successful run.
- Worker missing-source and timeout exceptions: failed job/request, no successful run or result.
- Worker interruption after claiming: running job and pending request, with no result returned.
- Existing candidate-cap overflow and failed persistence tests remain in place.

Subsequent work adds [lease/heartbeat-based recovery](SCREENING_JOB_RECOVERY.md) for interrupted queued jobs. The failure results below describe the initial interruption state before that recovery runs. Fault injection verifies failure handling; it does not establish a wall-clock timeout for every in-memory scan.

The full suite passes **46 tests plus 82 subtests**. These are synthetic regression checks, not measured real-world precision or recall. Historical investigator-labelled examples and multilingual/transliteration acceptance still require a representative operational corpus.

## 100,000-row benchmark

Local PostgreSQL 16.15, four query shapes, three executions each. The table contains mixed person/organisation and sanctions/PEP rows, generated aliases and two known targets. PostgreSQL has a trigram name index and a source/type index. The Parquet measurement starts with an already-loaded DataFrame and excludes file load time.

| Search | PostgreSQL median | In-memory Parquet median |
| --- | ---: | ---: |
| Exact name | 341 ms | 4,098 ms |
| Alias | 332 ms | 4,132 ms |
| No match | 344 ms | 4,200 ms |
| Company | 325 ms | 4,041 ms |

Four concurrent PostgreSQL alias searches: **368 ms median**, **383 ms maximum**, **0.383 seconds total wall time**. All benchmark decisions and candidate evidence agreed between backends. [Raw results](verification/search-benchmark-100000.json).

The measurements favour PostgreSQL for interactive use. Parquet scans and alias retrieval remain scaling concerns. This is not a production SLA or a sustained load test: generated names do not reproduce common-name density, large alias lists, production hardware, cold caches or concurrent ingestion. The four-query load sample is too small for a meaningful p95/p99 claim. Candidate overflow deliberately fails incomplete; it must not be treated as a fast successful search.

## Reproduce

Install application dependencies and use a local PostgreSQL account permitted to create databases:

```sh
SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres python -m pytest tests -q
SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres python scripts/benchmark_screening.py --rows 100000 --repeats 3 --concurrency 4 --output /tmp/search-benchmark.json
```

The benchmark accepts local PostgreSQL URLs/Unix sockets only. It creates and drops its own random `sanctions_benchmark_...` database and never uses `DATABASE_URL`. Existing application databases are not modified. The two backends retain different candidate retrieval methods; matching on this corpus does not guarantee parity for all names.

Next release work: investigator-labelled accuracy acceptance, production-shaped volume/latency checks, retention policy, and restored-backup migration rehearsal before a coordinated release.

Subsequent work adds [synchronous request recovery and idempotent retries](SCREENING_REQUEST_RECOVERY.md).
