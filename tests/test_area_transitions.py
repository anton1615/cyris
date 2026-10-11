"""A reader confirms, dismisses, merges and assigns areas; each change says whether it took."""

import pytest
from aiohttp.test_utils import TestClient, TestServer
from fakes import SqliteD1

from cyris.adapters.store.areas import D1AreaStore
from cyris.domain.areas import AreaCatalog
from cyris.entrypoints.triage_server import TriageServer

pytestmark = pytest.mark.integration


def _row(db: SqliteD1, area_id: int, name: str, status: str, position: int = 0) -> None:
    db.query(
        "INSERT INTO areas (id, name, position, status) VALUES (?, ?, ?, ?)",
        [area_id, name, position, status],
    )


async def _client(db: SqliteD1) -> TestClient:
    client = TestClient(TestServer(TriageServer(area_store=D1AreaStore(db))._app))
    await client.start_server()
    return client


@pytest.fixture
def db() -> SqliteD1:
    return SqliteD1()


@pytest.fixture
async def client(db: SqliteD1) -> TestClient:
    client = await _client(db)
    yield client
    await client.close()


async def test_activating_a_proposal_appends_it_to_the_closed_set(
    client: TestClient, db: SqliteD1
) -> None:
    _row(db, 1, "Tech", "active", position=0)
    _row(db, 2, "Food", "active", position=1)
    _row(db, 7, "Space", "proposed")

    response = await client.post("/api/areas/7/activate")

    assert response.status == 200
    body = await response.json()
    assert (body["ok"], body["id"], body["name"], body["position"]) == (True, 7, "Space", 2)
    assert AreaCatalog(D1AreaStore(db).list_areas()).names() == ["Tech", "Food", "Space"]


async def test_activating_an_active_area_is_a_409_and_changes_nothing(
    client: TestClient, db: SqliteD1
) -> None:
    _row(db, 1, "Tech", "active", position=0)

    response = await client.post("/api/areas/1/activate")

    assert response.status == 409
    assert D1AreaStore(db).get(1).status == "active"


async def test_activating_a_dismissed_area_is_a_409(client: TestClient, db: SqliteD1) -> None:
    _row(db, 3, "Old", "dismissed")

    response = await client.post("/api/areas/3/activate")

    assert response.status == 409
    assert D1AreaStore(db).get(3).status == "dismissed"


async def test_a_dismissed_name_keeps_its_row_and_stops_counting(
    client: TestClient, db: SqliteD1
) -> None:
    _row(db, 4, "Food", "proposed")
    db.query("UPDATE areas SET proposal_articles = 3 WHERE id = 4")

    response = await client.post("/api/areas/4/dismiss")
    D1AreaStore(db).record_proposals({"food": 5}, "r1")

    assert response.status == 200
    food = D1AreaStore(db).get(4)
    assert (food.status, food.proposal_articles) == ("dismissed", 3)


async def test_dismissing_an_active_area_is_a_409(client: TestClient, db: SqliteD1) -> None:
    _row(db, 1, "Tech", "active")

    response = await client.post("/api/areas/1/dismiss")

    assert response.status == 409
    assert D1AreaStore(db).get(1).status == "active"


async def test_dismissing_an_unknown_id_is_a_404(client: TestClient) -> None:
    response = await client.post("/api/areas/99/dismiss")

    assert response.status == 404
