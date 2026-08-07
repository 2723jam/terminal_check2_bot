from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from zoneinfo import ZoneInfo

import yaml
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from src.adapters.base import BaseAdapter
from src.adapters.weather_classifier import WeatherClassifier
from src.models import PortConfig, TerminalStatusEvent

MAX_DETAIL_LINKS = 20
DETAIL_FETCH_CONCURRENCY = 4
MAX_OPEN_EVENT_AGE = timedelta(days=7)
NPEDI_HOSTS = {"npedi.com", "www.npedi.com"}
NPEDI_LIST_PATH = "/portal-api/index/content/list"
NPEDI_FULL_RECOVERY_KEYWORDS = (
    "\u6062\u590d\u8fdb\u63d0\u7bb1\u4f5c\u4e1a",
    "\u6062\u590d\u96c6\u88c5\u7bb1\u8fdb\u63d0\u4f5c\u4e1a",
    "\u5168\u9762\u6062\u590d",
    "\u89e3\u9664\u5c01\u6e2f",
)
NPEDI_OUTSIDE_NINGBO_MARKERS = (
    "\u5609\u5174\u6e2f\u52a1",
    "\u72b6\u5143\u5c99\u7801\u5934",
    "\u91d1\u6d0b\u7801\u5934",
    "\u53f0\u5dde\u6e2f\u52a1",
)
NON_STOP_RESTRICTION_KEYWORDS = {
    "\uc791\uc5c5 \uc81c\ud55c",
    "\uc791\uc5c5\uc81c\ud55c",
    "\u4f5c\u4e1a\u53d7\u9650",
    "h\u1ea1n ch\u1ebf khai th\u00e1c",
}
NOTICE_DETAIL_PATH_MARKERS = (
    "/hsskcx/hxtg/",
    "/hsskcx/hxjg/",
    "/NB/hsyw/",
    "/nw4411/",
    "/nw17239/",
    "/news/articles/",
    "/article/view.do",
    "/CMS/Board/Board.do",
    "/bordContDetail.do",
    "/art/",
    "/tzgg/",
    "/jtfwxx/",
    "/yjgl/",
    "/vi/tin-tuc/",
)
ARTICLE_BODY_SELECTORS = (
    ".inline-notice",
    ".npedi-notice",
    "#ivs_content",
    ".p-section__article__body",
    ".article .view",
    ".TRS_UEDITOR",
    ".Article_content",
    ".notice_view",
    "#board_view",
    ".board.board-detail",
    "#board-wrap",
    "#boardContents",
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
        configured_stop_keywords: list[str] = payload.get("operation_stop_keywords", [])
        self.operation_stop_keywords = [
            keyword
            for keyword in configured_stop_keywords
            if keyword not in NON_STOP_RESTRICTION_KEYWORDS
        ]
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
                if self._is_npedi_feed(source_url):
                    events.extend(await self._check_npedi_feed(port, source_url, html))
                    continue
                inline_notices = self._extract_inline_notice_documents(source_url, html)
                if inline_notices:
                    for notice_html, notice_url in inline_notices:
                        events.extend(self.parse(port, notice_html, notice_url))
                    continue
                detail_links = [
                    url
                    for url in self._extract_detail_links(port, source_url, html)
                    if url not in seen_detail_urls
                ]
                if detail_links:
                    seen_detail_urls.update(detail_links)
                    events.extend(await self._check_detail_links(port, detail_links))
                elif self._looks_like_listing_page(html):
                    if not self._source_listing_supported(source_url):
                        self.record_failure(
                            port.port_code,
                            source_url,
                            RuntimeError("listing page has no supported notice-detail parser"),
                        )
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
    async def _check_npedi_feed(
        self,
        port: PortConfig,
        source_url: str,
        payload_text: str,
    ) -> list[TerminalStatusEvent]:
        """Read Ningbo Port EDI's public operational-notice API.

        The API is the authoritative data source behind npedi.com's public notice
        page. Only fresh stop notices newer than the latest full recovery notice
        are opened and parsed.
        """
        try:
            payload = json.loads(payload_text)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Ningbo Port EDI list response") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        entries = data.get("list") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise ValueError("Ningbo Port EDI list response has no data.list")

        timezone = ZoneInfo(port.timezone)
        now = datetime.now(timezone)
        dated_entries: list[tuple[datetime, dict[str, object]]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            published_at = self._parse_npedi_datetime(entry.get("inputdate"), timezone)
            if published_at is not None:
                dated_entries.append((published_at, entry))

        recovery_times = [
            published_at
            for published_at, entry in dated_entries
            if self._is_npedi_full_recovery(entry)
        ]
        latest_recovery = max(recovery_times, default=None)
        stop_entries = [
            entry
            for published_at, entry in sorted(
                dated_entries,
                reverse=True,
                key=lambda item: item[0],
            )
            if now - published_at <= MAX_OPEN_EVENT_AGE
            and (latest_recovery is None or published_at > latest_recovery)
            and not self._is_npedi_full_recovery(entry)
            and self._has_operation_stop_evidence(self._npedi_entry_text(entry))
        ][:MAX_DETAIL_LINKS]

        parsed_source = urlparse(source_url)
        origin = f"{parsed_source.scheme}://{parsed_source.netloc}"
        semaphore = asyncio.Semaphore(DETAIL_FETCH_CONCURRENCY)

        async def check_one(entry: dict[str, object]) -> list[TerminalStatusEvent]:
            content_id = str(entry.get("contentId") or "").strip()
            if not content_id.isdigit():
                return []
            detail_api_url = f"{origin}/portal-api/index/content/{content_id}"
            public_url = f"{origin}/contentDetail?contentId={content_id}"
            try:
                async with semaphore:
                    detail_payload = await self.fetch(detail_api_url)
                notice_html = self._npedi_detail_document(detail_payload)
                return self.parse(port=port, html=notice_html, source_url=public_url)
            except Exception as exc:  # noqa: BLE001 - keep checking other notices.
                self.record_failure(port.port_code, detail_api_url, exc)
                return []

        results = await asyncio.gather(*(check_one(entry) for entry in stop_entries))
        return [event for result in results for event in result]

    @staticmethod
    def _is_npedi_feed(source_url: str) -> bool:
        parsed = urlparse(source_url)
        return parsed.netloc.casefold() in NPEDI_HOSTS and parsed.path == NPEDI_LIST_PATH

    @staticmethod
    def _parse_npedi_datetime(value: object, timezone: ZoneInfo) -> datetime | None:
        if not value:
            return None
        try:
            parsed = date_parser.parse(str(value))
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone)
        return parsed.astimezone(timezone)

    @staticmethod
    def _npedi_entry_text(entry: dict[str, object]) -> str:
        return " ".join(
            str(entry.get(field) or "")
            for field in ("title", "description", "keywords")
        )

    @classmethod
    def _is_npedi_full_recovery(cls, entry: dict[str, object]) -> bool:
        folded = cls._npedi_entry_text(entry).casefold()
        return any(
            keyword.casefold() in folded
            for keyword in NPEDI_FULL_RECOVERY_KEYWORDS
        )

    @staticmethod
    def _npedi_detail_document(payload_text: str) -> str:
        try:
            payload = json.loads(payload_text)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Ningbo Port EDI detail response") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError("Ningbo Port EDI detail response has no data")

        title = str(data.get("title") or "").strip()
        published_at = str(data.get("inputdate") or "").strip()
        content = str(data.get("content") or data.get("description") or "").strip()
        if not title or not content:
            raise ValueError("Ningbo Port EDI detail response is incomplete")

        soup = BeautifulSoup(content, "lxml")
        paragraphs = [
            re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
            for node in soup.find_all(["p", "li"])
        ]
        paragraphs = [paragraph for paragraph in paragraphs if paragraph]
        if not paragraphs:
            paragraphs = [
                re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
            ]

        ningbo_paragraphs: list[str] = []
        for paragraph in paragraphs:
            if any(marker in paragraph for marker in NPEDI_OUTSIDE_NINGBO_MARKERS):
                break
            ningbo_paragraphs.append(paragraph)
        body = " ".join(ningbo_paragraphs).strip()
        if not body:
            raise ValueError("Ningbo Port EDI detail has no Ningbo operation text")

        return (
            "<html><head>"
            f'<meta property="og:title" content="{html_lib.escape(title, quote=True)}">'
            f'<meta property="article:published_time" '
            f'content="{html_lib.escape(published_at, quote=True)}">'
            "</head><body>"
            f'<article class="npedi-notice">{html_lib.escape(body)}</article>'
            "</body></html>"
        )


    def parse(self, port: PortConfig, html: str, source_url: str) -> list[TerminalStatusEvent]:
        text = self._extract_notice_text(html)
        if not text or not self._has_operation_stop_evidence(text):
            return []
        if self._requires_explicit_port_identity(source_url) and not self._contains_any(
            text,
            [*port.aliases, *port.terminals],
        ):
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

        body = ""
        for selector in ARTICLE_BODY_SELECTORS:
            candidates = [
                re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
                for node in soup.select(selector)
                if node.get_text(" ", strip=True)
            ]
            if candidates:
                body = max(candidates, key=len)
                break
        if not body:
            body = re.sub(
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
        if any(soup.select_one(selector) for selector in ARTICLE_BODY_SELECTORS):
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
        for selector in (
            ".notice_view h3",
            ".notice_view h4",
            "#board_view .board_title",
            ".board-detail .title",
            ".board-view-title .vtitle",
        ):
            node = soup.select_one(selector)
            if node:
                return node.get_text(" ", strip=True)

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

        synthetic_links = [
            *self._extract_embedded_terminal_notice_links(source_url, html),
            *self._extract_embedded_datastore_links(source_url, html),
            *self._extract_javascript_detail_links(source_url, soup),
        ]
        for index, (anchor_text, full_url) in enumerate(synthetic_links):
            if full_url in seen:
                continue
            relevant = self._link_is_relevant(port, anchor_text)
            if self._requires_explicit_port_identity(source_url):
                relevant = self._link_names_port(port, anchor_text)
            seen.add(full_url)
            candidates.append((0 if relevant else 1, index, full_url))

        offset = len(synthetic_links)
        for index, anchor in enumerate(soup.find_all("a", href=True), start=offset):
            href = str(anchor["href"]).strip()
            full_url = urljoin(source_url, href)
            path = urlparse(full_url).path
            marker = next(
                (item for item in allowed_markers if item.casefold() in path.casefold()),
                None,
            )
            if marker is None or not _is_detail_url(full_url):
                continue
            if full_url in seen:
                continue
            anchor_text = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
            relevant = self._link_is_relevant(port, anchor_text)
            if self._requires_explicit_port_identity(source_url):
                relevant = self._link_names_port(port, anchor_text)
            if self._requires_link_relevance(source_url) and not relevant:
                continue
            seen.add(full_url)
            candidates.append((0 if relevant else 1, index, full_url))

        candidates.sort(key=lambda item: (item[0], item[1]))
        return [url for _, _, url in candidates[:MAX_DETAIL_LINKS]]

    @staticmethod
    def _requires_explicit_port_identity(source_url: str) -> bool:
        host = urlparse(source_url).netloc.casefold()
        return host in {"www.maersk.com", "maersk.com"}

    @staticmethod
    def _requires_link_relevance(source_url: str) -> bool:
        parsed = urlparse(source_url)
        host = parsed.netloc.casefold()
        return host in {
            "www.shanghai.gov.cn",
            "www.maersk.com",
            "maersk.com",
            "haiphongport.com.vn",
            "www.sz.msa.gov.cn",
        }

    @staticmethod
    def _source_listing_supported(source_url: str) -> bool:
        parsed = urlparse(source_url)
        host = parsed.netloc.casefold()
        path = parsed.path.casefold()
        return (
            (host.endswith("icpa.or.kr") and path.endswith("/article/list.do"))
            or (host in {"www.pnitl.com", "www.hpnt.co.kr"} and path.endswith("/homepage/webpage/"))
            or (host == "www.bptc.co.kr" and path.endswith("/cms/board/board.do"))
            or (host == "www.ygpa.or.kr" and path.endswith("/bordcontlistpgng.do"))
            or (
                host == "www.sd.msa.gov.cn"
                and any(column in path for column in ("/col/col5301/", "/col/col5304/"))
            )
            or (host == "www.sz.msa.gov.cn" and path.endswith("/tzgg/index.jhtml"))
            or (host == "www.shanghai.gov.cn" and path.endswith("/index.html"))
            or (
                host.endswith("zj.msa.gov.cn")
                and any(marker.casefold() in path for marker in NOTICE_DETAIL_PATH_MARKERS[:3])
            )
            or (host in {"www.maersk.com", "maersk.com"} and "/news/category/" in path)
            or (host == "haiphongport.com.vn" and path.rstrip("/") == "/vi/tin-tuc")
            or host == "eport.saigonnewport.com.vn"
            or (host.endswith("zj.msa.gov.cn") and path.rstrip("/") == "/nb")
        )

    @staticmethod
    def _extract_embedded_terminal_notice_links(
        source_url: str,
        html: str,
    ) -> list[tuple[str, str]]:
        host = urlparse(source_url).netloc.casefold()
        if host not in {"www.pnitl.com", "www.hpnt.co.kr"}:
            return []

        match = re.search(r"const\s+noticeList\s*=\s*(\[.*?\]);", html, re.DOTALL)
        if not match:
            return []
        try:
            payload = json.loads(match.group(1))
        except (TypeError, ValueError):
            return []

        links: list[tuple[str, str]] = []
        detail_url = urljoin(source_url, "cust_noti.jsp")
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, dict):
                continue
            bid = str(item.get("BID") or "").strip().casefold()
            seq = str(item.get("SEQ") or "").strip()
            if not bid or not seq:
                continue
            title = str(item.get("TITLE") or "").strip()
            query = urlencode({"BID": bid, "LTYPE": "VIEW", "seq": seq})
            links.append((title, f"{detail_url}?{query}"))
        return links

    @staticmethod
    def _extract_embedded_datastore_links(
        source_url: str,
        html: str,
    ) -> list[tuple[str, str]]:
        if urlparse(source_url).netloc.casefold() != "www.sd.msa.gov.cn":
            return []

        pattern = re.compile(
            r"<a\b[^>]*\bhref\s*=\s*(?P<quote>[\"'])"
            r"(?P<href>/art/[^\"']+\.html)(?P=quote)[^>]*>.*?</a>",
            re.IGNORECASE | re.DOTALL,
        )
        links: list[tuple[str, str]] = []
        for match in pattern.finditer(html):
            anchor = BeautifulSoup(match.group(0), "lxml").find("a")
            if anchor is None:
                continue
            title = str(anchor.get("title") or anchor.get_text(" ", strip=True)).strip()
            href = html_lib.unescape(match.group("href"))
            links.append((title, urljoin(source_url, href)))
        return links

    @staticmethod
    def _extract_javascript_detail_links(
        source_url: str,
        soup: BeautifulSoup,
    ) -> list[tuple[str, str]]:
        parsed = urlparse(source_url)
        host = parsed.netloc.casefold()
        source_query = parse_qs(parsed.query)
        links: list[tuple[str, str]] = []

        if host.endswith("icpa.or.kr") and parsed.path.casefold().endswith("/article/list.do"):
            menu_key = (source_query.get("menuKey") or [""])[0]
            board_key = (source_query.get("boardKey") or [""])[0]
            detail_path = parsed.path.rsplit("/", 1)[0] + "/view.do"
            for anchor in soup.find_all("a"):
                action = f"{anchor.get('onclick') or ''} {anchor.get('href') or ''}"
                match = re.search(r"articleView\(['\"]?(\d+)", action, re.IGNORECASE)
                if not match:
                    continue
                query = urlencode(
                    {
                        "articleKey": match.group(1),
                        "boardKey": board_key,
                        "menuKey": menu_key,
                        "currentPageNo": "1",
                    }
                )
                title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
                links.append((title, parsed._replace(path=detail_path, query=query, fragment="").geturl()))

        if host == "www.ygpa.or.kr" and parsed.path.casefold().endswith("/bordcontlistpgng.do"):
            bbs_no = (source_query.get("bbs_no") or [""])[0]
            detail_path = re.sub(
                r"bordContListPgng\.do$",
                "bordContDetail.do",
                parsed.path,
                flags=re.IGNORECASE,
            )
            for anchor in soup.find_all("a"):
                action = f"{anchor.get('onclick') or ''} {anchor.get('href') or ''}"
                match = re.search(r"contDetail\(['\"]([A-F0-9-]+)", action, re.IGNORECASE)
                if not match:
                    continue
                query = urlencode(
                    {"mode": "W", "bbs_no": bbs_no, "pst_no": match.group(1)}
                )
                title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
                links.append((title, parsed._replace(path=detail_path, query=query, fragment="").geturl()))

        return links

    @staticmethod
    def _extract_inline_notice_documents(
        source_url: str,
        html: str,
    ) -> list[tuple[str, str]]:
        if urlparse(source_url).netloc.casefold() != "eport.saigonnewport.com.vn":
            return []

        soup = BeautifulSoup(html, "lxml")
        documents: list[tuple[str, str]] = []
        for detail in soup.select("div.row.snp-card[id$='div']"):
            detail_id = str(detail.get("id") or "")
            notice_id = detail_id[:-3]
            if not notice_id:
                continue
            toggle = soup.find(id=notice_id)
            header = toggle.find_parent("div", class_="row") if toggle else None
            if header is None:
                header = detail.find_previous_sibling("div", class_="row")
            if header is None:
                continue

            header_text = re.sub(r"\s+", " ", header.get_text(" ", strip=True)).strip()
            date_match = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", header_text)
            title = re.sub(r"\b\d{2}/\d{2}/\d{4}\b", "", header_text)
            title = re.sub(r"Xem chi \S+", "", title).strip()
            body = re.sub(r"\s+", " ", detail.get_text(" ", strip=True)).strip()
            if not title or not body:
                continue

            published_meta = ""
            if date_match:
                day, month, year = date_match.groups()
                published_meta = (
                    f'<meta name="publishdate" content="{year}-{month}-{day}">'
                )
            notice_html = (
                "<html><head>"
                f"<title>{html_lib.escape(title)}</title>"
                f"{published_meta}</head><body>"
                f'<div class="inline-notice">{html_lib.escape(title)} '
                f"{html_lib.escape(body)}</div></body></html>"
            )
            documents.append((notice_html, f"{source_url}#{notice_id}"))
        return documents

    def _link_names_port(self, port: PortConfig, text: str) -> bool:
        return self._contains_any(
            text,
            [*port.aliases, *port.terminals],
        )

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
        groups = match.groupdict()
        minute_text = groups.get("minute") or groups.get("minute_colon")

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

        visible_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        values.extend(
            match.group("value")
            for match in _VISIBLE_PUBLISHED_DATE_PATTERN.finditer(visible_text)
        )
        if not values:
            for selector in (".notice_view", "#board_view", ".board.board-detail", ".board-view-head"):
                node = soup.select_one(selector)
                if node is None:
                    continue
                match = _NUMERIC_DATE_PATTERN.search(node.get_text(" ", strip=True))
                if match:
                    values.append(match.group(0))
                    break

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
    r"(?:\s*(?P<hour>\d{1,2})(?:(?:\u65f6|\u70b9)(?:(?P<minute>\d{1,2})\u5206?)?"
    r"|[:\uff1a](?P<minute_colon>\d{2})))?"
)
_CHINESE_TIME_ONLY_PATTERN = re.compile(
    r"(?<![\u6708\u65e5\d])(?P<hour>\d{1,2})(?:\u65f6|\u70b9)(?:(?P<minute>\d{1,2})\u5206?)?"
)
_RANGE_SEPARATOR_PATTERN = re.compile(
    r"(?:~|\uff5e|\u2014|\u2013|\bto\b|\u81f3|\u5230|\u622a\u6b62\u81f3)",
    re.IGNORECASE,
)

