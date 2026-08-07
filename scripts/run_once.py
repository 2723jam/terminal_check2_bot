from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from src.aggregator import PortStatusAggregator
from src.models import load_ports_config
from src.scheduler import scheduled_slot_id_from_github_schedule
from src.state_store import JsonStateStore
from src.telegram_bot import TelegramBotSettings, TerminalCheckTelegramBot


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def github_schedule_expression() -> str | None:
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    path = Path(event_path)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    schedule = payload.get("schedule")
    return schedule if isinstance(schedule, str) else None


async def main() -> None:
    load_dotenv()

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    ports_config = os.getenv("PORTS_CONFIG", "config/ports.yaml")
    keywords_config = os.getenv("KEYWORDS_CONFIG", "config/keywords.yaml")
    state_file = os.getenv("STATE_FILE", "data/terminal_check2_bot_state.json")
    timezone_name = os.getenv("TIMEZONE", "Asia/Seoul")

    ports = load_ports_config(ports_config)
    state_store = JsonStateStore(state_file)
    current_slot = None
    if os.getenv("GITHUB_EVENT_NAME") == "schedule":
        current_slot = scheduled_slot_id_from_github_schedule(
            github_schedule_expression(),
            datetime.now(UTC),
            timezone_name,
        )
        if current_slot is None:
            print("Skipped scheduled run outside configured Asia/Seoul 09:05-18:05 window.")
            return
        if state_store.get_last_scheduled_slot() == current_slot:
            print(f"Skipped duplicate scheduled run for slot={current_slot}.")
            return

    aggregator = PortStatusAggregator(
        ports=ports,
        keywords_config_path=keywords_config,
        timeout_seconds=float(os.getenv("HTTP_TIMEOUT_SECONDS", "20")),
        user_agent=os.getenv("HTTP_USER_AGENT"),
        weather_risk_enabled=False,
        weather_risk_horizon_hours=int(os.getenv("WEATHER_RISK_HOURS", "12")),
    )

    settings = TelegramBotSettings(
        token=token,
        chat_id=chat_id,
        send_empty_report=env_bool("SEND_EMPTY_REPORT", False),
        send_unchanged_alerts=env_bool("SEND_UNCHANGED_ALERTS", False),
    )
    bot = TerminalCheckTelegramBot(settings=settings, aggregator=aggregator, state_store=state_store, ports=ports)
    await bot.application.initialize()
    try:
        await bot._check_and_send(reply_context=None, force_empty=False)
    finally:
        await bot.application.shutdown()

    state = state_store.load()
    if current_slot and state.get("last_success") is True:
        state_store.set_last_scheduled_slot(current_slot)
        state = state_store.load()

    print(
        "Finished one check. "
        f"success={state.get('last_success')} "
        f"last_run_at={state.get('last_run_at')} "
        f"recent_events={len(state.get('recent_events') or [])} "
        f"recent_weather_risks={len(state.get('recent_weather_risks') or [])} "
        f"last_failure_count={state.get('last_failure_count', 0)} "
        f"last_scheduled_slot={state.get('last_scheduled_slot') or '-'}"
    )


if __name__ == "__main__":
    asyncio.run(main())
