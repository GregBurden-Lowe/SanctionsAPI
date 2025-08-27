# api_server.py

from typing import Optional
from datetime import datetime
import os
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from utils import (
    perform_opensanctions_check,
    refresh_opensanctions_data,
    post_to_power_automate_async,
    send_audit_to_power_automate,  # used only when sync debug is enabled
)

# Use uvicorn's logger so messages show in `journalctl -u sanctions-api -f`
logger = logging.getLogger("uvicorn.access")

app = FastAPI(title="Sanctions/PEP Screening API", version="1.0.0")

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
    dob: Optional[str] = Field(None, description="Date of birth (YYYY-MM-DD) or null")
    entity_type: Optional[str] = Field("Person", description="'Person' or 'Organization'")
    requestor: Optional[str] = Field(None, description="User performing the check (required)")

class RefreshRequest(BaseModel):
    include_peps: bool = Field(
        True,
        description="Include consolidated PEPs in the parquet (uses additional memory)"
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
async def check_opensanctions(data: OpCheckRequest):
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
            content={"error": "missing_name", "message": "Please provide 'name' to run a check."},
        )

    # Run the core check
    results = perform_opensanctions_check(
        name=data.name.strip(),
        dob=(data.dob.strip() if isinstance(data.dob, str) else data.dob),
        entity_type=(data.entity_type or "Person"),
        requestor=data.requestor.strip(),
    )

    # Prepare audit payload (+ flattened top matches for PA convenience)
    top_matches = results.get("Top Matches") or []
    # Support either [(name,score), ...] or [{"name":..,"score":..}, ...]
    top_matches_flat = []
    for tm in top_matches:
        if isinstance(tm, (list, tuple)) and len(tm) >= 2:
            n, s = tm[0], tm[1]
        elif isinstance(tm, dict):
            n, s = tm.get("name"), tm.get("score")
        else:
            continue
        if isinstance(n, str):
            top_matches_flat.append(f"{n} ({s})")

    payload = {
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

    # Log what we're sending
    logger.info(f"[Audit->PA] prepared payload keys={list(payload.keys())}")

    # Send audit (async by default). Set POWER_AUTOMATE_SYNC_TEST=1 to send synchronously for debugging.
    try:
        if os.getenv("POWER_AUTOMATE_SYNC_TEST", "").strip() == "1":
            ok, msg = send_audit_to_power_automate(payload)
            logger.info(f"[Audit->PA][SYNC] ok={ok} msg={msg}")
        else:
            logger.info("[Audit->PA] dispatching async …")
            post_to_power_automate_async(payload)
    except Exception as e:
        logger.error(f"[Audit->PA] unexpected error: {e}")

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