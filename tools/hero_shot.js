/* The front page's phone picture (static/hero-today.webp): /today with an
   alert open, captured from a running server signed in to the demo
   business (demo_account.py), at a 320 px phone width, 1.5x, as a WebP.

     python demo_account.py --db /tmp/demo.db --password <password>
     CYBERLOGIX_SWEEP_SECONDS=0 CYBERLOGIX_DB_PATH=/tmp/demo.db python main.py
     BASE_URL=http://127.0.0.1:8080 DEMO_PASSWORD=<password> node tools/hero_shot.js

   With the sweep off the alert shows as it opened ("Nobody has answered
   yet"), not at the call step a server with calls switched off words as
   "calls are off on this server". Pretend data only: the demo's made-up business. Pilot's robot is left
   out of the picture. The page is labelled "Example" where it is shown
   (static/index.html), and tests/test_round11_insurance_pack_and_loss.py
   holds the file to its size (480 x 878, under 60 KB). */
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const BASE = (process.env.BASE_URL || "http://127.0.0.1:8080").replace(/\/$/, "");
const OUT = process.env.OUT || path.join(__dirname, "..", "static", "hero-today.webp");
const EMAIL = process.env.DEMO_EMAIL || "morgan@juniper-vine.example";
const PASSWORD = process.env.DEMO_PASSWORD || "";

(async () => {
  const login = await fetch(`${BASE}/api/accounts/login`, { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }) });
  if (!login.ok) throw new Error(`Could not sign in to the demo (${login.status}): pass DEMO_PASSWORD.`);
  const { token } = await login.json();
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 320, height: 585 }, deviceScaleFactor: 1.5, colorScheme: "dark" });
  await context.addInitScript((t) => { localStorage.setItem("cyberlogix.session", t); }, token);
  // Pilot is not loaded (the page's security policy allows no injected style).
  await context.route(/\/static\/pilot(\/|-kryozyn)/, (route) => route.abort());
  const page = await context.newPage();
  await page.goto(`${BASE}/today`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  const png = await page.screenshot({ type: "png" });
  // Chromium writes the WebP: the PNG drawn on a canvas and saved again.
  const blank = await context.newPage();
  const webp = await blank.evaluate(async (src) => {
    const img = new Image();
    img.src = src;
    await img.decode();
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    canvas.getContext("2d").drawImage(img, 0, 0);
    return canvas.toDataURL("image/webp", 0.8);
  }, `data:image/png;base64,${png.toString("base64")}`);
  const bytes = Buffer.from(webp.split(",")[1], "base64");
  fs.writeFileSync(OUT, bytes);
  console.log(`${OUT}: ${bytes.length.toLocaleString("en-US")} bytes`);
  await browser.close();
})().catch((err) => { console.error(err.message); process.exit(1); });
