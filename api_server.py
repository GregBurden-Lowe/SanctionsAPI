# api_server.py

from contextlib import asynccontextmanager
from typing import Optional, List, Literal
from datetime import datetime, timedelta
from enum import Enum
import os
import logging
import secrets
import json
import csv
import io

import jwt

from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, constr
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded


class SPAStaticFiles(StaticFiles):
    """Serve static files but fall back to index.html for missing paths so SPA client-side routing works on refresh."""

    _NO_FALLBACK_PREFIXES = (
        "api/",
        "auth/",
        "dashboard/",
        "docs",
        "redoc",
        "openapi.json",
        "admin/openapi.json",
        "mi/",
        "opcheck",
        "review/",
        "internal/",
        "refresh_opensanctions",
        "health",
        ".well-known/",
    )

    @staticmethod
    def _is_hidden_or_sensitive_path(path: str) -> bool:
        lowered = path.lower()
        if lowered.startswith("."):
            return True
        parts = [p for p in lowered.split("/") if p]
        if any(p.startswith(".") for p in parts):
            return True
        sensitive_suffixes = (
            ".env",
            ".ini",
            ".sql",
            ".log",
            ".bak",
            ".pem",
            ".key",
            ".crt",
            ".yml",
            ".yaml",
            ".toml",
            ".cfg",
            ".conf",
        )
        return lowered.endswith(sensitive_suffixes)

    @staticmethod
    def _should_spa_fallback(path: str) -> bool:
        normalized = (path or "").lstrip("/")
        if not normalized:
            return True
        lowered = normalized.lower()
        if any(lowered.startswith(prefix) for prefix in SPAStaticFiles._NO_FALLBACK_PREFIXES):
            return False
        if SPAStaticFiles._is_hidden_or_sensitive_path(lowered):
            return False
        # Treat extension-like paths as file requests, not client-side SPA routes.
        tail = lowered.rsplit("/", 1)[-1]
        if "." in tail:
            return False
        return True

    def lookup_path(self, path: str):
        full_path, stat_result = super().lookup_path(path)
        if stat_result is None:
            if not self._should_spa_fallback(path):
                return full_path, stat_result
            return super().lookup_path("index.html")
        return full_path, stat_result

from utils import (
    perform_opensanctions_check,
    perform_postgres_watchlist_check,
    refresh_opensanctions_data,
    sync_watchlist_entities_postgres,
    build_uk_sanctions_snapshot,
    compute_uk_snapshot_delta,
    derive_entity_key,
    derive_entity_key_variants,
    _normalize_text,
    detect_company_likeness,
    build_input_classification,
    get_matching_config,
    save_matching_config,
    get_effective_org_generic_tokens,
    get_protected_org_legal_suffixes,
    DATA_DIR,
)
from ai_triage import get_local_llm_config, ollama_health, run_ai_triage_batch
import screening_db
import auth_db
from routes.companies_house import router as companies_house_router

# Use uvicorn error logger for app logs (access logger expects HTTP access fields).
logger = logging.getLogger("uvicorn.error")
audit_logger = logging.getLogger("sanctions.audit")


def _rate_limit_key(request: Request) -> str:
    """Client IP for rate limiting; uses X-Forwarded-For only when behind trusted proxy (see _client_ip)."""
    return _client_ip(request) or "unknown"


