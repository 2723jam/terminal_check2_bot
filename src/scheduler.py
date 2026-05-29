from __future__ import annotations

from datetime import time
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src import BOT_NAME

SCHEDULE_HOURS = tuple(range(9, 19))
SCHEDULE_MINUTE = 5


def scheduled_run_times() -> list[time]:
    return [time(hour=hour, minute=SCHEDULE_MINUTE) for hour in SCHEDULE_HOURS]


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
