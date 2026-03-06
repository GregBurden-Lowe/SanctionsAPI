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

function MetricCard({
  title,
  value,
  subLabel,
}: {
  title: string
  value: string | number
  subLabel?: string
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardBody>
        <p className="font-mono text-[32px] leading-none font-extrabold text-[#0f2340]">{value}</p>
        {subLabel && <p className="mt-2 text-[12px] text-[#94a3b8]">{subLabel}</p>}
      </CardBody>
    </Card>
  )
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
    <div className="px-[26px] pt-[22px] pb-[26px]">
      <div className="max-w-[1600px] space-y-6">
        <SectionHeader title="Dashboard" />

        <div className="flex items-center gap-3">
          <Button type="button" variant="secondary" onClick={() => void loadSummary()} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh dashboard'}
          </Button>
        </div>

        {error && <ErrorBox message={error} />}

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <MetricCard title="Open high-risk reviews" value={summary?.risk.open_high_risk_reviews ?? 0} />
          <MetricCard title="Aged reviews (>24h)" value={summary?.risk.aged_reviews_over_24h ?? 0} />
          <MetricCard title="Aged reviews (>72h)" value={summary?.risk.aged_reviews_over_72h ?? 0} />
          <MetricCard
            title="Data freshness"
            value={
              summary?.data_freshness.hours_since_refresh != null
                ? `${summary.data_freshness.hours_since_refresh}h`
                : '—'
            }
            subLabel="since latest watchlist refresh"
          />
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>New matches</CardTitle>
            </CardHeader>
            <CardBody className="space-y-2 text-[13px]">
              <p className="text-[#64748b]">
                24h: <span className="font-bold text-[#0f2340]">{summary?.matches.new_matches_24h ?? 0}</span>
              </p>
              <p className="text-[#64748b]">
                7d: <span className="font-bold text-[#0f2340]">{summary?.matches.new_matches_7d ?? 0}</span>
              </p>
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Review throughput (today)</CardTitle>
            </CardHeader>
            <CardBody className="space-y-2 text-[13px]">
              <p className="text-[#64748b]">
                Claimed: <span className="font-bold text-[#0f2340]">{summary?.throughput_today.claimed ?? 0}</span>
              </p>
              <p className="text-[#64748b]">
                Completed: <span className="font-bold text-[#0f2340]">{summary?.throughput_today.completed ?? 0}</span>
              </p>
              <p className="text-[#64748b]">
                Completion rate:{' '}
                <span className="font-bold text-[#0f2340]">
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
                <div className="space-y-2 text-[13px]">
                  {summary.outcome_mix_30d.map((item) => (
                    <div key={item.outcome} className="flex items-center justify-between">
                      <span className="text-[#64748b]">{item.outcome}</span>
                      <span className="font-bold text-[#0f2340]">{item.count}</span>
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
            <CardBody className="space-y-2 text-[13px]">
              <p className="text-[#64748b]">
                Last refresh: <span className="font-bold text-[#0f2340]">{formatDate(summary?.data_freshness.last_refresh_at ?? null)}</span>
              </p>
              <p className="text-[#64748b]">
                UK list changed: <span className="font-bold text-[#0f2340]">{latestRefresh ? (latestRefresh.uk_changed ? 'Yes' : 'No') : '—'}</span>
              </p>
              <p className="text-[#64748b]">
                Added/Removed/Changed:{' '}
                <span className="font-bold text-[#0f2340]">
                  {latestRefresh
                    ? `${latestRefresh.delta_added}/${latestRefresh.delta_removed}/${latestRefresh.delta_changed}`
                    : '—'}
                </span>
              </p>
              <p className="text-[#64748b]">
                Re-screen queued: <span className="font-bold text-[#0f2340]">{latestRefresh?.queued_count ?? 0}</span>
              </p>
              <p className="text-[#64748b]">
                Re-screen failed: <span className="font-bold text-[#0f2340]">{latestRefresh?.failed_count ?? 0}</span>
              </p>
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  )
}
