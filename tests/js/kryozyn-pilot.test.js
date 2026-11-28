// Kryozyn's Pilot pack and console tools, run in Node:
// `node --test tests/js/kryozyn-pilot.test.js`. tests/test_pilot.py runs
// it too. The console API and the AI provider are both mocked: no request
// leaves this machine.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const core = (f) => path.join(__dirname, "..", "..", "static", "pilot", f);
const app = (f) => path.join(__dirname, "..", "..", "static", f);
const Store = require(core("pilot-store.js"));
const Providers = require(core("pilot-providers.js"));
globalThis.PilotProviders = Providers;
const Engine = require(core("pilot-engine.js"));

// The names, as main.pilot_facts puts them on Pilot's script tag (brand.py).
const NAMES = { appName: "Kryozyn", appShort: "Kryozyn", companyName: "MTRX Horizyn", checklistApp: "OmniArmor" };
const FACTS = {
  ...NAMES,
  fromPrice: "59", starterUnits: "3", restaurantPrice: "149", pharmacyPrice: "199", unitsPerLocation: "10",
  extraUnit: "15", trialDays: "14", trialUnits: "5", trialCalls: "5", fridgeLimit: "41", freezerLimit: "10",
  offlineMinutes: "30", callAfterMinutes: "10", textWithinSeconds: "60",
};

const OVERVIEW = {
  temperature_unit: "F",
  account: { paused: null },
  sensors: [
    { sensor_id: "WALKIN-1", location_name: "Kitchen walk-in", asset_noun: "walk-in", last_temperature_display: 47,
      danger_above_display: 41, danger_below_display: null, online: true, breaching: true, last_seen: "2026-11-20T10:00:00Z",
      tenant_id: "TEN-SECRET", site_name: "Harbor Grill" },
    { sensor_id: "FREEZER-1", location_name: "Bar freezer", asset_noun: "walk-in", last_temperature_display: 2,
      danger_above_display: 10, danger_below_display: null, online: true, breaching: false, hours_until_breach: 3,
      last_seen: "2026-11-20T10:00:00Z" },
    { sensor_id: "PREP-1", location_name: "Prep fridge", asset_noun: "walk-in", last_temperature_display: 38,
      danger_above_display: 41, online: false, breaching: false, last_seen: "2026-11-20T08:00:00Z" },
  ],
  incidents: [
    { incident_id: "INC-000001", sensor_id: "WALKIN-1", state: "open", temperature_display: 47, opened_at: "2026-11-20T09:55:00Z",
      minutes_open: 5, notified_count: 2, acknowledged_at: null, acknowledged_by: "Dana Reyes", resolved_at: null,
      voice_escalated_at: null, breach_details: "47°F is above the 41°F limit.",
      sms_fanout: [{ contact_name: "Dana Reyes", to: "+15550100" }], dispatched_sms_text: "ALERT Kitchen walk-in: 47F." },
    { incident_id: "INC-000000", sensor_id: "PREP-1", state: "resolved", resolved_at: "2026-11-19T09:00:00Z" },
  ],
  site_wide_alerts: [],
};

function memoryStorage(data = new Map()) {
  return { data, getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => { data.set(k, String(v)); },
    removeItem: (k) => { data.delete(k); } };
}

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

// A browser page at `where`, signed in or not, with the console's API mocked.
function page({ where = "/today", signedIn = true, bridge = false } = {}) {
  const calls = [];
  const added = [];
  const local = memoryStorage();
  if (signedIn) local.setItem("cyberlogix.session", "session-token");
  const answer = (url, init) => {
    const method = (init && init.method) || "GET";
    calls.push({ url, method, auth: init && init.headers && init.headers.Authorization, body: init && init.body });
    if (url === "/api/console/overview") return json(200, OVERVIEW);
    if (url.startsWith("/api/disposition/")) {
      return json(200, { window_hours: 24, headline: "Under two hours.", detail: "1.2 hours above 41°.", band: "cool_it_down",
        measured: { hours_above: 1.2, peak_f: 47, still_warm: false }, not_a_ruling: "This is a measurement and a widely-taught rule of thumb, not a food safety decision." });
    }
    if (url.startsWith("/api/voice/acknowledge/")) return json(200, { status: "ACKNOWLEDGED", message: "Escalation ladder halted." });
    if (url.startsWith("/api/voice/resolve/")) return json(200, { status: "RESOLVED" });
    if (url === "/api/proof-reports" && method === "GET") return json(200, { locations: [{ site_id: "SITE-1", name: "Harbor Grill" }] });
    if (url === "/api/proof-reports") return json(201, { report_id: "PRF-000001", month_label: "November 2026", location: "Harbor Grill", verify_code: "AAAA-BBBB" });
    return json(404, { detail: "no" });
  };
  const fakeFetch = async (url, init) => answer(url, init);
  globalThis.fetch = fakeFetch;
  globalThis.window = {
    location: { pathname: where },
    localStorage: local,
    Pilot: { config: { facts: FACTS, screen: () => ({ name: "Test screen" }) } },
    PilotPanel: { screenText: () => "## Your fridges\nKitchen walk-in 47°" },
  };
  if (bridge) {
    globalThis.window.KryozynConsole = {
      signedIn: () => signedIn,
      api: async (p, init) => { const r = await fakeFetch(p, { ...init, headers: { Authorization: "Bearer session-token" } });
        const b = await r.json(); if (!r.ok) throw new Error(b.detail); return b; },
      tab: () => "Alerts",
      sector: () => ({ vertical: "restaurant", name: "Restaurants", kinds: [{ kind: "fridge", alarm: "over 41°F" }, { kind: "freezer", alarm: "over 10°F" }] }),
      addSensor: async (location, kind) => { added.push({ location, kind });
        return { added: location, sensor_id: "BAR-1A2B", alarm_above: kind === "freezer" ? 10 : 41, units_used: "4 of 5" }; },
      refresh: () => {},
    };
  }
  delete require.cache[app("pilot-kryozyn-pack.js")];
  const pack = require(app("pilot-kryozyn-pack.js"));
  return { pack, calls, added, tools: Object.fromEntries(pack.tools().map((t) => [t.name, t])) };
}

function engineWith(pack, { replies, confirmAnswers = [] }) {
  const store = Store.create({ prefix: "c", local: memoryStorage(), session: memoryStorage() });
  store.saveSettings({ provider: "anthropic", key: "test-key", model: "test-model", maxTokens: 0 });
  const sent = [];
  const asked = [];
  const providers = { ...Providers, chat: async (s, req) => { sent.push(req); return { raw: {}, toolCalls: [], stop: "done", ...replies.shift() }; } };
  const host = { confirm: async (label) => { asked.push(label); return confirmAnswers.length ? confirmAnswers.shift() : false; },
    plan: async () => "cancel" };
  const engine = Engine.create({ store, providers, host,
    config: { appName: "Kryozyn", knowledge: () => pack.knowledge, strengths: () => pack.strengths, tools: () => pack.tools(),
      kidView: () => false, screen: () => ({ name: "Simple view" }) } });
  return { engine, sent, asked };
}

const toolResult = (sent, id) => sent[sent.length - 1].messages.find((m) => m.role === "tool").results.find((r) => r.id === id).content;

// ---------------------------------------------------------------- the pack

test("the knowledge pack has the real facts, with the server's figures", () => {
  const { pack } = page();
  for (const fact of ["over 41°F", "over 10°F", "From $59 a month", "5 alert calls", "What you need", "does not sell sensors",
    "proof of monitoring report", "Simple view (/today)", "Advanced tab", "If the power or the internet goes off",
    "after 30 minutes", "within 60 seconds", "within 10 minutes", "14 days", "$149 a month per location", "not a ruling",
    "Your AI key for alert wording"]) {
    assert.ok(pack.knowledge.includes(fact), fact);
  }
  assert.ok(pack.strengths.length >= 8);
  for (const s of pack.strengths) assert.doesNotMatch(s, /competitor|rival|unlike|better than/i, s);
  delete globalThis.window;
});

test("the tools: five that read, four that change and ask first, all accepted by the engine", () => {
  const { pack } = page();
  const tools = pack.tools();
  assert.deepEqual(tools.filter((t) => t.kind === "read").map((t) => t.name),
    ["show_units_out_of_range", "show_open_alerts", "explain_alert", "is_the_food_ok", "explain_this_page"]);
  assert.deepEqual(tools.filter((t) => t.kind === "change").map((t) => t.name),
    ["acknowledge_alert", "mark_alert_fixed", "add_sensor", "make_proof_report"]);
  for (const t of tools) assert.ok(Engine.checkTool(t), t.name);
  delete globalThis.window;
});

test("no console tools on a public page or when signed out; adding a unit only on the console", () => {
  for (const where of ["/", "/pricing", "/help", "/for/restaurant", "/demo"]) {
    const { pack } = page({ where });
    assert.deepEqual(pack.tools().filter((t) => t.available()).map((t) => t.name), ["explain_this_page"], where);
  }
  const out = page({ where: "/today", signedIn: false });
  assert.deepEqual(out.pack.tools().filter((t) => t.available()).map((t) => t.name), ["explain_this_page"]);
  const today = page({ where: "/today" });
  assert.equal(today.tools.add_sensor.available(), false);
  assert.equal(today.tools.show_open_alerts.available(), true);
  const consolePage = page({ where: "/console", bridge: true });
  assert.equal(consolePage.tools.add_sensor.available(), true);
  delete globalThis.window;
});

// ---------------------------------------------------------------- reading

test("which units are warm: through the console's API with the person's own session, and nothing private", async () => {
  const { tools, calls } = page();
  const r = await tools.show_units_out_of_range.run({});
  assert.deepEqual(r.out_of_range_now.map((u) => u.unit), ["Kitchen walk-in"]);
  assert.deepEqual(r.heading_out_of_range.map((u) => u.unit), ["Bar freezer"]);
  assert.deepEqual(r.not_reporting.map((u) => u.unit), ["Prep fridge"]);
  assert.equal(calls[0].url, "/api/console/overview");
  assert.equal(calls[0].auth, "Bearer session-token");
  assert.doesNotMatch(JSON.stringify(r), /TEN-SECRET/);
  delete globalThis.window;
});

