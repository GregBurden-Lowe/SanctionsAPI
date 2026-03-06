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
import { markFalsePositive, opcheck, searchScreened } from '@/api/client'
import type { ScreenedEntity } from '@/types/api'
import { ResultCard } from '@/pages/ScreeningPage'
import { useAuth } from '@/context/AuthContext'

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
  const { user } = useAuth()
  const [searchName, setSearchName] = useState('')
  const [searchEntityKey, setSearchEntityKey] = useState('')
  const [searchBusinessReference, setSearchBusinessReference] = useState('')
  const [loading, setLoading] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [items, setItems] = useState<ScreenedEntity[]>([])
  const [detailRow, setDetailRow] = useState<ScreenedEntity | null>(null)
  const [overrideLoading, setOverrideLoading] = useState(false)
  const [overrideError, setOverrideError] = useState<string | null>(null)
  const [overrideSuccess, setOverrideSuccess] = useState<string | null>(null)
  const [rerunDob, setRerunDob] = useState('')
  const [rerunCountry, setRerunCountry] = useState('')
  const [rerunLoading, setRerunLoading] = useState(false)
  const [rerunError, setRerunError] = useState<string | null>(null)
  const [rerunSuccess, setRerunSuccess] = useState<string | null>(null)
  const [idModalKey, setIdModalKey] = useState<string | null>(null)

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    const nameTrim = searchName.trim()
    const keyTrim = searchEntityKey.trim()
    const businessReferenceTrim = searchBusinessReference.trim()
    if (!nameTrim && !keyTrim && !businessReferenceTrim) {
      setError('Provide at least one of name, entity key, or business reference.')
      return
    }
    setHasSearched(true)
    setLoading(true)
    try {
      const res = await searchScreened({
        name: nameTrim || undefined,
        entity_key: keyTrim || undefined,
        business_reference: businessReferenceTrim || undefined,
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

  const handleMarkFalsePositive = async () => {
    if (!detailRow) return
    const result = detailRow.result_json ?? {}
    const canOverride = Boolean(result['Is Sanctioned'] || result['Is PEP'])
    if (!canOverride) return
    const confirmed = window.confirm(
      'Mark this screening result as a false positive and clear the sanction outcome?',
    )
    if (!confirmed) return
    const reasonInput = window.prompt('Reason for false positive override (required):', '')
    if (reasonInput === null) return
    const reason = reasonInput.trim()
    if (!reason) {
      setOverrideError('Reason is required.')
      return
    }
    setOverrideLoading(true)
    setOverrideError(null)
    setOverrideSuccess(null)
    try {
      const res = await markFalsePositive(detailRow.entity_key, reason)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setOverrideError((data as { detail?: string }).detail ?? 'Failed to clear false positive.')
        return
      }
      const updatedResult = (data as { result?: ScreenedEntity['result_json'] }).result
      if (updatedResult) {
        setDetailRow((prev) =>
          prev
            ? {
                ...prev,
                status: 'Cleared - False Positive',
                risk_level: 'Cleared',
                confidence: 'Manual Review',
                score: 0,
                result_json: updatedResult,
              }
            : prev,
        )
        setItems((prev) =>
          prev.map((it) =>
            it.entity_key === detailRow.entity_key
              ? {
                  ...it,
                  status: 'Cleared - False Positive',
                  risk_level: 'Cleared',
                  confidence: 'Manual Review',
                  score: 0,
                  result_json: updatedResult,
                }
              : it,
          ),
        )
      }
      setOverrideSuccess('Marked as false positive and cleared.')
    } catch (err) {
      setOverrideError(err instanceof Error ? err.message : 'Failed to clear false positive.')
    } finally {
      setOverrideLoading(false)
    }
  }

  const handleOpenDetails = (row: ScreenedEntity) => {
    setDetailRow(row)
    setOverrideError(null)
    setOverrideSuccess(null)
    setRerunError(null)
    setRerunSuccess(null)
    setRerunDob(row.date_of_birth ?? '')
    setRerunCountry(row.country_input ?? '')
  }

  const handleRerunCheck = async () => {
    if (!detailRow) return
    const result = detailRow.result_json ?? {}
    const canRerun = Boolean(result['Is Sanctioned'] || result['Is PEP'])
    if (!canRerun) return

    const isPerson = detailRow.entity_type === 'Person'
    const isOrganization = detailRow.entity_type === 'Organization'
    const dobTrim = rerunDob.trim()
    const countryTrim = rerunCountry.trim()

    if (isPerson && !dobTrim) {
      setRerunError('Please enter date of birth before re-running.')
      return
    }
    if (isOrganization && !countryTrim) {
      setRerunError('Please enter country before re-running.')
      return
    }
    if (!detailRow.business_reference?.trim()) {
      setRerunError('Business reference is missing on this record; cannot re-run.')
      return
    }
    if (!detailRow.reason_for_check?.trim()) {
      setRerunError('Reason for check is missing on this record; cannot re-run.')
      return
    }

    setRerunLoading(true)
    setRerunError(null)
    setRerunSuccess(null)
    try {
      const res = await opcheck({
        name: detailRow.display_name,
        dob: isPerson ? dobTrim : detailRow.date_of_birth || null,
        country: isOrganization ? countryTrim : detailRow.country_input || null,
        entity_type: detailRow.entity_type,
        business_reference: detailRow.business_reference,
        reason_for_check: detailRow.reason_for_check as
          | 'Client Onboarding'
          | 'Claim Payment'
          | 'Business Partner Payment'
          | 'Business Partner Due Diligence'
          | 'Periodic Re-Screen'
          | 'Ad-Hoc Compliance Review',
        requestor: detailRow.last_requestor ?? null,
        search_backend: 'postgres_beta',
        rerun_entity_key: detailRow.entity_key,
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setRerunError((data as { detail?: string; message?: string; error?: string }).detail ?? (data as { detail?: string; message?: string; error?: string }).message ?? (data as { detail?: string; message?: string; error?: string }).error ?? 'Failed to re-run check.')
        return
      }
      if ((data as { status?: string }).status === 'queued') {
        setRerunError('Re-run was queued due to system load. Please refresh shortly.')
        return
      }

      const nextResult = data as ScreenedEntity['result_json']
      const nextStatus = nextResult['Check Summary']?.Status ?? detailRow.status
      const nextRiskLevel = nextResult['Risk Level'] ?? detailRow.risk_level
      const nextConfidence = nextResult.Confidence ?? detailRow.confidence
      const nextScore = Number(nextResult.Score ?? detailRow.score)

      const updatedRow: ScreenedEntity = {
        ...detailRow,
        date_of_birth: isPerson ? dobTrim : detailRow.date_of_birth,
        country_input: isOrganization ? countryTrim : detailRow.country_input,
        result_json: nextResult,
        status: nextStatus,
        risk_level: nextRiskLevel,
        confidence: nextConfidence,
        score: Number.isFinite(nextScore) ? nextScore : detailRow.score,
        last_screened_at: new Date().toISOString(),
      }

      setDetailRow(updatedRow)
      setItems((prev) => prev.map((it) => (it.entity_key === updatedRow.entity_key ? updatedRow : it)))
      setRerunSuccess('Re-run completed and record updated.')
    } catch (err) {
      setRerunError(err instanceof Error ? err.message : 'Failed to re-run check.')
    } finally {
      setRerunLoading(false)
    }
  }

  return (
    <div className="px-[26px] pt-[22px] pb-[26px]">
      <div className="max-w-[1600px] space-y-6">
        <SectionHeader title="Search database" />
        <Card>
          <CardHeader>
            <CardTitle>Search screened entities</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="text-sm text-text-secondary mb-4">
              Search by name (partial match), entity key (exact), or business reference (exact). Provide at least one.
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
              <Input
                label="Search by business reference"
                value={searchBusinessReference}
                onChange={(e) => setSearchBusinessReference(e.target.value)}
                placeholder="Exact business reference"
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
                          <Button type="button" variant="ghost" size="sm" onClick={() => setIdModalKey(row.entity_key)}>
                            Show ID
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
                            onClick={() => handleOpenDetails(row)}
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
        size="wide"
        footer={
          detailRow ? (
            <div className="flex items-center gap-2">
              {user?.is_admin &&
                Boolean(detailRow.result_json?.['Is Sanctioned'] || detailRow.result_json?.['Is PEP']) && (
                <Button
                  variant="secondary"
                  onClick={() => void handleMarkFalsePositive()}
                  disabled={overrideLoading}
                >
                  {overrideLoading ? 'Clearing…' : 'Mark false positive'}
                </Button>
                )}
              <Button variant="secondary" onClick={() => setDetailRow(null)}>
                Close
              </Button>
            </div>
          ) : null
        }
      >
        {detailRow && (
          <div className="space-y-4">
            {overrideError && <ErrorBox message={overrideError} />}
            {overrideSuccess && (
              <p className="text-sm text-semantic-success">{overrideSuccess}</p>
            )}
            {Boolean(detailRow.result_json?.['Is Sanctioned'] || detailRow.result_json?.['Is PEP']) && (
              <Card>
                <CardHeader>
                  <CardTitle>{detailRow.entity_type === 'Person' ? 'Refine with date of birth' : 'Refine with country'}</CardTitle>
                </CardHeader>
                <CardBody className="space-y-3">
                  <p className="text-sm text-text-secondary">
                    Re-run this check with additional details and keep the same entity key record.
                  </p>
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                    {detailRow.entity_type === 'Person' ? (
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
                    <Button type="button" onClick={() => void handleRerunCheck()} disabled={rerunLoading}>
                      {rerunLoading
                        ? 'Re-running…'
                        : detailRow.entity_type === 'Person'
                          ? 'Re-run with DoB'
                          : 'Re-run with country'}
                    </Button>
                  </div>
                  {rerunError && <ErrorBox message={rerunError} />}
                  {rerunSuccess && <p className="text-sm text-semantic-success">{rerunSuccess}</p>}
                </CardBody>
              </Card>
            )}
            <p className="text-sm text-text-secondary">
              <span className="font-medium">Entity key</span>{' '}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-auto p-0 text-xs align-baseline"
                onClick={() => setIdModalKey(detailRow.entity_key)}
              >
                Show ID
              </Button>
              {' · '}
              <span className="font-medium">Business reference</span> {detailRow.business_reference ?? '—'}
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
                searchCountry: detailRow.country_input ?? '',
                businessReference: detailRow.business_reference ?? '',
                reasonForCheck: detailRow.reason_for_check ?? '',
                requestor: detailRow.last_requestor ?? '',
              }}
            />
          </div>
        )}
      </Modal>

      <Modal
        isOpen={idModalKey !== null}
        onClose={() => setIdModalKey(null)}
        title="Entity ID"
        footer={
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={() => setIdModalKey(null)}>
              Close
            </Button>
            {idModalKey && (
              <Button onClick={() => void navigator.clipboard.writeText(idModalKey)}>
                Copy ID
              </Button>
            )}
          </div>
        }
      >
        {idModalKey && (
          <code className="block w-full text-xs bg-surface px-2 py-2 rounded break-all font-mono">{idModalKey}</code>
        )}
      </Modal>
    </div>
  )
}
