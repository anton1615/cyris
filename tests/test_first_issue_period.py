"""The onboarding's first issue: a key no tick later the same day stores over.

An issue is keyed by (date, period), and the scheduled run `due_period` starts
stores its issue over any other with that key. A tick later today takes
`(today, due_period(tick))`, so the first issue's key must be none of those, and
there is always one: yesterday's evening while both of today's hours are to come.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cyris.service_layer.schedule import (
    due_period,
    first_issue_key,
    is_latest_issue,
    validate_schedule,
)

pytestmark = pytest.mark.unit

TAIPEI = ZoneInfo("Asia/Taipei")
DAY = datetime(2026, 10, 20, tzinfo=TAIPEI)
TODAY, YESTERDAY = "2026-10-20", "2026-10-19"
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


def _keys_after(now: datetime, times: list[str]) -> set[tuple[str, str]]:
    """The key each hourly tick still to come on `now`'s date stores its issue under."""
    ticks = [DAY.replace(hour=h) for h in range(now.hour + 1, 24)]
    return {(TODAY, period) for tick in ticks if (period := due_period(tick, times))}


@pytest.mark.parametrize("times", SCHEDULES, ids="-".join)
@pytest.mark.parametrize("now", _moments(), ids=lambda now: f"{now:%H%M}")
def test_no_later_tick_that_day_takes_the_first_issues_key(times, now) -> None:
    key = first_issue_key(now, times)

    assert key not in _keys_after(now, times)
    assert key[0] in (TODAY, YESTERDAY) and key[1] in ("morning", "evening")


@pytest.mark.parametrize("times", SCHEDULES, ids="-".join)
def test_today_is_taken_once_a_publish_hour_has_begun_and_yesterday_before(times) -> None:
    """Any key is safe on yesterday's date, so it is held to the hours it is meant for."""
    earlier, later = (int(t[:2]) for t in validate_schedule(times))
    for now in _moments():
        if now.hour < earlier:
            wanted = (YESTERDAY, "evening")
        else:
            wanted = (TODAY, "evening" if now.hour >= later else "morning")
        assert first_issue_key(now, times) == wanted, now


def test_before_the_morning_hour_it_is_yesterdays_evening() -> None:
    assert first_issue_key(DAY.replace(hour=7, minute=59), ["08:00", "20:00"]) == (
        YESTERDAY,
        "evening",
    )


def test_between_the_two_hours_it_is_todays_morning() -> None:
    assert first_issue_key(DAY.replace(hour=8), ["08:00", "20:00"]) == (TODAY, "morning")
    assert first_issue_key(DAY.replace(hour=19, minute=59), ["08:00", "20:00"]) == (
        TODAY,
        "morning",
    )


def test_after_the_evening_hour_it_is_todays_evening_up_to_midnight() -> None:
    """The run is started with this date, so one that ends after midnight keeps it."""
    assert first_issue_key(DAY.replace(hour=20), ["08:00", "20:00"]) == (TODAY, "evening")
    assert first_issue_key(DAY.replace(hour=23, minute=59), ["08:00", "20:00"]) == (
        TODAY,
        "evening",
    )


def test_the_first_of_the_month_reaches_back_to_the_month_before() -> None:
    first = datetime(2026, 11, 1, 3, tzinfo=TAIPEI)

    assert first_issue_key(first, ["08:00", "20:00"]) == ("2026-10-31", "evening")


def test_a_schedule_no_tick_can_honour_is_refused() -> None:
    with pytest.raises(ValueError):
        first_issue_key(DAY.replace(hour=12), ["08:30", "20:00"])


def test_todays_first_issue_is_the_latest_until_the_next_scheduled_one() -> None:
    """The archive and the publish read the first issue as they read any scheduled one."""
    first = (TODAY, "morning")
    before = [(YESTERDAY, "evening"), (YESTERDAY, "morning")]

    assert is_latest_issue(first, [first, *before])
    assert not is_latest_issue(first, [(TODAY, "evening"), first, *before])


def test_yesterdays_evening_is_the_latest_until_todays_morning() -> None:
    """Built before the morning hour, it leads the archive until that hour's issue lands."""
    first = (YESTERDAY, "evening")
    older = [(YESTERDAY, "morning"), ("2026-10-18", "evening")]

    assert is_latest_issue(first, [first, *older])
    assert not is_latest_issue(first, [(TODAY, "morning"), first, *older])
    assert not is_latest_issue((YESTERDAY, "morning"), [first, *older])


def test_yesterday_is_one_calendar_day_back_across_a_dst_change() -> None:
    lisbon = datetime(2026, 10, 25, 5, tzinfo=ZoneInfo("Europe/Lisbon"))

    assert first_issue_key(lisbon, ["08:00", "20:00"]) == ("2026-10-24", "evening")
