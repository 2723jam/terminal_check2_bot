from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from src.scheduler import (
    github_actions_watchdog_crons,
    github_actions_watchdog_run_times,
    scheduled_run_times,
    scheduled_slot_id,
    scheduled_slot_id_from_github_schedule,
)


def test_schedule_runs_hourly_from_0905_to_1805() -> None:
    assert scheduled_run_times() == [
        time(9, 5),
        time(10, 5),
        time(11, 5),
        time(12, 5),
        time(13, 5),
        time(14, 5),
        time(15, 5),
        time(16, 5),
        time(17, 5),
        time(18, 5),
    ]


def test_github_actions_watchdog_slots_cover_each_hour() -> None:
    run_times = github_actions_watchdog_run_times()
    assert len(run_times) == 52
    assert run_times[:4] == [time(6, 5), time(6, 20), time(6, 35), time(6, 50)]
    assert run_times[-4:] == [time(18, 5), time(18, 20), time(18, 35), time(18, 50)]
    assert github_actions_watchdog_crons() == [
        "5,20,35,50 21-23 * * *",
        "5,20,35,50 0-9 * * *",
    ]


def test_watchdog_runs_share_one_hourly_slot() -> None:
    timezone = ZoneInfo("Asia/Seoul")
    assert scheduled_slot_id(datetime(2026, 6, 5, 9, 5, tzinfo=timezone)) == "2026-06-05-0905-Asia/Seoul"
    assert scheduled_slot_id(datetime(2026, 6, 5, 9, 20, tzinfo=timezone)) == "2026-06-05-0905-Asia/Seoul"
    assert scheduled_slot_id(datetime(2026, 6, 5, 9, 50, tzinfo=timezone)) == "2026-06-05-0905-Asia/Seoul"


def test_schedule_slot_ignores_times_before_window() -> None:
    timezone = ZoneInfo("Asia/Seoul")
    assert scheduled_slot_id(datetime(2026, 6, 5, 8, 50, tzinfo=timezone)) is None
    assert scheduled_slot_id(datetime(2026, 6, 5, 9, 4, tzinfo=timezone)) is None


def test_github_schedule_uses_actual_runtime_hour_when_delayed() -> None:
    delayed_now = datetime(2026, 6, 5, 3, 33, tzinfo=UTC)
    assert (
        scheduled_slot_id_from_github_schedule("5,20,35,50 21-23 * * *", delayed_now)
        == "2026-06-05-1205-Asia/Seoul"
    )
    assert (
        scheduled_slot_id_from_github_schedule("5,20,35,50 0-9 * * *", delayed_now)
        == "2026-06-05-1205-Asia/Seoul"
    )


def test_delayed_watchdog_has_late_grace_for_final_slot() -> None:
    late_now = datetime(2026, 6, 5, 12, 15, tzinfo=UTC)
    assert (
        scheduled_slot_id_from_github_schedule(None, late_now)
        == "2026-06-05-1805-Asia/Seoul"
    )


def test_workflow_watchdog_crons_match_scheduler() -> None:
    workflow = Path(
        ".github/workflows/terminal_check2_bot.yml"
    ).read_text(encoding="utf-8")

    assert workflow.count("- cron:") == 2
    for expression in github_actions_watchdog_crons():
        assert f'- cron: "{expression}"' in workflow
    assert 'WEATHER_RISK_ENABLED: "false"' in workflow
    assert "TERMINAL_CHECK2_WEATHER_RISK_ENABLED" not in workflow
