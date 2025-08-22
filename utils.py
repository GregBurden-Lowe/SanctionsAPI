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

# =========================
# Configuration
# =========================

DATA_DIR = "data"
OSN_PARQUET = os.path.join(DATA_DIR, "opensanctions.parquet")

# Latest consolidated lists
CONSOLIDATED_SANCTIONS_URL = (
    "https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv"
)
CONSOLIDATED_PEPS_URL = (
    "https://data.opensanctions.org/datasets/latest/peps/targets.simple.csv"
)

# Columns we keep from OpenSanctions (small footprint)
BASE_COLS = [
    "schema",       # Person / Organization / Company / LegalEntity
    "name",         # primary display name
    "aliases",      # alias list (optional)
    "birth_date",   # ISO/partial date
    "program_ids",  # e.g. "EU-UKR;SECO-UKRAINE;UA-SA1644"
    "dataset",      # dataset label (e.g., "EU Financial Sanctions Files (FSF)")
    "sanctions",    # long descriptions; we take a short chunk for UI
]
ALL_COLS = BASE_COLS + ["source_type"]  # source_type = "sanctions" | "peps"

# Limit sanctions to UN / EU / OFAC / UK HMT (OFSI)
SANCTION_SOURCE_KEYWORDS = [
    # United Nations
    "united nations", "un security council", "unsc",
    # European Union
    "european union", "eu council", "eu sanctions", "eu fsf", "financial sanctions files",
    # OFAC / US Treasury
    "ofac", "specially designated nationals", "sdn", "us treasury",
    # UK HMT / OFSI
    "hm treasury", "hmt", "ofsi", "uk sanctions",
]

# =========================
# Mini helpers
# =========================

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

def _contains_any_keyword(text: str, keywords) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in keywords)

def _matches_allowed_sanction_sources(row) -> bool:
    """
    True if row appears to come from UN/EU/OFAC/UK sources, based on dataset/program_ids/sanctions.
    """
    dataset = _safe_str(row.get("dataset"))
    program = _safe_str(row.get("program_ids"))
    sanc    = _safe_str(row.get("sanctions"))
    return (
        _contains_any_keyword(dataset, SANCTION_SOURCE_KEYWORDS) or
        _contains_any_keyword(program, SANCTION_SOURCE_KEYWORDS) or
        _contains_any_keyword(sanc, SANCTION_SOURCE_KEYWORDS)
    )