def audit_log(
    event_type: str,
    *,
    actor: Optional[str] = None,
    action: str,
    resource: Optional[str] = None,
    outcome: str = "success",
    ip: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    """Structured audit log: timestamp, actor, action, resource, outcome. Do not log passwords or tokens."""
    import json
    payload = {
        "event": event_type,
        "action": action,
        "outcome": outcome,
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    if actor:
        payload["actor"] = actor
    if resource:
        payload["resource"] = resource
    if ip:
        payload["ip"] = ip
    if extra:
        payload["extra"] = extra
    audit_logger.info("%s", json.dumps(payload))


# ---------------------------
# GUI authentication (JWT; users in DB)
# ---------------------------
_JWT_ALGORITHM = "HS256"
_JWT_SECRET = os.environ.get("GUI_JWT_SECRET", "").strip() or os.environ.get("SECRET_KEY", "change-me-in-production")

_DEV_DEFAULT_SECRETS = frozenset({"", "change-me-in-production", "dev", "secret", "test"})


def _is_dev_mode() -> bool:
    """Best-effort environment check for local/dev/test usage."""
    env = (
        os.environ.get("APP_ENV")
        or os.environ.get("ENVIRONMENT")
        or os.environ.get("PYTHON_ENV")
        or os.environ.get("FASTAPI_ENV")
        or ""
    ).strip().lower()
    return env in ("dev", "development", "local", "test", "testing")


def _validate_jwt_secret() -> None:
    """When DB is enabled, require a strong JWT secret; otherwise fail closed. Set ALLOW_WEAK_JWT_SECRET=true for local dev only."""
    allow_weak = os.environ.get("ALLOW_WEAK_JWT_SECRET", "").strip().lower() in ("1", "true", "yes")
    if allow_weak and not _is_dev_mode():
        raise RuntimeError(
            "ALLOW_WEAK_JWT_SECRET is enabled but environment is not marked as dev/test. "
            "Disable ALLOW_WEAK_JWT_SECRET for staging/production."
        )
    if allow_weak and _is_dev_mode():
        logger.warning("ALLOW_WEAK_JWT_SECRET enabled in dev/test mode")
        return
    raw = (os.environ.get("GUI_JWT_SECRET") or os.environ.get("SECRET_KEY") or "").strip()
    if not raw or len(raw) < 32:
        raise RuntimeError(
            "GUI_JWT_SECRET must be set and at least 32 characters when using the database. "
            "Set GUI_JWT_SECRET to a strong random value (e.g. openssl rand -hex 32). "
            "For local dev only you may set ALLOW_WEAK_JWT_SECRET=true."
        )
    if raw.lower() in _DEV_DEFAULT_SECRETS:
        raise RuntimeError(
            "GUI_JWT_SECRET must not be a known dev default when using the database. "
            "Set GUI_JWT_SECRET to a strong random value. "
            "For local dev only you may set ALLOW_WEAK_JWT_SECRET=true."
        )


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
        _validate_jwt_secret()
        try:
            async with pool.acquire() as conn:
                await screening_db.ensure_schema(conn)
                await auth_db.ensure_auth_schema(conn)
                if os.environ.get("SEED_DEFAULT_ADMIN", "").strip().lower() in ("1", "true", "yes"):
                    await auth_db.seed_default_user(conn)
                else:
                    logger.info("auth_db: default admin seeding skipped (set SEED_DEFAULT_ADMIN=true to enable)")
            logger.info("screening_db + auth_db: schema ensured")
        except Exception as e:
            import traceback
            logger.warning("schema ensure failed: %s\n%s", e, traceback.format_exc())
    yield
    await screening_db.close_pool()


app = FastAPI(
    title="Sanctions/PEP Screening API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
_RATE_LIMIT_STORAGE_URL = os.environ.get("RATE_LIMIT_STORAGE_URL", "").strip()
if _RATE_LIMIT_STORAGE_URL:
    limiter = Limiter(key_func=_rate_limit_key, storage_uri=_RATE_LIMIT_STORAGE_URL)
    logger.info("rate limiting: using shared backend")
else:
    limiter = Limiter(key_func=_rate_limit_key)
    logger.warning("rate limiting: using in-memory backend (single-process only)")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_GENERIC_ERROR_MESSAGE = "An error occurred. Please try again or contact support."


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Set baseline security headers at app level (Nginx can still override/augment)."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    proto = (request.headers.get("x-forwarded-proto") or "").lower()
    if proto == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    # Prevent token-bearing auth responses from being cached by shared intermediaries.
    if request.url.path.startswith("/auth/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Preserve status; sanitise detail so we do not leak stack traces or internal messages. Keep 422 validation detail as-is."""
    detail = exc.detail
    if exc.status_code != 422 and isinstance(detail, str) and (
        "traceback" in detail.lower()
        or "exception" in detail.lower()
        or "error:" in detail.lower()
        or len(detail) > 200
    ):
        detail = _GENERIC_ERROR_MESSAGE
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Log full exception and return generic 500 to the client."""
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": _GENERIC_ERROR_MESSAGE},
    )


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
    dob: Optional[str] = Field(None, description="Date of birth (YYYY-MM-DD, DD-MM-YYYY, or YYYY) or null")
    country: Optional[str] = Field(None, description="Country (optional, organization checks)")
    entity_type: Optional[str] = Field("Person", description="'Person' or 'Organization'")
    business_reference: constr(strip_whitespace=True, min_length=1) = Field(..., description="Business reference (required)")
    reason_for_check: Literal[
        "Client Onboarding",
        "Claim Payment",
        "Business Partner Payment",
        "Business Partner Due Diligence",
        "Periodic Re-Screen",
        "Ad-Hoc Compliance Review",
    ] = Field(..., description="Reason for check (required enum)")
    requestor: Optional[str] = Field(None, description="User performing the check (required)")
    search_backend: Optional[str] = Field("postgres_beta", description="'postgres_beta' (default) or 'original' (parquet)")
    rerun_entity_key: Optional[str] = Field(
        None,
        description="Optional: rerun using this existing entity_key so the stored record is updated in-place",
    )

class RefreshRequest(BaseModel):
    include_peps: bool = Field(
        True,
        description="Include consolidated PEPs in refreshed data"
    )
    sync_postgres: bool = Field(
        True,
        description="When true (default), rebuild watchlist_entities in PostgreSQL from refreshed CSV data"
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
    email: str = Field(..., description="User email (must be from an allowed domain); temp password is emailed via Resend")


class ApiKeyCreateRequest(BaseModel):
    name: constr(strip_whitespace=True, min_length=1) = Field(..., description="Display name for API key")
    role: Literal["screening"] = Field("screening", description="API key role")


class ApiKeyUpdateRequest(BaseModel):
    active: bool = Field(..., description="Set whether API key is active")


class FalsePositiveRequest(BaseModel):
    entity_key: str = Field(..., description="Entity key to clear as false positive")
    reason: constr(strip_whitespace=True, min_length=1) = Field(..., description="Analyst/admin reason (required)")


class ReviewOutcome(str, Enum):
    FALSE_POSITIVE_PROCEEDED = "False Positive - Proceeded"
    FALSE_POSITIVE_PAYMENT_RELEASED = "False Positive - Payment Released"
    CONFIRMED_MATCH_PAYMENT_BLOCKED = "Confirmed Match - Payment Blocked"
    CONFIRMED_MATCH_ESCALATED = "Confirmed Match - Escalated to Compliance"
    PENDING_EXTERNAL_REVIEW = "Pending External Review"
    CANCELLED_NO_ACTION = "Cancelled / No Action Required"


class ReviewCompleteRequest(BaseModel):
    review_outcome: ReviewOutcome = Field(..., description="Mandatory structured review outcome")
    review_notes: constr(strip_whitespace=True, min_length=10) = Field(..., description="Mandatory review notes (min 10 chars)")


class ReviewRerunRequest(BaseModel):
    dob: Optional[str] = Field(None, description="Date of birth for person rerun")
    country: Optional[constr(strip_whitespace=True, min_length=1)] = Field(None, description="Country for organization rerun")
    entity_type: Optional[Literal["Person", "Organization"]] = Field(None, description="Optional override for rerun entity type")


class MatchingConfigUpdateRequest(BaseModel):
    custom_generic_words: List[str] = Field(default_factory=list, description="Additional generic organization words to exclude from strong matching")


class AiTriageRunRequest(BaseModel):
    limit: int = Field(25, ge=1, le=250, description="Maximum number of outstanding sanctions or PEP matches to triage")


class AiTriageDecisionRequest(BaseModel):
    reviewer_notes: Optional[constr(strip_whitespace=True, min_length=3)] = Field(None, description="Optional reviewer note")


# Internal queue-ingestion API: request body (no screening results returned).
class InternalScreeningRequest(BaseModel):
    name: str = Field(..., description="Full name or organization to screen")
    dob: Optional[str] = Field(None, description="Date of birth (YYYY-MM-DD, DD-MM-YYYY, or YYYY) or null")
    country: Optional[str] = Field(None, description="Country (optional, organization checks)")
    entity_type: Optional[str] = Field("Person", description="'Person' or 'Organization'")
    business_reference: constr(strip_whitespace=True, min_length=1) = Field(..., description="Business reference (required)")
    reason_for_check: Literal[
        "Client Onboarding",
        "Claim Payment",
        "Business Partner Payment",
        "Business Partner Due Diligence",
        "Periodic Re-Screen",
        "Ad-Hoc Compliance Review",
    ] = Field(..., description="Reason for check (required enum)")
    requestor: Optional[str] = Field(None, description="User/system requesting the screening")


# Bulk capped at 500 per request; callers should batch responsibly. Further rate controls may be added if misuse occurs.
class InternalScreeningBulkRequest(BaseModel):
    requests: List[InternalScreeningRequest] = Field(..., max_length=500)


def _trusted_proxy_ips() -> frozenset:
    """Parse TRUSTED_PROXY_IPS (comma-separated). Only when direct client is in this set do we trust X-Forwarded-For."""
    raw = os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1,::1").strip()
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def _client_ip(request: Request) -> str:
    """
    Client IP for auth and rate limiting. Use X-Forwarded-For only when the direct
    client (request.client.host) is in TRUSTED_PROXY_IPS; otherwise a client could spoof it.
    """
    direct = (request.client.host if request.client else "") or ""
    if direct not in _trusted_proxy_ips():
        return direct
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return direct


def _internal_screening_client_ip(request: Request) -> str:
    """Client IP for internal screening allowlist; uses trusted proxy when applicable."""
    return _client_ip(request)


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


def _api_key_route_allowed(path: str) -> bool:
    """API keys may access screening routes only."""
    p = (path or "").strip()
    return p == "/opcheck" or p.startswith("/opcheck/") or p == "/mi/export.csv"


async def get_current_user(request: Request) -> dict:
    """Require valid GUI JWT, or API key for screening routes only."""
    token = _get_token_from_request(request)
    payload = _decode_token(token)
    if payload:
        payload["auth_type"] = "jwt"
        return payload

    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    async with pool.acquire() as conn:
        api_key_row = await auth_db.get_active_api_key_by_token(conn, token)
        if api_key_row is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        if not _api_key_route_allowed(request.url.path):
            audit_log(
                "auth",
                action="api_key_auth",
                actor=f"service_account:api_key:{api_key_row.get('name')}",
                resource=request.url.path,
                outcome="failure",
                ip=_client_ip(request),
                extra={"reason": "route_not_allowed", "api_key_id": str(api_key_row.get("id")), "role": api_key_row.get("role")},
            )
            raise HTTPException(status_code=403, detail="API keys may access screening routes only")

        await auth_db.touch_api_key_last_used(conn, str(api_key_row["id"]))

    context = {
        "sub": f"service_account:api_key:{api_key_row.get('name')}",
        "is_admin": False,
        "must_change_password": False,
        "auth_type": "api_key",
        "api_key_id": str(api_key_row["id"]),
        "api_key_name": api_key_row.get("name"),
        "role": api_key_row.get("role"),
    }
    audit_log(
        "auth",
        action="api_key_auth",
        actor=context["sub"],
        resource=request.url.path,
        outcome="success",
        ip=_client_ip(request),
        extra={"api_key_id": context["api_key_id"], "api_key_name": context["api_key_name"], "role": context["role"]},
    )
    return context


async def require_admin(request: Request) -> dict:
    """Require valid JWT and is_admin=True."""
    payload = await get_current_user(request)
    if not payload.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin required")
    return payload


async def require_api_key_user(request: Request) -> dict:
    """Require authentication via API key only."""
    payload = await get_current_user(request)
    if payload.get("auth_type") != "api_key":
        raise HTTPException(status_code=403, detail="API key required")
    return payload


async def require_refresh_opensanctions_auth(request: Request) -> None:
    """
    Require either (a) valid JWT with is_admin=True, or (b) REFRESH_OPENSANCTIONS_API_KEY
    via header X-Refresh-Opensanctions-Key or Authorization: Bearer <key>.
    """
    # (a) JWT admin
    auth_header = request.headers.get("authorization", "").strip()
    if auth_header.startswith("Bearer "):
        token = auth_header.replace("Bearer ", "", 1).strip()
        payload = _decode_token(token)
        if payload and payload.get("is_admin"):
            return
    # (b) API key
    refresh_key = os.environ.get("REFRESH_OPENSANCTIONS_API_KEY", "").strip()
    if refresh_key:
        provided = request.headers.get("x-refresh-opensanctions-key", "").strip()
        if not provided and auth_header.startswith("Bearer "):
            provided = auth_header.replace("Bearer ", "", 1).strip()
        if provided == refresh_key:
            return
    raise HTTPException(
        status_code=401,
        detail="Refresh requires admin login or X-Refresh-Opensanctions-Key / REFRESH_OPENSANCTIONS_API_KEY",
    )


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
@limiter.limit("5/minute")
async def auth_login(request: Request, data: LoginRequest):
    """
    GUI login: email + password. Returns JWT. Users are stored in DB (seed user: see auth_db).
    Requires DATABASE_URL.
    """
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Login unavailable (configure DATABASE_URL)")
    email = data.username.strip().lower()
    client_ip = _client_ip(request)
    async with pool.acquire() as conn:
        remaining = await auth_db.get_login_backoff_remaining_seconds(conn, email)
        if remaining > 0:
            audit_log(
                "auth",
                action="login",
                actor=email,
                outcome="failure",
                ip=client_ip,
                extra={"reason": "account_backoff", "retry_after_seconds": remaining},
            )
            return JSONResponse(
                status_code=429,
                content={"detail": f"Too many failed login attempts. Try again in {remaining} seconds."},
                headers={"Retry-After": str(remaining)},
            )
        user = await auth_db.verify_user(conn, email, data.password)
        await auth_db.record_login_attempt(conn, email, success=(user is not None), client_ip=client_ip)
    if user is None:
        audit_log("auth", action="login", actor=email, outcome="failure", ip=client_ip)
        raise HTTPException(status_code=401, detail="Invalid email or password")
    audit_log("auth", action="login", actor=email, outcome="success", ip=client_ip)
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
async def auth_change_password(request: Request, data: ChangePasswordRequest, payload: dict = Depends(get_current_user)):
    """
    Change password (e.g. after first logon). Requires current password. Returns new JWT.
    """
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Unavailable")
    email = payload["sub"]
    client_ip = _client_ip(request)
    async with pool.acquire() as conn:
        user = await auth_db.get_user_by_email(conn, email)
        if user is None:
            audit_log("auth", action="change_password", actor=email, outcome="failure", ip=client_ip, extra={"reason": "user_not_found"})
            raise HTTPException(status_code=401, detail="User not found")
        if not auth_db.verify_password(data.current_password, user["password_hash"]):
            audit_log("auth", action="change_password", actor=email, outcome="failure", ip=client_ip, extra={"reason": "wrong_password"})
            raise HTTPException(status_code=401, detail="Current password is incorrect")
        err = _validate_signup_password(data.new_password)
        if err:
            raise HTTPException(status_code=400, detail=err)
        await auth_db.update_password(conn, str(user["id"]), data.new_password)
    audit_log("auth", action="change_password", actor=email, outcome="success", ip=client_ip)
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
# outlook.com = temporary for testing email delivery; remove before production
ALLOWED_SIGNUP_DOMAINS = frozenset({
    "legalprotectiongroup.co.uk",
    "devonbaysolutions.co.uk",
    "devonbayadjusting.co.uk",
    "devonbayinsurance.ai",
    "outlook.com",
})

# Resend: env RESEND_API_KEY (required for signup), RESEND_FROM e.g. "Sanctions Screening <noreply@yourdomain.com>"
def _send_temp_password_email(to_email: str, temp_password: str) -> None:
    """Send temporary password via Resend. Raises on failure."""
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    from_addr = os.environ.get("RESEND_FROM", "").strip() or "Sanctions Screening <onboarding@resend.dev>"
    if not api_key:
        raise ValueError("RESEND_API_KEY is not configured")
    import resend
    resend.api_key = api_key
    subject = "Your temporary password for Sanctions Screening"
    body = f"""You requested access to Sanctions Screening. Use this temporary password to sign in (you will be asked to set a new password):

{temp_password}

Sign in at your usual screening URL with your email and this password, then change your password when prompted.
"""
    params = {
        "from": from_addr,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    resend.Emails.send(params)


def _generate_temp_password() -> str:
    """Generate a random password that satisfies our strength rules (for first-login change)."""
    part = secrets.token_urlsafe(10)
    return part + "aA1!"  # ensure lower, upper, digit, special

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
@limiter.limit("3/minute")
async def auth_signup(request: Request, data: SignupRequest):
    """Request access: whitelist email only. A temporary password is emailed via Resend; user must sign in and change it."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Signup unavailable (configure DATABASE_URL)")
    if not os.environ.get("RESEND_API_KEY", "").strip():
        raise HTTPException(status_code=503, detail="Signup unavailable (email not configured)")
    email = data.email.strip().lower()
    client_ip = _client_ip(request)
    if not email:
        raise HTTPException(status_code=400, detail="Email required")
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    domain = email.split("@")[-1]
    if domain not in ALLOWED_SIGNUP_DOMAINS:
        audit_log("auth", action="signup", actor=email, outcome="failure", ip=client_ip, extra={"reason": "domain_not_allowed"})
        raise HTTPException(
            status_code=400,
            detail="Signup is only available for approved company email domains.",
        )
    temp_password = _generate_temp_password()
    try:
        async with pool.acquire() as conn:
            user = await auth_db.create_user(
                conn,
                email,
                temp_password,
                must_change_password=True,
                is_admin=False,
            )
        import asyncio
        await asyncio.to_thread(_send_temp_password_email, email, temp_password)
        audit_log("auth", action="signup", actor=email, outcome="success", ip=client_ip)
    except Exception as e:
        err = str(e).lower()
        if "unique" in err or "duplicate" in err or "exists" in err:
            audit_log("auth", action="signup", actor=email, outcome="failure", ip=client_ip, extra={"reason": "already_exists"})
            raise HTTPException(status_code=400, detail="An account with this email already exists")
        if "resend" in err or "RESEND" in str(e):
            audit_log("auth", action="signup", actor=email, outcome="failure", ip=client_ip, extra={"reason": "email_send_failed"})
            raise HTTPException(status_code=502, detail="Failed to send email. Please try again or contact support.")
        logger.exception("Signup failed: %s", e)
        audit_log("auth", action="signup", actor=email, outcome="failure", ip=client_ip)
        raise HTTPException(status_code=400, detail=_GENERIC_ERROR_MESSAGE)
    return {
        "message": "Check your email for a temporary password. Sign in with your email and that password; you will then be asked to set a new password.",
    }


@app.get("/auth/me")
async def auth_me(payload: dict = Depends(get_current_user)):
    """Return current user from JWT."""
    return {"username": payload["sub"], "email": payload["sub"], "must_change_password": payload.get("must_change_password", False), "is_admin": payload.get("is_admin", False)}


@app.get("/auth/users", dependencies=[Depends(require_admin)])
async def auth_list_users(request: Request, payload: dict = Depends(require_admin)):
    """List all users (admin only). Requires DATABASE_URL."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Unavailable")
    async with pool.acquire() as conn:
        users = await auth_db.list_users(conn)
    audit_log("admin", action="users_list", actor=payload.get("sub"), outcome="success", ip=_client_ip(request))
    return {"users": users}


@app.post("/auth/users", dependencies=[Depends(require_admin)])
async def auth_create_user(request: Request, data: CreateUserRequest, payload: dict = Depends(require_admin)):
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
        audit_log("admin", action="user_create", actor=payload.get("sub"), resource=email, outcome="success", ip=_client_ip(request))
        return user
    except Exception as e:
        err = str(e).lower()
        if "unique" in err or "duplicate" in err or "exists" in err:
            audit_log("admin", action="user_create", actor=payload.get("sub"), resource=email, outcome="failure", ip=_client_ip(request), extra={"reason": "already_exists"})
            raise HTTPException(status_code=400, detail="A user with this email already exists")
        logger.exception("Create user failed: %s", e)
        audit_log("admin", action="user_create", actor=payload.get("sub"), resource=email, outcome="failure", ip=_client_ip(request))
        raise HTTPException(status_code=400, detail=_GENERIC_ERROR_MESSAGE)


@app.patch("/auth/users/{user_id}", dependencies=[Depends(require_admin)])
async def auth_update_user(request: Request, user_id: str, body: UpdateUserRequest, payload: dict = Depends(require_admin)):
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
        audit_log("admin", action="user_update", actor=payload.get("sub"), resource=user_id, outcome="success", ip=_client_ip(request))
        return {"status": "ok"}
    except Exception as e:
        logger.exception("Update user failed: %s", e)
        audit_log("admin", action="user_update", actor=payload.get("sub"), resource=user_id, outcome="failure", ip=_client_ip(request))
        raise HTTPException(status_code=400, detail=_GENERIC_ERROR_MESSAGE)


@app.post("/auth/users/import", dependencies=[Depends(require_admin)])
async def auth_import_users(request: Request, body: ImportUsersRequest, payload: dict = Depends(require_admin)):
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
                    logger.exception("Import user %s failed: %s", email, e)
                    errors.append({"email": email, "error": "Create failed"})
    audit_log("admin", action="user_import", actor=payload.get("sub"), outcome="success", ip=_client_ip(request), extra={"created": created, "skipped": skipped, "errors_count": len(errors)})
    return {"created": created, "skipped": skipped, "errors": errors}


@app.get("/auth/api-keys", dependencies=[Depends(require_admin)])
async def auth_list_api_keys(request: Request, payload: dict = Depends(require_admin)):
    """List API keys (admin only)."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Unavailable")
    async with pool.acquire() as conn:
        items = await auth_db.list_api_keys(conn)
    audit_log("admin", action="api_keys_list", actor=payload.get("sub"), outcome="success", ip=_client_ip(request), extra={"count": len(items)})
    return {"items": items}


@app.post("/auth/api-keys", dependencies=[Depends(require_admin)])
async def auth_create_api_key(request: Request, body: ApiKeyCreateRequest, payload: dict = Depends(require_admin)):
    """Create API key (admin only). Returns plaintext key once."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Unavailable")
    async with pool.acquire() as conn:
        created = await auth_db.create_api_key(conn, name=body.name, role=body.role)
    audit_log(
        "admin",
        action="api_key_create",
        actor=payload.get("sub"),
        resource=created.get("id"),
        outcome="success",
        ip=_client_ip(request),
        extra={"name": created.get("name"), "role": created.get("role")},
    )
    return created


@app.patch("/auth/api-keys/{api_key_id}", dependencies=[Depends(require_admin)])
async def auth_update_api_key(
    request: Request,
    api_key_id: str,
    body: ApiKeyUpdateRequest,
    payload: dict = Depends(require_admin),
):
    """Activate/deactivate API key (admin only)."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Unavailable")
    async with pool.acquire() as conn:
        updated = await auth_db.set_api_key_active(conn, api_key_id, active=body.active)
    if not updated:
        raise HTTPException(status_code=404, detail="API key not found")
    audit_log(
        "admin",
        action="api_key_update",
        actor=payload.get("sub"),
        resource=api_key_id,
        outcome="success",
        ip=_client_ip(request),
        extra={"active": body.active},
    )
    return {"status": "ok", "id": api_key_id, "active": body.active}


@app.delete("/auth/api-keys/{api_key_id}", dependencies=[Depends(require_admin)])
async def auth_delete_api_key(
    request: Request,
    api_key_id: str,
    payload: dict = Depends(require_admin),
):
    """Delete API key (admin only)."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Unavailable")
    async with pool.acquire() as conn:
        deleted = await auth_db.delete_api_key(conn, api_key_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="API key not found")
    audit_log(
        "admin",
        action="api_key_delete",
        actor=payload.get("sub"),
        resource=api_key_id,
        outcome="success",
        ip=_client_ip(request),
    )
    return {"status": "ok", "id": api_key_id}


@app.post("/admin/testing/clear-screening-data", dependencies=[Depends(require_admin)])
async def admin_clear_screening_data(request: Request, payload: dict = Depends(require_admin)):
    """
    Testing utility (admin only): clear screening cache + job queue data.
    Does NOT touch users/auth tables.
    """
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Unavailable")
    async with pool.acquire() as conn:
        entities_before = await conn.fetchval("SELECT COUNT(*)::int FROM screened_entities")
        jobs_before = await conn.fetchval("SELECT COUNT(*)::int FROM screening_jobs")
        await conn.execute("TRUNCATE TABLE screening_jobs, screened_entities")
    audit_log(
        "admin",
        action="clear_screening_data",
        actor=payload.get("sub"),
        outcome="success",
        ip=_client_ip(request),
        extra={"screened_entities_removed": int(entities_before or 0), "screening_jobs_removed": int(jobs_before or 0)},
    )
    return {
        "status": "ok",
        "screened_entities_removed": int(entities_before or 0),
        "screening_jobs_removed": int(jobs_before or 0),
    }


@app.post("/admin/screening/jobs/bulk", dependencies=[Depends(require_admin)])
@limiter.limit("10/minute")
async def admin_screening_jobs_bulk(request: Request, body: InternalScreeningBulkRequest, payload: dict = Depends(require_admin)):
    """
    Admin bulk enqueue endpoint for web UI CSV uploads.
    Reuses internal queue logic; returns per-item statuses: reused | already_pending | queued.
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
    counts = {"reused": 0, "already_pending": 0, "queued": 0, "error": 0}
    for r in results:
        s = r.get("status")
        if s in counts:
            counts[s] += 1
    queued_job_ids = [str(r.get("job_id")) for r in results if r.get("status") == "queued" and r.get("job_id")]
    audit_log(
        "admin",
        action="bulk_screening_enqueue",
        actor=payload.get("sub"),
        outcome="ENQUEUED",
        ip=_client_ip(request),
        extra={
            "total": len(results),
            **counts,
            "job_id": queued_job_ids[0] if queued_job_ids else None,
            "job_ids_queued": queued_job_ids,
            "business_reference": sorted({(r.business_reference or "").strip() for r in body.requests if (r.business_reference or "").strip()}),
            "reason_for_check": sorted({str(r.reason_for_check) for r in body.requests if r.reason_for_check}),
        },
    )
    return {"results": results, "counts": counts}


@app.get("/admin/screening/jobs", dependencies=[Depends(require_admin)])
@limiter.limit("60/minute")
async def admin_list_screening_jobs(
    request: Request,
    payload: dict = Depends(require_admin),
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    """Admin queue monitor: list screening jobs with status and timestamps."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Screening queue requires DATABASE_URL")
    status_norm = (status or "").strip().lower() or None
    async with pool.acquire() as conn:
        items = await screening_db.list_screening_jobs(
            conn,
            status=status_norm,
            limit=limit,
            offset=offset,
        )
    audit_log(
        "admin",
        action="screening_jobs_list",
        actor=payload.get("sub"),
        outcome="success",
        ip=_client_ip(request),
        extra={"count": len(items), "status": status_norm or "all"},
    )
    return {"items": items}


@app.post("/admin/screening/false-positive", dependencies=[Depends(require_admin)])
@limiter.limit("30/minute")
async def admin_mark_false_positive(
    request: Request,
    body: FalsePositiveRequest,
    payload: dict = Depends(require_admin),
):
    """Admin action: clear a stored screening result as false positive (manual override)."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Search unavailable (configure DATABASE_URL)")
    entity_key = (body.entity_key or "").strip()
    if not entity_key:
        raise HTTPException(status_code=400, detail="entity_key is required")
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason is required")
    async with pool.acquire() as conn:
        result = await screening_db.mark_false_positive(
            conn,
            entity_key=entity_key,
            actor=payload.get("sub") or "admin",
            reason=reason,
        )
    if result is None:
        raise HTTPException(status_code=404, detail="Screening record not found")
    audit_log(
        "admin",
        action="mark_false_positive",
        actor=payload.get("sub"),
        resource=entity_key,
        outcome="success",
        ip=_client_ip(request),
        extra={"reason": reason},
    )
    return {
        "status": "ok",
        "entity_key": entity_key,
        "result": result,
    }


@app.get("/admin/screening/rescreen-summary", dependencies=[Depends(require_admin)])
@limiter.limit("60/minute")
async def admin_rescreen_summary(
    request: Request,
    payload: dict = Depends(require_admin),
    limit: int = 14,
):
    """Admin view: recent UK list refresh runs and latest transition summary."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Unavailable")
    async with pool.acquire() as conn:
        summary = await screening_db.get_refresh_run_summary(conn, limit=limit)
    audit_log(
        "admin",
        action="rescreen_summary",
        actor=payload.get("sub"),
        outcome="success",
        ip=_client_ip(request),
        extra={"limit": max(1, min(90, limit))},
    )
    return summary


@app.get("/admin/openapi.json", dependencies=[Depends(require_admin)])
@limiter.limit("30/minute")
async def admin_openapi_schema(request: Request, payload: dict = Depends(require_admin)):
    """Return OpenAPI schema for authenticated admin users."""
    audit_log(
        "admin",
        action="openapi_schema",
        actor=payload.get("sub"),
        outcome="success",
        ip=_client_ip(request),
    )
    return app.openapi()


@app.get("/admin/matching-config", dependencies=[Depends(require_admin)])
@limiter.limit("30/minute")
async def admin_get_matching_config(request: Request, payload: dict = Depends(require_admin)):
    cfg = get_matching_config()
    custom_generic_words = sorted(cfg.get("custom_generic_words") or [])
    effective_generic_words = sorted(get_effective_org_generic_tokens())
    default_generic_words = sorted(set(effective_generic_words) - set(custom_generic_words))
    audit_log(
        "admin",
        action="matching_config_view",
        actor=payload.get("sub"),
        outcome="success",
        ip=_client_ip(request),
        extra={"custom_word_count": len(custom_generic_words)},
    )
    return {
        "protected_legal_suffixes": sorted(get_protected_org_legal_suffixes()),
        "default_generic_words": default_generic_words,
        "custom_generic_words": custom_generic_words,
        "effective_generic_words": effective_generic_words,
    }


@app.put("/admin/matching-config", dependencies=[Depends(require_admin)])
@limiter.limit("10/minute")
async def admin_update_matching_config(
    request: Request,
    body: MatchingConfigUpdateRequest,
    payload: dict = Depends(require_admin),
):
    saved = save_matching_config(custom_generic_words=body.custom_generic_words)
    custom_generic_words = sorted(saved.get("custom_generic_words") or [])
    effective_generic_words = sorted(get_effective_org_generic_tokens())
    default_generic_words = sorted(set(effective_generic_words) - set(custom_generic_words))
    audit_log(
        "admin",
        action="matching_config_update",
        actor=payload.get("sub"),
        outcome="success",
        ip=_client_ip(request),
        extra={"custom_word_count": len(custom_generic_words)},
    )
    return {
        "status": "ok",
        "protected_legal_suffixes": sorted(get_protected_org_legal_suffixes()),
        "default_generic_words": default_generic_words,
        "custom_generic_words": custom_generic_words,
        "effective_generic_words": effective_generic_words,
    }


@app.get("/admin/ai-triage/health", dependencies=[Depends(require_admin)])
@limiter.limit("30/minute")
async def admin_ai_triage_health(request: Request, payload: dict = Depends(require_admin)):
    health = ollama_health()
    cfg = get_local_llm_config()
    audit_log(
        "admin",
        action="ai_triage_health_view",
        actor=payload.get("sub"),
        outcome="success",
        ip=_client_ip(request),
    )
    return {
        **health,
        "timeout_seconds": cfg["timeout_seconds"],
        "max_concurrency": cfg["max_concurrency"],
    }


@app.get("/admin/ai-triage/runs", dependencies=[Depends(require_admin)])
@limiter.limit("30/minute")
async def admin_ai_triage_runs(
    request: Request,
    payload: dict = Depends(require_admin),
    limit: int = 20,
):
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="AI triage unavailable (configure DATABASE_URL)")
    async with pool.acquire() as conn:
        items = await screening_db.list_ai_triage_runs(conn, limit=limit)
    audit_log(
        "admin",
        action="ai_triage_runs_view",
        actor=payload.get("sub"),
        outcome="success",
        ip=_client_ip(request),
        extra={"count": len(items)},
    )
    return {"items": items}


@app.post("/admin/ai-triage/run", dependencies=[Depends(require_admin)])
@limiter.limit("10/minute")
async def admin_run_ai_triage(
    request: Request,
    body: AiTriageRunRequest,
    payload: dict = Depends(require_admin),
):
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="AI triage unavailable (configure DATABASE_URL)")
    actor = str(payload.get("sub") or "").strip() or "unknown_user"
    async with pool.acquire() as conn:
        result = await run_ai_triage_batch(
            conn,
            screening_db_module=screening_db,
            trigger_type="manual",
            triggered_by=actor,
            limit=body.limit,
        )
    audit_log(
        "admin",
        action="ai_triage_run",
        actor=actor,
        outcome="success",
        ip=_client_ip(request),
        extra=result,
    )
    return result


@app.options("/opcheck")
@app.options("/opcheck/dataverse")
@app.options("/opcheck/screened")
@app.options("/dashboard/summary")
@app.options("/mi/export.csv")
@app.options("/review/queue")
@app.options("/review/{entity_key}/claim")
@app.options("/review/{entity_key}/complete")
@app.options("/review/{entity_key}/rerun")
@app.options("/refresh_opensanctions")
@app.options("/auth/config")
@app.options("/auth/login")
@app.options("/auth/me")
@app.options("/auth/change-password")
@app.options("/auth/signup")
@app.options("/auth/users")
@app.options("/auth/users/import")
@app.options("/auth/users/{user_id}")
@app.options("/auth/api-keys")
@app.options("/auth/api-keys/{api_key_id}")
@app.options("/admin/testing/clear-screening-data")
@app.options("/admin/screening/jobs/bulk")
@app.options("/admin/matching-config")
@app.options("/admin/ai-triage/health")
@app.options("/admin/ai-triage/runs")
@app.options("/admin/ai-triage/run")
@app.options("/admin/screening/jobs")
@app.options("/admin/screening/false-positive")
@app.options("/admin/screening/rescreen-summary")
@app.options("/admin/openapi.json")
@app.options("/ai-triage/tasks")
@app.options("/ai-triage/tasks/{triage_id}")
@app.options("/ai-triage/tasks/{triage_id}/approve")
@app.options("/ai-triage/tasks/{triage_id}/reject")
@app.options("/internal/screening/jobs")
@app.options("/internal/screening/jobs/bulk")
async def cors_preflight():
    """Ensure CORS preflight (OPTIONS) returns 200 so browsers allow the request."""
    return {}

def _run_check_sync(data: OpCheckRequest):
    """Run screening synchronously (used when DB is disabled)."""
    name = data.name.strip()
    dob = (data.dob.strip() if isinstance(data.dob, str) else data.dob)
    country = (data.country.strip() if isinstance(data.country, str) else data.country)
    entity_type_norm = (data.entity_type or "Person").strip().lower()
    pep_enabled = entity_type_norm == "person" and not _looks_like_company_name(name)
    pep_skip_reason = None
    if not pep_enabled:
        pep_skip_reason = "entity_type_organization" if entity_type_norm == "organization" else "company_name_detected"
    person_result = perform_opensanctions_check(
        name=name,
        dob=dob,
        country=None,
        entity_type="Person",
        requestor=data.requestor.strip(),
        log_search=False,
        include_peps=pep_enabled,
    )
    organization_result = perform_opensanctions_check(
        name=name,
        dob=None,
        country=country,
        entity_type="Organization",
        requestor=data.requestor.strip(),
        log_search=False,
        include_peps=False,
    )
    merged = _merge_dual_type_results(
        person_result,
        organization_result,
        name=name,
        submitted_entity_type=entity_type,
        pep_checked=pep_enabled,
        pep_skip_reason=pep_skip_reason,
    )
    summary = merged.get("Check Summary") if isinstance(merged.get("Check Summary"), dict) else None
    if summary:
        from utils import _append_search_to_csv
        _append_search_to_csv(name, summary)
    return merged


def _opcheck_queue_threshold() -> int:
    """Configurable queue pressure threshold; above this we enqueue instead of running sync. Default 5."""
    try:
        return max(0, int(os.environ.get("OPCHECK_QUEUE_THRESHOLD", "5")))
    except (ValueError, TypeError):
        return 5


def _attach_entity_id(result: dict, entity_key: str) -> dict:
    """
    Dataverse-friendly response shape:
    - entity_key keeps current API behavior.
    - entity_id is an alias for downstream systems expecting an explicit ID field.
    """
    out = dict(result or {})
    out["entity_key"] = entity_key
    out["entity_id"] = entity_key
    return out


def _result_priority(result: dict) -> int:
    if bool(result.get("Is Sanctioned")):
        return 3
    if bool(result.get("Is PEP")):
        return 2
    return 1


def _status_for_type_check(result: dict) -> str:
    summary = result.get("Check Summary") if isinstance(result.get("Check Summary"), dict) else {}
    status = str(summary.get("Status") or "").strip()
    if status:
        return status
    if bool(result.get("Is Sanctioned")):
        return "Fail Sanction"
    if bool(result.get("Is PEP")):
        return "Fail PEP"
    return "Cleared"


def _looks_like_company_name(name: str) -> bool:
    return bool(detect_company_likeness(name).get("looks_like_company"))


def _merge_dual_type_results(
    person_result: dict,
    organization_result: dict,
    *,
    name: str,
    submitted_entity_type: str,
    pep_checked: bool,
    pep_skip_reason: Optional[str] = None,
) -> dict:
    primary = person_result
    if _result_priority(organization_result) > _result_priority(person_result):
        primary = organization_result
    elif _result_priority(organization_result) == _result_priority(person_result):
        if float(organization_result.get("Score") or 0) > float(person_result.get("Score") or 0):
            primary = organization_result

    person_status = _status_for_type_check(person_result)
    org_status = _status_for_type_check(organization_result)
    merged = dict(primary)
    merged["Entity Type Checks"] = {
        "Person": {
            "status": person_status,
            "is_match": not person_status.lower().startswith("cleared"),
            "score": float(person_result.get("Score") or 0),
        },
        "Organization": {
            "status": org_status,
            "is_match": not org_status.lower().startswith("cleared"),
            "score": float(organization_result.get("Score") or 0),
        },
    }
    merged["PEP Check"] = {
        "checked": bool(pep_checked),
        "status": "checked" if pep_checked else "skipped",
        "reason": pep_skip_reason,
        "message": (
            (
                "PEP screening skipped for Organization checks."
                if pep_skip_reason == "entity_type_organization"
                else "PEP screening skipped because the query appears to be a company name."
            )
            if not pep_checked
            else "PEP screening executed."
        ),
    }
    merged["Input Classification"] = build_input_classification(
        name=name,
        submitted_as=submitted_entity_type,
        person_result=person_result,
        organization_result=organization_result,
        pep_checked=pep_checked,
    )
    return merged


async def _run_postgres_dual_check(
    conn,
    *,
    name: str,
    dob: Optional[str],
    country: Optional[str],
    entity_type: str,
    requestor: Optional[str],
) -> dict:
    entity_type_norm = (entity_type or "Person").strip().lower()
    pep_enabled = entity_type_norm == "person" and not _looks_like_company_name(name)
    pep_skip_reason = None
    if not pep_enabled:
        pep_skip_reason = "entity_type_organization" if entity_type_norm == "organization" else "company_name_detected"
    person_result = await perform_postgres_watchlist_check(
        conn,
        name=name,
        dob=dob,
        country=None,
        entity_type="Person",
        requestor=requestor,
        log_search=False,
        include_peps=pep_enabled,
    )
    organization_result = await perform_postgres_watchlist_check(
        conn,
        name=name,
        dob=None,
        country=country,
        entity_type="Organization",
        requestor=requestor,
        log_search=False,
        include_peps=False,
    )
    merged = _merge_dual_type_results(
        person_result,
        organization_result,
        name=name,
        submitted_entity_type=entity_type,
        pep_checked=pep_enabled,
        pep_skip_reason=pep_skip_reason,
    )
    summary = merged.get("Check Summary") if isinstance(merged.get("Check Summary"), dict) else None
    if summary:
        from utils import _append_search_to_csv
        _append_search_to_csv(name, summary)
    return merged


async def _check_opensanctions_impl(data: OpCheckRequest):
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
    country = (data.country.strip() if isinstance(data.country, str) else data.country) or None
    entity_type = (data.entity_type or "Person")
    entity_type_norm = entity_type.strip().lower()
    requestor = data.requestor.strip()
    business_reference = data.business_reference.strip()
    reason_for_check = data.reason_for_check
    search_backend = (data.search_backend or "postgres_beta").strip().lower()
    rerun_entity_key = (data.rerun_entity_key or "").strip() or None
    if search_backend not in ("original", "postgres_beta"):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_search_backend", "message": "search_backend must be 'original' or 'postgres_beta'."},
        )
    if rerun_entity_key:
        if entity_type_norm not in ("person", "organization"):
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_rerun_entity_type", "message": "rerun_entity_key is only supported for Person or Organization checks."},
            )
        if entity_type_norm == "person" and not dob:
            return JSONResponse(
                status_code=400,
                content={"error": "missing_dob_for_rerun", "message": "Date of birth is required when rerun_entity_key is provided."},
            )
        if entity_type_norm == "organization" and not country:
            return JSONResponse(
                status_code=400,
                content={"error": "missing_country_for_rerun", "message": "Country is required when rerun_entity_key is provided for Organization checks."},
            )

    pool = await screening_db.get_pool()
    if rerun_entity_key:
        if pool is None:
            return JSONResponse(
                status_code=503,
                content={"error": "rerun_requires_database", "message": "Re-run with preserved entity key requires DATABASE_URL."},
            )
        async with pool.acquire() as conn:
            existing = await screening_db.get_screened_entity_identity(conn, rerun_entity_key)
        if existing is None:
            return JSONResponse(
                status_code=404,
                content={"error": "rerun_entity_not_found", "message": "rerun_entity_key was not found in screened records."},
            )
        if (existing.get("normalized_name") or "") != _normalize_text(name):
            return JSONResponse(
                status_code=400,
                content={"error": "rerun_name_mismatch", "message": "Name does not match the existing rerun_entity_key record."},
            )

    if search_backend == "postgres_beta":
        if pool is None:
            return JSONResponse(
                status_code=503,
                content={"error": "backend_unavailable", "message": "postgres_beta requires DATABASE_URL and watchlist tables."},
            )
        key_variants = derive_entity_key_variants(display_name=name, entity_type=entity_type, dob=dob)
        entity_key_candidates = [f"{k}-pgb" for k in key_variants]
        if rerun_entity_key:
            entity_key_candidates = [rerun_entity_key]
        entity_key = entity_key_candidates[0]
        async with pool.acquire() as conn:
            if not rerun_entity_key:
                cached = None
                cached_key = entity_key
                for key_candidate in entity_key_candidates:
                    cached = await screening_db.get_valid_screening(conn, key_candidate)
                    if cached is not None:
                        cached_key = key_candidate
                        break
                if cached is not None:
                    try:
                        await screening_db.update_cached_screening_metadata(
                            conn,
                            entity_key=cached_key,
                            requestor=requestor,
                            business_reference=business_reference,
                            reason_for_check=reason_for_check,
                            country_input=country,
                        )
                    except Exception as e:
                        logger.warning("cached metadata update failed entity_key=%s: %s", cached_key[:16], e)
                    logger.info("postgres_beta screening reused (valid) entity_key=%s", cached_key[:16])
                    return _attach_entity_id(cached, cached_key)

            results = await _run_postgres_dual_check(
                conn,
                name=name,
                dob=dob,
                country=country,
                entity_type=entity_type,
                requestor=requestor,
            )
            # Persist beta checks so Search database can find them by returned entity_key.
            try:
                latest_meta = await screening_db.get_latest_uk_hash(conn)
                await screening_db.upsert_screening(
                    conn,
                    entity_key=entity_key,
                    display_name=name,
                    normalized_name=_normalize_text(name),
                    date_of_birth=dob,
                    country_input=country,
                    entity_type=entity_type,
                    requestor=requestor,
                    business_reference=business_reference,
                    reason_for_check=reason_for_check,
                    result=results,
                    screened_against_uk_hash=latest_meta.get("uk_hash"),
                    screened_against_refresh_run_id=latest_meta.get("refresh_run_id"),
                )
            except Exception as e:
                logger.warning("postgres_beta upsert failed entity_key=%s: %s", entity_key[:16], e)
        return _attach_entity_id(results, entity_key)

    entity_key_candidates = derive_entity_key_variants(display_name=name, entity_type=entity_type, dob=dob)
    if rerun_entity_key:
        entity_key_candidates = [rerun_entity_key]
    entity_key = entity_key_candidates[0]
    if pool is None:
        # No DB: run check synchronously
        return _attach_entity_id(_run_check_sync(data), entity_key)

    async with pool.acquire() as conn:
        if not rerun_entity_key:
            cached = None
            cached_key = entity_key
            for key_candidate in entity_key_candidates:
                cached = await screening_db.get_valid_screening(conn, key_candidate)
                if cached is not None:
                    cached_key = key_candidate
                    break
            if cached is not None:
                try:
                    await screening_db.update_cached_screening_metadata(
                        conn,
                        entity_key=cached_key,
                        requestor=requestor,
                        business_reference=business_reference,
                        reason_for_check=reason_for_check,
                        country_input=country,
                    )
                except Exception as e:
                    logger.warning("cached metadata update failed entity_key=%s: %s", cached_key[:16], e)
                # Reuse always first, regardless of load
                logger.info("screening reused (valid) entity_key=%s", cached_key[:16])
                return _attach_entity_id(cached, cached_key)

        # Queue pressure check: under threshold => sync; at or over => enqueue (graceful load protection)
        count = await screening_db.get_pending_running_count(conn)
        threshold = _opcheck_queue_threshold()
        if not rerun_entity_key and count >= threshold:
            job_id = await screening_db.enqueue_job(
                conn, entity_key=entity_key, name=name, date_of_birth=dob,
                country=country, entity_type=entity_type, requestor=requestor, business_reference=business_reference, reason_for_check=reason_for_check,
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
                    "entity_key": entity_key,
                    "entity_id": entity_key,
                    "message": "Screening queued (load protection). Poll GET /opcheck/jobs/{job_id} for outcome.",
                },
                headers={"Location": f"/opcheck/jobs/{job_id}"},
            )

    # Under threshold: run screening synchronously, then upsert
    logger.info("synchronous screening chosen entity_key=%s queue_depth=%s threshold=%s", entity_key[:16], count, threshold)
    pep_enabled = entity_type_norm == "person" and not _looks_like_company_name(name)
    pep_skip_reason = None
    if not pep_enabled:
        pep_skip_reason = "entity_type_organization" if entity_type_norm == "organization" else "company_name_detected"
    person_result = perform_opensanctions_check(
        name=name, dob=dob, country=None, entity_type="Person", requestor=requestor, log_search=False, include_peps=pep_enabled,
    )
    organization_result = perform_opensanctions_check(
        name=name, dob=None, country=country, entity_type="Organization", requestor=requestor, log_search=False, include_peps=False,
    )
    results = _merge_dual_type_results(
        person_result,
        organization_result,
        name=name,
        submitted_entity_type=entity_type,
        pep_checked=pep_enabled,
        pep_skip_reason=pep_skip_reason,
    )
    summary = results.get("Check Summary") if isinstance(results.get("Check Summary"), dict) else None
    if summary:
        from utils import _append_search_to_csv
        _append_search_to_csv(name, summary)
    async with pool.acquire() as conn:
        latest_meta = await screening_db.get_latest_uk_hash(conn)
        await screening_db.upsert_screening(
            conn, entity_key=entity_key, display_name=name, normalized_name=_normalize_text(name),
            date_of_birth=dob, country_input=country, entity_type=entity_type, requestor=requestor, business_reference=business_reference, reason_for_check=reason_for_check, result=results,
            screened_against_uk_hash=latest_meta.get("uk_hash"),
            screened_against_refresh_run_id=latest_meta.get("refresh_run_id"),
        )
    return _attach_entity_id(results, entity_key)


async def _auto_complete_review_after_rerun_if_cleared(
    *,
    entity_key: str,
    actor: str,
    entity_type: str,
    ip: Optional[str],
) -> bool:
    """
    Apply the same auto-complete behavior used by review reruns when a re-screen clears.
    This keeps review notes/status consistent even when reruns are triggered from other screens.
    """
    key = (entity_key or "").strip()
    if not key:
        return False
    pool = await screening_db.get_pool()
    if pool is None:
        return False

    async with pool.acquire() as conn:
        rows = await screening_db.search_screened_entities(conn, entity_key=key, limit=1, offset=0)
        if not rows:
            return False
        row = rows[0]
        completed = await screening_db.complete_review(
            conn,
            entity_key=key,
            completed_by=(actor or "unknown_user"),
            review_outcome=ReviewOutcome.FALSE_POSITIVE_PROCEEDED.value,
            review_notes=f"Auto-resolved after re-run with additional {('DOB' if (entity_type or '').strip().lower() == 'person' else 'country')} information.",
        )
    if completed.get("status") != "ok":
        return False

    audit_log(
        "review",
        action="REVIEW_COMPLETED",
        actor=(actor or "unknown_user"),
        resource=key,
        outcome="success",
        ip=ip,
        extra={
            "entity_key": key,
            "business_reference": row.get("business_reference"),
            "reason_for_check": row.get("reason_for_check"),
            "review_outcome": ReviewOutcome.FALSE_POSITIVE_PROCEEDED.value,
            "auto_completed": True,
            "source": "opcheck_rerun",
        },
    )
    return True


@app.post("/opcheck")
@limiter.limit("60/minute")
async def check_opensanctions(request: Request, data: OpCheckRequest, payload: dict = Depends(get_current_user)):
    """
    Screen an entity. With DB: 200 = result (reused or completed synchronously); 202 = queued due to load.
    Reuse always first. When no cache: if queue pressure is below threshold, run sync (200); else enqueue (202).
    """
    actor = payload.get("sub")
    audit_log(
        "screening",
        action="screening_attempted",
        actor=actor,
        resource="opcheck",
        outcome="attempted",
        ip=_client_ip(request),
        extra={"business_reference": data.business_reference, "reason_for_check": data.reason_for_check},
    )
    try:
        out = await _check_opensanctions_impl(data)
        status_code = 200
        decision = "Unknown"
        entity_key = None
        auto_review_completed = False
        payload = out if isinstance(out, dict) else None
        if isinstance(out, JSONResponse):
            status_code = out.status_code
            try:
                payload = json.loads(out.body.decode("utf-8")) if out.body else {}
            except Exception:
                payload = {}
        if isinstance(payload, dict):
            summary = payload.get("Check Summary") if isinstance(payload.get("Check Summary"), dict) else {}
            decision = (
                summary.get("Status")
                or ("Queued" if payload.get("status") == "queued" else None)
                or payload.get("error")
                or payload.get("detail")
                or "Unknown"
            )
            entity_key = payload.get("entity_key")
            decision_lower = str(decision).strip().lower()
            rerun_cleared = decision_lower.startswith("cleared") or (
                payload.get("Is Sanctioned") is False and payload.get("Is PEP") is False
            )
            if data.rerun_entity_key and status_code < 400 and rerun_cleared:
                auto_review_completed = await _auto_complete_review_after_rerun_if_cleared(
                    entity_key=str(entity_key or data.rerun_entity_key),
                    actor=str(actor or "unknown_user"),
                    entity_type=str(data.entity_type or "Person"),
                    ip=_client_ip(request),
                )
        audit_log(
            "screening",
            action="screening_completed",
            actor=actor,
            resource="opcheck",
            outcome="success" if status_code < 400 else "failure",
            ip=_client_ip(request),
            extra={
                "decision": str(decision),
                "status_code": status_code,
                "entity_key": entity_key,
                "business_reference": data.business_reference,
                "reason_for_check": data.reason_for_check,
                "review_auto_completed": auto_review_completed,
            },
        )
        return out
    except Exception:
        audit_log(
            "screening",
            action="screening_completed",
            actor=actor,
            resource="opcheck",
            outcome="failure",
            ip=_client_ip(request),
            extra={"decision": "Unhandled exception", "business_reference": data.business_reference, "reason_for_check": data.reason_for_check},
        )
        raise


@app.post("/opcheck/dataverse")
@limiter.limit("60/minute")
async def check_opensanctions_dataverse(request: Request, data: OpCheckRequest, payload: dict = Depends(get_current_user)):
    """
    Dataverse-facing opcheck route. Behavior is intentionally identical to /opcheck,
    and responses include entity_id (alias of entity_key) for downstream linking.
    """
    actor = payload.get("sub")
    audit_log(
        "screening",
        action="screening_attempted",
        actor=actor,
        resource="opcheck/dataverse",
        outcome="attempted",
        ip=_client_ip(request),
        extra={"business_reference": data.business_reference, "reason_for_check": data.reason_for_check},
    )
    try:
        out = await _check_opensanctions_impl(data)
        status_code = 200
        decision = "Unknown"
        entity_key = None
        auto_review_completed = False
        payload = out if isinstance(out, dict) else None
        if isinstance(out, JSONResponse):
            status_code = out.status_code
            try:
                payload = json.loads(out.body.decode("utf-8")) if out.body else {}
            except Exception:
                payload = {}
        if isinstance(payload, dict):
            summary = payload.get("Check Summary") if isinstance(payload.get("Check Summary"), dict) else {}
            decision = (
                summary.get("Status")
                or ("Queued" if payload.get("status") == "queued" else None)
                or payload.get("error")
                or payload.get("detail")
                or "Unknown"
            )
            entity_key = payload.get("entity_key")
            decision_lower = str(decision).strip().lower()
            rerun_cleared = decision_lower.startswith("cleared") or (
                payload.get("Is Sanctioned") is False and payload.get("Is PEP") is False
            )
            if data.rerun_entity_key and status_code < 400 and rerun_cleared:
                auto_review_completed = await _auto_complete_review_after_rerun_if_cleared(
                    entity_key=str(entity_key or data.rerun_entity_key),
                    actor=str(actor or "unknown_user"),
                    entity_type=str(data.entity_type or "Person"),
                    ip=_client_ip(request),
                )
        audit_log(
            "screening",
            action="screening_completed",
            actor=actor,
            resource="opcheck/dataverse",
            outcome="success" if status_code < 400 else "failure",
            ip=_client_ip(request),
            extra={
                "decision": str(decision),
                "status_code": status_code,
                "entity_key": entity_key,
                "business_reference": data.business_reference,
                "reason_for_check": data.reason_for_check,
                "review_auto_completed": auto_review_completed,
            },
        )
        return out
    except Exception:
        audit_log(
            "screening",
            action="screening_completed",
            actor=actor,
            resource="opcheck/dataverse",
            outcome="failure",
            ip=_client_ip(request),
            extra={"decision": "Unhandled exception", "business_reference": data.business_reference, "reason_for_check": data.reason_for_check},
        )
        raise


@app.get("/opcheck/jobs/{job_id}", dependencies=[Depends(get_current_user)])
@limiter.limit("60/minute")
async def get_opcheck_job(request: Request, job_id: str):
    """Return job status; when completed, include the screening result."""
    pool = await screening_db.get_pool()
    if pool is None:
        return JSONResponse(status_code=404, content={"error": "not_found", "message": "Job not found"})
    async with pool.acquire() as conn:
        out = await screening_db.get_job_status(conn, job_id)
    if out is None:
        return JSONResponse(status_code=404, content={"error": "not_found", "message": "Job not found"})
    return out


@app.get("/opcheck/screened", dependencies=[Depends(get_current_user)])
async def get_opcheck_screened(
    request: Request,
    payload: dict = Depends(get_current_user),
    name: Optional[str] = None,
    entity_key: Optional[str] = None,
    business_reference: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """Search screened_entities by name (partial) and/or entity_key (exact). Requires at least one. Auth required."""
    if (
        not (name or entity_key or business_reference)
        or (not (name or "").strip() and not (entity_key or "").strip() and not (business_reference or "").strip())
    ):
        raise HTTPException(status_code=400, detail="Provide at least one of name, entity_key, or business_reference")
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Search unavailable (configure DATABASE_URL)")
    limit = max(1, min(100, limit))
    offset = max(0, offset)
    try:
        async with pool.acquire() as conn:
            items = await screening_db.search_screened_entities(
                conn,
                name=(name or "").strip() or None,
                entity_key=(entity_key or "").strip() or None,
                business_reference=(business_reference or "").strip() or None,
                limit=limit,
                offset=offset,
            )
        audit_log("data_access", action="screened_search", actor=payload.get("sub"), resource="opcheck/screened", outcome="success", ip=_client_ip(request), extra={"count": len(items)})
        return {"items": items}
    except Exception as e:
        logger.exception("GET /opcheck/screened failed: %s", e)
        raise HTTPException(status_code=500, detail="Search failed. Please try again or contact support.")


@app.get("/dashboard/summary", dependencies=[Depends(get_current_user)])
@limiter.limit("120/minute")
async def get_dashboard_summary(request: Request, payload: dict = Depends(get_current_user)):
    """High-level dashboard metrics for operational monitoring."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Dashboard unavailable (configure DATABASE_URL)")
    try:
        async with pool.acquire() as conn:
            summary = await screening_db.get_dashboard_summary(conn)
        audit_log(
            "data_access",
            action="dashboard_summary",
            actor=payload.get("sub"),
            resource="dashboard/summary",
            outcome="success",
            ip=_client_ip(request),
        )
        return summary
    except Exception as e:
        logger.exception("GET /dashboard/summary failed: %s", e)
        raise HTTPException(status_code=500, detail="Dashboard summary failed. Please try again or contact support.")


