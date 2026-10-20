#!/usr/bin/env python3
"""Drive /onboarding in headless Chromium and check what a reader actually gets.

pytest holds the wizard's markup, its redirects and the shape of the bodies it
sends, but not what its script does in a browser: which step a press opens, what
a Next on a changed step writes, that a Skip writes nothing, and whether the page
fits a phone. This runs those checks against a real `TriageServer` whose settings
home and `sources` table are in memory, so what a step wrote can be read back
afterwards, with the Discord probe and the mail send answered offline.

Every check carries a sabotage that breaks the thing it reads. `--self-test`
runs each check clean, where it must pass, and sabotaged, where it must fail,
so a check that cannot fail is reported rather than trusted.

The browser half, which knows no page, is `cdp_probe.py`, shared with the
settings, raw, digest and labels probes.

This script must never be collected by pytest: it needs Chromium and Node,
which makes it a reviewer-run gate rather than a test. Importing it is safe,
and `tests/test_css_receipt.py` does so to check its fixtures and registry.
"""

import argparse
import asyncio
import contextlib
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest import mock

from aiohttp import web
from cdp_probe import (
    NO_SITE_BAR_AREAS,
    Check,
    at_largest_type_scale,
    base_prelude,
    chromium,
    largest_twin,
    require_node,
    restyle,
    run_all,
)
from css_computed import find_browser

from cyris.config import GRADE_D_KEYS
from cyris.diagnostics.doctor import Check as DoctorCheck
from cyris.domain.models import SourceConfig
from cyris.entrypoints.triage_server import TriageServer, load_onboarding_feeds

PAGE = "/onboarding"
SETTINGS = "/settings"

KINDS = ("empty", "empty-largest", "refuses-one", "has-sources", "json")

# What the reader types, and what each step must then store.
NEW_LANGUAGE = "en"
NEW_TIMEZONE = "Europe/Lisbon"
NEW_WEBHOOK = "https://discord.com/api/webhooks/9/NEWTOKEN"
NEW_MAIL_TO = "me@example.org"
NEW_MAIL_FROM = "digest@example.org"
UNKNOWN_TIMEZONE = "Mars/Base"
OFFLINE_DETAIL = "cyris-probe: answered offline"

# A deployment whose settings are all stored and whose notifications are not set
# yet: what the wizard opens on after a first boot. A key the registry adds
# without a value here fails at import.
_VALUES = {
    "general.digest_schedule": ["08:00", "20:00"],
    "general.timezone": "Asia/Taipei",
    "general.digest_window_hours": 24,
    "llm_provider.provider": "gemini",
    "llm_provider.model": "",
    "llm_provider.reasoning_effort": "default",
    "notify.discord_webhook_url": "",
    "notify.email_to": "",
    "notify.email_from": "",
    "digest.max_articles_per_digest": 200,
    "digest.max_articles_per_digest_output": 15,
    "digest.max_featured": 5,
    "digest.scoring_snippet_length": 1000,
    "digest.summarize_snippet_length": 1000,
    "digest.filter_snippet_length": 500,
    "digest.output_language": "zh-Hant",
    "digest.style_prompt": "",
    "digest.type_scale": 1,
    "digest.rank_by_preference": False,
    "digest.preference_source": "cosine",
    "routing.score_threshold": 70,
    "routing.summarize_score_threshold": 70,
    "vote_similarity.enabled": False,
    "vote_similarity.provider": "workers_ai",
    "vote_similarity.model": "",
    "vote_similarity.max_seeds": 200,
}
PROBE_VALUES = {key: _VALUES[key] for key in GRADE_D_KEYS}

A_SOURCE = SourceConfig(name="Simon Willison", url="https://simonwillison.net/atom/everything/")

# The feed list the server offers, and the two the reader picks: each carries two
# tags, so a body that lost one, or its order, is told from the list's own.
FEEDS = load_onboarding_feeds()
PICKS = [entry for entry in FEEDS if len(entry["tags"]) > 1][:2]
# The feed the `refuses-one` table turns down once, and the one it takes beside it.
REFUSED, TAKEN = FEEDS[1], FEEDS[0]
REFUSAL = "D1 is busy; try again"
MISNAMED = f"Added 1 of 2 feeds. {TAKEN['name']}: {REFUSAL}"


