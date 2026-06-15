from datetime import datetime
from zoneinfo import ZoneInfo

from src.message_formatter import format_events
from src.models import TerminalStatusEvent


def test_qingdao_weather_heavy_rain_format() -> None:
    event = TerminalStatusEvent(
        country="China",
        port_code="QINGDAO",
        terminal_name=None,
        status="planned",
        reason_category="weather",
        reason_detail="heavy_rain",
        reason_display_ko="기상악화(폭우)",
        start_time=datetime(2026, 5, 29, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        end_time=datetime(2026, 5, 30, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        end_time_uncertain=True,
        source_url="https://www.sd.msa.gov.cn/",
        source_title="test",
        raw_text="青岛港因暴雨暂停作业",
        confidence=0.9,
    )

    assert format_events([event]) == (
        "※ QINGDAO\n"
        "중단사유 : 기상악화(폭우)\n"
        "중단기간 : 26.05.29 13:00 ~ 26.05.30 08:00 (미정)"
    )


def test_tianjin_military_format_date_only_open_end() -> None:
    event = TerminalStatusEvent(
        country="China",
        port_code="TIANJIN",
        terminal_name=None,
        status="planned",
        reason_category="military",
        reason_detail="military_or_navigation_control",
        reason_display_ko="군사훈련",
        start_time=datetime(2026, 5, 30, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        end_time=None,
        end_time_uncertain=True,
        source_url="https://www.msa.gov.cn/",
        source_title="test",
        raw_text="天津港附近实弹射击禁航",
        confidence=0.9,
    )

    assert format_events([event]) == (
        "※ TIANJIN\n"
        "중단사유 : 군사훈련\n"
        "중단기간 : 26.05.30 ~ 미정"
    )
