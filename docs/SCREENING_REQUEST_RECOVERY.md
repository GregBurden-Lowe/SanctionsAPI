# Duplicate submission protection and synchronous request recovery

Implemented locally on 30 October 2026. No production migration or deployment.

## Submission contract

`POST /opcheck` and `POST /opcheck/dataverse` accept an optional `Idempotency-Key` header (1–128 visible ASCII characters). The key is scoped to the authenticated actor and endpoint. Clients must retain the same key and identical inputs when retrying an uncertain transport outcome; a new intentional check needs a new key.

- First submission creates one durable request and executes normally.
- Same key and inputs while running returns HTTP 202 with the existing request ID and a history Location header. It does not start another execution.
- Once finished, the key returns the saved response and its HTTP status, including failures. It does not recalculate against newer lists or reviews.
- Same key with changed inputs returns HTTP 409.
- Different actors/endpoints have independent keys. Submissions without keys remain separate requests.
- A queued job retains its request/job IDs on replay; queued work continues under worker leases.

Keys are retained with request history and have no independent expiry. Purging or archiving them needs a retention design that preserves the intended deduplication period. Bulk/internal ingestion and review-rerun endpoints do not gain transport idempotency in this stage.

## Expiry and atomic publication

Synchronous submissions receive an absolute deadline: `SCREENING_REQUEST_TIMEOUT_SECONDS`, default 600, minimum 30. This is a completion budget, not an immediate process-death detector. A healthy API instance sweeps up to 100 expired synchronous requests every 30 seconds and at startup; new submissions and history-detail reads also run recovery. PostgreSQL must be available. Larger backlogs can take multiple batches.

Expired requests become failed, with `request_interrupted`, no clearance and an instruction to create a linked retry. Recovery does not touch queued jobs. A still-running computation that exceeds its budget is treated as incomplete and cannot publish a late result.

The request row is locked and its state/deadline checked before a new current-state/run write or job attachment. New synchronous engine results and their successful request responses commit in the same transaction. Thus a process crash after the commit but before HTTP delivery leaves a replayable completed record rather than an orphaned request. Cached responses are finalized under the same state/deadline check. Terminal request responses cannot be overwritten by the normal completion path.

Parquet screening runs in a thread so it does not block the API event loop and its recovery task. Expiry does not forcibly terminate that thread; the database publication check rejects late output. Existing legacy CSV logging is a compatibility log, not a successful-response ledger; it can contain attempted work that subsequently fails database publication. The durable request/run records determine completion.

## Retry journey

Failed history records offer **Review inputs and retry**. The form restores the screening inputs, links back to the original record, and asks the user to review and submit. The new request supplies `retry_of` and a fresh submission key; the failed record is preserved. Only the authenticated actor who owns the failed request may create that link. Inputs may be corrected before retrying, and both versions remain recorded.

The form blocks overlapping clicks and retains one pending key in tab-scoped session storage after an uncertain network response. Submitting identical inputs again in that tab reuses it. Changed inputs get a new key. Once a recorded response is received, the pending key is cleared. This does not deduplicate independently initiated submissions across tabs/devices.

## Migration and verification

Migration `004_request_recovery.sql` adds key/hash/status/deadline/retry-link fields and indexes. Existing unqueued pending records receive a one-time ten-minute grace period. Deploy all API instances together with the migration; old instances do not enforce the new publication checks. Follow the coordinated worker/API release and restored-backup rehearsal requirements in [release procedure](SCREENING_HISTORY_RELEASE.md).

Verification: **62 tests and 82 subtests passed**, frontend build passed, and a local fixture browser check verified the failed-record link, restored inputs, parent request ID and submission header. PostgreSQL tests cover concurrent and in-flight duplicates, actor/route isolation, input conflicts, exact failure replay, linked retries and ownership, expiry, rejection of late entity/job writes, queue exclusion, and atomic run/request publication.

All four migrations passed the 10,000-record synthetic rollback/preservation/reentry rehearsal: [raw results](verification/request-recovery-migration-10000.json). This does not replace production-backup rehearsal or establish real-world matching accuracy.

```sh
SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres python -m pytest tests -q
SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres python scripts/rehearse_history_migration.py --rows 10000
npm run build --prefix frontend
```