@app.get("/mi/export.csv", dependencies=[Depends(require_api_key_user)])
@limiter.limit("30/minute")
async def export_mi_csv(
    request: Request,
    payload: dict = Depends(require_api_key_user),
    screened_from: Optional[str] = None,
    screened_to: Optional[str] = None,
    review_status: Optional[str] = None,
    include_cleared: bool = True,
):
    """
    CSV export for MI / Power BI.
    API key only. Returns a flat extract of screened_entities plus selected result_json fields.
    """
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="MI export unavailable (configure DATABASE_URL)")
    try:
        async with pool.acquire() as conn:
            items = await screening_db.export_screened_entities_for_mi(
                conn,
                screened_from=screened_from,
                screened_to=screened_to,
                review_status=review_status,
                include_cleared=include_cleared,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("GET /mi/export.csv failed: %s", e)
        raise HTTPException(status_code=500, detail="MI export failed. Please try again or contact support.")

    fieldnames = [
        "entity_key",
        "display_name",
        "normalized_name",
        "date_of_birth",
        "country_input",
        "entity_type",
        "last_screened_at",
        "screening_valid_until",
        "status",
        "risk_level",
        "confidence",
        "score",
        "uk_sanctions_flag",
        "pep_flag",
        "last_requestor",
        "business_reference",
        "reason_for_check",
        "review_status",
        "review_claimed_by",
        "review_claimed_at",
        "review_outcome",
        "review_notes",
        "review_completed_by",
        "review_completed_at",
        "updated_at",
        "result_sanctions_name",
        "result_birth_date",
        "result_regime",
        "result_is_sanctioned",
        "result_is_pep",
        "result_match_found",
        "result_risk_level",
        "result_confidence",
        "result_score",
        "result_check_status",
        "result_check_source",
        "result_check_date",
        "person_check_status",
        "person_check_is_match",
        "person_check_score",
        "organization_check_status",
        "organization_check_is_match",
        "organization_check_score",
        "pep_check_checked",
        "pep_check_status",
        "pep_check_reason",
        "pep_check_message",
        "input_submitted_as",
        "input_inferred_as",
        "input_likely_misclassified",
        "input_classification_confidence",
        "input_classification_signals_json",
        "top_matches_json",
        "result_json",
    ]

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        writer.writerow({key: item.get(key) for key in fieldnames})

    audit_log(
        "data_access",
        action="mi_export_csv",
        actor=payload.get("sub"),
        resource="mi/export.csv",
        outcome="success",
        ip=_client_ip(request),
        extra={
            "row_count": len(items),
            "screened_from": screened_from,
            "screened_to": screened_to,
            "review_status": review_status,
            "include_cleared": include_cleared,
            "api_key_id": payload.get("api_key_id"),
        },
    )

    filename_date = datetime.now().strftime("%Y%m%d")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="screening-mi-export-{filename_date}.csv"'},
    )


