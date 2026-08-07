from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from src.adapters.official_notice import (
    OfficialNoticeAdapter,
    _is_invalid_or_past_period,
    _is_stale_open_event,
)
from src.models import PortConfig, TerminalStatusEvent
ACTUAL_MILITARY_CONTROL_KEYWORDS = (
    "\u519b\u4e8b\u8bad\u7ec3",
    "\u519b\u4e8b\u6d3b\u52a8",
    "\u5b9e\u5f39\u5c04\u51fb",
    "\u7981\u822a",
    "\u7981\u9650\u822a",
    "\u9650\u5236\u822a\u884c",
    "\u4e34\u65f6\u7ba1\u5236",
    "\u5b9e\u65bd\u4ea4\u901a\u7ba1\u5236",
    "\u6d77\u4e0a\u6f14\u4e60",
    "\u5c04\u51fb\u8bad\u7ec3",
    "military exercise",
    "live-fire drill",
    "sea closure",
)


class ChinaMSAAdapter(OfficialNoticeAdapter):
    """Dedicated China MSA/navigation-warning adapter.

    Military drills, live-fire notices, navigation bans, and temporary sea controls
    are alertable for China even when they are not terminal-operator notices.
    Every alert requires explicit drill, control, closure, or suspension evidence.
    """

    adapter_name = "china_msa"

    def parse(self, port: PortConfig, html: str, source_url: str) -> list[TerminalStatusEvent]:
        text = self._extract_notice_text(html)
        if not text:
            return []

        military = self.classifier.classify_military_or_navigation(text)
        weather = self.classifier.classify_weather(text)
        source_implies_port = self._source_implies_port(port, source_url)
        alias_hit = self._mentions_port_or_terminal(port, text) or source_implies_port
        operation_evidence = self._find_port_operation_evidence(
            port, text, source_implies_port=source_implies_port
        )
        actual_military_control = military and self._contains_any(
            text, list(ACTUAL_MILITARY_CONTROL_KEYWORDS)
        )

        if actual_military_control and alias_hit:
            classification = military
            time_text = text
        elif operation_evidence and alias_hit:
            classification = weather or self.classifier.classify_non_weather_reason(text)
            time_text = operation_evidence
        else:
            return []

        terminal_name = self._detect_terminal(port, text)
        published_at = self._extract_published_at(html, port.timezone)
        start_time, end_time = self._extract_time_range(time_text, port.timezone, published_at)
        now = datetime.now(ZoneInfo(port.timezone))
        if _is_invalid_or_past_period(start_time, end_time, now) or _is_stale_open_event(
            published_at, end_time, now
        ):
            return []

        return [
            TerminalStatusEvent(
                country=port.country,
                port_code=port.port_code,
                terminal_name=terminal_name,
                status="planned" if start_time > now else "active",
                reason_category=classification.category,  # type: ignore[arg-type]
                reason_detail=classification.detail,
                reason_display_ko=classification.display_ko,
                start_time=start_time,
                end_time=end_time,
                end_time_uncertain=self._contains_any(text, self.uncertain_end_keywords) or end_time is None,
                source_url=source_url,
                source_title=self._extract_title(html),
                source_published_at=published_at,
                raw_text=text[:4000],
                confidence=0.9 if classification.category == "military" else 0.86,
            )
        ]

    @staticmethod
    def _mentions_port_or_terminal(port: PortConfig, text: str) -> bool:
        folded = text.lower()
        for value in [*port.aliases, *port.terminals]:
            if value and value.lower() in folded:
                return True
        return False

    @staticmethod
    def _source_implies_port(port: PortConfig, source_url: str) -> bool:
        host = urlparse(source_url).netloc.casefold()
        return port.port_code == "NINGBO" and host in {
            "npedi.com",
            "www.npedi.com",
        }


    def _find_port_operation_evidence(
        self,
        port: PortConfig,
        text: str,
        *,
        source_implies_port: bool = False,
    ) -> str | None:
        folded = text.casefold()
        candidates: list[tuple[int, int, str]] = []
        for keyword in self.operation_stop_keywords:
            keyword_folded = keyword.casefold()
            if not keyword_folded:
                continue
            cursor = 0
            while True:
                index = folded.find(keyword_folded, cursor)
                if index < 0:
                    break
                candidates.append((index, -len(keyword), keyword))
                cursor = index + max(1, len(keyword_folded))

        for index, _, keyword in sorted(candidates):
            sentence = self._sentence_around(text, index, index + len(keyword))
            if not self._sentence_has_port_scope(port, sentence):
                continue
            if source_implies_port:
                return text
            start = max(0, index - 220)
            end = min(len(text), index + len(keyword) + 280)
            window = text[start:end]
            if self._mentions_port_or_terminal(port, sentence):
                return sentence
            if self._mentions_port_or_terminal(port, window):
                return window
        return None

    @staticmethod
    def _sentence_around(text: str, start: int, end: int) -> str:
        separators = ".!?;\n\u3002\uff01\uff1f\uff1b"
        left = max(text.rfind(char, 0, start) for char in separators)
        right_candidates = [
            position
            for char in separators
            if (position := text.find(char, end)) >= 0
        ]
        right = min(right_candidates) if right_candidates else len(text)
        return text[left + 1 : right]

    def _sentence_has_port_scope(self, port: PortConfig, sentence: str) -> bool:
        if self._contains_any(sentence, self.port_operation_context_keywords):
            return True

        folded = sentence.casefold()
        specific_identifiers = [*port.terminals, *port.aliases[3:]]
        return any(
            value and value.casefold() in folded
            for value in specific_identifiers
        )
