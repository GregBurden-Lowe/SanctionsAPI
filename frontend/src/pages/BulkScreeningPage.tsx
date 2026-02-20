import { useMemo, useState } from 'react'
import { Button, Card, CardBody, CardHeader, CardTitle, ErrorBox, Input, SectionHeader } from '@/components'
import { enqueueBulkScreening, type BulkScreeningItem } from '@/api/client'
import { useAuth } from '@/context/AuthContext'

type ParsedRow = {
  name: string
  dob?: string
  entity_type: 'Person' | 'Organization'
  requestor: string
}

function parseCsv(text: string, fallbackRequestor: string): { rows: ParsedRow[]; errors: string[] } {
  const lines = text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
  if (lines.length === 0) return { rows: [], errors: ['CSV is empty.'] }

  const first = lines[0].toLowerCase()
  const hasHeader = first.includes('name')
  const dataLines = hasHeader ? lines.slice(1) : lines
  const rows: ParsedRow[] = []
  const errors: string[] = []

  for (let i = 0; i < dataLines.length; i++) {
    const raw = dataLines[i]
    const cols = raw.split(',').map((c) => c.trim())
    const name = cols[0] ?? ''
    const dob = cols[1] ?? ''
    const entityRaw = (cols[2] ?? 'Person').toLowerCase()
    const requestor = cols[3] || fallbackRequestor

    if (!name) {
      errors.push(`Row ${i + 1}: name is required.`)
      continue
    }
    if (!requestor) {
      errors.push(`Row ${i + 1}: requestor is required (or set Requested By).`)
      continue
    }
    const entity_type: 'Person' | 'Organization' = entityRaw === 'organization' ? 'Organization' : 'Person'
    rows.push({
      name,
      dob: dob || undefined,
      entity_type,
      requestor,
    })
  }
  return { rows, errors }
}

export function BulkScreeningPage() {
  const { user } = useAuth()
  const [requestor, setRequestor] = useState((user?.email || user?.username || '').trim())
  const [csvText, setCsvText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ counts?: Record<string, number>; results?: Array<{ status: string; job_id?: string; error?: string }> } | null>(null)

  const parsed = useMemo(() => parseCsv(csvText, requestor.trim()), [csvText, requestor])

  const handleFile = async (file: File) => {
    const text = await file.text()
    setCsvText(text)
  }

  const handleSubmit = async () => {
    setError(null)
    setResult(null)
    const req = requestor.trim()
    if (!req) {
      setError('Requested By is required.')
      return
    }
    if (parsed.errors.length > 0) {
      setError(parsed.errors[0])
      return
    }
    if (parsed.rows.length === 0) {
      setError('No valid rows to submit.')
      return
    }
    if (parsed.rows.length > 500) {
      setError('Maximum 500 rows per upload.')
      return
    }
    setLoading(true)
    try {
      const payload: BulkScreeningItem[] = parsed.rows.map((r) => ({
        name: r.name,
        dob: r.dob ?? null,
        entity_type: r.entity_type,
        requestor: r.requestor,
      }))
      const res = await enqueueBulkScreening(payload)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError((data as { detail?: string }).detail ?? 'Bulk enqueue failed.')
        return
      }
      setResult(data as { counts?: Record<string, number>; results?: Array<{ status: string; job_id?: string; error?: string }> })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Bulk enqueue failed.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="px-10 pb-10">
      <div className="max-w-5xl space-y-6">
        <SectionHeader title="Bulk screening upload" />
        <Card>
          <CardHeader>
            <CardTitle>Upload CSV and enqueue checks</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            <p className="text-sm text-text-secondary">
              CSV columns: <code>name,dob,entity_type,requestor</code>. Header row optional. If <code>requestor</code> is blank, Requested By is used.
            </p>
            <Input
              label="Requested By"
              value={requestor}
              onChange={(e) => setRequestor(e.target.value)}
              placeholder="Name or email"
            />
            <div className="space-y-2">
              <label className="text-xs font-medium text-text-primary">CSV file</label>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) handleFile(f).catch(() => setError('Failed to read file.'))
                }}
                className="block w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-primary"
              />
            </div>
            <div className="space-y-2">
              <label className="text-xs font-medium text-text-primary">Or paste CSV</label>
              <textarea
                value={csvText}
                onChange={(e) => setCsvText(e.target.value)}
                rows={10}
                className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-primary font-mono"
                placeholder={'name,dob,entity_type,requestor\nVladimir Putin,07-10-1952,Person,\nACME Ltd,,Organization,'}
              />
            </div>
            {error && <ErrorBox message={error} />}
            <div className="flex items-center justify-between">
              <p className="text-xs text-text-muted">Valid rows: {parsed.rows.length}</p>
              <Button type="button" onClick={handleSubmit} disabled={loading}>
                {loading ? 'Submitting…' : 'Enqueue bulk screening'}
              </Button>
            </div>
          </CardBody>
        </Card>

        {result?.counts && (
          <Card>
            <CardHeader>
              <CardTitle>Bulk enqueue result</CardTitle>
            </CardHeader>
            <CardBody>
              <pre className="text-xs text-text-secondary overflow-x-auto whitespace-pre-wrap font-mono">
                {JSON.stringify(result.counts, null, 2)}
              </pre>
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  )
}
