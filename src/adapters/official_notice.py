from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import yaml
from bs4 import BeautifulSoup

from src.adapters.base import BaseAdapter
from src.adapters.weather_classifier import WeatherClassifier
from src.models import PortConfig, TerminalStatusEvent


class OfficialNoticeAdapter(BaseAdapter):
    adapter_name = "official_notice"

    def __init__(
        self,
        keywords_config_path: str,
        timeout_seconds: float = 20.0,
        user_agent: str | None = None,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds, user_agent=user_agent)
        with open(keywords_config_path, "r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        self.operation_stop_keywords: list[str] = payload.get("operation_stop_keywords", [])
        self.uncertain_end_keywords: list[str] = payload.get("uncertain_end_keywords", [])
        self.classifier = WeatherClassifier.from_yaml(keywords_config_path)

    async def check(self, port: PortConfig) -> list[TerminalStatusEvent]:
        events: list[TerminalStatusEvent] = []
        for source_url in self.usable_source_urls(port):
            html = await self.fetch(source_url)
            events.extend(self.parse(port=port, html=html, source_url=source_url))
        return events

    def parse(self, port: PortConfig, html: str, source_url: str) -> list[TerminalStatusEvent]:
        text = self._html_to_text(html)
        if not text or not self._has_operation_stop_evidence(text):
            return []

        classification = (
            self.classifier.classify_weather(text)
            or self.classifier.classify_military_or_navigation(text)
            or self.classifier.classify_non_weather_reason(text)
        )
        terminal_name = self._detect_terminal(port, text)
        start_time, end_time = self._extract_time_range(text, port.timezone)
        end_uncertain = self._contains_any(text, self.uncertain_end_keywords) or end_time is None
        now = datetime.now(ZoneInfo(port.timezone))
        status = "planned" if start_time > now else "active"

        return [
            TerminalStatusEvent(
                country=port.country,
                port_code=port.port_code,
                terminal_name=terminal_name,
                status=status,
                reason_category=classification.category,  # type: ignore[arg-type]
                reason_detail=classification.detail,
                reason_display_ko=classification.display_ko,
                start_time=start_time,
                end_time=end_time,
                end_time_uncertain=end_uncertain,
                source_url=source_url,
                source_title=self._extract_title(html),
                source_published_at=None,
                raw_text=text[:4000],
                confidence=0.86,
            )
        ]

    def _has_operation_stop_evidence(self, text: str) -> bool:
        return self._contains_any(text, self.operation_stop_keywords)

    @staticmethod
    def _contains_any(text: str, keywords: list[str]) -> bool:
        text_lower = text.lower()
        return any(keyword.lower() in text_lower for keyword in keywords)

    @staticmethod
    def _html_to_text(html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()

    @staticmethod
    def _extract_title(html: str) -> str | None:
        soup = BeautifulSoup(html, "lxml")
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        heading = soup.find(["h1", "h2", "h3"])
        return heading.get_text(" ", strip=True) if heading else None

    @staticmethod
    def _detect_terminal(port: PortConfig, text: str) -> str | None:
        text_upper = text.upper()
        for terminal in port.terminals:
            if terminal.upper() in text_upper:
                return terminal
        return None

    def _extract_time_range(self, text: str, timezone_name: str) -> tuple[datetime, datetime | None]:
        tz = ZoneInfo(timezone_name)
        matches = list(_DATE_PATTERN.finditer(text))
        parsed = [self._parse_date_match(match, tz) for match in matches[:2]]
        parsed = [dt for dt in parsed if dt is not None]
        if not parsed:
            return datetime.now(tz), None
        if len(parsed) == 1:
            return parsed[0], None
        return parsed[0], parsed[1]

    @staticmethod
    def _parse_date_match(match: re.Match[str], tz: ZoneInfo) -> datetime | None:
        year_text = match.group("year")
        month = int(match.group("month"))
        day = int(match.group("day"))
        hour_text = match.group("hour")
        minute_text = match.group("minute")

        if not year_text:
            year = datetime.now(tz).year
        else:
            year = int(year_text)
            if year < 100:
                year += 2000

        hour = int(hour_text) if hour_text is not None else 0
        minute = int(minute_text) if minute_text is not None else 0
        try:
            return datetime(year, month, day, hour, minute, tzinfo=tz)
        except ValueError:
            return None


_DATE_PATTERN = re.compile(
    r"(?P<year>\d{2,4})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})"
    r"(?:\s*(?P<hour>\d{1,2}):(?P<minute>\d{2}))?"
)
