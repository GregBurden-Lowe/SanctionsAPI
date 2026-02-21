import { useEffect, useState } from 'react'
import { Button, Card, CardBody, CardHeader, CardTitle, ErrorBox, Input, Modal, SectionHeader } from '@/components'
import {
  claimReview,
  completeReview,
  getReviewQueue,
  type OpCheckParams,
  type ReviewQueueItem,
} from '@/api/client'
import type { ReviewOutcome, ReviewStatus } from '@/types/api'
import { useAuth } from '@/context/AuthContext'

const REASON_OPTIONS: OpCheckParams['reason_for_check'][] = [
  'Client Onboarding',
  'Claim Payment',
  'Business Partner Payment',
  'Business Partner Due Diligence',
  'Periodic Re-Screen',
  'Ad-Hoc Compliance Review',
]

const REVIEW_OUTCOME_OPTIONS: ReviewOutcome[] = [
  'False Positive - Proceeded',
  'False Positive - Payment Released',
  'Confirmed Match - Payment Blocked',
  'Confirmed Match - Escalated to Compliance',
  'Pending External Review',
  'Cancelled / No Action Required',
]

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

export function MatchReviewPage() {
  const { user } = useAuth()
  const [items, setItems] = useState<ReviewQueueItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reviewStatus, setReviewStatus] = useState<'' | ReviewStatus>('UNREVIEWED')
  const [businessReference, setBusinessReference] = useState('')
  const [reasonForCheck, setReasonForCheck] = useState<'' | OpCheckParams['reason_for_check']>('')
  const [includeCleared, setIncludeCleared] = useState(false)
  const [myMatchesOnly, setMyMatchesOnly] = useState(false)
  const [selected, setSelected] = useState<ReviewQueueItem | null>(null)
  const [reviewOutcome, setReviewOutcome] = useState<ReviewOutcome>(REVIEW_OUTCOME_OPTIONS[0])
  const [reviewNotes, setReviewNotes] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const currentUsername = (user?.username || '').trim().toLowerCase()
  const visibleItems = myMatchesOnly
    ? items.filter((item) => (item.review_claimed_by || '').trim().toLowerCase() === currentUsername)
    : items

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await getReviewQueue({
        review_status: reviewStatus || undefined,
        business_reference: businessReference.trim() || undefined,
        reason_for_check: reasonForCheck || undefined,
        include_cleared: includeCleared,
        limit: 200,
      })
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
      setSelected(null)
      setReviewNotes('')
      setReviewOutcome(REVIEW_OUTCOME_OPTIONS[0])
      await load()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to complete review.')
    } finally {
      setActionLoading(false)
    }
  }

  const actionsCell = (row: ReviewQueueItem) => {
    if (row.review_status === 'COMPLETED') {
      return <span className="text-xs text-text-muted">Completed</span>
    }
    if (row.review_status === 'IN_REVIEW') {
      return (
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={actionLoading}
          onClick={() => {
            setSelected(row)
            setActionError(null)
            setReviewNotes('')
            setReviewOutcome(REVIEW_OUTCOME_OPTIONS[0])
          }}
        >
          Complete review
        </Button>
      )
    }
    return (
      <Button
        type="button"
        variant="secondary"
        size="sm"
        disabled={actionLoading}
        onClick={() => void handleClaim(row)}
      >
        Claim
      </Button>
    )
  }

  return (
    <div className="px-10 pb-10">
      <div className="max-w-7xl space-y-6">
        <SectionHeader title="Match review queue" meta="Potential matches requiring analyst review" />

        <Card>
          <CardHeader>
            <CardTitle>Queue filters</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              <div className="space-y-2">
                <label htmlFor="review-status" className="block text-xs font-medium text-text-primary mb-1">
                  Review status
                </label>
                <select
                  id="review-status"
                  className="w-full h-10 rounded-lg border border-border bg-surface px-3 text-sm text-text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/15"
                  value={reviewStatus}
                  onChange={(e) => setReviewStatus((e.target.value as '' | ReviewStatus) || '')}
                >
                  <option value="">All</option>
                  <option value="UNREVIEWED">Unreviewed</option>
                  <option value="IN_REVIEW">In review</option>
                  <option value="COMPLETED">Completed</option>
                </select>
              </div>
              <Input
                label="Business reference"
                value={businessReference}
                onChange={(e) => setBusinessReference(e.target.value)}
                placeholder="Exact business reference"
              />
              <div className="space-y-2">
                <label htmlFor="reason-for-check" className="block text-xs font-medium text-text-primary mb-1">
                  Reason for check
                </label>
                <select
                  id="reason-for-check"
                  className="w-full h-10 rounded-lg border border-border bg-surface px-3 text-sm text-text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/15"
                  value={reasonForCheck}
                  onChange={(e) => setReasonForCheck((e.target.value as '' | OpCheckParams['reason_for_check']) || '')}
                >
                  <option value="">All</option>
                  {REASON_OPTIONS.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex items-end gap-3">
                <Button type="button" variant="secondary" onClick={() => void load()} disabled={loading}>
                  {loading ? 'Refreshing…' : 'Refresh'}
                </Button>
                <label className="inline-flex items-center gap-2 text-xs text-text-secondary">
                  <input
                    type="checkbox"
                    checked={includeCleared}
                    onChange={(e) => setIncludeCleared(e.target.checked)}
                  />
                  Include cleared
                </label>
                <label className="inline-flex items-center gap-2 text-xs text-text-secondary">
                  <input
                    type="checkbox"
                    checked={myMatchesOnly}
                    onChange={(e) => setMyMatchesOnly(e.target.checked)}
                  />
                  My matches
                </label>
              </div>
            </div>
            {error && <ErrorBox message={error} />}
            {actionError && <ErrorBox message={actionError} />}
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Review queue ({visibleItems.length})</CardTitle>
          </CardHeader>
          <CardBody>
            {loading ? (
              <p className="text-sm text-text-secondary">Loading…</p>
            ) : visibleItems.length === 0 ? (
              <p className="text-sm text-text-secondary">No queue items found.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead>
                    <tr className="border-b border-border/80">
                      <th className="py-2 pr-4 font-medium text-text-primary">Entity name</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Entity key</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Decision</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Business reference</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Reason for check</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Screening user</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Screened at</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Review status</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Claimed by</th>
                      <th className="py-2 font-medium text-text-primary">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleItems.map((row) => (
                      <tr key={row.entity_key} className="border-b border-border/70 hover:bg-muted/40">
                        <td className="py-2 pr-4 text-text-secondary">{row.entity_name}</td>
                        <td className="py-2 pr-4 text-text-secondary">
                          <code className="text-xs bg-surface px-1 rounded">{row.entity_key}</code>
                        </td>
                        <td className="py-2 pr-4 text-text-secondary">{row.decision}</td>
                        <td className="py-2 pr-4 text-text-secondary">{row.business_reference ?? '—'}</td>
                        <td className="py-2 pr-4 text-text-secondary">{row.reason_for_check ?? '—'}</td>
                        <td className="py-2 pr-4 text-text-secondary">{row.screening_user ?? '—'}</td>
                        <td className="py-2 pr-4 text-text-secondary">{formatDate(row.screening_timestamp)}</td>
                        <td className="py-2 pr-4 text-text-secondary">{row.review_status}</td>
                        <td className="py-2 pr-4 text-text-secondary">{row.review_claimed_by ?? '—'}</td>
                        <td className="py-2">{actionsCell(row)}</td>
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
    </div>
  )
}