@app.get("/review/queue", dependencies=[Depends(get_current_user)])
@limiter.limit("120/minute")
async def get_review_queue(
    request: Request,
    payload: dict = Depends(get_current_user),
    review_status: Optional[str] = None,
    business_reference: Optional[str] = None,
    reason_for_check: Optional[str] = None,
    include_cleared: bool = False,
    limit: int = 100,
    offset: int = 0,
):
    """Return potential-match review queue. By default excludes Cleared decisions unless include_cleared=true."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Review queue unavailable (configure DATABASE_URL)")
    try:
        async with pool.acquire() as conn:
            items = await screening_db.list_review_queue(
                conn,
                review_status=(review_status or "").strip() or None,
                business_reference=(business_reference or "").strip() or None,
                reason_for_check=(reason_for_check or "").strip() or None,
                include_cleared=bool(include_cleared),
                limit=limit,
                offset=offset,
            )
        return {"items": items}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("GET /review/queue failed: %s", e)
        raise HTTPException(status_code=500, detail="Review queue failed. Please try again or contact support.")


@app.post("/review/{entity_key}/claim", dependencies=[Depends(get_current_user)])
@limiter.limit("120/minute")
async def claim_review(
    request: Request,
    entity_key: str,
    payload: dict = Depends(get_current_user),
):
    """Claim an unreviewed potential match for review."""
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Review queue unavailable (configure DATABASE_URL)")
    actor = payload.get("sub")
    async with pool.acquire() as conn:
        out = await screening_db.claim_review(
            conn,
            entity_key=(entity_key or "").strip(),
            claimed_by=str(actor or "").strip() or "unknown_user",
        )
    if out.get("status") != "ok":
        err = out.get("error")
        if err == "not_found":
            raise HTTPException(status_code=404, detail="Entity not found")
        if err == "not_reviewable":
            raise HTTPException(status_code=409, detail="Only potential matches can be claimed for review")
        raise HTTPException(status_code=409, detail="Only unreviewed matches can be claimed")
    item = out.get("item") or {}
    audit_log(
        "review",
        action="REVIEW_CLAIMED",
        actor=actor,
        resource=str(item.get("entity_key") or entity_key),
        outcome="success",
        ip=_client_ip(request),
        extra={
            "entity_key": item.get("entity_key"),
            "business_reference": item.get("business_reference"),
            "reason_for_check": item.get("reason_for_check"),
        },
    )
    return {"status": "ok", "item": item}


@app.post("/review/{entity_key}/complete", dependencies=[Depends(get_current_user)])
@limiter.limit("120/minute")
async def complete_review(
    request: Request,
    entity_key: str,
    body: ReviewCompleteRequest,
    payload: dict = Depends(get_current_user),
):
    """
    Complete an in-review match with mandatory structured outcome and notes.
    Original screening decision fields remain unchanged.
    """
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Review queue unavailable (configure DATABASE_URL)")
    actor = payload.get("sub")
    try:
        async with pool.acquire() as conn:
            out = await screening_db.complete_review(
                conn,
                entity_key=(entity_key or "").strip(),
                completed_by=str(actor or "").strip() or "unknown_user",
                review_outcome=body.review_outcome.value,
                review_notes=body.review_notes,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if out.get("status") != "ok":
        err = out.get("error")
        if err == "not_found":
            raise HTTPException(status_code=404, detail="Entity not found")
        raise HTTPException(status_code=409, detail="Only IN_REVIEW matches can be completed")
    item = out.get("item") or {}
    audit_log(
        "review",
        action="REVIEW_COMPLETED",
        actor=actor,
        resource=str(item.get("entity_key") or entity_key),
        outcome="success",
        ip=_client_ip(request),
        extra={
            "entity_key": item.get("entity_key"),
            "business_reference": item.get("business_reference"),
            "reason_for_check": item.get("reason_for_check"),
            "review_outcome": item.get("review_outcome"),
        },
    )
    return {"status": "ok", "item": item}


@app.post("/review/{entity_key}/rerun", dependencies=[Depends(get_current_user)])
@limiter.limit("60/minute")
async def rerun_review(
    request: Request,
    entity_key: str,
    body: ReviewRerunRequest,
    payload: dict = Depends(get_current_user),
):
    """
    Re-run screening for a claimed review item using additional disambiguation data.
    Person requires dob. Organization requires country.
    If rerun result is Cleared, mark review as COMPLETED automatically.
    """
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Review queue unavailable (configure DATABASE_URL)")

    actor = str(payload.get("sub") or "").strip() or "unknown_user"
    key = (entity_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="entity_key is required")

    async with pool.acquire() as conn:
        rows = await screening_db.search_screened_entities(conn, entity_key=key, limit=1, offset=0)
    if not rows:
        raise HTTPException(status_code=404, detail="Entity not found")

    row = rows[0]
    original_entity_type = str(row.get("entity_type") or "Person").strip() or "Person"
    if (row.get("review_status") or "").upper() != "IN_REVIEW":
        raise HTTPException(status_code=409, detail="Only IN_REVIEW matches can be re-run")
    if (row.get("review_claimed_by") or "").strip().lower() != actor.lower():
        raise HTTPException(status_code=403, detail="Only the claiming user can re-run this review")

    entity_type = (body.entity_type or original_entity_type).strip() or "Person"
    type_norm = entity_type.strip().lower()
    rerun_dob = (body.dob.strip() if isinstance(body.dob, str) else body.dob) or None
    rerun_country = (body.country.strip() if isinstance(body.country, str) else body.country) or None
    if type_norm == "person" and not rerun_dob:
        raise HTTPException(status_code=400, detail="Date of birth is required for Person re-run")
    if type_norm == "organization" and not rerun_country:
        raise HTTPException(status_code=400, detail="Country is required for Organization re-run")

    audit_log(
        "review",
        action="REVIEW_RERUN_ATTEMPTED",
        actor=actor,
        resource=key,
        outcome="attempted",
        ip=_client_ip(request),
        extra={
            "business_reference": row.get("business_reference"),
            "reason_for_check": row.get("reason_for_check"),
            "original_entity_type": original_entity_type,
            "entity_type": entity_type,
            "type_corrected": entity_type != original_entity_type,
        },
    )

    op_req = OpCheckRequest(
        name=str(row.get("display_name") or ""),
        dob=rerun_dob if type_norm == "person" else (row.get("date_of_birth") or None),
        country=rerun_country if type_norm == "organization" else None,
        entity_type=entity_type,
        business_reference=str(row.get("business_reference") or "").strip() or key,
        reason_for_check=row.get("reason_for_check") or "Ad-Hoc Compliance Review",
        requestor=str(row.get("last_requestor") or actor),
        search_backend="postgres_beta",
        rerun_entity_key=key,
    )
    out = await _check_opensanctions_impl(op_req)
    rerun_payload: dict = out if isinstance(out, dict) else {}
    if isinstance(out, JSONResponse):
        if out.status_code >= 400:
            try:
                detail = json.loads(out.body.decode("utf-8")) if out.body else {}
            except Exception:
                detail = {"detail": "Re-run failed"}
            raise HTTPException(status_code=out.status_code, detail=detail.get("message") or detail.get("detail") or "Re-run failed")
        try:
            rerun_payload = json.loads(out.body.decode("utf-8")) if out.body else {}
        except Exception:
            rerun_payload = {}

    summary = rerun_payload.get("Check Summary") if isinstance(rerun_payload.get("Check Summary"), dict) else {}
    decision = str(summary.get("Status") or rerun_payload.get("status") or "Unknown")
    cleared = decision.lower().startswith("cleared")
    auto_completed = False
    completed_item = None
    if cleared:
        async with pool.acquire() as conn:
            completed = await screening_db.complete_review(
                conn,
                entity_key=key,
                completed_by=actor,
                review_outcome=ReviewOutcome.FALSE_POSITIVE_PROCEEDED.value,
                review_notes=(
                    f"Auto-resolved after re-run with additional {('DOB' if type_norm == 'person' else 'country')} "
                    f"information{' and entity type correction' if entity_type != original_entity_type else ''}."
                ),
            )
        if completed.get("status") == "ok":
            auto_completed = True
            completed_item = completed.get("item")
            audit_log(
                "review",
                action="REVIEW_COMPLETED",
                actor=actor,
                resource=key,
                outcome="success",
                ip=_client_ip(request),
                extra={
                    "entity_key": key,
                    "business_reference": row.get("business_reference"),
                    "reason_for_check": row.get("reason_for_check"),
                    "review_outcome": ReviewOutcome.FALSE_POSITIVE_PROCEEDED.value,
                    "auto_completed": True,
                },
            )

    audit_log(
        "review",
        action="REVIEW_RERUN_COMPLETED",
        actor=actor,
        resource=key,
        outcome="success",
        ip=_client_ip(request),
        extra={
            "entity_key": key,
            "decision": decision,
            "auto_completed": auto_completed,
            "original_entity_type": original_entity_type,
            "corrected_entity_type": entity_type,
            "type_corrected": entity_type != original_entity_type,
            "business_reference": row.get("business_reference"),
            "reason_for_check": row.get("reason_for_check"),
        },
    )
    return {
        "status": "ok",
        "entity_key": key,
        "decision": decision,
        "auto_completed": auto_completed,
        "original_entity_type": original_entity_type,
        "corrected_entity_type": entity_type,
        "type_corrected": entity_type != original_entity_type,
        "result": rerun_payload,
        "review_item": completed_item,
    }


@app.get("/ai-triage/tasks", dependencies=[Depends(get_current_user)])
@limiter.limit("120/minute")
async def list_ai_triage_tasks(
    request: Request,
    payload: dict = Depends(get_current_user),
    status: Optional[str] = "PENDING_REVIEW",
    limit: int = 100,
    offset: int = 0,
):
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="AI triage unavailable (configure DATABASE_URL)")
    try:
        async with pool.acquire() as conn:
            items = await screening_db.list_ai_triage_tasks(
                conn,
                status=(status or "").strip().upper() or None,
                limit=limit,
                offset=offset,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"items": items}


@app.get("/ai-triage/tasks/{triage_id}", dependencies=[Depends(get_current_user)])
@limiter.limit("120/minute")
async def get_ai_triage_task(
    request: Request,
    triage_id: str,
    payload: dict = Depends(get_current_user),
):
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="AI triage unavailable (configure DATABASE_URL)")
    async with pool.acquire() as conn:
        item = await screening_db.get_ai_triage_task(conn, triage_id=triage_id)
    if item is None:
        raise HTTPException(status_code=404, detail="AI triage task not found")
    return item


@app.post("/ai-triage/tasks/{triage_id}/approve", dependencies=[Depends(get_current_user)])
@limiter.limit("60/minute")
async def approve_ai_triage_task(
    request: Request,
    triage_id: str,
    body: AiTriageDecisionRequest,
    payload: dict = Depends(get_current_user),
):
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="AI triage unavailable (configure DATABASE_URL)")
    actor = str(payload.get("sub") or "").strip() or "unknown_user"
    async with pool.acquire() as conn:
        task = await screening_db.get_ai_triage_task(conn, triage_id=triage_id)
        if task is None:
            raise HTTPException(status_code=404, detail="AI triage task not found")
        if str(task.get("status") or "").upper() != "PENDING_REVIEW":
            raise HTTPException(status_code=409, detail="Only pending AI tasks can be approved")
        final_screening_outcome = task.get("screening_status") or task.get("final_screening_outcome")
        effective_action = str(task.get("effective_recommended_action") or "UNSURE").upper()
        if effective_action == "CLEAR":
            cleared = await screening_db.mark_false_positive(
                conn,
                entity_key=str(task.get("entity_key") or ""),
                actor=actor,
                reason=(
                    f"AI triage approved by {actor}. "
                    f"Model={task.get('llm_model')}; recommendation=CLEAR; "
                    f"confidence={task.get('ai_confidence_band') or task.get('ai_confidence_raw')}. "
                    f"{(body.reviewer_notes or '').strip()}".strip()
                ),
            )
            if cleared is not None:
                summary = cleared.get("Check Summary") if isinstance(cleared.get("Check Summary"), dict) else {}
                final_screening_outcome = summary.get("Status") or "Cleared - False Positive"
        approved = await screening_db.approve_ai_triage_task(
            conn,
            triage_id=triage_id,
            reviewer=actor,
            reviewer_notes=body.reviewer_notes,
            final_screening_outcome=str(final_screening_outcome or ""),
        )
    if approved is None:
        raise HTTPException(status_code=409, detail="Only pending AI tasks can be approved")
    audit_log(
        "review",
        action="AI_TRIAGE_APPROVED",
        actor=actor,
        resource=str(task.get("entity_key") or triage_id),
        outcome="success",
        ip=_client_ip(request),
        extra={
            "triage_id": triage_id,
            "recommended_action": task.get("effective_recommended_action"),
            "raw_recommended_action": task.get("raw_recommended_action"),
            "guardrail_overridden": task.get("guardrail_overridden"),
        },
    )
    return {"status": "ok", "item": approved}


@app.post("/ai-triage/tasks/{triage_id}/reject", dependencies=[Depends(get_current_user)])
@limiter.limit("60/minute")
async def reject_ai_triage_task(
    request: Request,
    triage_id: str,
    body: AiTriageDecisionRequest,
    payload: dict = Depends(get_current_user),
):
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="AI triage unavailable (configure DATABASE_URL)")
    actor = str(payload.get("sub") or "").strip() or "unknown_user"
    async with pool.acquire() as conn:
        task = await screening_db.get_ai_triage_task(conn, triage_id=triage_id)
        if task is None:
            raise HTTPException(status_code=404, detail="AI triage task not found")
        if str(task.get("status") or "").upper() != "PENDING_REVIEW":
            raise HTTPException(status_code=409, detail="Only pending AI tasks can be rejected")
        rejected = await screening_db.reject_ai_triage_task(
            conn,
            triage_id=triage_id,
            reviewer=actor,
            reviewer_notes=body.reviewer_notes,
            final_screening_outcome=str(task.get("screening_status") or ""),
        )
    if rejected is None:
        raise HTTPException(status_code=409, detail="Only pending AI tasks can be rejected")
    audit_log(
        "review",
        action="AI_TRIAGE_REJECTED",
        actor=actor,
        resource=str(task.get("entity_key") or triage_id),
        outcome="success",
        ip=_client_ip(request),
        extra={
            "triage_id": triage_id,
            "recommended_action": task.get("effective_recommended_action"),
            "raw_recommended_action": task.get("raw_recommended_action"),
        },
    )
    return {"status": "ok", "item": rejected}


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
    business_reference = (item.business_reference or "").strip()
    if not business_reference:
        return {"status": "error", "error": "missing_business_reference"}
    dob = (item.dob.strip() if isinstance(item.dob, str) else item.dob) or None
    country = (item.country.strip() if isinstance(item.country, str) else item.country) or None
    entity_type = (item.entity_type or "Person")

    entity_key_candidates = derive_entity_key_variants(display_name=name, entity_type=entity_type, dob=dob)
    entity_key = entity_key_candidates[0]

    valid = None
    for key_candidate in entity_key_candidates:
        valid = await screening_db.get_valid_screening(conn, key_candidate)
        if valid is not None:
            break
    if valid is not None:
        return {"status": "reused"}

    if any([await screening_db.has_pending_or_running_job(conn, key_candidate) for key_candidate in entity_key_candidates]):
        return {"status": "already_pending"}

    job_id = await screening_db.enqueue_job(
        conn, entity_key=entity_key, name=name, date_of_birth=dob,
        country=country, entity_type=entity_type, requestor=requestor, business_reference=business_reference, reason_for_check=item.reason_for_check,
    )
    return {"status": "queued", "job_id": job_id}


@app.post("/internal/screening/jobs", dependencies=[Depends(require_internal_screening_auth)])
@limiter.limit("120/minute")
async def internal_screening_jobs(request: Request, data: InternalScreeningRequest):
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
    audit_log(
        "screening",
        action="internal_screening_enqueue",
        actor=(data.requestor or "").strip() or "internal_api",
        resource="internal/screening/jobs",
        outcome="ENQUEUED",
        ip=_client_ip(request),
        extra={
            "status": outcome.get("status"),
            "job_id": outcome.get("job_id"),
            "business_reference": data.business_reference,
            "reason_for_check": data.reason_for_check,
        },
    )
    return outcome


@app.post("/internal/screening/jobs/bulk", dependencies=[Depends(require_internal_screening_auth)])
@limiter.limit("20/minute")
async def internal_screening_jobs_bulk(request: Request, body: InternalScreeningBulkRequest):
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
    queued_job_ids = [str(r.get("job_id")) for r in results if r.get("status") == "queued" and r.get("job_id")]
    audit_log(
        "screening",
        action="internal_bulk_screening_enqueue",
        actor="internal_api_bulk",
        resource="internal/screening/jobs/bulk",
        outcome="ENQUEUED",
        ip=_client_ip(request),
        extra={
            "total": len(results),
            **counts,
            "job_id": queued_job_ids[0] if queued_job_ids else None,
            "job_ids_queued": queued_job_ids,
            "business_reference": sorted({(r.business_reference or "").strip() for r in body.requests if (r.business_reference or "").strip()}),
            "reason_for_check": sorted({str(r.reason_for_check) for r in body.requests if r.reason_for_check}),
        },
    )
    return {"results": results}


@app.post("/refresh_opensanctions", dependencies=[Depends(require_refresh_opensanctions_auth)])
@limiter.limit("2/minute")
async def refresh_opensanctions(request: Request, body: RefreshRequest):
    """
    Download latest consolidated sanctions (and optionally PEPs), write to parquet,
    and (by default) rebuild PostgreSQL watchlist table from refreshed CSV data.
    Requires admin JWT or REFRESH_OPENSANCTIONS_API_KEY (header X-Refresh-Opensanctions-Key or Authorization: Bearer <key>).
    """
    try:
        refresh_opensanctions_data(include_peps=body.include_peps)
        postgres_synced = False
        postgres_rows = {"sanctions": 0, "peps": 0}
        refresh_run_info = None
        if body.sync_postgres:
            pool = await screening_db.get_pool()
            if pool is None:
                raise RuntimeError("DATABASE_URL is required for sync_postgres=true")
            sanctions_csv = os.path.join(DATA_DIR, "os_sanctions_latest.csv")
            peps_csv = os.path.join(DATA_DIR, "os_peps_latest.csv")
            async with pool.acquire() as conn:
                postgres_rows = await sync_watchlist_entities_postgres(
                    conn,
                    sanctions_csv_path=sanctions_csv,
                    peps_csv_path=peps_csv,
                    include_peps=body.include_peps,
                )
            postgres_synced = True

        # Delta-driven UK list monitoring and targeted re-screening.
        pool = await screening_db.get_pool()
        if pool is not None:
            sanctions_csv = os.path.join(DATA_DIR, "os_sanctions_latest.csv")
            uk_snapshot = build_uk_sanctions_snapshot(sanctions_csv)
            auto_delta_enabled = os.environ.get("UK_DELTA_RESCREEN_ENABLED", "true").strip().lower() in ("1", "true", "yes")
            max_terms = max(10, int(os.environ.get("UK_DELTA_MAX_TERMS", "250")))
            max_candidates = max(100, int(os.environ.get("UK_DELTA_MAX_CANDIDATES", "15000")))
            system_requestor = os.environ.get("UK_DELTA_SYSTEM_REQUESTOR", "system:uk-delta-rescreen").strip() or "system:uk-delta-rescreen"

            async with pool.acquire() as conn:
                previous_run = await screening_db.get_latest_refresh_run(conn)
                previous_hash = (previous_run or {}).get("uk_hash") if previous_run else None
                previous_entries = []
                if previous_run:
                    previous_entries = await screening_db.get_uk_snapshot_entries(
                        conn,
                        str(previous_run["refresh_run_id"]),
                    )

                delta = compute_uk_snapshot_delta(
                    uk_snapshot.get("entries", []),
                    previous_entries,
                )
                uk_hash = (uk_snapshot.get("uk_hash") or "").strip()
                uk_changed = bool(not previous_hash or uk_hash != (previous_hash or ""))

                refresh_run_id = await screening_db.create_refresh_run(
                    conn,
                    include_peps=body.include_peps,
                    postgres_synced=postgres_synced,
                    sanctions_rows=int(postgres_rows.get("sanctions", 0)),
                    peps_rows=int(postgres_rows.get("peps", 0)),
                    uk_hash=uk_hash,
                    prev_uk_hash=previous_hash,
                    uk_changed=uk_changed,
                    uk_row_count=int(uk_snapshot.get("row_count", 0)),
                    delta_added=int(delta.get("added", 0)),
                    delta_removed=int(delta.get("removed", 0)),
                    delta_changed=int(delta.get("changed", 0)),
                )
                await screening_db.replace_uk_snapshot_entries(
                    conn,
                    refresh_run_id=refresh_run_id,
                    entries=uk_snapshot.get("entries", []),
                )

                candidate_count = 0
                queued_count = 0
                already_pending_count = 0
                reused_count = 0
                failed_count = 0
                stale_overrides_marked = 0

                if uk_changed:
                    stale_overrides_marked = await screening_db.mark_manual_overrides_stale(
                        conn,
                        latest_uk_hash=uk_hash,
                    )
                    if auto_delta_enabled:
                        candidate_terms = (delta.get("candidate_terms") or [])[:max_terms]
                        candidates = await screening_db.shortlist_screened_entities_by_terms(
                            conn,
                            terms=candidate_terms,
                            max_candidates=max_candidates,
                        )
                        candidate_count = len(candidates)
                        for candidate in candidates:
                            entity_key = str(candidate.get("entity_key") or "").strip()
                            if not entity_key:
                                failed_count += 1
                                continue
                            if await screening_db.has_pending_or_running_job(conn, entity_key):
                                already_pending_count += 1
                                continue
                            try:
                                dob_raw = candidate.get("date_of_birth")
                                dob_str = dob_raw.isoformat() if hasattr(dob_raw, "isoformat") else (str(dob_raw) if dob_raw else None)
                                await screening_db.enqueue_job(
                                    conn,
                                    entity_key=entity_key,
                                    name=str(candidate.get("display_name") or ""),
                                    date_of_birth=dob_str,
                                    country=None,
                                    entity_type=str(candidate.get("entity_type") or "Person"),
                                    requestor=system_requestor,
                                    business_reference=f"UK-DELTA-{refresh_run_id}",
                                    reason_for_check="Periodic Re-Screen",
                                    reason="uk_delta_rescreen",
                                    refresh_run_id=refresh_run_id,
                                    force_rescreen=True,
                                )
                                queued_count += 1
                            except Exception:
                                failed_count += 1

                await screening_db.finalize_refresh_run(
                    conn,
                    refresh_run_id=refresh_run_id,
                    candidate_count=candidate_count,
                    queued_count=queued_count,
                    already_pending_count=already_pending_count,
                    reused_count=reused_count,
                    failed_count=failed_count,
                )

                refresh_run_info = {
                    "refresh_run_id": refresh_run_id,
                    "uk_hash": uk_hash,
                    "uk_changed": uk_changed,
                    "uk_row_count": int(uk_snapshot.get("row_count", 0)),
                    "delta": {
                        "added": int(delta.get("added", 0)),
                        "removed": int(delta.get("removed", 0)),
                        "changed": int(delta.get("changed", 0)),
                    },
                    "rescreen": {
                        "enabled": auto_delta_enabled,
                        "candidate_count": candidate_count,
                        "queued_count": queued_count,
                        "already_pending_count": already_pending_count,
                        "failed_count": failed_count,
                        "stale_overrides_marked": stale_overrides_marked,
                    },
                }
        return {
            "status": "ok",
            "include_peps": body.include_peps,
            "postgres_synced": postgres_synced,
            "postgres_rows": postgres_rows,
            "refresh_run": refresh_run_info,
        }
    except Exception as e:
        logger.exception("Refresh OpenSanctions failed: %s", e)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": _GENERIC_ERROR_MESSAGE},
        )

# Server-side Companies House integration routes (API key remains backend-only).
app.include_router(companies_house_router, dependencies=[Depends(get_current_user)])

# Serve built frontend from frontend/dist (must be last so API routes take precedence).
# SPA fallback: unknown paths (e.g. /admin/users) serve index.html so refresh/navigation works.
_app_dir = os.path.dirname(os.path.abspath(__file__))
_frontend_dist = os.path.join(_app_dir, "frontend", "dist")
if os.path.isdir(_frontend_dist):
    app.mount("/", SPAStaticFiles(directory=_frontend_dist, html=True), name="frontend")
