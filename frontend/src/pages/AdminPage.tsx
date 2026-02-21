import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, CardHeader, CardTitle, CardBody, SectionHeader, ErrorBox } from '@/components'
import { clearScreeningData, getRescreenSummary } from '@/api/client'
import type { RefreshRunSummaryResponse } from '@/types/api'

export function AdminPage() {
  const navigate = useNavigate()
  const [clearing, setClearing] = useState(false)
  const [clearError, setClearError] = useState<string | null>(null)
  const [clearResponse, setClearResponse] = useState<{ status: string; screened_entities_removed: number; screening_jobs_removed: number } | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  const [summary, setSummary] = useState<RefreshRunSummaryResponse | null>(null)

  const loadSummary = async () => {
    setSummaryLoading(true)
    setSummaryError(null)
    try {
      const res = await getRescreenSummary(14)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setSummaryError((data as { detail?: string }).detail ?? 'Failed to load rescreen summary.')
        setSummary(null)
        return
      }
      setSummary(data as RefreshRunSummaryResponse)
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : 'Failed to load rescreen summary.')
      setSummary(null)
    } finally {
      setSummaryLoading(false)
    }
  }

  useEffect(() => {
    void loadSummary()
  }, [])

  const handleClearScreeningData = async () => {
    setClearError(null)
    setClearResponse(null)
    const ok = window.confirm(
      'This will permanently delete all screened entities and queued/completed jobs. User accounts are NOT deleted. Continue?'
    )
    if (!ok) return
    setClearing(true)
    try {
      const res = await clearScreeningData()
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setClearError((data as { detail?: string }).detail ?? 'Failed to clear screening data.')
        return
      }
      setClearResponse(data as { status: string; screened_entities_removed: number; screening_jobs_removed: number })
    } catch (err) {
      setClearError(err instanceof Error ? err.message : 'Network error.')
    } finally {
      setClearing(false)
    }
  }

  return (
    <div className="px-10 pb-10">
      <div className="max-w-2xl space-y-6">
        <SectionHeader title="Admin tools" />
        <Card>
          <CardHeader>
            <CardTitle>Data refresh mode</CardTitle>
          </CardHeader>
          <CardBody className="space-y-3">
            <p className="text-sm text-text-secondary">
              OpenSanctions refresh is now API/cron driven and syncs Postgres by default. Use your 22:00 droplet cron job to keep watchlist tables current.
            </p>
            <div>
              <div className="flex items-center gap-2">
                <Button type="button" variant="secondary" onClick={() => navigate('/admin/docs')}>
                  Open API docs
                </Button>
                <Button type="button" variant="secondary" onClick={() => navigate('/admin/api-keys')}>
                  Manage API keys
                </Button>
              </div>
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Daily re-screen summary</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            <Button type="button" variant="secondary" onClick={loadSummary} disabled={summaryLoading}>
              {summaryLoading ? 'Refreshing…' : 'Refresh summary'}
            </Button>
            {summaryError && <ErrorBox message={summaryError} />}
            {summary?.latest && (
              <div className="rounded-lg border border-border bg-app p-4">
                <p className="text-xs font-medium text-text-muted mb-2">Latest refresh run</p>
                <pre className="text-xs text-text-secondary overflow-x-auto whitespace-pre-wrap font-mono">
                  {JSON.stringify(
                    {
                      ran_at: summary.latest.ran_at,
                      uk_changed: summary.latest.uk_changed,
                      delta: {
                        added: summary.latest.delta_added,
                        removed: summary.latest.delta_removed,
                        changed: summary.latest.delta_changed,
                      },
                      rescreen: {
                        candidate_count: summary.latest.candidate_count,
                        queued_count: summary.latest.queued_count,
                        already_pending_count: summary.latest.already_pending_count,
                        failed_count: summary.latest.failed_count,
                      },
                      transitions: summary.latest_transitions,
                    },
                    null,
                    2,
                  )}
                </pre>
              </div>
            )}
            {!summaryLoading && !summary?.latest && !summaryError && (
              <p className="text-sm text-text-secondary">No refresh runs recorded yet.</p>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Testing tools</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="text-sm text-text-secondary mb-4">
              Clear screening cache and queue so checks run fresh. This action does not delete users.
            </p>
            <Button
              type="button"
              variant="secondary"
              onClick={handleClearScreeningData}
              disabled={clearing}
            >
              {clearing ? 'Clearing…' : 'Clear screening data (testing)'}
            </Button>
            {clearError && (
              <div className="mt-4">
                <ErrorBox message={clearError} />
              </div>
            )}
            {clearResponse && (
              <div className="mt-4 rounded-lg border border-border bg-app p-4">
                <p className="text-xs font-medium text-text-muted mb-2">Clear result</p>
                <pre className="text-xs text-text-secondary overflow-x-auto whitespace-pre-wrap font-mono">
                  {JSON.stringify(clearResponse, null, 2)}
                </pre>
              </div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