def _derive_regime_like_row(row) -> Optional[str]:
    """
    Short label for UI:
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

def _empty_no_match_result():
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
            "Source": "OpenSanctions (UN/EU/OFAC/UK + PEPs)",
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
        pass  # best effort

# =========================
# Refresh (stream -> Parquet)
# =========================

def _osn_parquet_schema():
    return pa.schema([(c, pa.string()) for c in ALL_COLS])

def _normalize_chunk_columns(chunk: pd.DataFrame, source_type: str) -> pd.DataFrame:
    for c in BASE_COLS:
        if c not in chunk.columns:
            chunk[c] = ""
    chunk = chunk[[c for c in BASE_COLS]].copy()
    for c in BASE_COLS:
        chunk[c] = chunk[c].astype("string").fillna("")
    chunk["source_type"] = source_type
    return chunk

def _stream_csv_to_parquet(url: str,
                           writer: pq.ParquetWriter,
                           source_type: str,
                           filter_fn=None,
                           chunksize: int = 100_000,
                           usecols: Optional[List[str]] = None):
    if usecols is None:
        usecols = BASE_COLS
    for chunk in pd.read_csv(url, chunksize=chunksize, low_memory=False, usecols=lambda c: c in usecols):
        if filter_fn:
            chunk = filter_fn(chunk)
        if chunk is None or chunk.empty:
            continue
        chunk = _normalize_chunk_columns(chunk, source_type)
        table = pa.Table.from_pandas(chunk, schema=writer.schema, preserve_index=False)
        writer.write_table(table)

def refresh_opensanctions_data(include_peps: bool = True,
                               pep_only_person: bool = True,
                               pep_row_limit: Optional[int] = None):
    """
    Stream consolidated sanctions (filtered to UN/EU/OFAC/UK) + PEPs (persons).
    Writes a single compact parquet at OSN_PARQUET.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    schema = _osn_parquet_schema()
    tmp_path = OSN_PARQUET + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    writer = pq.ParquetWriter(tmp_path, schema)

    # ---- Sanctions: filter to allowed sources
    def sanc_filter(df: pd.DataFrame) -> pd.DataFrame:
        # Keep typical schemas
        if "schema" in df.columns:
            mask_schema = df["schema"].astype(str).str.lower().isin(["person", "organization", "company", "legalentity"])
            df = df[mask_schema]
        # Row-wise filter by allowed sources
        if not df.empty:
            df = df[df.apply(_matches_allowed_sanction_sources, axis=1)]
        keep = [c for c in BASE_COLS]
        existing = [c for c in keep if c in df.columns]
        return df[existing]

    print("[OpenSanctions] Streaming sanctions (UN/EU/OFAC/UK)…")
    _stream_csv_to_parquet(
        url=CONSOLIDATED_SANCTIONS_URL,
        writer=writer,
        source_type="sanctions",
        filter_fn=sanc_filter,
        chunksize=100_000,
        usecols=BASE_COLS
    )

    # ---- PEPs: persons only (optional)
    if include_peps:
        seen = 0
        def pep_filter(df: pd.DataFrame) -> pd.DataFrame:
            nonlocal seen, pep_row_limit
            if "schema" in df.columns and pep_only_person:
                df = df[df["schema"].astype(str).str.lower() == "person"]
            if pep_row_limit is not None and pep_row_limit >= 0:
                remaining = pep_row_limit - seen
                if remaining <= 0:
                    return df.iloc[0:0]
                if len(df) > remaining:
                    df = df.iloc[:remaining]
            keep = [c for c in BASE_COLS]
            existing = [c for c in keep if c in df.columns]
            seen += len(df)
            return df[existing]

        print("[OpenSanctions] Streaming PEPs (persons)…")
        _stream_csv_to_parquet(
            url=CONSOLIDATED_PEPS_URL,
            writer=writer,
            source_type="peps",
            filter_fn=pep_filter,
            chunksize=100_000,
            usecols=BASE_COLS
        )
        print(f"[OpenSanctions] PEP rows written: {seen}")

    writer.close()
    os.replace(tmp_path, OSN_PARQUET)
    print(f"[OpenSanctions] Parquet saved -> {OSN_PARQUET}")
    try:
        clear_osn_cache()
    except Exception:
        pass

# =========================
# Loading & cache
# =========================

@lru_cache(maxsize=1)
def get_opensanctions_df(parquet_path: str = OSN_PARQUET) -> pd.DataFrame:
    """
    Load parquet once, add normalized fields (kept small with Arrow dtypes).
    """
    if not os.path.exists(parquet_path):
        return pd.DataFrame(columns=ALL_COLS + ["name_norm", "birth_norm"])

    table = pq.read_table(parquet_path)
    # Ensure all required columns exist
    have = set(table.column_names)
    arrays, names = [], []
    for c in ALL_COLS:
        if c in have:
            arrays.append(table[c])
        else:
            arrays.append(pa.array([""] * len(table)))
        names.append(c)
    table2 = pa.Table.from_arrays(arrays, names=names)

    df = table2.to_pandas(types_mapper=pd.ArrowDtype)

    # Precompute normalized fields
    df["name_norm"] = df["name"].astype("string[pyarrow]").fillna("").map(str).map(_normalize_text)
    df["birth_norm"] = pd.to_datetime(
        df["birth_date"].astype("string[pyarrow]").fillna(""),
        errors="coerce"
    ).dt.strftime("%Y-%m-%d")

    df["source_type"] = df["source_type"].astype("string[pyarrow]").fillna("")
    return df

def clear_osn_cache():
    get_opensanctions_df.cache_clear()

