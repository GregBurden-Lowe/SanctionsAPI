# Documentation

Design and architecture docs for the Sanctions API.

| Document | Description |
|----------|-------------|
| **[SCREENING_PERSISTENCE.md](SCREENING_PERSISTENCE.md)** | PostgreSQL-backed screening cache and job queue: schema, entity key, request flow, worker, and API behaviour. |
| **[INTERNAL_SCREENING_API.md](INTERNAL_SCREENING_API.md)** | Internal queue-ingestion API for bulk/external screening: endpoints, security (API key / IP allowlist), and response semantics. |
| **[SCREENING_DECISION_RULES.md](SCREENING_DECISION_RULES.md)** | Current decision logic and thresholds for sanctions/PEP outcomes, including what "under current rules" means. |

See the [project README](../README.md) for setup, database connection, and deployment.
