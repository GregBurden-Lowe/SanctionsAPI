"""
fetch_eu_sanctions.py — Download and parse the EU Consolidated Financial Sanctions List.

Source: EU Financial Sanctions Files (FSF) — public token endpoint
  XML 1.1: https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content?token=dG9rZW4tMjAxNw

The public token (base64 "token-2017") is published in the EU Open Data Portal and
grants unauthenticated read access to the bulk export files.

Output: data/eu_sanctions.parquet (columns matching OSN_COLS schema)

Run:
    python scripts/fetch_eu_sanctions.py
    python scripts/fetch_eu_sanctions.py --output data/eu_sanctions.parquet
"""

from __future__ import annotations

import argparse
import logging
import os
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

EU_XML_URL = (
    "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList_1_1/content"
    "?token=dG9rZW4tMjAxNw"
)
EU_XML_FALLBACK_URL = (
    "https://webgate.ec.europa.eu/fsd/fsf/public/files/xmlFullSanctionsList/content"
    "?token=dG9rZW4tMjAxNw"
)
DEFAULT_OUTPUT = os.path.join("data", "eu_sanctions.parquet")

EU_NS = "http://eu.europa.ec/fpi/fsd/export"
NS_PREFIX = f"{{{EU_NS}}}"


def _attr(el: Optional[ET.Element], key: str) -> str:
    if el is None:
        return ""
    return (el.get(key) or "").strip()


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def _tag(el: ET.Element) -> str:
    return el.tag.replace(NS_PREFIX, "")


