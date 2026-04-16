import { useEffect, useMemo, useState } from 'react'
import { BiCheckCircle, BiRefresh, BiXCircle } from 'react-icons/bi'
import { Button, Card, CardBody, CardHeader, CardTitle, ErrorBox, Modal, SectionHeader } from '@/components'
import {
  approveAiTriageTask,
  getAiTriageTask,
  listAiTriageTasks,
  rejectAiTriageTask,
  type AiTriageTask,
} from '@/api/client'

function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  try {
    return new Date(value).toLocaleString()
  } catch {
    return value
  }
}

function recommendationTone(value: string): string {
  const upper = (value || '').toUpperCase()
  if (upper === 'CLEAR') return 'bg-[rgba(34,197,94,0.12)] border-[rgba(34,197,94,0.25)] text-[#16a34a]'
  if (upper === 'INVESTIGATE') return 'bg-[rgba(239,68,68,0.12)] border-[rgba(239,68,68,0.25)] text-[#dc2626]'
  return 'bg-[rgba(245,158,11,0.12)] border-[rgba(245,158,11,0.25)] text-[#d97706]'
}

function RecommendationBadge({ value }: { value: string }) {
  return (
    <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${recommendationTone(value)}`}>
      {value}
    </span>
  )
}

function formatConfidencePercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return `${Math.round(Number(value) * 100)}%`
}

function formatConfidenceBandPercent(value: string | null | undefined): string {
  if (!value) return '—'
  if (value === '<0.70') return '<70%'
  const rangeMatch = value.match(/^0\.(\d+)-0\.(\d+)$/)
  if (rangeMatch) {
    return `${rangeMatch[1]}%-${rangeMatch[2]}%`
  }
  const plusMatch = value.match(/^0\.(\d+)\+$/)
  if (plusMatch) {
    return `${plusMatch[1]}%+`
  }
  return value
}

export function AiTriagePage() {
  const [items, setItems] = useState<AiTriageTask[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<AiTriageTask | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [reviewNotes, setReviewNotes] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await listAiTriageTasks({ status: 'PENDING_REVIEW', limit: 200 })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError((data as { detail?: string }).detail ?? 'Failed to load AI suggestions.')
        setItems([])
        return
      }
      setItems(((data as { items?: AiTriageTask[] }).items ?? []) as AiTriageTask[])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load AI suggestions.')
      setItems([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const openDetail = async (item: AiTriageTask) => {
    setSelected(item)
    setReviewNotes('')
    setActionError(null)
    setDetailError(null)
    setDetailLoading(true)
    try {
      const res = await getAiTriageTask(item.triage_id)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setDetailError((data as { detail?: string }).detail ?? 'Failed to load AI suggestion detail.')
        return
      }
      setSelected(data as AiTriageTask)
    } catch (err) {
      setDetailError(err instanceof Error ? err.message : 'Failed to load AI suggestion detail.')
    } finally {
      setDetailLoading(false)
    }
  }

  const handleAction = async (action: 'approve' | 'reject', applyClear = false) => {
    if (!selected) return
    setActionLoading(true)
    setActionError(null)
    try {
      const res =
        action === 'approve'
          ? await approveAiTriageTask(selected.triage_id, reviewNotes.trim() || undefined, applyClear)
          : await rejectAiTriageTask(selected.triage_id, reviewNotes.trim() || undefined)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setActionError((data as { detail?: string }).detail ?? `Failed to ${action} AI suggestion.`)
        return
      }
      setToast(
        action === 'approve'
          ? applyClear
            ? 'AI suggestion approved and screening result cleared.'
            : 'AI suggestion approved.'
          : (() => {
              const claim = (data as { review_claim?: { status?: string; error?: string; item?: { review_claimed_by?: string | null } } }).review_claim
              if (claim?.status === 'ok') return 'AI suggestion rejected and moved into your Match Review queue.'
              if (claim?.error === 'claimed_by_other') return 'AI suggestion rejected. The underlying record is already claimed by another user in Match Review.'
              return 'AI suggestion rejected. Please review the record manually in Match Review.'
            })(),
      )
      setSelected(null)
      setReviewNotes('')
      await load()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : `Failed to ${action} AI suggestion.`)
    } finally {
      setActionLoading(false)
    }
  }

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 2500)
    return () => window.clearTimeout(timer)
  }, [toast])

  const counters = useMemo(
    () => ({
      clear: items.filter((item) => item.effective_recommended_action === 'CLEAR').length,
      investigate: items.filter((item) => item.effective_recommended_action === 'INVESTIGATE').length,
      unsure: items.filter((item) => item.effective_recommended_action === 'UNSURE').length,
    }),
    [items],
  )

  return (
    <div className="px-[26px] pt-[22px] pb-[26px]">
      <div className="max-w-[1600px] space-y-6">
        <SectionHeader title="AI suggestions" />

        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" variant="secondary" onClick={() => void load()} disabled={loading}>
            <BiRefresh className="h-4 w-4" />
            {loading ? 'Refreshing…' : 'Refresh suggestions'}
          </Button>
          <div className="flex flex-wrap gap-2 text-xs text-text-secondary">
            <span className="rounded-full border border-border bg-white px-3 py-1">Pending: {items.length}</span>
            <span className="rounded-full border border-border bg-white px-3 py-1">Clear: {counters.clear}</span>
            <span className="rounded-full border border-border bg-white px-3 py-1">Investigate: {counters.investigate}</span>
            <span className="rounded-full border border-border bg-white px-3 py-1">Unsure: {counters.unsure}</span>
          </div>
        </div>

        {toast && <div className="rounded-lg border border-[rgba(34,197,94,0.25)] bg-[rgba(34,197,94,0.08)] px-4 py-3 text-sm text-[#15803d]">{toast}</div>}
        {error && <ErrorBox message={error} />}

        <Card>
          <CardHeader>
            <CardTitle>Pending AI recommendations</CardTitle>
          </CardHeader>
          <CardBody>
            {items.length === 0 ? (
              <p className="text-sm text-text-secondary">
                {loading ? 'Loading AI suggestions…' : 'No pending AI recommendations right now.'}
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-border text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase tracking-[0.04em] text-text-muted">
                      <th className="px-3 py-2">Searched entity</th>
                      <th className="px-3 py-2">Matched entity</th>
                      <th className="px-3 py-2">Recommendation</th>
                      <th className="px-3 py-2">Confidence</th>
                      <th className="px-3 py-2">Rationale</th>
                      <th className="px-3 py-2">Created</th>
                      <th className="px-3 py-2">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {items.map((item) => (
                      <tr key={item.triage_id} className="align-top">
                        <td className="px-3 py-3">
                          <div className="font-semibold text-text-primary">{item.submitted_name}</div>
                          <div className="text-xs text-text-secondary">{item.submitted_entity_type ?? 'Unknown type'}</div>
                        </td>
                        <td className="px-3 py-3">
                          <div className="font-semibold text-text-primary">{item.matched_name ?? '—'}</div>
                          <div className="text-xs text-text-secondary">{item.matched_entity_type ?? item.source_label ?? 'Unknown source'}</div>
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex flex-col gap-2">
                            <RecommendationBadge value={item.effective_recommended_action} />
                            {item.guardrail_overridden && (
                              <span className="text-xs text-[#dc2626]">Guardrail override applied</span>
                            )}
                          </div>
                        </td>
                        <td className="px-3 py-3 text-text-secondary">
                          <div>{formatConfidenceBandPercent(item.ai_confidence_band)}</div>
                          <div className="text-xs">{formatConfidencePercent(item.ai_confidence_raw)}</div>
                        </td>
                        <td className="px-3 py-3 text-text-secondary max-w-[320px]">{item.rationale_short ?? '—'}</td>
                        <td className="px-3 py-3 text-text-secondary">{formatDate(item.created_at)}</td>
                        <td className="px-3 py-3">
                          <Button type="button" variant="secondary" onClick={() => void openDetail(item)}>
                            Review
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
        isOpen={selected !== null}
        onClose={() => {
          setSelected(null)
          setReviewNotes('')
          setActionError(null)
          setDetailError(null)
        }}
        size="wide"
        title="AI suggestion detail"
        footer={
          <div className="flex w-full items-center justify-between gap-3">
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                setSelected(null)
                setReviewNotes('')
                setActionError(null)
              }}
            >
              Close
            </Button>
            <div className="flex items-center gap-2">
              <Button type="button" variant="secondary" onClick={() => void handleAction('reject')} disabled={actionLoading || detailLoading}>
                <BiXCircle className="h-4 w-4" />
                Reject
              </Button>
              <Button type="button" variant="secondary" onClick={() => void handleAction('approve')} disabled={actionLoading || detailLoading}>
                <BiCheckCircle className="h-4 w-4" />
                Approve only
              </Button>
              <Button type="button" onClick={() => void handleAction('approve', true)} disabled={actionLoading || detailLoading}>
                <BiCheckCircle className="h-4 w-4" />
                Approve & clear
              </Button>
            </div>
          </div>
        }
      >
        {detailLoading && <p className="text-sm text-text-secondary">Loading suggestion detail…</p>}
        {detailError && <ErrorBox message={detailError} />}
        {selected && !detailLoading && (
          <div className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Original entity</CardTitle>
                </CardHeader>
                <CardBody className="space-y-2 text-sm text-text-secondary">
                  <p><span className="font-semibold text-text-primary">Name:</span> {selected.submitted_name}</p>
                  <p><span className="font-semibold text-text-primary">Type:</span> {selected.submitted_entity_type ?? '—'}</p>
                  <p><span className="font-semibold text-text-primary">Business reference:</span> {selected.business_reference ?? '—'}</p>
                  <p><span className="font-semibold text-text-primary">Reason for check:</span> {selected.reason_for_check ?? '—'}</p>
                </CardBody>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Matched entity</CardTitle>
                </CardHeader>
                <CardBody className="space-y-2 text-sm text-text-secondary">
                  <p><span className="font-semibold text-text-primary">Name:</span> {selected.matched_name ?? '—'}</p>
                  <p><span className="font-semibold text-text-primary">Type:</span> {selected.matched_entity_type ?? '—'}</p>
                  <p><span className="font-semibold text-text-primary">Country:</span> {selected.matched_country ?? '—'}</p>
                  <p><span className="font-semibold text-text-primary">Source:</span> {selected.source_label ?? '—'}</p>
                </CardBody>
              </Card>
            </div>

            <Card>
              <CardHeader>
                <CardTitle>AI assessment</CardTitle>
              </CardHeader>
              <CardBody className="space-y-3 text-sm text-text-secondary">
                <div className="flex flex-wrap items-center gap-2">
                  <RecommendationBadge value={selected.effective_recommended_action} />
                  <span className="rounded-full border border-border bg-white px-3 py-1 text-xs">
                    Confidence {formatConfidenceBandPercent(selected.ai_confidence_band)}
                  </span>
                  {selected.guardrail_overridden && (
                    <span className="rounded-full border border-[rgba(239,68,68,0.25)] bg-[rgba(239,68,68,0.08)] px-3 py-1 text-xs text-[#dc2626]">
                      Guardrail override
                    </span>
                  )}
                </div>
                <p><span className="font-semibold text-text-primary">Rationale:</span> {selected.rationale_short ?? '—'}</p>
                <p><span className="font-semibold text-text-primary">Reviewer note:</span> {selected.explanation_json?.reviewer_note ?? '—'}</p>
                <div>
                  <p className="font-semibold text-text-primary">Key differences</p>
                  {(selected.explanation_json?.key_differences ?? []).length === 0 ? (
                    <p className="mt-1">No structured differences returned.</p>
                  ) : (
                    <ul className="mt-2 list-disc pl-5">
                      {(selected.explanation_json?.key_differences ?? []).map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  )}
                </div>
                {selected.guardrail_overridden && (
                  <div>
                    <p className="font-semibold text-text-primary">Guardrail reasons</p>
                    <ul className="mt-2 list-disc pl-5">
                      {(selected.guardrail_reasons ?? []).map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </CardBody>
            </Card>

            <div>
              <label htmlFor="ai-triage-review-notes" className="mb-2 block text-xs font-medium text-text-muted">
                Reviewer note
              </label>
              <textarea
                id="ai-triage-review-notes"
                value={reviewNotes}
                onChange={(event) => setReviewNotes(event.target.value)}
                className="min-h-[120px] w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/15"
                placeholder="Optional note for why you approved, rejected, or cleared this AI recommendation."
              />
              <p className="mt-2 text-xs text-text-secondary">
                If you choose <span className="font-semibold">Approve & clear</span>, the AI rationale and your reviewer note will be stored against the underlying screening record as part of the false-positive clear reason.
              </p>
            </div>
            {actionError && <ErrorBox message={actionError} />}
          </div>
        )}
      </Modal>
    </div>
  )
}
