# Screening history foundation

This release adds durable request history and execution snapshots without replacing the current-state API. It does not implement the full matching/review redesign.

## Data model

- `screening_subjects`: stable UUID per existing entity key. This is a technical grouping, **not** verified real-world identity resolution. Person/company identifiers and explicit merge/link workflows remain future work.
- `screening_requests`: original submitted inputs, business reference, authenticated actor when available, source, request time, status and link to a run/job. Cached reuse creates another request. Background/internal requests do not invent a human authenticated actor.
- `screening_runs`: immutable inputs/results, screening timestamp, available refresh metadata, rules version and matching configuration. PostgreSQL triggers capture API and worker writes in the same transaction as current state. Manual changes are labelled `manual_decision`; existing records become `legacy_snapshot`.
- `screening_review_events`: append-only evidence of review state changes and manual overrides. Existing review state is not reconstructed into historical events.
- `screened_entities.current_run_id` and `screening_jobs.run_id` link to the exact snapshot. Completed jobs no longer read the latest entity result. Pre-migration completed jobs without an execution snapshot return an explicit `result_unavailable` flag; their historical result is not fabricated.

Legacy snapshots preserve only what survives in the current table. Their timestamp is the existing screening timestamp; their source label explicitly states they are not reconstructed requests. Overwritten references, original inputs and actors cannot be recovered by this migration.

## User journey and API

`/history` lists requests with exact business-reference filtering and pagination. `/history/{request_id}` loads saved inputs/results directly and polls pending requests. The normal result page links to this permanent record. The existing Search database page also recognises references recorded by historical requests, but continues to show **current** entity state; use History for evidence at the time of a request.

Authenticated endpoints:

- `GET /opcheck/history?business_reference=...&limit=25&offset=0`
- `GET /opcheck/history/{request_id}`

Existing successful `/opcheck` and `/opcheck/dataverse` fields remain; responses additionally include `request_id`, `run_id`, `screened_at`, and `reused`. Queue responses include request and job IDs. Internal ingestion retains status-only responses with additive IDs.

Database recording is now required for normal screening. An unavailable database, failed save, or unavailable required dataset must not issue a successful clearance. Structurally invalid requests rejected by FastAPI before handler invocation are not ledger entries. Failures after ledger creation are recorded where the database remains available. The synchronous endpoints now support actor/endpoint-scoped idempotency keys and deadline-based abandoned-request recovery; see [submission and retry contract](SCREENING_REQUEST_RECOVERY.md). Queued worker jobs have bounded lease-based recovery; see [job recovery](SCREENING_JOB_RECOVERY.md).

## Cache safeguards

Reuse requires matching normalised full name, DOB, country, entity type, backend, matching configuration and rules version, plus the latest recorded refresh-run ID. Legacy results without a fingerprint are not automatically reused. Reuse does not overwrite the original requestor/reference/country. Changing an input can create a new run under the existing technical entity key.

Interactive and worker paths now share the dual-type execution policy described in the current decision rules. Backend fingerprints remain separate conservatively. The `evidence-v3` version invalidates results from the earlier worker policy and earlier name handling.

This conservative invalidation means every newly recorded refresh can increase screening workload, even if the lists are unchanged. Assess queue capacity before release. Full per-dataset content versions and atomic publication of watchlists with refresh metadata remain future work; the stored refresh metadata must not be described as a complete reproducible source archive.

## Migration and deployment

`migrations/001_screening_history.sql` is additive and idempotent and is called at the end of the existing `ensure_schema` startup process. It is included in the Docker image. No production migration has been applied as part of development.

Before deployment:

1. Back up the database and rehearse against a production-sized copy. Backfill takes table locks and adds a full JSON snapshot per current record.
2. Pause screening writers/workers while applying the schema and releasing the API and worker together. Existing workers do not supply immutable job-result IDs or compatible fingerprints.
3. Verify legacy counts, a new request, a reused request under a different reference, a queued job, a rerun and both history endpoints.
4. Resume workers and monitor errors and increased cache misses.

