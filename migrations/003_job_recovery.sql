-- Deploy with all old workers stopped: old workers do not honour lease fencing.
ALTER TABLE screening_jobs ADD COLUMN IF NOT EXISTS lease_token UUID;
ALTER TABLE screening_jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
ALTER TABLE screening_jobs ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;
-- Give interrupted legacy jobs a bounded grace period on upgrade, only once.
UPDATE screening_jobs SET lease_expires_at=clock_timestamp()+interval '5 minutes'
WHERE status='running' AND lease_expires_at IS NULL;
CREATE INDEX IF NOT EXISTS screening_jobs_expired_idx ON screening_jobs(lease_expires_at) WHERE status='running';
CREATE TABLE IF NOT EXISTS screening_job_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    lease_token UUID,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS screening_job_events_job_idx ON screening_job_events(job_id, recorded_at);
CREATE OR REPLACE FUNCTION capture_screening_job_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.status, NEW.lease_token) IS DISTINCT FROM (OLD.status, OLD.lease_token) THEN
        INSERT INTO screening_job_events(job_id,status,attempt_count,lease_token,reason)
        VALUES(NEW.job_id,NEW.status,NEW.attempt_count,NEW.lease_token,NEW.error_message);
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS screening_job_event_capture ON screening_jobs;
CREATE TRIGGER screening_job_event_capture AFTER UPDATE ON screening_jobs
FOR EACH ROW EXECUTE FUNCTION capture_screening_job_event();
DROP TRIGGER IF EXISTS screening_job_event_immutable ON screening_job_events;
CREATE TRIGGER screening_job_event_immutable BEFORE UPDATE OR DELETE ON screening_job_events
FOR EACH ROW EXECUTE FUNCTION protect_screening_run();
