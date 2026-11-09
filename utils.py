# utils.py — lean, low-RAM sanctions watchlist utils

import os
import csv
import json
import hashlib
import re
from datetime import datetime, date
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
OWN_PARQUET = os.path.join(DATA_DIR, "watchlists.parquet")       # self-scraped combined watchlist
MATCHING_CONFIG_PATH = os.path.join(DATA_DIR, "matching_config.json")

# OpenSanctions URL — kept for reference; no longer downloaded at runtime.
CONSOLIDATED_SANCTIONS_URL = (
    "https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv"
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
    "source_type",  # we add this: always "sanctions"
]

# Restrict sanctions data to these sources only (case-insensitive substring match)
SANCTIONS_DATASET_ALLOWLIST = [
    "United Nations", "UN Security Council",   # UN Security Council
    "OFAC",                                    # US OFAC (SDN etc.)
    "HM Treasury", "HMT", "UK Financial", "UK FCDO", "UK Sanctions List", "OFSI",      # UK HMT/OFSI lists
    "EU Council", "EU Financial Sanctions", "European Union", "EU Consolidated",         # EU lists
]

# UK consolidated list indicators (for change detection and re-screen triggers)
UK_DATASET_PATTERNS = [
    "HM Treasury",
    "HMT",
    "OFSI",
    "UK Financial",
    "UK FCDO",
    "UK Sanctions List",  # FCDO replacement for legacy OFSI list
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

def refresh_opensanctions_data():
    """
    Refresh watchlist data from all self-scraped sources (UN, OFAC, UK, EU).

    Replaces the former OpenSanctions CSV download. Calls each scraper, merges
    the results into data/watchlists.parquet, and embeds a source manifest in
    the parquet metadata for freshness checking.
    """
    import sys as _sys
    import tempfile
    from pathlib import Path as _Path
    _scripts_dir = str(_Path(__file__).parent / "scripts")
    if _scripts_dir not in _sys.path:
        _sys.path.insert(0, _scripts_dir)
    from fetch_all_watchlists import fetch_all

    os.makedirs(DATA_DIR, exist_ok=True)
    # Fetch into a staging file: validation failure must not replace the
    # currently published combined snapshot.
    with tempfile.NamedTemporaryFile(dir=DATA_DIR, suffix='.parquet', delete=False) as staged:
        staged_path = staged.name
    try:
        df = fetch_all(include_peps=False, output_path=staged_path)
        if df.empty or "sanctions" not in set(df["source_type"].unique()):
            raise ScreeningUnavailableError(
                "Refresh did not provide sanctions data; existing watchlist retained."
            )
        for aliases in df['aliases']:
            _parse_aliases(aliases)
        from source_assurance import build_own_manifest, require_current, METADATA_KEY
        manifest = build_own_manifest(staged_path)
        require_current(manifest)
    finally:
        if os.path.exists(staged_path):
            os.unlink(staged_path)
    table = pa.Table.from_pandas(df)
    table = table.replace_schema_metadata(
        {**(table.schema.metadata or {}), METADATA_KEY: json.dumps(manifest).encode()}
    )
    with tempfile.NamedTemporaryFile(dir=DATA_DIR, suffix=".parquet", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        pq.write_table(table, tmp_path)
        archive_dir = os.path.join(DATA_DIR, "watchlist_versions")
        os.makedirs(archive_dir, exist_ok=True)
        try:
            os.link(tmp_path, os.path.join(archive_dir, manifest["version"] + ".parquet"))
        except FileExistsError:
            pass
        os.replace(tmp_path, OWN_PARQUET)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    print(f"[Watchlists] Parquet saved -> {OWN_PARQUET}")



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
    peps_csv_path: Optional[str] = None,
    include_peps: bool = False,
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
                # A non-Latin primary name normalises to empty; keep the row so its
                # aliases stay searchable, as they are on the Parquet path.
                name_norm = _normalize_text(name)

                # This happens inside the refresh transaction, so a malformed
                # nested value leaves the previous PostgreSQL snapshot intact.
                _parse_aliases(row.get('aliases'))

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
    manifest = build_manifest(sanctions_csv_path)
    published = parquet_manifest()
    if published.get('version') == manifest['version']:
        manifest = published
    require_current(manifest)
    async with conn.transaction():
        await conn.execute("CREATE TABLE IF NOT EXISTS watchlist_source_state (singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), manifest jsonb NOT NULL)")
        await conn.execute("TRUNCATE TABLE watchlist_entities")
        sanctions_count = await _ingest_csv(sanctions_csv_path, "sanctions")
        if not sanctions_count:
            raise ScreeningUnavailableError("Refresh did not provide sanctions data; existing watchlist retained.")

        await conn.execute("INSERT INTO watchlist_source_state(singleton,manifest) VALUES(true,$1::jsonb) ON CONFLICT(singleton) DO UPDATE SET manifest=EXCLUDED.manifest", json.dumps(manifest))

    return {"sanctions": sanctions_count}


async def sync_own_watchlists_postgres(
    conn,
    *,
    watchlists_parquet_path: Optional[str] = None,
    include_peps: bool = False,
    batch_size: int = 2_000,
) -> Dict[str, int]:
    """
    Rebuild watchlist_entities from the self-scraped watchlists parquet.

    Drop-in replacement for sync_watchlist_entities_postgres using our own
    scraped data (UN, OFAC, UK, EU).
    """
    from name_matching import name_tokens
    from source_assurance import parquet_manifest, require_current

    parquet_path = watchlists_parquet_path or OWN_PARQUET

    exists = await conn.fetchval("SELECT to_regclass('public.watchlist_entities') IS NOT NULL")
    if not exists:
        raise ScreeningUnavailableError("Watchlist data is unavailable")

    # The published file carries the manifest created from the staged source.
    # Re-hashing the metadata-bearing file would give a different version.
    manifest = parquet_manifest(parquet_path)
    require_current(manifest)

    has_country_col = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name   = 'watchlist_entities'
              AND column_name  = 'country'
        )
        """
    )
    if not has_country_col:
        try:
            await conn.execute("ALTER TABLE watchlist_entities ADD COLUMN IF NOT EXISTS country TEXT")
            has_country_col = True
        except Exception:
            has_country_col = False

    if has_country_col:
        sql = """
            INSERT INTO watchlist_entities (
                source_type, entity_id, entity_schema, name, name_norm,
                birth_date, country, dataset, regime, raw_json
            )
            VALUES ($1, $2, $3, $4, $5, $6::date, $7, $8, $9, $10::jsonb)
        """
    else:
        sql = """
            INSERT INTO watchlist_entities (
                source_type, entity_id, entity_schema, name, name_norm,
                birth_date, dataset, regime, raw_json
            )
            VALUES ($1, $2, $3, $4, $5, $6::date, $7, $8, $9::jsonb)
        """

    df = pd.read_parquet(parquet_path)
    df = df.fillna("")
    df = df[df["source_type"] != "peps"]

    counts: Dict[str, int] = {}
    batch: List[Tuple[Any, ...]] = []
    token_rows: Set[Tuple[str, str, str]] = set()
    name_rows: Set[Tuple[str, str, str]] = set()
    token_frequency: Dict[str, int] = {}

    async with conn.transaction():
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS watchlist_source_state "
            "(singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), manifest jsonb NOT NULL)"
        )
        await conn.execute("TRUNCATE TABLE watchlist_entities")

        for _, row in df.iterrows():
            source_type = _safe_str(row.get("source_type")).strip() or "sanctions"
            dataset = _safe_str(row.get("dataset")).strip()
            name = _safe_str(row.get("name")).strip()
            if not name:
                continue
            entity_schema = _safe_str(row.get("schema")).strip() or "Person"
            # A non-Latin primary name normalises to empty; keep the row so its
            # aliases stay searchable, as they are on the Parquet path.
            name_norm = _normalize_text(name)

            # Reject malformed legacy data instead of changing alias boundaries.
            # Newly scraped aliases are encoded as semicolon-delimited CSV.
            aliases_clean = _safe_str(row.get("aliases"))
            _parse_aliases(aliases_clean)

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
            if source_type == "sanctions":
                entity_kind = "person" if entity_schema.lower() == "person" else "organization"
                all_names = [name] + _parse_aliases(aliases_clean)
                name_rows.update((norm, entity_kind, entity_id) for norm in map(_normalize_text, all_names) if norm)
                for token in name_tokens(all_names):
                    token_rows.add((token, entity_kind, entity_id))
                    token_frequency[token] = token_frequency.get(token, 0) + 1

            raw_json = json.dumps(
                {
                    "aliases": aliases_clean,
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
                batch.append((source_type, entity_id, entity_schema, name, name_norm,
                               birth_date, country, dataset or None, regime, raw_json))
            else:
                batch.append((source_type, entity_id, entity_schema, name, name_norm,
                               birth_date, dataset or None, regime, raw_json))

            counts[source_type] = counts.get(source_type, 0) + 1

            if len(batch) >= batch_size:
                await conn.executemany(sql, batch)
                batch.clear()

        if batch:
            await conn.executemany(sql, batch)
            batch.clear()

        sanctions_count = counts.get("sanctions", 0)
        if not sanctions_count:
            raise ScreeningUnavailableError(
                "Refresh did not provide sanctions data; existing watchlist retained."
            )

        # A partial load still looks current, so every snapshot row must arrive.
        expected_rows = int((df["name"].astype(str).str.strip() != "").sum())
        loaded_rows = await conn.fetchval("SELECT count(*) FROM watchlist_entities")
        if loaded_rows != expected_rows:
            raise ScreeningUnavailableError(
                f"Refresh loaded {loaded_rows} of {expected_rows} snapshot rows; existing watchlist retained."
            )

        if await conn.fetchval("SELECT to_regclass('public.watchlist_name_tokens') IS NOT NULL"):
            await conn.execute("TRUNCATE TABLE watchlist_name_tokens, watchlist_tokens, watchlist_names")
            await conn.copy_records_to_table("watchlist_names", records=list(name_rows),
                                             columns=["name_norm", "entity_kind", "entity_id"])
            await conn.copy_records_to_table("watchlist_tokens", records=list(token_frequency.items()),
                                             columns=["token", "doc_frequency"])
            await conn.copy_records_to_table("watchlist_name_tokens", records=list(token_rows),
                                             columns=["token", "entity_kind", "entity_id"])

        await conn.execute(
            "INSERT INTO watchlist_source_state(singleton,manifest) VALUES(true,$1::jsonb) "
            "ON CONFLICT(singleton) DO UPDATE SET manifest=EXCLUDED.manifest",
            json.dumps(manifest),
        )

    return counts


def build_uk_sanctions_snapshot_from_parquet(
    uk_parquet_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a deterministic UK sanctions snapshot from the UK scraper parquet output.

    Reads data/uk_sanctions.parquet (written by fetch_uk_sanctions.py) instead
    of the OpenSanctions CSV, computing the same fingerprint structure used by
    compute_uk_snapshot_delta and watchlist_uk_snapshot_entries.
    """
    if uk_parquet_path is None:
        uk_parquet_path = os.path.join(DATA_DIR, "uk_sanctions.parquet")
    if not uk_parquet_path or not os.path.exists(uk_parquet_path):
        return {"uk_hash": "", "row_count": 0, "entries": []}

    df = pd.read_parquet(uk_parquet_path)
    df = df.fillna("")

    entries: List[Dict[str, str]] = []
    for _, row in df.iterrows():
        entry = _build_uk_snapshot_entry(dict(row))
        if entry is None:
            continue
        entries.append(entry)

    entries.sort(key=lambda e: e["fingerprint"])
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry["fingerprint"].encode("utf-8"))
        digest.update(b"\n")
    return {"uk_hash": digest.hexdigest(), "row_count": len(entries), "entries": entries}


