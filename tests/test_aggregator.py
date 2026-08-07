import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.adapters.china_msa import ChinaMSAAdapter
from src.adapters.official_notice import (
    OfficialNoticeAdapter,
    _is_stale_open_event,
)
from src.aggregator import (
    WEATHER_RISK_CONCURRENCY,
    PortStatusAggregator,
    dedupe_events,
)
from src.message_formatter import format_events
from src.models import AggregationResult, PortConfig, TerminalStatusEvent


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

    qingdao = port.model_copy(
        update={"port_code": "QINGDAO", "aliases": ["QINGDAO"]}
    )
    assert adapter._extract_detail_links(
        qingdao,
        "https://www.maersk.com/news/category/advisories",
        maersk_html,
    ) == []
    unrelated_article = """
    <div class="p-section__article__body">
      Shanghai port operations suspended due to typhoon.
    </div>
    """
    assert adapter.parse(
        qingdao,
        unrelated_article,
        "https://www.maersk.com/news/articles/2026/07/10/typhoon-update",
    ) == []

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
        published, None, published + timedelta(days=7, seconds=1)
    )
    assert not _is_stale_open_event(published, None, published + timedelta(days=6, hours=23))


def test_terminal_notice_lists_generate_verified_detail_urls() -> None:
    adapter = OfficialNoticeAdapter("config/keywords.yaml")
    port = PortConfig(
        country="Korea",
        port_code="BUSAN",
        display_name="BUSAN",
        timezone="Asia/Seoul",
        aliases=["BUSAN"],
        terminals=["PNIT"],
        source_urls=[],
    )
    pnit_html = """
    <script>
      const noticeList = [
        {"BID":"noti_pnit","SEQ":"5525","TITLE":"PNIT operation suspended"}
      ];
    </script>
    """
    assert adapter._extract_detail_links(
        port,
        "https://www.pnitl.com/homepage/webpage/",
        pnit_html,
    ) == [
        "https://www.pnitl.com/homepage/webpage/cust_noti.jsp?"
        "BID=noti_pnit&LTYPE=VIEW&seq=5525"
    ]

    incheon = port.model_copy(
        update={"port_code": "INCHEON", "aliases": ["INCHEON", "IPA"]}
    )
    scon_html = """
    <a href="#" onclick="articleView('2800')">IPA terminal operation suspended</a>
    """
    assert adapter._extract_detail_links(
        incheon,
        "https://scon.icpa.or.kr/article/list.do?menuKey=127&boardKey=0",
        scon_html,
    ) == [
        "https://scon.icpa.or.kr/article/view.do?"
        "articleKey=2800&boardKey=0&menuKey=127&currentPageNo=1"
    ]

    gwangyang = port.model_copy(
        update={"port_code": "GWANGYANG", "aliases": ["GWANGYANG", "YGPA"]}
    )
    ygpa_html = """
    <a href="#" onclick="contDetail('ABC-123')">YGPA port operation suspended</a>
    """
    assert adapter._extract_detail_links(
        gwangyang,
        "https://www.ygpa.or.kr/hmpg/ygpa/comu/news/anuc/"
        "bordContListPgng.do?bbs_no=213&miv_pageNo=1&miv_pageSize=10",
        ygpa_html,
    ) == [
        "https://www.ygpa.or.kr/hmpg/ygpa/comu/news/anuc/"
        "bordContDetail.do?mode=W&bbs_no=213&pst_no=ABC-123"
    ]


