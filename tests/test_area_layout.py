"""The area layout: where each card, group, cluster and row of an issue is placed."""

from collections import Counter

import pytest

from cyris.domain.area_layout import (
    LOWER_ROWS_PER_AREA,
    cap_lower_layers,
    layout_by_area,
)
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


def _content(area_order: list[str], articles_included: int = 0, **sections) -> DigestContent:
    return DigestContent(
        date="2026-10-15",
        period="morning",
        sources_processed=1,
        articles_received=0,
        articles_included=articles_included,
        area_order=area_order,
        **sections,
    )


def _headings(sections) -> list[str]:
    return [s.heading for s in sections]


def _rows(section) -> list[str]:
    return [r.item.title for r in section.rows]


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
    assert [r.kind for r in layout.sections[0].rows] == ["radar", "radar", "radar", "wire"]


def test_a_row_is_untranslated_when_it_is_radar_or_passed_through() -> None:
    content = _content(
        ["Tech"],
        attention_sections=[_sec("R", _item("radar", "Tech", 70))],
        filtered_headlines=[
            _item("plain", "Tech"),
            _item("passed", "Tech").model_copy(update={"passed_through": True}),
        ],
    )

    rows = layout_by_area(content).sections[0].rows

    assert [r.untranslated for r in rows] == [True, False, True]


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
        placed += [u for r in s.rows for u in r.item.urls]
    placed += [u for sec in sections for i in sec.items for u in i.urls]
    assert Counter(placed) == Counter(u for u in digest_urls(content))
    assert content.model_dump() == before


def test_a_multi_item_top_story_without_a_summary_keeps_every_item_in_the_headline() -> None:
    top = _sec("Top", _item("t1", "Tech"), _item("t2", "Health"), _item("t3", None))
    content = _content(
        ["Tech", "Health"],
        featured_articles=[top, _sec("F", _item("F", "Tech"))],
    )

    layout = layout_by_area(content)

    assert layout.headline is not None
    assert [i.title for i in layout.headline.items] == ["t1", "t2", "t3"]
    placed = []
    for s in layout.sections:
        assert not {"t1", "t2", "t3"} & {r.item.title for r in s.rows}
        placed += [
            u for sec in [*s.features, *s.following, *s.clusters] for i in sec.items for u in i.urls
        ]
        placed += [u for r in s.rows for u in r.item.urls]
    placed += [u for i in layout.headline.items for u in i.urls]
    assert Counter(placed) == Counter(digest_urls(content))


def test_the_top_story_stands_above_the_sections_and_is_not_repeated_in_one() -> None:
    top = _sec("Top", _item("t1", "Tech"), _item("t2", "Health"))
    content = _content(
        ["Tech", "Health"],
        featured_articles=[top, _sec("F", _item("F", "Tech"))],
    )

    layout = layout_by_area(content)

    assert layout.headline == top
    assert all("Top" not in _headings(s.features) for s in layout.sections)
    assert [(s.label, _headings(s.features)) for s in layout.sections] == [("Tech", ["F"])]


def test_an_issue_with_no_top_story_has_no_headline_and_places_the_rest() -> None:
    content = _content(["Tech"], filtered_headlines=[_item("t", "Tech")])

    layout = layout_by_area(content)

    assert layout.headline is None
    assert [(s.label, _rows(s)) for s in layout.sections] == [("Tech", ["t"])]


def test_a_top_story_that_was_the_only_tech_entry_leaves_no_tech_section() -> None:
    content = _content(
        ["Tech", "Health"],
        featured_articles=[_sec("Top", _item("Top", "Tech"))],
        filtered_headlines=[_item("h", "Health")],
    )

    layout = layout_by_area(content)

    assert layout.headline is not None and layout.headline.heading == "Top"
    assert [s.label for s in layout.sections] == ["Health"]


