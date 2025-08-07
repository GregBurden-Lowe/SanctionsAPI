import os
import re
import json
import csv
import unicodedata
from datetime import datetime
from io import BytesIO

import pandas as pd
import requests
import streamlit as st
from rapidfuzz import fuzz
from tinydb import TinyDB
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
import pyarrow as pa
import pyarrow.csv as pv
import pyarrow.parquet as pq
import pyarrow.dataset as ds
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle

# --- Auth Gate ---
def require_login():
    if "user" not in st.session_state:
        st.warning("You need to be logged in to access this page.")
        st.stop()
import requests
import pyarrow.csv as pv
import pyarrow.parquet as pq

# --- Normalize Utilities ---
def normalize_text(text):
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^\w\s]", "", text).lower().strip()
    return re.sub(r"\s+", " ", text)

def simplify_name(name):
    parts = name.strip().split()
    return f"{parts[0]} {parts[-1]}" if len(parts) >= 2 else name

def normalize_dob(dob):
    try:
        return pd.to_datetime(dob).strftime("%Y-%m-%d")
    except:
        return None

# --- TinyDB Safe Loader ---
def safe_load_tinydb(path):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                if not f.read().strip():
                    raise ValueError("Empty JSON")
        except Exception:
            with open(path, "w") as f:
                f.write("{}")
    return TinyDB(path)

# --- Confidence for Non-Matches ---
def confidence_no_match(score):
    if score < 30:
        return "Very High"
    elif score < 45:
        return "High"
    elif score < 55:
        return "Medium"
    else:
        return "Low"

# --- PDF Export (Manual Sanctions) ---
def generate_pdf_report(name, dob, sanctions_result, pep_result):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=18, alignment=TA_CENTER)
    normal = styles['Normal']
    elements = [
        Paragraph("Sanctions & PEP Check Report", title_style),
        Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", normal),
        Spacer(1, 12),
        Paragraph(f"<b>Name:</b> {name}", normal),
        Paragraph(f"<b>Date of Birth:</b> {dob or 'N/A'}", normal),
        Spacer(1, 12),
    ]

    if sanctions_result:
        elements.append(Paragraph("<b>Sanctions Result:</b>", styles["Heading2"]))
        for key in ["Sanctions Name", "Regime", "Reason", "Confidence", "Score", "Risk Level"]:
            elements.append(Paragraph(f"<b>{key}:</b> {sanctions_result.get(key, 'N/A')}", normal))
    else:
        elements.append(Paragraph("<b>Sanctions Result:</b> None", normal))

    if pep_result:
        elements.append(Paragraph("<b>PEP Result:</b>", styles["Heading2"]))
        for key in ["PEP Name", "Party", "Position", "DoB"]:
            elements.append(Paragraph(f"<b>{key}:</b> {pep_result.get(key, 'N/A')}", normal))
    else:
        elements.append(Paragraph("<b>PEP Result:</b> None", normal))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()

# --- Load OpenSanctions Data from Parquet ---
def load_opensanctions_from_parquet(parquet_path="data/opensanctions.parquet"):
    if not os.path.exists(parquet_path):
        st.error("Parquet file not found. Please run the background sync script first.")
        return pd.DataFrame()
    try:
        return pd.read_parquet(parquet_path)
    except Exception as e:
        st.error(f"Failed to load parquet file: {e}")
        return pd.DataFrame()

# --- Match Scoring ---
def calculate_match_score(name, dob, row):
    name_score = fuzz.token_set_ratio(normalize_text(name), normalize_text(row.get("name", ""))) / 100
    has_dob = bool(dob and row.get("birth_date"))
    dob_score = 0.3 if has_dob and normalize_dob(dob) == normalize_dob(row.get("birth_date")) else 0
    return name_score * 0.7 + dob_score if has_dob else name_score

# --- Sanctions Check (OFSI + Wikidata) ---
def perform_sanctions_check(name, dob, force_refresh=False):
    ofsi_df = get_ofsi_data(force_refresh=force_refresh)
    if ofsi_df is None:
        return {
            "Sanctions Result": None,
            "PEP Result": None,
            "Top Matches": [],
            "Risk Level": "Cleared"
        }

    sanctions_result = match_against_ofsi(name, dob, ofsi_df)
    pep_result = query_wikidata(name)

    return {
        "Sanctions Result": sanctions_result,
        "PEP Result": pep_result,
        "Suggestion": sanctions_result.get("Suggested Name") if sanctions_result else None,
        "Top Matches": sanctions_result.get("Top Matches") if sanctions_result else [],
        "Risk Level": sanctions_result.get("Risk Level") if sanctions_result else "Cleared"
    }