class ScreeningUnavailableError(RuntimeError):
    """Required source coverage is unavailable; never interpret this as no match."""


STANDARD_RULES_VERSION = "evidence-v8-vessel-types-v1-dob-links-v1"


def active_matcher_version() -> str:
    """The v2 matcher is opt-in; anything other than v1 or v2 stops screening."""
    version = os.getenv("SCREENING_MATCHER_VERSION", "v1").strip().lower()
    if version not in {"v1", "v2"}:
        raise ScreeningUnavailableError("Matching configuration is invalid. No clearance issued.")
    return version


def rules_version_for(backend) -> str:
    """v2 needs the PostgreSQL token index, so Parquet stays on the standard rules."""
    if str(backend) == "postgres_beta" and active_matcher_version() == "v2":
        from name_matching import MATCHER_RULES_VERSION
        return MATCHER_RULES_VERSION + "-vessel-types-v1-dob-links-v1"
    return STANDARD_RULES_VERSION


def screening_fingerprint(name, dob, country, entity_type, backend):
    # Preserve all name tokens; entity keys are only legacy grouping identifiers.
    from linked_listings import REVIEWED_LINKS
    payload = {"name": _normalize_text(name), "dob": _normalize_dob(dob),
               "country": _normalize_country(effective_screening_country(country)), "entity_type": (entity_type or "Person").lower(),
               "backend": backend, "rules_version": rules_version_for(backend), "matching_config": get_matching_config(),
               "reviewed_links": REVIEWED_LINKS}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def stamp_screening_result(result, name, dob, country, entity_type, backend):
    result["Screening Inputs"] = {"name": name, "dob": dob, "country": country, "entity_type": entity_type, "backend": backend}
    result["Screening Fingerprint"] = screening_fingerprint(name, dob, country, entity_type, backend)
    result["Matching Rules Version"] = rules_version_for(backend)
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


