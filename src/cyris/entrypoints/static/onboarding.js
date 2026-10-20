// The onboarding wizard: one step at a time, moved through with Back, Next and
// Skip, ending back on /settings. Three steps write settings, each through the
// route /settings saves that field with, so what lands in the store is what the
// settings form would have stored. The sources step writes each picked feed
// through POST /api/sources, the route the settings form adds a source with. The
// votes step deals cards from those feeds (POST /api/onboarding/cards) and
// writes the marked ones as votes (POST /api/onboarding/votes), then turns vote
// similarity on through /api/settings/vote-similarity, the Model form's route.
// The first-issue step is a placeholder until its own ticket lands
// (onboarding.html.j2): it writes nothing, so Next on it only moves on.
const $ = (id) => document.getElementById(id);

// The six steps, in order. Each one's hash opens it, so a reload keeps the place.
const STEPS = ["language", "schedule", "sources", "votes", "notify", "first-issue"];

// The settings written through /api/settings/values, by the id of their control.
// Each key and its route are src/cyris/settings_fields.json's own.
const PLAIN = {
  "o-output-language": "digest.output_language",
  "o-timezone": "general.timezone",
};
// The publish hours, written through /api/settings/schedule as one list.
const HOURS = ["o-morning", "o-evening"];
// The Discord webhook, written through /api/settings/notify.
const WEBHOOK = "o-discord-webhook";
// The mail pair, written through /api/settings/email, recipient first.
const MAIL = {"o-email-to": "notify.email_to", "o-email-from": "notify.email_from"};

const FIELDS = [...Object.keys(PLAIN), ...HOURS, WEBHOOK, ...Object.keys(MAIL)];

// What each control was loaded with: a step sends only what the reader changed,
// as /settings does, so Next over an untouched step stores nothing.
const loaded = new Map(FIELDS.map((id) => [id, $(id).value]));

// The result of a save, shown on the step the reader moves to.
let pending = null;
// Each setting's registry entry, by key, from GET /api/settings: a notice names
// a field by the same label /settings gives it.
let REGISTRY = {};
// The stored values GET /api/settings answered; null until it has. The votes
// step turns vote similarity on with the embedder stored here.
let VALUES = null;

const stepOf = () => {
  const hash = location.hash.slice(1);
  return STEPS.includes(hash) ? hash : STEPS[0];
};

const hideNotice = () => { $("o-result").hidden = true; };

// A "busy" notice says what a save is doing while it is out; its result replaces it.
function show(kind, text) {
  const el = $("o-result");
  el.className = kind === "err" ? "notice err" : "notice";
  if (kind === "busy") el.setAttribute("aria-busy", "true");
  else el.removeAttribute("aria-busy");
  el.textContent = text;
  el.hidden = false;
}

function route() {
  const step = stepOf();
  const index = STEPS.indexOf(step);
  document.querySelectorAll(".step").forEach((panel) => {
    panel.hidden = panel.dataset.step !== step;
  });
  document.querySelectorAll("#o-steps li").forEach((item, position) => {
    if (item.dataset.step === step) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
    item.classList.toggle("done", position < index);
  });
  $("o-progress").textContent = `Step ${index + 1} of ${STEPS.length}`;
  const last = index === STEPS.length - 1;
  $("o-back").hidden = index === 0;
  // Nothing is left to save or to skip on the last step: Finish, the link out
  // of the wizard, is the only thing to press.
  $("o-skip").hidden = last;
  $("o-next").hidden = last;
  $("o-finish").hidden = !last;
  hideNotice();
  if (pending) show("ok", pending);
  pending = null;
  window.scrollTo(0, 0);
  if (step === "votes") loadCards();
}

addEventListener("hashchange", route);

// A refusal pinned to a field is marked there until it is edited; the reason
// itself goes in the notice beside the buttons.
const mark = (ids) => ids.forEach((id) => {
  $(id).classList.add("invalid");
  $(id).setAttribute("aria-invalid", "true");
});

const unmark = (ids) => ids.forEach((id) => {
  $(id).classList.remove("invalid");
  $(id).removeAttribute("aria-invalid");
});

// The app Worker answers 401 once the session cookie has expired. Reloading
// /onboarding then serves the sign-in form, which returns here.
const SESSION_EXPIRED = new Error(
  "Your session has expired. Reload the page to sign in again.");

