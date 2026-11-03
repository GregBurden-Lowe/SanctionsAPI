"""
fetch_ofac_sanctions.py — Download and parse the US OFAC SDN and Consolidated Sanctions Lists.

Sources:
  SDN XML:          https://www.treasury.gov/ofac/downloads/sdn.xml
  Consolidated XML: https://www.treasury.gov/ofac/downloads/consolidated/consolidated.xml

Output: data/ofac_sanctions.parquet (columns matching OSN_COLS schema)

Run:
    python scripts/fetch_ofac_sanctions.py
    python scripts/fetch_ofac_sanctions.py --list sdn       # SDN only
    python scripts/fetch_ofac_sanctions.py --list cons      # Consolidated only
    python scripts/fetch_ofac_sanctions.py --output data/ofac_sanctions.parquet
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from pathlib import Path
from typing import Optional

import defusedxml.ElementTree as ET
import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

OFAC_SDN_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"
OFAC_CONS_URL = "https://www.treasury.gov/ofac/downloads/consolidated/consolidated.xml"
DEFAULT_OUTPUT = os.path.join("data", "ofac_sanctions.parquet")

# OFAC XML namespace
OFAC_NS = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML"
NS = {"o": OFAC_NS}


def _find(el: ET.Element, tag: str) -> Optional[ET.Element]:
    return el.find(f"o:{tag}", NS)


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _findall(el: ET.Element, tag: str):
    return el.findall(f"o:{tag}", NS)


def _parse_remarks(remarks: str) -> dict:
    """
    OFAC embeds useful data in free-text remarks, e.g.:
      'Passport: AB1234; DOB: 01 Jan 1970; POB: Tehran'
    Extract what we can.
    """
    extras = {}
    dob_match = re.search(r"DOB[:\s]+([0-9]{1,2}\s+\w+\s+[0-9]{4})", remarks, re.I)
    if dob_match:
        extras["dob_remark"] = dob_match.group(1)
    return extras


def _parse_entry(entry_el: ET.Element, source_label: str) -> dict:
    uid = _text(_find(entry_el, "uid"))
    entity_id = f"ofac-{uid}"

    # SDN type: Individual / Entity / Vessel / Aircraft
    sdn_type = _text(_find(entry_el, "sdnType"))
    schema = "Person" if sdn_type == "Individual" else "Organization"

    # Name
    first = _text(_find(entry_el, "firstName"))
    last = _text(_find(entry_el, "lastName"))
    name = f"{first} {last}".strip() if first else last

    # Aliases (aka names)
    alias_names = []
    aka_list = _find(entry_el, "akaList")
    if aka_list is not None:
        for aka in _findall(aka_list, "aka"):
            aka_first = _text(_find(aka, "firstName"))
            aka_last = _text(_find(aka, "lastName"))
            aka_name = f"{aka_first} {aka_last}".strip() if aka_first else aka_last
            if aka_name:
                alias_names.append(aka_name)

    # DOB
    dob = ""
    dob_list = _find(entry_el, "dateOfBirthList")
    if dob_list is not None:
        for dob_el in _findall(dob_list, "dateOfBirthItem"):
            dob_text = _text(_find(dob_el, "dateOfBirth"))
            if dob_text:
                dob = dob_text
                break

    # Nationality / citizenship
    nationalities = []
    nationality_list = _find(entry_el, "nationalityList")
    if nationality_list is not None:
        for nat_el in _findall(nationality_list, "nationality"):
            country_val = _text(_find(nat_el, "country"))
            if country_val:
                nationalities.append(country_val)
    citizenship_list = _find(entry_el, "citizenshipList")
    if citizenship_list is not None:
        for cit_el in _findall(citizenship_list, "citizenship"):
            country_val = _text(_find(cit_el, "country"))
            if country_val and country_val not in nationalities:
                nationalities.append(country_val)

    # Sanctions programs
    programs = []
    program_list = _find(entry_el, "programList")
    if program_list is not None:
        for prog_el in _findall(program_list, "program"):
            prog_val = _text(prog_el)
            if prog_val:
                programs.append(prog_val)

    # Remarks (free text — contains extra identity info)
    remarks = _text(_find(entry_el, "remarks"))

    # Title / position (individuals only)
    title = _text(_find(entry_el, "title"))

    return {
        "id": entity_id,
        "schema": schema,
        "name": name,
        "aliases": ";".join(alias_names),
        "birth_date": dob,
        "positions": title,
        "countries": ";".join(nationalities),
        "program_ids": ";".join(programs),
        "dataset": source_label,
        "sanctions": remarks or ";".join(programs),
        "source_type": "sanctions",
    }


def _fetch_list(url: str, label: str) -> list[dict]:
    log.info("Downloading %s from %s", label, url)
    resp = requests.get(url, timeout=180)
    resp.raise_for_status()
    log.info("Downloaded %d bytes", len(resp.content))

    root = ET.fromstring(resp.content)

    # Handle both namespaced and non-namespaced roots
    sdn_list = root.find(f"o:sdnEntry", NS)
    if sdn_list is None:
        # Try entries directly under root or inside sdnList
        entries = root.findall(f"o:sdnEntry", NS)
    else:
        entries = root.findall(f"o:sdnEntry", NS)

    log.info("Parsing %d entries from %s", len(entries), label)
    rows = [_parse_entry(e, label) for e in entries]
    return rows


def fetch_ofac_sanctions(
    include_sdn: bool = True,
    include_consolidated: bool = True,
    output_path: str = DEFAULT_OUTPUT,
) -> pd.DataFrame:
    all_rows = []

    if include_sdn:
        all_rows.extend(_fetch_list(OFAC_SDN_URL, "OFAC SDN List"))

    if include_consolidated:
        all_rows.extend(_fetch_list(OFAC_CONS_URL, "OFAC Consolidated Sanctions List"))

    df = pd.DataFrame(all_rows)
    df = df.fillna("")

    # Deduplicate: same OFAC uid may appear in both lists
    df = df.drop_duplicates(subset=["id"])

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    log.info("Wrote %d records to %s", len(df), output_path)
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch OFAC Sanctions Lists")
    parser.add_argument(
        "--list",
        choices=["sdn", "cons", "both"],
        default="both",
        help="Which OFAC list to download (default: both)",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    include_sdn = args.list in ("sdn", "both")
    include_consolidated = args.list in ("cons", "both")

    df = fetch_ofac_sanctions(
        include_sdn=include_sdn,
        include_consolidated=include_consolidated,
        output_path=args.output,
    )
    counts = df["schema"].value_counts().to_dict() if not df.empty else {}
    print(f"OFAC sanctions: {len(df)} records ({counts})")
