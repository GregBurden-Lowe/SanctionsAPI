import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'

const STORAGE_KEY_TOKEN = 'sanctions_token'
const STORAGE_KEY_USER = 'sanctions_user'

export interface AuthUser {
  username: string
  email?: string
  must_change_password?: boolean
  is_admin?: boolean
}

interface AuthState {
  user: AuthUser | null
  token: string | null
  loading: boolean
  checked: boolean
  /** When false, backend has no DB; app is accessible without login. */
  loginRequired: boolean
}

interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<AuthUser>
  logout: () => void
  setAuth: (token: string, user: AuthUser) => void
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    user: null,
    token: null,
    loading: false,
    checked: false,
    loginRequired: true,
  })

  const setAuth = useCallback((token: string, user: AuthUser) => {
    localStorage.setItem(STORAGE_KEY_TOKEN, token)
    localStorage.setItem(STORAGE_KEY_USER, JSON.stringify(user))
    setState((s) => ({ ...s, user, token, loading: false, checked: true }))
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY_TOKEN)
    localStorage.removeItem(STORAGE_KEY_USER)
    setState((s) => ({ ...s, user: null, token: null }))
  }, [])

  useEffect(() => {
    const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
    let cancelled = false
    ;(async () => {
      try {
        const res = await fetch(`${API_BASE}/auth/config`)
        const data = await res.json().catch(() => ({}))
        const loginRequired = Boolean(data.login_required)
        if (cancelled) return
        if (!loginRequired) {
          setState((s) => ({ ...s, checked: true, loginRequired: false }))
          return
        }
        const token = localStorage.getItem(STORAGE_KEY_TOKEN)
        const userRaw = localStorage.getItem(STORAGE_KEY_USER)
        if (token && userRaw) {
          try {
            const user = JSON.parse(userRaw) as AuthUser
            if (user?.username) {
              setState((s) => ({ ...s, token, user, checked: true, loginRequired }))
              return
            }
          } catch {
            // ignore
          }
        }
        setState((s) => ({ ...s, user: null, token: null, checked: true, loginRequired }))
      } catch {
        if (!cancelled) setState((s) => ({ ...s, checked: true, loginRequired: true }))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(
    async (email: string, password: string) => {
      setState((s) => ({ ...s, loading: true }))
      const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: email.trim(), password }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setState((s) => ({ ...s, loading: false }))
        throw new Error(data.detail ?? 'Login failed')
      }
      const token = data.access_token
      const user: AuthUser = data.user ?? { username: email.trim() }
      if (!token || !user.username) throw new Error('Invalid login response')
      setAuth(token, user)
      return user
    },
    [setAuth]
  )

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
      const token = getStoredToken()
      const res = await fetch(`${API_BASE}/auth/change-password`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.detail ?? 'Failed to change password')
      const newToken = data.access_token
      const user: AuthUser = data.user ?? state.user ?? { username: '' }
      if (!newToken || !user.username) throw new Error('Invalid response')
      setAuth(newToken, { ...user, must_change_password: false })
    },
    [state.user, setAuth]
  )

  const value: AuthContextValue = {
    ...state,
    login,
    logout,
    setAuth,
    changePassword,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

/** Used by API client to attach token to requests. */
export function getStoredToken(): string | null {
  return typeof localStorage !== 'undefined' ? localStorage.getItem(STORAGE_KEY_TOKEN) : null
}