// Rejects with what the reader needs: the server's own explanation when it
// gives one, otherwise what happened and what to do next.
async function post(url, body) {
  let res;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
  } catch (err) {
    throw new Error(`Could not reach cyris (${err.message}). Check the connection and try again.`);
  }
  if (res.status === 401) throw SESSION_EXPIRED;
  const data = await res.json().catch(() => ({}));
  if (data.ok) return data;
  const err = new Error(data.error || `cyris answered ${res.status} without saying why. ` +
    "Try again; if it keeps failing, check the server log.");
  // The setting the refusal is about, for the page to mark its field.
  err.field = data.field;
  throw err;
}

const changed = (ids) => ids.filter((id) => $(id).value !== loaded.get(id));
const keep = (ids) => ids.forEach((id) => loaded.set(id, $(id).value));
const fieldOf = (ids, keys, field) => ids.filter((id) => keys[id] === field);
// The registry's label, or the key itself when the settings read never answered.
const labelOf = (key) => REGISTRY[key]?.label ?? key;

// Each save answers the line to show when it stored something, "" when the step
// was left as it was loaded, or throws with what to tell the reader.
async function saveLanguage() {
  const ids = changed(Object.keys(PLAIN));
  if (!ids.length) return "";
  // Trimmed, as the settings form's own plain fields are.
  const values = Object.fromEntries(ids.map((id) => [PLAIN[id], $(id).value.trim()]));
  try {
    const data = await post("/api/settings/values", {values});
    keep(ids);
    const parts = ids.map((id) => `${labelOf(PLAIN[id])}: ${data.values[PLAIN[id]]}`);
    return `${parts.join(", ")}. ${data.note}`;
  } catch (err) {
    mark(err.field ? fieldOf(ids, PLAIN, err.field) : ids);
    throw err;
  }
}

async function saveSchedule() {
  if (!changed(HOURS).length) return "";
  // An empty hour is refused here: padded, it would reach the server as 00:00.
  const empty = HOURS.filter((id) => $(id).value === "");
  if (empty.length) {
    mark(empty);
    const names = empty.map((id) => $(id).labels[0].textContent).join(" and ");
    throw new Error(
      `${names} ${empty.length > 1 ? "need" : "needs"} a whole hour from 0 to 23.`);
  }
  const times = HOURS.map((id) => `${String($(id).value).padStart(2, "0")}:00`);
  try {
    const data = await post("/api/settings/schedule", {times});
    keep(HOURS);
    return `Publish hours: ${data.times.join(" and ")}. ${data.note}`;
  } catch (err) {
    mark(HOURS);
    throw err;
  }
}

// Two routes, as the settings form's Notifications has: the webhook and the
// mail pair, each tried on its own. An empty field is nothing to save rather
// than an off switch — turning notifications off is its own action, on /settings.
async function saveNotify() {
  const lines = [], problems = [];
  if (changed([WEBHOOK]).length && $(WEBHOOK).value.trim()) {
    show("busy", "Checking with Discord…");
    try {
      // Sent as typed, as the settings form sends it; the server trims it.
      const data = await post("/api/settings/notify", {discord_webhook_url: $(WEBHOOK).value});
      keep([WEBHOOK]);
      lines.push(`${data.detail} ${data.note}`);
    } catch (err) {
      mark([WEBHOOK]);
      problems.push(err.message);
    }
  }
  const mail = Object.keys(MAIL);
  const [to, from] = mail.map((id) => $(id).value);
  if (changed(mail).length && !to.trim() && from.trim()) {
    // The mirror of the server's own refusal of a recipient without a sender:
    // with nowhere to send it, the pair is not a setting yet.
    mark([mail[0]]);
    problems.push("Enter the address the digest is sent to, too.");
  } else if (changed(mail).length && to.trim()) {
    show("busy", "Sending a test message…");
    try {
      const data = await post("/api/settings/email", {email_to: to, email_from: from});
      keep(mail);
      lines.push(data.detail ? `${data.detail} ${data.note}` : data.note);
    } catch (err) {
      mark(err.field ? fieldOf(mail, MAIL, err.field) : mail);
      problems.push(err.message);
    }
  }
  if (problems.length) throw new Error(problems.join("\n"));
  return lines.join("\n");
}

// The feed list's rows, one per entry GET /api/onboarding/feeds sent, in its
// order; empty when the server offered none.
let FEEDS = [];

