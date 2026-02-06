/**
 * Central API client. Uses relative URLs by default; override with VITE_API_BASE_URL.
 * Does not expose secrets; all endpoints are public.
 */

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

function resolve(path: string): string {
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
}

export async function health(): Promise<string> {
  const res = await fetch(resolve('/health'))
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`)
  return res.text()
}

export interface OpCheckParams {
  name: string
  dob?: string | null
  entity_type?: string
  requestor?: string | null
}

export async function opcheck(params: OpCheckParams): Promise<Response> {
  return fetch(resolve('/opcheck'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: params.name,
      dob: params.dob ?? null,
      entity_type: params.entity_type ?? 'Person',
      requestor: params.requestor ?? null,
    }),
  })
}

export async function refreshOpensanctions(include_peps: boolean): Promise<Response> {
  return fetch(resolve('/refresh_opensanctions'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ include_peps }),
  })
}
