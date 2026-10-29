from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.models import Schedule
from app.scheduler import should_run_due


def test_skip_policy_skips_old_missed_cron_slot():
    zone = ZoneInfo("Asia/Jakarta")
    now = datetime(2026, 10, 29, 15, 2, tzinfo=zone).astimezone(timezone.utc)
    schedule = Schedule(
        cron_expr="0 * * * *",
        timezone="Asia/Jakarta",
        misfire_policy="skip",
        next_run_at=datetime(2026, 10, 29, 7, 0, tzinfo=timezone.utc),
    )

    assert should_run_due(schedule, now) is False


def test_skip_policy_runs_current_cron_slot():
    zone = ZoneInfo("Asia/Jakarta")
    now = datetime(2026, 10, 29, 15, 2, tzinfo=zone).astimezone(timezone.utc)
    schedule = Schedule(
        cron_expr="0 * * * *",
        timezone="Asia/Jakarta",
        misfire_policy="skip",
        next_run_at=datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc),
    )

    assert should_run_due(schedule, now) is True


def test_run_once_latest_keeps_one_missed_execution():
    schedule = Schedule(
        cron_expr="0 * * * *",
        timezone="Asia/Jakarta",
        misfire_policy="run_once_latest",
        next_run_at=datetime(2026, 10, 29, 6, 0, tzinfo=timezone.utc),
    )

    assert should_run_due(schedule, datetime(2026, 10, 29, 8, 2, tzinfo=timezone.utc)) is True