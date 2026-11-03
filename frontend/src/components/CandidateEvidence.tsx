import type { OpCheckResponse, CandidateEvidenceItem } from '@/types/api'
import type { SearchDetails } from '@/utils/exportScreeningPdf'
import { Card, CardBody, CardHeader, CardTitle } from '@/components'
import { BiCheckCircle, BiXCircle, BiMinus, BiInfoCircle } from 'react-icons/bi'

function formatIngestedDate(raw: string | undefined): string {
  if (!raw) return 'Unknown'
  try {
    return new Date(raw).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
  } catch {
    return raw
  }
}

function formatDisplayDob(raw: string | undefined): string {
  if (!raw) return '—'
  const ddmmyyyy = raw.match(/^(\d{1,2})-(\d{1,2})-(\d{4})$/)
  if (ddmmyyyy) {
    const d = new Date(parseInt(ddmmyyyy[3]), parseInt(ddmmyyyy[2]) - 1, parseInt(ddmmyyyy[1]))
    if (!isNaN(d.getTime())) return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
  }
  const yyyymmdd = raw.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/)
  if (yyyymmdd) {
    const d = new Date(parseInt(yyyymmdd[1]), parseInt(yyyymmdd[2]) - 1, parseInt(yyyymmdd[3]))
    if (!isNaN(d.getTime())) return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
  }
  if (/^\d{4}$/.test(raw)) return raw
  return raw
}

function DobBadge({ assessment }: { assessment: CandidateEvidenceItem['dob_assessment'] }) {
  const map = {
    exact:          { label: 'Exact match',   cls: 'text-semantic-success border-semantic-success/25 bg-semantic-success/10',  title: undefined },
    compatible:     { label: 'Compatible',    cls: 'text-semantic-warning border-semantic-warning/25 bg-semantic-warning/10',  title: undefined },
    conflicting:    { label: 'Conflicting',   cls: 'text-semantic-error border-semantic-error/25 bg-semantic-error/10',         title: undefined },
    unknown:        { label: 'Unknown',       cls: 'text-text-muted border-border bg-surface',                                  title: 'The candidate record does not include a date of birth to compare against.' },
    not_applicable: { label: 'N/A',           cls: 'text-text-muted border-border bg-surface',                                  title: undefined },
  }
  const { label, cls, title } = map[assessment] ?? { label: assessment, cls: 'text-text-muted border-border bg-surface', title: undefined }
  return <span className={`rounded-md border px-1.5 py-0.5 text-[10.5px] font-semibold cursor-default ${cls}`} title={title}>{label}</span>
}

function CountryBadge({ assessment }: { assessment: CandidateEvidenceItem['country_assessment'] }) {
  const map = {
    compatible: { label: 'Compatible', cls: 'text-semantic-success border-semantic-success/25 bg-semantic-success/10' },
    conflicting: { label: 'Conflicting', cls: 'text-semantic-error border-semantic-error/25 bg-semantic-error/10' },
    unknown: { label: 'Unknown', cls: 'text-text-muted border-border bg-surface' },
  }
  const { label, cls } = map[assessment] ?? { label: assessment, cls: 'text-text-muted border-border bg-surface' }
  return <span className={`rounded-md border px-1.5 py-0.5 text-[10.5px] font-semibold ${cls}`}>{label}</span>
}