def effective_screening_country(country: Optional[str]) -> str:
    """Use the operational default only when no country was supplied."""
    return country.strip() if isinstance(country, str) and country.strip() else "United Kingdom"


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
        name = _normalize_text(re.sub(r"[-‐‑‒–—;]", " ", name))
        blacklist = {
            "the", "ltd", "llc", "inc", "co", "company", "corp", "plc", "limited",
            "real", "estate", "group", "services", "solutions", "hub", "global",
            "trust", "association", "federation", "union", "committee", "organization",
            "network", "centre", "center", "international", "foundation", "institute", "bank",
        }
        tokens = [w for w in name.split() if w and w not in blacklist]
        return " ".join(tokens), tokens

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
    overlap = len(set(s_tokens) & set(c_tokens))

    # The decision threshold is applied to the established token-set score.
    # The reported score also reflects all original tokens, so a subset or a
    # repeated word is not presented as an identical name.
    full_search = _normalize_text(re.sub(r"[-‐‑‒–—;]", " ", search_name))
    full_candidate = _normalize_text(re.sub(r"[-‐‑‒–—;]", " ", candidate_name))
    def reported_score(decision_score):
        if sorted(full_search.split()) == sorted(full_candidate.split()):
            return decision_score
        return min(99, int(round(0.75 * decision_score + 0.25 * fuzz.token_sort_ratio(full_search, full_candidate))))

    if len(s_tokens) <= 2 and s_clean == c_clean:
        return reported_score(score) if score >= effective_threshold else None

    # A multi-token search needs two distinct token occurrences, even when a
    # source name or alias contains only one token. Otherwise a surname-only
    # alias can explain away a conflicting given name.
    min_overlap = min(2, len(s_tokens))
    # One-to-one token alignment permits spelling variation without letting one
    # common token explain an otherwise different name.
    remaining = list(c_tokens)
    aligned = 0
    aligned_informative = 0
    for token in sorted(s_tokens, key=lambda t: (t not in c_tokens, -len(t), t)):
        if not remaining:
            break
        best = max(sorted(remaining), key=lambda t: fuzz.ratio(token, t))
        similarity = fuzz.ratio(token, best)
        initial_match = min(len(token), len(best)) == 1 and token[0] == best[0]
        if token == best or initial_match or (min(len(token), len(best)) >= 4 and similarity >= 80):
            aligned += 1
            if token not in _PERSON_NAME_PARTICLES and best not in _PERSON_NAME_PARTICLES:
                aligned_informative += 1
            remaining.remove(best)
    if aligned < min_overlap:
        return None
    informative_query = [t for t in s_tokens if t not in _PERSON_NAME_PARTICLES]
    if aligned_informative < min(2, len(informative_query)):
        return None
    if any(len(token) == 1 for token in s_tokens + c_tokens):
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
    return reported_score(score)


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
    if sorted(query['normalized'].split()) != sorted(candidate['normalized'].split()):
        score = min(score, 99)
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
    approximate_compatible = False
    for raw in values:
        raw = raw.strip()
        approximate = re.fullmatch(r"(?:circa|approximately)\s+(\d{4})", raw, re.IGNORECASE)
        if approximate:
            year = int(approximate.group(1))
            if not 1 <= year <= 9999:
                unknown = True
            elif abs(int(query[:4]) - year) <= 1:
                approximate_compatible = True
            else:
                parsed.append(f"{year:04d}")
            continue
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
    if approximate_compatible:
        return "compatible"
    return "unknown" if unknown or not (parsed or approximate_compatible) else "conflicting"


