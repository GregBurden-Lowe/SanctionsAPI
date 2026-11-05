# Matcher v2

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
