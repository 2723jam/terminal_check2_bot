from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.adapters.weather_risk import WeatherRiskAdapter
from src.message_formatter import format_weather_risks
from src.models import PortConfig, WeatherRiskEvent


def test_weather_risk_format_is_separate_from_suspension_alert() -> None:
    event = WeatherRiskEvent(
        country="China",
        port_code="QINGDAO",
        risk_level="warning",
        reason_detail="heavy_rain",
        reason_display_ko="폭우",
        start_time=datetime(2026, 5, 29, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        end_time=datetime(2026, 5, 29, 19, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        source_url="https://api.open-meteo.com/v1/forecast",
        raw_text="precipitation=12.0mm",
        confidence=0.72,
    )

    assert format_weather_risks([event]) == (
        "[기상 작업속도 우려 - 실제 중단 공지 아님]\n\n"
        "※ QINGDAO\n"
        "우려사유 : 폭우\n"
        "예상기간 : 26.05.29 13:00 ~ 26.05.29 19:00\n\n"
        "참고 : 공식 작업 중단 공지가 아닌 기상 기반 주의 알림입니다."
    )


def test_weather_risk_adapter_detects_heavy_rain() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    base = datetime.now(tz).replace(minute=0, second=0, microsecond=0)
    port = PortConfig(
        country="China",
        port_code="QINGDAO",
        display_name="QINGDAO",
        timezone="Asia/Shanghai",
        latitude=36.0671,
        longitude=120.3826,
    )
    payload = {
        "hourly": {
            "time": [(base + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(8)],
            "precipitation": [0, 11, 9, 5, 0, 0, 0, 0],
            "snowfall": [0] * 8,
            "weather_code": [3] * 8,
            "wind_gusts_10m": [4] * 8,
        }
    }

    events = WeatherRiskAdapter(horizon_hours=12).parse(port, payload)

    assert len(events) == 1
    assert events[0].port_code == "QINGDAO"
    assert events[0].reason_detail == "heavy_rain"
    assert events[0].reason_display_ko == "폭우"
