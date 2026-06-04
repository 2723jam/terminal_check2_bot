from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src import BOT_NAME
from src.models import CheckFailure, TerminalStatusEvent, WeatherRiskEvent


class JsonStateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._default_state()
        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def save(self, state: dict[str, Any]) -> None:
        temp_path = self.path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        temp_path.replace(self.path)

    def update_run(self, success: bool, error: str | None = None) -> None:
        state = self.load()
        state["last_run_at"] = datetime.now().astimezone().isoformat()
        state["last_success"] = success
        state["last_error"] = error
        self.save(state)

    def get_last_message_hash(self) -> str | None:
        return self.load().get("last_message_hash")

    def set_last_message_hash(self, value: str) -> None:
        state = self.load()
        state["last_message_hash"] = value
        self.save(state)

    def record_events(self, events: list[TerminalStatusEvent]) -> None:
        state = self.load()
        state["recent_events"] = [_event_to_json(event) for event in events[-50:]]
        self.save(state)

    def record_weather_risks(self, events: list[WeatherRiskEvent]) -> None:
        state = self.load()
        state["recent_weather_risks"] = [_event_to_json(event) for event in events[-50:]]
        self.save(state)

    def record_failures(self, failures: list[CheckFailure]) -> None:
        state = self.load()
        state["recent_failures"] = [_event_to_json(failure) for failure in failures[-50:]]
        state["last_failure_count"] = len(failures)
        self.save(state)

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {
            "bot_name": BOT_NAME,
            "last_run_at": None,
            "last_success": None,
            "last_error": None,
            "last_message_hash": None,
            "recent_events": [],
            "recent_weather_risks": [],
            "recent_failures": [],
            "last_failure_count": 0,
        }


def _event_to_json(event: TerminalStatusEvent) -> dict[str, Any]:
    if hasattr(event, "model_dump"):
        return event.model_dump(mode="json")
    return event.dict()
