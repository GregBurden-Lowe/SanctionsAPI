# utils.py — lean, low-RAM OpenSanctions utils (no PDF)
# - Sanctions sources: UN, EU, OFAC, UK HMT/OFSI (filtered)
# - Optional PEPs (consolidated)
# - DOB exact-match required when DOB provided
# - Compact parquet, cached load
# - Search logging with requestor

import os
import csv
from datetime import datetime
from functools import lru_cache
from typing import Optional, List, Tuple

import pandas as pd
import requests
import pyarrow as pa
import pyarrow.parquet as pq
from rapidfuzz import fuzz


# ---------------------------
# Configuration
# ---------------------------

DATA_DIR = "data"
OSN_PARQUET = os.path.join(DATA_DIR, "opensanctions.parquet")

# Latest consolidated datasets
CONSOLIDATED_SANCTIONS_URL = "https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv"
CONSOLIDATED_PEPS_URL = "https://data.opensanctions.org/datasets/latest/peps/targets.simple.csv"

# Keep only columns we actually use
OSN_COLS = [
    "schema",       # Person / Organization / Company / LegalEntity
    "name",         # primary display name
    "aliases",      # aliases
    "birth_date",   # ISO or partial
    "program_ids",  # e.g. "EU-UKR;SECO-UKRAINE;UA-SA1644"
    "dataset",      # dataset label (e.g. "EU Financial Sanctions Files (FSF)")
    "sanctions",    # long text; we only take a short first chunk
    # extra (we add at refresh time):
    "source_type",  # "sanctions" or "peps"
]

# Sanction dataset filter (choose only these families)
SANCTION_DATASET_KEYWORDS = (
    "united nations",        # UN
    "security council",      # UN/UNSC
    "ofac",                  # US OFAC (SDN & non-SDN)
    "consolidated (non-sdn)",# US OFAC consolidated
    "sdn",                   # US OFAC SDN
    "financial sanctions files",  # EU FSF
    "eu council",            # EU OJ
    "uk sanctions list",     # UK HMT OFSI
    "hm treasury",           # UK HMT
    "ofsi",                  # UK OFSI
)

# ---------------------------
# Small helpers
# ---------------------------

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
    Create a short label for UI, prioritizing:
      1) program_ids (first token)
      2) sanctions (first ';' chunk or first line)
      3) dataset
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

def _top_matches_list(matches: List[Tuple[str, float, int]], df: pd.DataFrame, limit: int = 10) -> List[Tuple[str, float]]:
    out = []
    for _, score, idx in matches[:limit]:
        name = _safe_str(df.iloc[idx].get("name"))
        out.append((name, round(float(score), 1)))
    return out

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

def _append_search_to_csv(name, summary, requestor: Optional[str], path=os.path.join(DATA_DIR, "search_log.csv")):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        file_exists = os.path.isfile(path)
        with open(path, mode="a", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=["Date", "Name Searched", "Status", "Source", "Requestor"])
            if not file_exists:
                writer.writeheader()
            writer.writerow({
                "Date":       summary.get("Date"),
                "Name Searched": name,
                "Status":     summary.get("Status"),
                "Source":     summary.get("Source"),
                "Requestor":  requestor or "",
            })
    except Exception:
        # best-effort logging only
        pass

# ---------------------------
# Data refresh (download -> filter -> parquet)
# ---------------------------

def _download_csv(url: str, dest_path: str, timeout: int = 240):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(r.content)

def _ensure_cols(df: pd.DataFrame) -> pd.DataFrame:
    for c in OSN_COLS:
        if c not in df.columns and c != "source_type":
            df[c] = pd.Series(dtype="string")
    return df[[c for c in OSN_COLS if c != "source_type"]].copy()

def _filter_sanction_datasets(df: pd.DataFrame) -> pd.DataFrame:
    # keep rows where dataset contains any of the keywords
    ds = df["dataset"].astype(str).str.lower().fillna("")
    mask = False
    for kw in SANCTION_DATASET_KEYWORDS:
        mask = mask | ds.str.contains(kw, na=False)
    return df[mask]

