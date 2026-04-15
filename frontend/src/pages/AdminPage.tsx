import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, CardHeader, CardTitle, CardBody, SectionHeader, ErrorBox } from '@/components'
import { clearScreeningData, getMatchingConfig, getRescreenSummary, updateMatchingConfig, type MatchingConfigResponse } from '@/api/client'
import type { RefreshRunSummaryResponse } from '@/types/api'

export function AdminPage() {
  const navigate = useNavigate()
  const [clearing, setClearing] = useState(false)
  const [clearError, setClearError] = useState<string | null>(null)
  const [clearResponse, setClearResponse] = useState<{ status: string; screened_entities_removed: number; screening_jobs_removed: number } | null>(null)
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [summaryError, setSummaryError] = useState<string | null>(null)
  const [summary, setSummary] = useState<RefreshRunSummaryResponse | null>(null)
  const [matchingConfig, setMatchingConfig] = useState<MatchingConfigResponse | null>(null)
  const [matchingConfigText, setMatchingConfigText] = useState('')
  const [matchingLoading, setMatchingLoading] = useState(false)
  const [matchingSaving, setMatchingSaving] = useState(false)
  const [matchingError, setMatchingError] = useState<string | null>(null)
  const [matchingSaved, setMatchingSaved] = useState<string | null>(null)

  const loadSummary = async () => {
    setSummaryLoading(true)
    setSummaryError(null)
    try {
      const res = await getRescreenSummary(14)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setSummaryError((data as { detail?: string }).detail ?? 'Failed to load rescreen summary.')
        setSummary(null)
        return
      }
      setSummary(data as RefreshRunSummaryResponse)
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : 'Failed to load rescreen summary.')
      setSummary(null)
    } finally {
      setSummaryLoading(false)
    }
  }

  useEffect(() => {
    void loadSummary()
  }, [])

  const loadMatchingConfig = async () => {
    setMatchingLoading(true)
    setMatchingError(null)
    setMatchingSaved(null)
    try {
      const res = await getMatchingConfig()
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setMatchingError((data as { detail?: string }).detail ?? 'Failed to load matching configuration.')
        setMatchingConfig(null)
        return
      }
      const typed = data as MatchingConfigResponse
      setMatchingConfig(typed)
      setMatchingConfigText((typed.custom_generic_words ?? []).join('\n'))
    } catch (err) {
      setMatchingError(err instanceof Error ? err.message : 'Failed to load matching configuration.')
      setMatchingConfig(null)
    } finally {
      setMatchingLoading(false)
    }
  }

  useEffect(() => {
    void loadMatchingConfig()
  }, [])

  const handleSaveMatchingConfig = async () => {
    setMatchingSaving(true)
    setMatchingError(null)
    setMatchingSaved(null)
    const words = matchingConfigText
      .split(/[\n,]+/)
      .map((word) => word.trim())
      .filter(Boolean)
    try {
      const res = await updateMatchingConfig(words)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setMatchingError((data as { detail?: string }).detail ?? 'Failed to save matching configuration.')
        return
      }
      const typed = data as MatchingConfigResponse
      setMatchingConfig(typed)
      setMatchingConfigText((typed.custom_generic_words ?? []).join('\n'))
      setMatchingSaved('Excluded-word settings saved.')
    } catch (err) {
      setMatchingError(err instanceof Error ? err.message : 'Failed to save matching configuration.')
    } finally {
      setMatchingSaving(false)
    }
  }

  const handleClearScreeningData = async () => {
    setClearError(null)
    setClearResponse(null)
    const ok = window.confirm(
      'This will permanently delete all screened entities and queued/completed jobs. User accounts are NOT deleted. Continue?'
    )
    if (!ok) return
    setClearing(true)
    try {
      const res = await clearScreeningData()
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setClearError((data as { detail?: string }).detail ?? 'Failed to clear screening data.')
        return
      }
      setClearResponse(data as { status: string; screened_entities_removed: number; screening_jobs_removed: number })
    } catch (err) {
      setClearError(err instanceof Error ? err.message : 'Network error.')
    } finally {
      setClearing(false)
    }
  }

  return (
    <div className="px-[26px] pt-[22px] pb-[26px]">
      <div className="max-w-2xl space-y-6">
        <SectionHeader title="Admin tools" />
        <Card>
          <CardHeader>
            <CardTitle>Data refresh mode</CardTitle>
          </CardHeader>
          <CardBody className="space-y-3">
            <p className="text-sm text-text-secondary">
              OpenSanctions refresh is now API/cron driven and syncs Postgres by default. Use your 22:00 droplet cron job to keep watchlist tables current.
            </p>
            <div>
              <div className="flex items-center gap-2">
                <Button type="button" variant="secondary" onClick={() => navigate('/admin/docs')}>
                  Open API docs
                </Button>
                <Button type="button" variant="secondary" onClick={() => navigate('/admin/api-keys')}>
                  Manage API keys
                </Button>
              </div>
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Daily re-screen summary</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            <Button type="button" variant="secondary" onClick={loadSummary} disabled={summaryLoading}>
              {summaryLoading ? 'Refreshing…' : 'Refresh summary'}
            </Button>
            {summaryError && <ErrorBox message={summaryError} />}
            {summary?.latest && (
              <div className="rounded-lg border border-border bg-app p-4">
                <p className="text-xs font-medium text-text-muted mb-2">Latest refresh run</p>
                <pre className="text-xs text-text-secondary overflow-x-auto whitespace-pre-wrap font-mono">
                  {JSON.stringify(
                    {
                      ran_at: summary.latest.ran_at,
                      uk_changed: summary.latest.uk_changed,
                      delta: {
                        added: summary.latest.delta_added,
                        removed: summary.latest.delta_removed,
                        changed: summary.latest.delta_changed,
                      },
                      rescreen: {
                        candidate_count: summary.latest.candidate_count,
                        queued_count: summary.latest.queued_count,
                        already_pending_count: summary.latest.already_pending_count,
                        failed_count: summary.latest.failed_count,
                      },
                      transitions: summary.latest_transitions,
                    },
                    null,
                    2,
                  )}
                </pre>
              </div>
            )}
            {!summaryLoading && !summary?.latest && !summaryError && (
              <p className="text-sm text-text-secondary">No refresh runs recorded yet.</p>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Matching excluded words</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            <p className="text-sm text-text-secondary">
              Tune organization matching as MI develops. Protected legal suffixes stay built in. Add extra generic organization words here to stop them carrying too much weight in matching.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" variant="secondary" onClick={() => void loadMatchingConfig()} disabled={matchingLoading || matchingSaving}>
                {matchingLoading ? 'Refreshing…' : 'Refresh words'}
              </Button>
              <Button type="button" onClick={() => void handleSaveMatchingConfig()} disabled={matchingSaving || matchingLoading}>
                {matchingSaving ? 'Saving…' : 'Save excluded words'}
              </Button>
            </div>
            {matchingError && <ErrorBox message={matchingError} />}
            {matchingSaved && <p className="text-sm text-semantic-success">{matchingSaved}</p>}
            <div>
              <p className="mb-2 text-xs font-medium text-text-muted">Protected legal suffixes</p>
              <div className="flex flex-wrap gap-2">
                {(matchingConfig?.protected_legal_suffixes ?? []).map((word) => (
                  <span key={word} className="rounded-full border border-border bg-app px-2.5 py-1 text-xs text-text-secondary">
                    {word}
                  </span>
                ))}
              </div>
            </div>
            <div>
              <label htmlFor="matching-custom-words" className="mb-2 block text-xs font-medium text-text-muted">
                Custom generic excluded words
              </label>
              <textarea
                id="matching-custom-words"
                value={matchingConfigText}
                onChange={(e) => setMatchingConfigText(e.target.value)}
                className="min-h-[180px] w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/15"
                placeholder={'partners\nproperty\nmanagement'}
              />
              <p className="mt-2 text-xs text-text-secondary">
                Enter one word per line or separate with commas. These are added to the built-in generic-word list used for organization matching.
              </p>
            </div>
            <div>
              <p className="mb-2 text-xs font-medium text-text-muted">Effective generic excluded words</p>
              <div className="flex flex-wrap gap-2">
                {(matchingConfig?.effective_generic_words ?? []).map((word) => (
                  <span key={word} className="rounded-full border border-border bg-app px-2.5 py-1 text-xs text-text-secondary">
                    {word}
                  </span>
                ))}
              </div>
            </div>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Testing tools</CardTitle>
          </CardHeader>
          <CardBody>
            <p className="text-sm text-text-secondary mb-4">
              Clear screening cache and queue so checks run fresh. This action does not delete users.
            </p>
            <Button
              type="button"
              variant="secondary"
              onClick={handleClearScreeningData}
              disabled={clearing}
            >
              {clearing ? 'Clearing…' : 'Clear screening data (testing)'}
            </Button>
            {clearError && (
              <div className="mt-4">
                <ErrorBox message={clearError} />
              </div>
            )}
            {clearResponse && (
              <div className="mt-4 rounded-lg border border-border bg-app p-4">
                <p className="text-xs font-medium text-text-muted mb-2">Clear result</p>
                <pre className="text-xs text-text-secondary overflow-x-auto whitespace-pre-wrap font-mono">
                  {JSON.stringify(clearResponse, null, 2)}
                </pre>
              </div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
