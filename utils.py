# utils.py — lean, low-RAM OpenSanctions + PEPs utils

import os
import csv
import time
from datetime import datetime
from functools import lru_cache
from typing import Optional, List, Tuple, Dict, Any

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

# Latest consolidated dumps
CONSOLIDATED_SANCTIONS_URL = (
    "https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv"
)
CONSOLIDATED_PEPS_URL = (
    "https://data.opensanctions.org/datasets/latest/peps/targets.simple.csv"
)

# We only keep these columns to reduce memory
OSN_COLS = [
    "schema",       # Person / Organization / Company / LegalEntity
    "name",         # display name
    "aliases",
    "birth_date",
    "program_ids",
    "dataset",      # dataset label/source
    "sanctions",    # long text; we’ll trim for a short label
    "source_type",  # we add this: "sanctions" | "peps"
]

# Restrict sanctions data to these sources only (case-insensitive substring match)
SANCTIONS_DATASET_ALLOWLIST = [
    "United Nations",                          # UN Security Council
    "OFAC",                                    # US OFAC (SDN etc.)
    "HM Treasury", "HMT", "UK Financial",      # UK HMT/OFSI lists
    "EU Council", "EU Financial Sanctions",    # EU lists
]

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
        "Confidence": "Very High",  # confidence of 'no match'
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

    if not parts:
        print("[OpenSanctions] No data written (nothing downloaded).")
        return

    df = pd.concat(parts, ignore_index=True)

    # Write compact parquet
    table = pa.Table.from_pandas(df)
    pq.write_table(table, OSN_PARQUET)
    print(f"[OpenSanctions] Parquet saved -> {OSN_PARQUET}")

    # clear cache
    clear_osn_cache()

# =============================================================================
# Loading & caching
# =============================================================================

@lru_cache(maxsize=1)
def get_opensanctions_df(parquet_path: str = OSN_PARQUET) -> pd.DataFrame:
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
    return df

def clear_osn_cache():
    get_opensanctions_df.cache_clear()

# =============================================================================
# Matching
# =============================================================================

def _normalize_dob(dob: Optional[str]) -> Optional[str]:
    if not dob:
        return None
    try:
        dt = pd.to_datetime(str(dob), errors="coerce")
        if pd.isna(dt):
            return None
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def derive_entity_key(display_name: str, entity_type: str, dob: Optional[str]) -> str:
    """
    Stable key for one logical entity. Identity is based on:
    - normalized name (Unicode-normalized, punctuation stripped, lowercased)
    - entity type (Person / Organization)
    - DOB if provided (YYYY-MM-DD)

    Same inputs => same key. Used for screening cache and job queue.
    Note: entities with only name (no DOB) may collide across different real-world persons;
    this is an accepted business trade-off for simplicity and deterministic reuse.
    """
    import hashlib
    norm_name = _normalize_text(display_name or "")
    et = (entity_type or "Person").strip().lower()
    dob_str = _normalize_dob(dob) if dob else ""
    payload = f"{norm_name}|{et}|{dob_str}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def get_best_name_matches(search_name: str, candidates: List[str], limit=50, threshold=80):
    """Robust fuzzy match with simple heuristics."""
    def preprocess(name):
        name = _normalize_text(name)
        blacklist = {
            "the","ltd","llc","inc","co","company","corp","plc","limited",
            "real","estate","group","services","solutions","hub","global",
            "trust","association","federation","union","committee","organization",
            "network","centre","center","international","foundation","institute","bank"
        }
        tokens = [w for w in name.split() if w and w not in blacklist]
        return " ".join(tokens), set(tokens)

    s_clean, s_tokens = preprocess(search_name)
    clean_candidates = [(i, *preprocess(c)) for i, c in enumerate(candidates)]
    results = []

    for idx, c_clean, c_tokens in clean_candidates:
        score = fuzz.token_set_ratio(s_clean, c_clean)
        if score < threshold:
            continue
        token_union = s_tokens | c_tokens
        overlap = len(s_tokens & c_tokens)
        jaccard = overlap / max(1, len(token_union))

        # exact(ish) short matches
        if len(s_tokens) <= 2 and s_clean == c_clean:
            results.append((c_clean, score, idx))
            continue

        # Allow short queries (e.g. "Putin") to match longer names; require overlap >= 1 for single-token search
        min_overlap = min(2, len(s_tokens))
        if overlap < min_overlap:
            continue
        if len(s_tokens) > 2 and jaccard < 0.4:
            continue
        if abs(len(s_tokens) - len(c_tokens)) > 2:
            score -= 15
        if len(c_tokens) <= 2 and len(s_tokens) > 3:
            score -= 20

        if score >= threshold:
            results.append((c_clean, score, idx))

    return sorted(results, key=lambda x: x[1], reverse=True)[:limit]

