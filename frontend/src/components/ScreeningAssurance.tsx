import { useEffect, useState } from 'react'
import { Button, Card, CardHeader, CardTitle, CardBody, ErrorBox } from '@/components'
import { getScreeningAssurance, reconcileScreening } from '@/api/client'
import { useAuth } from '@/context/AuthContext'
import type { SourceCoverage } from '@/types/api'

type Assurance = { monitor?: {last_tick_at?: string; last_error?: string}; postgres: SourceCoverage; parquet: SourceCoverage; refresh: { last_error?: string; last_success_at?: string }; cycles: Array<{cycle_id: string; status: string; queued: number; started_at: string; error?: string}> }
export function ScreeningAssurance() {
  const { user } = useAuth()
  const [data, setData] = useState<Assurance | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const load = async () => {
    try {
      const response = await getScreeningAssurance()
      if (!response.ok) throw new Error('Source assurance is unavailable. Check the service before relying on screening.')
      setData(await response.json()); setError('')
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to load source assurance') }
  }
  useEffect(() => { void load() }, [])
  const reconcile = async () => {
    setBusy(true)
    try {
      const response = await reconcileScreening()
      if (!response.ok) throw new Error('Reconciliation could not start. Check source coverage and service logs.')
      await load()
    } catch (e) { setError(e instanceof Error ? e.message : 'Reconciliation failed') }
    finally { setBusy(false) }
  }
  return <Card><CardHeader><CardTitle>Source coverage and monitoring</CardTitle></CardHeader><CardBody className="space-y-3 text-sm">
    {error && <ErrorBox message={error} />}
    {data && <>
      {(['postgres', 'parquet'] as const).map(backend => <div key={backend}>
        <p className="font-medium">{backend === 'postgres' ? 'Interactive screening' : 'Background screening'}: {data[backend].status}</p>
        <p>Ingested: {data[backend].ingested_at ? new Date(data[backend].ingested_at!).toLocaleString() : 'Unknown'} · Missing lists: {data[backend].missing_lists?.join(', ') || 'None'}</p>
      </div>)}
      <p>Publication times are not verified by the current feed. Freshness describes ingestion time.</p>
      {data.postgres.version !== data.parquet.version && <ErrorBox message="The screening backends contain different dataset versions. Refresh both before relying on consistent results." />}
      <p>Monitoring heartbeat: {data.monitor?.last_tick_at ? new Date(data.monitor.last_tick_at).toLocaleString() : 'Not yet recorded'}</p>
      {data.monitor?.last_error && <ErrorBox message={data.monitor.last_error} />}
      {data.monitor?.last_tick_at && Date.now() - new Date(data.monitor.last_tick_at).getTime() > 300000 && <ErrorBox message="Monitoring has not reported for over five minutes. Check the screening worker." />}
      {data.refresh.last_error && <ErrorBox message={data.refresh.last_error} />}
      <p>Full-population monitoring covers all configured sanctions sources and PEPs. Failed checks require attention.</p>
      {data.cycles.slice(0, 3).map(c => <p key={c.cycle_id}>{new Date(c.started_at).toLocaleString()}: {c.status} · {c.queued} checks queued {c.error && `· ${c.error}`}</p>)}
      {!data.cycles.length && <p>No monitoring cycle has been recorded.</p>}
    </>}
    <div className="flex gap-2"><Button variant="secondary" onClick={() => void load()}>Refresh status</Button>
    {user?.is_admin && <Button disabled={busy} onClick={() => void reconcile()}>{busy ? 'Queueing…' : 'Reconcile monitored entities'}</Button>}</div>
  </CardBody></Card>
}
