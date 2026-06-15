from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.adapters.official_notice import OfficialNoticeAdapter, _is_invalid_or_past_period
from src.models import PortConfig, TerminalStatusEvent


class ChinaMSAAdapter(OfficialNoticeAdapter):
    """Dedicated China MSA/navigation-warning adapter.

    Military drills, live-fire notices, navigation bans, and temporary sea controls
    are alertable for China even when they are not terminal-operator notices.
    Weather wording still requires explicit operation suspension evidence.
    """

    adapter_name = "china_msa"

    def parse(self, port: PortConfig, html: str, source_url: str) -> list[TerminalStatusEvent]:
        text = self._html_to_text(html)
        if not text:
            return []

        military = self.classifier.classify_military_or_navigation(text)
        weather = self.classifier.classify_weather(text)
        has_stop_evidence = self._has_operation_stop_evidence(text)
        alias_hit = self._mentions_port_or_terminal(port, text)

        if military and alias_hit:
            classification = military
        elif weather and has_stop_evidence and alias_hit:
            classification = weather
        else:
            return []

        terminal_name = self._detect_terminal(port, text)
        start_time, end_time = self._extract_time_range(text, port.timezone)
        now = datetime.now(ZoneInfo(port.timezone))
        if _is_invalid_or_past_period(start_time, end_time, now):
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
                source_published_at=None,
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
