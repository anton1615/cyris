"""/settings stores the two birth-threshold numbers, and an unset threshold is simply absent."""

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


@pytest.fixture
def db() -> SqliteD1:
    return SqliteD1()


@pytest.fixture
async def client(db: SqliteD1) -> TestClient:
    client = await _client(TriageServer(area_store=D1AreaStore(db)))
    yield client
    await client.close()


def test_an_empty_table_is_no_threshold(db: SqliteD1) -> None:
    assert D1AreaStore(db).birth_threshold() is None


async def test_a_second_put_replaces_the_one_row(client: TestClient, db: SqliteD1) -> None:
    first = await client.put("/api/areas/birth-threshold", json={"articles": 10, "issues": 3})
    second = await client.put("/api/areas/birth-threshold", json={"articles": 8, "issues": 2})

    assert (first.status, second.status) == (200, 200)
    assert await second.json() == {"ok": True, "articles": 8, "issues": 2}
    assert db.query("SELECT count(*) AS n FROM area_birth_threshold").rows[0]["n"] == 1
    assert D1AreaStore(db).birth_threshold() == (8, 2)


async def test_a_value_below_one_is_refused_and_nothing_changes(
    client: TestClient, db: SqliteD1
) -> None:
    await client.put("/api/areas/birth-threshold", json={"articles": 8, "issues": 2})

    response = await client.put("/api/areas/birth-threshold", json={"articles": 0, "issues": 3})

    assert response.status == 400
    assert (await response.json())["field"] == "articles"
    assert D1AreaStore(db).birth_threshold() == (8, 2)


async def test_a_server_without_an_area_store_refuses_the_write() -> None:
    client = await _client(TriageServer())
    try:
        response = await client.put("/api/areas/birth-threshold", json={"articles": 1, "issues": 1})
    finally:
        await client.close()

    assert response.status == 409
