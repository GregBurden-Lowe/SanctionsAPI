import { ReviewDecisionCard } from '@/components/ReviewDecisionCard'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Button, Card, CardBody, ErrorBox, Input, SectionHeader } from '@/components'
import { screeningHistory, screeningRequest } from '@/api/client'
import { ResultCard, ScreeningInputsCard } from '@/pages/ScreeningPage'
import type { OpCheckResponse, ReviewDecision } from '@/types/api'

type HistoryItem = {
  request_id: string
  requested_at: string
  actor: string | null
  source: string
  business_reference: string | null
  status: string
  reused: boolean
  inputs: { name?: string; dob?: string; country?: string; entity_type?: string; requestor?: string; business_reference?: string; reason_for_check?: string; search_backend?: 'original' | 'postgres_beta' }
  response_json?: OpCheckResponse & { message?: string }
  screened_at?: string
  review_decisions?: ReviewDecision[]
  retry_of?: string
  snapshot_kind?: string
}
const date = (value?: string) => value ? new Date(value).toLocaleString('en-GB') : '—'

export function ScreeningHistoryPage() {
  const [reference, setReference] = useState('')
  const [filter, setFilter] = useState('')
  const [offset, setOffset] = useState(0)
  const [items, setItems] = useState<HistoryItem[]>([])
  const [more, setMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    setLoading(true); setError(null); setItems([])
    screeningHistory(filter, offset).then(async res => {
      if (!res.ok) throw new Error('Unable to load screening history.')
      const data = await res.json()
      if (active) { setItems(data.items); setMore(data.has_more) }
    }).catch(e => { if (active) setError(e.message) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [filter, offset])
  return <div className="p-6 space-y-5">
    <SectionHeader title="Screening history" />
    <p className="text-sm text-text-secondary">Each request is kept separately, including checks that reused an earlier result. Legacy entries preserve only the evidence available when history was introduced.</p>
    <form className="flex gap-3 items-end" onSubmit={e => { e.preventDefault(); setOffset(0); setFilter(reference.trim()) }}>
      <Input label="Business reference" value={reference} onChange={e => setReference(e.target.value)} placeholder="Exact policy or claim reference" />
      <Button type="submit">Search</Button>
      <Button type="button" variant="secondary" onClick={() => { setReference(''); setFilter(''); setOffset(0) }}>Show all</Button>
    </form>
    {error && <ErrorBox message={error} />}
    {loading ? <p>Loading history…</p> : <Card><CardBody><div className="overflow-x-auto">
      <table className="w-full text-sm text-left"><thead><tr>
        {['Requested', 'Name', 'Reference', 'Recorded actor', 'Status', 'Result', ''].map((h, i) => <th key={i} className="p-3">{h}</th>)}
      </tr></thead><tbody>{items.map(item => <tr key={item.request_id} className="border-t border-border">
        <td className="p-3">{date(item.requested_at)}</td><td className="p-3">{item.inputs.name}</td><td className="p-3">{item.business_reference || '—'}</td>
        <td className="p-3">{item.actor || 'Not recorded'}</td><td className="p-3">{item.status}</td>
        <td className="p-3">{item.source === 'legacy_snapshot' ? 'Legacy snapshot' : item.status === 'completed' ? item.reused ? 'Reused' : 'New screening' : '—'}</td>
        <td className="p-3"><Link className="underline" to={`/history/${item.request_id}`}>Open record</Link></td>
      </tr>)}</tbody></table>
      {!items.length && <p className="p-3">No requests found.</p>}
    </div></CardBody></Card>}
    <div className="flex gap-3"><Button variant="secondary" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous</Button>
      <Button variant="secondary" disabled={loading || !more} onClick={() => setOffset(offset + 25)}>Next</Button></div>
  </div>
}

export function ScreeningRequestPage() {
  const { requestId } = useParams()
  const [item, setItem] = useState<HistoryItem | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    setItem(null); setError(null)
    const load = async () => {
      try {
        const res = await screeningRequest(requestId!)
        if (!res.ok) throw new Error(res.status === 404 ? 'Screening request not found.' : 'Unable to load this screening request.')
        const data: HistoryItem = await res.json()
        if (!active) return
        setItem(data)
        if (data.status === 'queued' || data.status === 'requested') timer = setTimeout(load, 3000)
      } catch (e) { if (active) setError(e instanceof Error ? e.message : 'Unable to load record.') }
    }
    void load()
    return () => { active = false; clearTimeout(timer) }
  }, [requestId])
  const laterDecisions = item?.review_decisions?.filter(decision => decision.decision_id !== item.response_json?.['Review Decision']?.decision_id) || []
  const input = item?.inputs
  return <div className="p-6 space-y-5">
    <SectionHeader title="Saved screening request" />
    <Link className="underline text-sm" to="/history">Back to screening history</Link>
    {error && <ErrorBox message={error} />}
    {!item && !error && <p>Loading record…</p>}
    {item && input && <>
      {item.status === 'completed' && item.response_json?.['Check Summary'] && <ScreeningInputsCard result={item.response_json} searchDetails={{
        searchName: input.name || '', searchDob: input.dob || '', searchCountry: input.country || '', entityType: input.entity_type || 'Person',
        businessReference: input.business_reference || '', reasonForCheck: input.reason_for_check || '', requestor: input.requestor || '', searchBackend: input.search_backend,
      }} />}
      <Card><CardBody><dl className="grid grid-cols-2 gap-3 text-sm">
        <dt>Requested</dt><dd>{date(item.requested_at)}</dd><dt>Screened</dt><dd>{date(item.screened_at)}</dd>
        <dt>Business reference</dt><dd>{item.business_reference || '—'}</dd><dt>Authenticated actor</dt><dd>{item.actor || 'Not recorded'}</dd>
        <dt>Requested by</dt><dd>{input.requestor || '—'}</dd><dt>Status</dt><dd>{item.status}{item.reused ? ' · Previous result reused' : ''}</dd>
        {!input.country && item.response_json?.['Screening Inputs']?.country && <><dt>Country submitted</dt><dd>Not supplied; the screened country is shown above.</dd></>}
        <dt>Request ID</dt><dd className="break-all">{item.request_id}</dd>
      </dl></CardBody></Card>
      {item.source === 'legacy_snapshot' && <p>This is a legacy snapshot, not a reconstructed search history. Earlier overwritten requests and authenticated actor information are unavailable.</p>}
      {(item.status === 'queued' || item.status === 'requested') && <p>Screening is pending. This page will update automatically; no clearance has been issued.</p>}
      {item.status === 'failed' && <div className="space-y-3"><ErrorBox message={item.response_json?.message || 'Screening failed. No clearance has been issued.'} />
        <Link className="underline" to="/" state={{ retry: { request_id: item.request_id, inputs: item.inputs } }}>Review inputs and retry</Link>
        <p className="text-sm text-text-secondary">Retrying creates a linked request and preserves this failed record. You can retry requests recorded under your own account.</p>
      </div>}
      {item.retry_of && <Link className="underline text-sm" to={`/history/${item.retry_of}`}>View original failed request</Link>}
      {!!laterDecisions.length && <section className="space-y-3"><h3 className="font-semibold">Review decisions for this run</h3>
        <p className="text-sm text-text-secondary">These decisions may have been recorded after this request. The saved response below remains as it was returned at the time.</p>
        {laterDecisions.map(decision => <ReviewDecisionCard key={decision.decision_id} decision={decision} />)}
      </section>}
      {item.status === 'completed' && item.response_json?.['Check Summary'] && <ResultCard result={{ ...item.response_json, request_id: item.request_id }} searchDetails={{
        searchName: input.name || '', searchDob: input.dob || '', searchCountry: input.country || '', entityType: input.entity_type || 'Person',
        businessReference: input.business_reference || '', reasonForCheck: input.reason_for_check || '', requestor: input.requestor || '', searchBackend: input.search_backend,
      }} showScreeningInputs={false} />}
    </>}
  </div>
}
