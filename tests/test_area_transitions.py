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


async def test_a_merged_name_keeps_resolving_to_the_active_area(
    client: TestClient, db: SqliteD1
) -> None:
    _row(db, 1, "Tech", "active", position=0)
    _row(db, 2, "Chips", "proposed")

    response = await client.post("/api/areas/2/merge", json={"into": 1})

    assert response.status == 200
    chips = D1AreaStore(db).get(2)
    assert (chips.status, chips.merged_into) == ("merged", 1)
    assert AreaCatalog(D1AreaStore(db).list_areas()).place("Chips").area.name == "Tech"

    D1AreaStore(db).record_proposals({"Chips": 3}, "r2")
    assert D1AreaStore(db).get(2).proposal_articles == 0
    assert len(D1AreaStore(db).list_areas()) == 2


async def test_merging_into_a_proposal_or_into_itself_is_refused(
    client: TestClient, db: SqliteD1
) -> None:
    _row(db, 1, "Tech", "active", position=0)
    _row(db, 2, "Chips", "proposed")
    _row(db, 5, "Maybe", "proposed")

    into_proposal = await client.post("/api/areas/2/merge", json={"into": 5})
    into_itself = await client.post("/api/areas/2/merge", json={"into": 2})

    assert (into_proposal.status, into_itself.status) == (400, 400)
    assert (await into_proposal.json())["field"] == "into"
    assert (await into_itself.json())["field"] == "into"


async def test_merging_an_active_area_is_a_409(client: TestClient, db: SqliteD1) -> None:
    _row(db, 1, "Tech", "active", position=0)
    _row(db, 3, "Food", "active", position=1)

    response = await client.post("/api/areas/1/merge", json={"into": 3})

    assert response.status == 409
    assert D1AreaStore(db).get(1).status == "active"


async def test_a_rename_of_the_target_is_seen_through_the_merged_name(
    client: TestClient, db: SqliteD1
) -> None:
    _row(db, 1, "Tech", "active", position=0)
    _row(db, 2, "Chips", "proposed")
    await client.post("/api/areas/2/merge", json={"into": 1})
    await client.put("/api/areas/1", json={"name": "Technology"})

    place = AreaCatalog(D1AreaStore(db).list_areas()).place("Chips")

    assert (place.kind, place.area.name) == ("active", "Technology")
