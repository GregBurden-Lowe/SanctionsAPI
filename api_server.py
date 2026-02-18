# api_server.py

from contextlib import asynccontextmanager
from typing import Optional, List
from datetime import datetime, timedelta
import os
import logging
import secrets

import jwt

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from utils import (
    perform_opensanctions_check,
    refresh_opensanctions_data,
    derive_entity_key,
    _normalize_text,
)
import screening_db
import auth_db

# Use uvicorn's logger so messages show in `journalctl -u sanctions-api -f`
logger = logging.getLogger("uvicorn.access")

# ---------------------------
# GUI authentication (JWT; users in DB)
# ---------------------------
_JWT_ALGORITHM = "HS256"
_JWT_SECRET = os.environ.get("GUI_JWT_SECRET", "").strip() or os.environ.get("SECRET_KEY", "change-me-in-production")


def _create_access_token(email: str, is_admin: bool, must_change_password: bool) -> str:
    expire = datetime.utcnow() + timedelta(hours=24)
    return jwt.encode(
        {"sub": email, "exp": expire, "is_admin": is_admin, "must_change_password": must_change_password},
        _JWT_SECRET,
        algorithm=_JWT_ALGORITHM,
    )


def _decode_token(token: str) -> Optional[dict]:
    """Return payload dict (sub, is_admin, must_change_password) or None."""
    try:
        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        sub = (payload.get("sub") or "").strip()
        if not sub:
            return None
        return {
            "sub": sub,
            "is_admin": bool(payload.get("is_admin")),
            "must_change_password": bool(payload.get("must_change_password")),
        }
    except Exception:
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB pool and ensure schema when DATABASE_URL is set."""
    pool = await screening_db.get_pool()
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                await screening_db.ensure_schema(conn)
                await auth_db.ensure_auth_schema(conn)
                await auth_db.seed_default_user(conn)
            logger.info("screening_db + auth_db: schema ensured")
        except Exception as e:
            import traceback
            logger.warning("schema ensure failed: %s\n%s", e, traceback.format_exc())
    yield
    await screening_db.close_pool()


app = FastAPI(title="Sanctions/PEP Screening API", version="1.0.0", lifespan=lifespan)

# ---------------------------
# CORS (Dynamics/Dataverse + Power Apps + your domain)
# ---------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"^https?://localhost(:\d+)?/?$|"
        r"^https?://127\.0\.0\.1(:\d+)?/?$|"
        r"^https://([a-zA-Z0-9-]+\.)*dynamics\.com$|"
        r"^https://([a-zA-Z0-9-]+\.)*crm[0-9]*\.dynamics\.com$|"
        r"^https://make\.powerapps\.com$|"
        r"^https://([a-zA-Z0-9-]+\.)*powerapps(portals)?\.com$|"
        r"^https://(www\.)?sanctions-check\.co\.uk/?$"
    ),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
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


class LoginRequest(BaseModel):
    username: str = Field(..., description="Email (used as username)")
    password: str = Field(..., description="Password")


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., description="Current password")
    new_password: str = Field(..., description="New password")


class CreateUserRequest(BaseModel):
    email: str = Field(..., description="User email")
    password: str = Field(..., description="Initial password")
    require_password_change: bool = Field(True, description="Require password change at first logon")


class UpdateUserRequest(BaseModel):
    is_admin: Optional[bool] = Field(None, description="Set user type (admin or standard user)")
    new_password: Optional[str] = Field(None, description="Reset password; user must change at next logon")


class ImportUserItem(BaseModel):
    email: str = Field(..., description="User email")
    password: Optional[str] = Field(None, description="Initial password; if missing, a random one is set (user must change at first logon)")


class ImportUsersRequest(BaseModel):
    users: List[ImportUserItem] = Field(..., max_length=500, description="List of users to import")


class SignupRequest(BaseModel):
    email: str = Field(..., description="User email (must be from an allowed domain)")
    password: str = Field(..., description="Password")


# Internal queue-ingestion API: request body (no screening results returned).
class InternalScreeningRequest(BaseModel):
    name: str = Field(..., description="Full name or organization to screen")
    dob: Optional[str] = Field(None, description="Date of birth (YYYY-MM-DD) or null")
    entity_type: Optional[str] = Field("Person", description="'Person' or 'Organization'")
    requestor: Optional[str] = Field(None, description="User/system requesting the screening")


# Bulk capped at 500 per request; callers should batch responsibly. Further rate controls may be added if misuse occurs.
class InternalScreeningBulkRequest(BaseModel):
    requests: List[InternalScreeningRequest] = Field(..., max_length=500)


def _internal_screening_client_ip(request: Request) -> str:
    """Client IP: X-Forwarded-For first proxy or direct client."""
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


async def require_internal_screening_auth(request: Request) -> None:
    """
    Protect /internal/screening/*: require API key and/or IP allowlist.
    At least one mechanism must be configured; both can be used together.
    """
    api_key = os.environ.get("INTERNAL_SCREENING_API_KEY", "").strip()
    allowlist_raw = os.environ.get("INTERNAL_SCREENING_IP_ALLOWLIST", "").strip()
    allowlist = [s.strip() for s in allowlist_raw.split(",") if s.strip()]

    if not api_key and not allowlist:
        raise HTTPException(
            status_code=503,
            detail="Internal screening API is disabled (no API key or IP allowlist configured)",
        )

    if api_key:
        # Accept X-Internal-Screening-Key or Authorization: Bearer <key>
        provided = request.headers.get("x-internal-screening-key", "").strip()
        if not provided and request.headers.get("authorization", "").startswith("Bearer "):
            provided = request.headers.get("authorization", "").replace("Bearer ", "", 1).strip()
        if provided != api_key:
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    if allowlist:
        client_ip = _internal_screening_client_ip(request)
        if client_ip not in allowlist:
            raise HTTPException(status_code=403, detail="Client IP not allowed")


def _get_token_from_request(request: Request) -> str:
    auth = request.headers.get("authorization", "").strip()
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return auth.replace("Bearer ", "", 1).strip()


async def get_current_user(request: Request) -> dict:
    """Require valid GUI JWT; return payload dict with sub (email), is_admin, must_change_password."""
    token = _get_token_from_request(request)
    payload = _decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


async def require_admin(request: Request) -> dict:
    """Require valid JWT and is_admin=True."""
    payload = await get_current_user(request)
    if not payload.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin required")
    return payload


# ---------------------------
# Routes
# ---------------------------
@app.get("/health", response_class=PlainTextResponse)
async def health():
    return "ok"


@app.get("/auth/config")
async def auth_config():
    """Tell the frontend whether GUI login is required (DB must be configured)."""
    pool = await screening_db.get_pool()
    return {"login_required": pool is not None}


@app.post("/auth/login")
async def auth_login(data: LoginRequest):
    """
    GUI login: email + password. Returns JWT. Users are stored in DB (seed user: see auth_db).
    Requires DATABASE_URL.
    """
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Login unavailable (configure DATABASE_URL)")
    email = data.username.strip().lower()
    async with pool.acquire() as conn:
        user = await auth_db.verify_user(conn, email, data.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = _create_access_token(
        user["email"],
        is_admin=user["is_admin"],
        must_change_password=user["must_change_password"],
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "username": user["email"],
            "email": user["email"],
            "must_change_password": user["must_change_password"],
            "is_admin": user["is_admin"],
        },
    }


@app.post("/auth/change-password")
async def auth_change_password(data: ChangePasswordRequest, payload: dict = Depends(get_current_user)):
    """
    Change password (e.g. after first logon). Requires current password. Returns new JWT.
    """
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Unavailable")
    email = payload["sub"]
    async with pool.acquire() as conn:
        user = await auth_db.get_user_by_email(conn, email)
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        if not auth_db.verify_password(data.current_password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Current password is incorrect")
        err = _validate_signup_password(data.new_password)
        if err:
            raise HTTPException(status_code=400, detail=err)
        await auth_db.update_password(conn, str(user["id"]), data.new_password)
    token = _create_access_token(email, is_admin=user["is_admin"], must_change_password=False)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "username": email,
            "email": email,
            "must_change_password": False,
            "is_admin": user["is_admin"],
        },
    }


# Self-signup only allowed for these email domains (lowercase)
ALLOWED_SIGNUP_DOMAINS = frozenset({
    "legalprotectiongroup.co.uk",
    "devonbaysolutions.co.uk",
    "devonbayadjusting.co.uk",
    "devonbayinsurance.ai",
})

# Weak passwords to reject (lowercase)
_WEAK_PASSWORDS = frozenset({
    "password", "password1", "password12", "password123", "admin", "admin123",
    "letmein", "welcome", "monkey", "qwerty", "abc123", "password!", "password1!",
    "iloveyou", "sunshine", "princess", "football", "shadow", "master", "login",
})


def _validate_signup_password(password: str) -> Optional[str]:
    """Return an error message if password is weak, else None."""
    if not password or len(password) < 8:
        return "Password must be at least 8 characters."
    if password.lower() in _WEAK_PASSWORDS:
        return "Choose a stronger password that is not easily guessed."
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(c in "!@#$%^&*()_+-=[]{}|;:',.<>?/`~\"\\" for c in password)
    if not has_upper:
        return "Password must include at least one uppercase letter."
    if not has_lower:
        return "Password must include at least one lowercase letter."
    if not has_digit:
        return "Password must include at least one number."
    if not has_special:
        return "Password must include at least one special character (e.g. !@#$%)."
    return None


@app.post("/auth/signup")
async def auth_signup(data: SignupRequest):
    """Self-signup for users with an allowed company email domain. Users set their own password so no change required at first logon."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Signup unavailable (configure DATABASE_URL)")
    email = data.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email required")
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    domain = email.split("@")[-1]
    if domain not in ALLOWED_SIGNUP_DOMAINS:
        raise HTTPException(
            status_code=400,
            detail="Signup is only available for approved company email domains.",
        )
    err = _validate_signup_password(data.password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        async with pool.acquire() as conn:
            user = await auth_db.create_user(
                conn,
                email,
                data.password,
                must_change_password=False,
                is_admin=False,
            )
        token = _create_access_token(
            user["email"],
            is_admin=user["is_admin"],
            must_change_password=user["must_change_password"],
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "username": user["email"],
                "email": user["email"],
                "must_change_password": user["must_change_password"],
                "is_admin": user["is_admin"],
            },
        }
    except Exception as e:
        err = str(e).lower()
        if "unique" in err or "duplicate" in err or "exists" in err:
            raise HTTPException(status_code=400, detail="An account with this email already exists")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/auth/me")
async def auth_me(payload: dict = Depends(get_current_user)):
    """Return current user from JWT."""
    return {"username": payload["sub"], "email": payload["sub"], "must_change_password": payload.get("must_change_password", False), "is_admin": payload.get("is_admin", False)}


@app.get("/auth/users", dependencies=[Depends(require_admin)])
async def auth_list_users():
    """List all users (admin only). Requires DATABASE_URL."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Unavailable")
    async with pool.acquire() as conn:
        users = await auth_db.list_users(conn)
    return {"users": users}


@app.post("/auth/users", dependencies=[Depends(require_admin)])
async def auth_create_user(data: CreateUserRequest):
    """Create a new user (admin only). Requires DATABASE_URL."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Unavailable")
    email = data.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email required")
    err = _validate_signup_password(data.password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    try:
        async with pool.acquire() as conn:
            user = await auth_db.create_user(
                conn,
                email,
                data.password,
                must_change_password=data.require_password_change,
                is_admin=False,
            )
        return user
    except Exception as e:
        err = str(e).lower()
        if "unique" in err or "duplicate" in err or "exists" in err:
            raise HTTPException(status_code=400, detail="A user with this email already exists")
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/auth/users/{user_id}", dependencies=[Depends(require_admin)])
async def auth_update_user(user_id: str, body: UpdateUserRequest):
    """Update a user: set role (admin/user) and/or reset password (admin only)."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Unavailable")
    if body.is_admin is None and not body.new_password:
        raise HTTPException(status_code=400, detail="Provide is_admin and/or new_password")
    if body.new_password:
        err = _validate_signup_password(body.new_password)
        if err:
            raise HTTPException(status_code=400, detail=err)
    try:
        async with pool.acquire() as conn:
            await auth_db.update_user(
                conn,
                user_id,
                is_admin=body.is_admin,
                new_password=body.new_password.strip() if body.new_password else None,
            )
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/auth/users/import", dependencies=[Depends(require_admin)])
async def auth_import_users(body: ImportUsersRequest):
    """Import multiple users (admin only). All imported users have require password change at first logon. Max 500 per request."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Unavailable")
    created = 0
    skipped = 0
    errors: List[dict] = []
    async with pool.acquire() as conn:
        for item in body.users:
            email = (item.email or "").strip().lower()
            if not email:
                errors.append({"email": item.email or "", "error": "Email required"})
                continue
            password = (item.password or "").strip() or secrets.token_urlsafe(16)
            try:
                await auth_db.create_user(
                    conn,
                    email,
                    password,
                    must_change_password=True,
                    is_admin=False,
                )
                created += 1
            except Exception as e:
                err = str(e).lower()
                if "unique" in err or "duplicate" in err or "exists" in err:
                    skipped += 1
                else:
                    errors.append({"email": email, "error": str(e)})
    return {"created": created, "skipped": skipped, "errors": errors}


@app.options("/opcheck")
@app.options("/refresh_opensanctions")
@app.options("/auth/config")
@app.options("/auth/login")
@app.options("/auth/me")
@app.options("/auth/change-password")
@app.options("/auth/signup")
@app.options("/auth/users")
@app.options("/auth/users/import")
@app.options("/auth/users/{user_id}")
@app.options("/internal/screening/jobs")
@app.options("/internal/screening/jobs/bulk")
async def cors_preflight():
    """Ensure CORS preflight (OPTIONS) returns 200 so browsers allow the request."""
    return {}

def _run_check_sync(data: OpCheckRequest):
    """Run screening synchronously (used when DB is disabled)."""
    return perform_opensanctions_check(
        name=data.name.strip(),
        dob=(data.dob.strip() if isinstance(data.dob, str) else data.dob),
        entity_type=(data.entity_type or "Person"),
        requestor=data.requestor.strip(),
    )


def _opcheck_queue_threshold() -> int:
    """Configurable queue pressure threshold; above this we enqueue instead of running sync. Default 5."""
    try:
        return max(0, int(os.environ.get("OPCHECK_QUEUE_THRESHOLD", "5")))
    except (ValueError, TypeError):
        return 5


@app.post("/opcheck")
async def check_opensanctions(data: OpCheckRequest):
    """
    Screen an entity. With DB: 200 = result (reused or completed synchronously); 202 = queued due to load.
    Reuse always first. When no cache: if queue pressure is below threshold, run sync (200); else enqueue (202).
    """
    if not data.requestor or not data.requestor.strip():
        return JSONResponse(
            status_code=400,
            content={
                "error": "missing_requestor",
                "message": "Please provide 'requestor' (your name) to run a check."
            },
        )
    if not data.name or not data.name.strip():
        return JSONResponse(
            status_code=400,
            content={"error": "missing_name", "message": "Please provide 'name' to run a check."},
        )

    name = data.name.strip()
    dob = (data.dob.strip() if isinstance(data.dob, str) else data.dob) or None
    entity_type = (data.entity_type or "Person")
    requestor = data.requestor.strip()

    pool = await screening_db.get_pool()
    if pool is None:
        # No DB: run check synchronously
        return _run_check_sync(data)

    entity_key = derive_entity_key(display_name=name, entity_type=entity_type, dob=dob)
    async with pool.acquire() as conn:
        cached = await screening_db.get_valid_screening(conn, entity_key)
        if cached is not None:
            # Reuse always first, regardless of load
            logger.info("screening reused (valid) entity_key=%s", entity_key[:16])
            return cached

        # Queue pressure check: under threshold => sync; at or over => enqueue (graceful load protection)
        count = await screening_db.get_pending_running_count(conn)
        threshold = _opcheck_queue_threshold()
        if count >= threshold:
            job_id = await screening_db.enqueue_job(
                conn, entity_key=entity_key, name=name, date_of_birth=dob,
                entity_type=entity_type, requestor=requestor,
            )
            logger.info(
                "screening queued due to load job_id=%s entity_key=%s queue_depth=%s threshold=%s",
                job_id, entity_key[:16], count, threshold,
            )
            return JSONResponse(
                status_code=202,
                content={
                    "status": "queued",
                    "job_id": job_id,
                    "message": "Screening queued (load protection). Poll GET /opcheck/jobs/{job_id} for outcome.",
                },
                headers={"Location": f"/opcheck/jobs/{job_id}"},
            )

    # Under threshold: run screening synchronously, then upsert
    logger.info("synchronous screening chosen entity_key=%s queue_depth=%s threshold=%s", entity_key[:16], count, threshold)
    results = perform_opensanctions_check(
        name=name, dob=dob, entity_type=entity_type, requestor=requestor,
    )
    async with pool.acquire() as conn:
        await screening_db.upsert_screening(
            conn, entity_key=entity_key, display_name=name, normalized_name=_normalize_text(name),
            date_of_birth=dob, entity_type=entity_type, requestor=requestor, result=results,
        )
    return results


@app.get("/opcheck/jobs/{job_id}")
async def get_opcheck_job(job_id: str):
    """Return job status; when completed, include the screening result."""
    pool = await screening_db.get_pool()
    if pool is None:
        return JSONResponse(status_code=404, content={"error": "not_found", "message": "Job not found"})
    async with pool.acquire() as conn:
        out = await screening_db.get_job_status(conn, job_id)
    if out is None:
        return JSONResponse(status_code=404, content={"error": "not_found", "message": "Job not found"})
    return out

# ---------------------------
# Internal queue-ingestion API (enqueue only; no screening, no results)
# Protected by INTERNAL_SCREENING_API_KEY and/or INTERNAL_SCREENING_IP_ALLOWLIST.
# ---------------------------

async def _internal_screening_outcome(conn, item: InternalScreeningRequest) -> dict:
    """
    One entity: validate, then reused | already_pending | queued.
    Returns { status, job_id? }. Does NOT run screening; does NOT return results.
    Status meanings: reused = previously screened and still valid; queued = screening will run async;
    already_pending = a job for this entity is already in the queue (pending or running).
    """
    name = (item.name or "").strip()
    requestor = (item.requestor or "").strip()
    if not name:
        return {"status": "error", "error": "missing_name"}
    if not requestor:
        return {"status": "error", "error": "missing_requestor"}
    dob = (item.dob.strip() if isinstance(item.dob, str) else item.dob) or None
    entity_type = (item.entity_type or "Person")

    entity_key = derive_entity_key(display_name=name, entity_type=entity_type, dob=dob)

    valid = await screening_db.get_valid_screening(conn, entity_key)
    if valid is not None:
        return {"status": "reused"}

    if await screening_db.has_pending_or_running_job(conn, entity_key):
        return {"status": "already_pending"}

    job_id = await screening_db.enqueue_job(
        conn, entity_key=entity_key, name=name, date_of_birth=dob,
        entity_type=entity_type, requestor=requestor,
    )
    return {"status": "queued", "job_id": job_id}


@app.post("/internal/screening/jobs", dependencies=[Depends(require_internal_screening_auth)])
async def internal_screening_jobs(data: InternalScreeningRequest):
    """
    Enqueue a single screening request. Does NOT run screening; never returns screening results.
    Status: reused = previously screened and still valid; already_pending = job already in progress;
    queued = new job enqueued (screening will occur asynchronously). Requires DB.
    """
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail="Screening queue requires DATABASE_URL",
        )
    async with pool.acquire() as conn:
        outcome = await _internal_screening_outcome(conn, data)
    if outcome.get("status") == "error":
        raise HTTPException(status_code=400, detail=outcome.get("error", "validation error"))
    logger.info("internal screening status=%s job_id=%s", outcome.get("status"), outcome.get("job_id"))
    return outcome


@app.post("/internal/screening/jobs/bulk", dependencies=[Depends(require_internal_screening_auth)])
async def internal_screening_jobs_bulk(body: InternalScreeningBulkRequest):
    """
    Enqueue multiple screening requests. Does NOT run screening; never returns screening results.
    Status per item: reused | already_pending | queued (same meanings as single endpoint).
    Bulk is capped at 500 items per request; callers should batch responsibly. Further rate
    controls may be added if misuse occurs. Requires DB.
    """
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail="Screening queue requires DATABASE_URL",
        )
    results = []
    async with pool.acquire() as conn:
        for item in body.requests:
            outcome = await _internal_screening_outcome(conn, item)
            results.append(outcome)
    # Lightweight visibility: counts for "is the queue backing up?" / "reusing or re-screening?"
    counts = {"reused": 0, "already_pending": 0, "queued": 0, "error": 0}
    for r in results:
        s = r.get("status")
        if s in counts:
            counts[s] += 1
    logger.info("internal screening bulk total=%s reused=%s already_pending=%s queued=%s errors=%s",
                len(results), counts["reused"], counts["already_pending"], counts["queued"], counts["error"])
    return {"results": results}


@app.post("/refresh_opensanctions")
async def refresh_opensanctions(body: RefreshRequest):
    """
    Download latest consolidated sanctions (and optionally PEPs), write to parquet.
    This clears cached DataFrame in utils so new data is used immediately.
    No authentication required (API contract unchanged). GUI login only affects access to the website.
    """
    try:
        refresh_opensanctions_data(include_peps=body.include_peps)
        return {"status": "ok", "include_peps": body.include_peps}
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)},
        )

# Serve built frontend from frontend/dist (must be last so API routes take precedence).
# Backend works unchanged if dist is missing (e.g. API-only deployments).
_app_dir = os.path.dirname(os.path.abspath(__file__))
_frontend_dist = os.path.join(_app_dir, "frontend", "dist")
if os.path.isdir(_frontend_dist):
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")