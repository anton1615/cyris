"""Tests for the area catalog and the birth-threshold annotation."""

import pytest

from cyris.domain.areas import Area, AreaCatalog

pytestmark = pytest.mark.unit


def _area(
    area_id: int,
    name: str,
    *,
    position: int = 0,
    status: str = "active",
    merged_into: int | None = None,
) -> Area:
    return Area(
        id=area_id,
        name=name,
        position=position,
        status=status,
        merged_into=merged_into,
        proposal_articles=0,
        proposal_issues=0,
        last_proposed_run=None,
    )


def _catalog() -> AreaCatalog:
    return AreaCatalog(
        [
            _area(1, "Tech", position=0),
            _area(2, "Chips", status="merged", merged_into=1),
            _area(3, "Food", status="dismissed"),
            _area(4, "Sport", status="proposed"),
        ]
    )


def test_place_resolves_merged_name_to_active_target() -> None:
    placement = _catalog().place(" chips ")

    assert placement.kind == "active"
    assert placement.area is not None
    assert placement.area.id == 1


def test_place_reports_dismissed_proposed_unknown_and_blank() -> None:
    catalog = _catalog()

    food = catalog.place("food")
    assert food.kind == "dismissed"

    sport = catalog.place("Sport")
    assert sport.kind == "proposed"
    assert sport.area is not None
    assert sport.area.id == 4

    assert catalog.place("Art").kind == "unknown"
    assert catalog.place("  ").kind == "unknown"


def test_for_source_follows_renames_and_only_returns_active_areas() -> None:
    catalog = AreaCatalog(
        [
            _area(1, "Technology", position=0),
            _area(2, "Chips", status="merged", merged_into=1),
        ]
    )

    source_area = catalog.for_source(1)
    assert source_area is not None
    assert source_area.name == "Technology"
    assert catalog.for_source(2) is None
    assert catalog.for_source(None) is None
    assert catalog.for_source(99) is None


def test_names_lists_active_areas_in_position_order() -> None:
    catalog = AreaCatalog([_area(5, "B", position=1), _area(6, "A", position=0)])

    assert catalog.names() == ["A", "B"]


def test_merged_row_with_missing_or_inactive_target_is_dismissed() -> None:
    catalog = AreaCatalog(
        [
            _area(1, "Gone", status="merged", merged_into=99),
            _area(2, "Old", status="dismissed"),
            _area(3, "Stale", status="merged", merged_into=2),
        ]
    )

    assert catalog.place("Gone").kind == "dismissed"
    assert catalog.place("Stale").kind == "dismissed"
