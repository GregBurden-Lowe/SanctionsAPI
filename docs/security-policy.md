# Security policy

High-level security and resilience expectations for the Sanctions/PEP Screening API. Update owners and review dates to match your organisation.

## 1. Ownership and review

| Item | Owner | Next review |
|------|--------|-------------|
| Security policy | [Assign] | [Date] |
| Risk assessment | [Assign] | [Date] |
| DPIA / processing records | [Assign] | [Date] |
| Incident response & breach procedure | [Assign] | [Date] |

*Review at least annually or when significant changes occur.*

## 2. Technical controls (summary)

- **Authentication:** GUI login with JWT; strong secret required when DB is used (`GUI_JWT_SECRET`). No default admin seed in production.
- **Authorization:** Admin-only actions and refresh protected; internal screening API by API key and/or IP allowlist (prefer API key; IP only behind trusted proxy).
- **Rate limiting:** Applied to login, signup, opcheck, refresh to limit abuse.
- **Errors:** No raw backend errors or stack traces returned to clients; generic message and server-side logging.
- **Client IP:** `X-Forwarded-For` used only when the direct client is in `TRUSTED_PROXY_IPS`.
- **Audit:** Structured audit events for auth, admin actions, and access to screened data (see application logger `sanctions.audit`).
- **TLS:** Handled by reverse proxy; application must be fronted by HTTPS in production.
- **Database:** Use managed encryption at rest and least-privilege DB user; no application-level DB encryption.

## 3. Operational resilience

- **Backups:** Database and any critical configuration/files must be backed up. Backups must be **restore-tested** periodically (e.g. annually or per your SLA) and the outcome documented.
- **Patch and vulnerability management:** Apply security patches to OS, runtime, and dependencies within agreed SLAs (e.g. critical within N days). Consider running `pip audit` (or equivalent) in CI and acting on findings.
- **Third-party and cloud risk:** Document third parties (e.g. Resend, hosting provider, DB provider): contracts, access, exit arrangements, and incident/breach obligations. Align with FCA SYSC and FG16/5 where applicable (outsourcing/cloud).

## 4. MFA and access control

- **MFA:** Multi-factor authentication for admin users is planned as a follow-on; document in risk assessment until implemented.
- **Access reviews:** Access to production and sensitive data should be reviewed periodically; record where access review and pen test results are kept (see `docs/access-reviews-pentest.md`).

## 5. Related documents

- [Incident response runbook](runbooks/incident-response.md)
- [Breach log and procedure](breach-log-procedure.md)
- [DPIA / processing record](dpia.md)
- [Data retention](data-retention.md)
- [Access reviews and pen test](access-reviews-pentest.md)
