import { useState } from 'react'
import { Button, Card, CardHeader, CardTitle, CardBody, SectionHeader, ErrorBox } from '@/components'
import { clearScreeningData } from '@/api/client'

export function AdminPage() {
  const [clearing, setClearing] = useState(false)
  const [clearError, setClearError] = useState<string | null>(null)
  const [clearResponse, setClearResponse] = useState<{ status: string; screened_entities_removed: number; screening_jobs_removed: number } | null>(null)

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
    <div className="px-10 pb-10">
      <div className="max-w-2xl space-y-6">
        <SectionHeader title="Admin tools" />
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
