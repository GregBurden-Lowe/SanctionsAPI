# utils.py — lean, low‑RAM OpenSanctions utils (no PDF)

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

# Latest consolidated sanctions (targets.simple.csv)
CONSOLIDATED_SANCTIONS_URL = (
    "https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv"
)

# Optional: Consolidated PEPs (targets.simple.csv). Enable if you want PEPs included.
CONSOLIDATED_PEPS_URL = (
    "https://data.opensanctions.org/datasets/latest/peps/targets.simple.csv"
)

# Only keep columns we actually use to keep memory down:
OSN_COLS = [
    "schema",       # Person / Organization / Company / LegalEntity
    "name",         # primary display name
    "aliases",      # pipe/semicolon separated aliases (optional)
    "birth_date",   # ISO or partial
    "program_ids",  # e.g. "EU-UKR;SECO-UKRAINE;UA-SA1644"
    "dataset",      # dataset label (e.g. "EU Council Official Journal…")
    "sanctions",    # long text; we only take a short first chunk
    # internal flag we add when saving parquet:
    "source_type",  # "sanctions" or "peps"
]

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

def _top_matches_list(df: pd.DataFrame, limit: int = 10) -> List[Tuple[str, int]]:
    out = []
    if df.empty:
        return out
    for _, row in df.head(limit).iterrows():
        nm = _safe_str(row.get("name"))
        sc = int(row.get("score") or 0)
        out.append((nm, sc))
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

def _append_search_to_csv(name, summary, path=os.path.join(DATA_DIR, "search_log.csv")):
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
        # best-effort logging only
        pass

# ---------------------------
# Data refresh (download -> parquet)
# ---------------------------

def _download_csv(url: str, dest_path: str, timeout: int = 120):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(r.content)

