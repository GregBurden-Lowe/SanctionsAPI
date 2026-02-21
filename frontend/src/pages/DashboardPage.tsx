import { useEffect, useState } from 'react'
import { Button, Card, CardBody, CardHeader, CardTitle, ErrorBox, SectionHeader } from '@/components'
import { getDashboardSummary } from '@/api/client'
import type { DashboardSummaryResponse } from '@/types/api'

function formatDate(value: string | null): string {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

export function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummaryResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadSummary = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await getDashboardSummary()
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError((data as { detail?: string }).detail ?? 'Failed to load dashboard summary.')
        setSummary(null)
        return
      }
      setSummary(data as DashboardSummaryResponse)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard summary.')
      setSummary(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadSummary()
  }, [])

  const latestRefresh = summary?.data_freshness.latest_refresh

  return (
    <div className="px-10 pb-10">
      <div className="max-w-7xl space-y-6">
        <SectionHeader title="Dashboard" meta="High-level risk and operations" />

        <div className="flex items-center gap-3">
          <Button type="button" variant="secondary" onClick={() => void loadSummary()} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh dashboard'}
          </Button>
        </div>

        {error && <ErrorBox message={error} />}

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Card>
            <CardHeader>
              <CardTitle>Open high-risk reviews</CardTitle>
            </CardHeader>
            <CardBody>
              <p className="text-3xl font-semibold text-text-primary">{summary?.risk.open_high_risk_reviews ?? 0}</p>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Aged reviews (&gt;24h)</CardTitle>
            </CardHeader>
            <CardBody>
              <p className="text-3xl font-semibold text-text-primary">{summary?.risk.aged_reviews_over_24h ?? 0}</p>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Aged reviews (&gt;72h)</CardTitle>
            </CardHeader>
            <CardBody>
              <p className="text-3xl font-semibold text-text-primary">{summary?.risk.aged_reviews_over_72h ?? 0}</p>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Data freshness</CardTitle>
            </CardHeader>
            <CardBody>
              <p className="text-2xl font-semibold text-text-primary">
                {summary?.data_freshness.hours_since_refresh != null
                  ? `${summary.data_freshness.hours_since_refresh}h`
                  : '—'}
              </p>
              <p className="text-xs text-text-muted mt-1">since latest watchlist refresh</p>
            </CardBody>
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>New matches</CardTitle>
            </CardHeader>
            <CardBody className="space-y-2">
              <p className="text-sm text-text-secondary">
                24h: <span className="font-semibold text-text-primary">{summary?.matches.new_matches_24h ?? 0}</span>
              </p>
              <p className="text-sm text-text-secondary">
                7d: <span className="font-semibold text-text-primary">{summary?.matches.new_matches_7d ?? 0}</span>
              </p>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Review throughput (today)</CardTitle>
            </CardHeader>
            <CardBody className="space-y-2">
              <p className="text-sm text-text-secondary">
                Claimed: <span className="font-semibold text-text-primary">{summary?.throughput_today.claimed ?? 0}</span>
              </p>
              <p className="text-sm text-text-secondary">
                Completed: <span className="font-semibold text-text-primary">{summary?.throughput_today.completed ?? 0}</span>
              </p>
              <p className="text-sm text-text-secondary">
                Completion rate:{' '}
                <span className="font-semibold text-text-primary">
                  {summary?.throughput_today.completion_rate_percent ?? 0}%
                </span>
              </p>
            </CardBody>
          </Card>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Outcome mix (last 30 days)</CardTitle>
            </CardHeader>
            <CardBody>
              {!summary || summary.outcome_mix_30d.length === 0 ? (
                <p className="text-sm text-text-secondary">No completed review outcomes in the last 30 days.</p>
              ) : (
                <div className="space-y-2">
                  {summary.outcome_mix_30d.map((item) => (
                    <div key={item.outcome} className="flex items-center justify-between text-sm">
                      <span className="text-text-secondary">{item.outcome}</span>
                      <span className="font-semibold text-text-primary">{item.count}</span>
                    </div>
                  ))}
                </div>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Latest refresh impact</CardTitle>
            </CardHeader>
            <CardBody className="space-y-2">
              <p className="text-sm text-text-secondary">
                Last refresh: <span className="font-semibold text-text-primary">{formatDate(summary?.data_freshness.last_refresh_at ?? null)}</span>
              </p>
              <p className="text-sm text-text-secondary">
                UK list changed: <span className="font-semibold text-text-primary">{latestRefresh ? (latestRefresh.uk_changed ? 'Yes' : 'No') : '—'}</span>
              </p>
              <p className="text-sm text-text-secondary">
                Delta (A/R/C):{' '}
                <span className="font-semibold text-text-primary">
                  {latestRefresh
                    ? `${latestRefresh.delta_added}/${latestRefresh.delta_removed}/${latestRefresh.delta_changed}`
                    : '—'}
                </span>
              </p>
              <p className="text-sm text-text-secondary">
                Re-screen queued: <span className="font-semibold text-text-primary">{latestRefresh?.queued_count ?? 0}</span>
              </p>
              <p className="text-sm text-text-secondary">
                Re-screen failed: <span className="font-semibold text-text-primary">{latestRefresh?.failed_count ?? 0}</span>
              </p>
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  )
}
