import asyncio
import hashlib
import ipaddress
import json
import logging
import socket
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.async_api import async_playwright
from redis.asyncio import Redis
from sqlalchemy import select

from app.adapters import AdapterContext, AdapterError, ConfiguredSelectorAdapter, Credentials
from app.crypto import decrypt_secret, sha256_hex
from app.db import SessionLocal
from app.models import Approval, ApprovalStatus, Artifact, AuditLog, Job, JobStatus, TelegramUser, Website, WebsiteCredential, WebsiteStatus, utcnow
from app.models import Role
from app.settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("pbot.worker")
settings = get_settings()
adapter = ConfiguredSelectorAdapter()
terminal_statuses = {JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled, JobStatus.rejected, JobStatus.expired, JobStatus.needs_manual_action}


def log_event(event: str, **fields) -> None:
    logger.info(json.dumps({"ts": utcnow().isoformat(), "level": "INFO", "service": "worker", "event": event, **fields}, separators=(",", ":")))


async def claim_job():
    async with SessionLocal() as db:
        result = await db.execute(select(Job).where(Job.status.in_([JobStatus.queued, JobStatus.executing])).order_by(Job.created_at).with_for_update(skip_locked=True).limit(1))
        job = result.scalar_one_or_none()
        if not job:
            return None
        if job.cancel_requested:
            job.status = JobStatus.cancelled
            job.finished_at = utcnow()
            await db.commit()
            return None
        if job.status == JobStatus.queued:
            job.status = JobStatus.running
            job.started_at = utcnow()
        job.attempt_count += 1
        await db.commit()
        return job.id


async def save_artifact(db, job: Job, kind: str, content: bytes) -> None:
    digest = hashlib.sha256(content).hexdigest()
    extension = "png" if kind == "screenshot" else "json"
    key = f"{job.website_id}/{job.id}/{int(utcnow().timestamp())}.{extension}"
    target = Path(settings.artifact_dir) / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    db.add(Artifact(job_id=job.id, website_id=job.website_id, kind=kind, storage_key=key, checksum_sha256=digest, size_bytes=len(content), expires_at=utcnow() + timedelta(days=30)))


