from __future__ import annotations

import asyncio
from collections.abc import Iterable

from loguru import logger

from src.adapters.base import BaseAdapter
from src.adapters.china_msa import ChinaMSAAdapter
from src.adapters.official_notice import OfficialNoticeAdapter
from src.adapters.weather_risk import WeatherRiskAdapter
from src.message_formatter import sort_events
from src.models import AggregationResult, CheckFailure, PortConfig, TerminalStatusEvent, WeatherRiskEvent

WEATHER_RISK_CONCURRENCY = 5


class PortStatusAggregator:
    def __init__(
        self,
        ports: list[PortConfig],
        keywords_config_path: str,
        timeout_seconds: float = 20.0,
        user_agent: str | None = None,
        weather_risk_enabled: bool = False,
        weather_risk_horizon_hours: int = 12,
    ) -> None:
        self.ports = ports
        self.keywords_config_path = keywords_config_path
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.weather_risk_enabled = weather_risk_enabled
        self.weather_risk_horizon_hours = weather_risk_horizon_hours

    async def collect(self) -> AggregationResult:
        notice_tasks = [self._check_port(port) for port in self.ports]
        notice_results = await asyncio.gather(*notice_tasks)
        weather_results: list[AggregationResult] = []
        if self.weather_risk_enabled:
            weather_results = await self._collect_weather_risks()

        events: list[TerminalStatusEvent] = []
        weather_risks: list[WeatherRiskEvent] = []
        failures: list[CheckFailure] = []
        for result in [*notice_results, *weather_results]:
            events.extend(result.events)
            weather_risks.extend(result.weather_risks)
            failures.extend(result.failures)

        return AggregationResult(
            events=dedupe_events(sort_events(events, self.ports)),
            weather_risks=dedupe_weather_risks(weather_risks),
            failures=failures,
        )

    async def _check_port(self, port: PortConfig) -> AggregationResult:
        adapter = self._adapter_for_port(port)
        if not BaseAdapter.usable_source_urls(port):
            return AggregationResult(
                events=[],
                failures=[
                    CheckFailure(
                        port_code=port.port_code,
                        source_url=None,
                        error="no usable source URL configured",
                    )
                ],
            )
        try:
            events = await adapter.check(port)
            return AggregationResult(events=events, failures=adapter.last_failures)
        except Exception as exc:  # noqa: BLE001 - port-level isolation is intentional.
            logger.exception("port check failed: {}", port.port_code)
            return AggregationResult(
                events=[],
                failures=[
                    *adapter.last_failures,
                    CheckFailure(
                        port_code=port.port_code,
                        source_url=None,
                        error=str(exc),
                    ),
                ],
            )

    def _adapter_for_port(self, port: PortConfig) -> BaseAdapter:
        adapter_cls = ChinaMSAAdapter if port.country == "China" else OfficialNoticeAdapter
        return adapter_cls(
            keywords_config_path=self.keywords_config_path,
            timeout_seconds=self.timeout_seconds,
            user_agent=self.user_agent,
        )

    async def _collect_weather_risks(self) -> list[AggregationResult]:
        semaphore = asyncio.Semaphore(WEATHER_RISK_CONCURRENCY)

        async def check_one(port: PortConfig) -> AggregationResult:
            async with semaphore:
                return await self._check_weather_risk(port)

        return list(await asyncio.gather(*(check_one(port) for port in self.ports)))

    async def _check_weather_risk(self, port: PortConfig) -> AggregationResult:
        adapter = WeatherRiskAdapter(
            timeout_seconds=self.timeout_seconds,
            user_agent=self.user_agent,
            horizon_hours=self.weather_risk_horizon_hours,
        )
        try:
            weather_risks = await adapter.check(port)
            return AggregationResult(events=[], weather_risks=weather_risks, failures=[])
        except Exception as exc:  # noqa: BLE001 - risk checks should not block stop alerts.
            logger.exception("weather risk check failed: {}", port.port_code)
            return AggregationResult(
                events=[],
                weather_risks=[],
                failures=[CheckFailure(port_code=port.port_code, source_url=None, error=str(exc))],
            )


def dedupe_events(events: Iterable[TerminalStatusEvent]) -> list[TerminalStatusEvent]:
    seen: set[tuple[str, str | None, str, str, str, str | None]] = set()
    unique: list[TerminalStatusEvent] = []
    for event in events:
        key = (
            event.port_code,
            event.terminal_name,
            event.reason_category,
            event.start_time.isoformat(),
            event.source_url,
            event.end_time.isoformat() if event.end_time else None,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique


def dedupe_weather_risks(events: Iterable[WeatherRiskEvent]) -> list[WeatherRiskEvent]:
    seen: set[tuple[str, str, str, str]] = set()
    unique: list[WeatherRiskEvent] = []
    for event in events:
        key = (
            event.port_code,
            event.reason_detail,
            event.start_time.isoformat(),
            event.end_time.isoformat(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(event)
    return unique
