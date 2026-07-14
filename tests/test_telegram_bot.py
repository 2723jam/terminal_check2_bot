from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from telegram import Chat, Message, Update, User

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


def test_binding_accepts_only_dedicated_private_start() -> None:
    unrelated = make_update(1, 111, "private", "/start another_bot")
    group = make_update(2, -222, "group", "/start terminal_check2_bot")
    dedicated = make_update(3, 333, "private", "/start terminal_check2_bot")

    assert (
        _binding_chat_id_from_updates(
            [unrelated, group, dedicated],
            "terminal_check2_bot",
        )
        == "333"
    )
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
