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
async function today(overview, me = { user: { role: "owner" }, prefs: {} }) {
  const nodes = {};
  const calls = [];
  const document = {
    getElementById: (id) => nodes[id] || (nodes[id] = element(id)),
    createElement: () => element("made"),
    body: { appendChild() {} },
  };
  const fetch = async (url, init) => {
    calls.push({ url, init });
    if (url === "/api/accounts/me") return { ok: !!me, status: me ? 200 : 401, json: async () => me };
    if (url === "/api/console/overview") return { ok: true, status: 200, json: async () => overview };
    return { ok: true, status: 200, json: async () => ({}) };
  };
  const context = {
    document, fetch, console, Date, Math, JSON, Object, Array, Number, String, Set, Promise, Error, isNaN,
    location: { href: "/today" },
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
    words: "Back in range in 12 minutes", note: "Nobody has said yet whether the food was kept. One tap, for the record." })] }));
  const v = nodes.verdict.innerHTML;
  assert.match(v, /check the food in Walk-in fridge/);
  assert.match(v, /Back in range in 12 minutes/);
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
    alerts: 1, words: "This month: 1 alert, answered in 4 minutes, food thrown away.",
    food_answers: [{ unit: "Walk-in fridge", words: "Thrown away", by_name: "Jo", at }], food_usd: 0 } }));
  assert.equal(nodes["month-sub"].textContent, `Thrown away, Jo, ${CLXWords.clock(at)}.`);
  assert.equal(nodes["month-sub"].hidden, false);
  assert.doesNotMatch(nodes["month-sub"].textContent, /protected|\$/);
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