def _identity_country_evidence(candidate, query):
    if not _normalize_country(query) or not _safe_str(candidate).strip():
        return "unknown"
    return "compatible" if _country_matches(candidate, query) else "conflicting"


def _parse_aliases(value):
    """OpenSanctions' multi-value cells are nested semicolon-delimited CSV."""
    text = _safe_str(value)
    if not text:
        return []
    try:
        values = next(csv.reader([text], delimiter=';', strict=True))
    except (csv.Error, StopIteration) as exc:
        raise ScreeningUnavailableError('Watchlist alias data is malformed; screening cannot complete.') from exc
    return [item.strip() for item in values if item.strip()]


def _evaluate_watchlist_rows(name, dob, country, entity_type, rows, source_label, include_peps=False, token_stats=None, submitted_entity_type=None):
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
        if schema and schema not in ({"organization", "company", "legalentity", "vessel", "aircraft"} if et == "organization" else {"person"}):
            continue
        source_type = row.get("source_type", "sanctions")
        if source_type == "peps":
            continue
        display_name = _safe_str(row.get("name"))
        aliases = _safe_str(raw.get("aliases") or row.get("aliases"))
        names = list(dict.fromkeys([display_name] + _parse_aliases(aliases)))
        hits = get_best_name_matches(name, names, limit=len(names), threshold=75, has_dob=bool(_normalize_dob(dob)), entity_type=entity_type)
        aligned_indexes = set()
        if token_stats is not None:
            from name_matching import refine_hits
            hits, aligned_indexes = refine_hits(name, names, hits, has_dob=bool(_normalize_dob(dob)),
                                                entity_type=entity_type, stats=token_stats)
        # A single listed vessel word cannot establish a match to a multi-word
        # person query. Keep complete names and all organisation searches eligible.
        partial_vessel = schema == 'vessel' and (submitted_entity_type or '').lower() == 'person'
        if partial_vessel:
            query_words = _normalize_text(re.sub(r"[-‐‑‒–—;]", " ", name)).split()
            partial_vessel = len(query_words) > 1
            if partial_vessel:
                hits = [hit for hit in hits if len(_normalize_text(
                    re.sub(r"[-‐‑‒–—;]", " ", names[hit[2]])).split()) > 1]
        suggestion_hits = get_best_name_matches(name, names, limit=len(names), threshold=60, strict_short_queries=False, entity_type=entity_type)
        if partial_vessel:
            suggestion_hits = [hit for hit in suggestion_hits if len(_normalize_text(
                re.sub(r"[-‐‑‒–—;]", " ", names[hit[2]])).split()) > 1]
        if suggestion_hits:
            suggestions[display_name] = max(suggestions.get(display_name, 0), suggestion_hits[0][1])
        # New imports preserve original precision/multiple DOBs. Older PostgreSQL
        # rows contain only their stored DATE; no more precise evidence is invented.
        candidate_dob = _safe_str(raw.get("birth_date") if "birth_date" in raw else row.get("birth_date"))
        candidate_country = _safe_str(raw.get("countries") or row.get("countries") or row.get("country"))
        dob_status = _identity_dob_evidence(candidate_dob, dob) if et == "person" else "not_applicable"
        country_status = _identity_country_evidence(candidate_country, country)
        weak_name_with_dob = False
        if not hits and et == 'person' and dob_status == 'exact':
            # A fragment is supporting evidence, never an identity conclusion.
            # Require a full exact DOB and an exact whole token, not a fuzzy
            # surname, initial, partial date, or merely compatible year.
            query_tokens = _normalize_text(re.sub(r"[-‐‑‒–—]", " ", name)).split()
            if len(query_tokens) >= 2:
                for index, candidate_name in enumerate(names):
                    tokens = _normalize_text(re.sub(r"[-‐‑‒–—]", " ", candidate_name)).split()
                    if len(tokens) == 1 and len(tokens[0]) > 1 and tokens[0] in query_tokens:
                        matched_index = index
                        score = min(69, int(fuzz.ratio(_normalize_text(name), _normalize_text(candidate_name))))
                        weak_name_with_dob = True
                        break
        if hits:
            _, score, matched_index = hits[0]
        elif not weak_name_with_dob:
            continue
        excluded = dob_status == "conflicting"
        reasons = (["A single-word name or alias matched a search token and the full date of birth agrees; review required."]
                   if weak_name_with_dob else ["Name comparison passed the decision rules; the displayed similarity also reflects the full name."])
        if hits and matched_index in aligned_indexes:
            reasons.append("Name parts align word by word, allowing for spelling variation or omitted names.")
        if matched_index:
            reasons.append(f'Matched a listed alias: “{names[matched_index]}”.')
            if len(names) > 5:
                reasons.append(f'This entity has {len(names) - 1} listed aliases; short or common name components may appear in the alias set.')
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
            **({"match_rule": "token_alignment" if hits and matched_index in aligned_indexes else "standard"}
               if token_stats is not None else {}),
            "record_id": _safe_str(row.get("entity_id") or row.get("id")) or None,
            "source_evidence_hash": hashlib.sha256(json.dumps({'name': display_name, 'schema': schema, 'aliases': aliases, 'dob': candidate_dob, 'country': candidate_country, 'dataset': _safe_str(row.get('dataset')), 'regime': _safe_str(row.get('regime') or _derive_regime_like_row(row)), 'position': _safe_str(raw.get('positions') or row.get('positions')), 'program_ids': _safe_str(raw.get('program_ids') or row.get('program_ids')), 'sanctions': _safe_str(raw.get('sanctions') or row.get('sanctions'))}, sort_keys=True).encode()).hexdigest(),
            "name": display_name, "matched_name": names[matched_index], "matched_alias": bool(matched_index),
            "entity_type": {'vessel': 'Vessel', 'aircraft': 'Aircraft'}.get(schema, entity_type), "source_type": source_type,
            "dataset": _safe_str(row.get("dataset")), "regime": _safe_str(row.get("regime") or _derive_regime_like_row(row)),
            "position": _safe_str(raw.get("positions") or row.get("positions")) or None,
            "name_score": score, "birth_date": candidate_dob or None, "country": candidate_country or None,
            "all_birth_dates": [d.strip() for d in re.split(r'[;|]', candidate_dob) if d.strip()] if candidate_dob else [],
            "alias_count": len(names) - 1,
            "dob_assessment": dob_status, "country_assessment": country_status,
            "disposition": "excluded" if excluded else "potential_match", "reasons": reasons,
        })
    if et == 'person':
        from linked_listings import apply_reviewed_links
        apply_reviewed_links(evidence, dob, _identity_dob_evidence)
    evidence.sort(key=lambda x: (x['disposition'] != 'potential_match', -x['name_score'], x['name']))
    sanctions = [e for e in evidence if e['disposition'] == 'potential_match' and e['source_type'] == 'sanctions']
    result = _empty_no_match_result(source_label)
    result['Top Matches'] = sorted(suggestions.items(), key=lambda x: (-x[1], x[0]))[:5]
    result['Candidate Evidence'] = evidence[:50]
    result['Candidate Evidence Summary'] = {'evaluated_name_matches': len(evidence), 'potential_matches': len(sanctions), 'excluded': sum(e['disposition'] == 'excluded' for e in evidence), 'display_truncated': len(evidence) > 50}
    result['Decision Explanation'] = 'No potential match identified under the current matching rules. This is not proof of identity.'
    if sanctions:
        best = sanctions[0]
        score = best['name_score']
        result.update({'Sanctions Name': best['name'], 'Birth Date': best['birth_date'], 'Regime': best['regime'],
                       'Is Sanctioned': True, 'Match Found': True,
                       'Position': best['position'], 'Score': score, 'Confidence': 'High' if score >= 90 else 'Medium' if score >= 80 else 'Low',
                       'Risk Level': 'High Risk'})
        result['Check Summary'].update(Status='Fail Sanction', Source='; '.join(dict.fromkeys(e['dataset'] or source_label for e in sanctions)))
        result['Decision Explanation'] = 'Potential sanctions match; analyst verification is required.'
    return result


