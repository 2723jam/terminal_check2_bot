from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import yaml
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from src.adapters.base import BaseAdapter
from src.adapters.weather_classifier import WeatherClassifier
from src.models import PortConfig, TerminalStatusEvent

MAX_DETAIL_LINKS = 20
DETAIL_FETCH_CONCURRENCY = 4
MAX_OPEN_EVENT_AGE = timedelta(hours=36)
NOTICE_DETAIL_PATH_MARKERS = (
    "/hsskcx/hxtg/",
    "/hsskcx/hxjg/",
    "/NB/hsyw/",
    "/nw4411/",
    "/nw17239/",
    "/news/articles/",
)
BROAD_DETAIL_PATH_MARKERS = ("/nw4411/", "/nw17239/", "/news/articles/")
ARTICLE_BODY_SELECTORS = (
    "#ivs_content",
    ".p-section__article__body",
    ".article .view",
    ".TRS_UEDITOR",
    ".Article_content",
    "article",
)


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
        self.port_operation_context_keywords: list[str] = payload.get("port_operation_context_keywords", [])
        self.classifier = WeatherClassifier.from_yaml(keywords_config_path)
        weather_keywords = [
            keyword
            for keywords in payload.get("china_weather_keywords", {}).values()
            for keyword in keywords
        ]
        self.link_relevance_keywords = list(
            dict.fromkeys(
                [
                    *self.operation_stop_keywords,
                    *self.port_operation_context_keywords,
                    *weather_keywords,
                    *payload.get("china_military_keywords", []),
                    "omission",
                    "disruption",
                ]
            )
        )

    async def check(self, port: PortConfig) -> list[TerminalStatusEvent]:
        self.reset_failures()
        events: list[TerminalStatusEvent] = []
        seen_detail_urls: set[str] = set()
        for source_url in self.usable_source_urls(port):
            try:
                html = await self.fetch(source_url)
                detail_links = [
                    url
                    for url in self._extract_detail_links(port, source_url, html)
                    if url not in seen_detail_urls
                ]
                if detail_links:
                    seen_detail_urls.update(detail_links)
                    events.extend(await self._check_detail_links(port, detail_links))
                elif port.country == "China" and self._looks_like_listing_page(html):
                    continue
                else:
                    events.extend(self.parse(port=port, html=html, source_url=source_url))
            except Exception as exc:  # noqa: BLE001 - one bad source must not discard the port.
                self.record_failure(port.port_code, source_url, exc)
        return events

    async def _check_detail_links(self, port: PortConfig, detail_links: list[str]) -> list[TerminalStatusEvent]:
        semaphore = asyncio.Semaphore(DETAIL_FETCH_CONCURRENCY)

        async def check_one(detail_url: str) -> list[TerminalStatusEvent]:
            try:
                async with semaphore:
                    html = await self.fetch(detail_url)
                return self.parse(port=port, html=html, source_url=detail_url)
            except Exception as exc:  # noqa: BLE001 - keep checking other notices.
                self.record_failure(port.port_code, detail_url, exc)
                return []

        results = await asyncio.gather(*(check_one(url) for url in detail_links))
        return [event for result in results for event in result]

    def parse(self, port: PortConfig, html: str, source_url: str) -> list[TerminalStatusEvent]:
        text = self._extract_notice_text(html)
        if not text or not self._has_operation_stop_evidence(text):
            return []

        classification = (
            self.classifier.classify_weather(text)
            or self.classifier.classify_military_or_navigation(text)
            or self.classifier.classify_non_weather_reason(text)
        )
        terminal_name = self._detect_terminal(port, text)
        published_at = self._extract_published_at(html, port.timezone)
        start_time, end_time = self._extract_time_range(text, port.timezone, published_at)
        end_uncertain = self._contains_any(text, self.uncertain_end_keywords) or end_time is None
        now = datetime.now(ZoneInfo(port.timezone))
        if _is_invalid_or_past_period(start_time, end_time, now) or _is_stale_open_event(
            published_at, end_time, now
        ):
            return []
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
                source_published_at=published_at,
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

    @classmethod
    def _extract_notice_text(cls, html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        candidates: list[str] = []
        for selector in ARTICLE_BODY_SELECTORS:
            for node in soup.select(selector):
                text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
                if text:
                    candidates.append(text)

        body = max(candidates, key=len) if candidates else re.sub(
            r"\s+", " ", soup.get_text(" ", strip=True)
        ).strip()
        title = cls._extract_title(html)
        if title and title.casefold() not in body.casefold():
            return f"{title} {body}".strip()
        return body

    @staticmethod
    def _looks_like_listing_page(html: str) -> bool:
        soup = BeautifulSoup(html, "lxml")
        for meta in soup.find_all("meta"):
            key = str(meta.get("name") or meta.get("property") or "").casefold()
            if key == "articletitle" and meta.get("content"):
                return False
        if any(soup.select_one(selector) for selector in ARTICLE_BODY_SELECTORS[:-1]):
            return False
        return len(soup.find_all("a", href=True)) >= 10

    @staticmethod
    def _extract_title(html: str) -> str | None:
        soup = BeautifulSoup(html, "lxml")
        for meta in soup.find_all("meta"):
            key = str(meta.get("name") or meta.get("property") or "").casefold()
            if key in {"articletitle", "og:title", "twitter:title"} and meta.get("content"):
                return str(meta["content"]).strip()
        heading = soup.find(["h1", "h2", "h3"])
        if heading:
            return heading.get_text(" ", strip=True)
        if soup.title and soup.title.string:
            return soup.title.string.strip()
        return None

    def _extract_detail_links(self, port: PortConfig, source_url: str, html: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        candidates: list[tuple[int, int, str]] = []
        seen: set[str] = set()
        source_path = urlparse(source_url).path.rstrip("/")
        allowed_markers = NOTICE_DETAIL_PATH_MARKERS
        if source_path.casefold() == "/nb":
            allowed_markers = ("/NB/hsyw/",)

        for index, anchor in enumerate(soup.find_all("a", href=True)):
            href = str(anchor["href"]).strip()
            full_url = urljoin(source_url, href)
            path = urlparse(full_url).path
            marker = next((item for item in allowed_markers if item.casefold() in path.casefold()), None)
            if marker is None or not _is_detail_path(path):
                continue
            if full_url in seen:
                continue
            anchor_text = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
            relevant = self._link_is_relevant(port, anchor_text)
            if marker in BROAD_DETAIL_PATH_MARKERS and not relevant:
                continue
            seen.add(full_url)
            candidates.append((0 if relevant else 1, index, full_url))

        candidates.sort(key=lambda item: (item[0], item[1]))
        return [url for _, _, url in candidates[:MAX_DETAIL_LINKS]]

    def _link_is_relevant(self, port: PortConfig, text: str) -> bool:
        return self._contains_any(
            text,
            [*port.aliases, *port.terminals, *self.link_relevance_keywords],
        )

    @staticmethod
    def _detect_terminal(port: PortConfig, text: str) -> str | None:
        text_upper = text.upper()
        for terminal in port.terminals:
            if terminal.upper() in text_upper:
                return terminal
        return None

    def _extract_time_range(
        self,
        text: str,
        timezone_name: str,
        reference_time: datetime | None = None,
    ) -> tuple[datetime, datetime | None]:
        tz = ZoneInfo(timezone_name)
        reference = reference_time.astimezone(tz) if reference_time else datetime.now(tz)
        parsed = self._extract_datetime_matches(text, tz, reference)
        parsed = [item for item in parsed if not _is_as_of_timestamp(text, item[0])]
        if not parsed:
            if reference_time:
                return reference.replace(hour=0, minute=0, second=0, microsecond=0), None
            return reference, None

        start_span, _, start_time = parsed[0]
        end_time: datetime | None = None
        for end_span, _, candidate in parsed[1:]:
            between = text[start_span[1] : end_span[0]]
            if _RANGE_SEPARATOR_PATTERN.search(between):
                end_time = candidate
                if end_time < start_time and start_time.month == 12 and end_time.month == 1:
                    end_time = end_time.replace(year=end_time.year + 1)
                break
        return start_time, end_time

    def _extract_datetime_matches(
        self,
        text: str,
        tz: ZoneInfo,
        reference: datetime,
    ) -> list[tuple[tuple[int, int], bool, datetime]]:
        parsed: list[tuple[tuple[int, int], bool, datetime]] = []
        occupied: list[tuple[int, int]] = []
        for pattern in (_NUMERIC_DATE_PATTERN, _CHINESE_DATE_PATTERN):
            for match in pattern.finditer(text):
                value = self._parse_date_match(match, tz, reference)
                if value is None:
                    continue
                span = match.span()
                occupied.append(span)
                parsed.append((span, match.group("hour") is not None, value))

        for match in _CHINESE_TIME_ONLY_PATTERN.finditer(text):
            span = match.span()
            if any(_spans_overlap(span, used) for used in occupied):
                continue
            hour = int(match.group("hour"))
            minute = int(match.group("minute") or 0)
            if hour > 23 or minute > 59:
                continue
            value = reference.replace(hour=hour, minute=minute, second=0, microsecond=0)
            parsed.append((span, True, value))

        parsed.sort(key=lambda item: item[0][0])
        return parsed

    @staticmethod
    def _parse_date_match(match: re.Match[str], tz: ZoneInfo, reference: datetime) -> datetime | None:
        year_text = match.group("year")
        month = int(match.group("month"))
        day = int(match.group("day"))
        hour_text = match.group("hour")
        minute_text = match.group("minute")

        if not year_text:
            year = reference.year
        else:
            year = int(year_text)
            if year < 100:
                year += 2000

        hour = int(hour_text) if hour_text is not None else 0
        minute = int(minute_text) if minute_text is not None else 0
        try:
            value = datetime(year, month, day, hour, minute, tzinfo=tz)
        except ValueError:
            return None
        if not year_text and value < reference - timedelta(days=180):
            value = value.replace(year=value.year + 1)
        elif not year_text and value > reference + timedelta(days=180):
            value = value.replace(year=value.year - 1)
        return value

    @staticmethod
    def _extract_published_at(html: str, timezone_name: str) -> datetime | None:
        soup = BeautifulSoup(html, "lxml")
        values: list[str] = []
        published_keys = {
            "pubdate",
            "publishdate",
            "datepublished",
            "article:published_time",
            "og:published_time",
        }
        for meta in soup.find_all("meta"):
            key = str(meta.get("name") or meta.get("property") or "").casefold()
            if key in published_keys and meta.get("content"):
                values.append(str(meta["content"]))

        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                payload = json.loads(script.string or script.get_text())
            except (TypeError, ValueError):
                continue
            stack = payload if isinstance(payload, list) else [payload]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    if item.get("datePublished"):
                        values.append(str(item["datePublished"]))
                    stack.extend(item.values())
                elif isinstance(item, list):
                    stack.extend(item)

        for node in soup.find_all("time", datetime=True):
            values.append(str(node["datetime"]))

        tz = ZoneInfo(timezone_name)
        for value in values:
            try:
                parsed = date_parser.parse(value.replace("\u2236", ":").replace("\uff1a", ":"))
            except (TypeError, ValueError, OverflowError):
                continue
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=tz)
            return parsed.astimezone(tz)
        return None


_NUMERIC_DATE_PATTERN = re.compile(
    r"(?P<year>\d{2,4})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})"
    r"(?:[T\s]*(?P<hour>\d{1,2})[:\uff1a\u2236](?P<minute>\d{2})(?::\d{2}(?:\.\d+)?)?)?"
)
_CHINESE_DATE_PATTERN = re.compile(
    r"(?:(?P<year>\d{2,4})\u5e74)?(?P<month>\d{1,2})\u6708(?P<day>\d{1,2})\u65e5"
    r"(?:\s*(?P<hour>\d{1,2})(?:\u65f6|\u70b9)(?:(?P<minute>\d{1,2})\u5206?)?)?"
)
_CHINESE_TIME_ONLY_PATTERN = re.compile(
    r"(?<![\u6708\u65e5\d])(?P<hour>\d{1,2})(?:\u65f6|\u70b9)(?:(?P<minute>\d{1,2})\u5206?)?"
)
_RANGE_SEPARATOR_PATTERN = re.compile(
    r"(?:~|\uff5e|\u2014|\u2013|\bto\b|\u81f3|\u5230|\u622a\u6b62\u81f3)",
    re.IGNORECASE,
)


def _is_detail_path(path: str) -> bool:
    path_lower = path.casefold().rstrip("/")
    if "/news/articles/" in path_lower:
        return not path_lower.endswith("/news/articles")
    filename = path_lower.rsplit("/", 1)[-1]
    return filename.endswith(".html") and not filename.startswith("index")


def _spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _is_as_of_timestamp(text: str, span: tuple[int, int]) -> bool:
    prefix = text[max(0, span[0] - 4) : span[0]]
    return prefix.endswith(("\u622a\u81f3", "\u622a\u6b62"))


def _is_invalid_or_past_period(start_time: datetime, end_time: datetime | None, now: datetime) -> bool:
    if end_time is None:
        return False
    if end_time < start_time:
        return True
    return end_time < now


def _is_stale_open_event(
    published_at: datetime | None,
    end_time: datetime | None,
    now: datetime,
) -> bool:
    if published_at is None or end_time is not None:
        return False
    return now - published_at > MAX_OPEN_EVENT_AGE
