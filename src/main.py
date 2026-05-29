from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from loguru import logger

from src.aggregator import PortStatusAggregator
from src.models import load_ports_config
from src.scheduler import build_scheduler
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
    timezone_name = os.getenv("TIMEZONE", "Asia/Seoul")
    ports_config = os.getenv("PORTS_CONFIG", "config/ports.yaml")
    keywords_config = os.getenv("KEYWORDS_CONFIG", "config/keywords.yaml")
    state_file = os.getenv("STATE_FILE", "data/terminal_check2_bot_state.json")
    timeout_seconds = float(os.getenv("HTTP_TIMEOUT_SECONDS", "20"))
    user_agent = os.getenv("HTTP_USER_AGENT")

    logger.remove()
    logger.add(lambda message: print(message, end=""), level=os.getenv("LOG_LEVEL", "INFO"))

    ports = load_ports_config(ports_config)
    aggregator = PortStatusAggregator(
        ports=ports,
        keywords_config_path=keywords_config,
        timeout_seconds=timeout_seconds,
        user_agent=user_agent,
    )
    state_store = JsonStateStore(state_file)
    bot = TerminalCheckTelegramBot(
        settings=TelegramBotSettings(
            token=token,
            chat_id=chat_id,
            send_empty_report=env_bool("SEND_EMPTY_REPORT", False),
            send_unchanged_alerts=env_bool("SEND_UNCHANGED_ALERTS", False),
        ),
        aggregator=aggregator,
        state_store=state_store,
        ports=ports,
    )

    scheduler = build_scheduler(bot.run_scheduled_check, timezone_name=timezone_name)
    scheduler.start()
    logger.info("terminal_check2_bot started with {} ports", len(ports))
    await bot.application.initialize()
    await bot.application.start()
    await bot.application.updater.start_polling()
    try:
        await asyncio.Event().wait()
    finally:
        await bot.application.updater.stop()
        await bot.application.stop()
        await bot.application.shutdown()
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