# =========================
# Matching
# =========================

def _normalize_dob(dob: Optional[str]) -> Optional[str]:
    if not dob:
        return None
    try:
        return pd.to_datetime(str(dob), errors="coerce").strftime("%Y-%m-%d")
    except Exception:
        return None

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

def _source_label_for_row(row) -> str:
    """
    For sanctions: prefer dataset; fallback to 'OpenSanctions – Sanctions'.
    For PEPs: fixed 'Consolidated PEP list'.
    """
    st = _safe_str(row.get("source_type")).lower()
    if st == "peps":
        return "Consolidated PEP list"
    ds = _safe_str(row.get("dataset")).strip()
    return ds or "OpenSanctions – Sanctions"

def perform_opensanctions_check(name, dob, entity_type="Person", parquet_path=OSN_PARQUET):
    """
    Main /opcheck logic (DOB-strict when provided).
    """
    import math

    df = get_opensanctions_df(parquet_path)
    if df.empty:
        return {"error": "No data available."}

    # Filter by schema/entity_type
    et = (entity_type or "Person").lower()
    schemas = df["schema"].astype(str).str.lower() if "schema" in df.columns else pd.Series([], dtype="string")
    if et == "organization":
        df = df[schemas.isin(["organization", "legalentity", "company"])]
    else:
        df = df[schemas == "person"]

    if df.empty:
        result = _empty_no_match_result()
        _append_search_to_csv(name, result["Check Summary"])
        return result

    norm_name = _normalize_text(name)
    norm_dob = _normalize_dob(dob)

    def parse_dob(val):
        try:
            s = str(val)
            if not s or s.lower() in ("nan", "none", "nat"):
                return None
            return _normalize_dob(s)
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
            dob_ok = []
            for _, score, idx in matches:
                r = df_subset.iloc[idx]
                cand_dob = parse_dob(r.get("birth_date"))
                if cand_dob and cand_dob == norm_dob:
                    dob_ok.append((_, score, idx))
            if not dob_ok:
                return None, None, []
            matches = dob_ok

        matches_sorted = sorted(matches, key=lambda x: x[1], reverse=True)
        _, best_score, best_idx = matches_sorted[0]
        best_row = df_subset.iloc[best_idx]

        top_list = []
        for cleaned_name, score, idx in matches_sorted[:10]:
            display_name = as_safe_str(df_subset.iloc[idx].get("name") or cleaned_name)
            top_list.append((display_name, round(float(score), 1)))

        return best_row, float(best_score), top_list

    # Split by source_type
    st_lower = df["source_type"].astype(str).str.lower() if "source_type" in df.columns else pd.Series([], dtype="string")
    sanc_df = df[st_lower == "sanctions"]
    pep_df  = df[st_lower == "peps"]

    # Try sanctions first
    s_row, s_score, s_top = best_match_from(sanc_df)
    if s_row is not None:
        result = {
            "Sanctions Name": s_row.get("name"),
            "Birth Date": parse_dob(s_row.get("birth_date")),
            "Regime": _derive_regime_like_row(s_row),
            "Position": None,
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
                "Source": _source_label_for_row(s_row),
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        }
        _append_search_to_csv(name, result["Check Summary"])
        return result

    # Otherwise, try PEPs
    p_row, p_score, p_top = best_match_from(pep_df)
    if p_row is not None:
        result = {
            "Sanctions Name": p_row.get("name"),
            "Birth Date": parse_dob(p_row.get("birth_date")),
            "Regime": _derive_regime_like_row(p_row),
            "Position": None,
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
        _append_search_to_csv(name, result["Check Summary"])
        return result

    # Nothing matched (or DOB filter eliminated candidates)
    result = _empty_no_match_result()
    _append_search_to_csv(name, result["Check Summary"])
    return result

# =========================
# Legacy OFSI + Wikidata (optional; for /check)
# =========================

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
    Legacy /check endpoint: OFSI fuzzy + Wikidata hint (small memory).
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
