"""
fetch_cia_world_leaders.py — Scrape the CIA World Leaders directory for current PEPs.

Source: https://www.cia.gov/resources/world-leaders/foreign-governments/
Covers ~199 countries, current heads of state, cabinet ministers, central bank governors,
ambassadors to the US, and permanent UN representatives.

Each country page has alternating H4 (role) and P (name) elements. Roles are
mapped to canonical PEP position labels that complement the Wikidata PEP data
(Wikidata is strong on historical depth; CIA World Leaders is authoritative for
current officials including roles Wikidata models poorly such as defence ministers,
interior ministers, and central bank governors).

Output: data/cia_world_leaders.parquet (columns matching OSN_COLS schema)

Run:
    python scripts/fetch_cia_world_leaders.py
    python scripts/fetch_cia_world_leaders.py --workers 10   # parallel country fetches
    python scripts/fetch_cia_world_leaders.py --country albania  # one country only
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

CIA_BASE_URL = "https://www.cia.gov/resources/world-leaders/foreign-governments"
CIA_LISTING_URL = f"{CIA_BASE_URL}/"
DEFAULT_OUTPUT = os.path.join("data", "cia_world_leaders.parquet")
DEFAULT_WORKERS = 5

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SanctionsAPI/1.0; PEP screening; "
        "contact: compliance@example.com)"
    )
}

# ---------------------------------------------------------------------------
# All 199 country slugs from the CIA listing as of November 2026.
# To refresh: visit https://www.cia.gov/resources/world-leaders/foreign-governments/
# set per_page to All, and extract all <a href*="/foreign-governments/"> links.
# ---------------------------------------------------------------------------
COUNTRY_SLUGS = [
    "afghanistan", "albania", "algeria", "andorra", "angola",
    "antigua-and-barbuda", "argentina", "armenia", "aruba", "australia",
    "austria", "azerbaijan", "bahamas-the", "bahrain", "bangladesh",
    "barbados", "belarus", "belgium", "belize", "benin", "bermuda",
    "bhutan", "bolivia", "bosnia-and-herzegovina", "botswana", "brazil",
    "brunei", "bulgaria", "burkina-faso", "burma", "burundi", "cabo-verde",
    "cambodia", "cameroon", "canada", "central-african-republic", "chad",
    "chile", "china", "colombia", "comoros", "congo-democratic-republic-of-the",
    "congo-republic-of-the", "cook-islands", "costa-rica", "cote-divoire",
    "croatia", "cuba", "cyprus", "czechia", "denmark", "djibouti",
    "dominica", "dominican-republic", "ecuador", "egypt", "el-salvador",
    "equatorial-guinea", "eritrea", "estonia", "eswatini", "ethiopia",
    "fiji", "finland", "france", "gabon", "gambia-the", "georgia",
    "germany", "ghana", "greece", "grenada", "guatemala", "guinea",
    "guinea-bissau", "guyana", "haiti", "holy-see-vatican-city",
    "honduras", "hungary", "iceland", "india", "indonesia", "iran",
    "iraq", "ireland", "israel", "italy", "jamaica", "japan", "jordan",
    "kazakhstan", "kenya", "kiribati", "korea-north", "korea-south",
    "kosovo", "kuwait", "kyrgyzstan", "laos", "latvia", "lebanon",
    "lesotho", "liberia", "libya", "liechtenstein", "lithuania",
    "luxembourg", "madagascar", "malawi", "malaysia", "maldives", "mali",
    "malta", "marshall-islands", "mauritania", "mauritius", "mexico",
    "micronesia-federated-states-of", "moldova", "monaco", "mongolia",
    "montenegro", "morocco", "mozambique", "namibia", "nauru", "nepal",
    "netherlands", "new-zealand", "nicaragua", "niger", "nigeria", "niue",
    "north-macedonia", "norway", "oman", "pakistan", "palau", "panama",
    "papua-new-guinea", "paraguay", "peru", "philippines", "poland",
    "portugal", "qatar", "romania", "russia", "rwanda",
    "saint-kitts-and-nevis", "saint-lucia", "saint-vincent-and-the-grenadines",
    "samoa", "san-marino", "sao-tome-and-principe", "saudi-arabia",
    "senegal", "serbia", "seychelles", "sierra-leone", "singapore",
    "slovakia", "slovenia", "solomon-islands", "somalia", "south-africa",
    "south-sudan", "spain", "sri-lanka", "sudan", "suriname", "sweden",
    "switzerland", "syria", "taiwan", "tajikistan", "tanzania", "thailand",
    "timor-leste", "togo", "tonga", "trinidad-and-tobago", "tunisia",
    "turkey", "turkmenistan", "tuvalu", "uganda", "ukraine",
    "united-arab-emirates", "united-kingdom", "uruguay", "uzbekistan",
    "vanuatu", "venezuela", "vietnam", "yemen", "zambia", "zimbabwe",
]

# ---------------------------------------------------------------------------
# Role → canonical PEP position label mapping.
# CIA uses abbreviated titles; we normalise to something consistent with
# Wikidata position labels so deduplication across datasets is easier.
# Rules are checked in order; first match wins.
# ---------------------------------------------------------------------------
_ROLE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Head of state (royal/presidential)
    (re.compile(r"\b(king|queen|emperor|empress|sultan|emir|amir|monarch|sovereign|supreme leader)\b", re.I), "head of state"),
    (re.compile(r"\bpres\b", re.I), "head of state"),
    (re.compile(r"\bpresident\b", re.I), "head of state"),
    (re.compile(r"\bchairman.*(state|people)", re.I), "head of state"),

    # Deputy prime minister (must come before generic prime minister)
    (re.compile(r"\bdep[.\s]+prime\s+min", re.I), "deputy prime minister"),
    (re.compile(r"\bdeputy\s+prime\s+minister\b", re.I), "deputy prime minister"),
    (re.compile(r"\bfirst\s+dep[.\s]+prime", re.I), "deputy prime minister"),
    (re.compile(r"\bfirst\s+deputy\s+prime", re.I), "deputy prime minister"),
    (re.compile(r"\bvice\s+prime\s+min", re.I), "deputy prime minister"),

    # Head of government
    (re.compile(r"\bprime\s+min\b", re.I), "prime minister"),
    (re.compile(r"\bprime\s+minister\b", re.I), "prime minister"),
    (re.compile(r"\bpm\b", re.I), "prime minister"),
    (re.compile(r"\bchancellor\b", re.I), "head of government"),
    (re.compile(r"\bpremier\b", re.I), "head of government"),

    # Cabinet ministers — specific portfolios first
    (re.compile(r"\bmin[\s.]+of\s+finance\b", re.I), "minister of finance"),
    (re.compile(r"\bfinance\s+min\b", re.I), "minister of finance"),
    (re.compile(r"\bmin[\s.]+of\s+economy\b", re.I), "minister of finance"),
    (re.compile(r"\btreasury\b", re.I), "minister of finance"),

    (re.compile(r"\bmin[\s.]+(for|of)\s+(europe\s+&\s+)?foreign\b", re.I), "minister of foreign affairs"),
    (re.compile(r"\bforeign\s+min\b", re.I), "minister of foreign affairs"),
    (re.compile(r"\bforeign\s+affairs\b", re.I), "minister of foreign affairs"),
    (re.compile(r"\bsecretary\s+of\s+state\b", re.I), "minister of foreign affairs"),

    (re.compile(r"\bmin[\s.]+of\s+def(e|en)\w*\b", re.I), "minister of defence"),
    (re.compile(r"\bdefense\s+min\b", re.I), "minister of defence"),
    (re.compile(r"\bdefence\s+min\b", re.I), "minister of defence"),
    (re.compile(r"\bsecretary\s+of\s+def(e|en)\w*\b", re.I), "minister of defence"),

    (re.compile(r"\bmin[\s.]+of\s+int(erior|ernal)\b", re.I), "minister of interior"),
    (re.compile(r"\binterior\s+min\b", re.I), "minister of interior"),
    (re.compile(r"\bhome\s+affairs\b", re.I), "minister of interior"),
    (re.compile(r"\bsecretary\s+of\s+the\s+interior\b", re.I), "minister of interior"),

    (re.compile(r"\bmin[\s.]+of\s+justice\b", re.I), "minister of justice"),
    (re.compile(r"\battorney\s+gen\b", re.I), "minister of justice"),
    (re.compile(r"\bprocurator\s+gen\b", re.I), "minister of justice"),

    # Central bank governor (must come before generic "governor")
    (re.compile(r"\bgovernor[,\s]+(of\s+)?bank\b", re.I), "central bank governor"),
    (re.compile(r"\bcentral\s+bank\b", re.I), "central bank governor"),
    (re.compile(r"\bbank\s+governor\b", re.I), "central bank governor"),

    # Ambassador / diplomat
    (re.compile(r"\bambassador\b", re.I), "ambassador"),
    (re.compile(r"\bpermanent\s+rep(resentative)?\b", re.I), "permanent representative"),

    # Legislature
    (re.compile(r"\bspeaker\b", re.I), "speaker of parliament"),
    (re.compile(r"\bpresident\s+of\s+(the\s+)?(parliament|assembly|senate|congress)\b", re.I), "speaker of parliament"),

    # Generic minister fallback
    (re.compile(r"\bmin\b", re.I), "cabinet minister"),
    (re.compile(r"\bminister\b", re.I), "cabinet minister"),
    (re.compile(r"\bsecretary\b", re.I), "cabinet minister"),
]


def _map_role(role_text: str) -> str:
    """Map a CIA role string to a canonical PEP position label."""
    for pattern, label in _ROLE_PATTERNS:
        if pattern.search(role_text):
            return label
    return "government official"


def _fetch_country_page(session: requests.Session, slug: str, retries: int = 3) -> Optional[BeautifulSoup]:
    url = f"{CIA_BASE_URL}/{slug}/"
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                log.warning("Country not found: %s", slug)
                return None
            log.warning("HTTP error %s for %s (attempt %d)", e, slug, attempt + 1)
            time.sleep(2)
        except Exception as e:
            log.warning("Error fetching %s (attempt %d): %s", slug, attempt + 1, e)
            time.sleep(2)
    log.error("Failed to fetch country page: %s", slug)
    return None


def _parse_country_page(soup: BeautifulSoup, slug: str) -> list[dict]:
    """
    Parse a CIA country page into a list of official records.

    The page uses alternating H4 (role title) and P (person name) elements
    inside the main content area.
    """
    main = soup.find("main")
    if not main:
        return []

    # Country name from H1
    h1 = main.find("h1")
    country_name = h1.get_text(strip=True) if h1 else slug.replace("-", " ").title()

    # Last updated date (optional)
    last_updated = ""
    date_span = main.find("span", string=re.compile(r"\d{1,2}/\d{1,2}/\d{4}"))
    if date_span:
        last_updated = date_span.get_text(strip=True)

    officials: list[dict] = []
    current_role = None
    current_role_raw = None

    for el in main.find_all(["h4", "p"]):
        tag = el.name
        text = el.get_text(strip=True)
        if not text:
            continue

        if tag == "h4":
            current_role_raw = text
            current_role = _map_role(text)
        elif tag == "p" and current_role is not None:
            # Name may be "SURNAME Firstname" or "Firstname SURNAME" — keep as-is
            name = text
            if not name:
                continue

            canonical_pos = current_role
            sanctions_note = f"{current_role_raw} of {country_name}"
            if last_updated:
                sanctions_note += f" (as of {last_updated})"

            rec_id = f"cia-{slug}-{re.sub(r'[^a-z0-9]', '', name.lower())}"

            officials.append({
                "id": rec_id,
                "schema": "Person",
                "name": name,
                "aliases": "",
                "birth_date": "",
                "positions": canonical_pos,
                "countries": country_name,
                "program_ids": "",
                "dataset": "CIA World Leaders",
                "sanctions": sanctions_note,
                "source_type": "peps",
            })
            # Reset: next H4 starts a new role
            current_role = None
            current_role_raw = None

    return officials


def _fetch_and_parse(slug: str) -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)
    soup = _fetch_country_page(session, slug)
    if soup is None:
        return []
    return _parse_country_page(soup, slug)


def fetch_cia_world_leaders(
    slugs: Optional[list[str]] = None,
    workers: int = DEFAULT_WORKERS,
    output_path: str = DEFAULT_OUTPUT,
) -> pd.DataFrame:
    if slugs is None:
        slugs = COUNTRY_SLUGS

    log.info("Fetching CIA World Leaders for %d countries (workers=%d)", len(slugs), workers)

    all_rows: list[dict] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_slug = {pool.submit(_fetch_and_parse, slug): slug for slug in slugs}
        for i, future in enumerate(as_completed(future_to_slug), 1):
            slug = future_to_slug[future]
            try:
                rows = future.result()
                all_rows.extend(rows)
                if i % 20 == 0 or i == len(slugs):
                    log.info("[%d/%d] %s — %d officials so far", i, len(slugs), slug, len(all_rows))
            except Exception as e:
                log.warning("Error processing %s: %s", slug, e)

    # Deduplicate on id (same person can hold multiple roles — keep all via distinct ids)
    df = pd.DataFrame(all_rows)
    df = df.fillna("")

    # Drop empty names
    df = df[df["name"].str.strip() != ""]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    log.info("Wrote %d CIA World Leaders records to %s", len(df), output_path)
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape CIA World Leaders directory for current PEPs")
    parser.add_argument(
        "--country",
        default=None,
        metavar="SLUG",
        help="Only fetch one country (e.g. albania)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Parallel fetch workers (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    slugs = [args.country] if args.country else None
    df = fetch_cia_world_leaders(
        slugs=slugs,
        workers=args.workers,
        output_path=args.output,
    )
    print(f"\nCIA World Leaders: {len(df):,} records")
    if not df.empty:
        pos_counts = df["positions"].value_counts().head(10)
        print("\nTop positions:")
        print(pos_counts.to_string())
        print("\nSample records:")
        sample = df.head(8)[["name", "positions", "countries"]]
        print(sample.to_string(index=False))
