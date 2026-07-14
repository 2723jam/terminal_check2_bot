from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


WEATHER_DETAIL_KO = {
    "heavy_rain": "폭우",
    "typhoon": "태풍",
    "snow": "폭설",
    "strong_wind": "강풍",
    "fog": "안개",
    "marine_bad_weather": "해상악천후",
    "unspecified": "상세불명",
}


@dataclass(frozen=True)
class Classification:
    category: str
    detail: str
    display_ko: str
    matched_keyword: str


class WeatherClassifier:
    """Classifies the reason detail after an official stop notice is confirmed."""

    def __init__(
        self,
        weather_keywords: dict[str, list[str]],
        military_keywords: list[str],
        reason_keywords: dict[str, list[str]] | None = None,
    ) -> None:
        self.weather_keywords = weather_keywords
        self.military_keywords = military_keywords
        self.reason_keywords = reason_keywords or {}

    @classmethod
    def from_yaml(cls, path: str | Path) -> "WeatherClassifier":
        with open(path, "r", encoding="utf-8") as handle:
            payload: dict[str, Any] = yaml.safe_load(handle) or {}
        return cls(
            weather_keywords=payload.get("china_weather_keywords", {}),
            military_keywords=payload.get("china_military_keywords", []),
            reason_keywords=payload.get("reason_keywords", {}),
        )

    def classify_weather(self, text: str) -> Classification | None:
        typhoon_keyword = self._best_list_match(text, self.weather_keywords.get("typhoon", []))
        if typhoon_keyword:
            detail, keyword = "typhoon", typhoon_keyword
        else:
            match = self._best_keyword_match(text, self.weather_keywords)
            if not match:
                return None
            detail, keyword = match
        display = f"기상악화({WEATHER_DETAIL_KO.get(detail, '상세불명')})"
        return Classification("weather", detail, display, keyword)

    def classify_military_or_navigation(self, text: str) -> Classification | None:
        keyword = self._best_list_match(text, self.military_keywords)
        if not keyword:
            return None
        return Classification("military", "military_or_navigation_control", "군사훈련", keyword)

    def classify_non_weather_reason(self, text: str) -> Classification:
        for category, keywords in self.reason_keywords.items():
            keyword = self._best_list_match(text, keywords)
            if keyword:
                display = {
                    "congestion": "항만혼잡",
                    "equipment": "장비고장",
                    "power": "정전",
                }.get(category, "기타")
                return Classification(category, category, display, keyword)
        return Classification("other", "other", "기타", "")

    @staticmethod
    def _best_keyword_match(text: str, keyword_map: dict[str, list[str]]) -> tuple[str, str] | None:
        candidates: list[tuple[int, str, str]] = []
        text_lower = text.lower()
        for detail, keywords in keyword_map.items():
            for keyword in keywords:
                if keyword and keyword.lower() in text_lower:
                    candidates.append((len(keyword), detail, keyword))
        if not candidates:
            return None
        _, detail, keyword = sorted(candidates, key=lambda item: item[0], reverse=True)[0]
        return detail, keyword

    @staticmethod
    def _best_list_match(text: str, keywords: list[str]) -> str | None:
        text_lower = text.lower()
        matches = [keyword for keyword in keywords if keyword and keyword.lower() in text_lower]
        if not matches:
            return None
        return sorted(matches, key=len, reverse=True)[0]
