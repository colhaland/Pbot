import hmac
import json
import shlex
import uuid
import asyncio

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_gateway import AIGatewayError, interpret as ai_interpret
from app.crypto import decrypt_secret, sha256_hex
from app.db import get_db
from app.models import Approval, ApprovalStatus, AuditLog, AIConfig, Job, JobStatus, Role, Schedule, TelegramUser, Website, WebsiteStatus, utcnow
from app.policy import PolicyViolation, validate_action
from app.settings import get_settings

router = APIRouter()
settings = get_settings()


async def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    if not settings.telegram_bot_token:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}/sendMessage"
    payload = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except httpx.HTTPError:
        return


async def answer_callback(callback_id: str, text: str) -> None:
    if not settings.telegram_bot_token:
        return
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}/answerCallbackQuery"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"callback_query_id": callback_id, "text": text[:180], "show_alert": True})
    except httpx.HTTPError:
        return


def audit(db: AsyncSession, actor: str, action: str, target: str, result: str = "success", metadata: dict | None = None):
    db.add(AuditLog(actor_type="telegram", actor_id=actor, action=action, target_type="telegram", target_id=target, result=result, metadata_json=metadata or {}))


async def rate_limit(user_id: int) -> bool:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        key = f"telegram:rate:{user_id}"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
        return count <= 20
    finally:
        await redis.aclose()


async def create_telegram_job(db: AsyncSession, telegram_user: TelegramUser, site_id: str, action: str, parameters: dict, update_id: int, *, source: str = "telegram", allow_ai_write: bool = False):
    website = await db.get(Website, site_id)
    if not website or website.status != WebsiteStatus.active:
        raise ValueError("Website tidak aktif atau tidak terdaftar.")
    try:
        validated = validate_action(action=action, parameters=parameters, allowed_actions=website.allowed_actions, source="ai" if source == "ai" else "telegram", allow_ai_write_proposals=allow_ai_write)
    except PolicyViolation:
        raise ValueError("Tindakan atau parameter tidak diizinkan untuk website ini.") from None
    job = Job(website_id=website.id, action=validated.action, risk_level=validated.risk_level, source=source, requested_by=telegram_user.user_id, requested_by_telegram=telegram_user.telegram_user_id, payload=validated.parameters, payload_hash=sha256_hex(json.dumps(validated.parameters, sort_keys=True, separators=(",", ":"))), idempotency_key=f"telegram:{update_id}", adapter_version=website.adapter_version, status=JobStatus.queued)
    db.add(job)
    audit(db, str(telegram_user.telegram_user_id), "job.created", str(job.id), metadata={"website_id": website.id, "action": action})
    await db.flush()
    return job


