import hmac
import hashlib
import ipaddress
import json
import secrets
import socket
import uuid
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from pydantic import SecretStr
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from croniter import croniter
import pyotp
from redis.asyncio import Redis

from app.crypto import encrypt_secret, sha256_hex
from app.crypto import decrypt_secret
from app.ai_gateway import AIGatewayError, interpret as ai_interpret, list_models as ai_list_models
from app.bootstrap import ensure_bootstrap_owner
from app.db import create_schema, engine, get_db
from app.models import (
    Approval,
    ApprovalStatus,
    AuditLog,
    Artifact,
    AIConfig,
    DashboardSession,
    Job,
    JobStatus,
    Role,
    Schedule,
    TelegramUser,
    User,
    Website,
    WebsiteCredential,
    WebsiteStatus,
    utcnow,
)
from app.policy import ACTION_POLICIES, PolicyViolation, validate_action
from app.settings import get_settings
from app.telegram import router as telegram_router

settings = get_settings()
password_hasher = PasswordHasher()
_local_rate_limits: dict[str, list[float]] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.app_env == "development":
        await create_schema()
    await ensure_bootstrap_owner()
    yield
    await engine.dispose()


app = FastAPI(title="Pbot API", version="0.1.0", lifespan=lifespan)
app.include_router(telegram_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-Telegram-Bot-Api-Secret-Token"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    response.headers["X-Frame-Options"] = "DENY"
    if settings.app_env == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class WebsiteInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,32}$")
    name: str = Field(min_length=1, max_length=100)
    base_url: HttpUrl
    login_url: HttpUrl
    dashboard_url: HttpUrl
    adapter_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    adapter_version: str = Field(default="1.0.0", max_length=32)
    adapter_config: dict = Field(default_factory=dict)
    allowed_actions: list[str] = Field(min_length=1, max_length=10)


class CredentialInput(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class JobInput(BaseModel):
    website_id: str = Field(max_length=32)
    action: str = Field(max_length=64)
    payload: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)


class DecisionInput(BaseModel):
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str | None = Field(default=None, max_length=500)


class ScheduleInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    website_id: str = Field(max_length=32)
    action: str = Field(max_length=64)
    payload: dict = Field(default_factory=dict)
    cron_expr: str = Field(min_length=5, max_length=64)
    timezone: str = Field(default="Asia/Jakarta", max_length=64)
    is_active: bool = True
    misfire_policy: str = "run_once_latest"


class AIConfigInput(BaseModel):
    api_key: SecretStr | None = None
    primary_model_id: str = Field(min_length=1, max_length=128)
    fallback_model_id: str | None = Field(default=None, max_length=128)
    temperature: float = Field(default=0.1, ge=0, le=1)
    max_output_tokens: int = Field(default=1024, ge=128, le=8192)
    timeout_seconds: int = Field(default=30, ge=5, le=120)
    mode: str = Field(default="structured_only", pattern=r"^(structured_only|hybrid)$")
    allow_ai_write_proposals: bool = False
    system_instruction: str = Field(default="", max_length=4000)


class AIKeyInput(BaseModel):
    api_key: SecretStr


class TOTPInput(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class AIInterpretInput(BaseModel):
    instruction: str = Field(min_length=3, max_length=2000)
    idempotency_key: str | None = Field(default=None, max_length=128)


class TelegramUserInput(BaseModel):
    telegram_user_id: int = Field(gt=0)
    display_name: str = Field(min_length=1, max_length=100)
    role: Role


def error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, detail={"code": code, "message": message})


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def enforce_rate_limit(key: str, limit: int, window_seconds: int) -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with redis.pipeline(transaction=True) as pipeline:
            pipeline.incr(key)
            pipeline.expire(key, window_seconds)
            count, _ = await pipeline.execute()
        if count > limit:
            raise error(429, "RATE_LIMITED", "Terlalu banyak permintaan. Coba kembali nanti.")
    except HTTPException:
        raise
    except Exception:
        if settings.app_env == "production":
            raise error(503, "RATE_LIMIT_UNAVAILABLE", "Layanan rate limit belum siap.") from None
        now = time.time()
        recent = [item for item in _local_rate_limits.get(key, []) if item > now - window_seconds]
        if len(recent) >= limit:
            raise error(429, "RATE_LIMITED", "Terlalu banyak permintaan. Coba kembali nanti.") from None
        recent.append(now)
        _local_rate_limits[key] = recent
    finally:
        await redis.aclose()


def _validate_target(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise error(422, "INVALID_URL", "URL website harus menggunakan HTTPS.")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "metadata.google.internal"} or hostname.endswith((".localhost", ".local")):
        raise error(422, "PRIVATE_URL_BLOCKED", "URL lokal atau privat tidak diizinkan.")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address and (not address.is_global or address.is_private or address.is_loopback or address.is_link_local):
        raise error(422, "PRIVATE_URL_BLOCKED", "Alamat IP privat tidak diizinkan.")
    try:
        resolved = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise error(422, "HOST_UNRESOLVABLE", "Domain website tidak dapat diverifikasi.") from None
    for item in resolved:
        resolved_ip = ipaddress.ip_address(item[4][0])
        if not resolved_ip.is_global or resolved_ip.is_private or resolved_ip.is_loopback or resolved_ip.is_link_local:
            raise error(422, "PRIVATE_URL_BLOCKED", "Domain mengarah ke alamat IP privat.")


def _audit(
    db: AsyncSession,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    result: str = "success",
    metadata: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            result=result,
            metadata_json=metadata or {},
        )
    )


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise error(401, "AUTH_REQUIRED", "Silakan masuk terlebih dahulu.")
    session_result = await db.execute(
        select(DashboardSession).where(DashboardSession.token_hash == sha256_hex(raw_token))
    )
    dashboard_session = session_result.scalar_one_or_none()
    now = utcnow()
    if not dashboard_session or dashboard_session.revoked_at:
        raise error(401, "SESSION_INVALID", "Sesi tidak valid.")
    if dashboard_session.pending_totp:
        raise error(401, "TOTP_REQUIRED", "Verifikasi kode autentikasi dua faktor terlebih dahulu.")
    if as_utc(dashboard_session.expires_at) <= now or as_utc(dashboard_session.last_activity_at) <= now - timedelta(
        minutes=settings.session_idle_minutes
    ):
        dashboard_session.revoked_at = now
        await db.commit()
        raise error(401, "SESSION_EXPIRED", "Sesi telah berakhir. Silakan masuk kembali.")
    user = await db.get(User, dashboard_session.user_id)
    if not user or not user.is_active:
        raise error(401, "ACCOUNT_DISABLED", "Akun tidak aktif.")
    dashboard_session.last_activity_at = now
    await db.commit()
    return user


