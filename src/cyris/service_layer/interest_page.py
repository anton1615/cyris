"""The interest map, rendered on request from the reader's votes and live holds."""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from cyris.domain.interest_graph import MAX_INTEREST_NODES, build_graph
from cyris.domain.suppression import SUPPRESSION_CLOCK_DAYS


def render_interest_page(read: Callable[[str], Any], writer: Any, now: datetime) -> str:
    """The page for `now`: `read` is `D1InterestReader.read`, `writer` an `HtmlDigestWriter`."""
    at = now.isoformat(timespec="seconds")
    inputs = read(at)
    graph = build_graph(
        inputs.voted,
        inputs.clocks,
        now,
        clock_days=SUPPRESSION_CLOCK_DAYS,
        max_nodes=MAX_INTEREST_NODES,
    )
    return writer.render_interest(
        graph,
        holds_active=inputs.active_holds > 0,
        generated_at=at,
        clock_days=SUPPRESSION_CLOCK_DAYS,
    )