def test_normal_notice_lists_generate_verified_detail_urls() -> None:
    adapter = OfficialNoticeAdapter("config/keywords.yaml")
    qingdao = PortConfig(
        country="China",
        port_code="QINGDAO",
        display_name="QINGDAO",
        timezone="Asia/Shanghai",
        aliases=["QINGDAO"],
        terminals=[],
        source_urls=[],
    )
    qingdao_html = """
    <script type="text/xml"><datastore>
      <a href="/art/2026/7/14/art_5301_12345.html">Navigation warning</a>
    </datastore></script>
    """
    assert adapter._extract_detail_links(
        qingdao,
        "https://www.sd.msa.gov.cn/col/col5301/index.html",
        qingdao_html,
    ) == ["https://www.sd.msa.gov.cn/art/2026/7/14/art_5301_12345.html"]

    busan = qingdao.model_copy(
        update={"country": "Korea", "port_code": "BUSAN", "aliases": ["BUSAN"]}
    )
    bpt_html = (
        '<a href="/kor/CMS/Board/Board.do?mCode=MN032&amp;mode=view&amp;'
        'mgr_seq=1&amp;board_seq=1234">BPT notice</a>'
    )
    assert adapter._extract_detail_links(
        busan,
        "https://www.bptc.co.kr/kor/CMS/Board/Board.do?mCode=MN032",
        bpt_html,
    ) == [
        "https://www.bptc.co.kr/kor/CMS/Board/Board.do?"
        "mCode=MN032&mode=view&mgr_seq=1&board_seq=1234"
    ]


def test_precise_notice_body_ignores_navigation_stop_words() -> None:
    adapter = OfficialNoticeAdapter("config/keywords.yaml")
    port = PortConfig(
        country="Korea",
        port_code="BUSAN",
        display_name="BUSAN",
        timezone="Asia/Seoul",
        aliases=["BUSAN"],
        terminals=[],
        source_urls=[],
    )
    html = """
    <html><body>
      <nav>BUSAN operation suspended archive</nav>
      <div id="board_view">Routine terminal information only.</div>
    </body></html>
    """
    assert adapter.parse(port, html, "https://example.com/notice") == []


def test_visible_board_date_is_used_as_publication_time() -> None:
    published = OfficialNoticeAdapter._extract_published_at(
        "<div id='board_view'>\ub4f1\ub85d\uc77c : 2026-07-14 09:30</div>",
        "Asia/Seoul",
    )
    assert published == datetime(
        2026,
        7,
        14,
        9,
        30,
        tzinfo=ZoneInfo("Asia/Seoul"),
    )


def test_saigon_eport_inline_notices_are_split_into_documents() -> None:
    html = """
    <div class="row">
      Port notice 14/07/2026 <i id="notice-1">Xem chi tiet</i>
    </div>
    <div class="row snp-card" id="notice-1div">
      Terminal operation suspended due to typhoon.
    </div>
    """
    documents = OfficialNoticeAdapter._extract_inline_notice_documents(
        "https://eport.saigonnewport.com.vn/?page=1&search=cat%20lai",
        html,
    )
    assert len(documents) == 1
    assert documents[0][1].endswith("#notice-1")
    assert 'content="2026-07-14"' in documents[0][0]


@pytest.mark.asyncio
async def test_port_without_usable_source_is_reported() -> None:
    port = PortConfig(
        country="China",
        port_code="TIANJIN",
        display_name="TIANJIN",
        timezone="Asia/Shanghai",
        aliases=["TIANJIN"],
        terminals=[],
        source_urls=["TODO: official source"],
    )
    aggregator = PortStatusAggregator([port], "config/keywords.yaml")
    result = await aggregator._check_port(port)

    assert result.events == []
    assert len(result.failures) == 1
    assert result.failures[0].error == "no usable source URL configured"


@pytest.mark.asyncio
async def test_weather_checks_are_bounded_and_concurrent(monkeypatch) -> None:
    ports = [
        PortConfig(
            country="Korea",
            port_code=f"PORT-{index}",
            display_name=f"PORT-{index}",
            timezone="Asia/Seoul",
            aliases=[],
            terminals=[],
            source_urls=[],
        )
        for index in range(10)
    ]
    aggregator = PortStatusAggregator(ports, "config/keywords.yaml")
    active = 0
    max_active = 0

    async def fake_check(port: PortConfig) -> AggregationResult:
        nonlocal active, max_active
        del port
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return AggregationResult()

    monkeypatch.setattr(aggregator, "_check_weather_risk", fake_check)
    results = await aggregator._collect_weather_risks()

    assert len(results) == 10
    assert max_active == WEATHER_RISK_CONCURRENCY


