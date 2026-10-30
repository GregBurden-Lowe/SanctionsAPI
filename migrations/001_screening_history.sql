-- Additive migration. Run after screening_db.ensure_schema's existing schema.
CREATE TABLE IF NOT EXISTS screening_subjects (
    subject_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- entity_key is a technical grouping, not proof of a real-world identity.
CREATE TABLE IF NOT EXISTS screening_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id UUID NOT NULL REFERENCES screening_subjects(subject_id),
    entity_key TEXT NOT NULL,
    screened_at TIMESTAMPTZ NOT NULL,
    snapshot_kind TEXT NOT NULL CHECK (snapshot_kind IN ('screening', 'legacy_snapshot', 'manual_decision')),
    inputs JSONB NOT NULL,
    result_json JSONB NOT NULL,
    refresh_run_id UUID,
    uk_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE screened_entities ADD COLUMN IF NOT EXISTS current_run_id UUID REFERENCES screening_runs(run_id);
ALTER TABLE screening_jobs ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES screening_runs(run_id);
CREATE TABLE IF NOT EXISTS screening_requests (
    request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor TEXT,
    source TEXT NOT NULL,
    inputs JSONB NOT NULL,
    business_reference TEXT,
    status TEXT NOT NULL DEFAULT 'requested' CHECK (status IN ('requested', 'queued', 'completed', 'failed')),
    reused BOOLEAN NOT NULL DEFAULT false,
    run_id UUID REFERENCES screening_runs(run_id),
    job_id UUID,
    response_json JSONB,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS screening_requests_reference_idx ON screening_requests(business_reference, requested_at DESC);
CREATE INDEX IF NOT EXISTS screening_requests_time_idx ON screening_requests(requested_at DESC, request_id);
CREATE INDEX IF NOT EXISTS screening_requests_job_idx ON screening_requests(job_id);
CREATE INDEX IF NOT EXISTS screening_runs_entity_idx ON screening_runs(entity_key, screened_at DESC);
CREATE OR REPLACE FUNCTION capture_screening_run() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE sid UUID; rid UUID; kind TEXT;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.last_screened_at IS NOT DISTINCT FROM OLD.last_screened_at
           AND NEW.result_json IS NOT DISTINCT FROM OLD.result_json THEN
            RETURN NEW;
        END IF;
    END IF;
    INSERT INTO screening_subjects(entity_key) VALUES (NEW.entity_key) ON CONFLICT DO NOTHING;
    SELECT subject_id INTO sid FROM screening_subjects WHERE entity_key = NEW.entity_key;
    kind := CASE WHEN NEW.result_json ? 'Manual Override' THEN 'manual_decision' ELSE 'screening' END;
    INSERT INTO screening_runs(subject_id, entity_key, screened_at, snapshot_kind, inputs, result_json, refresh_run_id, uk_hash)
    VALUES (sid, NEW.entity_key, NEW.last_screened_at, kind,
        COALESCE(NEW.result_json->'Screening Inputs', jsonb_build_object(
            'name', NEW.display_name, 'dob', NEW.date_of_birth, 'country', NEW.country_input, 'entity_type', NEW.entity_type)),
        NEW.result_json, NEW.screened_against_refresh_run_id, NEW.screened_against_uk_hash)
    RETURNING run_id INTO rid;
    UPDATE screened_entities SET current_run_id = rid WHERE entity_key = NEW.entity_key;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS screening_run_capture ON screened_entities;
CREATE TRIGGER screening_run_capture AFTER INSERT OR UPDATE ON screened_entities FOR EACH ROW EXECUTE FUNCTION capture_screening_run();
-- Backfill only evidence that still exists. Never invent lost requests or provenance.
INSERT INTO screening_subjects(entity_key) SELECT entity_key FROM screened_entities ON CONFLICT DO NOTHING;
WITH snapshots AS (
    INSERT INTO screening_runs(subject_id, entity_key, screened_at, snapshot_kind, inputs, result_json, refresh_run_id, uk_hash)
    SELECT s.subject_id, e.entity_key, e.last_screened_at, 'legacy_snapshot',
        jsonb_build_object('name', e.display_name, 'dob', e.date_of_birth, 'country', e.country_input,
            'entity_type', e.entity_type, 'requestor', e.last_requestor, 'business_reference', e.business_reference,
            'reason_for_check', e.reason_for_check), e.result_json, e.screened_against_refresh_run_id, e.screened_against_uk_hash
    FROM screened_entities e JOIN screening_subjects s USING(entity_key) WHERE e.current_run_id IS NULL
    RETURNING run_id, entity_key
)
UPDATE screened_entities e SET current_run_id = s.run_id FROM snapshots s WHERE e.entity_key = s.entity_key;
CREATE OR REPLACE FUNCTION protect_screening_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Screening runs are immutable'; END $$;
DROP TRIGGER IF EXISTS screening_run_immutable ON screening_runs;
CREATE TRIGGER screening_run_immutable BEFORE UPDATE OR DELETE ON screening_runs FOR EACH ROW EXECUTE FUNCTION protect_screening_run();
CREATE OR REPLACE FUNCTION finish_screening_job_requests() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'completed' AND NEW.run_id IS NULL THEN
        RAISE EXCEPTION 'Completed screening jobs require a preserved run; upgrade the worker';
    END IF;
    IF NEW.status IN ('completed', 'failed') THEN
        UPDATE screening_requests SET status = NEW.status, run_id = NEW.run_id,
            response_json = CASE WHEN NEW.status = 'completed' THEN
                (SELECT result_json || jsonb_build_object('entity_key', entity_key, 'run_id', run_id, 'screened_at', screened_at)
                 FROM screening_runs WHERE run_id = NEW.run_id)
                ELSE jsonb_build_object('error', 'screening_failed', 'message', 'Screening could not be completed.') END,
            finished_at = NEW.finished_at
        WHERE job_id = NEW.job_id AND status IN ('requested', 'queued');
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS screening_job_requests ON screening_jobs;
CREATE TRIGGER screening_job_requests AFTER UPDATE ON screening_jobs FOR EACH ROW EXECUTE FUNCTION finish_screening_job_requests();

INSERT INTO screening_requests(source, inputs, business_reference, status, run_id, response_json, requested_at, finished_at)
SELECT 'legacy_snapshot', r.inputs, r.inputs->>'business_reference', 'completed', r.run_id,
       r.result_json || jsonb_build_object('entity_key', r.entity_key, 'run_id', r.run_id), r.screened_at, r.screened_at
FROM screening_runs r WHERE snapshot_kind='legacy_snapshot'
AND NOT EXISTS (SELECT 1 FROM screening_requests q WHERE q.run_id=r.run_id AND q.source='legacy_snapshot');
CREATE TABLE IF NOT EXISTS screening_review_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_key TEXT NOT NULL,
    run_id UUID REFERENCES screening_runs(run_id),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    evidence JSONB NOT NULL
);
CREATE OR REPLACE FUNCTION capture_screening_review_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.review_status, NEW.review_outcome, NEW.review_notes, NEW.review_claimed_by,
        NEW.review_completed_at, NEW.result_json->'Manual Override') IS DISTINCT FROM
       (OLD.review_status, OLD.review_outcome, OLD.review_notes, OLD.review_claimed_by,
        OLD.review_completed_at, OLD.result_json->'Manual Override') THEN
        INSERT INTO screening_review_events(entity_key, run_id, evidence)
        VALUES (NEW.entity_key, NEW.current_run_id, jsonb_build_object(
            'status', NEW.review_status, 'outcome', NEW.review_outcome, 'notes', NEW.review_notes,
            'claimed_by', NEW.review_claimed_by, 'claimed_at', NEW.review_claimed_at,
            'completed_by', NEW.review_completed_by, 'completed_at', NEW.review_completed_at,
            'manual_override', NEW.result_json->'Manual Override'));
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS screening_review_event_capture ON screened_entities;
CREATE TRIGGER screening_review_event_capture AFTER UPDATE ON screened_entities
FOR EACH ROW EXECUTE FUNCTION capture_screening_review_event();
DROP TRIGGER IF EXISTS screening_review_event_immutable ON screening_review_events;
CREATE TRIGGER screening_review_event_immutable BEFORE UPDATE OR DELETE ON screening_review_events
FOR EACH ROW EXECUTE FUNCTION protect_screening_run();
