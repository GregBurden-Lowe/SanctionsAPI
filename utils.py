# utils.py — lean, low-RAM OpenSanctions + PEPs utils

import os
import csv
import time
import json
import hashlib
import re
from datetime import datetime, date
from functools import lru_cache
from typing import Optional, List, Tuple, Dict, Any, Set

import requests
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from rapidfuzz import fuzz

# =============================================================================
# Configuration
# =============================================================================

DATA_DIR = "data"
OSN_PARQUET = os.path.join(DATA_DIR, "opensanctions.parquet")
MATCHING_CONFIG_PATH = os.path.join(DATA_DIR, "matching_config.json")

# Latest consolidated dumps
CONSOLIDATED_SANCTIONS_URL = (
    "https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv"
)
CONSOLIDATED_PEPS_URL = (
    "https://data.opensanctions.org/datasets/latest/peps/targets.simple.csv"
)

# We only keep these columns to reduce memory
OSN_COLS = [
    "id",           # Stable provider record identifier
    "schema",       # Person / Organization / Company / LegalEntity
    "name",         # display name
    "aliases",
    "birth_date",
    "positions",
    "countries",
    "program_ids",
    "dataset",      # dataset label/source
    "sanctions",    # long text; we’ll trim for a short label
    "source_type",  # we add this: "sanctions" | "peps"
]

# Restrict sanctions data to these sources only (case-insensitive substring match)
SANCTIONS_DATASET_ALLOWLIST = [
    "United Nations",                          # UN Security Council
    "OFAC",                                    # US OFAC (SDN etc.)
    "HM Treasury", "HMT", "UK Financial", "UK FCDO", "UK Sanctions List", "OFSI",      # UK HMT/OFSI lists
    "EU Council", "EU Financial Sanctions", "European Union",    # EU lists
]

# UK consolidated list indicators (for change detection and re-screen triggers)
UK_DATASET_PATTERNS = [
    "HM Treasury",
    "HMT",
    "OFSI",
    "UK Financial",
    "UK FCDO",
]

_PERSON_NAME_PARTICLES = frozenset({
    "al", "bin", "binti", "da", "de", "del", "della", "der", "di", "ibn", "la", "le", "st", "van", "von",
})

_ORG_SUFFIX_STOPWORDS = frozenset({
    "the", "ltd", "limited", "llc", "inc", "corp", "co", "company", "plc", "sa", "ag", "gmbh", "bv",
    "sarl", "pte", "pty", "holdings", "holding", "group",
})

_ORG_LEGAL_SUFFIXES = frozenset({
    "ltd", "limited", "llp", "llc", "inc", "corp", "co", "company", "plc", "doo", "sa", "ag", "gmbh",
    "bv", "sarl", "pte", "pty",
})

_ORG_GENERIC_TOKENS_DEFAULT = frozenset({
    "partners", "partner", "group", "holding", "holdings", "services", "solutions", "international", "global",
    "property", "management", "real", "estate", "move",
})

_ORG_CONNECTOR_TOKENS = frozenset({"and", "the", "of"})
_MATCHING_CONFIG_CACHE: Dict[str, Any] = {"mtime": None, "data": None}

# Country alias normalization to avoid false clears when users enter short forms
# (e.g., UK vs United Kingdom, USA vs United States).
_COUNTRY_ALIAS_RAW: Dict[str, Tuple[str, ...]] = {
    "united kingdom": ("uk", "u.k", "u.k.", "great britain", "britain", "england", "scotland", "wales", "northern ireland", "gb", "gbr"),
    "united states": ("usa", "u.s.a", "u.s.a.", "u.s", "u.s.", "us", "america", "united states of america"),
    "united arab emirates": ("uae", "u.a.e"),
    "saudi arabia": ("ksa", "kingdom of saudi arabia"),
    "russia": ("russian federation",),
    "south korea": ("republic of korea", "rok", "korea south"),
    "north korea": ("democratic peoples republic of korea", "dprk", "korea north"),
    "iran": ("iran islamic republic of", "islamic republic of iran"),
    "syria": ("syrian arab republic",),
    "laos": ("lao peoples democratic republic",),
    "moldova": ("republic of moldova",),
    "bolivia": ("bolivia plurinational state of",),
    "venezuela": ("venezuela bolivarian republic of",),
    "tanzania": ("united republic of tanzania",),
    "vietnam": ("viet nam",),
    "czech republic": ("czechia",),
    "ivory coast": ("cote divoire", "cote d ivoire", "côte d'ivoire"),
    "democratic republic of the congo": ("drc", "congo kinshasa"),
    "republic of the congo": ("congo brazzaville",),
}
_COUNTRY_ALIAS_CANONICAL: Dict[str, str] = {}

# =============================================================================
# Small helpers
# =============================================================================

def _normalize_text(s: str) -> str:
    import re, unicodedata
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("utf-8")
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s).lower().strip()