Rollback should stop writers and preserve the new history tables. Reverting application code alone loses new guarantees; do not silently drop history as a rollback step. Existing cache/job clearing and retention do **not** delete historical evidence. A separate, authorised retention policy and tooling for the new tables is required before production rollout.

## Verification

Run unit tests with `python -m pytest tests -q`.

PostgreSQL integration tests are opt-in:

```
SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres python -m pytest tests -q
```

They create randomly named disposable databases, apply and reapply the schema, exercise real triggers and the real synchronous worker, and drop those databases. Never point the admin DSN at production. The tests cover separate references, cache compatibility, failure recording, immutable evidence, job completion/reuse, legacy backfill, concurrent upserts and HTTP history retrieval.

Frontend: `npm run build` from `frontend/`.

## Subsequent matching stage

The local `evidence-v2` update supersedes this document's original statement that DOB/country rules were unchanged. See [current decision rules](SCREENING_DECISION_RULES.md). Existing immutable history remains preserved; older cache fingerprints are no longer reused.

## Separate review decisions

Migration `002_review_decisions.sql` runs after 001 at startup. Release the API and worker together after a database backup and migration rehearsal. No production migration or deployment has been performed during this local implementation.

New analyst decisions are immutable records tied to one screening run. False-positive resolution preserves the raw engine result, similarity, screening date, validity and source provenance. Current API responses retain the effective cleared status for compatibility and expose `Engine Result` and `Review Decision` separately. History preserves the response originally returned and lists subsequent decisions separately. Previously overwritten legacy engine evidence cannot be restored.

The normal review action requires ownership of the claim and the same run that was claimed. Admin and AI-assisted false-positive actions require an explicit expected run ID; stale or already-resolved runs return a conflict. Existing AI recommendations without that run ID must be regenerated. A no-match rerun is recorded as a system resolution, not an analyst false-positive decision.

Reopening is deliberately conservative: every fresh screening resets the current review; a fresh potential match enters the unreviewed queue even if its candidates look unchanged. Cached reuse of the exact run retains its decision, including worker responses. Changed watchlist hashes invalidate false-positive clearance; preserved engine flags are restored for new-format decisions and a fresh screening is required before another decision. Historical decisions and responses remain unchanged.

This stage does not add attachments, reassignment or a dedicated awaiting-information state. Existing review outcome choices remain. Test coverage includes immutable decisions, unchanged engine timestamps, stale-run conflicts, claim ownership, reopening, system resolutions, stale clearance and reviewed worker reuse.

## Local migration rehearsal

Both migrations passed on 10,000 and 100,000 synthetic legacy records, including rollback, exact evidence preservation and startup reentry. See [results and limits](SCREENING_MIGRATION_REHEARSAL.md). This does not replace rehearsal on an isolated restored production backup.

## Matching and failure validation

See [matching corpus, benchmark and failure results](SCREENING_VALIDATION.md). Shared worker/API type policy and person hyphen handling are implemented locally; real-world accuracy acceptance, production-scale validation remain distinct follow-ups. Synchronous requests now use [deadline recovery and linked retries](SCREENING_REQUEST_RECOVERY.md). Queued interrupted jobs now use [lease-based recovery](SCREENING_JOB_RECOVERY.md).

## Job recovery migration

Migration 003 adds worker leases and immutable job events. Stop old workers before upgrading; they cannot safely coexist with recovering workers. See [configuration and verified behaviour](SCREENING_JOB_RECOVERY.md).

## Submission recovery migration

Migration 004 adds request idempotency and synchronous recovery. Upgrade all API instances together so expired-request publication checks apply consistently. See [request recovery](SCREENING_REQUEST_RECOVERY.md).

## Retention policy preview

The [admin retention preview](SCREENING_RETENTION_PREVIEW.md) now reports inventory and overlapping dependencies without changing data. The user will supply the existing organisational policy; no retention period or evidence deletion has been enabled. Existing operational cleanup remains separate and must be reviewed against that policy.
