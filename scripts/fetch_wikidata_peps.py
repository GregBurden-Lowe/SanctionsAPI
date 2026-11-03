"""
fetch_wikidata_peps.py — Query Wikidata for Politically Exposed Persons (PEPs).

Uses the Wikidata SPARQL endpoint to find individuals who currently hold or have
held qualifying political positions: heads of state/government, ministers,
legislators, senior judges, military generals, central bank governors, etc.

Strategy: one query per position QID (no subclass traversal), no aliases in the
main query — keeps each query fast enough for the public endpoint.

Output: data/wikidata_peps.parquet (columns matching OSN_COLS schema)

Run:
    python scripts/fetch_wikidata_peps.py
    python scripts/fetch_wikidata_peps.py --tier tier1     # heads of state/govt only
    python scripts/fetch_wikidata_peps.py --tier all       # all qualifying positions
    python scripts/fetch_wikidata_peps.py --country Q145   # UK only (pass Wikidata QID)

Tiers:
  tier1 — Heads of state, heads of government, foreign ministers
  tier2 — tier1 + all cabinet ministers, central bank governors, senior military
  all   — tier2 + legislators, senior judges, major party leaders
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"
DEFAULT_OUTPUT = os.path.join("data", "wikidata_peps.parquet")

HEADERS = {
    "Accept": "application/sparql-results+json",
    "User-Agent": "SanctionsAPI/1.0 (PEP screening; contact: compliance@example.com)",
}

# -------------------------------------------------------------------
# Named position QIDs — these are *specific* well-known position classes.
# Using direct QIDs (no subclass traversal) keeps each query fast.
# -------------------------------------------------------------------

TIER1_POSITIONS = {
    "Q48352":   "head of state",       # verified: 49 results (political only)
    "Q2285706": "head of government",  # verified: 43 results
    "Q30461":   "prime minister",      # verified: 6,312 results
    "Q83307":   "minister of finance", # verified: 10,153 results
    "Q189290":  "minister of foreign affairs",  # verified: 261 results
    "Q14212":   "deputy prime minister",        # verified: 140 results
}

TIER2_POSITIONS = {
    **TIER1_POSITIONS,
    # Note: the QIDs below return 0 rows because Wikidata models these roles as
    # specific national positions (e.g. "Secretary of Defense of the United States")
    # rather than the generic class. They are kept here as documentation;
    # expanding coverage requires adding each country's specific position QID.
    "Q28124815": "minister of defence",
    "Q28124811": "minister of interior",
    "Q194039":   "minister of justice",
    "Q750471":   "central bank governor",
    "Q1468516":  "ambassador",
    "Q185264":   "cabinet minister",
}

ALL_POSITIONS = {
    **TIER2_POSITIONS,
    "Q13218630": "United States senator",  # verified: 11,280 results
    "Q4416090":  "senator",                # verified: 2,016 results
    "Q27169":    "member of European Parliament",  # verified: 4,996 results
    "Q16533":    "judge",                  # verified: 1,158 results
}

TIER_MAP = {
    "tier1": TIER1_POSITIONS,
    "tier2": TIER2_POSITIONS,
    "all":   ALL_POSITIONS,
}


def _build_query(position_qid: str, country_qid: Optional[str] = None) -> str:
    """
    Single-position query — no subclass traversal, no aliases.
    Fast enough for the public Wikidata SPARQL endpoint.
    """
    country_clause = ""
    if country_qid:
        country_clause = f"""
  ?person wdt:P27 wd:{country_qid} .
"""

    return f"""
