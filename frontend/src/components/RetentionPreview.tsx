import { useState } from 'react'
import { Button, Card, CardBody, CardHeader, CardTitle, ErrorBox } from '@/components'
import { retentionPreview } from '@/api/client'

type Report = {
  generated_at: string
  comparison_before: string | null
  inventory: Array<{ table: string; label: string; date_basis: string; total: number; older_than_cutoff: number | null; storage_bytes: number }>
  active: Record<string, number>
  dependencies: Record<string, number> | null
  notes: string[]
}
const label = (value: string) => value.replace(/_/g, ' ')

export function RetentionPreview() {
  const [before, setBefore] = useState('')
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function load() {
    setLoading(true); setError(null); setReport(null)
    try {
      const response = await retentionPreview(before ? `${before}T00:00:00Z` : undefined)
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Unable to load retention preview.')
      setReport(data)
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to load retention preview.') }
    finally { setLoading(false) }
  }
  return <Card>
    <CardHeader><CardTitle>Retention preview</CardTitle></CardHeader>
    <CardBody className="space-y-4">
      <p className="text-sm text-text-secondary">Awaiting your organisation’s retention policy. This preview only counts records; it cannot delete them. Existing operational cleanup is separate.</p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">Optional comparison date (UTC)
          <input type="date" value={before} max={new Date().toISOString().slice(0, 10)} onChange={e => setBefore(e.target.value)} className="block rounded border border-border px-3 py-2" />
        </label>
        <Button type="button" variant="secondary" disabled={loading} onClick={() => void load()}>{loading ? 'Loading…' : 'Preview retention'}</Button>
      </div>
      {error && <ErrorBox message={error} />}
      {report && <>
        <p className="text-sm">Snapshot: {new Date(report.generated_at).toLocaleString('en-GB')}. {report.comparison_before ? `Comparing records dated before ${new Date(report.comparison_before).toISOString()}. This is not a deletion list.` : 'Inventory only; no retention period selected.'}</p>
        <div className="overflow-x-auto"><table className="w-full text-sm text-left">
          <thead><tr><th className="p-2">Records</th><th className="p-2">Total</th><th className="p-2">Older than date</th><th className="p-2">Storage including indexes</th></tr></thead>
          <tbody>{report.inventory.map(item => <tr key={item.table} className="border-t border-border">
            <td className="p-2">{item.label}</td><td className="p-2">{item.total.toLocaleString()}</td>
            <td className="p-2">{item.older_than_cutoff?.toLocaleString() ?? '—'}</td><td className="p-2">{(item.storage_bytes / 1048576).toFixed(1)} MB</td>
          </tr>)}</tbody>
        </table></div>
        <h4 className="font-medium">Active work and audit links</h4>
        <dl className="grid grid-cols-2 gap-2 text-sm">{Object.entries(report.active).map(([key, value]) => <div key={key}><dt className="capitalize">{label(key)}</dt><dd>{value.toLocaleString()}</dd></div>)}</dl>
        {report.dependencies && <><h4 className="font-medium">Older records requiring dependency checks</h4>
          <p className="text-sm text-text-secondary">These counts overlap. A record’s age alone does not establish whether it can be removed.</p>
          <dl className="grid grid-cols-2 gap-2 text-sm">{Object.entries(report.dependencies).map(([key, value]) => <div key={key}><dt className="capitalize">{label(key)}</dt><dd>{value.toLocaleString()}</dd></div>)}</dl>
        </>}
        <ul className="list-disc pl-5 text-sm text-text-secondary">{report.notes.map(note => <li key={note}>{note}</li>)}</ul>
      </>}
    </CardBody>
  </Card>
}
