# Compliance Manager Brief

Last updated: 2026-02-21

## 1) Purpose and Scope

This application is an internal sanctions and PEP screening platform used for:

- Single checks for people and organisations.
- Batch screening workflows.
- Review and resolution of potential matches.
- Evidence export and audit traceability.

The platform supports internal compliance operations and does not replace human adjudication or legal decision-making.

## 2) What the Application Does Today

### Screening and Decisioning

- Runs sanctions and PEP checks from UI and API (`POST /opcheck`).
- Supports a Dataverse-focused route with explicit `entity_id` alias (`POST /opcheck/dataverse`).
- Accepts optional DOB formats: `YYYY-MM-DD`, `DD-MM-YYYY`, or `YYYY`.
- Enforces mandatory `business_reference` and mandatory enum `reason_for_check`.
- Applies sanctions-over-PEP decision precedence.
- Returns structured outputs including risk level, confidence, score, source summary, and entity key.

### Evidence, Search, and Reuse

- Persists screening outcomes to PostgreSQL (`screened_entities`).
- Reuses still-valid screening records to avoid unnecessary re-screening.
- Supports exact-key and name/business-reference search (`GET /opcheck/screened`).
- Exports a system-generated PDF with:
  - screening decision
  - request metadata
  - sources reviewed
  - business reference
  - reason for check
  - entity key reference

### Match Review Workflow

- Dedicated review queue (`GET /review/queue`).
- Claim flow for unreviewed potential matches (`POST /review/{entity_key}/claim`).
- Completion flow with mandatory structured outcome and mandatory notes (`POST /review/{entity_key}/complete`).
- Completed reviews are treated as immutable in application workflow.

### Queueing, Bulk Operations, and Worker Processing

- Queue-backed processing for load protection.
- Internal enqueue APIs for single and bulk jobs:
  - `POST /internal/screening/jobs`
  - `POST /internal/screening/jobs/bulk`
- Admin bulk upload UI for CSV intake.
- Background worker processes queued jobs and updates persistence state.

### Watchlist Refresh and Delta Re-Screen

- Refresh endpoint for OpenSanctions update (`POST /refresh_opensanctions`).
- PostgreSQL watchlist sync from refreshed source files.
- UK sanctions delta tracking with refresh run records:
  - added
  - removed
  - changed
- Targeted re-screen queueing when UK list changes.
- Admin summary endpoint for recent refresh and re-screen outcomes.

### Operational Monitoring

- Dashboard with key operational metrics:
  - open high-risk reviews
  - aged reviews
  - new matches (24h / 7d)
  - review throughput
  - data freshness
  - latest refresh impact (added/removed/changed and queued/failed counts)
- Screening jobs monitor (pending/running/completed/failed).

### Administration

- User administration (create/import/update roles/password reset behavior).
- API key management (admin-only):
  - list keys
  - create key (raw value shown once)
  - activate/deactivate
  - delete
- Admin false-positive override with mandatory reason.
- Admin-only OpenAPI schema endpoint (`GET /admin/openapi.json`).

## 3) Security Controls in Place

### Authentication and Access Control

- JWT-based user authentication for UI/API calls.
- Admin-only route enforcement for privileged functions.
- API key support for service-to-service screening access.
- API keys limited to screening routes and blocked from admin routes.
- Internal screening endpoints protected via dedicated API key and/or IP allowlist.

### API Key Security

- Keys are generated randomly and securely.
- Only key hashes are stored in database (`api_keys.key_hash`).
- `last_used_at` updates on successful key use.
- API key auth events are audit-logged.

### Application Hardening

- Public interactive docs are disabled (`docs_url=None`, `redoc_url=None`, `openapi_url=None`).
- Security headers are set at application layer.
- Generic error responses reduce internal detail leakage.
- Static file handling blocks hidden/sensitive file patterns from accidental exposure.

### Abuse Resistance

- Route rate-limits on auth, screening, queue polling, refresh, and internal bulk endpoints.
- Optional shared limiter backend (Redis) supported.
- Login backoff for repeated failed authentication attempts.

### Auditability

- Structured audit events for:
  - authentication
  - screening attempted/completed
  - admin operations
  - data access actions
  - review claimed/completed
  - API key usage
- Key business context captured in events, including business reference and reason for check.

## 4) Compliance-Relevant Design Points (GDPR / FCA Context)

### Data Minimization and Purpose Limitation

- Screening inputs are limited to operationally required fields.
- Mandatory business context fields (`business_reference`, `reason_for_check`) support lawful-purpose traceability.

### Traceability and Defensibility

- Each screening result has a stable entity key and stored metadata.
- Decision summaries, sources, and timestamps are retained for investigation and audit evidence.
- PDF evidence aligns with persisted screening metadata.

### Ongoing Screening Expectations

- Watchlist refresh supports scheduled update operations.
- Delta-driven UK list change handling supports targeted re-screening operations.
- Refresh and re-screen activity is visible through admin summary and dashboard metrics.

### Controlled Override Behavior

- False-positive override requires a reason and is restricted to admins.
- Match review workflow captures structured review outcomes and notes.

## 5) Operating Model (Recommended for Presentation)

- Daily watchlist refresh job (for example 22:00 UTC) via API key.
- Background worker always-on to process queue.
- Dashboard and jobs view used for daily operational checks.
- Match review queue used as controlled disposition workflow for potential matches.
- Periodic evidence sampling using Search Database and PDF export.

## 6) Residual Risks and Important Limitations

- Fuzzy name matching can still produce false positives/duplicates without broader identity-proofing controls.
- If DOB is omitted, false-positive likelihood is higher.
- Decision engine is deterministic but not identity verification.
- Audit logs are structured, but tamper-evidence/immutable external audit store depends on deployment logging architecture.
- MFA and certain governance controls are policy-dependent and should be tracked in security/risk documentation.

## 7) Supporting Documents to Bring to the Compliance Review

- `docs/SCREENING_DECISION_RULES.md`
- `docs/RELEASE_GATE_PHASE1.md`
- `docs/security-policy.md`
- `docs/dpia.md`
- `docs/data-retention.md`
- `docs/risk-assessment.md`
- `docs/NGINX_HARDENING.md`
- `docs/INTERNAL_SCREENING_API.md`

## 8) Suggested Demo Flow (10-15 Minutes)

- Run a screening with mandatory business reference and reason for check.
- Show result interpretation and generated PDF evidence.
- Search the stored record by entity key/business reference.
- Show match review claim and completion workflow.
- Show dashboard and latest refresh impact (added/removed/changed).
- Show admin API key creation and one-time display behavior.

