"""Reader-facing areas in D1, written from /settings.

A D1 deployment's only list of areas; a `json` deployment has none, so the run places
nothing and records nothing there.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from pydantic import ValidationError

from cyris.adapters.store.d1 import D1Queryable
from cyris.domain.areas import Area

logger = logging.getLogger(__name__)

_AREA_COLUMNS = (
    "id, name, position, status, merged_into, proposal_articles, proposal_issues, "
    "last_proposed_run"
)


class D1AreaStore:
    """Read rows of `areas` and `area_birth_threshold`."""

    def __init__(self, client: D1Queryable) -> None:
        self._db = client

    def list_areas(self) -> list[Area]:
        """Every area of every status, by id. A row that fails its rule is logged and left out."""
        rows = self._db.query(f"SELECT {_AREA_COLUMNS} FROM areas ORDER BY id").rows
        areas = []
        for row in rows:
            try:
                areas.append(Area.model_validate(row))
            except ValidationError as e:
                logger.warning("Ignoring area %r: %s", row.get("name"), e)
        return areas

    def record_proposals(self, counts: Mapping[str, int], run_id: str | None) -> None:
        """Add one run's suggested counts to each proposed name, inserting names not yet seen.

        Only a `proposed` row takes counts: an active, merged or dismissed name is left as it
        is. A run counts once toward a proposal's issues, however many batches named it.
        """
        for raw_name, articles in counts.items():
            name = raw_name.strip()
            if not name:
                continue
            rows = self._db.query(
                "SELECT status, last_proposed_run FROM areas WHERE name = ?", [name]
            ).rows
            if not rows:
                first_run = run_id is not None
                self._db.query(
                    "INSERT INTO areas (name, position, status, proposal_articles, "
                    "proposal_issues, last_proposed_run) VALUES (?, 0, 'proposed', ?, ?, ?)",
                    [name, articles, 1 if first_run else 0, run_id],
                )
            elif rows[0]["status"] == "proposed":
                new_run = run_id is not None and run_id != rows[0]["last_proposed_run"]
                self._db.query(
                    "UPDATE areas SET proposal_articles = proposal_articles + ?, "
                    "proposal_issues = proposal_issues + ?, last_proposed_run = ? WHERE name = ?",
                    [
                        articles,
                        1 if new_run else 0,
                        run_id if new_run else rows[0]["last_proposed_run"],
                        name,
                    ],
                )
