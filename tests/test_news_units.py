"""The remaining news's units: headlines grouped by title cosine, merged, batched."""

import math

import pytest

from cyris.domain.models import DigestItem
from cyris.domain.news_units import (
    GROUPING_COSINE,
    UNITS_PER_BATCH,
    batches,
    group_by_cosine,
    merge_unit,
)

pytestmark = pytest.mark.unit


def _unit_vector(degrees: float) -> list[float]:
    radians = math.radians(degrees)
    return [math.cos(radians), math.sin(radians)]


def test_the_measured_values_are_the_mvps() -> None:
    assert GROUPING_COSINE == {"gemini-embedding-001": 0.70}
    assert UNITS_PER_BATCH == 40


def test_a_key_joins_the_first_group_whose_leader_is_close_enough() -> None:
    # cos 30° ≈ 0.87 joins, cos 60° = 0.5 does not.
    vectors = {
        "a": _unit_vector(0),
        "b": _unit_vector(90),
        "c": _unit_vector(30),
        "d": _unit_vector(60),
    }
    assert group_by_cosine(["a", "b", "c", "d"], vectors, 0.70) == [["a", "c"], ["b", "d"]]


def test_closeness_is_to_the_leader_not_to_the_last_member() -> None:
    # c is close to b (30°) but 60° from the leader a, so the chain does not grow.
    vectors = {"a": _unit_vector(0), "b": _unit_vector(30), "c": _unit_vector(60)}
    assert group_by_cosine(["a", "b", "c"], vectors, 0.70) == [["a", "b"], ["c"]]


def test_a_key_with_no_vector_stands_alone_and_keeps_its_place() -> None:
    vectors = {"a": _unit_vector(0), "c": _unit_vector(10)}
    assert group_by_cosine(["a", "b", "c"], vectors, 0.70) == [["a", "c"], ["b"]]


def test_the_threshold_itself_groups() -> None:
    vectors = {"a": [1.0, 0.0], "b": [0.70, math.sqrt(1 - 0.70**2)]}
    assert group_by_cosine(["a", "b"], vectors, 0.70) == [["a", "b"]]


def test_batches_cut_in_order_with_the_last_one_short() -> None:
    assert batches(list(range(85)), 40) == [
        list(range(40)),
        list(range(40, 80)),
        list(range(80, 85)),
    ]
    assert batches([], 40) == []


def test_batches_refuse_a_size_below_one() -> None:
    with pytest.raises(ValueError):
        batches([1], 0)


def test_a_merged_unit_carries_every_members_links_and_sources_in_lockstep() -> None:
    a = DigestItem(
        title="A", summary="a.", sources=["S1"], urls=["https://a"], score=3.0, area="World"
    )
    b = DigestItem(
        title="B",
        summary="b.",
        sources=["S2"],
        urls=["https://b"],
        ref_urls=["https://b-ref"],
        score=7.0,
        area="World",
    )
    unit = merge_unit([a, b])
    assert unit.title == "A"
    assert unit.urls == ["https://a", "https://b"]
    assert unit.sources == ["S1", "S2"]
    assert unit.score == 7.0
    assert unit.area == "World"
    # A member with no reference link contributes its own http URL, as a cluster does.
    assert unit.ref_urls == ["https://a", "https://b-ref"]


def test_a_merged_unit_without_reference_links_has_none() -> None:
    a = DigestItem(title="A", summary="", sources=["S1"], urls=["https://a"])
    assert merge_unit([a]).ref_urls == []
    assert merge_unit([a]).score is None
