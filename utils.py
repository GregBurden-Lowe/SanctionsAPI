# utils.py — OpenSanctions + PEPs (consolidated) with DOB-first logic
# -------------------------------------------------------------------
import os
import csv
import re
import unicodedata
from datetime import datetime
from typing import Optional, List, Tuple

import pandas as pd
import requests
from rapidfuzz import fuzz

import pyarrow as pa
import pyarrow.parquet as pq


# =========================
# Basic helpers
# =========================
def normalize_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).lower().strip()

def normalize_dob(dob):
    if not dob:
        return None
    try:
        return pd.to_datetime(dob).strftime("%Y-%m-%d")
    except Exception:
        return None

def confidence_no_match(score):
    if score < 30:
        return "Very High"
    elif score < 45:
        return "High"
    elif score < 55:
        return "Medium"
    else:
        return "Low"


# =========================
# NA-safe row getters
# =========================
def _is_na(v) -> bool:
    try:
        return v is None or pd.isna(v)
    except Exception:
        return v is None

def sget_raw(row, key, default=None):
    try:
        v = row.get(key)
    except Exception:
        try:
            v = getattr(row, key)
        except Exception:
            v = default
    return default if _is_na(v) else v

def sget_str(row, key, default: str = "") -> str:
    v = sget_raw(row, key, default)
    return default if _is_na(v) else str(v)

def sget_list(row, key, default: Optional[List[str]] = None) -> List[str]:
    if default is None:
        default = []
    v = sget_raw(row, key, None)
    if _is_na(v):
        return list(default)
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        parts: List[str] = []
        for chunk in v.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "," in chunk and len(chunk) > 40:  # heuristic split CSV-like long chunks
                for c2 in chunk.split(","):
                    c2 = c2.strip()
                    if c2:
                        parts.append(c2)
            else:
                parts.append(chunk)
        return parts
    return list(default)


# =========================
# Name matching
# =========================
def get_best_name_matches(search_name: str, candidates: List[str], limit=50, threshold=80) -> List[Tuple[str, int, int]]:
    """Robust fuzzy match with token heuristics."""
    def preprocess(name):
        name = normalize_text(name)
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

        # allow exact short names
        if len(search_tokens) <= 2 and search_cleaned == candidate_cleaned:
            results.append((candidate_cleaned, int(score), idx))
            continue

        if overlap < 2:
            continue
        if jaccard < 0.4:
            continue

        # length penalty for very different token counts
        if abs(len(search_tokens) - len(candidate_tokens)) > 2:
            score -= 15
        if len(candidate_tokens) <= 2 and len(search_tokens) > 3:
            score -= 20

        if score >= threshold:
            results.append((candidate_cleaned, int(score), idx))

    return sorted(results, key=lambda x: x[1], reverse=True)[:limit]


# =========================
# OpenSanctions data
# =========================
OS_URLS = {
    "sanctions": "https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv",
    "peps":      "https://data.opensanctions.org/datasets/latest/peps/targets.simple.csv",
}

def refresh_opensanctions_data():
    """
    Download latest OpenSanctions consolidated sanctions + PEPs and write to Parquet.
    Adds 'source_type' column: 'sanctions' or 'peps'.
    """
    os.makedirs("data", exist_ok=True)
    combined_df = None

    for label, url in OS_URLS.items():
        try:
            r = requests.get(url, timeout=180)
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
        print("[OpenSanctions] No data written (empty).")
        return

    try:
        table = pa.Table.from_pandas(combined_df)
        pq.write_table(table, "data/opensanctions.parquet")
        print("[OpenSanctions] Parquet refreshed at data/opensanctions.parquet")
    except Exception as e:
        print(f"[OpenSanctions] Failed to write parquet: {e}")

