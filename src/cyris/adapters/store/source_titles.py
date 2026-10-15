"""The newest article titles of each source, for the Propose prompt to read.

One query over `stored_articles`, bounded by `first_seen_at`: a source that has
gone quiet reads as no titles, not as months-old ones.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cyris.adapters.store.d1 import D1Queryable

# Five headlines show a source's subject; each source's line is paid for in every Propose call.
TITLES_PER_SOURCE = 5
# A headline fits in 80 characters; a longer title is mostly a dek the model does not need.
TITLE_MAX_CHARS = 80
# The window feed_health.py's QUIET_DAYS uses: a source silent that long is quiet, not described.
TITLE_LOOKBACK_DAYS = 30

_RECENT = """
SELECT source_name, title FROM (
  SELECT source_name, title,
         row_number() OVER (PARTITION BY source_name ORDER BY published_at DESC, url) AS rn
    FROM stored_articles
   WHERE first_seen_at >= ? AND trim(title) <> ''
) WHERE rn <= ?
ORDER BY source_name, rn
"""


def _shorten(title: str) -> str:
    flat = " ".join(title.split())
    if len(flat) <= TITLE_MAX_CHARS:
        return flat
    return flat[: TITLE_MAX_CHARS - 1] + "…"


class D1SourceTitles:
    """Read the recent titles of every source in one query."""

    def __init__(self, client: D1Queryable) -> None:
        self._db = client

    def recent(self, now: datetime) -> dict[str, list[str]]:
        """Source name to its newest titles, newest first; a source with none is absent."""
        since = (now - timedelta(days=TITLE_LOOKBACK_DAYS)).astimezone(UTC)
        rows = self._db.query(
            _RECENT, [since.isoformat(timespec="microseconds"), TITLES_PER_SOURCE]
        ).rows
        titles: dict[str, list[str]] = {}
        for row in rows:
            titles.setdefault(row["source_name"], []).append(_shorten(row["title"]))
        return titles
