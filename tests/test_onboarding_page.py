"""/onboarding: who is sent to the wizard, its six steps, and what its three saves write.

The wizard adds no write path of its own: each settings step posts the body the
settings form posts, to the route `settings_fields.json` names for that field.
What a browser actually sends is checked by `scripts/onboarding_probe.py`; here
the shapes of those bodies are held to the settings page's own.
"""

import json
import re
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

import cyris.entrypoints
from cyris.config import SETTINGS_FIELDS
from cyris.domain.models import SourceConfig
from cyris.entrypoints.triage_server import TriageServer, render_onboarding_page

pytestmark = pytest.mark.unit

STATIC = Path(cyris.entrypoints.__file__).parent / "static"
ONBOARDING_JS = (STATIC / "onboarding.js").read_text()
SETTINGS_JS = (STATIC / "settings.js").read_text()

# The six steps, in the order the milestone sets: the three that write settings
# and the three the later tickets fill in.
STEPS = ["language", "schedule", "sources", "votes", "notify", "first-issue"]
COMING_NEXT = ["sources", "votes", "first-issue"]


class FakeSettings:
    """Stands in for `D1Settings`; records what the wizard decided to store."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def set(self, values: dict) -> None:
        self.calls.append(dict(values))


class FakeSourceStore:
    """Stands in for the D1 `sources` table, empty or not."""

    def __init__(self, sources: dict[str, SourceConfig]) -> None:
        self.sources = sources

    def list_sources(self) -> dict[str, SourceConfig]:
        return dict(self.sources)


A_SOURCE = SourceConfig(name="Feed", url="https://feed.test/rss")


def _server(*, table: dict | None, settings: FakeSettings | None = None) -> TriageServer:
    """A deployment whose `sources` table is `table`, or one with no table at all (`json`)."""
    return TriageServer(
        settings=settings,
        source_store=None if table is None else FakeSourceStore(table),
    )


@pytest.fixture
async def serve():
    clients: list[TestClient] = []

    async def start(server: TriageServer) -> TestClient:
        client = TestClient(TestServer(server._app))
        await client.start_server()
        clients.append(client)
        return client

    yield start
    for client in clients:
        await client.close()


class TestWhoSeesTheWizard:
    async def test_an_empty_source_table_sends_settings_to_the_wizard(self, serve) -> None:
        client = await serve(_server(table={}))

        response = await client.get("/settings", allow_redirects=False)

        assert response.status == 302
        assert response.headers["Location"] == "/onboarding"

    async def test_the_wizard_is_served_while_the_table_is_empty(self, serve) -> None:
        client = await serve(_server(table={}))

        response = await client.get("/onboarding")

        assert response.status == 200
        assert response.content_type == "text/html"
        assert 'id="o-steps"' in await response.text()

    async def test_the_wizards_own_exit_opens_settings(self, serve) -> None:
        """Walking the wizard leaves the table as empty as it was, so its exit is marked."""
        client = await serve(_server(table={}))

        response = await client.get("/settings?from=onboarding", allow_redirects=False)

        assert response.status == 200
        assert 'id="model-form"' in await response.text()

    async def test_a_deployment_with_a_source_still_opens_the_wizard(self, serve) -> None:
        """The sources step writes one, and a reload on a later step must not leave the wizard."""
        client = await serve(_server(table={"Feed": A_SOURCE}))

        response = await client.get("/onboarding", allow_redirects=False)

        assert response.status == 200
        assert 'id="o-steps"' in await response.text()

    async def test_a_deployment_with_a_source_keeps_its_settings_page(self, serve) -> None:
        client = await serve(_server(table={"Feed": A_SOURCE}))

        assert (await client.get("/settings")).status == 200

    @pytest.mark.parametrize("path", ["/onboarding", "/settings"])
    async def test_a_json_deployment_never_sees_the_wizard(self, serve, path: str) -> None:
        """No writable table is a `json` deployment: its sources live in a file nothing writes."""
        client = await serve(_server(table=None))

        response = await client.get(path, allow_redirects=False)

        assert (response.status, response.headers.get("Location")) == (
            (302, "/settings") if path == "/onboarding" else (200, None)
        )

    async def test_a_failed_source_read_still_serves_settings(self, serve) -> None:
        """/settings is where a reader fixes D1, so routing never depends on reading it."""

        class FailingSourceStore(FakeSourceStore):
            def list_sources(self) -> dict[str, SourceConfig]:
                raise RuntimeError("D1 timed out")

        client = await serve(TriageServer(settings=None, source_store=FailingSourceStore({})))

        response = await client.get("/settings", allow_redirects=False)

        assert response.status == 200
        assert 'id="model-form"' in await response.text()


class TestTheFrame:
    @pytest.fixture
    def page(self) -> str:
        return render_onboarding_page()

    def test_the_step_list_names_the_six_steps_in_order(self, page: str) -> None:
        listed = re.findall(r'<li class="label" data-step="([^"]+)">', page)

        assert listed == STEPS

    def test_every_listed_step_has_a_panel(self, page: str) -> None:
        panels = re.findall(r'<section class="step" data-step="([^"]+)"', page)

        assert panels == STEPS

    def test_the_script_walks_the_same_steps(self) -> None:
        listed = re.search(r"const STEPS = \[([^\]]+)\];", ONBOARDING_JS)
        assert listed, "no STEPS in onboarding.js"

        assert json.loads(f"[{listed[1]}]") == STEPS

    def test_the_steps_still_to_come_are_marked_as_placeholders(self, page: str) -> None:
        marked = re.findall(r'<section class="step" data-step="([^"]+)" data-coming-next', page)

        assert marked == COMING_NEXT

    @pytest.mark.parametrize("step", COMING_NEXT)
    def test_a_placeholder_step_says_so_and_says_what_to_do_meanwhile(
        self, page: str, step: str
    ) -> None:
        panel = re.search(
            rf'<section class="step" data-step="{step}"[^>]*>(.*?)</section>', page, re.S
        )
        assert panel, f"no {step} panel"

        assert "Coming next" in panel[1]

    def test_the_wizard_moves_with_back_skip_and_next(self, page: str) -> None:
        nav = re.search(r'<div class="step-nav">(.*?)</div>', page, re.S)
        assert nav, "no step navigation"
        pattern = r'<button class="btn [^"]*" type="button" id="([^"]+)">([^<]+)<'
        buttons = re.findall(pattern, nav[1])

        assert buttons == [("o-back", "Back"), ("o-skip", "Skip"), ("o-next", "Next")]

    def test_the_last_step_leaves_for_settings_with_the_exit_mark(self, page: str) -> None:
        nav = re.search(r'<div class="step-nav">(.*?)</div>', page, re.S)
        assert nav, "no step navigation"

        finish = '<a class="btn primary" id="o-finish" href="/settings?from=onboarding" hidden>'

        assert f"{finish}Finish</a>" in nav[1]

    def test_only_the_last_step_shows_finish(self) -> None:
        """The frame's own rule, read from the script: Next and Finish never show together."""
        assert '$("o-next").hidden = last;' in ONBOARDING_JS
        assert '$("o-finish").hidden = !last;' in ONBOARDING_JS

    def test_the_page_is_served_with_its_own_script(self, page: str) -> None:
        assert '<script src="/static/onboarding.js"></script>' in page


