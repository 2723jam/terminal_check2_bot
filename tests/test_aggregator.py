from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.adapters.china_msa import ChinaMSAAdapter
from src.adapters.official_notice import OfficialNoticeAdapter
from src.aggregator import dedupe_events
from src.message_formatter import format_events
from src.models import PortConfig, TerminalStatusEvent


def make_event(country: str, port_code: str, hour: int) -> TerminalStatusEvent:
    return TerminalStatusEvent(
        country=country,
        port_code=port_code,
        terminal_name=None,
        status="planned",
        reason_category="other",
        reason_detail="other",
        reason_display_ko="기타",
        start_time=datetime(2026, 5, 29, hour, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        end_time=None,
        end_time_uncertain=True,
        source_url=f"https://example.invalid/{port_code}",
        source_title="test",
        raw_text="작업 중단",
        confidence=0.8,
    )


def test_multiple_ports_integrated_and_sorted_in_one_message() -> None:
    events = [
        make_event("Vietnam", "HAIPHONG", 9),
        make_event("China", "TIANJIN", 9),
        make_event("Korea", "BUSAN", 9),
        make_event("China", "QINGDAO", 8),
        make_event("Korea", "INCHEON", 10),
    ]

    message = format_events(events)
    headers = [line for line in message.splitlines() if line.startswith("※")]
    assert headers == ["※ INCHEON", "※ BUSAN", "※ QINGDAO", "※ TIANJIN", "※ HAIPHONG"]


def test_dedupe_events() -> None:
    event = make_event("China", "QINGDAO", 8)
    assert dedupe_events([event, event]) == [event]


@pytest.mark.asyncio
async def test_official_notice_keeps_events_when_one_source_fails(tmp_path, monkeypatch) -> None:
    keywords = tmp_path / "keywords.yaml"
    keywords.write_text(
        """
operation_stop_keywords: ["operation suspended"]
uncertain_end_keywords: ["until further notice"]
reason_keywords:
  congestion: ["congestion"]
china_weather_keywords: {}
china_military_keywords: []
""",
        encoding="utf-8",
    )
    port = PortConfig(
        country="Korea",
        port_code="BUSAN",
        display_name="BUSAN",
        timezone="Asia/Seoul",
        aliases=["BUSAN"],
        terminals=["PNIT"],
        source_urls=["https://ok.example/notice", "https://bad.example/notice"],
    )
    adapter = OfficialNoticeAdapter(str(keywords))

    async def fake_fetch(url: str) -> str:
        if "bad" in url:
            raise RuntimeError("source down")
        return "<html><title>notice</title><body>PNIT operation suspended due to congestion 2026-05-29 13:00</body></html>"

    monkeypatch.setattr(adapter, "fetch", fake_fetch)

    events = await adapter.check(port)

    assert len(events) == 1
    assert events[0].terminal_name == "PNIT"
    assert events[0].reason_display_ko == "항만혼잡"
    assert len(adapter.last_failures) == 1
    assert adapter.last_failures[0].source_url == "https://bad.example/notice"


def test_china_msa_ignores_invalid_or_past_period() -> None:
    port = PortConfig(
        country="China",
        port_code="SHANGHAI",
        display_name="SHANGHAI",
        timezone="Asia/Shanghai",
        aliases=["上海", "上海港"],
        terminals=[],
        source_urls=[],
    )
    adapter = ChinaMSAAdapter("config/keywords.yaml")
    html = "<html><body>上海港附近军事训练，禁航时间 2026-05-25 至 2026-05-19</body></html>"

    assert adapter.parse(port, html, "https://www.sh.msa.gov.cn/") == []
