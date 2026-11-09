> Parquet screening was retired on 2026-11-05. PostgreSQL is the only screening route. Parquet remains the snapshot format, and references below to a Parquet route are historical.

# Screening decision rules — evidence-v8

This is the locally implemented matching stage following the history foundation. Production activation is a separate release step.

## Inputs and compatibility

Names are Unicode folded, punctuation normalised and whitespace collapsed. Person matching retains the existing fuzzy thresholds (75 base; stricter short-name thresholds without DOB), but uses one-to-one token alignment so an anchored spelling variation or initial can be considered. Hyphens in person names are treated as token boundaries, without changing legacy entity-key normalization. Initial-based scores are capped at 85 except identical normalized short names. Decision thresholds use the established token-set score and alignment rules; the displayed score also reflects the full token sequence for nonidentical person names and can therefore be below the internal decision threshold. Only identical normalized full token multisets can score 100. Common name particles do not count as independent informative anchors. Organisation generic-word controls remain in place.

Person matching preserves repeated token occurrences. A multiword search normally must align at least two occurrences with the same candidate name or alias; a surname-only source name or alias cannot alone trigger a match or suggestion. Single-word queries retain their existing behaviour. As a separate corroborated review path, an exact non-initial single-word source name or alias matching a search token can trigger a potential match when the full source and submitted dates of birth agree exactly. Its name score is capped below the normal threshold and its evidence explains why it was retained. A compatible year/month, missing DOB, fuzzy fragment, or country alone does not activate this path. This does not establish identity: people can share both a name fragment and a date of birth. It can still miss a genuine match when the source contains only a single word and no exact full DOB. Review this trade-off against independently labelled cases before release; a clearance is not proof that an incomplete source name is unrelated.

DOB input supports YYYY, DD-MM-YYYY, DD/MM/YYYY and YYYY-MM-DD. Invalid/future values and incomplete dates other than year-only are rejected instead of being silently omitted or assigned invented date parts.

The `evidence-v8` fingerprint invalidates cached results from previous matching/assurance rules, including the PostgreSQL alias retrieval correction. See [internal screening improvements](INTERNAL_SCREENING_IMPROVEMENTS.md) for the source and review controls. Existing historical runs remain unchanged.

## Shared execution policy

Interactive PostgreSQL, interactive Parquet and background worker checks now use `screening_policy.py`. Each performs person and organisation sanctions checks. PEP checking is enabled only for a submitted Person whose name does not look company-like. The supplied DOB applies to the person check; country evidence is retained for both types. The submitted entity type remains unchanged and the result explains possible misclassification.

Worker and interactive backend fingerprints remain separate conservatively; `evidence-v8` invalidates old cached decisions. Release API and worker together. The existing CSV compatibility log is appended once per successful execution; durable request/run history remains the audit record.

## Shared evaluator

PostgreSQL and Parquet use the same candidate evaluator. Multi-valued aliases are parsed as nested semicolon-delimited CSV, preserving quoted semicolons and escaped quotes; malformed alias values fail the affected screening instead of issuing a clearance. Refresh validates alias cells before publishing the new snapshot. Before deploying against an existing PostgreSQL watchlist, run the read-only alias preflight in [release rescreen](RELEASE_RESCREEN.md), since legacy data was ingested before this validation existed. Each candidate records:

- provider record ID where available, listed name, matched name/alias, entity type and source;
- name similarity score, original source DOB/country and evidence assessments;
- potential-match or excluded disposition and plain-language reasons.

New PostgreSQL imports preserve raw DOB strings, including multiple values and partial precision. Existing rows cannot recover precision already discarded; refresh the watchlist before evaluating precision parity with Parquet. Provider IDs are retained in newly refreshed Parquet files.

## DOB policy

| Evidence | Behaviour |
| --- | --- |
| Exact full-date agreement | Retain potential match; label exact. |
| Agreement at available year/month precision | Retain potential match; label compatible. |
| OFAC date ranges, e.g. `1970 to 1972` | Retain when the submitted date overlaps the inclusive range; exclude when all known alternatives conflict. |
| Missing/unparseable DOB, or DOB not supplied | Retain potential match; label unknown. |
| All known comparable DOBs conflict, with no unknown alternative | Exclude from decision; retain explanation in candidate evidence. |