def _body_keys(script: str) -> dict[str, list[set[str]]]:
    """The top-level keys of every body `script` POSTs, by the route it posts them to."""
    bodies: dict[str, list[set[str]]] = {}
    for match in re.finditer(r'post\(\s*"(/api/settings[^"]*)",\s*\{', script):
        route = match[1]
        depth, start = 1, match.end()
        for end in range(start, len(script)):
            depth += {"{": 1, "}": -1}.get(script[end], 0)
            if depth == 0:
                break
        fields, depth = [""], 0
        for char in script[start:end]:
            depth += {"{": 1, "}": -1, "(": 1, ")": -1, "[": 1, "]": -1}.get(char, 0)
            if char == "," and depth == 0:
                fields.append("")
            else:
                fields[-1] += char
        bodies.setdefault(route, []).append(
            {field.split(":")[0].strip() for field in fields if field.strip()}
        )
    return bodies


# Each field the wizard writes, by the key it writes it under, and the route
# `settings_fields.json` gives that key.
WRITTEN = {
    "digest.output_language": "plain",
    "general.timezone": "plain",
    "general.digest_schedule": "schedule",
    "notify.discord_webhook_url": "notify",
    "notify.email_to": "email",
    "notify.email_from": "email",
}


class TestItWritesWhatTheSettingsFormWrites:
    def test_the_body_reader_finds_a_bodys_keys(self) -> None:
        script = 'post("/api/settings/email", {email_to: f(a, b), email_from: x["y"]})'

        assert _body_keys(script) == {"/api/settings/email": [{"email_to", "email_from"}]}

    def test_the_wizard_posts_to_no_route_the_settings_page_does_not(self) -> None:
        assert set(_body_keys(ONBOARDING_JS)) <= set(_body_keys(SETTINGS_JS))

    def test_the_wizard_posts_only_the_four_settings_routes(self) -> None:
        assert set(_body_keys(ONBOARDING_JS)) == {
            "/api/settings/values",
            "/api/settings/schedule",
            "/api/settings/notify",
            "/api/settings/email",
        }

    @pytest.mark.parametrize("route", sorted(_body_keys(ONBOARDING_JS)))
    def test_each_body_is_one_the_settings_page_sends_too(self, route: str) -> None:
        """Same route, same field names: the store cannot tell the two pages apart."""
        wizard = _body_keys(ONBOARDING_JS)[route]
        form = _body_keys(SETTINGS_JS)[route]

        assert [keys for keys in wizard if keys not in form] == []

    @pytest.mark.parametrize("key", sorted(WRITTEN))
    def test_each_written_key_is_saved_by_its_registry_route(self, key: str) -> None:
        assert SETTINGS_FIELDS[key]["route"] == WRITTEN[key]

    def test_the_hours_are_padded_as_the_settings_form_pads_them(self) -> None:
        padded = 'padStart(2, "0")}:00'

        assert padded in ONBOARDING_JS and padded in SETTINGS_JS

    def test_skipping_a_step_sends_nothing(self) -> None:
        handler = re.search(
            r'\$\("o-skip"\)\.addEventListener\("click", \(\) => \{(.*?)\n\}\);',
            ONBOARDING_JS,
            re.S,
        )
        assert handler, "no Skip handler"

        assert "post(" not in handler[1] and "fetch(" not in handler[1]