def _parse_entity(entity_el: ET.Element) -> dict:
    logical_id = entity_el.get("logicalId", "")
    eu_ref = entity_el.get("euReferenceNumber", "")
    entity_id = f"eu-{logical_id or eu_ref}"

    # Subject type: 'person' or 'entity'/'enterprise'
    subtype_el = entity_el.find(f"{NS_PREFIX}subjectType")
    subtype_code = _attr(subtype_el, "code").lower() if subtype_el is not None else ""
    schema = "Person" if subtype_code == "person" else "Organization"

    # Names: nameAlias elements — 'strong=true' ones are primary/high-quality
    canonical_name = ""
    alias_names = []
    for na in entity_el.findall(f"{NS_PREFIX}nameAlias"):
        whole = na.get("wholeName", "").strip()
        if not whole:
            first = na.get("firstName", "").strip()
            middle = na.get("middleName", "").strip()
            last = na.get("lastName", "").strip()
            whole = " ".join(p for p in [first, middle, last] if p)
        if not whole:
            continue
        strong = na.get("strong", "true").lower() == "true"
        lang = na.get("nameLanguage", "").lower()
        if not canonical_name and (strong or not lang or lang == "en"):
            canonical_name = whole
        elif whole != canonical_name:
            alias_names.append(whole)

    # Preserve every alternative and its approximation marker. An approximate
    # date must not become an exact date during matching.
    dobs = []
    birth_places = []
    for bd in entity_el.findall(f"{NS_PREFIX}birthdate"):
        date_val = bd.get("birthdate", "").strip()
        year_val = bd.get("year", "").strip()
        month_val = (bd.get("monthOfYear") or bd.get("month") or "").strip()
        day_val = (bd.get("dayOfMonth") or "").strip()
        range_from = (bd.get("yearRangeFrom") or "").strip()
        range_to = (bd.get("yearRangeTo") or "").strip()
        remark = _text(bd.find(f"{NS_PREFIX}remark"))
        calendar_type = (bd.get("calendarType") or "GREGORIAN").strip().upper()
        if not any((date_val, year_val, month_val, day_val, range_from, range_to)):
            location = ', '.join(filter(None, (_attr(bd, 'city'), _attr(bd, 'region'), _attr(bd, 'place'), _attr(bd, 'countryDescription'))))
            if location:
                birth_places.append(location)
            # A remark can contain an age or an incomplete date; retain it as
            # unknown rather than assuming it only describes the birthplace.
            if remark:
                dobs.append('unparsed date details: ' + remark)
            continue
        if (range_from or range_to) and (date_val or year_val or month_val or day_val):
            value = 'unparsed mixed date fields: ' + ' '.join(filter(None, (date_val, year_val, month_val, day_val, range_from, range_to)))
        elif range_from or range_to:
            value = f'{range_from} to {range_to}' if range_from and range_to else 'unparsed date range: ' + (range_from or range_to)
        elif date_val:
            parts = date_val.split('-')
            contradictory = len(parts) == 3 and any(
                submitted and (not submitted.isdigit() or not actual.isdigit() or int(submitted) != int(actual))
                for submitted, actual in ((year_val, parts[0]), (month_val, parts[1]), (day_val, parts[2]))
            )
            value = ('unparsed inconsistent date fields: ' + ' '.join(filter(None, (date_val, year_val, month_val, day_val)))
                     if contradictory else date_val)
        elif year_val and month_val.isdigit() and day_val.isdigit():
            value = f'{year_val}-{month_val.zfill(2)}-{day_val.zfill(2)}'
        elif year_val and month_val.isdigit() and not day_val:
            value = f'{year_val}-{month_val.zfill(2)}'
        elif year_val and not month_val and not day_val:
            value = year_val
        else:
            value = 'unparsed date details: ' + ' '.join(filter(None, (year_val, month_val, day_val)))
        if calendar_type != 'GREGORIAN':
            value = f'unparsed {calendar_type} date: {value}'
        if not value:
            value = "unparsed date details"
        if bd.get("circa", "").lower() == "true":
            value = "circa " + value
        dobs.append(value)

    # Citizenship / nationality
    countries = []
    for cit in entity_el.findall(f"{NS_PREFIX}citizenship"):
        country = cit.get("countryDescription") or cit.get("countryIso2Code", "")
        if country and country not in countries:
            countries.append(country.strip())

    # Regime / programme from first regulation element
    programme = ""
    listed_date = ""
    for reg in entity_el.findall(f"{NS_PREFIX}regulation"):
        programme = reg.get("programme", "").strip()
        listed_date = reg.get("entryIntoForceDate") or reg.get("publicationDate", "")
        listed_date = listed_date.strip()
        if programme:
            break

    # Remark (usually states which UN resolution or EU regulation)
    remark = _text(entity_el.find(f"{NS_PREFIX}remark"))

    dataset_label = "EU Consolidated Financial Sanctions List"
    sanctions_label = programme or dataset_label
    if listed_date:
        sanctions_label += f" (listed {listed_date})"

    return {
        "id": entity_id,
        "schema": schema,
        "name": canonical_name,
        "aliases": encode_aliases(dict.fromkeys(alias_names)),
        "birth_date": ";".join(dict.fromkeys(dobs)),
        "birth_place": ";".join(dict.fromkeys(birth_places)),
        "positions": "",
        "countries": ";".join(countries),
        "program_ids": programme,
        "dataset": dataset_label,
        "sanctions": remark or sanctions_label,
        "source_type": "sanctions",
    }


def fetch_eu_sanctions(output_path: str = DEFAULT_OUTPUT) -> pd.DataFrame:
    content = None
    for url in [EU_XML_URL, EU_XML_FALLBACK_URL]:
        try:
            log.info("Downloading EU FSF list from %s", url.split("?")[0])
            resp = requests.get(url, timeout=180)
            resp.raise_for_status()
            content = resp.content
            log.info("Downloaded %d bytes", len(content))
            break
        except requests.HTTPError as e:
            log.warning("Failed (%s) — trying fallback", e)

    if content is None:
        raise RuntimeError("Could not download EU sanctions XML from any known URL")

    root = ET.fromstring(content)
    entities = root.findall(f"{NS_PREFIX}sanctionEntity")
    log.info("Parsing %d entities", len(entities))

    rows = [_parse_entity(el) for el in entities]
    rows = [r for r in rows if r["name"]]

    df = pd.DataFrame(rows)
    df = df.fillna("")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    log.info("Wrote %d records to %s", len(df), output_path)
    return df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch EU Consolidated Financial Sanctions List")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    df = fetch_eu_sanctions(args.output)
    counts = df["schema"].value_counts().to_dict() if not df.empty else {}
    print(f"EU sanctions: {len(df)} records ({counts})")
