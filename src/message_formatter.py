from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

from src.models import PortConfig, TerminalStatusEvent


COUNTRY_ORDER = {"Korea": 0, "China": 1, "Vietnam": 2}
PORT_ORDER = {
    "INCHEON": 0,
    "BUSAN": 1,
    "GWANGYANG": 2,
    "SHEKOU": 3,
    "QINGDAO": 4,
    "SHANGHAI": 5,
    "NINGBO": 6,
    "TIANJIN": 7,
    "HOCHIMINH": 8,
    "HAIPHONG": 9,
}

EMPTY_REPORT = "현재 확인된 항만/터미널 작업 중단 또는 중단 예정 건이 없습니다."


def sort_events(
    events: Iterable[TerminalStatusEvent],
    ports: Sequence[PortConfig] | None = None,
) -> list[TerminalStatusEvent]:
    timezone_by_port = {port.port_code: port.timezone for port in ports or []}

    def key(event: TerminalStatusEvent) -> tuple[int, int, datetime, str]:
        tz_name = timezone_by_port.get(event.port_code)
        local_start = event.start_time.astimezone(ZoneInfo(tz_name)) if tz_name else event.start_time
        return (
            COUNTRY_ORDER.get(event.country, 999),
            PORT_ORDER.get(event.port_code, 999),
            local_start,
            event.terminal_name or "",
        )

    return sorted(events, key=key)


def format_events(
    events: Iterable[TerminalStatusEvent],
    ports: Sequence[PortConfig] | None = None,
) -> str:
    sorted_items = sort_events(events, ports)
    blocks = [format_event(event, ports) for event in sorted_items]
    return "\n\n".join(blocks)


def format_event(event: TerminalStatusEvent, ports: Sequence[PortConfig] | None = None) -> str:
    header = f"※ {event.port_code}"
    if event.terminal_name:
        header += f" / {event.terminal_name}"
    return "\n".join(
        [
            header,
            f"중단사유 : {event.reason_display_ko}",
            f"중단기간 : {_format_period(event, ports)}",
        ]
    )


def split_telegram_message(message: str, max_length: int = 4096) -> list[str]:
    if len(message) <= max_length:
        return [message]

    blocks = message.split("\n\n")
    chunks: list[str] = []
    current = ""
    for block in blocks:
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= max_length - 16:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = block
        else:
            chunks.extend(_hard_split(block, max_length - 16))
            current = ""
    if current:
        chunks.append(current)

    total = len(chunks)
    return [f"[{index}/{total}]\n{chunk}" for index, chunk in enumerate(chunks, start=1)]


def message_hash(message: str) -> str:
    return sha256(message.encode("utf-8")).hexdigest()


def _format_period(event: TerminalStatusEvent, ports: Sequence[PortConfig] | None = None) -> str:
    start = _format_dt(_to_port_time(event.start_time, event.port_code, ports))
    if event.end_time is None:
        return f"{start} ~ 미정"

    end = _format_dt(_to_port_time(event.end_time, event.port_code, ports))
    suffix = " (미정)" if event.end_time_uncertain else ""
    return f"{start} ~ {end}{suffix}"


def _to_port_time(dt: datetime, port_code: str, ports: Sequence[PortConfig] | None = None) -> datetime:
    if not ports:
        return dt
    timezone_by_port = {port.port_code: port.timezone for port in ports}
    tz_name = timezone_by_port.get(port_code)
    return dt.astimezone(ZoneInfo(tz_name)) if tz_name else dt


def _format_dt(dt: datetime) -> str:
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt.strftime("%y.%m.%d")
    return dt.strftime("%y.%m.%d %H:%M")


def _hard_split(text: str, max_length: int) -> list[str]:
    return [text[index : index + max_length] for index in range(0, len(text), max_length)]
