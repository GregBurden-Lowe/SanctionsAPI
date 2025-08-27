# api_server.py

from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from utils import (
    perform_opensanctions_check,
    refresh_opensanctions_data,
    send_audit_to_power_automate,  # used via BackgroundTasks
)

app = FastAPI(title="Sanctions/PEP Screening API", version="1.1.0")

# ---------------------------
# CORS (Dynamics/Dataverse + Power Apps + your domain)
# ---------------------------
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
    max_age=86400,
)

# ---------------------------
# Models
# ---------------------------
class OpCheckRequest(BaseModel):
    name: str = Field(..., description="Full name or organization to screen")
    dob: Optional[str] = Field(None, description="Date of birth (YYYY-MM-DD) or None")
    entity_type: Optional[str] = Field("Person", description="'Person' or 'Organization'")
    requestor: Optional[str] = Field(
        None,
        description="Name of the user initiating the check (required for audit)",
    )

class RefreshRequest(BaseModel):
    include_peps: bool = Field(
        True,
        description="Include consolidated PEPs in the parquet (uses additional memory)",
    )

# ---------------------------
# Routes
# ---------------------------
@app.get("/", response_class=PlainTextResponse)
async def root():
    return "Sanctions/PEP Screening API is running."

@app.get("/health", response_class=PlainTextResponse)
async def health():
    return "ok"

@app.post("/opcheck")
async def check_opensanctions(data: OpCheckRequest, background_tasks: BackgroundTasks):
    # Validate presence of requestor with a friendly message
    if not data.requestor or not data.requestor.strip():
        return JSONResponse(
            status_code=400,
            content={
                "error": "missing_requestor",
                "message": "Please provide 'requestor' (your name) to run a check."
            },
        )

    # Validate name is present
    if not data.name or not data.name.strip():
        return JSONResponse(
            status_code=400,
            content={
                "error": "missing_name",
                "message": "Please provide 'name' to run a check."
            },
        )

    # Run the core check
    results = perform_opensanctions_check(
        name=data.name.strip(),
        dob=(data.dob.strip() if isinstance(data.dob, str) else data.dob),
        entity_type=(data.entity_type or "Person"),
        requestor=data.requestor.strip(),
    )

    # Build the audit payload for Power Automate (includes a flattened Top Matches helper)
    top_matches = results.get("Top Matches") or []
    top_matches_flat = [f"{n} ({int(s)})" for n, s in top_matches if isinstance(n, str)]

    post_payload: Dict[str, Any] = {
        "request": {
            "name": data.name.strip(),
            "dob": (data.dob.strip() if isinstance(data.dob, str) else None),
            "entity_type": (data.entity_type or "Person"),
            "requestor": data.requestor.strip(),
            "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        },
        "result": results,
        "top_matches_flat": top_matches_flat,
    }

    # Fire-and-forget audit push to Power Automate (uses env POWER_AUTOMATE_FLOW_URL)
    background_tasks.add_task(
        send_audit_to_power_automate,
        post_payload,   # payload
        None,           # flow_url -> use env
        12,             # timeout seconds
        2,              # retries
    )

    return results

@app.post("/refresh_opensanctions")
async def refresh_opensanctions(body: RefreshRequest):
    """
    Download latest consolidated sanctions (and optionally PEPs), write to parquet.
    This clears cached DataFrame in utils so new data is used immediately.
    """
    try:
        refresh_opensanctions_data(include_peps=body.include_peps)
        return {"status": "ok", "include_peps": body.include_peps}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )

# Small helper to confirm the Flow URL is visible to the app (does not leak the secret)
@app.get("/debug/flow_env")
async def debug_flow_env():
    import os, urllib.parse
    url = (os.getenv("POWER_AUTOMATE_FLOW_URL") or "").strip()
    if not url:
        return {"has_env": False}
    parts = urllib.parse.urlparse(url)
    return {"has_env": True, "host": parts.netloc, "path": parts.path}