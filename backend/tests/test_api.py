import json
from datetime import timedelta

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import main
from app import bootstrap
from app.crypto import sha256_hex
from app.db import get_db
from app.models import Approval, ApprovalStatus, Base, Job, JobStatus, Role, TelegramUser, User, Website, WebsiteStatus, utcnow
from app.settings import Settings
from app.telegram import create_telegram_job


@pytest_asyncio.fixture
async def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        db.add(User(email="owner@example.test", password_hash=PasswordHasher().hash("a-secure-test-password"), role=Role.owner, is_active=True))
        await db.commit()

    async def override_get_db():
        async with session_factory() as db:
            yield db

    def accept_test_url(_url):
        return None

    async def allow_test_rate_limit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main, "_validate_target", accept_test_url)
    monkeypatch.setattr(main, "enforce_rate_limit", allow_test_rate_limit)
    main.app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://test") as http:
        yield http, session_factory
    main.app.dependency_overrides.clear()
    await engine.dispose()


async def login(client):
    response = await client.post("/api/v1/auth/login", json={"email": "owner@example.test", "password": "a-secure-test-password"})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


async def register_website(client, site_id="site_01"):
    return await client.post("/api/v1/websites", json={
        "id": site_id, "name": "Portal", "base_url": "https://portal.example.test",
        "login_url": "https://portal.example.test/login", "dashboard_url": "https://portal.example.test/dashboard",
        "adapter_id": "configured_selectors", "allowed_actions": ["extract_data", "fill_form"],
    })


@pytest.mark.asyncio
async def test_session_login_cookie_and_owner_only_mutation(client):
    http, _ = client
    await login(http)
    assert http.cookies.get(main.settings.session_cookie_name)
    assert (await http.get("/api/v1/auth/me")).json()["role"] == "owner"
    response = await register_website(http)
    assert response.status_code == 201
    assert "password" not in response.text.lower()


@pytest.mark.asyncio
async def test_job_policy_derives_risk_and_deduplicates(client):
    http, _ = client
    await login(http)
    await register_website(http)
    read_request = {"website_id": "site_01", "action": "extract_data", "payload": {}, "idempotency_key": "read-once"}
    first_read = await http.post("/api/v1/jobs", json=read_request)
    duplicate_read = await http.post("/api/v1/jobs", json=read_request)
    assert first_read.status_code == 202
    assert duplicate_read.json()["duplicate"] is True
    assert first_read.json()["job_id"] == duplicate_read.json()["job_id"]

    write = await http.post("/api/v1/jobs", json={
        "website_id": "site_01", "action": "fill_form",
        "payload": {"template": "profile", "draft_id": "draft-1", "values": {"display_name": "Sari"}},
        "idempotency_key": "write-once",
    })
    assert write.status_code == 202
    assert write.json()["approval_required"] is True
    invalid = await http.post("/api/v1/jobs", json={"website_id": "site_01", "action": "submit_form", "payload": {}})
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_approval_is_bound_to_preview_and_single_use(client):
    http, session_factory = client
    await login(http)
    async with session_factory() as db:
        website = Website(id="site_02", name="Portal", base_url="https://portal.example.test", login_url="https://portal.example.test/login", dashboard_url="https://portal.example.test/dashboard", adapter_id="configured_selectors", adapter_version="1.0.0", adapter_config={}, allowed_actions=["fill_form"], status=WebsiteStatus.active)
        db.add(website)
        await db.flush()
        payload = {"template": "profile", "draft_id": "draft-2", "values": {"name": "Sari"}}
        owner_id = await db.scalar(select(User.id).limit(1))
        job = Job(website_id=website.id, action="fill_form", risk_level="write_low", requested_by=owner_id, payload=payload, payload_hash=sha256_hex(json.dumps(payload, sort_keys=True)), idempotency_key="approval-job", adapter_version=website.adapter_version, status=JobStatus.awaiting_approval)
        db.add(job)
        await db.flush()
        preview = {"changes": {"name": {"old": "Ayu", "new": "Sari"}}}
        approval_hash = await main._approval_hash(job, website, preview)
        approval = Approval(job_id=job.id, approval_hash=approval_hash, token_hash="a" * 64, preview_summary=preview, status=ApprovalStatus.pending, expires_at=utcnow() + timedelta(minutes=10))
        db.add(approval)
        await db.commit()
        approval_id = approval.id
        job_id = job.id

    decision = {"approval_hash": approval_hash}
    response = await http.post(f"/api/v1/approvals/{approval_id}/approve", json=decision)
    assert response.status_code == 200
    assert response.json()["job_status"] == "executing"
    replay = await http.post(f"/api/v1/approvals/{approval_id}/approve", json=decision)
    assert replay.status_code == 409
    assert (await http.get(f"/api/v1/jobs/{job_id}")).json()["status"] == "executing"


@pytest.mark.asyncio
async def test_viewer_cannot_mutate_or_create_job(client):
    http, session_factory = client
    async with session_factory() as db:
        owner = await db.scalar(select(User).limit(1))
        owner.role = Role.viewer
        await db.commit()
    await login(http)
    assert (await http.post("/api/v1/websites", json={
        "id": "site_01", "name": "Portal", "base_url": "https://portal.example.test",
        "login_url": "https://portal.example.test/login", "dashboard_url": "https://portal.example.test/dashboard",
        "adapter_id": "configured_selectors", "allowed_actions": ["extract_data"],
    })).status_code == 403