class FakeSettings:
    """Stands in for `D1Settings` and records every write a step makes."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def set(self, values: dict) -> None:
        self.calls.append(dict(values))


class FakeSourceStore:
    """Stands in for the D1 `sources` table, empty or not, and records every row it stored.

    A name in `refuse_once` is turned down the first time it is written, as a
    busy D1 would, and taken the next.
    """

    def __init__(self, sources: dict[str, SourceConfig], refuse_once: set[str] = frozenset()):
        self.sources = sources
        self.refuse = set(refuse_once)
        self.writes: list[SourceConfig] = []

    def list_sources(self) -> dict[str, SourceConfig]:
        return dict(self.sources)

    def upsert(self, source: SourceConfig) -> None:
        if source.name in self.refuse:
            self.refuse.discard(source.name)
            raise RuntimeError(REFUSAL)
        self.writes.append(source)
        self.sources[source.name] = source


@dataclass
class Fixture:
    """One server and the in-memory homes a check reads its receipt from."""

    app: web.Application
    settings: FakeSettings | None
    calls: list[dict] = field(default_factory=list)
    # Every source the table stored, in order; None on a `json` deployment.
    sources: FakeSourceStore | None = None


def build_fixture(kind: str) -> Fixture:
    """A deployment the wizard is for, or one of the two it is not.

    `empty` has a writable, empty source table, which is what waiting for
    onboarding is; `empty-largest` is that page at the largest type size, and
    `refuses-one` is that table turning `REFUSED` down once. `has-sources` has
    one source, so the wizard opens without the feed list, and `json` has no
    writable table at all, which is a `backend = "json"` deployment: it has no
    wizard.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown fixture {kind!r}")
    settings = FakeSettings()
    table = {"Simon Willison": A_SOURCE} if kind == "has-sources" else {}
    refuse = {REFUSED["name"]} if kind == "refuses-one" else set()
    store = None if kind == "json" else FakeSourceStore(table, refuse)
    server = TriageServer(settings=settings, values=PROBE_VALUES, source_store=store)
    if kind == "empty-largest":
        server._onboarding_page = at_largest_type_scale(server._onboarding_page)

    # The app Worker's own route, which the container does not serve: without it
    # the site bar's probe fails closed and its links stay hidden.
    async def signed_in(_: web.Request) -> web.Response:
        return web.json_response({"authorized": True})

    server._app.router.add_get("/api/vote", signed_in)
    return Fixture(server._app, settings, settings.calls, store)


@contextlib.contextmanager
def probe_environment() -> Iterator[SimpleNamespace]:
    """Answer the Discord probe and the mail send offline, from an empty directory.

    Both routes reach a third party on a real save, and the mail route also
    needs this deployment's Cloudflare credentials to get that far. Yields the
    patched edges so a caller can see they were the ones answering.
    """
    offline = DoctorCheck(name="probe", status="ok", detail=OFFLINE_DETAIL)
    discord = mock.AsyncMock(return_value=offline)
    mail = mock.AsyncMock(return_value="delivered")
    present = {
        "CLOUDFLARE_ACCOUNT_ID": "cyris-probe-account",
        "CLOUDFLARE_API_TOKEN": "cyris-probe-token",
    }
    with (
        tempfile.TemporaryDirectory(prefix="onboarding-probe-") as home,
        contextlib.chdir(home),
        mock.patch.dict(os.environ, present),
        mock.patch("cyris.entrypoints.triage_server.probe_discord", discord),
        mock.patch("cyris.adapters.mail.send_mail", mail),
    ):
        yield SimpleNamespace(discord=discord, mail=mail)


def rewrite_post(path: str, mutation: str) -> str:
    """A preload that changes the body of the page's POST to `path` before it leaves."""
    return f"""
{{
const realFetch = window.fetch;
window.fetch = (input, init) => {{
  if (String(input).endsWith({json.dumps(path)}) && init && init.method === "POST") {{
    const body = JSON.parse(init.body);
    {mutation}
    init = {{...init, body: JSON.stringify(body)}};
  }}
  return realFetch(input, init);
}};
}}
"""


# A sabotage: Skip presses Next, so a step the reader skipped is saved after all.
SKIP_SAVES = """{
document.addEventListener("DOMContentLoaded", () => {
  const skip = document.getElementById("o-skip");
  skip.addEventListener("click", (e) => e.stopImmediatePropagation(), true);
  skip.addEventListener("click", () => document.getElementById("o-next").click(), true);
});
}"""


def dead(button: str) -> str:
    """A sabotage: the button is swapped for a copy, which carries none of its listeners."""
    return f"""$("#{button}").replaceWith($("#{button}").cloneNode(true));"""


