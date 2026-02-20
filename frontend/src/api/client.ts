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
  search_backend?: 'original' | 'postgres_beta'
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
      search_backend: params.search_backend ?? 'postgres_beta',
    }),
  })
}

export async function refreshOpensanctions(include_peps: boolean, sync_postgres = true): Promise<Response> {
  return fetch(resolve('/refresh_opensanctions'), {
    method: 'POST',
    headers: defaultHeaders(),
    body: JSON.stringify({ include_peps, sync_postgres }),
  })
}

export async function clearScreeningData(): Promise<Response> {
  return fetch(resolve('/admin/testing/clear-screening-data'), {
    method: 'POST',
    headers: defaultHeaders(),
  })
}

export interface BulkScreeningItem {
  name: string
  dob?: string | null
  entity_type?: string
  requestor: string
}

export async function enqueueBulkScreening(requests: BulkScreeningItem[]): Promise<Response> {
  return fetch(resolve('/admin/screening/jobs/bulk'), {
    method: 'POST',
    headers: defaultHeaders(),
    body: JSON.stringify({ requests }),
  })
}

export interface ListScreeningJobsParams {
  status?: 'pending' | 'running' | 'completed' | 'failed'
  limit?: number
  offset?: number
}

export async function listScreeningJobs(params: ListScreeningJobsParams = {}): Promise<Response> {
  const sp = new URLSearchParams()
  if (params.status) sp.set('status', params.status)
  if (params.limit != null) sp.set('limit', String(params.limit))
  if (params.offset != null) sp.set('offset', String(params.offset))
  const qs = sp.toString()
  return fetch(resolve(`/admin/screening/jobs${qs ? `?${qs}` : ''}`), {
    method: 'GET',
    headers: defaultHeaders(),
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

export async function updateUser(
  userId: string,
  params: { is_admin?: boolean; new_password?: string }
): Promise<Response> {
  const body: { is_admin?: boolean; new_password?: string } = {}
  if (params.is_admin !== undefined) body.is_admin = params.is_admin
  if (params.new_password !== undefined && params.new_password) body.new_password = params.new_password
  if (Object.keys(body).length === 0) return new Response(null, { status: 400 })
  return fetch(resolve(`/auth/users/${userId}`), {
    method: 'PATCH',
    headers: defaultHeaders(),
    body: JSON.stringify(body),
  })
}

export interface ImportUserItem {
  email: string
  password?: string | null
}

export interface ImportUsersResult {
  created: number
  skipped: number
  errors: Array<{ email: string; error: string }>
}

export async function importUsers(users: ImportUserItem[]): Promise<Response> {
  return fetch(resolve('/auth/users/import'), {
    method: 'POST',
    headers: defaultHeaders(),
    body: JSON.stringify({ users }),
  })
}

export async function signup(email: string): Promise<Response> {
  return fetch(resolve('/auth/signup'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
}

export interface SearchScreenedParams {
  name?: string
  entity_key?: string
  limit?: number
  offset?: number
}

export async function searchScreened(params: SearchScreenedParams): Promise<Response> {
  const sp = new URLSearchParams()
  if (params.name != null && params.name !== '') sp.set('name', params.name)
  if (params.entity_key != null && params.entity_key !== '') sp.set('entity_key', params.entity_key)
  if (params.limit != null) sp.set('limit', String(params.limit))
  if (params.offset != null) sp.set('offset', String(params.offset))
  const qs = sp.toString()
  return fetch(resolve(`/opcheck/screened${qs ? `?${qs}` : ''}`), { method: 'GET', headers: defaultHeaders() })
}