def refresh_opensanctions_data(include_peps: bool = True):
    """
    Download latest consolidated sanctions (filtered to UN/EU/OFAC/UK),
    and optionally consolidated PEPs. Write a compact parquet and clear cache.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    parts = []

    # Sanctions
    try:
        sanc_csv = os.path.join(DATA_DIR, "os_sanctions_latest.csv")
        _download_csv(CONSOLIDATED_SANCTIONS_URL, sanc_csv, timeout=360)
        df_s = pd.read_csv(sanc_csv, low_memory=False)
        df_s = _ensure_cols(df_s)
        df_s = _filter_sanction_datasets(df_s)
        df_s["source_type"] = "sanctions"
        parts.append(df_s)
        print(f"[OpenSanctions] Sanctions filtered rows: {len(df_s):,}")
    except Exception as e:
        print(f"[OpenSanctions] Sanctions download/parsing failed: {e}")

    # PEPs (optional)
    if include_peps:
        try:
            peps_csv = os.path.join(DATA_DIR, "os_peps_latest.csv")
            _download_csv(CONSOLIDATED_PEPS_URL, peps_csv, timeout=360)
            df_p = pd.read_csv(peps_csv, low_memory=False)
            df_p = _ensure_cols(df_p)
            df_p["source_type"] = "peps"
            parts.append(df_p)
            print(f"[OpenSanctions] PEP rows: {len(df_p):,}")
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

    # clear cache so next request sees the new file
    clear_osn_cache()

# ---------------------------
# Loading & caching
# ---------------------------

@lru_cache(maxsize=1)
def get_opensanctions_df(parquet_path: str = OSN_PARQUET) -> pd.DataFrame:
    """
    Load parquet once, project required columns, and precompute normalized fields.
    Uses Arrow-backed dtypes to keep memory small.
    """
    if not os.path.exists(parquet_path):
        return pd.DataFrame(columns=OSN_COLS + ["name_norm", "birth_norm", "source_type"])

    table = pq.read_table(parquet_path)
    # ensure all required columns exist in the table
    have = set(table.column_names)
    arrays, names = [], []
    for c in OSN_COLS:
        if c in have:
            arrays.append(table[c])
        else:
            arrays.append(pa.array([""] * len(table)))
        names.append(c)
    table2 = pa.Table.from_arrays(arrays, names=names)

    # Convert to pandas (ArrowDtype to reduce memory)
    df = table2.to_pandas(types_mapper=pd.ArrowDtype)

    # Precompute normalized fields
    df["name_norm"] = df["name"].astype("string[pyarrow]").fillna("").map(str).map(_normalize_text)
    df["birth_norm"] = pd.to_datetime(
        df["birth_date"].astype("string[pyarrow]").fillna(""),
        errors="coerce"
    ).dt.strftime("%Y-%m-%d")

    # Ensure flags exist
    if "source_type" not in df.columns:
        df["source_type"] = "sanctions"
    df["source_type"] = df["source_type"].astype("string[pyarrow]").fillna("")
    return df

def clear_osn_cache():
    get_opensanctions_df.cache_clear()

def load_opensanctions_from_parquet(parquet_path: str = OSN_PARQUET) -> pd.DataFrame:
    """
    Legacy loader (used by some code paths). Prefer get_opensanctions_df().
    """
    try:
        return pd.read_parquet(parquet_path)
    except Exception as e:
        print(f"[OpenSanctions] Failed to load parquet: {e}")
        return pd.DataFrame()

# ---------------------------
# Matching
# ---------------------------

def get_best_name_matches(search_name, candidates, limit=50, threshold=80):
    """Robust fuzzy match with basic heuristics."""
    def preprocess(name):
        name = _normalize_text(name)
        blacklist = {
            "the","ltd","llc","inc","co","company","corp","plc","limited",
            "real","estate","group","services","solutions","hub","global",
            "trust","association","federation","union","committee","organization",
            "network","centre","center","international","foundation","institute","bank"
        }
        tokens = [w for w in name.split() if w not in blacklist]
        return " ".join(tokens), set(tokens)

    search_cleaned, search_tokens = preprocess(search_name)
    clean_candidates = [(i, *preprocess(c)) for i, c in enumerate(candidates)]
    results = []

    for idx, candidate_cleaned, candidate_tokens in clean_candidates:
        score = fuzz.token_set_ratio(search_cleaned, candidate_cleaned)
        if score < threshold:
            continue
        token_union = search_tokens | candidate_tokens
        overlap = len(search_tokens & candidate_tokens)
        jaccard = overlap / max(1, len(token_union))
        if len(search_tokens) <= 2 and search_cleaned == candidate_cleaned:
            results.append((candidate_cleaned, score, idx)); continue
        if overlap < 2 or jaccard < 0.4:
            continue
        if abs(len(search_tokens) - len(candidate_tokens)) > 2:
            score -= 15
        if len(candidate_tokens) <= 2 and len(search_tokens) > 3:
            score -= 20
        if score >= threshold:
            results.append((candidate_cleaned, score, idx))

    return sorted(results, key=lambda x: x[1], reverse=True)[:limit]

def perform_opensanctions_check(
    name: str,
    dob: Optional[str],
    entity_type: str = "Person",
    requestor: Optional[str] = None,
    parquet_path: str = OSN_PARQUET,
):
    """
    Main screening:
      - If DOB provided, require exact DOB among candidate matches.
      - Try sanctions first; if none (after DOB strictness), try PEPs.
    """
    df = get_opensanctions_df(parquet_path)
    if df.empty:
        return {"error": "No data available. Please refresh datasets."}

    # Filter by schema (entity type)
    et = (entity_type or "Person").lower()
    schemas = df["schema"].astype(str).str.lower()
    if et == "organization":
        mask = schemas.isin(["organization", "legalentity", "company"])
    else:
        mask = (schemas == "person")
    df = df[mask]
    if df.empty:
        result = _empty_no_match_result()
        _append_search_to_csv(name, result["Check Summary"], requestor)
        return result

    norm_name = _normalize_text(name)
    norm_dob = None
    if dob:
        try:
            norm_dob = pd.to_datetime(dob, errors="coerce")
            norm_dob = None if pd.isna(norm_dob) else norm_dob.strftime("%Y-%m-%d")
        except Exception:
            norm_dob = None

    def parse_dob(val):
        try:
            s = _safe_str(val)
            if not s:
                return None
            dt = pd.to_datetime(s, errors="coerce")
            return None if pd.isna(dt) else dt.strftime("%Y-%m-%d")
        except Exception:
            return None

    def best_match_from(df_subset: pd.DataFrame, top_limit=50, threshold=78):
        """Return (row, score, top_matches_list) or (None, None, []), honoring DOB strictness."""
        if df_subset is None or df_subset.empty:
            return None, None, []

        candidates = df_subset["name"].fillna("").tolist()
        matches = get_best_name_matches(norm_name, candidates, limit=top_limit, threshold=threshold)
        if not matches:
            return None, None, []

        # If DOB provided, require exact DOB match among the matched indices.
        if norm_dob:
            dob_ok = []
            for cleaned_name, score, idx in matches:
                cand_dob = parse_dob(df_subset.iloc[idx].get("birth_date"))
                if cand_dob and cand_dob == norm_dob:
                    dob_ok.append((cleaned_name, score, idx))
            if not dob_ok:
                return None, None, []
            matches = dob_ok

        best = sorted(matches, key=lambda x: x[1], reverse=True)[0]
        _, best_score, best_idx = best
        top_list = _top_matches_list(matches, df_subset, limit=10)
        return df_subset.iloc[best_idx], float(best_score), top_list

    # Split by source_type
    st_lower = df["source_type"].astype(str).str.lower()
    sanc_df = df[st_lower == "sanctions"]
    pep_df  = df[st_lower == "peps"]

    # Try sanctions first
    s_row, s_score, s_top = best_match_from(sanc_df)
    if s_row is not None:
        source_label = _safe_str(s_row.get("dataset")).strip() or "OpenSanctions – Sanctions"
        result = {
            "Sanctions Name": s_row.get("name"),
            "Birth Date": parse_dob(s_row.get("birth_date")),
            "Regime": _derive_regime_like_row(s_row),
            "Position": s_row.get("positions"),
            "Topics": [],
            "Is PEP": False,
            "Is Sanctioned": True,
            "Confidence": "High" if s_score >= 90 else "Medium" if s_score >= 80 else "Low",
            "Score": s_score,
            "Risk Level": "High Risk",
            "Top Matches": s_top,
            "Match Found": True,
            "Check Summary": {
                "Status": "Fail Sanction",
                "Source": source_label,
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
        _append_search_to_csv(name, result["Check Summary"], requestor)
        return result

    # Otherwise, try PEPs
    p_row, p_score, p_top = best_match_from(pep_df)
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
            "Top Matches": p_top,
            "Match Found": True,
            "Check Summary": {
                "Status": "Fail PEP",
                "Source": "Consolidated PEP list",
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
        _append_search_to_csv(name, result["Check Summary"], requestor)
        return result

    # Nothing matched (or DOB strictness eliminated candidates)
    result = _empty_no_match_result()
    _append_search_to_csv(name, result["Check Summary"], requestor)
    return result