def test_tracked_topics_pass_through_untouched_and_place_in_no_area() -> None:
    topic = _sec("Topic", _item("tp", "Tech"))
    content = _content(["Tech"], tracked_topics=[topic])

    layout = layout_by_area(content)

    assert layout.tracking == (topic,)
    assert layout.sections == ()


def test_thematic_summaries_alone_give_the_first_as_the_top_story() -> None:
    content = _content(
        ["Tech"],
        thematic_summaries=[_sec("S1", _item("s1", "Tech")), _sec("S2", _item("s2", "Tech"))],
    )

    layout = layout_by_area(content)

    assert layout.headline is not None and layout.headline.heading == "S1"
    assert [(s.label, _headings(s.features)) for s in layout.sections] == [("Tech", ["S2"])]


def test_with_no_area_order_every_entry_sits_in_one_unnamed_section() -> None:
    content = _content(
        [],
        filtered_headlines=[_item("a", "Tech"), _item("b", None)],
        featured_articles=[_sec("Top", _item("top", "Tech"))],
    )

    layout = layout_by_area(content)

    assert [(s.label, s.role, _rows(s)) for s in layout.sections] == [(None, "unnamed", ["a", "b"])]


def test_an_entry_with_no_area_closes_the_issue_under_other() -> None:
    content = _content(["Tech"], filtered_headlines=[_item("t", "Tech"), _item("h", None)])

    layout = layout_by_area(content)

    assert [(s.label, s.role, _rows(s)) for s in layout.sections] == [
        ("Tech", "named", ["t"]),
        (None, "unplaced", ["h"]),
    ]


def test_an_entry_whose_area_was_renamed_or_dismissed_closes_under_other() -> None:
    content = _content(["Tech"], filtered_headlines=[_item("g", "Gone")])

    layout = layout_by_area(content)

    assert [(s.role, _rows(s)) for s in layout.sections] == [("unplaced", ["g"])]


def test_no_other_section_when_every_entry_is_placed() -> None:
    content = _content(["Tech"], filtered_headlines=[_item("t", "Tech")])

    layout = layout_by_area(content)

    assert [s.label for s in layout.sections] == ["Tech"]


def test_an_issue_with_no_entries_has_no_sections() -> None:
    assert layout_by_area(_content(["Tech"])).sections == ()
    assert layout_by_area(_content([])).sections == ()


def test_an_area_named_other_is_a_named_section_apart_from_the_unplaced_one() -> None:
    content = _content(["Other"], filtered_headlines=[_item("o", "other"), _item("h", None)])

    layout = layout_by_area(content)

    assert [(s.label, s.role, _rows(s)) for s in layout.sections] == [
        ("Other", "named", ["o"]),
        (None, "unplaced", ["h"]),
    ]


def _titles(items) -> list[str]:
    return [i.title for i in items]


def test_a_named_area_keeps_its_first_five_headlines_and_the_count_drops_by_the_cut() -> None:
    content = _content(
        ["Tech"],
        filtered_headlines=[_item(f"h{i}", "Tech") for i in range(7)],
        articles_included=7,
    )

    capped = cap_lower_layers(content)

    assert _titles(capped.filtered_headlines) == ["h0", "h1", "h2", "h3", "h4"]
    assert capped.articles_included == 5


def test_clusters_are_kept_whole_and_count_as_rows_before_headlines() -> None:
    content = _content(
        ["Tech"],
        news_clusters=[
            _sec("C1", _item("a1", "Tech"), _item("a2", "Tech"), _item("a3", "Tech")),
            _sec("C2", _item("b1", "Tech")),
        ],
        filtered_headlines=[_item(f"h{i}", "Tech") for i in range(1, 5)],
        articles_included=8,
    )

    capped = cap_lower_layers(content)

    assert [s.heading for s in capped.news_clusters] == ["C1", "C2"]
    assert _titles(capped.filtered_headlines) == ["h1", "h2", "h3"]
    assert capped.articles_included == 7


