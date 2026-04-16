-- Screening persistence: one row per entity (current state) + job queue.
-- Run once against your PostgreSQL database (e.g. psql -f schema.sql).

-- Current screening state per entity. One row per entity_key; overwritten on re-screen.
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
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_screened_entities_valid_until
    ON screened_entities (screening_valid_until);
CREATE INDEX IF NOT EXISTS idx_screened_entities_last_screened
    ON screened_entities (last_screened_at);

-- Queue of screening jobs. Processed by 1–2 workers; completed/failed rows can be pruned.
CREATE TABLE IF NOT EXISTS screening_jobs (
    job_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_key      TEXT NOT NULL,

    name            TEXT NOT NULL,
    date_of_birth   TEXT,
    country         TEXT,
    entity_type     TEXT NOT NULL DEFAULT 'Person',
    requestor       TEXT NOT NULL,

    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    error_message   TEXT
);

-- No FK from screening_jobs to screened_entities: jobs are enqueued before the entity row exists.

CREATE INDEX IF NOT EXISTS idx_screening_jobs_pending
    ON screening_jobs (created_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_screening_jobs_status
    ON screening_jobs (status);

-- GUI users (login, password change, admin user management). Requires DATABASE_URL.
CREATE TABLE IF NOT EXISTS users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT UNIQUE NOT NULL,
    password_hash        TEXT NOT NULL,
    must_change_password BOOLEAN NOT NULL DEFAULT true,
    is_admin            BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

-- Pending access requests (email only); admin grants by creating user with temp password.
CREATE TABLE IF NOT EXISTS access_requests (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email        TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_access_requests_email ON access_requests (email);

-- AI triage runs and human-reviewable recommendation queue.
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
