"""Reader-facing areas in D1, written from /settings.

A D1 deployment's only list of areas; a `json` deployment has none, so the run places
nothing and records nothing there.
"""

from __future__ import annotations

import logging

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
