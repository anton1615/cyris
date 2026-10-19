"""The onboarding feed catalog: every entry reads as a `SourceConfig`.

docs/milestones/cold-start-onboarding.md sets the shape this file holds the catalog to:
a `SourceConfig` per entry, `tier: filter`, at least one tag each, and both a 繁中 and an
English entry so onboarding has something to offer either reader. Whether a URL still
answers is `scripts/validate_feed_catalog.py`'s question, not this file's — a reviewer-run
gate, not a test, because it asks about the network rather than the code
(`tests/CLAUDE.md` R3).
"""

import json
from pathlib import Path

import pytest

from cyris.domain.models import SourceConfig, Tier

pytestmark = [pytest.mark.unit, pytest.mark.guard]

CATALOG_PATH = Path(__file__).resolve().parents[1] / "src" / "cyris" / "onboarding_feeds.json"


def _raw_feeds() -> list[dict]:
    return json.loads(CATALOG_PATH.read_text())["feeds"]


def test_every_entry_reads_as_a_source_config() -> None:
    feeds = _raw_feeds()
    assert feeds, "catalog is empty"
    for entry in feeds:
        source = SourceConfig.model_validate(entry)
        assert source.fetchable, f"{source.name}: not fetchable (needs a url)"


def test_every_entry_is_filter_tier() -> None:
    for entry in _raw_feeds():
        source = SourceConfig.model_validate(entry)
        assert source.tier == Tier.FILTER, f"{source.name}: tier {source.tier}, want filter"


def test_every_entry_carries_at_least_one_tag() -> None:
    for entry in _raw_feeds():
        source = SourceConfig.model_validate(entry)
        assert source.tags, f"{source.name}: no tags"


def test_every_entry_names_a_language() -> None:
    for entry in _raw_feeds():
        source = SourceConfig.model_validate(entry)
        assert source.language in ("zh", "en"), f"{source.name}: language {source.language!r}"


def test_catalog_has_both_zh_and_en_entries() -> None:
    sources = [SourceConfig.model_validate(entry) for entry in _raw_feeds()]
    languages = {s.language for s in sources}
    assert "zh" in languages
    assert "en" in languages


def test_no_area_is_pre_assigned() -> None:
    """area_id points at a reader's own `areas` row; the catalog cannot know it in advance."""
    for entry in _raw_feeds():
        source = SourceConfig.model_validate(entry)
        assert source.area_id is None, f"{source.name}: area_id set"


def test_names_are_unique() -> None:
    names = [SourceConfig.model_validate(entry).name for entry in _raw_feeds()]
    assert len(names) == len(set(names)), "duplicate source name"