def require_role(*roles: Role):
    async def dependency(user: User = Depends(get_current_user)) -> User:
        allowed = {Role.owner, Role.operator, Role.viewer}
        if Role.owner in roles:
            allowed = {Role.owner}
        elif Role.operator in roles:
            allowed = {Role.owner, Role.operator}
        if user.role not in allowed:
            raise error(403, "FORBIDDEN", "Peran akun tidak memiliki izin untuk tindakan ini.")
        return user

    return dependency


async def require_csrf(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    raw_token = request.cookies.get(settings.session_cookie_name)
    csrf_token = request.headers.get("X-CSRF-Token", "")
    result = await db.execute(
        select(DashboardSession).where(DashboardSession.token_hash == sha256_hex(raw_token or ""))
    )
    dashboard_session = result.scalar_one_or_none()
    if not dashboard_session or not hmac.compare_digest(
        dashboard_session.csrf_hash, sha256_hex(csrf_token)
    ):
        raise error(403, "CSRF_INVALID", "Token keamanan request tidak valid.")
    return user


async def _approval_hash(job: Job, website: Website, preview: dict) -> str:
    canonical = json.dumps(
        {
            "job_id": str(job.id),
            "website_id": website.id,
            "adapter_version": job.adapter_version,
            "action": job.action,
            "parameters": job.payload,
            "preview_summary": preview,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return sha256_hex(canonical)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/api/v1/auth/bootstrap/status")
async def bootstrap_status(db: AsyncSession = Depends(get_db)):
    user_count = await db.scalar(select(func.count()).select_from(User))
    configured = bool(
        settings.bootstrap_owner_email
        and settings.bootstrap_owner_password
        and settings.bootstrap_owner_password.get_secret_value()
    )
    return {
        "initialized": bool(user_count),
        "bootstrap_configured": configured,
        "needs_bootstrap": not bool(user_count) and not configured,
    }


@app.get("/readyz")
async def readyz(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(select(1))
        redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        try:
            await redis.ping()
        finally:
            await redis.aclose()
    except Exception:
        raise error(503, "DEPENDENCY_UNAVAILABLE", "Database atau antrean belum siap.") from None
    return {"status": "ready", "database": "ok", "redis": "ok"}


@app.get("/api/v1/telegram/users")
async def list_telegram_users(_: User = Depends(require_role(Role.owner)), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TelegramUser).order_by(TelegramUser.display_name))
    return [{"telegram_user_id": item.telegram_user_id, "display_name": item.display_name, "role": item.role.value, "is_active": item.is_active} for item in result.scalars().all()]


@app.post("/api/v1/telegram/users", status_code=201)
async def add_telegram_user(
    data: TelegramUserInput,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    if await db.get(TelegramUser, data.telegram_user_id):
        raise error(409, "TELEGRAM_USER_EXISTS", "Telegram user ID sudah terdaftar.")
    entry = TelegramUser(telegram_user_id=data.telegram_user_id, display_name=data.display_name, role=data.role, is_active=True)
    db.add(entry)
    _audit(db, actor_type="user", actor_id=str(user.id), action="telegram.user.created", target_type="telegram_user", target_id=str(data.telegram_user_id), metadata={"role": data.role.value})
    await db.commit()
    return {"telegram_user_id": entry.telegram_user_id, "display_name": entry.display_name, "role": entry.role.value, "is_active": entry.is_active}


@app.patch("/api/v1/telegram/users/{telegram_user_id}")
async def update_telegram_user(
    telegram_user_id: int,
    data: dict,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    entry = await db.get(TelegramUser, telegram_user_id)
    if not entry:
        raise error(404, "TELEGRAM_USER_NOT_FOUND", "Telegram user tidak ditemukan.")
    if "display_name" in data:
        entry.display_name = str(data["display_name"])[:100]
    if "role" in data:
        try:
            entry.role = Role(data["role"])
        except ValueError:
            raise error(422, "ROLE_INVALID", "Peran Telegram tidak valid.") from None
    if "is_active" in data:
        entry.is_active = bool(data["is_active"])
    _audit(db, actor_type="user", actor_id=str(user.id), action="telegram.user.updated", target_type="telegram_user", target_id=str(telegram_user_id))
    await db.commit()
    return {"telegram_user_id": entry.telegram_user_id, "display_name": entry.display_name, "role": entry.role.value, "is_active": entry.is_active}


@app.post("/api/v1/auth/login")
async def login(data: LoginInput, response: Response, request: Request, db: AsyncSession = Depends(get_db)):
    ip_address = request.client.host if request.client else "unknown"
    await enforce_rate_limit(f"rate:login:ip:{ip_address}", 5, 60)
    await enforce_rate_limit(f"rate:login:email:{data.email.lower()}", 10, 3600)
    result = await db.execute(select(User).where(func.lower(User.email) == data.email.lower()))
    user = result.scalar_one_or_none()
    now = utcnow()
    if user and user.locked_until and as_utc(user.locked_until) > now:
        raise error(429, "ACCOUNT_LOCKED", "Akun dikunci sementara. Coba kembali nanti.")
    try:
        valid = user is not None and user.is_active and password_hasher.verify(user.password_hash, data.password)
    except VerifyMismatchError:
        valid = False
    except Exception:
        valid = False
    if not valid:
        if user:
            if user.last_failed_login_at and as_utc(user.last_failed_login_at) <= now - timedelta(minutes=15):
                user.failed_login_count = 0
            user.failed_login_count += 1
            user.last_failed_login_at = now
            if user.failed_login_count >= 5:
                user.failed_login_count = 0
                user.locked_until = now + timedelta(minutes=15)
            _audit(db, actor_type="user", actor_id=str(user.id), action="auth.login.failure", target_type="user", target_id=str(user.id), result="failure", metadata={"ip": ip_address})
            await db.commit()
        raise error(401, "LOGIN_FAILED", "Email atau password tidak sesuai.")

    user.failed_login_count = 0
    user.locked_until = None
    raw_token = secrets.token_urlsafe(48)
    requires_totp = user.role == Role.owner and user.totp_enabled
    csrf_token = "" if requires_totp else secrets.token_urlsafe(32)
    db.add(
        DashboardSession(
            user_id=user.id,
            token_hash=sha256_hex(raw_token),
            csrf_hash=sha256_hex(csrf_token) if csrf_token else "",
            expires_at=now + (timedelta(minutes=5) if requires_totp else timedelta(hours=settings.session_absolute_hours)),
            pending_totp=requires_totp,
        )
    )
    _audit(db, actor_type="user", actor_id=str(user.id), action="auth.login.success", target_type="user", target_id=str(user.id), metadata={"ip": ip_address})
    await db.commit()
    response.set_cookie(
        settings.session_cookie_name,
        raw_token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        max_age=settings.session_absolute_hours * 3600,
        path="/",
    )
    if requires_totp:
        return {"requires_totp": True}
    return {"user": {"id": str(user.id), "email": user.email, "role": user.role.value}, "csrf_token": csrf_token}


@app.post("/api/v1/auth/totp/verify")
async def verify_login_totp(data: TOTPInput, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    raw_token = request.cookies.get(settings.session_cookie_name, "")
    session = await db.scalar(select(DashboardSession).where(DashboardSession.token_hash == sha256_hex(raw_token), DashboardSession.pending_totp.is_(True)))
    if not session or as_utc(session.expires_at) <= utcnow():
        raise error(401, "TOTP_CHALLENGE_EXPIRED", "Sesi verifikasi sudah berakhir. Silakan masuk kembali.")
    user = await db.get(User, session.user_id)
    if not user or not user.totp_enabled or not user.totp_secret_enc:
        raise error(401, "TOTP_NOT_CONFIGURED", "Autentikasi dua faktor belum dikonfigurasi.")
    try:
        secret = decrypt_secret(user.totp_secret_enc, table="users.totp_secret", record_id=str(user.id))
    except Exception:
        raise error(503, "TOTP_SECRET_UNAVAILABLE", "Secret autentikasi dua faktor tidak dapat dibuka.") from None
    if not pyotp.TOTP(secret).verify(data.code, valid_window=1):
        raise error(401, "TOTP_INVALID", "Kode autentikasi tidak valid.")
    csrf_token = secrets.token_urlsafe(32)
    session.pending_totp = False
    session.csrf_hash = sha256_hex(csrf_token)
    session.expires_at = utcnow() + timedelta(hours=settings.session_absolute_hours)
    session.last_activity_at = utcnow()
    _audit(db, actor_type="user", actor_id=str(user.id), action="auth.totp.verified", target_type="user", target_id=str(user.id))
    await db.commit()
    response.set_cookie(settings.session_cookie_name, raw_token, httponly=True, secure=settings.session_cookie_secure, samesite="strict", max_age=settings.session_absolute_hours * 3600, path="/")
    return {"user": {"id": str(user.id), "email": user.email, "role": user.role.value}, "csrf_token": csrf_token}


@app.post("/api/v1/auth/totp/setup")
async def setup_totp(
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    if user.totp_enabled:
        raise error(409, "TOTP_ALREADY_ENABLED", "Autentikasi dua faktor sudah aktif.")
    secret = pyotp.random_base32()
    user.totp_secret_enc = encrypt_secret(secret, table="users.totp_secret", record_id=str(user.id))
    await db.commit()
    issuer = f"Pbot:{user.email}"
    return {"secret": secret, "otpauth_uri": pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name="Pbot"), "issuer": issuer}


@app.post("/api/v1/auth/totp/confirm")
async def confirm_totp(
    data: TOTPInput,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    if not user.totp_secret_enc:
        raise error(409, "TOTP_SETUP_REQUIRED", "Mulai konfigurasi dua faktor terlebih dahulu.")
    secret = decrypt_secret(user.totp_secret_enc, table="users.totp_secret", record_id=str(user.id))
    if not pyotp.TOTP(secret).verify(data.code, valid_window=1):
        raise error(401, "TOTP_INVALID", "Kode autentikasi tidak valid.")
    user.totp_enabled = True
    _audit(db, actor_type="user", actor_id=str(user.id), action="auth.totp.enabled", target_type="user", target_id=str(user.id))
    await db.commit()
    return {"enabled": True}


@app.post("/api/v1/auth/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    user: User = Depends(require_csrf),
    db: AsyncSession = Depends(get_db),
):
    raw_token = request.cookies.get(settings.session_cookie_name, "")
    result = await db.execute(select(DashboardSession).where(DashboardSession.token_hash == sha256_hex(raw_token)))
    dashboard_session = result.scalar_one_or_none()
    if dashboard_session:
        dashboard_session.revoked_at = utcnow()
    _audit(db, actor_type="user", actor_id=str(user.id), action="auth.logout", target_type="user", target_id=str(user.id))
    await db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")


@app.get("/api/v1/auth/me")
async def current_user(user: User = Depends(get_current_user)):
    return {"id": str(user.id), "email": user.email, "role": user.role.value}


@app.get("/api/v1/websites")
async def list_websites(_: User = Depends(require_role(Role.viewer)), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Website).order_by(Website.created_at.desc()))
    return [website_response(website) for website in result.scalars().all()]


def website_response(website: Website) -> dict:
    return {
        "id": website.id,
        "name": website.name,
        "base_url": website.base_url,
        "login_url": website.login_url,
        "dashboard_url": website.dashboard_url,
        "adapter_id": website.adapter_id,
        "adapter_version": website.adapter_version,
        "adapter_config": website.adapter_config,
        "allowed_actions": website.allowed_actions,
        "status": website.status.value,
        "last_login_ok_at": website.last_login_ok_at.isoformat() if website.last_login_ok_at else None,
        "last_login_error": website.last_login_error,
        "created_at": website.created_at.isoformat(),
    }


@app.post("/api/v1/websites", status_code=201)
async def create_website(
    data: WebsiteInput,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    for url in (str(data.base_url), str(data.login_url), str(data.dashboard_url)):
        _validate_target(url)
    count = await db.scalar(select(func.count()).select_from(Website).where(Website.status == WebsiteStatus.active))
    if count >= settings.website_limit:
        raise error(409, "WEBSITE_LIMIT_REACHED", "Batas jumlah website aktif telah tercapai.")
    if await db.get(Website, data.id):
        raise error(409, "WEBSITE_ID_EXISTS", "ID website sudah digunakan.")
    if len(set(data.allowed_actions)) != len(data.allowed_actions):
        raise error(422, "INVALID_ACTIONS", "Daftar tindakan tidak boleh berulang.")
    if any(action not in ACTION_POLICIES for action in data.allowed_actions):
        raise error(422, "INVALID_ACTIONS", "Daftar berisi tindakan yang tidak dikenal.")
    website = Website(
        **data.model_dump(exclude={"base_url", "login_url", "dashboard_url"}),
        base_url=str(data.base_url),
        login_url=str(data.login_url),
        dashboard_url=str(data.dashboard_url),
    )
    db.add(website)
    _audit(db, actor_type="user", actor_id=str(user.id), action="website.created", target_type="website", target_id=website.id)
    await db.commit()
    return website_response(website)


@app.get("/api/v1/websites/{website_id}")
async def get_website(website_id: str, _: User = Depends(require_role(Role.viewer)), db: AsyncSession = Depends(get_db)):
    website = await db.get(Website, website_id)
    if not website:
        raise error(404, "WEBSITE_NOT_FOUND", "Website tidak ditemukan.")
    return website_response(website)


@app.put("/api/v1/websites/{website_id}")
async def update_website(
    website_id: str,
    data: WebsiteInput,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    website = await db.get(Website, website_id)
    if not website:
        raise error(404, "WEBSITE_NOT_FOUND", "Website tidak ditemukan.")
    if data.id != website_id:
        raise error(422, "WEBSITE_ID_IMMUTABLE", "ID website tidak dapat diubah.")
    if any(action not in ACTION_POLICIES for action in data.allowed_actions):
        raise error(422, "INVALID_ACTIONS", "Daftar berisi tindakan yang tidak dikenal.")
    for url in (str(data.base_url), str(data.login_url), str(data.dashboard_url)):
        _validate_target(url)
    website.name = data.name
    website.base_url = str(data.base_url)
    website.login_url = str(data.login_url)
    website.dashboard_url = str(data.dashboard_url)
    website.adapter_id = data.adapter_id
    website.adapter_version = data.adapter_version
    website.adapter_config = data.adapter_config
    website.allowed_actions = data.allowed_actions
    _audit(db, actor_type="user", actor_id=str(user.id), action="website.updated", target_type="website", target_id=website.id, metadata={"adapter_id": website.adapter_id})
    await db.commit()
    return website_response(website)


@app.patch("/api/v1/websites/{website_id}/status")
async def set_website_status(
    website_id: str,
    data: dict,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    website = await db.get(Website, website_id)
    if not website:
        raise error(404, "WEBSITE_NOT_FOUND", "Website tidak ditemukan.")
    try:
        new_status = WebsiteStatus(data.get("status"))
    except ValueError:
        raise error(422, "INVALID_STATUS", "Status website tidak valid.") from None
    website.status = new_status
    _audit(db, actor_type="user", actor_id=str(user.id), action="website.status_changed", target_type="website", target_id=website_id, metadata={"status": new_status.value})
    await db.commit()
    return website_response(website)


@app.put("/api/v1/websites/{website_id}/credentials", status_code=204)
async def set_credentials(
    website_id: str,
    data: CredentialInput,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    website = await db.get(Website, website_id)
    if not website:
        raise error(404, "WEBSITE_NOT_FOUND", "Website tidak ditemukan.")
    credential_result = await db.execute(select(WebsiteCredential).where(WebsiteCredential.website_id == website_id))
    credential = credential_result.scalar_one_or_none()
    credential_id = str(credential.id) if credential else str(uuid.uuid4())
    encrypted_username = encrypt_secret(data.username, table="website_credentials.username", record_id=credential_id)
    encrypted_password = encrypt_secret(data.password, table="website_credentials.password", record_id=credential_id)
    if credential:
        credential.username_enc = encrypted_username
        credential.password_enc = encrypted_password
        credential.key_version = settings.encryption_key_version
        credential.rotated_at = utcnow()
        credential.is_active = True
    else:
        db.add(WebsiteCredential(id=uuid.UUID(credential_id), website_id=website_id, username_enc=encrypted_username, password_enc=encrypted_password, key_version=settings.encryption_key_version))
    _audit(db, actor_type="user", actor_id=str(user.id), action="credential.updated", target_type="website", target_id=website_id)
    await db.commit()


@app.post("/api/v1/websites/{website_id}/test-login", status_code=202)
async def test_website_login(
    website_id: str,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.operator)),
    db: AsyncSession = Depends(get_db),
):
    return await create_job(JobInput(website_id=website_id, action="test_login", payload={}), user, user, db)


@app.get("/api/v1/jobs")
async def list_jobs(
    limit: int = 20,
    user: User = Depends(require_role(Role.viewer)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Job).order_by(desc(Job.created_at)).limit(max(1, min(limit, 100))))
    return [job_response(job) for job in result.scalars().all()]


def job_response(job: Job) -> dict:
    return {
        "id": str(job.id),
        "website_id": job.website_id,
        "action": job.action,
        "risk_level": job.risk_level,
        "status": job.status.value,
        "payload": job.payload,
        "attempt_count": job.attempt_count,
        "last_error_code": job.last_error_code,
        "last_error_message": job.last_error_message,
        "created_at": job.created_at.isoformat(),
    }


@app.post("/api/v1/jobs", status_code=202)
async def create_job(
    data: JobInput,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.operator)),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(f"rate:jobs:{user.id}", 30, 60)
    website = await db.get(Website, data.website_id)
    if not website or website.status != WebsiteStatus.active:
        raise error(403, "WEBSITE_NOT_ALLOWED", "Website tidak aktif atau tidak terdaftar.")
    try:
        validated = validate_action(action=data.action, parameters=data.payload, allowed_actions=website.allowed_actions)
    except PolicyViolation as exception:
        raise error(422, str(exception), "Tindakan atau parameter tidak diizinkan.") from None
    key = data.idempotency_key or sha256_hex(f"{user.id}:{data.website_id}:{data.action}:{json.dumps(data.payload, sort_keys=True)}:{int(utcnow().timestamp()) // 60}")
    duplicate_result = await db.execute(select(Job).where(Job.idempotency_key == key, Job.created_at >= utcnow() - timedelta(minutes=10)))
    duplicate = duplicate_result.scalar_one_or_none()
    if duplicate:
        return {"job_id": str(duplicate.id), "status": duplicate.status.value, "duplicate": True}
    job = Job(
        website_id=website.id,
        action=validated.action,
        risk_level=validated.risk_level,
        requested_by=user.id,
        payload=validated.parameters,
        payload_hash=sha256_hex(json.dumps(validated.parameters, sort_keys=True, separators=(",", ":"))),
        idempotency_key=key,
        adapter_version=website.adapter_version,
        status=JobStatus.queued,
    )
    db.add(job)
    _audit(db, actor_type="user", actor_id=str(user.id), action="job.created", target_type="job", target_id=str(job.id), metadata={"website_id": website.id, "action": job.action, "risk_level": job.risk_level})
    await db.commit()
    return {"job_id": str(job.id), "status": job.status.value, "created_at": job.created_at.isoformat(), "approval_required": validated.requires_approval}


@app.get("/api/v1/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, _: User = Depends(require_role(Role.viewer)), db: AsyncSession = Depends(get_db)):
    job = await db.get(Job, job_id)
    if not job:
        raise error(404, "JOB_NOT_FOUND", "Job tidak ditemukan.")
    return job_response(job)


@app.post("/api/v1/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: uuid.UUID,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.operator)),
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(Job, job_id)
    if not job:
        raise error(404, "JOB_NOT_FOUND", "Job tidak ditemukan.")
    if job.status in {JobStatus.queued, JobStatus.awaiting_approval}:
        job.status = JobStatus.cancelled
        job.finished_at = utcnow()
        approval = await db.scalar(select(Approval).where(Approval.job_id == job.id, Approval.status == ApprovalStatus.pending))
        if approval:
            approval.status = ApprovalStatus.superseded
    elif job.status in {JobStatus.running, JobStatus.executing}:
        job.cancel_requested = True
    else:
        raise error(409, "JOB_NOT_CANCELLABLE", "Job tidak dapat dibatalkan pada status ini.")
    _audit(db, actor_type="user", actor_id=str(user.id), action="job.cancel_requested", target_type="job", target_id=str(job.id))
    await db.commit()
    return job_response(job)


@app.get("/api/v1/approvals")
async def list_approvals(_: User = Depends(require_role(Role.operator)), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Approval).where(Approval.status == ApprovalStatus.pending).order_by(Approval.expires_at))
    approvals = []
    for approval in result.scalars().all():
        job = await db.get(Job, approval.job_id)
        website = await db.get(Website, job.website_id) if job else None
        approvals.append({"id": str(approval.id), "job_id": str(approval.job_id), "website_name": website.name if website else "", "action": job.action if job else "", "preview_summary": approval.preview_summary, "approval_hash": approval.approval_hash, "expires_at": approval.expires_at.isoformat()})
    return approvals


async def decide_approval(
    approval_id: uuid.UUID,
    data: DecisionInput,
    decision: ApprovalStatus,
    user: User,
    db: AsyncSession,
):
    approval = await db.get(Approval, approval_id)
    if not approval:
        raise error(404, "APPROVAL_NOT_FOUND", "Persetujuan tidak ditemukan.")
    if approval.status != ApprovalStatus.pending:
        raise error(409, "APPROVAL_ALREADY_DECIDED", "Persetujuan sudah diputuskan.")
    now = utcnow()
    job = await db.get(Job, approval.job_id)
    website = await db.get(Website, job.website_id) if job else None
    if not job or not website:
        raise error(409, "APPROVAL_INVALID", "Data pratinjau tidak lagi tersedia.")
    if job.status != JobStatus.awaiting_approval:
        approval.status = ApprovalStatus.superseded
        await db.commit()
        raise error(409, "JOB_NOT_AWAITING_APPROVAL", "Job tidak lagi menunggu persetujuan.")
    if as_utc(approval.expires_at) <= now:
        approval.status = ApprovalStatus.expired
        job.status = JobStatus.expired
        await db.commit()
        raise error(410, "APPROVAL_EXPIRED", "Batas waktu persetujuan telah lewat.")
    latest_hash = await _approval_hash(job, website, approval.preview_summary)
    if not hmac.compare_digest(latest_hash, approval.approval_hash) or not hmac.compare_digest(data.approval_hash, approval.approval_hash):
        raise error(409, "PREVIEW_CHANGED", "Pratinjau berubah. Buat pratinjau baru sebelum menyetujui.")
    approval.status = decision
    approval.decided_by_user = user.id
    approval.decided_at = now
    approval.decision_reason = data.reason
    job.status = JobStatus.executing if decision == ApprovalStatus.approved else JobStatus.rejected
    if decision == ApprovalStatus.rejected:
        job.finished_at = now
    _audit(db, actor_type="user", actor_id=str(user.id), action=f"approval.{decision.value}", target_type="approval", target_id=str(approval.id), metadata={"job_id": str(job.id)})
    await db.commit()
    return {"approval_id": str(approval.id), "status": approval.status.value, "job_status": job.status.value}


@app.post("/api/v1/approvals/{approval_id}/approve")
async def approve(
    approval_id: uuid.UUID,
    data: DecisionInput,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.operator)),
    db: AsyncSession = Depends(get_db),
):
    return await decide_approval(approval_id, data, ApprovalStatus.approved, user, db)


@app.post("/api/v1/approvals/{approval_id}/reject")
async def reject(
    approval_id: uuid.UUID,
    data: DecisionInput,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.operator)),
    db: AsyncSession = Depends(get_db),
):
    return await decide_approval(approval_id, data, ApprovalStatus.rejected, user, db)


def schedule_response(schedule: Schedule) -> dict:
    return {"id": str(schedule.id), "name": schedule.name, "website_id": schedule.website_id, "action": schedule.action, "payload": schedule.payload, "cron_expr": schedule.cron_expr, "timezone": schedule.timezone, "is_active": schedule.is_active, "misfire_policy": schedule.misfire_policy, "next_run_at": schedule.next_run_at.isoformat(), "last_run_at": schedule.last_run_at.isoformat() if schedule.last_run_at else None}


def validate_schedule(data: ScheduleInput, website: Website) -> tuple[dict, datetime]:
    try:
        zone = ZoneInfo(data.timezone)
    except ZoneInfoNotFoundError:
        raise error(422, "TIMEZONE_INVALID", "Zona waktu IANA tidak dikenal.") from None
    try:
        validated = validate_action(action=data.action, parameters=data.payload, allowed_actions=website.allowed_actions)
    except PolicyViolation as exception:
        raise error(422, str(exception), "Tindakan atau parameter tidak diizinkan.") from None
    if validated.risk_level != "read":
        raise error(422, "SCHEDULE_WRITE_FORBIDDEN", "Jadwal hanya boleh menjalankan tindakan baca.")
    if not croniter.is_valid(data.cron_expr):
        raise error(422, "CRON_INVALID", "Ekspresi cron tidak valid.")
    local_now = utcnow().astimezone(zone)
    next_run = croniter(data.cron_expr, local_now).get_next(datetime)
    return validated.parameters, next_run.astimezone(timezone.utc)


@app.get("/api/v1/schedules")
async def list_schedules(_: User = Depends(require_role(Role.viewer)), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Schedule).order_by(Schedule.next_run_at))
    return [schedule_response(item) for item in result.scalars().all()]


@app.post("/api/v1/schedules", status_code=201)
async def create_schedule(
    data: ScheduleInput,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    website = await db.get(Website, data.website_id)
    if not website or website.status != WebsiteStatus.active:
        raise error(403, "WEBSITE_NOT_ALLOWED", "Website tidak aktif atau tidak terdaftar.")
    parameters, next_run = validate_schedule(data, website)
    if data.misfire_policy not in {"run_once_latest", "skip"}:
        raise error(422, "MISFIRE_POLICY_INVALID", "Kebijakan jadwal terlewat tidak valid.")
    schedule = Schedule(name=data.name, website_id=website.id, action=data.action, payload=parameters, cron_expr=data.cron_expr, timezone=data.timezone, is_active=data.is_active, misfire_policy=data.misfire_policy, next_run_at=next_run)
    db.add(schedule)
    _audit(db, actor_type="user", actor_id=str(user.id), action="schedule.created", target_type="schedule", target_id=str(schedule.id))
    await db.commit()
    return schedule_response(schedule)


@app.put("/api/v1/schedules/{schedule_id}")
async def update_schedule(
    schedule_id: uuid.UUID,
    data: ScheduleInput,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise error(404, "SCHEDULE_NOT_FOUND", "Jadwal tidak ditemukan.")
    website = await db.get(Website, data.website_id)
    if not website or website.status != WebsiteStatus.active:
        raise error(403, "WEBSITE_NOT_ALLOWED", "Website tidak aktif atau tidak terdaftar.")
    parameters, next_run = validate_schedule(data, website)
    if data.misfire_policy not in {"run_once_latest", "skip"}:
        raise error(422, "MISFIRE_POLICY_INVALID", "Kebijakan jadwal terlewat tidak valid.")
    schedule.name, schedule.website_id, schedule.action = data.name, website.id, data.action
    schedule.payload, schedule.cron_expr, schedule.timezone = parameters, data.cron_expr, data.timezone
    schedule.is_active, schedule.misfire_policy, schedule.next_run_at = data.is_active, data.misfire_policy, next_run
    _audit(db, actor_type="user", actor_id=str(user.id), action="schedule.updated", target_type="schedule", target_id=str(schedule.id))
    await db.commit()
    return schedule_response(schedule)


@app.delete("/api/v1/schedules/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: uuid.UUID,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    schedule = await db.get(Schedule, schedule_id)
    if not schedule:
        raise error(404, "SCHEDULE_NOT_FOUND", "Jadwal tidak ditemukan.")
    schedule.is_active = False
    _audit(db, actor_type="user", actor_id=str(user.id), action="schedule.disabled", target_type="schedule", target_id=str(schedule.id))
    await db.commit()


def ai_config_response(config: AIConfig | None) -> dict:
    if not config:
        return {"configured": False, "provider": "gemini", "api_key_masked": None, "primary_model_id": None, "fallback_model_id": None, "temperature": 0.1, "max_output_tokens": 1024, "timeout_seconds": 30, "mode": "structured_only", "allow_ai_write_proposals": False, "system_instruction": "", "status": "unverified"}
    return {"configured": bool(config.api_key_enc), "provider": config.provider, "api_key_masked": f"AIza••••{config.api_key_last4}" if config.api_key_last4 else None, "primary_model_id": config.primary_model_id, "fallback_model_id": config.fallback_model_id, "temperature": config.temperature, "max_output_tokens": config.max_output_tokens, "timeout_seconds": config.timeout_seconds, "mode": config.mode, "allow_ai_write_proposals": config.allow_ai_write_proposals, "system_instruction": config.system_instruction, "status": config.status}


async def ai_secret(config: AIConfig) -> str:
    if not config.api_key_enc:
        raise error(409, "AI_KEY_NOT_CONFIGURED", "API key Gemini belum dikonfigurasi.")
    try:
        return decrypt_secret(config.api_key_enc, table="ai_configs.api_key", record_id=str(config.id))
    except Exception:
        raise error(503, "AI_KEY_UNAVAILABLE", "Kunci AI tidak dapat dibuka. Periksa versi kunci enkripsi.") from None


@app.get("/api/v1/ai/config")
async def get_ai_config(_: User = Depends(require_role(Role.owner)), db: AsyncSession = Depends(get_db)):
    config = await db.scalar(select(AIConfig).order_by(AIConfig.updated_at.desc()).limit(1))
    return ai_config_response(config)


@app.put("/api/v1/ai/config")
async def update_ai_config(
    data: AIConfigInput,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    config = await db.scalar(select(AIConfig).order_by(AIConfig.updated_at.desc()).limit(1))
    key = data.api_key.get_secret_value() if data.api_key else None
    if not config and not key:
        raise error(422, "AI_KEY_REQUIRED", "API key Gemini diperlukan untuk konfigurasi awal.")
    if key and not key.startswith("AIza"):
        raise error(422, "AI_KEY_INVALID", "Format API key Gemini tidak valid.")
    if key and config is None:
        config = AIConfig()
        db.add(config)
        await db.flush()
    effective_key = key or (await ai_secret(config) if config else None)
    try:
        available_models = await asyncio.to_thread(ai_list_models, effective_key, force_refresh=True)
    except AIGatewayError as exception:
        raise error(502, exception.code, "Daftar model Gemini tidak dapat diverifikasi.") from None
    model_ids = {item["id"] for item in available_models}
    if data.primary_model_id not in model_ids or (data.fallback_model_id and data.fallback_model_id not in model_ids):
        raise error(422, "AI_MODEL_UNAVAILABLE", "Model utama dan cadangan harus berasal dari katalog Gemini yang valid.")
    if key:
        config.api_key_enc = encrypt_secret(key, table="ai_configs.api_key", record_id=str(config.id))
        config.api_key_last4 = key[-4:]
        config.status = "unverified"
    elif config is None:
        raise error(422, "AI_KEY_REQUIRED", "API key Gemini diperlukan.")
    config.primary_model_id = data.primary_model_id
    config.fallback_model_id = data.fallback_model_id
    config.temperature = data.temperature
    config.max_output_tokens = data.max_output_tokens
    config.timeout_seconds = data.timeout_seconds
    config.mode = data.mode
    config.allow_ai_write_proposals = data.allow_ai_write_proposals
    config.system_instruction = data.system_instruction
    _audit(db, actor_type="user", actor_id=str(user.id), action="ai.config.updated", target_type="ai_config", target_id=str(config.id), metadata={"model_id": config.primary_model_id, "mode": config.mode})
    await db.commit()
    return ai_config_response(config)


@app.get("/api/v1/ai/models")
async def get_ai_models(_: User = Depends(require_role(Role.owner)), db: AsyncSession = Depends(get_db)):
    config = await db.scalar(select(AIConfig).order_by(AIConfig.updated_at.desc()).limit(1))
    if not config:
        raise error(409, "AI_KEY_NOT_CONFIGURED", "API key Gemini belum dikonfigurasi.")
    key = await ai_secret(config)
    try:
        models = await asyncio.to_thread(ai_list_models, key)
    except AIGatewayError as exception:
        raise error(502, exception.code, "Daftar model Gemini tidak dapat dimuat.") from None
    return {"provider": "gemini", "fetched_at": utcnow().isoformat(), "models": models}


@app.post("/api/v1/ai/models/discover")
async def discover_ai_models(
    data: AIKeyInput,
    _: User = Depends(require_csrf),
    __: User = Depends(require_role(Role.owner)),
):
    key = data.api_key.get_secret_value()
    if not key.startswith("AIza"):
        raise error(422, "AI_KEY_INVALID", "Format API key Gemini tidak valid.")
    try:
        models = await asyncio.to_thread(ai_list_models, key, force_refresh=True)
    except AIGatewayError as exception:
        raise error(502, exception.code, "Daftar model Gemini tidak dapat dimuat.") from None
    return {"provider": "gemini", "fetched_at": utcnow().isoformat(), "models": models}


async def run_ai_test(config: AIConfig) -> None:
    key = await ai_secret(config)
    try:
        response = await asyncio.to_thread(
            ai_interpret,
            api_key=key,
            model_id=config.primary_model_id,
            instruction="Ambil data dari site_00.",
            system_instruction="Kembalikan usulan JSON valid.",
            websites=[{"website_id": "site_00", "name": "Website uji", "allowed_actions": ["extract_data"]}],
            temperature=0,
            max_output_tokens=256,
            timeout_seconds=config.timeout_seconds,
        )
    except AIGatewayError as exception:
        raise error(502, exception.code, "Uji koneksi Gemini gagal.") from None
    if response.intent not in {"execute_job", "clarify", "reject"}:
        raise error(502, "AI_TEST_INVALID", "Respons uji Gemini tidak sesuai skema.")


@app.post("/api/v1/ai/test")
async def test_ai_connection(
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(f"rate:ai-test:{user.id}", 20, 60)
    config = await db.scalar(select(AIConfig).order_by(AIConfig.updated_at.desc()).limit(1))
    if not config:
        raise error(409, "AI_KEY_NOT_CONFIGURED", "API key Gemini belum dikonfigurasi.")
    await run_ai_test(config)
    config.status = "valid"
    _audit(db, actor_type="user", actor_id=str(user.id), action="ai.test.success", target_type="ai_config", target_id=str(config.id))
    await db.commit()
    return {"status": "valid", "message": "Koneksi Gemini dan structured output berhasil."}


@app.post("/api/v1/ai/interpret", status_code=202)
async def interpret_ai_request(
    data: AIInterpretInput,
    user: User = Depends(require_csrf),
    _: User = Depends(require_role(Role.operator)),
    db: AsyncSession = Depends(get_db),
):
    await enforce_rate_limit(f"rate:ai-interpret:{user.id}", 20, 60)
    config = await db.scalar(select(AIConfig).order_by(AIConfig.updated_at.desc()).limit(1))
    if not config:
        raise error(409, "AI_KEY_NOT_CONFIGURED", "AI belum dikonfigurasi oleh owner.")
    key = await ai_secret(config)
    website_result = await db.execute(select(Website).where(Website.status == WebsiteStatus.active))
    websites = website_result.scalars().all()
    safe_websites = [{"website_id": site.id, "name": site.name, "allowed_actions": site.allowed_actions} for site in websites]
    try:
        proposal = await asyncio.to_thread(
            ai_interpret,
            api_key=key,
            model_id=config.primary_model_id,
            instruction=data.instruction,
            system_instruction=config.system_instruction,
            websites=safe_websites,
            temperature=config.temperature,
            max_output_tokens=config.max_output_tokens,
            timeout_seconds=config.timeout_seconds,
        )
    except AIGatewayError as exception:
        if config.fallback_model_id and exception.code in {"MODEL_REQUEST_FAILED", "EMPTY_RESPONSE"}:
            try:
                response = await asyncio.to_thread(
                    ai_interpret,
                    api_key=key,
                    model_id=config.fallback_model_id,
                    instruction=data.instruction,
                    system_instruction=config.system_instruction,
                    websites=safe_websites,
                    temperature=config.temperature,
                    max_output_tokens=config.max_output_tokens,
                    timeout_seconds=config.timeout_seconds,
                )
            except AIGatewayError:
                response = None
            if response:
                proposal = response
            else:
                _audit(db, actor_type="ai", actor_id=config.primary_model_id or "unknown", action="ai.request.failed", target_type="ai_config", target_id=str(config.id), result="failure", metadata={"error_code": exception.code})
                await db.commit()
                raise error(502, exception.code, "AI tidak dapat memproses instruksi ini.") from None
        else:
            _audit(db, actor_type="ai", actor_id=config.primary_model_id or "unknown", action="ai.request.failed", target_type="ai_config", target_id=str(config.id), result="failure", metadata={"error_code": exception.code})
            await db.commit()
            raise error(502, exception.code, "AI tidak dapat memproses instruksi ini.") from None
    if proposal.intent != "execute_job" or not proposal.website_id or not proposal.action:
        return {"intent": proposal.intent, "clarification_question": proposal.clarification_question, "user_summary": proposal.user_summary}
    website = await db.get(Website, proposal.website_id)
    if not website or website.status != WebsiteStatus.active:
        _audit(db, actor_type="ai", actor_id=config.primary_model_id or "unknown", action="ai.policy_violation", target_type="ai_config", target_id=str(config.id), result="denied", metadata={"reason": "website_not_allowed"})
        await db.commit()
        raise error(403, "WEBSITE_NOT_ALLOWED", "AI memilih website yang tidak terdaftar atau nonaktif.")
    try:
        validated = validate_action(action=proposal.action, parameters=proposal.parameters, allowed_actions=website.allowed_actions, allow_ai_write_proposals=config.allow_ai_write_proposals, source="ai")
    except PolicyViolation as exception:
        _audit(db, actor_type="ai", actor_id=config.primary_model_id or "unknown", action="ai.policy_violation", target_type="website", target_id=website.id, result="denied", metadata={"reason": str(exception), "action": proposal.action})
        await db.commit()
        raise error(403, str(exception), "Usulan AI tidak lolos kebijakan server.") from None
    key_hash = data.idempotency_key or sha256_hex(f"ai:{user.id}:{data.instruction}:{int(utcnow().timestamp()) // 60}")
    duplicate = await db.scalar(select(Job).where(Job.idempotency_key == key_hash, Job.created_at >= utcnow() - timedelta(minutes=10)))
    if duplicate:
        return {"job_id": str(duplicate.id), "status": duplicate.status.value, "duplicate": True}
    job = Job(website_id=website.id, action=validated.action, risk_level=validated.risk_level, source="ai", requested_by=user.id, payload=validated.parameters, payload_hash=sha256_hex(json.dumps(validated.parameters, sort_keys=True, separators=(",", ":"))), idempotency_key=key_hash, adapter_version=website.adapter_version, status=JobStatus.queued)
    db.add(job)
    _audit(db, actor_type="ai", actor_id=config.primary_model_id or "unknown", action="job.created", target_type="job", target_id=str(job.id), metadata={"website_id": website.id, "action": job.action, "risk_level": job.risk_level})
    await db.commit()
    return {"intent": "execute_job", "job_id": str(job.id), "status": job.status.value, "risk_level": job.risk_level, "requires_approval": validated.requires_approval, "user_summary": proposal.user_summary}


@app.get("/api/v1/audit-logs")
async def list_audit_logs(
    limit: int = 50,
    _: User = Depends(require_role(Role.owner)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AuditLog).order_by(desc(AuditLog.occurred_at)).limit(max(1, min(limit, 200))))
    return [{"id": item.id, "occurred_at": item.occurred_at.isoformat(), "actor_type": item.actor_type, "actor_id": item.actor_id, "action": item.action, "target_type": item.target_type, "target_id": item.target_id, "result": item.result, "metadata": item.metadata_json} for item in result.scalars().all()]


def artifact_signature(artifact_id: uuid.UUID, expires: int) -> str:
    message = f"{artifact_id}:{expires}".encode()
    key = settings.session_secret.get_secret_value().encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


@app.get("/api/v1/artifacts/{artifact_id}/url")
async def get_artifact_url(
    artifact_id: uuid.UUID,
    _: User = Depends(require_role(Role.viewer)),
    db: AsyncSession = Depends(get_db),
):
    artifact = await db.get(Artifact, artifact_id)
    if not artifact:
        raise error(404, "ARTIFACT_NOT_FOUND", "Artefak tidak ditemukan.")
    if not artifact.pinned and as_utc(artifact.expires_at) <= utcnow():
        raise error(410, "ARTIFACT_EXPIRED", "Artefak sudah melewati masa retensi.")
    expires = int(time.time()) + settings.artifact_signed_url_ttl
    signature = artifact_signature(artifact.id, expires)
    return {"url": f"/api/v1/artifact-download/{artifact.id}?expires={expires}&signature={signature}", "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat()}


@app.get("/api/v1/artifact-download/{artifact_id}")
async def download_artifact(
    artifact_id: uuid.UUID,
    expires: int,
    signature: str,
    db: AsyncSession = Depends(get_db),
):
    now = int(time.time())
    if expires < now or expires > now + settings.artifact_signed_url_ttl + 30:
        raise error(403, "ARTIFACT_URL_EXPIRED", "URL artefak tidak berlaku.")
    expected = artifact_signature(artifact_id, expires)
    if not hmac.compare_digest(expected, signature):
        raise error(403, "ARTIFACT_SIGNATURE_INVALID", "URL artefak tidak valid.")
    artifact = await db.get(Artifact, artifact_id)
    if not artifact or (not artifact.pinned and as_utc(artifact.expires_at) <= utcnow()):
        raise error(404, "ARTIFACT_NOT_FOUND", "Artefak tidak ditemukan.")
    root = Path(settings.artifact_dir).resolve()
    path = (root / artifact.storage_key).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise error(404, "ARTIFACT_NOT_FOUND", "Artefak tidak ditemukan.")
    media_type = "image/png" if artifact.kind == "screenshot" else "application/json"
    return FileResponse(path, media_type=media_type, filename=path.name)