import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import {
  Button,
  Input,
  Card,
  CardHeader,
  CardTitle,
  CardBody,
  SectionHeader,
  ErrorBox,
  Modal,
  Skeleton,
} from '@/components'
import { opcheck } from '@/api/client'
import type { OpCheckResponse, ApiErrorResponse } from '@/types/api'
import type { TopMatch } from '@/types/api'
import { generateScreeningPdf } from '@/utils/exportScreeningPdf'
import type { SearchDetails } from '@/utils/exportScreeningPdf'

function getStatusDisplay(summary: OpCheckResponse['Check Summary']) {
  const status = summary?.Status ?? 'Unknown'
  const risk = (status + (summary?.Source ?? '')).toLowerCase()
  if (risk.includes('cleared')) return { label: status, semantic: 'success' as const }
  if (risk.includes('fail sanction') || risk.includes('high risk'))
    return { label: status, semantic: 'error' as const }
  if (risk.includes('fail pep') || risk.includes('medium'))
    return { label: status, semantic: 'warning' as const }
  return { label: status, semantic: 'info' as const }
}

function formatTopMatch(m: TopMatch): { name: string; score: number } {
  if (Array.isArray(m) && m.length >= 2) return { name: m[0], score: m[1] }
  if (m && typeof m === 'object' && 'name' in m) return { name: (m as { name: string }).name, score: (m as { score: number }).score ?? 0 }
  return { name: String(m), score: 0 }
}

export function ScreeningPage() {
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [dob, setDob] = useState('')
  const [entityType, setEntityType] = useState<'Person' | 'Organization'>('Person')
  const [searchBackend, setSearchBackend] = useState<'original' | 'postgres_beta'>('original')
  const [requestor, setRequestor] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    const nameTrim = name.trim()
    const dobTrim = dob.trim()
    const requestorTrim = requestor.trim()
    if (!nameTrim) {
      setError("Please provide 'name' to run a check.")
      return
    }
    if (!requestorTrim) {
      setError("Please provide 'requestor' (your name) to run a check.")
      return
    }
    setLoading(true)
    try {
      const res = await opcheck({
        name: nameTrim,
        dob: dobTrim || null,
        entity_type: entityType,
        requestor: requestorTrim,
        search_backend: searchBackend,
      })
      const data = await res.json()
      if (!res.ok) {
        const err = data as ApiErrorResponse
        setError(err.message ?? err.error ?? 'Check failed.')
        return
      }
      const payload: ScreeningResultState = {
        result: data as OpCheckResponse,
        searchDetails: {
          searchName: nameTrim,
          entityType,
          searchDob: dobTrim,
          requestor: requestorTrim,
          searchBackend,
        },
      }
      sessionStorage.setItem('screening_last_result', JSON.stringify(payload))
      navigate('/results', { state: payload })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Network error.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="px-10 pb-10">
      <div className="max-w-2xl space-y-6">
        <SectionHeader title="Run check" />
        <Card>
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              label="Name or organization"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. John Smith or Acme Ltd"
              required
            />
            <div>
              <label htmlFor="entity_type" className="block text-xs font-medium text-text-primary mb-1">
                Entity type
              </label>
              <select
                id="entity_type"
                value={entityType}
                onChange={(e) => setEntityType(e.target.value as 'Person' | 'Organization')}
                className="w-full h-10 rounded-lg border border-border bg-surface px-3 text-sm text-text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/15"
              >
                <option value="Person">Person</option>
                <option value="Organization">Organization</option>
              </select>
            </div>
            <Input
              label="Date of birth (optional)"
              value={dob}
              onChange={(e) => setDob(e.target.value)}
              placeholder="YYYY-MM-DD"
            />
            <Input
              label="Your name (requestor)"
              value={requestor}
              onChange={(e) => setRequestor(e.target.value)}
              placeholder="Who is running this check"
              required
            />
            <div>
              <label htmlFor="search_backend" className="block text-xs font-medium text-text-primary mb-1">
                Search backend
              </label>
              <select
                id="search_backend"
                value={searchBackend}
                onChange={(e) => setSearchBackend(e.target.value as 'original' | 'postgres_beta')}
                className="w-full h-10 rounded-lg border border-border bg-surface px-3 text-sm text-text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/15"
              >
                <option value="original">Original (Parquet)</option>
                <option value="postgres_beta">Postgres (Beta)</option>
              </select>
              <p className="text-xs text-text-muted mt-1">
                Beta runs against watchlist tables in PostgreSQL and bypasses cache/queue reuse.
              </p>
            </div>
            <Button type="submit" className="w-full mt-6" disabled={loading}>
              {loading ? 'Checking…' : 'Check'}
            </Button>
          </form>
          {error && (
            <div className="mt-4">
              <ErrorBox message={error} />
            </div>
          )}
        </Card>

        {loading && (
          <Card>
            <CardHeader>
              <CardTitle>Preparing result</CardTitle>
            </CardHeader>
            <CardBody>
              <Skeleton className="h-6 w-48 mb-2" />
              <Skeleton className="h-4 w-full mb-2" />
              <Skeleton className="h-4 w-full" />
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  )
}

type ScreeningResultState = {
  result: OpCheckResponse
  searchDetails: SearchDetails
}

function SearchDetailsCard({ searchDetails }: { searchDetails: SearchDetails }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Original search details</CardTitle>
      </CardHeader>
      <CardBody>
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
          <div className="sm:col-span-2">
            <dt className="text-xs font-medium text-text-muted">Name or organization</dt>
            <dd className="text-text-primary mt-0.5">{searchDetails.searchName || '—'}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-text-muted">Entity type</dt>
            <dd className="text-text-primary mt-0.5">{searchDetails.entityType || '—'}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-text-muted">Date of birth</dt>
            <dd className="text-text-primary mt-0.5">{searchDetails.searchDob?.trim() ? searchDetails.searchDob : 'Not provided'}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-xs font-medium text-text-muted">Requestor</dt>
            <dd className="text-text-primary mt-0.5">{searchDetails.requestor || '—'}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-xs font-medium text-text-muted">Search backend</dt>
            <dd className="text-text-primary mt-0.5">
              {searchDetails.searchBackend === 'postgres_beta' ? 'Postgres (Beta)' : 'Original (Parquet)'}
            </dd>
          </div>
        </dl>
      </CardBody>
    </Card>
  )
}

export function ScreeningResultPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const statePayload = location.state as ScreeningResultState | undefined
  let payload = statePayload
  if (!payload) {
    const raw = sessionStorage.getItem('screening_last_result')
    if (raw) {
      try {
        payload = JSON.parse(raw) as ScreeningResultState
      } catch {
        payload = undefined
      }
    }
  }
  if (!payload?.result || !payload?.searchDetails) return <Navigate to="/" replace />

  return (
    <div className="px-10 pb-10">
      <div className="max-w-6xl space-y-6">
        <SectionHeader title="Screening result" />
        <div className="flex justify-end">
          <Button type="button" variant="ghost" onClick={() => navigate('/')}>
            Run another check
          </Button>
        </div>
        <SearchDetailsCard searchDetails={payload.searchDetails} />
        <ResultCard result={payload.result} searchDetails={payload.searchDetails} />
      </div>
    </div>
  )
}

