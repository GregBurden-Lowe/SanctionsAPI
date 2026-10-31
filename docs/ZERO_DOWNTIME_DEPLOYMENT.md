# Blue/green deployment on the Droplet

This workflow keeps the API available for **compatible releases after a coordinated baseline installation**. It is not a promise of availability through a database outage, host failure or incompatible migration. It does not run migrations during a rollout.

The rollout project manages two API slots and two lease-aware screening worker slots on the existing Docker network and data volume. It does not recreate PostgreSQL, Ollama or the existing singleton AI-triage scheduler. Keep the AI scheduler on its compatible baseline image; changes to that scheduler need a separate drained restart outside its scheduled run. Do not start duplicate AI schedulers.

## What is implemented

- Immutable image IDs; build/pull completes before the script starts.
- Candidate schema/protocol check before starting it against the database.
- `SANCTIONS_SCHEMA_MODE=verify`: startup verifies the installed contract and performs no schema DDL. Automatic startup migration remains the legacy default, but is rejected by the rollout readiness gate.
- `/ready` checks startup state, schema contract, database access and populated PostgreSQL sanctions/PEP sources. It reports release identity, has a five-second dependency budget and does not return credentials. `/health` remains simple liveness.
- Candidate worker verifies schema and Parquet sanctions/PEP coverage before becoming healthy. This is startup readiness, not a queue-throughput guarantee.
- Nginx configuration validation and graceful reload; three successful release-identity probes through the configured public URL before retirement.
- Retains old content-hashed frontend assets so already-open browser pages can still load their chunks after cutover. No automatic pruning.
- Waits for the pre-reload Nginx worker processes to exit before stopping the old API. Workers honour SIGTERM after finishing their current job. Docker stops use an unlimited grace period, never a forced kill.
- Exclusive host deployment lock, atomic state/journal files, automatic route restoration on a failed cutover and explicit interrupted-deployment recovery. Both slots remain available if recovery cannot be verified.

## One-time baseline: coordinated window required

The previously deployed application may lack immutable history, request expiry and worker lease fencing. It cannot safely coexist with this schema/writer generation. **Do not describe its first upgrade as zero downtime.** Back up and rehearse migrations 001–004 against an isolated restored production backup first; the existing acceptance and retention release conditions still apply.

On the Droplet, identify the actual Compose project, network, named sanctions volume, runtime environment file and Nginx site. Do not guess the names in the example. Keep the same JWT secret, database, trusted-proxy configuration and data volume. The runtime env file must include the service settings previously supplied by Compose, including `LOCAL_LLM_BASE_URL=http://ollama:11434` if required. Verify memory/disk/DB connection headroom for both APIs and screening workers running together, including each worker's loaded Parquet data. Shared Redis rate-limit storage is recommended; process-local limits multiply across instances.

1. Build a tagged image from a reviewed, clean checkout. Copy `deploy/rollout.example.json` to a host configuration such as `/etc/sanctions-rollout.json`, and replace its example paths/network/volume/domain. The script runs on Linux with `/proc`, Python 3, Docker Compose v2, host Nginx and systemd. Root access is needed for the Nginx include/reload. Do not put secrets in the JSON.
2. In the coordinated window, pause refresh/ingestion cron and drain/stop **all legacy writers**: API, screening workers and AI scheduler. Preserve their database, volumes and images; do not run `compose down`, delete volumes or prune images. Complete or explicitly resolve legacy running jobs before introducing the new workers.
3. Run the explicit schema installer using the new image and the existing network:

   ```bash
   docker run --rm --network YOUR_EXISTING_NETWORK \
     --env-file /opt/sanctions/.env \
     sanctions-api:RELEASE python deployment_runtime.py
   ```

   This transaction installs screening/auth schema plus the deployment contract. It has a five-second lock timeout and rolls back on failure. It does not seed users, download lists or create missing watchlist data. Existing PostgreSQL and Parquet sanctions/PEP sources must already be populated.

4. Prepare the blue slot. This copies frontend assets into the configured persistent host directory and starts the API and screening worker in verify-only mode:

   ```bash
   sudo python3 scripts/deploy.py bootstrap --config /etc/sanctions-rollout.json --image sanctions-api:RELEASE
   ```

