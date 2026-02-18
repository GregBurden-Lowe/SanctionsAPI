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
  Modal,
} from '@/components'
import { listUsers, createUser, importUsers, updateUser, type ApiUser, type ImportUserItem } from '@/api/client'

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
  const [importCsv, setImportCsv] = useState('')
  const [importing, setImporting] = useState(false)
  const [importResult, setImportResult] = useState<{ created: number; skipped: number; errors: Array<{ email: string; error: string }> } | null>(null)
  const [importError, setImportError] = useState<string | null>(null)
  const [resetUser, setResetUser] = useState<ApiUser | null>(null)
  const [resetPassword, setResetPassword] = useState('')
  const [resetting, setResetting] = useState(false)
  const [resetError, setResetError] = useState<string | null>(null)
  const [updatingRoleId, setUpdatingRoleId] = useState<string | null>(null)

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

  function parseCsvToUsers(csv: string): ImportUserItem[] {
    const lines = csv.trim().split(/\r?\n/).filter((line) => line.trim())
    if (lines.length === 0) return []
    const first = lines[0].toLowerCase()
    const hasHeader = first.includes('email') && !first.includes('@')
    const start = hasHeader ? 1 : 0
    const users: ImportUserItem[] = []
    for (let i = start; i < lines.length; i++) {
      const line = lines[i]
      const parts = line.split(',').map((p) => p.trim().replace(/^["']|["']$/g, ''))
      const email = parts[0]?.trim()
      if (!email) continue
      const password = parts[1]?.trim() || undefined
      users.push({ email, password: password || undefined })
    }
    return users
  }

  const handleImport = async () => {
    setImportError(null)
    setImportResult(null)
    const users = parseCsvToUsers(importCsv)
    if (users.length === 0) {
      setImportError('No valid rows. Use header "email" or "email,password" and one row per user.')
      return
    }
    if (users.length > 500) {
      setImportError('Maximum 500 users per import.')
      return
    }
    setImporting(true)
    try {
      const res = await importUsers(users)
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail ?? 'Import failed')
      setImportResult(data)
      setImportCsv('')
      loadUsers()
    } catch (err) {
      setImportError(err instanceof Error ? err.message : 'Import failed')
    } finally {
      setImporting(false)
    }
  }

  const handleCsvFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => setImportCsv(String(reader.result ?? ''))
    reader.readAsText(file)
    e.target.value = ''
  }

  const handleRoleChange = async (u: ApiUser, isAdmin: boolean) => {
    setUpdatingRoleId(u.id)
    setError(null)
    try {
      const res = await updateUser(u.id, { is_admin: isAdmin })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.detail ?? 'Update failed')
      }
      loadUsers()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Update failed')
    } finally {
      setUpdatingRoleId(null)
    }
  }

  const handleResetPasswordSubmit = async () => {
    if (!resetUser) return
    if (!resetPassword || resetPassword.length < 6) {
      setResetError('Password must be at least 6 characters.')
      return
    }
    setResetError(null)
    setResetting(true)
    try {
      const res = await updateUser(resetUser.id, { new_password: resetPassword })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.detail ?? 'Reset failed')
      }
      setResetUser(null)
      setResetPassword('')
      loadUsers()
    } catch (err) {
      setResetError(err instanceof Error ? err.message : 'Reset failed')
    } finally {
      setResetting(false)
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
            <CardTitle>Import users from CSV</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="text-sm text-text-secondary mb-4">
              Upload or paste a CSV with one user per row. Use a header row: <code className="text-xs bg-app px-1 rounded">email</code> or <code className="text-xs bg-app px-1 rounded">email,password</code>. If password is omitted, a random one is set and the user must change it at first logon. All imported users require password change at first logon. Max 500 per import.
            </p>
            <div className="space-y-4">
              <div>
                <label htmlFor="csv-import-file" className="block text-xs font-medium text-text-primary mb-1">CSV file or paste below</label>
                <input
                  id="csv-import-file"
                  type="file"
                  accept=".csv,text/csv,text/plain"
                  onChange={handleCsvFileChange}
                  aria-label="Choose CSV file to import users"
                  className="block w-full text-sm text-text-secondary file:mr-4 file:py-2 file:px-4 file:rounded file:border-0 file:text-sm file:font-medium file:bg-brand file:text-white hover:file:opacity-90"
                />
              </div>
              <textarea
                className="w-full h-32 rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-muted outline-none focus:border-brand font-mono"
                placeholder={'email\nuser1@example.com\nuser2@example.com,InitialPass123'}
                value={importCsv}
                onChange={(e) => setImportCsv(e.target.value)}
                disabled={importing}
              />
              {importError && <ErrorBox message={importError} />}
              {importResult && (
                <div className="rounded-lg border border-border bg-app p-4 text-sm text-text-secondary">
                  <p>Created: {importResult.created} · Skipped (already exist): {importResult.skipped}</p>
                  {importResult.errors.length > 0 && (
                    <p className="mt-2 text-semantic-error">
                      Errors: {importResult.errors.map((e) => `${e.email}: ${e.error}`).join('; ')}
                    </p>
                  )}
                </div>
              )}
              <Button type="button" onClick={handleImport} disabled={importing || !importCsv.trim()}>
                {importing ? 'Importing…' : 'Import users'}
              </Button>
            </div>
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
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm text-left">
                    <thead>
                      <tr className="border-b border-border">
                        <th className="py-2 pr-4 font-medium text-text-primary">Email</th>
                        <th className="py-2 pr-4 font-medium text-text-primary">Type</th>
                        <th className="py-2 pr-4 font-medium text-text-primary">Must change password</th>
                        <th className="py-2 pr-4 font-medium text-text-primary">Created</th>
                        <th className="py-2 font-medium text-text-primary">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {users.map((u) => (
                        <tr key={u.id} className="border-b border-border">
                          <td className="py-2 pr-4 text-text-secondary">{u.email}</td>
                          <td className="py-2 pr-4">
                            <select
                              value={u.is_admin ? 'admin' : 'user'}
                              onChange={(e) => handleRoleChange(u, e.target.value === 'admin')}
                              disabled={updatingRoleId === u.id}
                              className="h-8 rounded border border-border bg-surface px-2 text-sm text-text-primary outline-none focus:border-brand"
                            >
                              <option value="user">User</option>
                              <option value="admin">Admin</option>
                            </select>
                          </td>
                          <td className="py-2 pr-4 text-text-secondary">{u.must_change_password ? 'Yes' : 'No'}</td>
                          <td className="py-2 pr-4 text-text-muted">{new Date(u.created_at).toLocaleDateString()}</td>
                          <td className="py-2">
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              onClick={() => { setResetUser(u); setResetPassword(''); setResetError(null) }}
                            >
                              Reset password
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Modal
                isOpen={resetUser !== null}
                onClose={() => { setResetUser(null); setResetPassword(''); setResetError(null) }}
                title="Reset password"
                footer={
                  <>
                    <Button variant="secondary" onClick={() => { setResetUser(null); setResetPassword(''); setResetError(null) }}>
                      Cancel
                    </Button>
                    <Button onClick={handleResetPasswordSubmit} disabled={resetting}>
                      {resetting ? 'Resetting…' : 'Reset password'}
                    </Button>
                  </>
                }
              >
                {resetUser && (
                  <div className="space-y-4">
                    <p className="text-text-primary">
                      Set a new temporary password for <strong>{resetUser.email}</strong>. They will be required to change it on next sign-in.
                    </p>
                    <Input
                      label="New temporary password"
                      type="password"
                      autoComplete="new-password"
                      value={resetPassword}
                      onChange={(e) => setResetPassword(e.target.value)}
                      disabled={resetting}
                    />
                    {resetError && <ErrorBox message={resetError} />}
                  </div>
                )}
              </Modal>
              </>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
