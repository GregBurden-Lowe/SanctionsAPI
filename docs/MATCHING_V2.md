# Matcher v2

## Evidence grades and the date-of-birth-required outcome

`SCREENING_EVIDENCE_GRADES` controls whether the v2 matcher grades person name evidence. Set it in `.env` so the API and the screening worker both read it. Any unrecognised value stops screening with no clearance.

| Value | Effect |
|---|---|
| absent or `off` | Default. No grades; behaviour is exactly as described in the sections below. |
| `annotate` | Each person candidate carries `evidence_grade` (`strong`, `possible`, `weak`) and `evidence_grade_detail`. No decision, status, score or ordering changes, and the rules version is unchanged. |
| `enforce` | Also applies the policy below. The rules version gains `-evidence-grades-v2`, so earlier results are not reused. |

A candidate is `weak` when a searched word is not explained by the listed name and the listed name is not wholly accounted for either. `evidence_grading.py` has the exact rules. Under `enforce`, only weak person candidates of a search submitted as a person change:

| Situation | Candidate disposition | Search outcome if nothing else alerts |
|---|---|---|
| Date of birth agrees (exact or compatible) | `potential_match` | `Fail Sanction` |
| No date of birth supplied | `dob_required` | `Fail Sanction - DOB Required` |
| Date of birth supplied, listing has none to compare | `below_alert_threshold` | `Cleared` |

Strong and possible candidates, date-of-birth exclusions, searches submitted as an organisation, and organisation, vessel and aircraft candidates are unchanged.

`Fail Sanction - DOB Required` is not a clearance. It keeps the `Fail Sanction` prefix and the same `Is Sanctioned`, `Match Found` and `Risk Level` values as an alert, so an integration that has not been updated still treats it as an unresolved potential match. What differs is the status text, the `dob_required` candidates, `Candidate Evidence Summary.dob_required`, and the guidance finding `date_of_birth_required`.

Workflow:

- The screening stays in the review queue. `GET /review/queue?queue=awaiting_dob` returns only these, `queue=analyst` leaves them out, and every response carries `awaiting_dob_total`. Match Review shows them as "DOB required" and can filter on them.
- AI triage never selects them, and its guardrails refuse to clear one.
- Supplying the date of birth resolves it: either the existing review re-run, or a new `/opcheck` for the same name and business reference with a date of birth, which updates the waiting record in place. A cleared re-run completes the review as before.
- If the date of birth cannot be obtained, an analyst claims the item and records an outcome in the normal way. Nothing escalates automatically, so the awaiting list needs an owner.
- A date of birth arriving through a background job under a different entity key does not close the waiting record; resolve it from the review queue.

`scripts/shadow_grade_replay.py --live` replays cases with `enforce` against the grades-off result. On the captured 502-case queue (10 November 2026 snapshot), cases needing analyst review fell from 267 to 199, 52 became date of birth required and 16 no longer alerted. All 31 cases an earlier review marked as needing corroboration stayed an alert (23) or became date of birth required (8). None of this is validated on independently labelled cases.

To switch on: deploy, set `SCREENING_EVIDENCE_GRADES=enforce`, restart the API and worker, then run the controlled re-screen so open results are replaced. To switch off, remove the setting and restart; results produced under `enforce` keep their own rules version and are not reused.

## Name coverage update, 10 November 2026

The `evidence-v10-matching-v2` rules apply the coverage assessment in `shadow_matching.py` to retrieved name hits for each candidate's type. Unlike the historical v2 description below, this can remove a standard name hit and can add a sufficiently aligned organisation name. Some incomplete or conflicting name combinations remain potential matches for review at scores capped at 69 or 79. These scores are heuristics, not identity probabilities. Comparable conflicting dates of birth still exclude a person candidate; missing dates do not. Unsupported scripts remain fail-closed. This change affects future screenings; historical decisions are not automatically changed.

The offline 54-case review retained the 15 previously missed corroboration-priority cases but also restored five weaker overlaps. On 40 constructed different-surname searches, alerts were 40 for the previous production rules, 5 for the earlier shadow, and 34 for this update. These deliberately difficult cases are not independently verified identity labels or a production alert-rate estimate. Broader recall and multilingual retrieval remain unvalidated.

## Historical v2 baseline

Matcher v2 compares names word by word and uses name and word indexes for candidate retrieval. It is the production matcher. `SCREENING_MATCHER_VERSION` defaults to `v1` in code, and production sets `v2`.

Any value other than `v1` or `v2` stops screening with no clearance. Results record `evidence-v9-matching-v2` as their rules version, which is part of the screening fingerprint, so earlier results are not reused.

## Operating notes

If the name or word index is missing or empty, v2 refuses to screen. It does not fall back to v1. Each refresh rebuilds the indexes in the same transaction as the watchlist. Results produced under v1 remain as they are until screened again.

## What changes

| Area | v1 | v2 |
|---|---|---|
| Spelling in several words | Whole-name similarity | Each word aligned separately (Jaro-Winkler) |
| Omitted patronymic or middle name | Often rejected | Small penalty for listed words missing from the search |
| Distinctive word disagrees | Can still alert | Unchanged: v2 only adds matches |
| Company legal forms and hyphens | Partly handled | Removed before comparing distinctive words |
| Common words | Equal weight | Weighted by how often they appear on the lists |
| Retrieval | Trigrams and alias expansion per row | Indexed names and aliases with the same similarity test, plus a word index |
| Non-Latin searches | "Too many candidates" | "Enter a name containing supported letters" |

Date-of-birth conflicts still exclude a candidate, as in v1. Results show `match_rule` as `standard` or `token_alignment`, and aligned matches say so in their reasons.

## Why v2 only adds matches

`refine_hits` can also remove standard hits whose distinctive words disagree. This is off. It cut alerts on constructed names, but on a real-subject sample it removed 7 of 16 v1 alerts, some sharing words that are rare on the lists. Those could be the same person, and nobody has reviewed them. Do not enable it without independently labelled cases.

## Limits

The constants in `name_matching.py` are heuristics chosen on constructed cases. They are not calibrated, and scores are not probabilities of identity. Accepting a name needs at least two informative words to align, so single-word searches are not widened. Non-Latin searches are refused, not screened. There is no name-equivalents dictionary, so "Sasha" does not match "Alexander".

## Evidence

On 526 constructed cases not used during design, built from the public 2026-11-05 snapshot and run on local PostgreSQL 16:

| Measure | v1 | v2 |
|---|---:|---:|
| Expected listing found (of 391) | 349 | 386 |
| OFSI-pattern variations found (of 23) | 1 | 20 |
| Listings found by v1 and lost by v2 | n/a | 0 |

These figures were measured with the filter on, so the listings found are unaffected but alert volumes on non-listed names will be higher than the 10 of 74 measured with it. This is diagnostic evidence, not accuracy. There are no independently labelled cases yet.