5. Integrate `deploy/nginx-upstream.conf.example` into the **existing** TLS site, retaining certificates, security headers and proxy settings. Initially the include file must contain exactly `server 127.0.0.1:8101;` followed by a newline (or the configured blue port). All production traffic, including refresh cron, must use this upstream; no clients should target the slot ports directly. Ensure `/ready` traverses the same upstream without caching or edge rate-limit interference. Use sufficient proxy timeouts for screening and no Nginx `worker_shutdown_timeout` that truncates requests. Validate with `nginx -t`, then reload Nginx and verify the configured public `/ready` URL.
6. Adopt the verified baseline:

   ```bash
   sudo python3 scripts/deploy.py adopt --config /etc/sanctions-rollout.json
   ```

7. Resume cron against the public URL. Restart only the singleton AI scheduler on a compatible image if used. Disable restart of the old Compose API/screening workers so a later host reboot or `compose up` cannot resurrect obsolete writers. Do not use the legacy `docker compose up -d` to upgrade this managed API/worker pair.

If the baseline is already installed with this workflow, skip these steps. There is no automatic adoption of an arbitrary legacy container.

## Subsequent compatible release

```bash
docker build -t sanctions-api:NEW_RELEASE .
sudo python3 scripts/deploy.py deploy --config /etc/sanctions-rollout.json --image sanctions-api:NEW_RELEASE
sudo python3 scripts/deploy.py status --config /etc/sanctions-rollout.json
```

The script starts only the inactive slot with `compose up --no-deps`, validates it, switches traffic and retires the old slot after drain. It resolves tags to image IDs; retain the previous image. Never deploy concurrently through another mechanism. Deploy from a pinned checkout containing the same reviewed orchestration/Compose files as the release.

The contract hashes the migration files and a protocol identifier. Any changed migration file is intentionally blocked from rolling deployment until a coordinated schema installation. Developers must also change `deployment_runtime.PROTOCOL` for breaking writer/worker/API contracts not captured by migration text. Sharing a contract is an explicit compatibility declaration, not an automatic proof of semantic compatibility. Keep old frontend/API contracts compatible during overlap. This first implementation deliberately does not automate online expand/contract migrations.

## Failure, drain and rollback

```bash
# Recover an interrupted/failed switch with pending.json present:
sudo python3 scripts/deploy.py recover --config /etc/sanctions-rollout.json
# Finish retirement if old requests/jobs outlasted the host command:
sudo python3 scripts/deploy.py retire --config /etc/sanctions-rollout.json
# Redeploy the previous immutable image through the same guarded workflow:
sudo python3 scripts/deploy.py rollback --config /etc/sanctions-rollout.json
```

Readiness failure leaves the old route serving. If a candidate was started, recovery reloads the old upstream and records the candidate for graceful retirement. A failed recovery preserves `pending.json` and both slots; investigate host/database/Nginx health and retry recovery. Do not delete the journal or manually kill slots to bypass it. A SIGKILL/power loss cannot be handled in-process, so the journal blocks the next deploy until recovery.

If drain times out, the new route remains active and both slots stay running. Retry `retire`; it does not force requests/jobs to terminate. A host command timeout while Docker waits for a stop does not request a force kill. Investigate a genuinely hung worker separately using its lease/recovery evidence. The script does not reverse database migrations or remove historical records. Rollback requires the previous image's schema contract to remain compatible.

## Verification and limits

Local tests cover compatible/incompatible baselines, candidate and public-route failure, interrupted recovery, drift detection, drain guards, no-force stops, verify-only startup, source coverage and real PostgreSQL worker completion during graceful stop. Compose configuration and Python compilation are checked locally. No Docker daemon was available for a container-level rehearsal; no Droplet deployment, Nginx reload or continuous live request test has been performed.

Before enabling production rollouts, run this end-to-end on a staging Droplet with the same proxy/network topology. Maintain a stream of reads and representative idempotent screening submissions during deploy and rollback, confirm no dropped/5xx responses or duplicate executions, confirm queued jobs finish once, and test a deliberately unhealthy candidate. Check older open browser sessions and long requests as well as readiness. Record actual availability results; synthetic orchestration tests alone do not prove zero downtime.

Nginx graceful reload keeps old workers serving existing clients while new workers accept connections ([official control documentation](https://nginx.org/en/docs/control.html)). Docker's negative stop timeout waits indefinitely instead of escalating to SIGKILL ([official stop documentation](https://docs.docker.com/reference/cli/docker/container/stop/)).
