"""`cyris run --date`: a manual run names its issue's date; the tick never does."""

from pathlib import Path

import pytest
from fakes import make_config
from typer.testing import CliRunner

from cyris import bootstrap
from cyris.config import SourceConfig
from cyris.entrypoints.cli import app
from cyris.service_layer import run_digest as run_digest_module
from cyris.service_layer.run_digest import RunReport

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list:
    """The options each run was started with; nothing is fetched or built."""
    options: list = []
    cfg = make_config(sources={"feedA": SourceConfig(name="feedA", url="x")})
    monkeypatch.setattr(bootstrap, "load_effective_config", lambda *_: cfg)
    monkeypatch.setattr(bootstrap, "build_deps", lambda *a, **k: object())

    async def record(deps, opts) -> RunReport:
        options.append(opts)
        return RunReport(status="ok")

    monkeypatch.setattr(run_digest_module, "run_digest", record)
    return options


def _run(tmp_path: Path, *extra: str):
    args = ["run", "--config", str(tmp_path / "c.toml"), "--sources", str(tmp_path / "s.yaml")]
    return runner.invoke(app, [*args, *extra])


def test_a_named_date_reaches_the_run(tmp_path: Path, started: list) -> None:
    result = _run(tmp_path, "--period", "evening", "--date", "2026-10-19")

    assert result.exit_code == 0, result.output
    [options] = started
    assert (options.date, options.period) == ("2026-10-19", "evening")


def test_without_a_date_the_run_dates_its_issue_itself(tmp_path: Path, started: list) -> None:
    result = _run(tmp_path, "--period", "morning")

    assert result.exit_code == 0, result.output
    assert started[0].date is None


def test_a_date_with_if_due_is_refused_before_anything_runs(tmp_path: Path, started: list) -> None:
    result = _run(tmp_path, "--if-due", "--date", "2026-10-19")

    assert result.exit_code == 2
    assert "--date cannot be combined with --if-due" in result.output
    assert started == []


@pytest.mark.parametrize("date", ["2026-10-32", "2026-1-9", "yesterday", "2026-10-19T00:00"])
def test_a_date_that_is_not_a_real_yyyy_mm_dd_is_refused(
    tmp_path: Path, started: list, date: str
) -> None:
    result = _run(tmp_path, "--period", "evening", "--date", date)

    assert result.exit_code == 2
    assert "--date must be a real YYYY-MM-DD date" in result.output
    assert started == []
