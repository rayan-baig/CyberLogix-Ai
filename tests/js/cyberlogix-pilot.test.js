// CyberLogix's Pilot pack and console tools, run in Node:
// `node --test tests/js/cyberlogix-pilot.test.js`. tests/test_pilot.py runs
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

const FACTS = {
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
    globalThis.window.CyberLogixConsole = {
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
  delete require.cache[app("pilot-cyberlogix-pack.js")];
  const pack = require(app("pilot-cyberlogix-pack.js"));
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
    config: { appName: "CyberLogix AI", knowledge: () => pack.knowledge, strengths: () => pack.strengths, tools: () => pack.tools(),
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
  const config = fs.readFileSync(app("pilot-cyberlogix.js"), "utf8");
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
const CONFIG = fsx.readFileSync(app("pilot-cyberlogix.js"), "utf8");
const PANEL = (() => { const sb = { window: {}, document: {}, URL }; vmx.runInNewContext(fsx.readFileSync(core("pilot-panel.js"), "utf8"), sb); return sb.window.PilotPanel; })();
const BASE = "https://cyberlogix.example";
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
function mountAt(pathname, { data, signedIn = true, overview } = {}) {
  let given = null;
  const fetchTrap = () => { throw new Error("an answer called the network"); };
  const win = { Pilot: { mount: (c) => { given = c; } }, location: { pathname, href: BASE + pathname },
    localStorage: { getItem: (k) => (signedIn && k === "cyberlogix.session" ? "t" : null) }, CLXWords: Words, fetch: fetchTrap };
  if (pathname === "/console") win.CyberLogixConsole = { signedIn: () => signedIn, tab: () => "Fridges", overview: () => (signedIn ? overview : null) };
  if (pathname === "/today") win.CyberLogixToday = { overview: () => overview };
  vmx.runInNewContext(CONFIG, { window: win, fetch: fetchTrap, XMLHttpRequest: fetchTrap, URL,
    document: { getElementById: () => ({ dataset: data || DATA }), title: "Restaurants" } });
  return given;
}
const pills = (given, pathname) => PANEL.welcomePills(given.welcomePills, BASE + pathname);
// Arrays made inside the vm are compared as plain data.
const J = (x) => JSON.parse(JSON.stringify(x));
const ask = (pill) => (typeof pill.answer === "function" ? pill.answer({ facts: {}, ready: false, label: pill.label }) : pill.answer);
const NO_PEOPLE = /Dana|Reyes|dana@|\+1555|\]\(\/evil/;

test("1.4.0: the visitor's link pills are core links now (See it live, What does it cost?, What sensors do I need?); the 3.26.0 click catcher is gone", () => {
  for (const pathname of ["/", "/pricing", "/help", "/help/sensors", "/for/restaurant", "/demo"]) {
    const p = pills(mountAt(pathname), pathname);
    assert.deepEqual(J(p.map((x) => x.label)), ["Will it work for my café?", "What does it cost?", "See it live", "What sensors do I need?", "Add my AI key"], pathname);
    assert.equal(p[1].link, `${BASE}/pricing`);
    assert.equal(p[2].link, `${BASE}/demo`);
    assert.equal(p[3].link, `${BASE}/help/sensors`);
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
    assert.deepEqual(J(p.map((x) => x.label)), ["Which fridges need me?", "What does this alert mean?", "Is the food still OK?",
      "How do I connect a sensor?", "Add my AI key"], pathname);
    const text = ask(p[0]);
    assert.match(text, /These 2 need you, worst first:\n- \*\*Kitchen walk-in\*\*/);
    assert.match(text, /- \*\*Kitchen walk-in\*\* \(47°F\): Too warm for /);
    assert.match(text, /- \*\*Prep fridge x\(\/evil\)\*\*: No signal|- \*\*Prep fridge x\(\/evil\)\*\*: We have not heard/);
    assert.ok(!text.includes("Bar freezer"), "a fine unit is not listed");
    assert.ok(text.indexOf("Kitchen walk-in") < text.indexOf("Prep fridge"), "worst first");
    assert.match(text, /Nobody has answered an alert yet: press \*\*I'm on it\*\*/);
    assert.doesNotMatch(text, /\]\(\/evil\)/, "a unit's name can't add a link");
    assert.doesNotMatch(text, /Dana|Reyes|dana@|\+1555/);
  }
  // All fine: says so, with the count.
  assert.match(ask(pills(mountAt("/console", { overview: overviewWith({ quiet: true }) }), "/console")[0]),
    /Nothing needs you right now: all 2 fridges and freezers are within their limits\./);
  // A unit still waiting for its sensor: the wizard.
  assert.match(ask(pills(mountAt("/console", { overview: overviewWith({ waiting: true }) }), "/console")[0]),
    /\*\*Dessert fridge\*\* is waiting for a first reading\. \[Connect a sensor\]\(\/console#connect\)/);
  // No units yet.
  assert.match(ask(pills(mountAt("/console", { overview: { sensors: [], incidents: [] } }), "/console")[0]),
    /no fridges or freezers added yet\. \[Connect your first sensor\]\(\/console#connect\)/);
});

test("1.4.0: 'What does this alert mean?' explains the open alert from its own record", () => {
  const open = ask(pills(mountAt("/console", { overview: overviewWith() }), "/console")[1]);
  assert.match(open, /^\*\*Kitchen walk-in\*\* is too warm: 47°F, above its 41°F limit\./);
  assert.match(open, /It went out of range at .+ and has been out for .+; worst so far 48\.5°F\./);
  assert.match(open, /It stayed out longer than its 20-minute wait, so we alerted\./);
  // A reading far past the limit alerts at once: it never claims the wait passed.
  const fast = overviewWith();
  fast.incidents[0].opened_at = fast.incidents[0].out_since;
  assert.match(ask(pills(mountAt("/console", { overview: fast }), "/console")[1]),
    /It went far enough past its limit to alert at once, without its 20-minute wait\./);
  assert.match(open, /Nobody has answered yet\. Press \*\*I'm on it\*\* at the top of the page/);
  assert.match(open, /Check the door is shut/);
  assert.match(open, /\*\*Is the food still good\?\*\* button/);
  assert.doesNotMatch(open, NO_PEOPLE);
  // Someone has it: never their name.
  const ack = ask(pills(mountAt("/today", { overview: overviewWith({ ack: true }) }), "/today")[1]);
  assert.match(ack, /Someone on your team is on it\./);
  assert.doesNotMatch(ack, NO_PEOPLE);
  const fixed = ask(pills(mountAt("/today", { overview: overviewWith({ ack: true, fixed: true }) }), "/today")[1]);
  assert.match(fixed, /Someone on your team said it is fixed\. It closes by itself once it is back in range\./);
  // On /today the buttons are under Needs you now.
  assert.match(ask(pills(mountAt("/today", { overview: overviewWith() }), "/today")[1]), /\*\*I'm on it\*\* under Needs you now/);
  // No alert open: says so, and the last one in a line.
  const none = ask(pills(mountAt("/console", { overview: overviewWith({ none: true }) }), "/console")[1]);
  assert.match(none, /^No alert is open right now\. The last one: \*\*Prep fridge x\(\/evil\)\*\* was out of range for 30 minutes and came back on its own\. It is closed\./);
  assert.doesNotMatch(none, NO_PEOPLE);
});

test("1.4.0: 'Is the food still OK?' gives what was measured and the rule of thumb, always with its not-a-ruling note", () => {
  const text = ask(pills(mountAt("/console", { overview: overviewWith() }), "/console")[2]);
  assert.match(text, /\*\*Kitchen walk-in\*\* has been out of range for .+, at worst 48\.5°F, and it is not back yet\./);
  assert.match(text, /under 2 hours above the limit, cool it back down; 2 to 4 hours, use it straight away/);
  assert.match(text, /That is guidance, not a ruling: .+ your written food safety plan and local code decide\./);
  // Signed out: the rule and the note, and where to sign in; never a guess about their food.
  const out = ask(pills(mountAt("/console", { signedIn: false }), "/console")[2]);
  assert.match(out, /not a ruling/);
  assert.match(out, /\[sign in\]\(\/console\)/);
});

test("1.4.0: signed out, the console's answers say where to look instead of guessing; 'How do I connect a sensor?' opens the wizard", () => {
  const given = mountAt("/console", { signedIn: false });
  // The console's sign-in keeps the console's five (the page is the person's own).
  const p = pills(given, "/console");
  assert.match(ask(p[0]), /Sign in to see your fridges: \[sign in\]\(\/console\)/);
  assert.equal(p[3].link, `${BASE}/console#connect`);
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
  assert.ok(js.includes("  followConnectLink();\n  followInspectorLink().catch"), "and while the console is open");
  // The pages hand over what they loaded, and nothing else is fetched for it.
  assert.ok(js.includes("overview: () => (state.token ? state.data : null),"));
  const simple = fsx.readFileSync(app("simple.js"), "utf8");
  assert.ok(simple.includes("window.CyberLogixToday = Object.freeze({ overview: () => lastData });"));
});

test("1.4.0: page suggestions that are facts are answered at once; open questions stay AI prompts; every answer is local", () => {
  const kinds = (pathname, opts) => J(PANEL.cleanChips(mountAt(pathname, opts).chips(), BASE + pathname)
    .map((c) => (c.link ? "link" : c.answer !== undefined ? "answer" : "ai")));
  assert.deepEqual(kinds("/"), ["answer", "answer", "answer"]);
  assert.deepEqual(kinds("/pricing"), ["ai", "answer", "answer"]);
  assert.deepEqual(kinds("/help"), ["answer", "answer", "answer"]);
  assert.deepEqual(kinds("/help/sensors"), ["ai", "answer", "answer"]);
  assert.deepEqual(kinds("/for/restaurant"), ["ai", "ai", "answer"]);
  assert.deepEqual(kinds("/demo"), ["answer", "answer", "link"]);
  assert.deepEqual(kinds("/console", { overview: overviewWith() }), ["answer", "ai", "ai"]);
  const texts = {};
  for (const pathname of ["/", "/pricing", "/help", "/help/sensors", "/for/restaurant", "/demo"]) {
    for (const c of mountAt(pathname).chips()) if (c.answer) texts[c.label] = ask(c);
  }
  assert.match(texts["Will a door left open wake us?"], /^No\. A door opened for a minute doesn't alert anybody/);
  assert.match(texts["Will a door left open wake us?"], /20 minutes for a food fridge, 30 for a freezer/);
  assert.match(texts["Wi-Fi or power goes off?"], /after 30 minutes with nothing from a sensor everyone on call is texted/);
  assert.match(texts["Do you phone at night?"], /^Yes, on plans with calls\. If nobody answers the alert text within 10 minutes, CyberLogix phones the people on call/);
  assert.match(texts["What's in the trial?"], /14 days for up to 5 sensors, needs no card, and includes 5 alert calls/);
  assert.match(texts["Set up step by step"], /^1\. Buy one sensor/);
  for (const [label, text] of Object.entries(texts)) {
    assert.ok(text.length > 40 && text.length < 1200, label);
    // Links only to this site's own pages.
    for (const m of text.matchAll(/\]\(([^)]+)\)/g)) assert.match(m[1], /^\/[a-z]/, `${label}: ${m[1]}`);
  }
});
