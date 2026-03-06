import { useEffect, useState } from 'react'
import { Button, Card, CardBody, CardHeader, CardTitle, ErrorBox, Modal, SectionHeader } from '@/components'
import { listScreeningJobs, searchScreened } from '@/api/client'
import type { ScreenedEntity, ScreeningJob } from '@/types/api'
import { ResultCard } from '@/pages/ScreeningPage'
import { generateBatchScreeningPdf } from '@/utils/exportBatchScreeningPdf'

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
  const [detailRow, setDetailRow] = useState<ScreenedEntity | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [batchExporting, setBatchExporting] = useState(false)
  const [idModalKey, setIdModalKey] = useState<string | null>(null)

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

  const openResult = async (row: ScreeningJob) => {
    setDetailError(null)
    setDetailLoading(true)
    try {
      const res = await searchScreened({ entity_key: row.entity_key, limit: 1 })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setDetailError((data as { detail?: string }).detail ?? 'Failed to load result.')
        return
      }
      const first = ((data as { items?: ScreenedEntity[] }).items ?? [])[0]
      if (!first) {
        setDetailError('No stored screening result found for this job.')
        return
      }
      setDetailRow(first)
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : 'Failed to load result.')
    } finally {
      setDetailLoading(false)
    }
  }

  const exportBatchPdf = async (businessReference: string | null | undefined) => {
    const ref = (businessReference || '').trim()
    if (!ref) {
      setDetailError('This job has no business reference to group by.')
      return
    }
    setBatchExporting(true)
    setDetailError(null)
    try {
      const collected: ScreenedEntity[] = []
      let offset = 0
      const pageSize = 100
      while (true) {
        const res = await searchScreened({ business_reference: ref, limit: pageSize, offset })
        const data = await res.json().catch(() => ({}))
        if (!res.ok) {
          setDetailError((data as { detail?: string }).detail ?? 'Failed to load batch results.')
          return
        }
        const chunk = ((data as { items?: ScreenedEntity[] }).items ?? []) as ScreenedEntity[]
        collected.push(...chunk)
        if (chunk.length < pageSize) break
        offset += pageSize
        if (offset >= 5000) break
      }
      if (collected.length === 0) {
        setDetailError('No stored screening results found for this business reference.')
        return
      }
      generateBatchScreeningPdf(collected, { businessReference: ref })
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : 'Failed to export batch PDF.')
    } finally {
      setBatchExporting(false)
    }
  }

  return (
    <div className="px-6 pb-6">
      <div className="w-full max-w-[1600px] space-y-6">
        <SectionHeader title="Screening jobs" />
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
                    <th className="py-2 pr-4 font-medium text-text-primary">Outcome</th>
                    <th className="py-2 font-medium text-text-primary">Error</th>
                    <th className="py-2 font-medium text-text-primary">Actions</th>
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
                      <td className="py-2 pr-4 text-text-secondary">
                        {row.screening_status ?? (row.status === 'completed' ? 'Completed' : '—')}
                      </td>
                      <td className="py-2 text-text-muted">{row.error_message || '—'}</td>
                      <td className="py-2">
                        <div className="flex items-center gap-2">
                          <Button type="button" variant="ghost" size="sm" onClick={() => setIdModalKey(row.entity_key)}>
                            Show ID
                          </Button>
                          {row.status === 'completed' && (
                            <>
                              <Button type="button" variant="ghost" size="sm" onClick={() => void openResult(row)}>
                                View result
                              </Button>
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                onClick={() => void exportBatchPdf(row.business_reference)}
                                disabled={batchExporting}
                              >
                                {batchExporting ? 'Preparing…' : 'Batch PDF'}
                              </Button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                  {!loading && items.length === 0 && (
                    <tr>
                      <td className="py-3 text-text-secondary" colSpan={10}>No jobs found for this filter.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </CardBody>
        </Card>
        {detailError && <ErrorBox message={detailError} />}
        {detailLoading && <p className="text-sm text-text-secondary">Loading screening result…</p>}
      </div>

      <Modal
        isOpen={detailRow !== null}
        onClose={() => setDetailRow(null)}
        title="Screening result"
        size="wide"
        footer={
          detailRow ? (
            <Button variant="secondary" onClick={() => setDetailRow(null)}>
              Close
            </Button>
          ) : null
        }
      >
        {detailRow && (
          <ResultCard
            result={{ ...detailRow.result_json, entity_key: detailRow.entity_key }}
              searchDetails={{
                searchName: detailRow.display_name,
                entityType: detailRow.entity_type,
                searchDob: detailRow.date_of_birth ?? '',
                searchCountry: detailRow.country_input ?? '',
                businessReference: detailRow.business_reference ?? '',
                reasonForCheck: detailRow.reason_for_check ?? '',
                requestor: detailRow.last_requestor ?? '',
                searchBackend: 'postgres_beta',
              }}
          />
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
              <Button
                onClick={() => void navigator.clipboard.writeText(idModalKey)}
              >
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
    </div>
  )
}
