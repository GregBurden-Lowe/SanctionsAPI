import { useState, useEffect } from 'react'
import {
  Button,
  Input,
  Card,
  CardHeader,
  CardTitle,
  CardBody,
  SectionHeader,
  ErrorBox,
} from '@/components'
import { listUsers, createUser, type ApiUser } from '@/api/client'

export function UsersPage() {
  const [users, setUsers] = useState<ApiUser[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [createEmail, setCreateEmail] = useState('')
  const [createPassword, setCreatePassword] = useState('')
  const [requirePasswordChange, setRequirePasswordChange] = useState(true)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [createSuccess, setCreateSuccess] = useState(false)

  const loadUsers = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await listUsers()
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? 'Failed to load users')
      setUsers(data.users ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load users')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadUsers()
  }, [])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setCreateError(null)
    setCreateSuccess(false)
    const email = createEmail.trim().toLowerCase()
    if (!email) {
      setCreateError('Email is required.')
      return
    }
    if (!createPassword) {
      setCreateError('Password is required.')
      return
    }
    setCreating(true)
    try {
      const res = await createUser({
        email,
        password: createPassword,
        require_password_change: requirePasswordChange,
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? 'Failed to create user')
      setCreateEmail('')
      setCreatePassword('')
      setCreateSuccess(true)
      loadUsers()
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create user')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="px-10 pb-10">
      <div className="max-w-2xl space-y-6">
        <SectionHeader title="User management" />
        <Card>
          <CardHeader>
            <CardTitle>Create user</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="text-sm text-text-secondary mb-4">
              Add a new user. They will sign in with their email and the password you set.
            </p>
            <form onSubmit={handleCreate} className="space-y-4">
              <Input
                label="Email"
                type="email"
                autoComplete="off"
                value={createEmail}
                onChange={(e) => setCreateEmail(e.target.value)}
                disabled={creating}
              />
              <Input
                label="Initial password"
                type="password"
                autoComplete="new-password"
                value={createPassword}
                onChange={(e) => setCreatePassword(e.target.value)}
                disabled={creating}
              />
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="require_password_change"
                  checked={requirePasswordChange}
                  onChange={(e) => setRequirePasswordChange(e.target.checked)}
                  className="h-4 w-4 rounded border-border text-brand focus:ring-2 focus:ring-brand focus:ring-offset-2 focus:ring-offset-app"
                />
                <label htmlFor="require_password_change" className="text-sm font-medium text-text-primary">
                  Require password change at first logon
                </label>
              </div>
              {createError && <ErrorBox message={createError} />}
              {createSuccess && (
                <p className="text-sm text-semantic-success" role="status">User created.</p>
              )}
              <Button type="submit" disabled={creating}>
                {creating ? 'Creating…' : 'Create user'}
              </Button>
            </form>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Users</CardTitle>
          </CardHeader>
          <CardBody>
            {error && <ErrorBox message={error} />}
            {loading ? (
              <p className="text-sm text-text-secondary">Loading…</p>
            ) : users.length === 0 ? (
              <p className="text-sm text-text-secondary">No users yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="py-2 pr-4 font-medium text-text-primary">Email</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Admin</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Must change password</th>
                      <th className="py-2 font-medium text-text-primary">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((u) => (
                      <tr key={u.id} className="border-b border-border">
                        <td className="py-2 pr-4 text-text-secondary">{u.email}</td>
                        <td className="py-2 pr-4 text-text-secondary">{u.is_admin ? 'Yes' : 'No'}</td>
                        <td className="py-2 pr-4 text-text-secondary">{u.must_change_password ? 'Yes' : 'No'}</td>
                        <td className="py-2 text-text-muted">{new Date(u.created_at).toLocaleDateString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
