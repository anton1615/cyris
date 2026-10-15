"""POST /api/areas/assign places many sources at once, and refuses a bad request before writing."""

import pytest
from aiohttp.test_utils import TestClient, TestServer
from fakes import SqliteD1

from cyris.adapters.store.areas import D1AreaStore
from cyris.adapters.store.source_store import D1SourceStore
from cyris.domain.models import SourceConfig
from cyris.entrypoints.triage_server import TriageServer

pytestmark = pytest.mark.integration


async def _client(server: TriageServer) -> TestClient:
    client = TestClient(TestServer(server._app))
    await client.start_server()
    return client


def _area(db: SqliteD1, name: str, status: str, position: int = 0) -> int:
    db.query(
        "INSERT INTO areas (name, position, status) VALUES (?, ?, ?)",
        [name, position, status],
    )
    return db.query("SELECT max(id) AS id FROM areas").rows[0]["id"]


def _source(db: SqliteD1, name: str, **fields) -> None:
    D1SourceStore(db).upsert(SourceConfig(name=name, url=f"https://{name}.example/f", **fields))


def _area_ids(db: SqliteD1, name: str) -> int | None:
    return D1SourceStore(db).list_sources()[name].area_id


def _statuses(db: SqliteD1) -> dict[str, str]:
    return {row["name"]: row["status"] for row in db.query("SELECT name, status FROM areas").rows}


@pytest.fixture
def db() -> SqliteD1:
    db = SqliteD1()
    _area(db, "Tech", "active", 0)
    _area(db, "Dropped", "dismissed")
    _area(db, "Merged", "merged")
    _area(db, "Energy", "proposed")
    _source(db, "A")
    _source(db, "B")
    return db


def _server(db: SqliteD1) -> TriageServer:
    return TriageServer(area_store=D1AreaStore(db), source_store=D1SourceStore(db))


async def _post(db: SqliteD1, body, server: TriageServer | None = None):
    client = await _client(server or _server(db))
    try:
        response = await client.post("/api/areas/assign", json=body)
        return response.status, await response.json()
    finally:
        await client.close()


async def test_an_unknown_source_is_refused_with_its_name_and_writes_nothing(db: SqliteD1) -> None:
    status, data = await _post(db, {"assignments": {"Ghost": 1, "A": 1}})

    assert status == 400
    assert data == {
        "ok": False,
        "error": "Unknown sources: Ghost",
        "field": "assignments",
        "names": ["Ghost"],
    }
    assert _area_ids(db, "A") is None


async def test_a_dismissed_missing_or_merged_target_is_refused_and_writes_nothing(
    db: SqliteD1,
) -> None:
    dropped = _area(db, "Gone soon", "dismissed")
    energy = _area(db, "Also proposed", "proposed")

    status, _ = await _post(db, {"assignments": {"A": dropped}})
    assert status == 409
    assert _area_ids(db, "A") is None

    status, _ = await _post(db, {"assignments": {"A": 999}})
    assert status == 404
    assert _area_ids(db, "A") is None

    status, _ = await _post(db, {"assignments": {"A": energy, "B": dropped}})
    assert status == 409
    assert _statuses(db)["Also proposed"] == "proposed"
    assert _area_ids(db, "A") is None
    assert _area_ids(db, "B") is None


@pytest.mark.parametrize(
    "body",
    [
        {"assignments": {"A": "1"}},
        {"assignments": {"A": True}},
        {"assignments": {"A": 1.5}},
        [1, 2],
        {},
        {"assignments": [["A", 1]]},
    ],
)
async def test_a_malformed_body_is_refused_as_assignments_and_writes_nothing(
    db: SqliteD1, body
) -> None:
    status, data = await _post(db, body)

    assert status == 400
    assert data["field"] == "assignments"
    assert _area_ids(db, "A") is None


async def test_without_an_area_table_the_answer_matches_the_order_route(db: SqliteD1) -> None:
    server = TriageServer(source_store=D1SourceStore(db))
    client = await _client(server)
    try:
        assign = await client.post("/api/areas/assign", json={"assignments": {"A": 1}})
        order = await client.put("/api/areas/order", json={"ids": []})
        assign_body, order_body = await assign.json(), await order.json()
    finally:
        await client.close()

    assert assign.status == order.status == 409
    assert assign_body == order_body
