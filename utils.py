# utils.py — lean, low-RAM OpenSanctions + PEPs utils with Power Automate hook

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
import threading

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
    "HM Treasury", "HMT", "UK Financial",      # UK HMT
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

def _top_matches_list(df: pd.DataFrame, limit: int = 10) -> List[Tuple[str, float]]:
    out: List[Tuple[str, float]] = []
    if df is None or df.empty:
        return out
    rows = df.head(limit).itertuples(index=False)
    for r in rows:
        name = getattr(r, "name", None)
        score = getattr(r, "score", None)
        if name is None or score is None:
            continue
        try:
            out.append((_safe_str(name), float(score)))
        except Exception:
            continue
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

def _download_csv(url: str, dest_path: str, timeout: int = 240):
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

# Backward-compat helper (if you still call it elsewhere)
def load_opensanctions_from_parquet(parquet_path: str = OSN_PARQUET) -> pd.DataFrame:
    return get_opensanctions_df(parquet_path)

# =============================================================================
# Matching
# =============================================================================

def _normalize_dob(dob: Optional[str]) -> Optional[str]:
    if not dob:
        return None
    try:
        # accept YYYY-MM or YYYY too
        dt = pd.to_datetime(str(dob), errors="coerce")
        if pd.isna(dt):
            return None
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None

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

        if overlap < 2 or jaccard < 0.4:
            continue
        if abs(len(s_tokens) - len(c_tokens)) > 2:
            score -= 15
        if len(c_tokens) <= 2 and len(s_tokens) > 3:
            score -= 20

        if score >= threshold:
            results.append((c_clean, score, idx))

    return sorted(results, key=lambda x: x[1], reverse=True)[:limit]

def _suggest_name_matches(search_name: str, candidates: List[str], limit: int = 10):
    """
    Lenient, *assistive* suggestions for Top Matches:
    - Uses partial and token-sort ratios (no jaccard/token-overlap gating)
    - Strong bonus if candidate startswith the query (prefix/partial typing)
    - Keeps only reasonable scores (>=55) and returns top 'limit'
    Returns: list of tuples (idx, score)
    """
    s = _normalize_text(search_name)
    if not s:
        return []

    pairs = []
    for i, c in enumerate(candidates):
        cn = _normalize_text(c)
        if not cn:
            continue
        # partial is great for substrings; token_sort helps with transpositions
        score = max(
            fuzz.partial_ratio(s, cn),
            fuzz.token_sort_ratio(s, cn),
            fuzz.token_set_ratio(s, cn)
        )
        # strong boost if user typed a prefix of the name
        if cn.startswith(s):
            score = max(score, 95)
        if score >= 55:
            pairs.append((i, score))

    # sort by score desc and keep top N
    pairs.sort(key=lambda x: x[1], reverse=True)
    return pairs[:limit]



def _top_name_suggestions(
    df_subset: pd.DataFrame,
    search_name: str,
    limit: int = 5,
    threshold: int = 60,
) -> List[Tuple[str, int]]:
    """
    Relaxed suggestions for UI only (do NOT affect result):
    - Name similarity only (no DOB, no 2-token overlap rule)
    - Uses a 'best of' RapidFuzz score (partial_ratio / token_set_ratio / token_sort_ratio)
    - Deduplicates by display name and returns top N as (name, score)
    """
    if df_subset is None or df_subset.empty:
        return []

    # Build candidate list (keep original display names for dedup/display)
    candidates = df_subset["name"].fillna("").astype(str).tolist()
    if not candidates:
        return []

    def best_score(a: str, b: str) -> int:
        # Fast, robust scoring for suggestions
        s1 = fuzz.partial_ratio(a, b)
        s2 = fuzz.token_set_ratio(a, b)
        s3 = fuzz.token_sort_ratio(a, b)
        return max(s1, s2, s3)

    # Score all candidates (could be ~tens of thousands, still OK)
    # If you want to cap runtime, slice candidates[:N] here.
    scored: List[Tuple[str, int]] = []
    q = str(search_name or "")
    for cand in candidates:
        if not cand:
            continue
        sc = best_score(q, cand)
        if sc >= threshold:
            scored.append((cand, sc))

    if not scored:
        return []

    # Deduplicate by display name keeping highest score
    best_per_name: Dict[str, int] = {}
    for nm, sc in scored:
        prev = best_per_name.get(nm)
        if prev is None or sc > prev:
            best_per_name[nm] = sc

    # Sort and take top N
    top = sorted(best_per_name.items(), key=lambda x: x[1], reverse=True)[:limit]
    # Cast to int for stable JSON (no numpy types)
    return [(n, int(s)) for n, s in top]


