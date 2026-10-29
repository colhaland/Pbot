import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job, JobStatus, Schedule, Website, WebsiteStatus, utcnow
from app.policy import validate_action, PolicyViolation

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("pbot.scheduler")


def next_run(schedule: Schedule, now: datetime) -> datetime:
    zone = ZoneInfo(schedule.timezone)
    local_now = now.astimezone(zone)
    return croniter(schedule.cron_expr, local_now).get_next(datetime).astimezone(timezone.utc)


async def run_due_schedules() -> int:
    created = 0
    now = utcnow()
    async with SessionLocal() as db:
        result = await db.execute(select(Schedule).where(Schedule.is_active.is_(True), Schedule.next_run_at <= now).order_by(Schedule.next_run_at).with_for_update(skip_locked=True).limit(100))
        for schedule in result.scalars().all():
            website = await db.get(Website, schedule.website_id)
            if website and website.status == WebsiteStatus.active:
                try:
                    validated = validate_action(action=schedule.action, parameters=schedule.payload, allowed_actions=website.allowed_actions)
                except PolicyViolation:
                    schedule.is_active = False
                    continue
                scheduled_for = schedule.next_run_at.isoformat()
                key = f"schedule:{schedule.id}:{scheduled_for}"
                existing = await db.scalar(select(Job.id).where(Job.idempotency_key == key))
                if not existing:
                    payload_hash = hashlib.sha256(json.dumps(validated.parameters, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                    db.add(Job(website_id=website.id, action=validated.action, risk_level="read", source="schedule", payload=validated.parameters, payload_hash=payload_hash, idempotency_key=key, adapter_version=website.adapter_version, status=JobStatus.queued))
                    created += 1
                schedule.last_run_at = now
            schedule.next_run_at = next_run(schedule, now)
        await db.commit()
    return created


async def run_scheduler() -> None:
    while True:
        try:
            count = await run_due_schedules()
            if count:
                logger.info(json.dumps({"ts": utcnow().isoformat(), "level": "INFO", "service": "scheduler", "event": "schedule.jobs_created", "count": count}))
        except Exception as exception:
            logger.exception("scheduler_loop_failed", extra={"error_type": type(exception).__name__})
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(run_scheduler())