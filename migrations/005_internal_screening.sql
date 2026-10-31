-- Source state is published in the same transaction as the PostgreSQL rows.
CREATE TABLE IF NOT EXISTS watchlist_source_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), manifest jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS screening_candidate_resolutions (
    resolution_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id uuid NOT NULL REFERENCES screening_review_decisions(decision_id),
    subject_signature text NOT NULL, candidate_signature text NOT NULL,
    business_reference text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS candidate_resolution_lookup ON screening_candidate_resolutions
(subject_signature, business_reference, candidate_signature);
DROP TRIGGER IF EXISTS candidate_resolution_immutable ON screening_candidate_resolutions;
CREATE TRIGGER candidate_resolution_immutable BEFORE UPDATE OR DELETE ON screening_candidate_resolutions
FOR EACH ROW EXECUTE FUNCTION protect_screening_run();
ALTER TABLE screening_review_decisions DROP CONSTRAINT IF EXISTS screening_review_decisions_decision_type_check;
ALTER TABLE screening_review_decisions ADD CONSTRAINT screening_review_decisions_decision_type_check
CHECK(decision_type IN ('analyst', 'system_no_match', 'reused_candidate_decisions', 'ai_auto_clear'));
CREATE TABLE IF NOT EXISTS screening_monitoring_cycles (
    cycle_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), version text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
    queued integer NOT NULL DEFAULT 0, status text NOT NULL DEFAULT 'running',
    error text
);
CREATE TABLE IF NOT EXISTS screening_monitoring_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    last_completed_at timestamptz, last_version text
);
INSERT INTO screening_monitoring_state(singleton) VALUES(true) ON CONFLICT DO NOTHING;
-- One implementation shared by synchronous API publication and the worker.
CREATE OR REPLACE FUNCTION reuse_candidate_decisions(target_key text) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE e screened_entities%ROWTYPE; prior screening_review_decisions%ROWTYPE;
        c jsonb; saved screening_review_decisions%ROWTYPE; decision jsonb;
BEGIN
    SELECT * INTO e FROM screened_entities WHERE entity_key=target_key FOR UPDATE;
    IF e.review_decision IS NOT NULL OR NOT COALESCE((e.result_json->>'Review Reuse Eligible')::boolean,false)
       OR e.result_json->'Source Coverage'->>'status' IS DISTINCT FROM 'current' THEN RETURN NULL; END IF;
    -- Only the latest human decision for this exact identity/reference is eligible.
    SELECT d.* INTO prior FROM screening_review_decisions d JOIN screening_runs r USING(run_id)
      WHERE d.decision_type='analyst'
      AND r.result_json->>'Review Subject Signature'=e.result_json->>'Review Subject Signature'
      AND r.result_json->>'Review Business Reference'=e.business_reference
      ORDER BY d.decided_at DESC,d.decision_id DESC LIMIT 1;
    IF prior.decision_id IS NULL OR prior.outcome NOT LIKE 'False Positive%'
       OR prior.decided_at < now()-interval '180 days' THEN RETURN NULL; END IF;
    FOR c IN SELECT value FROM jsonb_array_elements(e.result_json->'Candidate Evidence')
             WHERE value->>'disposition'='potential_match' LOOP
        IF NOT EXISTS(SELECT 1 FROM screening_candidate_resolutions x
          WHERE x.decision_id=prior.decision_id AND x.business_reference=e.business_reference
          AND x.subject_signature=e.result_json->>'Review Subject Signature'
          AND x.candidate_signature=c->>'candidate_signature') THEN RETURN NULL; END IF;
    END LOOP;
    INSERT INTO screening_review_decisions(run_id,entity_key,actor,outcome,notes,decision_type)
      VALUES(e.current_run_id,target_key,'system:review-reuse',prior.outcome,
        'Unchanged candidate evidence and identity. Reused analyst decision '||prior.decision_id::text||
        ' by '||prior.actor||'. Original reason: '||prior.notes,'reused_candidate_decisions') RETURNING * INTO saved;
    decision=to_jsonb(saved)||jsonb_build_object('original_decision_id',prior.decision_id);
    UPDATE screened_entities SET review_decision=decision,review_status='COMPLETED',
      review_outcome=saved.outcome,review_notes=saved.notes,review_completed_by=saved.actor,
      review_completed_at=saved.decided_at,status='Cleared - False Positive',risk_level='Cleared',
      uk_sanctions_flag=false,pep_flag=false,manual_override_stale=false WHERE entity_key=target_key;
    RETURN decision;
END $$;
CREATE TABLE IF NOT EXISTS screening_monitoring_items (
    cycle_id uuid NOT NULL REFERENCES screening_monitoring_cycles(cycle_id),
    entity_key text NOT NULL, inputs jsonb NOT NULL, job_id uuid,
    PRIMARY KEY(cycle_id,entity_key)
);
CREATE TABLE IF NOT EXISTS screening_source_health (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    last_attempt_at timestamptz, last_success_at timestamptz, last_error text
);
CREATE TABLE IF NOT EXISTS company_screening_investigations (
    investigation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(), actor text NOT NULL,
    company_number text NOT NULL, business_reference text NOT NULL,
    relationship_snapshot jsonb NOT NULL, checks jsonb NOT NULL DEFAULT '[]',
    status text NOT NULL DEFAULT 'running'
);
ALTER TABLE screening_monitoring_state ADD COLUMN IF NOT EXISTS last_tick_at timestamptz;
ALTER TABLE screening_monitoring_state ADD COLUMN IF NOT EXISTS last_error text;
