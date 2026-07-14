from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.adapters.china_msa import ChinaMSAAdapter
from src.adapters.official_notice import (
    OfficialNoticeAdapter,
    _is_stale_open_event,
)
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


def test_official_notice_extracts_zhejiang_detail_links() -> None:
    html = """
    <html><body>
      <a href="./202606/t20260612_15299.html">宁波大榭关外码头疏浚作业</a>
      <a href="https://www.zj.msa.gov.cn/ZJ/wszw/bmcx/hsskcx/hxjg/202606/t20260614_15318.html">浙航警</a>
      <a href="/ZJ/zjmsa/ldhd/202605/t20260509_14364.html">领导活动</a>
    </body></html>
    """
    port = PortConfig(
        country="China",
        port_code="NINGBO",
        display_name="NINGBO",
        timezone="Asia/Shanghai",
        aliases=["NINGBO"],
        terminals=[],
        source_urls=[],
    )
    adapter = OfficialNoticeAdapter("config/keywords.yaml")

    assert adapter._extract_detail_links(
        port,
        "https://www.zj.msa.gov.cn/ZJ/wszw/bmcx/hsskcx/hxtg/",
        html,
    ) == [
        "https://www.zj.msa.gov.cn/ZJ/wszw/bmcx/hsskcx/hxtg/202606/t20260612_15299.html",
        "https://www.zj.msa.gov.cn/ZJ/wszw/bmcx/hsskcx/hxjg/202606/t20260614_15318.html",
    ]


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


def make_shanghai_port() -> PortConfig:
    return PortConfig(
        country="China",
        port_code="SHANGHAI",
        display_name="SHANGHAI",
        timezone="Asia/Shanghai",
        aliases=[
            "SHANGHAI",
            "Shanghai",
            "\u4e0a\u6d77",
            "\u4e0a\u6d77\u6e2f",
            "\u6d0b\u5c71",
            "\u5916\u9ad8\u6865",
        ],
        terminals=["YANGSHAN", "WGQ", "SIPG"],
        source_urls=[],
    )


def test_china_time_parser_handles_chinese_ranges_and_separate_starts() -> None:
    adapter = ChinaMSAAdapter("config/keywords.yaml")
    timezone = ZoneInfo("Asia/Shanghai")
    reference = datetime(2026, 7, 10, 19, 0, tzinfo=timezone)

    start, end = adapter._extract_time_range(
        "\u81ea7\u670811\u65e58\u65f6\u81f37\u670813\u65e512\u65f6",
        "Asia/Shanghai",
        reference,
    )
    assert start == datetime(2026, 7, 11, 8, 0, tzinfo=timezone)
    assert end == datetime(2026, 7, 13, 12, 0, tzinfo=timezone)

    start, end = adapter._extract_time_range(
        "7\u670811\u65e58\u65f6\u8d77\u6682\u505c\u7a7a\u7bb1\u8fdb\u63d0\u7bb1\u4f5c\u4e1a\uff1b"
        "7\u670811\u65e510\u65f6\u8d77\u6682\u505c\u91cd\u7bb1\u8fdb\u63d0\u7bb1\u4f5c\u4e1a",
        "Asia/Shanghai",
        reference,
    )
    assert start == datetime(2026, 7, 11, 8, 0, tzinfo=timezone)
    assert end is None


def test_china_msa_accepts_explicit_shanghai_typhoon_closure() -> None:
    port = make_shanghai_port()
    adapter = ChinaMSAAdapter("config/keywords.yaml")
    timezone = ZoneInfo("Asia/Shanghai")
    now = datetime.now(timezone).replace(second=0, microsecond=0)
    planned_start = now + timedelta(hours=2)
    notice_text = (
        f"\u4e0a\u6d77\u6e2f\u53d7\u53f0\u98ce\u5f71\u54cd\uff0c"
        f"{planned_start.month}\u6708{planned_start.day}\u65e5"
        f"{planned_start.hour}\u65f6{planned_start.minute}\u5206\u8d77"
        "\u505c\u6b62\u8239\u8236\u8fdb\u6e2f\uff0c"
        "\u6062\u590d\u65f6\u95f4\u53e6\u884c\u901a\u77e5"
    )
    html = (
        "<html><head>"
        f"<meta name='ArticleTitle' content='{notice_text}'>"
        f"<meta name='PubDate' content='{now:%Y-%m-%d %H:%M}'>"
        "</head><body><div class='article'><div class='view'>"
        f"{notice_text}"
        "</div></div></body></html>"
    )

    events = adapter.parse(port, html, "https://www.shanghai.gov.cn/notice.html")

    assert len(events) == 1
    assert events[0].reason_detail == "typhoon"
    assert events[0].start_time == planned_start
    assert events[0].end_time is None
    assert events[0].end_time_uncertain is True


