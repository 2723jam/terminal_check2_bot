from datetime import datetime, time
from zoneinfo import ZoneInfo

from src.scheduler import github_actions_watchdog_run_times, scheduled_run_times, scheduled_slot_id


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
    assert len(run_times) == 40
    assert run_times[:4] == [time(9, 5), time(9, 20), time(9, 35), time(9, 50)]
    assert run_times[-4:] == [time(18, 5), time(18, 20), time(18, 35), time(18, 50)]


def test_watchdog_runs_share_one_hourly_slot() -> None:
    timezone = ZoneInfo("Asia/Seoul")
    assert scheduled_slot_id(datetime(2026, 6, 5, 9, 5, tzinfo=timezone)) == "2026-06-05-0905-Asia/Seoul"
    assert scheduled_slot_id(datetime(2026, 6, 5, 9, 20, tzinfo=timezone)) == "2026-06-05-0905-Asia/Seoul"
    assert scheduled_slot_id(datetime(2026, 6, 5, 9, 50, tzinfo=timezone)) == "2026-06-05-0905-Asia/Seoul"


def test_schedule_slot_ignores_times_before_window() -> None:
    timezone = ZoneInfo("Asia/Seoul")
    assert scheduled_slot_id(datetime(2026, 6, 5, 8, 50, tzinfo=timezone)) is None
    assert scheduled_slot_id(datetime(2026, 6, 5, 9, 4, tzinfo=timezone)) is None
