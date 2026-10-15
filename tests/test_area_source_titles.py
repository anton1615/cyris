"""The Propose prompt reads each source's newest titles, flattened and shortened, in one query."""

from datetime import UTC, datetime, timedelta

import pytest
from fakes import CountingD1

from cyris.adapters.store.source_titles import D1SourceTitles

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _article(db, url, source, title, published, first_seen) -> None:
    db.query(
        "INSERT INTO stored_articles (url, title, published_at, source_name, source_tier, "
        "first_seen_at) VALUES (?, ?, ?, ?, 'filter', ?)",
        [
            url,
            title,
            published.isoformat(timespec="microseconds"),
            source,
            first_seen.isoformat(timespec="microseconds"),
        ],
    )


def test_each_source_gets_its_five_newest_titles_in_one_query() -> None:
    db = CountingD1()
    for day in range(7):
        published = NOW - timedelta(days=day)
        _article(db, f"https://a.test/{day}", "A", f"A day {day}", published, NOW)
    _article(db, "https://b.test/1", "B", "B only", NOW, NOW)
    for n in range(60):
        _article(db, f"https://s{n}.test/1", f"S{n}", f"S{n} title", NOW, NOW)
    db.query_count = 0

    titles = D1SourceTitles(db).recent(NOW)

    assert titles["A"] == ["A day 0", "A day 1", "A day 2", "A day 3", "A day 4"]
    assert titles["B"] == ["B only"]
    assert len(titles) == 62
    assert db.query_count == 1


def test_a_title_is_flattened_cut_and_stale_or_empty_articles_are_skipped() -> None:
    db = CountingD1()
    _article(db, "https://a.test/long", "A", "x" * 200, NOW, NOW)
    _article(db, "https://a.test/ws", "A", "a\n  b\tc", NOW - timedelta(days=1), NOW)
    _article(db, "https://a.test/empty", "A", "   ", NOW - timedelta(days=2), NOW)
    _article(db, "https://a.test/old", "A", "too old", NOW, NOW - timedelta(days=31))
    _article(db, "https://a.test/recent", "A", "in window", NOW, NOW - timedelta(days=29))

    titles = D1SourceTitles(db).recent(NOW)

    assert titles["A"][0] == "x" * 79 + "…"
    assert len(titles["A"][0]) == 80
    assert titles["A"][1:] == ["in window", "a b c"]
    assert "too old" not in titles["A"]
    assert "" not in titles["A"]


def test_a_source_with_no_article_is_absent() -> None:
    db = CountingD1()
    _article(db, "https://a.test/1", "A", "A title", NOW, NOW)

    assert "B" not in D1SourceTitles(db).recent(NOW)
