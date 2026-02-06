/**
 * API types matching the FastAPI contract exactly. Do not rename or restructure;
 * other systems depend on these response shapes.
 */

export interface OpCheckRequest {
  name: string
  dob?: string | null
  entity_type?: string
  requestor?: string | null
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

/** OpCheck response — keys and casing are frozen */
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
}

export interface RefreshRequest {
  include_peps: boolean
}

export interface RefreshResponse {
  status: 'ok'
  include_peps: boolean
}

export interface RefreshErrorResponse {
  status: 'error'
  message: string
}
