# Release Gate User Stories

This document captures user stories by persona for release gating.

It includes:
- Implemented stories (feature found in code)
- Gap stories (feature expected for production controls but not found)

For release gating, treat all `P0` stories as mandatory.

## Status Legend

- `Implemented`: Feature exists in current app/API.
- `Partial`: Feature exists but does not fully meet control intent.
- `Gap`: Feature not found; story remains open.

## Release Gate Rule

- `GO`: All `P0` stories are `Implemented` (or an approved risk exception is recorded).
- `NO-GO`: Any `P0` story is `Gap` without signed risk acceptance.

## Claims User Stories

| ID | Priority | User story | Acceptance criteria | Status | Evidence / notes |
|---|---|---|---|---|---|
| CU-001 | P0 | As a Claims user, I can run a sanctions/PEP check for a person or organisation so I can complete pre-claim due diligence. | Input supports name, entity type, optional DOB, requestor; check returns decision summary. | Implemented | `/frontend/src/pages/ScreeningPage.tsx`, `POST /opcheck` in `/api_server.py` |
| CU-002 | P0 | As a Claims user, I can view clear outcome guidance (Sanction/PEP/Cleared) so I know what action to take. | Result view shows risk level, confidence, score, and guidance text. | Implemented | `/frontend/src/pages/ScreeningPage.tsx` (`ResultCard`) |
| CU-003 | P0 | As a Claims user, I can export a screening PDF so I can attach evidence to the claim file. | PDF contains screening decision, sources, key metadata, and entity key reference. | Implemented | `/frontend/src/utils/exportScreeningPdf.ts` |
| CU-004 | P1 | As a Claims user, I can re-open prior screening evidence by name/entity key so I do not re-run unnecessary checks. | Search database supports partial name and exact entity key retrieval. | Implemented | `/frontend/src/pages/SearchDatabasePage.tsx`, `GET /opcheck/screened` |
| CU-005 | P1 | As a Claims user in Dataverse, I can receive an `entity_id` from screening so I can link CRM records to screened entities. | Dataverse route returns `entity_id` alias and web resource uses it in audit section. | Implemented | `POST /opcheck/dataverse` in `/api_server.py`, `/docs/dataverse-webresource-themed.html` |
| CU-006 | P0 | As a Claims user, I can store a claim/case reference with each screening so each check is linked to a business transaction. | Request includes claim reference; stored in DB; visible in search and PDF. | Gap | Claim/case reference field not found in request model, DB schema, or result rendering. |

## Admin User Stories

| ID | Priority | User story | Acceptance criteria | Status | Evidence / notes |
|---|---|---|---|---|---|
| AU-001 | P0 | As an Admin user, I can manage user access so only authorised staff can screen. | Create users, import users, reset passwords, change admin/user role. | Implemented | `/frontend/src/pages/UsersPage.tsx`, `/auth/users*` endpoints |
| AU-002 | P0 | As an Admin user, I can monitor screening queue jobs so I can detect operational backlog/failures. | Jobs list supports status filter and shows pending/running/completed/failed with timestamps and errors. | Implemented | `/frontend/src/pages/ScreeningJobsPage.tsx`, `GET /admin/screening/jobs` |
| AU-003 | P1 | As an Admin user, I can upload CSVs for bulk screening so high-volume onboarding can be processed. | CSV parse, validation, enqueue up to configured limit, result counts shown. | Implemented | `/frontend/src/pages/BulkScreeningPage.tsx`, `POST /admin/screening/jobs/bulk` |
| AU-004 | P1 | As an Admin user, I can view daily re-screen summary so I can confirm delta processing happened. | Summary includes latest run, UK changed flag, delta counts, and transition snapshot. | Implemented | `/frontend/src/pages/AdminPage.tsx`, `GET /admin/screening/rescreen-summary` |
| AU-005 | P1 | As an Admin user, I can clear screening test data in non-production testing cycles. | Admin-only clear endpoint removes screened entities and jobs with confirmation. | Implemented | `/frontend/src/pages/AdminPage.tsx`, `POST /admin/testing/clear-screening-data` |
| AU-006 | P1 | As an Admin user, I can access internal API docs without exposing public docs. | Docs route requires admin auth and is available in admin UI. | Implemented | `/frontend/src/pages/AdminApiDocsPage.tsx`, `GET /admin/openapi.json` |
| AU-007 | P0 | As an Admin user, I can trigger emergency watchlist refresh from UI when cron fails. | Manual refresh is possible from authenticated admin UI with result feedback. | Gap | UI refresh control removed; refresh is API/cron only. |
| AU-008 | P1 | As an Admin user, I can see worker health/process status to ensure queue is actually being processed. | Health view shows worker heartbeat/last poll/errors. | Gap | No worker health endpoint/page found; only logs/process checks. |

## Compliance User Stories