// A row's state, shown on its right before its tags. An added feed's checkbox
// is disabled, and that is what keeps a retry from sending it again.
function setState(row, state) {
  row.state = state;
  row.tags.textContent = [state, ...row.entry.tags].filter(Boolean).join(" · ");
  const added = state === "Added";
  row.box.disabled = added;
  row.label.classList.toggle("unavailable", added);
  if (state === "Not added") row.box.setAttribute("aria-invalid", "true");
  else row.box.removeAttribute("aria-invalid");
}

// The loading line goes only once the step is drawn, so a list that fails to
// draw leaves it in place for the reason.
function renderFeeds(data) {
  if (!data.feeds.length) {
    $("o-feeds-loading").remove();
    $("o-feeds-had").hidden = false;
    return;
  }
  FEEDS = data.feeds.map((entry) => {
    const label = document.createElement("label");
    label.className = "choice";
    const box = document.createElement("input");
    box.type = "checkbox";
    // The entry's own name and tags, as the list holds them.
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = entry.name;
    const tags = document.createElement("span");
    tags.className = "label";
    label.append(box, name, tags);
    const row = {entry, label, box, tags};
    box.addEventListener("change", () => { if (row.state === "Not added") setState(row, ""); });
    setState(row, "");
    return row;
  });
  $("o-feeds").append(...FEEDS.map((row) => row.label));
  $("o-feeds").hidden = false;
  $("o-feeds-loading").remove();
}

// One POST /api/sources per picked feed, each body the entry as listed, so the
// source stored is the list's own, tags and tier included. A feed already added
// is not sent again: Next after a partial failure retries only the ones that failed.
async function saveSources() {
  const picked = FEEDS.filter((row) => row.box.checked && !row.box.disabled);
  if (!picked.length) return "";
  const added = [], failed = [];
  let note = "";
  for (const [index, row] of picked.entries()) {
    show("busy", `Adding ${row.entry.name} (${index + 1} of ${picked.length})…`);
    try {
      ({note} = await post("/api/sources", row.entry));
      setState(row, "Added");
      added.push(row.entry.name);
    } catch (err) {
      setState(row, "Not added");
      failed.push(`${row.entry.name}: ${err.message}`);
      // A lapsed sign-in fails every feed after it the same way.
      if (err === SESSION_EXPIRED) break;
    }
  }
  if (failed.length) {
    const which = failed.length === 1 ? "This one was" : "These were";
    throw new Error([
      `Added ${added.length} of ${picked.length} feeds. ${which} not added:`,
      ...failed,
      "Press Next to try again, or Skip to go on without them.",
    ].join("\n"));
  }
  // The cards were dealt from the feeds there were; these are new.
  if (added.length) cardsDealt = false;
  const feeds = added.length === 1 ? "feed" : "feeds";
  return `Added ${added.length} ${feeds}: ${added.join(", ")}. ${note}`;
}

// The vote cards, one per card POST /api/onboarding/cards dealt, in its order.
// A card's `vote` is "up", "down" or null, the untouched card; `recorded` is
// set once the server has written its vote, and the card no longer changes.
let CARDS = [];
// Whether the cards on the page are dealt from the feeds the table holds now;
// a sources save that adds a feed clears it, and the next visit deals again.
let cardsDealt = false;
let dealing = false;

const VOTE_WORDS = {up: "Interested", down: "Not interested"};

function setVote(card, vote) {
  card.vote = vote;
  for (const [value, button] of Object.entries(card.buttons)) {
    button.setAttribute("aria-pressed", String(value === vote));
  }
}

// A recorded card shows what was written in place of its buttons, which would
// otherwise offer to change a vote that is already stamped as the reader's.
function setRecorded(card) {
  card.recorded = true;
  card.seg.hidden = true;
  card.state.textContent = `Recorded · ${VOTE_WORDS[card.vote]}`;
  card.state.hidden = false;
}

// A card whose article left its feed before Next: nothing to record it on, so
// it is not sent again.
function setGone(card) {
  card.gone = true;
  card.seg.hidden = true;
  card.state.textContent = "No longer in the feed · not recorded";
  card.state.hidden = false;
}

