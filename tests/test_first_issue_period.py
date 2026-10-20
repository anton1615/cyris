"""The onboarding's first issue: a key no tick later the same day stores over.

An issue is keyed by (date, period), and the scheduled run `due_period` starts
stores its issue over any other with that key. The first issue is dated the day
it is built, so its period must be one `due_period` gives at no later hour today.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from cyris.service_layer.schedule import (
    RUN_CAP,
    due_period,
    first_issue_period,
    is_latest_issue,
    validate_schedule,
)

pytestmark = pytest.mark.unit

TAIPEI = ZoneInfo("Asia/Taipei")
DAY = datetime(2026, 10, 20, tzinfo=TAIPEI)
SCHEDULES = [
    ["08:00", "20:00"],
    ["06:00", "18:00"],
    ["00:00", "12:00"],
    ["11:00", "23:00"],
    ["20:00", "08:00"],
]
# Every minute that matters within an hour: its first, its middle, its last.
MINUTES = [0, 30, 59]


def _moments() -> list[datetime]:
    return [DAY.replace(hour=h, minute=m) for h in range(24) for m in MINUTES]


def _ticks_after(now: datetime) -> list[datetime]:
    """Every hourly tick still to come on `now`'s date."""
    return [DAY.replace(hour=h) for h in range(now.hour + 1, 24)]


@pytest.mark.parametrize("times", SCHEDULES, ids="-".join)
@pytest.mark.parametrize("now", _moments(), ids=lambda now: f"{now:%H%M}")
def test_no_later_tick_that_day_gives_the_first_issues_period(times, now) -> None:
    period = first_issue_period(now, times)

    later = {due_period(tick, times) for tick in _ticks_after(now)}

    assert period is None or period not in later


@pytest.mark.parametrize("times", SCHEDULES, ids="-".join)
def test_a_period_is_given_whenever_a_publish_hour_has_begun(times) -> None:
    """None is safe on its own, so it is held to the two moments it is meant for."""
    earlier, later = (int(t[:2]) for t in validate_schedule(times))
    for now in _moments():
        near_midnight = (now + RUN_CAP).date() != now.date()
        if now.hour < earlier or near_midnight:
            assert first_issue_period(now, times) is None, now
        else:
            wanted = "evening" if now.hour >= later else "morning"
            assert first_issue_period(now, times) == wanted, now


def test_before_the_morning_hour_there_is_no_period_to_take() -> None:
    assert first_issue_period(DAY.replace(hour=7, minute=59), ["08:00", "20:00"]) is None


def test_between_the_two_hours_it_takes_the_morning() -> None:
    assert first_issue_period(DAY.replace(hour=8), ["08:00", "20:00"]) == "morning"
    assert first_issue_period(DAY.replace(hour=19, minute=59), ["08:00", "20:00"]) == "morning"


def test_after_the_evening_hour_it_takes_the_evening() -> None:
    assert first_issue_period(DAY.replace(hour=20), ["08:00", "20:00"]) == "evening"
    assert first_issue_period(DAY.replace(hour=23, minute=44), ["08:00", "20:00"]) == "evening"


def test_a_run_that_could_end_on_the_next_date_is_not_started() -> None:
    """The pipeline dates the issue when it writes it, up to `RUN_CAP` after the start."""
    last_safe = DAY.replace(hour=23, minute=59) - RUN_CAP + timedelta(minutes=1)

    assert first_issue_period(last_safe - timedelta(minutes=1), ["08:00", "20:00"]) == "evening"
    assert first_issue_period(last_safe, ["08:00", "20:00"]) is None


def test_a_schedule_no_tick_can_honour_is_refused() -> None:
    with pytest.raises(ValueError):
        first_issue_period(DAY.replace(hour=12), ["08:30", "20:00"])


def test_the_first_issue_is_the_latest_until_the_next_scheduled_one() -> None:
    """The archive and the publish read the first issue as they read any scheduled one."""
    first = ("2026-10-20", "morning")
    before = [("2026-10-19", "evening"), ("2026-10-19", "morning")]

    assert is_latest_issue(first, [first, *before])
    assert not is_latest_issue(first, [("2026-10-20", "evening"), first, *before])
