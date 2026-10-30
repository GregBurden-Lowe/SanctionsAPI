# Acceptance candidate export

In **Admin tools → Acceptance test cases**, choose the maximum number of confirmed-match and false-positive cases, optionally include unreviewed cleared searches, then select **Generate acceptance pack**.

The ZIP contains:

- `cases.csv`: searched identity details, recorded candidate, previous reviewer outcome/reason and five blank acceptance fields for independent review.
- `evidence.json`: saved inputs/results, run/decision identifiers and available refresh metadata. CSV `case_id` links to this evidence.
- `manifest.json`: generation time, available/exported counts by category and selection method.
- `README.txt`: review instructions and evidence limitations.

The endpoint is `GET /admin/screening/acceptance-export?reviewed_limit=50&cleared_limit=25`, restricted to admins. The reviewed limit applies separately to each outcome group (1–250); the cleared limit is 0–250. Most recent cases are selected deterministically by review/screening date and case ID. It is not a random sample, and different reviewed runs of the same entity can appear. An empty selection returns a readable 404 message; invalid limits return 422. Downloads use `Cache-Control: no-store`.

Saved analyst decisions are joined to their immutable runs, including runs that are no longer current. Current-state reviewed cases provide a fallback when no saved decision exists for that run. These are clearly labelled `current_state`; their surviving details cannot reconstruct overwritten historical inputs, engine findings or review evidence. Cleared sampling uses current, unreviewed cleared records only, excluding explicit manual overrides. Pending external reviews and system no-match resolutions are not acceptance labels. Historical engine inputs are used where present so partial DOBs are not silently replaced by the current DATE column.

Generation runs in a read-only repeatable-read database transaction with a per-statement timeout. It does not run screening, alter reviews, recover requests or delete data. The full saved evidence remains in JSON; CSV values that could execute as spreadsheet formulas are apostrophe-prefixed. Treat the pack as personal data and use the organisation's approved handling arrangements.

Expected outcomes are deliberately blank. A reviewer must verify the relevant watchlist entries and source version before acceptance. Stored refresh metadata is not yet a guaranteed exact source archive. The pack helps assemble a test corpus; it does not establish matching accuracy or sign off release.

Requires the application history migrations already shipped with this update. No additional migration is introduced by this export.

## Local verification

Database/HTTP regression tests cover access control, bounds, empty selections, archive contents, counts, category exclusion, preserved old decisions, legacy fallback, optional cleared samples, formula-like notes and unchanged database rows. The browser download controls were checked with a synthetic local fixture. No production data was exported.
