#!/usr/bin/env node
/* Browser smoke test for the operations console.
 *
 *   node tools/console_smoke.js                 # starts its own server
 *   BASE_URL=http://127.0.0.1:8080 node tools/console_smoke.js
 *
 * Needs Node 18+ and the `playwright` package with Chromium
 * (npm i playwright && npx playwright install chromium). Without
 * BASE_URL it starts uvicorn on a throwaway database with the sweep
 * loop off, using $PYTHON (default: python3) from the repository root.
 *
 * It signs a new business up through the console's own form, checks
 * every tab while the account is still empty, then registers sensors,
 * drives one into a breach and walks every tab, the sensor drawer and
 * the display controls again, at phone size (390x844) and desktop size.
 * Then: the poll must back off while the server is down, wall mode must
 * not show the account's private details, and an installed app that
 * loses its network must show the offline page rather than a copy of
 * the console.
 *
 * And a new business's first day: the front page says who it is for,
 * the price and the trial in the first screen; sign-up lands in the
 * console; every empty card offers the next step; the checklist ticks
 * itself and goes; the test alert opens no incident; and a simulated
 * unit is stamped as one and never reaches the real fleet.
 *
 * Exit code 0 when everything holds; 1 with a list of failures.
 */
"use strict";

const { chromium } = require("playwright");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const PHONE = { width: 390, height: 844 };
const DESKTOP = { width: 1366, height: 900 };
const TABS = ["all", "fridges", "alerts", "licences", "people", "money",
              "proof", "setup"];
// Words that only reach a page when a value was missing.
const JUNK = /\bundefined\b|\bNaN\b|\bnull\b|\[object Object\]|Infinity/;

const failures = [];
function fail(where, what) {
  failures.push(`${where}: ${what}`);
  console.log(`  FAIL ${where}: ${what}`);
}
function ok(what) { console.log(`  ok   ${what}`); }

/* ---------------- the server ---------------- */

async function waitFor(url, seconds) {
  const until = Date.now() + seconds * 1000;
  while (Date.now() < until) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch (_) { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`${url} did not answer within ${seconds}s`);
}

async function startServer() {
  if (process.env.BASE_URL) return { base: process.env.BASE_URL, stop() {} };
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "clx-smoke-"));
  const port = 20000 + Math.floor(Math.random() * 20000);
  const child = spawn(
    process.env.PYTHON || "python3",
    ["-m", "uvicorn", "main:app", "--port", String(port), "--log-level", "warning"],
    {
      cwd: ROOT,
      env: Object.assign({}, process.env, {
        CYBERLOGIX_DB_PATH: path.join(dir, "smoke.db"),
        CYBERLOGIX_SWEEP_SECONDS: "0",
      }),
      stdio: ["ignore", "inherit", "inherit"],
    },
  );
  const base = `http://127.0.0.1:${port}`;
  await waitFor(`${base}/api/health`, 30);
  return {
    base,
    stop() {
      child.kill();
      fs.rmSync(dir, { recursive: true, force: true });
    },
  };
}

/* ---------------- API helpers (setting the scene) ---------------- */

