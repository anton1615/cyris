"""Whether an issue is the newest by (date, period) order, whatever order issues were saved in."""

import pytest

from cyris.service_layer.schedule import is_latest_issue

pytestmark = pytest.mark.unit


def test_issue_is_not_latest_when_a_newer_one_is_listed() -> None:
    issues = [("2026-10-14", "evening"), ("2026-10-15", "morning")]
    assert is_latest_issue(("2026-10-14", "evening"), issues) is False


def test_order_of_saved_issues_does_not_matter() -> None:
    issues = [("2026-10-14", "evening"), ("2026-10-15", "morning")]
    assert is_latest_issue(("2026-10-15", "morning"), issues) is True
    assert is_latest_issue(("2026-10-14", "evening"), issues) is False


def test_same_date_orders_morning_before_evening() -> None:
    assert is_latest_issue(("2026-10-15", "morning"), [("2026-10-15", "evening")]) is False
    assert is_latest_issue(("2026-10-15", "evening"), [("2026-10-15", "morning")]) is True


def test_newer_key_with_no_issues_is_latest() -> None:
    assert is_latest_issue(("2026-10-15", "evening"), []) is True


def test_unknown_period_raises() -> None:
    with pytest.raises(ValueError):
        is_latest_issue(("2026-10-15", "morning"), [("2026-10-14", "night")])
