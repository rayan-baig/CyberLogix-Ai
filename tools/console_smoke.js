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
 * And one person with five businesses and a different role in some: the
 * switcher drawer at phone size, the right data and no other business's
 * after a switch, a reload and a new sign-in staying where she left off,
 * and the monthly email's link to another business's inspector log.
 *
 * And who is on call: an owner builds a rotation and sets quiet hours
 * through the card's forms; the card names who is on now and next, and a
 * muted person with turns as a gap; a viewer sees it all with nothing
 * to press.
 *
 * And a reseller: the operator makes a partner, finds a new business by
 * name and attaches it; the partner signs in to /partners with the key
 * and sees the account, that it needs a visit, and its units.
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

// The operator key the walk uses. Set to this on the server the smoke
// test starts; against BASE_URL, set SMOKE_ADMIN_KEY or the partner walk
// is skipped.
const ADMIN_KEY = process.env.SMOKE_ADMIN_KEY || "smoke-admin-key";

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
        // So the switcher walk can start five businesses from what look
        // like separate callers without meeting the sign-up ceiling.
        CYBERLOGIX_TRUST_PROXY_HEADER: "1",
        // So the partner walk can make a reseller, as the operator would.
        CYBERLOGIX_ADMIN_KEY: ADMIN_KEY,
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

/* Every context the walk opens reports a Content-Security-Policy refusal
   as a failure, whichever page it is on. The policy allows no inline
   script or style, so a refusal is a piece of the page that did not run
   or did not draw -- a dead button, an unstyled card -- and the browser
   says so only in its console. */
const CSP_INIT = () => {
  document.addEventListener("securitypolicyviolation", (e) => {
    console.error(`CSP violation: ${e.violatedDirective} blocked `
      + `${e.blockedURI || "inline"} at ${e.sourceFile || location.pathname}:${e.lineNumber}`);
  });
};
const CSP_PATTERN = /CSP violation|Content Security Policy/;

function watchPolicy(browser) {
  const open = browser.newContext.bind(browser);
  browser.newContext = async (options) => {
    const context = await open(options);
    await context.addInitScript(CSP_INIT);
    context.on("console", (msg) => {
      if (CSP_PATTERN.test(msg.text())) {
        let where = "";
        try { where = new URL(msg.page().url()).pathname; } catch (_) { /* closed */ }
        fail(`policy ${where}`, msg.text().slice(0, 240));
      }
    });
    return context;
  };
}

/* A style="..." attribute became a utility class (utilities.css). An
   inline style beat any rule, and a class does not, so this checks, on
   every element drawn, that each utility class still gets its way: the
   computed value with the class must equal the value with the same
   declaration applied inline. A mismatch is a component rule winning,
   and a card laid out differently than before. */
async function utilitiesHold(page) {
  return page.evaluate(() => {
    const rules = {};
    for (const sheet of Array.from(document.styleSheets)) {
      let list;
      try { list = sheet.cssRules; } catch (_) { continue; }
      for (const rule of Array.from(list || [])) {
        const m = /^\.(u-[a-z0-9-]+)\.\1$/.exec(rule.selectorText || "");
        if (m) rules[m[1]] = rule.style;
      }
    }
    const out = [];
    for (const el of Array.from(document.querySelectorAll('[class*="u-"]'))) {
      const mine = Array.from(el.classList).filter((c) => c.startsWith("u-"));
      for (const cls of mine) {
        const decl = rules[cls];
        if (!decl) { out.push(`${cls} has no rule`); continue; }
        for (const prop of Array.from(decl)) {
          // `font: inherit` then `font-size: 13px` on one element: the
          // longhand is meant to win, so the shorthand is not checked on it.
          if (mine.some((other) => other !== cls && rules[other]
              && Array.from(rules[other]).includes(prop))
              && Array.from(decl).length > 1) continue;
          const before = getComputedStyle(el).getPropertyValue(prop);
          const old = el.style.getPropertyValue(prop);
          el.style.setProperty(prop, decl.getPropertyValue(prop));
          const forced = getComputedStyle(el).getPropertyValue(prop);
          el.style.setProperty(prop, old);
          if (before !== forced) {
            const id = el.id ? `#${el.id}` : `${el.tagName.toLowerCase()}.${Array.from(el.classList).join(".")}`;
            out.push(`${id}: ${cls} ${prop} is ${before}, not ${forced}`);
          }
        }
      }
    }
    return Array.from(new Set(out)).slice(0, 20);
  });
}

