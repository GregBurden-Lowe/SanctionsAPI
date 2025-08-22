# utils.py
from __future__ import annotations

import os
import csv
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from rapidfuzz import fuzz
import pyarrow as pa
import pyarrow.parquet as pq


# -----------------------------------------------------------------------------
# General helpers
# -----------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^\w\s]", "", text)  # drop punctuation
    return re.sub(r"\s+", " ", text).lower().strip()


def normalize_dob(dob: Optional[str]) -> Optional[str]:
    if not dob:
        return None
    try:
        # accept many formats and normalize to YYYY-MM-DD
        return pd.to_datetime(dob, errors="coerce").strftime("%Y-%m-%d")
    except Exception:
        return None


def _safe_str(value: Any) -> str:
    """
    Return a safe string, treating pandas.NA/NaN/None as empty string.
    """
    if value is None:
        return ""
    try:
        # pandas.NA throws on bool(), but str() is fine
        s = str(value)
        if s.lower() in ("nan", "nat"):
            return ""
        return s
    except Exception:
        return ""


# -----------------------------------------------------------------------------
# OpenSanctions data refresh/load
# -----------------------------------------------------------------------------

def refresh_opensanctions_data() -> None:
    """
    Download latest OpenSanctions consolidated sanctions list and PEP list,
    tag rows with source_type, then write a single parquet for fast search.
    """
    urls = {
        "sanctions": "https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv",
        "peps":      "https://data.opensanctions.org/datasets/latest/peps/targets.simple.csv",
    }
    os.makedirs("data", exist_ok=True)

    combined_df: Optional[pd.DataFrame] = None

    for label, url in urls.items():
        try:
            r = requests.get(url, timeout=120)
            if r.status_code == 200:
                csv_path = f"data/opensanctions_{label}_latest.csv"
                with open(csv_path, "wb") as f:
                    f.write(r.content)
                df = pd.read_csv(csv_path, low_memory=False)
                df["source_type"] = label
                combined_df = pd.concat([combined_df, df], ignore_index=True) if combined_df is not None else df
                print(f"[OpenSanctions] Downloaded {label}: {len(df):,} rows")
            else:
                print(f"[OpenSanctions] Failed {label} download: HTTP {r.status_code}")
        except Exception as e:
            print(f"[OpenSanctions] Error downloading {label}: {e}")

    if combined_df is None or combined_df.empty:
        print("[OpenSanctions] No data written.")
        return

    try:
        table = pa.Table.from_pandas(combined_df)
        pq.write_table(table, "data/opensanctions.parquet")
        print("[OpenSanctions] Parquet saved -> data/opensanctions.parquet")
    except Exception as e:
        print(f"[OpenSanctions] Failed to write parquet: {e}")


def load_opensanctions_from_parquet(parquet_path: str = "data/opensanctions.parquet") -> pd.DataFrame:
    if not os.path.exists(parquet_path):
        return pd.DataFrame()
    try:
        return pd.read_parquet(parquet_path)
    except Exception as e:
        print(f"[OpenSanctions] Failed to load parquet: {e}")
        return pd.DataFrame()


# -----------------------------------------------------------------------------
# Matching helpers (OpenSanctions)
# -----------------------------------------------------------------------------

def match_name(query: str, primary: str, aliases: Any) -> int:
    """
    Score a candidate by fuzzy-name similarity between query and (name + aliases).
    Returns an integer 0..100.
    """
    q = normalize_text(query)
    if not q:
        return 0

    best = fuzz.token_set_ratio(q, normalize_text(_safe_str(primary)))

    # aliases may be a pipe/semicolon/comma separated string
    alias_str = _safe_str(aliases)
    if alias_str:
        # split on common separators, keep unique tokens
        for alias in re.split(r"[|;,]\s*", alias_str):
            alias = normalize_text(alias)
            if not alias:
                continue
            best = max(best, fuzz.token_set_ratio(q, alias))

    return int(best)


def _derive_regime_like(row: pd.Series) -> Optional[str]:
    """
    Create a short, useful label for UI from consolidated data.
    Priority:
      1) program_ids (e.g., 'EU-UKR;SECO-UKRAINE;UA-SA1644') -> first token
      2) sanctions (long text; take first semicolon/newline chunk)
      3) dataset (e.g., 'EU Council Official Journal Sanctioned Entities')
    Safe against pandas.NA.
    """
    prog = _safe_str(row.get("program_ids")).strip()
    if prog:
        return prog.split(";")[0].strip()

    sanc = _safe_str(row.get("sanctions")).strip()
    if sanc:
        # take the first sub-chunk
        part = sanc.split(";")[0].strip() or sanc.splitlines()[0].strip()
        if part:
            return part

    ds = _safe_str(row.get("dataset")).strip()
    return ds or None


