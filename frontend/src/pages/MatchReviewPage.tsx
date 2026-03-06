import { useEffect, useState } from 'react'
import { BiCheckCircle, BiRefresh, BiSearch, BiShow } from 'react-icons/bi'
import { Button, Card, ErrorBox, Input, Modal } from '@/components'
import {
  claimReview,
  completeReview,
  getReviewQueue,
  rerunReview,
  type ReviewRerunResponse,
  type ReviewQueueItem,
} from '@/api/client'
import type { ReviewOutcome } from '@/types/api'
import { useAuth } from '@/context/AuthContext'

const REVIEW_OUTCOME_OPTIONS: ReviewOutcome[] = [
  'False Positive - Proceeded',
  'False Positive - Payment Released',
  'Confirmed Match - Payment Blocked',
  'Confirmed Match - Escalated to Compliance',
  'Pending External Review',
  'Cancelled / No Action Required',
]

const FILTER_CHIPS = ['All', 'PEP', 'Sanctions', 'Adverse Media', 'Watchlist'] as const
type FilterChip = (typeof FILTER_CHIPS)[number]

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: 'short',
      timeStyle: 'short',
    })
  } catch {
    return iso
  }
}

function DecisionBadge({ decision }: { decision: string }) {
  const isSanctions = decision.toLowerCase().includes('sanction')
  return (
    <span
      className={`inline-flex items-center gap-[5px] rounded-[20px] border px-[10px] py-[3px] pl-[7px] font-mono text-[11.5px] font-semibold uppercase tracking-[0.04em] whitespace-nowrap ${
        isSanctions
          ? 'bg-[rgba(230,100,50,0.12)] border-[rgba(230,100,50,0.3)] text-[#e06030]'
          : 'bg-[rgba(220,60,60,0.1)] border-[rgba(220,60,60,0.25)] text-[#d94040]'
      }`}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${isSanctions ? 'bg-[#e06030]' : 'bg-[#d94040]'}`} />
      {decision}
    </span>
  )
}

function sectionDotTone(section: 'claimed' | 'unclaimed'): string {
  if (section === 'claimed') return '#3b82f6'
  return '#f59e0b'
}

export function MatchReviewPage() {
  const { user } = useAuth()
  const [items, setItems] = useState<ReviewQueueItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [activeChip, setActiveChip] = useState<FilterChip>('All')
  const [selected, setSelected] = useState<ReviewQueueItem | null>(null)
  const [reviewOutcome, setReviewOutcome] = useState<ReviewOutcome>(REVIEW_OUTCOME_OPTIONS[0])
  const [reviewNotes, setReviewNotes] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [rerunTarget, setRerunTarget] = useState<ReviewQueueItem | null>(null)
  const [rerunEntityType, setRerunEntityType] = useState<'Person' | 'Organization'>('Person')
  const [rerunDob, setRerunDob] = useState('')
  const [rerunCountry, setRerunCountry] = useState('')
  const [rerunLoading, setRerunLoading] = useState(false)
  const [rerunMessage, setRerunMessage] = useState<string | null>(null)
  const [idModalKey, setIdModalKey] = useState<string | null>(null)
  const [detailItem, setDetailItem] = useState<ReviewQueueItem | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const currentUsername = (user?.username || '').trim().toLowerCase()
  const scopedItems = items.filter((item) => item.review_status !== 'COMPLETED')
  const myClaimedItems = scopedItems.filter(
    (item) =>
      item.review_status === 'IN_REVIEW' &&
      (item.review_claimed_by || '').trim().toLowerCase() === currentUsername,
  )
  const unclaimedItems = scopedItems.filter((item) => {
    const claimedBy = (item.review_claimed_by || '').trim()
    if (claimedBy) return false
    return item.review_status === 'UNREVIEWED' || item.review_status === 'IN_REVIEW'
  })
  const completedCount = items.filter((item) => item.review_status === 'COMPLETED').length

  const showToast = (message: string) => {
    setToast(message)
    window.setTimeout(() => setToast(null), 2500)
  }

  const matchesViewFilters = (item: ReviewQueueItem): boolean => {
    const needle = searchTerm.trim().toLowerCase()
    const haystack = `${item.entity_name || ''} ${item.business_reference || ''} ${item.screening_user || ''}`.toLowerCase()
    const searchOk = !needle || haystack.includes(needle)
    const decision = (item.decision || '').toLowerCase()
    let chipOk = true
    if (activeChip === 'PEP') chipOk = decision.includes('pep')
    if (activeChip === 'Sanctions') chipOk = decision.includes('sanction')
    if (activeChip === 'Adverse Media') chipOk = decision.includes('adverse media')
    if (activeChip === 'Watchlist') chipOk = decision.includes('watchlist')
    return searchOk && chipOk
  }
  const myClaimedDisplay = myClaimedItems.filter(matchesViewFilters)
  const unclaimedDisplay = unclaimedItems.filter(matchesViewFilters)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await getReviewQueue({ include_cleared: false, limit: 300 })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError((data as { detail?: string }).detail ?? 'Failed to load review queue.')
        setItems([])
        return
      }
      setItems(((data as { items?: ReviewQueueItem[] }).items ?? []) as ReviewQueueItem[])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load review queue.')
      setItems([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const handleClaim = async (row: ReviewQueueItem) => {
    setActionLoading(true)
    setActionError(null)
    try {
      const res = await claimReview(row.entity_key)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setActionError((data as { detail?: string }).detail ?? 'Failed to claim review.')
        return
      }
      await load()
      showToast(`Claimed review for ${row.entity_name}`)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to claim review.')
    } finally {
      setActionLoading(false)
    }
  }

  const handleComplete = async () => {
    if (!selected) return
    const notes = reviewNotes.trim()
    if (notes.length < 10) {
      setActionError('Review notes must be at least 10 characters.')
      return
    }
    setActionLoading(true)
    setActionError(null)
    try {
      const res = await completeReview(selected.entity_key, {
        review_outcome: reviewOutcome,
        review_notes: notes,
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setActionError((data as { detail?: string }).detail ?? 'Failed to complete review.')
        return
      }
      const completedName = selected.entity_name
      setSelected(null)
      setReviewNotes('')
      setReviewOutcome(REVIEW_OUTCOME_OPTIONS[0])
      await load()
      showToast(`Review completed for ${completedName}`)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to complete review.')
    } finally {
      setActionLoading(false)
    }
  }

  const openRerun = (row: ReviewQueueItem) => {
    setRerunTarget(row)
    setRerunEntityType((row.entity_type || '').toLowerCase() === 'organization' ? 'Organization' : 'Person')
    setRerunDob(row.date_of_birth ?? '')
    setRerunCountry(row.country_input ?? '')
    setRerunMessage(null)
    setActionError(null)
  }

  const handleRerun = async () => {
    if (!rerunTarget) return
    const isPerson = rerunEntityType === 'Person'
    const dob = rerunDob.trim()
    const country = rerunCountry.trim()
    if (isPerson && !dob) {
      setActionError('Date of birth is required for Person re-run.')
      return
    }
    if (!isPerson && !country) {
      setActionError('Country is required for Organization re-run.')
      return
    }
    setRerunLoading(true)
    setActionError(null)
    setRerunMessage(null)
    try {
      const res = await rerunReview(rerunTarget.entity_key, {
        dob: isPerson ? dob : null,
        country: isPerson ? null : country,
        entity_type: rerunEntityType,
      })
      const data = (await res.json().catch(() => ({}))) as Partial<ReviewRerunResponse> & { detail?: string }
      if (!res.ok) {
        setActionError(data.detail ?? 'Re-run failed.')
        return
      }
      setRerunMessage(
        data.auto_completed
          ? `Re-run decision: ${data.decision ?? 'Unknown'}. Match was auto-completed as reviewed.`
          : `Re-run decision: ${data.decision ?? 'Unknown'}.`,
      )
      await load()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Re-run failed.')
    } finally {
      setRerunLoading(false)
    }
  }

  return (
    <div className="px-[26px] pt-[22px] pb-[26px]">
      <div className="w-full max-w-[1600px] space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-page-title">Match Review Queue</h1>
            <p className="text-[13px] text-[#64748b] mt-0.5">Review and clear flagged entity matches</p>
          </div>
          <div className="flex gap-2">
            {[
              { label: 'My Queue', value: myClaimedItems.length, color: '#3b82f6' },
              { label: 'Unclaimed', value: unclaimedItems.length, color: '#f59e0b' },
              { label: 'Completed', value: completedCount, color: '#22c55e' },
            ].map((stat) => (
              <div
                key={stat.label}
                className="bg-white rounded-[11px] border border-[#e2e8f0] px-4 py-[10px] min-w-[72px] text-center"
              >
                <div className="font-mono text-[20px] leading-none font-extrabold" style={{ color: stat.color }}>
                  {stat.value}
                </div>
                <div className="mt-[3px] text-[10.5px] uppercase tracking-[0.03em] text-[#94a3b8]">{stat.label}</div>
              </div>
            ))}
          </div>
        </div>

        <Card className="p-0">
          <div className="flex flex-wrap items-center gap-3 p-[13px_18px]">
            <div className="relative flex-[1_1_180px] max-w-[280px]">
              <BiSearch className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-[#94a3b8]" />
              <input
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                placeholder="Search by name or reference…"
                className="w-full h-[35px] rounded-lg border border-[#e2e8f0] bg-[#f8fafc] pl-8 pr-2.5 text-[12.5px] text-[#1e293b] outline-none transition focus:border-[#3b82f6]"
              />
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              {FILTER_CHIPS.map((chip) => {
                const active = chip === activeChip
                return (
                  <button
                    key={chip}
                    type="button"
                    onClick={() => setActiveChip(chip)}
                    className={`rounded-[20px] px-3 py-1 text-xs font-semibold transition ${
                      active
                        ? 'bg-[#eff6ff] text-[#2563eb] border border-[#3b82f6]'
                        : 'bg-transparent text-[#64748b] border border-[#e2e8f0] hover:bg-[#f8fafc]'
                    }`}
                  >
                    {chip}
                  </button>
                )
              })}
            </div>
          </div>
        </Card>

        {error && <ErrorBox message={error} />}
        {actionError && <ErrorBox message={actionError} />}

        <Card className="p-0 overflow-hidden">
          <div className="px-5 py-[13px] border-b border-[#f1f5f9] flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: sectionDotTone('claimed'), boxShadow: `0 0 0 3px rgba(59,130,246,0.2)` }}
              />
              <span className="font-bold text-[13.5px] text-[#0f2340]">My claimed reviews</span>
              <span className="rounded-[20px] border border-[#bfdbfe] bg-[#eff6ff] px-2 py-0.5 text-[11px] font-bold text-[#2563eb]">
                {myClaimedDisplay.length}
              </span>
            </div>
            <span className="text-[11.5px] text-[#94a3b8]">Assigned to you</span>
          </div>
          {loading ? (
            <div className="px-5 py-4 text-sm text-text-secondary">Loading…</div>
          ) : myClaimedDisplay.length === 0 ? (
            <div className="px-5 py-4 text-sm text-text-secondary">No claimed reviews for your user.</div>
          ) : (
            <div>
              {myClaimedDisplay.map((row, i) => (
                <div
                  key={row.entity_key}
                  className="flex items-center gap-3 px-5 py-3 hover:bg-[#fafbfc]"
                  style={{
                    borderBottom: i < myClaimedDisplay.length - 1 ? '1px solid #f8fafc' : 'none',
                    animation: 'row-fade-up 0.2s ease both',
                    animationDelay: `${i * 0.05}s`,
                  }}
                >
                  <div className="min-w-0 flex-1">
                    <div className="text-[13.5px] font-semibold text-[#1e293b]">{row.entity_name}</div>
                    <div className="text-[11.5px] text-[#94a3b8] font-mono">
                      Ref: {row.business_reference ?? `BUS-${row.entity_key.slice(0, 10)}`}
                    </div>
                  </div>
                  <DecisionBadge decision={row.decision} />
                  <div className="flex items-center gap-1.5 ml-2 whitespace-nowrap">
                    <button
                      type="button"
                      title="View details"
                      onClick={() => setDetailItem(row)}
                      className="inline-flex items-center justify-center rounded-lg border border-[#e2e8f0] p-[6px_8px] text-[#64748b] hover:bg-[#f1f5f9]"
                    >
                      <BiShow className="h-[13px] w-[13px]" />
                    </button>
                    <button
                      type="button"
                      title="Re-run check"
                      onClick={() => openRerun(row)}
                      disabled={actionLoading || rerunLoading}
                      className="inline-flex items-center justify-center rounded-lg border border-[#e2e8f0] p-[6px_8px] text-[#64748b] hover:bg-[#f1f5f9] disabled:opacity-50"
                    >
                      <BiRefresh className="h-[13px] w-[13px]" />
                    </button>
                    <button
                      type="button"
                      disabled={actionLoading || rerunLoading}
                      onClick={() => {
                        setSelected(row)
                        setActionError(null)
                        setReviewNotes('')
                        setReviewOutcome(REVIEW_OUTCOME_OPTIONS[0])
                      }}
                      className="rounded-lg bg-[#1e3a5f] px-[14px] py-[7px] text-[12.5px] font-semibold text-white hover:bg-[#2d5986] disabled:opacity-50"
                    >
                      Complete review →
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card className="p-0 overflow-hidden">
          <div className="px-5 py-[13px] border-b border-[#f1f5f9] flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: sectionDotTone('unclaimed'), boxShadow: `0 0 0 3px rgba(245,158,11,0.2)` }}
              />
              <span className="font-bold text-[13.5px] text-[#0f2340]">Unclaimed queue</span>
              <span className="rounded-[20px] border border-[#fde68a] bg-[#fffbeb] px-2 py-0.5 text-[11px] font-bold text-[#d97706]">
                {unclaimedDisplay.length}
              </span>
            </div>
            <span className="text-[11.5px] text-[#94a3b8]">Available to claim</span>
          </div>
          {loading ? (
            <div className="px-5 py-4 text-sm text-text-secondary">Loading…</div>
          ) : unclaimedDisplay.length === 0 ? (
            <div className="px-5 py-4 text-sm text-text-secondary">No unclaimed queue items found.</div>
          ) : (
            <div>
              {unclaimedDisplay.map((row, i) => (
                <div
                  key={row.entity_key}
                  className="flex items-center gap-3 px-5 py-3 hover:bg-[#fafbfc]"
                  style={{
                    borderBottom: i < unclaimedDisplay.length - 1 ? '1px solid #f8fafc' : 'none',
                    animation: 'row-fade-up 0.2s ease both',
                    animationDelay: `${i * 0.04}s`,
                  }}
                >
                  <div className="min-w-0 flex-1">
                    <div className="text-[13.5px] font-semibold text-[#1e293b]">{row.entity_name}</div>
                    <div className="text-[11.5px] text-[#94a3b8] font-mono">
                      Ref: {row.business_reference ?? `BUS-${row.entity_key.slice(0, 10)}`}
                    </div>
                  </div>
                  <DecisionBadge decision={row.decision} />
                  <div className="flex items-center gap-1.5 ml-2 whitespace-nowrap">
                    <button
                      type="button"
                      title="View details"
                      onClick={() => setDetailItem(row)}
                      className="inline-flex items-center justify-center rounded-lg border border-[#e2e8f0] p-[6px_8px] text-[#64748b] hover:bg-[#f1f5f9]"
                    >
                      <BiShow className="h-[13px] w-[13px]" />
                    </button>
                    {row.review_status === 'UNREVIEWED' ? (
                      <button
                        type="button"
                        disabled={actionLoading}
                        onClick={() => void handleClaim(row)}
                        className="rounded-lg border border-[#e2e8f0] bg-[#f8fafc] px-[14px] py-[6px] text-[12.5px] font-semibold text-[#475569] hover:bg-[#eff6ff] hover:text-[#2563eb] hover:border-[#bfdbfe] disabled:opacity-50"
                      >
                        Claim
                      </button>
                    ) : (
                      <span className="text-xs text-text-muted">Unavailable</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Modal
        isOpen={selected !== null}
        onClose={() => setSelected(null)}
        title="Complete review"
        footer={
          <div className="flex items-center gap-2">
            <Button type="button" variant="secondary" onClick={() => setSelected(null)} disabled={actionLoading}>
              Cancel
            </Button>
            <Button type="button" onClick={() => void handleComplete()} disabled={actionLoading}>
              {actionLoading ? 'Saving…' : 'Complete review'}
            </Button>
          </div>
        }
      >
        {selected && (
          <div className="space-y-4">
            <p className="text-sm text-text-secondary">
              <span className="font-medium">Entity:</span> {selected.entity_name}
              {' · '}
              <span className="font-medium">Entity key:</span> <code>{selected.entity_key}</code>
            </p>
            <div>
              <label htmlFor="review-outcome" className="block text-xs font-medium text-text-primary mb-1">
                Review outcome
              </label>
              <select
                id="review-outcome"
                className="w-full h-10 rounded-lg border border-border bg-surface px-3 text-sm text-text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/15"
                value={reviewOutcome}
                onChange={(e) => setReviewOutcome(e.target.value as ReviewOutcome)}
              >
                {REVIEW_OUTCOME_OPTIONS.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="review-notes" className="block text-xs font-medium text-text-primary mb-1">
                Review notes (minimum 10 characters)
              </label>
              <textarea
                id="review-notes"
                className="w-full min-h-[120px] rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-primary placeholder:text-text-muted outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/15"
                value={reviewNotes}
                onChange={(e) => setReviewNotes(e.target.value)}
                placeholder="Record analyst rationale for this review outcome."
              />
            </div>
            {actionError && <ErrorBox message={actionError} />}
          </div>
        )}
      </Modal>

      <Modal
        isOpen={detailItem !== null}
        onClose={() => setDetailItem(null)}
        title="Review item details"
        footer={
          <Button type="button" variant="secondary" onClick={() => setDetailItem(null)}>
            Close
          </Button>
        }
      >
        {detailItem && (
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <div className="sm:col-span-2">
              <dt className="text-xs font-medium text-text-muted">Entity name</dt>
              <dd className="text-text-primary mt-0.5">{detailItem.entity_name}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-text-muted">Entity key</dt>
              <dd className="mt-0.5">
                <Button type="button" variant="ghost" size="sm" className="h-auto p-0" onClick={() => setIdModalKey(detailItem.entity_key)}>
                  Show ID
                </Button>
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-text-muted">Decision</dt>
              <dd className="text-text-primary mt-0.5">{detailItem.decision}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-text-muted">Business reference</dt>
              <dd className="text-text-primary mt-0.5">{detailItem.business_reference ?? '—'}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-text-muted">Reason for check</dt>
              <dd className="text-text-primary mt-0.5">{detailItem.reason_for_check ?? '—'}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-text-muted">Screening user</dt>
              <dd className="text-text-primary mt-0.5">{detailItem.screening_user ?? '—'}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-text-muted">Screened at</dt>
              <dd className="text-text-primary mt-0.5">{formatDate(detailItem.screening_timestamp)}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-text-muted">Review status</dt>
              <dd className="text-text-primary mt-0.5">{detailItem.review_status}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-text-muted">Claimed by</dt>
              <dd className="text-text-primary mt-0.5 break-words">{detailItem.review_claimed_by ?? '—'}</dd>
            </div>
          </dl>
        )}
      </Modal>

      <Modal
        isOpen={rerunTarget !== null}
        onClose={() => setRerunTarget(null)}
        title="Re-run check"
        footer={
          <div className="flex items-center gap-2">
            <Button type="button" variant="secondary" onClick={() => setRerunTarget(null)} disabled={rerunLoading}>
              Close
            </Button>
            <Button type="button" onClick={() => void handleRerun()} disabled={rerunLoading}>
              {rerunLoading ? 'Re-running…' : 'Run re-check'}
            </Button>
          </div>
        }
      >
        {rerunTarget && (
          <div className="space-y-4">
            <p className="text-sm text-text-secondary">
              <span className="font-medium">Entity:</span> {rerunTarget.entity_name}
              {' · '}
              <span className="font-medium">Current Type:</span> {rerunTarget.entity_type}
            </p>
            <div>
              <label htmlFor="rerun-entity-type" className="block text-xs font-medium text-text-primary mb-1">
                Entity type for re-run
              </label>
              <select
                id="rerun-entity-type"
                className="w-full h-10 rounded-lg border border-border bg-surface px-3 text-sm text-text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/15"
                value={rerunEntityType}
                onChange={(e) => setRerunEntityType(e.target.value as 'Person' | 'Organization')}
              >
                <option value="Person">Person</option>
                <option value="Organization">Organisation</option>
              </select>
            </div>
            {rerunEntityType === 'Person' ? (
              <Input
                label="Date of birth"
                value={rerunDob}
                onChange={(e) => setRerunDob(e.target.value)}
                placeholder="DD-MM-YYYY or YYYY-MM-DD"
              />
            ) : (
              <Input
                label="Country"
                value={rerunCountry}
                onChange={(e) => setRerunCountry(e.target.value)}
                placeholder="e.g. UK, United Kingdom, USA, United States"
              />
            )}
            {rerunMessage && <p className="text-sm text-text-secondary">{rerunMessage}</p>}
            {actionError && <ErrorBox message={actionError} />}
          </div>
        )}
      </Modal>

      <Modal
        isOpen={idModalKey !== null}
        onClose={() => setIdModalKey(null)}
        title="Entity ID"
        footer={
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={() => setIdModalKey(null)}>
              Close
            </Button>
            {idModalKey && (
              <Button onClick={() => void navigator.clipboard.writeText(idModalKey)}>
                Copy ID
              </Button>
            )}
          </div>
        }
      >
        {idModalKey && (
          <code className="block w-full text-xs bg-surface px-2 py-2 rounded break-all font-mono">{idModalKey}</code>
        )}
      </Modal>

      {toast && (
        <div
          className="fixed bottom-5 right-5 z-50 inline-flex items-center gap-2 rounded-[11px] bg-[#0f2340] px-[18px] py-[11px] text-white shadow-[0_8px_24px_rgba(0,0,0,0.2)]"
          style={{ animation: 'toast-slide-in 0.2s ease both' }}
        >
          <BiCheckCircle className="h-4 w-4 text-[#4ade80]" />
          <span className="text-sm">{toast}</span>
        </div>
      )}
    </div>
  )
}
