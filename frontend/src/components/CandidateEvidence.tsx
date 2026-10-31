import type { OpCheckResponse } from '@/types/api'
import type { SearchDetails } from '@/utils/exportScreeningPdf'
import { Card, CardBody, CardHeader, CardTitle } from '@/components'

export function CandidateEvidence({ result, search }: { result: OpCheckResponse; search: SearchDetails }) {
  const evidence = result['Candidate Evidence']
  if (!evidence) return null
  const summary = result['Candidate Evidence Summary']
  return <Card><CardHeader><CardTitle>Candidate evidence</CardTitle></CardHeader><CardBody className="space-y-4">
    {result['Source Coverage'] && <div className="rounded-lg border border-border p-3 text-sm space-y-1">
      <p className="font-medium">Source coverage at screening: {result['Source Coverage'].status}</p>
      <p>Lists checked: {result['Source Coverage'].required_lists?.join(', ') || 'Not verified'}</p>
      <p>Ingested: {result['Source Coverage'].ingested_at || 'Unknown'} · Source publication time: {result['Source Coverage'].source_published_at || 'Not verified'}</p>
      <p className="break-all text-xs">Dataset version: {result['Source Coverage'].version || 'Not recorded'}</p>
    </div>}
    {result['Screening Guidance'] && <div className="text-sm"><p className="font-medium">Next actions</p><ul className="list-disc pl-5">{result['Screening Guidance'].next_actions.map(action => <li key={action}>{action}</li>)}</ul></div>}
    {result['Monitoring Change'] && <p className="text-sm">{result['Monitoring Change'].message} New candidates: {result['Monitoring Change'].added_candidates.length}; changed: {result['Monitoring Change'].changed_candidates.length}; removed: {result['Monitoring Change'].removed_candidates.length}.</p>}
    <p className="text-sm text-text-secondary">Name similarity is a matching score, not the probability that this is the same person or company. Missing details do not establish a mismatch.</p>
    {summary && <p className="text-sm">{summary.potential_matches} potential matches · {summary.excluded} excluded by conflicting DOB evidence</p>}
    {!evidence.length && <p className="text-sm">No candidate passed the name-matching rules.</p>}
    {evidence.map((candidate, i) => <details key={`${candidate.record_id || candidate.name}-${candidate.entity_type}-${i}`} className="border border-border rounded-lg p-3" open={i === 0}>
      <summary className="cursor-pointer text-sm font-medium">{candidate.name} · {candidate.source_type === 'peps' ? 'PEP' : 'Sanctions'} · {candidate.disposition === 'excluded' ? 'Excluded: DOB conflict' : 'Potential match'} · Name similarity {candidate.name_score}/100</summary>
      <div className="overflow-x-auto mt-3"><table className="w-full text-sm text-left"><thead><tr><th className="p-2">Detail</th><th className="p-2">Submitted</th><th className="p-2">Candidate</th><th className="p-2">Assessment</th></tr></thead>
        <tbody>
          <tr><th className="p-2">Name</th><td className="p-2">{search.searchName}</td><td className="p-2">{candidate.matched_name}</td><td className="p-2">{candidate.matched_alias ? 'Listed alias' : 'Listed name'}</td></tr>
          <tr><th className="p-2">DOB</th><td className="p-2">{search.searchDob || 'Not supplied'}</td><td className="p-2">{candidate.birth_date || 'Not available'}</td><td className="p-2">{candidate.dob_assessment.replace('_', ' ')}</td></tr>
          <tr><th className="p-2">Country</th><td className="p-2">{search.searchCountry || 'Not supplied'}</td><td className="p-2">{candidate.country || 'Not available'}</td><td className="p-2">{candidate.country_assessment}</td></tr>
        </tbody></table></div>
      <p className="text-sm mt-3">Source: {candidate.dataset || 'Not specified'} · Type: {candidate.entity_type}</p>
      {candidate.record_id && <p className="text-xs break-all mt-1">Source record: {candidate.record_id}</p>}
      <ul className="list-disc pl-5 mt-3 text-sm space-y-1">{candidate.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul>
    </details>)}
    {summary?.display_truncated && <p className="text-sm">Showing the first 50 candidates. The decision includes all evaluated candidates.</p>}
  </CardBody></Card>
}
