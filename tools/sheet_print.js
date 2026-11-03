#!/usr/bin/env node
/* Print every industry's one-page sales sheet, on A4 and on Letter.
 *
 *   node tools/sheet_print.js                 # starts its own server
 *   BASE_URL=http://127.0.0.1:8080 node tools/sheet_print.js
 *   SHEET_PDFS=/tmp/sheets node tools/sheet_print.js   # keep the PDFs
 *
 * Needs Node 18+ and the `playwright` package with Chromium, like
 * tools/console_smoke.js. Without BASE_URL it starts uvicorn on a
 * throwaway database with the sweep loop off, using $PYTHON (default:
 * python3) from the repository root.
 *
 * For each industry it prints /for/<trade>/sheet to PDF the way a
 * browser's Print does, at both paper sizes, and fails if any sheet runs
 * onto a second page, if anything on it is refused by the content
 * security policy, or if a value is missing (undefined, NaN, null).
 * Look at the PDFs too: a page count cannot tell you the sheet is
 * readable.
 *
 * Exit code 0 when every sheet is one page on both; 1 otherwise.
 */
"use strict";

const { chromium } = require("playwright");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const PAPERS = ["A4", "Letter"];
const JUNK = /\bundefined\b|\bNaN\b|\bnull\b|\[object Object\]/;

const failures = [];
function fail(what) { failures.push(what); console.log(`  FAIL ${what}`); }
function ok(what) { console.log(`  ok   ${what}`); }

async function waitFor(url, seconds) {
  const until = Date.now() + seconds * 1000;
  while (Date.now() < until) {
    try { if ((await fetch(url)).ok) return; } catch (_) { /* not up yet */ }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`${url} did not answer within ${seconds}s`);
}

async function startServer() {
  if (process.env.BASE_URL) return { base: process.env.BASE_URL, stop() {} };
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "clx-sheet-"));
  const port = 20000 + Math.floor(Math.random() * 20000);
  const child = spawn(
    process.env.PYTHON || "python3",
    ["-m", "uvicorn", "main:app", "--port", String(port), "--log-level", "warning"],
    {
      cwd: ROOT,
      env: Object.assign({}, process.env, {
        CYBERLOGIX_DB_PATH: path.join(dir, "sheet.db"),
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

/* Pages in a PDF as Chromium writes it: one "/Type /Page" object each. */
function pageCount(pdf) {
  const text = pdf.toString("latin1");
  return (text.match(/\/Type\s*\/Page(?![s\w])/g) || []).length;
}

(async () => {
  const server = await startServer();
  const out = process.env.SHEET_PDFS || "";
  if (out) fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch();
  try {
    const trades = (await (await fetch(`${server.base}/api/for`)).json())
      .industries.map((row) => row.vertical);
    if (!trades.length) fail("no industries listed at /api/for");
    const context = await browser.newContext({ serviceWorkers: "block" });
    for (const trade of trades) {
      const page = await context.newPage();
      const seen = [];
      page.on("pageerror", (err) => seen.push(`JS error: ${err.message}`));
      page.on("console", (msg) => {
        if (msg.type() === "error" && !/Failed to load resource|net::ERR_/.test(msg.text())) {
          seen.push(`console error: ${msg.text()}`);
        }
      });
      const route = `/for/${trade}/sheet`;
      const resp = await page.goto(`${server.base}${route}`, { waitUntil: "load" });
      if (!resp || resp.status() !== 200) {
        fail(`${route} answered ${resp && resp.status()}`);
        await page.close();
        continue;
      }
      const text = await page.locator(".sheet").innerText();
      const junk = text.match(JUNK);
      if (junk) fail(`${route} shows "${junk[0]}"`);
      for (const paper of PAPERS) {
        const pdf = await page.pdf({ format: paper, printBackground: true });
        const pages = pageCount(pdf);
        if (pages !== 1) fail(`${route} prints on ${pages} pages of ${paper}`);
        if (out) fs.writeFileSync(path.join(out, `${trade}-${paper}.pdf`), pdf);
      }
      seen.forEach((line) => fail(`${route}: ${line}`));
      await page.close();
    }
    ok(`${trades.length} sheets checked on ${PAPERS.join(" and ")}`);
  } finally {
    await browser.close();
    server.stop();
  }
  if (failures.length) {
    console.log(`\n${failures.length} failure(s).`);
    process.exit(1);
  }
  console.log("\nEvery sheet prints on one page.");
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
