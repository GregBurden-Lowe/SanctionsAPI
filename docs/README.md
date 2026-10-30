# Documentation

Design and architecture docs for the Sanctions API.

| Document | Description |
|----------|-------------|
| **[SCREENING_PERSISTENCE.md](SCREENING_PERSISTENCE.md)** | PostgreSQL-backed screening cache and job queue: schema, entity key, request flow, worker, and API behaviour. |
| **[INTERNAL_SCREENING_API.md](INTERNAL_SCREENING_API.md)** | Internal queue-ingestion API for bulk/external screening: endpoints, security (API key / IP allowlist), and response semantics. |
| **[SCREENING_DECISION_RULES.md](SCREENING_DECISION_RULES.md)** | Current decision logic and thresholds for sanctions/PEP outcomes, including what "under current rules" means. |

See the [project README](../README.md) for setup, database connection, and deployment.

- [Screening history foundation and release procedure](SCREENING_HISTORY_RELEASE.md)
- [Local migration rehearsal results and reproduction](SCREENING_MIGRATION_REHEARSAL.md)
- [Matching consistency, failure tests and performance measurements](SCREENING_VALIDATION.md)
- [Interrupted-job recovery, settings and rollout](SCREENING_JOB_RECOVERY.md)
- [Duplicate submissions, synchronous recovery and linked retries](SCREENING_REQUEST_RECOVERY.md)
- [Read-only retention inventory and policy requirements](SCREENING_RETENTION_PREVIEW.md)
- [Local release review and outstanding conditions](SCREENING_RELEASE_REVIEW.md)
- [Generate acceptance candidate packs from Admin tools](SCREENING_ACCEPTANCE_EXPORT.md)
