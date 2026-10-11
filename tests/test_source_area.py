"""A source carries the id of its area, in D1, in sources.yaml and through sources push."""

import json

import pydantic
import pytest
import yaml
from fakes import SqliteD1

from cyris.adapters.store.source_store import D1SourceStore
from cyris.config import SourcesConfig
from cyris.domain.models import SourceConfig

pytestmark = pytest.mark.integration


def _source(name: str = "a", **fields) -> SourceConfig:
    return SourceConfig(name=name, url=f"https://{name}.example/feed", **fields)


def test_an_area_id_round_trips_through_the_config_json() -> None:
    db = SqliteD1()
    store = D1SourceStore(db)

    store.upsert(_source(area_id=3))

    assert store.list_sources()["a"].area_id == 3
    raw = db.query("SELECT config FROM sources WHERE name = 'a'").rows[0]["config"]
    assert json.loads(raw)["area_id"] == 3


def test_a_stored_row_without_the_key_loads_as_no_area() -> None:
    db = SqliteD1()
    db.query(
        "INSERT INTO sources (name, url, type, config) VALUES (?, ?, ?, ?)",
        ["a", "https://a.example/feed", "rss", json.dumps({"tags": ["x"]})],
    )

    assert D1SourceStore(db).list_sources()["a"].area_id is None


def test_sources_push_stores_the_yaml_area_and_none_when_absent() -> None:
    db = SqliteD1()
    store = D1SourceStore(db)

    with_area = SourcesConfig.model_validate(
        yaml.safe_load("sources:\n  - {name: a, url: 'https://a.example/f', area_id: 3}\n")
    )
    store.replace_all({s.name: s for s in with_area.sources})
    assert store.list_sources()["a"].area_id == 3

    without = SourcesConfig.model_validate(
        yaml.safe_load("sources:\n  - {name: a, url: 'https://a.example/f'}\n")
    )
    store.replace_all({s.name: s for s in without.sources})
    assert store.list_sources()["a"].area_id is None


def test_a_non_integer_area_id_in_yaml_fails_validation_naming_the_field() -> None:
    with pytest.raises(pydantic.ValidationError, match="area_id"):
        SourcesConfig.model_validate(
            yaml.safe_load("sources:\n  - {name: a, url: 'https://a.example/f', area_id: tech}\n")
        )