/* Every public and private page, at phone and desktop size: no policy
   refusal, no script error, no utility class losing to a rule. */
async function checkPages(browser, base, token) {
  const pages = ["/", "/signup", "/pricing", "/help", "/help/sensors", "/for", "/for/restaurant",
                 "/for/pharmacy", "/for/restaurant/sheet", "/for/cryostorage/sheet",
                 "/developers", "/legal", "/reset", "/partners", "/book",
                 "/today", "/offline", "/proof/not-a-real-token", "/no-such-page"];
  for (const [label, viewport] of [["phone", PHONE], ["desktop", DESKTOP]]) {
    const context = await browser.newContext({ viewport, serviceWorkers: "block",
                                               reducedMotion: "reduce" });
    await context.addInitScript((t) => localStorage.setItem("cyberlogix.session", t), token);
    for (const route of pages) {
      const page = await context.newPage();
      const flush = watchErrors(page, `${label} ${route}`);
      await page.goto(`${base}${route}`, { waitUntil: "load" });
      await page.waitForTimeout(700);
      for (const bad of await utilitiesHold(page)) fail(`${label} ${route}`, bad);
      const wide = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      if (wide > 2) fail(`${label} ${route}`, `page scrolls sideways by ${wide}px`);
      await shoot(page, `page ${label} ${route}`);
      flush();
      await page.close();
    }
    await context.close();
    ok(`${label}: ${pages.length} pages load with no policy refusal or script error`);
  }
}

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
    for (const bad of await utilitiesHold(page)) fail(`${label} tab ${tab}`, bad);
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
  for (const bad of await utilitiesHold(page)) fail(`${label} drawer`, bad);
  const bars = await page.$$eval("[data-w]", (els) => els.filter((e) => !e.style.width).length);
  if (bars) fail(`${label} drawer`, `${bars} bar(s) drawn with no width`);
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

/* ---------------- who is on call: rotations and quiet hours ---------------- */

async function openConsoleAs(browser, base, token, viewport, label) {
  const context = await browser.newContext({ viewport, serviceWorkers: "block" });
  await context.addInitScript((t) => localStorage.setItem("cyberlogix.session", t), token);
  const page = await context.newPage();
  const flush = watchErrors(page, label);
  await page.goto(`${base}/console`);
  await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
  await page.waitForTimeout(900);
  await page.click('.f-tab[data-tab="people"]');
  await page.waitForTimeout(200);
  return { context, page, flush };
}

async function screenIsClean(page, label) {
  for (const bad of await junkOnScreen(page)) fail(label, bad);
  for (const bad of await utilitiesHold(page)) fail(label, bad);
  const wide = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (wide > 2) fail(label, `page scrolls sideways by ${wide}px`);
}

/* An owner builds a rotation and sets quiet hours through the card's own
   forms, at phone size; the card says who is on now and next, and names
   a muted person with turns as a gap. A viewer sees all of it, on both
   sizes, with nothing to press. Then the owner stops the rotation. */
