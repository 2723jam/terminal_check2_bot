from datetime import time

from src.scheduler import scheduled_run_times


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
