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
} from '@/components'
import { searchScreened } from '@/api/client'
import type { ScreenedEntity } from '@/types/api'
import { ResultCard } from '@/pages/ScreeningPage'

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: 'short',
      timeStyle: 'short',
    })
  } catch {
    return iso
  }
}

export function SearchDatabasePage() {
  const [searchName, setSearchName] = useState('')
  const [searchEntityKey, setSearchEntityKey] = useState('')
  const [loading, setLoading] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [items, setItems] = useState<ScreenedEntity[]>([])
  const [detailRow, setDetailRow] = useState<ScreenedEntity | null>(null)

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    const nameTrim = searchName.trim()
    const keyTrim = searchEntityKey.trim()
    if (!nameTrim && !keyTrim) {
      setError('Provide at least one of name or entity key.')
      return
    }
    setHasSearched(true)
    setLoading(true)
    try {
      const res = await searchScreened({
        name: nameTrim || undefined,
        entity_key: keyTrim || undefined,
        limit: 50,
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.detail ?? data.message ?? 'Search failed.')
        return
      }
      setItems(data.items ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed.')
      setItems([])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="px-10 pb-10">
      <div className="max-w-6xl space-y-6">
        <SectionHeader title="Search database" meta="Stored screening evidence" />
        <Card>
          <CardHeader>
            <CardTitle>Search screened entities</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="text-sm text-text-secondary mb-4">
              Search by name (partial match) or entity key (exact). Provide at least one.
            </p>
            <form onSubmit={handleSearch} className="space-y-4">
              <Input
                label="Search by name"
                value={searchName}
                onChange={(e) => setSearchName(e.target.value)}
                placeholder="e.g. Smith or Acme"
              />
              <Input
                label="Search by entity key"
                value={searchEntityKey}
                onChange={(e) => setSearchEntityKey(e.target.value)}
                placeholder="Exact entity key from a screening"
              />
              {error && <ErrorBox message={error} />}
              <Button type="submit" disabled={loading}>
                {loading ? 'Searching…' : 'Search'}
              </Button>
            </form>
          </CardBody>
        </Card>

        {items.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Results ({items.length})</CardTitle>
            </CardHeader>
            <CardBody>
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead>
                    <tr className="border-b border-border/80">
                      <th className="py-2 pr-4 font-medium text-text-primary">Entity key</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Name</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Type</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Requestor</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Last screened</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Status</th>
                      <th className="py-2 pr-4 font-medium text-text-primary">Risk</th>
                      <th className="py-2 font-medium text-text-primary">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((row) => (
                      <tr key={row.entity_key} className="border-b border-border/70 hover:bg-muted/40">
                        <td className="py-2 pr-4">
                          <code className="text-xs bg-surface px-1 rounded break-all font-mono">
                            {row.entity_key}
                          </code>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="ml-1 text-xs"
                            onClick={() => navigator.clipboard.writeText(row.entity_key)}
                          >
                            Copy
                          </Button>
                        </td>
                        <td className="py-2 pr-4 text-text-secondary">{row.display_name}</td>
                        <td className="py-2 pr-4 text-text-secondary">{row.entity_type}</td>
                        <td className="py-2 pr-4 text-text-muted">{row.last_requestor ?? '—'}</td>
                        <td className="py-2 pr-4 text-text-muted">{formatDate(row.last_screened_at)}</td>
                        <td className="py-2 pr-4 text-text-secondary">{row.status}</td>
                        <td className="py-2 pr-4 text-text-secondary">{row.risk_level}</td>
                        <td className="py-2">
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            onClick={() => setDetailRow(row)}
                          >
                            View details
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardBody>
          </Card>
        )}

        {!loading && hasSearched && !error && items.length === 0 && (
          <p className="text-sm text-text-secondary">No screenings match your search.</p>
        )}
      </div>

      <Modal
        isOpen={detailRow !== null}
        onClose={() => setDetailRow(null)}
        title="Screening details"
        footer={
          detailRow ? (
            <Button variant="secondary" onClick={() => setDetailRow(null)}>
              Close
            </Button>
          ) : null
        }
      >
        {detailRow && (
          <div className="space-y-4">
            <p className="text-sm text-text-secondary">
              <span className="font-medium">Entity key</span>{' '}
              <code className="text-xs bg-surface px-1 rounded">{detailRow.entity_key}</code>
              {' · '}
              <span className="font-medium">Requestor</span> {detailRow.last_requestor ?? '—'}
              {' · '}
              <span className="font-medium">Last screened</span> {formatDate(detailRow.last_screened_at)}
            </p>
            <ResultCard
              result={{ ...detailRow.result_json, entity_key: detailRow.entity_key }}
              searchDetails={{
                searchName: detailRow.display_name,
                entityType: detailRow.entity_type,
                searchDob: detailRow.date_of_birth ?? '',
                requestor: detailRow.last_requestor ?? '',
              }}
            />
          </div>
        )}
      </Modal>
    </div>
  )
}
