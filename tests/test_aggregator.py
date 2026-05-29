from datetime import datetime
from zoneinfo import ZoneInfo

from src.aggregator import dedupe_events
from src.message_formatter import format_events
from src.models import TerminalStatusEvent


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
    headers = [line for line in message.splitlines() if line.startswith("※ ")]
    assert headers == ["※ INCHEON", "※ BUSAN", "※ QINGDAO", "※ TIANJIN", "※ HAIPHONG"]


def test_dedupe_events() -> None:
    event = make_event("China", "QINGDAO", 8)
    assert dedupe_events([event, event]) == [event]
