import { useState } from 'react'
import { Button, Card, CardHeader, CardTitle, CardBody, SectionHeader, ErrorBox } from '@/components'
import { refreshOpensanctions } from '@/api/client'
import type { RefreshResponse, RefreshErrorResponse } from '@/types/api'

export function AdminPage() {
  const [includePeps, setIncludePeps] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [response, setResponse] = useState<RefreshResponse | RefreshErrorResponse | null>(null)

  const handleRefresh = async () => {
    setError(null)
    setResponse(null)
    setLoading(true)
    try {
      const res = await refreshOpensanctions(includePeps)
      const data = await res.json()
      if (!res.ok) {
        const err = data as RefreshErrorResponse
        setError(err.message ?? 'Refresh failed.')
        return
      }
      setResponse(data as RefreshResponse)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Network error.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="px-10 pb-10">
      <div className="max-w-2xl space-y-6">
        <SectionHeader title="Refresh data" />
        <Card>
          <CardHeader>
            <CardTitle>OpenSanctions data</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="text-sm text-text-secondary mb-4">
              Download latest sanctions and PEP data from OpenSanctions. This may take a few minutes.
            </p>
            <div className="flex items-center gap-2 mb-4">
              <input
                type="checkbox"
                id="include_peps"
                checked={includePeps}
                onChange={(e) => setIncludePeps(e.target.checked)}
                className="h-4 w-4 rounded border-border text-brand focus:ring-2 focus:ring-brand focus:ring-offset-2 focus:ring-offset-app"
              />
              <label htmlFor="include_peps" className="text-sm font-medium text-text-primary">
                Include PEPs
              </label>
            </div>
            <Button
              type="button"
              variant="secondary"
              onClick={handleRefresh}
              disabled={loading}
            >
              {loading ? 'Refreshing…' : 'Refresh OpenSanctions data'}
            </Button>
          </CardBody>
          {error && (
            <div className="mt-4">
              <ErrorBox message={error} />
            </div>
          )}
          {response && (
            <div className="mt-4 rounded-lg border border-border bg-app p-4">
              <p className="text-xs font-medium text-text-muted mb-2">Response</p>
              <pre className="text-xs text-text-secondary overflow-x-auto whitespace-pre-wrap font-mono">
                {JSON.stringify(response, null, 2)}
              </pre>
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
