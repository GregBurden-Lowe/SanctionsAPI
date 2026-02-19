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
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded


class SPAStaticFiles(StaticFiles):
    """Serve static files but fall back to index.html for missing paths so SPA client-side routing works on refresh."""

    def lookup_path(self, path: str):
        full_path, stat_result = super().lookup_path(path)
        if stat_result is None:
            return super().lookup_path("index.html")
        return full_path, stat_result

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


app = FastAPI(title="Sanctions/PEP Screening API", version="1.0.0", lifespan=lifespan)
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
    email: str = Field(..., description="User email (must be from an allowed domain); temp password is emailed via Resend")


# Internal queue-ingestion API: request body (no screening results returned).
class InternalScreeningRequest(BaseModel):
    name: str = Field(..., description="Full name or organization to screen")
    dob: Optional[str] = Field(None, description="Date of birth (YYYY-MM-DD) or null")
    entity_type: Optional[str] = Field("Person", description="'Person' or 'Organization'")
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


@app.options("/opcheck")
@app.options("/opcheck/screened")
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
@limiter.limit("60/minute")
async def check_opensanctions(request: Request, data: OpCheckRequest):
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
            return { **cached, "entity_key": entity_key }

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
    return { **results, "entity_key": entity_key }


@app.get("/opcheck/jobs/{job_id}")
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
    limit: int = 50,
    offset: int = 0,
):
    """Search screened_entities by name (partial) and/or entity_key (exact). Requires at least one. Auth required."""
    if not (name or entity_key) or (not (name or "").strip() and not (entity_key or "").strip()):
        raise HTTPException(status_code=400, detail="Provide at least one of name or entity_key")
    pool = await screening_db.get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="Search unavailable (configure DATABASE_URL)")
    limit = max(1, min(100, limit))
    offset = max(0, offset)
    try:
        async with pool.acquire() as conn:
            items = await screening_db.search_screened_entities(
                conn, name=(name or "").strip() or None, entity_key=(entity_key or "").strip() or None, limit=limit, offset=offset,
            )
        audit_log("data_access", action="screened_search", actor=payload.get("sub"), resource="opcheck/screened", outcome="success", ip=_client_ip(request), extra={"count": len(items)})
        return {"items": items}
    except Exception as e:
        logger.exception("GET /opcheck/screened failed: %s", e)
        raise HTTPException(status_code=500, detail="Search failed. Please try again or contact support.")


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
@limiter.limit("120/minute")
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
@limiter.limit("20/minute")
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


@app.post("/refresh_opensanctions", dependencies=[Depends(require_refresh_opensanctions_auth)])
@limiter.limit("2/minute")
async def refresh_opensanctions(request: Request, body: RefreshRequest):
    """
    Download latest consolidated sanctions (and optionally PEPs), write to parquet.
    Requires admin JWT or REFRESH_OPENSANCTIONS_API_KEY (header X-Refresh-Opensanctions-Key or Authorization: Bearer <key>).
    """
    try:
        refresh_opensanctions_data(include_peps=body.include_peps)
        return {"status": "ok", "include_peps": body.include_peps}
    except Exception as e:
        logger.exception("Refresh OpenSanctions failed: %s", e)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": _GENERIC_ERROR_MESSAGE},
        )

# Serve built frontend from frontend/dist (must be last so API routes take precedence).
# SPA fallback: unknown paths (e.g. /admin/users) serve index.html so refresh/navigation works.
_app_dir = os.path.dirname(os.path.abspath(__file__))
_frontend_dist = os.path.join(_app_dir, "frontend", "dist")
if os.path.isdir(_frontend_dist):
    app.mount("/", SPAStaticFiles(directory=_frontend_dist, html=True), name="frontend")
