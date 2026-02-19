# DPIA / processing record

Data Protection Impact Assessment and processing record for the Sanctions/PEP Screening API. Complete and review in line with UK GDPR (Articles 5, 25, 30, 35).

## 1. Purpose of processing

- **Service:** Sanctions and PEP (Politically Exposed Persons) screening of names (and optionally organisations).
- **Purpose:** To support legal/compliance checks by authorised users; screening results are cached and searchable for a limited period.

## 2. Categories of personal data

| Data | Purpose | Stored in |
|------|---------|-----------|
| **User account data** (email, password hash, admin flag, “must change password”) | GUI login and access control | `users` table |
| **Requestors** (name/identifier of person requesting a check) | Audit and screening context | Screening requests; `screened_entities.last_requestor`; logs |
| **Screened names** (and optional DoB) | Input to screening; displayed in results | `screened_entities`; job queue; logs |
| **Access request emails** | Sign-up / access requests | `access_requests` table (if used) |

*Do not log passwords or tokens.*

## 3. Lawful basis

- **Users:** Contract or legitimate interest (providing the screening service to authorised staff).
- **Requestors and screened names:** Legitimate interest (compliance/legal screening) or legal obligation, depending on your use case. Document your chosen basis per processing activity.

## 4. Retention

- **Screened entities:** Configurable via `SCREENED_ENTITIES_RETENTION_MONTHS`; see [data-retention.md](data-retention.md).
- **Screening jobs:** Configurable via `SCREENING_JOBS_RETENTION_DAYS` (worker cleanup).
- **Users:** No automatic retention in code; define policy (e.g. inactive account removal) and document in data-retention and runbooks.
- **Audit logs:** Retain per your policy (e.g. 90 days); configure at logging/aggregator level.
- **Search log (CSV):** Define retention and rotation; see [data-retention.md](data-retention.md).

## 5. Recipients and transfers

- **Recipients:** Authorised users of the service; no sale of data.
- **Transfers:** If hosting or sub-processors are outside the UK/EEA, document transfer mechanism (e.g. adequacy, SCCs) and sub-processor list.

## 6. Security measures

- See [security-policy.md](security-policy.md): access control, rate limiting, audit logging, no exposure of internal errors, TLS via reverse proxy, DB encryption at rest (managed), trusted proxy for client IP.

## 7. Data subject rights

- Define how you respond to access, rectification, erasure, restriction, portability, and objection (e.g. via support process or data protection contact). Document in your privacy notice and internal procedures.

## 8. Review

- Review this record when processing, technology, or risk changes; link to risk assessment and security policy review dates.