async def handle_approval(db: AsyncSession, approval_id: uuid.UUID, telegram_user: TelegramUser, approved: bool):
    if telegram_user.role not in {Role.owner, Role.operator}:
        raise ValueError("Peran Telegram tidak dapat memutuskan persetujuan.")
    approval = await db.get(Approval, approval_id)
    if not approval or approval.status != ApprovalStatus.pending:
        raise ValueError("Persetujuan tidak tersedia atau sudah diputuskan.")
    now = utcnow()
    if approval.expires_at <= now:
        approval.status = ApprovalStatus.expired
        job = await db.get(Job, approval.job_id)
        if job:
            job.status = JobStatus.expired
        await db.commit()
        raise ValueError("Persetujuan sudah kedaluwarsa.")
    job = await db.get(Job, approval.job_id)
    website = await db.get(Website, job.website_id) if job else None
    canonical = json.dumps({"job_id": str(job.id), "website_id": website.id, "adapter_version": job.adapter_version, "action": job.action, "parameters": job.payload, "preview_summary": approval.preview_summary}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if not hmac.compare_digest(sha256_hex(canonical), approval.approval_hash):
        raise ValueError("Pratinjau berubah. Buat pratinjau baru sebelum menyetujui.")
    approval.status = ApprovalStatus.approved if approved else ApprovalStatus.rejected
    approval.decided_by_telegram = telegram_user.telegram_user_id
    approval.decided_by_user = telegram_user.user_id
    approval.decided_at = now
    job.status = JobStatus.executing if approved else JobStatus.rejected
    if not approved:
        job.finished_at = now
    audit(db, str(telegram_user.telegram_user_id), f"approval.{approval.status.value}", str(approval.id), metadata={"job_id": str(job.id)})
    await db.commit()
    return job


@router.post("/api/v1/telegram/webhook")
async def telegram_webhook(
    request: Request,
    secret_token: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
    db: AsyncSession = Depends(get_db),
):
    configured_secret = settings.telegram_webhook_secret.get_secret_value() if settings.telegram_webhook_secret else ""
    if not configured_secret or not secret_token or not hmac.compare_digest(configured_secret, secret_token):
        raise HTTPException(status_code=403, detail={"code": "WEBHOOK_SECRET_INVALID", "message": "Webhook tidak diizinkan."})
    update = await request.json()
    callback = update.get("callback_query")
    message = callback.get("message", {}) if callback else update.get("message", {})
    sender = callback.get("from", {}) if callback else message.get("from", {})
    user_id = sender.get("id")
    chat_id = message.get("chat", {}).get("id")
    if not user_id or not chat_id:
        return {"ok": True}
    entry = await db.get(TelegramUser, int(user_id))
    if not entry or not entry.is_active:
        audit(db, str(user_id), "telegram.unauthorized_attempt", str(user_id), "denied")
        await db.commit()
        await send_message(chat_id, "Akses tidak tersedia.")
        if callback:
            await answer_callback(callback["id"], "Akses tidak tersedia.")
        return {"ok": True}
    if not await rate_limit(int(user_id)):
        await send_message(chat_id, "Batas pesan tercapai. Coba kembali sebentar lagi.")
        return {"ok": True}
    if callback:
        try:
            _, raw_approval_id, action = callback.get("data", "").split(":", 2)
            await handle_approval(db, uuid.UUID(raw_approval_id), entry, action == "approve")
            await answer_callback(callback["id"], "Keputusan dicatat.")
            await send_message(chat_id, "Keputusan dicatat. Worker akan memproses job.")
        except (ValueError, TypeError) as exception:
            await answer_callback(callback["id"], str(exception))
        return {"ok": True}

    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        await send_message(chat_id, "Gunakan /help untuk melihat perintah yang tersedia.")
        return {"ok": True}
    try:
        parts = shlex.split(text)
    except ValueError:
        await send_message(chat_id, "Format perintah tidak valid.")
        return {"ok": True}
    command = parts[0].split("@", 1)[0].lower()
    args = parts[1:]
    try:
        if command == "/help":
            await send_message(chat_id, "/websites · /status · /jobs [n] · /schedules\n/data <site_id> [range=today]\n/screenshot <site_id> [page=dashboard]\n/test <site_id> · /fill <site_id> template=... draft=... field=value\n/approve <job_id> · /reject <job_id> · /cancel <job_id>\n/ask <instruksi>")
        elif command == "/websites":
            sites = (await db.execute(select(Website).order_by(Website.name))).scalars().all()
            await send_message(chat_id, "\n".join(f"{site.id} · {site.name} · {site.status.value}" for site in sites) or "Belum ada website terdaftar.")
        elif command == "/status":
            jobs = (await db.execute(select(Job.status))).scalars().all()
            counts = {status.value: sum(1 for item in jobs if item == status) for status in JobStatus}
            await send_message(chat_id, f"API dan database tersambung.\nJob antre: {counts.get('queued', 0)} · berjalan: {counts.get('running', 0)}\nMenunggu approval: {len((await db.execute(select(Approval.id).where(Approval.status == ApprovalStatus.pending))).scalars().all())}")
        elif command == "/jobs":
            limit = max(1, min(int(args[0]) if args else 10, 20))
            jobs = (await db.execute(select(Job).order_by(desc(Job.created_at)).limit(limit))).scalars().all()
            await send_message(chat_id, "\n".join(f"{str(job.id)[:8]} · {job.website_id} · {job.action} · {job.status.value}" for job in jobs) or "Belum ada job.")
        elif command == "/schedules":
            schedules = (await db.execute(select(Schedule).where(Schedule.is_active.is_(True)).order_by(Schedule.next_run_at))).scalars().all()
            await send_message(chat_id, "\n".join(f"{schedule.name} · {schedule.website_id} · berikutnya {schedule.next_run_at.isoformat()}" for schedule in schedules) or "Tidak ada jadwal aktif.")
        elif command in {"/data", "/screenshot", "/test", "/fill"}:
            if entry.role not in {Role.owner, Role.operator}:
                raise ValueError("Peran viewer hanya dapat membaca status dan riwayat.")
            if not args:
                raise ValueError("ID website wajib diisi.")
            site_id, *raw_params = args
            params = dict(item.split("=", 1) for item in raw_params if "=" in item)
            if command == "/data":
                action, payload = "extract_data", {key: value for key, value in params.items() if key in {"date_range", "page"}}
            elif command == "/screenshot":
                action, payload = "take_screenshot", {key: value for key, value in params.items() if key == "page"}
            elif command == "/test":
                action, payload = "test_login", {}
            else:
                action = "fill_form"
                values = {key: value for key, value in params.items() if key not in {"template", "draft"}}
                payload = {"template": params.get("template", ""), "draft_id": params.get("draft", ""), "values": values}
            job = await create_telegram_job(db, entry, site_id, action, payload, int(update.get("update_id", 0)))
            await db.commit()
            await send_message(chat_id, f"Job {str(job.id)[:8]} masuk antrean · {action} pada {site_id}.")
        elif command in {"/approve", "/reject"}:
            if entry.role not in {Role.owner, Role.operator} or not args:
                raise ValueError("Perintah ini membutuhkan role operator dan job ID.")
            job = await db.scalar(select(Job).where(Job.id == uuid.UUID(args[0])))
            approval = await db.scalar(select(Approval).where(Approval.job_id == job.id, Approval.status == ApprovalStatus.pending)) if job else None
            if not approval:
                raise ValueError("Approval pending untuk job ini tidak ditemukan.")
            await handle_approval(db, approval.id, entry, command == "/approve")
            await send_message(chat_id, "Keputusan dicatat. Worker akan memproses job.")
        elif command == "/cancel":
            if entry.role not in {Role.owner, Role.operator} or not args:
                raise ValueError("Perintah ini membutuhkan role operator dan job ID.")
            job = await db.get(Job, uuid.UUID(args[0]))
            if not job or job.status not in {JobStatus.queued, JobStatus.awaiting_approval}:
                raise ValueError("Job tidak ditemukan atau tidak dapat dibatalkan.")
            job.status = JobStatus.cancelled
            job.finished_at = utcnow()
            audit(db, str(user_id), "job.cancelled", str(job.id))
            await db.commit()
            await send_message(chat_id, "Job dibatalkan.")
        elif command == "/ask":
            if entry.role not in {Role.owner, Role.operator} or not args:
                raise ValueError("Perintah ini membutuhkan role operator dan instruksi.")
            config = await db.scalar(select(AIConfig).order_by(AIConfig.updated_at.desc()).limit(1))
            if not config or not config.api_key_enc:
                raise ValueError("AI belum dikonfigurasi.")
            api_key = decrypt_secret(config.api_key_enc, table="ai_configs.api_key", record_id=str(config.id))
            sites = (await db.execute(select(Website).where(Website.status == WebsiteStatus.active))).scalars().all()
            proposal = await asyncio.to_thread(ai_interpret, api_key=api_key, model_id=config.primary_model_id, instruction=" ".join(args), system_instruction=config.system_instruction, websites=[{"website_id": site.id, "name": site.name, "allowed_actions": site.allowed_actions} for site in sites], temperature=config.temperature, max_output_tokens=config.max_output_tokens, timeout_seconds=config.timeout_seconds)
            if proposal.intent != "execute_job" or not proposal.website_id or not proposal.action:
                await send_message(chat_id, proposal.clarification_question or proposal.user_summary)
            else:
                site = await db.get(Website, proposal.website_id)
                validated = validate_action(action=proposal.action, parameters=proposal.parameters, allowed_actions=site.allowed_actions if site else [], allow_ai_write_proposals=config.allow_ai_write_proposals, source="ai")
                job = await create_telegram_job(db, entry, proposal.website_id, proposal.action, validated.parameters, int(update.get("update_id", 0)), source="ai", allow_ai_write=config.allow_ai_write_proposals)
                await db.commit()
                await send_message(chat_id, f"{proposal.user_summary}\nJob {str(job.id)[:8]} · {validated.risk_level} · approval {'wajib' if validated.requires_approval else 'tidak diperlukan'}.")
        else:
            await send_message(chat_id, "Perintah tidak dikenal. Gunakan /help.")
    except (ValueError, TypeError, AIGatewayError, PolicyViolation) as exception:
        await send_message(chat_id, str(exception) if isinstance(exception, ValueError) else "Permintaan tidak dapat diproses sesuai kebijakan.")
    return {"ok": True}