from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes

from src import BOT_NAME
from src.aggregator import PortStatusAggregator
from src.message_formatter import EMPTY_REPORT, format_events, format_weather_risks, message_hash, split_telegram_message
from src.models import PortConfig
from src.state_store import JsonStateStore


@dataclass
class TelegramBotSettings:
    token: str
    chat_id: str
    send_empty_report: bool = False
    send_unchanged_alerts: bool = False


class TerminalCheckTelegramBot:
    def __init__(
        self,
        settings: TelegramBotSettings,
        aggregator: PortStatusAggregator,
        state_store: JsonStateStore,
        ports: list[PortConfig],
    ) -> None:
        self.settings = settings
        self.aggregator = aggregator
        self.state_store = state_store
        self.ports = ports
        self.application = Application.builder().token(settings.token).build()
        self.application.add_handler(CommandHandler("check", self.handle_check))
        self.application.add_handler(CommandHandler("status", self.handle_status))
        self.application.add_handler(CommandHandler("ports", self.handle_ports))

    async def run_scheduled_check(self) -> None:
        await self._check_and_send(reply_context=None, force_empty=False)

    async def handle_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._check_and_send(reply_context=update, force_empty=True)

    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        state = self.state_store.load()
        text = "\n".join(
            [
                f"봇명 : {BOT_NAME}",
                f"마지막 실행 : {state.get('last_run_at') or '-'}",
                f"마지막 성공 : {state.get('last_success')}",
                f"마지막 오류 : {state.get('last_error') or '-'}",
                f"최근 이벤트 수 : {len(state.get('recent_events') or [])}",
                f"최근 기상 우려 수 : {len(state.get('recent_weather_risks') or [])}",
            ]
        )
        if update.effective_chat:
            await update.effective_chat.send_message(text)

    async def handle_ports(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        lines = [f"{port.country} : {port.port_code}" for port in self.ports]
        if update.effective_chat:
            await update.effective_chat.send_message("\n".join(lines))

    async def _check_and_send(self, reply_context: Update | None, force_empty: bool) -> None:
        try:
            result = await self.aggregator.collect()
            message_parts: list[str] = []
            if result.events:
                message_parts.append(format_events(result.events, self.ports))
            if result.weather_risks:
                message_parts.append(format_weather_risks(result.weather_risks, self.ports))
            message = "\n\n".join(part for part in message_parts if part)
            should_send_empty = self.settings.send_empty_report or force_empty
            if not message and should_send_empty:
                message = EMPTY_REPORT
            elif not message:
                self.state_store.update_run(success=True)
                self.state_store.record_events([])
                self.state_store.record_weather_risks([])
                return

            current_hash = message_hash(message)
            if (
                not self.settings.send_unchanged_alerts
                and (result.events or result.weather_risks)
                and current_hash == self.state_store.get_last_message_hash()
            ):
                logger.info("skip unchanged alert")
                self.state_store.update_run(success=True)
                return

            await self._send_message(message, reply_context)
            self.state_store.set_last_message_hash(current_hash)
            self.state_store.record_events(result.events)
            self.state_store.record_weather_risks(result.weather_risks)
            self.state_store.update_run(success=True)
        except Exception as exc:  # noqa: BLE001 - report and persist bot-level failure.
            logger.exception("check failed")
            self.state_store.update_run(success=False, error=str(exc))
            if reply_context and reply_context.effective_chat:
                await reply_context.effective_chat.send_message(f"체크 실패 : {exc}")

    async def _send_message(self, message: str, reply_context: Update | None) -> None:
        chunks = split_telegram_message(message)
        if reply_context and reply_context.effective_chat:
            for chunk in chunks:
                await reply_context.effective_chat.send_message(chunk)
            return

        for chunk in chunks:
            await self._send_to_configured_chat(chunk)

    async def _send_to_configured_chat(self, message: str) -> None:
        try:
            await self.application.bot.send_message(chat_id=self.settings.chat_id, text=message)
            return
        except BadRequest as exc:
            if "chat not found" not in str(exc).lower():
                raise

        fallback_chat_id = await self._latest_update_chat_id()
        if not fallback_chat_id:
            raise BadRequest("chat not found and no recent Telegram update is available")

        logger.warning("configured TELEGRAM_CHAT_ID was not found; sending to latest update chat")
        await self.application.bot.send_message(chat_id=fallback_chat_id, text=message)

    async def _latest_update_chat_id(self) -> str | None:
        updates = await self.application.bot.get_updates(limit=20, timeout=0)
        for update in reversed(updates):
            if update.effective_chat and update.effective_chat.id:
                return str(update.effective_chat.id)
        return None
