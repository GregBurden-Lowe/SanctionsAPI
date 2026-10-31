# Screening decision rules — evidence-v4

This is the locally implemented matching stage following the history foundation. Production activation is a separate release step.

## Inputs and compatibility

Names are Unicode folded, punctuation normalised and whitespace collapsed. Person matching retains the existing fuzzy thresholds (75 base; stricter short-name thresholds without DOB), but uses one-to-one token alignment so an anchored spelling variation or initial can be considered. Hyphens in person names are treated as token boundaries, without changing legacy entity-key normalization. Initial-based scores are capped at 85. Organisation generic-word controls remain in place.

DOB input supports YYYY, DD-MM-YYYY, DD/MM/YYYY and YYYY-MM-DD. Invalid/future values and incomplete dates other than year-only are rejected instead of being silently omitted or assigned invented date parts.

The `evidence-v4` fingerprint invalidates cached results from previous matching/assurance rules. See [internal screening improvements](INTERNAL_SCREENING_IMPROVEMENTS.md) for the new source and review controls. Existing historical runs remain unchanged.

## Shared execution policy

Interactive PostgreSQL, interactive Parquet and background worker checks now use `screening_policy.py`. Each performs person and organisation sanctions checks. PEP checking is enabled only for a submitted Person whose name does not look company-like. The supplied DOB applies to the person check; country evidence is retained for both types. The submitted entity type remains unchanged and the result explains possible misclassification.

Worker and interactive backend fingerprints remain separate conservatively; `evidence-v4` invalidates old cached decisions so unversioned results cannot bypass source assurance. Release API and worker together. The existing CSV compatibility log is appended once per successful execution; durable request/run history remains the audit record.

## Shared evaluator

PostgreSQL and Parquet use the same candidate evaluator, including listed aliases. Each candidate records:

- provider record ID where available, listed name, matched name/alias, entity type and source;
- name similarity score, original source DOB/country and evidence assessments;
- potential-match or excluded disposition and plain-language reasons.

New PostgreSQL imports preserve raw DOB strings, including multiple values and partial precision. Existing rows cannot recover precision already discarded; refresh the watchlist before evaluating precision parity with Parquet. Provider IDs are retained in newly refreshed Parquet files.

## DOB policy

| Evidence | Behaviour |
| --- | --- |
| Exact full-date agreement | Retain potential match; label exact. |
| Agreement at available year/month precision | Retain potential match; label compatible. |
| Missing/unparseable DOB, or DOB not supplied | Retain potential match; label unknown. |
| All known comparable DOBs conflict, with no unknown alternative | Exclude from decision; retain explanation in candidate evidence. |

An exact DOB supports review; it is not identity proof. Unknown information is not a mismatch. Candidate year/month precision is preserved rather than converted to an invented day.

## Country policy

Country is supporting evidence. Compatibility, conflict and unknown values are displayed. A differing country alone **does not clear a name match** because it is not a unique identity identifier. Semicolon/comma-separated country values are compared separately.

These conservative changes can increase the review queue and require analyst acceptance before deployment.

## Retrieval and result limits

PostgreSQL filters entity schema before the candidate limit and does not prefilter DOB or country. Listed aliases participate in candidate retrieval. More than the configured candidate limit produces an incomplete response asking for a more specific name, rather than treating a truncated search as a clearance.

All retrieved name matches are assessed before selecting outcomes. At most 50 evidence records are displayed/persisted in the evidence array; counts and a truncation flag disclose this. Potential matches sort before excluded candidates. Name-only suggestions remain advisory.

PostgreSQL uses trigram candidate retrieval; Parquet scans the relevant schema. A shared evaluator does not guarantee identical retrieval for every spelling/transliteration. The local 100,000-row synthetic benchmark is recorded in [validation results](SCREENING_VALIDATION.md); representative production-volume testing remains necessary.

## API and presentation

Legacy status/boolean fields remain for integrations: `Fail Sanction`, `Fail PEP`, `Cleared`, `Is Sanctioned`, `Is PEP`. These flags represent engine candidates, not confirmed identities. Sanctions takes primary precedence, while a PEP candidate found in another type check remains flagged.

New additive fields: `Candidate Evidence`, `Candidate Evidence Summary`, `Decision Explanation`. No-match confidence is `Not assessed`. Match confidence bands retain legacy values but are labelled as name similarity in the UI/PDF.

The UI says potential match/review required, distinguishes skipped PEP checks, and separates missing from conflicting identity evidence. Manual false-positive decisions retain original candidate evidence with an explanatory label. Reported source labels are not proof of full source coverage; generic backend labels are no longer expanded into assumed list badges.

## Availability and refresh

Screening requires populated required source datasets. Refreshing an empty PostgreSQL watchlist is allowed. A refresh lacking required sanctions/PEP records rolls back PostgreSQL replacement, while incomplete downloads do not replace the Parquet file. Parquet replacement is atomic.

## Verification and limits

`tests/fixtures/matching_cases.json` is a small labelled synthetic regression corpus covering spelling variation, diacritics, missing middle names, initials, word order and generic organisation false positives. Other tests cover identity precision, aliases, exclusion evidence, candidate caps, cross-backend evidence and refresh recovery.

The additional `policy_cases.json` corpus checks 28 scenarios through real PostgreSQL retrieval and the Parquet evaluator; a real worker test verifies the same cross-type outcome. These passing tests are not a measured real-world recall/precision claim. A larger reviewed operational corpus, production-volume performance testing and complete source-version publication remain release follow-ups. No model/AI component makes these matching decisions.