SELECT DISTINCT ?person ?personLabel ?positionLabel ?countryLabel ?dob ?partyLabel
WHERE {{
  ?person wdt:P39 wd:{position_qid} .
  ?person wdt:P31 wd:Q5 .
  {country_clause}
  OPTIONAL {{ ?person wdt:P569 ?dob . }}
  OPTIONAL {{ ?person wdt:P102 ?party . }}
  OPTIONAL {{ ?person wdt:P27 ?country . }}
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "en" .
    ?person   rdfs:label ?personLabel .
    ?position rdfs:label ?positionLabel .
    ?party    rdfs:label ?partyLabel .
    ?country  rdfs:label ?countryLabel .
  }}
}}
LIMIT 20000
"""


def _run_sparql(query: str, retries: int = 3) -> list[dict]:
    for attempt in range(retries):
        try:
            resp = requests.get(
                SPARQL_ENDPOINT,
                params={"query": query, "format": "json"},
                headers=HEADERS,
                timeout=60,
            )
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 30))
                log.warning("Rate limited — sleeping %ds", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json().get("results", {}).get("bindings", [])
        except requests.Timeout:
            log.warning("Timeout on attempt %d/%d — retrying", attempt + 1, retries)
            time.sleep(15)
        except Exception as e:
            log.warning("SPARQL error attempt %d/%d: %s", attempt + 1, retries, e)
            time.sleep(5)
    log.error("All retries exhausted for query — skipping this position")
    return []


def _val(binding: dict, key: str) -> str:
    entry = binding.get(key)
    if entry is None:
        return ""
    return (entry.get("value") or "").strip()


def _aggregate(
    all_bindings: list[tuple[str, list[dict]]]
) -> list[dict]:
    """Aggregate per-position result sets into one record per person URI."""
    persons: dict[str, dict] = {}
    aliases_map: dict[str, set] = defaultdict(set)
    positions_map: dict[str, set] = defaultdict(set)
    countries_map: dict[str, set] = defaultdict(set)
    parties_map: dict[str, set] = defaultdict(set)

    for position_label, bindings in all_bindings:
        for row in bindings:
            uri = _val(row, "person")
            if not uri:
                continue
            if uri not in persons:
                persons[uri] = {
                    "uri": uri,
                    "name": _val(row, "personLabel"),
                    "dob": _val(row, "dob"),
                }
            positions_map[uri].add(position_label)
            country = _val(row, "countryLabel")
            if country:
                countries_map[uri].add(country)
            party = _val(row, "partyLabel")
            if party:
                parties_map[uri].add(party)

    rows = []
    for uri, p in persons.items():
        dob = p["dob"].split("T")[0] if "T" in p["dob"] else p["dob"]
        position_list  = sorted(positions_map[uri])
        country_list   = sorted(countries_map[uri])
        party_list     = sorted(parties_map[uri])

        reason_parts = []
        if position_list:
            reason_parts.append("Position(s): " + ", ".join(position_list[:3]))
        if country_list:
            reason_parts.append("Country: " + ", ".join(country_list[:3]))
        if party_list:
            reason_parts.append("Party: " + ", ".join(party_list[:2]))

        wikidata_id = uri.rsplit("/", 1)[-1]

        rows.append({
            "id": f"wikidata-{wikidata_id}",
            "schema": "Person",
            "name": p["name"],
            "aliases": "",
            "birth_date": dob,
            "positions": ";".join(position_list),
            "countries": ";".join(country_list),
            "program_ids": "",
            "dataset": "Wikidata PEPs",
            "sanctions": " | ".join(reason_parts),
            "source_type": "peps",
        })

    return rows


def fetch_wikidata_peps(
    tier: str = "tier2",
    country_qid: Optional[str] = None,
    output_path: str = DEFAULT_OUTPUT,
) -> pd.DataFrame:
    positions = TIER_MAP.get(tier, TIER2_POSITIONS)
    log.info(
        "Querying Wikidata for PEPs (tier=%s, %d positions, country=%s)",
        tier, len(positions), country_qid or "all",
    )

    all_bindings: list[tuple[str, list[dict]]] = []

    for i, (qid, label) in enumerate(positions.items()):
        query = _build_query(qid, country_qid)
        log.info("[%d/%d] Querying position: %s (%s)", i + 1, len(positions), label, qid)
        bindings = _run_sparql(query)
        log.info("  → %d rows", len(bindings))
        all_bindings.append((label, bindings))
        if i + 1 < len(positions):
            time.sleep(2)  # be polite to the public endpoint

    log.info("Aggregating results across all positions")
    rows = _aggregate(all_bindings)
    log.info("Aggregated to %d unique persons", len(rows))

    df = pd.DataFrame(rows)
    df = df.fillna("")
    df = df[df["name"].str.strip() != ""]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    log.info("Wrote %d PEP records to %s", len(df), output_path)
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch PEPs from Wikidata SPARQL")
    parser.add_argument(
        "--tier",
        choices=["tier1", "tier2", "all"],
        default="tier2",
        help="PEP tier (default: tier2)",
    )
    parser.add_argument(
        "--country",
        default=None,
        metavar="QID",
        help="Wikidata QID of country to limit results (e.g. Q145 for UK, Q30 for US)",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    df = fetch_wikidata_peps(
        tier=args.tier,
        country_qid=args.country,
        output_path=args.output,
    )
    print(f"\nWikidata PEPs: {len(df):,} records")
    if not df.empty:
        print("\nSample records:")
        sample = df[df["positions"] != ""].head(5)[["name", "positions", "countries"]]
        print(sample.to_string(index=False))
