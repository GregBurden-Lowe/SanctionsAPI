# Risk assessment (placeholder)

Use this document to record and review security and privacy risks for the Sanctions/PEP Screening API.

## Purpose

- Identify and assess risks to confidentiality, integrity, and availability of the service and personal data.
- Link controls (see [security-policy.md](security-policy.md)) and track residual risk.
- Support regulatory expectations (e.g. ICO, FCA SYSC where applicable).

## Suggested structure

| Risk ID | Description | Likelihood | Impact | Mitigation / control | Residual | Owner |
|--------|-------------|------------|--------|----------------------|----------|--------|
| (example) | Weak JWT secret in production | Low | High | GUI_JWT_SECRET enforced at startup | Low | [Assign] |
| … | … | … | … | … | … | … |

*Add rows for threats such as: credential compromise, data breach, denial of service, third-party failure, inadequate retention.*

## MFA

- **Risk:** Admin accounts protected only by password.
- **Planned mitigation:** MFA (TOTP or similar) for admin users — document as follow-on in security policy and track here until implemented.

## Review

- Review and update this assessment at least annually or when the system or threat landscape changes.
- Record review date and reviewer in [security-policy.md](security-policy.md).