# --- OpenSanctions Matching ---
def perform_opensanctions_check(name, dob, entity_type="Person", parquet_path="data/opensanctions.parquet"):
    df = load_opensanctions_from_parquet(parquet_path)
    if df.empty:
        return {"error": "No data available."}

    entity_type = entity_type.lower()
    if entity_type == "organization":
        df = df[df["schema"].str.lower().isin(["organization", "legalentity", "company"])]
    else:
        df = df[df["schema"].str.lower() == entity_type]

    candidates = df["name"].fillna("").tolist()
    matches = get_best_name_matches(name, candidates)

    if not matches:
        result = {
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
                "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }
        _append_search_to_csv(name, result["Check Summary"])
        return result

    norm_name = normalize_text(name)
    norm_dob = normalize_dob(dob)

    sorted_matches = sorted(matches, key=lambda x: -x[1])

    def match_priority(idx):
        row = df.iloc[idx]
        candidate_name = normalize_text(row.get("name", ""))
        candidate_dob = normalize_dob(row.get("birth_date", ""))
        exact_name = candidate_name == norm_name
        dob_match = norm_dob and candidate_dob == norm_dob
        return (dob_match, exact_name)

    top_index = None
    for _, score, idx in sorted_matches:
        pri = match_priority(idx)
        if any(pri):
            top_index = idx
            break

    if top_index is None:
        _, _, top_index = sorted_matches[0]

    top_match = df.iloc[top_index]
    best_score = max([score for _, score, idx in matches if idx == top_index], default=0)

    source_type = top_match.get("source_type", "").lower()
    is_pep = source_type == "peps"
    is_sanctioned = source_type == "sanctions"

    if is_pep and is_sanctioned:
        status = "Fail Sanction & Pep"
    elif is_sanctioned:
        status = "Fail Sanction"
    elif is_pep:
        status = "Fail PEP"
    else:
        status = "Cleared"

    regime = top_match.get("regime")
    top_matches = [(df.iloc[i].get("name", "N/A"), round(score, 1)) for _, score, i in matches]

    check_summary = {
        "Status": status,
        "Source": "Open Sanctions",
        "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    result = {
        "Sanctions Name": top_match.get("name"),
        "Birth Date": top_match.get("birth_date"),
        "Regime": regime,
        "Position": top_match.get("positions"),
        "Topics": [],
        "Is PEP": is_pep,
        "Is Sanctioned": is_sanctioned,
        "Confidence": "High" if best_score >= 90 else "Medium" if best_score >= 80 else "Low",
        "Score": best_score,
        "Risk Level": "High Risk" if is_sanctioned else "Medium Risk" if is_pep else "Cleared",
        "Top Matches": top_matches,
        "Match Found": True,
        "Check Summary": check_summary
    }

    _append_search_to_csv(name, check_summary)
    return result



# --- Append Search to CSV Log ---
def _append_search_to_csv(name, summary, path="data/search_log.csv"):
    dir_path = os.path.dirname(path) or "."
    os.makedirs(dir_path, exist_ok=True)
    file_exists = os.path.isfile(path)
    with open(path, mode="a", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["Date", "Name Searched", "Status", "Source"])
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "Date": summary.get("Date"),
            "Name Searched": name,
            "Status": summary.get("Status"),
            "Source": summary.get("Source")
        })

# --- Refresh OpenSanctions CSV + Parquet ---
def refresh_opensanctions_data():
    urls = {
        "sanctions": "https://data.opensanctions.org/datasets/20250515/gb_hmt_sanctions/targets.simple.csv?v=latest",
        "peps": "https://data.opensanctions.org/datasets/20250513/peps/targets.simple.csv?v=latest"
    }
    combined_df = None
    os.makedirs("data", exist_ok=True)

    for label, url in urls.items():
        response = requests.get(url)
        if response.status_code == 200:
            csv_path = f"data/opensanctions_{label}_latest.csv"
            with open(csv_path, "wb") as f:
                f.write(response.content)
            df = pd.read_csv(csv_path, low_memory=False)
            df["source_type"] = label  # Tag each record
            combined_df = pd.concat([combined_df, df], ignore_index=True) if combined_df is not None else df
        else:
            print(f"⚠ Failed to download {label} data: {response.status_code}")

    if combined_df is not None:
        table = pa.Table.from_pandas(combined_df)
        pq.write_table(table, "data/opensanctions.parquet")
        print("✔ OpenSanctions data refreshed.")
    else:
        print("⚠ No data written.")