_VISIBLE_PUBLISHED_DATE_PATTERN = re.compile(
    r"(?:\ub4f1\ub85d\uc77c|\uc791\uc131\uc77c|\uac8c\uc2dc\uc77c|"
    r"\u53d1\u5e03\u65f6\u95f4|\u53d1\u5e03\u65e5\u671f|\u65e5\u671f|Published|Ng.y)"
    r"\s*[:\uff1a]?\s*(?P<value>\d{4}[./-]\d{1,2}[./-]\d{1,2}(?:\s+\d{1,2}:\d{2})?)",
    re.IGNORECASE,
)


def _is_detail_url(url: str) -> bool:
    parsed = urlparse(url)
    path_lower = parsed.path.casefold().rstrip("/")
    query = parse_qs(parsed.query)
    if "/news/articles/" in path_lower:
        return not path_lower.endswith("/news/articles")
    if path_lower.endswith("/article/view.do"):
        return bool(query.get("articleKey"))
    if path_lower.endswith("/cms/board/board.do"):
        return (query.get("mode") or [""])[0].casefold() == "view" and bool(
            query.get("board_seq")
        )
    if path_lower.endswith("/bordcontdetail.do"):
        return bool(query.get("pst_no"))
    filename = path_lower.rsplit("/", 1)[-1]
    return filename.endswith((".html", ".jhtml")) and not filename.startswith(
        "index"
    )


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