async function call(base, token, method, route, body) {
  const res = await fetch(base + route, {
    method,
    headers: Object.assign(
      { "Content-Type": "application/json" },
      token ? { Authorization: `Bearer ${token}` } : {},
    ),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let parsed = null;
  try { parsed = JSON.parse(text); } catch (_) { parsed = text; }
  if (!res.ok) throw new Error(`${method} ${route} -> ${res.status} ${text.slice(0, 200)}`);
  return parsed;
}

/* ---------------- page checks ---------------- */

function watchErrors(page, label) {
  const seen = [];
  page.on("pageerror", (err) => seen.push(`JS error: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    // A refused request (a plan that does not include a panel, the
    // deliberate outage below) is logged by the browser itself. That is
    // the network, not the page's code.
    if (/Failed to load resource|net::ERR_/.test(text)) return;
    seen.push(`console error: ${text}`);
  });
  return () => {
    seen.splice(0).forEach((line) => fail(label, line));
  };
}

/* Visible text of the app, card by card, with any junk word reported
   against the card it came from. */
async function junkOnScreen(page) {
  return page.evaluate((pattern) => {
    const junk = new RegExp(pattern);
    const out = [];
    const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const blocks = Array.from(document.querySelectorAll(
      "#app [data-find], #app .tiles, #app .banner, #app nav, #drawer, #first-day"));
    for (const el of blocks) {
      if (!visible(el)) continue;
      const text = el.innerText || "";
      const m = text.match(junk);
      if (m) {
        const at = text.indexOf(m[0]);
        const id = el.id || el.getAttribute("data-find")?.split(" ")[0] || el.className;
        out.push(`${id}: "...${text.slice(Math.max(0, at - 60), at + 40).replace(/\s+/g, " ")}..."`);
      }
    }
    return out;
  }, JUNK.source);
}

/* SMOKE_SHOTS=dir keeps a full-page screenshot of every step, for a
   person to look at. The checks above cannot see a layout that is wrong
   but full of real words. */
async function shoot(page, name) {
  const dir = process.env.SMOKE_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  await page.screenshot({
    path: path.join(dir, name.replace(/[^a-z0-9]+/gi, "-") + ".png"),
    fullPage: true,
  });
}

async function walkTabs(page, label) {
  for (const tab of TABS) {
    await page.click(`.f-tab[data-tab="${tab}"]`);
    await page.waitForTimeout(150);
    const showing = await page.$$eval("#app [data-find]",
      (cards) => cards.filter((c) => !c.hidden).length);
    const none = await page.isVisible("#find-none");
    if (!showing && !none) fail(label, `tab ${tab} shows nothing and says nothing`);
    for (const bad of await junkOnScreen(page)) fail(`${label} tab ${tab}`, bad);
    await shoot(page, `${label} ${tab}`);
    const wide = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    if (wide > 2) fail(`${label} tab ${tab}`, `page scrolls sideways by ${wide}px`);
  }
  await page.click('.f-tab[data-tab="all"]');
  ok(`${label}: every tab walked`);
}

/* ---------------- the walk ---------------- */

async function signUp(browser, base, viewport, who) {
  const context = await browser.newContext({ viewport, serviceWorkers: "block" });
  const page = await context.newPage();
  const flush = watchErrors(page, `${who} sign-up`);
  const logged = [];
  page.on("console", (msg) => logged.push(msg.text()));
  await page.goto(`${base}/console`);
  await page.click("#switch-btn");
  await page.fill("#ob-company", `Smoke ${who} Kitchen`);
  await page.fill("#ob-name", "Sam Smoke");
  await page.fill("#ob-phone", "+15550100");
  await page.fill("#ob-email", `${who}-${Date.now()}@smoke.example`);
  await page.fill("#ob-password", "smoke-test-password-1");
  await page.click("#onboard-form button[type=submit]");
  await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
  const token = await page.evaluate(() => localStorage.getItem("cyberlogix.session"));
  if (!token) throw new Error("sign-up did not leave a session");
  // The business key is shown once, so it has to be somewhere the owner
  // can actually see it -- not in the developer console.
  const key = await page.evaluate(() => {
    const box = document.getElementById("issued-key");
    return box && !box.hidden ? document.getElementById("issued-value").textContent : "";
  });
  if (key && logged.some((line) => line.includes(key))) {
    fail(`${who} sign-up`, "the business key was written to the browser console");
  }
  if (!key) fail(`${who} sign-up`, "the business key is not shown anywhere on the page");
  else {
    await page.click("#issued-done");
    ok(`${who}: the new business key is shown once, on the page`);
  }
  flush();
  ok(`${who}: signed up through the console`);
  return { context, page, token };
}

async function setTheScene(base, token, tag) {
  const id = (name) => `${tag}-${name}`;
  const sensors = [
    { sensor_id: id("WALKIN-1"), industry_vertical: "restaurant", location_name: "Kitchen walk-in" },
    { sensor_id: id("FREEZER-2"), industry_vertical: "restaurant", location_name: "Bar freezer" },
    { sensor_id: id("VAX-3"), industry_vertical: "pharmacy", location_name: "Vaccine fridge" },
  ];
  for (const s of sensors) await call(base, token, "POST", "/api/licenses/me/sensors", s);
  for (const name of ["WALKIN-1", "FREEZER-2"]) {
    for (const t of [0, 1, 2]) {
      await call(base, token, "POST", "/api/sensor-pulse",
                 { sensor_id: id(name), temperature_fahrenheit: t, battery_percent: 80 });
    }
  }
  await call(base, token, "POST", "/api/sensor-pulse",
             { sensor_id: id("VAX-3"), temperature_fahrenheit: 40 });
  // The breach.
  for (const t of [45, 51, 58]) {
    await call(base, token, "POST", "/api/sensor-pulse",
               { sensor_id: id("WALKIN-1"), temperature_fahrenheit: t });
  }
}

async function driveControls(page, label, tag) {
  const flush = watchErrors(page, label);
  // The sensor drawer, from the breaching card.
  await page.click(`#fleet [data-detail="${tag}-WALKIN-1"]`);
  await page.waitForSelector("#drawer:not([hidden])");
  await page.waitForTimeout(400);
  for (const bad of await junkOnScreen(page)) fail(`${label} drawer`, bad);
  await page.keyboard.press("Escape");
  await page.waitForSelector("#drawer", { state: "hidden" });
  ok(`${label}: sensor drawer opens and closes`);

  // Display controls: table view, unit, log, each there and back.
  for (const id of ["toggle-table", "unit-toggle", "term-toggle"]) {
    if (!(await page.isVisible(`#${id}`))) continue;
    await page.click(`#${id}`);
    await page.waitForTimeout(400);
    for (const bad of await junkOnScreen(page)) fail(`${label} after #${id}`, bad);
    await page.click(`#${id}`);
    await page.waitForTimeout(400);
  }
  ok(`${label}: table, unit and log toggles`);

  // Search narrows, and an impossible search says so.
  await page.fill("#find-q", "zzzz-nothing");
  if (!(await page.isVisible("#find-none"))) fail(label, "an empty search says nothing");
  await page.click("#find-clear");

  // Buttons that read rather than change: each must do something and
  // never put a raw error on screen.
  for (const sel of ["#vault-attest", "#vault-verify", "#run-sweep", "#run-compliance", "#group-sites", "#group-sites", "#leave-download"]) {
    if (!(await page.$(sel)) || !(await page.isVisible(sel))) continue;
    await page.click(sel);
    await page.waitForTimeout(800);
  }
  // A proxy answering for a server that is restarting sends HTML. The
  // button has to say something a person can read, not paste the page.
  await page.route("**/api/vault/attestation**", (route) => route.fulfill({
    status: 502, contentType: "text/html",
    body: "<html><head><title>502 Bad Gateway</title></head><body><h1>502 Bad Gateway</h1></body></html>",
  }), { times: 1 });
  if (await page.isVisible("#vault-attest")) {
    await page.click("#vault-attest");
    await page.waitForTimeout(500);
  }
  const toasts = await page.$$eval("#toasts .toast", (els) => els.map((e) => e.textContent));
  if (process.env.SMOKE_VERBOSE) toasts.forEach((t) => console.log(`       toast: ${t}`));
  for (const t of toasts) {
    if (/\[object|Traceback|Internal Server Error|undefined|NaN|<\/?[a-z]/i.test(t)) {
      fail(label, `raw error in a toast: ${t.slice(0, 120)}`);
    }
  }
  flush();
}

async function checkWall(page, label, secrets) {
  // Leave something private on screen first, the way a real owner would:
  // a freshly issued key, and the activity log open.
  await page.evaluate(() => {
    document.getElementById("issued-for").textContent = "WALKIN-1";
    document.getElementById("issued-value").textContent = "clx_sk_smoke_private_key";
    document.getElementById("issued-key").hidden = false;
  });
  if (await page.isVisible("#term-toggle")) {
    const open = await page.isVisible("#terminal");
    if (!open) await page.click("#term-toggle");
  }
  // And narrow the page the way somebody browsing it would: on another
  // tab, with a search typed into the fleet that matches nothing.
  await page.click('.f-tab[data-tab="money"]');
  if (await page.isVisible("#fleet-search")) await page.fill("#fleet-search", "zzzz");
  await page.click("#wall-mode");
  await page.waitForTimeout(300);
  await shoot(page, `${label} wall`);
  const wall = await page.evaluate(() => {
    const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    return {
      fleet: visible(document.getElementById("fleet-card")),
      units: Array.from(document.querySelectorAll("#fleet .sensor")).filter(visible).length,
      all: state.data ? state.data.sensors.length : null,
      buttons: Array.from(document.querySelectorAll("#app button, #app input, #app select"))
        .filter(visible).map((b) => b.id || b.textContent.trim().slice(0, 30)),
    };
  });
  if (!wall.fleet) fail(`${label} wall mode`, "the fleet is not on the wall");
  if (!wall.units) fail(`${label} wall mode`, "no unit is on the wall (a filter carried over)");
  if (wall.buttons.length) fail(`${label} wall mode`, `controls on the wall: ${wall.buttons.join(", ")}`);
  // Nothing on the wall can be pressed into opening something.
  const firstUnit = await page.$("#fleet [data-detail]");
  if (firstUnit) {
    await firstUnit.click({ force: true });
    await page.waitForTimeout(300);
    if (await page.isVisible("#drawer")) fail(`${label} wall mode`, "a tap on a unit opens the drawer");
  }
  const text = await page.evaluate(() => document.body.innerText);
  for (const [name, value] of Object.entries(secrets)) {
    if (value && text.includes(value)) fail(`${label} wall mode`, `shows the ${name}`);
  }
  if (await page.isVisible("#terminal")) fail(`${label} wall mode`, "shows the activity log");
  if (!(await page.isVisible("#wall-exit"))) fail(`${label} wall mode`, "has no way out");
  await page.click("#wall-exit");
  await page.waitForTimeout(200);
  const back = await page.$eval('.f-tab[aria-pressed="true"]', (t) => t.dataset.tab);
  if (back !== "money") fail(`${label} wall mode`, `leaving the wall lost the tab (on ${back})`);
  if (await page.isVisible("#fleet-search")) await page.fill("#fleet-search", "");
  await page.click('.f-tab[data-tab="all"]');
  await page.evaluate(() => {
    document.getElementById("issued-key").hidden = true;
    document.getElementById("issued-value").textContent = "";
  });
  if (await page.isVisible("#terminal")) await page.click("#term-toggle");
  ok(`${label}: wall mode shows the estate, not the account`);
}

/* With the server down the poll must slow down rather than hammer it
   every twenty seconds, and come back by itself when it answers. */
async function checkBackoff(browser, base, token, viewport) {
  const label = "server down";
  const context = await browser.newContext({ viewport, serviceWorkers: "block" });
  await context.addInitScript((t) => localStorage.setItem("cyberlogix.session", t), token);
  const page = await context.newPage();
  await page.clock.install();
  await page.goto(`${base}/console`);
  await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
  let down = true;
  let polls = 0;
  await page.route("**/api/console/overview", (route) => {
    polls += 1;
    return down ? route.abort("connectionrefused") : route.continue();
  });
  // Ten minutes of outage.
  for (let i = 0; i < 60; i++) {
    await page.clock.runFor(10_000);
    await page.waitForTimeout(20);
  }
  // A fixed twenty-second poll would have made thirty attempts.
  if (polls > 12) fail(label, `${polls} polls in ten minutes of outage (no back-off)`);
  else if (polls < 3) fail(label, `only ${polls} polls in ten minutes: it gave up`);
  else ok(`${label}: ${polls} polls in ten minutes, backing off`);
  if (!(await page.isVisible("#offline-banner"))) fail(label, "no 'not live' banner while down");
  down = false;
  const before = polls;
  for (let i = 0; i < 40 && polls === before; i++) {
    await page.clock.runFor(10_000);
    await page.waitForTimeout(20);
  }
  await page.waitForTimeout(500);
  if (polls === before) fail(label, "never tried again after the server came back");
  else if (await page.isVisible("#offline-banner")) fail(label, "banner stuck after recovery");
  else ok(`${label}: recovers by itself when the server answers`);
  await context.close();
}

/* An installed app with no network shows the offline page. Not a cached
   console, and not the sign-in form. */
async function checkOffline(browser, base, token) {
  const label = "offline";
  const context = await browser.newContext({ viewport: PHONE });
  await context.addInitScript((t) => localStorage.setItem("cyberlogix.session", t), token);
  const page = await context.newPage();
  await page.goto(`${base}/console`);
  await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
  await page.evaluate(() => navigator.serviceWorker.ready);
  // A second load, so the page is controlled by the worker.
  await page.reload();
  await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
  const controlled = await page.evaluate(() => !!navigator.serviceWorker.controller);
  if (!controlled) { fail(label, "the service worker never took control"); await context.close(); return; }

  await context.setOffline(true);
  for (const route of ["/console", "/today"]) {
    // A page of its own each time, so one navigation cannot race the next.
    const tab = await context.newPage();
    try {
      await tab.goto(`${base}${route}`, { timeout: 10000, waitUntil: "load" });
    } catch (err) {
      fail(label, `${route}: navigation failed outright (${err.message.split("\n")[0]})`);
      await tab.close();
      continue;
    }
    const title = await tab.title();
    await tab.close();
    if (!/No connection/.test(title)) fail(label, `${route} offline shows "${title}", not the offline page`);
    else ok(`${label}: ${route} shows the offline page`);
  }
  await context.setOffline(false);
  await context.close();
}

/* ---------------- the first day ---------------- */

/* Who it is for, the price and the trial, without scrolling. */
async function checkLanding(browser, base) {
  for (const [label, viewport] of [["phone", PHONE], ["laptop", { width: 1366, height: 768 }]]) {
    const context = await browser.newContext({ viewport, serviceWorkers: "block",
                                               reducedMotion: "reduce" });
    const page = await context.newPage();
    await page.goto(`${base}/`);
    await page.waitForTimeout(300);
    const got = await page.evaluate(() => {
      const facts = document.getElementById("facts");
      const cta = document.querySelector(".hero .btn-primary");
      return {
        text: facts ? facts.innerText : "",
        factsBottom: facts ? facts.getBoundingClientRect().bottom : 1e9,
        ctaBottom: cta ? cta.getBoundingClientRect().bottom : 1e9,
        height: window.innerHeight,
        raw: document.body.innerHTML.includes("%%"),
      };
    });
    await shoot(page, `landing ${label}`);
    const where = `landing ${label}`;
    if (got.raw) fail(where, "a %%TOKEN%% was left on the page");
    for (const want of [/Who it is for/i, /From \$\d[\d,]* a month per/, /\d+ days on up to \d+ sensors\. No card\./]) {
      if (!want.test(got.text)) fail(where, `the first screen does not say ${want}`);
    }
    if (got.factsBottom > got.height) fail(where, `who/price/trial end at ${Math.round(got.factsBottom)}px, below the ${got.height}px screen`);
    else if (got.ctaBottom > got.height) fail(where, "the free-trial button is below the first screen");
    else ok(`${where}: who it is for, the price and the trial in the first screen`);
    await context.close();
  }
}

async function checkFirstDay(browser, base) {
  const label = "first day";
  const context = await browser.newContext({ viewport: PHONE, serviceWorkers: "block" });
  const page = await context.newPage();
  const flush = watchErrors(page, label);
  await page.goto(`${base}/signup`);
  await page.waitForFunction(() => document.querySelector("#industry_vertical").options.length > 1);
  await page.fill("#company_name", "First Day Diner");
  await page.fill("#full_name", "Fran Day");
  await page.fill("#email", `first-${Date.now()}@smoke.example`);
  await page.fill("#contact_phone", "+15550177");
  await page.fill("#password", "smoke-test-password-1");
  await page.selectOption("#industry_vertical", "restaurant");
  await page.click("#go");
  await page.waitForURL(`${base}/console`, { timeout: 15000 });
  await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
  ok(`${label}: sign-up lands in the console, signed in`);
  await page.waitForTimeout(600);
  if (!(await page.isVisible("#first-day"))) fail(label, "no checklist on a new account");
  const key = await page.textContent("#issued-value");
  if (!/^clx_/.test(key || "")) fail(label, "the business key from sign-up is not shown");

  // Every empty card that a new business meets offers the next step.
  for (const card of ["#fleet", "#incidents", "#roster-body", "#site-body"]) {
    if (!(await page.$(`${card} .empty [data-go]`))) fail(label, `${card} is empty and offers nothing to do`);
  }
  for (const bad of await junkOnScreen(page)) fail(label, bad);
  await shoot(page, "first day empty");

  // The simulator, from the empty fleet: stamped, apart, and not a sensor.
  await page.click('#fleet [data-go="simulate"]');
  await page.waitForSelector("#sim-fleet .sensor.is-simulated", { timeout: 10000 });
  const stamp = await page.textContent("#sim-fleet .sim-stamp");
  if (!/SIMULATED/.test(stamp)) fail(label, "a simulated card is not stamped SIMULATED");
  await page.click("#sim-fleet [data-sim-break]");
  try {
    await page.waitForSelector("#sim-fleet .sensor.is-breaching", { timeout: 30000 });
    const would = await page.textContent("#sim-fleet .sim-would");
    if (!/Not sent/.test(would)) fail(label, "a simulated breach does not say nothing was sent");
    else ok(`${label}: a simulated unit fails on screen and nothing is sent`);
  } catch (_) { fail(label, "a broken simulated unit never breached"); }
  await shoot(page, "first day simulated");
  const real = await page.$$eval("#fleet .sensor", (els) => els.length);
  if (real) fail(label, "a simulated unit landed in the real fleet");
  await page.click("#sim-stop");
  await page.waitForSelector("#sim-zone", { state: "hidden" });

  // The checklist, one step at a time, through its own buttons.
  const steps = [
    ["site", async () => { await page.fill("#site-name", "Main Street"); await page.click("#site-form button[type=submit]"); }],
    ["sensor", async () => { await page.fill("#add-loc", "Kitchen walk-in"); await page.click("#add-form button[type=submit]"); }],
    ["oncall", async () => { await page.fill("#ct-name", "Fran"); await page.fill("#ct-phone", "+15550177"); await page.click("#contact-form button[type=submit]"); }],
    ["test", async () => { page.once("dialog", (d) => d.accept()); await page.click("#test-alert"); }],
  ];
  for (const [step, doIt] of steps) {
    const button = `#first-day [data-go="${step}"]`;
    if (!(await page.isVisible(button))) { fail(label, `no checklist button for ${step}`); continue; }
    await page.click(button);
    await page.waitForTimeout(500);
    await doIt();
    await page.waitForTimeout(1200);
    if (step !== "test" && (await page.isVisible(`#first-day [data-go="${step}"]`))) {
      fail(label, `the ${step} box did not tick itself`);
    }
  }
  await page.waitForTimeout(800);
  if (await page.isVisible("#first-day")) fail(label, "the checklist is still there with everything done");
  else ok(`${label}: the checklist ticks itself and goes when it is done`);
  const result = await page.textContent("#test-alert-out");
  if (!/dry run|written|sent/i.test(result || "")) fail(label, `the test alert said: ${result}`);
  const incidents = await page.$$eval("#incidents .incident", (els) => els.length);
  if (incidents) fail(label, "the test alert opened an incident");
  await shoot(page, "first day done");
  flush();
  await context.close();
}

async function main() {
  const server = await startServer();
  const browser = await chromium.launch();
  try {
    let sceneToken = null;
    let secrets = {};
    for (const [who, viewport] of [["phone", PHONE], ["desktop", DESKTOP]]) {
      console.log(`\n${who} ${viewport.width}x${viewport.height}`);
      const { context, page, token } = await signUp(browser, server.base, viewport, who);
      const flush = watchErrors(page, `${who} empty`);
      await page.waitForTimeout(800);
      await walkTabs(page, `${who} empty account`);
      flush();

      await setTheScene(server.base, token, who.toUpperCase());
      await page.reload();
      await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
      await page.waitForTimeout(1200);
      const flush2 = watchErrors(page, `${who} breach`);
      const breaching = await page.$$eval(".sensor.is-breaching", (els) => els.length);
      if (!breaching) fail(`${who} breach`, "no card shows the breach");
      else ok(`${who}: the breach shows on the fleet`);
      await walkTabs(page, `${who} with a breach`);
      flush2();
      await driveControls(page, who, who.toUpperCase());

      const me = await call(server.base, token, "GET", "/api/accounts/me");
      secrets = { email: me.user.email, phone: "+15550100", "issued key": "clx_sk_smoke_private_key" };
      await checkWall(page, who, secrets);
      sceneToken = token;
      await context.close();
    }
    console.log("\nfirst day");
    await checkLanding(browser, server.base);
    await checkFirstDay(browser, server.base);
    console.log("\nresilience");
    await checkBackoff(browser, server.base, sceneToken, DESKTOP);
    await checkOffline(browser, server.base, sceneToken);
  } finally {
    await browser.close();
    server.stop();
  }
  if (failures.length) {
    console.log(`\n${failures.length} failure(s):`);
    failures.forEach((f) => console.log(`  - ${f}`));
    process.exit(1);
  }
  console.log("\nconsole smoke: all checks passed");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
