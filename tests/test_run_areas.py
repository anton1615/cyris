"""A run offers the active areas to scoring and records unplaced suggestions, never failing."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes import FakeLLM, SqliteD1, settings_toml
from test_run_digest import FakeSource, make_deps

from cyris.adapters.store.areas import D1AreaStore
from cyris.domain.areas import Area
from cyris.domain.models import Article, Tier
from cyris.service_layer.run_digest import RunOptions, run_digest

pytestmark = pytest.mark.integration


def _active(area_id: int, name: str) -> Area:
    return Area(
        id=area_id,
        name=name,
        position=area_id - 1,
        status="active",
        merged_into=None,
        proposal_articles=0,
        proposal_issues=0,
        last_proposed_run=None,
    )


def _articles() -> list[Article]:
    return [
        Article(
            id=i,
            title=f"Orbit story {i}",
            url=f"https://example.com/{i}",
            content="A launch and a satellite.",
            published_at=datetime.now(UTC) - timedelta(hours=1),
            source_name="TechSource",
            source_tier=Tier.SUMMARIZE,
            source_tags=["tech"],
        )
        for i in (1, 2, 3)
    ]


def _scoring_reply(suggest: str | None) -> str:
    entries = [
        {
            "id": i,
            "score": 80,
            "language": "en",
            "tags": ["space"],
            **({"suggest_area": suggest} if suggest else {}),
        }
        for i in (1, 2, 3)
    ]
    return json.dumps({"scores": entries})


SUMMARY_REPLY = json.dumps(
    {
        "sections": [
            {
                "heading": "Space",
                "summary": "Launches.",
                "articles": [{"id": "0", "title": "Orbit story 1", "source": "TechSource"}],
            }
        ]
    }
)


class _SpyStore(D1AreaStore):
    def __init__(self, db: SqliteD1, *, fail: bool = False) -> None:
        super().__init__(db)
        self.calls: list[tuple[dict, str | None]] = []
        self._fail = fail

    def record_proposals(self, counts, run_id):
        self.calls.append((dict(counts), run_id))
        if self._fail:
            raise RuntimeError("D1 unavailable")
        super().record_proposals(counts, run_id)


async def _run(tmp_path: Path, suggest: str | None, store: _SpyStore, *, dry_run: bool = False):
    llm = FakeLLM([_scoring_reply(suggest), SUMMARY_REPLY])
    deps, _ = make_deps(tmp_path, llm, FakeSource(_articles()))
    deps.cfg.areas = [_active(1, "Tech")]
    summaries: list[dict] = []
    deps = replace(deps, area_store=store, record_run=summaries.append)
    report = await run_digest(deps, RunOptions(dry_run=dry_run))
    return report, llm, summaries


def _proposed(db: SqliteD1, name: str) -> dict | None:
    rows = db.query(
        "SELECT status, proposal_articles, proposal_issues FROM areas WHERE name = ?", [name]
    ).rows
    return dict(rows[0]) if rows else None


async def test_a_suggested_name_is_proposed_with_its_article_count(tmp_path: Path) -> None:
    db = SqliteD1()
    report, llm, _ = await _run(tmp_path, "Space", _SpyStore(db))

    assert report.status == "ok"
    assert _proposed(db, "Space") == {
        "status": "proposed",
        "proposal_articles": 3,
        "proposal_issues": 1,
    }
    assert "Tech" in llm.calls[0]["system"]


async def test_a_dry_run_records_nothing(tmp_path: Path) -> None:
    db = SqliteD1()
    store = _SpyStore(db)

    await _run(tmp_path, "Space", store, dry_run=True)

    assert store.calls == []
    assert _proposed(db, "Space") is None


async def test_a_failed_record_leaves_the_run_and_names_the_error(tmp_path: Path) -> None:
    db = SqliteD1()
    report, _, summaries = await _run(tmp_path, "Space", _SpyStore(db, fail=True))

    assert report.status == "ok"
    assert report.html_path is not None and report.html_path.exists()
    assert summaries[-1]["area_proposal_error"] == "RuntimeError"


async def test_a_run_with_no_suggestion_makes_no_store_call(tmp_path: Path) -> None:
    db = SqliteD1()
    store = _SpyStore(db)

    await _run(tmp_path, None, store)

    assert store.calls == []
    assert _proposed(db, "Space") is None


def test_an_articles_score_suggestion_is_proposed_with_no_issue(
    tmp_path: Path, monkeypatch
) -> None:
    from typer.testing import CliRunner

    from cyris.adapters.store import ArticleStore
    from cyris.domain.models import Article
    from cyris.entrypoints.cli import app

    vault = tmp_path / "agent-vault"
    vault.mkdir()
    config_path = tmp_path / "cyris.toml"
    config_path.write_text(
        settings_toml() + f'\n[agent_vault]\npath = "{vault}"\n', encoding="utf-8"
    )
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text("sources: []", encoding="utf-8")
    store = ArticleStore(vault)
    now = datetime.now(UTC)
    store.save(
        [
            Article(
                id=1,
                title="Launch",
                url="https://example.com/launch",
                content="A rocket.",
                published_at=now,
                source_name="Src",
                source_tier=Tier.SUMMARIZE,
            )
        ],
        now=now,
    )
    db = SqliteD1()
    reply = json.dumps(
        {"scores": [{"id": 1, "score": 60, "language": "en", "suggest_area": "Space"}]}
    )
    monkeypatch.setattr("cyris.bootstrap.build_d1_client", lambda cfg: db)
    monkeypatch.setattr("cyris.bootstrap.build_store", lambda cfg, d1=None: store)
    monkeypatch.setattr("cyris.bootstrap.build_llm", lambda llm_cfg: FakeLLM(reply))

    result = CliRunner().invoke(
        app, ["articles", "score", "--config", str(config_path), "--sources", str(sources_path)]
    )

    assert result.exit_code == 0, result.output
    assert _proposed(db, "Space") == {
        "status": "proposed",
        "proposal_articles": 1,
        "proposal_issues": 0,
    }