@pytest.mark.asyncio
async def test_cancelled_job_cannot_be_approved(client):
    http, session_factory = client
    await login(http)
    async with session_factory() as db:
        website = Website(id="site_03", name="Portal", base_url="https://portal.example.test", login_url="https://portal.example.test/login", dashboard_url="https://portal.example.test/dashboard", adapter_id="configured_selectors", adapter_version="1.0.0", adapter_config={}, allowed_actions=["fill_form"], status=WebsiteStatus.active)
        db.add(website)
        await db.flush()
        payload = {"template": "profile", "draft_id": "draft-3", "values": {"name": "Sari"}}
        owner_id = await db.scalar(select(User.id).limit(1))
        job = Job(website_id=website.id, action="fill_form", risk_level="write_low", requested_by=owner_id, payload=payload, payload_hash=sha256_hex(json.dumps(payload, sort_keys=True)), idempotency_key="cancelled-job", adapter_version=website.adapter_version, status=JobStatus.awaiting_approval)
        db.add(job)
        await db.flush()
        preview = {"changes": {"name": {"old": "Ayu", "new": "Sari"}}}
        approval_hash = await main._approval_hash(job, website, preview)
        approval = Approval(job_id=job.id, approval_hash=approval_hash, token_hash="b" * 64, preview_summary=preview, status=ApprovalStatus.pending, expires_at=utcnow() + timedelta(minutes=10))
        db.add(approval)
        await db.commit()
        approval_id, job_id = approval.id, job.id

    cancelled = await http.post(f"/api/v1/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    decision = await http.post(f"/api/v1/approvals/{approval_id}/approve", json={"approval_hash": approval_hash})
    assert decision.status_code == 409
    assert decision.json()["detail"]["code"] == "APPROVAL_ALREADY_DECIDED"


@pytest.mark.asyncio
async def test_bootstrap_owner_can_complete_first_login(client, monkeypatch):
    http, session_factory = client
    async with session_factory() as db:
        await db.execute(delete(User))
        await db.commit()
    monkeypatch.setattr(bootstrap, "SessionLocal", session_factory)
    monkeypatch.setattr(
        bootstrap,
        "get_settings",
        lambda: Settings(
            bootstrap_owner_email="first.owner@example.test",
            bootstrap_owner_password=SecretStr("secure-initial-owner-password"),
        ),
    )
    assert await bootstrap.ensure_bootstrap_owner() is True
    response = await http.post(
        "/api/v1/auth/login",
        json={"email": "first.owner@example.test", "password": "secure-initial-owner-password"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "owner"


@pytest.mark.asyncio
async def test_replayed_telegram_update_returns_existing_job(client):
    _, session_factory = client
    async with session_factory() as db:
        owner = await db.scalar(select(User).limit(1))
        website = Website(id="site_tg", name="Portal", base_url="https://portal.example.test", login_url="https://portal.example.test/login", dashboard_url="https://portal.example.test/dashboard", adapter_id="configured_selectors", adapter_version="1.0.0", adapter_config={}, allowed_actions=["extract_data"], status=WebsiteStatus.active)
        db.add(website)
        await db.flush()
        telegram_user = TelegramUser(telegram_user_id=90001, user_id=owner.id, display_name="Operator", role=Role.operator, is_active=True)
        db.add(telegram_user)
        await db.flush()

        first = await create_telegram_job(db, telegram_user, website.id, "extract_data", {}, 710001)
        duplicate = await create_telegram_job(db, telegram_user, website.id, "extract_data", {}, 710001)
        assert duplicate.id == first.id
        await db.commit()
        jobs = (await db.execute(select(Job).where(Job.idempotency_key == "telegram:710001"))).scalars().all()
        assert len(jobs) == 1


@pytest.mark.asyncio
async def test_owner_totp_setup_login_challenge_and_disable(client, monkeypatch):
    import pyotp

    from app import crypto

    http, _ = client
    await login(http)
    monkeypatch.setattr(
        crypto,
        "get_settings",
        lambda: Settings(encryption_master_key=SecretStr("c7" * 32)),
    )

    setup = await http.post("/api/v1/auth/totp/setup", json={})
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    confirmed = await http.post("/api/v1/auth/totp/confirm", json={"code": pyotp.TOTP(secret).now()})
    assert confirmed.status_code == 200
    assert (await http.get("/api/v1/auth/me")).json()["totp_enabled"] is True

    logged_in = await http.post("/api/v1/auth/login", json={"email": "owner@example.test", "password": "a-secure-test-password"})
    assert logged_in.status_code == 200
    assert logged_in.json() == {"requires_totp": True}
    verification = await http.post("/api/v1/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    assert verification.status_code == 200
    http.headers["X-CSRF-Token"] = verification.json()["csrf_token"]

    disabled = await http.post(
        "/api/v1/auth/totp/disable",
        json={"password": "a-secure-test-password", "code": pyotp.TOTP(secret).now()},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert (await http.get("/api/v1/auth/me")).json()["totp_enabled"] is False