function cardRow(card) {
  const row = document.createElement("div");
  row.className = "list-row vote-card";
  const source = document.createElement("span");
  source.className = "label";
  source.textContent = card.source;
  // The feed's own words, in the feed's own language.
  const title = document.createElement("a");
  title.className = "title";
  title.lang = "";
  title.textContent = card.title || card.url;
  // A feed decides this URL, so only a web address becomes a link.
  if (/^https?:\/\//i.test(card.url)) {
    title.href = card.url;
    title.target = "_blank";
    title.rel = "noopener";
  }
  row.append(source, title);
  if (card.excerpt) {
    const text = document.createElement("p");
    text.className = "small";
    text.lang = "";
    text.textContent = card.excerpt;
    row.append(text);
  }
  const seg = document.createElement("div");
  seg.className = "seg";
  seg.setAttribute("role", "group");
  seg.setAttribute("aria-label", `Vote on ${card.title || card.url}`);
  const state = document.createElement("span");
  state.className = "label";
  state.hidden = true;
  const entry = {card, row, seg, state, buttons: {}, vote: null, recorded: false};
  for (const [vote, word] of Object.entries(VOTE_WORDS)) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.vote = vote;
    button.textContent = word;
    // A second press on the pressed answer takes it back: the card is untouched again.
    button.addEventListener("click", () => setVote(entry, entry.vote === vote ? null : vote));
    entry.buttons[vote] = button;
    seg.append(button);
  }
  setVote(entry, null);
  row.append(seg, state);
  return entry;
}

// Each source the read found nothing for, named, and why the poll before it
// failed when it did: both say why a feed the reader picked shows no card.
function renderCards(data) {
  CARDS = data.cards.map(cardRow);
  $("o-cards").replaceChildren(...CARDS.map((card) => card.row));
  $("o-cards").hidden = !CARDS.length;
  const problems = [];
  if (data.empty.length) {
    const which = data.empty.length === 1 ? "this feed" : `these ${data.empty.length} feeds`;
    problems.push(`No articles came back from ${which}: ${data.empty.join(", ")}. ` +
      "They stay in your sources and the first issue reads them again; " +
      "check them under Sources in Settings.");
  }
  if (data.poll_error) {
    problems.push(`The feeds could not be fetched just now (${data.poll_error}), ` +
      "so these cards are only what had been fetched before.");
  }
  $("o-cards-empty").textContent = problems.join("\n");
  $("o-cards-empty").hidden = !problems.length;
  let none = "";
  if (!data.sources) {
    none = "There are no feeds to draw from yet. Press Back to pick some, or Next to go on.";
  } else if (!CARDS.length) {
    none = "There is no article to vote on yet. Press Next to go on: the first issue works without votes.";
  }
  $("o-cards-none").textContent = none;
  $("o-cards-none").hidden = !none;
}

// Deals once per set of feeds: a reload or a visit after new feeds deals
// again, a Back and Next over the same feeds keeps the marks made.
async function loadCards() {
  if (cardsDealt || dealing) return;
  dealing = true;
  const loading = $("o-cards-loading");
  loading.textContent = "Fetching the latest articles from your feeds. This can take a minute or two…";
  loading.setAttribute("aria-busy", "true");
  loading.hidden = false;
  try {
    renderCards(await post("/api/onboarding/cards", {}));
    cardsDealt = true;
    loading.hidden = true;
  } catch (err) {
    loading.removeAttribute("aria-busy");
    loading.textContent = `${err.message} Press Skip to go on without votes, or reload the page to try again.`;
  } finally {
    dealing = false;
  }
}

