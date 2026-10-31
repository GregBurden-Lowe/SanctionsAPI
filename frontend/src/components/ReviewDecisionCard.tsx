import { Card, CardBody, CardHeader, CardTitle } from '@/components'
import type { ReviewDecision } from '@/types/api'

export function ReviewDecisionCard({ decision }: { decision: ReviewDecision }) {
  return <Card><CardHeader><CardTitle>{decision.decision_type === 'ai_auto_clear' ? 'Automatic AI clearance' : decision.decision_type === 'system_no_match' ? 'Re-screening resolution' : decision.decision_type === 'reused_candidate_decisions' ? 'Reused analyst decision' : 'Analyst decision'}</CardTitle></CardHeader>
    <CardBody className="space-y-3 text-sm">
      {decision.stale && <p className="font-semibold text-semantic-warning">This decision needs reassessment. Run a fresh screening before relying on it.</p>}
      <dl className="grid grid-cols-2 gap-2">
        <dt>Outcome</dt><dd>{decision.outcome}</dd><dt>Recorded by</dt><dd>{decision.actor}</dd>
        <dt>Reviewed at</dt><dd>{new Date(decision.decided_at).toLocaleString('en-GB')}</dd>
        <dt>Screening run</dt><dd className="break-all">{decision.run_id}</dd>
      </dl>
      <p className="whitespace-pre-wrap">{decision.notes}</p>
      <p className="text-text-secondary">This decision is separate from the engine findings. It does not change the screening date or extend screening validity.</p>
    </CardBody>
  </Card>
}
