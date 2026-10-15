"""The area layout: where each card, group, cluster and row of an issue is placed."""

from collections import Counter

import pytest

from cyris.domain.area_layout import layout_by_area
from cyris.domain.models import DigestContent, DigestItem, DigestSection
from cyris.domain.selection import digest_urls

pytestmark = pytest.mark.unit


def _item(title: str, area: str | None = None, score: float | None = None) -> DigestItem:
    return DigestItem(
        title=title,
        summary="s",
        sources=["Src"],
        urls=[f"https://example.com/{title}"],
        score=score,
        area=area,
    )


def _sec(heading: str, *items: DigestItem) -> DigestSection:
    return DigestSection(heading=heading, items=list(items))


def _content(area_order: list[str], **sections) -> DigestContent:
    return DigestContent(
        date="2026-10-15",
        period="morning",
        sources_processed=1,
        articles_received=0,
        articles_included=0,
        area_order=area_order,
        **sections,
    )


def _headings(sections) -> list[str]:
    return [s.heading for s in sections]


def _rows(section) -> list[str]:
    return [r.title for r in section.rows]


def test_each_card_group_cluster_and_row_lands_in_its_area_in_reader_order() -> None:
    content = _content(
        ["Tech", "Health"],
        featured_articles=[
            _sec("H", _item("H", "Tech")),
            _sec("A", _item("A", "Health")),
            _sec("B", _item("B", "Tech")),
        ],
    )

    layout = layout_by_area(content)

    assert [(s.label, _headings(s.features)) for s in layout.sections] == [
        ("Tech", ["B"]),
        ("Health", ["A"]),
    ]


def test_a_cluster_spanning_areas_appears_once_in_its_first_items_area() -> None:
    content = _content(
        ["Tech", "Health"],
        news_clusters=[_sec("C", _item("c1", "Health"), _item("c2", "Tech"))],
    )

    layout = layout_by_area(content)

    assert [(s.label, _headings(s.clusters)) for s in layout.sections] == [("Health", ["C"])]


def test_a_following_group_spanning_areas_is_one_block_in_its_first_items_area() -> None:
    content = _content(
        ["Tech", "Health"],
        fan_sections=[_sec("Group", _item("x", "Tech"), _item("y", "Health"))],
    )

    layout = layout_by_area(content)

    assert [(s.label, _headings(s.following)) for s in layout.sections] == [("Tech", ["Group"])]


def test_an_attention_section_places_each_row_by_its_own_area() -> None:
    content = _content(
        ["Tech", "Health"],
        attention_sections=[_sec("Radar", _item("r1", "Tech"), _item("r2", "Health"))],
    )

    layout = layout_by_area(content)

    assert [(s.label, _rows(s)) for s in layout.sections] == [("Tech", ["r1"]), ("Health", ["r2"])]
    assert "Radar" not in repr(layout)


def test_a_section_with_no_item_in_an_area_is_omitted() -> None:
    content = _content(["Tech", "Health"], filtered_headlines=[_item("t", "Tech")])

    layout = layout_by_area(content)

    assert [s.label for s in layout.sections] == ["Tech"]


def test_an_area_name_matches_case_insensitively() -> None:
    content = _content(["Tech"], filtered_headlines=[_item("t", "tech")])

    layout = layout_by_area(content)

    assert [(s.label, _rows(s)) for s in layout.sections] == [("Tech", ["t"])]


def test_rows_are_radar_by_score_then_headlines_in_incoming_order() -> None:
    content = _content(
        ["Tech"],
        attention_sections=[
            _sec(
                "Radar",
                _item("r_none", "Tech", None),
                _item("r60", "Tech", 60),
                _item("r90", "Tech", 90),
            )
        ],
        filtered_headlines=[_item("head", "Tech", 50)],
    )

    layout = layout_by_area(content)

    assert _rows(layout.sections[0]) == ["r90", "r60", "r_none", "head"]


def test_features_keep_incoming_order_and_following_stays_apart() -> None:
    content = _content(
        ["Tech"],
        featured_articles=[
            _sec("Top", _item("Top", "Tech", 99)),
            _sec("F1", _item("F1", "Tech", 10)),
            _sec("F2", _item("F2", "Tech", 90)),
        ],
        fan_sections=[_sec("Group", _item("g", "Tech"))],
    )

    section = layout_by_area(content).sections[0]

    assert _headings(section.features) == ["F1", "F2"]
    assert _headings(section.following) == ["Group"]


def test_a_json_round_trip_gives_an_equal_layout() -> None:
    content = _content(
        ["Tech", "Health"],
        featured_articles=[_sec("Top", _item("Top", "Health")), _sec("F", _item("F", "Tech"))],
        news_clusters=[_sec("C", _item("c1", "Health"), _item("c2", "Tech"))],
        fan_sections=[_sec("G", _item("g1", "Tech"), _item("g2", "Health"))],
        attention_sections=[_sec("R", _item("r1", "Tech", 40), _item("r2", "Health"))],
        filtered_headlines=[_item("h1", "Health", 20), _item("h2", None)],
    )
    again = DigestContent.model_validate_json(content.model_dump_json())

    assert layout_by_area(again) == layout_by_area(content)


def test_every_url_appears_exactly_once_and_the_input_is_unchanged() -> None:
    content = _content(
        ["Tech", "Health"],
        featured_articles=[_sec("Top", _item("Top", "Health")), _sec("F", _item("F", "Tech"))],
        news_clusters=[_sec("C", _item("c1", "Health"), _item("c2", "Tech"))],
        fan_sections=[_sec("G", _item("g1", "Tech"), _item("g2", "Health"))],
        attention_sections=[_sec("R", _item("r1", "Tech", 40), _item("r2", "Health"))],
        filtered_headlines=[_item("h1", "Health", 20), _item("h2", "Tech")],
    )
    before = content.model_dump()

    layout = layout_by_area(content)

    placed = []
    sections = [layout.headline] if layout.headline else []
    for s in layout.sections:
        sections += [*s.features, *s.following, *s.clusters]
        placed += [u for r in s.rows for u in r.urls]
    placed += [u for sec in sections for i in sec.items for u in i.urls]
    assert Counter(placed) == Counter(u for u in digest_urls(content))
    assert content.model_dump() == before
