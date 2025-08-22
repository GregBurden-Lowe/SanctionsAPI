# utils.py (full)

import os
import csv
import re
import unicodedata
from datetime import datetime
from typing import Optional

import pandas as pd
import requests
from rapidfuzz import fuzz
import pyarrow as pa
import pyarrow.parquet as pq


# ---------------------------
# General helpers
# ---------------------------

def normalize_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).lower().strip()


def normalize_dob(dob) -> Optional[str]:
    try:
        return pd.to_datetime(dob).strftime("%Y-%m-%d")
    except Exception:
        return None


def confidence_no_match(score: float) -> str:
    if score < 30:
        return "Very High"
    elif score < 45:
        return "High"
    elif score < 55:
        return "Medium"
    else:
        return "Low"


def get_best_name_matches(search_name, candidates, limit=50, threshold=80):
    """
    Robust fuzzy match with token heuristics.
    Returns: list of tuples (clean_name, score, idx)
    """
    def preprocess(name):
        name = normalize_text(name)
        blacklist = {
            "the", "ltd", "llc", "inc", "co", "company", "corp", "plc", "limited",
            "real", "estate", "group", "services", "solutions", "hub", "global",
            "trust", "association", "federation", "union", "committee", "organization",
            "network", "centre", "center", "international", "foundation", "institute",
            "bank"
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
            results.append((candidate_cleaned, score, idx))
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
            results.append((candidate_cleaned, score, idx))

    return sorted(results, key=lambda x: x[1], reverse=True)[:limit]


# ---------------------------
# OpenSanctions data & search
# ---------------------------

def load_opensanctions_from_parquet(parquet_path="data/opensanctions.parquet") -> pd.DataFrame:
    if not os.path.exists(parquet_path):
        return pd.DataFrame()
    try:
        return pd.read_parquet(parquet_path)
    except Exception as e:
        print(f"[OpenSanctions] Failed to load parquet: {e}")
        return pd.DataFrame()


def refresh_opensanctions_data():
    """
    Download latest OpenSanctions:
      - Global consolidated sanctions
      - Global PEPs
    Tag rows with source_type ∈ {"sanctions","peps"} and write a single parquet.
    """
    urls = {
        "sanctions": "https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv",
        "peps":      "https://data.opensanctions.org/datasets/latest/peps/targets.simple.csv",
    }

    os.makedirs("data", exist_ok=True)
    combined = []

    wanted_cols = [
        "id", "schema", "name", "aliases", "birth_date",
        "countries", "addresses", "identifiers", "sanctions",
        "phones", "emails", "program_ids", "dataset",
        "first_seen", "last_seen", "last_change",
    ]

    for label, url in urls.items():
        try:
            r = requests.get(url, timeout=180)
            r.raise_for_status()
            csv_path = f"data/opensanctions_{label}_latest.csv"
            with open(csv_path, "wb") as f:
                f.write(r.content)

            df = pd.read_csv(csv_path, low_memory=False)

            # ensure expected columns exist (dataset may drift)
            for c in wanted_cols:
                if c not in df.columns:
                    df[c] = pd.NA

            for col in ["schema", "name", "birth_date", "program_ids", "dataset"]:
                df[col] = df[col].astype("string")

            df["source_type"] = label  # "sanctions" or "peps"
            combined.append(df[wanted_cols + ["source_type"]])

            print(f"[OpenSanctions] {label}: {len(df):,} rows")

        except Exception as e:
            print(f"[OpenSanctions] {label} download failed: {e}")

    if not combined:
        print("[OpenSanctions] No data written.")
        return

    all_df = pd.concat(combined, ignore_index=True)
    table = pa.Table.from_pandas(all_df, preserve_index=False)
    pq.write_table(table, "data/opensanctions.parquet")
    print("[OpenSanctions] Parquet refreshed at data/opensanctions.parquet")


def _append_search_to_csv(name, summary, path="data/search_log.csv"):
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


def _derive_regime_like(row) -> Optional[str]:
    """
    Create a short, useful label for UI from consolidated data.
    Priority:
      1) program_ids (e.g., 'EU-UKR;SECO-UKRAINE;UA-SA1644') -> first token
      2) sanctions (long text; take first semicolon/newline chunk)
      3) dataset (e.g., 'EU Council Official Journal Sanctioned Entities')
    """
    prog = str(row.get("program_ids") or "").strip()
    if prog:
        return prog.split(";")[0].strip()

    sanc = str(row.get("sanctions") or "").strip()
    if sanc:
        part = sanc.split(";")[0].strip()
        if not part:
            part = sanc.splitlines()[0].strip()
        if part:
            return part

    ds = str(row.get("dataset") or "").strip()
    return ds or None


def perform_opensanctions_check(name, dob, entity_type="Person", parquet_path="data/opensanctions.parquet"):
    """
    DOB handling:
      - If DOB is provided, we REQUIRE an exact match on both normalized name and DOB
        to consider it a positive match (PEP or Sanction). If none exists, we return Cleared
        but still include Top Matches for audit.
      - If DOB is not provided, we use classic fuzzy name matching.
    """
    df = load_opensanctions_from_parquet(parquet_path)
    if df.empty:
        return {"error": "No data available."}

    # Filter by entity type via schema
    et = (entity_type or "Person").lower()
    if et == "organization":
        df = df[df["schema"].astype(str).str.lower().isin(["organization", "legalentity", "company"])]
    else:
        df = df[df["schema"].astype(str).str.lower() == et]

    # if nothing left, return cleared
    if df.empty:
        result = _empty_no_match_result()
        _append_search_to_csv(name, result["Check Summary"])
        return result

    # Fuzzy candidates for audit/top-matches purposes
    candidates = df["name"].fillna("").tolist()
    matches = get_best_name_matches(name, candidates)

    # If DOB provided, enforce strict rule: require exact (normalized) name + exact DOB
    norm_name = normalize_text(name)
    norm_dob = normalize_dob(dob)

    strict_hit_idx = None
    if norm_dob:
        for _, _, idx in matches:
            row = df.iloc[idx]
            cand_name = normalize_text(str(row.get("name") or ""))
            cand_dob = normalize_dob(row.get("birth_date"))
            if cand_name == norm_name and cand_dob == norm_dob:
                strict_hit_idx = idx
                break

        if strict_hit_idx is None:
            # No strict hit -> Cleared, but include top matches for visibility
            result = _empty_no_match_result()
            result["Top Matches"] = [(df.iloc[i].get("name", "N/A"), round(score, 1)) for _, score, i in matches]
            _append_search_to_csv(name, result["Check Summary"])
            return result

        # We have a strict hit; treat like a normal positive match using that row
        top_index = strict_hit_idx
        best_score = 100.0
    else:
        # No DOB provided: pick the best fuzzy match, prefer exact normalized name where possible
        if not matches:
            result = _empty_no_match_result()
            _append_search_to_csv(name, result["Check Summary"])
            return result

        def priority(idx):
            row = df.iloc[idx]
            cand_name = normalize_text(str(row.get("name") or ""))
            return cand_name == norm_name

        # Choose first with exact-normalized name, otherwise top fuzzy
        sorted_matches = sorted(matches, key=lambda x: -x[1])
        top_index = None
        for _, _, i in sorted_matches:
            if priority(i):
                top_index = i
                break
        if top_index is None:
            _, _, top_index = sorted_matches[0]

        best_score = max([score for _, score, i in matches if i == top_index], default=0)

    # Build result from chosen row
    top_match = df.iloc[top_index]
    source_type = str(top_match.get("source_type", "")).lower()
    is_pep = (source_type == "peps")
    is_sanctioned = (source_type == "sanctions")

    if is_pep and is_sanctioned:
        status = "Fail Sanction & Pep"
    elif is_sanctioned:
        status = "Fail Sanction"
    elif is_pep:
        status = "Fail PEP"
    else:
        status = "Cleared"

    check_summary = {
        "Status": status,
        "Source": "Open Sanctions",
        "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    result = {
        "Sanctions Name": top_match.get("name"),
        "Birth Date": top_match.get("birth_date"),
        "Regime": _derive_regime_like(top_match),
        "Position": top_match.get("positions"),   # may be NaN depending on dataset
        "Topics": [],                              # legacy field kept for compatibility
        "Is PEP": is_pep,
        "Is Sanctioned": is_sanctioned,
        "Confidence": "High" if best_score >= 90 else "Medium" if best_score >= 80 else "Low",
        "Score": best_score,
        "Risk Level": "High Risk" if is_sanctioned else "Medium Risk" if is_pep else "Cleared",
        "Top Matches": [(df.iloc[i].get("name", "N/A"), round(score, 1)) for _, score, i in matches],
        "Match Found": True,
        "Check Summary": check_summary,
    }

    _append_search_to_csv(name, check_summary)
    return result


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


# ---------------------------
# (Optional) OFSI + simple PEP lookup for /check
# ---------------------------

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
    # simple "first + last" collapse then normalize
    def fl(n):
        parts = n.split()
        return " ".join((parts[:1] + parts[-1:])) if parts else ""
    df["Name"] = df["Raw Name"].apply(fl).apply(normalize_text)
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
    # kept for compatibility with /check; can be removed if not needed
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
    # legacy endpoint /check support
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
