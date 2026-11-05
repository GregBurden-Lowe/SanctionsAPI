# Matcher v2

Matcher v2 compares names word by word and uses a word index for candidate retrieval. It is off by default, and it changes who is alerted, so it must not be enabled without Compliance acceptance.

Set `SCREENING_MATCHER_VERSION=v2` to enable it. The default is `v1`, and any other value stops screening with no clearance. Results record `evidence-v9-matching-v2` as their rules version, which is part of the screening fingerprint, so earlier results are not reused.

## Before enabling

- **Move the worker to PostgreSQL first.** v2 applies only to the PostgreSQL path, and Parquet stays on `evidence-v8`. Enabling it earlier would run different rules for interactive and background screening.
- **Build the index.** Install migration `007_name_token_index.sql`, then refresh the watchlist. If the index is missing or empty, v2 refuses to screen. It does not fall back to v1.
- **Plan a re-screen.** Results produced under v1 remain as they are until they are screened again.

## What changes

| Area | v1 | v2 |
|---|---|---|
| Spelling in several words | Whole-name similarity | Each word aligned separately (Jaro-Winkler) |
| Omitted patronymic or middle name | Often rejected | Small penalty for listed words missing from the search |
| Distinctive word disagrees | Can still alert | Removed unless a date of birth was supplied |
| Company legal forms and hyphens | Partly handled | Removed before comparing distinctive words |
| Common words | Equal weight | Weighted by how often they appear on the lists |
| Retrieval | Trigrams and alias expansion per row | Word index, with fuzzy word lookup |
| Non-Latin searches | "Too many candidates" | "Enter a name containing supported letters" |

Date-of-birth conflicts still exclude a candidate, as in v1. Results show `match_rule` as `standard` or `token_alignment`, and aligned matches say so in their reasons.

## Limits

The constants in `name_matching.py` are heuristics chosen on constructed cases. They are not calibrated, and scores are not probabilities of identity. Accepting a name needs at least two informative words to align, so single-word searches are not widened. Non-Latin searches are refused, not screened. There is no name-equivalents dictionary, so "Sasha" does not match "Alexander".

## Evidence

On 526 constructed cases not used during design, built from the public 2026-11-05 snapshot and run on local PostgreSQL 16:

| Measure | v1 | v2 |
|---|---:|---:|
| Expected listing found (of 391) | 349 | 386 |
| OFSI-pattern variations found (of 23) | 1 | 20 |
| Listings found by v1 and lost by v2 | n/a | 0 |
| Alerts on non-listed names, excluding broad searches (of 74) | 18 | 10 |
| Candidates across all alerts | 3,460 | 3,966 |

This is diagnostic evidence, not accuracy. There are no independently labelled cases yet, and v2 has not been measured on real customer names. Alerts on cross-combined names rose from 0 to 3 of 15.
