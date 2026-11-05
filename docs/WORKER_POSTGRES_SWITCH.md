# Moving the worker to PostgreSQL

`SCREENING_WORKER_BACKEND` selects the background screening route: `parquet` (default) or `postgres`. Any other value stops the worker. The API reads the same setting to match queued jobs and saved results, so **the app and the worker must always use the same value**. Both read it from the shared `.env`.

With `postgres`, the worker uses the same retrieval and manifest as interactive screening, and records `postgres_beta` as the backend. It does not read Parquet to screen. The monitoring cycle also uses the PostgreSQL snapshot. Parquet is still written by the refresh, because it remains the snapshot format.

## Before switching

- Get Compliance acceptance. This is a change of screening route.
- Confirm the refresh produces matching row counts in Parquet and PostgreSQL.
- Measure the sustained load on the managed database. Each check is far heavier there than today, and a full cycle runs one check per screened subject.
- Let the current look-back finish, or accept that its remaining jobs finish on the new route.

## Switching

Set the value in `.env`, then recreate the app and worker together so they never disagree. No schema change is needed.

Results from the two routes have different fingerprints, so existing worker results are not reused after the switch. The next monitoring cycle re-screens them.

## Rolling back

Set the value back to `parquet` and recreate both services. Results produced on PostgreSQL stay valid as evidence, but they are not reused by the Parquet route.

## Known behaviour

Searches above the 2,000 candidate limit fail, and the job is marked failed. No clearance is issued, but these jobs need attention.
