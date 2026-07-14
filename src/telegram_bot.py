from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from telegram import Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import Application, CommandHandler, ContextTypes

from src import BOT_NAME
from src.aggregator import PortStatusAggregator
from src.message_formatter import EMPTY_REPORT, format_events, format_weather_risks, message_hash, split_telegram_message
from src.models import PortConfig
from src.state_store import JsonStateStore

BINDING_LINK = f"https://t.me/{BOT_NAME}?start={BOT_NAME}"
BINDING_CONFIRMATION = "terminal_check2_bot \uc54c\ub9bc \ucc44\ud305 \uc5f0\uacb0 \uc644\ub8cc"



@dataclass
class TelegramBotSettings:
    token: str
    chat_id: str
    bot_username: str = BOT_NAME
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
        self._delivery_chat_id: str | None = None
        self.application.add_handler(CommandHandler("check", self.handle_check))
        self.application.add_handler(CommandHandler("start", self.handle_start))
        self.application.add_handler(CommandHandler("status", self.handle_status))
        self.application.add_handler(CommandHandler("ports", self.handle_ports))

    async def handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if not chat:
            return
        if chat.type != "private" or context.args != [BOT_NAME]:
            await chat.send_message(
                "\uc544\ub798 \ub9c1\ud06c\ub97c \uc5f4\uace0 START\ub97c \ub20c\ub7ec "
                f"\uc5f0\uacb0\ud558\uc138\uc694.\n{BINDING_LINK}"
            )
            return

        await self._assert_bot_identity()
        self._delivery_chat_id = str(chat.id)
        self.state_store.set_bound_chat_id(self._delivery_chat_id)
        await chat.send_message(BINDING_CONFIRMATION)

    async def run_scheduled_check(self) -> None:
        await self._check_and_send(reply_context=None, force_empty=False)

    async def handle_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await self._check_and_send(reply_context=update, force_empty=True)

    async def handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        state = self.state_store.load()
        failed_ports = sorted(
            {
                str(item.get("port_code"))
                for item in state.get("recent_failures") or []
                if item.get("port_code")
            }
        )
        text = "\n".join(
            [
                f"봇명 : {BOT_NAME}",
                f"마지막 실행 : {state.get('last_run_at') or '-'}",
                f"마지막 성공 : {state.get('last_success')}",
                f"마지막 오류 : {state.get('last_error') or '-'}",
                f"최근 중단 이벤트 수 : {len(state.get('recent_events') or [])}",
                f"최근 기상 우려 수 : {len(state.get('recent_weather_risks') or [])}",
                f"최근 수집 실패 수 : {state.get('last_failure_count', 0)}",
                f"\ucd5c\uadfc \uc2e4\ud328 \ud3ec\ud2b8 : {', '.join(failed_ports) or '-'}",
                f"마지막 스케줄 슬롯 : {state.get('last_scheduled_slot') or '-'}",
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
            if reply_context is None:
                await self.validate_delivery_target()
            result = await self.aggregator.collect()
            self.state_store.record_failures(result.failures)
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
                return
            raise

    async def _send_message(self, message: str, reply_context: Update | None) -> None:
        chunks = split_telegram_message(message)
        if reply_context and reply_context.effective_chat:
            for chunk in chunks:
                await reply_context.effective_chat.send_message(chunk)
            return

        for chunk in chunks:
            await self._send_to_configured_chat(chunk)

    async def validate_delivery_target(self) -> str:
        await self._assert_bot_identity()
        candidates = [self.state_store.get_bound_chat_id(), self.settings.chat_id]
        seen: set[str] = set()
        for candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                await self.application.bot.get_chat(chat_id=candidate)
            except (BadRequest, Forbidden) as exc:
                logger.warning(
                    "Telegram delivery target rejected: {}",
                    type(exc).__name__,
                )
                continue
            self._delivery_chat_id = candidate
            return candidate

        fallback_chat_id = await self._latest_binding_update_chat_id()
        if not fallback_chat_id:
            raise BadRequest(
                "Telegram chat is not bound. Open "
                f"{BINDING_LINK} and press START, then run the workflow again."
            )

        await self.application.bot.send_message(
            chat_id=fallback_chat_id,
            text=BINDING_CONFIRMATION,
        )
        self.state_store.set_bound_chat_id(fallback_chat_id)
        self._delivery_chat_id = fallback_chat_id
        logger.info("terminal_check2_bot delivery chat bound from dedicated START command")
        return fallback_chat_id

    async def _assert_bot_identity(self) -> None:
        bot_user = await self.application.bot.get_me()
        actual = (bot_user.username or "").lstrip("@")
        expected = self.settings.bot_username.lstrip("@")
        if actual.casefold() != expected.casefold():
            raise RuntimeError(
                f"Telegram token belongs to @{actual or '(unknown)'}, expected @{expected}"
            )

    async def _send_to_configured_chat(self, message: str) -> None:
        chat_id = self._delivery_chat_id
        if not chat_id:
            chat_id = await self.validate_delivery_target()
        await self.application.bot.send_message(chat_id=chat_id, text=message)

    async def _latest_binding_update_chat_id(self) -> str | None:
        updates = await self.application.bot.get_updates(
            limit=100,
            timeout=0,
            allowed_updates=["message"],
        )
        return _binding_chat_id_from_updates(updates, self.settings.bot_username)


def _binding_chat_id_from_updates(
    updates: list[Update],
    bot_username: str,
) -> str | None:
    username = bot_username.lstrip("@")
    allowed_commands = {
        f"/start {BOT_NAME}".casefold(),
        f"/start@{username} {BOT_NAME}".casefold(),
    }
    for update in reversed(updates):
        chat = update.effective_chat
        message = update.effective_message
        text = (message.text or "").strip() if message else ""
        if (
            chat
            and chat.type == "private"
            and chat.id
            and text.casefold() in allowed_commands
        ):
            return str(chat.id)
    return None
