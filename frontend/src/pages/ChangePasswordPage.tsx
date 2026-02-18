import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Input, Card, CardHeader, CardTitle, CardBody, ErrorBox } from '@/components'
import { useAuth } from '@/context/AuthContext'

const pageClass = 'min-h-screen bg-app text-text-primary flex items-center justify-center p-6'

function validatePassword(pw: string): string | null {
  if (!pw) return null
  if (pw.length < 8) return 'At least 8 characters'
  if (!/[A-Z]/.test(pw)) return 'One uppercase letter'
  if (!/[a-z]/.test(pw)) return 'One lowercase letter'
  if (!/[0-9]/.test(pw)) return 'One number'
  if (!/[!@#$%^&*()_+\-=[\]{}|;:,.<>?/`~"\\]/.test(pw)) return 'One special character'
  const weak = new Set(['password', 'password1', 'password12', 'password123', 'admin', 'admin123', 'letmein', 'welcome', 'qwerty', 'abc123'])
  if (weak.has(pw.toLowerCase())) return 'Choose a stronger password'
  return null
}

export function ChangePasswordPage() {
  const { changePassword, user } = useAuth()
  const navigate = useNavigate()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (newPassword !== confirmPassword) {
      setError('New password and confirmation do not match.')
      return
    }
    const pwdErr = validatePassword(newPassword)
    if (pwdErr) {
      setError(`New password: ${pwdErr}.`)
      return
    }
    setLoading(true)
    try {
      await changePassword(currentPassword, newPassword)
      navigate('/', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to change password.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className={pageClass}>
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Change password</CardTitle>
        </CardHeader>
        <CardBody>
          <p className="text-sm text-text-secondary mb-4">
            {user?.must_change_password
              ? 'You must set a new password before continuing.'
              : 'Enter your current password and choose a new password.'}
          </p>
          <p className="text-xs text-text-muted mb-4">
            New password: at least 8 characters, with uppercase, lowercase, a number and a special character (e.g. !@#$%).
          </p>
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              label="Current password"
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              disabled={loading}
            />
            <Input
              label="New password"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              disabled={loading}
              error={validatePassword(newPassword) ?? undefined}
            />
            <Input
              label="Confirm new password"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              disabled={loading}
            />
            {error && <ErrorBox message={error} />}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? 'Updating…' : 'Update password'}
            </Button>
          </form>
        </CardBody>
      </Card>
    </div>
  )
}
