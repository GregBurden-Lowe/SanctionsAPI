-- Screening persistence: one row per entity (current state) + job queue.
-- Run once against your PostgreSQL database (e.g. psql -f schema.sql).

-- Current screening state per entity. One row per entity_key; overwritten on re-screen.
CREATE TABLE IF NOT EXISTS screened_entities (
    entity_key       TEXT PRIMARY KEY,
    display_name     TEXT NOT NULL,
    normalized_name  TEXT NOT NULL,
    date_of_birth    DATE,
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