PRELUDE = (
    base_prelude()
    + """
const setValue = (el, value) => {
  el.value = value;
  el.dispatchEvent(new Event("input", {bubbles: true}));
  el.dispatchEvent(new Event("change", {bubbles: true}));
};
const shownSteps = () => $$(".step").filter(visible).map((panel) => panel.dataset.step);
const marked = () => $$('#o-steps li[aria-current="step"]').map((item) => item.dataset.step);
const progress = () => $("#o-progress").textContent;
const notice = () => $("#o-result");
// A notice showing a save's result, not what the save is still doing.
const settled = () => visible(notice()) && notice().getAttribute("aria-busy") !== "true";
const onStep = (step) => waitFor(() => same(shownSteps(), [step]), `the ${step} step`);
const press = (id) => $(`#${id}`).click();
// The stored settings arrived: the step's own values are in its fields.
const loaded = () => waitFor(() => $("#o-timezone").value && !$("[data-loading]"), "the settings");
const skipTo = async (step) => {
  for (let left = 6; left > 0 && shownSteps()[0] !== step; left--) {
    press("o-skip");
    await sleep(25);
  }
  return onStep(step);
};
const marks = () => $$('.step:not([hidden]) [aria-invalid="true"]').map((el) => el.id);
// The sources step, drawn: its list, or the line saying the table has sources.
const feedRows = () => $$("#o-feeds .choice");
const feedsDrawn = () => waitFor(
  () => feedRows().length || visible($("#o-feeds-had")), "the feed list");
const rowNamed = (name) => feedRows().find((row) => $(".name", row).textContent === name);
const pick = (name) => { rowNamed(name).querySelector("input").click(); };
"""
)


def _calls(*wanted: dict) -> Callable[[Fixture], str | None]:
    """A receipt: the settings home took exactly these writes, in order."""
    return lambda fixture: None if fixture.calls == list(wanted) else f"writes: {fixture.calls}"


def _sources(*wanted: dict) -> Callable[[Fixture], str | None]:
    """A receipt: the table stored exactly these list entries, field for field, in order.

    Each stored row is read back in the entry's own fields, so a tag or a tier
    the page changed shows, and with no area, which the list cannot know.
    """

    def receipt(fixture: Fixture) -> str | None:
        rows = fixture.sources.writes
        if len(rows) == len(wanted) and fixture.calls == []:
            pairs = zip(rows, wanted, strict=True)
            if all(
                row.model_dump(mode="json", include=set(entry)) == entry
                and row.area_id is None
                and row.type == "rss"
                for row, entry in pairs
            ):
                return None
        dumped = [row.model_dump(mode="json") for row in rows]
        return f"sources: {dumped}, settings: {fixture.calls}"

    return receipt


def _both_untouched(fixture: Fixture) -> str | None:
    """A receipt: neither the settings home nor the table took a write."""
    return _sources()(fixture)


def _pick(*entries: dict) -> str:
    return "\n".join(f"pick({json.dumps(entry['name'])});" for entry in entries)


# Typing into one step, by the step it belongs to.
TYPE_LANGUAGE = f"""
setValue($("#o-output-language"), {json.dumps(NEW_LANGUAGE)});
setValue($("#o-timezone"), {json.dumps(NEW_TIMEZONE)});
"""
TYPE_HOURS = """
setValue($("#o-morning"), "7");
setValue($("#o-evening"), "19");
"""
TYPE_WEBHOOK = f"""setValue($("#o-discord-webhook"), {json.dumps(NEW_WEBHOOK)});"""
TYPE_MAIL = f"""
setValue($("#o-email-to"), {json.dumps(NEW_MAIL_TO)});
setValue($("#o-email-from"), {json.dumps(NEW_MAIL_FROM)});
"""

LANGUAGE_STORED = {"digest.output_language": NEW_LANGUAGE, "general.timezone": NEW_TIMEZONE}
HOURS_STORED = {"general.digest_schedule": ["07:00", "19:00"]}
WEBHOOK_STORED = {"notify.discord_webhook_url": NEW_WEBHOOK}
MAIL_STORED = {"notify.email_to": NEW_MAIL_TO, "notify.email_from": NEW_MAIL_FROM}