OFAC dates such as `03 Mar 1966` and `Mar 1966` are compared at their actual precision. A source range may also contain full dates. Unsupported values, invalid dates and reversed ranges remain unknown. The previously approved one-year tolerance applies only to `circa YYYY` and `approximately YYYY`; approximate full dates remain unknown. An exact DOB supports review; it is not identity proof. Unknown information is not a mismatch. Candidate year/month precision is preserved rather than converted to an invented day. The updated rules invalidate old matching fingerprints and require a coordinated API/worker release, Compliance review and representative production validation before activation.

## Country policy

For new person and organisation searches, including API, bulk and worker jobs, an omitted or blank country defaults to `United Kingdom`. A supplied country is preserved. This is a business default, not a verified statement of nationality or residence; the Channel Islands are not part of the UK. Existing queued jobs with no country use the default when executed, and historical request inputs remain unchanged. Screening results record the effective country.

Country is supporting evidence. Compatibility, conflict and unknown values are displayed. A differing country alone **does not clear a name match** because it is not a unique identity identifier. Semicolon/comma-separated country values are compared separately.

These conservative changes can increase the review queue and require analyst acceptance before deployment.

## Retrieval and result limits

PostgreSQL filters entity schema before the candidate limit and does not prefilter DOB or country. Listed aliases participate in candidate retrieval. More than the configured candidate limit produces an incomplete response asking for a more specific name, rather than treating a truncated search as a clearance.

All retrieved name matches are assessed before selecting outcomes. At most 50 evidence records are displayed/persisted in the evidence array; counts and a truncation flag disclose this. Potential matches sort before excluded candidates. Name-only suggestions remain advisory.

PostgreSQL uses trigram candidate retrieval; Parquet scans the relevant schema. A shared evaluator does not guarantee identical retrieval for every spelling/transliteration. The local 100,000-row synthetic benchmark is recorded in [validation results](SCREENING_VALIDATION.md); representative production-volume testing remains necessary.

PostgreSQL normalizes aliases with Unicode NFKD and ASCII folding before additional trigram, substring and exact-token checks, retaining the original raw alias checks as well. The shared evaluator decides whether a retrieved candidate matches. This requires a UTF-8 PostgreSQL database with Unicode normalization support; it does not guarantee parity for every name or transliteration. Tests cover accented and decomposed full and single-word aliases.

## API and presentation

Legacy status/boolean fields remain for integrations: `Fail Sanction`, `Fail PEP`, `Cleared`, `Is Sanctioned`, `Is PEP`. These flags represent engine candidates, not confirmed identities. Sanctions takes primary precedence, while a PEP candidate found in another type check remains flagged.

New additive fields: `Candidate Evidence`, `Candidate Evidence Summary`, `Decision Explanation`. No-match confidence is `Not assessed`. Match confidence bands retain legacy values but are labelled as name similarity in the UI/PDF.

The UI says potential match/review required, distinguishes skipped PEP checks, and separates missing from conflicting identity evidence. Manual false-positive decisions retain original candidate evidence with an explanatory label. Reported source labels are not proof of full source coverage; generic backend labels are no longer expanded into assumed list badges.

## Availability and refresh

Screening requires populated required source datasets. Refreshing an empty PostgreSQL watchlist is allowed. A refresh lacking required sanctions/PEP records rolls back PostgreSQL replacement, while incomplete downloads do not replace the Parquet file. Parquet replacement is atomic.

## Verification and limits

`tests/fixtures/matching_cases.json` is a labelled synthetic regression corpus covering spelling variation, diacritics, missing middle names, initials, word order, repeated names, single-word source names and generic organisation false positives. Other tests cover identity precision, aliases, exclusion evidence, candidate caps, cross-backend evidence and refresh recovery.

The additional `policy_cases.json` corpus checks 28 scenarios through real PostgreSQL retrieval and the Parquet evaluator; a real worker test verifies the same cross-type outcome. These passing tests are not a measured real-world recall/precision claim. A larger reviewed operational corpus, production-volume performance testing and complete source-version publication remain release follow-ups. No model/AI component makes these matching decisions.
