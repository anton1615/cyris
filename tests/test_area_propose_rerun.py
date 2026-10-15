"""The Propose rerun script: what it prints, what it writes, and what it leaks."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fakes import FakeLLM

from cyris.config import LLMProviderConfig
from cyris.domain.areas import Area
from cyris.domain.models import SourceConfig

pytestmark = pytest.mark.integration

_SCRIPTS = str(Path(__file__).parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
propose_rerun = importlib.import_module("propose_rerun")

SOURCES = {
    "A": SourceConfig(name="A", url="https://a.example/feed", tags=["ai"]),
    "B": SourceConfig(name="B", url="https://b.example/feed", tags=[]),
}
GROUPING = '{"Tech": ["A"]}'
SECRET_KEY = "sk-abc"
SECRET_TOKEN = "tok-123"


def fixed_clock():
    ticks = iter([10.0, 12.5])
    return lambda: next(ticks)


class ReadOnlyAreas:
    """An area store that answers its reads and raises on any write."""

    def __init__(self, areas):
        self._areas = areas

    def list_areas(self):
        return self._areas

    def __getattr__(self, name):
        raise AssertionError(f"a dry run must not call {name}")


def wire(monkeypatch, tmp_path, llm, store=None):
    """Point the script's loaders at in-memory stand-ins, with one real config file."""
    config = tmp_path / "cyris.toml"
    config.write_text("")
    cfg = SimpleNamespace(
        sources=SOURCES,
        app=SimpleNamespace(
            llm_provider=LLMProviderConfig(provider="anthropic", model="m", api_key=SECRET_KEY)
        ),
    )
    areas = [
        Area(
            id=1,
            name="Tech",
            position=0,
            status="active",
            merged_into=None,
            proposal_articles=0,
            proposal_issues=0,
            last_proposed_run=None,
        )
    ]
    monkeypatch.setattr(propose_rerun, "load_effective_config", lambda *_: cfg)
    monkeypatch.setattr(propose_rerun, "build_d1_client", lambda _cfg: "d1")
    monkeypatch.setattr(propose_rerun, "build_llm", lambda _provider: llm)
    monkeypatch.setattr(
        propose_rerun,
        "D1AreaStore",
        lambda _d1: store or ReadOnlyAreas(areas),
    )
    monkeypatch.setattr(
        propose_rerun,
        "D1SourceTitles",
        lambda _d1: SimpleNamespace(recent=lambda _now: {"A": ["Headline"]}),
    )
    return config


async def test_the_rerun_counts_what_the_model_was_sent_and_returned() -> None:
    llm = FakeLLM(GROUPING)
    titles = {"A": ["Headline"]}

    report = await propose_rerun.rerun(llm, SOURCES, titles, ["Tech"], fixed_clock())

    prompt = llm.calls[0]["prompt"]
    assert report == propose_rerun.Rerun(
        sources=2, with_titles=1, prompt_chars=len(prompt), groups=1, elapsed=2.5
    )


def test_the_run_prints_six_labelled_lines_and_writes_nothing(monkeypatch, tmp_path, capsys):
    llm = FakeLLM(GROUPING)
    config = wire(monkeypatch, tmp_path, llm)

    code = propose_rerun.main(["--config", str(config)], clock=fixed_clock())

    out = capsys.readouterr()
    prompt = llm.calls[0]["prompt"]
    assert code == 0
    assert out.out.splitlines() == [
        f"config: {config.resolve()}",
        "sources: 2",
        "sources with titles: 1",
        f"prompt chars: {len(prompt)}",
        "groups: 1",
        "elapsed seconds: 2.500",
    ]
    assert "Existing areas" in prompt
    assert "- Tech" in prompt


def test_a_store_that_raises_on_any_write_is_only_read(monkeypatch, tmp_path):
    llm = FakeLLM(GROUPING)
    config = wire(monkeypatch, tmp_path, llm)

    code = propose_rerun.main(["--config", str(config)], clock=fixed_clock())

    assert code == 0
    assert "- Tech" in llm.calls[0]["prompt"]


def test_no_secret_reaches_stdout_or_stderr_on_success(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("D1_API_TOKEN", SECRET_TOKEN)
    config = wire(monkeypatch, tmp_path, FakeLLM(GROUPING))

    propose_rerun.main(["--config", str(config)], clock=fixed_clock())

    out = capsys.readouterr()
    for text in (out.out, out.err):
        assert SECRET_KEY not in text
        assert SECRET_TOKEN not in text


def test_a_model_failure_exits_one_with_the_class_name_and_no_secret(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("D1_API_TOKEN", SECRET_TOKEN)
    failing = FakeLLM(error=RuntimeError(f"rejected {SECRET_KEY}"))
    config = wire(monkeypatch, tmp_path, failing)

    code = propose_rerun.main(["--config", str(config)], clock=fixed_clock())

    out = capsys.readouterr()
    assert code == 1
    assert out.err.strip() == "rerun failed: RuntimeError"
    assert SECRET_KEY not in out.out + out.err


def test_a_relative_config_prints_its_resolved_absolute_path(monkeypatch, tmp_path, capsys):
    config = wire(monkeypatch, tmp_path, FakeLLM(GROUPING))
    monkeypatch.chdir(tmp_path)

    code = propose_rerun.main(["--config", config.name], clock=fixed_clock())

    assert code == 0
    assert capsys.readouterr().out.splitlines()[0] == f"config: {config.resolve()}"


def test_a_missing_config_exits_two_with_one_line_and_no_traceback(monkeypatch, tmp_path, capsys):
    wire(monkeypatch, tmp_path, FakeLLM(GROUPING))
    missing = tmp_path / "nope.toml"

    code = propose_rerun.main(["--config", str(missing)], clock=fixed_clock())

    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert out.err.splitlines() == [f"cannot read --config {missing}"]
