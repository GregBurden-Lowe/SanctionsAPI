import { useState } from 'react'
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
  const [name, setName] = useState('')
  const [dob, setDob] = useState('')
  const [entityType, setEntityType] = useState<'Person' | 'Organization'>('Person')
  const [requestor, setRequestor] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<OpCheckResponse | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setResult(null)
    const nameTrim = name.trim()
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
        dob: dob.trim() || null,
        entity_type: entityType,
        requestor: requestorTrim,
      })
      const data = await res.json()
      if (!res.ok) {
        const err = data as ApiErrorResponse
        setError(err.message ?? err.error ?? 'Check failed.')
        return
      }
      setResult(data as OpCheckResponse)
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
              <CardTitle>Result</CardTitle>
            </CardHeader>
            <CardBody>
              <Skeleton className="h-6 w-48 mb-2" />
              <Skeleton className="h-4 w-full mb-2" />
              <Skeleton className="h-4 w-full" />
            </CardBody>
          </Card>
        )}

        {result && !loading && (
          <ResultCard
            result={result}
            searchDetails={{
              searchName: name,
              entityType,
              searchDob: dob,
              requestor,
            }}
          />
        )}
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

function ResultCard({
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

  const handleDownloadPdf = () => {
    setPdfError(null)
    try {
      generateScreeningPdf(result, searchDetails)
    } catch (err) {
      setPdfError(err instanceof Error ? err.message : 'Failed to generate PDF')
    }
  }

  return (
    <Card>
      <CardBody className="space-y-6">
        {/* Decision outcome — typography and spacing for authority; subtle colour accent */}
        <div className={`pl-4 border-l-2 ${borderAccent}`}>
          <p className="text-xs font-medium text-text-muted uppercase tracking-wide">
            Screening result
          </p>
          <p className="text-2xl font-semibold leading-snug text-text-primary mt-1">
            {statusLabel}
          </p>
        </div>

        {/* Guidance — decision context by result type */}
        <div className="space-y-2">
          <h3 className="text-xs font-medium text-text-muted uppercase tracking-wide">
            {guidance.heading}
          </h3>
          <p className={guidance.className}>{guidance.body}</p>
        </div>

        {/* Decision summary: risk, confidence, score, source */}
        <div className="space-y-3">
          <h3 className="text-xs font-medium text-text-muted uppercase tracking-wide">
            Decision summary
          </h3>
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
            <div>
              <dt className="text-xs font-medium text-text-muted">Risk level</dt>
              <dd className="text-text-primary font-medium mt-0.5">{result['Risk Level']}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-text-muted">Confidence</dt>
              <dd className="text-text-primary mt-0.5">{result.Confidence}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-text-muted">Score</dt>
              <dd className="text-text-primary mt-0.5">{result.Score}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-text-muted">Source</dt>
              <dd className="text-text-primary mt-0.5 flex flex-col gap-0.5">
                {sourceSummaryLines.map((line, i) => (
                  <span key={i} className="text-sm text-text-primary">
                    {line}
                  </span>
                ))}
                {sourceList.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setShowSourcesModal(true)}
                    className="text-xs font-medium text-text-primary underline hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-app rounded w-fit mt-0.5"
                  >
                    View sources
                  </button>
                )}
              </dd>
            </div>
          </dl>
          {summary?.Date && (
            <p className="text-xs text-text-muted mt-3 pt-2 border-t border-border">
              <span className="font-medium">Checked at</span> {summary.Date}
            </p>
          )}
          <div className="mt-4 pt-2 border-t border-border">
            <Button type="button" variant="secondary" onClick={handleDownloadPdf}>
              Download PDF
            </Button>
            {pdfError && (
              <p className="text-xs text-semantic-error mt-2" role="alert">
                {pdfError}
              </p>
            )}
          </div>
        </div>

        {/* Matched subject — only when a match was found */}
        {result['Match Found'] && (
          <div className="space-y-3 pt-2 border-t border-border">
            <h3 className="text-xs font-medium text-text-muted uppercase tracking-wide">
              Matched subject
            </h3>
            <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
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
          </div>
        )}

        {/* Name similarity suggestions — advisory only */}
        {hasTopMatches && (
          <div className="pt-4 border-t border-border space-y-2">
            <h3 className="text-xs font-medium text-text-muted uppercase tracking-wide">
              Name similarity suggestions
            </h3>
            <p className="text-xs text-text-secondary leading-relaxed">
              These names are similar to the search term for reference only. They do not affect the screening decision above.
            </p>
            <ul className="text-sm text-text-secondary space-y-1 mt-2">
              {result['Top Matches'].map((m, i) => {
                const { name: n, score: s } = formatTopMatch(m)
                return (
                  <li key={i} className="flex justify-between gap-4">
                    <span>{n}</span>
                    {s != null && <span className="text-text-muted shrink-0">{s}</span>}
                  </li>
                )
              })}
            </ul>
          </div>
        )}
      </CardBody>

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
    </Card>
  )
}
