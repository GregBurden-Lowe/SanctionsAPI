"""
fetch_all_watchlists.py — Orchestrate all sanctions data fetches and merge
into a single parquet file compatible with the existing screening pipeline.

Output: data/watchlists.parquet  (same columns as opensanctions.parquet)

Run:
    python scripts/fetch_all_watchlists.py          # all sanctions lists
    python scripts/fetch_all_watchlists.py --only un  # UN only
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

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = "data"
OUTPUT_PARQUET = os.path.join(DATA_DIR, "watchlists.parquet")

# Columns must match OSN_COLS in utils.py
REQUIRED_COLS = [
    "id", "schema", "name", "aliases", "alias_metadata", "birth_date", "birth_place", "birth_notes", "positions",
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
    include_peps: bool = False,
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
    parser = argparse.ArgumentParser(description="Fetch all sanctions watchlists")
    parser.add_argument(
        "--only",
        choices=["un", "ofac", "uk", "eu"],
        default=None,
        help="Fetch only one source",
    )
    parser.add_argument("--output", default=OUTPUT_PARQUET)
    args = parser.parse_args()

    only = args.only
    df = fetch_all(
        include_un=only in (None, "un"),
        include_ofac=only in (None, "ofac"),
        include_uk=only in (None, "uk"),
        include_eu=only in (None, "eu"),
        output_path=args.output,
    )
    print(f"Done. {len(df)} records written to {args.output}")