def refresh_opensanctions_data(include_peps: bool = False):
    """
    Download latest consolidated sanctions (and optionally PEPs), keep only columns we need,
    add a 'source_type' column, and write a single compact parquet for fast loading.
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    parts = []

    # Sanctions
    try:
        sanc_csv = os.path.join(DATA_DIR, "os_sanctions_latest.csv")
        _download_csv(CONSOLIDATED_SANCTIONS_URL, sanc_csv, timeout=240)
        df_s = pd.read_csv(sanc_csv, low_memory=False)
        # Keep only needed cols; fill missing
        for c in OSN_COLS:
            if c not in df_s.columns and c != "source_type":
                df_s[c] = pd.Series(dtype="string")
        df_s = df_s[[c for c in OSN_COLS if c != "source_type"]].copy()
        df_s["source_type"] = "sanctions"
        parts.append(df_s)
        print(f"[OpenSanctions] Downloaded sanctions ({len(df_s):,} rows).")
    except Exception as e:
        print(f"[OpenSanctions] Sanctions download/parsing failed: {e}")

    # PEPs (optional)
    if include_peps:
        try:
            peps_csv = os.path.join(DATA_DIR, "os_peps_latest.csv")
            _download_csv(CONSOLIDATED_PEPS_URL, peps_csv, timeout=240)
            df_p = pd.read_csv(peps_csv, low_memory=False)
            for c in OSN_COLS:
                if c not in df_p.columns and c != "source_type":
                    df_p[c] = pd.Series(dtype="string")
            df_p = df_p[[c for c in OSN_COLS if c != "source_type"]].copy()
            df_p["source_type"] = "peps"
            parts.append(df_p)
            print(f"[OpenSanctions] Downloaded PEPs ({len(df_p):,} rows).")
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
        return pd.DataFrame(columns=OSN_COLS + ["name_norm", "birth_norm"])

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

    # Convert to pandas with ArrowDtype to reduce memory
    df = table2.to_pandas(types_mapper=pd.ArrowDtype)

    # Precompute normalized fields
    df["name_norm"] = df["name"].astype("string[pyarrow]").fillna("").map(str).map(_normalize_text)
    df["birth_norm"] = pd.to_datetime(
        df["birth_date"].astype("string[pyarrow]").fillna(""),
        errors="coerce"
    ).dt.strftime("%Y-%m-%d")

    # Ensure flags exist (source_type is in the parquet)
    df["source_type"] = df["source_type"].astype("string[pyarrow]").fillna("")
    return df

def clear_osn_cache():
    get_opensanctions_df.cache_clear()

# ---------------------------
# Matching
# ---------------------------

def perform_opensanctions_check(name, dob, entity_type="Person", parquet_path="data/opensanctions.parquet"):
    import math

    df = load_opensanctions_from_parquet(parquet_path)
    if df.empty:
        return {"error": "No data available."}

    # Filter by entity type/schema
    et = (entity_type or "Person").lower()
    schema_col = df.get("schema")
    if schema_col is not None:
        schemas = schema_col.astype(str).str.lower()
        if et == "organization":
            df = df[schemas.isin(["organization", "legalentity", "company"])]
        else:
            df = df[schemas == "person"]

    if df.empty:
        result = _empty_no_match_result()
        _append_search_to_csv(name, result["Check Summary"])
        return result

    norm_name = normalize_text(name)
    norm_dob = normalize_dob(dob)

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
        """Return (row, score, top_matches_list) or (None, None, []) respecting DOB strictness."""
        if df_subset is None or df_subset.empty:
            return None, None, []

        candidates = df_subset["name"].fillna("").tolist()
        matches = get_best_name_matches(norm_name, candidates, limit=top_limit, threshold=threshold)
        if not matches:
            return None, None, []

        # If DOB provided, require exact DOB match among the matched indices.
        if norm_dob:
            dob_ok_matches = []
            for _, score, idx in matches:
                r = df_subset.iloc[idx]
                cand_dob = parse_dob(r.get("birth_date"))
                if cand_dob and cand_dob == norm_dob:
                    dob_ok_matches.append((_, score, idx))
            if not dob_ok_matches:
                # No exact DOB matches -> treat as no match for this dataset
                return None, None, []
            matches = dob_ok_matches

        # Pick the highest score after the DOB filter (if applied)
        matches_sorted = sorted(matches, key=lambda x: x[1], reverse=True)
        _, best_score, best_idx = matches_sorted[0]
        best_row = df_subset.iloc[best_idx]

        # Build a compact Top Matches list for UI
        top_list = []
        for i, (cleaned_name, score, idx) in enumerate(matches_sorted[:10]):
            display_name = as_safe_str(df_subset.iloc[idx].get("name") or cleaned_name)
            top_list.append((display_name, round(float(score), 1)))

        return best_row, float(best_score), top_list

    # Split by source_type
    st = df.get("source_type")
    if st is not None:
        st_lower = st.astype(str).str.lower()
        sanc_df = df[st_lower == "sanctions"]
        pep_df  = df[st_lower == "peps"]
    else:
        # Fallback: if not labeled, treat everything as sanctions (shouldn’t happen with your refresh)
        sanc_df, pep_df = df, df.iloc[0:0]

    # Try sanctions first
    s_row, s_score, s_top = best_match_from(sanc_df)
    if s_row is not None:
        # Derive fields
        is_sanctioned = True
        is_pep = False

        # Prefer dataset label for Source; fall back to consolidated label
        dataset_label = as_safe_str(s_row.get("dataset")).strip()
        source_label = dataset_label or "OpenSanctions – Sanctions"

        result = {
            "Sanctions Name": s_row.get("name"),
            "Birth Date": parse_dob(s_row.get("birth_date")),
            "Regime": _derive_regime_like(s_row),
            "Position": s_row.get("positions"),
            "Topics": [],
            "Is PEP": is_pep,
            "Is Sanctioned": is_sanctioned,
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
        _append_search_to_csv(name, result["Check Summary"])
        return result

    # Otherwise, try PEPs
    p_row, p_score, p_top = best_match_from(pep_df)
    if p_row is not None:
        is_sanctioned = False
        is_pep = True

        result = {
            "Sanctions Name": p_row.get("name"),
            "Birth Date": parse_dob(p_row.get("birth_date")),
            "Regime": _derive_regime_like(p_row),
            "Position": p_row.get("positions"),
            "Topics": [],
            "Is PEP": is_pep,
            "Is Sanctioned": is_sanctioned,
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
        _append_search_to_csv(name, result["Check Summary"])
        return result

    # Nothing matched (or DOB filter eliminated candidates)
    result = _empty_no_match_result()
    _append_search_to_csv(name, result["Check Summary"])
    return result

def load_opensanctions_from_parquet(parquet_path="data/opensanctions.parquet") -> pd.DataFrame:
    """Load the consolidated OpenSanctions parquet written during refresh."""
    if not os.path.exists(parquet_path):
        print(f"[OpenSanctions] parquet not found at {parquet_path}")
        return pd.DataFrame()
    try:
        return pd.read_parquet(parquet_path)
    except Exception as e:
        print(f"[OpenSanctions] Failed to load parquet: {e}")
        return pd.DataFrame()

def _source_label_for_row(row) -> str:
    """
    For sanctions: prefer dataset value; fallback to 'OpenSanctions'.
    For PEPs: fixed 'Consolidated PEP list'.
    """
    st = _safe_str(row.get("source_type")).lower()
    if st == "peps":
        return "Consolidated PEP list"
    ds = _safe_str(row.get("dataset")).strip()
    return ds or "OpenSanctions"

# ---------------------------
# Optional: OFSI + Wikidata (for /check endpoint compatibility)
# ---------------------------

def get_latest_ofsi_csv_url():
    return "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.csv"

def download_and_save_ofsi_file(force_refresh=False):
    path = os.path.join(DATA_DIR, "ofsi_latest.csv")
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(path) and not force_refresh:
        modified_time = datetime.fromtimestamp(os.path.getmtime(path))
        age_minutes = (datetime.now() - modified_time).total_seconds() / 60
        if age_minutes < 24 * 60:
            return path
    try:
        r = requests.get(get_latest_ofsi_csv_url(), timeout=120)
        if r.status_code == 200:
            with open(path, "wb") as f:
                f.write(r.content)
            return path
    except Exception:
        pass
    return None

def load_ofsi_data(file_path):
    df = pd.read_csv(file_path, header=1, low_memory=False)
    df = df.rename(columns={
        "DOB": "Date of Birth",
        "Regime": "Regime Name",
        "UK Sanctions List Date Designated": "UK Statement of Reasons",
    })
    name_cols = [f"Name {i}" for i in range(1, 7)]
    df["Raw Name"] = df[name_cols].astype(str).apply(
        lambda row: " ".join([x for x in row if x and x.strip().lower() != "nan"]),
        axis=1,
    )
    def _display_name(n: str) -> str:
        parts = n.split()
        return " ".join(parts[:1] + parts[-1:]) if parts else ""
    df["Name"] = df["Raw Name"].apply(_display_name).map(_normalize_text)
    return df[["Name", "Raw Name", "Date of Birth", "Group Type", "Regime Name", "UK Statement of Reasons"]]

def get_ofsi_data(force_refresh=False):
    path = download_and_save_ofsi_file(force_refresh)
    return load_ofsi_data(path) if path else None

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

def match_against_ofsi(customer_name, dob, ofsi_df):
    normalized_input = _normalize_text(customer_name)
    candidates = ofsi_df["Name"].fillna("").tolist()
    matches = get_best_name_matches(normalized_input, candidates)
    if not matches:
        return None

    _, best_score, idx = matches[0]
    best_match = ofsi_df.iloc[idx]
    suggestion = best_match["Raw Name"]
    dob_match = bool(
        dob and pd.notna(best_match["Date of Birth"]) and str(dob)[:4] in str(best_match["Date of Birth"])
    )
    if best_score < 80:
        return {
            "Sanctions Name": None,
            "Suggested Name": suggestion,
            "Regime": None,
            "Reason": None,
            "Confidence": "Low",
            "Score": best_score,
            "Risk Level": "Cleared",
            "Top Matches": matches[:5],
        }

    confidence = "High" if best_score > 90 or dob_match else "Medium"
    return {
        "Sanctions Name": suggestion,
        "Regime": best_match["Regime Name"],
        "Reason": best_match["UK Statement of Reasons"],
        "Confidence": confidence,
        "Score": best_score,
        "Risk Level": "High" if best_score >= 90 else "Medium" if best_score >= 85 else "Cleared",
        "Suggested Name": suggestion if best_score < 100 else None,
        "Top Matches": matches[:5],
    }

def query_wikidata(name):
    # Minimal, best-effort PEP hint; safe to keep or remove
    q = f'''
    SELECT ?personLabel ?dob ?partyLabel ?positionLabel WHERE {{
      ?person rdfs:label "{name}"@en.
      OPTIONAL {{ ?person wdt:P569 ?dob. }}
      OPTIONAL {{ ?person wdt:P102 ?party. }}
      OPTIONAL {{
        ?person p:P39 ?posStmt.
        ?posStmt ps:P39 ?position.
        ?posStmt pq:P580 ?startDate.
      }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}
    ORDER BY DESC(?startDate)
    LIMIT 1
    '''
    try:
        r = requests.get(
            "https://query.wikidata.org/sparql",
            params={"query": q},
            headers={"Accept": "application/sparql-results+json"},
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json().get("results", {}).get("bindings", [])
            if data:
                row = data[0]
                return {
                    "PEP Name": row.get("personLabel", {}).get("value"),
                    "DoB": row.get("dob", {}).get("value"),
                    "Party": row.get("partyLabel", {}).get("value"),
                    "Position": row.get("positionLabel", {}).get("value"),
                }
    except Exception:
        pass
    return None

def perform_sanctions_check(name, dob, force_refresh=False):
    """
    Legacy /check endpoint: OFSI fuzzy + Wikidata hint.
    Keeps memory impact small.
    """
    ofsi_df = get_ofsi_data(force_refresh=force_refresh)
    if ofsi_df is None:
        return {
            "Sanctions Result": None,
            "PEP Result": None,
            "Top Matches": [],
            "Risk Level": "Cleared",
        }
    sanctions_result = match_against_ofsi(name, dob, ofsi_df)
    pep_result = query_wikidata(name)
    return {
        "Sanctions Result": sanctions_result,
        "PEP Result": pep_result,
        "Suggestion": sanctions_result.get("Suggested Name") if sanctions_result else None,
        "Top Matches": sanctions_result.get("Top Matches") if sanctions_result else [],
        "Risk Level": sanctions_result.get("Risk Level") if sanctions_result else "Cleared",
    }