def _normalize_word_list(words: List[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for word in words:
        normalized = _normalize_text(str(word or ""))
        if not normalized or " " in normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _default_matching_config() -> Dict[str, Any]:
    return {
        "custom_generic_words": [],
    }


def get_matching_config() -> Dict[str, Any]:
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        mtime = os.path.getmtime(MATCHING_CONFIG_PATH) if os.path.exists(MATCHING_CONFIG_PATH) else None
    except OSError:
        mtime = None
    if _MATCHING_CONFIG_CACHE["data"] is not None and _MATCHING_CONFIG_CACHE["mtime"] == mtime:
        return dict(_MATCHING_CONFIG_CACHE["data"])

    data = _default_matching_config()
    if os.path.exists(MATCHING_CONFIG_PATH):
        try:
            with open(MATCHING_CONFIG_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                data["custom_generic_words"] = _normalize_word_list(list(raw.get("custom_generic_words") or []))
        except Exception:
            pass
    _MATCHING_CONFIG_CACHE["mtime"] = mtime
    _MATCHING_CONFIG_CACHE["data"] = dict(data)
    return dict(data)


def save_matching_config(*, custom_generic_words: List[str]) -> Dict[str, Any]:
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "custom_generic_words": _normalize_word_list(custom_generic_words),
    }
    with open(MATCHING_CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=True, indent=2, sort_keys=True)
        fh.write("\n")
    _MATCHING_CONFIG_CACHE["mtime"] = os.path.getmtime(MATCHING_CONFIG_PATH)
    _MATCHING_CONFIG_CACHE["data"] = dict(payload)
    return dict(payload)


def get_effective_org_generic_tokens() -> Set[str]:
    cfg = get_matching_config()
    return set(_ORG_GENERIC_TOKENS_DEFAULT) | set(cfg.get("custom_generic_words") or [])


def get_protected_org_legal_suffixes() -> Set[str]:
    return set(_ORG_LEGAL_SUFFIXES)


def _org_token_profile(name: str) -> Dict[str, Any]:
    norm = _normalize_text(name or "")
    tokens = [t for t in norm.split() if t]
    generic_token_set = get_effective_org_generic_tokens()
    legal_tokens = [t for t in tokens if t in _ORG_LEGAL_SUFFIXES]
    generic_tokens = [t for t in tokens if t in generic_token_set]
    informative_tokens = [
        t for t in tokens
        if t not in _ORG_LEGAL_SUFFIXES and t not in generic_token_set and t not in _ORG_CONNECTOR_TOKENS
    ]
    return {
        "normalized": norm,
        "tokens": tokens,
        "token_set": set(tokens),
        "legal_tokens": legal_tokens,
        "generic_tokens": generic_tokens,
        "generic_set": set(legal_tokens) | set(generic_tokens),
        "informative_tokens": informative_tokens,
        "informative_set": set(informative_tokens),
        "informative_clean": " ".join(informative_tokens),
    }


def _build_country_alias_canonical() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for canonical_name, aliases in _COUNTRY_ALIAS_RAW.items():
        cn = _normalize_text(canonical_name)
        if not cn:
            continue
        out[cn] = cn
        for alias in aliases:
            an = _normalize_text(alias)
            if an:
                out[an] = cn
    return out


_COUNTRY_ALIAS_CANONICAL = _build_country_alias_canonical()


def _canonicalize_name_for_key(display_name: str, entity_type: str) -> str:
    """
    Canonicalize display name for stable entity identity keys.
    Conservative rules:
    - Person: drop common particles/initials and keep first+last-centric representation.
    - Organization: drop generic legal suffixes.
    """
    norm = _normalize_text(display_name or "")
    tokens = [t for t in norm.split() if t]
    if not tokens:
        return ""

    et = (entity_type or "Person").strip().lower()
    if et == "person":
        filtered = [t for t in tokens if len(t) > 1 and t not in _PERSON_NAME_PARTICLES]
        if not filtered:
            filtered = tokens
        if len(filtered) >= 3:
            # Keep first + informative middle tokens + last to reduce collisions from token order/noise.
            first = filtered[0]
            last = filtered[-1]
            middle = [t for t in filtered[1:-1] if len(t) > 2]
            ordered = [first, *middle, last]
        else:
            ordered = filtered
        return " ".join(dict.fromkeys(ordered))

    filtered = [t for t in tokens if t not in _ORG_SUFFIX_STOPWORDS]
    return " ".join(filtered) if filtered else norm

def _safe_str(v) -> str:
    try:
        if v is None or pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v)

def _derive_regime_like_row(row) -> Optional[str]:
    """
    Create a short UI label using:
      1) program_ids (first token)
      2) sanctions (first ';' chunk or first line)
      3) dataset
    Always treats NA safely.
    """
    prog = _safe_str(row.get("program_ids")).strip()
    if prog:
        return prog.split(";")[0].strip()

    sanc = _safe_str(row.get("sanctions")).strip()
    if sanc:
        first = (sanc.split(";")[0] or sanc.splitlines()[0]).strip()
        if first:
            return first

    ds = _safe_str(row.get("dataset")).strip()
    return ds or None

def _empty_no_match_result(source_label: str = "OpenSanctions"):
    return {
        "Sanctions Name": None,
        "Birth Date": None,
        "Regime": None,
        "Position": None,
        "Topics": [],
        "Is PEP": False,
        "Is Sanctioned": False,
        "Confidence": "Not assessed",  # no-match is not an identity-confidence estimate
        "Score": 0,
        "Risk Level": "Cleared",
        "Top Matches": [],
        "Match Found": False,
        "Check Summary": {
            "Status": "Cleared",
            "Source": source_label,
            "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }

def _append_search_to_csv(
    name: str,
    summary: Dict[str, Any],
    path: str = os.path.join(DATA_DIR, "search_log.csv")
):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        file_exists = os.path.isfile(path)
        with open(path, mode="a", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=["Date", "Name Searched", "Status", "Source"])
            if not file_exists:
                writer.writeheader()
            writer.writerow({
                "Date":   summary.get("Date"),
                "Name Searched": name,
                "Status": summary.get("Status"),
                "Source": summary.get("Source"),
            })
    except Exception:
        # best-effort only
        pass

# =============================================================================
# Data refresh (download -> filter -> parquet)
# =============================================================================

def _download_csv(url: str, dest_path: str, timeout: int = 300):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(r.content)

def _dataset_allowed(label: str) -> bool:
    """Loose allowlist check on dataset labels."""
    if not label:
        return False
    L = label.lower()
    for needle in SANCTIONS_DATASET_ALLOWLIST:
        if needle.lower() in L:
            return True
    return False


def _dataset_is_uk(label: str) -> bool:
    if not label:
        return False
    lower = label.lower()
    return any(p.lower() in lower for p in UK_DATASET_PATTERNS)


def _first_birth_date(raw_value: str) -> str:
    """Return strict YYYY-MM-DD when available; else empty string."""
    value = str(raw_value or "").strip()
    if not value:
        return ""
    if ";" in value:
        value = value.split(";", 1)[0].strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    return ""


def _build_uk_snapshot_entry(row: Dict[str, Any]) -> Optional[Dict[str, str]]:
    dataset = _safe_str(row.get("dataset")).strip()
    if not _dataset_is_uk(dataset):
        return None
    name = _safe_str(row.get("name")).strip()
    name_norm = _normalize_text(name)
    if not name_norm:
        return None
    entity_id = _safe_str(row.get("id")).strip()
    birth_date = _first_birth_date(_safe_str(row.get("birth_date")))
    regime = (
        _safe_str(row.get("program_ids")).split(";", 1)[0].strip()
        or _safe_str(row.get("sanctions")).split(";", 1)[0].strip()
    )
    payload = "|".join([entity_id, name_norm, birth_date, dataset, regime])
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {
        "fingerprint": fingerprint,
        "entity_id": entity_id,
        "name_norm": name_norm,
        "birth_date": birth_date,
        "dataset": dataset,
        "regime": regime,
    }


def build_uk_sanctions_snapshot(sanctions_csv_path: str) -> Dict[str, Any]:
    """
    Build a deterministic UK sanctions snapshot from the latest sanctions CSV.
    Output:
      { uk_hash, row_count, entries: [ {fingerprint, entity_id, name_norm, birth_date, dataset, regime}, ... ] }
    """
    if not sanctions_csv_path or not os.path.exists(sanctions_csv_path):
        return {"uk_hash": "", "row_count": 0, "entries": []}

    entries: List[Dict[str, str]] = []
    with open(sanctions_csv_path, mode="r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            entry = _build_uk_snapshot_entry(row)
            if entry is None:
                continue
            entries.append(entry)

    entries.sort(key=lambda e: e["fingerprint"])
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry["fingerprint"].encode("utf-8"))
        digest.update(b"\n")
    return {"uk_hash": digest.hexdigest(), "row_count": len(entries), "entries": entries}


def compute_uk_snapshot_delta(
    current_entries: List[Dict[str, str]],
    previous_entries: List[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Compare UK snapshots and return delta stats + targetable entries.
    """
    curr_fp = {e["fingerprint"] for e in current_entries}
    prev_fp = {e["fingerprint"] for e in previous_entries}
    added_fp = curr_fp - prev_fp
    removed_fp = prev_fp - curr_fp

    curr_by_id = {e["entity_id"]: e for e in current_entries if e.get("entity_id")}
    prev_by_id = {e["entity_id"]: e for e in previous_entries if e.get("entity_id")}
    changed_ids: Set[str] = set()
    for entity_id, cur in curr_by_id.items():
        prev = prev_by_id.get(entity_id)
        if prev and prev.get("fingerprint") != cur.get("fingerprint"):
            changed_ids.add(entity_id)

    added_entries = [e for e in current_entries if e["fingerprint"] in added_fp]
    removed_entries = [e for e in previous_entries if e["fingerprint"] in removed_fp]
    changed_entries = [curr_by_id[eid] for eid in changed_ids if eid in curr_by_id]

    # Terms for shortlist matching against normalized customer names.
    stopwords = {
        "the", "and", "limited", "ltd", "llc", "inc", "company", "co", "group",
        "partners", "services", "holdings", "international", "global",
    }
    terms: Set[str] = set()
    for entry in added_entries + removed_entries + changed_entries:
        tokens = [t for t in entry.get("name_norm", "").split() if len(t) >= 4 and t not in stopwords]
        for token in tokens[:6]:
            terms.add(token)

    return {
        "added": len(added_entries),
        "removed": len(removed_entries),
        "changed": len(changed_entries),
        "added_entries": added_entries,
        "removed_entries": removed_entries,
        "changed_entries": changed_entries,
        "candidate_terms": sorted(terms),
    }

def refresh_opensanctions_data(include_peps: bool = True):
    """
    Download latest consolidated sanctions (filtered to UN/EU/OFAC/HMT) and optional PEPs.
    Keep only columns we need, add 'source_type', and write a single compact parquet.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    parts: List[pd.DataFrame] = []

    # Sanctions
    try:
        sanc_csv = os.path.join(DATA_DIR, "os_sanctions_latest.csv")
        _download_csv(CONSOLIDATED_SANCTIONS_URL, sanc_csv, timeout=300)
        df_s = pd.read_csv(sanc_csv, low_memory=False)

        # Ensure columns exist
        for c in OSN_COLS:
            if c not in df_s.columns and c != "source_type":
                df_s[c] = pd.Series(dtype="string")

        # Filter to allowlist datasets
        df_s["dataset"] = df_s["dataset"].astype("string").fillna("")
        mask = df_s["dataset"].apply(_dataset_allowed)
        df_s = df_s[mask].copy()

        df_s = df_s[[c for c in OSN_COLS if c != "source_type"]]
        df_s["source_type"] = "sanctions"
        parts.append(df_s)
        print(f"[OpenSanctions] Sanctions kept: {len(df_s):,} rows (filtered to UN/EU/OFAC/HMT).")
    except Exception as e:
        print(f"[OpenSanctions] Sanctions download/parsing failed: {e}")

    # PEPs (optional)
    if include_peps:
        try:
            peps_csv = os.path.join(DATA_DIR, "os_peps_latest.csv")
            _download_csv(CONSOLIDATED_PEPS_URL, peps_csv, timeout=300)
            df_p = pd.read_csv(peps_csv, low_memory=False)
            for c in OSN_COLS:
                if c not in df_p.columns and c != "source_type":
                    df_p[c] = pd.Series(dtype="string")
            df_p = df_p[[c for c in OSN_COLS if c != "source_type"]]
            df_p["source_type"] = "peps"
            parts.append(df_p)
            print(f"[OpenSanctions] PEPs added: {len(df_p):,} rows.")
        except Exception as e:
            print(f"[OpenSanctions] PEPs download/parsing failed: {e}")

    loaded_sources = {str(part["source_type"].iloc[0]) for part in parts if not part.empty}
    if "sanctions" not in loaded_sources or (include_peps and "peps" not in loaded_sources):
        raise ScreeningUnavailableError("Refresh did not provide all required datasets; existing watchlist retained.")

    df = pd.concat(parts, ignore_index=True)

    # Write compact parquet
    from source_assurance import build_manifest, require_current, METADATA_KEY
    manifest = build_manifest(sanc_csv, peps_csv if include_peps else None)
    require_current(manifest, include_peps)
    table = pa.Table.from_pandas(df)
    table = table.replace_schema_metadata({**(table.schema.metadata or {}), METADATA_KEY: json.dumps(manifest).encode()})
    import tempfile
    with tempfile.NamedTemporaryFile(dir=DATA_DIR, suffix=".parquet", delete=False) as temp:
        temp_path = temp.name
    try:
        pq.write_table(table, temp_path)
        # Content-addressed snapshots preserve the exact screening data for replay.
        archive_dir = os.path.join(DATA_DIR, 'watchlist_versions')
        os.makedirs(archive_dir, exist_ok=True)
        try:
            os.link(temp_path, os.path.join(archive_dir, manifest['version'] + '.parquet'))
        except FileExistsError:
            pass
        os.replace(temp_path, OSN_PARQUET)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    print(f"[OpenSanctions] Parquet saved -> {OSN_PARQUET}")

    # clear cache
    clear_osn_cache()


def _parse_birth_date_for_db(raw_value: str) -> Optional[date]:
    """Keep only strict YYYY-MM-DD dates as Python date objects; partial dates are NULL."""
    value = str(raw_value or "").strip()
    if not value:
        return None
    if ";" in value:
        value = value.split(";", 1)[0].strip()
    if len(value) == 10:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


async def sync_watchlist_entities_postgres(
    conn,
    *,
    sanctions_csv_path: str,
    peps_csv_path: Optional[str],
    include_peps: bool = True,
    batch_size: int = 2000,
) -> Dict[str, int]:
    """
    Rebuild watchlist_entities from latest OpenSanctions CSV files.
    Expected table columns:
      source_type, entity_id, entity_schema, name, name_norm, birth_date, country, dataset, regime, raw_json
    """
    exists = await conn.fetchval("SELECT to_regclass('public.watchlist_entities') IS NOT NULL")
    if not exists:
        raise ScreeningUnavailableError("Watchlist data is unavailable")
    has_country_col = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'watchlist_entities'
              AND column_name = 'country'
        )
        """
    )
    if not has_country_col:
        try:
            await conn.execute("ALTER TABLE watchlist_entities ADD COLUMN IF NOT EXISTS country TEXT")
            has_country_col = True
        except Exception:
            has_country_col = False

    async def _ingest_csv(path: str, source_type: str) -> int:
        if not path or not os.path.exists(path):
            return 0
        inserted = 0
        batch: List[Tuple[Any, ...]] = []
        if has_country_col:
            sql = """
                INSERT INTO watchlist_entities (
                    source_type,
                    entity_id,
                    entity_schema,
                    name,
                    name_norm,
                    birth_date,
                    country,
                    dataset,
                    regime,
                    raw_json
                )
                VALUES ($1, $2, $3, $4, $5, $6::date, $7, $8, $9, $10::jsonb)
            """
        else:
            sql = """
                INSERT INTO watchlist_entities (
                    source_type,
                    entity_id,
                    entity_schema,
                    name,
                    name_norm,
                    birth_date,
                    dataset,
                    regime,
                    raw_json
                )
                VALUES ($1, $2, $3, $4, $5, $6::date, $7, $8, $9::jsonb)
            """
        with open(path, mode="r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                dataset = _safe_str(row.get("dataset")).strip()
                if source_type == "sanctions" and not _dataset_allowed(dataset):
                    continue

                name = _safe_str(row.get("name")).strip()
                if not name:
                    continue

                entity_schema = _safe_str(row.get("schema")).strip() or "Person"
                name_norm = _normalize_text(name)
                if not name_norm:
                    continue

                birth_date = _parse_birth_date_for_db(_safe_str(row.get("birth_date")))
                country = _normalize_country(_safe_str(row.get("countries")))
                regime = (
                    _safe_str(row.get("program_ids")).split(";", 1)[0].strip()
                    or _safe_str(row.get("sanctions")).split(";", 1)[0].strip()
                    or dataset
                    or None
                )
                entity_id = _safe_str(row.get("id")).strip() or hashlib.sha1(
                    f"{source_type}|{name_norm}|{dataset}|{birth_date or ''}".encode("utf-8")
                ).hexdigest()

                raw_json = json.dumps(
                    {
                        "aliases": _safe_str(row.get("aliases")),
                        "birth_date": _safe_str(row.get("birth_date")),
                        "positions": _safe_str(row.get("positions")),
                        "program_ids": _safe_str(row.get("program_ids")),
                        "sanctions": _safe_str(row.get("sanctions")),
                        "countries": _safe_str(row.get("countries")),
                        "dataset": dataset,
                    },
                    ensure_ascii=True,
                )

                if has_country_col:
                    batch.append(
                        (
                            source_type,
                            entity_id,
                            entity_schema,
                            name,
                            name_norm,
                            birth_date,
                            country,
                            dataset or None,
                            regime,
                            raw_json,
                        )
                    )
                else:
                    batch.append(
                        (
                            source_type,
                            entity_id,
                            entity_schema,
                            name,
                            name_norm,
                            birth_date,
                            dataset or None,
                            regime,
                            raw_json,
                        )
                    )

                if len(batch) >= batch_size:
                    await conn.executemany(sql, batch)
                    inserted += len(batch)
                    batch.clear()

        if batch:
            await conn.executemany(sql, batch)
            inserted += len(batch)
            batch.clear()
        return inserted

    from source_assurance import build_manifest, require_current, parquet_manifest
    manifest = build_manifest(sanctions_csv_path, peps_csv_path if include_peps else None)
    published = parquet_manifest()
    if published.get('version') == manifest['version']:
        manifest = published
    require_current(manifest, include_peps)
    async with conn.transaction():
        await conn.execute("CREATE TABLE IF NOT EXISTS watchlist_source_state (singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), manifest jsonb NOT NULL)")
        await conn.execute("TRUNCATE TABLE watchlist_entities")
        sanctions_count = await _ingest_csv(sanctions_csv_path, "sanctions")
        peps_count = await _ingest_csv(peps_csv_path or "", "peps") if include_peps else 0
        if not sanctions_count or (include_peps and not peps_count):
            raise ScreeningUnavailableError("Refresh did not provide all required datasets; existing watchlist retained.")

        await conn.execute("INSERT INTO watchlist_source_state(singleton,manifest) VALUES(true,$1::jsonb) ON CONFLICT(singleton) DO UPDATE SET manifest=EXCLUDED.manifest", json.dumps(manifest))

    return {"sanctions": sanctions_count, "peps": peps_count}

# =============================================================================
# Loading & caching
# =============================================================================

def get_opensanctions_df(parquet_path: str = OSN_PARQUET):
    # Atomic replacement changes inode; every process observes updates without a restart.
    try:
        stat = os.stat(parquet_path)
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    except FileNotFoundError:
        signature = None
    return _load_opensanctions_df(parquet_path, signature)


@lru_cache(maxsize=2)
def _load_opensanctions_df(parquet_path: str, signature) -> pd.DataFrame:
    """
    Load parquet once, project required columns, and precompute normalized fields.
    Uses Arrow-backed dtypes to keep memory small.
    """
    if not os.path.exists(parquet_path):
        return pd.DataFrame(columns=OSN_COLS + ["name_norm", "birth_norm"])

    table = pq.read_table(parquet_path)
    have = set(table.column_names)
    arrays, names = [], []
    for c in OSN_COLS:
        if c in have:
            arrays.append(table[c])
        else:
            arrays.append(pa.array([""] * len(table)))
        names.append(c)
    table2 = pa.Table.from_arrays(arrays, names=names)

    # Convert to pandas with ArrowDtype
    df = table2.to_pandas(types_mapper=pd.ArrowDtype)

    # Precompute normalized fields
    df["name_norm"] = (
        df["name"]
        .astype("string[pyarrow]")
        .fillna("")
        .map(str)
        .map(_normalize_text)
    )
    df["birth_norm"] = pd.to_datetime(
        df["birth_date"].astype("string[pyarrow]").fillna(""),
        errors="coerce",
    ).dt.strftime("%Y-%m-%d")

    df["source_type"] = df["source_type"].astype("string[pyarrow]").fillna("")
    from source_assurance import METADATA_KEY, decode
    df.attrs['source_manifest'] = decode((table.schema.metadata or {}).get(METADATA_KEY))
    return df

def clear_osn_cache():
    _load_opensanctions_df.cache_clear()

# =============================================================================
# Matching
# =============================================================================

class ScreeningUnavailableError(RuntimeError):
    """Required source coverage is unavailable; never interpret this as no match."""


def screening_fingerprint(name, dob, country, entity_type, backend):
    # Preserve all name tokens; entity keys are only legacy grouping identifiers.
    payload = {"name": _normalize_text(name), "dob": _normalize_dob(dob),
               "country": _normalize_country(country), "entity_type": (entity_type or "Person").lower(),
               "backend": backend, "rules_version": "evidence-v4", "matching_config": get_matching_config()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def stamp_screening_result(result, name, dob, country, entity_type, backend):
    result["Screening Inputs"] = {"name": name, "dob": dob, "country": country, "entity_type": entity_type, "backend": backend}
    result["Screening Fingerprint"] = screening_fingerprint(name, dob, country, entity_type, backend)
    result["Matching Rules Version"] = "evidence-v4"
    result["Matching Configuration"] = get_matching_config()
    from screening_guidance import enrich_result
    enrich_result(result)
    return result


def _normalize_dob(dob: Optional[str]) -> Optional[str]:
    """Accept explicit dates/year only; do not silently invent missing parts."""
    if not dob:
        return None
    raw = str(dob).strip()
    if re.fullmatch(r"\d{4}", raw):
        return raw if 1 <= int(raw) <= date.today().year else None
    formats = [(r"\d{2}-\d{2}-\d{4}", "%d-%m-%Y"),
               (r"\d{2}/\d{2}/\d{4}", "%d/%m/%Y"),
               (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d")]
    for pattern, fmt in formats:
        if re.fullmatch(pattern, raw):
            try:
                parsed = datetime.strptime(raw, fmt).date()
                return parsed.isoformat() if parsed <= date.today() else None
            except ValueError:
                return None
    return None


def _normalize_country(country: Optional[str]) -> Optional[str]:
    if not country:
        return None
    raw = _normalize_text(str(country))
    if not raw:
        return None
    return _COUNTRY_ALIAS_CANONICAL.get(raw, raw)


def _country_tokens(raw_country: Optional[str]) -> Set[str]:
    if not raw_country:
        return set()
    value = str(raw_country).strip()
    if not value:
        return set()
    parts = re.split(r"[;,|/]+", value)
    tokens: Set[str] = set()
    for part in parts:
        p = _normalize_text(part)
        if not p:
            continue
        canonical = _normalize_country(p)
        if canonical:
            tokens.add(canonical)
        tokens.add(p)
    return tokens


def _country_matches(candidate_country: Optional[str], query_country: Optional[str]) -> bool:
    if not query_country:
        return True
    if not candidate_country:
        return False
    qn = _normalize_country(query_country)
    if not qn:
        return True
    return qn in _country_tokens(candidate_country)


def derive_entity_key(display_name: str, entity_type: str, dob: Optional[str]) -> str:
    """
    Stable key for one logical entity. Identity is based on:
    - canonicalized normalized name (to reduce duplicate logical searches)
    - entity type (Person / Organization)
    - DOB if provided (YYYY-MM-DD)

    Same inputs => same key. Used for screening cache and job queue.
    Note: entities with only name (no DOB) may collide across different real-world persons;
    this is an accepted business trade-off for simplicity and deterministic reuse.
    """
    norm_name = _canonicalize_name_for_key(display_name or "", entity_type or "Person")
    et = (entity_type or "Person").strip().lower()
    dob_str = _normalize_dob(dob) if dob else ""
    payload = f"{norm_name}|{et}|{dob_str}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def derive_entity_key_variants(display_name: str, entity_type: str, dob: Optional[str]) -> List[str]:
    """
    Return preferred key first plus legacy key (if different) for backward-compatible cache reuse.
    """
    preferred = derive_entity_key(display_name, entity_type, dob)
    legacy_payload = f"{_normalize_text(display_name or '')}|{(entity_type or 'Person').strip().lower()}|{_normalize_dob(dob) if dob else ''}"
    legacy = hashlib.sha256(legacy_payload.encode("utf-8")).hexdigest()
    if legacy == preferred:
        return [preferred]
    return [preferred, legacy]


def _score_person_candidate(
    search_name: str,
    candidate_name: str,
    *,
    threshold: int,
    has_dob: bool,
    strict_short_queries: bool,
) -> Optional[int]:
    def preprocess(name):
        # Hyphens separate name tokens; preserve legacy entity-key normalization.
        name = _normalize_text(re.sub(r"[-‐‑‒–—]", " ", name))
        blacklist = {
            "the", "ltd", "llc", "inc", "co", "company", "corp", "plc", "limited",
            "real", "estate", "group", "services", "solutions", "hub", "global",
            "trust", "association", "federation", "union", "committee", "organization",
            "network", "centre", "center", "international", "foundation", "institute", "bank",
        }
        tokens = [w for w in name.split() if w and w not in blacklist]
        return " ".join(tokens), set(tokens)

    s_clean, s_tokens = preprocess(search_name)
    c_clean, c_tokens = preprocess(candidate_name)
    if not s_clean or not c_clean:
        return None

    effective_threshold = int(threshold)
    if strict_short_queries and not has_dob:
        if len(s_tokens) <= 1:
            effective_threshold = max(effective_threshold, 88)
        elif len(s_tokens) == 2:
            effective_threshold = max(effective_threshold, 82)

    score = int(fuzz.token_set_ratio(s_clean, c_clean))
    if score < effective_threshold:
        return None
    overlap = len(s_tokens & c_tokens)

    if len(s_tokens) <= 2 and s_clean == c_clean:
        return score

    min_overlap = min(2, len(s_tokens), len(c_tokens))
    # One-to-one token alignment permits spelling variation without letting one
    # common token explain an otherwise different name.
    remaining = set(c_tokens)
    aligned = 0
    for token in sorted(s_tokens, key=lambda t: (t not in c_tokens, -len(t), t)):
        if not remaining:
            break
        best = max(sorted(remaining), key=lambda t: fuzz.ratio(token, t))
        similarity = fuzz.ratio(token, best)
        initial_match = min(len(token), len(best)) == 1 and token[0] == best[0]
        if token == best or initial_match or (min(len(token), len(best)) >= 4 and similarity >= 80):
            aligned += 1
            remaining.remove(best)
    if aligned < min_overlap:
        return None
    if any(len(token) == 1 for token in s_tokens | c_tokens):
        score = min(score, 85)
    if not overlap and fuzz.token_sort_ratio(s_clean, c_clean) < 90:
        return None
    if len(s_tokens) > 2 and aligned / max(1, min(len(s_tokens), len(c_tokens))) < 0.6:
        return None
    if len(s_tokens) == 1 and len(c_tokens) > 5:
        score -= 10
    if abs(len(s_tokens) - len(c_tokens)) > 2:
        score -= 15
    if len(c_tokens) <= 2 and len(s_tokens) > 3:
        score -= 20
    if score < effective_threshold:
        return None
    return score


def _score_org_candidate(
    search_name: str,
    candidate_name: str,
    *,
    threshold: int,
    suggestions: bool,
) -> Optional[int]:
    query = _org_token_profile(search_name)
    candidate = _org_token_profile(candidate_name)
    if not query["normalized"] or not candidate["normalized"]:
        return None

    full_ratio = int(fuzz.ratio(query["normalized"], candidate["normalized"]))
    token_set = int(fuzz.token_set_ratio(query["normalized"], candidate["normalized"]))
    informative_query = query["informative_clean"] or query["normalized"]
    informative_candidate = candidate["informative_clean"] or candidate["normalized"]
    informative_set_ratio = int(fuzz.token_set_ratio(informative_query, informative_candidate))
    informative_order_ratio = int(fuzz.ratio(informative_query, informative_candidate))

    shared_informative = query["informative_set"] & candidate["informative_set"]
    shared_generic = query["generic_set"] & candidate["generic_set"]
    near_identical_clean = bool(
        query["informative_clean"]
        and candidate["informative_clean"]
        and (
            query["informative_clean"] == candidate["informative_clean"]
            or informative_set_ratio >= 96
        )
    )

    if not shared_informative and not near_identical_clean:
        if not suggestions:
            return None
        if token_set < max(60, threshold):
            return None

    score = int(max(token_set, informative_set_ratio))
    if informative_query and informative_candidate:
        score = int(round((score * 0.55) + (full_ratio * 0.15) + (informative_order_ratio * 0.30)))

    if shared_generic and len(shared_informative) <= len(shared_generic):
        score -= 12
    if not shared_informative:
        score -= 24

    extra_distinctive = len(query["informative_set"] - candidate["informative_set"]) + len(candidate["informative_set"] - query["informative_set"])
    score -= min(18, extra_distinctive * 6)

    if len(shared_informative) >= 2 and informative_order_ratio < 72:
        score -= 10
    elif len(shared_informative) >= 1 and informative_order_ratio < 55:
        score -= 6

    if (
        len(shared_informative) == len(query["informative_set"]) == len(candidate["informative_set"])
        and query["informative_set"] == candidate["informative_set"]
        and query["informative_clean"] != candidate["informative_clean"]
    ):
        score -= 8

    if suggestions:
        if shared_informative:
            score += 4
        if not shared_informative and shared_generic:
            score -= 10
    elif not shared_informative and not near_identical_clean:
        return None

    if score < threshold:
        return None
    return max(0, min(100, score))


def get_best_name_matches(
    search_name: str,
    candidates: List[str],
    limit: int = 50,
    threshold: int = 80,
    *,
    has_dob: bool = False,
    strict_short_queries: bool = True,
    entity_type: str = "Person",
):
    """Robust fuzzy match with entity-type-specific heuristics."""
    if not _normalize_text(search_name):
        return []

    results = []
    entity_type_norm = (entity_type or "Person").strip().lower()

    for idx, candidate in enumerate(candidates):
        if entity_type_norm == "organization":
            score = _score_org_candidate(
                search_name,
                candidate,
                threshold=int(threshold),
                suggestions=not strict_short_queries,
            )
            cleaned_name = _org_token_profile(candidate)["normalized"]
        else:
            score = _score_person_candidate(
                search_name,
                candidate,
                threshold=int(threshold),
                has_dob=has_dob,
                strict_short_queries=strict_short_queries,
            )
            cleaned_name = _normalize_text(candidate)
        if score is None:
            continue
        results.append((cleaned_name, score, idx))

    return sorted(results, key=lambda x: x[1], reverse=True)[:limit]


def detect_company_likeness(name: str) -> Dict[str, Any]:
    profile = _org_token_profile(name)
    marker_token_set = get_protected_org_legal_suffixes() | get_effective_org_generic_tokens() | {"company"}
    marker_tokens = [t for t in profile["tokens"] if t in marker_token_set]
    signals: List[str] = []
    if marker_tokens:
        signals.append(f"Organisation markers detected: {', '.join(dict.fromkeys(marker_tokens))}")
    if len(profile["tokens"]) >= 3 and marker_tokens:
        signals.append("Name structure looks organization-like rather than person-like")
    inferred_as = "Organization" if marker_tokens else "Person"
    confidence = "high" if len(set(marker_tokens)) >= 2 else "medium" if marker_tokens else "low"
    return {
        "looks_like_company": bool(marker_tokens),
        "confidence": confidence,
        "signals": signals,
        "marker_tokens": list(dict.fromkeys(marker_tokens)),
        "submitted_as": None,
        "inferred_as": inferred_as,
        "likely_misclassified": False,
    }


def build_input_classification(
    *,
    name: str,
    submitted_as: str,
    person_result: Optional[Dict[str, Any]] = None,
    organization_result: Optional[Dict[str, Any]] = None,
    pep_checked: Optional[bool] = None,
) -> Dict[str, Any]:
    base = detect_company_likeness(name)
    submitted = "Organization" if str(submitted_as or "").strip().lower() == "organization" else "Person"
    inferred = base["inferred_as"]
    signals = list(base["signals"])
    person_score = float((person_result or {}).get("Score") or 0)
    org_score = float((organization_result or {}).get("Score") or 0)
    person_match = bool((person_result or {}).get("Match Found"))
    org_match = bool((organization_result or {}).get("Match Found"))

    if org_match and (not person_match or org_score >= person_score + 8):
        inferred = "Organization"
        signals.append("Organisation result is materially stronger than person result")
    elif person_match and (not org_match or person_score >= org_score + 8):
        inferred = "Person"

    if pep_checked is False and base["looks_like_company"]:
        signals.append("PEP screening was skipped because the input looked company-like")

    likely_misclassified = submitted != inferred and (
        base["looks_like_company"] or org_match or person_match
    )
    confidence = base["confidence"]
    if likely_misclassified and org_match and not person_match:
        confidence = "high"
    elif likely_misclassified and confidence == "low":
        confidence = "medium"

    return {
        "submitted_as": submitted,
        "inferred_as": inferred,
        "likely_misclassified": bool(likely_misclassified),
        "confidence": confidence,
        "signals": list(dict.fromkeys(signals)),
    }


def _dob_matches(candidate_dob: Optional[str], query_dob: Optional[str]) -> bool:
    """Compatibility means not contradicted; it does not confirm identity."""
    return _identity_dob_evidence(candidate_dob, query_dob) != "conflicting"


def _top_name_suggestions(
    df_subset: pd.DataFrame,
    search_name: str,
    limit: int = 5,
    threshold: int = 60,
    *,
    entity_type: str = "Person",
) -> List[Tuple[str, int]]:
    """
    Return up to 'limit' fuzzy suggestions [(name, score), ...] based ONLY on name similarity.
    DOB and other strict rules are ignored here so users can see likely intended names.
    """
    if df_subset is None or df_subset.empty:
        return []
    candidates = df_subset["name"].fillna("").tolist()
    hits = get_best_name_matches(
        search_name,
        candidates,
        limit=limit * 3,
        threshold=threshold,
        has_dob=False,
        strict_short_queries=False,
        entity_type=entity_type,
    )
    # Deduplicate by display name, keep highest score, then take top 'limit'
    seen: Dict[str, float] = {}
    for cleaned_name, score, idx in hits:
        display = str(df_subset.iloc[idx].get("name") or cleaned_name)
        if not display:
            continue
        if display not in seen or score > seen[display]:
            seen[display] = float(score)
    return [(n, int(s)) for n, s in sorted(seen.items(), key=lambda x: x[1], reverse=True)[:limit]]

def _identity_dob_evidence(candidate, query):
    """Compare known precision; unknown data is never a contradictory date."""
    query = _normalize_dob(query)
    if not query:
        return "unknown"
    values = re.split(r"[;|]", _safe_str(candidate))
    parsed = []
    unknown = False
    for raw in values:
        raw = raw.strip()
        if re.fullmatch(r"\d{4}(?:-\d{2})?(?:-\d{2})?", raw):
            try:
                datetime.strptime(raw, {4: "%Y", 7: "%Y-%m", 10: "%Y-%m-%d"}[len(raw)])
                parsed.append(raw)
                continue
            except ValueError:
                pass
        unknown = True
    for value in parsed:
        precision = min(len(value), len(query))
        if value[:precision] == query[:precision]:
            return "exact" if precision == 10 else "compatible"
    return "unknown" if unknown or not parsed else "conflicting"


def _identity_country_evidence(candidate, query):
    if not _normalize_country(query) or not _safe_str(candidate).strip():
        return "unknown"
    return "compatible" if _country_matches(candidate, query) else "conflicting"


def _evaluate_watchlist_rows(name, dob, country, entity_type, rows, source_label, include_peps):
    """One decision/evidence implementation shared by both storage backends."""
    if not _normalize_text(name):
        raise ScreeningUnavailableError("Enter a name containing supported letters or numbers.")
    if dob and not _normalize_dob(dob):
        raise ScreeningUnavailableError("Enter a valid date of birth or four-digit year.")
    evidence = []
    suggestions = {}
    et = (entity_type or "Person").strip().lower()
    for row in rows:
        raw = row.get("raw_json") or {}
        if isinstance(raw, str):
            raw = json.loads(raw)
        schema = _safe_str(row.get("entity_schema") or row.get("schema")).lower()
        if schema and schema not in ({"organization", "company", "legalentity"} if et == "organization" else {"person"}):
            continue
        source_type = row.get("source_type", "sanctions")
        if source_type == "peps" and not include_peps:
            continue
        display_name = _safe_str(row.get("name"))
        aliases = _safe_str(raw.get("aliases") or row.get("aliases"))
        names = list(dict.fromkeys([display_name] + [v.strip() for v in aliases.split(';') if v.strip()]))
        hits = get_best_name_matches(name, names, limit=len(names), threshold=75, has_dob=bool(_normalize_dob(dob)), entity_type=entity_type)
        suggestion_hits = get_best_name_matches(name, names, limit=1, threshold=60, strict_short_queries=False, entity_type=entity_type)
        if suggestion_hits:
            suggestions[display_name] = max(suggestions.get(display_name, 0), suggestion_hits[0][1])
        if not hits:
            continue
        _, score, matched_index = hits[0]
        # New imports preserve original precision/multiple DOBs. Older PostgreSQL
        # rows contain only their stored DATE; no more precise evidence is invented.
        candidate_dob = _safe_str(raw.get("birth_date") if "birth_date" in raw else row.get("birth_date"))
        candidate_country = _safe_str(raw.get("countries") or row.get("countries") or row.get("country"))
        dob_status = _identity_dob_evidence(candidate_dob, dob) if et == "person" else "not_applicable"
        country_status = _identity_country_evidence(candidate_country, country)
        excluded = dob_status == "conflicting"
        reasons = ["Name similarity passed the matching threshold."]
        if matched_index:
            reasons.append("Matched a listed alias.")
        if dob_status == "conflicting":
            reasons.append("Known dates of birth conflict; excluded from the decision.")
        elif dob_status == "unknown":
            reasons.append("Date of birth is unavailable or not comparable; identity remains unconfirmed.")
        elif dob_status == "compatible":
            reasons.append("Date of birth agrees at the available precision.")
        elif dob_status == "exact":
            reasons.append("Full date of birth agrees.")
        if country_status == "conflicting":
            reasons.append("Country differs; country alone does not exclude a name match.")
        elif country_status == "unknown":
            reasons.append("Country evidence is unavailable or was not supplied.")
        evidence.append({
            "record_id": _safe_str(row.get("entity_id") or row.get("id")) or None,
            "source_evidence_hash": hashlib.sha256(json.dumps({'name': display_name, 'aliases': aliases, 'dob': candidate_dob, 'country': candidate_country, 'dataset': _safe_str(row.get('dataset')), 'regime': _safe_str(row.get('regime') or _derive_regime_like_row(row)), 'position': _safe_str(raw.get('positions') or row.get('positions')), 'program_ids': _safe_str(raw.get('program_ids') or row.get('program_ids')), 'sanctions': _safe_str(raw.get('sanctions') or row.get('sanctions'))}, sort_keys=True).encode()).hexdigest(),
            "name": display_name, "matched_name": names[matched_index], "matched_alias": bool(matched_index),
            "entity_type": entity_type, "source_type": source_type,
            "dataset": _safe_str(row.get("dataset")), "regime": _safe_str(row.get("regime") or _derive_regime_like_row(row)),
            "position": _safe_str(raw.get("positions") or row.get("positions")) or None,
            "name_score": score, "birth_date": candidate_dob or None, "country": candidate_country or None,
            "dob_assessment": dob_status, "country_assessment": country_status,
            "disposition": "excluded" if excluded else "potential_match", "reasons": reasons,
        })
    evidence.sort(key=lambda x: (x['disposition'] != 'potential_match', x['source_type'] != 'sanctions', -x['name_score'], x['name']))
    sanctions = [e for e in evidence if e['disposition'] == 'potential_match' and e['source_type'] == 'sanctions']
    peps = [e for e in evidence if e['disposition'] == 'potential_match' and e['source_type'] == 'peps']
    result = _empty_no_match_result(source_label)
    result['Top Matches'] = sorted(suggestions.items(), key=lambda x: (-x[1], x[0]))[:5]
    result['Candidate Evidence'] = evidence[:50]
    result['Candidate Evidence Summary'] = {'evaluated_name_matches': len(evidence), 'potential_matches': len(sanctions) + len(peps), 'excluded': sum(e['disposition'] == 'excluded' for e in evidence), 'display_truncated': len(evidence) > 50}
    result['PEP Check'] = {'checked': bool(include_peps), 'status': 'checked' if include_peps else 'skipped'}
    result['Decision Explanation'] = 'No potential match identified under the current matching rules. This is not proof of identity.'
    if sanctions or peps:
        best = (sanctions or peps)[0]
        score = best['name_score']
        result.update({'Sanctions Name': best['name'], 'Birth Date': best['birth_date'], 'Regime': best['regime'],
                       'Is Sanctioned': bool(sanctions), 'Is PEP': bool(peps), 'Match Found': True,
                       'Position': best['position'], 'Score': score, 'Confidence': 'High' if score >= 90 else 'Medium' if score >= 80 else 'Low',
                       'Risk Level': 'High Risk' if sanctions else 'Medium Risk'})
        result['Check Summary'].update(Status='Fail Sanction' if sanctions else 'Fail PEP', Source='; '.join(dict.fromkeys(e['dataset'] or source_label for e in sanctions + peps)))
        result['Decision Explanation'] = 'Potential sanctions match; analyst verification is required.' if sanctions else 'Potential PEP match; review the identity evidence and record the decision.'
    return result


def perform_opensanctions_check(name, dob, country=None, entity_type="Person", parquet_path=OSN_PARQUET,
                               requestor=None, log_search=True, include_peps=True, dataframe=None):
    df = dataframe if dataframe is not None else get_opensanctions_df(parquet_path)
    sources = set(df.get("source_type", pd.Series(dtype=str)).astype(str).str.lower())
    if df.empty or "sanctions" not in sources or (include_peps and "peps" not in sources):
        raise ScreeningUnavailableError("Required sanctions or PEP data is unavailable.")
    # Filter before conversion to avoid duplicating the entire watchlist in memory.
    schemas = df['schema'].fillna('').astype(str).str.lower()
    allowed = ['organization', 'company', 'legalentity'] if entity_type.lower() == 'organization' else ['person']
    selected = df[schemas.isin(allowed)]
    result = _evaluate_watchlist_rows(name, dob, country, entity_type, (dict(zip(selected.columns, values)) for values in selected.itertuples(index=False, name=None)), 'OpenSanctions', include_peps)
    if log_search:
        _append_search_to_csv(name, result['Check Summary'])
    return result


async def perform_postgres_watchlist_check(conn, name, dob, country=None, entity_type="Person", requestor=None,
                                          candidate_limit=400, log_search=True, include_peps=True):
    if not await conn.fetchval("SELECT to_regclass('public.watchlist_entities') IS NOT NULL"):
        raise ScreeningUnavailableError("Watchlist data is unavailable.")
    sources = await conn.fetch("SELECT DISTINCT source_type FROM watchlist_entities")
    available = {r['source_type'] for r in sources}
    if 'sanctions' not in available or (include_peps and 'peps' not in available):
        raise ScreeningUnavailableError("Required sanctions or PEP data is unavailable.")
    # Retrieval must not suppress missing/conflicting identity evidence. Apply type
    # before LIMIT so unrelated schemas cannot crowd out eligible candidates.
    limit = max(1, min(2000, int(candidate_limit)))
    schemas = ['organization', 'company', 'legalentity'] if entity_type.lower() == 'organization' else ['person']
    rows = []
    for source_type in (['sanctions', 'peps'] if include_peps else ['sanctions']):
        candidates = await conn.fetch('''
            SELECT entity_id, name, birth_date::text AS birth_date, dataset, regime,
                   entity_schema, raw_json, source_type
            FROM watchlist_entities
            WHERE source_type=$1 AND lower(entity_schema)=ANY($3::text[])
              AND (name_norm % $2 OR strpos(name_norm, $2)>0
                   OR EXISTS (SELECT 1 FROM unnest(string_to_array(COALESCE(raw_json->>'aliases',''), ';')) alias
                              WHERE similarity(lower(alias), $2)>=0.3 OR strpos(lower(alias), $2)>0))
            ORDER BY similarity(name_norm, $2) DESC, entity_id
            LIMIT $4
        ''', source_type, _normalize_text(name), schemas, limit + 1)
        if len(candidates) > limit:
            raise ScreeningUnavailableError("Too many possible candidates to complete screening. Refine the name and try again.")
        rows.extend(dict(r) for r in candidates)
    result = _evaluate_watchlist_rows(name, dob, country, entity_type, rows, 'Postgres watchlist', include_peps)
    if log_search:
        _append_search_to_csv(name, result['Check Summary'])
    return result
