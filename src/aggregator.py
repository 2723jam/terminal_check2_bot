from __future__ import annotations

import asyncio
from collections.abc import Iterable

from loguru import logger

from src.adapters.base import BaseAdapter
from src.adapters.china_msa import ChinaMSAAdapter
from src.adapters.official_notice import OfficialNoticeAdapter
from src.message_formatter import sort_events
from src.models import AggregationResult, CheckFailure, PortConfig, TerminalStatusEvent


class PortStatusAggregator:
    def __init__(
        self,
        ports: list[PortConfig],
        keywords_config_path: str,
        timeout_seconds: float = 20.0,
        user_agent: str | None = None,
    ) -> None:
        self.ports = ports
        self.keywords_config_path = keywords_config_path
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    async def collect(self) -> AggregationResult:
        tasks = [self._check_port(port) for port in self.ports]
        results = await asyncio.gather(*tasks)

        events: list[TerminalStatusEvent] = []
        failures: list[CheckFailure] = []
        for result in results:
            events.extend(result.events)
            failures.extend(result.failures)

        return AggregationResult(events=dedupe_events(sort_events(events, self.ports)), failures=failures)

    async def _check_port(self, port: PortConfig) -> AggregationResult:
        adapter = self._adapter_for_port(port)
        try:
            events = await adapter.check(port)
            return AggregationResult(events=events, failures=[])
        except Exception as exc:  # noqa: BLE001 - port-level isolation is intentional.
            logger.exception("port check failed: {}", port.port_code)
            return AggregationResult(
                events=[],
                failures=[CheckFailure(port_code=port.port_code, source_url=None, error=str(exc))],
            )

    def _adapter_for_port(self, port: PortConfig) -> BaseAdapter:
        adapter_cls = ChinaMSAAdapter if port.country == "China" else OfficialNoticeAdapter
        return adapter_cls(
            keywords_config_path=self.keywords_config_path,
            timeout_seconds=self.timeout_seconds,
            user_agent=self.user_agent,
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
