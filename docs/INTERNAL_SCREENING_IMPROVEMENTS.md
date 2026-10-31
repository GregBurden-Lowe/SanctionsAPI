# Internal screening improvements

Implemented locally, 31 October 2026. No production migration, deployment or live provider validation has been performed for this change.

## API compatibility

Existing `/opcheck`, `/opcheck/dataverse`, `/opcheck/jobs/{job_id}`, internal/bulk screening, history and review endpoints retain their paths, request requirements and legacy response keys. Existing status and boolean meanings remain unchanged: engine matches are potential matches, not confirmed identities. New fields are additive:

- `Source Coverage`: exact projected dataset version, ingestion timestamp, source-family counts, required/missing lists and freshness assessment at execution.
- `Screening Guidance`: finding, limitations and next actions.
- Candidate `source_evidence_hash` and `candidate_signature`: evidence-change detection and conservative review reuse.
- `Monitoring Change`: previous run and displayed candidates added, removed or changed.

`evidence-v4` invalidates old cache fingerprints. Missing, unversioned or stale source coverage yields the existing incomplete/error path, never a clearance. Connected clients must already handle non-200 responses and queued results; no new required input is introduced.

The new authenticated UI endpoints are `/screening/assurance`, `/api/companies/{company_number}/investigations` (GET/POST) and `/api/company-investigations/{investigation_id}`. `/admin/screening/reconcile` is admin-only. These supplement existing endpoints.

## 1. Coverage and version assurance

Refresh builds a manifest from exact downloaded CSV hashes. It checks presence of the configured UK, UN, EU and OFAC source families, plus the PEP collection when needed. Counts identify records associated with these families; they are not independent verification of every upstream list's completeness. UK FCDO/UK Sanctions List labels are recognised.

The manifest is embedded in Parquet. PostgreSQL publishes its manifest and replacement rows in one transaction. A reader holds a table lock across both person/organisation passes, so a refresh cannot split a single execution. Parquet readers pin one dataframe and its embedded metadata for both passes. File replacement is detected across processes without a worker restart.

Source publication time is explicitly unverified; it is never invented from download time. The default maximum ingestion age is 48 hours (`SCREENING_MAX_SOURCE_AGE_HOURS`). Readiness also checks versioned PostgreSQL coverage. Exact versions are stored with immutable screening-run results; legacy refresh IDs are no longer misrepresented as exact execution versions.

Content-addressed projected snapshots are retained at `data/watchlist_versions/<version>.parquet` for replay. They do not contain every field in the original upstream CSV. No automatic archive deletion is introduced: monitor storage and agree retention separately.

A shared-volume file lock prevents concurrent refreshes interleaving CSV files. The two backends are published independently: if PostgreSQL publication fails after Parquet publication, their versions can differ. Each execution remains accurately labelled, the dashboard shows the mismatch, and the background loop retries. This is not a distributed atomic publication guarantee.

## 2. Useful results

Results distinguish potential sanctions/PEP matches from identity verification. Guidance identifies missing/partial DOB evidence, company relationship checks and escalation actions. The result screen, saved history, PDF and review queue expose guidance/coverage or link to the exact saved evidence. Reused review decisions are labelled separately from new analyst decisions.

## 3. Scoped decision reuse

A human false-positive decision records resolutions for its displayed candidate pairs. A subsequent run can reuse the latest human decision only when:

- The normalized matching identity, rules/configuration and business reference match. Backend-specific caches stay separate, but an identical candidate resolution can carry between interactive and background runs.
- Person DOB, or organisation country, was supplied. Name-only results are ineligible.
- Every potential candidate has a stable source record ID and unchanged evidence signature.
- Evidence is complete and untruncated, and current source coverage is valid.
- The human decision is less than 180 days old and remains the latest human conclusion for that identity/reference.

A new candidate, changed relevant source evidence, identity/context change, newer contrary decision or expiry prevents reuse. Reuse creates a new immutable review decision referencing the original human decision; original engine evidence is retained. This does not introduce a global name ignore list. Existing same-run cache behaviour remains compatible; this new rule governs review carry-forward to new runs.

## 4. Ongoing monitoring

The existing screening worker starts a coordinated background loop. Defaults:

- `SCREENING_MONITORING_ENABLED=true`: monitor the current `screened_entities` population.
- `SCREENING_AUTO_REFRESH_ENABLED=true`: refresh both datasets when ingestion reaches 24 hours, coverage is missing/stale, or versions differ.
- Loop: approximately every 60 seconds; source refresh failures retry on later loops.
- Full-population reconciliation on a dataset version change and at least daily, subject to completion of the prior cycle.
- 100 entities queued per tick, with every entity captured durably in the cycle. No capped name shortlist is used as a coverage boundary.

Pending jobs prevent a second concurrent monitoring submission for that entity, but the monitoring item stays pending and is picked up later. Original DOB precision, country and business reference are retained. Cycles finish only after jobs are terminal; failed jobs produce a failed cycle, not a successful screening. Failed cycles retry the next day or through the admin reconciliation action. Completed cycles remain available in the dashboard; monitoring heartbeat and errors identify a stopped or failing loop.

The cycle version identifies its trigger. Jobs bind their actual results to the dataset loaded at execution. If data changes during a cycle, the subsequent reconciliation handles the newer version.

The legacy UK refresh summary fields remain present and identify the old mechanism as superseded; the new monitoring panel provides the authoritative cycle counts. No email, Teams or external messaging is added.

## 5. Measured matching quality

The existing private acceptance-export/replay workflow now scores explicitly labelled cases through `acceptance_metrics.py`. Provider record IDs are preserved where present. Reports include:

- Expected-candidate detection rate, including incomplete/unassessable positives as not demonstrated.
- False-alert rate on completed independently labelled negatives.
- Incomplete rate, p95 runtime, counts and per-case assessments, separately for each replayed backend.

A scoreable label requires expected outcome, expected candidate IDs for positive cases, reviewer, reason and ISO review date. Multiple required IDs use semicolons. Historical clearances and false-positive dispositions never become ground truth automatically. Unlabelled cases stay unscored. No production recall/precision or market-leader parity is claimed; independently reviewed cases are still required. Multilingual matching limitations and candidate caps remain visible areas for subsequent measured tuning.

Use the existing runner:

```sh
SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres python scripts/validate_acceptance.py \
  --pack /private/path/to/labelled-pack \
  --snapshot /private/path/to/version.parquet \
  --output /private/path/to/new-report-directory
```

The runner creates a disposable local database; private reports must remain outside the repository. Diagnostic matching intentionally permits unversioned historical snapshots and is not the production clearance path.

## 6. Connected company review

Companies House officer retrieval now paginates. PSC and PSC-statement retrieval paginates separately, retains current relationships, distinguishes individuals/corporate entities, and exposes protected/unavailable ownership information rather than interpreting it as no owners.

The Companies House screen can create/reopen a saved connected investigation. Company, active officers and disclosed current PSCs use the existing screening/request-ledger implementation. The investigation retains its relationship snapshot, actor, reference and individual request IDs. Results link to saved evidence. A `screened` investigation means individual checks completed, not that the company is cleared. Missing ownership information or incomplete checks keep it incomplete.

Interactive investigations process at most 100 relationships and explicitly report unprocessed relationships. Cases above that limit need separate handling; no overall clearance is issued. Registry observations describe direct reported relationships only, not a complete ownership graph or a legal determination of ownership/control. Corporate officers are screened as organisations; partial DOBs are not turned into invented full dates.

## Release and validation

Migration `005_internal_screening.sql` is additive and included in the schema contract. Release API and workers together using the existing coordinated migration process. Before routing traffic, refresh and verify both source manifests; old unversioned data deliberately fails the new production checks. Start workers only against the upgraded schema. Retain `SCREENED_ENTITIES_RETENTION_MONTHS=0` until the separately documented retention risks are resolved.

Local tests cover missing/stale sources, source-version locking, file replacement, API contract preservation, cached stale-result rejection, scoped decision reuse and reopening, monitoring pagination/pending-job handling/failure visibility, PSC pagination, connected investigation persistence and labelled metrics. Full verification commands:

```sh
SCREENING_MONITORING_ENABLED=false SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres \
  /tmp/sanctions-history-venv/bin/python -m pytest tests -q
npm run build --prefix frontend
git diff --check
```

Tests use synthetic fixtures/disposable databases. No live provider calls or production changes are necessary for this verification. Docker execution and representative production-volume throughput remain separate release checks.

Verification result: the full local suite passed with 100 tests and 89 subtests; the frontend production build and `git diff --check` passed. A subsequent focused run also covered cross-backend review carry-forward. No browser walkthrough or live Companies House/OpenSanctions execution was performed.