// The marked cards not yet written, then vote similarity, without which the
// votes change nothing. A card left alone is not sent. Nothing marked and
// nothing recorded is a Next that writes nothing, as a Skip is.
async function saveVotes() {
  const marked = CARDS.filter((card) => card.vote && !card.recorded && !card.gone);
  const lines = [];
  if (marked.length) {
    show("busy", `Recording ${marked.length} ${marked.length === 1 ? "vote" : "votes"}…`);
    const data = await post("/api/onboarding/votes", {
      votes: marked.map((card) => ({url: card.card.url, vote: card.vote})),
    });
    const gone = new Set(data.missing);
    marked.forEach((card) => (gone.has(card.card.url) ? setGone : setRecorded)(card));
    const kept = data.accepted.length + data.rejected.length;
    lines.push(`Recorded ${kept} ${kept === 1 ? "vote" : "votes"}: ` +
      `${data.accepted.length} interested, ${data.rejected.length} not interested.`);
    if (gone.size) {
      lines.push(`${gone.size} ${gone.size === 1 ? "article has" : "articles have"} left ` +
        "its feed since the cards were dealt, so that vote was not recorded.");
    }
  }
  if (!CARDS.some((card) => card.recorded) || VALUES?.["vote_similarity.enabled"] === true) {
    return lines.join("\n");
  }
  try {
    if (!VALUES) throw new Error("The settings did not load. Reload the page.");
    show("busy", "Turning on vote similarity: checking the embedder…");
    // The embedder this deployment already names: which model it embeds with is
    // the deployer's choice, under Model in Settings, and the wizard leaves it.
    const data = await post("/api/settings/vote-similarity", {
      enabled: true,
      provider: VALUES["vote_similarity.provider"],
      model: VALUES["vote_similarity.model"] ?? "",
      max_seeds: VALUES["vote_similarity.max_seeds"],
    });
    Object.assign(VALUES, data.values);
    lines.push(`Vote similarity is on. ${data.detail} ${data.note}`);
  } catch (err) {
    throw new Error([
      ...lines,
      `Vote similarity is still off, so the votes have no effect yet: ${err.message}`,
      "Press Next to try again, or Skip and turn it on under Model in Settings.",
    ].join("\n"));
  }
  return lines.join("\n");
}

const SAVES = {
  language: saveLanguage,
  schedule: saveSchedule,
  sources: saveSources,
  votes: saveVotes,
  notify: saveNotify,
};

// Only ever called from a step that has one after it: the last step shows
// Finish in Next's and Skip's place.
function advance() {
  const index = STEPS.indexOf(stepOf());
  location.hash = STEPS[index + 1];
}

const moving = (on) => {
  ["o-back", "o-skip", "o-next"].forEach((id) => { $(id).disabled = on; });
};

$("o-next").addEventListener("click", async () => {
  const save = SAVES[stepOf()];
  if (!save) return advance();
  moving(true);
  unmark(FIELDS);
  show("busy", "Saving…");
  try {
    pending = await save();
    moving(false);
    hideNotice();
    advance();
  } catch (err) {
    moving(false);
    show("err", err.message);
  }
});

$("o-skip").addEventListener("click", () => {
  unmark(FIELDS);
  advance();
});

$("o-back").addEventListener("click", () => {
  unmark(FIELDS);
  const index = STEPS.indexOf(stepOf());
  if (index > 0) location.hash = STEPS[index - 1];
});

for (const id of FIELDS) {
  $(id).addEventListener("input", () => unmark([id]));
}

function render(state) {
  const values = state.values;
  VALUES = values;
  REGISTRY = state.fields;
  $("o-languages").replaceChildren(...state.languages.map((tag) => new Option("", tag)));
  if (values["digest.output_language"] != null) {
    $("o-output-language").value = values["digest.output_language"];
  }
  if (values["general.timezone"] != null) $("o-timezone").value = values["general.timezone"];
  const [morning, evening] = values["general.digest_schedule"] || [];
  if (morning) $("o-morning").value = parseInt(morning, 10);
  if (evening) $("o-evening").value = parseInt(evening, 10);
  // The stored webhook comes back masked, and a masked URL is not one Discord
  // would confirm, so that field opens empty; the mail pair is shown in full.
  $("o-email-to").value = values["notify.email_to"] ?? "";
  $("o-email-from").value = values["notify.email_from"] ?? "";
  keep(FIELDS);
}

route();

fetch("/api/settings")
  .then((r) => (r.status === 401 ? Promise.reject(SESSION_EXPIRED) : r.json()))
  .then(render)
  .catch((e) => {
    show("err", e === SESSION_EXPIRED ? e.message
      : `Could not load settings: ${e}. Reload the page to try again.`);
  })
  .finally(() => document.querySelectorAll(".step [data-loading]").forEach((el) => el.remove()));

fetch("/api/onboarding/feeds")
  .then(async (r) => {
    if (r.status === 401) throw SESSION_EXPIRED;
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.error || `cyris answered ${r.status}`);
    }
    return r.json();
  })
  .then(renderFeeds)
  .catch((e) => {
    $("o-feeds-loading").textContent = e === SESSION_EXPIRED ? e.message
      : `Could not load the feed list: ${e}. Reload the page to try again.`;
  });