class TestTheStepsWriteThroughTheExistingApi:
    """The bodies the wizard sends, stored by the real routes into a fake settings home."""

    async def test_the_language_step_stores_the_language_and_the_timezone(self, serve) -> None:
        settings = FakeSettings()
        client = await serve(_server(table={}, settings=settings))

        response = await client.post(
            "/api/settings/values",
            json={"values": {"digest.output_language": "en", "general.timezone": "Asia/Taipei"}},
        )

        assert response.status == 200
        assert settings.calls == [
            {"digest.output_language": "en", "general.timezone": "Asia/Taipei"}
        ]

    async def test_the_hours_step_stores_the_schedule(self, serve) -> None:
        settings = FakeSettings()
        client = await serve(_server(table={}, settings=settings))

        response = await client.post("/api/settings/schedule", json={"times": ["07:00", "19:00"]})

        assert response.status == 200
        assert settings.calls == [{"general.digest_schedule": ["07:00", "19:00"]}]

    async def test_a_step_stores_nothing_when_the_body_is_refused(self, serve) -> None:
        settings = FakeSettings()
        client = await serve(_server(table={}, settings=settings))

        response = await client.post(
            "/api/settings/values", json={"values": {"general.timezone": "Mars/Base"}}
        )

        assert response.status == 400
        assert (await response.json())["field"] == "general.timezone"
        assert settings.calls == []
