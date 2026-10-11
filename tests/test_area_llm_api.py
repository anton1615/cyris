"""/settings suggests an area per source and proposes areas for all, never failing a save."""

import json
import logging

import pytest
from aiohttp.test_utils import TestClient, TestServer
from fakes import FakeLLM, SqliteD1

from cyris.adapters.store.areas import D1AreaStore
from cyris.adapters.store.source_store import D1SourceStore
from cyris.diagnostics import doctor
from cyris.entrypoints.triage_server import TriageServer

pytestmark = pytest.mark.integration

SECRET = "sk-secret-123"


def _row(db: SqliteD1, area_id: int, name: str, status: str, **counts) -> None:
    db.query(
        "INSERT INTO areas (id, name, position, status, proposal_articles, proposal_issues) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [area_id, name, area_id, status, counts.get("articles", 0), counts.get("issues", 0)],
    )


async def _client(db: SqliteD1, llm_factory, **extra) -> TestClient:
    server = TriageServer(
        area_store=D1AreaStore(db),
        source_store=D1SourceStore(db),
        llm_factory=llm_factory,
        **extra,
    )
    client = TestClient(TestServer(server._app))
    await client.start_server()
    return client


def _suggest_body(**fields) -> dict:
    return {
        "name": "Feed",
        "url": "https://feed.example/rss",
        "type": "rss",
        "tags": ["x"],
        "homepage": None,
        **fields,
    }


@pytest.fixture
def db() -> SqliteD1:
    db = SqliteD1()
    _row(db, 1, "Tech", "active")
    _row(db, 2, "Food", "active")
    return db


async def test_a_suggestion_is_one_active_area_or_none(db: SqliteD1) -> None:
    llm = FakeLLM(['{"area": "tech"}', '{"area": "Art"}'])
    client = await _client(db, lambda values: llm)
    try:
        first = await (await client.post("/api/sources/suggest-area", json=_suggest_body())).json()
        second = await (await client.post("/api/sources/suggest-area", json=_suggest_body())).json()
    finally:
        await client.close()

    assert first["suggestion"] == {"id": 1, "name": "Tech"}
    assert second["suggestion"] is None
    assert len(llm.calls) == 2


async def test_a_failed_call_is_a_200_without_the_key(db: SqliteD1, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    llm = FakeLLM(error=RuntimeError(f"401 key={SECRET}"))
    client = await _client(db, lambda values: llm)
    try:
        response = await client.post("/api/sources/suggest-area", json=_suggest_body())
        body = await response.text()
    finally:
        await client.close()

    data = json.loads(body)
    assert response.status == 200
    assert data["suggestion"] is None
    assert data["note"] == "The model call failed (RuntimeError)."
    assert SECRET not in body
    assert SECRET not in caplog.text


async def test_the_save_still_succeeds_after_a_failed_suggestion(db: SqliteD1) -> None:
    llm = FakeLLM(error=RuntimeError("boom"))
    client = await _client(db, lambda values: llm)
    try:
        await client.post("/api/sources/suggest-area", json=_suggest_body())
        save = await client.post(
            "/api/sources", json={"name": "Feed", "url": "https://feed.example/rss"}
        )
    finally:
        await client.close()

    assert save.status == 200
    assert "Feed" in D1SourceStore(db).list_sources()


async def test_the_factory_gets_the_provider_saved_on_the_settings_page(
    db: SqliteD1, monkeypatch
) -> None:
    async def healthy(llm_cfg):
        return doctor.Check("llm probe", "ok", "answered")

    monkeypatch.setattr(doctor, "probe_llm", healthy)

    received: list[dict] = []

    class Settings:
        def set(self, values):
            self.values = values

    def factory(values):
        received.append(dict(values))
        return FakeLLM('{"area": null}')

    client = await _client(db, factory, settings=Settings())
    try:
        saved = await client.post(
            "/api/settings", json={"provider": "gemini", "model": "gemini-test-model"}
        )
        assert saved.status == 200
        await client.post("/api/sources/suggest-area", json=_suggest_body())
    finally:
        await client.close()

    assert received[-1]["llm_provider.provider"] == "gemini"
    assert received[-1]["llm_provider.model"] == "gemini-test-model"


async def test_no_active_area_or_no_llm_asks_the_model_nothing(db: SqliteD1) -> None:
    llm = FakeLLM('{"area": "Tech"}')
    client = await _client(db, lambda values: None)
    try:
        no_llm = await (await client.post("/api/sources/suggest-area", json=_suggest_body())).json()
    finally:
        await client.close()

    empty = SqliteD1()
    client = await _client(empty, lambda values: llm)
    try:
        no_area = await (
            await client.post("/api/sources/suggest-area", json=_suggest_body())
        ).json()
    finally:
        await client.close()

    assert no_llm["suggestion"] is None and "LLM" in no_llm["note"]
    assert no_area["suggestion"] is None
    assert llm.calls == []