async function checkRotation(browser, base, token) {
  const names = ["Riley Rota", "Morgan Rota", "Casey Rota"];
  const people = [];
  for (const [i, name] of names.entries()) {
    people.push(await call(base, token, "POST", "/api/contacts",
      { full_name: name, phone: `+1555010${i + 3}` }));
  }

  // The owner, on a phone.
  let label = "rotation phone owner";
  let { context, page, flush } = await openConsoleAs(browser, base, token, PHONE, label);
  if (!(await page.isVisible("#rotation-form"))) fail(label, "an owner has no form to make a rotation");
  if (!/everyone on the roster is\s+on call all the time/.test(await page.textContent("#rotation-body"))) {
    fail(label, "with no rotation the card does not say everyone is on call");
  }
  for (const person of [people[1], people[0]]) {
    await page.selectOption("#rot-add", person.contact_id);
    await page.click("#rot-add-btn");
  }
  // Riley first: move up from second place.
  await page.click('#rot-members [data-rot-up="1"]');
  const order = await page.$$eval("#rot-members .rota-member", (els) => els.map((e) => e.textContent.trim()));
  if (!/Riley/.test(order[0] || "") || !/Morgan/.test(order[1] || "")) {
    fail(label, `the order did not follow the Up button: ${order.join(" | ")}`);
  }
  await page.fill("#rot-tz", "America/New_York");
  await page.fill("#rot-handover", "08:00");
  await page.fill("#rot-days", "7");
  await page.click('#rotation-form button[type="submit"]');
  try {
    await page.waitForSelector("#rotation-body .rota", { timeout: 8000 });
    const text = await page.textContent("#rotation-body");
    if (!/On call now\s*Riley Rota/.test(text)) fail(label, `not saying who is on now: ${text.slice(0, 160)}`);
    if (!/Next\s*Morgan Rota/.test(text)) fail(label, "not saying who is next");
    if (!/Always on call\s*Casey Rota/.test(text)) fail(label, "not saying who is on call all the time");
    if (!(await page.$("#rotation-body .rota-shifts li.is-now"))) fail(label, "no list of upcoming turns");
    else ok("rotation: an owner builds one on a phone; now, next and always on call are shown");
  } catch (_) { fail(label, "the saved rotation never appeared"); }

  await page.selectOption("#qh-person", people[2].contact_id);
  await page.fill("#qh-start", "22:00");
  await page.fill("#qh-end", "07:00");
  await page.fill("#qh-tz", "Europe/London");
  await page.click('#quiet-form button[type="submit"]');
  await page.waitForTimeout(1200);
  const quiet = await page.textContent("#quiet-body");
  if (!/Casey Rota\s*Quiet 22:00 to 07:00 Europe\/London/.test(quiet)) {
    fail(label, `quiet hours not shown after saving: ${quiet.slice(0, 160)}`);
  } else ok("rotation: quiet hours set through the form and shown");
  await screenIsClean(page, label);
  await shoot(page, "rotation phone owner");
  flush();
  await context.close();

  // A muted person with turns is named as a gap.
  await call(base, token, "PATCH", `/api/contacts/${people[1].contact_id}`, { active: false });

  // A viewer, at both sizes: everything to read, nothing to press.
  const viewerEmail = `viewer-${Date.now()}@smoke.example`;
  await call(base, token, "POST", "/api/accounts/users", {
    email: viewerEmail, full_name: "Vic Viewer", password: "smoke-viewer-password-1", role: "viewer",
  });
  const session = await call(base, null, "POST", "/api/accounts/login",
    { email: viewerEmail, password: "smoke-viewer-password-1" });
  for (const [size, viewport] of [["phone", PHONE], ["desktop", DESKTOP]]) {
    label = `rotation ${size} viewer`;
    ({ context, page, flush } = await openConsoleAs(browser, base, session.token, viewport, label));
    const seen = await page.evaluate(() => {
      const visible = (el) => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
      const card = document.getElementById("find-on-call-rotation");
      return {
        rota: card.querySelectorAll(".rota").length,
        text: document.getElementById("rotation-body").innerText,
        forms: ["rotation-form", "quiet-form"].filter((id) => visible(document.getElementById(id))),
        buttons: Array.from(card.querySelectorAll("button")).filter(visible).length,
        note: visible(document.getElementById("rotation-viewer")),
      };
    });
    if (!seen.rota) fail(label, "a viewer cannot see the rotation");
    if (!/Riley Rota/.test(seen.text)) fail(label, "a viewer cannot see who is on call");
    if (!/muted but has turns/.test(seen.text)) fail(label, "a muted person with turns is not named as a gap");
    if (seen.forms.length) fail(label, `a viewer is shown ${seen.forms.join(", ")}`);
    if (seen.buttons) fail(label, `a viewer is shown ${seen.buttons} button(s) on the card`);
    if (!seen.note) fail(label, "a viewer is not told who can change it");
    if (!seen.forms.length && !seen.buttons && seen.rota) ok(`rotation: a ${size} viewer sees it and can change nothing`);
    await screenIsClean(page, label);
    await shoot(page, `rotation ${size} viewer`);
    flush();
    await context.close();
  }

  // The owner on a desktop, then stops it.
  label = "rotation desktop owner";
  ({ context, page, flush } = await openConsoleAs(browser, base, token, DESKTOP, label));
  await screenIsClean(page, label);
  await shoot(page, "rotation desktop owner");
  page.once("dialog", (d) => d.accept());
  await page.click('#rotation-body [data-rota-stop="estate"]');
  try {
    await page.waitForFunction(() => !document.querySelector("#rotation-body .rota"), null, { timeout: 8000 });
    ok("rotation: an owner stops it from the card");
  } catch (_) { fail(label, "stopping the rotation did not take it off the card"); }
  flush();
  await context.close();
  await call(base, token, "PATCH", `/api/contacts/${people[1].contact_id}`, { active: true });
}

