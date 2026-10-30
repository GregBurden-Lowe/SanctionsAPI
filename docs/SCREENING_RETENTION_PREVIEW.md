# Retention inventory and policy preview

Local implementation on 30 October 2026. The user will supply the organisation's existing retention policy. No period has been chosen, and no evidence deletion or archive operation has been added or run.

## What is available

Admin tools now has **Retention preview**, backed by the admin-only `GET /admin/screening/retention-preview` endpoint. It runs only when requested. With no comparison date it reports inventory; an optional historical cutoff adds counts of older rows and dependency checks. A cutoff is a hypothetical comparison, not a saved policy or deletion instruction.

The report uses a repeatable-read, read-only PostgreSQL transaction with a 15-second statement timeout. It never calls schema creation, request recovery, cleanup or mutation helpers. Output contains aggregate counts, timestamps and storage sizes; it contains no names, DOBs, business references, notes or idempotency-key values. Missing schema or query failure returns an error rather than a partial success.

Inventory covers subject groups, requests, runs, review decisions, review events, job recovery events, current state, operational jobs and AI suggestions/reviewer actions. Storage includes indexes and auxiliary storage, not an estimate of space deletion would reclaim. Backups, CSV files, application logs, watchlist data, authentication/audit systems and external copies are outside this report.

## Comparison dates

All comparisons are strictly **before** the cutoff; records exactly at the cutoff are excluded. The UI treats the chosen date as midnight UTC. API/CLI timestamps must include a timezone and cannot be in the future. JSON output includes each table's exact date basis:

| Record type | Comparison date |
| --- | --- |
| Subject group | Group creation time, not the age of its evidence |
| Request | Completion time; submission time if unfinished |
| Engine run | Original screening time |
| Review decision | Decision time |
| Review / job event | Event recording time |
| Current state | Last screening time |
| Operational job | Completion time; creation time if unfinished |
| AI suggestion | Suggestion creation time |

These comparison dates are implementation facts for this preview. They are not a proposed retention trigger. The organisation's policy may require a different trigger, such as case closure or last activity, which is not necessarily stored here.

## Dependencies and active work

The report separately counts unfinished requests, active jobs, open reviews, pending AI suggestions, duplicate-protection keys and linked retries. A `Pending External Review` outcome counts as open work even though the existing review workflow records it under COMPLETED.

For an optional cutoff it flags older unfinished requests, request keys and retry parents; older runs referenced by current state, newer requests, review evidence or jobs; older current-state rows with open reviews/active jobs; and older jobs with unfinished attached requests. Counts overlap. They are dependency indicators, not an exhaustive graph of removal eligibility or a promise that unflagged rows can be deleted. Review evidence, legal holds, archive rules and dependent AI records need an approved design before any removal tooling.

## Existing cleanup is separate

No existing cleanup setting or action has been run or changed by this preview:

- Worker job cleanup defaults to seven days for completed/failed job rows (`SCREENING_JOBS_RETENTION_DAYS`). Saved requests and job events survive.
- Optional current-state cleanup uses `SCREENED_ENTITIES_RETENTION_MONTHS`; default zero disables that cleanup. It currently checks last screening age, without protecting open reviews or active jobs.
- The admin testing action truncates current-state and finished job rows only when there are no unfinished requests, active jobs or open reviews. An atomic table-lock guard also rejects concurrent database use. It leaves history tables and is not a retention procedure.
- New immutable history/review/event tables have no purge operation. Request idempotency keys remain with request records.

Some legacy review metadata may exist only in current-state rows; the legacy snapshot migration cannot reconstruct missing review events. Do not assume that clearing current state preserves every legacy detail. The preview itself does not change cleanup behaviour. The separate release review added active-work protection to the admin testing action; age-based worker cleanup still requires policy review.

## Command-line use

Use a database account with read-only access and explicitly configure `SANCTIONS_REPORT_DSN` in the process environment. The tool does not read `DATABASE_URL` or apply migrations:

```sh
python scripts/preview_retention.py
python scripts/preview_retention.py --before 2025-01-01T00:00:00Z --output /tmp/retention-preview.json
```

The cutoff above is an example only. The script also sets `default_transaction_read_only=on`. `--output` writes the aggregate report to the specified local file; without it, JSON goes to stdout. The existing tests create their own disposable local databases.

## Verification and next step

The full suite passes 66 tests and 82 subtests; the frontend build passes. Tests cover aggregate counts, exact cutoff boundaries, retry/idempotency/shared-run dependencies, open external reviews, timezone/future-date validation, admin access and the command-line connection. A before/after snapshot of every reported table proves that previewing changes no records, including overdue requests. Browser verification used synthetic data and checked both inventory-only and cutoff views.

Next: map the supplied policy to record categories, retention start dates, exceptions/holds, archival requirements and the idempotency guarantee. Then show a concrete impact preview and agree a controlled implementation. Policy-dependent deletion remains unimplemented.
