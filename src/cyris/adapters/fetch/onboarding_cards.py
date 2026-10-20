"""The onboarding wizard's vote cards, read through the run's own RSS edge.

A card's URL must be the one the first run reads from the same feed, or a vote
would land on a row the run never sees and the article would reach the first
issue a second time under its other URL. So the cards are not fetched by a
parser of their own: `bootstrap.build_onboarding_articles` hands this module the
`FetchSource` `build_deps` wires for RSS, and the cards are what its
`fetch_articles` returns. With the rss Worker that is the buffer, after a
`POST /poll` that fills it with the feeds just picked; without it, `RssSource`
polling the feeds directly, through the same feedparser and tracking-parameter
strip a run uses.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from cyris.domain.models import Article, SourceConfig
from cyris.service_layer.ports import FetchSource

logger = logging.getLogger(__name__)

# Cards drawn from each source, and from all of them. Grade A (docs/architecture.md
# §5): each card is a decision the reader is asked for. Three per source says what
# a source is about when the pick is small; the total holds a full pick of 12 feeds
# to one card each, since 36 cards read as a triage session (owner, 2026-10-20).
# The reader may leave any card alone.
CARDS_PER_SOURCE = 3
CARDS_TOTAL = 12

# How far back the cards may reach: the rss Worker's `RETENTION_DAYS`, the
# buffer's whole depth, so a direct poll offers the same span as the buffer.
BUFFER_DAYS = 8


def rss_sources(sources: dict[str, SourceConfig]) -> dict[str, SourceConfig]:
    """The sources a card can come from: the RSS feeds, as the Worker polls them."""
    return {name: s for name, s in sources.items() if s.type == "rss" and s.url}


@dataclass(frozen=True)
class CardDraw:
    """The cards to show, and the sources the read found no article for."""

    cards: list[Article]
    # By the sources' own order. A source here had nothing in the read at all,
    # not merely too few to draw from.
    empty: list[str]


def draw_cards(articles: list[Article], sources: dict[str, SourceConfig]) -> CardDraw:
    """Each source's oldest articles, at most `CARDS_PER_SOURCE`, interleaved across sources
    and cut at `CARDS_TOTAL`.

    Only the older half of a source's articles is drawn from, by publish time:
    a voted article leaves the first issue's candidates, and the newest are
    what that issue is for. A source with one article therefore gives no card,
    and still is not empty. The cards alternate between sources, so the first
    few already span the reader's picks.
    """
    by_source: dict[str, dict[str, Article]] = {name: {} for name in rss_sources(sources)}
    for article in articles:
        held = by_source.get(article.source_name)
        if held is not None:
            held.setdefault(article.url, article)

    drawn = []
    for held in by_source.values():
        oldest = sorted(held.values(), key=lambda a: (a.published_at, a.url))
        drawn.append(oldest[: min(CARDS_PER_SOURCE, len(oldest) // 2)])

    cards = [
        column[row] for row in range(CARDS_PER_SOURCE) for column in drawn if row < len(column)
    ][:CARDS_TOTAL]
    empty = [name for name, held in by_source.items() if not held]
    return CardDraw(cards=cards, empty=empty)


@dataclass(frozen=True)
class CardRead:
    """What one read found, and why the poll before it failed, when it did."""

    articles: list[Article]
    poll_error: str | None = None


class OnboardingArticles:
    """Reads the reader's feeds through the run's RSS `FetchSource`.

    `poll` is the rss Worker's `POST /poll`, None without a Worker: `RssSource`
    fetches the feeds on every read, so there is no buffer to fill first.
    """

    def __init__(
        self,
        source: FetchSource,
        poll: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        self._source = source
        self._poll = poll

    async def read(self, sources: dict[str, SourceConfig], now: datetime) -> list[Article]:
        """The feeds' articles of the last `BUFFER_DAYS`, as a run's fetch maps them."""
        feeds = rss_sources(sources)
        if not feeds:
            return []
        articles = await self._source.fetch_articles(
            after=now - timedelta(days=BUFFER_DAYS), before=now, sources=feeds
        )
        return [a for a in articles if a.source_name in feeds]

    async def refresh(
        self, sources: dict[str, SourceConfig], now: Callable[[], datetime]
    ) -> CardRead:
        """Poll the Worker for the feeds now, then read.

        A failed poll still reads: the buffer may already hold the feeds from
        an earlier tick, and the reason goes back to say why it may not. `now`
        is called after the poll, so the read's window covers the rows it stored.
        """
        feeds = rss_sources(sources)
        if not feeds:
            return CardRead(articles=[])
        poll_error = None
        if self._poll is not None:
            try:
                await self._poll(len(feeds))
            except Exception as e:  # noqa: BLE001 - the read goes on; the reason is reported
                logger.warning("Onboarding poll failed: %s", e)
                poll_error = str(e) or type(e).__name__
        return CardRead(articles=await self.read(feeds, now()), poll_error=poll_error)