def load_opensanctions_from_parquet(parquet_path="data/opensanctions.parquet") -> pd.DataFrame:
    if not os.path.exists(parquet_path):
        return pd.DataFrame()
    try:
        return pd.read_parquet(parquet_path)
    except Exception as e:
        print(f"[OpenSanctions] Failed to load parquet: {e}")
        return pd.DataFrame()


def _append_search_to_csv(name, summary, path="data/search_log.csv"):
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


def _derive_regime_like(row) -> Optional[str]:
    """
    Short, useful label for UI from consolidated data.
    Priority:
      1) program_ids -> first token
      2) sanctions   -> first ';' or first line
      3) dataset
    """
    prog = sget_str(row, "program_ids", "")
    if prog:
        return prog.split(";")[0].strip()

    sanc = sget_str(row, "sanctions", "")
    if sanc:
        part = sanc.split(";")[0].strip()
        if not part and "\n" in sanc:
            part = sanc.splitlines()[0].strip()
        if part:
            return part

    ds = sget_str(row, "dataset", "")
    return ds or None


def _empty_no_match_result():
    return {
        "Sanctions Name": None,
        "Birth Date": None,
        "Regime": None,
        "Position": None,
        "Topics": [],
        "Is PEP": False,
        "Is Sanctioned": False,
        "Confidence": confidence_no_match(0),
        "Score": 0,
        "Risk Level": "Cleared",
        "Top Matches": [],
        "Match Found": False,
        "Check Summary": {
            "Status": "Cleared",
            "Source": "Open Sanctions",
            "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    }


# =========================
# Core search with DOB-first logic
# =========================
def perform_opensanctions_check(name, dob, entity_type="Person", parquet_path="data/opensanctions.parquet"):
    df = load_opensanctions_from_parquet(parquet_path)
    if df.empty:
        return {"error": "No data available."}

    # Filter by schema/entity type
    et = (entity_type or "Person").lower()
    schema_series = df["schema"].astype(str).str.lower()
    if et == "organization":
        df = df[schema_series.isin(["organization", "legalentity", "company"])]
    else:
        df = df[schema_series == et]

    if df.empty:
        result = _empty_no_match_result()
        _append_search_to_csv(name, result["Check Summary"])
        return result

    # Fuzzy search on name
    candidates = df["name"].fillna("").tolist()
    matches = get_best_name_matches(name, candidates)
    if not matches:
        result = _empty_no_match_result()
        _append_search_to_csv(name, result["Check Summary"])
        return result

    norm_name = normalize_text(name)
    norm_dob  = normalize_dob(dob)

    # Prefer rows with exact-normalized name or DOB match if present
    def match_priority(idx):
        row = df.iloc[idx]
        candidate_name = normalize_text(sget_str(row, "name", ""))
        candidate_dob  = normalize_dob(sget_str(row, "birth_date", ""))
        exact_name = (candidate_name == norm_name)
        dob_match  = bool(norm_dob and candidate_dob == norm_dob)
        return (dob_match, exact_name)

    sorted_matches = sorted(matches, key=lambda x: -x[1])
    top_index = None
    for _, _, idx in sorted_matches:
        if any(match_priority(idx)):
            top_index = idx
            break
    if top_index is None:
        _, _, top_index = sorted_matches[0]

    top_match = df.iloc[top_index]
    # base name score (0-100)
    base_name_score = max([score for _, score, i in matches if i == top_index], default=0)
    try:
        base_name_score = float(base_name_score)
    except Exception:
        base_name_score = 0.0

    # DOB logic
    cand_dob  = normalize_dob(sget_str(top_match, "birth_date", ""))
    dob_match = bool(norm_dob and cand_dob == norm_dob)

    # Combine score: if DOB provided & matches, boost; if provided & NOT match, reduce and clear.
    if norm_dob:
        if dob_match:
            final_score = int(round(min(100.0, 0.6 * base_name_score + 40.0)))
        else:
            final_score = int(round(0.6 * base_name_score))
    else:
        final_score = int(round(base_name_score))

    # Determine flags from source_type (still informative, but DOB can override risk reporting below)
    source_type = sget_str(top_match, "source_type", "").lower()
    is_pep = (source_type == "peps")
    is_sanctioned = (source_type == "sanctions")

    # Status normally follows flags…
    status = (
        "Fail Sanction & Pep" if (is_pep and is_sanctioned)
        else "Fail Sanction"  if is_sanctioned
        else "Fail PEP"       if is_pep
        else "Cleared"
    )
    risk_level = "High Risk" if is_sanctioned else "Medium Risk" if is_pep else "Cleared"

    # …but if the user supplied DOB and it does NOT match, CLEAR the result.
    if norm_dob and not dob_match:
        status = "Cleared"
        is_pep = False
        is_sanctioned = False
        risk_level = "Cleared"

    confidence = "High" if final_score >= 90 else "Medium" if final_score >= 80 else "Low"

    # Build Top Matches list (name + adjusted score considering DOB where available)
    top_matches: List[Tuple[str, int]] = []
    for _, nm_score, i in matches[:10]:
        row_i = df.iloc[i]
        row_dob = normalize_dob(sget_str(row_i, "birth_date", ""))
        if norm_dob:
            if row_dob == norm_dob:
                adj = int(round(min(100.0, 0.6 * float(nm_score) + 40.0)))
            else:
                adj = int(round(0.6 * float(nm_score)))
        else:
            adj = int(round(float(nm_score)))
        top_matches.append((sget_str(row_i, "name", "N/A"), adj))

    check_summary = {
        "Status": status,
        "Source": "Open Sanctions",
        "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    result = {
        "Sanctions Name": sget_str(top_match, "name", None) or None,
        "Birth Date": sget_str(top_match, "birth_date", None) or None,
        "Regime": _derive_regime_like(top_match),
        "Position": sget_list(top_match, "positions", []),
        "Topics": [],
        "Is PEP": bool(is_pep),
        "Is Sanctioned": bool(is_sanctioned),
        "Confidence": confidence,
        "Score": final_score,
        "Risk Level": risk_level,
        "Top Matches": top_matches,
        "Match Found": True,
        "Check Summary": check_summary,
    }

    _append_search_to_csv(name, check_summary)
    return result


# =========================
# (Optional) OFSI + simple PEP hint (unchanged/light)
# =========================
def get_latest_ofsi_csv_url():
    return "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.csv"

def download_and_save_ofsi_file(force_refresh=False):
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
    # simple first+last normalization
    def simplify(n: str) -> str:
        parts = n.split()
        return " ".join(parts[:1] + parts[-1:]) if parts else n
    df["Name"] = df["Raw Name"].apply(simplify).apply(normalize_text)
    return df[["Name", "Raw Name", "Date of Birth", "Group Type", "Regime Name", "UK Statement of Reasons"]]

def get_ofsi_data(force_refresh=False):
    path = download_and_save_ofsi_file(force_refresh)
    return load_ofsi_data(path) if path else None

def classify_risk(score):
    return "High" if score >= 90 else "Medium" if score >= 85 else "Cleared"

def match_against_ofsi(customer_name, dob, ofsi_df):
    normalized_input = normalize_text(customer_name)
    candidates = ofsi_df["Name"].fillna("").tolist()
    matches = get_best_name_matches(normalized_input, candidates)
    if not matches:
        return None

    _, best_score, idx = matches[0]
    best_match = ofsi_df.iloc[idx]
    suggestion = best_match["Raw Name"]
    dob_match = bool(dob and pd.notna(best_match["Date of Birth"]) and str(dob)[:4] in str(best_match["Date of Birth"]))

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
        "Risk Level": classify_risk(best_score),
        "Suggested Name": suggestion if best_score < 100 else None,
        "Top Matches": matches[:5],
    }

def query_wikidata(name):
    # kept minimal; optional hint only
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

def perform_sanctions_check(name, dob, force_refresh=False):
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
