"""scripts/validate_feed_catalog.py: both parsers run, and recency counts the same way
a wizard session would see it.

Never aimed at a real feed (`scripts/validate_feed_catalog.py`'s own docstring explains
why): every HTTP fetch here is a stub, and `parse.js` runs for real through Node so a
change that breaks the Worker's own parser shows up here instead of only in production.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cyris.domain.models import SourceConfig

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from validate_feed_catalog import (  # noqa: E402
    RECENT_WINDOW_DAYS,
    _print_report,
    _recent_count,
    evaluate_feed,
    load_catalog,
    parse_with_feedparser,
    parse_with_worker,
    validate_catalog,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 20, tzinfo=UTC)
RECENT = NOW - timedelta(days=2)
STALE = NOW - timedelta(days=30)


def _rfc822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


def _rss(*items: tuple[str, str, str]) -> bytes:
    """items: (title, link, pubDate string)."""
    body = "".join(
        f"<item><title>{title}</title><link>{link}</link>"
        f"<pubDate>{pub_date}</pubDate><description>body</description></item>"
        for title, link, pub_date in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{body}</channel></rss>'.encode()


def _require_node() -> None:
    if shutil.which("node") is None:
        assert "CI" not in os.environ, "CI must provide Node to run parse.js"
        pytest.skip("node not installed")


def test_recent_count_is_inclusive_of_the_window_edge() -> None:
    cutoff = NOW - timedelta(days=RECENT_WINDOW_DAYS)
    assert _recent_count([cutoff], NOW) == 1
    assert _recent_count([cutoff - timedelta(seconds=1)], NOW) == 0


def test_recent_count_ignores_a_date_in_the_future() -> None:
    assert _recent_count([NOW + timedelta(days=1)], NOW) == 0


def test_an_empty_report_prints_none_usable(capsys) -> None:
    _print_report([])
    assert "0/0 usable." in capsys.readouterr().out


def test_parse_with_feedparser_reads_dates_and_skips_undated_entries() -> None:
    xml = _rss(
        ("Recent", "https://example.test/a", _rfc822(RECENT)),
        ("Stale", "https://example.test/b", _rfc822(STALE)),
    )
    dates = parse_with_feedparser(xml)
    assert len(dates) == 2
    assert _recent_count(dates, NOW) == 1


def test_parse_with_worker_agrees_with_feedparser_on_a_clean_feed() -> None:
    _require_node()
    xml = _rss(
        ("Recent", "https://example.test/a?utm_source=x", _rfc822(RECENT)),
        ("Stale", "https://example.test/b", _rfc822(STALE)),
    )
    feedparser_dates = parse_with_feedparser(xml)
    worker_dates = parse_with_worker(xml, "Test Feed")
    assert len(worker_dates) == len(feedparser_dates) == 2
    assert _recent_count(worker_dates, NOW) == _recent_count(feedparser_dates, NOW) == 1


@pytest.mark.asyncio
async def test_evaluate_feed_is_usable_when_both_parsers_find_a_recent_article() -> None:
    _require_node()
    xml = _rss(("Recent", "https://example.test/a", _rfc822(RECENT)))
    source = SourceConfig(name="Good Feed", url="https://example.test/feed.xml", tags=["news"])

    async def fetch(url: str) -> bytes:
        return xml

    result = await evaluate_feed(source, fetch, NOW)
    assert result.usable
    assert result.feedparser_recent == result.worker_recent == 1


@pytest.mark.asyncio
async def test_evaluate_feed_drops_a_feed_with_nothing_recent() -> None:
    _require_node()
    xml = _rss(("Old", "https://example.test/a", _rfc822(STALE)))
    source = SourceConfig(name="Stale Feed", url="https://example.test/feed.xml")

    async def fetch(url: str) -> bytes:
        return xml

    result = await evaluate_feed(source, fetch, NOW)
    assert not result.usable
    assert result.feedparser_recent == 0
    assert result.worker_recent == 0


@pytest.mark.asyncio
async def test_evaluate_feed_drops_a_feed_only_one_parser_can_date() -> None:
    """A date format one parser reads and the other doesn't is still a drop.

    docs/milestones/cold-start-onboarding.md: ETtoday and 遠見's date format beat
    feedparser (`rss_source.py:97-100`) though an HTTP GET answers 200; this is that
    shape, built rather than fetched, so the check runs with no network at all.
    """
    _require_node()
    xml = (
        b'<?xml version="1.0"?><rss version="2.0"><channel>'
        b"<item><title>Odd date</title><link>https://example.test/a</link>"
        b"<pubDate>not a real date</pubDate><description>body</description></item>"
        b"</channel></rss>"
    )
    source = SourceConfig(name="Odd Date Feed", url="https://example.test/feed.xml")

    async def fetch(url: str) -> bytes:
        return xml

    result = await evaluate_feed(source, fetch, NOW)
    assert result.feedparser_entries == 0  # feedparser drops the undated entry
    assert not result.usable


@pytest.mark.asyncio
async def test_evaluate_feed_reports_a_fetch_failure() -> None:
    source = SourceConfig(name="Unreachable", url="https://example.test/feed.xml")

    async def fetch(url: str) -> bytes:
        raise RuntimeError("HTTP 404")

    result = await evaluate_feed(source, fetch, NOW)
    assert not result.usable
    assert result.error == "HTTP 404"


def test_parse_with_worker_raises_on_a_missing_node_binary() -> None:
    xml = _rss(("Recent", "https://example.test/a", _rfc822(RECENT)))
    with pytest.raises(OSError):
        parse_with_worker(xml, "No Node", node_bin="no-such-node-binary-xyz")


@pytest.mark.asyncio
async def test_evaluate_feed_reports_a_worker_parser_failure(monkeypatch) -> None:
    """A broken worker parser is reported per feed, not raised, and the feed is dropped."""
    xml = _rss(("Recent", "https://example.test/a", _rfc822(RECENT)))
    source = SourceConfig(name="Broken Worker", url="https://example.test/feed.xml")

    def boom(content: bytes, name: str, node_bin: str = "node", timeout: float = 30):
        raise RuntimeError("parse.js exploded")

    monkeypatch.setattr("validate_feed_catalog.parse_with_worker", boom)

    async def fetch(url: str) -> bytes:
        return xml

    result = await evaluate_feed(source, fetch, NOW)
    assert not result.usable
    assert result.error == "parse.js: parse.js exploded"
    assert result.feedparser_recent == 1  # feedparser still ran


@pytest.mark.asyncio
async def test_validate_catalog_preserves_order_and_runs_concurrently() -> None:
    _require_node()
    sources = [
        SourceConfig(name=f"Feed {i}", url=f"https://example.test/{i}.xml") for i in range(3)
    ]
    xml = _rss(("Recent", "https://example.test/a", _rfc822(RECENT)))

    async def fetch(url: str) -> bytes:
        return xml

    results = await validate_catalog(sources, fetch, now=NOW)
    assert [r.name for r in results] == ["Feed 0", "Feed 1", "Feed 2"]
    assert all(r.usable for r in results)


def test_load_catalog_reads_the_real_file() -> None:
    root = Path(__file__).resolve().parents[1]
    sources = load_catalog(root / "src" / "cyris" / "onboarding_feeds.json")
    assert len(sources) > 0
    assert all(isinstance(s, SourceConfig) for s in sources)
