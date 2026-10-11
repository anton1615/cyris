"""Every digest item is stamped with its area, from the model's pick or from its source."""

import pytest

from cyris.domain.areas import Area, AreaCatalog
from cyris.domain.models import DigestContent, DigestItem, DigestSection, SourceConfig
from cyris.service_layer.area_stamp import stamp_areas

pytestmark = pytest.mark.unit


def _area(area_id: int, name: str) -> Area:
    return Area(
        id=area_id,
        name=name,
        position=area_id,
        status="active",
        merged_into=None,
        proposal_articles=0,
        proposal_issues=0,
        last_proposed_run=None,
    )


CATALOG = AreaCatalog([_area(1, "Tech"), _area(2, "Food")])


def _content(**sections) -> DigestContent:
    return DigestContent(
        date="2026-10-12",
        period="morning",
        sources_processed=1,
        articles_received=3,
        articles_included=3,
        **sections,
    )


def _item(urls: list[str], sources: list[str]) -> DigestItem:
    return DigestItem(title="t", summary="s", sources=sources, urls=urls)


def _all_items(content: DigestContent) -> list[DigestItem]:
    items = []
    for section in content.featured_articles + content.fan_sections + content.tracked_topics:
        items.extend(section.items)
    return items + content.filtered_headlines


def test_the_pick_for_the_first_article_wins_over_the_source() -> None:
    item = _item(["u1", "u2"], ["feedA"])
    content = _content(featured_articles=[DigestSection(heading="H", items=[item])])

    counts = stamp_areas(
        content,
        placements={"u1": "Tech"},
        sources={"feedA": SourceConfig(name="feedA", url="x", area_id=2)},
        catalog=CATALOG,
    )

    assert item.area == "Tech"
    assert counts.picked == 1


def test_without_a_pick_the_source_area_is_used() -> None:
    item = _item(["u1", "u2"], ["feedA"])
    content = _content(featured_articles=[DigestSection(heading="H", items=[item])])

    counts = stamp_areas(
        content,
        placements={"u2": "Tech"},
        sources={"feedA": SourceConfig(name="feedA", url="x", area_id=2)},
        catalog=CATALOG,
    )

    assert item.area == "Food"
    assert counts.source == 1


def test_every_item_kind_gets_the_source_area_by_value() -> None:
    sources = {"feedA": SourceConfig(name="feedA", url="x", area_id=2)}
    degrade = _item(["d"], ["feedA"])
    fan = _item(["f"], ["feedA"])
    topic = _item(["t"], ["feedA"])
    headline = _item(["h"], ["feedA"])
    content = _content(
        featured_articles=[DigestSection(heading="D", items=[degrade])],
        fan_sections=[DigestSection(heading="F", items=[fan])],
        tracked_topics=[DigestSection(heading="T", items=[topic])],
        filtered_headlines=[headline],
    )

    counts = stamp_areas(content, placements={}, sources=sources, catalog=CATALOG)

    assert [i.area for i in _all_items(content)] == ["Food"] * 4
    assert counts.source == 4


def test_no_area_and_no_pick_is_none_and_an_old_digest_loads_with_none() -> None:
    item = _item(["u1"], ["feedA"])
    content = _content(featured_articles=[DigestSection(heading="H", items=[item])])

    counts = stamp_areas(
        content,
        placements={},
        sources={"feedA": SourceConfig(name="feedA", url="x")},
        catalog=CATALOG,
    )

    assert item.area is None
    assert counts.none == 1
    old = DigestItem.model_validate({"title": "t", "summary": "s", "sources": [], "urls": []})
    assert old.area is None