def perform_opensanctions_check(name, dob, entity_type="Person", parquet_path="data/opensanctions.parquet", requestor: Optional[str]=None):
    import math

    df = load_opensanctions_from_parquet(parquet_path)
    if df.empty:
        # Still provide suggestions from empty => none
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
    # Normalize DOB for strict result logic (unchanged)
    def normalize_dob(d):
        try:
            if not d:
                return None
            return pd.to_datetime(str(d), errors="coerce").strftime("%Y-%m-%d")
        except Exception:
            return None
    norm_dob = normalize_dob(dob)

    # ---- NEW: compute suggestions ignoring DOB (from BOTH sanctions+peps in the filtered schema) ----
    st = df.get("source_type")
    if st is not None:
        st_lower = st.astype(str).str.lower()
        sanc_df = df[st_lower == "sanctions"]
        pep_df  = df[st_lower == "peps"]
        combined_for_suggestions = pd.concat([sanc_df, pep_df], ignore_index=True)
    else:
        sanc_df, pep_df = df, df.iloc[0:0]
        combined_for_suggestions = df

    top_suggestions = _top_name_suggestions(combined_for_suggestions, name, limit=5, threshold=60)

    # ---- Existing strict match logic (unchanged except Top Matches assignment) ----

    def parse_dob(val):
        try:
            s = str(val)
            if not s or s.lower() in ("nan", "none", "nat"):
                return None
            return normalize_dob(s)
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

        # DOB strictness for the ACTUAL result (unchanged)
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

    # Try sanctions first
    s_row, s_score = best_match_from(sanc_df)
    if s_row is not None:
        dataset_label = as_safe_str(s_row.get("dataset")).strip()
        source_label = dataset_label or "OpenSanctions – Sanctions"
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
            # NEW: suggestions shown regardless of strict logic; they do NOT affect the result
            "Top Matches": top_suggestions,
            "Match Found": True,
            "Check Summary": {
                "Status": "Fail Sanction",
                "Source": source_label,
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
        _append_search_to_csv(name, result["Check Summary"])
        return result

    # Otherwise, try PEPs
    p_row, p_score = best_match_from(pep_df)
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
            # NEW: suggestions independent of strict result
            "Top Matches": top_suggestions,
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

# =============================================================================
# Power Automate audit hook
# =============================================================================

def send_audit_to_power_automate(
    payload: Dict[str, Any],
    flow_url: Optional[str] = None,
    timeout: int = 8,
    retries: int = 1,
) -> Tuple[bool, str]:
    """
    POST 'payload' to a Power Automate HTTP trigger.

    Returns: (ok, message)
      ok=True  -> delivered (2xx)
      ok=False -> not delivered; message contains status or exception

    Reads FLOW URL from env POWER_AUTOMATE_FLOW_URL unless 'flow_url' is provided.
    Safe to call inline; it won't raise.
    """
    url = (flow_url or os.getenv("POWER_AUTOMATE_FLOW_URL", "")).strip()
    if not url:
        return (False, "POWER_AUTOMATE_FLOW_URL not set")

    headers = {"Content-Type": "application/json"}

    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if 200 <= resp.status_code < 300:
                return (True, f"Delivered {resp.status_code}")
            transient = (resp.status_code == 429) or (500 <= resp.status_code < 600)
            if attempt < retries and transient:
                time.sleep(1.5 * (attempt + 1))
                continue
            body_snip = (resp.text or "")[:200]
            return (False, f"HTTP {resp.status_code}: {body_snip}")
        except requests.RequestException as e:
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return (False, f"Exception: {e.__class__.__name__}: {e}")

def post_to_power_automate_async(
    payload: Dict[str, Any],
    flow_url: Optional[str] = None,
    timeout: int = 8,
    retries: int = 1,
) -> None:
    """
    Spawn a daemon thread to call send_audit_to_power_automate(payload, ...).
    Never raises; returns immediately.
    """
    def _worker():
        try:
            ok, msg = send_audit_to_power_automate(
                payload=payload,
                flow_url=flow_url,
                timeout=timeout,
                retries=retries,
            )
            print(f"[Audit->PA] ok={ok} msg={msg}")
        except Exception as e:
            # absolutely don't let background failures crash anything
            print(f"[Audit->PA] unexpected error: {e}")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
