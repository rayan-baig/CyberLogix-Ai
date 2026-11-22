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

// 3.26.0: on a new business's first screen on a phone the robot sat
// top-left, on the logo. Core 1.3.0's last resort (every corner busy) picks
// the corner whose tucked-away robot covers least, and a box in `avoid`
// counts as a million square pixels. With the whole Getting started card in
// this app's avoid list (it spans a phone's screen) both bottom corners
// "covered" it, so a top corner won. This runs the core's own choosePlace on
// that screen, with the avoid list as the config has it now.
test("the robot never goes onto the logo on a new business's phone screen", () => {
  const fs = require("node:fs");
  const M = require(core("pilot-mascot.js"));
  const config = fs.readFileSync(app("pilot-cyberlogix.js"), "utf8");
  const list = config.slice(config.indexOf("avoid: ["), config.indexOf("],", config.indexOf("avoid: [")));
  const avoid = new Set(Array.from(list.matchAll(/"([^"]+)"/g), (m) => m[1]));
  const view = { width: 375, height: 812 };
  const size = { width: 56, height: 54 };
  // The page: the header bar (logo, Sign in), the Getting started card
  // filling the screen below it, and its button row; the card's words stop
  // short of the side room, so the bottom corners have nothing to read.
  const page = [
    { sel: ".top", box: { left: 0, top: 0, right: 375, bottom: 60 } },
    { sel: "#app > .nav", box: { left: 0, top: 0, right: 375, bottom: 60 } },
    { sel: "#first-day", box: { left: 30, top: 250, right: 345, bottom: 1400 } },
    { sel: ".checklist-acts", box: { left: 79, top: 690, right: 300, bottom: 726 } },
  ];
  const words = [{ left: 16, top: 20, right: 42, bottom: 46 }, { left: 290, top: 14, right: 359, bottom: 52 }];
  const blocks = page.filter((p) => avoid.has(p.sel)).map((p) => p.box);
  const busy = (box) => words.some((w) => M.overlaps(box, w, 0));
  const cost = (box) => words.reduce((n, w) => n + M.areaOf(w, box), 0);
  const place = M.choosePlace({ corner: "br", view, size, gap: 12, blocks, hard: [], busy, cost,
    keep: { scale: 0.82, margin: 3 } });
  assert.equal(place.corner[0], "b", `robot went to ${place.corner}`);
  // And the old list (the whole card, no header) is what sent it top-left.
  const old = M.choosePlace({ corner: "br", view, size, gap: 12, hard: [], busy, cost, keep: { scale: 0.82, margin: 3 },
    blocks: [page[2].box] });
  assert.equal(old.corner, "tl");
});

// 3.26.0: the visitor's three pills that need no AI go to their page, key or
// no key; the core has no link pill, so the config catches the press.
test("the visitor's link pills go to their pages without a key", () => {
  const fs = require("node:fs");
  const config = fs.readFileSync(app("pilot-cyberlogix.js"), "utf8");
  for (const [label, href] of [["See it live", "/demo"], ["What does it cost?", "/pricing#calc-title"],
    ["What sensors do I need?", "/help/sensors"]]) {
    assert.ok(config.includes(`"${label}": "${href}"`), label);
    assert.ok(config.includes(`label: "${label}"`), label);
  }
  assert.ok(config.includes("event.stopImmediatePropagation();"));
  assert.ok(config.includes('closest(".pilot-pill")'));
});