async def _retrieve_candidates_v2(conn, name, entity_type, full_dob, limit):
    """Fetch rows sharing enough similar words with the query, plus the query's word statistics."""
    from name_matching import TokenStats, retrieval_plan, similar_tokens

    if not await conn.fetchval("SELECT to_regclass('public.watchlist_tokens') IS NOT NULL") or \
            not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM watchlist_tokens)"):
        raise ScreeningUnavailableError("Matching index is unavailable. No clearance issued.")
    plan = retrieval_plan(name, entity_type)
    kind = "organization" if entity_type.lower() == "organization" else "person"
    frequencies = {r["token"]: r["doc_frequency"] for r in await conn.fetch(
        "SELECT token, doc_frequency FROM watchlist_tokens WHERE token = ANY($1::text[])", plan.query_tokens)}
    total = await conn.fetchval("SELECT count(*) FROM watchlist_entities WHERE source_type='sanctions'")
    stats = TokenStats(frequencies, total)
    if not plan.query_tokens:
        return [], stats
    matched_tokens, matched_positions = [], []
    for position in plan.informative:
        token = plan.query_tokens[position]
        nearby = await conn.fetch(
            """SELECT token FROM watchlist_tokens WHERE token = $1 OR (length($1) >= 4 AND (token % $1 OR
               (left(token, 1) = left($1, 1) AND length(token) BETWEEN length($1) - 2 AND length($1) + 2)))""", token)
        for match in similar_tokens(token, [r["token"] for r in nearby]):
            matched_tokens.append(match)
            matched_positions.append(position)
    # Core candidates follow v1's retrieval rules, so v2 keeps everything v1 would find,
    # and exceeding the limit here refuses the search exactly as v1 does.
    query_norm = _normalize_text(name)
    candidate_ids = {r["entity_id"] for r in await conn.fetch(
        """SELECT DISTINCT entity_id FROM watchlist_names
           WHERE entity_kind = $1 AND (name_norm % $2 OR strpos(name_norm, $2) > 0) LIMIT $3""",
        kind, query_norm, limit + 1)} if query_norm else set()
    if kind == "person" and len(set(plan.query_tokens)) >= 2:
        candidate_ids |= {r["entity_id"] for r in await conn.fetch(
            """SELECT entity_id FROM watchlist_name_tokens WHERE entity_kind = 'person' AND token = ANY($1::text[])
               GROUP BY entity_id HAVING count(DISTINCT token) >= 2 LIMIT $2""", plan.query_tokens, limit + 1)}
    if len(candidate_ids) > limit:
        raise ScreeningUnavailableError("Too many possible candidates to complete screening. Refine the name and try again.")
    # Spelling variations are additions, so an overflow keeps the closest rather than refusing.
    candidate_ids |= {r["entity_id"] for r in await conn.fetch(
        """SELECT t.entity_id FROM watchlist_name_tokens t
           JOIN unnest($1::text[], $2::int[]) AS q(token, position) USING (token)
           WHERE t.entity_kind = $3 GROUP BY t.entity_id
           HAVING count(DISTINCT q.position) >= $4
           ORDER BY count(DISTINCT q.position) DESC, t.entity_id LIMIT $5""",
        matched_tokens, matched_positions, kind, plan.required_matches, limit)}
    if full_dob and kind == "person":
        # A single listed word plus an exact date of birth stays reviewable.
        candidate_ids |= {r["entity_id"] for r in await conn.fetch(
            """SELECT e.entity_id FROM watchlist_entities e
               JOIN watchlist_name_tokens t ON t.entity_id = e.entity_id AND t.entity_kind = 'person'
               WHERE t.token = ANY($1::text[]) AND strpos(COALESCE(e.raw_json->>'birth_date', ''), $2) > 0""",
            plan.query_tokens, full_dob)}
    rows = await conn.fetch(
        """SELECT entity_id, name, birth_date::text AS birth_date, dataset, regime, entity_schema, raw_json, source_type
           FROM watchlist_entities WHERE source_type = 'sanctions' AND entity_id = ANY($1::text[])""",
        list(candidate_ids))
    return [dict(r) for r in rows], stats


