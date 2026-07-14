from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.adapters.official_notice import (
    OfficialNoticeAdapter,
    _is_invalid_or_past_period,
    _is_stale_open_event,
)
from src.models import PortConfig, TerminalStatusEvent


class ChinaMSAAdapter(OfficialNoticeAdapter):
    """Dedicated China MSA/navigation-warning adapter.

    Military drills, live-fire notices, navigation bans, and temporary sea controls
    are alertable for China even when they are not terminal-operator notices.
    Weather wording still requires explicit operation suspension evidence.
    """

    adapter_name = "china_msa"

    def parse(self, port: PortConfig, html: str, source_url: str) -> list[TerminalStatusEvent]:
        text = self._extract_notice_text(html)
        if not text:
            return []

        military = self.classifier.classify_military_or_navigation(text)
        weather = self.classifier.classify_weather(text)
        operation_evidence = self._find_port_operation_evidence(port, text)
        alias_hit = self._mentions_port_or_terminal(port, text)

        if military and alias_hit:
            classification = military
            time_text = text
        elif weather and operation_evidence and alias_hit:
            classification = weather
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
                confidence=0.9 if military else 0.86,
            )
        ]

    @staticmethod
    def _mentions_port_or_terminal(port: PortConfig, text: str) -> bool:
        folded = text.lower()
        for value in [*port.aliases, *port.terminals]:
            if value and value.lower() in folded:
                return True
        return False

    def _find_port_operation_evidence(self, port: PortConfig, text: str) -> str | None:
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
