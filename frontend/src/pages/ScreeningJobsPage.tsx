import { useEffect, useState } from 'react'
import { Button, Card, CardBody, CardHeader, CardTitle, ErrorBox, SectionHeader } from '@/components'
import { listScreeningJobs } from '@/api/client'
import type { ScreeningJob } from '@/types/api'

function formatDate(value: string | null): string {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

function statusTone(status: ScreeningJob['status']): string {
  if (status === 'completed') return 'text-semantic-success'
  if (status === 'failed') return 'text-semantic-error'
  if (status === 'running') return 'text-semantic-warning'
  return 'text-text-secondary'
}

export function ScreeningJobsPage() {
  const [items, setItems] = useState<ScreeningJob[]>([])
  const [status, setStatus] = useState<'all' | ScreeningJob['status']>('all')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await listScreeningJobs({
        status: status === 'all' ? undefined : status,
        limit: 200,
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError((data as { detail?: string }).detail ?? 'Failed to load jobs.')
        setItems([])
        return
      }
      setItems(((data as { items?: ScreeningJob[] }).items ?? []) as ScreeningJob[])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load jobs.')
      setItems([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [status])

  return (
    <div className="px-10 pb-10">
      <div className="max-w-6xl space-y-6">
        <SectionHeader title="Screening jobs" meta="Queue and worker progress" />
        <Card>
          <CardHeader>
            <CardTitle>Job monitor</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            <div className="flex items-center gap-3">
              <label htmlFor="job_status" className="text-sm text-text-secondary">Status</label>
              <select
                id="job_status"
                value={status}
                onChange={(e) => setStatus(e.target.value as typeof status)}
                className="h-10 rounded-lg border border-border bg-surface px-3 text-sm text-text-primary"
              >
                <option value="all">All</option>
                <option value="pending">Pending</option>
                <option value="running">Running</option>
                <option value="completed">Completed</option>
                <option value="failed">Failed</option>
              </select>
              <Button type="button" onClick={() => void load()} disabled={loading}>
                {loading ? 'Refreshing…' : 'Refresh'}
              </Button>
            </div>
            {error && <ErrorBox message={error} />}
            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead>
                  <tr className="border-b border-border/80">
                    <th className="py-2 pr-4 font-medium text-text-primary">Created</th>
                    <th className="py-2 pr-4 font-medium text-text-primary">Status</th>
                    <th className="py-2 pr-4 font-medium text-text-primary">Name</th>
                    <th className="py-2 pr-4 font-medium text-text-primary">Type</th>
                    <th className="py-2 pr-4 font-medium text-text-primary">Requestor</th>
                    <th className="py-2 pr-4 font-medium text-text-primary">Started</th>
                    <th className="py-2 pr-4 font-medium text-text-primary">Finished</th>
                    <th className="py-2 pr-4 font-medium text-text-primary">Entity key</th>
                    <th className="py-2 font-medium text-text-primary">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((row) => (
                    <tr key={row.job_id} className="border-b border-border/70 hover:bg-muted/40">
                      <td className="py-2 pr-4 text-text-muted">{formatDate(row.created_at)}</td>
                      <td className={`py-2 pr-4 font-medium ${statusTone(row.status)}`}>{row.status}</td>
                      <td className="py-2 pr-4 text-text-secondary">{row.name}</td>
                      <td className="py-2 pr-4 text-text-secondary">{row.entity_type}</td>
                      <td className="py-2 pr-4 text-text-muted">{row.requestor || '—'}</td>
                      <td className="py-2 pr-4 text-text-muted">{formatDate(row.started_at)}</td>
                      <td className="py-2 pr-4 text-text-muted">{formatDate(row.finished_at)}</td>
                      <td className="py-2 pr-4">
                        <code className="text-xs bg-surface px-1 rounded break-all font-mono">{row.entity_key}</code>
                      </td>
                      <td className="py-2 text-text-muted">{row.error_message || '—'}</td>
                    </tr>
                  ))}
                  {!loading && items.length === 0 && (
                    <tr>
                      <td className="py-3 text-text-secondary" colSpan={9}>No jobs found for this filter.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </CardBody>
        </Card>
      </div>
    </div>
  )
}