def generate_opensanctions_pdf_report(name, dob, result, user_name=None, user_email=None):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'Title', parent=styles['Heading1'],
        fontSize=20, alignment=TA_CENTER, textColor=colors.HexColor("#003366"), spaceAfter=14
    )
    heading_style = ParagraphStyle(
        'Heading', parent=styles['Heading2'],
        fontSize=14, textColor=colors.HexColor("#003366"), spaceBefore=8, spaceAfter=6
    )
    normal = styles['Normal']

    elements = []

    # Title
    elements.append(Paragraph("Sanctions & PEP Screening Report", title_style))
    elements.append(Spacer(1, 6))

    # Metadata
    elements.append(Paragraph(f"Checked By: {user_name or 'N/A'} ({user_email or 'N/A'})", normal))
    elements.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", normal))
    elements.append(Spacer(1, 12))

    # Search Information Table
    info_table_data = [
        ["Name Searched", name or "N/A"],
        ["Date of Birth", dob or "N/A"]
    ]
    info_table = Table(info_table_data, colWidths=[130, 400])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 1), (0, -1), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 12))

    # Match Summary Table
    elements.append(Paragraph("Match Summary", heading_style))
    summary_data = [
        ["Sanctions Regime", result.get("Regime") or "None"],
        ["Is Sanctioned", "Yes" if result.get("Is Sanctioned") else "No"],
        ["Is PEP", "Yes" if result.get("Is PEP") else "No"],
        ["Risk Level", result.get("Risk Level", "N/A")],
        ["Confidence", result.get("Confidence", "N/A")],
        ["Score", f"{result.get('Score', 0)}%"]
    ]
    summary_table = Table(summary_data, colWidths=[130, 400])
    summary_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('BACKGROUND', (0, 0), (0, -1), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 12))

    # Utility: Add bulleted section
    def add_bullets(title, items):
        if isinstance(items, list) and items:
            elements.append(Paragraph(title, heading_style))
            for item in items:
                elements.append(Paragraph(f"• {item}", normal))
            elements.append(Spacer(1, 10))

    add_bullets("PEP Positions", result.get("Position", []))
    add_bullets("Topics", result.get("Topics", []))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()
# --- Helper: Normalize and simplify name ---
def normalize_text(text):
    if not isinstance(text, str):
        return ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^\w\s]", "", text)  # removes punctuation
    return re.sub(r"\s+", " ", text).lower().strip()

def simplify_name(name):
    parts = name.strip().split()
    return f"{parts[0]} {parts[-1]}" if len(parts) >= 2 else name

# --- Helper: Get OFSI CSV URL ---
def get_latest_ofsi_csv_url():
    return "https://ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.csv"

# --- Download OFSI CSV if needed ---
def download_and_save_ofsi_file(force_refresh=False):
    url = get_latest_ofsi_csv_url()
    file_path = "ofsi_latest.csv"
    
    if os.path.exists(file_path):
        modified_time = datetime.fromtimestamp(os.path.getmtime(file_path))
        age_minutes = (datetime.now() - modified_time).total_seconds() / 60
        if not force_refresh and age_minutes < 1440:  # Use cached file if < 24h old
            return file_path
    
    try:
        response = requests.get(url)
        if response.status_code == 200:
            with open(file_path, "wb") as f:
                f.write(response.content)
            return file_path
        else:
            st.error(f"OFSI download failed: {response.status_code}")
    except Exception as e:
        st.error(f"Error downloading OFSI file: {e}")
    
    return None

# --- Load and clean OFSI CSV data ---
def load_ofsi_data(file_path):
    df = pd.read_csv(file_path, header=1, low_memory=False)
    
    df = df.rename(columns={
        "DOB": "Date of Birth",
        "Regime": "Regime Name",
        "UK Sanctions List Date Designated": "UK Statement of Reasons"
    })

    # Combine name columns into one
    name_cols = [f"Name {i}" for i in range(1, 7)]
    df["Raw Name"] = df[name_cols].astype(str).apply(
        lambda row: " ".join(filter(lambda x: x and x.strip().lower() != "nan", row)), axis=1
    )
    df["Name"] = df["Raw Name"].apply(simplify_name).apply(normalize_text)

    return df[["Name", "Raw Name", "Date of Birth", "Group Type", "Regime Name", "UK Statement of Reasons"]]
