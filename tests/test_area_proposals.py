"""The store counts how often a model suggests a name that is not yet an active area."""

import pytest
from fakes import SqliteD1

from cyris.adapters.store.areas import D1AreaStore

pytestmark = pytest.mark.unit


def _row(db: SqliteD1, name: str) -> dict | None:
    rows = db.query(
        "SELECT name, status, merged_into, proposal_articles, proposal_issues, "
        "last_proposed_run FROM areas WHERE name = ?",
        [name],
    ).rows
    return dict(rows[0]) if rows else None


def _count(db: SqliteD1) -> int:
    return db.query("SELECT count(*) AS n FROM areas").rows[0]["n"]


def _seed(db: SqliteD1, name: str, status: str, merged_into: int | None = None) -> None:
    db.query(
        "INSERT INTO areas (name, position, status, merged_into, proposal_articles, "
        "proposal_issues, last_proposed_run) VALUES (?, 0, ?, ?, 0, 0, NULL)",
        [name, status, merged_into],
    )


def test_counts_accumulate_and_issues_count_each_run_once() -> None:
    db = SqliteD1()
    store = D1AreaStore(db)

    store.record_proposals({"Space": 3}, "r1")
    assert _row(db, "Space")["proposal_articles"] == 3
    assert _row(db, "Space")["proposal_issues"] == 1

    store.record_proposals({"space": 2}, "r1")
    assert _row(db, "Space")["proposal_articles"] == 5
    assert _row(db, "Space")["proposal_issues"] == 1

    store.record_proposals({"Space": 1}, "r2")
    assert _row(db, "Space")["proposal_articles"] == 6
    assert _row(db, "Space")["proposal_issues"] == 2


def test_a_null_run_adds_articles_but_no_issue() -> None:
    db = SqliteD1()
    store = D1AreaStore(db)

    store.record_proposals({"Space": 2}, None)
    assert _row(db, "Space")["proposal_issues"] == 0

    store.record_proposals({"Space": 1}, "r1")
    assert _row(db, "Space")["proposal_issues"] == 1


def test_active_merged_and_dismissed_names_are_left_alone() -> None:
    db = SqliteD1()
    _seed(db, "Food", "dismissed")
    _seed(db, "Chips", "merged", merged_into=1)
    _seed(db, "Tech", "active")
    before = _count(db)

    D1AreaStore(db).record_proposals({"Food": 3, "Chips": 3, "Tech": 3}, "r1")

    assert _row(db, "Food")["proposal_articles"] == 0
    assert _row(db, "Chips")["proposal_articles"] == 0
    assert _row(db, "Tech")["proposal_articles"] == 0
    assert _count(db) == before
