"""The `staging` Preview runs the container production runs, through the binding the Worker reads.

Wrangler copies no container and no binding from the top level into a Preview
(https://developers.cloudflare.com/workers/previews/resources/#containers), so
`wrangler.toml` states both twice, and the copies drift with no diff to notice:
a Preview container without its regions can start in Hong Kong, where the LLM
providers refuse it, and one without its binding answers 1101.
"""

import tomllib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.guard]

ROOT = Path(__file__).resolve().parents[1]


def _config() -> dict:
    return tomllib.loads((ROOT / "wrangler.toml").read_text(encoding="utf-8"))


def test_the_preview_container_is_production_s_but_for_its_generated_name() -> None:
    config = _config()
    [production] = config["containers"]
    [preview] = config["previews"]["containers"]

    assert (ROOT / preview.pop("image")).resolve() == (ROOT / production.pop("image")).resolve()
    production.pop("name")
    assert preview == production


def test_the_preview_binds_the_container_the_worker_reads() -> None:
    config = _config()

    assert config["previews"]["durable_objects"] == config["durable_objects"]


def test_a_preview_names_no_worker_for_the_digest_link() -> None:
    """Named, a Preview run would look up this Worker's custom domains, which are production's."""
    assert "CYRIS_APP_WORKER_NAME" not in _config()["previews"].get("vars", {})


def test_the_tracked_preview_bakes_no_commit() -> None:
    """The build commit is rendered per deploy; a tracked one is wrong from the next commit on."""
    [preview] = _config()["previews"]["containers"]

    assert "image_vars" not in preview
