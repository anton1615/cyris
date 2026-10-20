"""Is this hour a digest hour?

The container's cron fires hourly and asks; the answer comes from the effective
`digest_schedule`, which the settings page can change without a rebuild. A fixed
`0 8,20 * * *` crontab could not — it is baked into the image.

Hour granularity is the contract, not a rounding: an hourly tick cannot honour
`08:30`, so the write surface rejects it rather than silently firing at 08:00.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Literal, get_args

Period = Literal["morning", "evening"]

# The order a day's issues are published in: `due_period` gives the first label to
# the earlier hour. The archive sorts a day's issues by it.
PERIOD_ORDER: tuple[str, ...] = get_args(Period)

# How long a run started now may take to date its issue: the run container's
# own cap (`RUN_SLEEP_AFTER` in workers/app/src/index.js). An issue is dated when
# the pipeline writes it, not when the run starts, so a run started closer than
# this to midnight could carry tomorrow's date.
RUN_CAP = timedelta(minutes=15)


def validate_schedule(times: list[str]) -> list[str]:
    """Exactly two distinct whole hours, earliest first. Raises ValueError."""
    if len(times) != 2:
        raise ValueError("the digest schedule is exactly two times: a morning and an evening")
    hours = []
    for t in times:
        try:
            hour, minute = (int(part) for part in t.split(":", 1))
        except ValueError:
            raise ValueError(f"{t!r} is not a HH:MM time") from None
        if not 0 <= hour <= 23 or minute != 0:
            raise ValueError(f"{t!r} must be a whole hour — the schedule tick is hourly")
        hours.append(hour)
    if hours[0] == hours[1]:
        raise ValueError("the two digest times must be different hours")
    return [f"{h:02d}:00" for h in sorted(hours)]


def due_period(now: datetime, times: list[str]) -> Period | None:
    """Which digest `now` is due for, or None. `now` must already be local."""
    try:
        earlier, later = validate_schedule(times)
    except ValueError:
        return None
    current = f"{now.hour:02d}:00"
    if current == earlier:
        return "morning"
    if current == later:
        return "evening"
    return None


def first_issue_period(now: datetime, times: list[str]) -> Period | None:
    """The period a run started at `now` can take without a later tick replacing its issue.

    An issue is keyed by (date, period), and the run that `due_period` starts at
    a publish hour stores its issue over any other with that key. So the first
    issue takes the latest publish hour that has already begun today: every
    tick left today falls on a later hour, and none of them gives that period
    again before the date changes. None while both hours are still to come, and
    within `RUN_CAP` of midnight, where the run could finish on the next date;
    the next morning's scheduled issue is then the first. `now` must already be
    local. Raises ValueError on a schedule `validate_schedule` refuses.
    """
    earlier, later = (int(time.split(":")[0]) for time in validate_schedule(times))
    if (now + RUN_CAP).date() != now.date():
        return None
    if now.hour >= later:
        return "evening"
    if now.hour >= earlier:
        return "morning"
    return None


def is_latest_issue(key: tuple[str, str], issues: Iterable[tuple[str, str]]) -> bool:
    """True when no issue ranks after `key` by (date, PERIOD_ORDER index). Raises ValueError."""

    def rank(issue: tuple[str, str]) -> tuple[str, int]:
        date, period = issue
        return date, PERIOD_ORDER.index(period)

    key_rank = rank(key)
    return all(rank(issue) <= key_rank for issue in issues)
