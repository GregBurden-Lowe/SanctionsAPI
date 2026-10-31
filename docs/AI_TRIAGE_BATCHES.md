# Accumulating AI suggestions and automatic Clear approval

Implemented locally; no live suggestions or production data were changed.

## Batch progression

Selection excludes recommendations already produced for the current immutable screening run **before** applying the batch limit. Pending, approved, rejected and superseded recommendations are not generated repeatedly for that run. Failed model calls have a 24-hour retry cooldown so they cannot starve the rest of the queue. A new screening run can receive a new recommendation; an old pending recommendation is superseded.

Manual runs retain their existing endpoint and batch size. The scheduled worker starts at or after the configured hour in `AI_TRIAGE_TIMEZONE` (default Europe/London), then processes further batches on successive polls until there is no eligible backlog. The default remains 25 per batch and 300 seconds between polls. A database session lock serializes manual/scheduled batch processing; a disconnected worker releases that lock.

The UI now has status views and Load more pagination beyond the initial 200 displayed suggestions. Approved suggestions, including automatic clearances, remain available for inspection. Automatic approvals are visibly labelled.

## Automatic clearance

`AI_TRIAGE_AUTO_CLEAR_ENABLED=true` is the default, as requested. Each newly generated effective `CLEAR` recommendation is applied immediately after generation. Each batch also processes up to its batch limit of existing pending Clear recommendations before generating new suggestions. Set the option to `false` to retain manual approval only.

Automatic approval:

1. Locks the recommendation and current screening record in one transaction.
2. Checks the recommendation is pending, its effective action is Clear, and its saved run ID and full engine-state hash still match the current run.
3. Refuses a stale manual override or a screening already resolved by another decision.
4. Rechecks guardrails, including every potential candidate and truncated evidence. A raw model Clear overridden to Investigate is not eligible. New model prompts include all displayed candidate evidence; they require all candidates to be resolved before suggesting Clear.
5. Writes an immutable `ai_auto_clear` review decision and marks the suggestion approved atomically. The original engine result remains unchanged. The effective response is `Cleared - False Positive`, preserving the existing API contract.

There is no additional confidence threshold: any effective Clear that passes those integrity/guardrail checks is eligible. Neither Investigate nor Unsure is automatically approved. The audit identifies `system:ai-auto-clear`, model, confidence, recommendation ID and rationale, and explicitly states that no human approval occurred. These decisions are not stored as human candidate resolutions or reused as human precedents.

Stale/replaced recommendations become superseded. Guardrail-blocked recommendations remain pending as Investigate. The existing manual approval endpoint now uses the same transaction/lock order, preventing a manual/automatic approval race from partially applying a decision.

## Release and verification

Migration 006 extends allowed review decision types and adds a current-run selection index. Schema installation remains repeatable after automatic decisions exist. Migration 005's allowed-type declaration includes the new type because this repository replays additive migrations during schema installation.

Apply the coordinated schema/API/AI-worker release before activation. Existing pending Clear recommendations are processed on the next manual or scheduled batch, not by the migration itself. No API endpoint or required request field changes are needed.

Regression coverage includes 60 recommendations accumulating as 25+25+10, pagination, fresh-run supersession, backlog approval, exactly-once clearance, rollback of both approval and clearance on failure, concurrent batch exclusion and secondary-candidate guardrails. Tests use a stub model and disposable PostgreSQL databases; no live LLM or production clearance is performed.
