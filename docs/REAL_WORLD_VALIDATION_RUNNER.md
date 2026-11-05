# Replay an acceptance candidate pack locally

`scripts/validate_acceptance.py` checks pack IDs/counts and replays the exported identity inputs through the current PostgreSQL matcher, using a disposable local database loaded from an explicitly supplied Parquet snapshot. It can also replay a targeted subset through the Parquet matcher. Candidate-cap failures and changed formerly-clear results are selected first for those secondary checks.

For each case run through both backends, the private report compares the candidate evidence as well as the overall outcome. This can expose a missing candidate even when both backends raised an alert. It identifies records by provider ID, or by source-evidence hash when no provider ID exists, and compares type, disposition, matched name, score and multiplicity; PostgreSQL-only surrogate IDs from older snapshots are not treated as provider IDs. A failed backend, truncated evidence or missing usable identity makes the comparison incomplete, not a parity pass. Run all cases with `--parquet-count` at least equal to the number of cases, up to the 1,000-case limit. Selection rotates through all remaining case categories after prioritising incomplete and changed formerly-clear cases.

```bash
SANCTIONS_TEST_ADMIN_DSN=postgresql:///postgres python scripts/validate_acceptance.py \
  --pack /absolute/path/to/screening-acceptance-candidates \
  --snapshot /absolute/path/to/opensanctions.parquet \
  --output /absolute/path/outside/repository/new-validation-directory \
  --parquet-count 10
```

For a later export, add `--only-new-against /absolute/path/to/earlier-pack` to replay only new or changed records. Comparison includes the complete parsed evidence and acceptance fields, not just IDs. The summary records both packs' hashes, original count and skipped count. Keep prior results when reporting a combined corpus, deduplicate by case ID, and confirm snapshot/configuration equality before aggregating runs. A larger UI limit does not guarantee more exported rows; check the manifest's actual counts.

The output directory must be new and outside the repository. It contains private `results.json` (checkpointed after each case) and `summary.json`. The local DB is dropped in normal completion and handled failure; a host/process crash may require removal of the specifically named `sanctions_acceptance_*` database. The database contains watchlist rows, not saved screening requests. The runner never uses `DATABASE_URL`, fetches provider data, refreshes sources, records searches or updates reviews. Raw inputs, notes, candidate results and reports must not be committed.

The report records source SHA-256, file time, row count/columns, matching configuration and per-result rules fingerprints. A file timestamp is not proof of a publication date. Surrogate row IDs used for a legacy snapshot are not provider identifiers. PostgreSQL uses the same snapshot rows rather than an independently refreshed list, so the replay cannot establish equivalence with deployed sources or configuration.

The replay loads snapshot rows directly and is not a rehearsal of the production import pipeline. Production import now validates and preserves encoded aliases; historical imports may have replaced quotation marks, so compare actual deployed snapshot versions and refresh before treating a replay as representative. Local source files may be stale or malformed; do not silently repair an existing snapshot and describe it as the original.

This is diagnostic replay, not automatic acceptance scoring. In particular:

- A previous false-positive review is a human disposition, not an instruction that the engine should return no candidates.
- Cleared unreviewed records provide no established negative ground truth.
- Auto-resolved reruns must not be counted as independent investigator decisions.
- Missing original inputs and replaced legacy evidence cannot be reconstructed from reviewer notes.
- The historical `Organisation` spelling is explicitly mapped to the supported `Organization` input and recorded as a normalization.
- Even populated acceptance fields need a documented expected candidate and source version; the runner retains them but deliberately does not turn free-text outcomes into pass/fail labels.
- Differences between backends include safe incompleteness when PostgreSQL's retrieval cap is exceeded. They are not automatically missed matches or matching errors.
- Comparison by provider ID does not compare all source metadata, and evidence hashes can differ between backend representations. Matching output and snapshot contents still need investigator review. Agreement within a sample does not prove general retrieval parity.

Formal accuracy acceptance still requires an agreed, versioned, investigator-labelled corpus with confirmed matches and adequate coverage. See [acceptance export](SCREENING_ACCEPTANCE_EXPORT.md).
