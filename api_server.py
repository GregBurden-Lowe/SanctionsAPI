# api_server.py

from typing import Optional, Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from utils import (
    perform_sanctions_check,
    perform_opensanctions_check,
    refresh_opensanctions_data,
)

app = FastAPI(title="Sanctions & PEP Screening API")

# --- CORS (Dynamics/Power Apps + your domain) ---
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"^https://([a-zA-Z0-9-]+\.)*dynamics\.com$|"
        r"^https://([a-zA-Z0-9-]+\.)*crm[0-9]*\.dynamics\.com$|"
        r"^https://make\.powerapps\.com$|"
        r"^https://([a-zA-Z0-9-]+\.)*powerapps(portals)?\.com$|"
        r"^https://(www\.)?sanctions-check\.co\.uk$"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Models ---
class SearchRequest(BaseModel):
    name: str
    dob: Optional[str] = None

class OpenSanctionsRequest(SearchRequest):
    # Accept "Person" or "Organization"
    entity_type: Literal["Person", "Organization"] = "Person"

# --- Startup: ensure data is present ---
@app.on_event("startup")
def startup_event():
    print("🔄 Refreshing OpenSanctions data on startup...")
    try:
        refresh_opensanctions_data()
        print("✅ OpenSanctions data loaded.")
    except Exception as e:
        print(f"❌ Failed to refresh data: {e}")

# --- Health ---
@app.get("/")
def root():
    return {"message": "API is running"}

# --- UK OFSI + light PEP (Wikidata) ---
@app.post("/check")
def check_ofsi_pep(data: SearchRequest):
    results = perform_sanctions_check(name=data.name, dob=data.dob)
    return JSONResponse(content=results)

# --- OpenSanctions consolidated matching ---
@app.post("/opcheck")
def check_opensanctions(data: OpenSanctionsRequest):
    results = perform_opensanctions_check(
        name=data.name,
        dob=data.dob,
        entity_type=data.entity_type,
    )
    return JSONResponse(content=results)

# --- Manually refresh OpenSanctions parquet ---
@app.post("/refresh_opensanctions")
def trigger_refresh():
    try:
        refresh_opensanctions_data()
        return JSONResponse({"status": "success", "message": "OpenSanctions data refreshed."})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
