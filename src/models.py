from __future__ import annotations

from datetime import datetime
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class PortConfig(BaseModel):
    country: str
    port_code: str
    display_name: str
    timezone: str
    latitude: float | None = None
    longitude: float | None = None
    aliases: list[str] = Field(default_factory=list)
    terminals: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    enabled: bool = True


class TerminalStatusEvent(BaseModel):
    country: str
    port_code: str
    terminal_name: str | None = None
    status: Literal["active", "planned"]
    reason_category: Literal["weather", "military", "congestion", "equipment", "power", "other"]
    reason_detail: str | None = None
    reason_display_ko: str
    start_time: datetime
    end_time: datetime | None = None
    end_time_uncertain: bool = False
    source_url: str
    source_title: str | None = None
    source_published_at: datetime | None = None
    raw_text: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class CheckFailure(BaseModel):
    port_code: str
    source_url: str | None = None
    error: str


class WeatherRiskEvent(BaseModel):
    country: str
    port_code: str
    risk_level: Literal["watch", "warning"]
    reason_detail: str
    reason_display_ko: str
    start_time: datetime
    end_time: datetime
    source_url: str
    raw_text: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class AggregationResult(BaseModel):
    events: list[TerminalStatusEvent] = Field(default_factory=list)
    weather_risks: list[WeatherRiskEvent] = Field(default_factory=list)
    failures: list[CheckFailure] = Field(default_factory=list)


def load_ports_config(path: str) -> list[PortConfig]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}

    ports = [PortConfig(**item) for item in payload.get("ports", [])]
    return [port for port in ports if port.enabled]
