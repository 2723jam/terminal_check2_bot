from src.adapters.weather_classifier import WeatherClassifier


def classifier() -> WeatherClassifier:
    return WeatherClassifier.from_yaml("config/keywords.yaml")


def test_china_weather_keyword_details() -> None:
    cases = [
        ("青岛港因暴雨暂停作业", "heavy_rain", "기상악화(폭우)"),
        ("上海港受台风影响停止作业", "typhoon", "기상악화(태풍)"),
        ("宁波港暴雪预警码头关闭", "snow", "기상악화(폭설)"),
        ("天津港雷雨大风暂停作业", "strong_wind", "기상악화(강풍)"),
        ("大雾导致低能见度，船舶作业暂停", "fog", "기상악화(안개)"),
        ("海况恶劣，码头停止作业", "marine_bad_weather", "기상악화(해상악천후)"),
        ("气象灾害预警，港区封闭", "unspecified", "기상악화(상세불명)"),
    ]

    c = classifier()
    for text, detail, display in cases:
        result = c.classify_weather(text)
        assert result is not None
        assert result.detail == detail
        assert result.display_ko == display


def test_military_and_navigation_control_keywords() -> None:
    c = classifier()
    for text in [
        "天津港附近海域军事训练，禁止驶入",
        "黄海海域实弹射击航行警告",
        "temporary sea closure due to live-fire drill",
        "navigation warning: military exercise",
        "临时管制，禁航",
    ]:
        result = c.classify_military_or_navigation(text)
        assert result is not None
        assert result.category == "military"
        assert result.display_ko == "군사훈련"
