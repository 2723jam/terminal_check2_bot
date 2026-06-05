from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src import BOT_NAME

SCHEDULE_HOURS = tuple(range(9, 19))
GITHUB_ACTIONS_UTC_HOURS = tuple(range(0, 10))
SCHEDULE_MINUTE = 5
GITHUB_ACTIONS_WATCHDOG_MINUTES = (5, 20, 35, 50)


def scheduled_run_times() -> list[time]:
    return [time(hour=hour, minute=SCHEDULE_MINUTE) for hour in SCHEDULE_HOURS]


def github_actions_watchdog_run_times() -> list[time]:
    return [time(hour=hour, minute=minute) for hour in SCHEDULE_HOURS for minute in GITHUB_ACTIONS_WATCHDOG_MINUTES]


def github_actions_watchdog_crons() -> list[str]:
    minutes = ",".join(str(minute) for minute in GITHUB_ACTIONS_WATCHDOG_MINUTES)
    return [f"{minutes} {hour} * * *" for hour in GITHUB_ACTIONS_UTC_HOURS]


def scheduled_slot_id(now: datetime, timezone_name: str = "Asia/Seoul") -> str | None:
    timezone = ZoneInfo(timezone_name)
    if now.tzinfo is None:
        local_now = now.replace(tzinfo=timezone)
    else:
        local_now = now.astimezone(timezone)

    if local_now.hour not in SCHEDULE_HOURS or local_now.minute < SCHEDULE_MINUTE:
        return None
    return f"{local_now:%Y-%m-%d}-{local_now.hour:02d}{SCHEDULE_MINUTE:02d}-{timezone_name}"


def scheduled_slot_id_from_github_schedule(
    schedule_expression: str | None,
    now: datetime,
    timezone_name: str = "Asia/Seoul",
) -> str | None:
    if not schedule_expression:
        return scheduled_slot_id(now, timezone_name)

    parts = schedule_expression.split()
    if len(parts) != 5:
        return scheduled_slot_id(now, timezone_name)

    try:
        utc_hour = int(parts[1])
    except ValueError:
        return scheduled_slot_id(now, timezone_name)

    if utc_hour not in GITHUB_ACTIONS_UTC_HOURS:
        return scheduled_slot_id(now, timezone_name)

    utc_now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    scheduled_utc = utc_now.replace(hour=utc_hour, minute=SCHEDULE_MINUTE, second=0, microsecond=0)
    return scheduled_slot_id(scheduled_utc, timezone_name)


def build_scheduler(
    callback: Callable[[], Awaitable[None]],
    timezone_name: str = "Asia/Seoul",
) -> AsyncIOScheduler:
    timezone = ZoneInfo(timezone_name)
    scheduler = AsyncIOScheduler(timezone=timezone)
    for hour in SCHEDULE_HOURS:
        scheduler.add_job(
            callback,
            CronTrigger(hour=hour, minute=SCHEDULE_MINUTE, timezone=timezone),
            id=f"{BOT_NAME}_{hour:02d}{SCHEDULE_MINUTE:02d}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    return scheduler
