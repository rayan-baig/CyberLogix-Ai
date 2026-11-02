#!/usr/bin/env node
/* Walk a new business from the landing page to its first sensor reading,
   the way a person would, and count what it took.

     PYTHON=.venv/bin/python node tools/first_day_walk.js [label]
     WIDE=1 ...        desktop size instead of a phone
     SHOTS=/tmp/walk   a screenshot of each step

   Starts its own server on a throwaway database. Prints one JSON object:
   total seconds, page loads, clicks and fields typed, and every step with
   the second it happened. Needs the same `playwright` as console_smoke.js.

   Steps counted: page loads a person waits through, clicks, and fields
   typed. "First reading" = a device sends one reading with the ingest key
   it was given, and the console shows the temperature on the card. */
"use strict";
const { chromium } = require("playwright");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const VIEW = process.env.WIDE ? { width: 1366, height: 900 } : { width: 390, height: 844 };
const SHOTS = process.env.SHOTS || "";

async function waitFor(url, s) {
  const until = Date.now() + s * 1000;
  while (Date.now() < until) {
    try { if ((await fetch(url)).ok) return; } catch (_) {}
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("server did not start");
}

async function main() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "clx-walk-"));
  const port = 21000 + Math.floor(Math.random() * 10000);
  const child = spawn(process.env.PYTHON || "python3",
    ["-m", "uvicorn", "main:app", "--port", String(port), "--log-level", "warning"],
    { cwd: ROOT, stdio: ["ignore", "inherit", "inherit"],
      env: Object.assign({}, process.env, {
        CYBERLOGIX_DB_PATH: path.join(dir, "w.db"), CYBERLOGIX_SWEEP_SECONDS: "0" }) });
  const base = `http://127.0.0.1:${port}`;
  await waitFor(`${base}/api/health`, 30);
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: VIEW, serviceWorkers: "block" });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const steps = [];
  const t0 = Date.now();
  const step = (kind, what) => {
    steps.push({ kind, what, at: ((Date.now() - t0) / 1000).toFixed(1) });
  };
  let shot = 0;
  const snap = async (name) => {
    if (!SHOTS) return;
    fs.mkdirSync(SHOTS, { recursive: true });
    await page.screenshot({ path: path.join(SHOTS, `${String(++shot).padStart(2, "0")}-${name}.png`), fullPage: false });
  };

  try {
    await page.goto(`${base}/`);
    step("load", "landing page");
    await snap("landing");
    await page.click("text=Start a free trial >> nth=0");
    await page.waitForSelector("#form");
    step("click", "Start a free trial");
    step("load", "sign-up page");
    await page.waitForFunction(() => document.querySelector("#industry_vertical").options.length > 1);
    const fields = [["#company_name", "Harbour Street Kitchen"], ["#full_name", "Sam Reyes"],
      ["#email", `sam-${Date.now()}@walk.example`], ["#contact_phone", "+15550142"],
      ["#password", "correct-horse-battery"]];
    for (const [sel, v] of fields) { await page.fill(sel, v); step("type", sel.slice(1)); }
    await page.selectOption("#industry_vertical", "restaurant"); step("type", "industry_vertical");
    await snap("signup-filled");
    await page.click("#go"); step("click", "Start the trial");

    // Wherever the sign-up leads, follow it to the console.
    await page.waitForFunction(() => location.pathname === "/console"
      || document.getElementById("open-console"), null, { timeout: 15000 });
    if (await page.$("#open-console")) {
      await snap("signup-done");
      step("read", "sign-up done panel (key + four curl commands)");
      await page.click("#open-console"); step("click", "Open the console");
    }
    await page.waitForSelector("#app:not([hidden])", { timeout: 15000 });
    step("load", "console");
    await page.waitForTimeout(600);
    await snap("console-first");

    // Find the way to add a unit: the checklist if there is one, else the
    // Setup tab where the form lives.
    if (await page.isVisible("#first-day [data-go='sensor']")) {
      await page.click("#first-day [data-go='sensor']"); step("click", "checklist: add a fridge");
    } else if (await page.isVisible("#fleet [data-go='sensor']")) {
      await page.click("#fleet [data-go='sensor']"); step("click", "empty fleet: add a fridge");
    } else {
      await page.click('.f-tab[data-tab="setup"]'); step("click", "Setup tab (to find the form)");
    }
    await page.waitForTimeout(300);
    const idRequired = await page.$eval("#add-id", (el) => el.required).catch(() => false);
    if (idRequired) { await page.fill("#add-id", "WALKIN-1"); step("type", "add-id"); }
    await page.fill("#add-loc", "Kitchen walk-in"); step("type", "add-loc");
    await snap("add-filled");
    await page.click("#add-form button[type=submit]"); step("click", "Claim a seat");
    await page.waitForFunction(() => {
      const v = document.getElementById("issued-value");
      return v && /^clx_snr_/.test(v.textContent) && document.getElementById("issued-for").textContent !== "this business (master key)";
    }, null, { timeout: 10000 });
    const ingest = await page.textContent("#issued-value");
    const sensorId = await page.textContent("#issued-for");
    step("read", `ingest key shown for ${sensorId}`);
    await snap("key-shown");

    // The device: one reading with its own key, the moment it is set up.
    const tSend = Date.now();
    const res = await fetch(`${base}/api/sensor-pulse`, { method: "POST",
      headers: { "Content-Type": "application/json", "X-CyberLogix-Sensor-Key": ingest },
      body: JSON.stringify({ sensor_id: sensorId, temperature_fahrenheit: 2.4 }) });
    if (!res.ok) throw new Error("pulse " + res.status + " " + await res.text());
    step("device", "first reading sent");
    await page.waitForFunction((id) => {
      const card = [...document.querySelectorAll("#fleet .sensor")]
        .find((c) => c.querySelector(`[data-detail="${id}"]`));
      return card && /2\.4/.test(card.querySelector(".sensor-temp").textContent);
    }, sensorId, { timeout: 60000, polling: 200 });
    const lag = ((Date.now() - tSend) / 1000).toFixed(1);
    step("seen", `reading on the console card (${lag}s after it was sent)`);
    await snap("reading-seen");
  } finally {
    const total = ((Date.now() - t0) / 1000).toFixed(1);
    const count = (k) => steps.filter((s) => s.kind === k).length;
    console.log(JSON.stringify({ label: process.argv[2] || "", viewport: VIEW, total_s: total,
      page_loads: count("load"), clicks: count("click"), fields: count("type"),
      person_steps: count("click") + count("type"), errors, steps }, null, 1));
    await browser.close();
    child.kill();
    fs.rmSync(dir, { recursive: true, force: true });
  }
}
main().catch((e) => { console.error(e); process.exit(1); });
