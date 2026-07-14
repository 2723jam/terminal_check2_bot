from __future__ import annotations

from datetime import datetime, time
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src import BOT_NAME

SCHEDULE_HOURS = tuple(range(9, 19))
GITHUB_ACTIONS_WATCHDOG_LOCAL_HOURS = tuple(range(6, 19))
SCHEDULE_MINUTE = 5
GITHUB_ACTIONS_WATCHDOG_MINUTES = (5, 20, 35, 50)
GITHUB_ACTIONS_LATE_GRACE_END_HOUR = 22


def scheduled_run_times() -> list[time]:
    return [time(hour=hour, minute=SCHEDULE_MINUTE) for hour in SCHEDULE_HOURS]


def github_actions_watchdog_run_times() -> list[time]:
    return [
        time(hour=hour, minute=minute)
        for hour in GITHUB_ACTIONS_WATCHDOG_LOCAL_HOURS
        for minute in GITHUB_ACTIONS_WATCHDOG_MINUTES
    ]


def github_actions_watchdog_crons() -> list[str]:
    minutes = ",".join(str(minute) for minute in GITHUB_ACTIONS_WATCHDOG_MINUTES)
    return [
        f"{minutes} 21-23 * * *",
        f"{minutes} 0-9 * * *",
    ]


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
    del schedule_expression
    slot = scheduled_slot_id(now, timezone_name)
    if slot:
        return slot

    timezone = ZoneInfo(timezone_name)
    local_now = now.replace(tzinfo=timezone) if now.tzinfo is None else now.astimezone(timezone)
    if 19 <= local_now.hour < GITHUB_ACTIONS_LATE_GRACE_END_HOUR:
        return (
            f"{local_now:%Y-%m-%d}-"
            f"{SCHEDULE_HOURS[-1]:02d}{SCHEDULE_MINUTE:02d}-{timezone_name}"
        )
    return None


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
