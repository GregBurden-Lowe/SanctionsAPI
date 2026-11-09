import { ReviewDecisionCard } from '@/components/ReviewDecisionCard'
import { CandidateEvidence } from '@/components/CandidateEvidence'
import { useEffect, useState, useRef } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import type { IconType } from 'react-icons'
import { BiCheckCircle, BiInfoCircle, BiMinus, BiQuestionMark, BiXCircle } from 'react-icons/bi'
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
import { opcheck, type OpCheckParams } from '@/api/client'
import { useAuth } from '@/context/AuthContext'
import type { OpCheckResponse, ApiErrorResponse } from '@/types/api'
import type { TopMatch } from '@/types/api'
import { generateScreeningPdf } from '@/utils/exportScreeningPdf'
import type { SearchDetails } from '@/utils/exportScreeningPdf'


function formatTopMatch(m: TopMatch): { name: string; score: number } {
  if (Array.isArray(m) && m.length >= 2) return { name: m[0], score: m[1] }
  if (m && typeof m === 'object' && 'name' in m) return { name: (m as { name: string }).name, score: (m as { score: number }).score ?? 0 }
  return { name: String(m), score: 0 }
}

function formatEntityTypeLabel(entityType: string): string {
  return entityType === 'Organization' ? 'Organisation' : entityType
}

const REASON_FOR_CHECK_OPTIONS = [
  'Client Onboarding',
  'Claim Payment',
  'Business Partner Payment',
  'Business Partner Due Diligence',
  'Periodic Re-Screen',
  'Ad-Hoc Compliance Review',
] as const

type ReasonForCheck = (typeof REASON_FOR_CHECK_OPTIONS)[number] | ''

