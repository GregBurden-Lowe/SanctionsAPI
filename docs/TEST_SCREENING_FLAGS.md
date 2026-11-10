# Test screening flags

Administrators can mark an individual saved screening as test data from Match Review, with a reason and the expected current run. The flag is scoped to the current entity key, business reference, normalized name, type, date of birth and country. If those details change the exclusion no longer applies. Names, automated references and historic request labels do not by themselves prove that an entity is test data. The flag does not clear or delete any screening, review, request, recommendation or historical run.

Matching flags are hidden from the default review queue and excluded from AI candidate selection and automatic AI clearance. Administrators can list flags and restore a record to normal handling with a new reason. Flag and restore actions produce immutable audit events. A concurrent screening causes a stale-run conflict rather than flagging a different run.

New monitoring cycles omit matching flags. In an existing cycle, flagged items that have not yet been queued are skipped without preventing cycle completion. The flag action refuses records with queued or running jobs; wait for the job to finish and reload the record before marking it. This prevents an older queued snapshot from replacing the reference after classification. Unflagging an item still present in an active cycle makes it eligible for that cycle; a record excluded from a completed or snapshotted cycle returns in a later monitoring cycle. Manual screening is unchanged. This is a record-level monitoring exclusion, not an isolated benchmark environment or a sanctions clearance.

Migration 009 changes the schema contract. Release the API and all workers together with the existing coordinated migration procedure, and take a verified database backup and rehearse the schema upgrade on a restored production copy first. Stop all old writers, including any temporary priority worker, before installing the migration; restart the needed monitoring and priority work using the new image. Do not mark existing records automatically. Verify specific records and their associated references before flagging them.

## Flagging the records a benchmark created

Two scripts handle a batch of test screenings. Run both from the release runtime with its existing `DATABASE_URL`, and keep their reports in a private directory outside the repository: they contain names.

`scripts/find_test_screenings.py` is read-only. It selects recorded requests by time window plus at least one of the login or API key (`--actor`), request source, requestor, or business reference prefix or pattern, and lists the saved screenings those requests produced. A window on its own must be asked for with `--window-only`. `--listed-names-only` narrows a selection to names that are exactly a listed name or alias; a name is never a selector by itself, because a real customer can share a listed name.

```sh
python scripts/find_test_screenings.py \
  --start '2026-11-05T09:00:00+00:00' --end '2026-11-05T18:00:00+00:00' \
  --actor BENCHMARK_LOGIN_OR_KEY \
  --output /secure/operator/path/test-screenings.json
sha256sum /secure/operator/path/test-screenings.json
```

Each screening is `eligible` only when every recorded request for it is inside the selection. One that anyone else also searched is `needs_review`, as is one with a queued or running check, no business reference, or an earlier flag that no longer applies. Monitoring re-screens are not recorded as requests, so they do not change this. Read the report before going further: eligible means the selection explains the record, not that the record is proven to be test data.

`scripts/flag_test_screenings.py` flags the eligible entries with the same rules and audit events as Match Review. Without `--execute` it is a dry run: every check runs and is rolled back.

```sh
python scripts/flag_test_screenings.py \
  --inventory /secure/operator/path/test-screenings.json --inventory-sha256 REPORT_SHA256 \
  --reason 'Created by the 5 November matcher benchmark, not a customer.' --actor OPERATOR_NAME \
  --output /secure/operator/path/test-flags-dry-run.json
```

Add `--execute` and a new `--output` path to write the flags. A screening is skipped if it changed after the report: a new run, a new recorded request, a queued or running check, or an existing flag. Exit code 2 means something was skipped. `needs_review` entries are never flagged by the script; decide those one at a time in Match Review. Flags are recorded with the actor `bulk-test-flag:OPERATOR_NAME` and can be restored individually.

Flagging removes a record from the review queue, AI triage, monitoring re-screens and the release re-screen. It does not delete anything, and it does not remove a record from search, the dashboard or the MI export.