async def perform_postgres_watchlist_check(conn, name, dob, country=None, entity_type="Person", requestor=None,
                                          candidate_limit=2000, log_search=True, include_peps=False, submitted_entity_type=None):
    country = effective_screening_country(country)
    if not await conn.fetchval("SELECT to_regclass('public.watchlist_entities') IS NOT NULL"):
        raise ScreeningUnavailableError("Watchlist data is unavailable.")
    sources = await conn.fetch("SELECT DISTINCT source_type FROM watchlist_entities")
    available = {r['source_type'] for r in sources}
    if 'sanctions' not in available:
        raise ScreeningUnavailableError("Sanctions data is unavailable.")
    # Retrieval must not suppress missing/conflicting identity evidence. Apply type
    # before LIMIT so unrelated schemas cannot crowd out eligible candidates.
    limit = max(1, min(2000, int(candidate_limit)))
    schemas = ['organization', 'company', 'legalentity', 'vessel', 'aircraft'] if entity_type.lower() == 'organization' else ['person']
    normalized_dob = _normalize_dob(dob)
    full_dob = normalized_dob if normalized_dob and len(normalized_dob) == 10 and entity_type.lower() != 'organization' else None
    query_tokens = _normalize_text(re.sub(r"[-‐‑‒–—]", " ", name)).split()
    if active_matcher_version() == "v2":
        rows, token_stats = await _retrieve_candidates_v2(conn, name, entity_type, full_dob, limit)
        result = _evaluate_watchlist_rows(name, dob, country, entity_type, rows, 'Postgres watchlist', token_stats=token_stats, submitted_entity_type=submitted_entity_type)
        if log_search:
            _append_search_to_csv(name, result['Check Summary'])
        return result
    rows = []
    for source_type in ['sanctions']:
        candidates = await conn.fetch('''
            SELECT entity_id, name, birth_date::text AS birth_date, dataset, regime,
                   entity_schema, raw_json, source_type
            FROM watchlist_entities
            WHERE source_type=$1 AND lower(entity_schema)=ANY($3::text[])
              AND (name_norm % $2 OR strpos(name_norm, $2)>0
                   OR ($7::boolean AND cardinality(ARRAY(
                       SELECT DISTINCT token FROM regexp_split_to_table(
                           lower(trim(regexp_replace(regexp_replace(normalize(
                               regexp_replace(name, '[-‐‑‒–—;]', ' ', 'g'), NFKD) COLLATE "C",
                               '[^a-zA-Z0-9_[:space:]]', '', 'g'), '[[:space:]]+', ' ', 'g'))), ' ') AS token
                       WHERE token = ANY($6::text[]) AND token <> ''
                   )) >= 2)
                   OR EXISTS (SELECT 1 FROM (
                                  SELECT unnest(string_to_array(COALESCE(raw_json->>'aliases',''), ';')) AS alias
                                  WHERE strpos(COALESCE(raw_json->>'aliases',''), '"') = 0
                                  UNION ALL
                                  SELECT replace(COALESCE(matched[3],matched[4]), '""', '"') AS alias
                                  FROM regexp_matches(COALESCE(raw_json->>'aliases',''),
                                      '(^|;)("((?:[^"]|"")*)"|([^;]*))', 'g') AS matched
                                  WHERE strpos(COALESCE(raw_json->>'aliases',''), '"') > 0
                              ) alias_values
                              CROSS JOIN LATERAL (SELECT lower(trim(regexp_replace(
                                  regexp_replace(normalize(replace(alias,';',' '), NFKD) COLLATE "C", '[^a-zA-Z0-9_[:space:]]', '', 'g'),
                                  '[[:space:]]+', ' ', 'g'))) AS alias_norm) normalized
                              WHERE similarity(lower(alias), $2)>=0.3 OR strpos(lower(alias), $2)>0
                                 OR similarity(alias_norm, $2)>=0.3 OR strpos(alias_norm, $2)>0
                                 OR ($7::boolean AND cardinality(ARRAY(
                                     SELECT DISTINCT token FROM regexp_split_to_table(
                                         lower(trim(regexp_replace(regexp_replace(normalize(
                                             regexp_replace(alias, '[-‐‑‒–—;]', ' ', 'g'), NFKD) COLLATE "C",
                                             '[^a-zA-Z0-9_[:space:]]', '', 'g'), '[[:space:]]+', ' ', 'g'))), ' ') AS token
                                     WHERE token = ANY($6::text[]) AND token <> ''
                                 )) >= 2)
                                 OR ($5::text IS NOT NULL AND
                                     (birth_date::text=$5 OR EXISTS (SELECT 1 FROM regexp_split_to_table(
                                         COALESCE(raw_json->>'birth_date',''), '[;|]') AS source_dob WHERE trim(source_dob)=$5)) AND
                                     alias_norm=ANY($6::text[])))
                   OR ($5::text IS NOT NULL AND
                       (birth_date::text=$5 OR EXISTS (SELECT 1 FROM regexp_split_to_table(
                           COALESCE(raw_json->>'birth_date',''), '[;|]') AS source_dob WHERE trim(source_dob)=$5)) AND
                       name_norm=ANY($6::text[])))
            ORDER BY similarity(name_norm, $2) DESC, entity_id
            LIMIT $4
        ''', source_type, _normalize_text(name), schemas, limit + 1, full_dob, query_tokens,
            entity_type.lower() != 'organization' and len(set(query_tokens)) >= 2)
        if len(candidates) > limit:
            raise ScreeningUnavailableError("Too many possible candidates to complete screening. Refine the name and try again.")
        rows.extend(dict(r) for r in candidates)
    result = _evaluate_watchlist_rows(name, dob, country, entity_type, rows, 'Postgres watchlist', submitted_entity_type=submitted_entity_type)
    if log_search:
        _append_search_to_csv(name, result['Check Summary'])
    return result
