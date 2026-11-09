-- Explicit, reference-scoped test-data classification; this is not a clearance.
CREATE TABLE IF NOT EXISTS screening_test_flags (
    entity_key text PRIMARY KEY,
    business_reference text NOT NULL,
    normalized_name text,
    entity_type text,
    date_of_birth date,
    country_input text,
    active boolean NOT NULL,
    reason text NOT NULL,
    actor text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION screening_is_test(s screened_entities) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT 1 FROM screening_test_flags f WHERE f.entity_key=s.entity_key AND f.active
      AND f.business_reference=s.business_reference
      AND f.normalized_name IS NOT DISTINCT FROM s.normalized_name
      AND f.entity_type IS NOT DISTINCT FROM s.entity_type
      AND f.date_of_birth IS NOT DISTINCT FROM s.date_of_birth
      AND f.country_input IS NOT DISTINCT FROM s.country_input)
$$;
CREATE TABLE IF NOT EXISTS screening_test_flag_events (
    event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_key text NOT NULL,
    business_reference text NOT NULL,
    active boolean NOT NULL,
    reason text NOT NULL,
    actor text NOT NULL,
    run_id uuid,
    identity_scope jsonb NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
DROP TRIGGER IF EXISTS screening_test_flag_event_immutable ON screening_test_flag_events;
CREATE TRIGGER screening_test_flag_event_immutable BEFORE UPDATE OR DELETE ON screening_test_flag_events
FOR EACH ROW EXECUTE FUNCTION protect_screening_run();