/* ---------------- one person, five businesses ---------------- */

async function callFrom(base, token, method, route, body, caller) {
  const res = await fetch(base + route, {
    method,
    headers: Object.assign(
      { "Content-Type": "application/json", "X-Forwarded-For": caller },
      token ? { Authorization: `Bearer ${token}` } : {},
    ),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`${method} ${route} -> ${res.status} ${text.slice(0, 200)}`);
  return JSON.parse(text);
}

/* Dana owns three businesses, is an operator at Sam's and a viewer at
   Kim's. At phone size: the chip counts the others that need her, the
   drawer lists all five with her role in each and does not scroll
   sideways, opening one shows that business and none of the others'
   units, a reload stays there, signing in again through the form lands
   there, and a link to another business's inspector log switches to it
   and fills in the dates. The drawer again at desktop size. */
async function checkSwitcher(browser, base) {
  const stamp = Date.now();
  const password = "smoke-switcher-password-1";
  const person = (name, tag, n) => ({
    full_name: name, email: `${tag}-${stamp}@switch.example`, password,
    contact_phone: "+15550100", industry_vertical: "restaurant",
    caller: `198.51.100.${n}`,
  });
  const dana = person("Dana Switch", "dana", 21);
  const businesses = [];
  const signupAs = async (who, company) => {
    const made = await callFrom(base, null, "POST", "/api/signup",
      Object.assign({ company_name: company }, who, { caller: undefined }), who.caller);
    return made;
  };
  let made = await signupAs(dana, "Switch Grill");
  businesses.push({ name: "Switch Grill", id: made.tenant.tenant_id, key: made.api_key, role: "owner" });
  let token = made.token;
  for (const name of ["Switch Bakery", "Switch Deli"]) {
    made = await callFrom(base, token, "POST", "/api/accounts/businesses",
      { company_name: name, industry_vertical: "restaurant" }, dana.caller);
    businesses.push({ name, id: made.tenant.tenant_id, key: made.api_key, role: "owner" });
    token = made.token;
  }
  for (const [who, name, role, n] of [["sam", "Switch Pharmacy", "operator", 22],
                                      ["kim", "Switch Florist", "viewer", 23]]) {
    const owner = person(`${who} Owner`, who, n);
    const theirs = await signupAs(owner, name);
    businesses.push({ name, id: theirs.tenant.tenant_id, key: theirs.api_key, role });
    const code = await callFrom(base, theirs.token, "POST", "/api/accounts/businesses/join-codes",
      { role }, owner.caller);
    await callFrom(base, token, "POST", "/api/accounts/businesses/join", { code: code.code }, dana.caller);
  }
  for (const [i, b] of businesses.entries()) {
    const unit = `SW${i}-UNIT`;
    b.unit = unit;
    const headers = { "Content-Type": "application/json", "X-CyberLogix-Key": b.key };
    await fetch(`${base}/api/licenses/me/sensors`, { method: "POST", headers,
      body: JSON.stringify({ sensor_id: unit, industry_vertical: "restaurant",
                             location_name: `${b.name} cold room` }) });
    await fetch(`${base}/api/sensor-pulse`, { method: "POST", headers,
      body: JSON.stringify({ sensor_id: unit, temperature_fahrenheit: 50 }) });
  }
  const florist = businesses[4];
  const grill = businesses[0];

  // Phone.
  let label = "switcher phone";
  const context = await browser.newContext({ viewport: PHONE, serviceWorkers: "block" });
  await context.addInitScript((t) => {
    if (!sessionStorage.getItem("smoke.seeded")) {
      localStorage.setItem("cyberlogix.session", t);
      sessionStorage.setItem("smoke.seeded", "1");
    }
  }, token);
  const page = await context.newPage();
  let flush = watchErrors(page, label);
  await page.goto(`${base}/console`);
  await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
  await page.waitForTimeout(900);
  const chip = (await page.textContent("#chip-company")).trim();
  if (!chip.startsWith("Switch Deli")) fail(label, `the chip shows "${chip}", not the business open`);
  const badge = await page.isVisible("#biz-badge") ? (await page.textContent("#biz-badge")).trim() : "";
  if (badge !== "4") fail(label, `the chip counts ${badge || "nothing"} other businesses needing her, not 4`);
  await page.click("#chip-company");
  await page.waitForSelector("#drawer .biz-row", { timeout: 8000 });
  const rows = await page.$$eval("#drawer .biz-row", (els) => els.map((e) => e.innerText));
  if (rows.length !== 5) fail(label, `the drawer lists ${rows.length} businesses, not 5`);
  for (const b of businesses) {
    const row = rows.find((r) => r.includes(b.name)) || "";
    if (!row.includes(`you are ${b.role}`)) fail(label, `${b.name} does not say she is ${b.role} there`);
  }
  await screenIsClean(page, label);
  await shoot(page, "switcher phone drawer");
  if (!failures.some((f) => f.startsWith(label))) ok("switcher: five businesses, her role in each, on a phone");

  await Promise.all([
    page.waitForNavigation({ timeout: 15000 }),
    page.click(`#drawer [data-open-biz="${florist.id}"]`),
  ]);
  await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
  await page.waitForTimeout(1200);
  const seen = async () => page.evaluate(() => document.getElementById("app").innerText);
  let text = await seen();
  if (!(await page.textContent("#chip-company")).includes(florist.name)) fail(label, "opening the florist did not open it");
  if (!text.includes(florist.unit)) fail(label, "the florist's own unit is not shown");
  for (const b of businesses) {
    if (b !== florist && (text.includes(b.unit) || text.includes(`${b.name} cold room`))) {
      fail(label, `after switching, ${b.name}'s unit is on the page`);
    }
  }
  if (!(await page.isVisible("#role-banner"))
      || !/viewer/i.test(await page.textContent("#role-banner"))) {
    fail(label, "a viewer is not told she is a viewer here");
  }
  await screenIsClean(page, `${label} florist`);
  await shoot(page, "switcher phone florist");

  await page.reload();
  await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
  await page.waitForTimeout(800);
  if (!(await page.textContent("#chip-company")).includes(florist.name)) {
    fail(label, "a reload did not stay in the business she opened");
  }

  // Signed out and in again through the form: where she left off.
  const current = await page.evaluate(() => localStorage.getItem("cyberlogix.session"));
  await callFrom(base, current, "POST", "/api/accounts/logout", undefined, dana.caller);
  await page.evaluate(() => localStorage.removeItem("cyberlogix.session"));
  await page.reload();
  await page.waitForSelector("#signin-form", { timeout: 15000 });
  await page.fill("#si-email", dana.email);
  await page.fill("#si-password", password);
  await page.click('#signin-form button[type="submit"]');
  await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
  await page.waitForTimeout(900);
  if (!(await page.textContent("#chip-company")).includes(florist.name)) {
    fail(label, "signing in again did not open the business she had open");
  } else ok("switcher: a reload and a new sign-in both stay where she left off");

  // The monthly email's link to another business's log.
  await page.goto(`${base}/console#inspector-log?business=${grill.id}&from=2026-10-01&to=2026-10-31`);
  await page.waitForTimeout(2500);
  await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
  await page.waitForTimeout(1200);
  const landed = (await page.textContent("#chip-company")).trim();
  const dates = await page.evaluate(() => [
    document.getElementById("inspector-from").value,
    document.getElementById("inspector-to").value,
    !document.getElementById("find-inspector-log").hidden,
  ]);
  if (!landed.startsWith(grill.name)) fail(label, `the log link opened "${landed}", not ${grill.name}`);
  if (dates[0] !== "2026-10-01" || dates[1] !== "2026-10-31" || !dates[2]) {
    fail(label, `the log link did not show the log card with its dates: ${dates.join(", ")}`);
  } else ok("switcher: the monthly email's log link opens that business with the dates filled");
  await shoot(page, "switcher phone inspector link");
  flush();
  await context.close();

  // Desktop: the drawer.
  label = "switcher desktop";
  const latest = await callFrom(base, null, "POST", "/api/accounts/login",
    { email: dana.email, password }, dana.caller);
  const desk = await openConsoleAs(browser, base, latest.token, DESKTOP, label);
  await desk.page.click("#chip-company");
  await desk.page.waitForSelector("#drawer .biz-row", { timeout: 8000 });
  await screenIsClean(desk.page, label);
  await shoot(desk.page, "switcher desktop drawer");
  desk.flush();
  await desk.context.close();
  if (!failures.some((f) => f.startsWith("switcher"))) ok("switcher: desktop drawer is clean");
}

/* ---------------- a reseller's portal ---------------- */

/* The operator makes a partner, finds a new business by name and puts it
   under them; the business fits a sensor and it goes warm. Then the
   partner signs in to /partners with the key, at phone and desktop size:
   the book shows the account and that it needs a visit, the account opens
   with its units, and nothing on screen is missing a value or scrolls
   sideways. */
async function checkPartner(browser, base) {
  const admin = { "Content-Type": "application/json", "X-CyberLogix-Admin": ADMIN_KEY };
  const made = await fetch(`${base}/api/partners`, {
    method: "POST", headers: admin,
    body: JSON.stringify({ company_name: "Smoke Refrigeration", contact_name: "Ray Smoke",
                           contact_email: "ray@smoke-refrigeration.example" }),
  });
  if (made.status === 401 || made.status === 503) {
    console.log("  skip partner walk: the server does not take this admin key");
    return;
  }
  if (!made.ok) { fail("partner", `making a partner answered ${made.status}`); return; }
  const partner = await made.json();
  const company = `Partner Smoke Grill ${Date.now()}`;
  const business = await callFrom(base, null, "POST", "/api/signup", {
    company_name: company, full_name: "Sam Partner", email: `sam-${Date.now()}@partner.example`,
    password: "smoke-partner-password-1", contact_phone: "+15550100",
    industry_vertical: "restaurant",
  }, "198.51.100.77");
  const found = await (await fetch(
    `${base}/api/partners/businesses?search=${encodeURIComponent(company)}`,
    { headers: admin })).json();
  const id = (found.businesses[0] || {}).tenant_id;
  if (id !== business.tenant.tenant_id) { fail("partner", "the search did not find the business"); return; }
  await fetch(`${base}/api/partners/${partner.partner_id}/accounts`, {
    method: "POST", headers: admin, body: JSON.stringify({ tenant_id: id }),
  });
  await call(base, business.token, "POST", "/api/licenses/me/sensors",
             { sensor_id: "PS-WALKIN", industry_vertical: "restaurant", location_name: "Walk-in" });
  await call(base, business.token, "POST", "/api/sensor-pulse",
             { sensor_id: "PS-WALKIN", temperature_fahrenheit: 51 });

  for (const [label, viewport] of [["phone", PHONE], ["desktop", DESKTOP]]) {
    const context = await browser.newContext({ viewport, serviceWorkers: "block" });
    const page = await context.newPage();
    const flush = watchErrors(page, `partner ${label}`);
    await page.goto(`${base}/partners`);
    await page.fill("#pk", partner.api_key);
    await page.click("#key-form button[type=submit]");
    await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
    await page.waitForTimeout(400);
    const text = await page.innerText("#app");
    if (!text.includes(company)) fail(`partner ${label}`, "the book does not show the account");
    if (!(await page.innerText("#attn-body")).includes(company)) {
      fail(`partner ${label}`, "the warm walk-in is not under needs attention");
    }
    const junk = text.match(JUNK);
    if (junk) fail(`partner ${label}`, `shows "${junk[0]}"`);
    await shoot(page, `partner ${label} book`);
    await page.click(`[data-open="${id}"]`);
    await page.waitForSelector("#detail-view:not([hidden])", { timeout: 5000 });
    const detail = await page.innerText("#detail-view");
    if (!detail.includes("PS-WALKIN")) fail(`partner ${label}`, "the account opens without its units");
    const wide = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    if (wide > 2) fail(`partner ${label}`, `page scrolls sideways by ${wide}px`);
    await shoot(page, `partner ${label} account`);
    flush();
    await context.close();
  }
  if (!failures.some((f) => f.startsWith("partner"))) {
    ok("partner: found, attached, and the portal shows the book and the account");
  }
}

async function main() {
  const server = await startServer();
  const browser = await chromium.launch();
  watchPolicy(browser);
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
    console.log("\nwho is on call");
    await checkRotation(browser, server.base, sceneToken);
    console.log("\none person, five businesses");
    await checkSwitcher(browser, server.base);
    console.log("\na reseller");
    await checkPartner(browser, server.base);
    console.log("\nevery page, under the policy");
    await checkPages(browser, server.base, sceneToken);
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