async def execute_job(job_id) -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    async with SessionLocal() as db:
        job = await db.get(Job, job_id)
        if not job or job.status in terminal_statuses:
            await redis.aclose()
            return
        website = await db.get(Website, job.website_id)
        credential_result = await db.execute(select(WebsiteCredential).where(WebsiteCredential.website_id == job.website_id, WebsiteCredential.is_active.is_(True)))
        credential = credential_result.scalar_one_or_none()
        if not website or website.status != WebsiteStatus.active or not credential:
            job.status = JobStatus.failed
            job.last_error_code = "WEBSITE_OR_CREDENTIAL_UNAVAILABLE"
            job.last_error_message = "Website tidak aktif atau kredensial belum disetel."
            job.finished_at = utcnow()
            await db.commit()
            await redis.aclose()
            return
        if website.adapter_id != adapter.adapter_id:
            website.status = WebsiteStatus.needs_attention
            job.status = JobStatus.failed
            job.last_error_code = "ADAPTER_NOT_FOUND"
            job.last_error_message = "Adapter website belum terdaftar pada worker."
            job.finished_at = utcnow()
            await db.commit()
            await redis.aclose()
            return
        lock = redis.lock(f"website:{website.id}", timeout=settings.job_default_timeout_seconds + 60, blocking_timeout=1)
        try:
            if not await lock.acquire():
                job.status = JobStatus.queued
                await db.commit()
                return
            username = decrypt_secret(credential.username_enc, table="website_credentials.username", record_id=str(credential.id))
            password = decrypt_secret(credential.password_enc, table="website_credentials.password", record_id=str(credential.id))
            allowed_hosts = {urlparse(url).hostname for url in (website.base_url, website.login_url, website.dashboard_url)}
            config = dict(website.adapter_config or {})
            config["login"] = {**(config.get("login") or {}), "url": website.login_url}
            config["dashboard_url"] = website.dashboard_url
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
                context = await browser.new_context()
                async def guard_route(route):
                    request_url = route.request.url
                    parsed = urlparse(request_url)
                    blocked = parsed.scheme not in {"https", "about", "data"} or (parsed.hostname and parsed.hostname not in allowed_hosts)
                    if not blocked and parsed.hostname:
                        try:
                            addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or 443, 0, socket.SOCK_STREAM)
                            blocked = any(not ipaddress.ip_address(record[4][0]).is_global for record in addresses)
                        except (socket.gaierror, ValueError):
                            blocked = True
                    if blocked:
                        await route.abort()
                    else:
                        await route.continue_()
                await context.route("**/*", guard_route)
                page = await context.new_page()
                adapter_context = AdapterContext(str(job.id), website.id, page, config, Credentials(username, password), allowed_hosts)
                try:
                    if job.cancel_requested:
                        job.status = JobStatus.cancelled
                        job.finished_at = utcnow()
                        approval = await db.scalar(select(Approval).where(Approval.job_id == job.id, Approval.status == ApprovalStatus.pending))
                        if approval:
                            approval.status = ApprovalStatus.superseded
                    else:
                        await adapter.ensure_session(adapter_context)
                        website.last_login_ok_at = utcnow()
                        website.last_login_error = None
                        website.login_failures_consecutive = 0
                        db.add(AuditLog(actor_type="system", actor_id="worker", action="credential.used", target_type="website", target_id=website.id, result="success", metadata_json={"job_id": str(job.id)}))
                        if job.risk_level == "read":
                            if job.action != "test_login":
                                result = await adapter.execute_read(adapter_context, job.action, job.payload)
                                content = result["content"] if isinstance(result["content"], bytes) else json.dumps(result["content"], ensure_ascii=False, indent=2).encode()
                                await save_artifact(db, job, result["kind"], content)
                            job.status = JobStatus.succeeded
                            job.finished_at = utcnow()
                        elif job.status == JobStatus.running:
                            changes = await adapter.prepare_form(adapter_context, job.payload)
                            preview = {"changes": changes}
                            approval_hash = sha256_hex(json.dumps({"job_id": str(job.id), "website_id": website.id, "adapter_version": job.adapter_version, "action": job.action, "parameters": job.payload, "preview_summary": preview}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
                            db.add(Approval(job_id=job.id, approval_hash=approval_hash, token_hash=sha256_hex(__import__("secrets").token_urlsafe(32)), preview_summary=preview, status=ApprovalStatus.pending, expires_at=utcnow() + timedelta(seconds=settings.approval_ttl_seconds)))
                            job.status = JobStatus.awaiting_approval
                            masks = [page.locator(selector) for selector in config.get("sensitive_selectors", [])]
                            await save_artifact(db, job, "screenshot", await page.screenshot(full_page=True, type="png", mask=masks, mask_color="#000000"))
                        elif job.status == JobStatus.executing:
                            approval_result = await db.execute(select(Approval).where(Approval.job_id == job.id, Approval.status == ApprovalStatus.approved))
                            approval = approval_result.scalar_one_or_none()
                            if not approval:
                                raise AdapterError("APPROVAL_INVALID")
                            await adapter.execute_form(adapter_context, job.payload, approval.preview_summary)
                            job.status = JobStatus.succeeded
                            job.finished_at = utcnow()
                finally:
                    await context.close()
                    await browser.close()
        except AdapterError as exception:
            job.last_error_code = exception.code
            job.last_error_message = "Tindakan memerlukan pemeriksaan konfigurasi atau manual." if exception.code in {"NEEDS_MANUAL_ACTION", "ADAPTER_NOT_CONFIGURED", "PREVIEW_MISMATCH"} else "Eksekusi tidak dapat diselesaikan."
            job.status = JobStatus.needs_manual_action if exception.code == "NEEDS_MANUAL_ACTION" else JobStatus.failed
            job.finished_at = utcnow()
            if exception.code == "LOGIN_FAILED":
                website.login_failures_consecutive += 1
                website.last_login_error = exception.code
                if website.login_failures_consecutive >= 3:
                    website.status = WebsiteStatus.needs_attention
            if exception.code == "SELECTOR_NOT_FOUND":
                website.status = WebsiteStatus.needs_attention
        except Exception as exception:
            job.last_error_code = "WORKER_ERROR"
            job.last_error_message = "Worker gagal menjalankan job. Periksa konfigurasi dan log operasional."
            job.status = JobStatus.failed
            job.finished_at = utcnow()
            log_event("job.failed", job_id=str(job.id), website_id=job.website_id, error_type=type(exception).__name__)
        finally:
            await db.commit()
            if job.status == JobStatus.awaiting_approval and settings.telegram_bot_token:
                approval = await db.scalar(select(Approval).where(Approval.job_id == job.id))
                operators = (await db.execute(select(TelegramUser).where(TelegramUser.is_active.is_(True), TelegramUser.role.in_([Role.owner, Role.operator])))).scalars().all()
                if approval:
                    preview_text = "\n".join(f"{name}: {change.get('old', '')} → {change.get('new', '')}"[:240] for name, change in approval.preview_summary.get("changes", {}).items())
                    message = f"Persetujuan diperlukan\nWebsite: {website.name}\nAksi: {job.action}\nJob: {job.id}\n\nPerubahan:\n{preview_text}\n\nBerlaku sampai {approval.expires_at.isoformat()}"
                    keyboard = {"inline_keyboard": [[{"text": "Setujui", "callback_data": f"a:{approval.id}:approve"}, {"text": "Tolak", "callback_data": f"a:{approval.id}:reject"}]]}
                    telegram_url = f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}/sendMessage"
                    async with httpx.AsyncClient(timeout=10) as client:
                        for operator in operators:
                            try:
                                await client.post(telegram_url, json={"chat_id": operator.telegram_user_id, "text": message[:4000], "reply_markup": keyboard, "disable_web_page_preview": True})
                            except httpx.HTTPError:
                                log_event("telegram.notification_failed", job_id=str(job.id), website_id=website.id)
            try:
                await lock.release()
            except Exception:
                pass
            await redis.aclose()
        log_event("job.completed", job_id=str(job.id), website_id=job.website_id, status=job.status.value)


async def run_worker() -> None:
    active: set[asyncio.Task] = set()

    async def execute_with_timeout(job_id):
        try:
            await asyncio.wait_for(execute_job(job_id), timeout=settings.job_default_timeout_seconds)
        except asyncio.TimeoutError:
            async with SessionLocal() as db:
                job = await db.get(Job, job_id)
                if job and job.status not in terminal_statuses:
                    job.status = JobStatus.failed
                    job.last_error_code = "TIMEOUT"
                    job.last_error_message = "Job melewati batas waktu eksekusi."
                    job.finished_at = utcnow()
                    await db.commit()

    while True:
        active = {task for task in active if not task.done()}
        if len(active) >= settings.browser_max_concurrency:
            await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            continue
        job_id = await claim_job()
        if job_id:
            active.add(asyncio.create_task(execute_with_timeout(job_id)))
        elif active:
            await asyncio.wait(active, timeout=2, return_when=asyncio.FIRST_COMPLETED)
        else:
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(run_worker())