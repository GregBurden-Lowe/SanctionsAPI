# Release rescreen

The rules fingerprint prevents reuse of old matching-rule results by new compatible screening requests. It does not proactively rerun earlier checks. The release rescreen tools provide a frozen read-only inventory and a separately invoked executor; they are not an automatic deployment hook.

The scope agreed for this release is every recorded request from Monday 2 November 2026, 00:00 Europe/London (UTC+00:00 on that date) up to the inventory cutoff. Generate the inventory after the prior release's APIs and workers have drained and after the new release and sources have been verified. Handle pending requests first, then inventory again if necessary. The new release must not be rolled back to older matching rules while the executor is running. Use the application runtime's existing `DATABASE_URL`; never place credentials in a command, report, or chat.

Before enabling the release against an existing PostgreSQL watchlist, run `python scripts/validate_watchlist_aliases.py` from the verified candidate runtime against the intended database. The command reads the entire watchlist under a read-only transaction and verifies that nested alias cells parse, both source types are present, source coverage is current, PostgreSQL is version 13 or later with UTF-8 and `pg_trgm` installed; it returns exit code 2 if validation fails. It does not certify matching quality. A newly refreshed watchlist is validated before publication; do not proceed on a failed preflight or an unverified source change.

```sh
python scripts/plan_release_rescreen.py \
  --start '2026-11-02T00:00:00+00:00' \
  --output /secure/operator/path/release-rescreen-inventory.json
sha256sum /secure/operator/path/release-rescreen-inventory.json
```

The inventory captures a consistent cutoff and includes every recorded request in the interval. Matching groups require identical submitted values (apart from surrounding whitespace), entity type and original backend; every request ID and business reference is retained. The report flags unknown or mismatched backends, missing inputs/references, active jobs, incomplete requests, legacy snapshots and historical runs. Counts overlap. Legacy backfills are not original requests. The file contains personal data, is created mode 0600 and cannot overwrite another file.

## Execution

After investigating the inventory and completing the matching release acceptance gate, run the following **inside the verified new release runtime**, using its existing `DATABASE_URL` and `RELEASE_ID`. Use a writable, private host-mounted directory for the inventory and report so they survive container removal. Replace the placeholders with the exact inventory hash and verified runtime release ID. The drain flag attests that the old API and worker writers have stopped; it is not an automated check. Do not run against a mixed old/new writer deployment.

The legacy single-stack Compose configuration does not itself set `RELEASE_ID`. After independently verifying the running container's image and release code, the operator must provide the verified identifier to the executor process, for example with `docker compose exec -e RELEASE_ID=VERIFIED_RELEASE_ID app ...`, and supply the same value to `--release-id`. This supplied value is an operator attestation, not independent proof of the image. The rolling configuration sets the value from its release image identity.

```sh
python scripts/execute_release_rescreen.py \
  --inventory /secure/operator/path/release-rescreen-inventory.json \
  --inventory-sha256 INVENTORY_SHA256 \
  --rules-version RUNTIME_RULES_VERSION \
  --release-id VERIFIED_RELEASE_ID \
  --old-writers-drained \
  --output /secure/operator/path/release-rescreen-result.json \
  --execute
```

Requests whose saved screening is flagged as test data appear in the inventory with the issue `flagged_as_test_data`. The executor leaves them out, reports them as `skipped_test_data`, and does not count them as needing attention. Flag test records before taking the inventory: a record flagged afterwards is not re-screened either, but is reported as unresolved until the inventory is regenerated.

`RUNTIME_RULES_VERSION` is the exact version the release runtime stamps on results. It names every rule set in force and depends on `SCREENING_MATCHER_VERSION` and `SCREENING_EVIDENCE_GRADES`, so read it from the release runtime with its environment, not from this document:

```sh
python -c "from utils import rules_version_for; print(rules_version_for('postgres_beta'))"
```

The executor refuses to start if the value given differs from what the runtime stamps.

The executor verifies the hash, database name, database OID and server address/port, time window, original requests, runtime release ID and matching rules version. A failover or changed database endpoint requires a new inventory; endpoint checks alone do not prove target identity, so the operator must also verify the target. It executes each identical input group once through its recorded backend with required current source coverage; it does not reuse old matching results. PostgreSQL-origin checks remain PostgreSQL checks and Parquet-origin checks remain Parquet checks. Each original request receives its own linked immutable result and business reference. Historical results and review decisions remain in the ledger. Automatic candidate-decision reuse is disabled for these new results so that newly detected candidates receive fresh review.

An entity's current result changes only when the inventoried current run is still current, its recorded inputs agree, its review state has not changed, no review is in progress and no screening job is active. Request and entity rows are locked before publication, and the jobs table is locked briefly to fence new queue activity. Otherwise a successful screening is retained in history only and marked for follow-up; it does not replace current state. Unknown/mismatched backend, missing source data, invalid inputs, missing reference, active jobs, changed requests and screening errors remain unresolved. Original failed or queued requests are reported but are not treated as completed screenings. The executor does not issue a clearance when it cannot complete a check.

A short PostgreSQL advisory lock prevents simultaneous release executors. Each group's new runs, request links and any eligible current-state updates commit together. Idempotency is keyed by original request and rules version, so interruption or a repeat invocation does not rerun already completed work. A partially processed group from a different inventory is reported for inspection. Every run needs a new output path. A private `started_or_interrupted` marker is reserved before database writes and replaced atomically by the final report; if interrupted, rerun using the same frozen inventory and a new output path. History-only items remain flagged on repeats.

The final report maps original request IDs to new request/run IDs, reports current-state updates and prior/new statuses, and lists unresolved or history-only items. `complete` means all eligible original requests have completed with current-state updates; exit code 2 and `needs_attention` mean unresolved or history-only cases remain. The report is private (mode 0600). Examine every such case and every changed outcome; neither script silently repairs or overrides them. Verify source versions, API/worker release identity, previous-writer drain and release health separately.

## Verification and limitations

Disposable PostgreSQL tests exercise grouped request links, backend selection, live source assurance, current-state updates, prior immutable evidence, idempotent retries, stale current runs, review changes and active claims, jobs arriving during screening, transaction rollback and failure paths. This is local verification, not production-volume throughput, Docker or live deployment evidence. `RELEASE_ID` and the drain flag are operator checks, not a substitute for verifying the deployed image or absence of old writers. The matching-validation release gate also remains open: the diagnostic replay's changed sanctions outcomes require investigator review.
