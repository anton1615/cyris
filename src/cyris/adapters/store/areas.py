"""Reader-facing areas in D1, written from /settings.

A D1 deployment's only list of areas; a `json` deployment has none, so the run places
nothing and records nothing there.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from pydantic import ValidationError

from cyris.adapters.store.d1 import D1Queryable
from cyris.domain.areas import Area, fold_name

logger = logging.getLogger(__name__)

_AREA_COLUMNS = (
    "id, name, position, status, merged_into, proposal_articles, proposal_issues, last_proposed_run"
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
            rows = self._db.query("SELECT id, name, status, last_proposed_run FROM areas").rows
            match = next((row for row in rows if fold_name(row["name"]) == fold_name(name)), None)
            if match is None:
                first_run = run_id is not None
                self._db.query(
                    "INSERT INTO areas (name, position, status, proposal_articles, "
                    "proposal_issues, last_proposed_run) VALUES (?, 0, 'proposed', ?, ?, ?)",
                    [name, articles, 1 if first_run else 0, run_id],
                )
            elif match["status"] == "proposed":
                new_run = run_id is not None and run_id != match["last_proposed_run"]
                self._db.query(
                    "UPDATE areas SET proposal_articles = proposal_articles + ?, "
                    "proposal_issues = proposal_issues + ?, last_proposed_run = ? WHERE id = ?",
                    [
                        articles,
                        1 if new_run else 0,
                        run_id if new_run else match["last_proposed_run"],
                        match["id"],
                    ],
                )

    def birth_threshold(self) -> tuple[int, int] | None:
        """The (articles, issues) a proposal must reach, or None when it was never set."""
        rows = self._db.query("SELECT articles, issues FROM area_birth_threshold WHERE id = 1").rows
        if not rows:
            return None
        return rows[0]["articles"], rows[0]["issues"]

    def set_birth_threshold(self, articles: int, issues: int) -> None:
        """Write the one threshold row, replacing whatever it held."""
        self._db.query(
            "INSERT INTO area_birth_threshold (id, articles, issues) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET articles = excluded.articles, issues = excluded.issues",
            [articles, issues],
        )

    def get(self, area_id: int) -> Area | None:
        rows = self._db.query(f"SELECT {_AREA_COLUMNS} FROM areas WHERE id = ?", [area_id]).rows
        return Area.model_validate(rows[0]) if rows else None

    def name_taken(self, name: str, except_id: int) -> bool:
        """Whether another row of any status holds `name`, case-insensitively."""
        wanted = fold_name(name)
        rows = self._db.query("SELECT id, name FROM areas WHERE id <> ?", [except_id]).rows
        return any(fold_name(row["name"]) == wanted for row in rows)

    def rename(self, area_id: int, name: str) -> None:
        """Change the name only; position, status, counts and merges stay as they were."""
        self._db.query("UPDATE areas SET name = ? WHERE id = ?", [name, area_id])

    def set_order(self, ids: list[int]) -> None:
        """Give each active area its index in `ids` as its position."""
        for position, area_id in enumerate(ids):
            self._db.query("UPDATE areas SET position = ? WHERE id = ?", [position, area_id])

    def activate(self, area_id: int) -> int:
        """Make a proposal active, last in the order; the rows changed, 0 or 1."""
        return self._db.query(
            "UPDATE areas SET status = 'active', position = "
            "(SELECT coalesce(max(position) + 1, 0) FROM areas WHERE status = 'active') "
            "WHERE id = ? AND status = 'proposed'",
            [area_id],
        ).changes

    def dismiss(self, area_id: int) -> int:
        """Reject a proposal; its row and counts stay, so its name is not proposed again."""
        return self._db.query(
            "UPDATE areas SET status = 'dismissed' WHERE id = ? AND status = 'proposed'",
            [area_id],
        ).changes

    def merge(self, area_id: int, into: int) -> int:
        """Fold a proposal into an active area; its name still resolves to that area."""
        return self._db.query(
            "UPDATE areas SET status = 'merged', merged_into = ? "
            "WHERE id = ? AND status = 'proposed'",
            [into, area_id],
        ).changes

    def insert_proposed(self, name: str) -> int:
        """Add a name the model proposed and nothing holds yet, with no counts; its id."""
        self._db.query(
            "INSERT INTO areas (name, position, status, proposal_articles, proposal_issues) "
            "VALUES (?, 0, 'proposed', 0, 0)",
            [name],
        )
        return self._db.query("SELECT id FROM areas WHERE name = ?", [name]).rows[0]["id"]
