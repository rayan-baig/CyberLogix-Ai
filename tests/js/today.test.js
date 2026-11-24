// /today (static/simple.js) run in Node against a pretend page and a
// pretend server: `node --test tests/js/today.test.js`.
// tests/test_round9_food_answer_and_small_cafe.py runs this file too.
// Nothing leaves this machine: fetch answers from the fixtures below.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const read = (f) => fs.readFileSync(path.join(__dirname, "..", "..", "static", f), "utf8");
const SOURCES = ["words.js", "answer.js", "simple.js"].map(read);

function element(id) {
  const classes = new Set();
  const node = {
    id, innerHTML: "", textContent: "", hidden: false, className: "", open: false, dataset: {},
    classList: {
      add: (c) => classes.add(c), remove: (c) => classes.delete(c),
      toggle: (c, on) => { if (on === undefined ? !classes.has(c) : on) classes.add(c); else classes.delete(c); },
      contains: (c) => classes.has(c),
    },
    listeners: {},
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    querySelector() { return node.summary || (node.summary = element(`${id}-summary`)); },
  };
  return node;
}

/* Runs /today once against `overview` (and `me` for /api/accounts/me).
   Returns the page's elements and the requests it made. */
async function today(overview, me = { user: { role: "owner" }, prefs: {} }, did = DID) {
  const nodes = {};
  const calls = [];
  const document = {
    getElementById: (id) => nodes[id] || (nodes[id] = element(id)),
    createElement: () => element("made"),
    body: { appendChild() {} },
    querySelectorAll: () => [],
  };
  const fetch = async (url, init) => {
    calls.push({ url, init });
    if (url === "/api/accounts/me") return { ok: !!me, status: me ? 200 : 401, json: async () => me };
    if (url === "/api/console/overview") return { ok: true, status: 200, json: async () => overview };
    if (url.startsWith("/api/console/did-for-you")) return { ok: true, status: 200, json: async () => did };
    return { ok: true, status: 200, json: async () => ({}) };
  };
  const context = {
    document, fetch, console, Date, Math, JSON, Object, Array, Number, String, Set, Promise, Error, isNaN,
    location: { href: "/today", hash: "", pathname: "/today", search: "" },
    history: { replaceState() {} },
    addEventListener() {},
    localStorage: { getItem: () => "session-token", setItem() {} },
    setTimeout: () => 0, clearTimeout() {},
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  for (const code of SOURCES) vm.runInContext(code, context);
  for (let n = 0; n < 20; n += 1) await new Promise((r) => setImmediate(r));
  return { nodes, calls, CLXWords: context.CLXWords };
}

const NOW = new Date().toISOString();
// What Kryozyn did for you (3.32.0), as the server says it.
const DID = { period: "week", label: "This week", figures: { readings: 2016, alerts: 1 },
  lines: ["Checked 2,016 readings from 1 fridge or freezer, 100% in range.", "1 alert, 1 answered in 4 minutes."],
  log_link: "/console?view=full#inspector-log?from=2026-11-16&to=2026-11-23&open=1", sample: null };
const fridge = (condition = { state: "ok" }, temp = 38) => ({
  sensor_id: "WALK-1", location_name: "Walk-in fridge", last_temperature_display: temp,
  temperature_unit: "F", last_seen: NOW, online: true, condition,
});
const base = (extra) => Object.assign({
  sensors: [fridge()], incidents: [], site_wide_alerts: [], account: { paused: null },
  entitlements: { calls: { mode: "none", words: "" } },
  this_month: { alerts: 0, words: "This month: no alerts.", food_answers: [], food_usd: 0 },
}, extra);
const closedAlert = (question) => ({
  incident_id: "INC-1", sensor_id: "WALK-1", state: "resolved", opened_at: NOW, resolved_at: NOW,
  food_question: Object.assign({ word: "food", at_risk: true, words: "Food at risk: check it",
    note: "It was out of range for more than 2 hours. Say what happened to it, for the record.",
    back_minutes: 300, ask_on_today: true }, question),
});

test("3.30.0: never 'Everything is fine' while food is at risk: one thing to do, with the two answers right there", async () => {
  const { nodes } = await today(base({ incidents: [closedAlert()] }));
  const v = nodes.verdict.innerHTML;
  assert.match(v, /One thing\s+to do:\s+check the food in Walk-in fridge/);
  assert.match(v, /data-food-kept="INC-1"/);
  assert.match(v, /data-food-thrown="INC-1"/);
  assert.doesNotMatch(v, /Everything is fine|nothing for you to do/);
  assert.equal(nodes.verdict.className, "verdict bad");
});

test("3.30.0: a closed alert back in time but unanswered still asks, and says how long it took", async () => {
  const { nodes } = await today(base({ incidents: [closedAlert({ at_risk: false,
    words: "Cold again after 12 minutes out", out_since: "2026-11-22T21:10:00Z",
    note: "Nobody has said yet whether the food was kept. One tap, for the record." })] }));
  const v = nodes.verdict.innerHTML;
  assert.match(v, /check the food in Walk-in fridge/);
  // 3.32.0: "Cold again after 12 minutes out (since 9:10 PM)", in the reader's clock.
  const W = require(path.join(__dirname, "..", "..", "static", "words.js"));
  assert.ok(v.includes(`Cold again after 12 minutes out (since ${W.clock("2026-11-22T21:10:00Z")})`), v);
  assert.doesNotMatch(v, /Back in range in/);
  // The "Food kept" button is there; no claim that it was.
  assert.doesNotMatch(v, /Everything is fine|protected|\$/i);
});

test("3.30.0: once answered, nothing is asked and everything is fine again", async () => {
  const answered = closedAlert();
  answered.food_question = null;
  const { nodes } = await today(base({ incidents: [answered] }));
  assert.match(nodes.verdict.innerHTML, /Everything is fine/);
});

test("3.30.0: a viewer sees the question but not the buttons", async () => {
  const { nodes } = await today(base({ incidents: [closedAlert()] }), { user: { role: "viewer" }, prefs: {} });
  assert.match(nodes.verdict.innerHTML, /check the food in Walk-in fridge/);
  assert.doesNotMatch(nodes.verdict.innerHTML, /data-food-kept/);
});

test("3.30.0: an open alert whose reading is back in range says 'Back in range: press It's fixed', never 'Too warm' beside 38°", async () => {
  const live = { incident_id: "INC-2", sensor_id: "WALK-1", state: "acknowledged", opened_at: NOW,
    out_since: NOW, acknowledged_by_name: "Jo", temperature_display: 47 };
  const { nodes } = await today(base({
    sensors: [fridge({ state: "alert", back_in_range: true, too: null, out_since: NOW })],
    incidents: [live],
  }));
  assert.match(nodes.verdict.innerHTML, /Back in range: press It's fixed/);
  assert.match(nodes.things.innerHTML, /Back in range: press It&#39;s fixed/);
  assert.doesNotMatch(nodes.things.innerHTML, /Too warm/);
  assert.doesNotMatch(nodes.todo.innerHTML, /warming up/, "not a second, wrong job for the same fridge");
  assert.match(nodes.needs.innerHTML, /back in range at 38°/);
  assert.doesNotMatch(nodes.needs.innerHTML, /out of range for/);
});

test("3.30.0: the month line says who answered the food and when", async () => {
  const at = "2026-11-22T16:09:00Z";
  const { nodes, CLXWords } = await today(base({ this_month: {
    alerts: 1, words: "This month: 1 alert · answered in 4 minutes · food thrown away",
    food_answers: [{ incident_id: "INC-1", unit: "Walk-in fridge", words: "Thrown away", by_name: "Jo", at,
      thrown: true, disposition: "discarded", changeable: false }], food_usd: 0 } }));
  assert.match(nodes["month-sub"].innerHTML, new RegExp(`Thrown away, Jo, ${CLXWords.clock(at)}\\.`));
  assert.equal(nodes["month-sub"].hidden, false);
  assert.doesNotMatch(nodes["month-sub"].innerHTML, /protected|\$|Change/, "past 24 hours: no Change");
});

test("3.30.0: 'What this is' follows the person's saved choice, not the device", async () => {
  const folded = await today(base(), { user: { role: "owner" }, prefs: { today_intro: "closed" } });
  assert.equal(folded.nodes.intro.open, false);
  assert.ok(folded.nodes.intro.classList.contains("intro-back"));
  assert.ok(!folded.calls.some((c) => c.url === "/api/accounts/me/prefs"), "nothing to save");

  const opened = await today(base(), { user: { role: "owner" }, prefs: { today_intro: "open" } });
  assert.equal(opened.nodes.intro.open, true);

  // Never chosen: open once, and saved as folded for next time, on the account.
  const first = await today(base(), { user: { role: "owner" }, prefs: {} });
  assert.equal(first.nodes.intro.open, true);
  const saved = first.calls.find((c) => c.url === "/api/accounts/me/prefs");
  assert.ok(saved, "saved on the account");
  assert.deepEqual(JSON.parse(saved.init.body), { today_intro: "closed" });

  // The person folding it saves that too.
  first.nodes.intro.open = false;
  first.nodes.intro.listeners.toggle.forEach((fn) => fn());
  const last = first.calls.filter((c) => c.url === "/api/accounts/me/prefs").pop();
  assert.deepEqual(JSON.parse(last.init.body), { today_intro: "closed" });
});

// ---------------------------------------------------------------- 3.32.0

const LATER = new Date(Date.now() + 20 * 3600 * 1000).toISOString();

test("3.32.0: a short blip recorded safe by itself: no 'one thing to do', the month line says so, with Change", async () => {
  const { nodes } = await today(base({ this_month: {
    alerts: 1, words: "This month: 1 alert · cold again by itself · food safe", food_usd: 0,
    food_answers: [{ incident_id: "INC-9", unit: "Walk-in fridge", words: "Food safe: out for 4 minutes", by_name: null,
      at: NOW, auto: true, thrown: false, disposition: "unaffected", changeable: true, change_until: LATER }] } }));
  assert.match(nodes.verdict.innerHTML, /Everything is fine/);
  assert.equal(nodes.verdict.className, "verdict ok", "no red or amber");
  const sub = nodes["month-sub"].innerHTML;
  assert.match(sub, /Food safe: out for 4 minutes, recorded automatically/);
  assert.match(sub, /data-food-change="INC-9"[^>]*>Change</);
  assert.match(sub, /data-food-kept="INC-9"/);
  assert.match(sub, /data-food-thrown="INC-9"/);
  assert.doesNotMatch(sub, /insurance pack|\$/);
});

test("3.32.0: after 'Thrown away', the insurance pack and the note of what went, and Change, for 24 hours", async () => {
  const answer = { incident_id: "INC-5", unit: "Walk-in fridge", words: "Thrown away", by_name: "Jo", at: NOW,
    auto: false, thrown: true, disposition: "discarded", changeable: true, change_until: LATER, has_note: false };
  const { nodes } = await today(base({ this_month: { alerts: 1, words: "This month: 1 alert", food_usd: 0, food_answers: [answer] } }));
  const sub = nodes["month-sub"].innerHTML;
  assert.match(sub, /data-loss-pack="INC-5"[^>]*>Make the insurance pack</);
  assert.match(sub, /data-loss-note="INC-5"[^>]*>Add what was thrown away</);
  assert.match(sub, /data-food-change="INC-5"/);
  // A note already written: only the pack is offered.
  const noted = await today(base({ this_month: { alerts: 1, words: "x", food_usd: 0, food_answers: [{ ...answer, has_note: true }] } }));
  assert.doesNotMatch(noted.nodes["month-sub"].innerHTML, /data-loss-note/);
  assert.match(noted.nodes["month-sub"].innerHTML, /data-loss-pack/);
  // A viewer: no buttons at all.
  const viewer = await today(base({ this_month: { alerts: 1, words: "x", food_usd: 0, food_answers: [answer] } }),
    { user: { role: "viewer" }, prefs: {} });
  assert.doesNotMatch(viewer.nodes["month-sub"].innerHTML, /data-loss|data-food/);
});

test("3.32.0: What Kryozyn did for you, with the log for the same days, and the sample in a trial's first week", async () => {
  const { nodes, calls } = await today(base());
  assert.ok(calls.some((c) => c.url === "/api/console/did-for-you?period=week"));
  assert.equal(nodes.did.hidden, false);
  assert.equal(nodes["did-teaser"].textContent, "This week: 2,016 readings checked, 1 alert.");
  assert.match(nodes["did-lines"].innerHTML, /Checked 2,016 readings/);
  assert.equal(nodes["did-log"].href, DID.log_link);
  assert.equal(nodes["did-sample"].hidden, true);
  const trial = await today(base(), undefined, { ...DID, sample: { note: "A sample of what this shows.", lines: ["Checked 6,048 readings."] } });
  assert.equal(trial.nodes["did-sample"].hidden, false);
  assert.match(trial.nodes["did-sample-lines"].innerHTML, /6,048/);
});

test("3.32.0: the 'What this is' label follows the toggle", async () => {
  const first = await today(base(), { user: { role: "owner" }, prefs: { today_intro: "closed" } });
  const summary = first.nodes.intro.querySelector("summary");
  assert.equal(summary.textContent, "How this works");
  first.nodes.intro.open = true;
  first.nodes.intro.listeners.toggle.forEach((fn) => fn());
  assert.equal(summary.textContent, "What this is, and how it works");
  first.nodes.intro.open = false;
  first.nodes.intro.listeners.toggle.forEach((fn) => fn());
  assert.equal(summary.textContent, "How this works");
});

// ---------------------------------------------------------------- 3.33.0

test("3.33.0: for a day after food is thrown away, a calm headline with the insurance pack, never 'nothing for you to do'", async () => {
  const loss = { incident_id: "INC-7", unit: "Walk-in fridge", at: NOW, by_name: "Maya", note: null, until: LATER, others: 0 };
  const answer = { incident_id: "INC-7", unit: "Walk-in fridge", words: "Thrown away", by_name: "Maya", at: NOW,
    auto: false, thrown: true, disposition: "discarded", changeable: true, change_until: LATER, has_note: false, note: null };
  const { nodes, CLXWords } = await today(base({ recent_loss: loss,
    this_month: { alerts: 1, words: "This month: 1 alert", food_usd: 0, food_answers: [answer] } }));
  const v = nodes.verdict.innerHTML;
  assert.match(v, /<h1>Food thrown away from Walk-in fridge: make the insurance pack<\/h1>/);
  assert.ok(v.includes(`Recorded at ${CLXWords.clock(NOW)}, by Maya.`), v);
  assert.match(v, /data-loss-pack="INC-7"[^>]*>Make the insurance pack</);
  assert.match(v, /data-loss-note="INC-7"[^>]*>Add what was thrown away</);
  assert.doesNotMatch(v, /Everything is fine|nothing for you to do/);
  assert.equal(nodes.verdict.className, "verdict calm", "calm: not red, not amber");
  // The pack is offered once, in the headline, not again on the month line.
  assert.doesNotMatch(nodes["month-sub"].innerHTML, /data-loss-pack/);
  // With the note saved: only the pack, and the note under the food line.
  const noted = await today(base({ recent_loss: { ...loss, note: "Thrown away from Walk-in fridge: 20 lb chicken" },
    this_month: { alerts: 1, words: "x", food_usd: 0,
      food_answers: [{ ...answer, has_note: true, note: "Thrown away from Walk-in fridge: 20 lb chicken" }] } }));
  assert.doesNotMatch(noted.nodes.verdict.innerHTML, /data-loss-note/);
  assert.match(noted.nodes["month-sub"].innerHTML, /<p class="answer-note">“Thrown away from Walk-in fridge: 20 lb chicken”<\/p>/);
  // A viewer: the pack, no note button.
  const viewer = await today(base({ recent_loss: loss }), { user: { role: "viewer" }, prefs: {} });
  assert.match(viewer.nodes.verdict.innerHTML, /data-loss-pack/);
  assert.doesNotMatch(viewer.nodes.verdict.innerHTML, /data-loss-note/);
  // An open alert still comes first; none at all, and it is fine again.
  assert.match((await today(base())).nodes.verdict.innerHTML, /Everything is fine/);
});

test("3.33.0: plurals: '1 reading checked', never '1 readings'", async () => {
  const { nodes } = await today(base(), undefined, { ...DID, figures: { readings: 1, alerts: 0 } });
  assert.equal(nodes["did-teaser"].textContent, "This week: 1 reading checked, no alerts.");
  const two = await today(base(), undefined, { ...DID, figures: { readings: 2, alerts: 1 } });
  assert.equal(two.nodes["did-teaser"].textContent, "This week: 2 readings checked, 1 alert.");
  const W = require(path.join(__dirname, "..", "..", "static", "words.js"));
  assert.equal(W.count(1, "alert"), "1 alert");
  assert.equal(W.count(2, "alert"), "2 alerts");
  assert.equal(W.count(1240, "reading"), "1,240 readings");
  assert.equal(W.count(0, "fridge or freezer", "fridges and freezers"), "0 fridges and freezers");
});

test("3.33.0: the console's insurance-pack list: '1 alert', '2 alerts', each by its unit's name, never a sensor id", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "console.js"), "utf8");
  const start = src.indexOf("const CLAIM_STATE");
  const code = src.slice(start, src.indexOf("\n}\n", src.indexOf("function renderClaims", start)) + 3);
  const box = { innerHTML: "" };
  const context = { CLXWords: require(path.join(__dirname, "..", "..", "static", "words.js")), $: () => box,
    esc: (s) => String(s == null ? "" : s), shortDate: () => "Nov 23", noun: () => "fridge", nextSteps: () => "", state: { data: {} } };
  vm.createContext(context);
  vm.runInContext(`${code}\nthis.renderClaims = renderClaims;`, context);
  const row = (id, unit, food) => ({ incident_id: id, sensor_id: "WALK-IN-1", unit, opened_at: NOW, catastrophe: "Fridge warming",
    state: "resolved", readings_available: true, food });
  context.renderClaims({ count: 1, period_days: 365, incidents: [row("INC-1", "Walk-in fridge", "Thrown away")] });
  assert.match(box.innerHTML, /1 alert an insurance pack can be made for:/);
  assert.match(box.innerHTML, /<span class="fc-name">Walk-in fridge<\/span>/);
  assert.match(box.innerHTML, /Nov 23 · Thrown away/);
  assert.doesNotMatch(box.innerHTML, /WALK-IN-1|alert\(s\)/);
  context.renderClaims({ count: 2, period_days: 365, incidents: [row("INC-1", "Walk-in fridge", null), row("INC-2", "Prep fridge", null)] });
  assert.match(box.innerHTML, /2 alerts an insurance pack can be made for:/);
  assert.match(box.innerHTML, /Nov 23 · closed/);
});