def test_china_msa_accepts_recent_operator_planned_port_closure() -> None:
    port = make_shanghai_port()
    adapter = ChinaMSAAdapter("config/keywords.yaml")
    published = datetime.now(ZoneInfo("Asia/Shanghai")).replace(microsecond=0)
    html = f"""
    <html><head>
      <meta property="og:title" content="Typhoon service update">
      <script type="application/ld+json">
        {{"@type":"NewsArticle","datePublished":"{published.isoformat()}"}}
      </script>
    </head><body>
      <div class="p-section__article__body">
        Due to Typhoon Bavi, the ports of Shanghai and Ningbo are expected
        to experience port closures and extended waiting times.
      </div>
    </body></html>
    """

    events = adapter.parse(port, html, "https://www.maersk.com/news/articles/example")

    assert len(events) == 1
    assert events[0].reason_detail == "typhoon"
    assert events[0].source_title == "Typhoon service update"


def test_china_msa_rejects_weather_forecast_and_unrelated_work_stop() -> None:
    port = make_shanghai_port()
    adapter = ChinaMSAAdapter("config/keywords.yaml")
    forecast = (
        "<html><body>\u4e0a\u6d77\u6e2f\u53d7\u53f0\u98ce\u5f71\u54cd\uff0c"
        "\u9884\u8ba1\u6709\u5f3a\u98ce\u548c\u66b4\u96e8</body></html>"
    )
    unrelated_stop = (
        "<html><body>\u4e0a\u6d77\u53d7\u53f0\u98ce\u5f71\u54cd\uff0c"
        "\u6237\u5916\u7279\u79cd\u8bbe\u5907\u505c\u6b62\u4f5c\u4e1a\u3002"
        "\u6d0b\u5c71\u6e2f\u533a\u8bbe\u5907\u5df2\u5b8c\u6210\u52a0\u56fa\u3002"
        "</body></html>"
    )

    assert adapter.parse(port, forecast, "https://www.shanghai.gov.cn/forecast.html") == []
    assert adapter.parse(port, unrelated_stop, "https://www.shanghai.gov.cn/safety.html") == []


def test_broad_official_and_operator_lists_keep_only_relevant_details() -> None:
    port = make_shanghai_port()
    adapter = ChinaMSAAdapter("config/keywords.yaml")
    maersk_html = """
    <html><body>
      <a href="/news/articles/2026/07/13/structural-change">Structural changes</a>
      <a href="/news/articles/2026/07/10/typhoon-update">
        Shanghai omission and Ningbo omission
      </a>
    </body></html>
    """
    assert adapter._extract_detail_links(
        port,
        "https://www.maersk.com/news/category/advisories",
        maersk_html,
    ) == ["https://www.maersk.com/news/articles/2026/07/10/typhoon-update"]

    shanghai_html = (
        "<html><body>"
        "<a href='/nw4411/20260712/unrelated.html'>"
        "\u57ce\u5e02\u6587\u5316\u6d3b\u52a8"
        "</a>"
        "<a href='/nw4411/20260712/port.html'>"
        "\u4ece\u6e2f\u53e3\u7801\u5934\u5230\u5730\u94c1\u673a\u573a"
        "</a>"
        "</body></html>"
    )
    assert adapter._extract_detail_links(
        port,
        "https://www.shanghai.gov.cn/nw4411/index.html",
        shanghai_html,
    ) == ["https://www.shanghai.gov.cn/nw4411/20260712/port.html"]


@pytest.mark.asyncio
async def test_china_check_does_not_parse_listing_page_as_one_notice(monkeypatch) -> None:
    port = make_shanghai_port().model_copy(
        update={"source_urls": ["https://www.sh.msa.gov.cn/"]}
    )
    adapter = ChinaMSAAdapter("config/keywords.yaml")
    links = "".join(
        f"<a href='/news/{index}'>item {index}</a>"
        for index in range(12)
    )
    html = (
        "<html><head><title>MSA home</title></head><body>"
        + links
        + "\u4e0a\u6d77\u6e2f\u9644\u8fd1\u6d77\u57df"
        + "\u519b\u4e8b\u8bad\u7ec3 2026-07-02 \u81f3 2026-08-15"
        + "</body></html>"
    )

    async def fake_fetch(url: str) -> str:
        return html

    monkeypatch.setattr(adapter, "fetch", fake_fetch)

    events = await adapter.check(port)

    assert events == []


def test_open_ended_notice_expires_after_freshness_window() -> None:
    timezone = ZoneInfo("Asia/Shanghai")
    published = datetime(2026, 7, 10, 10, 0, tzinfo=timezone)

    assert _is_stale_open_event(
        published, None, published + timedelta(hours=36, seconds=1)
    )
    assert not _is_stale_open_event(published, None, published + timedelta(hours=35))
