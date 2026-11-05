-- The review queue looks up each item's request by run; without this index every
-- item scans the whole table. The partial index serves the queue's order and count.
CREATE INDEX IF NOT EXISTS screening_requests_run_idx ON screening_requests(run_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS screened_entities_review_queue_idx ON screened_entities(last_screened_at DESC, entity_key)
WHERE status NOT ILIKE 'Cleared%';