test("open alerts and what an alert means carry no person's name or number", async () => {
  const { tools } = page();
  const open = await tools.show_open_alerts.run({});
  assert.deepEqual(open.open_alerts.map((a) => a.alert_id), ["INC-000001"]);
  const meant = await tools.explain_alert.run({});
  assert.match(meant.meaning, /Nobody has said they're on it/);
  for (const text of [JSON.stringify(open), JSON.stringify(meant)]) {
    assert.doesNotMatch(text, /Dana|\+1555/);
  }
  delete globalThis.window;
});

test("is the food still OK: the careful rule of thumb, always with its not-a-ruling note", async () => {
  const { tools, calls } = page();
  const r = await tools.is_the_food_ok.run({});
  assert.equal(r.unit, "Kitchen walk-in");
  assert.match(r.not_a_ruling, /not a food safety decision/);
  assert.ok(calls.some((c) => c.url === "/api/disposition/WALKIN-1?hours=24"));
  assert.match(tools.is_the_food_ok.description, /never a food safety ruling/);
  delete globalThis.window;
});

// ---------------------------------------------------------------- confirm first

test("acknowledging waits for Allow: Cancel sends nothing, Allow posts through the console API", async () => {
  for (const [answer, posted] of [[false, 0], [true, 1]]) {
    const { pack, calls } = page();
    await pack.tools().find((t) => t.name === "show_open_alerts").run({}); // the card names the unit
    const { engine, asked, sent } = engineWith(pack, { confirmAnswers: [answer],
      replies: [{ toolCalls: [{ id: "a", name: "acknowledge_alert", args: { alert_id: "INC-000001" } }] }, { text: "ok" }] });
    await engine.send("I'm on it");
    assert.deepEqual(asked, ["Acknowledge the alert on “Kitchen walk-in” (you're on it; nobody else is called about it)"]);
    const posts = calls.filter((c) => c.method === "POST");
    assert.equal(posts.length, posted, String(answer));
    if (posted) {
      assert.equal(posts[0].url, "/api/voice/acknowledge/INC-000001");
      assert.equal(posts[0].auth, "Bearer session-token");
    } else {
      assert.match(toolResult(sent, "a"), /said no/);
    }
    delete globalThis.window;
  }
});

test("marking fixed and making a proof report also wait for Allow", async () => {
  const { pack, calls } = page();
  const { engine, asked } = engineWith(pack, { confirmAnswers: [false, false],
    replies: [{ toolCalls: [{ id: "f", name: "mark_alert_fixed", args: { alert_id: "INC-000001" } }] },
      { toolCalls: [{ id: "p", name: "make_proof_report", args: { month: "2026-10" } }] }, { text: "ok" }] });
  await engine.send("Close it and make the report");
  assert.equal(asked.length, 2);
  assert.match(asked[0], /^Mark the alert on .* as fixed/);
  assert.match(asked[1], /^Make a proof of monitoring report for 2026-10, for every location/);
  assert.equal(calls.filter((c) => c.method === "POST").length, 0);
  delete globalThis.window;
});

test("adding a sensor asks fridge or freezer, waits for Allow, and never hands the key to the AI", async () => {
  const { pack, added } = page({ where: "/console", bridge: true });
  const tool = pack.tools().find((t) => t.name === "add_sensor");
  assert.deepEqual(tool.parameters.properties.kind.enum, ["fridge", "freezer"]);
  assert.deepEqual(tool.parameters.required, ["location", "kind"]);
  assert.match(tool.description, /ask the person whether it is a fridge or a freezer/);
  const { engine, asked, sent } = engineWith(pack, { confirmAnswers: [true],
    replies: [{ toolCalls: [{ id: "s", name: "add_sensor", args: { location: "Bar freezer 2", kind: "freezer" } }] }, { text: "done" }] });
  await engine.send("Add a freezer for the bar");
  assert.deepEqual(asked, ["Add a freezer called “Bar freezer 2”; it alarms if it stays over 10°F"]);
  assert.deepEqual(added, [{ location: "Bar freezer 2", kind: "freezer" }]);
  assert.doesNotMatch(toolResult(sent, "s"), /ingest|clx_/);
  // Without a kind, the engine refuses before anything is asked or added.
  const again = page({ where: "/console", bridge: true });
  const second = engineWith(again.pack, { confirmAnswers: [true],
    replies: [{ toolCalls: [{ id: "s", name: "add_sensor", args: { location: "Bar" } }] }, { text: "Fridge or freezer?" }] });
  await second.engine.send("Add one for the bar");
  assert.deepEqual(second.asked, []);
  assert.deepEqual(again.added, []);
  delete globalThis.window;
});

// ---------------------------------------------------------------- never

test("never: plan changes, deleting, the on-call list, texts or calls are refused", async () => {
  const { pack, calls, tools } = page({ where: "/console", bridge: true });
  // No tool for any of them...
  for (const t of pack.tools()) {
    assert.doesNotMatch(t.name, /plan|delete|remove|contact|roster|call|sms|text|message|escalat|pay|subscri/, t.name);
  }
  // ...the engine would refuse one by its name anyway...
  for (const name of ["change_plan_upgrade", "delete_sensor", "remove_contact", "send_sms_to_team", "email_everyone", "make_payment"]) {
    assert.equal(Engine.checkTool({ name, kind: "change", describe: () => "", run: () => {} }), null, name);
  }
  // ...and the tools' own gate refuses every such address before a request is made.
  for (const [method, url] of [["POST", "/api/licenses/me/plan"], ["DELETE", "/api/contacts/CON-1"], ["PATCH", "/api/contacts/CON-1"],
    ["PUT", "/api/contacts/rotations"], ["POST", "/api/contacts/test-alert"], ["POST", "/api/voice/escalate/INC-000001"],
    ["DELETE", "/api/licenses/me/sensors/WALKIN-1"], ["POST", "/api/accounts/business/erase"], ["POST", "/api/contracts/cancel"],
    ["GET", "/api/contacts"], ["DELETE", "/api/proof-reports/PRF-1"], ["POST", "/api/proof-reports/PRF-1/revoke"]]) {
    assert.equal(pack.allowed(method, url), false, `${method} ${url}`);
  }
  // A model that asks for a tool that isn't there gets nothing run.
  const { engine, asked } = engineWith(pack, { confirmAnswers: [true],
    replies: [{ toolCalls: [{ id: "x", name: "change_plan", args: {} }, { id: "y", name: "send_text", args: {} }] }, { text: "I can't do that." }] });
  await engine.send("Upgrade my plan and text everyone");
  assert.deepEqual(asked, []);
  assert.equal(calls.length, 0);
  assert.ok(tools.acknowledge_alert);
  delete globalThis.window;
});

// 3.26.0 kept the robot off the logo by listing every header bar in this
// app's avoid list; core 1.4.0 fixes the cause (a phone tries its bottom
// corners first, a bar that stays put is busy, and a link it would cover
// is weighed). This runs the core's own choosePlace on a new business's
// phone screen with the avoid list as the config has it now, with no
// header bar in it: the robot stays at the bottom.
test("core 1.4.0: on a new business's phone screen the robot stays at the bottom, with no header bar in the avoid list", () => {
  const fs = require("node:fs");
  const M = require(core("pilot-mascot.js"));
  const config = fs.readFileSync(app("pilot-kryozyn.js"), "utf8");
  const list = config.slice(config.indexOf("avoid: ["), config.indexOf("],", config.indexOf("avoid: [")));
  const avoid = new Set(Array.from(list.matchAll(/"([^"]+)"/g), (m) => m[1]));
  // 3.38.0: the console's header is listed (the robot sat in it beside
  // °F at 375px with the alert bar showing); the other headers are not.
  for (const sel of [".top", ".demo-top"]) assert.ok(!avoid.has(sel), sel);
  assert.ok(avoid.has("#app > .nav"));
  const view = { width: 375, height: 812 };
  const size = { width: 56, height: 54 };
  const page = [
    { sel: "#first-day", box: { left: 30, top: 250, right: 345, bottom: 1400 } },
    { sel: ".checklist-acts", box: { left: 79, top: 690, right: 300, bottom: 726 } },
  ];
  // The logo and Sign in (links) at the top; the card's words stop short of
  // the side room, so the bottom corners have a few words at most.
  const links = [{ left: 16, top: 20, right: 42, bottom: 46, kind: "control" }, { left: 290, top: 14, right: 359, bottom: 52, kind: "control" }];
  const blocks = page.filter((p) => avoid.has(p.sel)).map((p) => p.box);
  const busy = (box) => links.some((w) => M.overlaps(box, w, 0));
  const cost = (box) => M.costOf(links.filter((w) => M.overlaps(box, w, 0)), box);
  for (const narrow of [true, undefined]) {
    const place = M.choosePlace({ corner: "br", view, size, gap: 12, blocks, hard: [], busy, cost, narrow,
      keep: { scale: 0.82, margin: 3 } });
    assert.equal(place.corner[0], "b", `robot went to ${place.corner}`);
  }
  // Even with every bottom corner busy, a phone never takes a top corner over the logo.
  const all = M.choosePlace({ corner: "br", view, size, gap: 12, blocks, hard: [], busy: () => true,
    cost: (box, c) => (c[0] === "t" ? 100 : 2000), narrow: true, keep: { scale: 0.82, margin: 3 } });
  assert.equal(all.corner[0], "b");
});

// ---------------------------------------------------------------- core 1.4.0: useful before a key

const fsx = require("node:fs");
const vmx = require("node:vm");
const Words = require(app("words.js"));
const CONFIG = fsx.readFileSync(app("pilot-kryozyn.js"), "utf8");
const PANEL = (() => { const sb = { window: {}, document: {}, URL }; vmx.runInNewContext(fsx.readFileSync(core("pilot-panel.js"), "utf8"), sb); return sb.window.PilotPanel; })();
const BASE = "https://kryozyn.example";
const DATA = { fromPrice: "59", starterUnits: "3", trialDays: "14", trialUnits: "5", trialCalls: "5", fridgeLimit: "41",
  freezerLimit: "10", offlineMinutes: "30", callAfterMinutes: "10", textWithinSeconds: "60", alertAfterFridge: "20",
  alertAfterFreezer: "30", alertAtOnceDegrees: "15" };

// A console overview as /api/console/overview gives it, with the server's
// `condition` on each unit. The people in it must never reach an answer.
const NOW = Date.parse("2026-11-20T10:20:00Z");
function overviewWith({ ack = false, fixed = false, none = false, quiet = false, waiting = false } = {}) {
  const sensors = [
    { sensor_id: "WALKIN-1", location_name: "Kitchen walk-in", last_temperature_display: 47, temperature_unit: "F",
      danger_above_display: 41, danger_below_display: null, alert_after_minutes: 20,
      condition: { state: "alert", too: "warm", out_since: "2026-11-20T09:55:00Z" } },
    { sensor_id: "FREEZER-1", location_name: "Bar freezer", last_temperature_display: 2, temperature_unit: "F",
      danger_above_display: 10, condition: { state: "ok" } },
    { sensor_id: "PREP-1", location_name: "Prep **fridge** [x](/evil)", last_temperature_display: 38, temperature_unit: "F",
      danger_above_display: 41, condition: { state: "offline" } },
  ];
  if (waiting) sensors.push({ sensor_id: "NEW-1", location_name: "Dessert fridge", last_temperature_display: null, condition: { state: "waiting_first" } });
  const live = { incident_id: "INC-000002", sensor_id: "WALKIN-1", state: ack ? "acknowledged" : "open", temperature_display: 47,
    peak_display: 48.5, temperature_unit: "F", opened_at: "2026-11-20T10:15:00Z", out_since: "2026-11-20T09:55:00Z",
    acknowledged_by: ack ? "Dana Reyes <dana@cafe.example>" : null, acknowledged_by_name: ack ? "Dana Reyes" : null,
    fix_reported_at: fixed ? "2026-11-20T10:18:00Z" : null, fix_reported_by_name: fixed ? "Dana Reyes" : null,
    breach_details: "Too warm: 47°F (limit 41°F).", sms_fanout: [{ contact_name: "Dana Reyes", to: "+15550100" }],
    dispatched_sms_text: "ALERT Kitchen walk-in: 47F. Dana Reyes" };
  const closed = { incident_id: "INC-000001", sensor_id: "PREP-1", state: "resolved", opened_at: "2026-11-19T08:00:00Z",
    out_since: "2026-11-19T07:40:00Z", out_until: "2026-11-19T08:10:00Z", resolved_at: "2026-11-19T08:10:00Z", peak_display: 44,
    back_to_normal_on_its_own: true, resolved_by_name: "Dana Reyes" };
  if (none || quiet) sensors[0].condition = { state: "ok" };
  return { temperature_unit: "F", sensors: quiet ? sensors.slice(0, 2) : sensors, incidents: none || quiet ? [closed] : [live, closed], site_wide_alerts: [] };
}

// The config on a page, with what that page's own bridge hands over.
// fetch is a trap: an answer that touched the network fails the test.
function mountAt(pathname, { data, signedIn = true, overview, did } = {}) {
  let given = null;
  const fetchTrap = () => { throw new Error("an answer called the network"); };
  const win = { Pilot: { mount: (c) => { given = c; } }, location: { pathname, href: BASE + pathname },
    localStorage: { getItem: (k) => (signedIn && k === "cyberlogix.session" ? "t" : null) }, CLXWords: Words, fetch: fetchTrap };
  if (pathname === "/console") {
    win.KryozynConsole = { signedIn: () => signedIn, tab: () => "Fridges", overview: () => (signedIn ? overview : null),
      // The console's own session, for "What Kryozyn did for you" only (3.33.0).
      api: async (url) => { if (url === "/api/console/did-for-you?period=week" && did) return did; throw new Error(`not mocked: ${url}`); } };
  }
  if (pathname === "/today") win.KryozynToday = { overview: () => overview, did: () => did || null };
  vmx.runInNewContext(CONFIG, { window: win, fetch: fetchTrap, XMLHttpRequest: fetchTrap, URL,
    document: { getElementById: () => ({ dataset: { ...NAMES, ...(data || DATA) } }), title: "Restaurants" } });
  return given;
}
const pills = (given, pathname) => PANEL.welcomePills(given.welcomePills, BASE + pathname);
// 3.33.0: "What does this alert mean?" is a suggestion shown while an alert
// is open, not a welcome pill.
const chipOf = (given, label) => given.chips(given.screen()).find((c) => c.label === label);
const meaningOf = (pathname, overview) => chipOf(mountAt(pathname, { overview }), "What does this alert mean?");
// Arrays made inside the vm are compared as plain data.
const J = (x) => JSON.parse(JSON.stringify(x));
const ask = (pill) => (typeof pill.answer === "function" ? pill.answer({ facts: {}, ready: false, label: pill.label }) : pill.answer);
const NO_PEOPLE = /Dana|Reyes|dana@|\+1555|\]\(\/evil/;

test("3.32.0: the visitor's pills: the price and the sensors answered from the real figures (with their pages linked), See it live a core link; the 3.26.0 click catcher is gone", () => {
  for (const pathname of ["/", "/pricing", "/help", "/help/sensors", "/for/restaurant", "/demo"]) {
    const p = pills(mountAt(pathname), pathname);
    assert.deepEqual(J(p.map((x) => x.label)), ["Will it work for my café?", "What does it cost?", "See it live", "What sensors do I need?", "Add my AI key"], pathname);
    const price = ask(p[1]);
    assert.ok(price.includes("**Starter $59 a month, up to 3 fridges or freezers**"), price);
    assert.ok(price.includes("**Growth $149 a month a location**") && price.includes("[See all prices](/pricing)"), price);
    assert.equal(p[2].link, `${BASE}/demo`);
    const sensors = ask(p[3]);
    assert.ok(sensors.startsWith("We don't sell sensors."), sensors);
    assert.ok(sensors.includes("[Which sensors work, brand by brand](/help/sensors)"), sensors);
    assert.equal(p[4].action, "settings");
    assert.equal(p[4].builtin, true);
  }
  assert.ok(!CONFIG.includes("LINK_PILLS"));
  assert.ok(!CONFIG.includes("stopImmediatePropagation"));
  assert.ok(!CONFIG.includes('closest(".pilot-pill")'));
  assert.equal(mountAt("/").answerMore, "Want more? Add your AI key to ask anything.");
});

test("1.4.0: 'Will it work for my café?' is a short answer with the real figures and links, no key needed", () => {
  const text = ask(pills(mountAt("/"), "/")[0]);
  for (const bit of ["41°F", "10°F", "20 minutes", "60 seconds", "$59 a month", "up to 3 fridges", "14 days, no card",
    "[See it live](/demo)", "[start a free trial](/signup)", "[See prices](/pricing)", "[which sensors work](/help/sensors)"]) {
    assert.ok(text.includes(bit), bit);
  }
  assert.ok(text.split("\n").length <= 8, "short");
  // Figures from the server's tag, not written into the config.
  assert.ok(ask(pills(mountAt("/", { data: { ...DATA, fromPrice: "65", fridgeLimit: "40" } }), "/")[0]).includes("$65 a month"));
});

test("1.4.0: 'Which fridges need me?' lists the units out of range or quiet from the page's own data, worst first, with no names of people", () => {
  for (const pathname of ["/console", "/today"]) {
    const p = pills(mountAt(pathname, { overview: overviewWith() }), pathname);
    // 3.36.0: a sensor has reported, so no "How do I connect a sensor?".
    assert.deepEqual(J(p.map((x) => x.label)), ["Which fridges need me?", "Is the food still OK?",
      "Who gets the texts?", "Do you phone us?", "Add my AI key"], pathname);
    const text = ask(p[0]);
    assert.match(text, /These 2 need you, worst first:\n- \*\*Kitchen walk-in\*\*/);
    assert.match(text, /- \*\*Kitchen walk-in\*\* \(47.0°F\): Too warm for /);
    assert.match(text, /- \*\*Prep fridge x\(\/evil\)\*\*: No signal|- \*\*Prep fridge x\(\/evil\)\*\*: We have not heard/);
    // 3.32.0: a fine unit is not in the list, but its temperature is under it.
    assert.ok(!/- \*\*Bar freezer/.test(text), "a fine unit is not listed");
    assert.ok(text.includes("The rest are fine: Bar freezer 2.0°F."), text);
    assert.ok(text.indexOf("Kitchen walk-in") < text.indexOf("Prep fridge"), "worst first");
    assert.match(text, /Nobody has answered an alert yet: press \*\*I'm on it\*\*/);
    assert.doesNotMatch(text, /\]\(\/evil\)/, "a unit's name can't add a link");
    assert.doesNotMatch(text, /Dana|Reyes|dana@|\+1555/);
  }
  // All fine: says so, with the count, in one line (3.41.0).
  assert.equal(ask(pills(mountAt("/console", { overview: overviewWith({ quiet: true }) }), "/console")[0]),
    "Nothing needs you right now: all 2 are within their limits.");
  // A unit still waiting for its sensor: the wizard.
  assert.match(ask(pills(mountAt("/console", { overview: overviewWith({ waiting: true }) }), "/console")[0]),
    /\*\*Dessert fridge\*\* is waiting for a first reading\. \[Connect a sensor\]\(\/console#connect\)/);
  // No units yet.
  assert.match(ask(pills(mountAt("/console", { overview: { sensors: [], incidents: [] } }), "/console")[0]),
    /no fridges or freezers added yet\. \[Connect your first sensor\]\(\/console#connect\)/);
});

test("1.4.0: 'What does this alert mean?' explains the open alert from its own record", () => {
  const open = ask(meaningOf("/console", overviewWith()));
  assert.match(open, /^\*\*Kitchen walk-in\*\* is too warm: 47.0°F, above its 41°F limit\./);
  assert.match(open, /It went out of range at .+ and has been out for .+; worst so far 48\.5°F\./);
  assert.match(open, /It stayed out longer than its 20-minute wait, so we alerted\./);
  // A reading far past the limit alerts at once: it never claims the wait passed.
  const fast = overviewWith();
  fast.incidents[0].opened_at = fast.incidents[0].out_since;
  fast.incidents[0].temperature_display = 57;
  assert.match(ask(meaningOf("/console", fast)),
    /It went far enough past its limit to alert at once, without its 20-minute wait\./);
  // Opened early but only 6°F over (3.41.0): never "far enough to alert
  // at once", and never that the wait passed.
  const early = overviewWith();
  early.incidents[0].opened_at = early.incidents[0].out_since;
  const said = ask(meaningOf("/console", early));
  assert.doesNotMatch(said, /alert at once|stayed out longer than its/);
  assert.match(open, /Nobody has answered yet\. Press \*\*I'm on it\*\* at the top of the page/);
  assert.match(open, /Check the door is shut/);
  assert.match(open, /\*\*Is the food still good\?\*\* button/);
  assert.doesNotMatch(open, NO_PEOPLE);
  // Someone has it: never their name.
  const ack = ask(meaningOf("/today", overviewWith({ ack: true })));
  assert.match(ack, /Someone on your team is on it\./);
  assert.doesNotMatch(ack, NO_PEOPLE);
  const fixed = ask(meaningOf("/today", overviewWith({ ack: true, fixed: true })));
  assert.match(fixed, /Someone on your team said it is fixed\. It closes by itself once it is back in range\./);
  // On /today the buttons are on the alert at the top of the page (3.38.0:
  // its card has no "Needs you now" heading under the alert's headline).
  assert.match(ask(meaningOf("/today", overviewWith())), /\*\*I'm on it\*\* on the alert at the top of the page/);
  // No alert open (3.33.0): no such suggestion, but typed it says so, and the last one in a line.
  assert.equal(meaningOf("/console", overviewWith({ none: true })), undefined);
  const none = typedAnswer("/console", "What does this alert mean?", { overview: overviewWith({ none: true }) });
  assert.match(none, /^No alert is open right now\. The last one: \*\*Prep fridge x\(\/evil\)\*\* was out of range for 30 minutes and came back on its own\. It is closed\./);
  assert.doesNotMatch(none, NO_PEOPLE);
});

test("1.4.0: 'Is the food still OK?' gives what was measured and the rule of thumb, always with its not-a-ruling note", () => {
  const text = ask(pills(mountAt("/console", { overview: overviewWith() }), "/console")[1]);
  assert.match(text, /\*\*Kitchen walk-in\*\* has been out of range for .+, at worst 48\.5°F, and it is not back yet\./);
  assert.match(text, /under 2 hours above the limit, cool it back down; 2 to 4 hours, use it straight away/);
  assert.match(text, /That is guidance, not a ruling: .+ your written food safety plan and local code decide\./);
  // Signed out: the rule and the note, and where to sign in; never a guess about their food.
  const out = ask(pills(mountAt("/console", { signedIn: false }), "/console")[1]);
  assert.match(out, /not a ruling/);
  assert.match(out, /\[sign in\]\(\/console\)/);
});

test("1.4.0: signed out, the console's answers say where to look instead of guessing; 'How do I connect a sensor?' opens the wizard", () => {
  const given = mountAt("/console", { signedIn: false });
  // The console's sign-in keeps the console's five (the page is the person's own).
  const p = pills(given, "/console");
  assert.match(ask(p[0]), /Sign in to see your fridges: \[sign in\]\(\/console\)/);
  // 3.32.0: an answer with the link, so a typed sensor question gets the right one too.
  assert.match(ask(p[2]), /\[Open Connect your sensor\]\(\/console#connect\)/);
  // Its suggestions need no key either: how to sign in, the reset page, the trial.
  const chips = PANEL.cleanChips(given.chips(), BASE + "/console");
  assert.match(ask(chips[0]), /email and password you signed up with, on \[the console\]\(\/console\)/);
  assert.equal(chips[1].link, `${BASE}/reset`);
  assert.equal(chips[2].link, `${BASE}/signup`);
  // The console follows "#connect" to "Connect your sensor", once.
  const js = fsx.readFileSync(app("console.js"), "utf8");
  const follow = js.slice(js.indexOf("function followConnectLink"), js.indexOf("// A link followed while the console is already open"));
  assert.ok(follow.includes('location.hash !== "#connect"'));
  assert.ok(follow.includes("history.replaceState(null, \"\", location.pathname + location.search);"));
  assert.ok(follow.includes('openConnect("");'));
  assert.ok(js.includes("  followConnectLink();\n  followPacketLink();\n  followInspectorLink().catch"), "and while the console is open");
  // The pages hand over what they loaded, and nothing else is fetched for it.
  assert.ok(js.includes("overview: () => (state.token ? state.data : null),"));
  const simple = fsx.readFileSync(app("simple.js"), "utf8");
  assert.ok(simple.includes("window.KryozynToday = Object.freeze({ overview: () => lastData,"));
});

test("1.4.0: page suggestions that are facts are answered at once; open questions stay AI prompts; every answer is local", () => {
  const kinds = (pathname, opts) => J(PANEL.cleanChips(mountAt(pathname, opts).chips(), BASE + pathname)
    .map((c) => (c.link ? "link" : c.answer !== undefined ? "answer" : "ai")));
  assert.deepEqual(kinds("/"), ["answer", "answer", "answer"]);
  assert.deepEqual(kinds("/pricing"), ["answer", "answer", "answer"]);
  assert.deepEqual(kinds("/help"), ["answer", "answer", "answer"]);
  assert.deepEqual(kinds("/help/sensors"), ["ai", "answer", "answer"]);
  assert.deepEqual(kinds("/for/restaurant"), ["ai", "ai", "answer"]);
  assert.deepEqual(kinds("/demo"), ["answer", "answer", "link"]);
  // "Log for the inspector" goes straight to the log once signed in (3.30.0).
  // 3.32.0: "What Kryozyn did for you" in place of the proof report, which needed a key.
  // 3.33.0: with an alert open, what it means and "It's fixed now" first; with none, the
  // week's summary (quoted, an answer now) and the power.
  assert.deepEqual(kinds("/console", { overview: overviewWith() }), ["answer", "answer", "link"]);
  assert.deepEqual(kinds("/console", { overview: overviewWith({ none: true }) }), ["link", "answer", "answer"]);
  const texts = {};
  for (const pathname of ["/", "/pricing", "/help", "/help/sensors", "/for/restaurant", "/demo"]) {
    for (const c of mountAt(pathname).chips()) if (c.answer) texts[c.label] = ask(c);
  }
  assert.match(texts["Will a door left open wake us?"], /^No\. A door opened for a minute doesn't alert anybody/);
  assert.match(texts["Will a door left open wake us?"], /20 minutes for a food fridge, 30 for a freezer/);
  assert.match(texts["Wi-Fi or power goes off?"], /after 30 minutes with nothing from a sensor everyone on call is texted/);
  assert.match(texts["Do you phone at night?"], /^Yes, on plans with calls\. If nobody answers the alert text within 10 minutes, Kryozyn phones the people on call/);
  assert.match(texts["What's in the trial?"], /14 days for up to 5 sensors, needs no card, and includes 5 alert calls/);
  assert.match(texts["Set up step by step"], /^1\. Buy one sensor/);
  for (const [label, text] of Object.entries(texts)) {
    assert.ok(text.length > 40 && text.length < 1200, label);
    // Links only to this site's own pages.
    for (const m of text.matchAll(/\]\(([^)]+)\)/g)) assert.match(m[1], /^\/[a-z]/, `${label}: ${m[1]}`);
  }
});

test("3.28.0: 'It's fixed now' points at the real button, and never closes anything itself", () => {
  const fixedChip = (pathname, opts) => J(mountAt(pathname, opts).chips()).find((c) => c.label === "It's fixed now");
  for (const pathname of ["/console", "/today"]) {
    const given = mountAt(pathname, { overview: overviewWith() });
    const chip = given.chips().find((c) => c.label === "It's fixed now");
    assert.equal(chip.prompt, undefined, "not an AI prompt any more");
    const text = ask(chip);
    assert.match(text, /^Press \*\*It's fixed\*\* on \*\*Kitchen walk-in\*\*/);
    assert.match(text, pathname === "/today" ? /on the alert at the top of the page/ : /top of the page/);
    assert.match(text, /was the food kept or thrown away/);
    assert.doesNotMatch(text, NO_PEOPLE);
  }
  // 3.33.0: with no alert open it isn't offered at all.
  assert.equal(mountAt("/today", { overview: overviewWith({ none: true }) }).chips()
    .find((c) => c.label === "It's fixed now"), undefined);
  assert.equal(fixedChip("/console", { overview: overviewWith() }).prompt, undefined);
});

test("3.30.0: 'Log for the inspector' links straight to the log once signed in, and a visitor gets Sign in", () => {
  const chip = (pathname, opts) => PANEL.cleanChips(mountAt(pathname, opts).chips(), BASE + pathname)
    .find((c) => /og for the inspector/.test(c.label));
  // From /today: the full console, opened on the last week's log.
  assert.equal(chip("/today", { overview: overviewWith() }).link, `${BASE}/console?view=full#inspector-log?open=1`);
  // On the console itself only the # changes, so nothing reloads.
  assert.equal(chip("/console", { overview: overviewWith() }).link, `${BASE}/console#inspector-log?open=1`);
  // A visitor still gets the way in.
  assert.match(ask(mountAt("/help").chips().find((c) => c.label === "A log for the inspector")), /\[Sign in\]\(\/console\)/);
});

test("3.28.0: 'Which plan for a café?' answers with Starter first, from the server's figures", () => {
  const chip = mountAt("/pricing").chips().find((c) => c.label === "Which plan for a café?");
  const text = ask(chip);
  assert.match(text, /^\*\*One café or shop with up to 3 fridges or freezers: Starter, \$59 a month\.\*\*/);
  assert.match(text, /Growth, \$149 a month a location/);
  assert.ok(ask(mountAt("/pricing", { data: { ...DATA, fromPrice: "65", restaurantPrice: "160" } }).chips()
    .find((c) => c.label === "Which plan for a café?")).includes("$65 a month"));
});

test("3.28.0: 'What does this alert mean?' says 'we have phoned' only when a call really went out", () => {
  const meaning = (extra) => {
    const o = overviewWith();
    Object.assign(o.incidents[0], extra);
    return ask(meaningOf("/console", o));
  };
  assert.match(meaning({ voice_escalated_at: "2026-11-20T10:25:00Z", phoned: true }), /Nobody has answered yet, and we have phoned\./);
  const off = meaning({ voice_escalated_at: "2026-11-20T10:25:00Z", phoned: false, calls_off: true });
  assert.doesNotMatch(off, /we have phoned/);
  assert.match(off, /We would have phoned the list now, but calls are off on this server/);
  const failed = meaning({ voice_escalated_at: "2026-11-20T10:25:00Z", phoned: false, calls_off: false });
  assert.match(failed, /the call did not go through/);
  assert.doesNotMatch(meaning({}), /phoned/);
});

test("3.28.0: the robot keeps off /today's fridge rows and temperatures and the demo cards", () => {
  const avoid = J(mountAt("/today", { overview: overviewWith() }).avoid);
  // 3.42.0: on /today the alert's own words and buttons, not its whole
  // cards, so the robot's handle has an edge to peek in at on a phone.
  for (const sel of [".thing-temp", ".thing-name", ".demo-unit-temp", ".demo-unit-top", ".need-what", ".need-when", ".need-acts", "#verdict > *"]) {
    assert.ok(avoid.includes(sel), sel);
  }
  for (const sel of ["#needs", ".need", "#verdict"]) assert.ok(!avoid.includes(sel), sel);
  // The console keeps its whole cards listed.
  const consoleAvoid = J(mountAt("/console", { overview: overviewWith() }).avoid);
  for (const sel of ["#needs", ".need", "#verdict", "#needs-wrap"]) assert.ok(consoleAvoid.includes(sel), sel);
});

// ---------------------------------------------------------------- core 1.4.1 (3.29.0)

test("1.4.1: after one of this app's own answers the welcome's pills come back underneath, without the one just asked", () => {
  const row = (pathname, asked, keyPill) => {
    const given = mountAt(pathname, { overview: overviewWith() });
    const list = PANEL.ownPills(given.welcomePills, BASE + pathname);
    const msgs = [{ role: "user", text: asked }, { role: "assistant", text: "…", local: true }];
    return J(PANEL.againPills(msgs, list, keyPill)).map((p) => p.label);
  };
  // The console and /today: Which fridges need me? leaves the other three (the key pill
  // waits while the "Want more?" line right above offers the key). 3.36.0: the
  // welcome of the situation (a sensor reports, an alert is live).
  for (const pathname of ["/console", "/today"]) {
    assert.deepEqual(row(pathname, "Which fridges need me?"), ["Is the food still OK?", "Who gets the texts?", "Do you phone us?"], pathname);
    // Under a later answer (no "Want more?" there: once per chat), the key pill comes back.
    assert.deepEqual(row(pathname, "Is the food still OK?", true),
      ["Which fridges need me?", "Who gets the texts?", "Do you phone us?", "Add my AI key"], pathname);
  }
  // A visitor: Will it work for my café? leaves the three links.
  for (const pathname of ["/", "/pricing", "/help/sensors", "/demo"]) {
    assert.deepEqual(row(pathname, "Will it work for my café?"), ["What does it cost?", "See it live", "What sensors do I need?"], pathname);
  }
});

test("1.4.1: the console's own answers never reach the AI provider once a key is added; the stand-in takes their place", async () => {
  const given = mountAt("/console", { overview: overviewWith() });
  const text = ask(chipOf(given, "What does this alert mean?"));
  assert.ok(text.includes("Kitchen walk-in"), text);
  // Shown with no key and kept in the chat, then a key is added and the next question is asked.
  const store = Store.create({ prefix: "c141", local: memoryStorage(), session: memoryStorage() });
  store.saveChat([{ role: "user", text: "What does this alert mean?" }, { role: "assistant", text, local: true },
    { role: "note", kind: "more", text: given.answerMore }]);
  store.saveSettings({ provider: "anthropic", key: "test-key", model: "test-model", maxTokens: 0 });
  const sent = [];
  const providers = { ...Providers, chat: async (st, req) => { sent.push(req); return { raw: {}, toolCalls: [], stop: "done", text: "Sure." }; } };
  const engine = Engine.create({ store, providers, host: { confirm: async () => false, plan: async () => "cancel" },
    config: { appName: "Kryozyn", tools: () => [], kidView: () => false, screen: () => ({ name: "Console" }) } });
  await engine.send("And what should I check first?");
  assert.equal(sent.length, 1);
  const wire = JSON.stringify(sent[0].messages);
  assert.ok(!wire.includes("Kitchen walk-in") && !wire.includes("47"), wire);
  assert.ok(wire.includes(Engine.LOCAL_STANDIN));
  assert.ok(wire.includes("What does this alert mean?") && wire.includes("And what should I check first?"));
  // Still on screen, unchanged.
  assert.equal(store.loadChat().find((m) => m.local).text, text);
});

test("1.4.1: the config gives the one-line key note, leaves hideWhenCrowded at its default, keeps the temperatures and status labels in avoid, and leaves the search box and tabs to the core", () => {
  for (const pathname of ["/", "/console", "/today"]) {
    const given = mountAt(pathname, { overview: overviewWith() });
    assert.equal(given.keyNoteShort, "AI keys are for grown-ups (18+).");
    assert.ok(given.keyNote.length > given.keyNoteShort.length);
    assert.equal("hideWhenCrowded" in given, false, "the core's default: hide rather than cover on a phone");
    const avoid = J(given.avoid);
    for (const sel of [".thing-temp", ".thing-word", ".sensor-top", ".demo-unit-temp", ".demo-word", ".badge", "#needs-bar"]) {
      assert.ok(avoid.includes(sel), sel);
    }
    // The console's search box and tabs are left to the core (below).
    assert.ok(!avoid.includes(".finder") && !avoid.includes(".f-tab"));
  }
  assert.ok(!CONFIG.includes("hideWhenCrowded:"));
});

// At 320px with an open alert, the pinned "Needs you now" bar fills the top
// and the console's search box crosses the bottom (measured in Chromium at
// y=500: bar 0,0-320,124, search box 30,562-290,663). Core 1.4.0 tucked the
// robot over the search box's end; core 1.4.1 with this app's avoid list,
// which no longer names the search box or tabs, hides it there instead.
test("1.4.1: the 320px console with an open alert: the robot hides rather than tuck over the search box, with no .finder in avoid", () => {
  const M = require(core("pilot-mascot.js"));
  const avoid = J(mountAt("/console", { overview: overviewWith() }).avoid);
  const view = { width: 320, height: 640 };
  const size = { width: 56, height: 54 };
  const keep = { scale: 0.82, margin: 3 };
  const bar = { left: 0, top: 0, right: 320, bottom: 124 };
  const finder = { left: 30, top: 562, right: 290, bottom: 663, kind: "control" };
  // Only what the config lists is a block; the search box is the core's own find (a box: a control).
  const blocks = avoid.includes("#needs-bar") ? [bar] : [];
  assert.equal(blocks.length, 1);
  const found = [{ ...bar, kind: "pinned" }, finder];
  const under = (box) => found.filter((w) => M.overlaps(box, w, 0));
  const o = { corner: "bl", view, size, gap: 12, keep, blocks, hard: [], narrow: true,
    busy: (box) => under(box).length > 0, cost: (box) => M.costOf(under(box), box) };
  const place = M.choosePlace({ ...o, away: true });
  assert.equal(place.hide, true, JSON.stringify(place));
  assert.equal(place.corner[0], "b", "hidden, it keeps a bottom corner");
});

// ---------------------------------------------------------------- core 1.4.2 (3.31.0)

// What the window compares a typed question with, with no key (core 1.4.2's
// typedHere): the welcome's pills and this page's suggestions.
const typedItems = (given, pathname) => PANEL.ownPills(given.welcomePills, BASE + pathname)
  .concat(PANEL.cleanChips(given.chips(given.screen()), BASE + pathname));
const typed = (pathname, words, opts) => {
  const given = mountAt(pathname, { overview: overviewWith(), ...opts });
  const hit = PANEL.matchTyped(words, typedItems(given, pathname));
  return hit ? hit.label : null;
};

test("1.4.2: with no key, a typed question finds this app's own answer on the console, /today and the public pages", () => {
  for (const pathname of ["/console", "/today"]) {
    for (const [words, want] of [["Is my walk-in fridge OK?", "Which fridges need me?"], ["why is my freezer warm", "Which fridges need me?"],
      ["What does the alert mean?", "What does this alert mean?"], ["Is the food safe?", "Is the food still OK?"],
      ["Should I throw the food away?", "Is the food still OK?"],
      ["Where is the inspector log?", "Log for the inspector"], ["How do I close this alert?", "It's fixed now"], ["fridge", "Which fridges need me?"]]) {
      assert.equal(typed(pathname, words), want, `${pathname}: ${words}`);
    }
    // 3.39.0: adding one more points to Add a fridge or freezer (it was the
    // connect answer, for the last unit added).
    assert.match(typedAnswer(pathname, "How do I add a new fridge?"), /^Press \*\*Add a fridge or freezer\*\* in the console/, pathname);
  }
  // Signed out on the console: its own sign-in suggestions.
  assert.equal(typed("/console", "How do I sign in?", { signedIn: false }), "How do I sign in?");
  assert.equal(typed("/console", "I forgot my password", { signedIn: false }), "I forgot my password");
  // A visitor.
  for (const pathname of ["/", "/pricing", "/help", "/help/sensors", "/for/restaurant", "/demo"]) {
    for (const [words, want] of [["Will it work for my restaurant?", "Will it work for my café?"], ["Is it good for a coffee shop?", "Will it work for my café?"],
      ["How much does it cost?", "What does it cost?"], ["How much is it?", "What does it cost?"], ["Can I see a demo?", "See it live"],
      ["What sensor should I buy?", "What sensors do I need?"]]) {
      assert.equal(typed(pathname, words), want, `${pathname}: ${words}`);
    }
  }
  // Each page's own facts.
  assert.equal(typed("/", "What if the wifi goes down?"), "Wi-Fi or power goes off?");
  assert.equal(typed("/", "Will opening the door set off the alarm?"), "Will a door left open wake us?");
  assert.equal(typed("/", "Do you call at night?"), "Do you phone at night?");
  assert.equal(typed("/pricing", "Which plan for my cafe?"), "Which plan for a café?");
  assert.equal(typed("/pricing", "Do I need a card for the trial?"), "What's in the trial?");
  assert.equal(typed("/help", "My fridge is too warm, what do I do?"), "A fridge is warm: what now?");
  assert.equal(typed("/help/sensors", "How do I set up a sensor?"), "Set up step by step");
  assert.equal(typed("/demo", "What am I seeing?"), "What am I seeing?");
});

test("1.4.2: anything else typed with no key still goes to key settings: no guess, and \"does this\" never picks the alert's answer", () => {
  const never = ["Write me a poem about fridges", "What's the weather like?", "Tell me a joke", "ok", "Is it ok?", "Who made you?",
    "What can you do?", "How do I install a fridge sensor with my wifi router?", "Explain this page", "Add my AI key", "", "   ",
    // Core 1.4.2 counts "does" and "this" (as "doe" and "thi"); both are in "What does this alert mean?".
    "Does this work for a bakery?", "Does this work with my sensor?", "What does this button do?", "What does this page do?", "this"];
  for (const pathname of ["/console", "/today", "/", "/pricing", "/help", "/help/sensors", "/for/restaurant", "/demo"]) {
    for (const words of never) {
      const got = typed(pathname, words);
      if (pathname === "/" || pathname === "/pricing" || pathname === "/help" || pathname === "/help/sensors"
        || pathname === "/for/restaurant" || pathname === "/demo") {
        // A visitor asking if it works for a bakery gets the café answer: that one is right.
        if (words === "Does this work for a bakery?") { assert.equal(got, "Will it work for my café?", pathname); continue; }
        if (words === "Does this work with my sensor?") { assert.equal(got, "What sensors do I need?", pathname); continue; }
      }
      // 3.33.0: on the person's own pages too, the sensors answer (3.36.0: through
      // whichever item carries its words, the connect pill being gone once a sensor reports).
      if (words === "Does this work with my sensor?") { assert.match(typedAnswer(pathname, words), /^Most likely, yes\./, pathname); continue; }
      assert.equal(got, null, `${pathname}: ${words}`);
    }
  }
  // Every answer or link this app gives with no key carries its own words, and none is a prompt or a built-in.
  for (const [pathname, signedIn] of [["/console", true], ["/console", false], ["/today", true], ["/", false], ["/pricing", false],
    ["/help", false], ["/help/sensors", false], ["/for", false], ["/for/restaurant", false], ["/demo", false]]) {
    const given = mountAt(pathname, { overview: overviewWith(), signedIn });
    for (const item of typedItems(given, pathname)) {
      const plain = Boolean(item.link || item.answer !== undefined);
      assert.equal(Boolean(item.match && item.match.length), plain, `${pathname}: ${item.label}`);
    }
  }
});

test("1.4.2: on the console and /today the suggestion after the page's own is this app's summary, with no key and nothing sent; the public pages keep the core's (key only)", () => {
  for (const pathname of ["/console", "/today"]) {
    const given = mountAt(pathname, { overview: overviewWith() });
    const chip = PANEL.screenChip(given.screenChip, false, BASE + pathname);
    assert.equal(chip.label, "Sum up my fridges");
    assert.equal(typeof chip.answer, "function");
    assert.equal(chip.prompt, undefined, "never sent to the AI");
    // With a key too, the same (the core's "Summarise this" would send the screen).
    assert.equal(PANEL.screenChip(given.screenChip, true, BASE + pathname).label, "Sum up my fridges");
    const text = ask(chip);
    assert.ok(text.startsWith("**3 fridges and freezers:** 1 fine, 2 need you."), text);
    assert.ok(text.includes("1 alert is open, and nobody has answered it."), text);
    assert.ok(text.includes("Ask **Which fridges need me?** for each one."), text);
    assert.doesNotMatch(text, NO_PEOPLE);
    // Someone on it, and a quiet day.
    const ack = ask(PANEL.screenChip(mountAt(pathname, { overview: overviewWith({ ack: true }) }).screenChip, false, BASE + pathname));
    assert.ok(ack.includes("1 alert is open, and someone is on it."), ack);
    assert.doesNotMatch(ack, NO_PEOPLE);
    const quiet = ask(PANEL.screenChip(mountAt(pathname, { overview: overviewWith({ quiet: true }) }).screenChip, false, BASE + pathname));
    assert.equal(quiet, "**2 fridges and freezers:** 2 fine.\n\nNo alert is open.\n\nNothing needs you right now.");
    // Not loaded yet, or signed out: where to look instead.
    const out = ask(PANEL.screenChip(mountAt(pathname, { signedIn: false }).screenChip, false, BASE + pathname));
    assert.match(out, /sign in/i);
  }
  for (const pathname of ["/", "/pricing", "/help", "/demo"]) {
    const given = mountAt(pathname);
    assert.equal(given.screenChip, undefined);
    assert.equal(PANEL.screenChip(given.screenChip, false, BASE + pathname), null, "no suggestion that only asks for a key");
    assert.equal(PANEL.screenChip(given.screenChip, true, BASE + pathname).label, "Summarise this");
  }
});

test("1.4.2: the config leaves the handle and typed matching on and sets no core stopgap", () => {
  for (const pathname of ["/", "/console", "/today"]) {
    const given = mountAt(pathname, { overview: overviewWith() });
    // Not the logo: on /today with an alert at 320px the handle's only free spot on the first screen is beside it.
    for (const sel of [".brand", ".brand-mark", ".brand-name"]) assert.ok(!J(given.avoid).includes(sel), sel);
    assert.equal("handle" in given, false, "the handle at the screen's edge stays on (the core's default)");
    assert.equal("matchTyped" in given, false, "typed questions are matched (the core's default)");
    assert.equal("hideWhenCrowded" in given, false);
  }
  assert.ok(!CONFIG.includes("--pilot-keep-d"));
});

// ---------------------------------------------------------------- 3.32.0: built-in answers on every page

const typedAnswer = (pathname, words, opts) => {
  const given = mountAt(pathname, { overview: overviewWith(), ...opts });
  const hit = PANEL.matchTyped(words, typedItems(given, pathname));
  if (!hit) return null;
  return hit.link ? `LINK ${hit.link}` : String(typeof hit.answer === "function" ? hit.answer({ facts: {}, ready: false, label: words }) : hit.answer);
};
const EVERY = ["/console", "/today", "/", "/pricing", "/help", "/help/sensors", "/for/restaurant", "/demo"];

test("3.32.0: the price, the sensors and the café plan are answered on every page, from the real figures, with no key", () => {
  for (const pathname of EVERY) {
    const price = typedAnswer(pathname, "How much does it cost?");
    assert.ok(price && price.includes("Starter $59 a month, up to 3 fridges or freezers"), `${pathname}: ${price}`);
    assert.ok(typedAnswer(pathname, "how much is starter").includes("Starter $59 a month"), pathname);
    const sensors = typedAnswer(pathname, "Do you sell sensors?");
    assert.ok(sensors && sensors.startsWith("We don't sell sensors."), `${pathname}: ${sensors}`);
    assert.ok(typedAnswer(pathname, "What sensor should I buy?").startsWith("We don't sell sensors."), pathname);
    const plan = typedAnswer(pathname, "Which plan for my café?");
    assert.ok(plan && plan.includes("Starter, $59 a month"), `${pathname}: ${plan}`);
  }
  // The figures are the server's, not written into the config.
  const given = mountAt("/console", { data: { ...DATA, fromPrice: "65", starterUnits: "4" }, overview: overviewWith() });
  const hit = PANEL.matchTyped("How much does it cost?", typedItems(given, "/console"));
  assert.ok(hit.answer({ label: "How much does it cost?" }).includes("Starter $65 a month, up to 4 fridges or freezers"));
});

test("3.32.0: 'is my fridge OK' gives temperatures, the unit asked about first, and says which kind isn't there", () => {
  const ok = { temperature_unit: "F", incidents: [], site_wide_alerts: [], sensors: [
    { sensor_id: "W-1", location_name: "Walk-in fridge", unit_kind: "fridge", last_temperature_display: 37.9, temperature_unit: "F", condition: { state: "ok" } },
    { sensor_id: "P-1", location_name: "Prep fridge", unit_kind: "fridge", last_temperature_display: 38.4, temperature_unit: "F", condition: { state: "ok" } }] };
  for (const pathname of ["/console", "/today"]) {
    const walk = typedAnswer(pathname, "Is my walk-in fridge OK?", { overview: ok });
    assert.ok(walk.startsWith("**Walk-in fridge** 37.9°F, fine."), walk);
    // The rest in one line (3.41.0), not listed.
    assert.ok(walk.includes("Nothing needs you right now: the other one is within its limits too."), walk);
    const freezer = typedAnswer(pathname, "How is the freezer?", { overview: ok });
    assert.ok(freezer.startsWith("You haven't added a freezer. [Connect one](/console#connect)"), freezer);
    // Pressed, with nothing to do: one line (3.41.0), not every unit.
    const pressed = ask(pills(mountAt(pathname, { overview: ok }), pathname)[0]);
    assert.equal(pressed, "Nothing needs you right now: all 2 are within their limits.");
    assert.ok(!pressed.includes("haven't added"), pressed);
  }
});

test("3.32.0: a typed question gets the closest answer, whichever pill the core matched; the misses still miss", () => {
  // Matched to "It's fixed now" (it carries the words), answered by the closest: calls, power.
  assert.match(typedAnswer("/console", "Do you call at night?"), /^Yes, on plans with calls/);
  assert.match(typedAnswer("/today", "What if the wifi goes down?"), /^Sensors send their readings over your internet/);
  // On the person's own pages a warm fridge is theirs, not the visitor's explainer.
  assert.match(typedAnswer("/console", "My fridge is too warm, what do I do?"), /need you|within their limits/);
  assert.match(typedAnswer("/help", "My fridge is too warm, what do I do?"), /^When a fridge stays out of range/);
  for (const pathname of EVERY) {
    for (const words of ["Write me a poem about fridges", "What's the weather like?", "What does this button do?", "Tell me a joke"]) {
      assert.equal(typedAnswer(pathname, words), null, `${pathname}: ${words}`);
    }
  }
});

test("3.32.0: the food question's line says when it went out, in the reader's clock", () => {
  const ov = overviewWith({ quiet: true });
  ov.incidents = [{ incident_id: "INC-3", sensor_id: "WALKIN-1", state: "resolved", resolved_at: "2026-11-20T10:00:00Z",
    food_question: { word: "food", at_risk: false, words: "Cold again after 2 minutes out", out_since: "2026-11-20T09:58:00Z", ask_on_today: true } }];
  const text = ask(pills(mountAt("/today", { overview: ov }), "/today")[0]);
  assert.ok(text.includes(`Cold again after 2 minutes out (since ${Words.clock("2026-11-20T09:58:00Z")})`), text);
});

// ---------------------------------------------------------------- core 1.4.3 (3.32.1)

const PAGES_143 = [["/console", true], ["/console", false], ["/today", true], ["/", false], ["/pricing", false], ["/help", false],
  ["/help/sensors", false], ["/for", false], ["/for/restaurant", false], ["/demo", false]];
const answerOf = (item, label) => (item.link ? `LINK ${item.link}`
  : String(typeof item.answer === "function" ? item.answer({ facts: {}, ready: false, label }) : item.answer));

test("1.4.3: every pill's and suggestion's own words, typed, get that item and the answer it gives when pressed", () => {
  let checked = 0;
  for (const [pathname, signedIn] of PAGES_143) {
    const given = mountAt(pathname, { overview: overviewWith(), signedIn });
    const items = typedItems(given, pathname);
    for (const item of items.filter((x) => x.link || x.answer !== undefined)) {
      const pressed = answerOf(item, item.label);
      for (const words of [item.label, item.label.toLowerCase().replace(/[?’'!.:]/g, ""), ` ${item.label.toUpperCase()} `]) {
        const hit = PANEL.matchTyped(words, items);
        assert.ok(hit && PANEL.sameItem(hit, item), `${pathname}: "${words}" -> ${hit && hit.label}`);
        // The routed answer treats its own label typed as a press (3.32.0 routed only an exact match).
        assert.equal(answerOf(hit, words), pressed, `${pathname}: "${words}"`);
        checked += 1;
      }
    }
  }
  assert.ok(checked >= 90, checked);
  // Accents aside, for this app's own answer too.
  const given = mountAt("/", {});
  const cafe = PANEL.ownPills(given.welcomePills, `${BASE}/`).find((p) => p.label === "Will it work for my café?");
  assert.equal(answerOf(cafe, "will it work for my cafe"), answerOf(cafe, cafe.label));
});

test("1.4.3: the \"does/this\" workaround is gone; \"does\" and \"this\" alone never pick an answer, the labels still do", () => {
  assert.doesNotMatch(CONFIG, /"doe thi"|\bdoe thi\b/);
  const match = CONFIG.slice(CONFIG.indexOf("const MATCH = {"), CONFIG.indexOf("const TOPICS = ["));
  assert.doesNotMatch(match, /"does"|"this"/);
  for (const pathname of ["/console", "/today"]) {
    for (const words of ["What does this button do?", "How does this work?", "What is this?", "Does it?"]) {
      assert.equal(typed(pathname, words), null, `${pathname}: ${words}`);
    }
    // 3.33.0: "work" and "sensor" are two words of the sensors answer.
    assert.match(typedAnswer(pathname, "Does this work with my sensor?"), /^Most likely, yes\./);
    assert.equal(typed(pathname, "What does this alert mean?"), "What does this alert mean?");
    assert.equal(typed(pathname, "what does the alert mean"), "What does this alert mean?");
    assert.equal(typed(pathname, "Is my walk-in fridge OK?"), "Which fridges need me?");
  }
  for (const pathname of ["/", "/pricing", "/help", "/demo"]) {
    assert.equal(typed(pathname, "How much does it cost?"), "What does it cost?", pathname);
    assert.ok(typedAnswer(pathname, "How much does it cost?").includes("Starter $59 a month"), pathname);
  }
});

test("1.4.3: with no key, a question nothing fits gets the core's note in the chat with this page's own answers under it, never a prompt", () => {
  for (const [pathname, signedIn] of PAGES_143) {
    const given = mountAt(pathname, { overview: overviewWith(), signedIn });
    // 3.37.0: this app's own note, for the question that missed; with no
    // question being sent, nothing, so the core's own words.
    assert.equal(given.noKeyReply, "", "no question, no note of this app's own");
    const pillsHere = PANEL.ownPills(given.welcomePills, BASE + pathname);
    const chipsHere = PANEL.cleanChips(given.chips(given.screen()), BASE + pathname);
    const row = Array.from(PANEL.noKeyItems(pillsHere, chipsHere, true));
    assert.equal(row[row.length - 1].label, "Add my AI key", pathname);
    assert.ok(row.length >= 3 && row.length <= PANEL.AGAIN_MOST + 1, `${pathname}: ${row.length}`);
    for (const item of row.slice(0, -1)) assert.ok(item.link || item.answer !== undefined, `${pathname}: ${item.label}`);
    assert.equal(new Set(row.map((x) => x.label)).size, row.length, `${pathname}: each once`);
    assert.equal(PANEL.noKeyText(given.noKeyReply, true, row.length > 1),
      "I need an AI key to answer that one. Here's what I can help with right now:");
    assert.equal(PANEL.matchTyped("Write me a poem about fridges", typedItems(given, pathname)), null, pathname);
  }
  // On the console, signed in: the live suggestions first, then the welcome's answers.
  const given = mountAt("/console", { overview: overviewWith() });
  const row = J(PANEL.noKeyItems(PANEL.ownPills(given.welcomePills, `${BASE}/console`), PANEL.cleanChips(given.chips(given.screen()), `${BASE}/console`), true));
  assert.deepEqual(row.map((x) => x.label), ["What does this alert mean?", "It's fixed now", "Log for the inspector",
    "Which fridges need me?", "Is the food still OK?", "Who gets the texts?", "Add my AI key"]);
});

test("3.32.1: the unit asked about, when it needs you, ends its line with one full stop", () => {
  const ov = overviewWith();
  ov.sensors[0].condition = { state: "alert", too: "warm", out_since: "2026-11-20T09:55:00Z" };
  for (const pathname of ["/console", "/today"]) {
    const text = typedAnswer(pathname, "Is my kitchen walk-in OK?", { overview: ov });
    assert.ok(text.startsWith("**Kitchen walk-in** (47.0°F): "), text);
    assert.doesNotMatch(text, /\.\.(\s|$)/, text);
    assert.match(text.split("\n")[0], /[.!?]$/, text);
  }
});

// ---------------------------------------------------------------- 3.33.0 (round 11)

const ROUND11 = [
  ["Does this work with my sensor?", /^Most likely, yes\. Kryozyn works with any wireless sensor that can send its readings to a web address/],
  ["Does it work with Monnit?", /^Yes, \*\*Monnit\*\* works: .+\[The Monnit guide\]\(\/help\/sensors#monnit\)/],
  ["does it work with sensorpush", /^Yes, \*\*SensorPush\*\* works/],
  ["how do I add my chef to get texts", /\*\*Who gets alerts\*\* in the console \(the \*\*Team\*\* tab\): .*name and .*mobile number/],
  ["What if the power goes out?", /^Sensors send their readings over your internet\. If the power or the Wi-Fi goes off/],
  ["what happens if the wifi goes down", /^Sensors send their readings over your internet/],
  ["How much for a pharmacy?", /^\*\*Pharmacies: \$199 a month per pharmacy\*\*, for up to 10 fridges and freezers there; each one past that is \$15 a month\./],
  ["pharmacy price", /^\*\*Pharmacies: \$199 a month per pharmacy\*\*/],
  ["How do I cancel my subscription?", /^In the console: \*\*Money\*\* tab, \*\*Pause or cancel\*\*, then \*\*Cancel my plan\*\*\./],
  ["cancel subscription", /^In the console: \*\*Money\*\* tab/],
  ["Is my data safe?", /^Yes\. What we keep: .+\n- Nothing is sold or used to train anything\.[\s\S]+\[Read the privacy statement\]\(\/legal#privacy\)$/],
];

test("3.33.0: sensors, the team, the power, a pharmacy's price, cancelling and the data: answered with no key on every page, an alert open or not", () => {
  for (const pathname of EVERY) {
    const states = pathname === "/console" || pathname === "/today"
      ? [overviewWith(), overviewWith({ none: true }), overviewWith({ quiet: true })] : [overviewWith()];
    for (const overview of states) {
      for (const [words, want] of ROUND11) {
        const got = typedAnswer(pathname, words, { overview });
        assert.match(got || "(no answer)", want, `${pathname} (${overview.incidents.length} alerts): ${words}`);
        assert.doesNotMatch(got, NO_PEOPLE);
      }
    }
  }
  // Signed out on the console too.
  for (const [words, want] of ROUND11) assert.match(typedAnswer("/console", words, { signedIn: false }) || "", want, words);
  // A visitor adding the chef: the way in. Signed in: the console's Team card, opened by its link.
  assert.match(typedAnswer("/", "how do I add my chef to get texts"), /\[Start a free trial\]\(\/signup\)$/);
  assert.match(typedAnswer("/today", "how do I add my chef to get texts"), /\[Add someone\]\(\/console\?view=full#team\)$/);
  assert.match(typedAnswer("/console", "how do I add my chef to get texts"), /\[Add someone\]\(\/console#team\)$/);
  // Cancelling quotes the rule checkout shows (main.pilot_facts "cancel-rule"); its fallback is the same words.
  assert.ok(typedAnswer("/pricing", "How do I cancel?", { data: { ...DATA, cancelRule: "Cancel any time. RULE." } }).includes("Cancel any time. RULE."));
  assert.ok(CONFIG.includes("What you have already paid is not refunded, in part or in full."));
  // The figures are the server's.
  assert.ok(typedAnswer("/", "How much for a pharmacy?", { data: { ...DATA, pharmacyPrice: "210" } }).startsWith("**Pharmacies: $210 a month"));
  // The misses still miss, the "does/this" ones included.
  for (const pathname of EVERY) {
    for (const words of ["How does this work?", "Write me a poem about fridges", "What does this button do?", "Tell me a joke", "Does it?"]) {
      assert.equal(typedAnswer(pathname, words), null, `${pathname}: ${words}`);
    }
  }
});

test("3.33.0: signed out, 'Is my walk-in fridge OK?' says to sign in, with the link, on every page", () => {
  for (const pathname of EVERY) {
    const got = typedAnswer(pathname, "Is my walk-in fridge OK?", { signedIn: false, overview: null });
    assert.match(got || "(no answer)", /^Sign in to see your fridges: \[sign in\]\(\/console\)/, pathname);
  }
});

test("3.33.0: 'What Kryozyn did for you', pressed or typed, quotes the week's summary line, an alert open or not", async () => {
  const did = { period: "week", label: "This week", figures: { readings: 1, alerts: 2 },
    lines: ["Checked 1 reading from 1 fridge or freezer, 100% in range.", "2 alerts, 1 answered in 4 minutes.", "Every sensor is reporting."] };
  for (const pathname of ["/today", "/console"]) {
    for (const overview of [overviewWith({ none: true }), overviewWith()]) {
      const got = await Promise.resolve((() => {
        const given = mountAt(pathname, { overview, did });
        const hit = PANEL.matchTyped("What Kryozyn did for you", typedItems(given, pathname));
        return hit.answer({ facts: {}, ready: false, label: "What Kryozyn did for you" });
      })());
      assert.ok(got.startsWith("**This week: 1 reading checked, 2 alerts.**"), `${pathname}: ${got}`);
      assert.ok(got.includes("- 2 alerts, 1 answered in 4 minutes."), got);
      // A link Pilot draws (a bare "#did" was printed as words).
      assert.ok(got.endsWith("[Open What Kryozyn did for you](/today#did)"), got);
    }
    // Pressed, with no alert open, the same.
    const chip = chipOf(mountAt(pathname, { overview: overviewWith({ none: true }), did }), "What Kryozyn did for you");
    assert.ok((await ask(chip)).startsWith("**This week: 1 reading checked, 2 alerts.**"));
  }
  // Nothing loaded yet: where it is.
  assert.match(await ask(chipOf(mountAt("/today", { overview: overviewWith({ none: true }) }), "What Kryozyn did for you")),
    /^\*\*What Kryozyn did for you\*\* shows this week or this month/);
});

test("3.33.0: 'It's fixed now' and 'What does this alert mean?' are offered only while an alert is open", () => {
  for (const pathname of ["/console", "/today"]) {
    const calm = mountAt(pathname, { overview: overviewWith({ none: true }) });
    const labels = J(typedItems(calm, pathname).map((x) => x.label));
    assert.ok(!labels.includes("It's fixed now") && !labels.includes("What does this alert mean?"), `${pathname}: ${labels}`);
    assert.deepEqual(J(calm.chips(calm.screen()).map((c) => c.label)), ["Log for the inspector", "What Kryozyn did for you", "Wi-Fi or power goes off?"]);
    const live = mountAt(pathname, { overview: overviewWith() });
    assert.deepEqual(J(live.chips(live.screen()).map((c) => c.label)), ["What does this alert mean?", "It's fixed now", "Log for the inspector"]);
    // Not loaded yet counts as none open.
    const loading = mountAt(pathname, { overview: null });
    assert.ok(!J(loading.chips(loading.screen()).map((c) => c.label)).includes("It's fixed now"));
  }
  // The console follows "#team" to Who gets alerts, once.
  const js = fsx.readFileSync(app("console.js"), "utf8");
  const follow = js.slice(js.indexOf("function followTeamLink"), js.indexOf("// A link followed while the console is already open"));
  assert.ok(follow.includes('location.hash !== "#team"') && follow.includes('goTo("oncall");'));
});

test("3.33.0: the robot keeps off the opened summary's list on /today", () => {
  const avoid = J(mountAt("/today", { overview: overviewWith() }).avoid);
  for (const sel of ["#did-lines", "#did-sample-lines", ".did-tabs"]) assert.ok(avoid.includes(sel), sel);
});

// ---------------------------------------------------------------- 3.35.0

// A day after food was thrown away: the alert closed, the answer
// recorded, /today's headline saying to make the pack.
function lossOverview({ note = "Two trays of chicken" } = {}) {
  const data = overviewWith({ quiet: true });
  data.incidents = [{ incident_id: "INC-000009", sensor_id: "WALKIN-1", state: "resolved", opened_at: "2026-11-20T08:00:00Z",
    out_since: "2026-11-20T07:50:00Z", out_until: "2026-11-20T09:10:00Z", resolved_at: "2026-11-20T09:10:00Z", peak_display: 58,
    product_disposition: "discarded", food_answered_at: "2026-11-20T09:12:00Z", food_answered_by_name: "Dana Reyes", food_auto: false }];
  data.recent_loss = { incident_id: "INC-000009", unit: "Kitchen walk-in", at: "2026-11-20T09:12:00Z", by_name: "Dana", note };
  data.texted_first_names = ["Dana", "Sam"];
  return data;
}

test("3.35.0: 'How do I make the insurance pack?' and 'How do I sign up?' are answered with no key", () => {
  for (const pathname of ["/", "/pricing", "/help", "/help/sensors", "/demo"]) {
    const pack = typedAnswer(pathname, "How do I make the insurance pack?");
    assert.match(pack || "(none)", /\[See a sample pack\]\(\/sample-insurance-pack\)/, pathname);
    const signup = typedAnswer(pathname, "How do I sign up?");
    assert.match(signup || "(none)", /^Press \[Start a free trial\]\(\/signup\): your business name/, pathname);
    assert.match(signup, /14 days for up to 5 sensors/);
  }
  // Signed in, a day after a loss: straight to that alert's pack.
  for (const pathname of ["/today", "/console"]) {
    const got = typedAnswer(pathname, "How do I make the insurance pack?", { overview: lossOverview() });
    assert.match(got || "(none)", /^For the food thrown away from \*\*Kitchen walk-in\*\*: \[Make the insurance pack\]\(\/console[^)]*#packet=INC-000009\)/, pathname);
    assert.doesNotMatch(got, /Add a line saying what went/);
    const bare = typedAnswer(pathname, "How do I make the insurance pack?", { overview: lossOverview({ note: null }) });
    assert.match(bare, /Add a line saying what went in the bin first/);
    // With no loss: where the button is.
    assert.match(typedAnswer(pathname, "How do I make the insurance pack?", { overview: overviewWith({ none: true }) }) || "",
      /open \*\*Insurance packs\*\* on the \*\*Proof\*\* tab/);
  }
  assert.match(typedAnswer("/today", "How do I sign up?", { overview: overviewWith() }) || "", /^You are already signed in\./);
});

test("3.35.0: while the loss headline shows, the welcome offers the pack first", async () => {
  for (const pathname of ["/today", "/console"]) {
    // 3.36.0: the welcome's first pill, not a suggestion under it.
    const given = mountAt(pathname, { overview: lossOverview() });
    const labels = J(pills(given, pathname).map((c) => c.label));
    assert.equal(labels[0], "Make the insurance pack", `${pathname}: ${labels}`);
    assert.match(await ask(pills(given, pathname)[0]), /#packet=INC-000009\)/);
    assert.ok(!J(given.chips(given.screen()).map((c) => c.label)).includes("Make the insurance pack"), "said once");
    // An alert open outranks it; nothing lost, no pack pill.
    const live = mountAt(pathname, { overview: { ...lossOverview(), incidents: overviewWith().incidents } });
    assert.equal(J(live.chips(live.screen()).map((c) => c.label))[0], "What does this alert mean?");
    assert.ok(!J(pills(live, pathname).map((c) => c.label)).includes("Make the insurance pack"));
    const calm = mountAt(pathname, { overview: overviewWith({ none: true }) });
    assert.ok(!J(pills(calm, pathname).map((c) => c.label)).includes("Make the insurance pack"));
  }
});

test("3.35.0: 'Is the food safe to eat?' respects a recorded 'Thrown away'", () => {
  for (const pathname of ["/today", "/console"]) {
    const got = typedAnswer(pathname, "Is the food safe to eat?", { overview: lossOverview() });
    assert.match(got || "(none)", /You recorded it as thrown away at (?:[A-Z][a-z]{2} \d{1,2}, |[A-Z][a-z]{2} )?\d{1,2}:\d\d [AP]M, so there is nothing left to decide about that food\./, pathname);
    assert.doesNotMatch(got, /rule of thumb/, "no rule of thumb over food already in the bin");
    assert.match(got, /#packet=INC-000009\)/);
    assert.doesNotMatch(got, NO_PEOPLE);
  }
});

test("3.35.0: 'Who gets the texts?' names the people on the list, first names only, when signed in", () => {
  for (const pathname of ["/today", "/console"]) {
    const got = typedAnswer(pathname, "Who gets the texts?", { overview: lossOverview() });
    assert.match(got || "(none)", /^Right now an alert text goes to \*\*Dana and Sam\*\*\./, pathname);
    assert.doesNotMatch(got, /Reyes|\+1555|@/);
  }
  // A visitor: no names.
  assert.doesNotMatch(typedAnswer("/", "Who gets the texts?") || "", /Right now/);
});

test("3.35.0: 'What did Kryozyn do for me?' finds the summary, an alert open or not", () => {
  for (const pathname of ["/today", "/console"]) {
    for (const overview of [overviewWith({ none: true }), overviewWith(), lossOverview()]) {
      const given = mountAt(pathname, { overview });
      const hit = PANEL.matchTyped("What did Kryozyn do for me?", typedItems(given, pathname));
      assert.ok(hit, `${pathname} (${overview.incidents.length}): no item`);
      const got = hit.answer({ facts: {}, ready: false, label: "What did Kryozyn do for me?" });
      assert.match(String(got && got.then ? "async" : got), /async|What Kryozyn did for you/, pathname);
    }
  }
});

test("3.35.0: every item's words still fit the core's 20", () => {
  for (const pathname of EVERY.concat(["/sample-insurance-pack"])) {
    for (const overview of [overviewWith(), overviewWith({ none: true }), lossOverview()]) {
      const given = mountAt(pathname, { overview });
      for (const item of [...given.welcomePills, ...given.chips(given.screen())]) {
        assert.ok(!item.match || item.match.length <= 20, `${pathname} ${item.label}: ${item.match && item.match.length}`);
      }
    }
  }
});

// ---------------------------------------------------------------- 3.36.0

// A business whose overview says what the welcome should follow.
function situationOverview({ reported = true, food = "kept", loss = false, texts = "landed" } = {}) {
  const data = overviewWith({ quiet: true });
  if (!reported) data.sensors = [{ sensor_id: "NEW-1", location_name: "Walk-in fridge", last_temperature_display: null, condition: { state: "waiting_first" } }];
  const closed = { incident_id: "INC-000020", sensor_id: "WALKIN-1", state: "resolved", opened_at: "2026-11-20T08:20:00Z",
    out_since: "2026-11-20T08:00:00Z", resolved_at: "2026-11-20T09:00:00Z", peak_display: 52 };
  if (food === "kept") Object.assign(closed, { product_disposition: "kept", food_answered_at: "2026-11-20T09:02:00Z" });
  if (food === "waiting") closed.food_question = { word: "food", at_risk: true, words: "Food at risk: check it", out_since: closed.out_since, ask_on_today: true, note: "" };
  if (texts === "off") Object.assign(closed, { texts_off: true, sms_fanout: [{ contact_name: "Dana Reyes", to: "+15550100", delivered: false, status: "not_configured" }] });
  if (texts === "failed") Object.assign(closed, { alert_texts_failed: true, sms_fanout: [{ contact_name: "Dana Reyes", to: "+15550100", delivered: false, status: "failed" },
    { contact_name: "Sam Lee", to: "+15550101", delivered: false, status: "failed" }] });
  if (texts === "landed") Object.assign(closed, { texts_landed: true, sms_fanout: [{ contact_name: "Dana Reyes", to: "+15550100", delivered: true },
    { contact_name: "Sam Lee", to: "+15550101", delivered: true }] });
  data.incidents = food === "none" ? [] : [closed];
  if (loss) return lossOverview();
  return data;
}
const labelsOf = (pathname, overview) => J(pills(mountAt(pathname, { overview }), pathname).map((p) => p.label));

test("3.36.0: the welcome follows the situation: the pack first after a loss, no connect pill once a sensor reports, no food pill once answered", () => {
  for (const pathname of ["/today", "/console"]) {
    // After food was thrown away: the pack first; the food answered and a sensor reporting, so neither of those.
    const loss = labelsOf(pathname, lossOverview());
    assert.equal(loss[0], "Make the insurance pack", `${pathname}: ${loss}`);
    assert.ok(!loss.includes("Is the food still OK?") && !loss.includes("How do I connect a sensor?"), `${pathname}: ${loss}`);
    // A new business, its sensor not heard from yet: how to connect it, and nothing about food.
    const fresh = labelsOf(pathname, situationOverview({ reported: false, food: "none" }));
    assert.ok(fresh.includes("How do I connect a sensor?"), `${pathname}: ${fresh}`);
    assert.ok(!fresh.includes("Is the food still OK?"), `${pathname}: ${fresh}`);
    // The food answered: no food pill. Still to answer: the food pill.
    assert.ok(!labelsOf(pathname, situationOverview({ food: "kept" })).includes("Is the food still OK?"));
    assert.ok(labelsOf(pathname, situationOverview({ food: "waiting" })).includes("Is the food still OK?"));
    // An alert live: the food pill, whatever else.
    assert.ok(labelsOf(pathname, overviewWith()).includes("Is the food still OK?"));
    // Before the page has loaded: the four, as before.
    assert.deepEqual(labelsOf(pathname, null), ["Which fridges need me?", "Is the food still OK?", "How do I connect a sensor?",
      "Who gets the texts?", "Add my AI key"]);
    // Always five at most, the key last, each once.
    for (const ov of [lossOverview(), situationOverview(), situationOverview({ reported: false, food: "none" }), overviewWith()]) {
      const l = labelsOf(pathname, ov);
      assert.ok(l.length <= 5 && l[l.length - 1] === "Add my AI key" && new Set(l).size === l.length, `${pathname}: ${l}`);
    }
  }
  // The visitor's welcome is unchanged.
  assert.deepEqual(labelsOf("/", null), ["Will it work for my café?", "What does it cost?", "See it live", "What sensors do I need?", "Add my AI key"]);
});

test("3.36.0: signed out, the insurer, setting up, the food and Canada are answered with no key", () => {
  for (const pathname of ["/", "/pricing", "/help", "/help/sensors", "/for/restaurant", "/demo", "/sample-insurance-pack"]) {
    const insurer = typedAnswer(pathname, "What does my insurer get?", { signedIn: false, overview: null });
    assert.match(insurer || "(none)", /^Your insurer gets an \*\*insurance pack\*\*: the readings around the alert, who was texted and when/, pathname);
    assert.match(insurer, /\[See a sample pack\]\(\/sample-insurance-pack\)/);
    const easy = typedAnswer(pathname, "Is it hard to set up?", { signedIn: false, overview: null });
    assert.match(easy || "(none)", /^Not hard, and no coding\./, pathname);
    assert.match(easy, /\[Step-by-step guides\]\(\/help\/sensors\)/);
    const food = typedAnswer(pathname, "Is the food safe to eat?", { signedIn: false, overview: null });
    assert.match(food || "(none)", /^The widely taught rule of thumb: under 2 hours above the limit/, pathname);
    assert.match(food, /That is guidance, not a ruling/);
    const canada = typedAnswer(pathname, "Does it work in Canada?", { signedIn: false, overview: null });
    assert.match(canada || "(none)", /^Yes\. Kryozyn can show every reading in Celsius/, pathname);
    assert.match(canada, /over 41°F \(5°C\) unless you set your own with \*\*Change limit\*\*/);
    // 3.37.0: in plain words, no text provider's name for a visitor.
    assert.match(canada, /reach any mobile number written with its country code: a Canadian number \(\+1\)/);
    assert.doesNotMatch(canada, /Twilio/);
    assert.match(canada, /Prices are in US dollars/);
  }
  // On the person's own pages too.
  for (const pathname of ["/today", "/console"]) {
    assert.match(typedAnswer(pathname, "Does it work in Canada?") || "", /Celsius/, pathname);
    assert.match(typedAnswer(pathname, "Is it hard to set up?") || "", /^Not hard, and no coding\./, pathname);
  }
  // The limit is the server's.
  assert.match(typedAnswer("/", "Does it work in Canada?", { data: { ...DATA, fridgeLimit: "40" } }), /over 40°F \(4°C\)/);
  // The misses still miss.
  for (const words of ["Write me a poem about fridges", "Tell me a joke", "How does this work?"]) assert.equal(typedAnswer("/", words), null, words);
});

test("3.36.0: signed in, what was thrown away, why nobody got a text, and the pack's address, from the page's own record", () => {
  for (const pathname of ["/today", "/console"]) {
    // The note, quoted.
    const thrown = typedAnswer(pathname, "What was thrown away?", { overview: lossOverview() });
    assert.match(thrown || "(none)", /^Recorded for \*\*Kitchen walk-in\*\* at .+: \u201cTwo trays of chicken\u201d/, pathname);
    assert.match(thrown, /#packet=INC-000009\)/);
    const bare = typedAnswer(pathname, "What was thrown away?", { overview: lossOverview({ note: null }) });
    assert.match(bare, /nobody has written down what went yet/);
    assert.match(typedAnswer(pathname, "What was thrown away?", { overview: situationOverview() }), /^Nothing has been recorded as thrown away/);
    // Texts off: written, not delivered; never the people.
    const off = typedAnswer(pathname, "why did nobody get a text", { overview: situationOverview({ texts: "off" }) });
    assert.match(off || "(none)", /^The alert for \*\*Kitchen walk-in\*\* at .+ was written but not delivered: texts are off on this server, so nobody was texted\./, pathname);
    assert.doesNotMatch(off, /Dana|Sam|Reyes|\+1555|Right now an alert text goes to/);
    const failed = typedAnswer(pathname, "Why didn't I get a text?", { overview: situationOverview({ texts: "failed" }) });
    assert.match(failed || "(none)", /went to 2 people and failed: none went through\./, pathname);
    assert.doesNotMatch(failed, /Dana|Sam|Reyes|\+1555/);
    const landed = typedAnswer(pathname, "nobody got the alert text", { overview: situationOverview({ texts: "landed" }) });
    assert.match(landed || "(none)", /was texted to 2 people, and the phone network took both\./, pathname);
    assert.match(landed, /\*\*Send a test alert\*\*/);
    // The address: where the box is, with a link that opens it.
    for (const overview of [overviewWith(), situationOverview(), lossOverview()]) {
      const addr = typedAnswer(pathname, "How do I add my address to the pack?", { overview });
      assert.match(addr || "(none)", /type it in \*\*Site address\*\* and press \*\*Save\*\*/, pathname);
      assert.match(addr, /\[Add the address\]\([^)]*#site-address\)/);
    }
  }
  // The console follows "#site-address" to the box.
  const js = fsx.readFileSync(app("console.js"), "utf8");
  assert.ok(js.includes('location.hash !== "#site-address"') && js.includes('goTo("address")'));
});

test("3.36.0: every item's words fit the core's 20 in every situation", () => {
  const states = [overviewWith(), overviewWith({ none: true }), overviewWith({ ack: true, fixed: true }), lossOverview(), situationOverview(),
    situationOverview({ reported: false, food: "none" }), situationOverview({ food: "waiting" }), null];
  for (const pathname of EVERY.concat(["/sample-insurance-pack", "/for"])) {
    for (const overview of states) {
      for (const signedIn of [true, false]) {
        const given = mountAt(pathname, { overview, signedIn });
        for (const item of [...given.welcomePills, ...given.chips(given.screen())]) {
          assert.ok(!item.match || item.match.length <= 20, `${pathname} ${item.label}: ${item.match && item.match.length}`);
        }
      }
    }
  }
});

// ---------------------------------------------------------------- 3.37.0: what a typed question is after, and every miss

// The config on a page with a real chat box: the core's submit is the
// chat box's own form, which this app listens to (capture), then the core
// matches the question against the items as they are at that moment.
// Every page under /for, as main.pilot_facts puts them on Pilot's script
// tag (3.41.0): "slug|name|monthly dollars|per", "" priced by quote.
const SECTORS_DATA = ["cannabis|Licensed Cannabis Cultivation||", "country_club|High-End Country Clubs||", "cryostorage|IVF Clinics & Cryostorage||",
  "cybersecurity|Data centers||", "logistics|High-Stakes Cold-Chain Transport||", "medical_lab|Medical Labs & Blood Banks||",
  "pharmacy|Retail & Hospital Pharmacies|199|pharmacy", "private_aviation|Private Aviation Hangars||", "restaurant|Restaurants & Cafés|149|location",
  "solar_infrastructure|Solar Infrastructure & Storage||", "superyacht|Luxury Superyacht Engine Bays||", "wine_and_art|Fine Wine & Art Storage||",
  "breweries|Breweries||", "butchers|Butchers|149|location", "florists|Florists||", "grocery-stores|Grocery stores|149|location",
  "ice-cream-shops|Ice-cream shops|149|location", "school-cafeterias|School cafeterias|149|location", "vaccine-clinics|Vaccine clinics|199|pharmacy"].join(";");

function chatAt(pathname, opts = {}) {
  let listener = null;
  const box = { value: "" };
  const form = { classList: { contains: (c) => c === "pilot-compose" }, querySelector: (q) => (q === ".pilot-input" ? box : null) };
  let given = null;
  const fetchTrap = () => { throw new Error("an answer called the network"); };
  const signedIn = opts.signedIn !== undefined ? opts.signedIn : true;
  const overview = opts.overview !== undefined ? opts.overview : overviewWith();
  const win = { Pilot: { mount: (c) => { given = c; } }, location: { pathname, href: BASE + pathname },
    localStorage: { getItem: (k) => (signedIn && k === "cyberlogix.session" ? "t" : null) }, CLXWords: Words, fetch: fetchTrap };
  if (pathname === "/console") win.KryozynConsole = { signedIn: () => signedIn, tab: () => "Fridges", overview: () => (signedIn ? overview : null),
    rotations: () => (signedIn && opts.rotations !== undefined ? opts.rotations : null),
    bills: () => (signedIn && opts.bills !== undefined ? opts.bills : null) };
  if (pathname === "/today") win.KryozynToday = { overview: () => overview, did: () => null };
  const doc = { getElementById: () => ({ dataset: { ...NAMES, ...DATA, restaurantPrice: "149", pharmacyPrice: "199",
    unitsPerLocation: "10", extraUnit: "15", chainFrom: "5", chainPercent: "10", graceDays: "14", sectors: SECTORS_DATA, ...(opts.data || {}) } }), title: "Restaurants",
    addEventListener: (type, fn, capture) => { if (type === "submit" && capture) listener = fn; },
    querySelector: (q) => (q === ".pilot-input" ? box : null) };
  vmx.runInNewContext(CONFIG, { window: win, fetch: fetchTrap, XMLHttpRequest: fetchTrap, URL, setTimeout, document: doc });
  // Send a question as the core does: the form's submit (this app hears
  // it first), then matchTyped over the welcome and the suggestions, then
  // the answer, or on a miss the note (noKeyReply) and, once drawn, the
  // suggestions again.
  const send = (words) => {
    box.value = words;
    listener({ target: form });
    const hit = PANEL.matchTyped(words, typedItems(given, pathname));
    if (hit) {
      box.value = "";
      return { label: hit.label, text: hit.link ? `LINK ${hit.link}` : String(hit.answer({ facts: {}, ready: false, label: words })) };
    }
    return { label: null, note: PANEL.noKeyText(given.noKeyReply, true, true) };
  };
  return { given, send, tick: () => new Promise((r) => setTimeout(r, 0)) };
}

test("3.37.0: a price with a number is worked out from the plan figures, on every page", async () => {
  for (const pathname of EVERY) {
    const chat = chatAt(pathname);
    const two = chat.send("how much for 2 fridges?");
    assert.ok(two.label, `${pathname}: answered with no key`);
    assert.match(two.text, /^\*\*2 fridges: Starter, \$59 a month\.\*\* It covers one café or shop with up to 3 fridges or freezers/, pathname);
    await chat.tick();
    const five = chat.send("What would 5 fridges cost?");
    assert.match(five.text || "", /^\*\*5 fridges: Growth, \$149 a month\*\* \(Starter covers up to 3\)\./, `${pathname}: ${five.text}`);
    await chat.tick();
    const fourteen = chat.send("price for 14 units");
    assert.match(fourteen.text || "", /^\*\*14 fridges or freezers: Growth, \$209 a month\*\*/, `${pathname}: ${fourteen.text}`);
    assert.match(fourteen.text, /each one past that is \$15 a month: 4 more is \$60/);
    await chat.tick();
    const chain = chat.send("How much for 6 restaurants?");
    assert.match(chain.text || "", /^\*\*6 locations: Growth, \$894 a month\*\*/, `${pathname}: ${chain.text}`);
    assert.match(chain.text, /From 5 locations, 10% comes off/);
    await chat.tick();
    const pharmacy = chat.send("how much for 2 pharmacies");
    assert.match(pharmacy.text || "", /^\*\*2 pharmacies: \$398 a month\*\*, \$199 a pharmacy\./, `${pathname}: ${pharmacy.text}`);
    await chat.tick();
  }
});

test("3.37.0: the night, 3am and a call get the calls answer; the Wi-Fi dropping the power answer; metal and walk-ins the sensors", async () => {
  for (const pathname of EVERY) {
    const chat = chatAt(pathname);
    for (const words of ["What happens at 3am?", "Will you call me at night?", "do you ring people overnight", "If nobody answers at 2 am do you phone?"]) {
      const got = chat.send(words);
      assert.match(got.text || "(missed)", /^Yes, on plans with calls/, `${pathname}: ${words}`);
      await chat.tick();
    }
    for (const words of ["What if the wifi drops?", "our internet keeps dropping", "What happens when the sensor is offline?", "power cut last night", "My fridge says no signal"]) {
      const got = chat.send(words);
      assert.match(got.text || "(missed)", /^Sensors send their readings over your internet/, `${pathname}: ${words}`);
      await chat.tick();
    }
    for (const words of ["Will a sensor work in a metal walk-in?", "Does the signal get through a walk-in freezer's walls?", "steel box, will it reach the wifi?"]) {
      const got = chat.send(words);
      assert.match(got.text || "(missed)", /^A walk-in's metal walls and door can block a wireless signal/, `${pathname}: ${words}`);
      assert.match(got.text, /The sensors that work:\n- One that sends each reading to a web address/);
      await chat.tick();
    }
    // A door left open at night is the door's answer, not the calls.
    assert.match(chat.send("Will a door left open wake us at night?").text || "", /^No\. A door opened for a minute/, pathname);
    await chat.tick();
  }
});

test("3.37.0: every miss gets a note naming the question and the two closest answers first; a second miss is never silent", async () => {
  for (const [pathname, signedIn] of PAGES_143) {
    const chat = chatAt(pathname, { signedIn });
    const first = chat.send("Write me a poem about fridges");
    assert.equal(first.label, null, pathname);
    assert.equal(first.note, "I don't have a built-in answer for “Write me a poem about fridges”. The two closest are first below. With an AI key, I can answer anything.");
    await chat.tick();
    const near = PANEL.cleanChips(chat.given.chips(chat.given.screen()), BASE + pathname);
    assert.equal(near.length, 2, pathname);
    for (const c of near) assert.equal(typeof c.answer, "function", `${pathname}: ${c.label}`);
    // Fridges: the status answer first on the person's own pages.
    if (signedIn && (pathname === "/console" || pathname === "/today")) assert.equal(near[0].label, "Which fridges need me?");
    // Under the note the two lead the row, the key pill last.
    const row = Array.from(PANEL.noKeyItems(PANEL.ownPills(chat.given.welcomePills, BASE + pathname), near, true));
    assert.deepEqual(J(row.slice(0, 2).map((x) => x.label)), J(near.map((x) => x.label)));
    assert.equal(row[row.length - 1].label, "Add my AI key");
    // A second miss: other words in the note, so the chat changes.
    const second = chat.send("Tell me a joke");
    assert.equal(second.note, "That one isn't built in either: “Tell me a joke”. The two closest are first below. With an AI key, I can answer anything.");
    assert.notEqual(second.note, first.note);
    await chat.tick();
    // The same words again: still a different note from the one before.
    const third = chat.send("Tell me a joke");
    assert.notEqual(third.note, second.note, pathname);
    await chat.tick();
    // Pressing one of the two answers it, and the page's own suggestions come back.
    const pressed = PANEL.cleanChips(chat.given.chips(chat.given.screen()), BASE + pathname)[0];
    assert.ok(String(pressed.answer({ facts: {}, ready: false, label: pressed.label })).length > 20);
    const after = chat.given.chips(chat.given.screen()).map((c) => c.label);
    assert.notDeepEqual(J(after), J(near.map((x) => x.label)), `${pathname}: back to the page's own`);
  }
});

test("3.37.0: the earlier typed answers still go where they did, and every item's words still fit the core's 20", async () => {
  for (const pathname of ["/console", "/today"]) {
    const chat = chatAt(pathname);
    assert.equal(chat.send("Is my walk-in fridge OK?").label !== null, true);
    await chat.tick();
    assert.match(chat.send("How much does it cost?").text, /Starter \$59 a month, up to 3 fridges or freezers/);
    await chat.tick();
    assert.match(chat.send("Is the food safe?").text, /rule of thumb|recorded/);
    await chat.tick();
  }
  for (const pathname of EVERY) {
    const chat = chatAt(pathname);
    chat.send("how much for 2 fridges?");
    for (const item of typedItems(chat.given, pathname)) assert.ok(!item.match || item.match.length <= 20, `${pathname}: ${item.label}`);
    await chat.tick();
  }
});

// ---------------------------------------------------------------- 3.38.0: the core questions with no key

// An account as the overview gives it, with the plan, the people texted
// now and the sites (first names only ever reach an answer).
function accountOverview({ trial = false, paused = false } = {}) {
  const o = overviewWith();
  o.sensors[1].location_name = "Harbourside / Bar fridge";
  o.sensors[1].site_name = "Harbourside";
  o.sensors[1].danger_above_display = 41;
  o.sensors[0].site_name = "Old Mill";
  o.sites = [{ site_id: "SITE-1", name: "Harbourside" }, { site_id: "SITE-2", name: "Old Mill" }];
  o.texted_first_names = ["Morgan"];
  o.account = { on_trial: trial, plan: trial ? "trial" : "growth", collections: { overdue_usd: 0 },
    paused: paused ? { resume_on: "1 February 2027", words: "Paused until 1 February 2027. No bills and no alerts until then." } : null };
  o.subscription = trial
    ? { plan: "trial", plan_name: "Trial", units_total: 3, monthly_total_usd: 149,
      after_trial: { plan_name: "Starter", monthly_total_usd: 59 }, line_items: [] }
    : { plan: "growth", plan_name: "Growth", units_total: 20, monthly_total_usd: 447,
      line_items: [{ description: "3 locations (20 sensors)" }] };
  return o;
}
const ROTA_NOW = [{ scope: "estate", on_shift_now: { contact_id: "C1", full_name: "Morgan Hale" },
  schedule: [{ full_name: "Morgan Hale", ends_local: "Fri 04 Dec 08:00 CST" }, { full_name: "Priya Lund", ends_local: "Fri 11 Dec 08:00 CST" }] }];

test("3.38.0: a visitor's nobody answering, a contract, and an electrician are answered with no key, on every page", async () => {
  for (const pathname of EVERY) {
    const chat = chatAt(pathname, { signedIn: pathname === "/console" || pathname === "/today" });
    for (const words of ["What happens if nobody answers?", "what if no one answers the text", "And if the chef doesn't answer?"]) {
      assert.match(chat.send(words).text || "(missed)", /^If nobody answers the alert text within 10 minutes, Kryozyn phones the people on call/, `${pathname}: ${words}`);
      await chat.tick();
    }
    for (const words of ["Is there a contract or can I leave any time?", "Is there a commitment?", "am I locked in?"]) {
      const got = chat.send(words).text || "(missed)";
      assert.match(got, /^No lock-in: you pay by the month, or by the year if you choose, and can leave any time\. A signed term sets your price and renews by itself, but cancelling still stops the bills, with no fee for leaving early\.\n\nIn the console: \*\*Money\*\* tab/, `${pathname}: ${words}`);
      await chat.tick();
    }
    for (const words of ["Do I need an electrician?", "is it hard to install?", "Is there any wiring?"]) {
      assert.match(chat.send(words).text || "(missed)", /^Not hard, and no coding\. No electrician either: the sensors are wireless/, `${pathname}: ${words}`);
      await chat.tick();
    }
  }
  // Pressed, "How do I cancel?" stays as it was: no lock-in line it wasn't asked for.
  const chat = chatAt("/pricing", { signedIn: false });
  assert.match(chat.send("How do I cancel my subscription?").text, /^In the console: \*\*Money\*\* tab/);
});

test("3.38.0: signed in, what's wrong, who is on call, the bill, a pause and a limit come from the page's own data", async () => {
  for (const pathname of ["/console", "/today"]) {
    const overview = accountOverview();
    const chat = chatAt(pathname, { overview, rotations: ROTA_NOW });
    const wrong = chat.send("What's wrong right now?");
    assert.equal(wrong.label !== null, true, pathname);
    assert.match(wrong.text, /^These 2 need you, worst first:\n- \*\*Kitchen walk-in\*\* \(47.0°F\)/, pathname);
    await chat.tick();
    assert.match(chat.send("status").text, /^These 2 need you/, pathname);
    await chat.tick();

    const oncall = chat.send("Who is on call this week?").text || "(missed)";
    if (pathname === "/console") assert.match(oncall, /^On call now: \*\*Morgan\*\* until Fri Dec 4, 8:00 AM, then \*\*Priya\*\*\./);
    else assert.match(oncall, /^An alert text goes to \*\*Morgan\*\* right now\.\n\nThe week's turns are on the \*\*Team\*\* tab, under \*\*On call now\*\*\./);
    assert.match(oncall, /\[Open On call now\]\(/);
    assert.doesNotMatch(oncall, /Hale|Lund|\+1555/);
    await chat.tick();

    const bill = chat.send("How much am I paying?").text || "(missed)";
    assert.match(bill, /^You're on \*\*Growth: \$447 a month\*\*, for 3 locations \(20 sensors\)\. Nothing is overdue\./, `${pathname}: ${bill}`);
    await chat.tick();

    const pause = chat.send("We're closed for Christmas, can I pause alerts?").text || "(missed)";
    assert.match(pause, /^There's no pause for a few days\. The shortest is the account pause, 1, 2 or 3 months/);
    assert.match(pause, /\*\*Money\*\* tab, under \*\*Pause or cancel\*\*/);
    assert.match(pause, /\*\*Mute\*\*/);
    await chat.tick();

    const limit = chat.send("change the bar fridge limit to 38").text || "(missed)";
    assert.match(limit, /^For \*\*Bar fridge\*\* at Harbourside \(limit 41°F\): Press \*\*Change limit\*\* on its card/, `${pathname}: ${limit}`);
    assert.match(limit, /type 38 under \*\*Text us if it goes over\*\*, and press \*\*Save\*\*/);
    await chat.tick();
  }
  // A trial is never billed; a paused account says until when.
  assert.match(chatAt("/today", { overview: accountOverview({ trial: true }) }).send("how much am I paying?").text,
    /^You're on the free trial: nothing is billed\. Your 3 units would be \*\*Starter, \$59 a month\*\* after it\./);
  assert.match(chatAt("/console", { overview: accountOverview({ paused: true }) }).send("closed for the holidays: can I pause alerts?").text,
    /^Paused until 1 February 2027\./);
  // A visitor asking what they pay gets the prices; who is on call, how it works.
  assert.match(chatAt("/pricing", { signedIn: false }).send("How much am I paying?").text || "", /^\*\*Starter \$59 a month/);
  assert.match(chatAt("/", { signedIn: false }).send("Who is on call this week?").text || "", /set a rotation on the \*\*Team\*\* tab/);
  assert.match(chatAt("/", { signedIn: false }).send("Can I change the limit to 38?").text || "", /^Every fridge and freezer has its own limit\. Press \*\*Change limit\*\*/);
});

test("3.38.0: '3 restaurants with about 8 fridges each' is 24 fridges across 3, priced at each location's share", async () => {
  for (const pathname of ["/", "/pricing", "/console"]) {
    const chat = chatAt(pathname);
    for (const words of ["How much for 3 restaurants with about 8 fridges each?", "price for 3 restaurants, 8 fridges per restaurant"]) {
      assert.match(chat.send(words).text || "(missed)", /^\*\*24 fridges across 3 locations: Growth, \$447 a month\*\*/, `${pathname}: ${words}`);
      await chat.tick();
    }
    // A total over the places is shared out: 20 over 3 is under 10 at each, no extras.
    assert.match(chat.send("how much for 20 fridges across 3 restaurants").text || "", /^\*\*20 fridges across 3 locations: Growth, \$447 a month\*\*\./);
    await chat.tick();
    // 12 at each of 2 is 2 past the allowance at each.
    assert.match(chat.send("2 restaurants with 12 fridges each, how much?").text || "", /^\*\*24 fridges across 2 locations: Growth, \$358 a month\*\*/);
    await chat.tick();
  }
});

// ---------------------------------------------------------------- 3.39.0: more with no key, and quieter

// A twenty-unit business as the demo is, with a week's alerts dated from
// now, a bar fridge at each of two sites, and one unit warm.
function bigOverview({ trial = false } = {}) {
  const o = accountOverview({ trial });
  const ago = (h) => new Date(Date.now() - h * 3600 * 1000).toISOString();
  const fine = ["Walk-in cooler", "Prep fridge, grill", "Prep fridge, salads", "Dessert display", "Dairy cooler", "Blast chiller",
    "Prep fridge, sauces", "Walk-in freezer 1", "Walk-in freezer 2", "Ice-cream freezer", "Prep fridge, hot line", "Prep fridge, pastry",
    "Walk-in freezer", "Old Mill / Bar fridge", "Prep fridge, bakery", "Blast chiller 2"];
  fine.forEach((name, n) => o.sensors.push({ sensor_id: `U-${n}`, location_name: name, last_temperature_display: n % 2 ? 36.4 : 35,
    temperature_unit: "F", danger_above_display: 41, site_name: n % 2 ? "Old Mill" : "Harbourside", condition: { state: "ok" } }));
  o.sensors[1].last_temperature_display = 37.2;
  o.incidents = [
    { incident_id: "INC-9", sensor_id: "WALKIN-1", state: "open", opened_at: ago(1), out_since: ago(1.5), temperature_display: 43.5,
      sms_fanout: [{ contact_name: "Morgan Hale", to: "+1 555 010 0141", delivered: true, status: "delivered", demo: true }] },
    { incident_id: "INC-8", sensor_id: "U-7", state: "resolved", opened_at: ago(72), acknowledged_at: ago(71.9), resolved_at: ago(70.5) },
    { incident_id: "INC-1", sensor_id: "U-3", state: "resolved", opened_at: ago(24 * 9), resolved_at: ago(24 * 9 - 1) },
  ];
  return o;
}

test("3.39.0: another fridge, trying with no sensor, and an app, answered with no key on every page", async () => {
  for (const pathname of EVERY) {
    const own = pathname === "/console" || pathname === "/today";
    const chat = chatAt(pathname, { signedIn: own, overview: bigOverview() });
    for (const words of ["How do I add a second fridge?", "can I add another sensor?", "adding a freezer"]) {
      const got = chat.send(words).text || "(missed)";
      if (own) {
        assert.match(got, /^Press \*\*Add a fridge or freezer\*\* in the console \(the \*\*Setup\*\* tab\)/, `${pathname}: ${words}`);
        assert.match(got, /\[Add a fridge or freezer\]\([^)]*#add-unit\)/);
      } else assert.match(got, /^Once you've signed up, \*\*Add a fridge or freezer\*\* in the console adds each one/, `${pathname}: ${words}`);
      await chat.tick();
    }
    for (const words of ["Can I try it without a sensor?", "can I see it working before signing up?"]) {
      const got = chat.send(words).text || "(missed)";
      assert.match(got, own ? /^Yes\. On the console's \*\*Fridges\*\* tab an owner can press \*\*Simulate\*\*/ : /^Yes, with no sensor at all:/, `${pathname}: ${words}`);
      assert.match(got, /\[See it live\]\(\/demo\)/);
      assert.match(got, /\*\*Simulate\*\*/);
      await chat.tick();
    }
    for (const words of ["Do you have an app?", "Is there an iPhone app?"]) {
      const got = chat.send(words).text || "(missed)";
      assert.match(got, /^There's no app to download from an app store: Kryozyn installs from the browser\./, `${pathname}: ${words}`);
      assert.match(got, /\*\*Add to Home Screen\*\*/);
      await chat.tick();
    }
  }
  // The console opens Add a fridge or freezer from "#add-unit".
  const js = fsx.readFileSync(app("console.js"), "utf8");
  assert.ok(js.includes('location.hash !== "#add-unit"') && js.includes('goTo("sensor")'));
});

test("3.39.0: a butcher, a bakery and a deli get the restaurant answer", async () => {
  for (const pathname of ["/", "/pricing", "/for/restaurant", "/today"]) {
    const chat = chatAt(pathname, { signedIn: pathname === "/today" });
    for (const [words, shop] of [["Will it work for my butcher shop?", "butcher's"], ["Do you do bakeries?", "bakery"], ["Is it any good for a deli?", "deli"]]) {
      const got = chat.send(words).text || "(missed)";
      assert.ok(got.startsWith(`Yes: a ${shop} is watched the same way as a restaurant, on the restaurant plan with its food limits.`), `${pathname}: ${words}: ${got}`);
      assert.match(got, /A fridge alarms over 41°F/);
      await chat.tick();
    }
  }
  // A unit called "Deli fridge" is a fridge, not a question about delis.
  assert.doesNotMatch(chatAt("/today", { overview: bigOverview() }).send("Is the deli fridge ok?").text || "", /^Yes: a deli/);
  // A price with a number is still a price.
  assert.match(chatAt("/pricing", { signedIn: false }).send("how much for a bakery with 2 fridges?").text, /^\*\*2 fridges: Starter, \$59 a month\.\*\*/);
});

test("3.39.0: signed in, this week's alerts and the warmest come from the page's own data, in a line or a short list", async () => {
  for (const pathname of ["/console", "/today"]) {
    const chat = chatAt(pathname, { overview: bigOverview() });
    const week = chat.send("How many alerts this week?").text || "(missed)";
    assert.match(week, /^\*\*2 alerts in the last 7 days\*\*, 1 still open:\n- \*\*Kitchen walk-in\*\*, .+: open, nobody has answered yet\n- \*\*Walk-in freezer 1\*\*, .+: closed, answered in 6 minutes$/, `${pathname}: ${week}`);
    await chat.tick();
    const warm = chat.send("Which is warmest?").text || "(missed)";
    assert.equal(warm, "**Kitchen walk-in** is the warmest for its limit: 47.0°F, 6.0° over its 41°F limit.", pathname);
    assert.ok(!warm.includes("\n"), "one line");
    await chat.tick();
    // The bar fridges asked about, and the other eighteen in a line.
    const bar = chat.send("Is the bar fridge ok?").text || "(missed)";
    assert.match(bar, /^- \*\*Harbourside \/ Bar fridge\*\* 37\.2°F, fine\.\n- \*\*Old Mill \/ Bar fridge\*\* 36\.4°F, fine\./, `${pathname}: ${bar}`);
    assert.match(bar, /2 others need you: Kitchen walk-in, Prep fridge x\(\/evil\)\. Ask \*\*Which fridges need me\?\*\* for the list\./);
    assert.ok(!bar.includes("Dessert display"), "not all twenty");
    await chat.tick();
  }
});

test("3.39.0: readings always have their tenth, and the demo's pretend texts are called pretend", async () => {
  const chat = chatAt("/today", { overview: bigOverview() });
  const all = chat.send("Which fridges need me?").text;
  assert.match(all, /Walk-in cooler 35\.0°F/);
  assert.match(all, /Prep fridge, grill 36\.4°F/);
  await chat.tick();
  const told = chat.send("Why did nobody get a text?").text || "(missed)";
  assert.match(told, /was a pretend text to 1 person \(demo\): nothing is really sent from the demo\./);
  assert.doesNotMatch(told, /phone network took/);
});

test("3.39.0: a different count at each location is priced location by location, as the calculator does", async () => {
  for (const pathname of ["/", "/pricing", "/console"]) {
    const chat = chatAt(pathname, { signedIn: pathname === "/console" });
    for (const words of ["How much for 15 fridges at one restaurant and 4 at the other?", "15 at one, 4 at the other: what would it cost?",
      "price for fifteen at one and four at the other"]) {
      const got = chat.send(words).text || "(missed)";
      assert.match(got, /^\*\*19 fridges or freezers across 2 locations \(15 and 4\): Growth, \$373 a month\.\*\*/, `${pathname}: ${words}: ${got}`);
      assert.match(got, /\$149 a month a location covers up to 10 fridges and freezers there; each one past that is \$15 a month: 5 past it at the one with 15, \$75 in all\./);
      await chat.tick();
    }
    const three = chat.send("How much for 12 at the first, 6 at the second and 3 at the third?").text || "(missed)";
    assert.match(three, /^\*\*21 fridges or freezers across 3 locations \(12, 6 and 3\): Growth, \$477 a month\.\*\*/, three);
    await chat.tick();
    const pharm = chat.send("Two pharmacies, 12 at one and 3 at the other: how much?").text || "(missed)";
    assert.match(pharm, /^\*\*15 fridges or freezers across 2 pharmacies \(12 and 3\): \$428 a month\*\*, \$199 a pharmacy\./, pharm);
    await chat.tick();
  }
});

test("3.39.0: signed in, nobody is told to start the free trial; on the trial they are told they are on it", async () => {
  for (const pathname of ["/console", "/today"]) {
    for (const words of ["How much does it cost?", "Which plan for my café?", "How much for 6 restaurants?", "How much for a pharmacy?"]) {
      const paid = chatAt(pathname, { overview: bigOverview() }).send(words).text || "(missed)";
      assert.doesNotMatch(paid, /Start with the free|start with the free|start a free trial/i, `${pathname}: ${words}: ${paid}`);
      const trial = chatAt(pathname, { overview: bigOverview({ trial: true }) }).send(words).text || "(missed)";
      assert.doesNotMatch(trial, /Start with the free|start with the free/, `${pathname}: ${words}`);
      assert.match(trial, /You're on the free trial now: nothing is billed until you choose a plan on the console's \*\*Money\*\* tab\./, `${pathname}: ${words}: ${trial}`);
    }
    assert.match(chatAt(pathname, { overview: bigOverview({ trial: true }) }).send("What's in the trial?").text || "", /^You're on it now\. The free trial lasts 14 days/);
    assert.match(chatAt(pathname, { overview: bigOverview() }).send("What's in the trial?").text || "", /^You're past the free trial/);
  }
  // A visitor still gets the trial.
  assert.match(chatAt("/pricing", { signedIn: false }).send("How much does it cost?").text, /Start with the free 14-day trial, no card\./);
});

test("3.39.0: on-call times read \"Fri Dec 4, 8:00 AM\", in the rotation's own time", async () => {
  const rota = [{ scope: "estate", on_shift_now: { contact_id: "C1", full_name: "Morgan Hale" },
    schedule: [{ full_name: "Morgan Hale", ends_local: "Sat 12 Dec 20:30 CST" }, { full_name: "Priya Lund", ends_local: "Sat 19 Dec 20:30 CST" }] }];
  const got = chatAt("/console", { overview: accountOverview(), rotations: rota }).send("Who is on call this week?").text;
  assert.match(got, /^On call now: \*\*Morgan\*\* until Sat Dec 12, 8:30 PM, then \*\*Priya\*\*\./);
  assert.doesNotMatch(got, /CST|20:30/);
});

test("3.39.0: \"Here's what I can tell you without an AI key:\" shows once in a chat, not before every answer", async () => {
  let given = null;
  const LEAD = "Here's what I can tell you without an AI key:";
  const R = { render: (text) => text };
  const win = { Pilot: { mount: (c) => { given = c; } }, location: { pathname: "/pricing", href: BASE + "/pricing" },
    localStorage: { getItem: () => null }, CLXWords: Words, PilotRender: R, PilotPanel: { MATCH_LEAD: LEAD } };
  vmx.runInNewContext(CONFIG, { window: win, URL, Promise, document: { getElementById: () => ({ dataset: { ...NAMES, ...DATA } }), title: "Pricing" } });
  const first = PANEL.welcomePills(given.welcomePills, BASE + "/pricing").find((p) => typeof p.answer === "function");
  first.answer({ facts: {}, ready: false, label: "How much is it?" });
  const local = { local: BASE + "/pricing" };
  // One drawing of the chat: two typed answers, then one pressed.
  const drawn = [R.render(`${LEAD}\n\nOne.`, null, local), R.render(`${LEAD}\n\nTwo.`, null, local), R.render("Three.", null, local)];
  assert.deepEqual(drawn, [`${LEAD}\n\nOne.`, "Two.", "Three."]);
  // The next drawing starts again from the top: the first keeps it.
  await Promise.resolve();
  assert.equal(R.render(`${LEAD}\n\nOne.`, null, local), `${LEAD}\n\nOne.`);
  // An AI's answer (not local) is never touched.
  assert.equal(R.render(`${LEAD} x`, null, undefined), `${LEAD} x`);
});

test("3.39.0: on a phone the words in the robot's corners are named for it to keep off; on a desktop nothing is added", () => {
  const make = (width) => {
    let given = null;
    const attrs = new Set();
    const text = { nodeType: 3, textContent: "Texts are not switched on" };
    const para = { childNodes: [text], setAttribute: (k) => attrs.add(k), removeAttribute: (k) => attrs.delete(k) };
    const doc = { getElementById: () => ({ dataset: { ...NAMES, ...DATA } }), title: "Today",
      querySelector: () => null,
      elementsFromPoint: (x, y) => (x > width - 80 && y > 812 - 130 ? [para] : []),
      createRange: () => ({ selectNodeContents() {}, getClientRects: () => [{ left: width - 70, right: width - 16, top: 760, bottom: 780, width: 54 }] }) };
    const win = { Pilot: { mount: (c) => { given = c; } }, location: { pathname: "/today", href: BASE + "/today" }, innerWidth: width, innerHeight: 812,
      localStorage: { getItem: () => "t" }, CLXWords: Words, KryozynToday: { overview: () => bigOverview(), did: () => null } };
    vmx.runInNewContext(CONFIG, { window: win, URL, document: doc });
    return { given, attrs };
  };
  const phone = make(375);
  assert.deepEqual(J(phone.given.yieldTo), ["[data-pilot-text]"]);
  assert.ok(phone.attrs.has("data-pilot-text"));
  assert.deepEqual(J(phone.given.gutter), ["body > .wrap"], "/today's column gets the side room <main> has elsewhere");
  const desk = make(1280);
  assert.deepEqual(J(desk.given.yieldTo), []);
  assert.ok(!desk.attrs.has("data-pilot-text"));
});

// ---------------------------------------------------------------- 3.40.0
//
// The phrasings a review asked with no key, and more like them: each with
// the built-in answer it must get. Signed in on /today with twenty units;
// the visitor's rows on /pricing. `topicFor` is the config's own answer to
// "which built-in answer does this get?"; the chat must also answer it.
const OWN_ONLY = new Set(["sites", "week", "warmest", "status", "bill"]);
const PHRASINGS = [
  // Wrong before 3.40.0.
  ["Does it work for a food truck?", "cafe"], ["Would this suit a food truck?", "cafe"], ["We do catering, is it for us?", "cafe"],
  ["Can I see it on my phone?", "mobile"], ["Does it work on mobile?", "mobile"], ["Can I check my fridges from my phone?", "mobile"],
  ["Will it work on an iPad?", "mobile"],
  ["Turn texts on", "texts"], ["How do I turn on text alerts?", "texts"], ["Switch on SMS", "texts"], ["Why are texts not switched on?", "texts"],
  ["enable texts", "texts"],
  ["Was the food in the walk-in freezer ok on Wednesday?", "food"], ["Is the food still OK?", "food"], ["Is the food in the walk-in safe?", "food"],
  ["was the stock ok yesterday", "food"], ["Is the food safe to eat?", "food"],
  ["What is Kryozyn?", "what"], ["what's kryozyn", "what"], ["What does Kryozyn do?", "what"], ["How does it work?", "what"],
  ["Tell me about Kryozyn", "what"], ["what is this app?", "what"],
  ["What temperature should my fridge be?", "limits"], ["How cold should a freezer be?", "limits"],
  ["What's the right temperature for a walk-in?", "limits"], ["What is the safe temperature for a fridge?", "limits"],
  ["what temp should the freezer be at", "limits"], ["ideal fridge temperature", "limits"],
  ["My freezer is at 15 degrees", "reading"], ["the walk-in is at 45", "reading"], ["my fridge says 44F", "reading"],
  ["Our freezer reads 2 degrees", "reading"], ["fridge is at 5C", "reading"],
  ["How many phone calls do I have left?", "callsleft"], ["how many calls are left on my trial", "callsleft"], ["calls remaining?", "callsleft"],
  // New.
  ["hi", "hello"], ["Hello!", "hello"], ["hey", "hello"], ["Hi there", "hello"], ["good morning", "hello"],
  ["insurance", "pack"], ["Can you help with my insurance?", "pack"], ["What does my insurer need?", "pack"],
  ["Will it help with an insurance claim?", "pack"],
  ["How is this different from a cheap thermometer alarm?", "versus"], ["Why not just buy a $20 thermometer?", "versus"],
  ["What's the difference between this and a fridge alarm?", "versus"], ["Is it better than a data logger?", "versus"],
  ["How long does setup take?", "setuptime"], ["how long to set up", "setuptime"], ["How quickly can I get it running?", "setuptime"],
  ["How long will it take to install?", "setuptime"],
  ["What do I do next?", "next"], ["What's next?", "next"], ["next steps", "next"], ["Where do I start?", "next"], ["what's left to set up", "next"],
  ["When does my trial end?", "trialend"], ["How many days left in my trial?", "trialend"], ["Is my trial over?", "trialend"],
  ["how long is the free trial", "trialend"],
  ["How much has this saved me?", "saved"], ["How much money have I saved?", "saved"], ["Has it paid for itself?", "saved"],
  ["what has it saved us", "saved"],
  ["Is anything about to go wrong?", "forecast"], ["Show me the forecast", "forecast"], ["Which fridge is going to fail?", "forecast"],
  ["anything at risk?", "forecast"], ["Are any units drifting?", "forecast"],
  ["How many fridges at Old Mill?", "sites"], ["How many fridges at each location?", "sites"],
  ["how many units do we have at Harbourside", "sites"], ["How many sensors do I have?", "sites"], ["fridges per location", "sites"],
  ["What is a sensor key?", "sensorkey"], ["I lost my sensor key", "sensorkey"], ["what's the key for?", "sensorkey"],
  ["How long has the walk-in been warm?", "alert"], ["How long is the wait before an alert?", "door"],
  // The earlier ones still go where they did.
  ["Which fridges need me?", "status"], ["Is my walk-in fridge OK?", "status"], ["What's wrong right now?", "status"],
  ["What's the walk-in at?", "status"], ["How cold is the bar fridge?", "status"],
  ["How much does it cost?", "price"], ["how much for 2 fridges?", "quote"], ["How much for 3 fridges at 2 locations?", "quote"],
  ["price for 3 restaurants, 8 fridges per restaurant", "quote"], ["Do you sell sensors?", "sensors"],
  ["What if the wifi goes down?", "power"], ["Do you call at night?", "calls"], ["What happens if nobody answers?", "calls"],
  ["Will a door left open wake us?", "door"], ["Who is on call?", "oncall"], ["How do I cancel?", "cancel"], ["Can I pause alerts?", "pause"],
  ["Change the bar fridge limit to 38", "limit"], ["How many alerts this week?", "week"], ["Which is warmest?", "warmest"],
  ["Do you have an app?", "app"], ["How do I add a second fridge?", "addunit"], ["Is there a contract?", "cancel"],
  ["Does it work in Canada?", "canada"], ["Is it hard to set up?", "easy"], ["How do I make the insurance pack?", "pack"],
  // 3.41.0: wrong or missed before.
  ["the bakery fridge alarm, what should i do", "alarm"], ["the walk-in is beeping", "alarm"], ["the prep fridge alarm keeps going off", "alarm"],
  ["Who can see my temperature readings?", "privacy"], ["Can your staff see my data?", "privacy"], ["Is my data private?", "privacy"],
  ["what sensor do you recommend for a walk-in freezer?", "sensors"], ["Which sensor is best for a freezer?", "sensors"],
  ["What did I pay last month?", "bill"], ["show me my last invoice", "bill"], ["When is my next bill?", "bill"],
  ["what if I have 12 fridges in one restaurant?", "quote"], ["We have 2 restaurants with 12 fridges each", "quote"],
  ["Change the walk-in limit to 38", "limit"],
  ["Do you do florists?", "sector"], ["good for a florist shop?", "sector"], ["Do you work with breweries?", "sector"],
  ["Is it for data centers?", "sector"], ["Do you do labs?", "sector"], ["What about ice cream shops?", "sector"],
  ["What happens after the trial?", "trialafter"], ["what happens when my trial ends?", "trialafter"],
  ["Can I add a second location?", "addsite"], ["How do I add another site?", "addsite"],
  ["Can I change the alert wait to 30 minutes?", "wait"], ["can it wait longer before texting?", "wait"],
  ["remove Theo from the account", "removeperson"], ["How do I remove someone from the team?", "removeperson"],
  ["How do I change Priya's phone number?", "number"], ["Morgan has a new mobile number", "number"],
  ["Which fridges need me right now?", "status"],
];

test("3.40.0: about a hundred phrasings each get the answer they are after, with no key", async () => {
  assert.ok(PHRASINGS.length >= 100, String(PHRASINGS.length));
  for (const [where, signedIn] of [["/today", true], ["/console", true], ["/pricing", false]]) {
    for (const [words, want] of PHRASINGS) {
      if (!signedIn && OWN_ONLY.has(want)) continue;
      const chat = chatAt(where, { signedIn, overview: bigOverview() });
      assert.equal(chat.given.topicFor(words), want, `${where}: ${words}`);
      const got = chat.send(words);
      assert.ok(got.label, `${where}: ${words}: answered, not missed (${got.note})`);
      await chat.tick();
    }
  }
});

test("3.40.0: 'should', 'what is' and 'how long' never get the list of your fridges by their words alone", async () => {
  for (const words of ["What temperature should my fridge be?", "What is the meaning of the blue dot?", "How long have I been a customer?",
    "How should I clean the fridge?"]) {
    const chat = chatAt("/today", { overview: bigOverview() });
    assert.notEqual(chat.given.topicFor(words), "status", words);
    assert.doesNotMatch(chat.send(words).text || "", /^- \*\*|need you, worst first|Nothing needs you right now/, words);
    await chat.tick();
  }
});

// The demo as /api/console/overview gives it: a plan with calls and the
// forecast, Getting started, the food kept, and a closed alert on the
// walk-in freezer last Wednesday.
function lastWeekday(day) {
  const d = new Date();
  d.setHours(12, 0, 0, 0);
  while (d.getDay() !== day || d.getTime() > Date.now()) d.setDate(d.getDate() - 1);
  return d;
}
function demoOverview({ trial = false, starter = false, steps = 4 } = {}) {
  const o = bigOverview({ trial });
  const wed = lastWeekday(3);
  const iso = (ms) => new Date(ms).toISOString();
  o.sensors.push({ sensor_id: "OML-WIF-1", location_name: "Old Mill / Walk-in freezer", site_name: "Old Mill", unit_kind: "freezer",
    last_temperature_display: -1.2, temperature_unit: "F", danger_above_display: 10, condition: { state: "ok" } });
  o.incidents.push({ incident_id: "INC-W", sensor_id: "OML-WIF-1", state: "resolved", opened_at: iso(wed.getTime()), out_since: iso(wed.getTime()),
    out_until: iso(wed.getTime() + 90 * 60000), resolved_at: iso(wed.getTime() + 92 * 60000), peak_display: 21, temperature_display: 12.5,
    product_disposition: "unaffected", food_auto: false, food_answered_at: iso(wed.getTime() + 90 * 60000) });
  // Last Wednesday's alert is the walk-in freezer's at Old Mill only
  // (3.41.0): bigOverview's 72-hours-ago alert on "Walk-in freezer 1" fell
  // on a Wednesday afternoon too when the suite ran on a Saturday
  // afternoon, and was the newer of the two. Here it is the walk-in
  // cooler's.
  o.incidents.filter((i) => i.incident_id === "INC-8").forEach((i) => { i.sensor_id = "U-0"; });
  o.entitlements = trial ? { voice_escalation: false, predictive_forecasting: false,
    calls: { mode: "trial", left: 3, total: 5, words: "Your free trial includes 5 phone calls (about a minute each) so you can hear what an alert call sounds like; 3 left. After that, alerts are texts only until you move to a plan with calls (Growth)." } }
    : starter ? { voice_escalation: false, predictive_forecasting: false, calls: { mode: "none", left: null, total: null, words: "Your Starter plan sends texts, not phone calls. Growth includes phone calls." } }
      : { voice_escalation: true, predictive_forecasting: true, calls: { mode: "plan", left: null, total: null, words: "If nobody answers the text, we phone them until somebody does." } };
  o.account.expires_at = trial ? "2026-12-12T09:00:00Z" : "2027-12-28T11:09:31Z";
  o.account.days_to_expiry = trial ? 9 : 395;
  const names = ["Add your first sensor", "Add who gets alerts", "First reading received", "Send a test alert"];
  o.first_day = { steps: names.map((label, n) => ({ key: String(n), label, detail: n === 3 ? "A text that starts TEST and says nothing is wrong." : "", done: n < steps })),
    done: steps, total: 4, complete: steps === 4 };
  o.roi = { period_days: 30, quantified_saves: 1, loss_avoided_usd: 4000, return_multiple: 8.9 };
  o.this_month = { food_usd: 4000 };
  o.sensors[2].hours_until_breach = 1.5; // quiet: its forecast is stale, never "heading the wrong way"
  o.sensors[3].hours_until_breach = 2.4;
  return o;
}

test("3.40.0: the eight wrong answers are put right, each true to the product", async () => {
  const chat = chatAt("/today", { overview: demoOverview() });
  const say = async (words) => { const got = chat.send(words).text || "(missed)"; await chat.tick(); return got; };
  assert.match(await say("Does it work for a food truck?"), /^Yes, while its fridges have internet: a food truck is watched the same way as a restaurant, on the restaurant plan with its food limits\./);
  const phone = await say("Can I see it on my phone?");
  assert.match(phone, /^Yes\. The console and the Simple view \(Today\) work in your phone's browser/);
  assert.match(phone, /\*\*Add to Home Screen\*\*/);
  assert.doesNotMatch(phone, /presses 1|phones the people/);
  const texts = await say("Turn texts on");
  assert.match(texts, /^Alert texts go to everyone under \*\*Who gets alerts\*\* on the console's \*\*Team\*\* tab who isn't muted/);
  assert.match(texts, /Texts are not switched on for this server/);
  const wed = await say("Was the food in the walk-in freezer ok on Wednesday?");
  assert.match(wed, /^\*\*Old Mill \/ Walk-in freezer\*\* went out of range at .+ for 1 hour 30 minutes, at worst 21\.0°F \(limit 10°F\), and it is closed\. It was recorded as \*\*kept\*\*/, wed);
  assert.match(await say("Was the food in the walk-in freezer ok on Thursday?"),
    /^No alert on \*\*Walk-in freezer 1\*\*, \*\*Walk-in freezer 2\*\*, \*\*Walk-in freezer\*\* and 1 more on Thursday: they stayed in range/);
  assert.match(await say("What is Kryozyn?"), /^Kryozyn watches the temperature of your fridges and freezers, day and night\./);
  const limits = await say("What temperature should my fridge be?");
  assert.match(limits, /^\*\*A fridge: under 41°F\. A freezer: under 10°F\.\*\*/);
  assert.match(limits, /\*\*Change limit\*\*/);
  const fifteen = await say("My freezer is at 15 degrees");
  assert.match(fifteen, /^\*\*15°F is 5° over a freezer's 10°F limit\.\*\* Check the door is shut/);
  assert.match(fifteen, /If it stays over for 30 minutes, everyone on call is texted\./);
  assert.doesNotMatch(fifteen, /haven't added/);
  assert.match(await say("My fridge is at 38"), /^\*\*38°F is under a fridge's 41°F limit\*\*, so that's fine/);
  assert.match(await say("the walk-in is at 60"), /^\*\*60°F is 19° over a fridge's 41°F limit\.\*\*[\s\S]*this far over, it alerts at once/);
  assert.match(await say("How many phone calls do I have left?"), /^Your plan includes phone calls, so there's no number of calls to use up/);
  const trial = chatAt("/today", { overview: demoOverview({ trial: true }) });
  assert.match(trial.send("How many phone calls do I have left?").text, /^Your free trial includes 5 phone calls .+; 3 left\./);
  const starter = chatAt("/console", { overview: demoOverview({ starter: true }) });
  assert.match(starter.send("How many calls do I have left?").text, /^Your Starter plan sends texts, not phone calls\./);
  assert.match(chatAt("/pricing", { signedIn: false }).send("How many phone calls do I have left?").text,
    /^Signed in, I can tell you from your plan\. The free trial includes 5 alert calls/);
});

test("3.40.0: hello, insurance, a thermometer alarm, setup time, the next step, the trial, the food kept, the forecast, each location and the key", async () => {
  const chat = chatAt("/today", { overview: demoOverview({ steps: 2 }) });
  const say = async (words) => { const got = chat.send(words).text || "(missed)"; await chat.tick(); return got; };
  assert.match(await say("hi"), /^Hi\. Ask me about your fridges and freezers/);
  assert.match(chatAt("/pricing", { signedIn: false }).send("Hello!").text, /^Hi\. I'm Pilot\. I can tell you what Kryozyn costs/);
  assert.match(await say("Can you help with my insurance?"), /\*\*Insurance packs\*\*|insurance pack/i);
  assert.match(chatAt("/", { signedIn: false }).send("insurance").text, /^Your insurer gets an \*\*insurance pack\*\*/);
  const versus = await say("How is this different from a cheap thermometer alarm?");
  assert.match(versus, /^A thermometer alarm beeps in the kitchen, so it only helps if somebody is there to hear it\./);
  assert.match(versus, /texted within 60 seconds/);
  assert.match(await say("How long does setup take?"), /^About five minutes for the first fridge, once the sensor is in your hands/);
  assert.match(await say("What do I do next?"), /^Next under \*\*Getting started\*\* \(2 steps of 4 done\): \*\*First reading received\*\*\./);
  assert.match(chatAt("/today", { overview: demoOverview() }).send("What do I do next?").text, /^Getting started is done\. Right now an alert needs somebody/);
  assert.match(await say("When does my trial end?"), /^You're not on the free trial: you're on Growth\./);
  const trial = chatAt("/today", { overview: demoOverview({ trial: true }) });
  assert.match(trial.send("When does my trial end?").text, /^\*\*Your free trial ends on Dec 12, 2026: 9 days left\.\*\* Nothing is billed during it/);
  assert.match(chatAt("/pricing", { signedIn: false }).send("When does the trial end?").text, /^The free trial lasts 14 days/);
  assert.match(await say("How much has this saved me?"), /^\*\*About \$4,000 of food kept in the last 30 days\*\*: 1 closed alert with the food recorded as kept\. It is an estimate of what a typical fridge or freezer of each kind holds, 8\.9× the subscription\./);
  const forecast = await say("Is anything about to go wrong?");
  assert.match(forecast, /^\*\*Heading the wrong way:\*\*\n- \*\*Walk-in cooler\*\* 35\.0°F: at this rate it passes its limit in about 2 hours/, forecast);
  assert.match(forecast, /already out of range: that's an alert, not a forecast/);
  assert.match(chatAt("/today", { overview: demoOverview({ starter: true }) }).send("Show me the forecast").text, /Your plan doesn't include it; Growth does\./);
  assert.equal(await say("How many fridges at Old Mill?"), "**Old Mill**: 10 fridges and freezers.");
  assert.match(await say("How many fridges at each location?"), /^\*\*20 fridges and freezers across 2 locations:\*\*\n- Old Mill: 10\n- Harbourside: 9\n- Not at a location yet: 1$/);
  const key = await say("What is a sensor key?");
  assert.match(key, /^A sensor key is the password one sensor uses to send its readings to Kryozyn\./);
  assert.match(key, /\*\*New key\*\*/);
});

// ---------------------------------------------------------------- 3.41.0
//
// The review's confident wrong answers and misses, each answered from the
// product's own rules and the page's own data.
function bakeryAlert() {
  const o = demoOverview();
  const bakery = o.sensors.find((u) => u.location_name === "Prep fridge, bakery");
  bakery.location_name = "Commissary / Prep fridge, bakery";
  bakery.last_temperature_display = 46.5;
  bakery.condition = { state: "alert", too: "warm", out_since: o.incidents[0].out_since };
  bakery.alert_after_minutes = 20;
  const walk = o.sensors.find((u) => u.sensor_id === "WALKIN-1");
  walk.condition = { state: "ok" };
  walk.last_temperature_display = 37.1;
  o.incidents[0].sensor_id = bakery.sensor_id;
  o.incidents[0].peak_display = 46.5;
  return o;
}
const DEMO_BILLS = { ledger: { invoices: [{ invoice_id: "INV-006732", number: "CLX-2026-0001", state: "paid", total_usd: 447,
  issued_at: new Date(Date.now() - 3600 * 1000).toISOString(), paid_at: new Date(Date.now() - 3600 * 1000).toISOString(),
  due_at: "2026-12-28T12:44:25Z", lines: [{ description: "Restaurants & Cafés — 3 locations (20 sensors)" }] }] },
  contract: { next_billing_at: "2026-12-20T12:44:23Z" } };

test("3.41.0: an alarm on a unit named gets that unit's alert, never the pitch for its trade", async () => {
  for (const where of ["/today", "/console"]) {
    const chat = chatAt(where, { overview: bakeryAlert() });
    const bakery = chat.send("the bakery fridge alarm, what should i do").text || "(missed)";
    assert.match(bakery, /^\*\*Commissary \/ Prep fridge, bakery\*\* is too warm: 46\.5°F, above its 41°F limit\./, `${where}: ${bakery}`);
    assert.match(bakery, /Nobody has answered yet\. Press \*\*I'm on it\*\*/);
    assert.doesNotMatch(bakery, /watched the same way as a restaurant|Starter is \$59/);
    await chat.tick();
    const beep = chat.send("the walk-in is beeping").text || "(missed)";
    assert.match(beep, /^No alert is open on any of the \d+ that fit: all are within their limits\./, `${where}: ${beep}`);
    assert.match(beep, /A beep in the kitchen is the unit's own alarm, not Kryozyn's/);
    assert.match(beep, /The alert open now is on \*\*Commissary \/ Prep fridge, bakery\*\*/);
    await chat.tick();
  }
  // Nothing open on the bakery fridge: what it reads, and what to do.
  const calm = chatAt("/today", { overview: demoOverview() });
  const quiet = calm.send("the bakery fridge alarm, what should i do").text;
  assert.match(quiet, /^No alert is open on \*\*Prep fridge, bakery\*\*: it reads 35\.0°F, under its 41°F limit\./, quiet);
  assert.match(quiet, /Check the door is shut and sealing/);
  // Signed out: what happens when a fridge is warm, never the bakery pitch.
  const visitor = chatAt("/", { signedIn: false }).send("the bakery fridge alarm, what should i do").text;
  assert.match(visitor, /^When a fridge stays out of range past its wait, everyone on call is texted/);
  // A bakery as a business is still a bakery.
  assert.match(chatAt("/", { signedIn: false }).send("Do you work for a bakery?").text, /^Yes: a bakery is watched the same way as a restaurant/);
});

test("3.41.0: who can see the readings and which sensor to buy are answered before anything about your fridges", () => {
  for (const [where, signedIn] of [["/", false], ["/pricing", false], ["/today", true]]) {
    const who = chatAt(where, { signedIn, overview: bigOverview() }).send("Who can see my temperature readings?").text || "(missed)";
    assert.match(who, /^The people who can sign in to your account see your readings: owners, operators and viewers/, `${where}: ${who}`);
    assert.match(who, /Nobody else gets your readings in bulk\./);
    assert.match(who, /\[Read the privacy statement\]\(\/legal#privacy\)/);
    assert.doesNotMatch(who, /Sign in to see your fridges|need you/);
    const sensor = chatAt(where, { signedIn, overview: bigOverview() }).send("what sensor do you recommend for a walk-in freezer?").text || "(missed)";
    assert.match(sensor, /^We don't sell sensors\. We don't back one brand either/, `${where}: ${sensor}`);
    assert.match(sensor, /probe on a thin cable/);
    assert.match(sensor, /\[Which sensors work, brand by brand\]\(\/help\/sensors\)/);
  }
});

test("3.41.0: the bills come from the page's own bills, never the price list", async () => {
  const chat = chatAt("/console", { overview: accountOverview(), bills: DEMO_BILLS });
  const paid = chat.send("What did I pay last month?").text || "(missed)";
  const month = new Date(new Date().getFullYear(), new Date().getMonth() - 1, 1).toLocaleDateString("en-US", { month: "long" });
  assert.match(paid, new RegExp(`^Nothing was billed in ${month}\\. Your last bill: \\*\\*\\$447 on [A-Z][a-z]{2} \\d{1,2}, \\d{4}\\*\\*, paid [A-Z][a-z]{2} \\d{1,2}, \\d{4}, for Restaurants & Cafés — 3 locations \\(20 sensors\\)\\. It's your first bill\\.`), paid);
  assert.match(paid, /\[Open the bill\]\(\/console#invoice=INV-006732\)/);
  assert.doesNotMatch(paid, /Starter \$59|a month a location/);
  await chat.tick();
  assert.match(chat.send("show me my last invoice").text, /^Your last bill: \*\*\$447 on /);
  await chat.tick();
  assert.match(chat.send("When is my next bill?").text, /^\*\*Your next bill is on Dec 20, 2026\*\*: about \$447, for 3 locations \(20 sensors\), at the units you have today\. \[Open your bills\]\(\/console#bills\)/);
  // /today loads no bills: where they are, and the plan.
  const today = chatAt("/today", { overview: accountOverview() }).send("What did I pay last month?").text;
  assert.match(today, /^Your bills are on the console's \*\*Money\*\* tab, under \*\*Bills\*\*/);
  assert.match(today, /\[Open your bills\]\(\/console\?view=full#bills\)/);
  // On the trial, nothing is billed.
  assert.match(chatAt("/console", { overview: accountOverview({ trial: true }), bills: DEMO_BILLS }).send("When is my next bill?").text,
    /^You're on the free trial: nothing is billed\./);
});

test("3.41.0: a count in one place is priced as the calculator prices it; a limit lists every unit that fits, with its limit", async () => {
  for (const [where, signedIn] of [["/", false], ["/today", true]]) {
    const q = chatAt(where, { signedIn, overview: bigOverview() }).send("what if I have 12 fridges in one restaurant?").text || "(missed)";
    assert.match(q, /^\*\*12 fridges: Growth, \$179 a month\*\* \(Starter covers up to 3\)\./, `${where}: ${q}`);
    assert.match(q, /each one past that is \$15 a month: 2 more is \$30/);
  }
  const o = demoOverview();
  const limit = chatAt("/today", { overview: o }).send("Change the walk-in limit to 38").text;
  const walkIns = o.sensors.filter((u) => /walk-in/i.test(u.location_name || "")).length;
  assert.match(limit, new RegExp(`The ${walkIns} that fit:\\n`), limit);
  assert.match(limit, /- \*\*Walk-in freezer\*\* at Old Mill \(limit 10°F\)/);
  assert.match(limit, /\(limit 41°F\)/);
  assert.doesNotMatch(limit, /now over/);
  assert.equal((limit.match(/^- /gm) || []).length, walkIns);
});

test("3.41.0: any sector with a page is recognised, with its page and its price", () => {
  for (const words of ["Do you do florists?", "good for a florist shop?"]) {
    const got = chatAt("/", { signedIn: false }).send(words).text || "(missed)";
    assert.match(got, /^Yes: Kryozyn is set up for florists, with limits of their own\./, words);
    assert.match(got, /The price: priced for what you run: ask for a quote on its page\./);
    assert.match(got, /\[Kryozyn for florists\]\(\/for\/florists\)/);
  }
  assert.match(chatAt("/", { signedIn: false }).send("What about ice cream shops?").text, /\$149 a month per location, for up to 10 fridges and freezers there\.[\s\S]*\(\/for\/ice-cream-shops\)/);
  assert.match(chatAt("/pricing", { signedIn: false }).send("Do you work with breweries?").text, /\(\/for\/breweries\)/);
  assert.match(chatAt("/pricing", { signedIn: false }).send("Is it for data centers?").text, /\(\/for\/cybersecurity\)/);
  assert.match(chatAt("/pricing", { signedIn: false }).send("Do you do labs?").text, /\(\/for\/medical_lab\)/);
  // Every sector on the script tag is reached by its own page's name.
  for (const row of SECTORS_DATA.split(";")) {
    const [slug, name] = row.split("|");
    if (["restaurant", "pharmacy", "butchers"].includes(slug)) continue;
    const got = chatAt("/", { signedIn: false }).send(`Do you work for ${name.toLowerCase()}?`).text || "(missed)";
    assert.ok(got.includes(`(/for/${slug})`), `${name}: ${got}`);
  }
  // Words that only look like a sector stay where they were.
  const plain = chatAt("/", { signedIn: false });
  assert.equal(plain.given.topicFor("Is my data private?"), "privacy");
  assert.notEqual(plain.given.topicFor("Does it work in my country?"), "sector");
});

test("3.41.0: after the trial, a second location, the alert wait, taking someone off and a new number, true to the console", async () => {
  const trial = chatAt("/console", { overview: demoOverview({ trial: true }) });
  const after = trial.send("What happens after the trial?").text || "(missed)";
  assert.match(after, /^Your trial ends on \*\*Dec 12, 2026\*\*\. When it ends, nothing is billed and nothing moves you to a paid plan by itself\./);
  assert.match(after, /still watched, alerts and all, for 14 more days while you decide\. After that, alerts stop until you choose a plan\. Nothing is deleted/);
  await trial.tick();
  assert.match(trial.send("Can I add a second location?").text, /^Yes\. On the console's \*\*Setup\*\* tab, open \*\*Your locations\*\*, type the new one's name and press \*\*Add site\*\*\. The free trial takes as many locations as you like/);
  await trial.tick();
  assert.match(chatAt("/", { signedIn: false }).send("What happens after the trial?").text, /^When it ends, nothing is billed/);
  const growth = chatAt("/today", { overview: demoOverview() });
  assert.match(growth.send("Can I add a second location?").text, /On Growth a restaurant pays \$149 a month a location, for up to 10 fridges and freezers there/);
  await growth.tick();
  const wait = growth.send("Can I change the alert wait to 30 minutes?").text || "(missed)";
  assert.match(wait, /^Yes\. Press \*\*Alert wait\*\* on the unit's card in the console \(the \*\*Fridges\*\* tab\), pick \*\*30 min\*\*, and press \*\*Save\*\*\./, wait);
  assert.match(wait, /a reading 15°F or more past the limit is sent at once/);
  await growth.tick();
  const off = growth.send("remove Theo from the account").text || "(missed)";
  assert.match(off, /To stop alert texts and calls to Theo: press \*\*Remove\*\* beside the name under \*\*Who gets alerts\*\*/);
  assert.match(off, /To stop Theo signing in: press \*\*Remove\*\* beside the name under \*\*People who can sign in\*\*/);
  assert.match(off, /\[Open People who can sign in\]\(\/console\?view=full#people\)/);
  await growth.tick();
  const num = growth.send("How do I change Priya's phone number?").text || "(missed)";
  assert.match(num, /^Under \*\*Who gets alerts\*\* on the console's \*\*Team\*\* tab, press \*\*Change number\*\* beside Priya/, num);
  assert.match(num, /Priya keeps the same place in the call order/);
});

test("3.41.0: nothing needing you is one line; a miss offers two sensible answers, never 'It's fixed now' with no alert open", async () => {
  const calm = bigOverview();
  calm.incidents = calm.incidents.filter((i) => i.state === "resolved");
  calm.sensors.forEach((u) => { u.condition = { state: "ok" }; });
  for (const words of ["Which fridges need me?", "Which fridges need me right now?"]) {
    const got = chatAt("/today", { overview: calm }).send(words).text || "(missed)";
    assert.equal(got.split("\n").length, 1, `${words}: ${got}`);
    assert.match(got, /^Nothing needs you right now: all \d+ (?:fridges )?are within their limits/, words);
  }
  const busy = chatAt("/today", { overview: bigOverview() }).send("Which fridges need me right now?").text;
  assert.match(busy, /^These 2 need you, worst first:/, busy);
  // A miss: two answers under the note, neither of them about an alert
  // when none is open.
  const chat = chatAt("/today", { overview: calm });
  const miss = chat.send("can you sing me a song about penguins");
  assert.equal(miss.label, null);
  await chat.tick();
  const offered = chat.given.chips(chat.given.screen()).slice(0, 2).map((c) => c.label);
  assert.equal(offered.length, 2);
  for (const label of offered) assert.ok(!["It's fixed now", "What does this alert mean?"].includes(label), label);
});

test("3.41.0: the calls answer has no owner jargon", () => {
  const got = chatAt("/today", { overview: demoOverview() }).send("How many phone calls do I have left?").text;
  assert.doesNotMatch(got, /Spending limits|Advanced/);
  assert.match(got, /daily cap on calls/);
});
// ---------------------------------------------------------------- 3.42.0: an adversarial table
//
// Questions a visitor (signed out, /pricing), an owner (signed in on
// /today, a paid plan) and a business on the free trial (signed in on the
// console) really type: varied wording, typos, run-ons, and unit names or
// food words that turn up only in passing. Each has the answer it must
// get, "a|b" when either is right, and "miss" where no built-in answer
// fits and the honest note is right: a confident wrong answer is worse
// than a miss. A string is for all three; {v, o, t} for each.
const ADVERSARIAL = [
  // Prices, fees, the year.
  ["how much is it", "price"], ["Whats the price per month?", "price"], ["is it expensive", "price"],
  ["how much dose it cost", "price"], ["Is the price per fridge or per location?", "price"],
  ["how much for 4 fridges", "quote"], ["price for 2 restaurants", "quote"], ["how much for 6 restaurants", "quote"],
  ["how much does it cost for a pharmacy", "pharmacy"],
  ["Is there a setup fee?", "fees"], ["any hidden fees?", "fees"], ["are there any extra charges I should know about", "fees"],
  ["Do you charge for installation?", "fees|easy"], ["Are texts extra?", "fees"], ["do you charge per text", "fees|price"],
  ["is there a discount if I pay yearly", "yearly"], ["Can I pay annually?", "yearly"], ["yearly plan price?", "yearly"],
  ["do you do an annual discount", "yearly"], ["Is it cheaper if I pay for the whole year?", "yearly"],
  ["what does it cost per year", "yearly|price"], ["is there a monthly contract or yearly", "cancel|yearly"],
  ["is there a cheaper plan for small cafes", "plan|price"], ["which plan should i pick for my coffee shop", "plan"],
  ["Whats the difference between starter and growth", "plan"], ["do i need growth for phone calls", "plan|calls"],
  ["Do you have a chain discount?", "miss|price"], ["do sensors come included in the price", "sensors|price"],
  ["hi how much is it for a small cafe with 2 fridges and a freezer", "quote|plan|price"],
  ["we have a walk in and two reach ins and a bar fridge what plan do we need", "plan|quote"],
  ["food truck pricing", "cafe|price"], ["how many sensors can I have", "price|plan|trial|addunit|miss"],
  ["Does it work with 5 fridges?", "quote|plan|cafe"],
  // Cancelling, the data, how long it is kept.
  ["can i get a refund", "cancel"], ["how do i cancel", "cancel"], ["I want to cancel", "cancel"],
  ["is there a contract or can i leave anytime", "cancel"],
  ["What happens to my data if I cancel?", "retention"], ["if i cancel do you delete everything", "retention"],
  ["How long do you keep my temperature records?", "retention"], ["how long is data stored", "retention"],
  ["how far back can i see readings", "retention"], ["do you keep records for 2 years?", "retention"],
  ["how long do you store data after I cancel", "retention"], ["do you delete readings", "retention"],
  ["how many months of history do you keep", "retention"], ["will I lose my records if I stop paying", "retention|trialafter"],
  ["Can I export my data before I leave?", "retention|privacy"], ["please delete my account", "retention|cancel|privacy"],
  ["is my data safe", "privacy"], ["who can see our readings", "privacy"], ["do you sell my data", "privacy"],
  // The trial, signing up, the demo.
  ["how long is the free trial", "trialend|trial"], ["do I need a credit card for the trial", "trial"],
  ["whats included in the trial", "trial"], ["is ther a free trail", "trial|miss"],
  ["what happens when the trial ends", "trialafter"], ["will you charge me automatically after the trial?", "trialafter|trial"],
  ["do I have to pay when the trial ends", "trialafter"], ["how do I sign up", { v: "signup", o: "signup|miss", t: "signup|miss" }],
  ["can i try it without buying sensors", "try"], ["is there a demo", "demo|try"], ["show me a demo", "demo|try"],
  ["am I on the trial", { v: "trial|trialend|miss", o: "trialend|trial|bill", t: "trialend|trial|bill" }],
  ["can I add more than 5 sensors on the trial", "trial|addunit|miss"],
  // Who it is for.
  ["I have a food truck, is that ok?", "cafe"], ["will it work in a food truck", "cafe"], ["do you support bakeries", "cafe"],
  ["I run a small deli", "cafe|miss"], ["We're a pizza place with 3 fridges, does this work?", "cafe|quote"],
  ["is it good for a coffee shop", "cafe"], ["Will it work for my restaurant?", "cafe"], ["cn i use it 4 my resturant", "cafe|miss"],
  ["do you work with flower shops", "sector"], ["I own a flower shop", "sector"], ["flower shop", "sector"],
  ["is this for florists", "sector"], ["can a brewery use it", "sector"], ["can i use it for a florist cooler", "sector|miss"],
  ["We're a flower shop with one cooler", "sector|miss"], ["do you do pharmacies", "pharmacy"],
  ["is it suitable for a vaccine fridge", "pharmacy|sector"],
  ["Can I use it for two restaurants?", "addsite"], ["I have three restaurants, can I see them all in one place?", "addsite"],
  ["We own 2 cafes", "addsite|quote"], ["does it work for multiple locations", "addsite"], ["can one account cover two sites", "addsite"],
  ["do you work in canada", "canada"], ["does it show celsius", "canada"], ["do you work in the UK?", "miss"],
  ["Is it HACCP compliant?", "haccp"], ["does it help with haccp", "haccp"], ["is haccp included", "haccp"],
  ["do you give me a haccp plan", "haccp"], ["what about food safety compliance", "haccp|log"],
  ["Will this keep the health inspector happy?", "log|haccp"], ["can i print a temperature log for the inspector", "log"],
  ["do I still need paper logs", "log|haccp"],
  ["I have a fridge full of food at home, can I use this?", "miss|cafe"], ["we sell ice cream from a cart", "sector|cafe|miss"],
  // Sensors and batteries.
  ["Which sensor should I get for a walk-in freezer?", "sensors"], ["what sensors work with this", "sensors"],
  ["do you sell the sensors", "sensors"], ["does it work with monnit", "sensors"], ["can i use my existing sensorpush sensors", "sensors"],
  ["will a sensor work inside a metal walk in", "sensors"], ["best probe for a chest freezer", "sensors"],
  ["what thermometer do i need", "sensors"], ["how much are the sensors", "sensors|price"], ["wat sensors do i need", "sensors"],
  ["Which sensor for a pharmacy vaccine fridge?", "sensors|pharmacy"], ["what's the range of the sensor", "sensors"],
  ["Can I put a sensor in a wine fridge?", "sensors|sector|miss"], ["do the sensors fit in a small bar fridge", "sensors|miss"],
  ["Does the walk-in freezer need its own sensor?", "sensors|addunit|connect"], ["do the sensors need wifi", "sensors|power"],
  ["can the sensor run on cellular", "sensors|miss"], ["does it work without wifi", "power|sensors"],
  ["Do the sensors need batteries?", "battery"], ["how long do sensor batteries last", "battery"],
  ["what happens when the battery dies", "battery"], ["the sensor battery is dying", "battery"], ["low battery warning?", "battery"],
  ["the sensor battery is low what do i do", "battery"], ["how do I know if a battery is dying", "battery"], ["replace battery", "battery"],
  ["do I need an electrician", "easy"], ["is it hard to install", "easy"], ["how long does setup take", "setuptime"],
  ["how do i connect a sensor", "connect"], ["how do i set it up", "connect|steps|easy|setuptime"], ["What's a sensor key", "sensorkey"],
  // How the alerts work.
  ["how quickly will i get a text", "speed"], ["How fast are the alerts?", "speed"],
  ["how long does it take for the text to arrive", "speed"], ["is the text instant", "speed"],
  ["if the freezer breaks how soon do i find out", "speed"], ["how long until i get the text after the fridge gets warm", "speed|door"],
  ["will a door left open set it off", "door"], ["what if someone leaves the walk-in door open for a minute", "door"],
  ["Will it wake me up every time the door opens?", "door"], ["I'm worried about false alarms", "door"],
  ["too many false alarms with our old system", "door|miss"],
  ["my last system texted me at 3am for nothing because someone opened the door, does yours do that", "door"],
  ["my staff keep leaving the walk in door open", "door"], ["can it tell me who left the door open", "door|miss"],
  ["how long does it wait before alerting", "door|wait"], ["Can I change how long it waits before alerting?", "wait"],
  ["can i make it wait longer before it texts", "wait"], ["change the alert wait to 30 minutes", "wait"],
  ["can i change the temperature limit", "limit"], ["can i set my own limits", "limit"], ["can I set different limits for each fridge", "limit"],
  ["how do I change the limit on the walk-in to 38", "limit"], ["can operators change limits", "limit|miss"],
  ["what temperature does it alert at", "limits"], ["what temp should a walk in cooler be", "limits"],
  ["what should my freezer be set to", "limits"], ["how cold should the walk in be", "limits"],
  ["Is 38 ok for a fridge", "limits|reading"], ["is 5 degrees too warm for a freezer", "limits|reading"],
  ["do you call at 3am", "calls"], ["Do you phone if nobody answers the text?", "calls"], ["what if nobody responds", "calls"],
  ["do u call at nite", "calls|miss"], ["what if my phone is off", "calls|miss"], ["can it call my landline", "calls|miss"],
  ["How do I stop getting texts at 3am?", "quiet"], ["can I turn off alerts at night for one person", "quiet"],
  ["does it have quiet hours", "quiet"], ["can I mute someone", "quiet"], ["my manager doesnt want texts on her day off", "quiet|miss"],
  ["i dont want to be woken up at night by texts", "quiet"], ["stop texting me at night", "quiet"],
  ["Can I stop alerts while we're closed for christmas", "pause"], ["can i pause it for the winter", "pause"],
  ["what if the wifi goes down", "power"], ["what happens in a power cut", "power"], ["what if the sensor goes offline", "power"],
  ["if the power goes out at night do you still text me", "power"], ["does it still work if the internet is down at night", "power"],
  ["who gets the texts", "team"], ["Who gets the alerts?", "team"], ["can more than one person get alerts", "team"],
  ["can i add my chef to the alerts", "team"], ["how many people can get texts", "team"], ["can it text my whole team at once", "team"],
  ["Who gets the alerts right now?", "team|oncall"], ["add my sous chef to the texts", "team"],
  ["Do you send emails as well as texts?", "email"], ["Can I get alerts by email", "email"],
  ["can I get texts only for the walk-in", "miss|team"],
  ["is there an app", "app"], ["can i see it on my phone", "mobile"], ["does it work on android", "app|mobile"],
  ["how is this better than a cheap fridge thermometer", "versus"], ["what does kryozyn do", "what"], ["how does it work", "what"],
  ["what is this", "what|miss"], ["hi", "hello"], ["hello there", "hello"],
  ["what's the insurance pack", "pack"], ["will my insurer accept this", "pack"],
  ["can it help me make an insurance claim after a power cut", "pack"], ["can i get my insurance cheaper with this", "pack|miss"],
  // Not ours to answer: the honest note, never a guess.
  ["thanks", "miss"], ["ok", "miss"], ["lol", "miss"], ["What's the weather tomorrow?", "miss"], ["can you write me a menu", "miss"],
  ["how do I cook a turkey safely", "miss"], ["what's the best chicken recipe", "miss"], ["do you have a phone number I can call", "miss"],
  ["Can I talk to a human?", "miss"], ["is your company legit", "miss"], ["Where are you based?", "miss"],
  ["can i monitor the temperature of my food deliveries", "miss"], ["can it track food expiry dates", "miss"], ["does it count stock", "miss"],
  ["can it monitor my oven", "miss"], ["can I monitor humidity", "miss"], ["how do I clean the fridge", "miss"],
  ["my fridge is making a weird noise", "miss"], ["the freezer door seal is broken, who should I call", "miss"],
  ["can you recommend a fridge repair company", "miss"], ["what fridge should I buy for my restaurant", "miss"],
  ["is it ok to keep raw chicken above cooked food", "miss"], ["how long can food stay out of the fridge", "food|miss"],
  ["is it safe to refreeze thawed meat", "miss|food"], ["do I need a thermometer in every fridge by law", "miss|log|haccp|sensors"],
  ["can it post to slack", "miss"], ["do you integrate with toast pos", "miss"], ["is there an api", "miss"],
  ["how do I log out", "miss"], ["what can a viewer do", "miss|addlogin"], ["is the food truck generator a problem", "power|cafe|miss"],
  ["how do I change my password", { v: "password", o: "miss", t: "miss" }], ["I forgot my password", { v: "password", o: "miss", t: "miss" }],
  ["how do I sign in", { v: "signin", o: "miss", t: "miss" }],
  // Your own fridges, signed in. Signed out, these say to sign in.
  ["which fridges need me", "status"], ["is the walk-in ok", "status"], ["how's the bar fridge doing", "status"],
  ["whats the freezer at right now", "status"], ["anything wrong?", "status"], ["Is everything ok right now?", "status"],
  ["what's the temperature in the dairy cooler", "status"], ["what's the fridge temperature right now", "status"],
  ["what does this alert mean", { v: "alert|warm", o: "alert", t: "alert" }],
  ["why is the walk-in alarming", { v: "alarm|warm|miss", o: "alarm|alert", t: "alarm|alert" }],
  ["the walk in cooler is beeping", { v: "alarm|warm|miss", o: "alarm", t: "alarm" }],
  ["i got a text saying walk in cooler too warm what do i do", { v: "warm|alarm|miss", o: "alert|alarm", t: "alert|alarm" }],
  ["got an alert but the fridge feels cold", { v: "warm|alarm|miss", o: "alert|alarm", t: "alert|alarm" }],
  ["how long has the walk in been warm", { v: "alert|miss", o: "alert", t: "alert" }],
  ["did anyone answer the walk in alert", { v: "alert|delivery|miss", o: "alert|delivery", t: "alert|delivery" }],
  ["who answered the alert", { v: "miss|alert", o: "alert|miss", t: "alert|miss" }], ["is someone on it already", "alert|miss"],
  ["how do I acknowledge the alert", { v: "alert|warm|miss", o: "alert|miss", t: "alert|miss" }],
  ["hey so the walk in has been warm since like 2am and nobody answered whats going on", { v: "calls|warm|miss", o: "alert|alarm|status|calls", t: "alert|alarm|status|calls" }],
  ["ok the freezer alarm went off last night but its fine now do i need to do anything", { v: "warm|alarm|miss", o: "alarm|alert|food|fixed|week", t: "alarm|alert|food|fixed|week" }],
  ["why did I get a text at 3am", { v: "miss|calls|quiet|alert", o: "alert|alarm|week|quiet", t: "alert|alarm|week|quiet" }],
  ["my alert says nobody answered, what now", { v: "calls|warm|alarm", o: "calls|alert|alarm", t: "calls|alert|alarm" }],
  ["the alarm keeps going off for no reason", { v: "door|warm|miss", o: "door|alarm|miss", t: "door|alarm|miss" }],
  ["can i snooze an alert", "alert|pause|miss|fixed"],
  ["its fixed", { v: "miss|fixed", o: "fixed", t: "fixed" }], ["I fixed the door, how do I close the alert?", { v: "miss|fixed|warm", o: "fixed", t: "fixed" }],
  ["is the food still ok", "food"], ["Is the chicken in the walk-in still safe to use?", "food"], ["should i throw out the milk", "food"],
  ["can I still serve the fish from the prep fridge", "food"], ["the fridge was at 50 for an hour is the food ok", "food|reading"],
  ["I threw the food away, how do I record that?", "foodrecord"], ["how do i mark the food as thrown away", "foodrecord"],
  ["where do I say we kept the food", "foodrecord"], ["we binned everything in the freezer, where do I log it", "foodrecord"],
  ["what was thrown away", { v: "miss|foodrecord", o: "lossnote", t: "lossnote" }],
  ["Who is getting texted tonight", "oncall|team"], ["who's on call this weekend", "oncall"], ["who is on call", "oncall"],
  ["how do I add someone who can log in", "addlogin"], ["can my manager have their own login", "addlogin"],
  ["give my business partner access to the console", "addlogin"], ["how do i invite a coworker", "addlogin"],
  ["can my staff have their own logins", "addlogin"], ["can more than one person log in", "addlogin"],
  ["does each manager get a separate login", "addlogin"], ["can my accountant see the bills", "addlogin|privacy|miss"],
  ["remove Theo from the account", "removeperson"], ["Jess left, how do I stop her getting texts", "removeperson|quiet"],
  ["how do I change Priya's phone number", "number"],
  ["how do I remove a fridge", "removeunit"], ["we got rid of the old chest freezer, how do I delete it", "removeunit"],
  ["delete a sensor", "removeunit"], ["how do i get rid of a unit we don't use anymore", "removeunit"],
  ["remove the old bar fridge", "removeunit"], ["stop monitoring the ice cream freezer", "removeunit|pause|miss"],
  ["how do i add another fridge", "addunit"], ["I bought a new freezer, how do I add it", "addunit"],
  ["my sensor isn't showing readings", "noreadings"], ["the new sensor says waiting for first reading", "noreadings"],
  ["why is my sensor not sending data", "noreadings"], ["sensor shows no signal", "power|noreadings"],
  ["the prep fridge sensor stopped reporting", "noreadings|power"], ["no readings from my sensor", "noreadings"],
  ["can I stop alerts for the bar fridge, its being repaired", "unitpause|pause|miss"],
  ["how much am i paying", { v: "price", o: "bill", t: "bill" }], ["when is my next bill", { v: "miss|price", o: "bill", t: "bill" }],
  ["show me my last invoice", { v: "miss|price", o: "bill", t: "bill" }], ["what plan am i on", { v: "miss|price|plan", o: "bill", t: "bill" }],
  ["how many alerts this week", "week"], ["which unit is warmest", "warmest"], ["how many fridges do we have at old mill", "sites"],
  ["how much has it saved us", "saved"], ["is anything about to fail", "forecast"],
  ["make a log for the inspector", "log"], ["I need the temperature records for last month for the health department", "log"],
  ["make the insurance pack", "pack"], ["add my address to the insurance pack", "address"],
  ["why didn't I get a text", "delivery"], ["nobody got the alert text last night", "delivery"], ["turn on texts", "texts"],
  ["how many calls do I have left", "callsleft"], ["when does my trial end", "trialend"], ["how many days left on my trial", "trialend"],
  ["what do I do next", "next"], ["what did kryozyn do for me this week", "did"],
  ["My freezer is at 15 degrees", "reading"], ["the walk-in says 45", "reading"], ["the dessert display is at 44, is that bad?", "reading|status|miss"],
  ["can i see the temperature history", "log|status|miss"], ["can i download a graph of the fridge temps", "log|miss"],
  ["export to csv", "log|retention|miss"],
  // Two more rounds, written after the first was put right, to check the
  // fixes are the pattern and not the instances.
  ["whats the monthly cost for one location", "price|quote"], ["do you charge a startup fee", "fees"], ["any fees on top of the monthly price?", "fees"],
  ["is there a discount for paying up front for 12 months", "yearly"], ["how much do i save going annual", "yearly"],
  ["what happens to all my temperature logs if we close down", "retention"], ["do you keep the alerts forever", "retention"],
  ["how soon after the fridge warms up do I hear about it", "speed|door"], ["are alerts sent in real time", "speed"],
  ["can I set a quiet time for my sous chef", "quiet"], ["I don't want my phone buzzing at 2am", "quiet"],
  ["how to stop alerts going to me at the weekend", "quiet"], ["who will get texted if the walk-in fails", "team"],
  ["Which people get notified", "team"], ["the milk was thrown out, where do I put that", "foodrecord"],
  ["how do I tell the system we kept the stock", "foodrecord"], ["we're a food trailer, would it work", "cafe"],
  ["I run a juice bar", "miss|cafe"], ["do you do florist shops", "sector"], ["flower stall with a cooler room", "sector|miss"],
  ["ive got 2 restaurants can i use one account", "addsite|quote"], ["can it cover both of my cafes", "addsite"],
  ["does this meet HACCP requirements", "haccp"], ["do you help with food safety audits", "haccp|log"],
  ["new sensor not showing up", "noreadings"], ["the sensor isnt connecting", "noreadings"], ["I plugged in the sensor and nothing happens", "noreadings|miss"],
  ["can my head chef log in too", "addlogin"], ["set up a login for my manager", "addlogin"],
  ["get rid of the dessert fridge from my list", "removeunit"], ["we sold the bar freezer, remove it", "removeunit"],
  ["the battery on the walk in sensor is at 10%", "battery"], ["do batteries need changing", "battery"],
  ["what sensor would you buy for a reach-in", "sensors"], ["recommend me a probe for the walk in", "sensors"],
  ["my walk in freezer is at 12, is that bad", "reading"], ["is 43 too warm for a fridge", "reading|limits"],
  ["how cold does a freezer need to be", "limits"], ["will it text me if my chef leaves the door open all night", "door|calls"],
  ["does it ring my phone if no one replies", "calls"], ["can i get phone calls on starter", "plan|calls"],
  ["can I see the walk-in temp now", "status"], ["anything I need to deal with?", "status"], ["is everything cold", "status|miss"],
  ["whats going on with the prep fridge", "status|alarm"], ["the bar fridge alarm went off", { v: "alarm|warm|miss", o: "alarm", t: "alarm" }],
  ["is the cheese ok after the power cut", "food|power"], ["should we bin the chicken", "food"],
  ["how do i get the inspection report", "log"], ["print my temperature sheets", "log"],
  ["our health inspection is next week what do I need", "log|haccp"], ["what does the insurer get", "pack"],
  ["can I get a demo first", "demo|try"], ["do I have to give a card for the trial", "trial"],
  ["what happens on day 15 of the trial", "trialafter|trial|miss"], ["can I cancel any time", "cancel"],
  ["who do I call for support", "miss"], ["do you integrate with my POS", "miss"], ["what's your refund policy", "cancel"],
  ["can it measure the temperature of food on the pass", "miss"], ["how do i defrost a freezer", "miss"],
  ["can I use it at home for my wine", "miss|sector"], ["is my info shared with anyone", "privacy"],
  ["can I change the number that gets the texts", "number"], ["how do I add another freezer", "addunit"],
  ["what's the cheapest plan", "price|plan"], ["can I set the limit to 5C", "limit|canada"],
  ["our pastry chef wants to know if the walk-in is cold enough", "status"], ["is the ice cream freezer ok", "status"],
  ["can you text my partner as well when there's a problem", "team"], ["how many phone numbers can I add for alerts", "team"],
  ["what does the text message look like", "miss|alert|team"], ["can I customise the alert message", "miss"],
  ["I run a catering company with 2 vans", "cafe|addsite|quote"], ["is this ok for a school kitchen", "sector|cafe|miss"],
  ["do you work with hospitals", "sector|pharmacy|miss"], ["can a pharmacy with 3 vaccine fridges use it", "pharmacy|quote"],
  ["whats the catch", "fees|miss"], ["is it really free for 14 days", "trial"], ["do I get charged if I forget to cancel the trial", "trialafter|trial"],
  ["will my data be deleted when the trial ends", "retention|trialafter"], ["how long do readings stay in the system", "retention"],
  ["how fast do you call if nobody answers", "calls|speed"], ["is there a delay before the text", "speed|door"],
  ["can it email me a weekly report", "did|miss"], ["send me a summary every monday", "did|miss"],
  ["can I see last weeks alerts", { v: "miss", o: "week", t: "week" }], ["the freezer was at 20 for 3 hours, can I keep the meat", "food|reading"],
  ["is frozen chicken safe after a power cut", "food|power"], ["will a power cut set off an alert", "power"],
  ["what if the router reboots", "power"], ["will it work in a basement walk in with no signal", "sensors|power"],
  ["do sensors work in a blast chiller", "sensors|miss"], ["can I put two sensors in one walk-in", "sensors|miss|addunit"],
  ["my manager is on holiday, can she stop getting alerts", "quiet|pause"], ["mute the alerts for tonight", "quiet|pause"],
  ["how do I make sure someone always gets the alert at night", "quiet|oncall|team|calls|miss"], ["set up a rota for the weekend", "oncall"],
  ["Remove Sam's access", "removeperson"], ["take Lucy off the alert list", "removeperson"],
  ["delete the test fridge I added", "removeunit"], ["I added the wrong freezer by mistake", "removeunit|miss"],
  ["whats the sensor battery level", "battery"], ["how long does a sensor last", "battery|sensors|miss"],
  ["do I need to keep paper records as well", "log|haccp"], ["what records does the inspector want", "log|haccp"],
  ["will this lower my insurance premium", "pack|miss"], ["how do I prove to my insurer the fridge was fine", "pack"],
  ["where is the bill", { v: "miss|price", o: "bill", t: "bill" }], ["upgrade to growth", "plan|miss"],
  ["how do I switch plans", "plan|miss|bill"], ["can I downgrade", "plan|miss|cancel"],
  ["the alert says too cold, what do I do", { v: "alarm|warm|miss", o: "alarm|alert", t: "alarm|alert" }],
  ["the reading looks wrong", "miss|noreadings"], ["my sensor shows 99 degrees", "miss|reading|noreadings"],
  ["thank you!", "miss"], ["you're useless", "miss"], ["how old are you", "miss"],
  ["Make a proof report", "proof"], ["make a proof of monitoring report", "proof"], ["how do I make a proof report for my insurer", "proof"],
];

// The answers only a signed-in business gets, and the ones only somebody
// signed out gets: elsewhere they are a miss.
const SIGNED_IN_ONLY = new Set(["lossnote", "bill", "week", "warmest", "sites"]);
const SIGNED_OUT_ONLY = new Set(["signin", "password", "warm"]);
const ADVERSARIAL_AT = {
  v: () => chatAt("/pricing", { signedIn: false }),
  o: () => chatAt("/today", { overview: bigOverview() }),
  t: () => chatAt("/console", { overview: bigOverview({ trial: true }) }),
};

test("3.42.0: an adversarial table of over 300 questions: each gets its answer or the honest note, never a confident wrong one", async () => {
  assert.ok(ADVERSARIAL.length >= 300, String(ADVERSARIAL.length));
  const wrong = [];
  for (const [words, want] of ADVERSARIAL) {
    for (const who of ["v", "o", "t"]) {
      const ok = String(typeof want === "string" ? want : want[who]).split("|")
        .map((x) => ((who === "v" && SIGNED_IN_ONLY.has(x)) || (who !== "v" && SIGNED_OUT_ONLY.has(x)) ? "miss" : x));
      const chat = ADVERSARIAL_AT[who]();
      const got = chat.send(words);
      const topic = got.label ? (chat.given.answeredTopic() || (/^LINK /.test(got.text || "") && got.label === "See it live" ? "demo" : null)) : null;
      if (!ok.includes(topic || "miss")) wrong.push(`${who}: ${words} -> ${topic || "miss"} (want ${ok.join("|")})`);
      // A miss is the note naming the question, never a list of fridges.
      if (!topic) assert.match(got.note || got.text || "", /built-in answer|built in either/, `${who}: ${words}`);
      else assert.doesNotMatch(got.text || "", /undefined|NaN|\[object Object|Morgan Hale|\+1 555/, `${who}: ${words}`);
      await chat.tick();
    }
  }
  assert.deepEqual(wrong, []);
});

test("3.42.0: the five named misroutes get the right answer, true to the console", async () => {
  const visitor = chatAt("/pricing", { signedIn: false });
  assert.match(visitor.send("I have a food truck, is that ok?").text, /^Yes, while its fridges have internet: a food truck is watched the same way as a restaurant/);
  await visitor.tick();
  const night = visitor.send("How do I stop getting texts at 3am?").text;
  assert.match(night, /\*\*Quiet hours\*\*/);
  assert.match(night, /\*\*A rotation\*\*/);
  assert.match(night, /\*\*Mute\*\* beside a name under \*\*Who gets alerts\*\*/);
  assert.doesNotMatch(night, /Yes, on plans with calls/);
  await visitor.tick();
  const sensor = visitor.send("Which sensor should I get for a walk-in freezer?").text;
  assert.match(sensor, /^We don't sell sensors\.[\s\S]*For a walk-in or a freezer, pick one made for freezer temperatures/);
  await visitor.tick();
  const trial = chatAt("/console", { overview: bigOverview({ trial: true }) });
  assert.match(trial.send("Who gets the alerts right now?").text, /^Right now an alert text goes to \*\*Morgan\*\*\./);
  await trial.tick();
  const thrown = trial.send("I threw the food away, how do I record that?").text;
  assert.match(thrown, /\*\*Food kept\*\* or \*\*Thrown away\*\*\. Press \*\*Thrown away\*\*, then \*\*Add what was thrown away\*\*/);
  assert.doesNotMatch(thrown, /rule of thumb/);
});

test("3.42.0: the new answers say only what the product does, from the server's figures", async () => {
  const v = chatAt("/pricing", { signedIn: false });
  const ask = async (words) => { const got = v.send(words); await v.tick(); return got.text || "(missed)"; };
  assert.match(await ask("Is there a setup fee?"), /^\*\*No setup fee, and no hidden fees\.\*\* You pay the plan's monthly price: Starter \$59 a month/);
  assert.match(await ask("How long do you keep my temperature records?"), /^\*\*Readings: 13 months\.\*\* Every reading is kept in full for 30 days/);
  assert.match(await ask("What happens to my data if I cancel?"), /^\*\*If you cancel\*\*, nothing is deleted: the service runs to the end of what you have paid for/);
  assert.match(await ask("how quickly will i get a text"), /^\*\*Within 60 seconds of the alert opening\*\*/);
  assert.match(await ask("is there a discount if I pay yearly"), /^\*\*Yes: pay yearly and you get 12 months for the price of 10 \(2 months free\)\.\*\* Starter is then \$590 a year, and Growth \$1,490 a year a location/);
  assert.match(await ask("Is it HACCP compliant?"), /It is a record, not a certificate/);
  assert.match(await ask("Can I use it for two restaurants?"), /^Yes\. Once you've signed up, on the console's \*\*Setup\*\* tab, open \*\*Your locations\*\*/);
  assert.match(await ask("I own a flower shop"), /\(\/for\/florists\)/);
  assert.match(await ask("my sensor isn't showing readings"), /\*\*Waiting for its first reading\*\* until one arrives/);
  assert.match(await ask("can my manager have their own login"), /\*\*People who can sign in\*\* on the console's \*\*Team\*\* tab: \*\*Add someone\*\*/);
  assert.match(await ask("how do I remove a fridge"), /^Press \*\*Remove\*\* on the unit's card on the console's \*\*Fridges\*\* tab/);
  assert.match(await ask("the sensor battery is dying"), /after 30 minutes everyone on call is texted that it has/);
  assert.match(await ask("Can I change how long it waits before alerting?"), /^Yes\. Press \*\*Alert wait\*\* on the unit's card in the console/);
  // The figures are the server's: a setup fee or another yearly offer would show.
  const data = { setupFee: "25", yearlyCharged: "11", yearlyCovered: "12", keepReadingsMonths: "24" };
  const sent = (words) => chatAt("/pricing", { signedIn: false, data }).send(words).text || "(missed)";
  assert.match(sent("Is there a setup fee?"), /^\*\*A one-off setup fee of \$25 a location/);
  assert.match(sent("Can I pay annually?"), /12 months for the price of 11 \(1 month free\)/);
  assert.match(sent("how long is data stored"), /^\*\*Readings: 24 months\.\*\*/);
  // Signed in, the battery and the readings answers name the units.
  const own = bigOverview();
  own.sensors[2].battery_low = true;
  own.sensors[2].battery_percent = 12;
  const o = chatAt("/today", { overview: own });
  assert.match(o.send("low battery warning?").text, /^Low now: \*\*Prep fridge x\(\/evil\)\*\* \(12%\)\./);
});

// ---------------------------------------------------------------- 3.43.0: history, changing settings, nights, named places
//
// Round 20's table: new phrasings, written before anything was changed and
// scored as they stood (CHANGELOG 3.43.0), each with the answer it should
// get or the honest note. Asked by a visitor (v), an owner on /today (o),
// a business on the trial on the console (t), and the demo's console with
// last Wednesday's Old Mill freezer alert and a rotation (d). Something in
// the past is the 7-day history, never what is happening now; a change is
// the button that makes it; the night is quiet hours, never "Yes, on
// plans with calls"; a unit or a place named is that one, never another's.
const ROUND20 = [
  // In the past: the 7-day history, never the alert open now.
  ["Was the Old Mill freezer alert on Wednesday handled?", "week"], ["Who answered the Wednesday alert?", "week"],
  ["Warmest the Harbourside walk-in got this week?", "week"], ["what happened yesterday with the freezer", "week"],
  ["did anyone deal with the alert last night", "week"], ["any alerts over the weekend?", "week"],
  ["how many times did the walk-in go warm this week", "week"], ["was there an alarm on monday", "week"],
  ["who handled the alert on tuesday", "week"], ["show me alerts from last week", "week"],
  ["did the bar fridge alarm yesterday", "week"], ["what time did the alert start on wednesday", "week"],
  ["how long did it take someone to answer the last alert", "week"], ["was the freezer ok last night", "week"],
  ["did the dessert display go out of range this week", "week"], ["has anything gone wrong this week", "week"],
  ["what alerts did we get yesterday", "week"], ["list last week's alarms", "week"],
  ["Was the walk-in cooler alert closed?", "week"], ["when was the last alert", "week"],
  ["Did someone answer the Old Mill alert?", "alert|week"], ["who was on call when the freezer alarmed on thursday", "week|oncall"],
  ["what happened with the alarm on sunday morning", "week"], ["were there any problems last month", "week|log|miss"],
  ["how warm did the ice-cream freezer get yesterday", "week|miss"], ["highest temperature the bakery fridge hit this week", "week|miss"],
  ["did texts go out for the wednesday alert", "week|delivery"], ["Was Morgan the one who answered on Wednesday?", "week"],
  ["who closed the alert yesterday", "week"], ["did we throw food away after the last alert", "week|lossnote|foodrecord"],
  ["how quickly did we respond to alerts this week", "week"], ["Was the Harbourside bar fridge alarm handled?", "week"],
  ["what went wrong earlier today", "week|status"], ["did the alert get answered", "alert|week"],
  ["any alarms overnight", "week"], ["how many alerts yesterday", "week"], ["what did the alert say yesterday", "week"],
  ["any issues this morning", "week|status"], ["was it warm overnight", "week"],
  // Changing a setting: the button that does it, never the status.
  ["Change the walk-in freezer alarm to 5 degrees", "limit"], ["set the bar fridge limit to 38", "limit"],
  ["raise the dessert display alarm to 45", "limit"], ["lower the freezer threshold", "limit"],
  ["can I adjust the temperature the walk-in alerts at", "limit"], ["make the bakery fridge alarm at 40 instead", "limit"],
  ["change the alarm temperature for the ice-cream freezer", "limit"], ["I want the walk-in to alert at 39", "limit"],
  ["set the walk-in cooler alarm higher", "limit"], ["adjust the freezer alert", "limit|wait"],
  ["change the Old Mill walk-in freezer limit", "limit"], ["can I have different limits for each fridge", "limit"],
  ["We're defrosting the ice-cream freezer, stop it alarming", "unitpause"], ["how do I pause one freezer while we clean it", "unitpause"],
  ["we're deep cleaning the walk-in tomorrow, will it go off?", "unitpause"], ["can I silence the walk-in while the engineer fixes it", "unitpause"],
  ["turn off alerts for the blast chiller during cleaning", "unitpause"], ["snooze the bar fridge for an hour", "unitpause"],
  ["mute the dessert display alarm", "unitpause"], ["the freezer is being defrosted, how do I stop texts", "unitpause"],
  ["stop alerts on the walk-in, we're moving stock", "unitpause"], ["pause monitoring for the prep fridge", "unitpause"],
  ["is there a maintenance mode", "unitpause"], ["can I turn a unit off for a few hours", "unitpause"],
  ["the freezer alarms every time we defrost", "unitpause"], ["is the defrost cycle going to set off alerts", "unitpause"],
  ["change how long before it alerts to 45 minutes", "wait"], ["make the freezer wait longer before texting", "wait"],
  ["change the alert delay on the walk-in", "wait"], ["stop the alarms", "pause|quiet|unitpause"],
  ["mute alerts for Sam", "quiet"], ["silence alerts at night", "quiet"], ["change the text message wording", "miss"],
  ["can I stop it alarming when the door is open for deliveries", "door|wait|unitpause"],
  ["how do I turn the alarm off", "fixed|unitpause|pause|quiet|miss"],
  // Nights and weekends: quiet hours, a rotation, Mute; calls only when asked.
  ["My phone keeps buzzing at night", "quiet"], ["different person woken on weekends", "quiet|oncall"],
  ["I don't want to be woken up at 2am", "quiet"], ["can someone else get the alerts on weekends", "quiet|oncall"],
  ["how do I set weekend cover", "oncall|quiet"], ["texts keep waking me up", "quiet"],
  ["can my night manager get the alerts instead of me", "quiet|oncall|team"], ["I need sleep, stop the overnight texts", "quiet"],
  ["who gets alerted at night", "team|quiet|oncall"], ["set quiet hours for me", "quiet"],
  ["only alert me during business hours", "quiet"], ["can alerts go to the chef on saturdays and me on weekdays", "oncall|quiet"],
  ["do you phone at night", "calls"], ["will it ring my phone at 3am if nobody answers the text", "calls"],
  ["will you call me in the middle of the night", "calls"], ["my phone buzzed at 4am, why", "alert"],
  ["can we take turns being on call", "oncall"], ["who is on call this weekend", "oncall"],
  ["the alerts are annoying at night", "quiet"], ["do not disturb mode", "quiet"],
  ["can I stop getting woken for the bar fridge", "quiet|unitpause|door|wait|limit"], ["weekend rota", "oncall"],
  ["rotate who gets woken", "oncall|quiet"], ["night shift alerts", "quiet|oncall|miss"],
  ["why did I get woken up", "alert"], ["what happens if nobody answers", "calls"],
  // A unit or a place named: that one, or that it isn't there.
  ["Is the Harbourside walk-in ok?", "status"], ["how's the old mill bar fridge", "status"],
  ["what's the bakery fridge at", "status"], ["what is the temperature of the dessert display", "status|miss"],
  ["Is the sushi fridge ok?", "status|miss"], ["how is the Old Mill walk-in freezer doing", "status"],
  ["are all the Old Mill units fine", "status"], ["is the ice cream freezer cold enough", "status"],
  ["warmest unit at Old Mill right now", { v: "status|miss", o: "warmest", t: "warmest", d: "warmest" }], ["which is the warmest fridge at Harbourside", "warmest"],
  ["how many fridges at Old Mill", "sites"], ["the walk-in at harbourside is beeping", "alarm"],
  ["Old Mill alarm going off", { v: "alarm|miss", o: "alarm", t: "alarm", d: "alarm" }], ["is the pastry fridge ok", "status"],
  ["is the blast chiller 2 working", "status"], ["what's the dairy cooler reading", "status"],
  ["is the wine fridge ok", "status|miss"], ["Downtown site fridges ok?", "status|miss"],
  // Adding people.
  ["add my sous chef Marco to alerts", "team"], ["how do I add a new staff member to get texts", "team"],
  ["put Jess on the alert list", "team"], ["add my manager's number", "team"],
  ["can I add my business partner so she gets alerts too", "team"], ["add another phone number", "team"],
  ["my new chef needs the alerts", "team"], ["invite my manager to the dashboard", "addlogin"],
  ["give my assistant manager a login", "addlogin"], ["add a second person for texts", "team"],
  ["how do I add Priya", "team|addlogin"], ["can 3 people get the texts", "team"],
  ["add the night porter to alerts", "team"], ["who gets the texts", "team"],
  ["how do I add my husband to alerts", "team"], ["can I get the alerts on two phones", "team"],
  ["text my sous chef too", "team"], ["who gets alerted when the alarm goes off", "team"],
  ["does the whole team get texted", "team"], ["do you call everyone at once", "calls|team"],
  // Common questions, each to its answer.
  ["why wasn't I called?", "whycall"], ["why didn't I get a phone call", "whycall"],
  ["nobody phoned me when the freezer was warm", "whycall"], ["I thought you would call me", "whycall"],
  ["why no call last night", "whycall"], ["the reading isn't showing up", "noreadings"],
  ["temperature not showing", "noreadings"], ["no temperature on the new fridge", "noreadings"],
  ["do I get charged automatically after 14 days?", "trialafter"], ["will my card be charged when the trial ends", "trialafter"],
  ["I own 6 pizza places, monthly bill?", "quote"], ["what would 4 cafes cost a month", "quote"],
  ["what if my internet dies at night?", "power"], ["what if the wifi goes down overnight", "power"],
  ["power cut at night, what happens", "power"], ["can my staff use it without training?", "training"],
  ["is it easy for my staff", "training|easy"], ["do my staff need training", "training"],
  ["will my cooks understand the alerts", "training"], ["email alerts instead of text?", "email"],
  ["can I get alerts by email", "email"], ["do you send emails when it's warm", "email"],
  ["can the alerts go to my email", "email"], ["whatsapp alerts?", "miss|email|texts"],
  ["can I see the history of a fridge", "log|retention|week|miss"], ["is there a weekly email", "did|miss|email"],
  ["how do I change who gets called first", "team|oncall|miss"], ["can I get a text when the alert is fixed", "miss"],
  ["do I need to keep the website open", "app|mobile|miss|what"], ["can I test the alerts", "texts|team|miss"],
  ["my staff left, remove her", "removeperson"], ["change Marco's number", "number"],
  ["the alert keeps coming back after I close it", "miss|fixed|alert"], ["how do I mark the alert done", "fixed"],
  ["I didn't get the text last night", "delivery|week"], ["the fridge was at 50 last night, is the food ok", "food|reading|week"],
  ["can my staff see the dashboard without me", "addlogin|privacy"],
];

// Round 20's unseen batch: written after the table above passed, scored
// once as it stood (CHANGELOG 3.43.0), then put right where it missed.
const ROUND20_UNSEEN = [
  ["Did the Harbourside dairy cooler have any trouble on Tuesday?", "week"], ["what was the worst alert this week", "week"],
  ["tell me about the alert from friday", "week"], ["who acknowledged yesterday's alarm", "week"],
  ["was anybody texted on saturday", "week|delivery"], ["How did we do on alerts last week?", "week"],
  ["did the ice-cream freezer warm up overnight", "week"], ["previous alarms for the pastry fridge", "week"],
  ["has the Old Mill bar fridge alarmed this week", "week"], ["how fast was the last alert answered", "week"],
  ["bump the dairy cooler alarm up to 42", "limit"], ["can the freezer alarm be set at 0 instead of 10", "limit"],
  ["I'd like the Harbourside walk-in freezer to alert at 5F", "limit"], ["drop the bar fridge threshold to 39", "limit"],
  ["the walk-in cooler alarm is too sensitive, raise it", "limit|wait"], ["we're doing a deep clean on the blast chiller tonight", "unitpause"],
  ["freezer's being serviced tomorrow, can I switch off its alerts", "unitpause"], ["temporarily disable the dessert display", "unitpause"],
  ["hold alerts on the prep fridge while we restock it", "unitpause"], ["we turn the walk-in off to defrost it every sunday", "unitpause"],
  ["can it wait an hour before alerting on the walk-in", "wait"], ["alerts wake my whole family at night", "quiet"],
  ["I only want texts in the daytime", "quiet"], ["can the alerts skip me when I'm asleep", "quiet"],
  ["my partner gets woken by my phone buzzing", "quiet"], ["sundays I'm off, can someone else cover", "oncall|quiet"],
  ["can it call my manager at night instead of texting", "calls"], ["do you ring people in the night", "calls"],
  ["will the phone call wake me", "calls"], ["is the Old Mill dessert display ok", "status"],
  ["how cold is the Harbourside walk-in freezer", "status"], ["is the meat fridge ok", "status|miss"],
  ["what's the temperature at Riverside", "status|miss"], ["hottest unit at Harbourside", "warmest"],
  ["Is everything ok at Old Mill?", "status"], ["add Tom from the kitchen to the texts", "team"],
  ["my head chef should get alerts too", "team"], ["put my wife's number on the list", "team"],
  ["can I add 2 more people to get alerts", "team"], ["how does someone new get the alarm texts", "team"],
  ["why did nobody ring me when the freezer died", "whycall"], ["I never got a call about the warm fridge", "whycall"],
  ["you were meant to phone me", "whycall"], ["no temperatures coming through from the new sensor", "noreadings"],
  ["the sensor shows nothing", "noreadings|miss"], ["after the free trial will it take money automatically", "trialafter"],
  ["what if our broadband dies overnight", "power"], ["do you do email notifications", "email"],
  ["can it email me as well as text", "email"], ["is it simple enough for teenage staff", "training|easy"],
  ["do I have to train my kitchen team", "training"], ["I run 3 pizza shops, what's the monthly price", "quote"],
  ["is there a lock-in contract", "cancel"], ["the money page says it renews automatically, can I still cancel", "cancel"],
  ["why didn't anyone call me last night", "whycall"], ["stop the freezer texting me during defrost", "unitpause"],
  ["did someone close the walk-in alert on monday", "week"], ["when did the bar fridge last alarm", "week"],
  ["change the ice cream freezer limit", "limit"], ["silence the walk-in alarm while we move stock", "unitpause"],
];
const ROUND20_AT = { ...ADVERSARIAL_AT, d: () => chatAt("/console", { overview: demoOverview(), rotations: ROTA_NOW }) };

function scoreTable(table, at) {
  const wrong = [];
  let asked = 0;
  return (async () => {
    for (const [words, want] of table) {
      for (const who of Object.keys(at)) {
        const ok = String(typeof want === "string" ? want : want[who]).split("|")
          .map((x) => ((who === "v" && SIGNED_IN_ONLY.has(x)) || (who !== "v" && SIGNED_OUT_ONLY.has(x)) ? "miss" : x));
        const chat = at[who]();
        const got = chat.send(words);
        const topic = got.label ? (chat.given.answeredTopic() || (/^LINK /.test(got.text || "") && got.label === "See it live" ? "demo" : null)) : null;
        asked += 1;
        if (!ok.includes(topic || "miss")) wrong.push(`${who}: ${words} -> ${topic || "miss"} (want ${ok.join("|")})`);
        if (!topic) assert.match(got.note || got.text || "", /built-in answer|built in either/, `${who}: ${words}`);
        else assert.doesNotMatch(got.text || "", /undefined|NaN|\[object Object|Morgan Hale|\+1 555/, `${who}: ${words}`);
        await chat.tick();
      }
    }
    return { wrong, asked };
  })();
}

test("3.43.0: round 20's table: history, changes, nights, named places and people each get their answer or the honest note", async () => {
  assert.ok(ROUND20.length >= 150, String(ROUND20.length));
  const { wrong, asked } = await scoreTable(ROUND20, ROUND20_AT);
  if (process.env.PILOT_SCORE) console.log(`ROUND20 ${asked - wrong.length}/${asked} right, ${wrong.length} wrong\n${wrong.join("\n")}`);
  assert.deepEqual(wrong, []);
});

test("3.43.0: round 20's unseen batch, scored once before it was put right, gets its answers too", async () => {
  assert.ok(ROUND20_UNSEEN.length >= 60, String(ROUND20_UNSEEN.length));
  const { wrong, asked } = await scoreTable(ROUND20_UNSEEN, ROUND20_AT);
  if (process.env.PILOT_SCORE) console.log(`UNSEEN ${asked - wrong.length}/${asked} right, ${wrong.length} wrong\n${wrong.join("\n")}`);
  assert.deepEqual(wrong, []);
});

test("3.43.0: the reviewer's confident wrong answers are put right, true to the console", async () => {
  const demo = chatAt("/console", { overview: demoOverview(), rotations: ROTA_NOW });
  const ask = async (chat, words) => { const got = chat.send(words); await chat.tick(); return got.text || got.note || "(missed)"; };
  const limit = await ask(demo, "Change the walk-in freezer alarm to 5 degrees");
  assert.match(limit, /^Press \*\*Change limit\*\* on its card \(the \*\*Fridges\*\* tab\), type 5 under \*\*Text us if it goes over\*\*/);
  assert.doesNotMatch(limit, /No alert is open|check the door|Check the door/);
  const handled = await ask(demo, "Was the Old Mill freezer alert on Wednesday handled?");
  assert.match(handled, /^\*\*1 alert\*\* on the 3 freezers at \*\*Old Mill\*\* on Wednesday:\n- \*\*Old Mill \/ Walk-in freezer\*\*, Wed .+ \(worst 21\.0°F\): closed, food kept$/);
  const who = await ask(demo, "Who answered the Wednesday alert?");
  assert.match(who, /on Wednesday:/);
  assert.doesNotMatch(who, /Nobody has answered yet|Kitchen walk-in/);
  assert.match(who, /Pilot doesn't name people: who answered and who closed it are on the alert itself, under \*\*Alerts\*\*\./);
  const warmest = await ask(demo, "Warmest the Harbourside walk-in got this week?");
  assert.match(warmest, /at \*\*Harbourside\*\* in the last 7 days/);
  assert.doesNotMatch(warmest, /bakery|Kitchen walk-in|Old Mill/);
  assert.match(warmest, /\*\*Log for an inspector\*\*/);
  const defrost = await ask(demo, "We're defrosting the ice-cream freezer, stop it alarming");
  assert.match(defrost, /^There's no switch that pauses one unit's alerts/);
  assert.match(defrost, /\*\*Alert wait\*\*[\s\S]*\*\*Use the usual\*\*[\s\S]*\*\*I'm on it\*\*/);
  assert.doesNotMatch(defrost, /No alert is open|Check the door/);
  for (const words of ["My phone keeps buzzing at night", "different person woken on weekends"]) {
    const got = await ask(demo, words);
    assert.match(got, /\*\*Quiet hours\*\*[\s\S]*\*\*A rotation\*\*[\s\S]*\*\*Mute\*\*/, words);
    assert.doesNotMatch(got, /Yes, on plans with calls/, words);
  }
  // A place named is that place; a name nobody has is said so.
  const harbour = await ask(demo, "Is the Harbourside walk-in ok?");
  assert.match(harbour, /^At \*\*Harbourside\*\*:\n\n- \*\*Walk-in cooler\*\* 35\.0°F, fine\./);
  assert.doesNotMatch(harbour, /Kitchen walk-in|Old Mill/);
  const sushi = await ask(demo, "Is the sushi fridge ok?");
  assert.match(sushi, /^I can't find a unit or a location called “sushi fridge”, so I won't guess with another one's numbers\./);
  assert.doesNotMatch(sushi, /°F/);
  assert.match(await ask(demo, "which is the warmest fridge at Harbourside"), /^\*\*Harbourside \/ Bar fridge\*\* is the warmest at Harbourside for its limit/);
  // Who is texted: with a rotation, whoever is on call in it.
  const team = await ask(demo, "who gets the texts");
  assert.match(team, /^Right now an alert text goes to \*\*Morgan\*\*\./);
  assert.match(team, /With your rotation, whoever is on call in it is texted when a fridge stays too warm/);
  assert.doesNotMatch(team, /Everyone on the list is texted/);
  const noRota = chatAt("/today", { overview: bigOverview() });
  assert.match(noRota.send("who gets the texts").text, /Everyone on the list is texted when a fridge stays too warm/);
  const rotaToday = bigOverview();
  rotaToday.texted_by_rotation = true;
  assert.match(chatAt("/today", { overview: rotaToday }).send("who gets the texts").text, /With your rotation, whoever is on call in it is texted/);
});

test("3.43.0: the common questions get their answers: who to add, why no call, no reading, the trial, a quote, the Wi-Fi, training, email", async () => {
  const v = chatAt("/pricing", { signedIn: false, data: { callsLive: "no" } });
  const ask = async (words) => { const got = v.send(words); await v.tick(); return { topic: got.label ? v.given.answeredTopic() : "miss", text: got.text || got.note || "" }; };
  assert.equal((await ask("add my sous chef Marco to alerts")).topic, "team");
  const call = await ask("why wasn't I called?");
  assert.equal(call.topic, "whycall");
  assert.match(call.text, /^A phone call comes only on a plan with calls \(Growth and Enterprise; the free trial has 5 to try\), and only when nobody has answered the alert text within 10 minutes\./);
  assert.match(call.text, /\*\*Calls are off on this server\*\*: it has no phone service switched on/);
  assert.doesNotMatch((await ask("why didn't I get a phone call")).text, /^Yes, on plans with calls/);
  assert.equal((await ask("the reading isn't showing up")).topic, "noreadings");
  assert.equal((await ask("do I get charged automatically after 14 days?")).topic, "trialafter");
  const pizza = await ask("I own 6 pizza places, monthly bill?");
  assert.equal(pizza.topic, "quote");
  assert.match(pizza.text, /6 locations/);
  assert.equal((await ask("what if my internet dies at night?")).topic, "power");
  const training = await ask("can my staff use it without training?");
  assert.match(training.text, /^Yes\. Nobody has to learn the console to answer an alert/);
  const email = await ask("email alerts instead of text?");
  assert.match(email.text, /There's no email-only choice for a person: \*\*Who gets alerts\*\* takes mobile numbers\./);
  assert.match(email.text, /Each alert is also emailed once, as a copy, to the business's own email address/);
  // Signed in on Starter, the plan's own words; on a live server, nothing about calls being off.
  const starter = chatAt("/console", { overview: demoOverview({ starter: true }), data: { callsLive: "yes" } });
  const why = starter.send("why wasn't I called?").text;
  assert.match(why, /^\*\*Your plan sends texts, not phone calls\.\*\* Your Starter plan sends texts, not phone calls\./);
  assert.doesNotMatch(why, /Calls are off on this server/);
});
