import { useState } from 'react'
import { Button, Card, CardBody, CardHeader, CardTitle, ErrorBox } from '@/components'
import { exportAcceptanceCases } from '@/api/client'

export function AcceptanceExport() {
  const [reviewedLimit, setReviewedLimit] = useState(50)
  const [clearedLimit, setClearedLimit] = useState(25)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [downloaded, setDownloaded] = useState(false)
  async function generate() {
    setLoading(true); setError(null); setDownloaded(false)
    try {
      const response = await exportAcceptanceCases(reviewedLimit, clearedLimit)
      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Unable to generate acceptance cases.')
      }
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url; link.download = 'screening-acceptance-candidates.zip'
      document.body.appendChild(link); link.click(); link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      setDownloaded(true)
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to generate acceptance cases.') }
    finally { setLoading(false) }
  }
  return <Card>
    <CardHeader><CardTitle>Acceptance test cases</CardTitle></CardHeader>
    <CardBody className="space-y-4">
      <p className="text-sm text-text-secondary">Generate a reviewer pack from confirmed matches and false positives, with an optional sample of cleared searches. Pending external reviews are excluded. The most recent cases in each group are selected; fewer are exported when fewer are available.</p>
      <div className="flex flex-wrap items-end gap-4">
        <label className="text-sm">Maximum cases per reviewed outcome
          <select className="block mt-1 rounded border border-border bg-surface p-2" value={reviewedLimit} onChange={e => setReviewedLimit(Number(e.target.value))} disabled={loading}>
            {[25, 50, 100, 250].map(value => <option key={value} value={value}>{value} confirmed + {value} false positive</option>)}
          </select>
        </label>
        <label className="text-sm">Unreviewed cleared searches
          <select className="block mt-1 rounded border border-border bg-surface p-2" value={clearedLimit} onChange={e => setClearedLimit(Number(e.target.value))} disabled={loading}>
            {[0, 25, 50, 100, 250].map(value => <option key={value} value={value}>{value === 0 ? 'Do not include' : `Up to ${value}`}</option>)}
          </select>
        </label>
        <Button onClick={generate} disabled={loading}>{loading ? 'Generating…' : 'Generate acceptance pack'}</Button>
      </div>
      <p className="text-sm text-text-secondary">Downloads a ZIP containing a CSV for reviewer decisions, saved evidence, case counts and instructions. Expected outcomes are left blank for independent review. Historical decisions and cleared results are not acceptance sign-off.</p>
      <p className="text-xs text-text-muted">Contains names, dates of birth and reviewer notes. Generation does not run searches or change screening records.</p>
      {error && <ErrorBox message={error} />}
      {downloaded && <p role="status" className="text-sm">Pack generated and download started. Extract the ZIP and open cases.csv to begin the review.</p>}
    </CardBody>
  </Card>
}
