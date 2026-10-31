-- Automatic AI decisions are explicitly distinct from human decisions.
ALTER TABLE screening_review_decisions DROP CONSTRAINT IF EXISTS screening_review_decisions_decision_type_check;
ALTER TABLE screening_review_decisions ADD CONSTRAINT screening_review_decisions_decision_type_check
CHECK(decision_type IN ('analyst','system_no_match','reused_candidate_decisions','ai_auto_clear'));
CREATE INDEX IF NOT EXISTS ai_triage_screening_run_idx ON ai_triage_recommendations
(entity_key, (result_snapshot_json->>'run_id'), status);
