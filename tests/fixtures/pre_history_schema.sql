-- Pre-history schema from screening_db.ensure_schema at 09a535a2d2debeab1cf13d5a5cfffab6223fdfcd
-- Synthetic migration rehearsal only.

CREATE TABLE IF NOT EXISTS screened_entities (
            entity_key       TEXT PRIMARY KEY,
            display_name     TEXT NOT NULL,
            normalized_name  TEXT NOT NULL,
            date_of_birth    DATE,
            country_input    TEXT,
            entity_type      TEXT NOT NULL DEFAULT 'Person',
            last_screened_at     TIMESTAMPTZ NOT NULL,
            screening_valid_until TIMESTAMPTZ NOT NULL,
            status          TEXT NOT NULL,
            risk_level      TEXT NOT NULL,
            confidence      TEXT NOT NULL,
            score           NUMERIC(5,2) NOT NULL,
            uk_sanctions_flag BOOLEAN NOT NULL DEFAULT FALSE,
            pep_flag        BOOLEAN NOT NULL DEFAULT FALSE,
            result_json     JSONB NOT NULL,
            last_requestor  TEXT,
            business_reference TEXT,
            reason_for_check TEXT,
            review_status TEXT,
            review_claimed_by TEXT,
            review_claimed_at TIMESTAMPTZ,
            review_outcome TEXT,
            review_notes TEXT,
            review_completed_by TEXT,
            review_completed_at TIMESTAMPTZ,
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

CREATE INDEX IF NOT EXISTS idx_screened_entities_valid_until
        ON screened_entities (screening_valid_until);

CREATE TABLE IF NOT EXISTS screening_jobs (
            job_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_key      TEXT NOT NULL,
            name            TEXT NOT NULL,
            date_of_birth   TEXT,
            country         TEXT,
            entity_type     TEXT NOT NULL DEFAULT 'Person',
            requestor       TEXT NOT NULL,
            business_reference TEXT,
            reason_for_check TEXT,
            reason          TEXT NOT NULL DEFAULT 'manual',
            refresh_run_id  UUID,
            force_rescreen  BOOLEAN NOT NULL DEFAULT FALSE,
            status          TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'completed', 'failed')),
            previous_status TEXT,
            result_status   TEXT,
            transition      TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at      TIMESTAMPTZ,
            finished_at     TIMESTAMPTZ,
            error_message   TEXT
        );

CREATE INDEX IF NOT EXISTS idx_screening_jobs_pending
        ON screening_jobs (created_at) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS watchlist_refresh_runs (
            refresh_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ran_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            include_peps BOOLEAN NOT NULL DEFAULT TRUE,
            postgres_synced BOOLEAN NOT NULL DEFAULT FALSE,
            sanctions_rows INTEGER NOT NULL DEFAULT 0,
            peps_rows INTEGER NOT NULL DEFAULT 0,
            uk_hash TEXT,
            prev_uk_hash TEXT,
            uk_changed BOOLEAN NOT NULL DEFAULT FALSE,
            uk_row_count INTEGER NOT NULL DEFAULT 0,
            delta_added INTEGER NOT NULL DEFAULT 0,
            delta_removed INTEGER NOT NULL DEFAULT 0,
            delta_changed INTEGER NOT NULL DEFAULT 0,
            candidate_count INTEGER NOT NULL DEFAULT 0,
            queued_count INTEGER NOT NULL DEFAULT 0,
            already_pending_count INTEGER NOT NULL DEFAULT 0,
            reused_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0
        );

CREATE TABLE IF NOT EXISTS watchlist_uk_snapshot_entries (
            refresh_run_id UUID NOT NULL REFERENCES watchlist_refresh_runs(refresh_run_id) ON DELETE CASCADE,
            fingerprint TEXT NOT NULL,
            entity_id TEXT,
            name_norm TEXT NOT NULL,
            birth_date TEXT,
            dataset TEXT,
            regime TEXT,
            PRIMARY KEY (refresh_run_id, fingerprint)
        );

CREATE INDEX IF NOT EXISTS idx_watchlist_snapshot_name_norm
        ON watchlist_uk_snapshot_entries (name_norm);

CREATE TABLE IF NOT EXISTS ai_triage_runs (
            run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trigger_type TEXT NOT NULL,
            triggered_by TEXT,
            llm_runtime TEXT NOT NULL,
            llm_model TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            selected_count INTEGER NOT NULL DEFAULT 0,
            created_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            superseded_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ
        );

CREATE TABLE IF NOT EXISTS ai_triage_recommendations (
            triage_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID REFERENCES ai_triage_runs(run_id) ON DELETE SET NULL,
            entity_key TEXT NOT NULL,
            screening_state_hash TEXT NOT NULL,
            submitted_name TEXT NOT NULL,
            submitted_entity_type TEXT,
            matched_name TEXT,
            matched_entity_type TEXT,
            matched_birth_date TEXT,
            matched_country TEXT,
            source_label TEXT,
            screening_status TEXT,
            screening_risk_level TEXT,
            screening_score NUMERIC(5,2),
            llm_runtime TEXT NOT NULL,
            llm_model TEXT NOT NULL,
            raw_recommended_action TEXT NOT NULL,
            effective_recommended_action TEXT NOT NULL,
            ai_confidence_raw NUMERIC(5,4),
            ai_confidence_band TEXT,
            rationale_short TEXT,
            explanation_json JSONB,
            raw_output_json JSONB,
            result_snapshot_json JSONB NOT NULL,
            guardrail_overridden BOOLEAN NOT NULL DEFAULT FALSE,
            guardrail_reasons JSONB,
            status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
            human_decision TEXT,
            reviewer TEXT,
            reviewed_at TIMESTAMPTZ,
            reviewer_notes TEXT,
            final_screening_outcome TEXT,
            agreement_indicator TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

CREATE INDEX IF NOT EXISTS idx_ai_triage_recommendations_status
        ON ai_triage_recommendations (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ai_triage_recommendations_entity_key
        ON ai_triage_recommendations (entity_key, created_at DESC);

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS screened_against_uk_hash TEXT;

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS screened_against_refresh_run_id UUID;

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS manual_override_uk_hash TEXT;

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS manual_override_stale BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS business_reference TEXT;

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS reason_for_check TEXT;

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS country_input TEXT;

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_status TEXT;

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_claimed_by TEXT;

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_claimed_at TIMESTAMPTZ;

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_outcome TEXT;

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_notes TEXT;

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_completed_by TEXT;

ALTER TABLE screened_entities
        ADD COLUMN IF NOT EXISTS review_completed_at TIMESTAMPTZ;

ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT 'manual';

ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS business_reference TEXT;

ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS reason_for_check TEXT;

ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS country TEXT;

ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS refresh_run_id UUID;

ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS force_rescreen BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS previous_status TEXT;

ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS result_status TEXT;

ALTER TABLE screening_jobs
        ADD COLUMN IF NOT EXISTS transition TEXT;

CREATE INDEX IF NOT EXISTS idx_screening_jobs_refresh_run
        ON screening_jobs (refresh_run_id);