/** UK sanctions list indicators (display-only; matches existing backend source strings). */
const UK_SOURCE_PATTERNS = [
  'uk',
  'hmt',
  'ofsi',
  'hm treasury',
  'uk fcdo',
  'uk financial sanctions',
]

function isUKSource(item: string): boolean {
  const lower = item.toLowerCase()
  return UK_SOURCE_PATTERNS.some((p) => lower.includes(p))
}

/** Parse backend Source string for display: split by delimiters, detect UK, return list and summary. */
function parseSourceList(source: string | undefined): {
  list: string[]
  hasUK: boolean
  otherCount: number
  summaryLines: string[]
} {
  const raw = (source ?? '').trim()
  if (!raw) return { list: [], hasUK: false, otherCount: 0, summaryLines: ['—'] }
  const list = raw
    .split(/[;,\n]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  const items = list.length > 0 ? list : [raw]
  const hasUK = items.some(isUKSource)
  const otherCount = items.filter((i) => !isUKSource(i)).length
  const summaryLines: string[] = []
  summaryLines.push(hasUK ? 'UK sanctions: Yes' : 'UK sanctions: No')
  if (otherCount > 0) {
    summaryLines.push(otherCount === 1 ? 'Other sanctions lists: 1' : `Other sanctions lists: ${otherCount}`)
  }
  return { list: items, hasUK, otherCount, summaryLines }
}

function getResultGuidance(result: OpCheckResponse): { heading: string; body: string; className: string } {
  if (result['Is Sanctioned']) {
    return {
      heading: 'What this means',
      body: 'This result indicates a sanctions match. Further verification is required before proceeding.',
      className: 'text-sm text-text-primary font-medium leading-relaxed',
    }
  }
  if (result['Is PEP']) {
    return {
      heading: 'What this means',
      body: 'This individual is identified as a Politically Exposed Person. This does not prevent proceeding, but should be recorded for audit purposes.',
      className: 'text-sm text-text-secondary leading-relaxed',
    }
  }
  return {
    heading: 'What this means',
    body: 'No match was found against sanctions or PEP lists. No further action required.',
    className: 'text-sm text-text-secondary leading-relaxed',
  }
}

export function ResultCard({
  result,
  searchDetails,
}: {
  result: OpCheckResponse
  searchDetails: SearchDetails
}) {
  const summary = result['Check Summary']
  const { label: statusLabel, semantic } = getStatusDisplay(summary)
  const borderAccent =
    semantic === 'success'
      ? 'border-l-semantic-success'
      : semantic === 'error'
        ? 'border-l-semantic-error'
        : semantic === 'warning'
          ? 'border-l-semantic-warning'
          : 'border-l-semantic-info'

  const hasTopMatches = (result['Top Matches']?.length ?? 0) > 0
  const guidance = getResultGuidance(result)
  const { list: sourceList, summaryLines: sourceSummaryLines } = parseSourceList(summary?.Source)
  const [showSourcesModal, setShowSourcesModal] = useState(false)
  const [pdfError, setPdfError] = useState<string | null>(null)
  const topMatches = (result['Top Matches'] ?? []).map(formatTopMatch)

  const statusClasses =
    semantic === 'success'
      ? 'bg-semantic-success/15 text-semantic-success border-semantic-success/30'
      : semantic === 'error'
        ? 'bg-semantic-error/15 text-semantic-error border-semantic-error/30'
        : semantic === 'warning'
          ? 'bg-semantic-warning/15 text-semantic-warning border-semantic-warning/30'
          : 'bg-semantic-info/15 text-semantic-info border-semantic-info/30'

  const verificationRows = [
    {
      title: 'Sanctions status',
      subtitle: result['Is Sanctioned'] ? 'Potential sanctions match found' : 'No sanctions match detected',
      badge: result['Is Sanctioned'] ? 'Review required' : 'Cleared',
      tone: result['Is Sanctioned'] ? 'warn' : 'ok',
    },
    {
      title: 'PEP status',
      subtitle: result['Is PEP'] ? 'Politically Exposed Person indicator found' : 'No PEP indicator found',
      badge: result['Is PEP'] ? 'Monitor' : 'Clear',
      tone: result['Is PEP'] ? 'warn' : 'ok',
    },
    {
      title: 'Confidence',
      subtitle: `Engine confidence: ${result.Confidence}`,
      badge: String(result.Score),
      tone: 'neutral',
    },
    {
      title: 'Source coverage',
      subtitle: sourceSummaryLines.join(' · '),
      badge: sourceList.length > 0 ? `${sourceList.length} source${sourceList.length > 1 ? 's' : ''}` : 'No sources',
      tone: 'neutral',
    },
  ] as const

  const handleDownloadPdf = async () => {
    setPdfError(null)
    try {
      await generateScreeningPdf(result, searchDetails)
    } catch (err) {
      setPdfError(err instanceof Error ? err.message : 'Failed to generate PDF')
    }
  }

  return (
    <div className="space-y-6">
      <Card className="bg-surface/90">
        <CardBody className="space-y-5">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className={`pl-4 border-l-2 ${borderAccent}`}>
              <p className="text-xs font-medium text-text-muted uppercase tracking-wide">
                Screening result
              </p>
              <p className="text-3xl font-semibold leading-tight text-text-primary mt-1">
                {statusLabel}
              </p>
              <p className="text-sm text-text-secondary mt-2">{guidance.body}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <span className={`inline-flex items-center rounded-lg border px-3 py-1 text-xs font-semibold ${statusClasses}`}>
                {result['Risk Level']}
              </span>
              <span className="inline-flex items-center rounded-lg border border-border bg-app px-3 py-1 text-xs font-medium text-text-secondary">
                Confidence {result.Confidence}
              </span>
              <span className="inline-flex items-center rounded-lg border border-border bg-app px-3 py-1 text-xs font-medium text-text-secondary">
                Score {result.Score}
              </span>
            </div>
          </div>
        </CardBody>
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
        <div className="xl:col-span-5 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Decision summary</CardTitle>
            </CardHeader>
            <CardBody className="space-y-4 text-sm">
              <dl className="space-y-3">
                <div className="flex justify-between gap-4 border-b border-border pb-2">
                  <dt className="text-text-muted">Risk level</dt>
                  <dd className="text-text-primary font-medium">{result['Risk Level']}</dd>
                </div>
                <div className="flex justify-between gap-4 border-b border-border pb-2">
                  <dt className="text-text-muted">Confidence</dt>
                  <dd className="text-text-primary">{result.Confidence}</dd>
                </div>
                <div className="flex justify-between gap-4 border-b border-border pb-2">
                  <dt className="text-text-muted">Score</dt>
                  <dd className="text-text-primary">{result.Score}</dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-text-muted">Source summary</dt>
                  <dd className="text-right text-text-primary">{sourceSummaryLines.join(' · ')}</dd>
                </div>
              </dl>
              {summary?.Date && (
                <p className="text-xs text-text-muted pt-2 border-t border-border">
                  <span className="font-medium">Checked at</span> {summary.Date}
                </p>
              )}
              {result.entity_key && (
                <p className="text-xs text-text-muted pt-2 border-t border-border flex items-center gap-2 flex-wrap">
                  <span className="font-medium">Reference</span>
                  <code className="text-text-primary font-mono text-xs bg-app px-1.5 py-0.5 rounded break-all">
                    {result.entity_key}
                  </code>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="text-xs"
                    onClick={() => navigator.clipboard.writeText(result.entity_key!).then(() => {})}
                  >
                    Copy
                  </Button>
                </p>
              )}
            </CardBody>
          </Card>

          {result['Match Found'] && (
            <Card>
              <CardHeader>
                <CardTitle>Matched subject</CardTitle>
              </CardHeader>
              <CardBody>
                <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
                  {result['Sanctions Name'] && (
                    <div className="sm:col-span-2">
                      <dt className="text-xs font-medium text-text-muted">Name</dt>
                      <dd className="text-text-primary mt-0.5">{result['Sanctions Name']}</dd>
                    </div>
                  )}
                  {result.Regime && (
                    <div>
                      <dt className="text-xs font-medium text-text-muted">Regime</dt>
                      <dd className="text-text-secondary mt-0.5">{result.Regime}</dd>
                    </div>
                  )}
                  {result['Birth Date'] && (
                    <div>
                      <dt className="text-xs font-medium text-text-muted">Date of birth</dt>
                      <dd className="text-text-secondary mt-0.5">{result['Birth Date']}</dd>
                    </div>
                  )}
                </dl>
              </CardBody>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Actions</CardTitle>
            </CardHeader>
            <CardBody className="space-y-3">
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="secondary" onClick={handleDownloadPdf}>
                  Download PDF
                </Button>
                {sourceList.length > 0 && (
                  <Button type="button" variant="ghost" onClick={() => setShowSourcesModal(true)}>
                    View sources
                  </Button>
                )}
              </div>
              {pdfError && (
                <p className="text-xs text-semantic-error" role="alert">
                  {pdfError}
                </p>
              )}
            </CardBody>
          </Card>
        </div>

        <div className="xl:col-span-7 space-y-6">
          <Card>
            <CardHeader className="items-center">
              <CardTitle>Verification board</CardTitle>
              <span className="text-xs rounded-md border border-border bg-app px-2 py-1 text-text-secondary">
                {verificationRows.length} checks
              </span>
            </CardHeader>
            <CardBody className="space-y-3">
              {verificationRows.map((row) => (
                <div key={row.title} className="flex items-center justify-between gap-4 rounded-lg border border-border bg-app/70 px-3 py-3">
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-text-primary truncate">{row.title}</p>
                    <p className="text-xs text-text-secondary mt-0.5">{row.subtitle}</p>
                  </div>
                  <span
                    className={`shrink-0 rounded-md px-2 py-1 text-xs font-semibold ${
                      row.tone === 'ok'
                        ? 'bg-semantic-success/15 text-semantic-success'
                        : row.tone === 'warn'
                          ? 'bg-semantic-warning/15 text-semantic-warning'
                          : 'bg-surface text-text-secondary border border-border'
                    }`}
                  >
                    {row.badge}
                  </span>
                </div>
              ))}
            </CardBody>
          </Card>

          {hasTopMatches && (
            <Card>
              <CardHeader>
                <CardTitle>Name similarity suggestions</CardTitle>
              </CardHeader>
              <CardBody className="space-y-3">
                <p className="text-xs text-text-secondary leading-relaxed">
                  Similar names shown for investigator context. They do not change the decision outcome.
                </p>
                <div className="space-y-2">
                  {topMatches.map((item, i) => (
                    <div key={`${item.name}-${i}`} className="flex items-center justify-between gap-4 rounded-lg border border-border bg-app/70 px-3 py-2">
                      <span className="text-sm text-text-primary">{item.name}</span>
                      <span className="text-xs text-text-secondary rounded-md bg-surface border border-border px-2 py-1">
                        Score {item.score}
                      </span>
                    </div>
                  ))}
                </div>
              </CardBody>
            </Card>
          )}
        </div>
      </div>

      <Modal
        isOpen={showSourcesModal}
        onClose={() => setShowSourcesModal(false)}
        title="Sources"
        footer={
          <Button type="button" variant="secondary" onClick={() => setShowSourcesModal(false)}>
            Close
          </Button>
        }
      >
        <p className="text-text-muted text-xs mb-3">Sanction lists matched for this result. Read-only reference.</p>
        <ul className="max-h-[60vh] overflow-y-auto space-y-2 text-text-secondary">
          {sourceList.map((item, i) => (
            <li key={i} className="py-1 border-b border-border last:border-0">
              {item}
            </li>
          ))}
        </ul>
      </Modal>
    </div>
  )
}
