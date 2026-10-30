"""Aggregate-only retention inventory. No deletion policy or apply operation."""
from datetime import datetime, timezone

TABLES = {
    'screening_subjects': ('created_at', 'Subject groups'),
    'screening_requests': ('COALESCE(finished_at, requested_at)', 'Requests and saved responses'),
    'screening_runs': ('screened_at', 'Engine snapshots'),
    'screening_review_decisions': ('decided_at', 'Review decisions'),
    'screening_review_events': ('recorded_at', 'Review events'),
    'screening_job_events': ('recorded_at', 'Job recovery events'),
    'screened_entities': ('last_screened_at', 'Current screening state'),
    'screening_jobs': ('COALESCE(finished_at, created_at)', 'Operational jobs'),
    'ai_triage_recommendations': ('created_at', 'AI suggestions and reviewer actions'),
}


async def preview(conn, before=None):
    if before is not None:
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError('The comparison cutoff must include a timezone.')
        if before > datetime.now(timezone.utc):
            raise ValueError('The comparison cutoff cannot be in the future.')
    async with conn.transaction(isolation='repeatable_read', readonly=True):
        await conn.execute("SET LOCAL statement_timeout='15s'")
        generated_at=await conn.fetchval('SELECT transaction_timestamp()')
        inventory=[]
        for table,(date_basis,label) in TABLES.items():
            # Identifiers are fixed constants, never supplied by the caller.
            if not await conn.fetchval('SELECT to_regclass($1) IS NOT NULL','public.'+table):
                raise RuntimeError('Retention preview requires the current screening schema.')
            row=await conn.fetchrow(f'''SELECT count(*) AS total,
                min({date_basis}) AS oldest, max({date_basis}) AS newest,
                CASE WHEN $1::timestamptz IS NULL THEN NULL ELSE count(*) FILTER(WHERE {date_basis}<$1) END AS older_than_cutoff,
                pg_total_relation_size('public.{table}'::regclass) AS storage_bytes
                FROM {table}''',before)
            inventory.append(dict(table=table,label=label,date_basis=date_basis,**dict(row)))
        active=dict(await conn.fetchrow('''SELECT
            (SELECT count(*) FROM screening_requests WHERE status IN ('requested','queued')) AS unfinished_requests,
            (SELECT count(*) FROM screening_jobs WHERE status IN ('pending','running')) AS active_jobs,
            (SELECT count(*) FROM screened_entities WHERE review_status='IN_REVIEW' OR review_outcome='Pending External Review'
                OR (review_status IS NULL AND status NOT ILIKE 'Cleared%')) AS open_reviews,
            (SELECT count(*) FROM screening_requests WHERE idempotency_key IS NOT NULL) AS duplicate_protection_keys,
            (SELECT count(*) FROM screening_requests WHERE retry_of IS NOT NULL) AS linked_retries,
            (SELECT count(*) FROM ai_triage_recommendations WHERE status='PENDING_REVIEW') AS pending_ai_suggestions'''))
        dependencies=None
        if before is not None:
            dependencies=dict(await conn.fetchrow('''SELECT
                (SELECT count(*) FROM screening_requests q WHERE COALESCE(q.finished_at,q.requested_at)<$1
                    AND q.status IN ('requested','queued')) AS old_unfinished_requests,
                (SELECT count(*) FROM screening_requests q WHERE COALESCE(q.finished_at,q.requested_at)<$1
                    AND q.idempotency_key IS NOT NULL) AS old_requests_with_duplicate_protection,
                (SELECT count(*) FROM screening_requests q WHERE COALESCE(q.finished_at,q.requested_at)<$1
                    AND EXISTS(SELECT 1 FROM screening_requests child WHERE child.retry_of=q.request_id)) AS old_retry_parents,
                (SELECT count(*) FROM screening_runs r WHERE r.screened_at<$1
                    AND EXISTS(SELECT 1 FROM screened_entities e WHERE e.current_run_id=r.run_id)) AS old_runs_used_by_current_state,
                (SELECT count(*) FROM screening_runs r WHERE r.screened_at<$1
                    AND EXISTS(SELECT 1 FROM screening_requests q WHERE q.run_id=r.run_id AND q.requested_at>=$1)) AS old_runs_used_by_recent_requests,
                (SELECT count(*) FROM screening_runs r WHERE r.screened_at<$1 AND (
                    EXISTS(SELECT 1 FROM screening_review_decisions d WHERE d.run_id=r.run_id)
                    OR EXISTS(SELECT 1 FROM screening_review_events e WHERE e.run_id=r.run_id))) AS old_runs_with_review_evidence,
                (SELECT count(*) FROM screening_runs r WHERE r.screened_at<$1
                    AND EXISTS(SELECT 1 FROM screening_jobs j WHERE j.run_id=r.run_id)) AS old_runs_linked_to_jobs,
                (SELECT count(*) FROM screened_entities e WHERE e.last_screened_at<$1 AND
                    (e.review_status='IN_REVIEW' OR e.review_outcome='Pending External Review' OR (e.review_status IS NULL AND e.status NOT ILIKE 'Cleared%'))) AS old_current_rows_with_open_reviews,
                (SELECT count(*) FROM screened_entities e WHERE e.last_screened_at<$1
                    AND EXISTS(SELECT 1 FROM screening_jobs j WHERE j.entity_key=e.entity_key AND j.status IN ('pending','running'))) AS old_current_rows_with_active_jobs,
                (SELECT count(*) FROM screening_jobs j WHERE COALESCE(j.finished_at,j.created_at)<$1 AND
                    EXISTS(SELECT 1 FROM screening_requests q WHERE q.job_id=j.job_id AND q.status IN ('requested','queued'))) AS old_jobs_with_unfinished_requests
                ''',before))
        return dict(mode='preview_only',policy_status='awaiting_organisation_policy',deletion_enabled=False,
            generated_at=generated_at,comparison_before=before,inventory=inventory,active=active,dependencies=dependencies,
            notes=[
                'Older-than counts are a hypothetical comparison, not approval or eligibility for deletion.',
                'Dependency counts overlap and must not be added together.',
                'Current-state and job cleanup settings are separate from evidence retention.',
                'Storage includes indexes and auxiliary database storage; it is not estimated reclaimable disk space.',
                'This report excludes backups, compatibility CSV files, application logs and external systems.',
                'No legal holds, approved retention period or archive policy are configured by this report.',
            ])
