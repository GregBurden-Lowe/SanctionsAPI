# Local release review — 30 October 2026

Status: local checks pass, but production release is not signed off. No commit, push, deployment, production migration or live-provider check was performed.

## Scope

Reviewed request recording and replay, database snapshot/review triggers, synchronous publication, worker lease fencing and recovery, shared matching policy, history and retention UI, cleanup paths and Docker packaging. This is a code and local integration review, not an independent security audit or real-world matching accuracy assessment.

## Confirmed defects fixed

1. **High: testing cleanup could strand queued requests.** Truncating jobs left saved requests queued indefinitely because request recovery intentionally excludes queued work. The admin clear action now atomically checks for unfinished requests, active jobs and open reviews before clearing. It takes non-waiting exclusive locks on the affected tables to prevent a submission/claim racing the check; contention or active work returns HTTP 409 without deletion. External-review cases are protected even when their legacy workflow status says COMPLETED. UI wording describes the guard.
2. **Medium: terminal queued replays omitted the request ID.** Job triggers store a common result for attached requests, so the stored body does not necessarily contain an individual request ID. Idempotent response replay now adds the replaying request's own ID without changing saved evidence.

Regression tests exercise queued-work rejection, claimed/unreviewed/external reviews, concurrent database use, unfinished synchronous work, allowed clearing with saved history preserved, and terminal queued replay identity.

## Outstanding release conditions

- **High, conditional: age-based current-state cleanup is not review-aware.** `screening_worker.py` and `screening_db.purge_screened_entities_older_than` delete by screening age without protecting open reviews or active jobs. Keep `SCREENED_ENTITIES_RETENTION_MONTHS=0` until an approved cleanup design is implemented. No deployed setting was inspected or changed. Completed legacy review metadata may exist only in current state, so the testing clear action must not be treated as a production retention tool either.
- **Source provenance limitation:** matching and refresh metadata publication are not one versioned operation. Interactive paths obtain latest refresh metadata after matching; workers can attach a queued refresh ID while reading the currently loaded Parquet data. A refresh during execution can therefore make that ID an unreliable identifier of the exact source content. The existing release documentation already defers atomic dataset/version publication. Exact reproducibility requires that follow-up; current snapshots must not be represented as an exact source archive.
- **Policy:** the organisation's existing retention policy is still awaited. Preview is read-only; there is no approved historical evidence deletion operation.
- **Accuracy acceptance:** synthetic fixtures and retrieval parity tests do not establish production recall. Investigator-labelled real-world examples, including common names, aliases and multilingual cases, remain necessary.
- **Migration and rollout:** rehearse migrations 001–004 on an isolated restored production backup. Existing synthetic rehearsals do not establish production lock time, disk growth or compatibility. Stop old writers and release all API/worker instances together; old workers do not honour lease fencing.

## Verification

- `SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres /tmp/sanctions-history-venv/bin/python -m pytest tests -q`: **68 tests and 82 subtests passed**, using disposable local databases.
- `npm run build --prefix frontend`: passed; existing Browserslist-age and bundle-size warnings remain.
- `git diff --check`: passed.
- Test runtime was local Python 3.14; Docker targets Python 3.12. The production Docker image was not built or executed in this review.
- No new browser walkthrough was performed for this review; changed UI text was type-checked and built. Prior-stage browser checks remain separate evidence.

The next technical follow-up is binding executions to exact watchlist versions, followed by the restored-backup rehearsal and real-world acceptance checks. Retention implementation depends on the supplied policy.
