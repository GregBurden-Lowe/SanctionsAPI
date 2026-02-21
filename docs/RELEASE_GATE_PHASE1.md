# Release Gate User Stories  
## Phase 1 – Internal Operational Release

This document defines the **minimum operational and compliance controls required for Phase 1 release** of the internal screening tool.

This system is:

- An internal compliance support tool
- Used for claims, onboarding, and partner due diligence
- Not a client-facing regulated screening service
- Not a monetised SaaS product

The goal of Phase 1 is **operational defensibility**, not enterprise governance maturity.

---

# Release Gate Rule

## GO
All **Phase 1 Mandatory (P1-M)** stories are Implemented.

## NO-GO
Any **Phase 1 Mandatory (P1-M)** story is Partial or Gap.

Phase 1b and Phase 2 items do not block release.

---

# Status Legend

- `Implemented`
- `Partial`
- `Gap`

---

# Definitions

## Business Reference

A mandatory identifier linking the screening to a legitimate business action.

Examples:
- Claim reference
- Onboarding ID
- Payment reference
- Partner review reference

This field must:
- Be required
- Be non-empty
- Be stored
- Be searchable
- Be included in PDF exports

---

## Reason for Check (Mandatory Enum)

Every screening must include one of the following values:

- Client Onboarding
- Claim Payment
- Business Partner Payment
- Business Partner Due Diligence
- Periodic Re-Screen
- Ad-Hoc Compliance Review

Free-text reasons are not permitted in Phase 1.

---

# Claims / Operations User Stories

## Phase 1 Mandatory (P1-M)

| ID | User Story | Acceptance Criteria | Status |
|----|------------|--------------------|--------|
| CU-001 | As a user, I can run a sanctions/PEP check for a person or organisation. | Input supports name, entity type, optional DOB; check returns decision summary. | Implemented |
| CU-002 | As a user, I can view clear outcome guidance (Sanction / PEP / Cleared). | Result shows risk level, score, decision, and guidance text. | Implemented |
| CU-003 | As a user, I can export a screening PDF for file evidence. | PDF contains decision, timestamp, entity key, sources, business reference, and reason for check. | Implemented |
| CU-004 | As a user, I must record a business reference with each screening. | Required, non-empty, stored in DB, searchable, included in PDF export. | Gap |
| CU-005 | As a user, I must select a reason for check from an approved list. | Enum validation enforced; invalid values rejected; stored in DB; included in audit and PDF. | Gap |
| CU-006 | As a user, I can claim a potential match for review. | Only unreviewed matches can be claimed; review_status becomes IN_REVIEW; review_claimed_by and review_claimed_at stored; action is audit logged. | Gap |
| CU-007 | As a user, I must record a structured outcome when completing a review. | review_outcome required (enum); review_notes required (min length 10); review_status becomes COMPLETED; review_completed_by and review_completed_at stored; screening result remains unchanged; action is audit logged. | Gap |
| CU-008 | As a user, I can view a dedicated queue of potential matches requiring review. | Queue displays entity_name, entity_key, decision, business_reference, reason_for_check, screening_user, screening_timestamp, review_status, review_claimed_by. | Gap |

---

# Compliance User Stories

## Phase 1 Mandatory (P1-M)

| ID | User Story | Acceptance Criteria | Status |
|----|------------|--------------------|--------|
| CO-001 | Sanctions outcomes take precedence over PEP outcomes. | If both match, sanction status remains primary and PEP flag is surfaced. | Implemented |
| CO-002 | Screening decisions are stored with full traceable evidence. | Stored inputs, match data, decision result, business reference, reason for check, actor, and timestamp retained. | Implemented |
| CO-003 | False-positive overrides require a mandatory reason. | Override request rejects empty reason and stores justification. | Partial |
| CO-004 | Source data is restricted to approved sanctions + PEP lists. | Source allowlist enforced in refresh logic. | Implemented |
| CO-005 | UK sanctions list changes trigger re-screen queueing. | Delta detection queues impacted entities for re-screening. | Implemented |
| CO-006 | Screening immutability. | Review actions do not modify original screening decision data. | Gap |

---

# Admin User Stories

## Phase 1 Mandatory (P1-M)

| ID | User Story | Acceptance Criteria | Status |
|----|------------|--------------------|--------|
| AU-001 | Only authorised staff can perform screening. | Authentication enforced; screening and admin routes protected. | Partial |
| AU-002 | Admin can monitor screening job status. | Jobs list shows pending/running/completed/failed. | Implemented |

---

# Audit User Stories

## Phase 1 Mandatory (P1-M)

| ID | User Story | Acceptance Criteria | Status |
|----|------------|--------------------|--------|
| AD-001 | Every screening has a stable reference key. | Entity key present in results and exports. | Implemented |
| AD-002 | All screening actions record actor and timestamp. | Authenticated user, business reference, reason for check, and outcome logged. | Partial |
| AD-003 | Audit records cannot be modified via application UI. | No UI route supports editing/deleting audit events. | Implemented |
| AD-004 | Review audit trail is preserved. | REVIEW_CLAIMED and REVIEW_COMPLETED events logged; includes user, entity_key, business_reference, reason_for_check, outcome (if completed); completed reviews cannot be edited. | Gap |

---

# Phase 1b – Operational Enhancements (Non-Blocking)

| ID | User Story |
|----|------------|
| AU-003 | Admin can trigger emergency watchlist refresh. |
| AU-004 | Admin can clear non-production test data. |
| CU-006 | Users can search prior screenings by business reference. |

---

# Phase 2 – Governance Hardening

| ID | User Story |
|----|------------|
| CO-007 | High-risk overrides require maker-checker approval. |
| CO-008 | Screening rule thresholds are version-controlled and approvable. |
| AD-005 | Audit logs are cryptographically tamper-evident. |
| AD-006 | Audit logs can be exported via UI by date/actor/entity. |
| AD-007 | Dedicated read-only auditor role exists. |
| FI-001 | Usage reporting by team/month/client. |
| FI-002 | Billing-ready CSV export. |
| FI-003 | Consolidated usage ledger across UI/API. |
| FI-004 | Cost centre tagging support. |

---

# Phase 1 Release Summary

## Phase 1 is READY when:

- Business reference is mandatory and persisted
- Reason for check is mandatory and validated against approved list
- Override reason is mandatory and persisted
- Screening endpoints require authentication
- Screening actions are audit logged with actor, timestamp, business reference, and reason
- Review queue exists
- Review claim and completion recorded
- Review outcomes mandatory enum
- Review notes mandatory
- Review actions audit logged
- Screening data immutable
- Sanctions logic precedence enforced
- Approved data sources enforced
- No UI route allows audit deletion

---

# Current Release Status

## 🚫 NO-GO (Pending Implementation)

- CU-004 – Business reference persistence
- CU-005 – Mandatory reason for check (enum)
- CO-003 – Mandatory override reason
- AU-001 – Screening endpoints require authentication
- AD-002 – Screening execution audit logging

Once all P1-M items are Implemented:

## 🟢 GO – Phase 1 Internal Operational Release Approved

Stop iterating.
Move all remaining items to Phase 2 backlog.
