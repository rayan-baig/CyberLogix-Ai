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
  for (const sel of [".top", ".demo-top", "#app > .nav"]) assert.ok(!avoid.has(sel), sel);
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
    assert.match(text, /- \*\*Kitchen walk-in\*\* \(47°F\): Too warm for /);
    assert.match(text, /- \*\*Prep fridge x\(\/evil\)\*\*: No signal|- \*\*Prep fridge x\(\/evil\)\*\*: We have not heard/);
    // 3.32.0: a fine unit is not in the list, but its temperature is under it.
    assert.ok(!/- \*\*Bar freezer/.test(text), "a fine unit is not listed");
    assert.ok(text.includes("The rest are fine: Bar freezer 2°F."), text);
    assert.ok(text.indexOf("Kitchen walk-in") < text.indexOf("Prep fridge"), "worst first");
    assert.match(text, /Nobody has answered an alert yet: press \*\*I'm on it\*\*/);
    assert.doesNotMatch(text, /\]\(\/evil\)/, "a unit's name can't add a link");
    assert.doesNotMatch(text, /Dana|Reyes|dana@|\+1555/);
  }
  // All fine: says so, with the count.
  assert.match(ask(pills(mountAt("/console", { overview: overviewWith({ quiet: true }) }), "/console")[0]),
    /Nothing needs you right now\. All 2 are within their limits:\n- \*\*Kitchen walk-in\*\* 47°F, fine\n- \*\*Bar freezer\*\* 2°F, fine/);
  // A unit still waiting for its sensor: the wizard.
  assert.match(ask(pills(mountAt("/console", { overview: overviewWith({ waiting: true }) }), "/console")[0]),
    /\*\*Dessert fridge\*\* is waiting for a first reading\. \[Connect a sensor\]\(\/console#connect\)/);
  // No units yet.
  assert.match(ask(pills(mountAt("/console", { overview: { sensors: [], incidents: [] } }), "/console")[0]),
    /no fridges or freezers added yet\. \[Connect your first sensor\]\(\/console#connect\)/);
});

test("1.4.0: 'What does this alert mean?' explains the open alert from its own record", () => {
  const open = ask(meaningOf("/console", overviewWith()));
  assert.match(open, /^\*\*Kitchen walk-in\*\* is too warm: 47°F, above its 41°F limit\./);
  assert.match(open, /It went out of range at .+ and has been out for .+; worst so far 48\.5°F\./);
  assert.match(open, /It stayed out longer than its 20-minute wait, so we alerted\./);
  // A reading far past the limit alerts at once: it never claims the wait passed.
  const fast = overviewWith();
  fast.incidents[0].opened_at = fast.incidents[0].out_since;
  assert.match(ask(meaningOf("/console", fast)),
    /It went far enough past its limit to alert at once, without its 20-minute wait\./);
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
  // On /today the buttons are under Needs you now.
  assert.match(ask(meaningOf("/today", overviewWith())), /\*\*I'm on it\*\* under Needs you now/);
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
    assert.match(text, pathname === "/today" ? /under \*\*Needs you now\*\*/ : /top of the page/);
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
  for (const sel of [".thing-temp", ".thing-name", ".demo-unit-temp", ".demo-unit-top", "#needs", ".need"]) {
    assert.ok(avoid.includes(sel), sel);
  }
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
    // 3.36.0: with a sensor reporting there is no connect pill; adding one
    // more still gets the connect answer, through whichever item has its words.
    assert.match(typedAnswer(pathname, "How do I add a new fridge?"), /^\*\*Connect your sensor\*\* walks you through it/, pathname);
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
    assert.ok(walk.includes("- **Prep fridge** 38.4°F, fine"), walk);
    const freezer = typedAnswer(pathname, "How is the freezer?", { overview: ok });
    assert.ok(freezer.startsWith("You haven't added a freezer. [Connect one](/console#connect)"), freezer);
    // Pressed, the pill lists every unit with its temperature.
    const pressed = ask(pills(mountAt(pathname, { overview: ok }), pathname)[0]);
    assert.ok(pressed.includes("- **Walk-in fridge** 37.9°F, fine\n- **Prep fridge** 38.4°F, fine"), pressed);
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
    assert.ok(text.startsWith("**Kitchen walk-in** (47°F): "), text);
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
  if (pathname === "/console") win.KryozynConsole = { signedIn: () => signedIn, tab: () => "Fridges", overview: () => (signedIn ? overview : null) };
  if (pathname === "/today") win.KryozynToday = { overview: () => overview, did: () => null };
  const doc = { getElementById: () => ({ dataset: { ...NAMES, ...DATA, restaurantPrice: "149", pharmacyPrice: "199",
    unitsPerLocation: "10", extraUnit: "15", chainFrom: "5", chainPercent: "10" } }), title: "Restaurants",
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
