# Data retention

This document describes configurable retention and recommended processes for personal and screening data (GDPR Article 5; data minimization and storage limitation).

## Screened entities

- **Table:** `screened_entities` (current screening state per entity).
- **Config:** Set `SCREENED_ENTITIES_RETENTION_MONTHS` (e.g. `12` or `24`) in the environment of the **screening worker**.
- **Behaviour:** When set to a positive number, the worker deletes rows where `last_screened_at` is older than that many months. Cleanup runs in the same periodic cleanup pass as `screening_jobs` (see `SCREENING_CLEANUP_EVERY_N_LOOPS`).
- **Default:** Unset or `0` = no automatic deletion (data kept until you run a manual purge or enable this).
- **Manual purge:** You can run a one-off delete via SQL, e.g.  
  `DELETE FROM screened_entities WHERE last_screened_at < NOW() - INTERVAL '12 months';`  
  Or use the API’s screening_db helper `purge_screened_entities_older_than(conn, months)` from an admin script if you expose it.

## Screening jobs (queue)

- **Table:** `screening_jobs`.
- **Config:** `SCREENING_JOBS_RETENTION_DAYS` (default `7`). The worker deletes completed/failed jobs older than this.
- **See:** Worker env vars and `SCREENING_PERSISTENCE.md`.

## Users (GUI accounts)

- **Table:** `users` (and optionally `access_requests`).
- **Retention:** No automatic retention in application code. Recommended:
  - Define a retention policy (e.g. remove or anonymise inactive accounts after N months of no login).
  - Implement via periodic process: e.g. soft-delete flag + job, or SQL runbook that deletes/anonymises rows based on `created_at` or a last-login timestamp if you add one.
  - Document the policy and where the process is run (cron, manual runbook).

## Search log (CSV)

- **Location:** `utils.py` appends to a CSV file (default path under `DATA_DIR`, e.g. `search_log.csv`).
- **Retention:** No rotation or purge in code. Recommended:
  - Rotate or truncate the file by date (e.g. logrotate, or a script that archives/removes rows older than N days).
  - Or disable writing by not calling the append function if you do not need this log.

## Audit logs

- **Source:** Application audit events (login, admin actions, screened search access) are logged via the `sanctions.audit` logger.
- **Retention:** Configure at the logging layer (e.g. file handler rotation, or log aggregator retention). Recommended retention for compliance: e.g. 90 days; document in operational runbooks.
