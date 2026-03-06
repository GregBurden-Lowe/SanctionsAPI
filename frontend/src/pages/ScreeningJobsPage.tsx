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
  if (status === 'completed') return 'text-[#22c55e]'
  if (status === 'failed') return 'text-[#d94040]'
  if (status === 'running' || status === 'pending') return 'text-[#f59e0b]'
  return 'text-[#64748b]'
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
    <div className="px-[26px] pt-[22px] pb-[26px]">
      <div className="w-full max-w-[1600px] space-y-6">
        <SectionHeader title="Screening jobs" />
        <Card>
          <CardHeader>
            <CardTitle>Job monitor</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              {[
                { key: 'all', label: 'All' },
                { key: 'completed', label: 'Completed' },
                { key: 'pending', label: 'Pending' },
                { key: 'failed', label: 'Failed' },
              ].map((chip) => {
                const active = status === chip.key
                return (
                  <button
                    key={chip.key}
                    type="button"
                    onClick={() => setStatus(chip.key as typeof status)}
                    className={`rounded-[20px] border px-3 py-1 text-xs font-semibold ${
                      active
                        ? 'bg-[#eff6ff] text-[#2563eb] border-[#3b82f6]'
                        : 'bg-transparent text-[#64748b] border-[#e2e8f0] hover:bg-[#f8fafc]'
                    }`}
                  >
                    {chip.label}
                  </button>
                )
              })}
            </div>
            {error && <ErrorBox message={error} />}
            <div className="overflow-x-auto rounded-[13px] border border-[#e2e8f0]">
              <table className="w-full text-left bg-white">
                <thead>
                  <tr className="border-b border-[#e2e8f0]">
                    <th className="px-4 py-[10px] pr-4 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-[#94a3b8]">Created</th>
                    <th className="px-4 py-[10px] pr-4 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-[#94a3b8]">Status</th>
                    <th className="px-4 py-[10px] pr-4 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-[#94a3b8]">Name</th>
                    <th className="px-4 py-[10px] pr-4 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-[#94a3b8]">Type</th>
                    <th className="px-4 py-[10px] pr-4 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-[#94a3b8]">Requestor</th>
                    <th className="px-4 py-[10px] pr-4 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-[#94a3b8]">Started</th>
                    <th className="px-4 py-[10px] pr-4 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-[#94a3b8]">Finished</th>
                    <th className="px-4 py-[10px] pr-4 text-[11.5px] font-semibold uppercase tracking-[0.04em] text-[#94a3b8]">Outcome</th>
                    <th className="px-4 py-[10px] text-[11.5px] font-semibold uppercase tracking-[0.04em] text-[#94a3b8]">Error</th>
                    <th className="px-4 py-[10px] text-[11.5px] font-semibold uppercase tracking-[0.04em] text-[#94a3b8]">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((row) => (
                    <tr key={row.job_id} className="border-b border-[#f8fafc] hover:bg-[#fafbfc]">
                      <td className="px-4 py-[11px] pr-4 text-[13px] text-[#64748b]">{formatDate(row.created_at)}</td>
                      <td className={`px-4 py-[11px] pr-4 font-mono text-[13px] font-semibold ${statusTone(row.status)}`}>{row.status}</td>
                      <td className="px-4 py-[11px] pr-4 text-[13px] text-[#1e293b]">{row.name}</td>
                      <td className="px-4 py-[11px] pr-4 text-[13px] text-[#1e293b]">{row.entity_type}</td>
                      <td className="px-4 py-[11px] pr-4 text-[13px] text-[#64748b]">{row.requestor || '—'}</td>
                      <td className="px-4 py-[11px] pr-4 text-[13px] text-[#64748b]">{formatDate(row.started_at)}</td>
                      <td className="px-4 py-[11px] pr-4 text-[13px] text-[#64748b]">{formatDate(row.finished_at)}</td>
                      <td className="px-4 py-[11px] pr-4 text-[13px] text-[#1e293b]">
                        {row.screening_status ?? (row.status === 'completed' ? 'Completed' : '—')}
                      </td>
                      <td className="px-4 py-[11px] text-[13px] text-[#64748b]">{row.error_message || '—'}</td>
                      <td className="px-4 py-[11px]">
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
                      <td className="px-4 py-3 text-[13px] text-[#64748b]" colSpan={10}>No jobs found for this filter.</td>
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