export function ScreeningPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const retry = (location.state as { retry?: { request_id: string; inputs: OpCheckParams } } | null)?.retry
  const inFlight = useRef(false)
  const [name, setName] = useState(retry?.inputs.name || '')
  const [dob, setDob] = useState(retry?.inputs.dob || '')
  const [country, setCountry] = useState(retry?.inputs.country || 'United Kingdom')
  const [entityType, setEntityType] = useState<'Person' | 'Organization'>(retry?.inputs.entity_type === 'Organization' ? 'Organization' : 'Person')
  const [businessReference, setBusinessReference] = useState(
    retry?.inputs.business_reference || sessionStorage.getItem('screening_last_business_reference') || ''
  )
  const [reasonForCheck, setReasonForCheck] = useState<ReasonForCheck>(retry?.inputs.reason_for_check || '')
  const [requestor, setRequestor] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const autoRequestor = (user?.email || user?.username || '').trim()
    if (autoRequestor && !requestor.trim()) {
      setRequestor(autoRequestor)
    }
  }, [user, requestor])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (inFlight.current) return
    setError(null)
    const nameTrim = name.trim()
    const dobTrim = dob.trim()
    const countryTrim = country.trim() || 'United Kingdom'
    const businessReferenceTrim = businessReference.trim()
    const requestorTrim = requestor.trim()
    if (!nameTrim) {
      setError("Please provide a name to run a check.")
      return
    }
    if (!businessReferenceTrim) {
      setError("Please provide a business reference to link this check to a client file or case number.")
      return
    }
    if (!reasonForCheck) {
      setError("Please select a reason for check.")
      return
    }
    if (!requestorTrim) {
      setError("Please provide the requestor name to run a check.")
      return
    }
    inFlight.current = true
    setLoading(true)
    try {
      sessionStorage.setItem('screening_last_business_reference', businessReferenceTrim)
      const params: OpCheckParams = {
        name: nameTrim,
        dob: dobTrim || null,
        country: countryTrim || null,
        entity_type: entityType,
        business_reference: businessReferenceTrim,
        reason_for_check: reasonForCheck as (typeof REASON_FOR_CHECK_OPTIONS)[number],
        requestor: requestorTrim,
        search_backend: 'postgres_beta',
        rerun_entity_key: retry?.inputs.rerun_entity_key,
        retry_of: retry?.request_id,
      }
      const signature = JSON.stringify({ user: user?.email || user?.username, params })
      let pending: { signature: string; key: string } | null = null
      try { pending = JSON.parse(sessionStorage.getItem('screening_pending_submission') || 'null') } catch { /* discard invalid local state */ }
      if (pending?.signature !== signature) pending = { signature, key: crypto.randomUUID() }
      sessionStorage.setItem('screening_pending_submission', JSON.stringify(pending))
      const res = await opcheck(params, pending!.key)
      const data = await res.json()
      if (data.request_id || res.status === 409) sessionStorage.removeItem('screening_pending_submission')
      if (!res.ok) {
        if (data.request_id) { navigate(`/history/${data.request_id}`); return }
        const err = data as ApiErrorResponse
        setError(err.message ?? err.error ?? 'Check failed.')
        return
      }
      if ((data.status === 'queued' || data.status === 'requested') && data.request_id) {
        navigate(`/history/${data.request_id}`)
        return
      }
      const payload: ScreeningResultState = {
        result: data as OpCheckResponse,
        searchDetails: {
          searchName: nameTrim,
          entityType,
          searchDob: dobTrim,
          searchCountry: countryTrim,
          businessReference: businessReferenceTrim,
          reasonForCheck,
          requestor: requestorTrim,
          searchBackend: 'postgres_beta',
        },
      }
      sessionStorage.setItem('screening_last_result', JSON.stringify(payload))
      navigate('/results', { state: payload })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Network error.')
    } finally {
      inFlight.current = false
      setLoading(false)
    }
  }

  return (
    <div className="px-[26px] pt-[22px] pb-[26px]">
      <div className="max-w-3xl space-y-6">
        <SectionHeader title="Run check" />
        <div>
          <Card>
            <CardBody className="max-w-[920px]">
              <form onSubmit={handleSubmit} className="space-y-4">
                {retry && <p className="text-sm">Retrying a failed screening. Review the inputs before submitting; the original record will be preserved. <Link className="underline" to={`/history/${retry.request_id}`}>View original request</Link></p>}
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="md:col-span-2">
                    <Input
                      label="Name or organization"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="e.g. John Smith or Acme Ltd"
                      required
                    />
                  </div>
                  <div>
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
                        <option value="Organization">Organisation</option>
                      </select>
                    </div>
                  </div>
                  <div>
                    <Input
                      label="Date of birth (optional)"
                      value={dob}
                      onChange={(e) => setDob(e.target.value)}
                      placeholder="DD-MM-YYYY or YYYY"
                    />
                  </div>
                  <div className="md:col-span-2">
                    <Input
                      label="Country (defaults to United Kingdom)"
                      value={country}
                      onChange={(e) => setCountry(e.target.value)}
                      placeholder="United Kingdom"
                    />
                    <p className="mt-1 text-xs text-text-secondary">Default for UK and Channel Islands business; change it if a different country is known.</p>
                  </div>
                  <div className="md:col-span-2">
                    <Input
                      label="Business reference *"
                      value={businessReference}
                      onChange={(e) => setBusinessReference(e.target.value)}
                      placeholder="e.g. BR-2026-000123"
                      hint="Link this check to a client file or case number for your audit trail."
                      required
                    />
                  </div>
                  <div className="md:col-span-2">
                    <div>
                      <label htmlFor="reason_for_check" className="block text-[12.5px] font-semibold text-[#374151] mb-[5px]">
                        Reason for check *
                      </label>
                      <select
                        id="reason_for_check"
                        value={reasonForCheck}
                        onChange={(e) => setReasonForCheck(e.target.value as ReasonForCheck)}
                        className={`w-full h-10 rounded-lg border bg-[#f8fafc] px-3 text-[13px] outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/15 ${reasonForCheck ? 'text-[#1e293b] border-[#e2e8f0]' : 'text-[#94a3b8] border-[#e2e8f0]'}`}
                        required
                      >
                        <option value="" disabled>Select a reason…</option>
                        {REASON_FOR_CHECK_OPTIONS.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div className="md:col-span-2">
                    <Input
                      label="Requested By"
                      value={requestor}
                      onChange={(e) => setRequestor(e.target.value)}
                      placeholder="Who is running this check"
                      required
                    />
                  </div>
                </div>
                <Button type="submit" className="w-full mt-6" disabled={loading}>
                  {loading ? 'Checking…' : 'Check'}
                </Button>
              </form>
            </CardBody>
            {error && (
              <div className="px-5 pb-4">
                <ErrorBox message={error} />
              </div>
            )}
          </Card>
        </div>

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

function effectiveSearchDetails(result: OpCheckResponse, search: SearchDetails): SearchDetails {
  const inputs = result['Screening Inputs'] ?? result['Engine Result']?.['Screening Inputs']
  if (!inputs) return search
  return {
    ...search,
    searchName: inputs.name ?? search.searchName,
    entityType: inputs.entity_type ?? search.entityType,
    searchDob: 'dob' in inputs ? inputs.dob ?? '' : search.searchDob,
    searchCountry: 'country' in inputs ? inputs.country ?? '' : search.searchCountry,
    searchBackend: (inputs.backend as SearchDetails['searchBackend']) ?? search.searchBackend,
  }
}

export function ScreeningInputsCard({ result, searchDetails }: { result: OpCheckResponse; searchDetails: SearchDetails }) {
  const details = effectiveSearchDetails(result, searchDetails)
  const hasRecordedInputs = Boolean(result['Screening Inputs'] ?? result['Engine Result']?.['Screening Inputs'])
  return (
    <Card>
      <CardHeader>
        <CardTitle>Screening details</CardTitle>
      </CardHeader>
      <CardBody>
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
          <div className="sm:col-span-2">
            <dt className="text-xs font-medium text-text-muted">Name or organization</dt>
            <dd className="text-text-primary mt-0.5">{details.searchName || '—'}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-text-muted">Entity type</dt>
            <dd className="text-text-primary mt-0.5">{formatEntityTypeLabel(details.entityType || '—')}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-text-muted">Date of birth</dt>
            <dd className="text-text-primary mt-0.5">{details.searchDob?.trim() ? details.searchDob : 'Not provided'}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-text-muted">Country</dt>
            <dd className="text-text-primary mt-0.5">{details.searchCountry?.trim() ? details.searchCountry : 'Not provided'}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-xs font-medium text-text-muted">Reason for check</dt>
            <dd className="text-text-primary mt-0.5">{details.reasonForCheck || '—'}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-xs font-medium text-text-muted">Requestor</dt>
            <dd className="text-text-primary mt-0.5">{details.requestor || '—'}</dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-xs font-medium text-text-muted">Business reference</dt>
            <dd className="text-text-primary mt-0.5">{details.businessReference || '—'}</dd>
          </div>
        </dl>
        {!hasRecordedInputs && <p className="mt-3 text-xs text-text-muted">This older result does not contain a complete snapshot of the screened inputs. Available recorded details are shown.</p>}
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
  const [result, setResult] = useState<OpCheckResponse>(payload.result)
  const [searchDetails, setSearchDetails] = useState<SearchDetails>(payload.searchDetails)
  const [rerunDob, setRerunDob] = useState(payload.searchDetails.searchDob ?? '')
  const [rerunCountry, setRerunCountry] = useState(payload.searchDetails.searchCountry ?? '')
  const [rerunLoading, setRerunLoading] = useState(false)
  const [rerunError, setRerunError] = useState<string | null>(null)
  const personRerunEnabled = searchDetails.entityType === 'Person'
  const organizationRerunEnabled = searchDetails.entityType === 'Organization'
  const hasAdverseOutcome = Boolean(result['Is Sanctioned'])
  const dobAlreadyProvided = Boolean(searchDetails.searchDob?.trim())
  const countryAlreadyProvided = Boolean(searchDetails.searchCountry?.trim())
  const rerunEnabled =
    (personRerunEnabled && !dobAlreadyProvided && hasAdverseOutcome) ||
    (organizationRerunEnabled && !countryAlreadyProvided && hasAdverseOutcome)

  const handleRerunWithDob = async () => {
    const dobTrim = rerunDob.trim()
    const countryTrim = rerunCountry.trim()
    if (!rerunEnabled) return
    if (personRerunEnabled && !dobTrim) {
      setRerunError('Please enter date of birth before re-running.')
      return
    }
    if (organizationRerunEnabled && !countryTrim) {
      setRerunError('Please enter country before re-running.')
      return
    }
    if (!result.entity_key) {
      setRerunError('Entity key is missing; cannot re-run against the same stored record.')
      return
    }
    setRerunError(null)
    setRerunLoading(true)
    try {
      const res = await opcheck({
        name: searchDetails.searchName,
        dob: personRerunEnabled ? dobTrim : searchDetails.searchDob || null,
        country: organizationRerunEnabled ? countryTrim : searchDetails.searchCountry || null,
        entity_type: searchDetails.entityType,
        business_reference: searchDetails.businessReference,
        reason_for_check: searchDetails.reasonForCheck as
          | 'Client Onboarding'
          | 'Claim Payment'
          | 'Business Partner Payment'
          | 'Business Partner Due Diligence'
          | 'Periodic Re-Screen'
          | 'Ad-Hoc Compliance Review',
        requestor: searchDetails.requestor,
        search_backend: 'postgres_beta',
        rerun_entity_key: result.entity_key,
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        const detail = (data as { detail?: string }).detail
        const message = (data as { message?: string }).message
        const error = (data as { error?: string }).error
        setRerunError(detail ?? message ?? error ?? 'Failed to re-run with date of birth.')
        return
      }
      if ((data as { status?: string }).status === 'queued') {
        setRerunError('Re-run was queued due to system load. Please refresh shortly.')
        return
      }
      const nextResult = data as OpCheckResponse
      const nextSearch: SearchDetails = {
        ...searchDetails,
        searchDob: personRerunEnabled ? dobTrim : searchDetails.searchDob,
        searchCountry: nextResult['Screening Inputs']?.country ?? (organizationRerunEnabled ? countryTrim : searchDetails.searchCountry || 'United Kingdom'),
      }
      const nextPayload: ScreeningResultState = { result: nextResult, searchDetails: nextSearch }
      setResult(nextResult)
      setSearchDetails(nextSearch)
      sessionStorage.setItem('screening_last_result', JSON.stringify(nextPayload))
    } catch (err) {
      setRerunError(err instanceof Error ? err.message : 'Failed to re-run with date of birth.')
    } finally {
      setRerunLoading(false)
    }
  }

  return (
    <div className="px-[26px] pt-[22px] pb-[26px]">
      <div className="max-w-[1600px] space-y-5">
        <div className="flex items-center justify-between gap-4">
          <SectionHeader title="Screening result" />
          <Button type="button" variant="ghost" size="sm" onClick={() => navigate('/')}>
            Run another check
          </Button>
        </div>
        <ScreeningInputsCard result={result} searchDetails={searchDetails} />
        {rerunEnabled && (
          <Card>
            <CardHeader>
              <CardTitle>{personRerunEnabled ? 'Refine with date of birth' : 'Refine with country'}</CardTitle>
            </CardHeader>
            <CardBody className="space-y-3">
              <p className="text-sm text-text-secondary">
                Adding {personRerunEnabled ? 'a date of birth' : 'a country'} can help confirm or rule out these candidates. The result will update the same entity record.
              </p>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                {personRerunEnabled ? (
                  <Input
                    label="Date of birth"
                    value={rerunDob}
                    onChange={(e) => setRerunDob(e.target.value)}
                    placeholder="DD-MM-YYYY or YYYY-MM-DD or YYYY"
                  />
                ) : (
                  <Input
                    label="Country"
                    value={rerunCountry}
                    onChange={(e) => setRerunCountry(e.target.value)}
                    placeholder="e.g. UK"
                  />
                )}
                <Button type="button" onClick={() => void handleRerunWithDob()} disabled={rerunLoading}>
                  {rerunLoading ? 'Re-running…' : personRerunEnabled ? 'Re-run with DoB' : 'Re-run with country'}
                </Button>
              </div>
              {rerunError && (
                <p className="text-sm text-semantic-error" role="alert">
                  {rerunError}
                </p>
              )}
            </CardBody>
          </Card>
        )}
        <ResultCard result={result} searchDetails={searchDetails} showScreeningInputs={false} />
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
  summaryLines.push(hasUK ? 'UK-labelled result source' : 'No UK-labelled result source')
  if (otherCount > 0) {
    summaryLines.push(otherCount === 1 ? 'Other source labels: 1' : `Other source labels: ${otherCount}`)
  }
  return { list: items, hasUK, otherCount, summaryLines }
}

function getOutcome(result: OpCheckResponse): {
  heading: string
  detail: string
  semantic: 'success' | 'error' | 'warning'
} {
  const status = result['Check Summary']?.Status ?? ''
  if (status.includes('False Positive')) {
    return {
      heading: 'Cleared — discounted as false positive',
      detail: 'An analyst resolved this as a false positive. Original engine findings are preserved below.',
      semantic: 'warning',
    }
  }
  if (result['Is Sanctioned']) {
    return {
      heading: 'Potential sanctions match — review required',
      detail: 'Stop and escalate for enhanced due diligence before proceeding.',
      semantic: 'error',
    }
  }
  return {
    heading: 'Cleared — no sanctions match',
    detail: 'No potential sanctions match under current rules. This is not proof of identity.',
    semantic: 'success',
  }
}

export function ResultCard({
  result,
  searchDetails,
  showScreeningInputs = true,
}: {
  result: OpCheckResponse
  searchDetails: SearchDetails
  showScreeningInputs?: boolean
}) {
  searchDetails = effectiveSearchDetails(result, searchDetails)
  const summary = result['Check Summary']
  const outcome = getOutcome(result)
  const { list: sourceList } = parseSourceList(summary?.Source)
  const [showSourcesModal, setShowSourcesModal] = useState(false)
  const [pdfError, setPdfError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const topMatches = (result['Top Matches'] ?? []).map(formatTopMatch)
  const inputClassification = result['Input Classification']
  const candidateSummary = result['Candidate Evidence Summary']
  const potentialMatches = candidateSummary?.potential_matches ?? (result['Match Found'] ? 1 : 0)
  const excluded = candidateSummary?.excluded ?? 0

  // Outcome bar
  const outcomeBar = {
    success: { bar: 'bg-semantic-success/10 border-semantic-success/25', text: 'text-semantic-success', icon: 'bg-semantic-success' },
    error:   { bar: 'bg-semantic-error/10 border-semantic-error/25',     text: 'text-semantic-error',   icon: 'bg-semantic-error'   },
    warning: { bar: 'bg-semantic-warning/10 border-semantic-warning/25', text: 'text-semantic-warning', icon: 'bg-semantic-warning' },
  }[outcome.semantic]
  const OutcomeIcon = outcome.semantic === 'error' ? BiXCircle : outcome.semantic === 'warning' ? BiInfoCircle : BiCheckCircle

  // Check rows
  type CheckTone = 'pass' | 'fail' | 'neutral' | 'info'
  type CheckRow = { title: string; subtitle: string; badge: string; icon: IconType; tone: CheckTone }

  const verificationRows: CheckRow[] = [
    {
      title: 'Sanctions status',
      subtitle: result['Is Sanctioned'] ? 'Potential sanctions match found' : result['Engine Result']?.['Is Sanctioned'] ? 'Engine match resolved by analyst' : 'No sanctions match detected',
      badge: result['Is Sanctioned'] ? 'Match found' : 'Clear',
      icon: result['Is Sanctioned'] ? BiXCircle : BiCheckCircle,
      tone: result['Is Sanctioned'] ? 'fail' : 'pass',
    },
    {
      title: 'Person list',
      subtitle: result['Entity Type Checks']?.Person?.status ? `Person list: ${result['Entity Type Checks'].Person.status}` : 'Person list result unavailable',
      badge: result['Entity Type Checks']?.Person?.is_match === true ? 'Match' : result['Entity Type Checks']?.Person?.is_match === false ? 'Clear' : 'N/A',
      icon: result['Entity Type Checks']?.Person?.is_match === true ? BiXCircle : result['Entity Type Checks']?.Person?.is_match === false ? BiCheckCircle : BiMinus,
      tone: result['Entity Type Checks']?.Person?.is_match === true ? 'fail' : result['Entity Type Checks']?.Person?.is_match === false ? 'pass' : 'neutral',
    },
    {
      title: 'Organisation list',
      subtitle: result['Entity Type Checks']?.Organization?.status ? `Organisation list: ${result['Entity Type Checks'].Organization.status}` : 'Organisation list result unavailable',
      badge: result['Entity Type Checks']?.Organization?.is_match === true ? 'Match' : result['Entity Type Checks']?.Organization?.is_match === false ? 'Clear' : 'N/A',
      icon: result['Entity Type Checks']?.Organization?.is_match === true ? BiXCircle : result['Entity Type Checks']?.Organization?.is_match === false ? BiCheckCircle : BiMinus,
      tone: result['Entity Type Checks']?.Organization?.is_match === true ? 'fail' : result['Entity Type Checks']?.Organization?.is_match === false ? 'pass' : 'neutral',
    },
    {
      title: 'Entity type',
      subtitle: inputClassification?.likely_misclassified
        ? `Submitted as ${inputClassification.submitted_as ?? '—'}, engine infers ${inputClassification.inferred_as ?? '—'}.`
        : inputClassification?.signals?.[0] ?? 'No entity-type mismatch detected.',
      badge: inputClassification?.likely_misclassified ? 'Mismatch' : inputClassification?.inferred_as ?? formatEntityTypeLabel(searchDetails.entityType || 'Person'),
      icon: inputClassification?.likely_misclassified ? BiQuestionMark : BiInfoCircle,
      tone: inputClassification?.likely_misclassified ? 'neutral' : 'info',
    },
    {
      title: 'Name similarity',
      subtitle: result['Match Found'] ? 'Similarity score — identity not confirmed' : 'No candidate reached the alert threshold',
      badge: result['Match Found'] ? `${result.Score} / 100` : 'N/A',
      icon: result['Match Found'] ? BiInfoCircle : BiMinus,
      tone: result['Match Found'] ? 'info' : 'neutral',
    },
  ]

  const toneBadge = (tone: CheckTone) => ({
    pass:    'bg-semantic-success/10 text-semantic-success border-semantic-success/25',
    fail:    'bg-semantic-error/10 text-semantic-error border-semantic-error/25',
    neutral: 'bg-surface text-text-muted border-border',
    info:    'bg-semantic-info/10 text-semantic-info border-semantic-info/25',
  }[tone])

  const toneIcon = (tone: CheckTone) => ({
    pass:    'text-semantic-success',
    fail:    'text-semantic-error',
    neutral: 'text-text-muted',
    info:    'text-semantic-info',
  }[tone])

  const handleDownloadPdf = async () => {
    setPdfError(null)
    try {
      await generateScreeningPdf(result, searchDetails)
    } catch (err) {
      setPdfError(err instanceof Error ? err.message : 'Failed to generate PDF')
    }
  }

  const handleCopy = () => {
    if (!result.entity_key) return
    navigator.clipboard.writeText(result.entity_key).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  // Result detail rows
  const detailRows: Array<[string, string]> = [
    ['Screened name', searchDetails.searchName || '—'],
    ['Entity type', formatEntityTypeLabel(searchDetails.entityType || '—')],
    ['Potential matches', String(potentialMatches)],
    ['Excluded', String(excluded)],
    ...(summary?.Date ? [['Checked at', summary.Date] as [string, string]] : []),
  ]

  return (
    <div className="space-y-4">
      {showScreeningInputs && <ScreeningInputsCard result={result} searchDetails={searchDetails} />}
      {/* Outcome bar */}
      <div className={`flex flex-col gap-3 rounded-[13px] border px-5 py-4 sm:flex-row sm:items-center ${outcomeBar.bar}`}>
        <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${outcomeBar.icon}`}>
          <OutcomeIcon className="h-5 w-5 text-white" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <p className={`font-semibold text-[14px] leading-snug ${outcomeBar.text}`}>{outcome.heading}</p>
          <p className={`text-[12.5px] mt-1 font-medium ${outcomeBar.text} ${outcome.semantic === 'error' ? '' : 'opacity-75'}`}>{outcome.detail}</p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {result.request_id && (
            <Link
              to={`/history/${result.request_id}`}
              className="text-xs text-text-secondary underline hover:text-text-primary"
            >
              {result.reused ? 'Reused record' : 'Permanent record'}
            </Link>
          )}
          {(result['Source Coverage'] || sourceList.length > 0) && (
            <Button type="button" variant="ghost" size="sm" onClick={() => setShowSourcesModal(true)}>
              Sources
            </Button>
          )}
          <Button type="button" variant="secondary" size="sm" onClick={handleDownloadPdf}>
            Download PDF
          </Button>
        </div>
      </div>

      {pdfError && <p className="text-xs text-semantic-error" role="alert">{pdfError}</p>}

      {/* Meta tiles */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {([
          ['Risk level', result['Risk Level'] || '—'],
          ['Match score', `${result.Score} / 100`],
          ['Similarity band', result.Confidence || 'Not assessed'],
          ['Checked at', summary?.Date ?? '—'],
        ] as [string, string][]).map(([label, value]) => (
          <div key={label} className="rounded-[13px] border border-border bg-surface p-3">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-text-muted mb-1">{label}</p>
            <p className="text-[13px] font-semibold text-text-primary leading-snug">{value}</p>
          </div>
        ))}
      </div>

      {result['Review Decision'] && <ReviewDecisionCard decision={result['Review Decision']} />}
      {result['Engine Result'] && (
        <p className="text-xs text-text-muted px-1">
          Original engine outcome before analyst review: <span className="text-text-secondary">{result['Engine Result']['Check Summary']?.Status ?? '—'}</span>
        </p>
      )}

      <CandidateEvidence result={result} search={searchDetails} />

      {/* Checks + detail */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Checks run</CardTitle>
            <span className="ml-auto text-[11px] rounded-md border border-border bg-app px-2 py-0.5 text-text-muted">
              {verificationRows.length}
            </span>
          </CardHeader>
          <CardBody className="grid grid-cols-1 gap-2 sm:grid-cols-2 p-3">
            {verificationRows.map((row) => (
              <div
                key={row.title}
                className="flex items-start gap-2.5 rounded-lg border border-border bg-app/60 px-3 py-2.5"
              >
                <row.icon className={`mt-0.5 h-4 w-4 shrink-0 ${toneIcon(row.tone)}`} aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="text-[12px] font-semibold text-text-primary leading-snug">{row.title}</p>
                  <p className="text-[11px] text-text-muted mt-0.5 leading-snug">{row.subtitle}</p>
                </div>
                <span className={`shrink-0 rounded-md border px-1.5 py-0.5 text-[10.5px] font-semibold ${toneBadge(row.tone)}`}>
                  {row.badge}
                </span>
              </div>
            ))}
          </CardBody>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Result detail</CardTitle></CardHeader>
            <CardBody className="p-0">
              {detailRows.map(([label, value]) => (
                <div key={label} className="flex items-baseline justify-between gap-4 border-b border-border px-5 py-2.5 last:border-0">
                  <dt className="shrink-0 text-[12px] text-text-muted">{label}</dt>
                  <dd className="text-right text-[12px] font-medium text-text-primary">{value}</dd>
                </div>
              ))}
              {result['Match Found'] && result['Sanctions Name'] && (
                <>
                  <div className="flex items-baseline justify-between gap-4 border-b border-border px-5 py-2.5">
                    <dt className="shrink-0 text-[12px] text-text-muted">Matched name</dt>
                    <dd className="text-right text-[12px] font-medium text-text-primary">{result['Sanctions Name']}</dd>
                  </div>
                  {result.Regime && (
                    <div className="flex items-baseline justify-between gap-4 border-b border-border px-5 py-2.5">
                      <dt className="shrink-0 text-[12px] text-text-muted">Regime</dt>
                      <dd className="text-right text-[12px] font-medium text-text-primary">{result.Regime}</dd>
                    </div>
                  )}
                  {result['Birth Date'] && (
                    <div className="flex items-baseline justify-between gap-4 px-5 py-2.5">
                      <dt className="shrink-0 text-[12px] text-text-muted">Matched DOB</dt>
                      <dd className="text-right text-[12px] font-medium text-text-primary">{result['Birth Date']}</dd>
                    </div>
                  )}
                </>
              )}
            </CardBody>
          </Card>

          {topMatches.length > 0 && (
            <Card>
              <CardHeader><CardTitle>Name suggestions</CardTitle></CardHeader>
              <CardBody className="p-0">
                <p className="px-5 pt-3 pb-2 text-[11px] text-text-muted">Similar names for context — they do not change the outcome.</p>
                {topMatches.map((item, i) => (
                  <div key={`${item.name}-${i}`} className="flex items-center justify-between gap-4 border-t border-border px-5 py-2.5">
                    <span className="text-[12px] text-text-primary">{item.name}</span>
                    <span className="text-[11px] text-text-muted border border-border bg-app px-2 py-0.5 rounded-md">
                      {item.score}
                    </span>
                  </div>
                ))}
              </CardBody>
            </Card>
          )}
        </div>
      </div>

      {/* Reference bar */}
      {result.entity_key && (
        <div className="flex items-center gap-3 rounded-[13px] border border-border bg-surface px-4 py-3">
          <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wider text-text-muted">Reference</span>
          <code className="min-w-0 flex-1 truncate font-mono text-xs text-text-secondary">{result.entity_key}</code>
          <Button type="button" variant="ghost" size="sm" onClick={handleCopy}>
            {copied ? 'Copied' : 'Copy'}
          </Button>
        </div>
      )}

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
        {(() => {
          const coverage = result['Source Coverage']
          const listCounts = coverage?.list_counts ?? {}
          const listsSearched = coverage?.required_lists ?? []
          const missingLists = coverage?.missing_lists ?? []

          // Map of known list names to readable labels and scrape sources
          const LIST_META: Record<string, { label: string; source: string }> = {
            UK:   { label: 'UK (OFSI)',           source: 'HM Treasury / OFSI — gov.uk' },
            UN:   { label: 'UN Security Council', source: 'United Nations — un.org' },
            EU:   { label: 'EU Consolidated',     source: 'European Commission — eur-lex.europa.eu' },
            OFAC: { label: 'OFAC SDN',            source: 'US Treasury / OFAC — ofac.treas.gov' },
          }

          return (
            <div className="space-y-4">
              {listsSearched.length > 0 && (
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-text-muted mb-2">
                    Lists searched at time of check
                  </p>
                  <ul className="space-y-0 divide-y divide-border">
                    {listsSearched.map((list) => {
                      const meta = LIST_META[list]
                      const count = listCounts[list]
                      const isMissing = missingLists.includes(list)
                      return (
                        <li key={list} className="flex items-start justify-between gap-4 py-2.5">
                          <div className="min-w-0">
                            <p className="text-[13px] font-medium text-text-primary leading-snug">
                              {meta?.label ?? list}
                              {isMissing && (
                                <span className="ml-2 text-[10.5px] font-semibold text-semantic-error border border-semantic-error/25 bg-semantic-error/10 rounded px-1.5 py-0.5">
                                  Missing
                                </span>
                              )}
                            </p>
                            {meta?.source && (
                              <p className="text-[11px] text-text-muted mt-0.5">{meta.source}</p>
                            )}
                          </div>
                          {count != null && (
                            <span className="shrink-0 text-[11px] tabular-nums text-text-muted">
                              {count.toLocaleString()} records
                            </span>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                  {coverage?.ingested_at && (
                    <p className="text-[11px] text-text-muted mt-3">
                      Data ingested {new Date(coverage.ingested_at).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })}
                      {coverage.status ? ` · ${coverage.status}` : ''}
                    </p>
                  )}
                </div>
              )}

              {sourceList.length > 0 && (
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-wider text-text-muted mb-2">
                    Matched result labels
                  </p>
                  <ul className="space-y-0 divide-y divide-border">
                    {sourceList.map((item, i) => (
                      <li key={i} className="py-2 text-[13px] text-text-secondary">{item}</li>
                    ))}
                  </ul>
                </div>
              )}

              {listsSearched.length === 0 && sourceList.length === 0 && (
                <p className="text-[13px] text-text-muted">No source information available for this result.</p>
              )}
            </div>
          )
        })()}
      </Modal>
    </div>
  )
}
