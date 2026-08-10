from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from telegram import Chat, Message, Update, User
from telegram.error import BadRequest

from src.models import AggregationResult, TerminalStatusEvent
from src.state_store import JsonStateStore
from src.telegram_bot import (
    TelegramBotSettings,
    TerminalCheckTelegramBot,
    _binding_chat_id_from_updates,
)


def make_update(update_id: int, chat_id: int, chat_type: str, text: str) -> Update:
    user = User(id=update_id, first_name="Tester", is_bot=False)
    chat = Chat(id=chat_id, type=chat_type)
    message = Message(
        message_id=update_id,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text=text,
    )
    return Update(update_id=update_id, message=message)


def test_binding_accepts_private_start_for_dedicated_bot() -> None:
    unrelated = make_update(1, 111, "private", "/start another_bot")
    group = make_update(2, -222, "group", "/start terminal_check2_bot")
    dedicated = make_update(3, 333, "private", "/start terminal_check2_bot")
    plain = make_update(4, 444, "private", "/start")

    assert (
        _binding_chat_id_from_updates(
            [unrelated, group, dedicated],
            "terminal_check2_bot",
        )
        == "333"
    )
    assert _binding_chat_id_from_updates([plain], "terminal_check2_bot") == "444"
    assert _binding_chat_id_from_updates([unrelated, group], "terminal_check2_bot") is None


def test_state_store_persists_bound_chat_id(tmp_path) -> None:
    store = JsonStateStore(str(tmp_path / "state.json"))

    assert store.get_bound_chat_id() is None
    store.set_bound_chat_id("333")
    assert store.get_bound_chat_id() == "333"


@pytest.mark.asyncio
async def test_scheduled_failure_is_raised_for_github_actions(tmp_path) -> None:
    class BrokenAggregator:
        async def collect(self):
            raise RuntimeError("collection failed")

    store = JsonStateStore(str(tmp_path / "state.json"))
    bot = TerminalCheckTelegramBot(
        settings=TelegramBotSettings(token="123:test", chat_id="333"),
        aggregator=BrokenAggregator(),
        state_store=store,
        ports=[],
    )
    bot.validate_delivery_target = AsyncMock(return_value="333")

    with pytest.raises(RuntimeError, match="collection failed"):
        await bot._check_and_send(reply_context=None, force_empty=False)

    state = store.load()
    assert state["last_success"] is False
    assert state["last_error"] == "collection failed"


@pytest.mark.asyncio
async def test_empty_scheduled_check_consumes_start_binding(tmp_path) -> None:
    class EmptyAggregator:
        async def collect(self) -> AggregationResult:
            return AggregationResult(events=[])

    store = JsonStateStore(str(tmp_path / "state.json"))
    bot = TerminalCheckTelegramBot(
        settings=TelegramBotSettings(token="123:test", chat_id="2723jam"),
        aggregator=EmptyAggregator(),
        state_store=store,
        ports=[],
    )
    bot._try_bind_delivery_target = AsyncMock(return_value="333")

    await bot._check_and_send(reply_context=None, force_empty=False)

    bot._try_bind_delivery_target.assert_awaited_once_with()
    state = store.load()
    assert state["last_success"] is True
    assert state["recent_events"] == []


@pytest.mark.asyncio
async def test_event_is_persisted_before_telegram_delivery_validation(tmp_path) -> None:
    timezone = ZoneInfo("Asia/Shanghai")
    event = TerminalStatusEvent(
        country="China",
        port_code="NINGBO",
        terminal_name=None,
        status="planned",
        reason_category="weather",
        reason_detail="typhoon",
        reason_display_ko="\uae30\uc0c1\uc545\ud654(\ud0dc\ud48d)",
        start_time=datetime.now(timezone) + timedelta(hours=1),
        end_time=None,
        end_time_uncertain=True,
        source_url="https://www.npedi.com/contentDetail?contentId=59901",
        source_title="Ningbo suspension",
        raw_text="port operation suspended",
        confidence=0.9,
    )

    class EventAggregator:
        async def collect(self) -> AggregationResult:
            return AggregationResult(events=[event])

    store = JsonStateStore(str(tmp_path / "state.json"))
    bot = TerminalCheckTelegramBot(
        settings=TelegramBotSettings(token="123:test", chat_id="2723jam"),
        aggregator=EventAggregator(),
        state_store=store,
        ports=[],
    )
    bot.validate_delivery_target = AsyncMock(
        side_effect=BadRequest("chat not found")
    )

    with pytest.raises(BadRequest, match="chat not found"):
        await bot._check_and_send(reply_context=None, force_empty=False)

    state = store.load()
    assert len(state["recent_events"]) == 1
    assert state["recent_events"][0]["port_code"] == "NINGBO"
    assert state["recent_weather_risks"] == []
    assert state["last_success"] is False
