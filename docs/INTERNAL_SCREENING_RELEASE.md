# Deploy the internal screening and AI release

This release includes migrations 005 and 006, source manifests, monitoring, and automatic approval of effective Clear recommendations. Existing endpoint URLs and required request fields remain unchanged. Plan a maintenance window: the schema contract changes, so this is not a compatible rolling release.

## Existing single-stack Docker Compose installation

Use these commands only if the existing production services are the repository's `app`, `worker` and `ai-triage-worker`. Run from the existing server checkout with its existing Compose project name, `.env`, network and data volumes. Do not create a second project. If blue/green deployment is already active, use the coordinated baseline procedure in [the deployment guide](ZERO_DOWNTIME_DEPLOYMENT.md), stopping all active slot writers and the singleton AI scheduler before schema installation; do not start the legacy Compose app alongside it.

1. Back up PostgreSQL and the sanctions data volume using the established server backup procedure. Retain the current images. Rehearse the schema installation on a restored backup. Pause external refresh cron, and allow running screening/AI work to finish before stopping the services. Database migrations do not themselves clear pending recommendations.
2. Pull and build before the maintenance window:

   ```bash
   git pull --ff-only origin main
   docker compose build app worker ai-triage-worker
   ```

3. In the existing `.env`, set or confirm:

   ```dotenv
   SANCTIONS_SCHEMA_MODE=verify
   AI_TRIAGE_ENABLED=true
   AI_TRIAGE_AUTO_CLEAR_ENABLED=true
   AI_TRIAGE_BATCH_LIMIT=25
   AI_TRIAGE_POLL_SECONDS=300
   AI_TRIAGE_RUN_HOUR=22
   AI_TRIAGE_TIMEZONE=Europe/London
   SCREENED_ENTITIES_RETENTION_MONTHS=0
   ```

   Preserve database/JWT/provider credentials and the existing model settings. Auto-clear is also applied to eligible existing pending Clear suggestions on the next batch. Set `AI_TRIAGE_AUTO_CLEAR_ENABLED=false` if automation needs to be suspended.

4. During the maintenance window, stop all three writers without forcing termination, install the complete schema transactionally, and start only the API. Run each command separately; stop if a command fails.

   ```bash
   docker compose stop --timeout -1 app worker ai-triage-worker
   docker compose run --rm --no-deps app python deployment_runtime.py
   docker compose up -d --no-deps app
   docker compose logs --tail=100 app
   ```

   The installer applies all required migrations, including 005 and 006, and records the schema contract. Do not run `docker compose down`, remove volumes, or restart an old image against the changed schema if installation/startup fails.

5. Keep general screening traffic paused while an administrator signs in and runs **Admin → Refresh**, including PEP data and PostgreSQL synchronization. Alternatively use the existing authenticated `POST /refresh_opensanctions` with `{"include_peps":true,"sync_postgres":true}`. Confirm refresh success and current source coverage for both PostgreSQL and Parquet. Old unversioned source snapshots intentionally fail the new coverage checks; `/health` alone does not validate them.
6. Start the workers, verify readiness and inspect logs:

   ```bash
   docker compose up -d --no-deps worker ai-triage-worker
   docker compose ps
   docker compose logs --tail=100 app worker ai-triage-worker
   curl --fail --show-error https://sanctions-check.co.uk/ready
   ```

   Use the actual public domain if different. Ollama must already be running with the configured model, as in the existing installation. Confirm a representative screening request completes with source evidence and a history entry before reopening normal traffic. Resume external refresh cron if retained; the monitoring worker also performs scheduled source refreshes.
7. Run an AI batch from the existing Admin controls, or wait for the scheduled hour. Confirm subsequent runs select further records, approved Clear suggestions appear in the **Approved** view, and their decisions identify `system:ai-auto-clear`. Each batch can process up to 25 existing Clear suggestions and generate up to 25 new suggestions. Scheduled batches continue every five minutes until the eligible queue is drained.

## Recovery and validation boundaries

If schema installation fails, it rolls back; leave writers stopped and investigate before proceeding. After successful schema changes, code rollback requires a compatible schema contract, or a coordinated restore of the pre-release database/data backup. A restore discards subsequent writes, so do not perform it as an automatic rollback.

Local validation passed: 108 tests, 89 subtests and the frontend production build. The release has not been deployed or rehearsed against the production backup by this work. Container-level and live provider/model verification remain deployment checks.
