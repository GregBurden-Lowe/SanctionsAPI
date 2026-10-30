ALTER TABLE screening_requests ADD COLUMN IF NOT EXISTS idempotency_key TEXT;
ALTER TABLE screening_requests ADD COLUMN IF NOT EXISTS payload_hash TEXT;
ALTER TABLE screening_requests ADD COLUMN IF NOT EXISTS response_status INTEGER;
ALTER TABLE screening_requests ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE screening_requests ADD COLUMN IF NOT EXISTS retry_of UUID REFERENCES screening_requests(request_id);
CREATE UNIQUE INDEX IF NOT EXISTS screening_request_idempotency_idx
ON screening_requests ((COALESCE(actor,'')),source,idempotency_key) WHERE idempotency_key IS NOT NULL;
-- One bounded grace period for pre-upgrade synchronous requests. Queued work uses job leases.
UPDATE screening_requests SET expires_at=clock_timestamp()+interval '10 minutes'
WHERE status='requested' AND job_id IS NULL AND expires_at IS NULL;
CREATE INDEX IF NOT EXISTS screening_requests_expiry_idx ON screening_requests(expires_at)
WHERE status='requested' AND job_id IS NULL;