def test_qingdao_explicit_navigation_restriction_is_alert_evidence() -> None:
    port = PortConfig(
        country="China",
        port_code="QINGDAO",
        display_name="QINGDAO",
        timezone="Asia/Shanghai",
        aliases=["QINGDAO", "\u9752\u5c9b", "\u9752\u5c9b\u6e2f"],
        terminals=[],
        source_urls=[],
    )
    adapter = ChinaMSAAdapter("config/keywords.yaml")
    now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(microsecond=0)
    notice_text = (
        "\u9752\u5c9b\u6d77\u4e8b\u5c40\u53d1\u5e03\u6d77\u4e0a\u98ce\u9669\u9884\u8b66\uff0c"
        "\u53d7\u53f0\u98ce\u5f71\u54cd\uff0c"
        "\u5404\u5355\u4f4d\u4e25\u683c\u5b9e\u65bd\u7981\u9650\u822a\u63aa\u65bd\uff0c"
        "\u6062\u590d\u65f6\u95f4\u53e6\u884c\u901a\u77e5\u3002"
    )
    html = (
        "<html><head>"
        f"<meta name='ArticleTitle' content='{notice_text}'>"
        f"<meta name='PubDate' content='{now:%Y-%m-%d %H:%M}'>"
        "</head><body><div class='article'><div class='view'>"
        f"{notice_text}"
        "</div></div></body></html>"
    )

    events = adapter.parse(
        port,
        html,
        "https://www.sd.msa.gov.cn/art/example.html",
    )

    assert len(events) == 1
    assert events[0].reason_detail == "typhoon"


def make_ningbo_port(source_url: str) -> PortConfig:
    return PortConfig(
        country="China",
        port_code="NINGBO",
        display_name="NINGBO",
        timezone="Asia/Shanghai",
        aliases=["NINGBO", "Ningbo", "\u5b81\u6ce2", "\u5b81\u6ce2\u821f\u5c71\u6e2f"],
        terminals=["NBCT", "MSICT", "BLCT", "CMICT"],
        source_urls=[source_url],
    )


@pytest.mark.asyncio
async def test_npedi_feed_parses_current_typhoon_suspension(monkeypatch) -> None:
    source_url = (
        "https://www.npedi.com/portal-api/index/content/list"
        "?categoryId=1&pageNum=1&pageSize=20"
    )
    port = make_ningbo_port(source_url)
    adapter = ChinaMSAAdapter("config/keywords.yaml")
    timezone = ZoneInfo("Asia/Shanghai")
    now = datetime.now(timezone).replace(second=0, microsecond=0)
    published = now - timedelta(hours=1)
    planned_start = now + timedelta(hours=3)
    content_id = 59901

    list_payload = {
        "code": 200,
        "data": {
            "list": [
                {
                    "contentId": content_id,
                    "inputdate": published.strftime("%Y-%m-%d %H:%M:%S"),
                    "title": (
                        "\u5173\u4e8e\u53f0\u98ce\u201c\u767d\u6d77\u8c5a\u201d"
                        "\u5404\u6e2f\u533a\u8fdb\u63d0\u6682\u505c\u4f5c\u4e1a"
                        "\u7684\u901a\u77e5"
                    ),
                    "description": "\u9632\u53f0\u5c01\u6e2f",
                }
            ]
        },
    }
    detail_payload = {
        "code": 200,
        "data": {
            "contentId": content_id,
            "inputdate": published.strftime("%Y-%m-%d %H:%M:%S"),
            "title": list_payload["data"]["list"][0]["title"],
            "content": (
                "<p>\u53d7\u7b2c13\u53f7\u53f0\u98ce\u5f71\u54cd\uff0c"
                "\u90e8\u5206\u7801\u5934\u8ba1\u5212\u6682\u505c"
                "\u96c6\u88c5\u7bb1\u8fdb\u63d0\u4f5c\u4e1a\uff0c"
                "\u9632\u53f0\u5c01\u6e2f\uff0c"
                "\u6062\u590d\u65f6\u95f4\u53e6\u884c\u901a\u77e5\u3002</p>"
                f"<p>\u5317\u4e00\u96c6\u53f8\uff1a{planned_start.month}"
                f"\u6708{planned_start.day}\u65e5{planned_start:%H:%M}"
                "\u5f00\u59cb\u6682\u505c\u7a7a\u7bb1\u8fdb\u63d0\u7bb1"
                "\u4f5c\u4e1a\uff1b</p>"
                "<p>\u5609\u5174\u6e2f\u52a1\uff1a1\u67081\u65e500:00"
                "\u5f00\u59cb\u6682\u505c\u4f5c\u4e1a\uff1b</p>"
            ),
        },
    }

    async def fake_fetch(url: str) -> str:
        if url == source_url:
            return json.dumps(list_payload)
        if url.endswith(f"/{content_id}"):
            return json.dumps(detail_payload)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(adapter, "fetch", fake_fetch)

    events = await adapter.check(port)

    assert len(events) == 1
    event = events[0]
    assert event.port_code == "NINGBO"
    assert event.terminal_name is None
    assert event.status == "planned"
    assert event.reason_detail == "typhoon"
    assert event.start_time == planned_start
    assert event.end_time is None
    assert event.end_time_uncertain is True
    assert event.source_url == (
        "https://www.npedi.com/contentDetail?contentId=59901"
    )
    assert "\u5609\u5174\u6e2f\u52a1" not in event.raw_text


