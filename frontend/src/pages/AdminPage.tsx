import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, CardHeader, CardTitle, CardBody, SectionHeader, ErrorBox } from '@/components'
import {
  clearScreeningData,
  getAiTriageHealth,
  getMatchingConfig,
  getRescreenSummary,
  listAiTriageRuns,
  runAiTriage,
  updateMatchingConfig,
  type MatchingConfigResponse,
} from '@/api/client'
import type { AiTriageHealthResponse, AiTriageRun, RefreshRunSummaryResponse } from '@/types/api'

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
  const [aiHealth, setAiHealth] = useState<AiTriageHealthResponse | null>(null)
  const [aiRuns, setAiRuns] = useState<AiTriageRun[]>([])
  const [aiLoading, setAiLoading] = useState(false)
  const [aiRunning, setAiRunning] = useState(false)
  const [aiError, setAiError] = useState<string | null>(null)
  const [aiMessage, setAiMessage] = useState<string | null>(null)

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

  const loadAiTriage = async () => {
    setAiLoading(true)
    setAiError(null)
    setAiMessage(null)
    try {
      const [healthRes, runsRes] = await Promise.all([getAiTriageHealth(), listAiTriageRuns(10)])
      const healthData = await healthRes.json().catch(() => ({}))
      const runsData = await runsRes.json().catch(() => ({}))
      if (!healthRes.ok) {
        setAiError((healthData as { detail?: string }).detail ?? 'Failed to load AI triage health.')
        setAiHealth(null)
      } else {
        setAiHealth(healthData as AiTriageHealthResponse)
      }
      if (!runsRes.ok) {
        setAiError((runsData as { detail?: string }).detail ?? 'Failed to load AI triage runs.')
        setAiRuns([])
      } else {
        setAiRuns(((runsData as { items?: AiTriageRun[] }).items ?? []) as AiTriageRun[])
      }
    } catch (err) {
      setAiError(err instanceof Error ? err.message : 'Failed to load AI triage tools.')
      setAiHealth(null)
      setAiRuns([])
    } finally {
      setAiLoading(false)
    }
  }

  useEffect(() => {
    void loadAiTriage()
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

  const handleRunAiTriage = async () => {
    setAiRunning(true)
    setAiError(null)
    setAiMessage(null)
    try {
      const res = await runAiTriage(25)
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setAiError((data as { detail?: string }).detail ?? 'Failed to start AI triage run.')
        return
      }
      const typed = data as { run_id?: string; selected?: number; created?: number; skipped?: number; errors?: number }
      setAiMessage(
        `AI triage run started${typed.run_id ? ` (${typed.run_id})` : ''}. Selected ${(data as { selected_count?: number }).selected_count ?? typed.selected ?? 0}, created ${(data as { created_count?: number }).created_count ?? typed.created ?? 0}, skipped ${(data as { skipped_count?: number }).skipped_count ?? typed.skipped ?? 0}, errors ${(data as { error_count?: number }).error_count ?? typed.errors ?? 0}.`,
      )
      await loadAiTriage()
    } catch (err) {
      setAiError(err instanceof Error ? err.message : 'Failed to start AI triage run.')
    } finally {
      setAiRunning(false)
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
            <CardTitle>AI triage</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4">
            <p className="text-sm text-text-secondary">
              Run local Ollama triage for outstanding sanctions and PEP matches. Recommendations stay advisory until a human approves them in the AI Suggestions queue.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" variant="secondary" onClick={() => void loadAiTriage()} disabled={aiLoading || aiRunning}>
                {aiLoading ? 'Refreshing…' : 'Refresh AI status'}
              </Button>
              <Button type="button" onClick={() => void handleRunAiTriage()} disabled={aiRunning || aiLoading}>
                {aiRunning ? 'Running…' : 'Run AI triage now'}
              </Button>
            </div>
            {aiError && <ErrorBox message={aiError} />}
            {aiMessage && <p className="text-sm text-semantic-success">{aiMessage}</p>}
            <div className="rounded-lg border border-border bg-app p-4 text-sm text-text-secondary">
              <p><span className="font-semibold text-text-primary">Runtime:</span> {aiHealth?.runtime ?? '—'}</p>
              <p><span className="font-semibold text-text-primary">Configured model:</span> {aiHealth?.configured_model ?? '—'}</p>
              <p><span className="font-semibold text-text-primary">Reachable:</span> {aiHealth ? (aiHealth.reachable ? 'Yes' : 'No') : '—'}</p>
              <p><span className="font-semibold text-text-primary">Model present:</span> {aiHealth ? (aiHealth.model_present ? 'Yes' : 'No') : '—'}</p>
              <p><span className="font-semibold text-text-primary">Concurrency:</span> {aiHealth?.max_concurrency ?? '—'}</p>
              {aiHealth?.error && <p><span className="font-semibold text-text-primary">Error:</span> {aiHealth.error}</p>}
            </div>
            <div>
              <p className="mb-2 text-xs font-medium text-text-muted">Recent runs</p>
              {aiRuns.length === 0 ? (
                <p className="text-sm text-text-secondary">No AI triage runs recorded yet.</p>
              ) : (
                <div className="space-y-2">
                  {aiRuns.map((run) => (
                    <div key={run.run_id} className="rounded-lg border border-border bg-white px-3 py-3 text-sm text-text-secondary">
                      <p className="font-semibold text-text-primary">{run.trigger_type} run • {run.status}</p>
                      <p>Started: {new Date(run.started_at).toLocaleString()}</p>
                      <p>Model: {run.llm_model}</p>
                      <p>Selected/created/skipped/errors: {run.selected_count}/{run.created_count}/{run.skipped_count}/{run.error_count}</p>
                    </div>
                  ))}
                </div>
              )}
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
