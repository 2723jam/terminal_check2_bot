from src.adapters.weather_classifier import WeatherClassifier


def classifier() -> WeatherClassifier:
    return WeatherClassifier.from_yaml("config/keywords.yaml")


def test_china_weather_keyword_details() -> None:
    cases = [
        ("青岛港因暴雨暂停作业", "heavy_rain", "기상악화(폭우)"),
        ("上海港受台风影响停止靠泊", "typhoon", "기상악화(태풍)"),
        ("宁波舟山港因暴雪暂停作业", "snow", "기상악화(폭설)"),
        ("天津港海上大风导致船舶作业暂停", "strong_wind", "기상악화(강풍)"),
        ("大雾造成青岛港低能见度，暂停靠泊", "fog", "기상악화(안개)"),
        ("恶劣海况导致宁波港作业暂停", "marine_bad_weather", "기상악화(해상악천후)"),
        ("气象灾害预警，港区作业受限", "unspecified", "기상악화(상세불명)"),
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
        "天津港附近海域军事训练，实施禁航",
        "青岛附近海域实弹射击临时管制",
        "temporary sea closure due to live-fire drill",
        "navigation warning: military exercise",
        "上海海域航行警告",
    ]:
        result = c.classify_military_or_navigation(text)
        assert result is not None
        assert result.category == "military"
        assert result.display_ko == "군사훈련"


def test_english_operator_weather_keywords() -> None:
    c = classifier()
    cases = [
        ("Port closure due to Typhoon Bavi", "typhoon"),
        ("Typhoon Bavi brings torrential rain to a closed port", "typhoon"),
        ("Terminal suspended after torrential rain", "heavy_rain"),
        ("Vessel operations suspended in dense fog", "fog"),
        ("Port closed because of rough seas", "marine_bad_weather"),
    ]
    for text, expected in cases:
        result = c.classify_weather(text)
        assert result is not None
        assert result.detail == expected


def test_vietnamese_weather_keyword_details() -> None:
    c = classifier()
    cases = [
        ("m\u01b0a l\u1edbn", "heavy_rain"),
        ("b\u00e3o", "typhoon"),
        ("gi\u00f3 m\u1ea1nh", "strong_wind"),
        ("s\u01b0\u01a1ng m\u00f9", "fog"),
        ("bi\u1ec3n \u0111\u1ed9ng", "marine_bad_weather"),
    ]

    for text, expected in cases:
        result = c.classify_weather(text)
        assert result is not None
        assert result.detail == expected
