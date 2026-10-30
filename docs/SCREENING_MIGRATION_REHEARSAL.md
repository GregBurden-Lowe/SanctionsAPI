# Local history and review migration rehearsal

Completed 30 October 2026 on local PostgreSQL 16.15 (Homebrew). No production data, production schema changes, deployment, or live provider calls were involved.

## Results

| Synthetic legacy records | Migration | Repeat startup | Reference lookup | Result |
| --- | --- | --- | --- | --- |
| 10,000 | 0.528 s | 0.038 s | 0.857 ms | Passed |
| 100,000 | 6.234 s | 0.570 s | 0.796 ms | Passed |

Raw results: [10,000 records](verification/history-migration-10000.json), [100,000 records](verification/history-migration-100000.json).

Both runs verified:

- Both migration transactions can be rolled back without leaving a partial history schema.
- Every legacy entity receives exactly one subject, run and request snapshot.
- Every original current-state field is unchanged, including reviewer, notes, dates, validity and result JSON.
- Snapshot JSON and screening timestamps match the source rows exactly.
- Existing in-progress claims are bound to the preserved run.
- Migration does not invent authenticated actors, analyst decisions or review events.
- Repeating startup does not duplicate history.
- Exact business-reference history retrieval returns the expected record.
- The disposable database is removed after the rehearsal.

The full regression suite also passed: **38 tests and 24 subtests**, including HTTP history, new and reused requests, background worker completion, reviewed-result reuse, stale-run rejection and review reopening.

## Reproduce

From the repository root, with the application's Python dependencies installed and a local PostgreSQL account allowed to create databases:

```sh
SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres python scripts/rehearse_history_migration.py --rows 100000 --output /tmp/history-rehearsal.json
SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres python -m pytest tests -q
```

The runner accepts only local PostgreSQL URLs or Unix sockets. It creates a randomly named `sanctions_rehearsal_...` database and only seeds, migrates and drops that database. It never uses `DATABASE_URL`. An output file is written only after all checks and cleanup succeed. An abrupt process kill can leave the disposable database behind; review its exact name before manually removing it.

The frozen baseline schema is extracted from `screening_db.ensure_schema` at commit `09a535a2d2debeab1cf13d5a5cfffab6223fdfcd` and stored in `tests/fixtures/pre_history_schema.sql`. Fixtures mix completed false positives, claimed reviews and unreviewed matches, with roughly 2 KB of synthetic JSON padding per record. The runner applies the real migration files through the current startup function.

## Limits and next release steps

These are single-run local measurements after a rollback exercise, with warm caches and no production traffic. JSON padding is compressible; database byte counts are diagnostic only, not a production storage forecast. The tests do not measure matching throughput, production lock contention, source refresh publication, or actual production data shape. The frozen baseline is the repository baseline, not a verified copy of the deployed schema.

Before release, verify the deployed schema and rehearse on an isolated restored production backup, measure lock duration and disk/WAL headroom, and validate backup restoration. Then follow the coordinated API/worker release procedure in [Screening history release](SCREENING_HISTORY_RELEASE.md). Preserve evidence tables on rollback; do not revert to an old writer or worker against the new schema without a tested compatibility plan.

Real-world matching corpus/performance checks and the history retention policy remain separate outstanding release work. This rehearsal does not mark those complete or authorize deployment.
