/**
 * Central API client. Uses relative URLs by default; override with VITE_API_BASE_URL.
 * Attaches GUI JWT when available (localStorage) for authenticated endpoints.
 */

import { getStoredToken } from '@/context/AuthContext'

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

function resolve(path: string): string {
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
}

function defaultHeaders(): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getStoredToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
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
    headers: defaultHeaders(),
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
    headers: defaultHeaders(),
    body: JSON.stringify({ include_peps }),
  })
}

export interface ApiUser {
  id: string
  email: string
  must_change_password: boolean
  is_admin: boolean
  created_at: string
}

export async function listUsers(): Promise<Response> {
  return fetch(resolve('/auth/users'), { method: 'GET', headers: defaultHeaders() })
}

export async function createUser(params: {
  email: string
  password: string
  require_password_change: boolean
}): Promise<Response> {
  return fetch(resolve('/auth/users'), {
    method: 'POST',
    headers: defaultHeaders(),
    body: JSON.stringify(params),
  })
}
