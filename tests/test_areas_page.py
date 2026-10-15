"""/settings has an Areas category: its nav link, its panel, and the route that opens it."""

import re
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

import cyris.entrypoints
from cyris.entrypoints.triage_server import TriageServer

pytestmark = pytest.mark.unit

STATIC = Path(cyris.entrypoints.__file__).parent / "static"


@pytest.fixture
async def page() -> str:
    client = TestClient(TestServer(TriageServer()._app))
    await client.start_server()
    try:
        response = await client.get("/settings")
        assert response.status == 200
        return await response.text()
    finally:
        await client.close()


def test_the_category_list_links_the_areas_hash(page: str) -> None:
    assert re.search(r'<a [^>]*href="#areas"[^>]*data-tab="areas"', page)


def test_the_areas_category_has_a_panel_with_one_heading(page: str) -> None:
    panel = re.search(r'<div class="tab" data-tab="areas"[^>]*>(.*?)<div class="tab"', page, re.S)
    assert panel, "no Areas panel"
    assert re.findall(r'<h2 class="heading">([^<]+)</h2>', panel.group(1)) == ["Areas"]


def test_the_script_routes_the_areas_hash() -> None:
    script = (STATIC / "settings.js").read_text()
    assert re.search(r'const TABS = \[[^\]]*"areas"[^\]]*\];', script)


def test_the_areas_doc_names_the_not_active_option() -> None:
    doc = (Path(__file__).parents[1] / "docs/design/ui-language.md").read_text()
    row = next(line for line in doc.splitlines() if line.startswith("| `#areas`"))
    assert row.count("`Area N (not active)`") == 1
