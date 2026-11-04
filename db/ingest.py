"""
db/ingest.py — Public entry point for loading self-scraped watchlist data into PostgreSQL.

The core implementation lives in utils.sync_own_watchlists_postgres, co-located
with the other watchlist helpers. This module re-exports it for use by standalone
scripts that need a clean, minimal import surface.

Usage (from a script):
    import asyncpg
    from db.ingest import load_watchlist_records

    conn = await asyncpg.connect(DATABASE_URL)
    counts = await load_watchlist_records(
        conn,
        watchlists_parquet_path="data/watchlists.parquet",
    )
    print(counts)  # e.g. {"sanctions": 34000}
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path when this module is imported from scripts/
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils import sync_own_watchlists_postgres as load_watchlist_records  # noqa: E402

__all__ = ["load_watchlist_records"]
