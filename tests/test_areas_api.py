"""/settings reads, renames and reorders areas, and the page is told which ones it can write."""

import pytest
from aiohttp.test_utils import TestClient, TestServer
from fakes import SqliteD1

from cyris.adapters.store.areas import D1AreaStore
from cyris.domain.areas import AreaCatalog
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


async def test_a_rename_changes_only_the_name_and_sources_still_resolve(
    client: TestClient, db: SqliteD1
) -> None:
    tech = _area(db, "Tech", "active", position=0)
    db.query(
        "INSERT INTO sources (name, url, type, config) VALUES ('a', 'https://a.example', 'rss', ?)",
        [f'{{"area_id": {tech}}}'],
    )

    response = await client.put(f"/api/areas/{tech}", json={"name": "Technology"})

    assert response.status == 200
    assert D1AreaStore(db).get(tech).name == "Technology"
    assert D1AreaStore(db).get(tech).position == 0
    assert D1AreaStore(db).get(tech).status == "active"
    assert AreaCatalog(D1AreaStore(db).list_areas()).for_source(tech).name == "Technology"
    config = db.query("SELECT config FROM sources WHERE name = 'a'").rows[0]["config"]
    assert f'"area_id": {tech}' in config


async def test_a_rename_onto_another_rows_name_is_refused_case_insensitively(
    client: TestClient, db: SqliteD1
) -> None:
    tech = _area(db, "Tech", "active", position=0)
    _area(db, "Chips", "merged", merged_into=tech)

    response = await client.put(f"/api/areas/{tech}", json={"name": "chips"})

    assert response.status == 409
    assert (await response.json())["field"] == "name"
    assert D1AreaStore(db).get(tech).name == "Tech"


async def test_a_rename_onto_a_non_ascii_name_is_refused_case_insensitively(
    client: TestClient, db: SqliteD1
) -> None:
    _area(db, "Économie", "active", position=0)
    tech = _area(db, "Tech", "active", position=1)

    response = await client.put(f"/api/areas/{tech}", json={"name": "économie"})

    assert response.status == 409
    assert (await response.json())["field"] == "name"
    assert D1AreaStore(db).get(tech).name == "Tech"


async def test_a_blank_or_multiline_name_is_refused(client: TestClient, db: SqliteD1) -> None:
    tech = _area(db, "Tech", "active", position=0)

    blank = await client.put(f"/api/areas/{tech}", json={"name": "   "})
    multiline = await client.put(f"/api/areas/{tech}", json={"name": "Te\nch"})

    assert (blank.status, multiline.status) == (400, 400)
    assert (await blank.json())["field"] == "name"
    assert D1AreaStore(db).get(tech).name == "Tech"


async def test_renaming_an_unknown_id_is_a_404(client: TestClient) -> None:
    response = await client.put("/api/areas/99", json={"name": "Anything"})

    assert response.status == 404


async def test_reordering_sets_the_positions_the_list_follows(
    client: TestClient, db: SqliteD1
) -> None:
    one = _area(db, "One", "active", position=0)
    two = _area(db, "Two", "active", position=1)
    three = _area(db, "Three", "active", position=2)

    response = await client.put("/api/areas/order", json={"ids": [three, one, two]})

    assert response.status == 200
    listed = await (await client.get("/api/areas")).json()
    assert [(a["name"], a["position"]) for a in listed["areas"]] == [
        ("Three", 0),
        ("One", 1),
        ("Two", 2),
    ]


async def test_a_missing_active_id_is_refused_and_the_order_stays(
    client: TestClient, db: SqliteD1
) -> None:
    one = _area(db, "One", "active", position=0)
    two = _area(db, "Two", "active", position=1)
    three = _area(db, "Three", "active", position=2)
    await client.put("/api/areas/order", json={"ids": [one, two, three]})

    response = await client.put("/api/areas/order", json={"ids": [three, one]})

    assert response.status == 400
    assert (await response.json())["field"] == "ids"
    assert [a.id for a in D1AreaStore(db).list_areas() if a.status == "active"] == [one, two, three]


async def test_a_duplicate_or_a_proposed_id_is_refused(client: TestClient, db: SqliteD1) -> None:
    one = _area(db, "One", "active", position=0)
    two = _area(db, "Two", "active", position=1)
    _area(db, "Tech", "active", position=2)
    proposed = _area(db, "Maybe", "proposed", articles=1, issues=1)

    duplicate = await client.put("/api/areas/order", json={"ids": [one, one, two, 3]})
    with_proposed = await client.put("/api/areas/order", json={"ids": [one, two, 3, proposed]})

    assert (duplicate.status, with_proposed.status) == (400, 400)


async def test_a_rename_onto_the_unplaced_label_is_refused_case_insensitively(
    client: TestClient, db: SqliteD1
) -> None:
    tech = _area(db, "Tech", "active", position=0)

    response = await client.put(f"/api/areas/{tech}", json={"name": " other "})

    assert response.status == 409
    assert (await response.json())["field"] == "name"
    assert D1AreaStore(db).get(tech).name == "Tech"
