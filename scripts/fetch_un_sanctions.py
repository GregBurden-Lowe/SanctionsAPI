"""
fetch_un_sanctions.py — Download and parse the UN Security Council Consolidated Sanctions List.

Source: https://scsanctions.un.org/resources/xml/en/consolidated.xml
Output: data/un_sanctions.parquet (columns matching OSN_COLS schema)

Run:
    python scripts/fetch_un_sanctions.py
    python scripts/fetch_un_sanctions.py --output data/un_sanctions.parquet
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import defusedxml.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

try:
    from .alias_encoding import encode_aliases
except ImportError:
    from alias_encoding import encode_aliases

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

UN_XML_URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
DEFAULT_OUTPUT = os.path.join("data", "un_sanctions.parquet")

# Namespaces used in the UN XML
NS = ""  # The UN list uses no XML namespace


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _parse_individual(el: ET.Element) -> dict:
    """Parse a INDIVIDUAL element into a normalised row dict."""
    ref_num = _text(el.find("REFERENCE_NUMBER")) or el.get("dataId", "") or el.get("referenceNumber", "")
    entity_id = f"un-individual-{ref_num}"

    # Name parts
    first = _text(el.find("FIRST_NAME"))
    second = _text(el.find("SECOND_NAME"))
    third = _text(el.find("THIRD_NAME"))
    fourth = _text(el.find("FOURTH_NAME"))
    name_parts = [p for p in [first, second, third, fourth] if p]
    canonical_name = " ".join(name_parts)

    # Aliases
    alias_names = []
    for alias_el in el.findall("INDIVIDUAL_ALIAS"):
        alias_quality = _text(alias_el.find("QUALITY"))
        alias_name = _text(alias_el.find("ALIAS_NAME"))
        if alias_name:
            alias_names.append(alias_name)

    # Preserve every alternative, including approximate and unsupported dates.
    dobs = []
    for dob_el in el.findall("INDIVIDUAL_DATE_OF_BIRTH"):
        date_str = _text(dob_el.find("DATE"))
        year_str = _text(dob_el.find("YEAR"))
        date_type = _text(dob_el.find("TYPE_OF_DATE")).upper()
        value = date_str or year_str
        if not value:
            value = "unparsed " + " ".join(f"{_tag.tag}={_text(_tag)}" for _tag in dob_el if _text(_tag))
        elif date_type not in ("", "EXACT"):
            value = date_type.lower() + " " + value
        dobs.append(value)

    # Nationality
    nationalities = []
    for nat_el in el.findall("NATIONALITY"):
        val = _text(nat_el.find("VALUE"))
        if val:
            nationalities.append(val)

    # Listed on
    listed_on = _text(el.find("LISTED_ON"))

    # Comments / designation info
    comments = _text(el.find("COMMENTS1"))
    designation = _text(el.find("DESIGNATION/VALUE"))

    # Regime / committee reference
    un_list_type = _text(el.find("UN_LIST_TYPE"))
    reference_num = ref_num

    sanctions_label = un_list_type or "UN Consolidated"
    if listed_on:
        sanctions_label += f" (listed {listed_on})"

    return {
        "id": entity_id,
        "schema": "Person",
        "name": canonical_name,
        "aliases": encode_aliases(alias_names),
        "birth_date": ";".join(dict.fromkeys(dobs)),
        "positions": designation,
        "countries": ";".join(nationalities),
        "program_ids": reference_num,
        "dataset": "UN Security Council Consolidated List",
        "sanctions": comments or un_list_type,
        "source_type": "sanctions",
    }


def _parse_entity(el: ET.Element) -> dict:
    """Parse an ENTITY element into a normalised row dict."""
    ref_num = _text(el.find("REFERENCE_NUMBER")) or el.get("dataId", "") or el.get("referenceNumber", "")
    entity_id = f"un-entity-{ref_num}"

    canonical_name = _text(el.find("FIRST_NAME"))

    alias_names = []
    for alias_el in el.findall("ENTITY_ALIAS"):
        alias_name = _text(alias_el.find("ALIAS_NAME"))
        if alias_name:
            alias_names.append(alias_name)

    comments = _text(el.find("COMMENTS1"))
    un_list_type = _text(el.find("UN_LIST_TYPE"))
    reference_num = ref_num
    listed_on = _text(el.find("LISTED_ON"))

    sanctions_label = un_list_type or "UN Consolidated"
    if listed_on:
        sanctions_label += f" (listed {listed_on})"

    return {
        "id": entity_id,
        "schema": "Organization",
        "name": canonical_name,
        "aliases": encode_aliases(alias_names),
        "birth_date": "",
        "positions": "",
        "countries": "",
        "program_ids": reference_num,
        "dataset": "UN Security Council Consolidated List",
        "sanctions": comments or un_list_type,
        "source_type": "sanctions",
    }


def fetch_un_sanctions(output_path: str = DEFAULT_OUTPUT) -> pd.DataFrame:
    log.info("Downloading UN Consolidated List from %s", UN_XML_URL)
    response = requests.get(UN_XML_URL, timeout=120)
    response.raise_for_status()
    log.info("Downloaded %d bytes", len(response.content))

    root = ET.fromstring(response.content)

    rows = []

    individuals_section = root.find("INDIVIDUALS")
    if individuals_section is not None:
        individuals = individuals_section.findall("INDIVIDUAL")
        log.info("Parsing %d individuals", len(individuals))
        for el in individuals:
            rows.append(_parse_individual(el))

    entities_section = root.find("ENTITIES")
    if entities_section is not None:
        entities = entities_section.findall("ENTITY")
        log.info("Parsing %d entities", len(entities))
        for el in entities:
            rows.append(_parse_entity(el))

    df = pd.DataFrame(rows)
    df = df.fillna("")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    log.info("Wrote %d records to %s", len(df), output_path)
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch UN Consolidated Sanctions List")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    df = fetch_un_sanctions(args.output)
    print(f"UN sanctions: {len(df)} records ({df['schema'].value_counts().to_dict()})")
