"""
fetch_all_watchlists.py — Orchestrate all sanctions + PEP data fetches and merge
into a single parquet file compatible with the existing screening pipeline.

Output: data/watchlists.parquet  (same columns as opensanctions.parquet)

Run:
    python scripts/fetch_all_watchlists.py               # all lists, all PEPs
    python scripts/fetch_all_watchlists.py --no-peps     # sanctions only
    python scripts/fetch_all_watchlists.py --only un     # UN only
    python scripts/fetch_all_watchlists.py --pep-tier tier1
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from fetch_un_sanctions import fetch_un_sanctions
from fetch_ofac_sanctions import fetch_ofac_sanctions
from fetch_uk_sanctions import fetch_uk_sanctions
from fetch_eu_sanctions import fetch_eu_sanctions
from fetch_wikidata_peps import fetch_wikidata_peps
from fetch_cia_world_leaders import fetch_cia_world_leaders

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = "data"
OUTPUT_PARQUET = os.path.join(DATA_DIR, "watchlists.parquet")

# Columns must match OSN_COLS in utils.py
REQUIRED_COLS = [
    "id", "schema", "name", "aliases", "birth_date", "positions",
    "countries", "program_ids", "dataset", "sanctions", "source_type",
]


def _ensure_cols(df: pd.DataFrame) -> pd.DataFrame:
    for col in REQUIRED_COLS:
        if col not in df.columns:
            df[col] = ""
    return df[REQUIRED_COLS].fillna("")


def fetch_all(
    include_un: bool = True,
    include_ofac: bool = True,
    include_uk: bool = True,
    include_eu: bool = True,
    include_peps: bool = True,
    include_cia: bool = True,
    pep_tier: str = "tier2",
    cia_workers: int = 5,
    output_path: str = OUTPUT_PARQUET,
) -> pd.DataFrame:
    frames = []

    if include_un:
        log.info("=== UN Security Council Consolidated List ===")
        try:
            df = fetch_un_sanctions(os.path.join(DATA_DIR, "un_sanctions.parquet"))
            frames.append(_ensure_cols(df))
            log.info("UN: %d records", len(df))
        except Exception as e:
            log.error("UN fetch failed: %s", e)

    if include_ofac:
        log.info("=== OFAC SDN + Consolidated Lists ===")
        try:
            df = fetch_ofac_sanctions(output_path=os.path.join(DATA_DIR, "ofac_sanctions.parquet"))
            frames.append(_ensure_cols(df))
            log.info("OFAC: %d records", len(df))
        except Exception as e:
            log.error("OFAC fetch failed: %s", e)

    if include_uk:
        log.info("=== UK OFSI Consolidated Sanctions List ===")
        try:
            df = fetch_uk_sanctions(os.path.join(DATA_DIR, "uk_sanctions.parquet"))
            frames.append(_ensure_cols(df))
            log.info("UK: %d records", len(df))
        except Exception as e:
            log.error("UK fetch failed: %s", e)

    if include_eu:
        log.info("=== EU Financial Sanctions List ===")
        try:
            df = fetch_eu_sanctions(os.path.join(DATA_DIR, "eu_sanctions.parquet"))
            frames.append(_ensure_cols(df))
            log.info("EU: %d records", len(df))
        except Exception as e:
            log.error("EU fetch failed: %s", e)

    if include_peps:
        log.info("=== Wikidata PEPs (tier=%s) ===", pep_tier)
        try:
            df = fetch_wikidata_peps(
                tier=pep_tier,
                output_path=os.path.join(DATA_DIR, "wikidata_peps.parquet"),
            )
            frames.append(_ensure_cols(df))
            log.info("PEPs: %d records", len(df))
        except Exception as e:
            log.error("Wikidata PEP fetch failed: %s", e)

    if include_cia:
        log.info("=== CIA World Leaders (current officials) ===")
        try:
            df = fetch_cia_world_leaders(
                workers=cia_workers,
                output_path=os.path.join(DATA_DIR, "cia_world_leaders.parquet"),
            )
            frames.append(_ensure_cols(df))
            log.info("CIA World Leaders: %d records", len(df))
        except Exception as e:
            log.error("CIA World Leaders fetch failed: %s", e)

    if not frames:
        raise RuntimeError("No data fetched — all sources failed")

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["id"])
    combined = combined.fillna("")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(output_path, index=False)

    log.info("=== Combined watchlist written to %s ===", output_path)
    log.info("Total records: %d", len(combined))
    log.info("By source_type: %s", combined["source_type"].value_counts().to_dict())
    log.info("By dataset:\n%s", combined["dataset"].value_counts().to_string())

    return combined


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch all sanctions + PEP watchlists")
    parser.add_argument("--no-peps", action="store_true", help="Skip Wikidata PEP fetch")
    parser.add_argument("--no-cia", action="store_true", help="Skip CIA World Leaders fetch")
    parser.add_argument(
        "--only",
        choices=["un", "ofac", "uk", "eu", "peps", "cia"],
        default=None,
        help="Fetch only one source",
    )
    parser.add_argument(
        "--pep-tier",
        choices=["tier1", "tier2", "all"],
        default="tier2",
        help="PEP tier depth (default: tier2)",
    )
    parser.add_argument(
        "--cia-workers",
        type=int,
        default=5,
        help="Parallel workers for CIA country fetches (default: 5)",
    )
    parser.add_argument("--output", default=OUTPUT_PARQUET)
    args = parser.parse_args()

    only = args.only
    df = fetch_all(
        include_un=only in (None, "un"),
        include_ofac=only in (None, "ofac"),
        include_uk=only in (None, "uk"),
        include_eu=only in (None, "eu"),
        include_peps=(not args.no_peps) and only in (None, "peps"),
        include_cia=(not args.no_cia) and only in (None, "cia"),
        pep_tier=args.pep_tier,
        cia_workers=args.cia_workers,
        output_path=args.output,
    )

    print(f"\nTotal: {len(df):,} records")
    print(df["dataset"].value_counts().to_string())