| ID | Priority | User story | Acceptance criteria | Status | Evidence / notes |
|---|---|---|---|---|---|
| CO-001 | P0 | As a Compliance user, sanctions outcomes take precedence over PEP outcomes so adverse sanctions are never downgraded. | If both match, primary status remains sanction fail and PEP flag is still surfaced. | Implemented | Decision rules in `/docs/SCREENING_DECISION_RULES.md`, matching logic in `/utils.py` |
| CO-002 | P0 | As a Compliance user, I can search historical screenings and inspect full evidence so I can perform investigations. | Search supports name/entity key and detail modal shows full result card and metadata. | Implemented | `/frontend/src/pages/SearchDatabasePage.tsx` |
| CO-003 | P1 | As a Compliance user, I can mark a false positive when evidence supports override. | Admin can mark false positive and result changes to cleared override state. | Implemented | `POST /admin/screening/false-positive`, `/screening_db.py` override handling |
| CO-004 | P0 | As a Compliance user, UK list changes trigger targeted re-screen queueing so stale outcomes are not relied upon. | Refresh run stores UK hash/delta and queues re-screen candidates when changed. | Implemented | `/api_server.py` refresh flow, `/screening_db.py` refresh tables |
| CO-005 | P0 | As a Compliance user, data source scope is constrained to approved sanctions lists + PEP data. | Source allowlist includes UN/EU/OFAC/HMT and consolidated PEP dataset. | Implemented | `/docs/SCREENING_DECISION_RULES.md`, refresh/sync logic |
| CO-006 | P0 | As a Compliance user, false-positive overrides require a mandatory reason. | Override request must reject empty reason and persist reason text. | Partial | API accepts optional reason today; reason is not mandatory. |
| CO-007 | P0 | As a Compliance user, high-risk override actions require maker-checker approval. | One user submits override; second authorised user approves before effective status change. | Gap | No dual-approval workflow found. |
| CO-008 | P1 | As a Compliance user, I can version and approve rule-threshold changes with effective dates. | Rule version is stored and linked to each screening result. | Gap | No configurable rule engine/version store in UI/API. |

## Finance User Stories

| ID | Priority | User story | Acceptance criteria | Status | Evidence / notes |
|---|---|---|---|---|---|
| FI-001 | P0 | As a Finance user, I can see screening usage by month/team/client for chargeback and cost control. | Report includes counts by period and business owner dimensions. | Gap | No finance reporting page or usage-report endpoint found. |
| FI-002 | P1 | As a Finance user, I can export billing-ready usage data to CSV. | Downloadable usage extract with stable schema and date filters. | Gap | No billing export feature found. |
| FI-003 | P1 | As a Finance user, I can reconcile API usage with UI usage to avoid invoice disputes. | Combined metering record across UI, internal API, Dataverse API. | Gap | No consolidated usage ledger found. |
| FI-004 | P1 | As a Finance user, I can apply cost centre tags to screenings for internal allocation. | Request supports cost centre; retained in storage and reportable. | Gap | No cost centre/coding field in screening request model. |

## Audit User Stories

| ID | Priority | User story | Acceptance criteria | Status | Evidence / notes |
|---|---|---|---|---|---|
| AD-001 | P0 | As an Audit user, every screening can be traced by stable reference key. | Entity key/entity ID is present in result payloads and evidence exports. | Implemented | `entity_key` in app; `entity_id` alias in dataverse route; PDF footer key text |
| AD-002 | P0 | As an Audit user, I can retrieve prior evidence by entity key. | Screened search supports exact key and returns stored result JSON. | Implemented | `GET /opcheck/screened`, `/screening_db.py` search |
| AD-003 | P1 | As an Audit user, I can confirm who performed key actions. | Auth/admin/screened-search events are logged with actor and outcome. | Implemented | `audit_log(...)` usage in `/api_server.py`, policy docs |
| AD-004 | P1 | As an Audit user, sensitive technical docs are not publicly exposed. | Public `/docs` disabled; admin docs endpoint is authenticated. | Implemented | `/api_server.py` docs route behavior and `/admin/openapi.json` |
| AD-005 | P0 | As an Audit user, I can access immutable append-only audit logs with tamper-evidence. | Audit events stored in immutable store or cryptographically chained log. | Gap | Current audit logger exists, but immutable/tamper-evident mechanism not found. |
| AD-006 | P0 | As an Audit user, I can self-serve audit log export by date, actor, and entity reference. | UI/API provides filtered export with retained schema and timezone consistency. | Gap | No audit export endpoint/UI found. |
| AD-007 | P1 | As an Audit user, I can verify PDF authenticity against server-side registry. | Verification endpoint validates document fingerprint/token against stored record. | Gap | No server-side document verification registry endpoint found. |
| AD-008 | P1 | As an Audit user, I have read-only auditor role separate from admin. | RBAC includes dedicated auditor role with non-destructive permissions. | Gap | Current model appears admin/non-admin only. |

## Open Gap Summary (Release Gate Focus)

The following `P0` stories are currently not fully met and should be treated as release blockers unless risk-accepted:

- `CU-006` Claim/case reference persistence.
- `AU-007` Emergency admin refresh control.
- `CO-006` Mandatory reason for false-positive override (currently partial).
- `CO-007` Maker-checker approvals for override actions.
- `FI-001` Finance usage visibility and reporting.
- `AD-005` Immutable/tamper-evident audit log.
- `AD-006` Self-service audit log export.

## Suggested Next Step

Create delivery tickets directly from each `Gap` story ID and map them into phased releases:

- Phase 1: Close all `P0` gaps.
- Phase 2: Close `P1` governance/reporting gaps.
- Phase 3: Add UX and automation improvements.

