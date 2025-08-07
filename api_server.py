from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import JSONResponse
from utils import (
    perform_sanctions_check,
    perform_opensanctions_check,
    refresh_opensanctions_data  
)

app = FastAPI(title="Sanctions & PEP Screening API")

# --- Shared Request Model ---
class SearchRequest(BaseModel):
    name: str
    dob: str | None = None

class OpenSanctionsRequest(SearchRequest):
    entity_type: str = "Person"  # default to Person, allow "Organization"

# --- OFSI + Wikidata Endpoint ---
@app.post("/check")
def check_ofsi_pep(data: SearchRequest):
    results = perform_sanctions_check(name=data.name, dob=data.dob)
    return JSONResponse(content=results)

# --- OpenSanctions Endpoint ---
@app.post("/opcheck")
def check_opensanctions(data: OpenSanctionsRequest):
    results = perform_opensanctions_check(name=data.name, dob=data.dob, entity_type=data.entity_type)
    return JSONResponse(content=results)


# --- OpenSanctions Refresh ---
@app.post("/refresh_opensanctions")
def trigger_refresh():
    try:
        refresh_opensanctions_data()
        return JSONResponse({"status": "success", "message": "OpenSanctions data refreshed."})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/")
def root():
    return {"message": "API is running"}