_CHECKS: list[Check] = [
    Check(
        id="opens-on-the-first-step",
        fixture="empty",
        path=PAGE,
        act="await loaded();",
        script="""
            expect(same(shownSteps(), ["language"]), `shown: ${shownSteps()}`);
            expect(same(marked(), ["language"]), `marked: ${marked()}`);
            expect(progress() === "Step 1 of 6", `progress: ${progress()}`);
            expect(!visible($("#o-back")), "Back shows on the first step");
            expect(visible($("#o-next")) && visible($("#o-skip")), "Next or Skip is hidden");
            expect(!visible($("#o-finish")), "Finish shows before the last step");
        """,
        sabotage="""$('.step[data-step="schedule"]').hidden = false;""",
    ),
    Check(
        id="the-step-list-names-every-step",
        fixture="empty",
        path=PAGE,
        act="await loaded();",
        script="""
            const names = $$("#o-steps li").filter(visible).map((li) => li.dataset.step);
            const wanted = ["language", "schedule", "sources", "votes", "notify", "first-issue"];
            expect(same(names, wanted), `listed: ${names}`);
            const words = $$("#o-steps li").map((li) => li.textContent.trim());
            expect(words.every(Boolean), `an unnamed step: ${words.join("|")}`);
        """,
        sabotage="""$('#o-steps li[data-step="sources"]').hidden = true;""",
    ),
    Check(
        id="a-step-still-to-come-says-so",
        fixture="empty",
        path=PAGE,
        act="""
            await loaded();
            await skipTo("votes");
        """,
        script="""
            const text = $('.step[data-step="votes"]').textContent;
            expect(text.includes("Coming next"), `the votes step says: ${text}`);
            expect(!$$(".step:not([hidden]) input").length, "the placeholder has a field");
        """,
        sabotage="""$('.step[data-step="votes"] .small').textContent = "Vote on a few cards";""",
        receipt=_both_untouched,
    ),
    Check(
        id="sources-step-lists-every-entry",
        fixture="empty",
        path=PAGE,
        act="""
            await loaded();
            await skipTo("sources");
            await feedsDrawn();
        """,
        script=f"""
            const names = feedRows().filter(visible).map((row) => $(".name", row).textContent);
            const wanted = {json.dumps([entry["name"] for entry in FEEDS], ensure_ascii=False)};
            expect(same(names, wanted), `listed: ${{names.join("|")}}`);
            const picked = feedRows().filter((row) => $("input", row).checked).length;
            expect(picked === 0, `${{picked}} feeds start picked`);
            expect(!visible($("#o-feeds-had")), "the has-sources line shows over the list");
        """,
        sabotage="""feedRows()[3].remove();""",
        receipt=_both_untouched,
    ),
    Check(
        id="sources-step-stores-the-picked-entries",
        fixture="empty",
        path=f"{PAGE}#sources",
        act=f"""
            await loaded();
            await feedsDrawn();
            {_pick(*PICKS)}
            press("o-next");
            await onStep("votes");
            await waitFor(settled, "the result notice");
        """,
        script=f"""
            const text = notice().textContent;
            expect(!notice().classList.contains("err"), `an error: ${{text}}`);
            const added = {
            json.dumps("Added 2 feeds: " + ", ".join(p["name"] for p in PICKS), ensure_ascii=False)
        };
            expect(text.includes(added), `notice: ${{text}}`);
        """,
        sabotage_preload=rewrite_post("/api/sources", "body.tags = body.tags.slice(1);"),
        receipt=_sources(*PICKS),
    ),
    Check(
        id="sources-step-stores-the-listed-tier",
        fixture="empty",
        path=f"{PAGE}#sources",
        act=f"""
            await loaded();
            await feedsDrawn();
            {_pick(PICKS[0])}
            press("o-next");
            await onStep("votes");
        """,
        script="""await waitFor(settled, "the result notice");""",
        sabotage_preload=rewrite_post("/api/sources", 'body.tier = "summarize";'),
        receipt=_sources(PICKS[0]),
    ),
    Check(
        id="sources-step-skipped-writes-no-source",
        fixture="empty",
        path=f"{PAGE}#sources",
        act=f"""
            await loaded();
            await feedsDrawn();
            {_pick(*PICKS)}
            press("o-skip");
            await onStep("votes");
        """,
        script="""
            expect(same(marked(), ["votes"]), `marked: ${marked()}`);
            expect(!visible(notice()), `a notice: ${notice().textContent}`);
        """,
        sabotage_preload=SKIP_SAVES,
        receipt=_both_untouched,
    ),
    Check(
        id="sources-step-with-nothing-picked-writes-nothing",
        fixture="empty",
        path=f"{PAGE}#sources",
        act="""
            await loaded();
            await feedsDrawn();
        """,
        # After the act, so the pick is made on the drawn list.
        sabotage=_pick(PICKS[0]),
        gestures=({"press": "#o-next", "pointer": "mouse"}, {"release": True}),
        script="""await onStep("votes");""",
        receipt=_both_untouched,
    ),
    Check(
        id="sources-step-names-a-feed-it-could-not-add",
        fixture="refuses-one",
        path=f"{PAGE}#sources",
        act=f"""
            await loaded();
            await feedsDrawn();
            {_pick(TAKEN, REFUSED)}
            press("o-next");
            await waitFor(settled, "the refusal");
        """,
        script=f"""
            const text = notice().textContent;
            expect(notice().classList.contains("err"), `not an error: ${{text}}`);
            expect(text.includes("Added 1 of 2 feeds"), `notice: ${{text}}`);
            const refused = {json.dumps(f"{REFUSED['name']}: {REFUSAL}", ensure_ascii=False)};
            expect(text.includes(refused), `notice: ${{text}}`);
            expect(text.includes("Press Next to try again"), `notice: ${{text}}`);
            expect(same(shownSteps(), ["sources"]), `shown: ${{shownSteps()}}`);
            const failed = feedRows().filter((row) => $('[aria-invalid="true"]', row))
              .map((row) => $(".name", row).textContent);
            expect(same(failed, [{json.dumps(REFUSED["name"], ensure_ascii=False)}]),
              `marked: ${{failed}}`);
        """,
        # The notice names the wrong feed: the one that was added.
        sabotage=f"notice().textContent = {json.dumps(MISNAMED)};",
        receipt=_sources(TAKEN),
    ),
    Check(
        id="sources-step-retries-only-what-it-could-not-add",
        fixture="refuses-one",
        path=f"{PAGE}#sources",
        act=f"""
            await loaded();
            await feedsDrawn();
            {_pick(TAKEN, REFUSED)}
            press("o-next");
            await waitFor(settled, "the refusal");
        """,
        # After the first try: the added feed is offered again, so a retry
        # that reads the rows sends it twice.
        sabotage=f"""$("input", rowNamed({json.dumps(TAKEN["name"])})).disabled = false;""",
        gestures=({"press": "#o-next", "pointer": "mouse"}, {"release": True}),
        script=f"""
            await onStep("votes");
            await waitFor(settled, "the result notice");
            const text = notice().textContent;
            const added = {json.dumps(f"Added 1 feed: {REFUSED['name']}", ensure_ascii=False)};
            expect(text.includes(added), `notice: ${{text}}`);
        """,
        receipt=_sources(TAKEN, REFUSED),
    ),
    Check(
        id="next-walks-to-the-next-step",
        fixture="empty",
        path=PAGE,
        act="""
            await loaded();
            press("o-next");
            await onStep("schedule");
        """,
        script="""
            expect(same(marked(), ["schedule"]), `marked: ${marked()}`);
            expect(progress() === "Step 2 of 6", `progress: ${progress()}`);
            expect(location.hash === "#schedule", `hash: ${location.hash}`);
            expect(visible($("#o-back")), "Back is hidden off the first step");
            const done = $$("#o-steps li.done").map((li) => li.dataset.step);
            expect(same(done, ["language"]), `done: ${done}`);
        """,
        sabotage="""location.hash = "#language";""",
        # Nothing was typed, so walking past a step stores nothing.
        receipt=_calls(),
    ),
    Check(
        id="back-returns-to-the-previous-step",
        fixture="empty",
        path=f"{PAGE}#schedule",
        act="""
            await loaded();
            press("o-back");
            await onStep("language");
        """,
        script="""
            expect(progress() === "Step 1 of 6", `progress: ${progress()}`);
            expect(!visible($("#o-back")), "Back shows on the first step");
        """,
        sabotage="""$("#o-progress").textContent = "Step 2 of 6";""",
        receipt=_calls(),
    ),
    Check(
        id="skip-walks-to-the-last-step",
        fixture="empty",
        path=PAGE,
        act="""
            await loaded();
            await skipTo("first-issue");
        """,
        script="""
            expect(progress() === "Step 6 of 6", `progress: ${progress()}`);
            expect(!visible($("#o-next")) && !visible($("#o-skip")),
              "Next or Skip still shows on the last step");
            expect(visible($("#o-finish")), "Finish is hidden on the last step");
        """,
        sabotage="""location.hash = "#notify";""",
        receipt=_calls(),
    ),
    Check(
        id="skip-writes-nothing",
        fixture="empty",
        path=PAGE,
        act=f"""
            await loaded();
            {TYPE_LANGUAGE}
            press("o-skip");
            await onStep("schedule");
        """,
        script="""expect(!visible(notice()), `a notice: ${notice().textContent}`);""",
        sabotage_preload=SKIP_SAVES,
        receipt=_calls(),
    ),
    Check(
        id="language-step-saves-both-values",
        fixture="empty",
        path=PAGE,
        act=f"""
            await loaded();
            {TYPE_LANGUAGE}
            press("o-next");
            await onStep("schedule");
        """,
        script="""
            await waitFor(settled, "the result notice");
            const text = notice().textContent;
            expect(!notice().classList.contains("err"), `an error: ${text}`);
            expect(text.includes("Output language: en"), `notice: ${text}`);
            expect(text.includes("Timezone: Europe/Lisbon"), `notice: ${text}`);
        """,
        sabotage_preload=rewrite_post(
            "/api/settings/values", 'body.values["general.timezone"] = "UTC";'
        ),
        receipt=_calls(LANGUAGE_STORED),
    ),
    Check(
        id="language-step-sends-only-what-changed",
        fixture="empty",
        path=PAGE,
        act=f"""
            await loaded();
            setValue($("#o-timezone"), {json.dumps(NEW_TIMEZONE)});
            press("o-next");
            await onStep("schedule");
            await waitFor(settled, "the result notice");
        """,
        script="""
            const text = notice().textContent;
            expect(!text.includes("Output language"), `notice names the language: ${text}`);
        """,
        sabotage_preload=rewrite_post(
            "/api/settings/values", 'body.values["digest.output_language"] = "en";'
        ),
        receipt=_calls({"general.timezone": NEW_TIMEZONE}),
    ),
    Check(
        id="an-untouched-step-writes-nothing",
        fixture="empty",
        path=PAGE,
        act="await loaded();",
        # After the act, so the value is not one the page loaded with.
        sabotage=f"""setValue($("#o-timezone"), {json.dumps(NEW_TIMEZONE)});""",
        gestures=({"press": "#o-next", "pointer": "mouse"}, {"release": True}),
        script="""await onStep("schedule");""",
        receipt=_calls(),
    ),
    Check(
        id="hours-step-saves-the-schedule",
        fixture="empty",
        path=f"{PAGE}#schedule",
        act=f"""
            await loaded();
            {TYPE_HOURS}
            press("o-next");
            await onStep("sources");
            await waitFor(settled, "the result notice");
        """,
        script="""
            const text = notice().textContent;
            expect(!notice().classList.contains("err"), `an error: ${text}`);
            expect(text.includes("07:00 and 19:00"), `notice: ${text}`);
        """,
        sabotage_preload=rewrite_post("/api/settings/schedule", 'body.times = ["08:00", "20:00"];'),
        receipt=_calls(HOURS_STORED),
    ),
    Check(
        id="hours-step-refuses-an-empty-hour",
        fixture="empty",
        path=f"{PAGE}#schedule",
        act="""
            await loaded();
            setValue($("#o-morning"), "");
            press("o-next");
            await waitFor(settled, "the refusal");
        """,
        script="""
            const text = notice().textContent;
            expect(visible(notice()), "the refusal is not shown");
            expect(notice().classList.contains("err"), `not an error: ${text}`);
            expect(text.includes("whole hour from 0 to 23"), `notice: ${text}`);
            expect(same(shownSteps(), ["schedule"]), `shown: ${shownSteps()}`);
            expect(same(marks(), ["o-morning"]), `marked: ${marks()}`);
        """,
        sabotage="""notice().hidden = true;""",
        # Refused on the page: the server was never asked to store an 00:00 morning.
        receipt=_calls(),
    ),
    Check(
        id="a-refused-value-is-marked-in-its-own-field",
        fixture="empty",
        path=PAGE,
        act=f"""
            await loaded();
            setValue($("#o-timezone"), {json.dumps(UNKNOWN_TIMEZONE)});
            press("o-next");
            await waitFor(settled, "the refusal");
        """,
        script="""
            const text = notice().textContent;
            expect(notice().classList.contains("err"), `not an error: ${text}`);
            expect(text.includes("is not a timezone this server knows"), `notice: ${text}`);
            expect(same(shownSteps(), ["language"]), `shown: ${shownSteps()}`);
            expect(same(marks(), ["o-timezone"]), `marked: ${marks()}`);
        """,
        sabotage="""$("#o-timezone").removeAttribute("aria-invalid");""",
        receipt=_calls(),
    ),
    Check(
        id="notify-step-saves-the-webhook",
        fixture="empty",
        path=f"{PAGE}#notify",
        act=f"""
            await loaded();
            {TYPE_WEBHOOK}
            press("o-next");
            await onStep("first-issue");
            await waitFor(settled, "the result notice");
        """,
        script="""
            const text = notice().textContent;
            expect(!notice().classList.contains("err"), `an error: ${text}`);
            expect(text.includes("answered offline"), `notice: ${text}`);
            expect(!document.body.textContent.includes("NEWTOKEN"), "the token is on the page");
        """,
        sabotage_preload=rewrite_post(
            "/api/settings/notify", 'body.discord_webhook_url += "-sabotaged";'
        ),
        receipt=_calls(WEBHOOK_STORED),
    ),
    Check(
        id="notify-step-saves-the-mail-pair",
        fixture="empty",
        path=f"{PAGE}#notify",
        act=f"""
            await loaded();
            {TYPE_MAIL}
            press("o-next");
            await onStep("first-issue");
            await waitFor(settled, "the result notice");
        """,
        script="""
            const text = notice().textContent;
            expect(!notice().classList.contains("err"), `an error: ${text}`);
            expect(text.includes("me@example.org"), `notice: ${text}`);
        """,
        sabotage_preload=rewrite_post("/api/settings/email", 'body.email_to = "x@example.test";'),
        receipt=_calls(MAIL_STORED),
    ),
    Check(
        id="notify-step-saves-both-parts",
        fixture="empty",
        path=f"{PAGE}#notify",
        act=f"""
            await loaded();
            {TYPE_WEBHOOK}
            {TYPE_MAIL}
            press("o-next");
            await onStep("first-issue");
            await waitFor(settled, "the result notice");
        """,
        script="""
            expect(!notice().classList.contains("err"), `an error: ${notice().textContent}`);
        """,
        sabotage_preload=rewrite_post("/api/settings/email", "body.email_to = body.email_from;"),
        # The webhook first, as the settings form sends them.
        receipt=_calls(WEBHOOK_STORED, MAIL_STORED),
    ),
    Check(
        id="notify-step-refuses-a-sender-without-a-recipient",
        fixture="empty",
        path=f"{PAGE}#notify",
        act=f"""
            await loaded();
            setValue($("#o-email-from"), {json.dumps(NEW_MAIL_FROM)});
            press("o-next");
            await waitFor(settled, "the refusal");
        """,
        script="""
            const text = notice().textContent;
            expect(visible(notice()), "the refusal is not shown");
            expect(notice().classList.contains("err"), `not an error: ${text}`);
            expect(text.includes("sent to, too"), `notice: ${text}`);
            expect(same(shownSteps(), ["notify"]), `shown: ${shownSteps()}`);
            expect(same(marks(), ["o-email-to"]), `marked: ${marks()}`);
        """,
        sabotage="""notice().hidden = true;""",
        receipt=_calls(),
    ),
    Check(
        id="notify-step-left-empty-writes-nothing",
        fixture="empty",
        path=f"{PAGE}#notify",
        act="await loaded();",
        sabotage=TYPE_WEBHOOK,
        gestures=({"press": "#o-next", "pointer": "mouse"}, {"release": True}),
        script="""await onStep("first-issue");""",
        receipt=_calls(),
    ),
    Check(
        id="finish-leaves-for-settings",
        fixture="empty",
        path=f"{PAGE}#first-issue",
        act="await loaded();",
        script="""
            const finish = $("#o-finish");
            expect(visible(finish), "Finish is hidden on the last step");
            expect(finish.getAttribute("href") === "/settings?from=onboarding",
              `Finish points at ${finish.getAttribute("href")}`);
            const bar = $(".site-nav .settings-link");
            expect(bar.getAttribute("href") === "/settings?from=onboarding",
              `the site bar points at ${bar.getAttribute("href")}`);
        """,
        sabotage="""$("#o-finish").setAttribute("href", "/settings");""",
    ),
    Check(
        id="an-empty-deployment-is-sent-from-settings-to-the-wizard",
        fixture="empty",
        path=SETTINGS,
        act="await loaded();",
        script="""
            expect(location.pathname === "/onboarding", `landed on ${location.pathname}`);
            expect(visible($("#o-steps")), "no step list");
        """,
        sabotage="""$("#o-steps").hidden = true;""",
    ),
    Check(
        id="has-sources-opens-the-wizard-without-the-list",
        fixture="has-sources",
        path=PAGE,
        act="""
            await loaded();
            await skipTo("sources");
            await feedsDrawn();
        """,
        script="""
            expect(location.pathname === "/onboarding", `landed on ${location.pathname}`);
            expect(visible($("#o-steps")), "no step list");
            const line = $("#o-feeds-had");
            expect(visible(line), "the line saying this deployment has sources is hidden");
            expect(line.textContent.includes("under Sources in Settings"), line.textContent);
            expect(!feedRows().filter(visible).length, `${feedRows().length} feeds listed`);
        """,
        sabotage="""$("#o-feeds-had").hidden = true;""",
        receipt=_both_untouched,
    ),
    Check(
        id="has-sources-keeps-the-step-a-reload-opens",
        fixture="has-sources",
        path=f"{PAGE}#votes",
        act="await loaded();",
        script="""
            expect(location.pathname === "/onboarding", `landed on ${location.pathname}`);
            expect(same(shownSteps(), ["votes"]), `shown: ${shownSteps()}`);
        """,
        sabotage="""$('.step[data-step="votes"]').hidden = true;""",
    ),
    Check(
        id="json-has-no-wizard",
        fixture="json",
        path=PAGE,
        act="""await waitFor(() => $(".settings-nav"), "the settings page");""",
        script="""
            expect(location.pathname === "/settings", `landed on ${location.pathname}`);
            expect(!$("#o-steps"), "the wizard is on the page");
            expect($(".settings-nav"), "not the settings page");
        """,
        sabotage="""$(".settings-nav").remove();""",
    ),
    Check(
        id="fits-375",
        fixture="empty",
        path=PAGE,
        width=375,
        act="await loaded();",
        script="""
            const problem = overflow("onboarding");
            expect(!problem, problem);
        """,
        sabotage="""$(".steps").style.minWidth = "600px";""",
    ),
    Check(
        id="fits-sources-375",
        fixture="empty",
        path=f"{PAGE}#sources",
        width=375,
        act="""
            await loaded();
            await feedsDrawn();
        """,
        script="""
            const problem = overflow("onboarding");
            expect(!problem, problem);
        """,
        sabotage="""feedRows()[0].style.minWidth = "600px";""",
    ),
    Check(
        id="tap-feeds-375",
        fixture="empty",
        path=f"{PAGE}#sources",
        width=375,
        act="""
            await loaded();
            await feedsDrawn();
        """,
        script="""expectTapTargets("#o-feeds .choice");""",
        sabotage=restyle("#o-feeds .choice { padding: 0 !important; line-height: 1 !important; }"),
    ),
    Check(
        id="tap-steps-375",
        fixture="empty",
        path=f"{PAGE}#schedule",
        width=375,
        act="await loaded();",
        script="""expectTapTargets(".step-nav .btn");""",
        sabotage=restyle(".step-nav .btn { height: 30px !important; }"),
    ),
    Check(
        id="tap-site-bar-375",
        fixture="empty",
        path=PAGE,
        width=375,
        act="""await waitFor(() => visible($(".site-nav a.gated")), "the archive link");""",
        script="""expectTapTargets(".brand, .site-nav a");""",
        sabotage=NO_SITE_BAR_AREAS,
    ),
]
# The page never scrolls sideways at the largest type size either.
_CHECKS += [
    largest_twin(check, check.id.replace("fits-", "fits-largest-"), "empty-largest")
    for check in _CHECKS
    if check.id in ("fits-375", "fits-sources-375")
]

CHECKS = _CHECKS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default=None, help="Chromium binary (else $CYRIS_CHROMIUM)")
    parser.add_argument("--only", action="append", default=[], metavar="ID", help="run one check")
    parser.add_argument(
        "--self-test", action="store_true", help="also run every check sabotaged; each must fail"
    )
    args = parser.parse_args()

    known = {check.id for check in CHECKS}
    if unknown := sorted(set(args.only) - known):
        parser.error(f"unknown check: {', '.join(unknown)}")
    checks = [check for check in CHECKS if not args.only or check.id in args.only]

    browser = find_browser(args.browser)
    require_node()
    # Chromium first: probe_environment() moves into a temporary home, where a
    # relative --browser path would no longer resolve.
    with chromium(browser) as socket_url, probe_environment():
        good = asyncio.run(run_all(checks, socket_url, args.self_test, build_fixture, PRELUDE))
    raise SystemExit(0 if good else 1)


if __name__ == "__main__":
    main()
