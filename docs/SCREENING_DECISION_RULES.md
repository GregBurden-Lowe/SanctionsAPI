# Screening Decision Rules

This document defines the current screening decision rules used by the app for sanctions and PEP checks.

It is intended to support audit/compliance traceability for wording such as:
"No sanctions or PEP match found under current rules."

## 1. Data Scope

- Sanctions data is limited to allowlisted datasets:
- `United Nations`
- `OFAC`
- `HM Treasury` / `HMT` / `UK Financial`
- `EU Council` / `EU Financial Sanctions`
- PEP data is included from the consolidated OpenSanctions PEP dataset.
- Search can run against either:
- Parquet-backed matcher (`perform_opensanctions_check`)
- PostgreSQL-backed matcher (`perform_postgres_watchlist_check`)

Business outcome rules are intentionally aligned across both backends.

## 2. Input Normalization Rules

- Name is normalized before matching:
- Unicode folded to ASCII
- punctuation removed
- lowercased
- extra whitespace collapsed
- Date of birth (`dob`) normalization:
- `YYYY` is accepted (year-only)
- `DD-MM-YYYY` is accepted and converted to `YYYY-MM-DD`
- `YYYY-MM-DD` is accepted
- fallback parser is used for compatible date-like strings
- if parsing fails, DOB is treated as not provided

## 3. Matching Rules

## 3.1 Candidate filtering

- Entity type filter:
- `Person` searches only person-like records
- `Organization` searches organization/legal entity/company-like records
- Matching is performed separately against:
- sanctions records
- PEP records

## 3.2 Name similarity

- Core similarity uses fuzzy token-set matching (`rapidfuzz`).
- Thresholds:
- Decision matching threshold: `75`
- Suggestions threshold (non-decision): `60`

## 3.3 DOB constraint for decision matches

- If user did not provide DOB:
- Name threshold rules alone can produce a match.
- If user provided DOB:
- A candidate must satisfy DOB compatibility, otherwise it is rejected even if name score is high.
- DOB compatibility:
- exact `YYYY-MM-DD` match, or
- if query is year-only (`YYYY`), candidate year must match

## 3.4 Top Matches behavior

- `Top Matches` are suggestions only.
- They do not change risk level or final decision.
- They ignore strict DOB rejection used in the final decision path.

## 4. Decision Precedence and Outcomes

Both sanctions and PEP checks run, but sanctions drives the primary decision when both match.

## 4.1 Sanctions match found

- `Is Sanctioned = true`
- `Risk Level = High Risk`
- `Check Summary.Status = Fail Sanction`
- `Match Found = true`
- `Confidence` derived from score:
- `High` if score >= 90
- `Medium` if score >= 80
- `Low` otherwise
- If PEP also matches, `Is PEP = true` is set in addition to sanctions failure.

## 4.2 No sanctions match, but PEP match found

- `Is Sanctioned = false`
- `Is PEP = true`
- `Risk Level = Medium Risk`
- `Check Summary.Status = Fail PEP`
- `Match Found = true`
- `Confidence` uses the same score bands as above.

## 4.3 No sanctions match and no PEP match

- `Is Sanctioned = false`
- `Is PEP = false`
- `Risk Level = Cleared`
- `Check Summary.Status = Cleared`
- `Confidence = Very High`
- `Score = 0`
- `Match Found = false`

This is what "under current rules" refers to.

## 5. Source Summary Behavior

- Source summary is derived from matched dataset labels and/or backend source labels.
- For cleared results, source labels may be generic (`OpenSanctions` or `Postgres watchlist`) even when underlying datasets include UN/EU/OFAC/HMT.

## 6. Known Limitations

- Name-based fuzzy matching can produce duplicate logical searches for variants/typos unless a separate dedupe policy is applied at persistence level.
- If DOB is omitted, more false positives are possible; if DOB is provided, matches become stricter.
- Decision logic is deterministic but not identity-proofing; analyst review remains required for adverse outcomes.

## 7. Code References

- `/Volumes/HD2/Code/Sanctions/SanctionsAPI/utils.py`
- `_normalize_text`
- `_normalize_dob`
- `get_best_name_matches`
- `_dob_matches`
- `perform_opensanctions_check`
- `perform_postgres_watchlist_check`
- `_empty_no_match_result`

Last reviewed: 2026-02-20
