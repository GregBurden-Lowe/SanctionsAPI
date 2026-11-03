import { useEffect, useState } from 'react'
import { Button, Card, CardHeader, CardTitle, CardBody, ErrorBox } from '@/components'
import { getScreeningAssurance, reconcileScreening } from '@/api/client'
import { useAuth } from '@/context/AuthContext'
import type { SourceCoverage } from '@/types/api'
import { BiCheckCircle, BiXCircle, BiTime } from 'react-icons/bi'

type Assurance = {
  monitor?: { last_tick_at?: string; last_error?: string }
  postgres: SourceCoverage
  parquet: SourceCoverage
  refresh: { last_error?: string; last_success_at?: string }
  cycles: Array<{ cycle_id: string; status: string; queued: number; started_at: string; error?: string }>
}

function fmtDate(raw: string | undefined | null): string {
  if (!raw) return '—'
  try {
    return new Date(raw).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch { return raw }
}

function StatusBadge({ status }: { status: string }) {
  const ok = status === 'current'
  return (
    <span className={`rounded-md border px-2 py-0.5 text-[11px] font-semibold ${ok ? 'bg-semantic-success/10 text-semantic-success border-semantic-success/25' : 'bg-semantic-error/10 text-semantic-error border-semantic-error/25'}`}>
      {ok ? 'Current' : status}
    </span>
  )
}

function BackendRow({ label, data }: { label: string; data: SourceCoverage }) {
  const missing = data.missing_lists ?? []
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-border bg-app/60 px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[13px] font-semibold text-text-primary">{label}</p>
        <StatusBadge status={data.status} />
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-text-muted">
        <span>Ingested <span className="text-text-secondary">{fmtDate(data.ingested_at)}</span></span>
        {missing.length > 0
          ? <span className="text-semantic-error font-medium">Missing: {missing.join(', ')}</span>
          : <span>No missing lists</span>}
      </div>
    </div>
  )
}

export function ScreeningAssurance() {
  const { user } = useAuth()
  const [data, setData] = useState<Assurance | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = async () => {
    try {
      const response = await getScreeningAssurance()
      if (!response.ok) throw new Error('Source assurance is unavailable. Check the service before relying on screening.')
      setData(await response.json())
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to load source assurance')
    }
  }

  useEffect(() => { void load() }, [])

  const reconcile = async () => {
    setBusy(true)
    try {
      const response = await reconcileScreening()
      if (!response.ok) throw new Error('Reconciliation could not start. Check source coverage and service logs.')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Reconciliation failed')
    } finally {
      setBusy(false)
    }
  }

  const heartbeatStale = data?.monitor?.last_tick_at
    ? Date.now() - new Date(data.monitor.last_tick_at).getTime() > 300_000
    : false

  const versionMismatch = data && data.postgres.version !== data.parquet.version

  return (
    <Card>
      <CardHeader>
        <CardTitle>Source coverage and monitoring</CardTitle>
      </CardHeader>
      <CardBody className="space-y-4">
        {error && <ErrorBox message={error} />}

        {data && (
          <>
            {/* Backend status */}
            <div className="space-y-2">
              <BackendRow label="Interactive screening" data={data.postgres} />
              <BackendRow label="Background screening" data={data.parquet} />
              {versionMismatch && (
                <ErrorBox message="The two screening backends are on different dataset versions. Refresh both before relying on consistent results." />
              )}
              <p className="text-[11px] text-text-muted px-1">
                Publication times are not verified by the current feed — freshness reflects ingestion time only.
              </p>
            </div>

            {/* Monitoring */}
            <div className="rounded-lg border border-border bg-app/60 px-4 py-3 space-y-2">
              <div className="flex items-center justify-between gap-3">
                <p className="text-[13px] font-semibold text-text-primary">Monitoring</p>
                {heartbeatStale
                  ? <span className="flex items-center gap-1 text-[11px] font-semibold text-semantic-error"><BiXCircle className="h-3.5 w-3.5" aria-hidden />Heartbeat stale</span>
                  : data.monitor?.last_tick_at
                    ? <span className="flex items-center gap-1 text-[11px] font-semibold text-semantic-success"><BiCheckCircle className="h-3.5 w-3.5" aria-hidden />Active</span>
                    : <span className="flex items-center gap-1 text-[11px] text-text-muted"><BiTime className="h-3.5 w-3.5" aria-hidden />Not yet recorded</span>
                }
              </div>
              <p className="text-[11px] text-text-muted">
                Last heartbeat <span className="text-text-secondary">{fmtDate(data.monitor?.last_tick_at)}</span>
              </p>
              {data.monitor?.last_error && <ErrorBox message={data.monitor.last_error} />}
              {heartbeatStale && <ErrorBox message="Monitoring has not reported for over five minutes. Check the screening worker." />}
              {data.refresh.last_error && <ErrorBox message={data.refresh.last_error} />}
            </div>

            {/* Recent cycles */}
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-wider text-text-muted mb-2 px-1">Recent monitoring cycles</p>
              {data.cycles.length === 0
                ? <p className="text-[12px] text-text-muted px-1">No monitoring cycle has been recorded.</p>
                : (
                  <div className="divide-y divide-border rounded-lg border border-border overflow-hidden">
                    {data.cycles.slice(0, 3).map((c) => (
                      <div key={c.cycle_id} className="flex items-center justify-between gap-4 px-4 py-2.5 bg-app/40">
                        <span className="text-[12px] text-text-secondary">{fmtDate(c.started_at)}</span>
                        <div className="flex items-center gap-3">
                          {c.error && <span className="text-[11px] text-semantic-error">{c.error}</span>}
                          <span className="text-[11px] text-text-muted tabular-nums">{c.queued.toLocaleString()} queued</span>
                          <span className={`rounded-md border px-1.5 py-0.5 text-[10.5px] font-semibold ${c.status === 'running' ? 'bg-semantic-warning/10 text-semantic-warning border-semantic-warning/25' : c.status === 'completed' ? 'bg-semantic-success/10 text-semantic-success border-semantic-success/25' : 'bg-surface text-text-muted border-border'}`}>
                            {c.status}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )
              }
            </div>
          </>
        )}

        <div className="flex gap-2 pt-1">
          <Button variant="secondary" size="sm" onClick={() => void load()}>Refresh status</Button>
          {user?.is_admin && (
            <Button size="sm" disabled={busy} onClick={() => void reconcile()}>
              {busy ? 'Queueing…' : 'Reconcile monitored entities'}
            </Button>
          )}
        </div>
      </CardBody>
    </Card>
  )
}
