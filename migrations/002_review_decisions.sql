-- Separate analyst decisions from immutable engine evidence.
ALTER TABLE screened_entities ADD COLUMN IF NOT EXISTS review_decision JSONB;
ALTER TABLE screened_entities ADD COLUMN IF NOT EXISTS review_claimed_run_id UUID REFERENCES screening_runs(run_id);
CREATE TABLE IF NOT EXISTS screening_review_decisions (
    decision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES screening_runs(run_id),
    entity_key TEXT NOT NULL,
    actor TEXT NOT NULL,
    outcome TEXT NOT NULL,
    notes TEXT NOT NULL,
    decision_type TEXT NOT NULL CHECK (decision_type IN ('analyst', 'system_no_match')),
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS screening_review_decisions_run_idx ON screening_review_decisions(run_id, decided_at);
DROP TRIGGER IF EXISTS screening_review_decision_immutable ON screening_review_decisions;
CREATE TRIGGER screening_review_decision_immutable BEFORE UPDATE OR DELETE ON screening_review_decisions
FOR EACH ROW EXECUTE FUNCTION protect_screening_run();
UPDATE screened_entities SET review_claimed_run_id=current_run_id
WHERE review_status='IN_REVIEW' AND review_claimed_run_id IS NULL;
CREATE OR REPLACE FUNCTION reset_review_on_new_screening() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.last_screened_at IS DISTINCT FROM OLD.last_screened_at
       OR NEW.result_json IS DISTINCT FROM OLD.result_json THEN
        NEW.review_decision := NULL;
        NEW.review_status := NULL;
        NEW.review_claimed_by := NULL;
        NEW.review_claimed_at := NULL;
        NEW.review_claimed_run_id := NULL;
        NEW.review_outcome := NULL;
        NEW.review_notes := NULL;
        NEW.review_completed_by := NULL;
        NEW.review_completed_at := NULL;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS screening_review_reset ON screened_entities;
CREATE TRIGGER screening_review_reset BEFORE UPDATE ON screened_entities
FOR EACH ROW EXECUTE FUNCTION reset_review_on_new_screening();
-- Capture the decision separately; never create a new engine run for a review.
CREATE OR REPLACE FUNCTION capture_screening_review_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.review_status, NEW.review_outcome, NEW.review_notes, NEW.review_claimed_by,
        NEW.review_completed_at, NEW.review_decision, NEW.manual_override_stale) IS DISTINCT FROM
       (OLD.review_status, OLD.review_outcome, OLD.review_notes, OLD.review_claimed_by,
        OLD.review_completed_at, OLD.review_decision, OLD.manual_override_stale) THEN
        INSERT INTO screening_review_events(entity_key, run_id, evidence)
        VALUES (NEW.entity_key, OLD.current_run_id, jsonb_build_object(
            'event_type', CASE WHEN NEW.last_screened_at IS DISTINCT FROM OLD.last_screened_at
                OR NEW.result_json IS DISTINCT FROM OLD.result_json THEN 'new_screening_review_reset'
                ELSE 'review_updated' END,
            'status', NEW.review_status, 'outcome', NEW.review_outcome, 'notes', NEW.review_notes,
            'claimed_by', NEW.review_claimed_by, 'claimed_at', NEW.review_claimed_at,
            'completed_by', NEW.review_completed_by, 'completed_at', NEW.review_completed_at,
            'decision', NEW.review_decision, 'decision_stale', NEW.manual_override_stale));
    END IF;
    RETURN NEW;
END $$;
-- A reused job preserves the effective response seen at completion, including
-- a decision already attached to that exact run. Later reviews never rewrite it.
ALTER TABLE screening_jobs ADD COLUMN IF NOT EXISTS response_json JSONB;
CREATE OR REPLACE FUNCTION finish_screening_job_requests() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'completed' AND NEW.run_id IS NULL THEN
        RAISE EXCEPTION 'Completed screening jobs require a preserved run; upgrade the worker';
    END IF;
    IF NEW.status IN ('completed', 'failed') THEN
        UPDATE screening_requests SET status=NEW.status, run_id=NEW.run_id,
            response_json=CASE WHEN NEW.status='completed' THEN COALESCE(NEW.response_json,
                (SELECT result_json || jsonb_build_object('entity_key', entity_key, 'run_id', run_id, 'screened_at', screened_at)
                 FROM screening_runs WHERE run_id=NEW.run_id))
                ELSE jsonb_build_object('error','screening_failed','message','Screening could not be completed.') END,
            finished_at=NEW.finished_at
        WHERE job_id=NEW.job_id AND status IN ('requested','queued');
    END IF;
    RETURN NEW;
END $$;