def _find_candidates(df: pd.DataFrame, name: str, entity_type: str = "Person") -> pd.DataFrame:
    """
    Prefilter dataset by entity_type and non-empty names to speed matching.
    """
    if df.empty:
        return df

    # schema filtering
    et = (entity_type or "Person").strip().lower()
    schema = df.get("schema")
    if schema is not None:
        s = schema.astype(str).str.lower()
        if et == "organization":
            mask = s.isin(["organization", "company", "legalentity"])
        else:
            # default: person
            mask = s.isin(["person"])
        df = df[mask]

    # require name present
    df = df[df["name"].astype(str).str.strip().ne("")]
    return df


def _append_search_to_csv(name: str, summary: Dict[str, Any], path: str = "data/search_log.csv") -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    file_exists = os.path.isfile(path)
    with open(path, mode="a", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["Date", "Name Searched", "Status", "Source"])
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "Date": summary.get("Date"),
            "Name Searched": name,
            "Status": summary.get("Status"),
            "Source": summary.get("Source"),
        })


# -----------------------------------------------------------------------------
# Main: OpenSanctions check (with DOB-first logic and source display)
# -----------------------------------------------------------------------------

def perform_opensanctions_check(
    name: str,
    dob: Optional[str] = None,
    entity_type: str = "Person",
    parquet_path: str = "data/opensanctions.parquet",
) -> Dict[str, Any]:
    """
    Screening against the combined OpenSanctions parquet.
    - Name fuzzy match (including aliases).
    - If DOB provided: we require an *exact DOB match* to produce Fail/Review.
      Otherwise return Cleared (but include Top Matches for operator review).
    """
    df = load_opensanctions_from_parquet(parquet_path)
    if df.empty:
        return {"error": "OpenSanctions dataset not loaded"}

    df = _find_candidates(df, name, entity_type)
    if df.empty:
        result = _empty_no_match_result()
        _append_search_to_csv(name, result["Check Summary"])
        return result

    # Compute scores and DOB flags
    norm_dob = normalize_dob(dob)
    scored: List[Tuple[pd.Series, int, bool]] = []

    for _, row in df.iterrows():
        score = match_name(name, _safe_str(row.get("name")), row.get("aliases"))
        # DOB exact match (string equivalence after normalization)
        cand_dob = normalize_dob(_safe_str(row.get("birth_date")))
        dob_match = bool(norm_dob and cand_dob and cand_dob == norm_dob)

        # If user supplied DOB and it doesn't match, penalize heavily
        if norm_dob and not dob_match:
            score = max(0, score - 40)

        if score > 0:
            scored.append((row, score, dob_match))

    if not scored:
        result = _empty_no_match_result()
        _append_search_to_csv(name, result["Check Summary"])
        return result

    # Sort by score
    scored.sort(key=lambda t: t[1], reverse=True)

    # If DOB supplied: only candidates with exact DOB count for Fail/Review
    if norm_dob:
        dob_hits = [t for t in scored if t[2] is True]

        if not dob_hits:
            # No exact DOB match: Cleared (but show info)
            top = scored[0][0]  # best name-only match for display context
            display_source = _display_source_for_row(top)
            check_summary = {
                "Status": "Cleared",
                "Source": display_source,
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            result = {
                "Check Summary": check_summary,
                "Risk Level": "Low",
                "Confidence": "No exact DoB match",
                "Is Sanctioned": False,
                "Is PEP": False,
                "Sanctions Name": None,
                "Birth Date": None,
                "Regime": None,
                "Position": None,
                "Score": 0,
                "Top Matches": [
                    ( _safe_str(r.get("name")), int(s) ) for r, s, _ in scored[:10]
                ],
            }
            _append_search_to_csv(name, check_summary)
            return result

        # Take best exact-DOB match
        row, top_score, _ = dob_hits[0]
        source_type = _safe_str(row.get("source_type")).lower()
        is_sanction = (source_type == "sanctions")
        is_pep = (source_type == "peps")

        # Thresholds when DOB matches
        if top_score >= 90:
            status, risk, confidence = ("Fail", "High Risk" if is_sanction else "Medium Risk", f"Strong match ({top_score}%)")
        elif top_score >= 75:
            status, risk, confidence = ("Review", "Medium", f"Possible match ({top_score}%)")
        else:
            status, risk, confidence = ("Cleared", "Low", f"Weak match ({top_score}%)")

        display_source = _display_source_for_row(row)
        check_summary = {
            "Status": status,
            "Source": display_source,
            "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        result = {
            "Check Summary": check_summary,
            "Risk Level": risk,
            "Confidence": confidence,
            "Is Sanctioned": is_sanction,
            "Is PEP": is_pep,
            "Sanctions Name": _safe_str(row.get("name")),
            "Birth Date": _safe_str(row.get("birth_date")),
            "Regime": _derive_regime_like(row),
            "Position": _safe_str(row.get("position")),  # may be empty
            "Score": int(top_score),
            "Top Matches": [
                (_safe_str(r.get("name")), int(s)) for r, s, _ in scored[:10]
            ],
        }
        _append_search_to_csv(name, check_summary)
        return result

    # No DOB supplied: standard thresholds on best overall match
    row, top_score, _ = scored[0]
    source_type = _safe_str(row.get("source_type")).lower()
    is_sanction = (source_type == "sanctions")
    is_pep = (source_type == "peps")

    if top_score >= 90:
        status, risk, confidence = ("Fail", "High Risk" if is_sanction else "Medium Risk", f"Strong match ({top_score}%)")
    elif top_score >= 75:
        status, risk, confidence = ("Review", "Medium", f"Possible match ({top_score}%)")
    else:
        status, risk, confidence = ("Cleared", "Low", f"Weak match ({top_score}%)")

    display_source = _display_source_for_row(row)
    check_summary = {
        "Status": status,
        "Source": display_source,
        "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    result = {
        "Check Summary": check_summary,
        "Risk Level": risk,
        "Confidence": confidence,
        "Is Sanctioned": is_sanction,
        "Is PEP": is_pep,
        "Sanctions Name": _safe_str(row.get("name")),
        "Birth Date": _safe_str(row.get("birth_date")),
        "Regime": _derive_regime_like(row),
        "Position": _safe_str(row.get("position")),
        "Score": int(top_score),
        "Top Matches": [
            (_safe_str(r.get("name")), int(s)) for r, s, _ in scored[:10]
        ],
    }
    _append_search_to_csv(name, check_summary)
    return result


def _display_source_for_row(row: pd.Series) -> str:
    source_type = _safe_str(row.get("source_type")).lower()
    if source_type == "sanctions":
        # Prefer dataset name if present, else fallback
        return _safe_str(row.get("dataset")) or "Open Sanctions"
    if source_type == "peps":
        return "Consolidated PEP list"
    return "Open Sanctions"


def _empty_no_match_result() -> Dict[str, Any]:
    return {
        "Check Summary": {
            "Status": "Cleared",
            "Source": "Open Sanctions",
            "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "Risk Level": "Low",
        "Confidence": "No match",
        "Is Sanctioned": False,
        "Is PEP": False,
        "Sanctions Name": None,
        "Birth Date": None,
        "Regime": None,
        "Position": None,
        "Score": 0,
        "Top Matches": [],
    }


# -----------------------------------------------------------------------------
# Optional: OFSI + very-light PEP (used by /check endpoint)
# -----------------------------------------------------------------------------

def get_latest_ofsi_csv_url() -> str:
    return "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.csv"


def download_and_save_ofsi_file(force_refresh: bool = False) -> Optional[str]:
    path = "ofsi_latest.csv"
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
        else:
            print(f"[OFSI] download failed: {r.status_code}")
    except Exception as e:
        print(f"[OFSI] error: {e}")
    return None


def load_ofsi_data(file_path: str) -> pd.DataFrame:
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
    # simple first+last normalization
    def _simplify(n: str) -> str:
        parts = n.split()
        return " ".join(parts[:1] + parts[-1:]) if parts else n
    df["Name"] = df["Raw Name"].apply(_simplify).apply(normalize_text)
    return df[["Name", "Raw Name", "Date of Birth", "Group Type", "Regime Name", "UK Statement of Reasons"]]


def get_ofsi_data(force_refresh: bool = False) -> Optional[pd.DataFrame]:
    path = download_and_save_ofsi_file(force_refresh)
    return load_ofsi_data(path) if path else None


def classify_risk(score: int) -> str:
    return "High" if score >= 90 else "Medium" if score >= 85 else "Cleared"


def match_against_ofsi(customer_name: str, dob: Optional[str], ofsi_df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    normalized_input = normalize_text(customer_name)
    candidates = ofsi_df["Name"].fillna("").tolist()

    # quick fuzzy shortlist
    results: List[Tuple[str, int, int]] = []
    for idx, cand in enumerate(candidates):
        score = fuzz.token_set_ratio(normalized_input, cand)
        if score >= 80:
            results.append((cand, score, idx))

    if not results:
        return None

    results.sort(key=lambda x: x[1], reverse=True)
    _, best_score, idx = results[0]
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
            "Score": int(best_score),
            "Risk Level": "Cleared",
            "Top Matches": results[:5],
        }

    confidence = "High" if best_score > 90 or dob_match else "Medium"
    return {
        "Sanctions Name": suggestion,
        "Regime": best_match["Regime Name"],
        "Reason": best_match["UK Statement of Reasons"],
        "Confidence": confidence,
        "Score": int(best_score),
        "Risk Level": classify_risk(int(best_score)),
        "Suggested Name": suggestion if best_score < 100 else None,
        "Top Matches": results[:5],
    }


def query_wikidata(name: str) -> Optional[Dict[str, Any]]:
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
    except Exception as e:
        print(f"[Wikidata] error: {e}")
    return None


def perform_sanctions_check(name: str, dob: Optional[str], force_refresh: bool = False) -> Dict[str, Any]:
    """
    Legacy /check endpoint logic (OFSI + a hint from Wikidata PEP).
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
