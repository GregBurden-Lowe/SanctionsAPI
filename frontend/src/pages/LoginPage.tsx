import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Input, Card, CardHeader, CardTitle, CardBody, ErrorBox } from '@/components'
import { useAuth } from '@/context/AuthContext'
import { signup } from '@/api/client'

const pageClass = 'min-h-screen bg-app text-text-primary flex items-center justify-center p-6'

export function LoginPage() {
  const { login, loading } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [signupMode, setSignupMode] = useState(false)
  const [signupEmail, setSignupEmail] = useState('')
  const [signingUp, setSigningUp] = useState(false)
  const [signupError, setSignupError] = useState<string | null>(null)
  const [signupSuccess, setSignupSuccess] = useState(false)

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

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault()
    setSignupError(null)
    setSignupSuccess(false)
    const email = signupEmail.trim().toLowerCase()
    if (!email) {
      setSignupError('Please enter your email address.')
      return
    }
    setSigningUp(true)
    try {
      const res = await signup(email)
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? 'Request failed')
      setSignupSuccess(true)
    } catch (err) {
      setSignupError(err instanceof Error ? err.message : 'Request failed')
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
              {signupSuccess ? (
                <div className="space-y-4">
                  <p className="text-sm text-semantic-success font-medium">
                    Check your email for a temporary password.
                  </p>
                  <p className="text-sm text-text-secondary">
                    Sign in with your email and that password; you will then be asked to set a new password.
                  </p>
                  <button
                    type="button"
                    onClick={() => { setSignupMode(false); setSignupSuccess(false); setSignupEmail('') }}
                    className="w-full text-sm text-brand hover:underline"
                  >
                    Back to sign in
                  </button>
                </div>
              ) : (
                <>
                  <p className="text-sm text-text-secondary mb-4">
                    Request access with your company email (approved domains only). A temporary password will be sent to your inbox.
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
                    {signupError && <ErrorBox message={signupError} />}
                    <Button type="submit" className="w-full" disabled={signingUp}>
                      {signingUp ? 'Sending…' : 'Request access'}
                    </Button>
                    <button
                      type="button"
                      onClick={() => { setSignupMode(false); setSignupError(null) }}
                      className="w-full text-sm text-brand hover:underline"
                    >
                      Back to sign in
                    </button>
                  </form>
                </>
              )}
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
