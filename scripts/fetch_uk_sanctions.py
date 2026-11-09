"""
fetch_uk_sanctions.py — Download and parse the UK Sanctions List (FCDO).

The OFSI Consolidated List closed 28 January 2026. The replacement is the
UK Sanctions List published by FCDO:
  XML: https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml

Output: data/uk_sanctions.parquet (columns matching OSN_COLS schema)

Run:
    python scripts/fetch_uk_sanctions.py
    python scripts/fetch_uk_sanctions.py --output data/uk_sanctions.parquet
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

try:
    from .alias_encoding import encode_aliases
except ImportError:
    from alias_encoding import encode_aliases

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

UK_XML_URL = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml"
DEFAULT_OUTPUT = os.path.join("data", "uk_sanctions.parquet")


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _parse_names(desig_el: ET.Element) -> tuple[str, list[str]]:
    """Return (canonical_name, aliases) from a Designation element."""
    canonical = ""
    aliases = []

    names_el = desig_el.find("Names")
    if names_el is None:
        return canonical, aliases

    for name_el in names_el.findall("Name"):
        name_type = _text(name_el.find("NameType"))
        # Name parts: Name1=first, Name2=middle, Name6=last (org: Name6 only)
        parts = [
            _text(name_el.find("Name1")),
            _text(name_el.find("Name2")),
            _text(name_el.find("Name3")),
            _text(name_el.find("Name4")),
            _text(name_el.find("Name5")),
            _text(name_el.find("Name6")),
        ]
        full_name = " ".join(p for p in parts if p)
        if not full_name:
            continue

        if name_type == "Primary Name" or not canonical:
            if not canonical:
                canonical = full_name
            elif full_name != canonical:
                aliases.append(full_name)
        else:
            if full_name != canonical:
                aliases.append(full_name)

    return canonical, list(dict.fromkeys(aliases))  # preserve order, dedupe


def _parse_dob(ind_el: ET.Element) -> str:
    """Preserve every DOB alternative, including unrecognised values."""
    dobs_el = ind_el.find("DOBs")
    if dobs_el is None:
        return ""
    values = []
    for dob_el in dobs_el.findall("DOB"):
        raw = _text(dob_el)
        if not raw:
            continue
        # Format is dd/mm/yyyy or dd/mm/yyyy with 'dd' as literal placeholder
        # Normalise: strip placeholder parts, keep what's real
        parts = raw.split("/")
        if len(parts) == 3:
            day, month, year = parts
            day = "" if day.lower() == "dd" else day
            month = "" if month.lower() == "mm" else month
            year = "" if year.lower() == "yyyy" else year
            if year:
                if month and day:
                    raw = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                elif month:
                    raw = f"{year}-{month.zfill(2)}"
                else:
                    raw = year
        values.append(raw)
    return ";".join(dict.fromkeys(values))


def _parse_nationalities(ind_el: ET.Element) -> list[str]:
    nats_el = ind_el.find("Nationalities")
    if nats_el is None:
        return []
    result = []
    for nat_el in nats_el:
        # Nationality element may have text directly or a sub-element
        val = _text(nat_el)
        if not val:
            val = _text(nat_el.find("Nationality"))
        if val:
            result.append(val)
    return result


def _parse_positions(ind_el: ET.Element) -> list[str]:
    pos_el = ind_el.find("Positions")
    if pos_el is None:
        return []
    result = []
    for p in pos_el:
        val = _text(p)
        if val:
            result.append(val)
    return result


def _parse_designation(desig_el: ET.Element) -> dict:
    unique_id = _text(desig_el.find("UniqueID"))
    entity_type = _text(desig_el.find("IndividualEntityShip"))
    schema = {"individual": "Person", "ship": "Vessel"}.get(entity_type.lower(), "Organization")

    canonical_name, aliases = _parse_names(desig_el)

    regime = _text(desig_el.find("RegimeName"))
    date_designated = _text(desig_el.find("DateDesignated"))
    sanctions_imposed = _text(desig_el.find("SanctionsImposed"))
    other_info = _text(desig_el.find("OtherInformation"))

    # Statement of reasons may be a text node or have sub-elements
    reasons_el = desig_el.find("UKStatementofReasons")
    reasons = _text(reasons_el) if reasons_el is not None else ""

    sanctions_label = sanctions_imposed or regime
    if date_designated:
        sanctions_label += f" (designated {date_designated})"

    # Individual-specific fields
    dobs = []
    countries = []
    positions = []
    titles = []

    titles_el = desig_el.find("Titles")
    if titles_el is not None:
        titles = [_text(t) for t in titles_el if _text(t)]

    ind_details = desig_el.find("IndividualDetails")
    if ind_details is not None:
        for ind_el in ind_details.findall("Individual"):
            dob = _parse_dob(ind_el)
            if dob:
                dobs.append(dob)
            countries.extend(_parse_nationalities(ind_el))
            positions.extend(_parse_positions(ind_el))

    return {
        "id": f"uk-{unique_id}",
        "schema": schema,
        "name": canonical_name,
        "aliases": encode_aliases(aliases),
        "birth_date": ";".join(dobs),
        "positions": ";".join(titles + positions),
        "countries": ";".join(dict.fromkeys(countries)),
        "program_ids": regime,
        "dataset": "UK Sanctions List (FCDO)",
        "sanctions": reasons or other_info or sanctions_label,
        "source_type": "sanctions",
    }


def fetch_uk_sanctions(output_path: str = DEFAULT_OUTPUT) -> pd.DataFrame:
    log.info("Downloading UK Sanctions List from %s", UK_XML_URL)
    resp = requests.get(UK_XML_URL, timeout=180)
    resp.raise_for_status()
    log.info("Downloaded %d bytes", len(resp.content))

    root = ET.fromstring(resp.content)
    designations = root.findall("Designation")
    log.info("Parsing %d designations", len(designations))

    rows = [_parse_designation(el) for el in designations]
    rows = [r for r in rows if r["name"]]  # drop any with no parsed name

    df = pd.DataFrame(rows)
    df = df.fillna("")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    log.info("Wrote %d records to %s", len(df), output_path)
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch UK Sanctions List (FCDO)")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    df = fetch_uk_sanctions(args.output)
    counts = df["schema"].value_counts().to_dict() if not df.empty else {}
    print(f"UK sanctions: {len(df)} records ({counts})")