def test_features_following_and_headline_are_never_cut() -> None:
    content = _content(
        ["Tech"],
        featured_articles=[_sec("Top", _item("top", "Tech"))]
        + [_sec(f"F{i}", _item(f"f{i}", "Tech")) for i in range(8)],
        fan_sections=[_sec("G", *[_item(f"g{i}", "Tech") for i in range(6)])],
        filtered_headlines=[_item(f"h{i}", "Tech") for i in range(7)],
        articles_included=22,
    )

    capped = cap_lower_layers(content)

    assert len(capped.featured_articles) == 9
    assert len(capped.fan_sections[0].items) == 6
    assert len(capped.filtered_headlines) == 5


def test_an_empty_area_order_gives_an_equal_copy() -> None:
    content = _content([], filtered_headlines=[_item(f"h{i}", "Tech") for i in range(40)])

    assert cap_lower_layers(content) == content


def test_unplaced_entries_are_never_cut() -> None:
    content = _content(
        ["Tech"],
        filtered_headlines=[_item(f"h{i}", None) for i in range(9)],
        articles_included=9,
    )

    capped = cap_lower_layers(content)

    assert len(capped.filtered_headlines) == 9
    assert capped.articles_included == 9


def test_an_attention_section_keeps_its_survivors_and_a_section_with_none_is_dropped() -> None:
    content = _content(
        ["Tech"],
        attention_sections=[
            _sec("A", _item("a96", "Tech", 96), _item("a95", "Tech", 95), _item("a94", "Tech", 94)),
            _sec("B", *[_item(f"b{s}", "Tech", s) for s in (100, 99, 98, 97)]),
            _sec("C", _item("c10", "Tech", 10)),
        ],
        articles_included=8,
    )

    capped = cap_lower_layers(content)

    assert [(s.heading, _titles(s.items)) for s in capped.attention_sections] == [
        ("A", ["a96"]),
        ("B", ["b100", "b99", "b98", "b97"]),
    ]
    assert capped.articles_included == 5


def test_after_the_cut_every_named_area_has_at_most_five_lower_rows() -> None:
    content = _content(
        ["Tech", "Health"],
        news_clusters=[_sec("C", _item("c1", "Tech"), _item("c2", "Tech"))],
        filtered_headlines=[_item(f"t{i}", "Tech") for i in range(7)]
        + [_item(f"h{i}", "Health") for i in range(6)],
        articles_included=15,
    )

    for section in layout_by_area(cap_lower_layers(content)).sections:
        assert len(section.clusters) + len(section.rows) <= LOWER_ROWS_PER_AREA


def test_a_negative_row_count_is_refused_and_the_default_is_five() -> None:
    content = _content(["Tech"])

    with pytest.raises(ValueError):
        cap_lower_layers(content, rows_per_area=-1)
    assert LOWER_ROWS_PER_AREA == 5


def test_the_input_content_is_unchanged_by_the_cut() -> None:
    content = _content(
        ["Tech"],
        news_clusters=[_sec("C", _item("c1", "Tech"))],
        filtered_headlines=[_item(f"h{i}", "Tech") for i in range(7)],
        articles_included=8,
    )
    before = content.model_dump()

    cap_lower_layers(content)

    assert content.model_dump() == before


def test_the_cut_spares_the_unplaced_section_even_when_an_area_is_named_other() -> None:
    headlines = [_item(f"o{i}", None) for i in range(7)]
    content = _content(["Other"], filtered_headlines=headlines, articles_included=7)

    assert len(cap_lower_layers(content).filtered_headlines) == 7


def test_the_cut_of_a_named_other_area_leaves_the_unplaced_entries_whole() -> None:
    content = _content(
        ["Other"],
        filtered_headlines=[_item(f"n{i}", "other") for i in range(6)] + [_item("u", None)],
        articles_included=7,
    )

    kept = [i.title for i in cap_lower_layers(content).filtered_headlines]

    assert kept == ["n0", "n1", "n2", "n3", "n4", "u"]
