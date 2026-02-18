import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Input, Card, CardHeader, CardTitle, CardBody, ErrorBox } from '@/components'
import { useAuth } from '@/context/AuthContext'
import { signup } from '@/api/client'

const pageClass = 'min-h-screen bg-app text-text-primary flex items-center justify-center p-6'

export function LoginPage() {
  const { login, loading, setAuth } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [signupMode, setSignupMode] = useState(false)
  const [signupEmail, setSignupEmail] = useState('')
  const [signupPassword, setSignupPassword] = useState('')
  const [signupConfirm, setSignupConfirm] = useState('')
  const [signingUp, setSigningUp] = useState(false)
  const [signupError, setSignupError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    const u = username.trim()
    const p = password
    if (!u || !p) {
      setError('Please enter username and password.')
      return
    }
    try {
      const user = await login(u, p)
      navigate(user?.must_change_password ? '/change-password' : '/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed.')
    }
  }

  const signupPasswordError = ((): string | null => {
    if (!signupPassword) return null
    if (signupPassword.length < 8) return 'At least 8 characters'
    if (!/[A-Z]/.test(signupPassword)) return 'One uppercase letter'
    if (!/[a-z]/.test(signupPassword)) return 'One lowercase letter'
    if (!/[0-9]/.test(signupPassword)) return 'One number'
    if (!/[!@#$%^&*()_+\-=[\]{}|;:,.<>?/`~"\\]/.test(signupPassword)) return 'One special character'
    const weak = new Set(['password', 'password1', 'password12', 'password123', 'admin', 'admin123', 'letmein', 'welcome', 'qwerty', 'abc123'])
    if (weak.has(signupPassword.toLowerCase())) return 'Choose a stronger password'
    return null
  })()

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault()
    setSignupError(null)
    const email = signupEmail.trim().toLowerCase()
    if (!email) {
      setSignupError('Please enter your email address.')
      return
    }
    if (signupPasswordError) {
      setSignupError(`Password: ${signupPasswordError}.`)
      return
    }
    if (signupPassword !== signupConfirm) {
      setSignupError('Passwords do not match.')
      return
    }
    setSigningUp(true)
    try {
      const res = await signup(email, signupPassword)
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? 'Signup failed')
      const token = data.access_token
      const user = data.user ?? { username: email, email, must_change_password: true, is_admin: false }
      if (!token || !user.email) throw new Error('Invalid signup response')
      setAuth(token, user)
      navigate(user.must_change_password ? '/change-password' : '/', { replace: true })
    } catch (err) {
      setSignupError(err instanceof Error ? err.message : 'Signup failed')
    } finally {
      setSigningUp(false)
    }
  }

  return (
    <div className={pageClass}>
      <Card className="w-full max-w-sm">
        {signupMode ? (
          <>
            <CardHeader>
              <CardTitle>Sign up</CardTitle>
            </CardHeader>
            <CardBody>
              <p className="text-sm text-text-secondary mb-4">
                Create an account with your company email (approved domains only).
              </p>
              <p className="text-xs text-text-muted mb-4">
                Password: at least 8 characters, with uppercase, lowercase, a number and a special character (e.g. !@#$%).
              </p>
              <form onSubmit={handleSignup} className="space-y-4">
                <Input
                  label="Email"
                  type="email"
                  autoComplete="email"
                  value={signupEmail}
                  onChange={(e) => setSignupEmail(e.target.value)}
                  disabled={signingUp}
                />
                <Input
                  label="Password"
                  type="password"
                  autoComplete="new-password"
                  value={signupPassword}
                  onChange={(e) => setSignupPassword(e.target.value)}
                  disabled={signingUp}
                  error={signupPasswordError ?? undefined}
                />
                <Input
                  label="Confirm password"
                  type="password"
                  autoComplete="new-password"
                  value={signupConfirm}
                  onChange={(e) => setSignupConfirm(e.target.value)}
                  disabled={signingUp}
                />
                {signupError && <ErrorBox message={signupError} />}
                <Button type="submit" className="w-full" disabled={signingUp}>
                  {signingUp ? 'Creating account…' : 'Sign up'}
                </Button>
                <button
                  type="button"
                  onClick={() => { setSignupMode(false); setSignupError(null) }}
                  className="w-full text-sm text-brand hover:underline"
                >
                  Back to sign in
                </button>
              </form>
            </CardBody>
          </>
        ) : (
          <>
            <CardHeader>
              <CardTitle>Sign in</CardTitle>
            </CardHeader>
            <CardBody>
              <p className="text-sm text-text-secondary mb-4">
                Sign in to use Sanctions &amp; PEP Screening.
              </p>
              <form onSubmit={handleSubmit} className="space-y-4">
                <Input
                  label="Email"
                  type="email"
                  autoComplete="email"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  disabled={loading}
                />
                <Input
                  label="Password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={loading}
                />
                {error && <ErrorBox message={error} />}
                <Button type="submit" className="w-full" disabled={loading}>
                  {loading ? 'Signing in…' : 'Sign in'}
                </Button>
                <button
                  type="button"
                  onClick={() => setSignupMode(true)}
                  className="w-full text-sm text-brand hover:underline"
                >
                  Sign up
                </button>
              </form>
            </CardBody>
          </>
        )}
      </Card>
    </div>
  )
}
