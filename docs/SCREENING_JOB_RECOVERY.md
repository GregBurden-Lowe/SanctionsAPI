# Interrupted screening-job recovery

Implemented and verified locally on 30 October 2026. No production migration or deployment performed.

## Behaviour

Each claimed job receives a unique lease token, an expiry time and an incremented attempt count. A separate database connection renews the lease while screening runs. Every healthy worker checks expired jobs before claiming new work. Recovery uses row locks and skips locked rows so workers do not compete over the same recovery transaction.

An expired running job returns to pending without creating a new job or request. Existing business references and attached request IDs are preserved. On reaching the attempt limit, the job becomes failed; the existing completion trigger marks all attached queued requests failed. No result or clearance is issued by recovery itself. Ordinary matcher/source failures remain failed rather than being automatically retried.

Before writing either a new result or a reused result, a worker locks the job row and verifies its token and unexpired lease. That lock is held in the same transaction as publication. A worker that resumes after takeover cannot overwrite current screening state, complete the job or fail the replacement attempt. The worker's compatibility CSV append occurs only after successful database publication.

`screening_job_events` preserves immutable claim, retry and terminal-state events. Heartbeat renewals do not create audit noise. Events survive the existing job-row retention cleanup. Job status responses include `attempt_count`; lease tokens are not exposed.

## Settings

Configure all worker instances consistently:

| Setting | Default | Behaviour |
| --- | --- | --- |
| `SCREENING_JOB_LEASE_SECONDS` | 60 | Minimum 15 seconds; heartbeat runs every one-third of the lease duration. |
| `SCREENING_JOB_MAX_RUNTIME_SECONDS` | 600 | Maximum attempt lifetime maintained by heartbeat; must be at least the lease duration. |
| `SCREENING_JOB_MAX_ATTEMPTS` | 3 | Total claims, including the first attempt; minimum 1. |

Heartbeat connection timeout and statement timeout are each three seconds. A failed heartbeat stops renewal; publication still requires a valid lease. Once the maximum runtime is reached, renewal stops and the job can be reclaimed. This fences the result; it does not forcibly terminate a hung Python computation. Recovery requires a healthy polling worker and available PostgreSQL. A hung sole worker still needs its process supervisor to restart it, or another worker to recover its jobs.

Long legitimate searches may exceed the default runtime. Choose the budget from production-volume measurements. The local synthetic benchmark does not establish that ten minutes suits every production workload.

## Migration and rollout

`003_job_recovery.sql` is applied after 001 and 002 by startup. Existing running jobs without leases receive a one-time five-minute grace period. Repeated startup does not extend that period. They become retryable after expiry.

**Stop all old workers before migration and release the API/new workers together.** Old workers do not honour lease tokens; mixing them with recovering workers can permit stale publication. Follow the backup and restored-copy rehearsal requirements in [release procedure](SCREENING_HISTORY_RELEASE.md). Preserve new event data on rollback. No production rollout is implied by the local verification.

This handles queued worker jobs. Synchronous API requests now have a separate [deadline recovery and duplicate protection policy](SCREENING_REQUEST_RECOVERY.md); do not infer successful clearance from a pending request.

## Verification

Full suite: **54 tests plus 82 subtests passed**. PostgreSQL integration tests cover:

- Active leases are left alone and real heartbeat renewal extends expiry.
- Expired leases are retried and old tokens cannot renew or publish.
- A publisher's row lock prevents competing recovery.
- Three interrupted attempts produce terminal failure for all attached requests.
- Attempt runtime limits stop heartbeat renewal.
- An interrupted real worker restarts and completes the original job/request with one saved run.
- A worker that finishes after takeover saves no result or current entity.
- Legacy running jobs receive one grace period, preserved on schema reentry.
- Recovery events cannot be deleted or rewritten.

All three migrations also passed the 10,000 synthetic-record rollback/preservation/reentry rehearsal: [raw results](verification/job-recovery-migration-10000.json). The original 100,000-row rehearsal predates migration 003; it is not evidence of 003 at that scale.

```sh
SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres python -m pytest tests -q
SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres python scripts/rehearse_history_migration.py --rows 10000
```

No frontend behaviour was changed. The existing pending-history polling follows the original request through retry to completion or failure.
