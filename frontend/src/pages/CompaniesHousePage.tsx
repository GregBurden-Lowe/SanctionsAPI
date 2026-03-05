import { useState } from 'react'
import { Button, Card, CardBody, CardHeader, CardTitle, ErrorBox, Input, SectionHeader } from '@/components'
import {
  enqueueBulkScreening,
  getCompaniesHouseScreenBundle,
  searchCompaniesHouse,
  type BulkScreeningItem,
  type CompaniesHouseScreeningBundle,
  type CompaniesHouseSearchItem,
} from '@/api/client'
import { useAuth } from '@/context/AuthContext'

const REASON_OPTIONS: BulkScreeningItem['reason_for_check'][] = [
  'Client Onboarding',
  'Claim Payment',
  'Business Partner Payment',
  'Business Partner Due Diligence',
  'Periodic Re-Screen',
  'Ad-Hoc Compliance Review',
]

function riskTone(level: string | null | undefined): string {
  const v = (level || '').toUpperCase()
  if (v === 'HIGH') return 'text-semantic-error'
  if (v === 'MEDIUM') return 'text-semantic-warning'
  return 'text-semantic-success'
}

export function CompaniesHousePage() {
  const { user } = useAuth()
  const [query, setQuery] = useState('')
  const [loadingSearch, setLoadingSearch] = useState(false)
  const [loadingBundle, setLoadingBundle] = useState(false)
  const [loadingQueue, setLoadingQueue] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [queueMessage, setQueueMessage] = useState<string | null>(null)
  const [items, setItems] = useState<CompaniesHouseSearchItem[]>([])
  const [bundle, setBundle] = useState<CompaniesHouseScreeningBundle | null>(null)
  const [loadedCompanyNumber, setLoadedCompanyNumber] = useState<string | null>(null)
  const [businessReference, setBusinessReference] = useState('')
  const [reasonForCheck, setReasonForCheck] = useState<BulkScreeningItem['reason_for_check']>('Ad-Hoc Compliance Review')

  const runSearch = async () => {
    const q = query.trim()
    if (!q) return
    setLoadingSearch(true)
    setError(null)
    setQueueMessage(null)
    setBundle(null)
    setLoadedCompanyNumber(null)
    try {
      const res = await searchCompaniesHouse(q)
      const data = (await res.json().catch(() => ({}))) as { items?: CompaniesHouseSearchItem[]; detail?: string }
      if (!res.ok) {
        setItems([])
        setError(data.detail || 'Companies House search failed.')
        return
      }
      setItems(data.items ?? [])
    } catch (e) {
      setItems([])
      setError(e instanceof Error ? e.message : 'Companies House search failed.')
    } finally {
      setLoadingSearch(false)
    }
  }

  const loadBundle = async (companyNumber: string | null) => {
    const num = (companyNumber || '').trim()
    if (!num) return
    setLoadingBundle(true)
    setError(null)
    try {
      const res = await getCompaniesHouseScreenBundle(num)
      const data = (await res.json().catch(() => ({}))) as CompaniesHouseScreeningBundle & { detail?: string }
      if (!res.ok) {
        setBundle(null)
        setError(data.detail || 'Failed to load company screening bundle.')
        return
      }
      setBundle(data)
      setLoadedCompanyNumber(num)
      setBusinessReference(`CH-${num}`)
    } catch (e) {
      setBundle(null)
      setError(e instanceof Error ? e.message : 'Failed to load company screening bundle.')
    } finally {
      setLoadingBundle(false)
    }
  }

  const enqueueFromBundle = async (mode: 'company' | 'officers' | 'all') => {
    if (!bundle) return
    const requestor = (user?.username || '').trim()
    const reference = businessReference.trim()
    if (!requestor) {
      setError('No signed-in user available for requestor.')
      return
    }
    if (!reference) {
      setError('Business reference is required.')
      return
    }
    const requests: BulkScreeningItem[] = []
    if (mode === 'company' || mode === 'all') {
      const companyName = (bundle.company.company_name || '').trim()
      if (companyName) {
        requests.push({
          name: companyName,
          entity_type: 'Organization',
          requestor,
          business_reference: reference,
          reason_for_check: reasonForCheck,
        })
      }
    }
    if (mode === 'officers' || mode === 'all') {
      for (const officer of bundle.officers) {
        if ((officer.status || '').toLowerCase() !== 'active') continue
        const name = (officer.name || '').trim()
        if (!name) continue
        const year = officer.date_of_birth?.year
        requests.push({
          name,
          dob: typeof year === 'number' ? String(year) : null,
          entity_type: 'Person',
          requestor,
          business_reference: reference,
          reason_for_check: reasonForCheck,
        })
      }
    }
    if (requests.length === 0) {
      setError('No valid company/officer names available to queue.')
      return
    }
    setLoadingQueue(true)
    setError(null)
    setQueueMessage(null)
    try {
      const res = await enqueueBulkScreening(requests)
      const data = (await res.json().catch(() => ({}))) as { detail?: string; results?: Array<{ status?: string }> }
      if (!res.ok) {
        setError(data.detail || 'Failed to queue screening jobs.')
        return
      }
      const statuses = (data.results || []).reduce<Record<string, number>>((acc, row) => {
        const key = row.status || 'unknown'
        acc[key] = (acc[key] || 0) + 1
        return acc
      }, {})
      const summary = Object.entries(statuses)
        .map(([k, v]) => `${k}: ${v}`)
        .join(' · ')
      setQueueMessage(`Queued screening request(s). ${summary}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to queue screening jobs.')
    } finally {
      setLoadingQueue(false)
    }
  }

  return (
    <div className="px-10 pb-10">
      <div className="max-w-7xl space-y-6">
        <SectionHeader title="Companies House" />

        <Card>
          <CardHeader>
            <CardTitle>Search companies</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            <div className="flex gap-3">
              <Input
                label="Company name, number or postcode"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="e.g. The Lettings Hub or SW1A 1AA"
              />
              <div className="pt-7">
                <Button type="button" onClick={() => void runSearch()} disabled={loadingSearch}>
                  {loadingSearch ? 'Searching…' : 'Search'}
                </Button>
              </div>
            </div>
            {error && <ErrorBox message={error} />}
            {queueMessage && <p className="text-sm text-semantic-success">{queueMessage}</p>}
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <CardTitle>Results ({items.length})</CardTitle>
              {loadedCompanyNumber && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setLoadedCompanyNumber(null)
                    setBundle(null)
                  }}
                >
                  Back to results
                </Button>
              )}
            </div>
          </CardHeader>
          {!loadedCompanyNumber && <CardBody>
            {items.length === 0 ? (
              <p className="text-sm text-text-secondary">No results yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead>
                    <tr className="border-b border-border/80">
                      <th className="py-2 pr-4 font-medium text-text-primary">Company</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Number</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Status</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Created</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Address</th>
                      <th className="py-2 font-medium text-text-primary">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((row, idx) => (
                      <tr key={`${row.company_number || 'unknown'}-${idx}`} className="border-b border-border/70 hover:bg-muted/40">
                        <td className="py-2 pr-4 text-text-secondary">{row.company_name || '—'}</td>
                        <td className="py-2 pr-4 text-text-secondary">{row.company_number || '—'}</td>
                        <td className="py-2 pr-4 text-text-secondary">{row.company_status || '—'}</td>
                        <td className="py-2 pr-4 text-text-secondary">{row.date_of_creation || '—'}</td>
                        <td className="py-2 pr-4 text-text-secondary">{row.address_snippet || '—'}</td>
                        <td className="py-2">
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            disabled={loadingBundle || !row.company_number}
                            onClick={() => void loadBundle(row.company_number)}
                          >
                            {loadingBundle ? 'Loading…' : 'Load'}
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardBody>}
        </Card>

        {bundle && (
          <div className="grid grid-cols-1 xl:grid-cols-12 gap-6">
            <div className="xl:col-span-6 space-y-6">
              <Card>
                <CardHeader>
                  <CardTitle>Queue sanctions screening</CardTitle>
                </CardHeader>
                <CardBody className="space-y-3">
                  <Input
                    label="Business reference"
                    value={businessReference}
                    onChange={(e) => setBusinessReference(e.target.value)}
                    placeholder="Required reference"
                  />
                  <div>
                    <label htmlFor="ch-reason" className="block text-xs font-medium text-text-primary mb-1">
                      Reason for check
                    </label>
                    <select
                      id="ch-reason"
                      className="w-full h-10 rounded-lg border border-border bg-surface px-3 text-sm text-text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/15"
                      value={reasonForCheck}
                      onChange={(e) => setReasonForCheck(e.target.value as BulkScreeningItem['reason_for_check'])}
                    >
                      {REASON_OPTIONS.map((v) => (
                        <option key={v} value={v}>
                          {v}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" variant="secondary" onClick={() => void enqueueFromBundle('company')} disabled={loadingQueue}>
                      {loadingQueue ? 'Queueing…' : 'Queue company'}
                    </Button>
                    <Button type="button" variant="secondary" onClick={() => void enqueueFromBundle('officers')} disabled={loadingQueue}>
                      {loadingQueue ? 'Queueing…' : 'Queue officers'}
                    </Button>
                    <Button type="button" onClick={() => void enqueueFromBundle('all')} disabled={loadingQueue}>
                      {loadingQueue ? 'Queueing…' : 'Queue company + officers'}
                    </Button>
                  </div>
                </CardBody>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Company profile</CardTitle>
                </CardHeader>
                <CardBody className="space-y-2 text-sm">
                  <p><span className="text-text-muted">Name:</span> {bundle.company.company_name || '—'}</p>
                  <p><span className="text-text-muted">Number:</span> {bundle.company.company_number || '—'}</p>
                  <p><span className="text-text-muted">Status:</span> {bundle.company.company_status || '—'}</p>
                  <p><span className="text-text-muted">Created:</span> {bundle.company.date_of_creation || '—'}</p>
                </CardBody>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Risk summary</CardTitle>
                </CardHeader>
                <CardBody className="space-y-2 text-sm">
                  <p className={riskTone(bundle.shell_risk?.risk_level)}><span className="text-text-muted">Shell risk:</span> {bundle.shell_risk?.risk_level || '—'} (score {bundle.shell_risk?.score ?? 0})</p>
                  <p className={riskTone(bundle.address_risk?.risk_level)}><span className="text-text-muted">Address risk:</span> {bundle.address_risk?.risk_level || '—'} ({bundle.address_risk?.company_count ?? 0} companies)</p>
                  <p className={riskTone(bundle.age_risk?.risk_level)}><span className="text-text-muted">Age risk:</span> {bundle.age_risk?.risk_level || '—'} ({bundle.age_risk?.age_months ?? '—'} months)</p>
                  <p><span className="text-text-muted">Director turnover:</span> {bundle.director_turnover?.rapid_turnover ? 'Rapid' : 'Normal'} ({bundle.director_turnover?.resigned_last_12_months ?? 0} in 12 months)</p>
                </CardBody>
              </Card>
            </div>
            <div className="xl:col-span-6 space-y-6">
              <Card>
                <CardHeader>
                  <CardTitle>Officers ({bundle.officers.length})</CardTitle>
                </CardHeader>
                <CardBody>
                  <div className="space-y-2">
                    {bundle.officers.map((o, idx) => (
                      <div key={`${o.name || 'officer'}-${idx}`} className="rounded-lg border border-border bg-app/70 px-3 py-2 text-sm">
                        <div className="font-medium text-text-primary">{o.name || 'Unknown'}</div>
                        <div className="text-text-secondary text-xs">{o.officer_role || '—'} · Appointed {o.appointed_on || '—'}</div>
                        {o.risk && (
                          <div className={`text-xs mt-1 ${riskTone(o.risk.risk_level)}`}>
                            Director risk: {o.risk.risk_level} · dissolved {o.risk.dissolved_companies} · total {o.risk.total_companies}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </CardBody>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Insolvency</CardTitle>
                </CardHeader>
                <CardBody className="text-sm">
                  {!bundle.insolvency || bundle.insolvency.cases.length === 0 ? (
                    <p className="text-text-secondary">No insolvency cases reported.</p>
                  ) : (
                    <div className="space-y-2">
                      {bundle.insolvency.cases.map((c, idx) => (
                        <div key={`${c.type || 'case'}-${idx}`} className="rounded-lg border border-border bg-app/70 px-3 py-2">
                          <div className="font-medium text-text-primary">{c.type || 'Unknown case type'}</div>
                          <div className="text-xs text-text-secondary">Start date: {c.case_start_date || '—'}</div>
                          <div className="text-xs text-text-secondary">Practitioners: {(c.practitioners || []).join(', ') || '—'}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </CardBody>
              </Card>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