@pytest.mark.asyncio
async def test_npedi_full_recovery_suppresses_older_stop_notice(monkeypatch) -> None:
    source_url = (
        "https://www.npedi.com/portal-api/index/content/list"
        "?categoryId=1&pageNum=1&pageSize=20"
    )
    port = make_ningbo_port(source_url)
    adapter = ChinaMSAAdapter("config/keywords.yaml")
    now = datetime.now(ZoneInfo("Asia/Shanghai")).replace(microsecond=0)
    payload = {
        "code": 200,
        "data": {
            "list": [
                {
                    "contentId": 2,
                    "inputdate": (now - timedelta(hours=1)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "title": (
                        "\u5173\u4e8e\u6062\u590d\u8fdb\u63d0\u7bb1"
                        "\u4f5c\u4e1a\u7684\u901a\u77e5"
                    ),
                },
                {
                    "contentId": 1,
                    "inputdate": (now - timedelta(hours=2)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "title": (
                        "\u5173\u4e8e\u53f0\u98ce\u5404\u6e2f\u533a"
                        "\u6682\u505c\u4f5c\u4e1a\u7684\u901a\u77e5"
                    ),
                },
            ]
        },
    }

    async def fake_fetch(url: str) -> str:
        assert url == source_url
        return json.dumps(payload)

    monkeypatch.setattr(adapter, "fetch", fake_fetch)

    assert await adapter.check(port) == []


def test_selective_operation_restriction_is_not_a_stop_event() -> None:
    port = PortConfig(
        country="Korea",
        port_code="INCHEON",
        display_name="INCHEON",
        timezone="Asia/Seoul",
        aliases=["INCHEON"],
        terminals=["E1CT"],
        source_urls=[],
    )
    adapter = OfficialNoticeAdapter("config/keywords.yaml")
    html = (
        "<html><body><div id='board_view'>"
        "E1CT EMPTY \ubc18\ucd9c\uc785 \uc81c\ud55c \uc548\ub0b4. "
        "\ud2b9\uc815 \uc120\uc0ac \uacf5\ucee8 \uc791\uc5c5\uc81c\ud55c."
        "</div></body></html>"
    )

    assert adapter.parse(port, html, "https://example.com/notice") == []


def test_bare_navigation_warning_is_not_an_actual_control_event() -> None:
    port = PortConfig(
        country="China",
        port_code="QINGDAO",
        display_name="QINGDAO",
        timezone="Asia/Shanghai",
        aliases=["QINGDAO", "\u9752\u5c9b", "\u9752\u5c9b\u6e2f"],
        terminals=[],
        source_urls=[],
    )
    adapter = ChinaMSAAdapter("config/keywords.yaml")
    html = (
        "<html><body>"
        "\u9752\u5c9b\u6d77\u4e8b\u5c40\u53d1\u5e03\u822a\u884c"
        "\u8b66\u544a\uff0c\u8bf7\u8239\u8236\u6ce8\u610f\u5b89\u5168\u3002"
        "</body></html>"
    )

    assert adapter.parse(port, html, "https://www.sd.msa.gov.cn/notice") == []