def get_best_name_matches(search_name, candidates, limit=50, threshold=80):
    def preprocess(name):
        name = normalize_text(name)
        blacklist = {
            "the", "ltd", "llc", "inc", "co", "company", "corp", "plc", "limited",
            "real", "estate", "group", "services", "solutions", "hub", "global",
            "trust", "association", "federation", "union", "committee", "organization",
            "network", "centre", "center", "international", "foundation", "institute"
        }
        tokens = [word for word in name.split() if word not in blacklist]
        return " ".join(tokens), set(tokens)

    search_cleaned, search_tokens = preprocess(search_name)
    clean_candidates = [(i, *preprocess(c)) for i, c in enumerate(candidates)]
    results = []

    for idx, candidate_cleaned, candidate_tokens in clean_candidates:
        score = fuzz.token_set_ratio(search_cleaned, candidate_cleaned)

        if score < threshold:
            continue

        token_overlap = len(search_tokens & candidate_tokens)
        token_union = search_tokens | candidate_tokens
        jaccard = len(search_tokens & candidate_tokens) / max(1, len(token_union))

        # ✅ Allow short, direct matches (e.g. "houthis") to pass
        if len(search_tokens) <= 2 and search_cleaned == candidate_cleaned:
            results.append((candidate_cleaned, score, idx))
            continue

        # ⛔ Enforce token overlap and semantic similarity otherwise
        if token_overlap < 2:
            continue

        if jaccard < 0.4:
            continue

        if abs(len(search_tokens) - len(candidate_tokens)) > 2:
            score -= 15

        if len(candidate_tokens) <= 2 and len(search_tokens) > 3:
            score -= 20

        if score < threshold:
            continue

        results.append((candidate_cleaned, score, idx))

    return sorted(results, key=lambda x: x[1], reverse=True)[:limit]
# --- Main OFSI Data Loader (use this) ---
def get_ofsi_data(force_refresh=False):
    file_path = download_and_save_ofsi_file(force_refresh=force_refresh)
    return load_ofsi_data(file_path) if file_path else None
# --- Helper: Matching + token set logic ---


    return sorted(results, key=lambda x: x[1], reverse=True)[:limit]
# --- Helper: Risk classification ---
def classify_risk(score):
    return "High" if score >= 90 else "Medium" if score >= 85 else "Cleared"

# --- Main Matching Logic for OFSI ---
def match_against_ofsi(customer_name, dob, ofsi_df):
    normalized_input = normalize_text(simplify_name(customer_name))
    candidates = ofsi_df["Name"].fillna("").tolist()
    matches = get_best_name_matches(normalized_input, candidates)

    if not matches:
        return None

    best_name, best_score, idx = matches[0]
    best_match = ofsi_df.iloc[idx]
    suggestion = best_match["Raw Name"]

    # Check DoB match
    dob_match = dob and pd.notna(best_match["Date of Birth"]) and dob[:4] in str(best_match["Date of Birth"])

    confidence = "High" if best_score > 90 or dob_match else "Medium"
    risk = classify_risk(best_score)

    if best_score < 80:
        return {
            "Sanctions Name": None,
            "Suggested Name": suggestion,
            "Regime": None,
            "Reason": None,
            "Confidence": "Low",
            "Score": best_score,
            "Risk Level": "Cleared",
            "Top Matches": matches[:5]
        }

    return {
        "Sanctions Name": suggestion,
        "Regime": best_match["Regime Name"],
        "Reason": best_match["UK Statement of Reasons"],
        "Confidence": confidence,
        "Score": best_score,
        "Risk Level": risk,
        "Suggested Name": suggestion if best_score < 100 else None,
        "Top Matches": matches[:5]
    }

def query_wikidata(name):
    query = f'''
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

    headers = {"Accept": "application/sparql-results+json"}
    response = requests.get(
        "https://query.wikidata.org/sparql",
        params={"query": query},
        headers=headers
    )

    if response.status_code == 200:
        data = response.json().get('results', {}).get('bindings', [])
        if data:
            row = data[0]
            return {
                "PEP Name": row.get("personLabel", {}).get("value"),
                "DoB": row.get("dob", {}).get("value"),
                "Party": row.get("partyLabel", {}).get("value"),
                "Position": row.get("positionLabel", {}).get("value")
            }

    return None

def perform_sanctions_check(name, dob, force_refresh=False):
    ofsi_df = get_ofsi_data(force_refresh=force_refresh)
    if ofsi_df is None:
        return {
            "Sanctions Result": None,
            "PEP Result": None,
            "Top Matches": [],
            "Risk Level": "Cleared"
        }
    sanctions_result = match_against_ofsi(name, dob, ofsi_df)
    pep_result = query_wikidata(name)
    return {
        "Sanctions Result": sanctions_result,
        "PEP Result": pep_result,
        "Suggestion": sanctions_result.get("Suggested Name") if sanctions_result else None,
        "Top Matches": sanctions_result.get("Top Matches") if sanctions_result else [],
        "Risk Level": sanctions_result.get("Risk Level") if sanctions_result else "Cleared"
    }