test("3.36.0: after It's fixed while still warm, 'Being dealt with' waits: who said it, that it closes by itself, and no It's fixed button", async () => {
  const live = { incident_id: "INC-3", sensor_id: "WALK-1", state: "acknowledged", opened_at: NOW, out_since: NOW,
    acknowledged_by_name: "Maya", fix_reported_at: NOW, fix_reported_by_name: "Maya", temperature_display: 47 };
  const { nodes } = await today(base({
    sensors: [fridge({ state: "alert", too: "warm", out_since: NOW }, 47)], incidents: [live],
  }));
  assert.match(nodes.verdict.innerHTML, /<h1>Being dealt with<\/h1>/);
  assert.match(nodes.verdict.innerHTML, /Maya said it&#39;s fixed\. It closes by itself once it&#39;s cold\.|Maya said it's fixed\. It closes by itself once it's cold\./);
  assert.doesNotMatch(nodes.verdict.innerHTML, /Press <b>It's fixed<\/b>/);
  assert.equal(nodes.verdict.className, "verdict watch", "waiting, not alarm");
  assert.match(nodes.needs.innerHTML, /Maya said it(&#39;|')s fixed at .+\. Nothing more to press\./);
  assert.equal(nodes["needs-h"].textContent, "Waiting to get cold");
  assert.doesNotMatch(nodes.needs.innerHTML, /data-fixed=/, "nothing more to press");
  // Before anybody said fixed, the button is there.
  const before = await today(base({
    sensors: [fridge({ state: "alert", too: "warm", out_since: NOW }, 47)], incidents: [{ ...live, fix_reported_at: null, fix_reported_by_name: null }],
  }));
  assert.match(before.nodes.needs.innerHTML, /data-fixed="INC-3"/);
  assert.match(before.nodes.verdict.innerHTML, /Press <b>It's fixed<\/b> once it is cold again/);
  assert.equal(before.nodes["needs-h"].textContent, "Needs you now");
});
