from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from src.models import PortConfig, WeatherRiskEvent


OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass(frozen=True)
class WeatherRiskThresholds:
    heavy_rain_mm_per_hour: float = 10.0
    heavy_rain_mm_6h: float = 25.0
    strong_wind_gust_ms: float = 17.0
    marine_bad_weather_gust_ms: float = 21.0
    snow_mm_per_hour: float = 2.0


def _is_retryable_fetch_error(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {429, 500, 502, 503, 504}:
        return True
    return False


class WeatherRiskAdapter:
    """Forecast-based work-speed risk monitor.

    This adapter never creates a terminal suspension event. It produces a
    separate weather-risk advisory for productivity concerns only.
    """

    def __init__(
        self,
        timeout_seconds: float = 20.0,
        user_agent: str | None = None,
        thresholds: WeatherRiskThresholds | None = None,
        horizon_hours: int = 12,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent or "terminal_check2_bot/1.0"
        self.thresholds = thresholds or WeatherRiskThresholds()
        self.horizon_hours = horizon_hours

    async def check(self, port: PortConfig) -> list[WeatherRiskEvent]:
        if port.latitude is None or port.longitude is None:
            return []
        payload = await self.fetch(port)
        return self.parse(port, payload)

    @retry(
        retry=retry_if_exception(_is_retryable_fetch_error),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def fetch(self, port: PortConfig) -> dict:
        params = {
            "latitude": port.latitude,
            "longitude": port.longitude,
            "hourly": ",".join(
                [
                    "precipitation",
                    "rain",
                    "showers",
                    "snowfall",
                    "weather_code",
                    "wind_speed_10m",
                    "wind_gusts_10m",
                ]
            ),
            "wind_speed_unit": "ms",
            "forecast_days": 2,
            "timezone": port.timezone,
        }
        headers = {"User-Agent": self.user_agent}
        async with httpx.AsyncClient(timeout=self.timeout_seconds, headers=headers) as client:
            response = await client.get(OPEN_METEO_URL, params=params)
            response.raise_for_status()
            return response.json()

    def parse(self, port: PortConfig, payload: dict) -> list[WeatherRiskEvent]:
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            return []

        tz = ZoneInfo(port.timezone)
        now = datetime.now(tz)
        horizon_end = now + timedelta(hours=self.horizon_hours)
        precip = hourly.get("precipitation") or []
        snowfall = hourly.get("snowfall") or []
        codes = hourly.get("weather_code") or []
        wind_gusts = hourly.get("wind_gusts_10m") or []

        risk_points: list[tuple[datetime, str, str, str]] = []
        for index, time_text in enumerate(times):
            hour = datetime.fromisoformat(time_text).replace(tzinfo=tz)
            if hour < now.replace(minute=0, second=0, microsecond=0) or hour > horizon_end:
                continue
            detail, display = self._risk_for_hour(index, precip, snowfall, codes, wind_gusts)
            if detail:
                risk_points.append((hour, detail, display, self._raw_snapshot(index, precip, snowfall, codes, wind_gusts)))

        if not risk_points:
            return []

        start = risk_points[0][0]
        end = risk_points[-1][0] + timedelta(hours=1)
        detail = self._highest_priority_detail([point[1] for point in risk_points])
        display = WEATHER_RISK_DISPLAY_KO[detail]
        level = "warning" if detail in {"marine_bad_weather", "heavy_rain"} else "watch"
        raw_text = "; ".join(point[3] for point in risk_points[:6])

        return [
            WeatherRiskEvent(
                country=port.country,
                port_code=port.port_code,
                risk_level=level,
                reason_detail=detail,
                reason_display_ko=display,
                start_time=start,
                end_time=end,
                source_url=OPEN_METEO_URL,
                raw_text=raw_text,
                confidence=0.72,
            )
        ]

    def _risk_for_hour(
        self,
        index: int,
        precip: list[float | int | None],
        snowfall: list[float | int | None],
        codes: list[int | None],
        wind_gusts: list[float | int | None],
    ) -> tuple[str | None, str | None]:
        precipitation = _value_at(precip, index)
        snow = _value_at(snowfall, index)
        code = int(_value_at(codes, index) or 0)
        gust = _value_at(wind_gusts, index)
        rolling_6h = sum(_value_at(precip, i) for i in range(index, min(index + 6, len(precip))))

        if gust >= self.thresholds.marine_bad_weather_gust_ms:
            return "marine_bad_weather", WEATHER_RISK_DISPLAY_KO["marine_bad_weather"]
        if precipitation >= self.thresholds.heavy_rain_mm_per_hour or rolling_6h >= self.thresholds.heavy_rain_mm_6h:
            return "heavy_rain", WEATHER_RISK_DISPLAY_KO["heavy_rain"]
        if gust >= self.thresholds.strong_wind_gust_ms:
            return "strong_wind", WEATHER_RISK_DISPLAY_KO["strong_wind"]
        if snow >= self.thresholds.snow_mm_per_hour:
            return "snow", WEATHER_RISK_DISPLAY_KO["snow"]
        if code in {45, 48}:
            return "fog", WEATHER_RISK_DISPLAY_KO["fog"]
        if code in {95, 96, 99}:
            return "marine_bad_weather", WEATHER_RISK_DISPLAY_KO["marine_bad_weather"]
        return None, None

    @staticmethod
    def _highest_priority_detail(details: list[str]) -> str:
        priority = ["marine_bad_weather", "heavy_rain", "strong_wind", "fog", "snow"]
        for item in priority:
            if item in details:
                return item
        return details[0]

    @staticmethod
    def _raw_snapshot(
        index: int,
        precip: list[float | int | None],
        snowfall: list[float | int | None],
        codes: list[int | None],
        wind_gusts: list[float | int | None],
    ) -> str:
        return (
            f"hour+{index}: precipitation={_value_at(precip, index)}mm, "
            f"snowfall={_value_at(snowfall, index)}mm, "
            f"weather_code={_value_at(codes, index)}, "
            f"wind_gust={_value_at(wind_gusts, index)}m/s"
        )


WEATHER_RISK_DISPLAY_KO = {
    "heavy_rain": "폭우",
    "strong_wind": "강풍",
    "marine_bad_weather": "해상악천후",
    "fog": "안개",
    "snow": "폭설",
}


def _value_at(values: list[float | int | None], index: int) -> float:
    if index >= len(values) or values[index] is None:
        return 0.0
    return float(values[index])