def _top_name_suggestions(
    df_subset: pd.DataFrame,
    search_name: str,
    limit: int = 5,
    threshold: int = 60
) -> List[Tuple[str, int]]:
    """
    Return up to 'limit' fuzzy suggestions [(name, score), ...] based ONLY on name similarity.
    DOB and other strict rules are ignored here so users can see likely intended names.
    """
    if df_subset is None or df_subset.empty:
        return []
    candidates = df_subset["name"].fillna("").tolist()
    hits = get_best_name_matches(search_name, candidates, limit=limit * 3, threshold=threshold)
    # Deduplicate by display name, keep highest score, then take top 'limit'
    seen: Dict[str, float] = {}
    for cleaned_name, score, idx in hits:
        display = str(df_subset.iloc[idx].get("name") or cleaned_name)
        if not display:
            continue
        if display not in seen or score > seen[display]:
            seen[display] = float(score)
    return [(n, int(s)) for n, s in sorted(seen.items(), key=lambda x: x[1], reverse=True)[:limit]]

def perform_opensanctions_check(
    name,
    dob,
    entity_type="Person",
    parquet_path=OSN_PARQUET,
    requestor: Optional[str] = None,
):
    import math

    df = get_opensanctions_df(parquet_path)
    if df.empty:
        result = _empty_no_match_result()
        _append_search_to_csv(name, result["Check Summary"])
        return result

    # Filter by entity type/schema
    et = (entity_type or "Person").lower()
    schema_col = df.get("schema")
    if schema_col is not None:
        schemas = schema_col.astype(str).str.lower()
        if et == "organization":
            df = df[schemas.isin(["organization", "legalentity", "company"])]
        else:
            df = df[schemas == "person"]

    # Precompute normalized search
    norm_name = _normalize_text(name)
    def _norm_dob(d):
        try:
            if not d:
                return None
            return pd.to_datetime(str(d), errors="coerce").strftime("%Y-%m-%d")
        except Exception:
            return None
    norm_dob = _norm_dob(dob)

    # Split by source type and build pool for suggestions
    st = df.get("source_type")
    if st is not None:
        st_lower = st.astype(str).str.lower()
        sanc_df = df[st_lower == "sanctions"]
        pep_df  = df[st_lower == "peps"]
        combined_for_suggestions = pd.concat([sanc_df, pep_df], ignore_index=True)
    else:
        sanc_df, pep_df = df, df.iloc[0:0]
        combined_for_suggestions = df

    # Suggestions are based only on name similarity and do NOT affect result
    top_suggestions = _top_name_suggestions(
        combined_for_suggestions, norm_name, limit=5, threshold=60
    )

    def parse_dob(val):
        try:
            s = str(val)
            if not s or s.lower() in ("nan", "none", "nat"):
                return None
            return _norm_dob(s)
        except Exception:
            return None

    def as_safe_str(x):
        if x is None:
            return ""
        if isinstance(x, float) and math.isnan(x):
            return ""
        return str(x)

    def best_match_from(df_subset, top_limit=50, threshold=75):
        """Return (row, score) using strict DOB rule if norm_dob is provided."""
        if df_subset is None or df_subset.empty:
            return None, None

        candidates = df_subset["name"].fillna("").tolist()
        matches = get_best_name_matches(norm_name, candidates, limit=top_limit, threshold=threshold)
        if not matches:
            return None, None

        # DOB strictness for the ACTUAL result
        if norm_dob:
            dob_ok_matches = []
            for _, score, idx in matches:
                r = df_subset.iloc[idx]
                cand_dob = parse_dob(r.get("birth_date"))
                if cand_dob and cand_dob == norm_dob:
                    dob_ok_matches.append((_, score, idx))
            if not dob_ok_matches:
                return None, None
            matches = dob_ok_matches

        matches_sorted = sorted(matches, key=lambda x: x[1], reverse=True)
        _, best_score, best_idx = matches_sorted[0]
        best_row = df_subset.iloc[best_idx]
        return best_row, float(best_score)

    # Evaluate both lists; sanctions remains the controlling outcome when both match.
    s_row, s_score = best_match_from(sanc_df)
    p_row, p_score = best_match_from(pep_df)

    if s_row is not None:
        dataset_label = as_safe_str(s_row.get("dataset")).strip()
        source_label = dataset_label or "OpenSanctions – Sanctions"
        if p_row is not None:
            source_label = f"{source_label}; Consolidated PEP list"
        result = {
            "Sanctions Name": s_row.get("name"),
            "Birth Date": parse_dob(s_row.get("birth_date")),
            "Regime": _derive_regime_like_row(s_row),
            "Position": s_row.get("positions"),
            "Topics": [],
            "Is PEP": bool(p_row is not None),
            "Is Sanctioned": True,
            "Confidence": "High" if s_score >= 90 else "Medium" if s_score >= 80 else "Low",
            "Score": s_score,
            "Risk Level": "High Risk",
            "Top Matches": top_suggestions,  # suggestions only
            "Match Found": True,
            "Check Summary": {
                "Status": "Fail Sanction",
                "Source": source_label,
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
        _append_search_to_csv(name, result["Check Summary"])
        return result

    # No sanctions hit: return PEP outcome if matched.
    if p_row is not None:
        result = {
            "Sanctions Name": p_row.get("name"),
            "Birth Date": parse_dob(p_row.get("birth_date")),
            "Regime": _derive_regime_like_row(p_row),
            "Position": p_row.get("positions"),
            "Topics": [],
            "Is PEP": True,
            "Is Sanctioned": False,
            "Confidence": "High" if p_score >= 90 else "Medium" if p_score >= 80 else "Low",
            "Score": p_score,
            "Risk Level": "Medium Risk",
            "Top Matches": top_suggestions,  # suggestions only
            "Match Found": True,
            "Check Summary": {
                "Status": "Fail PEP",
                "Source": "Consolidated PEP list",
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
        _append_search_to_csv(name, result["Check Summary"])
        return result

    # Nothing matched under strict rules -> Cleared, but STILL return suggestions
    result = _empty_no_match_result(source_label="OpenSanctions")
    result["Top Matches"] = top_suggestions
    _append_search_to_csv(name, result["Check Summary"])
    return result


async def perform_postgres_watchlist_check(
    conn,
    name: str,
    dob: Optional[str],
    entity_type: str = "Person",
    requestor: Optional[str] = None,
    candidate_limit: int = 400,
) -> Dict[str, Any]:
    """
    Beta matcher using watchlist_entities in PostgreSQL.
    Designed for side-by-side rollout with parquet path.
    """
    import math

    exists = await conn.fetchval("SELECT to_regclass('public.watchlist_entities') IS NOT NULL")
    if not exists:
        raise ValueError("watchlist_entities table is not available")

    norm_name = _normalize_text(name or "")
    norm_dob = _normalize_dob(dob)
    et = (entity_type or "Person").strip().lower()
    source_filter = "person" if et != "organization" else "organization"

    async def _fetch_candidates(source_type: str) -> List[dict]:
        rows = await conn.fetch(
            """
            SELECT
                name,
                birth_date::text AS birth_date,
                dataset,
                regime,
                entity_schema,
                raw_json
            FROM watchlist_entities
            WHERE source_type = $1
              AND (
                name_norm % $2
                OR name_norm ILIKE ('%' || $2 || '%')
              )
              AND (
                $3::text IS NULL
                OR birth_date::text = $3::text
                OR birth_date IS NULL
              )
            ORDER BY similarity(name_norm, $2) DESC
            LIMIT $4
            """,
            source_type,
            norm_name,
            norm_dob,
            max(50, min(2000, int(candidate_limit))),
        )
        out: List[dict] = []
        for r in rows:
            d = dict(r)
            schema = str(d.get("entity_schema") or "").lower()
            if source_filter == "person" and schema and schema != "person":
                continue
            if source_filter == "organization" and schema and schema not in ("organization", "legalentity", "company"):
                continue
            out.append(d)
        return out

    sanc_rows = await _fetch_candidates("sanctions")
    pep_rows = await _fetch_candidates("peps")
    combined = sanc_rows + pep_rows

    if not combined:
        result = _empty_no_match_result()
        _append_search_to_csv(name, result["Check Summary"])
        return result

    def _parse_dob(val) -> Optional[str]:
        try:
            if val is None:
                return None
            s = str(val)
            if not s or s.lower() in ("nan", "none", "nat"):
                return None
            return _normalize_dob(s)
        except Exception:
            return None

    def _as_safe_str(x) -> str:
        if x is None:
            return ""
        if isinstance(x, float) and math.isnan(x):
            return ""
        return str(x)

    def _best_match_from_rows(rows: List[dict], threshold: int = 75):
        if not rows:
            return None, None
        candidates = [_as_safe_str(r.get("name")) for r in rows]
        matches = get_best_name_matches(norm_name, candidates, limit=50, threshold=threshold)
        if not matches:
            return None, None
        if norm_dob:
            dob_ok = []
            for _, score, idx in matches:
                cand_dob = _parse_dob(rows[idx].get("birth_date"))
                if cand_dob and cand_dob == norm_dob:
                    dob_ok.append((_, score, idx))
            if not dob_ok:
                return None, None
            matches = dob_ok
        _, best_score, best_idx = sorted(matches, key=lambda x: x[1], reverse=True)[0]
        return rows[best_idx], float(best_score)

    top_suggestions: List[Tuple[str, int]] = []
    candidate_names = [_as_safe_str(r.get("name")) for r in combined if _as_safe_str(r.get("name"))]
    if candidate_names:
        hits = get_best_name_matches(norm_name, candidate_names, limit=15, threshold=60)
        seen: Dict[str, float] = {}
        for cleaned_name, score, idx in hits:
            display = candidate_names[idx] if idx < len(candidate_names) else cleaned_name
            if display and (display not in seen or score > seen[display]):
                seen[display] = float(score)
        top_suggestions = [(n, int(s)) for n, s in sorted(seen.items(), key=lambda x: x[1], reverse=True)[:5]]

    s_row, s_score = _best_match_from_rows(sanc_rows)
    p_row, p_score = _best_match_from_rows(pep_rows)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if s_row is not None:
        source_label = (_as_safe_str(s_row.get("dataset")).strip() or "Postgres sanctions table")
        if p_row is not None:
            source_label = f"{source_label}; Consolidated PEP list"
        result = {
            "Sanctions Name": s_row.get("name"),
            "Birth Date": _parse_dob(s_row.get("birth_date")),
            "Regime": s_row.get("regime") or s_row.get("dataset"),
            "Position": ((s_row.get("raw_json") or {}).get("positions") if isinstance(s_row.get("raw_json"), dict) else None),
            "Topics": [],
            "Is PEP": bool(p_row is not None),
            "Is Sanctioned": True,
            "Confidence": "High" if s_score >= 90 else "Medium" if s_score >= 80 else "Low",
            "Score": s_score,
            "Risk Level": "High Risk",
            "Top Matches": top_suggestions,
            "Match Found": True,
            "Check Summary": {"Status": "Fail Sanction", "Source": source_label, "Date": now_str},
        }
        _append_search_to_csv(name, result["Check Summary"])
        return result

    if p_row is not None:
        result = {
            "Sanctions Name": p_row.get("name"),
            "Birth Date": _parse_dob(p_row.get("birth_date")),
            "Regime": p_row.get("regime") or p_row.get("dataset"),
            "Position": ((p_row.get("raw_json") or {}).get("positions") if isinstance(p_row.get("raw_json"), dict) else None),
            "Topics": [],
            "Is PEP": True,
            "Is Sanctioned": False,
            "Confidence": "High" if p_score >= 90 else "Medium" if p_score >= 80 else "Low",
            "Score": p_score,
            "Risk Level": "Medium Risk",
            "Top Matches": top_suggestions,
            "Match Found": True,
            "Check Summary": {"Status": "Fail PEP", "Source": "Consolidated PEP list", "Date": now_str},
        }
        _append_search_to_csv(name, result["Check Summary"])
        return result

    result = _empty_no_match_result(source_label="Postgres watchlist")
    result["Top Matches"] = top_suggestions
    _append_search_to_csv(name, result["Check Summary"])
    return result
