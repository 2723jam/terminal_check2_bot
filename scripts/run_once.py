from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from src.aggregator import PortStatusAggregator
from src.models import load_ports_config
from src.state_store import JsonStateStore
from src.telegram_bot import TelegramBotSettings, TerminalCheckTelegramBot


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


async def main() -> None:
    load_dotenv()

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    ports_config = os.getenv("PORTS_CONFIG", "config/ports.yaml")
    keywords_config = os.getenv("KEYWORDS_CONFIG", "config/keywords.yaml")
    state_file = os.getenv("STATE_FILE", "data/terminal_check2_bot_state.json")

    ports = load_ports_config(ports_config)
    state_store = JsonStateStore(state_file)
    aggregator = PortStatusAggregator(
        ports=ports,
        keywords_config_path=keywords_config,
        timeout_seconds=float(os.getenv("HTTP_TIMEOUT_SECONDS", "20")),
        user_agent=os.getenv("HTTP_USER_AGENT"),
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
    print(
        "Finished one check. "
        f"success={state.get('last_success')} "
        f"last_run_at={state.get('last_run_at')} "
        f"recent_events={len(state.get('recent_events') or [])}"
    )


if __name__ == "__main__":
    asyncio.run(main())
