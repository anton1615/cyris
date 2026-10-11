"""/settings reads, renames and reorders areas, and the page is told which ones it can write."""

import pytest
from aiohttp.test_utils import TestClient, TestServer
from fakes import SqliteD1

from cyris.adapters.store.areas import D1AreaStore
from cyris.entrypoints.triage_server import TriageServer

pytestmark = pytest.mark.integration


async def _client(server: TriageServer) -> TestClient:
    client = TestClient(TestServer(server._app))
    await client.start_server()
    return client


def _area(
    db: SqliteD1,
    name: str,
    status: str,
    *,
    position: int = 0,
    articles: int = 0,
    issues: int = 0,
    merged_into: int | None = None,
) -> int:
    db.query(
        "INSERT INTO areas (name, position, status, merged_into, proposal_articles, "
        "proposal_issues) VALUES (?, ?, ?, ?, ?, ?)",
        [name, position, status, merged_into, articles, issues],
    )
    return db.query("SELECT max(id) AS id FROM areas").rows[0]["id"]


@pytest.fixture
def db() -> SqliteD1:
    return SqliteD1()


@pytest.fixture
async def client(db: SqliteD1) -> TestClient:
    client = await _client(TriageServer(area_store=D1AreaStore(db)))
    yield client
    await client.close()


async def test_areas_come_in_the_order_a_reader_expects(client: TestClient, db: SqliteD1) -> None:
    _area(db, "B", "active", position=1)
    _area(db, "A", "active", position=0)
    _area(db, "X", "proposed", articles=4, issues=1)
    _area(db, "Y", "proposed", articles=9, issues=2)
    _area(db, "D", "dismissed")

    data = await (await client.get("/api/areas")).json()

    assert [a["name"] for a in data["areas"]] == ["A", "B", "Y", "X", "D"]


async def test_the_threshold_marks_proposals_and_unset_marks_nothing(
    client: TestClient, db: SqliteD1
) -> None:
    _area(db, "A", "active", position=0)
    _area(db, "X", "proposed", articles=4, issues=1)
    _area(db, "Y", "proposed", articles=9, issues=2)
    await client.put("/api/areas/birth-threshold", json={"articles": 5, "issues": 2})

    set_data = await (await client.get("/api/areas")).json()
    marks = {a["name"]: a["meets_birth_threshold"] for a in set_data["areas"]}
    assert marks == {"A": None, "X": False, "Y": True}
    assert set_data["birth_threshold"] == {"articles": 5, "issues": 2}

    db.query("DELETE FROM area_birth_threshold")
    unset = await (await client.get("/api/areas")).json()
    assert unset["birth_threshold"] is None
    assert all(a["meets_birth_threshold"] is None for a in unset["areas"])


async def test_no_area_store_is_read_only_and_empty() -> None:
    client = await _client(TriageServer())
    try:
        data = await (await client.get("/api/areas")).json()
    finally:
        await client.close()

    assert data == {"writable": False, "areas": [], "birth_threshold": None}


class _BrokenStore(D1AreaStore):
    def list_areas(self):
        raise RuntimeError("D1 said: no such table secret-detail")


async def test_a_store_read_error_is_a_500_without_the_exception_text() -> None:
    client = await _client(TriageServer(area_store=_BrokenStore(SqliteD1())))
    try:
        response = await client.get("/api/areas")
        body = await response.text()
    finally:
        await client.close()

    assert response.status == 500
    assert "secret-detail" not in body