export function CandidateEvidence({ result, search, isPepOnly }: { result: OpCheckResponse; search: SearchDetails; isPepOnly?: boolean }) {
  const evidence = result['Candidate Evidence']
  if (!evidence) return null

  const summary = result['Candidate Evidence Summary']
  const coverage = result['Source Coverage']
  const guidance = result['Screening Guidance']
  const monitoring = result['Monitoring Change']

  const potentialMatches = summary?.potential_matches ?? 0
  const excluded = summary?.excluded ?? 0
  const evaluated = (summary?.evaluated_name_matches ?? evidence.length)

  const hasMatch = potentialMatches > 0
  const VerdictIcon = isPepOnly ? BiInfoCircle : (hasMatch ? BiXCircle : BiMinus)
  const verdictColor = isPepOnly ? 'text-semantic-info' : (hasMatch ? 'text-semantic-error' : 'text-text-muted')

  const finding = isPepOnly
    ? `${potentialMatches} PEP candidate${potentialMatches !== 1 ? 's' : ''} found — no sanctions match`
    : hasMatch
      ? `${potentialMatches} potential match${potentialMatches !== 1 ? 'es' : ''} — review required`
      : 'No potential match identified'
  const nextAction = isPepOnly
    ? 'No action required. PEP details recorded for reference.'
    : (guidance?.next_actions?.[0]
      ?? (hasMatch ? 'Review the candidate evidence and verify identity before proceeding.' : 'Apply normal business controls and retain this screening record.'))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Candidate evidence</CardTitle>
        {evaluated > 0 && (
          <span className="ml-auto text-[11px] rounded-md border border-border bg-app px-2 py-0.5 text-text-muted">
            {evaluated} evaluated
          </span>
        )}
      </CardHeader>
      <CardBody className="space-y-4">

        {/* Source coverage */}
        {coverage && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">Lists checked</span>
            <div className="flex flex-wrap gap-1.5">
              {(coverage.required_lists ?? []).map((list) => (
                <span
                  key={list}
                  className="rounded-md border border-border bg-app px-2 py-0.5 text-[11px] font-medium text-text-secondary"
                >
                  {list}
                </span>
              ))}
            </div>
            <span className="text-[11px] text-text-muted">
              · Data {coverage.status} · {formatIngestedDate(coverage.ingested_at)}
            </span>
          </div>
        )}

        {/* Verdict row */}
        <div className={`flex items-start gap-3 rounded-lg border px-4 py-3 ${isPepOnly ? 'border-semantic-info/25 bg-semantic-info/5' : hasMatch ? 'border-semantic-error/25 bg-semantic-error/5' : 'border-border bg-app/60'}`}>
          <VerdictIcon className={`mt-0.5 h-4 w-4 shrink-0 ${verdictColor}`} aria-hidden />
          <div className="min-w-0 flex-1">
            <p className={`text-[13px] font-semibold leading-snug ${isPepOnly ? 'text-semantic-info' : hasMatch ? 'text-semantic-error' : 'text-text-primary'}`}>
              {finding}
            </p>
            <p className="text-[12px] text-text-secondary mt-0.5 leading-snug">{nextAction}</p>
            {hasMatch && !isPepOnly && evidence.length > 0 && (() => {
              const highConf = evidence.filter(c => c.disposition === 'potential_match' && c.name_score >= 90).length
              const lowConf = evidence.filter(c => c.disposition === 'potential_match' && c.name_score < 90).length
              if (highConf === 0 && lowConf === 0) return null
              return (
                <p className="text-[11px] text-text-muted mt-1.5">
                  {highConf > 0 && <span className="text-semantic-error font-medium">{highConf} high-confidence match{highConf !== 1 ? 'es' : ''} (≥90)</span>}
                  {highConf > 0 && lowConf > 0 && <span className="mx-1">·</span>}
                  {lowConf > 0 && <span>{lowConf} lower-confidence match{lowConf !== 1 ? 'es' : ''}</span>}
                </p>
              )
            })()}
          </div>
          <div className="shrink-0 text-right">
            <p className="text-[11px] text-text-muted leading-relaxed">
              {potentialMatches} match{potentialMatches !== 1 ? 'es' : ''}
            </p>
            {excluded > 0 && (
              <p className="text-[11px] text-text-muted leading-relaxed">{excluded} excluded</p>
            )}
          </div>
        </div>

        {/* Monitoring change */}
        {monitoring && (
          <div className="rounded-lg border border-border bg-app/60 px-4 py-3 text-[12px] text-text-secondary space-y-1">
            <p className="font-medium text-text-primary">{monitoring.message}</p>
            {(monitoring.added_candidates.length > 0 || monitoring.removed_candidates.length > 0 || monitoring.changed_candidates.length > 0) && (
              <p>Added: {monitoring.added_candidates.length} · Removed: {monitoring.removed_candidates.length} · Changed: {monitoring.changed_candidates.length}</p>
            )}
          </div>
        )}

        {/* Candidate rows */}
        {evidence.length > 0 && (
          <div className="space-y-2">
            {evidence.map((candidate, i) => {
              const isPotentialMatch = candidate.disposition === 'potential_match'
              const isHighConfidence = isPotentialMatch && candidate.name_score >= 90
              return (
                <details
                  key={`${candidate.record_id || candidate.name}-${candidate.entity_type}-${i}`}
                  className="group rounded-lg border border-border overflow-hidden"
                  open={i === 0 && isHighConfidence}
                >
                  <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 hover:bg-app/60 [&::-webkit-details-marker]:hidden">
                    <span className={`h-2 w-2 shrink-0 rounded-full ${isPotentialMatch && isPepOnly ? 'bg-semantic-info' : isPotentialMatch ? 'bg-semantic-error' : 'bg-text-muted'}`} />
                    <span className="min-w-0 flex-1 text-[13px] font-medium text-text-primary truncate">{candidate.name}</span>
                    <span className="shrink-0 text-[11px] text-text-muted">{candidate.source_type === 'peps' ? 'PEP' : 'Sanctions'}</span>
                    <span className={`shrink-0 rounded-md border px-1.5 py-0.5 text-[10.5px] font-semibold ${isPotentialMatch && isPepOnly ? 'bg-semantic-info/10 text-semantic-info border-semantic-info/25' : isPotentialMatch ? 'bg-semantic-error/10 text-semantic-error border-semantic-error/25' : 'bg-surface text-text-muted border-border'}`}>
                      {isPotentialMatch ? 'Potential match' : 'Excluded'}
                    </span>
                    <span className="shrink-0 text-[11px] text-text-muted tabular-nums">{candidate.name_score}/100</span>
                  </summary>
                  <div className="border-t border-border bg-app/40 px-4 py-3 space-y-3">
                    <table className="w-full text-[12px]">
                      <thead>
                        <tr className="text-text-muted text-[10.5px] uppercase tracking-wider">
                          <th className="pb-1.5 text-left font-semibold w-24">Field</th>
                          <th className="pb-1.5 text-left font-semibold">Submitted</th>
                          <th className="pb-1.5 text-left font-semibold">Candidate</th>
                          <th className="pb-1.5 text-left font-semibold">Assessment</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        <tr>
                          <td className="py-1.5 text-text-muted">Name</td>
                          <td className="py-1.5 text-text-primary">{search.searchName}</td>
                          <td className="py-1.5 text-text-primary">{candidate.matched_name}</td>
                          <td className="py-1.5 text-text-secondary">{candidate.matched_alias ? 'Listed alias' : 'Listed name'}</td>
                        </tr>
                        <tr>
                          <td className="py-1.5 text-text-muted">DOB</td>
                          <td className="py-1.5 text-text-primary">{formatDisplayDob(search.searchDob)}</td>
                          <td className="py-1.5 text-text-primary">{formatDisplayDob(candidate.birth_date ?? undefined)}</td>
                          <td className="py-1.5"><DobBadge assessment={candidate.dob_assessment} /></td>
                        </tr>
                        <tr>
                          <td className="py-1.5 text-text-muted">Country</td>
                          <td className="py-1.5 text-text-primary">{search.searchCountry || '—'}</td>
                          <td className="py-1.5 text-text-primary">{candidate.country || '—'}</td>
                          <td className="py-1.5"><CountryBadge assessment={candidate.country_assessment} /></td>
                        </tr>
                      </tbody>
                    </table>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-muted">
                      <span>Source: <span className="text-text-secondary">{candidate.dataset || 'Not specified'}</span></span>
                      <span>Type: <span className="text-text-secondary">{candidate.entity_type}</span></span>
                      {candidate.record_id && (
                        <span className="break-all">Record: <span className="font-mono text-text-secondary">{candidate.record_id}</span></span>
                      )}
                    </div>
                    {candidate.reasons.length > 0 && (
                      <ul className="space-y-1">
                        {candidate.reasons.map((reason) => (
                          <li key={reason} className="flex items-start gap-2 text-[11px] text-text-secondary">
                            <BiCheckCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-text-muted" aria-hidden />
                            {reason}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </details>
              )
            })}
          </div>
        )}

        {summary?.display_truncated && (
          <p className="text-[11px] text-text-muted">Showing first 50 candidates. The outcome includes all evaluated candidates.</p>
        )}

        <p className="text-[11px] text-text-muted leading-relaxed">
          Name similarity is a matching score, not an identity confirmation. Missing details do not establish a mismatch.
        </p>
      </CardBody>
    </Card>
  )
}
