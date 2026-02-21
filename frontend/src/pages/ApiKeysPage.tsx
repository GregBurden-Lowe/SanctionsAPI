import { useEffect, useState } from 'react'
import { Button, Card, CardBody, CardHeader, CardTitle, ErrorBox, Input, Modal, SectionHeader } from '@/components'
import { createApiKey, deleteApiKey, listApiKeys, setApiKeyActive, type ApiKeyCreated, type ApiKeyItem } from '@/api/client'

function formatDate(value: string | null): string {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

export function ApiKeysPage() {
  const [items, setItems] = useState<ApiKeyItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [newKey, setNewKey] = useState<ApiKeyCreated | null>(null)
  const [updatingId, setUpdatingId] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await listApiKeys()
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError((data as { detail?: string }).detail ?? 'Failed to load API keys.')
        setItems([])
        return
      }
      setItems(((data as { items?: ApiKeyItem[] }).items ?? []) as ApiKeyItem[])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load API keys.')
      setItems([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    const trimmed = name.trim()
    if (!trimmed) {
      setError('Key name is required.')
      return
    }
    setCreating(true)
    try {
      const res = await createApiKey(trimmed)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError((data as { detail?: string }).detail ?? 'Failed to create API key.')
        return
      }
      setNewKey(data as ApiKeyCreated)
      setName('')
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create API key.')
    } finally {
      setCreating(false)
    }
  }

  const handleToggle = async (item: ApiKeyItem) => {
    setUpdatingId(item.id)
    setError(null)
    try {
      const res = await setApiKeyActive(item.id, !item.active)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError((data as { detail?: string }).detail ?? 'Failed to update API key.')
        return
      }
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update API key.')
    } finally {
      setUpdatingId(null)
    }
  }

  const handleDelete = async (item: ApiKeyItem) => {
    const ok = window.confirm(`Delete API key "${item.name}"? This cannot be undone.`)
    if (!ok) return
    setUpdatingId(item.id)
    setError(null)
    try {
      const res = await deleteApiKey(item.id)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError((data as { detail?: string }).detail ?? 'Failed to delete API key.')
        return
      }
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete API key.')
    } finally {
      setUpdatingId(null)
    }
  }

  return (
    <div className="px-10 pb-10">
      <div className="max-w-5xl space-y-6">
        <SectionHeader title="API keys" />
        <Card>
          <CardHeader>
            <CardTitle>Create API key</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            <form onSubmit={handleCreate} className="space-y-4">
              <Input
                label="Key name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Dataverse integration"
                disabled={creating}
              />
              <Button type="submit" disabled={creating}>
                {creating ? 'Creating…' : 'Create API key'}
              </Button>
            </form>
            {error && <ErrorBox message={error} />}
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Existing keys</CardTitle>
          </CardHeader>
          <CardBody>
            {loading ? (
              <p className="text-sm text-text-secondary">Loading…</p>
            ) : items.length === 0 ? (
              <p className="text-sm text-text-secondary">No API keys yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead>
                    <tr className="border-b border-border/80">
                      <th className="py-2 pr-4 font-medium text-text-primary">Name</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Active</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Created</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Last used</th>
                      <th className="py-2 font-medium text-text-primary">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr key={item.id} className="border-b border-border/70 hover:bg-muted/40">
                        <td className="py-2 pr-4 text-text-secondary">{item.name}</td>
                        <td className="py-2 pr-4 text-text-secondary">{item.active ? 'Yes' : 'No'}</td>
                        <td className="py-2 pr-4 text-text-muted">{formatDate(item.created_at)}</td>
                        <td className="py-2 pr-4 text-text-muted">{formatDate(item.last_used_at)}</td>
                        <td className="py-2 flex items-center gap-2">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={updatingId === item.id}
                            onClick={() => void handleToggle(item)}
                          >
                            {item.active ? 'Deactivate' : 'Reactivate'}
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={updatingId === item.id}
                            onClick={() => void handleDelete(item)}
                          >
                            Delete
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardBody>
        </Card>
      </div>

      <Modal
        isOpen={newKey !== null}
        onClose={() => setNewKey(null)}
        title="API key created"
        footer={
          <Button type="button" variant="secondary" onClick={() => setNewKey(null)}>
            Close
          </Button>
        }
      >
        {newKey && (
          <div className="space-y-3">
            <p className="text-sm text-text-secondary">
              Copy this key now. For security, it will not be shown again.
            </p>
            <div className="rounded-lg border border-border bg-app p-3">
              <code className="text-xs break-all font-mono text-text-primary">{newKey.api_key}</code>
            </div>
            <div>
              <Button
                type="button"
                variant="secondary"
                onClick={() => navigator.clipboard.writeText(newKey.api_key)}
              >
                Copy key
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}
