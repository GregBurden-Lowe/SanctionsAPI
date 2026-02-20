/**
 * API types matching the FastAPI contract exactly. Do not rename or restructure;
 * other systems depend on these response shapes.
 */

export interface OpCheckRequest {
  name: string
  dob?: string | null
  entity_type?: string
  requestor?: string | null
  search_backend?: 'original' | 'postgres_beta'
}

/** Backend error response (400/500) */
export interface ApiErrorResponse {
  error?: string
  message?: string
  status?: string
}

/** Check Summary sub-object — casing must match backend */
export interface CheckSummary {
  Status: string
  Source: string
  Date: string
}

/** Top Matches: backend may return [name, score] tuples or { name, score } */
export type TopMatch = [string, number] | { name: string; score: number }

/** OpCheck response — keys and casing are frozen. entity_key is set when DB is used. */
export interface OpCheckResponse {
  'Sanctions Name': string | null;
  'Birth Date': string | null;
  Regime: string | null;
  Position: unknown;
  Topics: unknown[];
  'Is PEP': boolean;
  'Is Sanctioned': boolean;
  Confidence: string;
  Score: number;
  'Risk Level': string;
  'Top Matches': TopMatch[];
  'Match Found': boolean;
  'Check Summary': CheckSummary;
  entity_key?: string;
}

/** One row from GET /opcheck/screened (stored screening). */
export interface ScreenedEntity {
  entity_key: string;
  display_name: string;
  normalized_name: string;
  date_of_birth: string | null;
  entity_type: string;
  last_screened_at: string;
  screening_valid_until: string;
  status: string;
  risk_level: string;
  confidence: string;
  score: number;
  uk_sanctions_flag: boolean;
  pep_flag: boolean;
  result_json: OpCheckResponse;
  last_requestor: string | null;
  updated_at: string;
}

export interface RefreshRequest {
  include_peps: boolean
  sync_postgres?: boolean
}

export interface RefreshResponse {
  status: 'ok'
  include_peps: boolean
  postgres_synced: boolean
  postgres_rows: {
    sanctions: number
    peps: number
  }
  refresh_run?: {
    refresh_run_id: string
    uk_hash: string
    uk_changed: boolean
    uk_row_count: number
    delta: {
      added: number
      removed: number
      changed: number
    }
    rescreen: {
      enabled: boolean
      candidate_count: number
      queued_count: number
      already_pending_count: number
      failed_count: number
      stale_overrides_marked: number
    }
  } | null
}

export interface RefreshErrorResponse {
  status: 'error'
  message: string
}

export interface ScreeningJob {
  job_id: string
  entity_key: string
  name: string
  date_of_birth: string | null
  entity_type: string
  requestor: string
  reason?: string
  refresh_run_id?: string | null
  force_rescreen?: boolean
  status: 'pending' | 'running' | 'completed' | 'failed'
  previous_status?: string | null
  result_status?: string | null
  transition?: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  screening_status: string | null
  screening_risk_level: string | null
}

export interface RefreshRunRecord {
  refresh_run_id: string
  ran_at: string
  include_peps: boolean
  postgres_synced: boolean
  sanctions_rows: number
  peps_rows: number
  uk_hash: string | null
  prev_uk_hash: string | null
  uk_changed: boolean
  uk_row_count: number
  delta_added: number
  delta_removed: number
  delta_changed: number
  candidate_count: number
  queued_count: number
  already_pending_count: number
  reused_count: number
  failed_count: number
}

export interface RefreshRunSummaryResponse {
  latest: RefreshRunRecord | null
  runs: RefreshRunRecord[]
  latest_transitions: Array<{ transition: string; n: number }>
}
