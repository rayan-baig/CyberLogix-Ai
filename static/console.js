"use strict";

const TOKEN_STORAGE = "cyberlogix.session";
// The sign-up page leaves the new business key here for exactly one load.
const NEW_KEY_STORAGE = "cyberlogix.newkey";
// A longer interval than a live dashboard strictly wants, because every poll
// is a request that keeps a Cloud Run instance warm and billing.
const POLL_MS = 20000;

const state = {
  token: null, user: null, data: null,
  timer: null, tableView: false, industries: [],
  // Fleet view controls. Held here rather than read from the DOM on every
  // render, so a poll landing mid-search does not reset what was typed.
  search: "", filter: "all", site: "all", groupBySite: false,
  unit: "F", drawerSensor: null, wall: false,
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* A bar whose length is a live figure (battery, signal, spend against
   savings) carries it as data-w="42" rather than style="width:42%": the
   Content-Security-Policy refuses style attributes in markup, including
   markup built with innerHTML, but a width set through element.style is
   the page's own script and is allowed. Every render that adds bars goes
   through innerHTML, so one observer sizes them all as they arrive. */
function sizeBars(root) {
  if (!root.querySelectorAll) return;
  const bars = root.matches && root.matches("[data-w]") ? [root] : [];
  bars.push(...root.querySelectorAll("[data-w]"));
  for (const bar of bars) {
    const pct = Math.max(0, Math.min(100, Number(bar.dataset.w) || 0));
    bar.style.width = `${pct}%`;
  }
}
new MutationObserver((changes) => {
  for (const change of changes) change.addedNodes.forEach(sizeBars);
}).observe(document.documentElement, { childList: true, subtree: true });

const TERMINAL_STORAGE = "cyberlogix.terminal";

function readTerminalPreference() {
  try { return localStorage.getItem(TERMINAL_STORAGE) === "on"; }
  catch (_) { return false; }
}

function setTerminal(on, options = {}) {
  $("terminal").hidden = !on;
  document.body.classList.toggle("has-terminal", on);
  const btn = $("term-toggle");
  if (btn) {
    btn.setAttribute("aria-pressed", String(on));
    btn.textContent = on ? "Hide log" : "Activity log";
  }
  if (options.remember === false) return;
  try { localStorage.setItem(TERMINAL_STORAGE, on ? "on" : "off"); }
  catch (_) {}
}

function logToConsole(message, kind = "info") {
  const body = $("term-body");
  if (!body) return;
  const line = document.createElement("div");
  const stamp = new Date().toLocaleTimeString([], { hour12: false });
  line.innerHTML = `<span class="t">${esc(stamp)}</span>`
                 + `<span class="${esc(kind)}">&gt; ${esc(message)}</span>`;
  body.appendChild(line);
  // Keep the log bounded; an ops console can run for days.
  while (body.childElementCount > 200) body.removeChild(body.firstChild);
  body.scrollTop = body.scrollHeight;
}

/* A toast for the moment, a terminal line for the record. */
function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = message;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 5200);
  logToConsole(message, kind === "err" ? "err" : kind === "ok" ? "ok" : "info");
}

/* --- the terms, and whether this account is still on them ------------
   An acceptance pins the SHA-256 of each document. Changing a document
   does not void the old agreement -- it means the customer is still on
   the old one, which is worth knowing before anybody relies on a clause
   that was added afterwards. Only an owner can accept, because a record
   attested by "API key" is not attested by anybody. */
async function checkTerms() {
  const banner = $("terms-banner");
  const status = await api("/api/legal/acceptance/status");
  if (status.all_current) { banner.hidden = true; return; }

  const never = status.documents.filter((d) => !d.accepted);
  const stale = status.documents.filter((d) => d.accepted && !d.current);
  const owner = state.user && state.user.role === "owner";

  $("terms-text").textContent = never.length
    ? ("This account has not accepted "
       + never.map((d) => d.title).join(", ") + "."
       + (owner ? "" : " An owner needs to."))
    : ("The " + stale.map((d) => d.title).join(", ")
       + " changed since this account agreed to "
       + (stale.length === 1 ? "it" : "them")
       + ". You are still on the version you accepted."
       + (owner ? "" : " An owner needs to accept the new one."));
  $("terms-accept").hidden = !owner;
  banner.hidden = false;
}

async function acceptTerms() {
  const button = $("terms-accept");
  const status = await api("/api/legal/acceptance/status");
  const outstanding = status.documents
    .filter((d) => !d.current)
    .map((d) => d.slug);
  const titles = status.documents
    .filter((d) => !d.current)
    .map((d) => d.title)
    .join(", ");
  // Opened, not just agreed to. A one-click accept on text nobody was
  // shown is the kind of record that is worth less the moment it is
  // questioned, so the documents are put in front of the person first.
  window.open("/legal", "_blank", "noopener");
  if (!window.confirm(
        "Accept " + titles + " as they read today?\n\n"
        + "They have opened in a new tab. This records your name and the "
        + "exact text, by hash, against this account.")) {
    return;
  }
  button.disabled = true;
  try {
    await api("/api/legal/accept", {
      method: "POST",
      body: JSON.stringify({
        documents: outstanding,
        accepted_by: state.user.full_name,
        title: state.user.role,
      }),
    });
    await checkTerms();
    toast("Acceptance recorded against this account.");
  } catch (err) {
    toast(err.message || "That could not be recorded.");
  } finally {
    button.disabled = false;
  }
}

/* What went wrong, in a sentence.
 *
 * A rejected form used to surface as the raw validation payload:
 *   [{"type":"value_error","loc":["body","url"],"msg":"Value error, ...
 * which contains the real reason and buries it. Every form in this
 * console shared that, so this is fixed here rather than in each one.
 */
function readableError(body, status) {
  const detail = body && body.detail;
  if (typeof detail === "string" && detail) return detail;

  // FastAPI's validation shape: a list of {loc, msg}. The message is
  // the useful part, and "Value error, " in front of it is noise from
  // the framework rather than anything a reader needs.
  if (Array.isArray(detail) && detail.length) {
    const lines = detail
      .map((row) => {
        const where = Array.isArray(row.loc)
          ? row.loc.filter((p) => p !== "body").join(" ") : "";
        const why = String(row.msg || "").replace(/^Value error, /, "");
        return where && !why.toLowerCase().includes(where.toLowerCase())
          ? `${where}: ${why}` : why;
      })
      .filter(Boolean);
    if (lines.length) return lines.join(" ");
  }

  // Some endpoints answer with an object carrying its own explanation.
  if (detail && typeof detail === "object") {
    if (typeof detail.message === "string") return detail.message;
    return JSON.stringify(detail);
  }
  return `Request failed (${status})`;
}

async function api(path, options = {}) {
  const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;

  let res;
  try {
    res = await fetch(path, Object.assign({}, options, { headers }));
  } catch (netErr) {
    // fetch rejects with a bare TypeError("Failed to fetch") when there
    // is no network, which then gets shown to somebody standing next to
    // a freezer. Say what actually happened.
    const offline = new Error(
      "No connection to CyberLogix. Nothing shown here is current."
    );
    offline.offline = true;
    showConnection(false, "this device is offline");
    throw offline;
  }
  const text = await res.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch (_) {
    // Not ours: a proxy or load balancer answering for a server that is
    // down or restarting sends a page of HTML, and that page went into
    // a toast word for word. Short plain text is kept; markup is not.
    body = { detail: /</.test(text) || text.length > 200
      ? (res.status >= 500
          ? `The service did not answer properly (${res.status}). Try again in a minute.`
          : `Request failed (${res.status}).`)
      : text };
  }

  if (!res.ok) {
    const err = new Error(readableError(body, res.status));
    err.status = res.status;
    throw err;
  }
  return body;
}

/* ---------------- gate ---------------- */

let onboarding = false;

$("switch-btn").addEventListener("click", () => {
  onboarding = !onboarding;
  $("signin-form").hidden = onboarding;
  $("onboard-form").hidden = !onboarding;
  $("switch-text").textContent = onboarding ? "Already have a key?" : "No account yet?";
  $("switch-btn").textContent = onboarding ? "Sign in" : "Onboard a company";
});

$("signin-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const btn = event.target.querySelector("button");
  btn.disabled = true;
  try {
    const body = await api("/api/accounts/login", {
      method: "POST",
      body: JSON.stringify({
        email: $("si-email").value.trim(),
        password: $("si-password").value,
      }),
    });
    rememberEmail($("si-email").value.trim());
    await enter(body.token, body.user);
  } catch (err) {
    toast(err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

$("onboard-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const btn = event.target.querySelector("button");
  btn.disabled = true;
  try {
    const email = $("ob-email").value.trim();
    const password = $("ob-password").value;

    // Through the same front door as /signup, not around it.
    //
    // This used to be three calls -- create a tenant at
    // /api/licenses/tenants, bootstrap its owner, sign in -- with a plan
    // picker defaulting to Growth. Creating anything but a trial there
    // needs the operator's provisioning key, which a browser never has, so
    // the default choice was a guaranteed 403 and only "Trial" ever
    // worked. Even that went round signup: no rate limit, no welcome, no
    // nurture ladder, and no record that the terms were accepted.
    const made = await api("/api/signup", {
      method: "POST",
      body: JSON.stringify({
        company_name: $("ob-company").value.trim(),
        full_name: $("ob-name").value.trim(),
        contact_phone: $("ob-phone").value.trim(),
        email,
        password,
      }),
    });

    await enter(made.token, made.user);
    // The business key is shown once. It used to go to the browser's
    // developer console, with a toast telling the owner to keep a key
    // they had no way to see -- and a key in the console log stays in
    // the log. It goes where every other new key goes.
    showIssuedKey("this business (master key)", made.api_key);
    toast("Trial started. Copy your business key from \"Add a fridge or "
          + "freezer\" now: it is shown once.", "ok");
  } catch (err) {
    toast(err.message, "err");
  } finally {
    btn.disabled = false;
  }
});

async function enter(token, user) {
  if (!token) return;
  state.token = token;
  state.user = user || null;
  try {
    await refresh();
  } catch (err) {
    state.token = null;
    state.user = null;
    toast(err.message, "err");
    return;
  }
  try { localStorage.setItem(TOKEN_STORAGE, token); } catch (_) {}
  $("gate").hidden = true;
  $("app").hidden = false;
  // Off unless this browser asked for it. A scrolling log reads as a demo,
  // not as the console of a service that bills five figures a month.
  setTerminal(readTerminalPreference());
  logToConsole("Session opened.", "info");
  await loadIndustries();
  markFresh();
  startPolling();
  loadSims();
  watchForFirstReading();
  // The business key from the sign-up page, handed over for this one
  // page load and deleted as it is read: shown once, here, where every
  // other new key is shown.
  let handed = null;
  try {
    handed = sessionStorage.getItem(NEW_KEY_STORAGE);
    sessionStorage.removeItem(NEW_KEY_STORAGE);
  } catch (_) { /* storage refused: there is nothing to show */ }
  if (handed) {
    showIssuedKey("this business (master key)", handed, { scroll: false });
    toast("Your business key is shown once, under \"Add a fridge or "
          + "freezer\". Copy it if you will use the API.", "ok");
  }
  followInspectorLink().catch(() => { /* the estate is the page */ });
}

/* Tearing down the session locally, without calling the API — used when
   the token is already dead and a logout call would only 401 again. */
function signOutLocally() {
  try { localStorage.removeItem(TOKEN_STORAGE); } catch (_) {}
  clearTimeout(state.timer);
  clearInterval(state.firstWatch);
  clearInterval(state.simTimer);
  state.simTimer = null;
  state.pollChain = (state.pollChain || 0) + 1;
  state.token = null;
  state.user = null;
  state.data = null;
  state.benchmarkLoaded = false;
  state.attestation = null;
  closeDrawer();
  $("app").hidden = true;
  $("stale-banner").hidden = true;
  setTerminal(false, { remember: false });
  $("gate").hidden = false;
  $("si-password").value = "";
  $("si-password").type = "password";
  $("si-see").textContent = "Show";
  $("si-see").setAttribute("aria-pressed", "false");
  primeTheGate();
}

/* Download or delete everything. The server checks the role, the typed
   name and the password; this only shows the buttons to an owner. */
function renderLeave() {
  const owner = Boolean(state.user && state.user.role === "owner");
  $("leave-owner").hidden = !owner;
  $("leave-not-owner").hidden = owner;
}

$("leave-download").addEventListener("click", async () => {
  const button = $("leave-download");
  button.disabled = true;
  try {
    const body = await api("/api/accounts/business/export");
    const blob = new Blob([JSON.stringify(body, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `cyberlogix-${body.tenant_id}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 10000);
  } catch (err) {
    toast(err.message, "err");
  } finally {
    button.disabled = false;
  }
});

$("leave-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await api("/api/accounts/business/erase", {
      method: "POST",
      body: JSON.stringify({
        company_name: $("leave-name").value.trim(),
        password: $("leave-password").value,
      }),
    });
    $("leave-password").value = "";
    signOutLocally();
    toast("Deleted. Everything for that business is gone, except the bills we sent.");
  } catch (err) {
    $("leave-password").value = "";
    toast(err.message, "err");
  } finally {
    button.disabled = false;
  }
});

$("sign-out").addEventListener("click", async () => {
  try { await api("/api/accounts/logout", { method: "POST" }); } catch (_) {}
  signOutLocally();
});

/* A dashboard that quietly stops updating is worse than one that says it
   has. The banner stays up until a poll succeeds, and a session that has
   expired sends the operator back to the gate rather than showing them a
   frozen estate. */
function markFresh() {
  state.lastGoodAt = Date.now();
  state.pollFailures = 0;
  $("stale-banner").hidden = true;
}

function markStale(err) {
  state.pollFailures = (state.pollFailures || 0) + 1;
  if (err && err.status === 401) {
    toast("Your session has expired. Sign in again.", "err");
    signOutLocally();
    return;
  }
  // One dropped poll is a blip; two is worth saying out loud.
  if (state.pollFailures < 2) return;
  const since = state.lastGoodAt
    ? Math.round((Date.now() - state.lastGoodAt) / 1000)
    : null;
  const banner = $("stale-banner");
  banner.hidden = false;
  banner.textContent =
    `Not reaching the hub${since ? ` for ${since} seconds` : ""}. `
    + "What is on screen is the last good reading, not the current one.";
}

/* A server that is down is polled less and less often, not every twenty
   seconds by every open console at once: twenty, forty, eighty seconds
   and so on up to five minutes, back to twenty the moment one answers.
   A chain of timeouts rather than an interval, so one slow answer never
   has a second request queued up behind it. */
const POLL_MAX_MS = 5 * 60 * 1000;

function nextPollDelay() {
  const misses = state.pollFailures || 0;
  return misses ? Math.min(POLL_MS * 2 ** misses, POLL_MAX_MS) : POLL_MS;
}

function startPolling() {
  clearTimeout(state.timer);
  // A new chain retires the old one, even if the old one has a request
  // in flight: switching business must not leave two chains polling.
  const chain = (state.pollChain = (state.pollChain || 0) + 1);
  const tick = () => {
    refresh().then(markFresh).catch(markStale).finally(() => {
      if (chain !== state.pollChain || !state.token) return;
      state.timer = setTimeout(tick, nextPollDelay());
    });
  };
  state.timer = setTimeout(tick, POLL_MS);
}

$("pricing-btn").addEventListener("click", async () => {
  const card = $("pricing-card");
  if (!card.hidden) { card.hidden = true; return; }

  try {
    const book = await api("/api/billing/pricing");
    $("pricing-rows").innerHTML = book.pricing.map((row) => `
      <div class="u-d-flex u-gap-14 u-items-baseline u-p-10-0 u-bb">
        <span class="grow">
          <span class="u-fs-13-5 u-fw-650">${esc(row.industry)}</span>
          <span class="u-d-block u-fs-12 u-c-muted u-mt-2 u-maxw-52ch">${esc(row.pitch)}</span>
        </span>
        <span class="u-ws-nowrap u-fs-13-5 u-fw-650 u-tnum">
          $${row.monthly_usd.toLocaleString()}
          <span class="u-c-muted u-fw-400 u-fs-12">
            / ${esc(row.unit)} / mo</span>
        </span>
      </div>`).join("");
    card.hidden = false;
  } catch (err) {
    toast(err.message, "err");
  }
});

/* ---------------- data ---------------- */

async function loadIndustries() {
  const body = await api("/api/industries");
  state.industries = body.industries;
  // The knocking card may already be on screen with an empty dropdown.
  fillKnockIndustries();
  $("add-vertical").innerHTML = body.industries
    .map((i) => `<option value="${esc(i.vertical)}">`
      + `${esc(i.name)} — $${i.monthly_usd} / ${esc(i.billing_unit)}</option>`)
    .join("");
  const plain = body.industries
    .map((i) => `<option value="${esc(i.vertical)}">${esc(i.name)}</option>`)
    .join("");
  $("tx-vertical").innerHTML = plain;
  $("calc-vertical").innerHTML = plain;
  // The overview may already have landed; if it has, this is the call
  // that actually sets the sector.
  preferSector(state.data);
  calculateBilling();
}

$("add-vertical").addEventListener("change", (event) => {
  showSectorExample(event.target.value);
});

/* Open every sector picker on the sector this customer actually runs.
 *
 * Called from both the industry load and the overview load, because it
 * needs both and they race. Measured: the first overview lands before
 * the industry list, so a version wired only into refresh() saw zero
 * industries, matched nothing, and left the picker exactly where it
 * started. Whichever finishes last does the work, and the flag means it
 * happens once however the race falls out.
 *
 * They tell us at sign-up. The audit trail records it. And then the
 * first form they saw defaulted to whatever happened to be first in the
 * list — for a restaurant that signed up ten seconds earlier, "CyberTech
 * Data Centers".
 *
 * That is not a cosmetic default. A walk-in freezer registered under the
 * data-centre sector is monitored against a 78F limit instead of 32F, so
 * it alarms long after the food is gone. The one number that has to be
 * right on this form was being chosen by list order.
 *
 * Applied once per session, never on a poll: refresh() runs on a timer,
 * and re-selecting the dropdown every few seconds would yank it back
 * from somebody halfway through registering a unit in a second sector. */
/* The example unit in the form follows the sector it is registering.
 *
 * A cryostorage customer should not be shown a restaurant's walk-in as
 * the thing to type, and once somebody changes the picker by hand the
 * form has to stay coherent with what they chose. Placeholders only, so
 * this can never overwrite something already typed. */
function showSectorExample(vertical) {
  const profile = (state.industries || []).find((i) => i.vertical === vertical);
  if (!profile || !profile.asset_noun) return;
  const noun = profile.asset_noun;
  const idField = $("add-id");
  const locField = $("add-loc");
  if (idField) {
    idField.placeholder =
      `${noun.toUpperCase().replace(/[^A-Z0-9]+/g, "-")}-01`;
  }
  if (locField) locField.placeholder = `Main site / ${noun}`;
}


function preferSector(overview) {
  if (state.sectorChosen) return;
  // Nothing to choose from yet: the other caller will come back.
  if (!state.industries || !state.industries.length) return;
  if (!overview) return;

  const stated = overview && overview.tenant
    && overview.tenant.industry_vertical;
  // Then whatever they have actually registered most of, so an estate
  // that grew past what it said at sign-up still opens on itself.
  const counts = {};
  (overview && overview.sensors ? overview.sensors : []).forEach((s) => {
    counts[s.industry_vertical] = (counts[s.industry_vertical] || 0) + 1;
  });
  const dominant = Object.entries(counts)
    .sort((a, b) => b[1] - a[1]).map(([key]) => key)[0];

  const known = new Set((state.industries || []).map((i) => i.vertical));
  const wanted = [stated, dominant].find((v) => v && known.has(v));
  if (!wanted) return;

  ["add-vertical", "tx-vertical", "calc-vertical"].forEach((id) => {
    const select = $(id);
    if (select) select.value = wanted;
  });

  showSectorExample(wanted);
  state.sectorChosen = true;
  calculateBilling();
}

/* Panels that are allowed to be missing, and what to say in their place.
 *
 * An account whose licence has lapsed gets a 402 from five of the calls
 * this page makes — costs, roster, webhooks, pipeline — and
 * with a single Promise.all one 402 threw away the other eight. The
 * console rendered nothing at all for the one customer who most needed to
 * read it, because the thing it would have told them is how to fix
 * exactly that.
 *
 * So the overview is the only call that has to succeed. Everything else
 * is a panel, and a panel that cannot load says so where it would have
 * been rather than taking the page down with it. */
const OPTIONAL_PANELS = {
  costs: "cost-body", roster: "roster-body", team: "team-body",
  rotations: "rotation-body",
  hooks: "hook-body", invoices: "invoice-body",
  audit: "audit-body",
  forecast: "forecast-body",
  claims: "claims-body",
  escalation: "escalation-body",
  byodSeen: "byod-samples",
  knocking: "knock-body",
  pollers: "poller-body",
  tasks: "task-body",
  peopleAlerts: "licence-alerts",
  peopleCal: "people-cal",
  staff: "staff-list",
  leavers: "leavers",
};

function panelUnavailable(key, err) {
  const id = OPTIONAL_PANELS[key];
  const el = id && $(id);
  if (!el) return;
  const message = String((err && err.message) || "");
  const lapsed = /402/.test(message);
  // A plan that does not include something is not a failure, and saying
  // "Could not be loaded" about it reads as a bug in the product rather
  // than a line on the price list. The server already writes the right
  // sentence; this just stops the wrapper contradicting it.
  const notIncluded = (err && err.status === 403) || /does not include/.test(message);
  if (notIncluded) {
    el.innerHTML = `<div class="empty u-p-16-0">Not on this plan.
      <span class="sub">${esc(message.slice(0, 160))}</span></div>`;
    return;
  }
  el.innerHTML = `<div class="empty u-p-16-0">${
    lapsed ? "Withheld while the licence is unpaid."
           : "Could not be loaded."}
    <span class="sub">${esc(lapsed
      ? "Monitoring and alerting are unaffected."
      : message.slice(0, 120))}</span></div>`;
}

async function refresh() {
  // The one call the page cannot do without. If this fails the session is
  // genuinely broken and the caller's error handling should see it.
  let overview, health;
  try {
    [overview, health] = await Promise.all([
      api("/api/console/overview"),
      api("/api/health"),
    ]);
  } catch (err) {
    // A failed refresh leaves whatever was already painted on screen,
    // which on this page is a set of temperatures. Say so before
    // rethrowing, or the app quietly shows yesterday's estate.
    showConnection(false, err.status ? "HTTP " + err.status : "no answer");
    throw err;
  }
  showConnection(true);

  const wanted = {
    costs: () => api("/api/costs?days=30"),
    audit: () => api("/api/accounts/audit?limit=8"),
    me: () => (state.user ? Promise.resolve({ user: state.user })
                          : api("/api/accounts/me")),
    roster: () => api("/api/contacts"),
    rotations: () => api("/api/contacts/rotations"),
    team: () => api("/api/accounts/users"),
    businesses: () => api("/api/accounts/businesses"),
    catalogue: () => (state.shortcutCatalogue
      ? Promise.resolve(state.shortcutCatalogue) : api("/api/shortcuts")),
    hooks: () => api("/api/webhooks"),
    invoices: () => api("/api/invoices"),
    contract: () => api("/api/contracts"),
    pipeline: () => api("/api/contracts/pipeline"),
    forecast: () => api("/api/forecast/fleet"),
    claims: () => api("/api/claims/eligible"),
    escalation: () => api("/api/contacts/preview"),
    byodSeen: () => api("/api/v1/bridge/samples"),
    knocking: () => api("/api/doorstep"),
    peopleAlerts: () => api("/api/people/alerts"),
    pollers: () => api("/api/pollers"),
    tasks: () => api("/api/tasks"),
    staff: () => api("/api/people"),
    peopleCal: () => api("/api/people/calendar?days=180"),
    leavers: () => api("/api/people/departures"),
  };
  const keys = Object.keys(wanted);
  const settled = await Promise.allSettled(keys.map((k) => wanted[k]()));
  const got = {};
  const failed = {};
  keys.forEach((k, i) => {
    if (settled[i].status === "fulfilled") got[k] = settled[i].value;
    else failed[k] = settled[i].reason;
  });

  state.shortcutCatalogue = got.catalogue || state.shortcutCatalogue;
  state.data = overview;
  preferSector(overview);
  if (got.me) state.user = got.me.user;
  state.unit = overview.temperature_unit || "F";

  render(overview, health);
  // Never a broken panel: without the list the chip is just the name.
  try { paintBusinessChip(got.businesses || null); } catch (_) { /* the estate is the page */ }
  renderSites(overview);
  renderVault();
  loadBenchmarks(overview);
  if (state.drawerSensor) openDrawer(state.drawerSensor, { quiet: true });
  renderBilling(overview.subscription, overview.roi, overview.tenant);

  const paint = (key, fn) => {
    if (got[key] === undefined) { panelUnavailable(key, failed[key]); return; }
    try { fn(); } catch (err) { panelUnavailable(key, err); }
  };
  paint("hooks", () => renderHooks(got.hooks));
  paint("invoices", () => renderInvoices(got.invoices));
  paint("costs", () => renderCosts(got.costs));
  paint("audit", () => renderAudit(got.audit));
  renderLeave();
  paint("roster", () => renderRoster(got.roster));
  paint("rotations", () => renderRotations(got.rotations, got.roster));
  paint("forecast", () => renderForecast(got.forecast));
  paint("claims", () => renderClaims(got.claims));
  paint("escalation", () => renderEscalation(got.escalation));
  paint("byodSeen", () => renderByodSamples(got.byodSeen));
  paint("knocking", () => renderKnocking(got.knocking));
  paint("pollers", () => renderPollers(got.pollers));
  paint("tasks", () => renderTasks(got.tasks));
  paint("peopleAlerts", () => {
    renderLicenceAlerts(got.peopleAlerts);
    renderLeaverAlerts(got.peopleAlerts);
  });
  paint("peopleCal", () => renderPeople(got.peopleCal));
  paint("staff", () => renderStaff(got.staff));
  paint("leavers", () => renderLeavers(got.leavers));
  paint("team", () => renderTeam(got.team));

  // The contract card is where the way back lives, so it renders from
  // whatever arrived rather than needing both halves.
  if (got.contract) {
    renderContract(got.contract, got.pipeline || { opportunities: [] },
                   overview && overview.tenant);
  }
  if (state.shortcutCatalogue) {
    renderShortcuts(overview.sensors, state.shortcutCatalogue);
  }
}

/* ---------------- render ---------------- */

const RISK_CLASS = {
  critical: "s-critical", high: "s-serious", elevated: "s-warning",
  low: "s-good", stable: "s-good", unknown: "s-muted",
};
const RISK_ICON = {
  critical: "▲", high: "▲", elevated: "△",
  low: "●", stable: "●", unknown: "—",
};

function badge(kind, label) {
  return `<span class="badge ${RISK_CLASS[kind] || "s-muted"}">`
       + `<span class="dot" aria-hidden="true"></span>`
       + `<span aria-hidden="true">${RISK_ICON[kind] || ""}</span>${esc(label)}</span>`;
}

function render(d, health) {
  const s = d.summary;
  const t = d.tenant;

  $("sys-status").textContent = health.status === "online" ? "online" : health.status;
  $("about-version").textContent = health.version ? `Version ${health.version}` : "Version —";
  $("about-plan").textContent = t.plan_name ? `${t.plan_name} plan` : "—";
  const support = d.support_email || "";
  $("about-support").hidden = !support;
  $("about-support-link").textContent = support || "—";
  $("about-support-link").href = support ? `mailto:${support}` : "mailto:";
  $("chip-user").textContent = state.user
    ? `${state.user.full_name} · ${state.user.role}`
    : "—";

  // Say up front what this account can do. The console shows every
  // control to everybody and lets the server refuse, which is the right
  // place for the decision but the wrong place to learn about it — a
  // viewer should not find out that they cannot acknowledge an alert by
  // pressing the button during one.
  const roleBanner = $("role-banner");
  if (state.user && state.user.role === "viewer") {
    roleBanner.textContent =
      "Signed in as a viewer. You can see everything here and change " +
      "nothing — registering sensors, changing alarm limits and the " +
      "licence all need an operator or owner.";
    roleBanner.hidden = false;
  } else if (state.user && state.user.must_change_password) {
    // Whoever invited them chose this password, so they know it.
    roleBanner.textContent =
      "Your password was set by whoever added you. Change it under Team " +
      "below so only you know it.";
    roleBanner.hidden = false;
  } else {
    roleBanner.hidden = true;
  }
  // Whether this account is on the text that is live today. Loaded on
  // its own and allowed to fail: the console is how somebody watches a
  // freezer, and a legal check that 500s must not take that away.
  checkTerms().catch(() => { /* the estate is the page */ });

  $("chip-company").textContent = `${t.company_name} ▾`;
  $("chip-plan").textContent = t.plan_name + " plan";
  $("chip-seats").textContent = `${t.seats_used}/${t.seats_total} units`;
  $("chip-spend").textContent = d.subscription
    ? `$${d.subscription.monthly_total_usd.toLocaleString()}/mo`
    : "—";
  $("chip-delivery").textContent =
    health.message_delivery === "twilio" ? "SMS + voice live" : "Delivery: dry run";
  $("unit-toggle").textContent = `°${state.unit}`;
  $("unit-toggle").title = state.unit === "C"
    ? "Showing Celsius. Click for Fahrenheit."
    : "Showing Fahrenheit. Click for Celsius.";

  /* The licence. Loudest thing on the page when it needs to be, and
     silent when it does not — a banner that is always on stops being
     read well before the day it matters. */
  const account = $("account-banner");
  const a = d.account;
  if (a && a.headline) {
    account.className = "banner " + (a.severity === "critical" ? "critical" : "warn");
    account.innerHTML = esc(a.headline)
      + (a.alerting_continues
          ? ` <span class="u-o-75">Alerting is still running.</span>` : "")
      + (a.pay_url
          ? ` <a class="banner-act" id="account-pay" href="${esc(a.pay_url)}"
               target="_blank" rel="noopener">Pay now</a>` : "")
      + ` <button type="button" class="banner-act" id="account-act">${
          a.on_trial ? "See what it costs" : "Sort it out"}</button>`;
    account.hidden = false;
    $("account-act").addEventListener("click", () => {
      $("contract-body").scrollIntoView({ behavior: "smooth", block: "center" });
    });
  } else {
    account.hidden = true;
  }

  const banner = $("delivery-banner");
  if (health.message_delivery !== "twilio") {
    banner.hidden = false;
    banner.textContent =
      "Twilio is not configured, so alerts are composed and logged but not actually "
      + "sent. Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER to go live.";
  } else {
    banner.hidden = true;
  }

  $("tiles").innerHTML = [
    tile(`${Noun(2)} online`, `${s.sensors_online}`,
         `of ${s.sensors_total} under watch`),
    tile("Breaching now", `${s.sensors_breaching}`,
         s.sensors_breaching
           ? `${noun(s.sensors_breaching)} at risk`
           : "all within tolerance"),
    tile("Open incidents", `${s.open_incidents}`,
         s.escalation_due ? `${s.escalation_due} due for a call` : "none awaiting escalation"),
    tile("At-risk forecast", d.entitlements.predictive_forecasting ? `${s.at_risk}` : "—",
         d.entitlements.predictive_forecasting ? "projected to breach" : "upgrade to unlock"),
    tile("Compliance", s.compliance_percent === null ? "—" : `${s.compliance_percent}%`,
         `last ${s.compliance_days} days`),
    tile("Flat batteries", `${s.low_battery ?? 0}`,
         s.low_battery ? "replace before they go dark" : "all healthy"),
    tile("Readings logged", `${s.readings_logged}`,
         `${s.readings_breached} out of band`),
  ].join("");
  animateFigures($("tiles"));

  // Rebuilt on every poll because a site can be added from the sidebar.
  const siteSelect = $("fleet-site");
  const chosen = state.site;
  siteSelect.innerHTML = [`<option value="all">Every site</option>`]
    .concat((d.sites || []).map((site) =>
      `<option value="${esc(site.site_id)}">${esc(site.name)}</option>`))
    .concat(s.unplaced_sensors
      ? [`<option value="none">Not at a site</option>`] : [])
    .join("");
  siteSelect.value = (d.sites || []).some((x) => x.site_id === chosen)
    || chosen === "none" ? chosen : "all";
  state.site = siteSelect.value;

  $("fleet-title").textContent = d.sensors.length
    ? `${d.sensors.length} ${noun(d.sensors.length)} being watched`
    : "Your fridges and freezers";
  $("fleet-search").placeholder = `Search ${noun(2)}, location or site`;
  $("fleet-filter").options[0].textContent = `All ${noun(2)}`;
  const shown = visibleSensors(d.sensors);
  $("fleet-note").textContent = shown.length === d.sensors.length
    ? `${d.sensors.length} ${noun(d.sensors.length)}`
    : `${shown.length} of ${d.sensors.length} shown`;
  renderFleet(shown);
  renderTable(shown);

  renderFirstDay(d.first_day);
  const owner = Boolean(state.user && state.user.role === "owner");
  $("sim-start").hidden = !owner;
  const canTest = Boolean(state.user && state.user.role !== "viewer");
  $("test-alert-box").hidden = !canTest;
  $("test-call-wrap").hidden = !(d.entitlements && d.entitlements.voice_escalation);

  const open = d.incidents.filter((i) => i.state !== "resolved").length;
  $("incident-note").textContent =
    d.incidents.length ? `${open} active, ${d.incidents.length} total` : "";
  renderIncidents(d.incidents, d.entitlements);
  renderByod(d.sensors);
}

/* The estate's own words. Every string that would otherwise say "sensor"
   goes through here, so a yacht owner reads "engine bays" and a data
   centre reads "racks" — from the same code, off the same data. */
/* An estate of one industry gets that industry's own word -- coolers,
   walk-ins, hangars -- which is what the people who work there say. An
   estate spanning several falls back to "asset", which is what nobody
   says. "Assets under watch" is a phrase from a spreadsheet, not from a
   kitchen, so the generic case says the plain thing instead. */
const GENERIC_NOUNS = new Set(["asset", "assets", "unit", "units"]);

function noun(count) {
  const d = state.data || {};
  const one = d.fleet_noun || "sensor";
  const many = d.fleet_plural || "sensors";
  const word = count === 1 ? one : many;
  return GENERIC_NOUNS.has(word.toLowerCase())
    ? (count === 1 ? "fridge or freezer" : "fridges and freezers")
    : word;
}
function Noun(count) {
  const word = noun(count);
  return word.charAt(0).toUpperCase() + word.slice(1);
}

function tile(label, value, foot) {
  // data-to lets the figure count up after paint. A number that snaps
  // reads as a page reloading; one that settles reads as an instrument.
  const numeric = typeof value === "string" && /^[0-9.]+%?$/.test(value);
  return `<div class="tile"><div class="tile-label">${esc(label)}</div>`
       + `<div class="tile-value"${numeric ? ` data-to="${esc(value)}"` : ""}>`
       + `${numeric ? "" : esc(value)}</div>`
       + `<div class="tile-foot">${esc(foot)}</div></div>`;
}

/* Count a figure up to its value over about half a second. Skipped
   outright for anyone who has asked for reduced motion. */
function animateFigures(root) {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  root.querySelectorAll("[data-to]").forEach((el) => {
    const raw = el.dataset.to;
    const suffix = raw.endsWith("%") ? "%" : "";
    const target = parseFloat(raw);
    if (!isFinite(target)) { el.textContent = raw; return; }
    if (reduced || target === 0) { el.textContent = raw; return; }

    const decimals = (raw.split(".")[1] || "").replace("%", "").length;
    const started = performance.now();
    const DURATION = 520;
    const step = (now) => {
      const t = Math.min(1, (now - started) / DURATION);
      // Ease out, so it decelerates into the figure rather than stopping.
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = (target * eased).toFixed(decimals) + suffix;
      if (t < 1) requestAnimationFrame(step);
      else el.textContent = raw;
    };
    requestAnimationFrame(step);
  });
}

/* An estate of two hundred freezers is a list nobody can read. Search,
   filter and grouping are what turn it back into something answerable. */
function visibleSensors(sensors) {
  // A wall shows every unit: its filters are behind controls it hides.
  if (state.wall) return sensors;
  const needle = state.search.trim().toLowerCase();
  return sensors.filter((s) => {
    if (needle) {
      const hay = [s.sensor_id, s.location_name, s.site_name, s.industry_name]
        .filter(Boolean).join(" ").toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    if (state.site === "none" && s.site_id) return false;
    if (state.site !== "all" && state.site !== "none"
        && s.site_id !== state.site) return false;

    switch (state.filter) {
      case "breaching": return !!s.breaching;
      case "offline":   return !s.online;
      case "battery":   return !!s.battery_low;
      case "unplaced":  return !s.site_id;
      default:          return true;
    }
  });
}

function renderFleet(sensors) {
  if (!sensors.length) {
    const filtered = state.search || state.filter !== "all" || state.site !== "all";
    $("fleet").innerHTML = `<div class="empty">${
      filtered
        ? `Nothing matches that.<span class="sub">Clear the search or the filter
             to see the whole estate.</span>`
        : `Nothing is being watched yet.<span class="sub">Add your first
             ${esc(noun(1))} and its readings appear here as they arrive.</span>`
          + nextSteps([["sensor", `Add a ${noun(1)}`, true]].concat(
              state.user && state.user.role === "owner"
                ? [["simulate", "No hardware yet? Try a simulated one"]] : []))
    }</div>`;
    return;
  }

  if (state.groupBySite) {
    const groups = new Map();
    sensors.forEach((s) => {
      const key = s.site_name || "Not at a site";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(s);
    });
    // "Not at a site" sits last: it is the leftovers, not a location.
    const names = [...groups.keys()].sort((a, b) =>
      a === "Not at a site" ? 1 : b === "Not at a site" ? -1 : a.localeCompare(b));
    $("fleet").style.display = "block";
    $("fleet").innerHTML = names.map((name) => {
      const rows = groups.get(name);
      const bad = rows.filter((s) => s.breaching).length;
      return `<div class="group-head">
          <span>${esc(name)}</span>
          <span class="count">${rows.length} sensor${rows.length === 1 ? "" : "s"}${
            bad ? ` · ${bad} breaching` : ""
          }</span>
        </div>
        <div class="fleet">${rows.map(sensorCard).join("")}</div>`;
    }).join("");
    return;
  }

  $("fleet").style.display = "";
  $("fleet").innerHTML = sensors.map(sensorCard).join("");
}

function healthPills(s) {
  const bits = [];
  if (s.site_name) {
    bits.push(`<span class="pill site">${esc(s.site_name)}</span>`);
  }
  if (s.battery_percent !== null && s.battery_percent !== undefined) {
    const cls = s.battery_low ? "bad" : s.battery_percent <= 40 ? "warn" : "";
    bits.push(`<span class="pill ${cls}">battery ${esc(Math.round(s.battery_percent))}%`
      + `<span class="meter"><i data-w="${Math.max(2, Math.min(100, s.battery_percent))}"></i></span>`
      + `</span>`);
  }
  if (s.signal_percent !== null && s.signal_percent !== undefined) {
    const cls = s.signal_percent <= 25 ? "warn" : "";
    bits.push(`<span class="pill ${cls}">signal ${esc(Math.round(s.signal_percent))}%`
      + `<span class="meter"><i data-w="${Math.max(2, Math.min(100, s.signal_percent))}"></i></span>`
      + `</span>`);
  }
  return bits.length ? `<div class="health">${bits.join("")}</div>` : "";
}

function sensorCard(s) {
  {
    const risk = s.risk_level || "unknown";
    // "unknown" is the forecaster saying it has not got enough history to
    // fit a trend yet — a freshly installed sensor, or one that has just
    // come back. Printed as the word UNKNOWN next to a green ONLINE it
    // reads as "something is wrong with this asset", which is the first
    // thing a new customer sees across a whole healthy fleet on their
    // first morning. Say what is actually true instead.
    const label = !s.risk_level
      ? "no forecast"
      : risk === "unknown"
        ? "trend pending"
        : (s.hours_until_breach !== null && s.hours_until_breach > 0
            ? `${risk} · ${s.hours_until_breach}h`
            : risk);
    const unit = s.temperature_unit || state.unit;
    const reading = s.last_temperature_display;
    const temp = (reading === null || reading === undefined)
      ? "—" : reading.toFixed(1);
    const above = s.danger_above_display, below = s.danger_below_display;
    const bound = s.external_device_sn
      ? `BYOD ${esc(s.external_device_sn)}` : "Native pulse";

    return `<article class="sensor ${s.breaching ? "is-breaching" : ""}">
      <div class="sensor-top">
        <span class="sensor-id grow clickable" data-detail="${esc(s.sensor_id)}"
              role="button" tabindex="0">${esc(s.sensor_id)}</span>
        ${badge(s.breaching ? "critical" : risk, s.breaching ? "breaching" : label)}
      </div>
      <div class="sensor-loc">${esc(s.location_name)} · ${esc(s.industry_name)}</div>
      <div class="sensor-read">
        <span class="sensor-temp">${esc(temp)}</span>
        <span class="sensor-unit">°${esc(unit)}</span>
        <span class="nav-spacer"></span>
        ${badge(s.online ? "low" : "unknown", s.online ? "online" : "offline")}
      </div>
      <div class="spark-wrap">${sparkline(s)}</div>
      ${healthPills(s)}
      ${s.online ? "" : `<a class="offline-help" href="/help#sensor-offline"
          >Offline: what to check</a>`}
      <div class="sensor-meta">
        <span>${esc(bound)}</span>
        <span>limit ${below !== null && below !== undefined
            ? esc(below) + "–" : "&lt; "}${esc(above)}°${esc(unit)}${
          s.uses_override ? ' <span class="u-c-warning">tuned</span>' : ""
        }</span>
      </div>
      <div class="sensor-actions">
        <button class="btn btn-sm" data-detail="${esc(s.sensor_id)}">History</button>
        <button class="btn btn-sm" data-tune="${esc(s.sensor_id)}">Retune</button>
        <button class="btn btn-sm" data-rekey="${esc(s.sensor_id)}">New key</button>
        <button class="btn btn-sm" data-retire="${esc(s.sensor_id)}">Retire</button>
      </div>
    </article>`;
  }
}

/* 12-point sparkline: recessive line, current point in the accent. */
function sparkline(sensor) {
  const values = sensor.spark || [];
  if (values.length < 2) {
    return `<div class="u-h-40 u-d-grid u-place-center u-c-muted u-fs-11-5">
              awaiting readings</div>`;
  }

  const W = 250, H = 40, PAD = 5;
  const min = Math.min(...values), max = Math.max(...values);
  const span = (max - min) || 1;
  const x = (i) => PAD + (i * (W - PAD * 2)) / (values.length - 1);
  const y = (v) => H - PAD - ((v - min) / span) * (H - PAD * 2);

  const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const lastX = x(values.length - 1), lastY = y(values[values.length - 1]);
  const breached = sensor.spark_breached || [];
  const endColor = breached[breached.length - 1] ? "var(--critical)" : "var(--accent)";


  const hits = values.map((v, i) => {
    const at = (sensor.spark_at || [])[i] || "";
    return `<rect x="${(x(i) - 10).toFixed(1)}" y="0" width="20" height="${H}"
              fill="transparent" data-v="${v}" data-at="${esc(at)}"
              data-cx="${x(i).toFixed(1)}" data-cy="${y(v).toFixed(1)}"></rect>`;
  }).join("");

  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"
               role="img" aria-label="Recent temperature trend for ${esc(sensor.sensor_id)}">
    <polyline points="${points}" fill="none" stroke="var(--muted)" stroke-width="1.6"
              stroke-linecap="round" stroke-linejoin="round"/>
    <circle cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="4"
            fill="${endColor}" stroke="var(--surface-2)" stroke-width="2"/>
    <g class="spark-hits">${hits}</g>
  </svg>`;
}

/* Sparkline hover tooltip. */
document.addEventListener("mouseover", (event) => {
  const hit = event.target.closest(".spark-hits rect");
  if (!hit) return;
  const tip = $("tip");
  const at = hit.dataset.at
    ? new Date(hit.dataset.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "";
  tip.innerHTML = `<div class="tip-v">${esc(hit.dataset.v)} °${esc(state.unit)}</div>`
                + (at ? `<div class="tip-t">${esc(at)}</div>` : "");
  tip.hidden = false;
});
document.addEventListener("mousemove", (event) => {
  const tip = $("tip");
  if (tip.hidden) return;
  tip.style.left = Math.min(event.clientX + 14, window.innerWidth - tip.offsetWidth - 10) + "px";
  tip.style.top = (event.clientY - tip.offsetHeight - 12) + "px";
});
document.addEventListener("mouseout", (event) => {
  if (event.target.closest(".spark-hits rect")) $("tip").hidden = true;
});

function renderTable(sensors) {
  if (!sensors.length) { $("fleet-table").innerHTML = ""; return; }
  const rows = sensors.map((s) => `<tr>
      <td>${esc(s.sensor_id)}</td>
      <td>${esc(s.site_name || "—")}</td>
      <td>${esc(s.location_name)}</td>
      <td class="num">${s.last_temperature_display === null
          || s.last_temperature_display === undefined
          ? "—" : esc(s.last_temperature_display.toFixed(1))}</td>
      <td class="num">${s.battery_percent === null || s.battery_percent === undefined
          ? "—" : esc(Math.round(s.battery_percent)) + "%"}</td>
      <td>${esc(s.online ? "online" : "offline")}</td>
      <td>${esc(s.breaching ? "breaching" : (s.risk_level || "—"))}</td>
      <td class="num">${s.hours_until_breach === null || s.hours_until_breach === undefined
          ? "—" : esc(s.hours_until_breach)}</td>
    </tr>`).join("");
  $("fleet-table").innerHTML = `<div class="scrollx"><table>
      <caption class="sr">Sensor fleet, as a table</caption>
      <thead><tr>
        <th>Sensor</th><th>Site</th><th>Location</th><th class="num">°${esc(state.unit)}</th>
        <th class="num">Battery</th>
        <th>Link</th><th>Risk</th><th class="num">Hours to breach</th>
      </tr></thead><tbody>${rows}</tbody></table></div>`;
}

/* ---------------- fleet controls ---------------- */

/* Re-render from the data already in hand. A filter that waits for the
   next poll to take effect feels broken. */
function reapplyFleet() {
  if (!state.data) return;
  const shown = visibleSensors(state.data.sensors);
  $("fleet-note").textContent = shown.length === state.data.sensors.length
    ? `${state.data.sensors.length} ${noun(state.data.sensors.length)}`
    : `${shown.length} of ${state.data.sensors.length} shown`;
  renderFleet(shown);
  renderTable(shown);
}

$("fleet-search").addEventListener("input", (event) => {
  state.search = event.target.value;
  reapplyFleet();
});
$("fleet-filter").addEventListener("change", (event) => {
  state.filter = event.target.value;
  reapplyFleet();
});
$("fleet-site").addEventListener("change", (event) => {
  state.site = event.target.value;
  reapplyFleet();
});
$("group-sites").addEventListener("click", () => {
  state.groupBySite = !state.groupBySite;
  $("group-sites").setAttribute("aria-pressed", String(state.groupBySite));
  reapplyFleet();
});

/* ---------------- unit ---------------- */

$("unit-toggle").addEventListener("click", async () => {
  const next = state.unit === "C" ? "F" : "C";
  try {
    await api("/api/licenses/me/temperature-unit", {
      method: "POST", body: JSON.stringify({ temperature_unit: next }),
    });
    state.unit = next;
    toast(next === "C" ? "Showing Celsius." : "Showing Fahrenheit.", "ok");
    await refresh();
  } catch (err) { toast(err.message, "err"); }
});

/* ---------------- wall display ---------------- */

/* A screen in the back office nobody is sitting at. Big numbers, no
   controls, and nothing that can be clicked by a passing elbow. */
function setWall(on) {
  if (on === state.wall) return;
  state.wall = on;
  document.body.classList.toggle("wall", on);
  if (on) {
    closeDrawer();
    // The whole estate, whatever this browser had narrowed the page to.
    // Entered from the Money tab, or with "bar" in the fleet search, the
    // wall showed a bill, or one freezer, and nothing said it was a part.
    state.beforeWall = { tab: finder.tab, query: finder.query };
    finder.tab = "all";
    finder.query = "";
  } else if (state.beforeWall) {
    finder.tab = state.beforeWall.tab;
    finder.query = state.beforeWall.query;
    state.beforeWall = null;
  }
  applyFinder();
  reapplyFleet();
}
$("wall-mode").addEventListener("click", () => setWall(true));
$("wall-exit").addEventListener("click", () => setWall(false));

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (state.wall) { setWall(false); return; }
  if (!$("drawer").hidden) closeDrawer();
});
$("scrim").addEventListener("click", closeDrawer);

/* A sensor id is a button for keyboard users too. */
document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  if (state.wall) return;
  const target = event.target.closest("[data-detail]");
  if (!target || target.tagName === "BUTTON") return;
  event.preventDefault();
  openDrawer(target.dataset.detail);
});

$("toggle-table").addEventListener("click", () => {
  state.tableView = !state.tableView;
  $("fleet").hidden = state.tableView;
  $("fleet-table").hidden = !state.tableView;
  $("toggle-table").textContent = state.tableView ? "Card view" : "Table view";
  $("toggle-table").setAttribute("aria-pressed", String(state.tableView));
});

function deliveryLine(record, channel) {
  if (!record) return `<span class="badge s-muted"><span class="dot"></span>${channel} not attempted</span>`;
  if (record.delivered) {
    return `<span class="badge s-good"><span class="dot"></span>${channel} sent`
         + `${record.provider_sid ? " · " + esc(record.provider_sid) : ""}</span>`;
  }
  return `<span class="badge s-warning"><span class="dot"></span>`
       + `${channel} not sent · ${esc(record.status)}</span>`;
}

/* Every alert text failed. Said at the top of the card, because the card
   otherwise reads as if the roster has been told. */
function textsFailedNote(i) {
  if (!i.alert_texts_failed || i.state === "resolved") return "";
  const next = i.next_sms_retry_at && i.state === "open"
    ? ` Sending again at ${new Date(i.next_sms_retry_at).toLocaleTimeString([],
        { hour: "2-digit", minute: "2-digit" })}.`
    : " Phone the roster yourself.";
  return `<div class="card-note" role="alert"><span class="badge s-critical">`
       + `<span class="dot"></span>Alert texts failed</span> `
       + `No text has reached anyone.${esc(next)}</div>`;
}

/* What happened, in order. A stack of timestamps buried in JSON is not an
   answer to "why did nobody pick this up", and that is the question asked
   after every incident that goes wrong. */
function incidentTimeline(i) {
  const when = (iso) => iso
    ? new Date(iso).toLocaleString([], { month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit" })
    : "";
  const steps = [];

  steps.push({ cls: "hit", when: i.opened_at,
    text: `Breach detected at ${i.temperature_display ?? i.temperature_fahrenheit}°`
        + `${i.temperature_unit || state.unit}` });

  const texted = (i.sms_fanout || []).length || (i.sms_delivery ? 1 : 0);
  if (texted) {
    const landed = (i.sms_fanout || []).filter((f) => f.delivered).length
      || (i.sms_delivery && i.sms_delivery.delivered ? 1 : 0);
    steps.push({ cls: "hit", when: i.opened_at,
      text: `Texted ${texted} on the roster · ${landed} delivered` });
  }
  for (const r of (i.sms_retries || [])) {
    const sent = (r.deliveries || []).length;
    const got = (r.deliveries || []).filter((f) => f.delivered).length;
    steps.push({ cls: got ? "hit" : "", when: r.at,
      text: `Text sent again · ${got} of ${sent} delivered` });
  }

  if (i.voice_escalated_at) {
    const reached = (i.voice_fanout || []).find((f) => f.delivered);
    steps.push({ cls: "call", when: i.voice_escalated_at,
      text: reached
        ? `Called ${reached.contact_name || reached.to}`
        : "Called the ladder — nobody answered" });
  }
  if (i.acknowledged_at) {
    steps.push({ cls: "ack", when: i.acknowledged_at,
      text: `Acknowledged by ${i.acknowledged_by || "someone"}` });
  }
  if (i.resolved_at) {
    steps.push({ cls: "done", when: i.resolved_at, text: "Resolved" });
  }
  if (!i.acknowledged_at && !i.resolved_at) {
    steps.push({ cls: "", when: null,
      text: `Still open after ${Math.round(i.minutes_open)} minutes` });
  }

  return `<ul class="timeline">${steps.map((s) =>
    `<li class="${s.cls}">${esc(s.text)}`
    + (s.when ? ` <span class="when">${esc(when(s.when))}</span>` : "")
    + `</li>`).join("")}</ul>`;
}

function renderIncidents(incidents, entitlements) {
  if (!incidents.length) {
    const total = (state.data && state.data.sensors.length) || 0;
    $("incidents").innerHTML = `<div class="empty">${
      total
        ? `All ${total} ${noun(total)} are within tolerance.`
        : `Nothing to report yet.<span class="sub">Anything that goes out of
             its safe range shows up here, once something is being
             watched.</span>` + nextSteps([["sensor", `Add a ${noun(1)}`]])
    }</div>`;
    return;
  }

  $("incidents").innerHTML = incidents.map((i) => {
    const stateKind = i.state === "open" ? "critical"
                    : i.state === "acknowledged" ? "elevated" : "low";
    const canEscalate = i.state === "open" && !i.voice_escalated_at
                        && entitlements.voice_escalation;

    return `<article class="incident st-${esc(i.state)}">
      <div class="incident-top">
        ${badge(stateKind, i.state)}
        <span class="incident-title grow">${esc(i.catastrophe_type)}</span>
        <span class="card-note">${esc(i.sensor_id)} · ${esc(i.minutes_open)} min</span>
      </div>
      <div class="card-note">${esc(i.breach_details)}</div>
      ${textsFailedNote(i)}
      ${incidentTimeline(i)}

      <div class="quote">
        <div class="quote-label">SMS dispatched${i.sms_dispatch_source === "fallback_template"
          ? " (template fallback)" : ""}</div>
        ${esc(i.dispatched_sms_text)}
      </div>
      ${i.voice_script ? `<div class="quote">
        <div class="quote-label">Voice call script</div>${esc(i.voice_script)}</div>` : ""}

      <div class="row u-gap-6">
        ${deliveryLine(i.texts_landed && !(i.sms_delivery && i.sms_delivery.delivered)
          ? { delivered: true, provider_sid: "on a re-send" } : i.sms_delivery, "SMS")}
        ${i.voice_escalated_at ? deliveryLine(i.voice_delivery, "Call") : ""}
      </div>

      <div class="incident-actions">
        ${i.state === "open"
          ? `<button class="btn btn-sm" data-ack="${esc(i.incident_id)}">Acknowledge</button>` : ""}
        ${i.state !== "resolved"
          ? `<button class="btn btn-sm" data-resolve="${esc(i.incident_id)}">Resolve</button>` : ""}
        ${canEscalate
          ? `<button class="btn btn-sm" data-escalate="${esc(i.incident_id)}">Escalate to call</button>` : ""}
        <button class="btn btn-sm" data-packet="${esc(i.incident_id)}">Claim packet</button>
        <!-- The question actually being asked in the kitchen. Everything
             else on this row is about the incident; this one is about
             the food. -->
        <button class="btn btn-sm" data-food="${esc(i.sensor_id)}">Is the
          food still good?</button>
      </div>
      <div class="food-out" data-food-for="${esc(i.sensor_id)}"></div>
    </article>`;
  }).join("");
}

/* Spend controls: what was spent, and what the controls kept off the bill. */
function renderCosts(report) {
  const totals = report.totals;
  const saved = report.estimated_saved_usd;
  const spent = report.estimated_spend_usd;

  $("cost-note").textContent = `last ${report.period_days} days`;

  const bar = (() => {
    if (!(spent + saved)) return "";
    const pct = Math.round((saved / (spent + saved)) * 100);
    return `<div class="u-m-10-0-4">
      <div class="u-h-8 u-r-999 u-bg-raised u-ov-hidden u-d-flex">
        <div class="u-bg-good" data-w="${pct}"></div>
        <div class="u-bg-accent u-ml-2" data-w="${100 - pct}"></div>
      </div>
      <div class="u-d-flex u-justify-between u-fs-11-5 u-c-muted u-mt-5">
        <span>● avoided $${saved.toFixed(2)}</span>
        <span>spent $${spent.toFixed(2)} ●</span>
      </div>
    </div>`;
  })();

  const line = (label, value, note) =>
    `<div class="u-d-flex u-justify-between u-gap-10 u-p-5-0 u-fs-12-5">
       <span class="u-c-ink-2">${esc(label)}</span>
       <span class="u-tnum">${esc(value)}${
         note ? ` <span class="u-c-muted">${esc(note)}</span>` : ""
       }</span>
     </div>`;

  $("cost-body").innerHTML = bar
    + line("Model calls", totals.ai_calls,
           report.ai_cache_hit_rate_percent !== null
             ? `${report.ai_cache_hit_rate_percent}% from cache` : "")
    + line("Cache hits", totals.ai_cache_hits, "no charge")
    + line("SMS sent", totals.sms_sent,
           totals.sms_suppressed ? `${totals.sms_suppressed} capped` : "")
    + line("Voice calls", totals.voice_calls,
           totals.voice_suppressed ? `${totals.voice_suppressed} capped` : "")
    + `<div class="u-bt u-mt-8 u-pt-8">
         ${line("Estimated spend", "$" + spent.toFixed(2), "list rates")}
       </div>`;
}

function renderAudit(trail) {
  if (!trail.entries.length) {
    $("audit-body").innerHTML = `<div class="empty">No activity recorded yet.</div>`;
    return;
  }
  $("audit-body").innerHTML = trail.entries.map((e) => {
    const when = new Date(e.at).toLocaleString([], {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
    return `<div class="u-p-7-0 u-bb">
      <div class="u-d-flex u-gap-8 u-justify-between">
        <span class="u-fs-12-5 u-fw-500">${esc(e.action)}</span>
        <span class="u-fs-11-5 u-c-muted u-ws-nowrap">${esc(when)}</span>
      </div>
      <div class="u-fs-12 u-c-ink-2 u-mt-2">${esc(e.detail)}</div>
      <div class="u-fs-11-5 u-c-muted">${esc(e.actor)}</div>
    </div>`;
  }).join("");
}

/* Who gets woken, in the order the ladder tries them. */
/* What the estate costs, and what answering alerts has been worth. */
/* The trial's way out.
 *
 * The conversion emails have always said "move the account onto Growth —
 * it takes one click", and until now there was no click: no page in this
 * console called /api/licenses/me/plan, so the only route from a finished
 * trial to a paying customer ran through somebody answering an email. The
 * endpoint existed the whole time.
 *
 * Owners only, because that is what the endpoint requires; anyone else is
 * told who can do it rather than shown a button that 403s. */
function upgradeBlock(tenant) {
  // The confirmation has to be drawn here, not written into the button's
  // result line: a successful upgrade refreshes the whole console, the card
  // is rebuilt, and the trial block -- line included -- is no longer drawn
  // because the account is no longer a trial. Measured in a browser: the
  // plan changed on the server and the owner saw the section simply vanish.
  if (tenant && state.justUpgradedTo && tenant.plan === state.justUpgradedTo) {
    return `<div class="u-bt u-mt-9 u-pt-9 u-fs-12-5">
         <span class="s-good">You are on ${esc(tenant.plan_name || tenant.plan)}
           now.</span>
         <span class="u-c-muted">Phone-call escalation and the
           forecast are on. Your first invoice follows by email.</span>
       </div>`;
  }
  if (!tenant || tenant.plan !== "trial") return "";

  if (!(state.user && state.user.role === "owner")) {
    return `<div class="u-bt u-mt-9 u-pt-9 u-fs-12 u-c-muted">
         This is a trial. An owner on the account can move it onto a paid
         plan from here.
       </div>`;
  }

  return `<div class="u-bt u-mt-9 u-pt-10">
      <div class="u-fs-12-5 u-c-ink-2 u-mb-3">
        Move off the trial</div>
      <div class="u-fs-11-5 u-c-muted u-mb-8">
        Unlocks phone-call escalation and the 12-hour forecast. You are
        invoiced afterwards — nothing is charged here and no card is asked
        for.</div>
      <div class="u-d-flex u-gap-8 u-flex-wrap">
        <button class="btn btn-sm btn-primary" data-upgrade="growth">
          Growth · 50 units</button>
        <button class="btn btn-sm" data-upgrade="enterprise">
          Enterprise · 1000 units</button>
      </div>
      <div id="upgrade-out" class="u-fs-11-5 u-mt-7"></div>
    </div>`;
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-upgrade]");
  if (!button) return;
  const plan = button.getAttribute("data-upgrade");
  const out = $("upgrade-out");
  const buttons = [...document.querySelectorAll("[data-upgrade]")];
  buttons.forEach((b) => { b.disabled = true; });
  if (out) out.innerHTML = `<span class="u-c-muted">Moving…</span>`;
  try {
    await api("/api/licenses/me/plan", {
      method: "POST",
      body: JSON.stringify({ plan }),
    });
    // Long enough to be read, short enough not to become a permanent banner.
    state.justUpgradedTo = plan;
    setTimeout(() => { state.justUpgradedTo = null; }, 30000);
    logToConsole(`Plan changed to ${plan}.`, "ok");
    await refresh();
  } catch (err) {
    // Left enabled: a seat-count refusal is something they can act on and
    // try again, and a disabled button gives them nothing to try again with.
    buttons.forEach((b) => { b.disabled = false; });
    if (out) {
      out.innerHTML = `<span class="s-warning">${esc(readableError(err))}</span>`;
    }
  }
});

function renderBilling(subscription, roi, tenant) {
  $("bill-note").textContent = subscription.billable
    ? `${subscription.units_total} billable units`
    : "trial — not charged";

  if (!subscription.line_items.length) {
    $("bill-body").innerHTML =
      `<div class="empty u-p-14">
         No units registered, so nothing to bill.
         ${nextSteps([["sensor", `Add a ${noun(1)}`]])}
       </div>` + upgradeBlock(tenant);
    return;
  }

  const lines = subscription.line_items.map((l) => `
    <div class="u-d-flex u-justify-between u-gap-10 u-p-5-0 u-fs-12-5">
      <span class="u-c-ink-2">${esc(l.industry)}
        <span class="u-c-muted">· ${esc(l.description)}
          @ $${l.unit_price_usd}</span></span>
      <span class="u-tnum">$${l.line_total_usd.toLocaleString()}</span>
    </div>`).join("");

  const saved = roi && roi.loss_avoided_usd
    ? `<div class="u-bt u-mt-9 u-pt-9">
         <div class="u-d-flex u-justify-between u-fs-12-5">
           <span class="u-c-ink-2">Loss avoided · 30 days</span>
           <span class="s-good u-fw-650 u-tnum">
             $${roi.loss_avoided_usd.toLocaleString()}</span>
         </div>
         <div class="u-fs-11-5 u-c-muted u-mt-3">
           ${roi.quantified_saves} answered incident${roi.quantified_saves === 1 ? "" : "s"}
           in verticals with a published loss figure${
             roi.return_multiple ? ` · ${roi.return_multiple}× the subscription` : ""
           }
         </div>
       </div>`
    : `<div class="u-bt u-mt-9 u-pt-9 u-fs-11-5 u-c-muted">
         No quantified saves yet this period.
       </div>`;

  $("bill-body").innerHTML = lines
    + `<div class="u-bt u-mt-8 u-pt-8 u-d-flex u-justify-between u-items-baseline">
         <span class="u-fs-12-5 u-c-ink-2">Monthly</span>
         <span class="u-fs-20 u-fw-650 u-ls-n01em">
           $${subscription.monthly_total_usd.toLocaleString()}</span>
       </div>`
    + `<div class="u-ta-right u-fs-11-5 u-c-muted">
         $${subscription.annual_total_usd.toLocaleString()} a year</div>`
    + saved
    + upgradeBlock(tenant);
}

function renderRoster(roster) {
  $("roster-note").textContent = roster.using_fallback
    ? "using fallback contact"
    : `${roster.count} on call`;

  if (!roster.contacts.length) {
    $("roster-body").innerHTML =
      `<div class="empty u-p-14">
         No roster yet, so alerts go to ${esc(roster.fallback.full_name)}
         (${esc(roster.fallback.phone)}).
         <span class="sub">Add whoever should be woken at 3am, in the order
         to try them.</span>${nextSteps([["oncall", "Add someone to call"]])}
       </div>`;
    return;
  }

  $("roster-body").innerHTML = roster.contacts.map((c, index) => {
    const channels = [c.receives_sms ? "SMS" : null, c.receives_voice ? "call" : null]
      .filter(Boolean).join(" + ") || "muted";
    return `<div class="u-d-flex u-items-center u-gap-9 u-p-7-0 u-bb${c.active ? "" : " u-o-5"}">
      <span class="u-w-20 u-c-muted u-fs-12 u-tnum">${index + 1}</span>
      <span class="grow">
        <span class="u-fs-13 u-fw-500">${esc(c.full_name)}</span>
        <span class="u-d-block u-fs-11-5 u-c-muted">
          ${esc(c.phone)} · ${esc(channels)}${c.active ? "" : " · off duty"}
        </span>
      </span>
      <button class="btn btn-sm" data-contact-toggle="${esc(c.contact_id)}"
              data-active="${c.active ? "1" : "0"}">${c.active ? "Mute" : "Unmute"}</button>
      <button class="btn btn-sm" data-contact-remove="${esc(c.contact_id)}">Remove</button>
    </div>`;
  }).join("");
}

/* ---------------- on call now: rotations and quiet hours ----------------
   The roster says who can be called. This says who is on call at which
   hour: rotations take turns, handing over at a local time, and quiet
   hours keep somebody from being texted while anybody else is awake.
   Everything shown comes from /api/contacts/rotations as the server
   works it out (oncall.py) -- the page never decides whose turn it is.

   Owners and operators get the forms; a viewer sees the same rotations,
   handovers and warnings with nothing to press. The forms are in the
   page rather than rebuilt by the poll, so the twenty-second refresh
   never wipes something half typed. */

const ROTA = { roster: [], rotations: [], draft: [], ready: false };

function canEditRoster() {
  return Boolean(state.user && state.user.role !== "viewer") && !state.wall;
}

function rotaPlace(scope) {
  if (!scope || scope === "estate") return "Whole estate";
  const site = ((state.data && state.data.sites) || []).find((s) => s.site_id === scope);
  return site ? site.name : "A location";
}

function turnWords(days) {
  const n = Number(days);
  if (n === 1) return "Daily turns";
  if (n === 7) return "Weekly turns";
  if (n % 7 === 0) return `${n / 7}-week turns`;
  return `${n}-day turns`;
}

/* Options for a select, rebuilt only when they change, so a poll never
   closes a list somebody has open or forgets what they picked. */
function fillSelect(select, options) {
  const signature = JSON.stringify(options);
  if (select.dataset.sig === signature) return;
  const current = select.value;
  select.innerHTML = options.map(([value, label]) =>
    `<option value="${esc(value)}">${esc(label)}</option>`).join("");
  select.dataset.sig = signature;
  if (options.some(([value]) => value === current)) select.value = current;
}

function browserZone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; }
  catch (_) { return ""; }
}

function rotaDefaults() {
  if (ROTA.ready) return;
  ROTA.ready = true;
  const zone = browserZone();
  if (!$("rot-tz").value) $("rot-tz").value = zone;
  if (!$("qh-tz").value) $("qh-tz").value = zone;
  let zones = [];
  try { zones = Intl.supportedValuesOf ? Intl.supportedValuesOf("timeZone") : []; }
  catch (_) { zones = []; }
  $("tz-list").innerHTML = zones.map((z) => `<option value="${esc(z)}">`).join("");
}

function renderRotationDraft() {
  const byId = new Map(ROTA.roster.map((c) => [c.contact_id, c]));
  // Somebody taken off the roster since is out of the draft too.
  ROTA.draft = ROTA.draft.filter((id) => byId.has(id));
  $("rot-members").innerHTML = ROTA.draft.length
    ? ROTA.draft.map((id, index) => {
        const c = byId.get(id);
        return `<li class="rota-member${c.active ? "" : " is-muted"}">
          <span class="grow">${esc(c.full_name)}${c.active ? "" : " (muted)"}</span>
          <button class="btn btn-sm" type="button" data-rot-up="${index}"${
            index === 0 ? " disabled" : ""}>Up</button>
          <button class="btn btn-sm" type="button" data-rot-down="${index}"${
            index === ROTA.draft.length - 1 ? " disabled" : ""}>Down</button>
          <button class="btn btn-sm" type="button" data-rot-drop="${index}">Remove</button>
        </li>`;
      }).join("")
    : `<li class="rota-member is-none">Nobody yet. Add people in the
         order they take turns.</li>`;
  const left = ROTA.roster.filter((c) => !ROTA.draft.includes(c.contact_id));
  fillSelect($("rot-add"), [["", left.length ? "Choose someone" : "Everyone is in"]]
    .concat(left.map((c) => [c.contact_id, c.full_name])));
}

function renderRotations(data, roster) {
  const people = (roster && roster.contacts) || [];
  const rotations = data.rotations || [];
  ROTA.roster = people;
  ROTA.rotations = rotations;
  const editor = canEditRoster();

  $("rotation-note").textContent = rotations.length
    ? `${rotations.length} rotation${rotations.length === 1 ? "" : "s"}`
    : "no rotation";

  const taking = new Set(rotations.flatMap((r) => r.members.map((m) => m.contact_id)));
  const always = people.filter((c) => c.active && !taking.has(c.contact_id));
  const alwaysLine = people.length
    ? `<div class="rota-line"><span class="rota-label">Always on call</span>${
        always.length ? esc(always.map((c) => c.full_name).join(", "))
                      : "nobody outside a rotation"}</div>`
    : "";

  // A muted person keeps their turns, and on them nobody from the
  // rotation is on call; the schedule says so next to the name.
  const muted = new Set(people.filter((c) => !c.active).map((c) => c.contact_id));
  const named = (row) => esc(row.full_name || "Somebody removed")
    + (muted.has(row.contact_id) ? " (muted)" : "");
  const blocks = rotations.map((r) => {
    const schedule = r.schedule || [];
    const now = r.on_shift_now;
    const next = schedule[1];
    const turns = schedule.map((s, index) => `
      <li class="${index === 0 ? "is-now" : ""}">
        <span class="rota-who">${named(s)}</span>
        <span class="rota-when">${esc(s.starts_local)} to ${esc(s.ends_local)}</span>
      </li>`).join("");
    const buttons = editor
      ? `<button class="btn btn-sm" type="button" data-rota-edit="${esc(r.scope)}">Change</button>
         <button class="btn btn-sm" type="button" data-rota-stop="${esc(r.scope)}">Stop</button>`
      : "";
    const who = now
      ? `<div class="rota-line is-now"><span class="rota-label">On call now</span><b>${named(now)}</b>${
           schedule[0] ? ` until ${esc(schedule[0].ends_local)}` : ""}</div>
         <div class="rota-line"><span class="rota-label">Next</span>${
           next ? named(next) : "nobody"}</div>`
      : `<div class="rota-line is-empty">Nobody is in this rotation, so
           nobody from it is on call.</div>`;
    return `<div class="rota">
      <div class="rota-head">
        <span class="grow"><b>${esc(rotaPlace(r.scope))}</b>
          <span class="rota-sub">${esc(turnWords(r.shift_days))}, handing over at
            ${esc(r.handover)} ${esc(r.timezone)} · ${esc(r.members.length)} ${
            r.members.length === 1 ? "person" : "people"}</span></span>
        ${buttons}
      </div>
      ${who}
      ${turns ? `<ol class="rota-shifts">${turns}</ol>` : ""}
    </div>`;
  }).join("");

  const warnings = (data.warnings || []).map((w) =>
    `<div class="card-note rota-gap" role="alert"><span class="badge s-warning">`
    + `<span class="dot"></span>Gap</span> ${esc(w)}</div>`).join("");

  let body;
  if (!people.length && !rotations.length) {
    body = `<div class="empty u-p-14">Nobody to take turns yet.
      <span class="sub">Add people to Who gets called first.</span>${
      nextSteps([["oncall", "Add someone to call"]])}</div>`;
  } else if (!rotations.length) {
    body = `<div class="rota-line">No rotation, so everyone on the roster is
      on call all the time.</div>${alwaysLine}`;
  } else {
    body = blocks + alwaysLine;
  }
  $("rotation-body").innerHTML = body + warnings;

  const withQuiet = people.filter((c) => c.quiet_hours);
  $("quiet-body").innerHTML = !people.length
    ? `<div class="rota-line">Nobody on the roster yet.</div>`
    : (withQuiet.length
      ? withQuiet.map((c) => `<div class="rota-quiet">
          <span class="grow"><b>${esc(c.full_name)}</b>
            <span class="rota-sub">Quiet ${esc(c.quiet_hours.start)} to ${
              esc(c.quiet_hours.end)} ${esc(c.quiet_hours.timezone)}</span></span>
          ${editor ? `<button class="btn btn-sm" type="button" data-quiet-edit="${
            esc(c.contact_id)}">Change</button>` : ""}
        </div>`).join("")
      : `<div class="rota-line">Nobody has quiet hours.</div>`)
      + (people.length ? `<div class="card-note u-mt-8">In their quiet hours
          somebody is texted only if nobody else would be, and called last.
          An alert always reaches someone.</div>` : "");

  $("rotation-form").hidden = !editor || !people.length;
  $("quiet-form").hidden = !editor || !people.length;
  $("rotation-viewer").hidden = editor || state.wall;
  if (!editor || !people.length) return;

  rotaDefaults();
  const sites = (state.data && state.data.sites) || [];
  fillSelect($("rot-scope"), [["estate", "Whole estate"]]
    .concat(sites.map((s) => [s.site_id, s.name])));
  fillSelect($("qh-person"), people.map((c) => [c.contact_id, c.full_name]));
  renderRotationDraft();
}

function loadRotationIntoForm(scope) {
  const rotation = ROTA.rotations.find((r) => r.scope === scope);
  if (!rotation) {
    $("rot-form-title").textContent = "Make a rotation";
    return false;
  }
  $("rot-scope").value = rotation.scope;
  $("rot-tz").value = rotation.timezone;
  $("rot-handover").value = rotation.handover;
  $("rot-days").value = String(rotation.shift_days);
  $("rot-start").value = rotation.starts_on || "";
  ROTA.draft = rotation.members.map((m) => m.contact_id);
  $("rot-form-title").textContent = `Change the rotation: ${rotaPlace(rotation.scope)}`;
  renderRotationDraft();
  return true;
}

function resetRotationForm() {
  ROTA.draft = [];
  $("rot-start").value = "";
  $("rot-form-title").textContent = "Make a rotation";
  renderRotationDraft();
}

function loadQuietHours(contactId) {
  const person = ROTA.roster.find((c) => c.contact_id === contactId);
  const quiet = person && person.quiet_hours;
  $("qh-start").value = quiet ? quiet.start : "22:00";
  $("qh-end").value = quiet ? quiet.end : "07:00";
  $("qh-tz").value = quiet ? quiet.timezone : ($("qh-tz").value || browserZone());
}

$("find-on-call-rotation").addEventListener("click", (event) => {
  if (state.wall) return;
  const btn = event.target.closest("button");
  if (!btn) return;
  const d = btn.dataset;
  const move = (from, to) => {
    const [id] = ROTA.draft.splice(from, 1);
    ROTA.draft.splice(to, 0, id);
    renderRotationDraft();
  };
  if (d.rotUp !== undefined) move(Number(d.rotUp), Number(d.rotUp) - 1);
  else if (d.rotDown !== undefined) move(Number(d.rotDown), Number(d.rotDown) + 1);
  else if (d.rotDrop !== undefined) {
    ROTA.draft.splice(Number(d.rotDrop), 1);
    renderRotationDraft();
  } else if (d.rotaEdit) {
    loadRotationIntoForm(d.rotaEdit);
    $("rotation-form").scrollIntoView({ behavior: "smooth", block: "start" });
  } else if (d.rotaStop) {
    if (!confirm(`Stop the rotation for ${rotaPlace(d.rotaStop)}? Its people `
                 + "go back to being on call all the time.")) return;
    act(() => api(`/api/contacts/rotations/${encodeURIComponent(d.rotaStop)}`,
                  { method: "DELETE" }), "Rotation stopped.");
  } else if (d.quietEdit) {
    $("qh-person").value = d.quietEdit;
    loadQuietHours(d.quietEdit);
    $("quiet-form").scrollIntoView({ behavior: "smooth", block: "start" });
  }
});

$("rot-add-btn").addEventListener("click", () => {
  const id = $("rot-add").value;
  if (!id || ROTA.draft.includes(id)) return;
  ROTA.draft.push(id);
  renderRotationDraft();
});

$("rot-scope").addEventListener("change", () => {
  // A place that already has a rotation opens it to change, rather than
  // replacing it with whatever was half built for somewhere else.
  if (!loadRotationIntoForm($("rot-scope").value)) {
    $("rot-form-title").textContent = "Make a rotation";
  }
});

$("rot-reset").addEventListener("click", resetRotationForm);

$("rotation-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (!ROTA.draft.length) {
    toast("Add at least one person to take turns.", "err");
    return;
  }
  const scope = $("rot-scope").value;
  act(async () => {
    await api("/api/contacts/rotations", {
      method: "PUT",
      body: JSON.stringify({
        site_id: scope === "estate" ? null : scope,
        timezone: $("rot-tz").value.trim(),
        handover: $("rot-handover").value,
        shift_days: Number($("rot-days").value),
        starts_on: $("rot-start").value || null,
        members: ROTA.draft.slice(),
      }),
    });
    resetRotationForm();
  }, "Rotation saved.");
});

$("qh-person").addEventListener("change", () => loadQuietHours($("qh-person").value));

$("quiet-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const id = $("qh-person").value;
  if (!id) return;
  act(() => api(`/api/contacts/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify({ quiet_hours: {
      start: $("qh-start").value,
      end: $("qh-end").value,
      timezone: $("qh-tz").value.trim(),
    } }),
  }), "Quiet hours saved.");
});

$("qh-clear").addEventListener("click", () => {
  const id = $("qh-person").value;
  if (!id) return;
  act(() => api(`/api/contacts/${encodeURIComponent(id)}`, {
    method: "PATCH", body: JSON.stringify({ quiet_hours: null }),
  }), "Quiet hours removed.");
});


/* ---------------- invoices ---------------- */

const INVOICE_STATE_CLASS = { issued: "", paid: "good", void: "" };

/* The contract: what was signed, what it bills, what it is not billing
   yet, and how far behind it is.
 *
 * The three subsystems behind this — the self-billing contract, the
 * collections ladder and the revenue pipeline — existed with no way to
 * reach any of them from the product. An owner could be a month from the
 * end of a term, with an overdue invoice and four add-ons nobody had
 * offered them, and the console would show none of it.
 */
function renderContract(contract, pipeline, tenant) {
  const body = $("contract-body");
  const note = $("contract-note");
  const c = contract.contract;
  const money0 = (n) => "$" + Math.round(Number(n || 0)).toLocaleString();

  const collections = (contract.collections || {});
  const behind = collections.overdue_usd > 0
    ? `<div class="card-note u-c-critical u-mt-10">
         ${money(collections.overdue_usd)} overdue by ${
           collections.days_overdue} days.
         ${collections.withheld.length
            ? "Reporting is withheld until it clears."
            : ""}
       </div>`
    : "";

  // A trial cannot sign: POST /api/contracts refuses one with a 409 until
  // the licence is on a paid plan. The form used to be drawn for trials
  // anyway -- the one money control a trial could see was one that
  // refused trials -- so it pointed at a wall. Point at the door instead.
  if (!c && tenant && tenant.plan === "trial") {
    note.textContent = "after the trial";
    body.innerHTML =
      `<div class="empty u-p-18-0">
         Contracts start once the account is off the trial.
         <span class="sub">Move onto a paid plan under "What you pay" first
         — then a term can be signed here and invoices issue on their
         own.</span>
       </div>` + behind + opportunities(pipeline, null);
    return;
  }

  if (!c) {
    note.textContent = "not signed";
    body.innerHTML =
      `<div class="empty u-p-18-0">
         Nothing bills itself yet.
         <span class="sub">Sign a term and invoices issue on their own, at
         the rate that was agreed, without anybody remembering to.</span>
       </div>
       <form id="contract-form" class="u-mt-12">
         <div class="row">
           <label class="sr" for="term-years">Term</label>
           <select id="term-years" class="grow u-p-9-11 u-bg-sunken u-bd u-r-9 u-c-ink">
             <option value="1">1 year</option>
             <option value="3" selected>3 years — 5% a year escalator</option>
             <option value="5">5 years — 5% a year escalator</option>
           </select>
           <button class="btn btn-sm btn-primary" type="submit">Sign</button>
         </div>
         <label class="row u-mt-8 u-fs-12 u-c-muted">
           <input type="checkbox" id="term-prepay" class="u-w-auto">
           Pay annually — 10% off, one invoice a year
         </label>
       </form>` + behind + opportunities(pipeline, null);
    wireContractForm();
    return;
  }

  note.textContent = `${c.term_years}-year term · year ${
    c.current_contract_year}`;

  const rows = (contract.schedule || []).map((y) => `
    <tr class="${y.year === c.current_contract_year ? "now" : ""}">
      <td>Year ${y.year}</td>
      <td>${money0(y.monthly_usd)}/mo</td>
      <td>${y.prepay_discount_usd
             ? "−" + money0(y.prepay_discount_usd) : "—"}</td>
      <td>${money0(y.payable_usd)}</td>
    </tr>`).join("");

  const renewal = (pipeline.renewal || {});
  body.innerHTML = `
    <div class="u-fs-12-5 u-c-muted u-lh-1-6">
      ${esc(c.billing_interval === "annual" ? "Billed annually" : "Billed monthly")},
      ${c.escalator_percent}% a year, ${
        c.auto_renew ? "renews automatically" : "does not auto-renew"}.
      Next invoice ${esc((c.next_billing_at || "").slice(0, 10))}.
    </div>
    <table class="term-table">
      <thead><tr><th>Term</th><th>Rate</th><th>Prepay</th><th>Payable</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="card-note u-mt-9">
      Ends ${esc((c.ends_at || "").slice(0, 10))} —
      ${renewal.days_away} days away.
    </div>
    ${behind}
    ${opportunities(pipeline, c)}
    <div class="row u-mt-12">
      <button class="btn btn-sm" id="contract-bill">Bill what is due</button>
      <button class="btn btn-sm" id="contract-renew">Renew</button>
    </div>`;

  wireAddOnButtons(c);

  $("contract-bill").addEventListener("click", async () => {
    try {
      const out = await api("/api/contracts/billing-run", { method: "POST" });
      toast(out.invoices_issued
        ? `${out.invoices_issued} invoice(s) issued.`
        : "Nothing is due yet.", out.invoices_issued ? "ok" : "");
      refresh();
    } catch (err) { toast(err.message, "err"); }
  });
  $("contract-renew").addEventListener("click", async () => {
    try {
      await api("/api/contracts/renew", {
        method: "POST", body: JSON.stringify({ term_years: c.term_years }) });
      toast("Renewed at the rate the last term finished on.", "ok");
      refresh();
    } catch (err) { toast(err.message, "err"); }
  });
}

/* Revenue this estate could produce and does not.
 *
 * This list could say "not buying Loss Assurance, $8,940 a year" from the
 * day it shipped, with nothing anywhere that would let anybody buy it.
 * Every sale had to go through a conversation, which for a company with
 * one founder means most of them never happened. Each priced row is a
 * button now.
 *
 * A coverage gap has no unit count, so it is named rather than priced and
 * has nothing to press. */
function opportunities(pipeline, contract) {
  const rows = (pipeline.opportunities || []).slice(0, 4);
  if (!rows.length) return "";
  const attached = (contract && contract.add_ons) || [];
  return `<div class="u-mt-14">
    <div class="card-note u-mb-4">
      Not on this contract${pipeline.identified_annual_usd
        ? " — " + money(pipeline.identified_annual_usd) + " a year" : ""}
    </div>
    ${rows.map((o) => `<div class="opp">
        <span class="grow">${esc(o.name)}</span>
        <b>${o.annual_usd ? "$" + Math.round(o.annual_usd).toLocaleString()
                            + "/yr" : "—"}</b>
        ${o.kind === "add_on" && contract
          ? `<button class="btn btn-sm" data-buy="${esc(o.key)}"
                     title="Added to the next invoice, never backdated"
             >Add</button>`
          : ""}
      </div>`).join("")}
  </div>`;
}

/* Buying is the whole set, not a delta: two people clicking at once must
   not land on a contract neither of them chose. */
function wireAddOnButtons(contract) {
  for (const button of document.querySelectorAll("[data-buy]")) {
    button.addEventListener("click", async () => {
      const wanted = new Set(contract.add_ons || []);
      wanted.add(button.dataset.buy);
      button.disabled = true;
      try {
        const out = await api("/api/contracts/add-ons", {
          method: "POST",
          body: JSON.stringify({ add_ons: [...wanted] }),
        });
        toast(out.message, "ok");
        refresh();
      } catch (err) {
        button.disabled = false;
        toast(err.message, "err");
      }
    });
  }
}

function wireContractForm() {
  const form = $("contract-form");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/contracts", {
        method: "POST",
        body: JSON.stringify({
          term_years: Number($("term-years").value),
          annual_prepay: $("term-prepay").checked,
        }),
      });
      toast("Signed. Invoices now issue themselves.", "ok");
      refresh();
    } catch (err) { toast(err.message, "err"); }
  });
}

function renderInvoices(ledger) {
  $("invoice-note").textContent = ledger.outstanding_count
    ? `${money(ledger.outstanding_usd)} outstanding`
    : ledger.count ? "all settled" : "none yet";

  if (!ledger.invoices.length) {
    $("invoice-body").innerHTML =
      `<div class="empty u-p-22">
         Nothing billed yet.
         <span class="sub">Issue one and it is frozen at today's figures.</span>
       </div>`;
    return;
  }

  $("invoice-body").innerHTML = ledger.invoices.slice(0, 6).map((inv) => {
    const late = inv.overdue;
    const due = inv.state !== "issued" ? inv.state
      : late ? `${Math.abs(inv.days_until_due)}d overdue`
      : `due in ${inv.days_until_due}d`;
    return `<div class="u-d-flex u-items-center u-gap-9 u-p-8-0 u-bb${inv.state === "void" ? " u-o-5" : ""}">
      <span class="grow">
        <span class="u-ff-mono u-fs-12">${
          esc(inv.number)}</span>
        <span class="u-d-block u-fs-11-5 ${
          late ? "u-c-critical" : "u-c-muted"}">${esc(due)}</span>
      </span>
      <span class="u-tnum">${
        money(inv.total_usd)}</span>
      <button class="btn btn-sm" data-invoice="${esc(inv.invoice_id)}">View</button>
    </div>`;
  }).join("") + (ledger.invoices.length > 6
    ? `<div class="card-note u-mt-8">${
        ledger.invoices.length - 6} older not shown.</div>` : "");
}

const money = (n) => "$" + Number(n || 0).toLocaleString(undefined,
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/* The invoice as a document, in the same drawer and to the same print
   rules as the claim packet — a finance department prints this too. */
async function openInvoice(invoiceId) {
  let inv;
  try { inv = await api(`/api/invoices/${encodeURIComponent(invoiceId)}`); }
  catch (err) { toast(err.message, "err"); return; }

  const rows = inv.lines.map((line) => `<tr>
      <td>${esc(line.description)}</td>
      <td class="num">${esc(line.quantity)}</td>
      <td class="num">${money(line.unit_price_usd)}</td>
      <td class="num">${money(line.amount_usd)}</td>
    </tr>`).join("");

  const status = inv.state === "paid"
    ? `Settled ${stamp(inv.paid_at)}${
        inv.payment_reference ? ` · ${esc(inv.payment_reference)}` : ""}`
    : inv.state === "void" ? `Voided ${stamp(inv.voided_at)}`
    : inv.overdue ? `Overdue by ${Math.abs(inv.days_until_due)} days`
    : `Due ${stamp(inv.due_at)}`;

  $("drawer").innerHTML = `
    <div class="drawer-head">
      <div class="grow">
        <div class="doc-kicker" data-doc="invoice">Invoice</div>
        <div class="drawer-title" id="drawer-title">${esc(inv.number)}</div>
        <div class="drawer-sub">${esc(status)}</div>
      </div>
      <button class="btn btn-sm" id="invoice-print">Print</button>
      <button class="btn btn-sm" id="drawer-close">Close</button>
    </div>

    <div class="doc-issuer">
      <div class="doc-issuer-name">${esc(inv.issued_by.legal_name)}</div>
      ${inv.issued_by.address
        ? `<div>${esc(inv.issued_by.address)}</div>` : ""}
      ${inv.issued_by.tax_id
        ? `<div>Tax ID ${esc(inv.issued_by.tax_id)}</div>` : ""}
      ${inv.issued_by.email
        ? `<div>${esc(inv.issued_by.email)}</div>` : ""}
      ${inv.issued_by.note
        ? `<div class="doc-issuer-warn">${esc(inv.issued_by.note)}</div>` : ""}
    </div>

    <div class="doc-section">Billed to</div>
    <div class="readout">
      <div><span class="k">Company</span><span class="v">${
        esc(inv.billed_to.company_name)}</span></div>
      <div><span class="k">Attention</span><span class="v">${
        esc(inv.billed_to.contact_name)}</span></div>
      <div><span class="k">Email</span><span class="v">${
        esc(inv.billed_to.contact_email)}</span></div>
      ${inv.purchase_order ? `<div><span class="k">Purchase order</span>
        <span class="v">${esc(inv.purchase_order)}</span></div>` : ""}
      ${inv.service_period && inv.service_period.label
        ? `<div><span class="k">Period</span><span class="v">${
            esc(inv.service_period.label)}</span></div>` : ""}
      <div><span class="k">Issued</span><span class="v">${
        esc(stamp(inv.issued_at))}</span></div>
      <div><span class="k">Due</span><span class="v">${
        esc(stamp(inv.due_at))}</span></div>
    </div>

    <div class="doc-section">Charges</div>
    <div class="scrollx"><table>
      <thead><tr><th>Description</th><th class="num">Qty</th>
        <th class="num">Rate</th><th class="num">Amount</th></tr></thead>
      <tbody>${rows}</tbody>
      <tfoot>
      ${inv.subtotal_usd !== inv.total_usd ? `<tr>
        <td colspan="3" class="u-ta-right u-c-muted">Subtotal</td>
        <td class="num">${money(inv.subtotal_usd)}</td></tr>` : ""}
      <tr class="doc-total">
        <td colspan="3" class="u-ta-right u-c-muted">Total${
          inv.amount_paid_usd ? "" : " due"}</td>
        <td class="num u-ff-display u-fs-19">${
          money(inv.total_usd)}</td>
      </tr>
      ${inv.amount_paid_usd ? `<tr>
        <td colspan="3" class="u-ta-right u-c-muted">Received</td>
        <td class="num">-${money(inv.amount_paid_usd)}</td></tr>
      <tr>
        <td colspan="3" class="u-ta-right u-c-muted">Balance due</td>
        <td class="num">${money(Math.max(0, inv.total_usd - inv.amount_paid_usd))}</td></tr>` : ""}
      </tfoot>
    </table></div>

    <div class="card-note u-mt-16">${esc(inv.terms)}</div>
    ${inv.issued_by.remit_to
      ? `<div class="card-note">Remit to: ${esc(inv.issued_by.remit_to)}</div>`
      : ""}
    <div class="doc-footer">CyberLogix AI is a product of MTRX Horizyn.
      ${esc(inv.number)} · issued ${esc(stamp(inv.issued_at))}</div>

    ${inv.state === "issued" ? `<div class="row no-print u-mt-18">
      <button class="btn btn-sm" data-settle="${esc(inv.invoice_id)}">
        Record payment</button>
      <button class="btn btn-sm" data-voidinv="${esc(inv.invoice_id)}">
        Void</button>
    </div>` : ""}
  `;

  showDrawer();
  $("drawer-close").addEventListener("click", closeDrawer);
  $("invoice-print").addEventListener("click", () => window.print());
}

/* ---------------- licences, and who has left ----------------
   The calendar reads down by month because a rota is planned a month at
   a time, and the thing that matters is not "which of these is expired"
   but "what do I have to deal with before the end of the month". */

/* What will be emailed, shown before the calendar that explains it.
   The calendar is the reference; this is the part that goes out whether
   or not anybody opens this page, and an alerter you cannot read back
   is one you have to take on trust. Same function on both sides, so the
   page and the inbox cannot disagree. */

function renderAlertLane(boxId, due, keep, note) {
  const box = $(boxId);
  if (!due) { box.innerHTML = ""; return; }
  const urgent = due.urgent.filter(keep);
  const advisory = due.advisory.filter(keep);
  if (!urgent.length && !advisory.length) {
    box.innerHTML = "";
    return;
  }
  renderLane(box, urgent, advisory, due.sent_to, note);
}

/* Licences on the Licences card, a leaver still being phoned on the
   People card. They arrive from one endpoint because they are one email;
   they are shown apart because renewing a certificate and offboarding
   somebody are not the same afternoon's work. */
/* Each lane says how often its own emails arrive. The endpoint sends one
   sentence covering both, and printed under the leavers it described a
   renewal ladder that has nothing to do with them. */
function renderLicenceAlerts(due) {
  renderAlertLane("licence-alerts", due,
                  (row) => row.kind !== "leaver_on_roster",
                  "A renewal notice goes out once per licence at 60, 30, "
                  + "14, 7 and 1 days out, and once on the day itself. One "
                  + "that has already lapsed is emailed daily for a week, "
                  + "then weekly, until it is renewed.");
}

function renderLeaverAlerts(due) {
  renderAlertLane("leaver-alerts", due,
                  (row) => row.kind === "leaver_on_roster",
                  "Emailed daily for a week, then weekly, until they are "
                  + "off the roster. Taking them off the on-call list is "
                  + "what stops it.");
}

function renderLane(box, urgent, advisory, sentTo, note) {
  /* The badge is the real number of days, never the rung that triggered
     the email: "60d" beside a line reading "expires in 41 days" makes the
     reader work out which of the two to believe, and they will pick the
     wrong one. */
  const badge = (row) =>
    row.kind === "leaver_on_roster" ? "on roster"
    : row.kind === "licence_expired" ? "expired"
    : row.days <= 0 ? "today"
    : `${row.days}d`;

  const rows = (items, loud) => items.map((row) => `<div class="fc-row">
      <span class="fc-when ${loud ? "high" : "medium"}">${
        esc(badge(row))}</span>
      <span class="grow"><span class="sub">${esc(row.line)}</span></span>
    </div>`).join("");

  box.innerHTML =
    `<h3 class="sub-head">Being emailed to ${esc(sentTo)}</h3>`
    + (urgent.length
        ? `<div class="read-out no u-m-0-0-6"><strong>${
            urgent.length} need${urgent.length === 1 ? "s" : ""
          } attention today.</strong></div>` + rows(urgent, true)
        : "")
    + rows(advisory, false)
    + `<div class="sub u-m-8-0-2">${esc(note)}</div>`;
}

/* ---------------- devices knocking ----------------
   The card hides itself when nothing is waiting. A permanent empty box
   headed "Devices knocking" is one more thing to scroll past; a box
   that only appears when something is actually there is a message. */

/* What the card was last drawn for, so a poll that changes nothing
   redraws nothing. Without this the twenty-second refresh rebuilt the
   card under whoever was typing a location into it and threw the words
   away -- the one form on the page somebody types a sentence into. */
let knockSignature = null;

function renderKnocking(seen) {
  const card = $("find-knocking");
  card.hidden = !seen.count;
  if (!seen.count) {
    $("knock-body").innerHTML = "";
    knockSignature = null;
    return;
  }

  $("knock-note").textContent = `${seen.count} waiting`;

  // The serials and whether each still needs a unit. Counts and
  // timestamps are deliberately left out: a device sending another
  // reading is not a reason to wipe the box somebody is typing in.
  const signature = seen.devices
    .map((d) => `${d.serial}:${d.needs_unit ? "u" : ""}`).sort().join("|");
  if (signature === knockSignature) {
    fillKnockIndustries();
    return;
  }
  knockSignature = signature;
  $("knock-body").innerHTML = seen.devices.map((d) => `
    <div class="fc-row u-flex-wrap u-gap-8-10">
      <span class="fc-when medium">${esc(d.times_seen)} sent</span>
      <span class="grow u-minw-170">
        <span class="fc-id">${esc(d.serial)}</span>
        <span class="sub">${esc(d.why)}</span>
      </span>
      <span class="row u-gap-6 u-flex-1-1-100pc">
        <input class="grow" data-knock-where="${esc(d.serial)}"
               placeholder="Where is it? e.g. Back kitchen" maxlength="160"
               aria-label="Where this device is">
        <select data-knock-kind="${esc(d.serial)}"
                aria-label="What it is watching"></select>
        ${d.needs_unit ? `<select data-knock-unit="${esc(d.serial)}"
             aria-label="Which unit">
             <option value="temperature_f">\u00B0F</option>
             <option value="temperature_c">\u00B0C</option>
           </select>` : ""}
        <button class="btn btn-sm btn-primary u-w-auto"
                data-knock-add="${esc(d.serial)}">Add it</button>
        <button class="btn btn-sm" data-knock-drop="${esc(d.serial)}">Not
          mine</button>
      </span>
    </div>`).join("");

  fillKnockIndustries();
}

/* The industry list arrives from its own call, which finishes after the
   first paint. Filling the dropdown separately means the card can be
   drawn before the list exists and corrected the moment it does, rather
   than rendering a select with nothing in it and never coming back. */
function fillKnockIndustries() {
  const options = (state.industries || []).map((row) =>
    `<option value="${esc(row.vertical)}">${esc(row.name)}</option>`).join("");
  if (!options) return;
  document.querySelectorAll("[data-knock-kind]").forEach((select) => {
    if (select.options.length) return;   // never reset a chosen answer
    select.innerHTML = options;
  });
}

document.addEventListener("click", async (event) => {
  const add = event.target.closest("[data-knock-add]");
  const drop = event.target.closest("[data-knock-drop]");
  if (!add && !drop) return;
  const serial = (add || drop).dataset.knockAdd
    || (add || drop).dataset.knockDrop;
  const button = add || drop;
  button.disabled = true;
  try {
    if (drop) {
      await api(`/api/doorstep/${encodeURIComponent(serial)}`,
                { method: "DELETE" });
      toast("Stopped holding it.", "ok");
    } else {
      const where = document.querySelector(
        `[data-knock-where="${CSS.escape(serial)}"]`);
      const kind = document.querySelector(
        `[data-knock-kind="${CSS.escape(serial)}"]`);
      const unit = document.querySelector(
        `[data-knock-unit="${CSS.escape(serial)}"]`);
      if (!where.value.trim()) {
        toast("Say where it is, so an alert can name the place.", "err");
        button.disabled = false;
        return;
      }
      const out = await api("/api/doorstep/adopt", {
        method: "POST",
        body: JSON.stringify({
          serial,
          industry_vertical: kind.value,
          location_name: where.value.trim(),
          ...(unit ? { unit: unit.value } : {}),
        }),
      });
      toast(`Added. ${out.replayed_readings} reading(s) it already sent `
            + "were kept.", "ok");
    }
    renderKnocking(await api("/api/doorstep"));
    refresh().catch(() => {});
  } catch (err) {
    toast(err.message, "err");
    button.disabled = false;
  }
});

/* ---------------- sensor clouds we fetch from ----------------
   The credential is never rendered. Only the header names come back
   from the server, because a console is logged into by an operator who
   should not be able to read the owner's vendor password off it. */

function renderPollers(seen) {
  $("poller-note").textContent = seen.count
    ? (seen.broken ? `${seen.broken} broken` : `${seen.count} connected`)
    : "none yet";

  if (!seen.count) {
    $("poller-body").innerHTML = `<div class="sub">${esc(seen.note)}</div>`;
    return;
  }

  $("poller-body").innerHTML = seen.sources.map((row) => `
    <div class="fc-row">
      <span class="fc-when ${esc(row.broken || row.stopped ? "high"
          : row.last_status === "ok" ? "" : "medium")}">${
        esc(row.stopped ? "stopped" : row.broken ? "broken"
            : row.last_status === "ok" ? "ok"
            : row.last_status || "never asked")}</span>
      <span class="grow">
        <span class="fc-id">${esc(row.name)}</span>
        <span class="sub">${esc(row.readings_ingested)} reading(s) fetched${
          row.last_detail ? ` \u00B7 ${esc(row.last_detail)}` : ""}</span>
      </span>
      <button class="btn btn-sm" data-poll-test="${esc(row.source_id)}">Try
        now</button>
    </div>`).join("")
    + `<div class="sub u-mt-8">${esc(seen.note)}</div>`;
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-poll-test]");
  if (!button) return;
  button.disabled = true;
  try {
    const out = await api(
      `/api/pollers/${encodeURIComponent(button.dataset.pollTest)}/test`,
      { method: "POST" });
    renderPollers(await api("/api/pollers"));
    toast(out.result.detail || out.result.status,
          out.result.status === "ok" ? "ok" : "err");
  } catch (err) {
    toast(err.message, "err");
  } finally {
    button.disabled = false;
  }
});

$("poller-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("poller-hname").value.trim();
  try {
    await api("/api/pollers", {
      method: "POST",
      body: JSON.stringify({
        name: $("poller-name").value.trim(),
        url: $("poller-url").value.trim(),
        interval_minutes: Number($("poller-mins").value) || 10,
        // An empty name with a value, or the reverse, is a header the
        // vendor will reject in a way nobody can read. Send neither.
        headers: name && $("poller-hval").value
          ? { [name]: $("poller-hval").value } : {},
      }),
    });
    ["poller-name", "poller-url", "poller-hname", "poller-hval"]
      .forEach((id) => { $(id).value = ""; });
    renderPollers(await api("/api/pollers"));
    toast("Added. Press Try now to see what comes back.", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
});

/* ---------------- things somebody agreed to do ----------------
   Late first, because a list that needs scrolling to find the fire is
   not a list. Finished work sinks rather than vanishing: ticking
   something off and watching it disappear entirely makes people
   reluctant to tick. */

function renderTasks(seen) {
  $("task-note").textContent = seen.open
    ? (seen.late ? `${seen.late} late` : `${seen.open} to do`)
    : "nothing outstanding";

  if (!seen.count) {
    $("task-body").innerHTML =
      `<div class="sub">Nothing written down yet. Anything somebody
       agreed to do goes here &mdash; it is the difference between a
       meeting and a plan.</div>`;
    return;
  }

  const band = seen.late
    ? `<div class="read-out no u-m-0-0-8">${esc(seen.note)}</div>`
    : "";

  $("task-body").innerHTML = band + seen.tasks.map((row) => `
    <div class="fc-row${row.state === "done" ? " u-o-55" : ""}">
      <span class="fc-when ${esc(row.state === "late" ? "high"
          : row.state === "today" ? "medium" : "")}">${esc(row.when)}</span>
      <span class="grow">
        <span class="fc-id">${esc(row.title)}</span>
        <span class="sub">${esc(row.owner || "nobody named")}${
          row.source ? ` \u00B7 from ${esc(row.source)}` : ""}${
          row.priority === "High" ? " \u00B7 high" : ""}</span>
      </span>
      <button class="btn btn-sm" data-task="${esc(row.task_id)}"
              data-doing="${row.state === "done" ? "reopen" : "done"}">${
        row.state === "done" ? "Undo" : "Done"}</button>
    </div>`).join("");
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-task]");
  if (!button) return;
  button.disabled = true;
  try {
    const out = await api(
      `/api/tasks/${encodeURIComponent(button.dataset.task)}/`
      + button.dataset.doing, { method: "POST" });
    renderTasks(out.outstanding);
  } catch (err) {
    toast(err.message, "err");
    button.disabled = false;
  }
});

$("task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = $("task-title").value.trim();
  if (!title) return;
  try {
    await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({
        title,
        owner: $("task-owner").value.trim(),
        // An empty date input reads as "", which is not a date and not
        // null; sent as-is it fails validation on an otherwise fine form.
        due_on: $("task-due").value || null,
      }),
    });
    $("task-title").value = "";
    $("task-owner").value = "";
    $("task-due").value = "";
    renderTasks(await api("/api/tasks"));
    toast("Written down.", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
});

/* ---------------- who works here ----------------
   The licence calendar below knows these names already, but only as the
   owner of a date. This is the roll: who is on the books, what they do,
   and whether anything they hold has lapsed. */

function renderStaff(book) {
  const box = $("staff-list");
  if (!book || !book.count) {
    box.innerHTML = `<div class="sub">Nobody on the books yet. Add the
      crew and the licence dates follow them.</div>`;
    return;
  }
  const here = book.people.filter((row) => row.active);
  box.innerHTML = `<h3 class="sub-head">On the books (${here.length})</h3>`
    + here.map((row) => {
        const creds = row.credentials || [];
        const lapsed = creds.filter((c) => c.state === "expired").length;
        const soon = creds.filter(
          (c) => c.state === "critical" || c.state === "soon").length;
        return `<div class="fc-row">
          <span class="fc-when ${lapsed ? "high" : soon ? "medium" : ""}">${
            esc(lapsed ? `${lapsed} lapsed` : soon ? `${soon} due` : "clear")
          }</span>
          <span class="grow">
            <span class="fc-id">${esc(row.full_name)}</span>
            <span class="sub">${esc(row.role || "No role recorded")}${
              creds.length ? ` · ${creds.length} licence(s)` : ""}</span>
          </span>
        </div>`;
      }).join("");
}

function renderPeople(book) {
  $("licence-note").textContent = book.credentials
    ? `${book.expiring_within_30_days} within 30 days`
    : "no licences recorded";

  if (!book.credentials) {
    $("people-cal").innerHTML =
      `<div class="sub">No staff licences recorded. A food handler card,
       a CDL, a pharmacy registration &mdash; anything somebody legally
       needs to do the job.</div>`;
    return;
  }

  const band = book.expired_and_blocking
    ? `<div class="read-out no u-m-0-0-10">${esc(book.note)}</div>`
    : "";

  $("people-cal").innerHTML = band + book.by_month.map((month) => {
    const when = new Date(month.month + "-01");
    const label = isNaN(when) ? month.month
      : when.toLocaleDateString([], { month: "long", year: "numeric" });
    return `<div class="sub u-m-12-0-4">${esc(label)}</div>`
      + month.entries.map((row) => `<div class="fc-row">
          <span class="fc-when ${esc(row.state === "expired" ? "high"
              : row.state === "critical" ? "high"
              : row.state === "soon" ? "medium" : "")}">${
            esc(row.days_left < 0 ? `${Math.abs(row.days_left)}d ago`
                                  : `${row.days_left}d`)}</span>
          <span class="grow">
            <span class="fc-id">${esc(row.full_name)}</span>
            <span class="sub">${esc(row.name)}${
              row.stops_them_working ? " · required for the job" : ""}</span>
          </span>
          <span class="fc-trend">${esc(row.expires_on)}</span>
        </div>`).join("");
  }).join("");
}

function renderLeavers(seen) {
  const money = seen.recovery || {};
  const bleeding = money.still_bleeding_monthly_usd || 0;
  $("people-note").textContent = seen.count
    ? (bleeding
        ? `$${bleeding.toLocaleString()} a month still going out`
        : `${seen.count} person(s) have left`)
    : "nobody has left";
  if (!seen.count) {
    $("leavers").innerHTML = "";
    return;
  }
  const cash = (n) => "$" + Number(n || 0).toLocaleString(undefined,
    { minimumFractionDigits: 0, maximumFractionDigits: 0 });

  /* Three figures that people run together and must not. Money still
     going out is happening now. Money already spent is mostly gone.
     Money clawed back is the only one anybody gets to celebrate, and
     the only one our share is taken from. */
  const headline = bleeding
    ? `<div class="read-out no u-m-0-0-10">
         <strong>${cash(bleeding)} a month</strong> is still being paid for
         people who no longer work here &mdash; ${cash(
           money.still_bleeding_yearly_usd)} a year if nobody stops it.
       </div>`
    : `<div class="sub u-m-0-0-10">Nothing is still being paid
         for anybody who has left.</div>`;

  const ledger = `<div class="readout">
      <div><span class="k">Still going out, monthly</span>
           <span class="v">${cash(bleeding)}</span></div>
      <div><span class="k">Paid out since they left</span>
           <span class="v">${cash(money.wasted_to_date_usd)}</span></div>
      <div><span class="k">Already stopped, monthly</span>
           <span class="v">${cash(money.stopped_monthly_usd)}</span></div>
      <div><span class="k">Actually refunded</span>
           <span class="v">${cash(money.refunded_usd)}</span></div>
      <div><span class="k">Our share (${money.our_share_percent || 15}% of
           refunds only)</span>
           <span class="v">${cash(money.our_share_usd)}</span></div>
    </div>`;

  const urgent = seen.still_reachable_by_alerts;
  $("leavers").innerHTML =
    `<h3 class="sub-head">People who have left</h3>`
    + headline
    + (urgent ? `<div class="read-out no">${esc(seen.note)}</div>` : "")
    + seen.departures.map((row) => `<div class="fc-row">
        <span class="fc-when ${esc(row.monthly_usd ? "high"
            : row.still_on_the_roster ? "high" : "")}">${
          esc(row.monthly_usd ? cash(row.monthly_usd) + "/mo"
              : row.still_on_the_roster ? "on roster" : "clear")}</span>
        <span class="grow">
          <span class="fc-id">${esc(row.full_name)}</span>
          <span class="sub">left ${esc(row.left_on)}${
            row.still_running_count
              ? ` · ${row.still_running_count} thing(s) still being paid for`
              : " · nothing still being paid for"}${
            row.wasted_usd ? ` · ${cash(row.wasted_usd)} gone` : ""}</span>
        </span>
      </div>`).join("")
    + ledger
    + `<div class="sub u-m-8-0-2">${esc(money.note || "")}</div>`;
}

/* ---------------- sensors you already own ----------------
   The simulator above sends our field names, which is what the endpoint
   used to require and why "point your existing sensor at this URL" was
   not true. This reads the vendor's own shape instead. */

function renderReading(read) {
  const box = $("shape-out");
  if (!read.understood) {
    box.className = "read-out no";
    box.innerHTML = `<strong>Not enough to go on.</strong>
      <div class="sub">Missing ${esc(read.missing.join("; "))}.</div>
      <div class="sub">Fields seen: ${esc(
        (read.every_field_seen || []).slice(0, 12).join(", "))}</div>`;
    return;
  }
  const used = read.fields_used || {};
  box.className = "read-out ok";
  box.innerHTML = `<strong>Understood${
      read.preset ? " as " + esc(read.preset) : " by looking at the fields"
    }.</strong>
    <dl>
      <dt>Serial</dt><dd>${esc(read.serial)} &nbsp;<span class="sub">${
        esc(used.serial || "")}</span></dd>
      <dt>Reading</dt><dd>${esc(read.value)} &nbsp;<span class="sub">${
        esc(used.value || "")}</span></dd>
      <dt>Unit</dt><dd>${esc(read.unit || "not stated")}</dd>
    </dl>
    ${read.note ? `<div class="sub u-mt-6">${
      esc(read.note)}</div>` : ""}
    ${read.checked_against ? `<div class="sub u-mt-6">
      This preset was checked against ${esc(read.checked_against)}.</div>` : ""}`;
}

function renderByodSamples(seen) {
  if (!seen.count) {
    $("byod-samples").innerHTML =
      `<div class="sub">Nothing has reached the open endpoint yet. A device
       that never appears here never reached us at all, which is a
       different problem from one that arrives and is not understood.</div>`;
    return;
  }
  $("byod-samples").innerHTML = seen.samples.map((row) => `
    <div class="fc-row">
      <span class="fc-when ${row.understood ? "" : "medium"}">${
        esc(row.understood ? "read" : "not read")}</span>
      <span class="grow">
        <span class="fc-id">${esc(row.serial || "no serial found")}</span>
        <span class="sub">${esc(shortDate(row.at))}${
          row.value === null || row.value === undefined
            ? "" : " · " + esc(row.value)}</span>
      </span>
    </div>`).join("");
}

/* ---------------- the installed app ----------------
   Registration, and the one thing an offline monitoring app must never
   do: imply that what it is showing is current. */

function registerWorker() {
  if (!("serviceWorker" in navigator)) return;
  // Registered after load so it never competes with the first render --
  // the estate is the page, and the install is a convenience.
  addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" })
      .catch(() => { /* an app that cannot install still works */ });
  });
}

/* Whether this page is showing live data or a memory of it.
   `navigator.onLine` is famously optimistic -- it reports a connection
   to a router that reaches nothing -- so the real signal is whether the
   last refresh actually completed. */
let lastGoodLoad = null;

function showConnection(live, detail) {
  const banner = $("offline-banner");
  // A banner is read; a dashboard is glanced at. Somebody looking at
  // their phone sees "BREACHING NOW 1" in 40px type and acts on it,
  // whatever a paragraph above it says -- so the figures themselves go
  // quiet when they are no longer current.
  document.body.toggleAttribute("data-stale", !live);
  if (live) {
    lastGoodLoad = new Date();
    banner.hidden = true;
    banner.classList.remove("warn");
    return;
  }
  banner.hidden = false;
  banner.classList.add("warn");
  const since = lastGoodLoad
    ? `Last loaded ${shortDate(lastGoodLoad.toISOString())}.`
    : "Nothing has loaded yet.";
  banner.textContent =
    "Not live. " + since + " Nothing on this screen is current, so nothing "
    + "here means your assets are fine. Monitoring and escalation run on "
    + "the server and are unaffected"
    + (detail ? " (" + detail + ")." : ".");
}

addEventListener("offline", () => showConnection(false, "this device is offline"));
// Back online: look now, and poll from the short interval again rather
// than waiting out whatever back-off the outage had built up.
addEventListener("online", () => {
  if (!state.token) return;
  refresh().then(() => { markFresh(); startPolling(); }).catch(() => {});
});

/* A date a person reads, not an instant a machine wrote. */
function shortDate(value) {
  if (!value) return "unknown";
  const at = new Date(value);
  return isNaN(at) ? String(value) : at.toLocaleString([], {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/* ---------------- what breaks next ----------------
   Ranked by how soon, because that is the order somebody would walk to
   them in. A forecast with no hours attached is not actionable, so the
   ones the model cannot call are summarised rather than listed. */
function renderForecast(fleet) {
  const risky = (fleet.at_risk || []).slice().sort((a, b) => {
    const ah = a.hours_until_breach, bh = b.hours_until_breach;
    if (ah === null) return 1;
    if (bh === null) return -1;
    return ah - bh;
  });
  const tally = fleet.risk_tally || {};
  const unknown = tally.unknown || 0;

  $("forecast-note").textContent = risky.length
    ? `${risky.filter((f) => !(f.pattern
        && ["defrost", "door"].includes(f.pattern.pattern))).length
       } in the next ${fleet.window_hours}h`
    : `${fleet.sensors_analysed} analysed`;

  if (!risky.length && !fleet.sensors_analysed) {
    $("forecast-body").innerHTML =
      `<div class="empty u-p-14">
         Nothing to forecast yet.
         <span class="sub">Once a ${esc(noun(1))} has a few hours of readings,
         anything drifting the wrong way shows up here first.</span>
         ${nextSteps([["sensor", `Add a ${noun(1)}`]])}
       </div>`;
    return;
  }
  if (!risky.length) {
    $("forecast-body").innerHTML =
      `<div class="empty u-p-14">
         Nothing is trending out of band.
         <span class="sub">${esc(fleet.sensors_analysed)} sensor(s) modelled
         over ${esc(fleet.window_hours)} hours${
           unknown ? `; ${esc(unknown)} without enough history yet` : ""}.</span>
       </div>`;
    return;
  }

  /* A defrost crosses the line on purpose, twice a day, and the slope
     is confidently wrong about it every time. Split rather than hidden:
     the reading is real and somebody may want to see it, but it must
     not sit in a list headed "heading for trouble" looking like a unit
     about to fail. Three of those and nobody reads the fourth. */
  const routine = risky.filter((f) => f.pattern
      && (f.pattern.pattern === "defrost" || f.pattern.pattern === "door"));
  const faults = risky.filter((f) => !routine.includes(f));

  const row = (f, muted) => {
    const when = f.hours_until_breach === null
      ? "trend unclear"
      : f.hours_until_breach < 1
        ? `in under an hour`
        : `in about ${Math.round(f.hours_until_breach)}h`;
    // The forecast measures drift in Fahrenheit; a Celsius account reads
    // it in Celsius, or "+1.8°/h" is a degree an hour it is not.
    const perHour = f.trend_f_per_hour === null ? null
      : state.unit === "C" ? Math.round(f.trend_f_per_hour * 5 / 9 * 1000) / 1000
      : f.trend_f_per_hour;
    const trend = perHour === null ? "" :
      `${perHour > 0 ? "+" : ""}${perHour}°${state.unit || "F"}/h`;
    const shape = f.pattern || {};
    return `<div class="fc-row${muted ? " u-o-62" : ""}" data-forecast-sensor="${esc(f.sensor_id)}">
      <span class="fc-when ${esc(muted ? "" : f.risk_level)}">${
        esc(muted ? shape.pattern : when)}</span>
      <span class="grow">
        <span class="fc-id">${esc(f.sensor_id)}</span>
        <span class="sub">${esc(f.location_name)} · ${
          esc(muted ? shape.because : f.likely_catastrophe)}</span>
      </span>
      <span class="fc-trend">${esc(trend)}</span>
    </div>`;
  };

  $("forecast-body").innerHTML =
    (faults.length
      ? faults.map((f) => row(f, false)).join("")
      : `<div class="sub u-pb-6">Nothing is drifting
           in a way that looks like a fault.</div>`)
    + (routine.length
      ? `<h3 class="sub-head">Warm, but this is what it always does</h3>`
        + routine.map((f) => row(f, true)).join("")
        + `<div class="sub u-mt-6">Equipment defrosts on a
             timer and doors get opened. These are above the line and we
             are not calling anybody about them.</div>`
      : "")
    + (unknown ? `<div class="sub u-pt-8">${esc(unknown)
        } sensor(s) have too little history to model yet.</div>` : "");
}

/* ---------------- do I throw this away? ----------------
   The number on the screen is not the question a manager is asking at
   7am with service starting. This is.

   It never says food is safe, and the caveat is rendered with the
   answer rather than under it: this is a screen read at speed, and a
   disclaimer somebody has to scroll to is one that does not exist. */

const FOOD_BAND = {
  in_range: "", cool_it_down: "medium", use_now: "medium",
  discard: "high", still_warm: "high",
};

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-food]");
  if (!button) return;
  const sensorId = button.dataset.food;
  const box = document.querySelector(`[data-food-for="${CSS.escape(sensorId)}"]`);
  if (!box) return;
  button.disabled = true;
  try {
    const out = await api(
      `/api/disposition/${encodeURIComponent(sensorId)}?hours=24`);
    box.innerHTML = `
      <div class="read-out u-mt-10 ${esc(FOOD_BAND[out.band] === "high" ? "no" : "")}">
        <strong>${esc(out.headline)}</strong>
        <div class="sub u-mt-6">${esc(out.detail)}</div>
        <div class="sub u-mt-8 u-o-85">${
          esc(out.not_a_ruling)}</div>
      </div>`;
  } catch (err) {
    box.innerHTML = `<div class="sub u-mt-8">${
      esc(err.message)}</div>`;
  } finally {
    button.disabled = false;
  }
});

/* ---------------- what could be claimed ----------------
   A packet could always be produced for an incident. Nothing said which
   incidents one could be produced for, so using the feature meant
   already knowing the answer. */
function renderClaims(eligible) {
  if (!eligible.count) {
    $("claims-body").innerHTML =
      `<div class="sub">Nothing claimable in the last ${
        esc(eligible.period_days)} days.</div>`;
    return;
  }
  $("claims-body").innerHTML =
    `<div class="sub u-mb-8">${esc(eligible.count)
      } incident(s) a packet can be produced for:</div>`
    + eligible.incidents.map((i) => `<div class="fc-row">
        <span class="grow">
          <span class="fc-id">${esc(i.sensor_id)}</span>
          <span class="sub">${esc(shortDate(i.opened_at))} · ${
            esc(i.catastrophe)} · ${esc(i.state)}${
            i.readings_available ? "" : " · no readings kept"}</span>
        </span>
        <button class="btn btn-sm" data-packet="${esc(i.incident_id)}"
                type="button">Packet</button>
      </div>`).join("");
}

/* ---------------- who a real alert reaches ----------------
   The roster is what was configured. This is what the dispatcher would
   do with it, which is not always the same thing -- a contact with both
   channels muted is on the roster and reaches nobody. */
function renderEscalation(preview) {
  const texted = preview.sms_recipients || [];
  const ladder = preview.voice_ladder || [];
  const names = (rows) => rows.map((r) => esc(r.name)).join(", ");

  $("escalation-body").innerHTML = `
    <div class="sub u-bt u-pt-10">
      <strong>A real alert right now.</strong><br>
      Text to ${texted.length ? names(texted) : "nobody"}.
      ${ladder.length
        ? "Then it calls " + names(ladder) + ", stopping at whoever answers."
        : "No call ladder, so an unanswered text is the end of it."}
    </div>
    ${(preview.warnings || []).map((w) =>
      `<div class="card-note u-mt-8" role="alert">`
      + `<span class="badge s-warning"><span class="dot"></span>Gap</span> `
      + `${esc(w)}</div>`).join("")}`;
}

/* ---------------- claim packet ---------------- */

/* Rendered in the drawer as the document it is, because the person who
   will read it is a loss adjuster, and a JSON dump is why claims get
   reduced. Printable straight from here. */
async function openPacket(incidentId) {
  let packet;
  try {
    packet = await api(
      `/api/claims/${encodeURIComponent(incidentId)}/packet`, { method: "POST" });
  } catch (err) { toast(err.message, "err"); return; }

  state.packet = packet;
  state.drawerSensor = null;

  const ev = packet.event;
  const loc = packet.location;
  const evidence = packet.evidence;

  const timeline = packet.response.timeline.map((step) => `<li>
      <b>${esc(step.event)}</b>${step.detail ? " — " + esc(step.detail) : ""}
      <span class="when">${esc(stamp(step.at))}</span>
    </li>`).join("");

  const deliveries = packet.response.sms_deliveries
    .concat(packet.response.voice_deliveries);
  const deliveryRows = deliveries.length
    ? deliveries.map((d) => `<tr>
        <td>${esc(d.channel)}</td>
        <td>${esc(d.recipient || "—")}</td>
        <td>${esc(d.to)}</td>
        <td>${d.delivered ? "delivered" : esc(d.status || "not sent")}</td>
        <td>${esc(d.provider_reference || "—")}</td>
      </tr>`).join("")
    : `<tr><td colspan="5">No delivery was attempted.</td></tr>`;

  const excursions = evidence.readings.filter((r) => r.breached);
  const readingRows = evidence.readings.map((r) => `<tr>
      <td>${esc(stamp(r.at))}</td>
      <td class="num${r.breached ? " u-c-critical" : ""}">${
        esc(r.temperature)}°${esc(ev.temperature_unit)}</td>
      <td class="num">${r.humidity === null ? "—" : esc(r.humidity) + "%"}</td>
      <td>${r.breached ? "out of band" : "in band"}</td>
    </tr>`).join("");

  $("drawer").innerHTML = `
    <div class="drawer-head">
      <div class="grow">
        <div class="doc-kicker">Loss event evidence packet</div>
        <div class="drawer-title" id="drawer-title">${esc(ev.incident_id)}</div>
        <div class="drawer-sub">${esc(packet.insured.company_name)} ·
          issued ${esc(stamp(packet.issued_at))}</div>
      </div>
      <button class="btn btn-sm" id="packet-print">Print</button>
      <button class="btn btn-sm" id="drawer-close">Close</button>
    </div>

    ${packet.cover_letter ? `<div class="doc-letter">
      <div class="quote-label">Covering summary${
        packet.cover_letter_source === "fallback_template"
          ? " (template)" : ""}</div>
      ${esc(packet.cover_letter)}
    </div>` : ""}

    <div class="doc-section">Insured and location</div>
    <div class="readout">
      <div><span class="k">Insured</span><span class="v">${
        esc(packet.insured.company_name)}</span></div>
      <div><span class="k">Contact</span><span class="v">${
        esc(packet.insured.contact_name)} · ${
        esc(packet.insured.contact_phone)}</span></div>
      <div><span class="k">Site</span><span class="v">${
        esc(loc.site || "not recorded")}</span></div>
      <div><span class="k">Asset</span><span class="v">${
        esc(loc.asset)} (${esc(loc.asset_type)})</span></div>
      <div><span class="k">Sector</span><span class="v">${
        esc(loc.industry)}</span></div>
    </div>

    <div class="doc-section">The event</div>
    <div class="readout">
      <div><span class="k">Detected</span><span class="v">${
        esc(stamp(ev.opened_at))}</span></div>
      <div><span class="k">Reading at detection</span><span class="v">${
        esc(ev.detected_reading)}°${esc(ev.temperature_unit)}</span></div>
      <div><span class="k">Breach</span><span class="v">${
        esc(ev.breach_details)}</span></div>
      <div><span class="k">Probable cause</span><span class="v">${
        esc(ev.probable_cause)}</span></div>
      <div><span class="k">Acknowledged after</span><span class="v">${
        ev.minutes_to_acknowledge === null
          ? "not acknowledged"
          : esc(ev.minutes_to_acknowledge) + " min"}</span></div>
    </div>

    <div class="doc-section">Response</div>
    <ul class="timeline">${timeline}</ul>
    <div class="scrollx"><table>
      <thead><tr><th>Channel</th><th>Recipient</th><th>Number</th>
        <th>Outcome</th><th>Reference</th></tr></thead>
      <tbody>${deliveryRows}</tbody>
    </table></div>

    <div class="doc-section">Recorded evidence</div>
    <div class="readout">
      <div><span class="k">Window</span><span class="v">${
        esc(stamp(evidence.window_start))} — ${
        esc(stamp(evidence.window_end))}</span></div>
      <div><span class="k">Readings</span><span class="v">${
        esc(evidence.readings_in_window)}</span></div>
      <div><span class="k">Out of band</span><span class="v">${
        esc(evidence.excursions_in_window)}</span></div>
      <div><span class="k">Peak</span><span class="v">${
        evidence.peak_reading === null ? "—"
          : esc(evidence.peak_reading) + "°" + esc(ev.temperature_unit)}</span></div>
    </div>
    ${excursions.length ? "" : `<div class="card-note u-mt-8">
       No reading in this window was outside the band.</div>`}
    <div class="scrollx u-maxh-280 u-ovy-auto"><table>
      <thead><tr><th>When</th><th class="num">Reading</th>
        <th class="num">Humidity</th><th>State</th></tr></thead>
      <tbody>${readingRows}</tbody>
    </table></div>

    <div class="doc-section">Prior incidents on this asset</div>
    ${packet.prior_incidents_90_days.length
      ? `<ul class="timeline">${packet.prior_incidents_90_days.map((p) =>
          `<li><b>${esc(p.incident_id)}</b> — ${esc(p.detail)}
            <span class="when">${esc(stamp(p.opened_at))}</span></li>`).join("")}
         </ul>
         <div class="card-note">Disclosed rather than omitted: an adjuster
           finds these anyway, and a packet that hides them is worth less
           than one that does not.</div>`
      : `<div class="card-note">None in the preceding 90 days.</div>`}

    <div class="doc-section">Verification</div>
    <div class="digest">
      <span class="digest-label">Chain head at issue</span>
      <code>${esc(packet.attestation ? packet.attestation.chain_head : "—")}</code>
    </div>
    <div class="card-note u-mt-8">${esc(packet.verification)}</div>
    <div class="card-note">${esc(packet.signing.note)}</div>
  `;

  showDrawer();
  $("drawer-close").addEventListener("click", closeDrawer);
  $("packet-print").addEventListener("click", () => window.print());
}

function stamp(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString([], {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

/* ---------------- the inspector's log ----------------
   One click: the log for the chosen dates opens as a document and the
   print dialog comes up over it (print or save as PDF). On paper only the
   document prints -- theme.css re-sets it for ink, one unit to a section,
   and repeats the table headings on each page. */

function isoDay(date) {
  return date.toISOString().slice(0, 10);
}

function initInspectorForm() {
  const to = $("inspector-to");
  const from = $("inspector-from");
  if (!to || !from) return;
  const today = new Date();
  to.value = isoDay(today);
  from.value = isoDay(new Date(today.getTime() - 6 * 86400000));
  $("inspector-form").addEventListener("submit", (event) => {
    event.preventDefault();
    openInspectorLog(from.value, to.value, true);
  });
}

/* A link straight to the log for given dates, as the monthly summary
   email carries: #inspector-log?business=TEN-1&from=2026-10-01&to=2026-10-31.
   It fills the form and shows the card. When the link is about another of
   this person's businesses, that business is opened first (the server
   decides whether it is theirs); the reload keeps the link, so it lands. */
const DAY = /^\d{4}-\d{2}-\d{2}$/;

function inspectorLink() {
  const found = /^#inspector-log\?(.*)$/.exec(location.hash || "");
  if (!found) return null;
  const params = new URLSearchParams(found[1]);
  const from = params.get("from") || "";
  const to = params.get("to") || "";
  if (!DAY.test(from) || !DAY.test(to)) return null;
  return { business: params.get("business") || "", from, to };
}

async function followInspectorLink() {
  const link = inspectorLink();
  if (!link || !state.data || !state.data.tenant) return;
  if (link.business && link.business !== state.data.tenant.tenant_id) {
    try {
      const opened = await api(
        `/api/accounts/businesses/${encodeURIComponent(link.business)}/switch`,
        { method: "POST" });
      landIn(opened.token);
      return;
    } catch (_) {
      history.replaceState(null, "", location.pathname + location.search);
      toast("That log is for a business this sign-in cannot open.", "err");
      return;
    }
  }
  // Followed once: a reload of the console is a reload, not the link again.
  history.replaceState(null, "", location.pathname + location.search);
  $("inspector-from").value = link.from;
  $("inspector-to").value = link.to;
  finder.tab = "proof";
  applyFinder();
  const card = $("find-inspector-log");
  if (card) card.scrollIntoView({ block: "center" });
}

// A link followed while the console is already open changes only the hash.
window.addEventListener("hashchange", () => {
  if (state.token) followInspectorLink().catch(() => { /* the estate is the page */ });
});

function dayLabel(day, withYear) {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString([], Object.assign(
    { weekday: "short", day: "numeric", month: "short" },
    withYear ? { year: "numeric" } : {}));
}

async function openInspectorLog(from, to, print) {
  let log;
  const btn = $("inspector-print");
  if (btn) btn.disabled = true;
  try {
    log = await api(`/api/autopilot/inspector-log?start=${encodeURIComponent(from)}`
                    + `&end=${encodeURIComponent(to)}`);
  } catch (err) { toast(err.message, "err"); return; }
  finally { if (btn) btn.disabled = false; }

  const u = log.temperature_unit;
  const deg = (v) => (v === null || v === undefined ? "—" : `${esc(v)}°${esc(u)}`);
  const limits = (unit) => [
    unit.limit_below !== null ? `not below ${deg(unit.limit_below)}` : "",
    unit.limit_above !== null ? `not above ${deg(unit.limit_above)}` : "",
  ].filter(Boolean).join(", ");

  const sections = log.units.map((unit) => {
    const days = unit.days.length
      ? unit.days.map((d) => `<tr${d.out_of_range ? ' class="log-out"' : ""}>
          <td>${esc(dayLabel(d.day))}</td>
          <td class="num">${deg(d.low)}</td>
          <td class="num">${deg(d.high)}</td>
          <td class="num">${esc(d.readings)}</td>
          <td class="num">${d.out_of_range
            ? `${esc(d.hours_out_of_range)} h` : "—"}</td>
        </tr>`).join("")
      : `<tr><td colspan="5">No readings held for these dates.</td></tr>`;
    const breaches = unit.breaches.length
      ? `<div class="scrollx"><table class="log-table">
          <thead><tr><th>When</th><th class="num">Worst</th>
            <th>Acknowledged by</th><th>What was done</th><th>Reviewed</th>
            <th>Closed</th></tr></thead>
          <tbody>${unit.breaches.map((b) => `<tr>
            <td>${esc(stamp(b.opened_at))}</td>
            <td class="num">${deg(b.worst)}</td>
            <td>${b.acknowledged_by
              ? `${esc(b.acknowledged_by)}<span class="log-sub">${
                  esc(stamp(b.acknowledged_at))}${b.minutes_to_acknowledge !== null
                  ? ` · after ${esc(b.minutes_to_acknowledge)} min` : ""}</span>`
              : `<strong>Nobody</strong>`}</td>
            <td>${b.corrective_action
              ? `${esc(b.corrective_action)}${b.product_disposition
                  ? `<span class="log-sub">Product: ${esc(b.product_disposition)}</span>` : ""}`
              : `<strong>Not recorded</strong>`}</td>
            <td>${esc(b.reviewed_by || "—")}</td>
            <td>${b.closed_at ? esc(stamp(b.closed_at)) : "still open"}</td>
          </tr>`).join("")}</tbody>
        </table></div>`
      : `<div class="card-note">${unit.out_of_range_readings
          ? "Some readings were out of range, but no alert was opened for "
            + "them: a routine defrost is not alerted."
          : "It stayed in range the whole time."}</div>`;
    return `<section class="log-unit">
      <h3 class="log-unit-name">${esc(unit.location_name)}${
        unit.site_name ? ` <span class="log-sub-inline">· ${esc(unit.site_name)}</span>` : ""}</h3>
      <div class="log-meta">${esc(unit.sensor_id)} · ${esc(unit.industry_name)} · limit ${
        limits(unit) || "none set"} · ${esc(unit.readings)} readings${
        unit.in_range_percent !== null ? `, ${esc(unit.in_range_percent)}% in range` : ""}</div>
      <div class="scrollx"><table class="log-table log-days">
        <thead><tr><th>Day</th><th class="num">Lowest</th><th class="num">Highest</th>
          <th class="num">Readings</th><th class="num">Out of range</th></tr></thead>
        <tbody>${days}</tbody>
      </table></div>
      <div class="log-breach-head">Times it went out of range</div>
      ${breaches}
    </section>`;
  }).join("");

  $("drawer").innerHTML = `
    <div class="drawer-head">
      <div class="grow">
        <div class="doc-kicker">Temperature log</div>
        <div class="drawer-title" id="drawer-title">${esc(log.company_name)}</div>
        <div class="drawer-sub">${esc(dayLabel(log.period_start, true))} to ${
          esc(dayLabel(log.period_end, true))} · temperatures in °${esc(u)}</div>
      </div>
      <button class="btn btn-sm" id="log-print">Print</button>
      <button class="btn btn-sm" id="drawer-close">Close</button>
    </div>
    <div class="readout">
      <div><span class="k">Units</span><span class="v">${esc(log.units.length)}</span></div>
      <div><span class="k">Times out of range</span><span class="v">${esc(log.breaches)}</span></div>
      <div><span class="k">Not acknowledged</span><span class="v">${esc(log.unanswered)}</span></div>
    </div>
    ${log.covers_the_whole_period ? "" : `<div class="read-out no u-mt-10">${esc(log.caveat)}</div>`}
    ${sections || `<div class="card-note u-mt-10">No units yet.</div>`}
    <div class="card-note u-mt-16">${esc(log.note)} Made ${esc(stamp(log.generated_at))}
      by CyberLogix AI, a product of MTRX Horizyn.</div>
  `;
  state.drawerSensor = null;
  showDrawer();
  $("drawer-close").addEventListener("click", closeDrawer);
  $("log-print").addEventListener("click", () => window.print());
  if (print) setTimeout(() => window.print(), 50);
}

/* ---------------- compliance vault ---------------- */

function renderVault(attestation) {
  const held = attestation || state.attestation;
  if (!held) {
    $("vault-note").textContent = "ready";
    $("vault-body").innerHTML =
      `<div class="card-note">
         Every reading is chained: altering one changes every digest after
         it. Issue an attestation to hand an insurer or an auditor a figure
         they can check without trusting either of us.
       </div>`;
    return;
  }

  state.attestation = held;
  $("vault-note").textContent =
    `${held.readings.toLocaleString()} readings sealed`;
  $("vault-body").innerHTML = `
    <div class="readout">
      <div><span class="k">Period</span><span class="v">${
        esc(held.period_days)} days</span></div>
      <div><span class="k">${Noun(2)} attested</span><span class="v">${
        esc(held.sensors)}</span></div>
      <div><span class="k">Within tolerance</span><span class="v">${
        held.within_band_percent === null ? "—"
          : esc(held.within_band_percent) + "%"}</span></div>
      <div><span class="k">Counter-signed</span><span class="v">${
        held.signing.counter_signed ? "yes" : "no"}</span></div>
    </div>
    <div class="digest" title="The one figure that fixes this whole record">
      <span class="digest-label">Estate digest</span>
      <code>${esc(held.estate_digest)}</code>
    </div>
    ${held.signing.counter_signed ? "" :
      `<div class="card-note u-mt-8">
         ${esc(held.signing.note)}
       </div>`}`;
}

/* ---------------- sector standing ---------------- */

/* Heavier than the other panels — it walks every operator in the cohort —
   so it is fetched once and refreshed on demand rather than every poll. */
async function loadBenchmarks(overview) {
  if (state.benchmarkLoaded) return;
  state.benchmarkLoaded = true;

  const verticals = {};
  (overview.sensors || []).forEach((s) => {
    verticals[s.industry_vertical] = (verticals[s.industry_vertical] || 0) + 1;
  });
  const dominant = Object.entries(verticals).sort((a, b) => b[1] - a[1])[0];
  if (!dominant) {
    $("bench-note").textContent = "";
    $("bench-body").innerHTML =
      `<div class="card-note">Nothing to compare yet.</div>`;
    return;
  }

  try {
    renderBenchmark(await api(
      `/api/benchmarks/${encodeURIComponent(dominant[0])}`));
  } catch (err) {
    $("bench-body").innerHTML =
      `<div class="card-note">${esc(err.message)}</div>`;
  }
}

function renderBenchmark(body) {
  if (!body.available) {
    $("bench-note").textContent = "not yet published";
    $("bench-body").innerHTML =
      `<div class="card-note">${esc(body.reason)}</div>`;
    return;
  }

  $("bench-note").textContent = `${body.cohort_size} operators`;
  const you = body.you;
  if (!you) {
    $("bench-body").innerHTML =
      `<div class="card-note">
         No readings of your own in this period to compare.
       </div>`;
    return;
  }

  const rows = [
    ["Excursion rate", `${you.excursion_rate_percent}%`,
     body.cohort.excursion_rate_percent, you.standing.excursion_rate],
    ["Minutes to acknowledge",
     you.minutes_to_acknowledge === null ? "—" : you.minutes_to_acknowledge,
     body.cohort.minutes_to_acknowledge, you.standing.minutes_to_acknowledge],
    ["Unit uptime", null,
     body.cohort.unit_uptime_percent, you.standing.uptime],
  ];

  $("bench-body").innerHTML = rows.map(([label, mine, dist, standing]) => {
    if (!dist) return "";
    const grade = standing ? standing.reading : "—";
    const cls = grade.startsWith("top") ? "good"
              : grade.startsWith("above") ? ""
              : "warn";
    return `<div class="stand">
      <div class="stand-top">
        <span class="grow">${esc(label)}</span>
        <span class="pill ${cls}">${esc(grade)}</span>
      </div>
      <div class="stand-scale">
        <span>best ${esc(dist.best_quartile)}</span>
        <span>median ${esc(dist.median)}</span>
        <span>worst ${esc(dist.worst_quartile)}</span>
      </div>
      ${mine !== null
        ? `<div class="stand-mine">You: <b>${esc(mine)}</b></div>` : ""}
    </div>`;
  }).join("") + `<div class="card-note u-mt-10">
      ${esc(body.privacy)}
    </div>`;
}

/* ---------------- sites ---------------- */

function renderSites(d) {
  const sites = d.sites || [];
  const unplaced = d.summary.unplaced_sensors || 0;
  $("site-note").textContent = sites.length
    ? `${sites.length} location${sites.length === 1 ? "" : "s"}`
    : "none yet";

  if (!sites.length) {
    $("site-body").innerHTML =
      `<div class="empty u-p-14">
         No sites yet. Group sensors by location and alerts reach the manager
         who can actually walk to the freezer.
         ${nextSteps([["site", "Add your first site"]])}
       </div>`;
    return;
  }

  const rows = sites.map((site) => {
    const breaching = (d.sensors || []).filter(
      (s) => s.site_id === site.site_id && s.breaching).length;
    return `<div class="u-d-flex u-items-center u-gap-9 u-p-7-0 u-bb">
      <span class="grow">
        <span class="u-fs-13 u-fw-500">${esc(site.name)}</span>
        <span class="u-d-block u-fs-11-5 u-c-muted">
          ${esc(site.sensor_count)} sensor${site.sensor_count === 1 ? "" : "s"} ·
          ${esc(site.online)} online${breaching ? ` · ${breaching} breaching` : ""}
        </span>
      </span>
      <button class="btn btn-sm" data-site-view="${esc(site.site_id)}">View</button>
      <button class="btn btn-sm" data-site-remove="${esc(site.site_id)}">Remove</button>
    </div>`;
  }).join("");

  $("site-body").innerHTML = rows + (unplaced
    ? `<div class="card-note u-mt-9">
         ${unplaced} sensor${unplaced === 1 ? " is" : "s are"} not at a site yet.
         Billing counts them; a per-store report does not.
       </div>`
    : "");
}

/* ---------------- alert channels ---------------- */

const HOOK_NAMES = { slack: "Slack", teams: "Teams",
                     pagerduty: "PagerDuty", generic: "JSON" };

function renderHooks(hooks) {
  $("hook-note").textContent = hooks.count
    ? `${hooks.count} connected` : "none yet";

  if (!hooks.webhooks.length) {
    $("hook-body").innerHTML =
      `<div class="empty u-p-14">
         Nothing connected. A breach that only shows up on a dashboard is a
         breach nobody sees until morning.
       </div>`;
    return;
  }

  $("hook-body").innerHTML = hooks.webhooks.map((h) => {
    const failing = h.consecutive_failures >= 3;
    const last = h.last_status
      ? `${h.last_status}${failing ? ` · failing (${h.consecutive_failures})` : ""}`
      : "never fired";
    return `<div class="u-d-flex u-items-center u-gap-9 u-p-7-0 u-bb${h.active ? "" : " u-o-5"}">
      <span class="grow">
        <span class="u-fs-13 u-fw-500">
          ${esc(HOOK_NAMES[h.kind] || h.kind)}${h.label ? " · " + esc(h.label) : ""}
        </span>
        <span class="u-d-block u-fs-11-5 ${failing ? "u-c-critical" : "u-c-muted"}">
          ${esc(h.target)} · ${esc(last)}${h.active ? "" : " · paused"}
        </span>
      </span>
      <button class="btn btn-sm" data-hook-test="${esc(h.webhook_id)}">Test</button>
      <button class="btn btn-sm" data-hook-remove="${esc(h.webhook_id)}">Remove</button>
    </div>`;
  }).join("");
}

/* ---------------- sensor drill-down ---------------- */

async function openDrawer(sensorId, options = {}) {
  let detail;
  try {
    detail = await api(`/api/console/sensor/${encodeURIComponent(sensorId)}`);
  } catch (err) {
    // A quiet refresh of a sensor that has since been retired must not
    // shout at the operator; an explicit click must.
    if (!options.quiet) toast(err.message, "err");
    closeDrawer();
    return;
  }

  state.drawerSensor = sensorId;
  const s = detail.sensor;
  const unit = detail.temperature_unit;
  const f = detail.forecast;

  const rows = detail.readings.slice().reverse().slice(0, 24).map((r) => `<tr>
      <td>${esc(new Date(r.at).toLocaleString([], { month: "short", day: "numeric",
              hour: "2-digit", minute: "2-digit" }))}</td>
      <td class="num${r.breached ? " u-c-critical u-fw-650" : ""}">
        ${esc(r.temperature)}°${esc(unit)}</td>
      <td class="num">${r.humidity === null ? "—" : esc(r.humidity) + "%"}</td>
      <td>${r.breached ? "out of band" : "in band"}</td>
    </tr>`).join("");

  $("drawer").innerHTML = `
    <div class="drawer-head">
      <div class="grow">
        <div class="drawer-title" id="drawer-title">${esc(s.sensor_id)}</div>
        <div class="drawer-sub">${esc(s.location_name)}${
          s.site_name ? " · " + esc(s.site_name) : ""} · ${esc(s.industry_name)}</div>
      </div>
      <button class="btn btn-sm" id="drawer-close">Close</button>
    </div>

    <div class="readout">
      <div><span class="k">Now</span><span class="v">${
        s.last_temperature_display === null || s.last_temperature_display === undefined
          ? "—" : esc(s.last_temperature_display) + "°" + esc(unit)}</span></div>
      <div><span class="k">Safe band</span><span class="v">${
        s.danger_below_display !== null && s.danger_below_display !== undefined
          ? esc(s.danger_below_display) + " to " : "under "}${
        esc(s.danger_above_display)}°${esc(unit)}</span></div>
      <div><span class="k">Link</span><span class="v">${
        esc(s.online ? "online" : "offline")}</span></div>
      <div><span class="k">Battery</span><span class="v">${
        s.battery_percent === null || s.battery_percent === undefined
          ? "not reported" : esc(s.battery_percent) + "%"
            + (s.battery_low ? " — replace it" : "")}</span></div>
      <div><span class="k">Signal</span><span class="v">${
        s.signal_percent === null || s.signal_percent === undefined
          ? "not reported" : esc(s.signal_percent) + "%"}</span></div>
      <div><span class="k">Readings held</span><span class="v">${
        esc(detail.readings_total)}</span></div>
      ${f ? `<div><span class="k">Forecast</span><span class="v">${
        esc(f.risk_level)}${f.hours_until_breach ? ` · ${esc(f.hours_until_breach)}h`
        : ""}</span></div>` : ""}
    </div>
    ${s.online ? "" : `<details class="offline-box" id="offline-box" open>
      <summary>It is offline. What to check</summary>
      <div id="offline-box-body"><p class="card-plain">Loading the steps…</p></div>
    </details>`}

    <div class="card-head u-mt-18">
      <span class="card-title">Recent history</span>
      <span class="card-note">${esc(detail.readings.length)} points ·
        ${esc(detail.breached_count)} out of band</span>
    </div>
    ${historyChart(detail)}
    <div class="scrollx"><table>
      <thead><tr><th>When</th><th class="num">Reading</th>
        <th class="num">Humidity</th><th>State</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="4">No readings yet.</td></tr>`}</tbody>
    </table></div>

    <div class="card-head u-mt-18">
      <span class="card-title">Problems</span>
      <span class="card-note">${esc(detail.incidents.length)} total ·
        ${esc(detail.open_incidents)} open</span>
    </div>
    <p class="card-plain">Something went out of its safe range. These are the ones that still need dealing with.</p>
    ${detail.incidents.length
      ? detail.incidents.map((i) => `<article class="incident st-${esc(i.state)}">
          <div class="incident-top">
            <span class="incident-title grow">${esc(i.catastrophe_type)}</span>
            <span class="card-note">${esc(i.state)}</span>
          </div>
          ${incidentTimeline(i)}
        </article>`).join("")
      : `<div class="empty">No incidents on this sensor.</div>`}
  `;

  showDrawer();
  $("drawer-close").addEventListener("click", closeDrawer);
  if (!s.online) paintOfflineHelp();
}

/* What to check when a sensor has gone quiet. The steps are the help
   page's own list (helpdesk.py), fetched once, so the drawer, the help
   page and the docs say the same thing. */
async function paintOfflineHelp() {
  const body = $("offline-box-body");
  if (!body) return;
  try {
    state.offlineHelp = state.offlineHelp || await api("/api/help/sensor-offline");
    const help = state.offlineHelp;
    body.innerHTML = `<p class="card-plain">${esc(help.summary)}</p>
      <ol class="offline-steps">${help.steps.map((step) => `<li><strong>${
        esc(step.title)}${/\?$/.test(step.title) ? "" : "."}</strong> ${
        esc(step.detail)}</li>`).join("")}</ol>
      <a class="offline-help" href="/help#sensor-offline">Open the help page</a>`;
  } catch (err) {
    body.innerHTML = `<p class="card-plain">The steps are on the
      <a href="/help#sensor-offline">help page</a>.</p>`;
  }
}

/* ---------------- your businesses ----------------
   One sign-in, several businesses. The company chip in the nav is the
   switcher: it opens this panel, which lists every business this person
   can open with what needs them there, and adds another. Switching swaps
   the session for one on the other business and reloads, so nothing from
   one business's page is left painted while the next one loads. */

function businessStatus(b) {
  if (b.removed) return { kind: "unknown", text: "You no longer have access" };
  if (b.licence === "suspended") return { kind: "unknown", text: "Licence suspended" };
  const parts = [];
  if (b.open_alerts) parts.push(`${b.open_alerts} open alert${b.open_alerts === 1 ? "" : "s"}`);
  if (b.sensors_offline) parts.push(`${b.sensors_offline} unit${b.sensors_offline === 1 ? "" : "s"} offline`);
  if (b.licence === "grace") parts.push(`trial ended, ${b.grace_days_left} day${b.grace_days_left === 1 ? "" : "s"} of monitoring left`);
  if (b.licence === "lapsed") parts.push("no longer monitored");
  if (parts.length) return { kind: b.open_alerts || b.licence === "lapsed" ? "critical" : "elevated", text: parts.join(" · ") };
  return { kind: "low", text: b.sensors ? "All clear" : "No units yet" };
}

/* The chip: the business you are in, and a count of what needs you in the
   others, so trouble somewhere else is not invisible from here. */
function paintBusinessChip(list) {
  state.businesses = list || null;
  const badge = $("biz-badge");
  if (!list || list.count < 2) { badge.hidden = true; return; }
  const elsewhere = list.businesses.filter((b) => !b.current && b.can_open && b.needs_attention);
  badge.hidden = !elsewhere.length;
  badge.textContent = String(elsewhere.length);
  badge.title = elsewhere.length
    ? `${elsewhere.length} of your other businesses need${elsewhere.length === 1 ? "s" : ""} you`
    : "";
}

async function openBusinesses() {
  let list;
  try {
    list = await api("/api/accounts/businesses");
  } catch (err) {
    toast(err.message, "err");
    return;
  }
  paintBusinessChip(list);
  const t = list.totals;
  const rows = list.businesses.map((b) => {
    const st = businessStatus(b);
    const action = b.current
      ? `<span class="card-note">You are here</span>`
      : b.can_open
        ? `<button class="btn btn-sm btn-primary" type="button" data-open-biz="${esc(b.tenant_id)}">Open</button>`
        : "";
    return `<article class="biz-row${b.current ? " is-current" : ""}">
        <div class="grow">
          <div class="biz-name">${esc(b.company_name)}</div>
          <div class="biz-meta">${b.industry ? esc(b.industry) + " · " : ""}you are ${esc(b.role)}
            · ${b.sensors} unit${b.sensors === 1 ? "" : "s"}</div>
          <div class="biz-state">${badge(st.kind, st.text)}</div>
        </div>
        ${action}
      </article>`;
  }).join("");

  const sectorOptions = (state.industries || [])
    .map((i) => `<option value="${esc(i.vertical)}">${esc(i.name)}</option>`).join("");
  const addForm = list.can_add ? `
      <form id="biz-add" class="biz-add">
        <div class="card-title">Add a business</div>
        <p class="biz-meta">It starts on the free trial, under the terms you already
          agreed to. You become its owner, and it keeps its own units, alerts, team
          and bills, separate from your other businesses.</p>
        <div class="field"><label for="biz-name">Business name</label>
          <input id="biz-name" required maxlength="200" autocomplete="organization"></div>
        <div class="field"><label for="biz-sector">Sector</label>
          <select id="biz-sector">${sectorOptions}</select></div>
        <button class="btn btn-primary" type="submit">Add and open it</button>
      </form>`
    : `<p class="biz-meta">${list.businesses.length >= list.max_businesses
        ? `You have the most businesses one sign-in can hold (${list.max_businesses}).`
        : "Only an owner can add a business. Open one you own to add another."}</p>`;

  // Joining somebody else's business needs a code its owner made, entered
  // here from your own account: both of you have to agree. An owner makes
  // codes for the business that is open now.
  const here = list.businesses.find((b) => b.current);
  const joinForm = `
      <form id="biz-join" class="biz-add">
        <div class="card-title">Join a business</div>
        <p class="biz-meta">Got a join code from a business's owner? Enter it to add
          that business here. It works once.</p>
        <div class="field"><label for="biz-code">Join code</label>
          <input id="biz-code" required minlength="8" maxlength="120" autocomplete="off"></div>
        <button class="btn" type="submit">Join</button>
      </form>`;
  const codeForm = here && here.role === "owner" ? `
      <form id="biz-invite" class="biz-add">
        <div class="card-title">Let someone join ${esc(here.company_name)}</div>
        <p class="biz-meta">For somebody who already has a CyberLogix sign-in. Make a
          code, give it to them yourself, and they add this business from their own
          account. It works once and lasts three days.</p>
        <div class="field"><label for="biz-role">Their role here</label>
          <select id="biz-role"><option value="viewer">Viewer</option>
            <option value="operator" selected>Operator</option>
            <option value="owner">Owner</option></select></div>
        <button class="btn" type="submit">Make a join code</button>
        <code class="biz-key" id="biz-joincode" hidden></code>
        <div id="biz-codes"></div>
      </form>` : "";

  $("drawer").innerHTML = `
    <div class="drawer-head">
      <div class="grow">
        <div class="drawer-title" id="drawer-title">Your businesses</div>
        <div class="drawer-sub">Signed in as ${esc(list.person.full_name)}. Each business
          keeps its own data, and your role in it.</div>
      </div>
      <button class="btn btn-sm" id="drawer-close">Close</button>
    </div>
    <p class="biz-totals">${list.count} business${list.count === 1 ? "" : "es"}
      · ${t.open_alerts} open alert${t.open_alerts === 1 ? "" : "s"}
      · ${t.sensors_offline} unit${t.sensors_offline === 1 ? "" : "s"} offline</p>
    <div class="biz-list">${rows}</div>
    ${addForm}
    ${joinForm}
    ${codeForm}
    <div id="biz-made" hidden></div>
  `;
  showDrawer();
  $("drawer-close").addEventListener("click", closeDrawer);
  $("drawer").querySelectorAll("[data-open-biz]").forEach((btn) => {
    btn.addEventListener("click", () => switchBusiness(btn.dataset.openBiz, btn));
  });
  const form = $("biz-add");
  if (form) {
    const sector = state.data && state.data.tenant && state.data.tenant.industry_vertical;
    if (sector) $("biz-sector").value = sector;
    form.addEventListener("submit", addBusiness);
  }
  $("biz-join").addEventListener("submit", joinBusiness);
  const invite = $("biz-invite");
  if (invite) {
    invite.addEventListener("submit", makeJoinCode);
    $("biz-codes").addEventListener("click", revokeJoinCode);
    showJoinCodes();
  }
}

/* The codes this business has made that nobody has used yet, each with a
   way to withdraw it: given to the wrong person, or they are not coming. */
async function showJoinCodes() {
  const box = $("biz-codes");
  if (!box) return;
  let list;
  try { list = await api("/api/accounts/businesses/join-codes"); }
  catch (_) { box.innerHTML = ""; return; }
  box.innerHTML = list.codes.length ? `
    <div class="biz-meta">Waiting to be used</div>
    ${list.codes.map((c) => `
      <div class="row u-p-5-0 u-bb">
        <span class="grow u-fs-12-5">${esc(c.role)} · made ${esc(stamp(c.issued_at))}
          <span class="u-d-block u-fs-11-5 u-c-muted">until ${esc(stamp(c.expires_at))}</span>
        </span>
        <button class="btn btn-sm" type="button" data-revoke-code="${esc(c.code_id)}">Withdraw</button>
      </div>`).join("")}` : "";
}

async function revokeJoinCode(event) {
  const btn = event.target.closest("[data-revoke-code]");
  if (!btn) return;
  btn.disabled = true;
  try {
    const done = await api(
      `/api/accounts/businesses/join-codes/${encodeURIComponent(btn.dataset.revokeCode)}`,
      { method: "DELETE" });
    toast(done.message, "ok");
  } catch (err) {
    toast(err.message, "err");
  }
  showJoinCodes();
}

async function joinBusiness(event) {
  event.preventDefault();
  const btn = event.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const joined = await api("/api/accounts/businesses/join", {
      method: "POST",
      body: JSON.stringify({ code: $("biz-code").value.trim() }),
    });
    toast(joined.message, "ok");
    openBusinesses();
  } catch (err) {
    toast(err.message, "err");
    btn.disabled = false;
  }
}

async function makeJoinCode(event) {
  event.preventDefault();
  const btn = event.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const made = await api("/api/accounts/businesses/join-codes", {
      method: "POST",
      body: JSON.stringify({ role: $("biz-role").value }),
    });
    const box = $("biz-joincode");
    box.textContent = made.code;
    box.hidden = false;
    toast("Shown once. Give it to them yourself.", "ok");
    showJoinCodes();
  } catch (err) {
    toast(err.message, "err");
  } finally {
    btn.disabled = false;
  }
}

/* A new session for the other business, then a clean page. */
function landIn(token) {
  try { localStorage.setItem(TOKEN_STORAGE, token); } catch (_) {}
  location.reload();
}

async function switchBusiness(tenantId, btn) {
  if (btn) btn.disabled = true;
  try {
    const opened = await api(`/api/accounts/businesses/${encodeURIComponent(tenantId)}/switch`, {
      method: "POST",
    });
    landIn(opened.token);
  } catch (err) {
    toast(err.message, "err");
    if (btn) btn.disabled = false;
  }
}

async function addBusiness(event) {
  event.preventDefault();
  const btn = event.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const made = await api("/api/accounts/businesses", {
      method: "POST",
      body: JSON.stringify({
        company_name: $("biz-name").value.trim(),
        industry_vertical: $("biz-sector").value || null,
      }),
    });
    // The old session ended when this one began, so the panel can only
    // move forward. The sensor key is shown once, here, as sign-up does.
    event.target.hidden = true;
    const box = $("biz-made");
    box.innerHTML = `
      <div class="card-title">${esc(made.tenant.company_name)} is ready</div>
      <p class="biz-meta">Its sensor key, for plugging units in. It is shown once:
        copy it somewhere safe now.</p>
      <code class="biz-key" id="biz-key"></code>
      <div class="biz-actions">
        <button class="btn btn-sm" type="button" id="biz-copy">Copy key</button>
        <button class="btn btn-sm btn-primary" type="button" id="biz-go">Open ${esc(made.tenant.company_name)}</button>
      </div>`;
    $("biz-key").textContent = made.api_key;
    box.hidden = false;
    $("drawer-close").onclick = () => landIn(made.token);
    $("biz-go").addEventListener("click", () => landIn(made.token));
    $("biz-copy").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(made.api_key); toast("Key copied.", "ok"); }
      catch (_) { toast("Select the key and copy it by hand.", "err"); }
    });
  } catch (err) {
    toast(err.message, "err");
    btn.disabled = false;
  }
}

$("chip-company").addEventListener("click", openBusinesses);

function showDrawer() {
  // Remembered so focus can go back where it came from, rather than
  // dumping a keyboard user at the top of the page on close.
  if (!document.body.classList.contains("drawer-open")) {
    state.focusReturn = document.activeElement;
  }
  $("drawer").hidden = false;
  $("scrim").hidden = false;
  document.body.classList.add("drawer-open");
  const close = $("drawer-close");
  if (close) close.focus();
}

function closeDrawer() {
  const wasOpen = !$("drawer").hidden;
  state.drawerSensor = null;
  state.packet = null;
  $("drawer").hidden = true;
  $("scrim").hidden = true;
  document.body.classList.remove("drawer-open");
  if (wasOpen && state.focusReturn && state.focusReturn.isConnected) {
    state.focusReturn.focus();
  }
  state.focusReturn = null;
}

/* Tab must not escape an open panel: a modal a keyboard user can walk out
   of behind is not modal. */
document.addEventListener("keydown", (event) => {
  if (event.key !== "Tab" || $("drawer").hidden) return;
  const focusable = $("drawer").querySelectorAll(
    'a[href], button:not(:disabled), input, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

/* A full-width line of the last hundred-odd readings, with the safe band
   drawn behind it: a number out of band is obvious, a list of them is not. */
function historyChart(detail) {
  const values = detail.readings.map((r) => r.temperature);
  if (values.length < 2) {
    return `<div class="empty">Not enough readings to plot yet.</div>`;
  }

  const W = 520, H = 150, PAD = 8;
  const above = detail.sensor.danger_above_display;
  const below = detail.sensor.danger_below_display;
  const candidates = values.concat(
    [above, below].filter((v) => v !== null && v !== undefined));
  const min = Math.min(...candidates), max = Math.max(...candidates);
  const span = (max - min) || 1;
  const x = (i) => PAD + (i * (W - PAD * 2)) / (values.length - 1);
  const y = (v) => H - PAD - ((v - min) / span) * (H - PAD * 2);

  const bandTop = above !== null && above !== undefined ? y(above) : PAD;
  const bandBottom = below !== null && below !== undefined ? y(below) : H - PAD;

  const line = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const dots = detail.readings.map((r, i) => r.breached
    ? `<circle cx="${x(i).toFixed(1)}" cy="${y(r.temperature).toFixed(1)}" r="2.6"
               fill="var(--critical)"/>` : "").join("");

  return `<svg class="history" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"
       role="img" aria-label="Reading history for ${esc(detail.sensor.sensor_id)}">
    <rect x="0" y="${bandTop.toFixed(1)}" width="${W}"
          height="${Math.max(0, bandBottom - bandTop).toFixed(1)}"
          fill="rgba(126,154,114,0.09)"/>
    <polyline points="${line}" fill="none" stroke="var(--accent)" stroke-width="2"
              stroke-linejoin="round" stroke-linecap="round"/>
    ${dots}
  </svg>`;
}

function renderTeam(team) {
  $("team-note").textContent = `${team.count} with access`;
  $("team-body").innerHTML = team.users.map((u) => `
    <div class="u-d-flex u-items-center u-gap-9 u-p-7-0 u-bb${u.disabled ? " u-o-5" : ""}">
      <span class="grow">
        <span class="u-fs-13 u-fw-500">${esc(u.full_name)}</span>
        <span class="u-d-block u-fs-11-5 u-c-muted">
          ${esc(u.email)} · ${esc(u.role)}${u.disabled ? " · disabled" : ""}
        </span>
      </span>
      ${u.disabled || u.user_id === (state.user && state.user.user_id)
        ? ""
        : `<button class="btn btn-sm" data-reset-user="${esc(u.user_id)}">Reset</button>`}
    </div>`).join("")
    // The business's master key, for scripts and devices without their own
    // key. Owners can replace it the day it turns up somewhere it should not.
    + (state.user && state.user.role === "owner" ? `
    <div class="u-d-flex u-items-center u-gap-9 u-p-10-0-2">
      <span class="grow u-fs-12 u-c-muted">
        Master key leaked, or someone left who had it?</span>
      <button class="btn btn-sm" data-rekey-business="1">Replace master key</button>
    </div>` : "");
}

/* Only the sectors this estate actually has sensors in: a health inspector
   sheet for a company with no restaurants is noise. */
function renderShortcuts(sensors, catalogue) {
  const owned = [...new Set(sensors.map((s) => s.industry_vertical))];
  const rows = catalogue.shortcuts.filter((s) => owned.includes(s.vertical));

  if (!rows.length) {
    $("shortcut-list").innerHTML =
      `<div class="empty u-p-14">
         Register a sensor and its sector's document appears here.
         ${nextSteps([["sensor", `Add a ${noun(1)}`]])}
       </div>`;
    return;
  }

  $("shortcut-list").innerHTML = rows.map((s) => `
    <div class="u-p-8-0 u-bb">
      <div class="u-d-flex u-gap-9 u-items-center">
        <span class="grow u-fs-13 u-fw-500">
          ${esc(s.shortcut_name)}</span>
        <button class="btn btn-sm" data-shortcut="${esc(s.vertical)}">Generate</button>
      </div>
      <div class="u-fs-11-5 u-c-muted u-mt-3">
        ${esc(s.description)}
      </div>
    </div>`).join("");
}

function renderByod(sensors) {
  const bound = sensors.find((s) => s.external_device_sn);
  const serial = bound ? bound.external_device_sn : "YOUR-DEVICE-SERIAL";
  $("byod-snippet").textContent =
`POST ${location.origin}/api/v1/bridge/sensor-webhook-ingest
Content-Type: application/json

{
  "device_sn": "${serial}",
  "api_key_token": "clx_…",
  "reading_value": 7.0,
  "metric_type": "temperature_c"
}`;
}

/* ---------------- the first day ----------------
   An empty console used to say what was missing and stop there. Every
   empty card now ends with the button for the next thing to do, and the
   checklist at the top says which of the four first things are done --
   as the server counts them, never as this page guesses. */

/* Where each next step lives: the card, and the field to put the cursor in. */
const GO = {
  site:   { card: "find-sites", field: "site-name" },
  sensor: { card: "find-add-to-the-estate", field: "add-loc" },
  oncall: { card: "find-on-call-roster", field: "ct-name" },
  test:   { card: "find-on-call-roster", field: "test-alert" },
};
const GO_LABEL = {
  site: "Add a site", sensor: "Add one", oncall: "Add someone", test: "Send one",
};

/* One or more next-step buttons, for the bottom of an empty card. Each is
   [where, label, primary?]. */
function nextSteps(steps) {
  if (state.user && state.user.role === "viewer") {
    return `<span class="sub">An owner or operator can set this up.</span>`;
  }
  return `<span class="empty-acts">${steps.map(([key, label, primary]) =>
    `<button class="btn btn-sm${primary ? " btn-go" : ""}" type="button"
             data-go="${esc(key)}">${esc(label)}</button>`).join("")}</span>`;
}

function goTo(key) {
  if (key === "simulate") { simAdd(); return; }
  const where = GO[key];
  const card = where && $(where.card);
  if (!card) return;
  if (card.hidden) {
    // On another tab, or behind a search: bring the whole page back first.
    finder.tab = "all";
    finder.query = "";
    $("find-q").value = "";
    applyFinder();
  }
  card.scrollIntoView({ behavior: "smooth", block: "start" });
  const field = $(where.field);
  if (field) setTimeout(() => field.focus({ preventScroll: true }), 350);
}

function renderFirstDay(fd) {
  const card = $("first-day");
  const canAct = Boolean(state.user && state.user.role !== "viewer");
  // Gone once all four are done, and never shown to somebody who could
  // not do any of them, or on a wall.
  if (!fd || fd.complete || !canAct || state.wall) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  $("first-day-note").textContent = `${fd.done} of ${fd.total} done`;
  $("first-day-list").innerHTML = fd.steps.map((step) => `
    <li class="${step.done ? "is-done" : ""}">
      <span class="tick" aria-hidden="true">${step.done ? "&#10003;" : ""}</span>
      <span class="grow"><b>${esc(step.label)}</b>
        <span class="sub">${esc(step.detail)}</span></span>
      ${step.done
        ? `<span class="sr">done</span>`
        : `<button class="btn btn-sm" type="button" data-go="${esc(step.key)}">${
            esc(GO_LABEL[step.key] || "Do it")}</button>`}
    </li>`).join("");
}

/* A new sensor's first reading, shown when it lands rather than on the
   next twenty-second poll. Only the overview is asked for, only while a
   registered unit has never reported, and for ten minutes at most. */
function watchForFirstReading() {
  clearInterval(state.firstWatch);
  const until = Date.now() + 10 * 60 * 1000;
  const waiting = () => ((state.data && state.data.sensors) || [])
    .filter((s) => s.last_temperature_display === null
                || s.last_temperature_display === undefined)
    .map((s) => s.sensor_id);
  if (!waiting().length) return;
  state.firstWatch = setInterval(async () => {
    const before = waiting();
    if (!before.length || Date.now() > until || !state.token) {
      clearInterval(state.firstWatch);
      return;
    }
    try {
      const fresh = await api("/api/console/overview");
      const landed = fresh.sensors.some((s) => before.includes(s.sensor_id)
        && s.last_temperature_display !== null
        && s.last_temperature_display !== undefined);
      if (landed) {
        await refresh();
        toast("First reading in.", "ok");
      }
    } catch (_) { /* the ordinary poll reports trouble */ }
  }, 2500);
}

/* A name for a unit nobody named: the location, made into an id, with a
   short random tail so two kitchens' walk-ins cannot collide. */
function sensorIdFrom(location) {
  const stem = location.toUpperCase().replace(/[^A-Z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "").slice(0, 28) || "UNIT";
  const bytes = new Uint8Array(2);
  crypto.getRandomValues(bytes);
  return `${stem}-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0"))
    .join("").toUpperCase()}`;
}

/* ---------------- the test alert ---------------- */

$("test-alert").addEventListener("click", async () => {
  const button = $("test-alert");
  const call = $("test-call").checked && !$("test-call-wrap").hidden;
  if (!confirm("Text everyone on the roster now"
      + (call ? ", and ring the first person" : "") + "?\n\n"
      + "The message starts with TEST and says nothing is wrong. No problem "
      + "is opened, nothing escalates, and it counts towards today's text "
      + "allowance.")) return;
  button.disabled = true;
  try {
    const out = await api("/api/contacts/test-alert", {
      method: "POST", body: JSON.stringify({ call }),
    });
    $("test-alert-out").innerHTML = `<div class="test-result">
        <p>${esc(out.message)}</p>
        <ul>${out.results.map((r) => `<li>
          ${badge(r.delivered ? "low" : (r.status === "not_configured" ? "unknown" : "critical"),
                  r.channel === "voice" ? "call" : "text")}
          ${esc(r.name)} <span class="sub">${esc(r.phone)} &middot; ${esc(
            r.delivered ? "handed to the network" : r.status === "not_configured"
              ? "written, not sent (dry run)" : r.detail || r.status)}</span></li>`).join("")}
        </ul></div>`;
    toast("Test alert done. No problem was opened.", "ok");
    await refresh();
  } catch (err) {
    $("test-alert-out").innerHTML =
      `<div class="test-result"><p class="s-warning">${esc(err.message)}</p></div>`;
  } finally {
    button.disabled = false;
  }
});

/* ---------------- the simulator ----------------
   Pretend units for a demo, the owner's only. The server keeps them in
   memory and nowhere else: never a reading, an incident, a seat or a
   line in the vault. Every card says so on its face. */

function simCard(u) {
  const temp = (u.last_temperature_display === null
                || u.last_temperature_display === undefined)
    ? "—" : u.last_temperature_display.toFixed(1);
  const above = u.danger_above_display, below = u.danger_below_display;
  const would = u.would_alert;
  return `<article class="sensor is-simulated ${u.breaching ? "is-breaching" : ""}">
      <div class="sim-stamp">SIMULATED &middot; not a real reading, not recorded</div>
      <div class="sensor-top">
        <span class="sensor-id grow">${esc(u.sensor_id)}</span>
        ${badge(u.breaching ? "critical" : "low", u.breaching ? "would alert" : "simulated")}
      </div>
      <div class="sensor-loc">${esc(u.location_name)} &middot; ${esc(u.industry_name)}</div>
      <div class="sensor-read">
        <span class="sensor-temp">${esc(temp)}</span>
        <span class="sensor-unit">°${esc(u.temperature_unit)}</span>
      </div>
      <div class="spark-wrap">${sparkline(u)}</div>
      <div class="sensor-meta">
        <span>Simulated</span>
        <span>limit ${below !== null && below !== undefined
          ? esc(below) + "–" : "&lt; "}${esc(above)}°${esc(u.temperature_unit)}</span>
      </div>
      ${would ? `<div class="sim-would">${esc(would.text)}
        <span class="sub">Not sent. A real breach would text ${
          esc(would.recipients.join(", ") || "the contact on the account")}.</span></div>` : ""}
      <div class="sensor-actions">
        ${u.broken
          ? `<button class="btn btn-sm" type="button" data-sim-fix="${esc(u.sensor_id)}">Fix it</button>`
          : `<button class="btn btn-sm" type="button" data-sim-break="${esc(u.sensor_id)}">Make it fail</button>`}
        <button class="btn btn-sm" type="button" data-sim-remove="${esc(u.sensor_id)}">Remove</button>
      </div>
    </article>`;
}

function renderSims(body) {
  const units = (body && body.units) || [];
  state.sims = units;
  const zone = $("sim-zone");
  zone.hidden = !units.length;
  if (!units.length) {
    clearInterval(state.simTimer);
    state.simTimer = null;
    return;
  }
  $("sim-note").textContent = `${units.length} of ${body.max_units}`;
  $("sim-notice").textContent = body.notice;
  $("sim-add").hidden = units.length >= body.max_units;
  $("sim-fleet").innerHTML = units.map(simCard).join("");
  if (!state.simTimer) {
    state.simTimer = setInterval(loadSims,
      Math.max(2000, (body.tick_seconds || 4) * 1000));
  }
}

async function loadSims() {
  if (!state.token || !state.user || state.user.role !== "owner") {
    renderSims(null);
    return;
  }
  try { renderSims(await api("/api/simulator")); }
  catch (_) { /* a demo that cannot load is not worth a toast */ }
}

async function simAdd() {
  try {
    await api("/api/simulator/units", { method: "POST", body: "{}" });
    await loadSims();
    $("sim-zone").scrollIntoView({ behavior: "smooth", block: "start" });
    toast("Simulated unit added. Nothing it does is recorded or sent.", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
}

async function simAct(path, method, okMessage) {
  try {
    await api("/api/simulator" + path, { method });
    await loadSims();
    if (okMessage) toast(okMessage, "ok");
  } catch (err) {
    toast(err.message, "err");
  }
}

$("sim-start").addEventListener("click", simAdd);
$("sim-stop").addEventListener("click", () => simAct("", "DELETE",
  "Simulation stopped. There was nothing to clean up."));

/* ---------------- actions ---------------- */

async function act(fn, okMessage) {
  try {
    await fn();
    if (okMessage) toast(okMessage, "ok");
    await refresh();
  } catch (err) {
    toast(err.message, "err");
  }
}

document.addEventListener("click", (event) => {
  // A wall display has nothing to press. The way out has its own handler.
  if (state.wall) return;
  // The sensor id itself opens the drawer, and it is a span, not a button.
  const opener = event.target.closest("[data-detail]");
  if (opener) {
    openDrawer(opener.dataset.detail);
    return;
  }

  const btn = event.target.closest("button");
  if (!btn) return;

  if (btn.dataset.siteView) {
    state.site = btn.dataset.siteView;
    $("fleet-site").value = state.site;
    reapplyFleet();
    $("fleet").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  if (btn.dataset.siteRemove) {
    act(() => api(`/api/sites/${encodeURIComponent(btn.dataset.siteRemove)}`, {
      method: "DELETE",
    }), "Site removed. Its sensors are still registered.");
  }

  if (btn.dataset.hookTest) {
    act(async () => {
      const out = await api(
        `/api/webhooks/${encodeURIComponent(btn.dataset.hookTest)}/test`,
        { method: "POST" });
      if (!out.delivered) throw new Error(out.message);
      return out;
    }, "Test posted. Check the channel.");
  }

  if (btn.dataset.hookRemove) {
    act(() => api(`/api/webhooks/${encodeURIComponent(btn.dataset.hookRemove)}`, {
      method: "DELETE",
    }), "Alert channel removed.");
  }

  if (btn.dataset.invoice) {
    openInvoice(btn.dataset.invoice);
    return;
  }

  if (btn.dataset.settle) {
    const reference = prompt(
      "Payment reference (wire number, cheque, processor id):");
    if (!reference) return;
    act(() => api(`/api/invoices/${encodeURIComponent(btn.dataset.settle)}/paid`, {
      method: "POST", body: JSON.stringify({ reference }),
    }), "Payment recorded.");
    closeDrawer();
    return;
  }

  if (btn.dataset.voidinv) {
    if (!confirm("Void this invoice? The number is never reissued.")) return;
    act(() => api(`/api/invoices/${encodeURIComponent(btn.dataset.voidinv)}/void`, {
      method: "POST",
    }), "Invoice voided.");
    closeDrawer();
    return;
  }

  if (btn.dataset.packet) {
    openPacket(btn.dataset.packet);
    return;
  }

  if (btn.dataset.ack) {
    act(() => api(`/api/voice/acknowledge/${encodeURIComponent(btn.dataset.ack)}`, {
      method: "POST", body: JSON.stringify({}),
    }), "Acknowledged. Escalation halted.");
  }

  if (btn.dataset.resolve) {
    act(() => api(`/api/voice/resolve/${encodeURIComponent(btn.dataset.resolve)}`, {
      method: "POST", body: JSON.stringify({}),
    }), "Incident resolved.");
  }

  if (btn.dataset.escalate) {
    act(() => api(
      `/api/voice/escalate/${encodeURIComponent(btn.dataset.escalate)}?force=true`,
      { method: "POST" }
    ), "Voice escalation dispatched.");
  }

  if (btn.dataset.contactToggle) {
    const active = btn.dataset.active !== "1";
    act(() => api(`/api/contacts/${encodeURIComponent(btn.dataset.contactToggle)}`, {
      method: "PATCH", body: JSON.stringify({ active }),
    }), active ? "Back on call." : "Muted for now.");
  }

  if (btn.dataset.packet) {
    openPacket(btn.dataset.packet);
    return;
  }

  if (btn.dataset.contactRemove) {
    act(() => api(`/api/contacts/${encodeURIComponent(btn.dataset.contactRemove)}`, {
      method: "DELETE",
    }), "Removed from the roster.");
  }

  if (btn.dataset.resetUser) {
    act(async () => {
      const out = await api(
        `/api/accounts/users/${encodeURIComponent(btn.dataset.resetUser)}/reset`,
        { method: "POST" }
      );
      // Shown once, so it has to be readable rather than a toast that fades.
      $("team-body").insertAdjacentHTML("afterbegin",
        `<div class="quote"><div class="quote-label">One-time reset token for
           ${esc(out.user.email)} — hand it over directly</div>
         <code class="u-wb-break-all">${esc(out.reset_token)}</code></div>`);
    });
  }

  if (btn.dataset.retire) {
    const id = btn.dataset.retire;
    if (!confirm(`Retire ${id}? Its readings are deleted and the seat is freed.`)) return;
    act(() => api(`/api/licenses/me/sensors/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }), `${id} retired.`);
  }

  if (btn.dataset.rekeyBusiness) {
    if (!confirm(
      "Replace this business's master key?\n\n"
      + "The current key stops working immediately. Any script or device that "
      + "still uses it will be refused until you give it the new one. Sensors "
      + "with their own keys carry on as before."
    )) return;
    act(async () => {
      const rotated = await api("/api/licenses/me/rotate-key", { method: "POST" });
      showIssuedKey("this business (master key)", rotated.api_key);
    }, "New master key issued.");
  }

  if (btn.dataset.rekey) {
    const id = btn.dataset.rekey;
    if (!confirm(
      `Issue a new ingest key for ${id}?\n\n`
      + "The current key stops working immediately, so the device will go "
      + "silent until you put the new one on it."
    )) return;
    act(async () => {
      const rotated = await api(
        `/api/licenses/me/sensors/${encodeURIComponent(id)}/rotate-key`,
        { method: "POST" }
      );
      showIssuedKey(id, rotated.ingest_key);
    }, `New key issued for ${id}.`);
  }

  if (btn.dataset.tune) {
    const sensor = state.data.sensors.find((s) => s.sensor_id === btn.dataset.tune);
    if (!sensor) return;
    // Asked in the account's own unit, the one the card shows. Asking in
    // Fahrenheit on a Celsius account meant somebody reading "5°C" on the
    // card typed 5 and set the limit to 5°F, so every reading alarmed.
    const unit = sensor.temperature_unit || state.unit || "F";
    const shown = sensor.danger_above_display;
    const current = shown === null || shown === undefined ? "" : String(shown);
    const answer = prompt(
      `Upper limit for ${sensor.sensor_id} in °${unit}.\n`
      + `Blank restores the ${sensor.industry_name} default.`,
      current
    );
    if (answer === null) return;
    const value = answer.trim() === "" ? null : Number(answer);
    if (value !== null && !Number.isFinite(value)) {
      toast("That is not a number.", "err");
      return;
    }
    // The API holds limits in Fahrenheit, whatever the account reads in.
    const fahrenheit = value === null || unit !== "C"
      ? value : Math.round((value * 9 / 5 + 32) * 100) / 100;
    act(() => api(
      `/api/licenses/me/sensors/${encodeURIComponent(sensor.sensor_id)}/thresholds`,
      { method: "POST", body: JSON.stringify({
          danger_above: fahrenheit, danger_below: sensor.override_below ?? null }) }
    ), value === null ? "Restored the industry default." : `Limit set to ${value}°${unit}.`);
  }

  if (btn.dataset.shortcut) {
    const vertical = btn.dataset.shortcut;
    btn.disabled = true;
    act(async () => {
      const out = await api(`/api/shortcuts/${encodeURIComponent(vertical)}?days=30`, {
        method: "POST",
      });
      const ev = out.evidence;
      $("shortcut-out").innerHTML =
        `<div class="quote u-mt-12">
           <div class="quote-label">${esc(out.shortcut_name)} · last
             ${out.period_days} days · ${ev.sensor_count} sensor(s),
             ${ev.total_readings} readings, ${ev.total_excursions} excursion(s)${
               out.document_source === "fallback_template"
                 ? " · model unavailable, figures only" : ""
             }</div>
           <pre class="u-ws-pre-wrap u-m-0 u-ff-inherit u-fs-12-5 u-maxh-260 u-ov-auto">${
             esc(out.document)}</pre>
         </div>`;
      logToConsole(`${out.shortcut_name} generated from ${ev.total_readings} readings.`, "ok");
    }).finally(() => { btn.disabled = false; });
  }

  if (btn.dataset.go) goTo(btn.dataset.go);
  if (btn.id === "sim-add") simAdd();
  if (btn.dataset.simBreak) simAct(`/units/${encodeURIComponent(btn.dataset.simBreak)}/break`, "POST",
    "It is warming. Nothing is sent to anybody.");
  if (btn.dataset.simFix) simAct(`/units/${encodeURIComponent(btn.dataset.simFix)}/fix`, "POST");
  if (btn.dataset.simRemove) simAct(`/units/${encodeURIComponent(btn.dataset.simRemove)}`, "DELETE");
});

/* Show an issued ingest key, once, somewhere it will stay put until it
   has been dealt with. The response is otherwise thrown away and the key
   is unrecoverable — rotation would be the only way back to one. */
const DEVICE_KEY_NOTE = "Put this on the device and keep it nowhere else. It is "
  + "shown once and never again — if it is lost, rotate the sensor for a new "
  + "one. It can report readings for this asset and nothing more, which is "
  + "why it, and not your account key, belongs on hardware.";
const MASTER_KEY_NOTE = "This key speaks for the whole business: it is for "
  + "scripts and integrations, never for a device. It is shown once — if it "
  + "is lost, replace it from Setup. Sensors get keys of their own.";

function showIssuedKey(sensorId, key, options = {}) {
  if (!key) return;
  const master = /master key/.test(sensorId);
  $("issued-for").textContent = sensorId;
  $("issued-value").textContent = key;
  $("issued-note").textContent = master ? MASTER_KEY_NOTE : DEVICE_KEY_NOTE;
  $("issued-key").hidden = false;
  // Arriving from sign-up, the first screen is the checklist, not a key
  // box half way down the page: the key waits where "Add a sensor" leads.
  $("issued-value").focus({ preventScroll: options.scroll === false });
}

$("issued-copy").addEventListener("click", async () => {
  const key = $("issued-value").textContent;
  try {
    await navigator.clipboard.writeText(key);
    toast("Key copied.");
  } catch (err) {
    // Clipboard access can be refused outright. Select it so it can be
    // copied by hand rather than leaving the operator stuck.
    const range = document.createRange();
    range.selectNodeContents($("issued-value"));
    const sel = getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    toast("Clipboard blocked — the key is selected, copy it by hand.", "err");
  }
});

$("issued-done").addEventListener("click", () => {
  $("issued-key").hidden = true;
  $("issued-value").textContent = "";
});

$("add-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const serial = $("add-sn").value.trim();
  const sensorId = $("add-id").value.trim() || sensorIdFrom($("add-loc").value.trim());
  act(async () => {
    const made = await api("/api/licenses/me/sensors", {
      method: "POST",
      body: JSON.stringify({
        sensor_id: sensorId,
        industry_vertical: $("add-vertical").value,
        location_name: $("add-loc").value.trim(),
        external_device_sn: serial || null,
      }),
    });
    event.target.reset();
    // reset() puts the picker back on the first sector in the list; the
    // next unit is far likelier to be in the same one as this.
    $("add-vertical").value = made.sensor.industry_vertical;
    showIssuedKey(made.sensor.sensor_id, made.ingest_key);
  }, "Sensor registered.").then(watchForFirstReading);
});

/* Posts through the real BYOD ingest route, so what the console shows is
   what a vendor's webhook would actually get back. */
$("byod-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const btn = event.target.querySelector("button");
  btn.disabled = true;
  const serial = $("byod-sn").value.trim();
  const temp = Number($("byod-temp").value);

  try {
    const body = await api("/api/v1/bridge/sensor-webhook-ingest", {
      method: "POST",
      body: JSON.stringify({
        device_sn: serial,
        reading_value: temp,
        metric_type: "temperature_f",
      }),
    });

    $("byod-out").innerHTML = `<div class="readout">
        <div><span class="k">Bound sensor</span><span class="v">${esc(body.bound_sensor_id)}</span></div>
        <div><span class="k">Reading</span><span class="v">${esc(body.processed_metric)} °F</span></div>
        <div><span class="k">Alert</span><span class="v ${body.alert_triggered ? "s-critical" : "s-good"}">
          ${body.alert_triggered ? "TRIGGERED" : "nominal"}</span></div>
      </div>
      <div class="card-note u-mt-6">${esc(body.action_taken_by_ai)}</div>`;
    logToConsole(
      `BYOD ${serial} @ ${temp}°F -> ${body.alert_triggered ? "ALERT" : "nominal"}`,
      body.alert_triggered ? "err" : "ok"
    );
    await refresh();
  } catch (err) {
    $("byod-out").innerHTML =
      `<div class="readout"><div><span class="k">Rejected</span>
         <span class="v s-warning">${esc(err.message)}</span></div></div>`;
    logToConsole(`BYOD ${serial}: ${err.message}`, "err");
  } finally {
    btn.disabled = false;
  }
});

/* Live contract value as the branch count is typed. */
let calcTimer = null;
async function calculateBilling() {
  const branches = Number($("calc-branches").value);
  const units = Number($("calc-units").value) || 1;
  const vertical = $("calc-vertical").value;
  if (!Number.isFinite(branches) || branches < 1) return;

  try {
    const q = await api(
      `/api/v1/enterprise-billing/quote?industry_vertical=${encodeURIComponent(vertical)}`
      + `&total_branch_locations=${branches}&units_per_branch=${units}`
    );
    const vol = q.enterprise_volume;
    const per = q.per_unit;
    const winner = q.cheaper_model === "enterprise_volume" ? "volume contract" : "rate card";

    $("calc-out").innerHTML = `<div class="readout">
        <div><span class="k">Tier</span><span class="v">${esc(vol.pricing_tier_applied)}</span></div>
        <div><span class="k">Monthly</span>
             <span class="v c-commerce">$${vol.monthly_usd.toLocaleString()}</span></div>
        <div><span class="k">Annual contract</span>
             <span class="v c-commerce">$${vol.annual_usd.toLocaleString()}</span></div>
        <div><span class="k">Per branch</span>
             <span class="v">$${vol.effective_rate_per_branch.toLocaleString()}</span></div>
        <div><span class="k">Rate card would be</span>
             <span class="v">$${per.monthly_usd.toLocaleString()}</span></div>
        <div><span class="k">Cheaper</span><span class="v">${esc(winner)}</span></div>
      </div>`
      + (vol.next_tier
        ? `<div class="card-note u-mt-6 u-c-warning">
             ${vol.next_tier.units_until_next_discount} more unit${
               vol.next_tier.units_until_next_discount === 1 ? "" : "s"} reaches
             ${esc(vol.next_tier.next_discount_label)} at
             $${vol.next_tier.next_tier_monthly_usd.toLocaleString()}/mo —
             a $${vol.next_tier.monthly_increase_usd.toLocaleString()} step.
           </div>`
        : `<div class="card-note u-mt-6">
             Flat enterprise tier — further branches add nothing.
           </div>`);
  } catch (err) {
    $("calc-out").innerHTML =
      `<div class="card-note u-c-critical">${esc(err.message)}</div>`;
  }
}

["calc-branches", "calc-units", "calc-vertical"].forEach((id) => {
  $(id).addEventListener("input", () => {
    clearTimeout(calcTimer);
    calcTimer = setTimeout(calculateBilling, 250);
  });
});

$("vault-attest").addEventListener("click", async () => {
  const btn = $("vault-attest");
  btn.disabled = true;
  try {
    renderVault(await api("/api/vault/attestation?days=30"));
    toast("Attestation issued. The digest fixes this record.", "ok");
  } catch (err) { toast(err.message, "err"); }
  finally { btn.disabled = false; }
});

/* Re-derives every chain from what is stored right now. Slow on a large
   estate and worth the wait: it is the only honest way to answer "has
   anything been altered". */
$("vault-verify").addEventListener("click", async () => {
  const btn = $("vault-verify");
  btn.disabled = true;
  try {
    const sensors = (state.data && state.data.sensors) || [];
    if (!sensors.length) { toast("Nothing to verify yet."); return; }
    const results = await Promise.all(sensors.map((s) =>
      api(`/api/vault/verify/${encodeURIComponent(s.sensor_id)}?days=30`)));
    const broken = results.filter((r) => !r.intact);
    if (broken.length) {
      toast(`${broken.length} record(s) do not verify. Investigate now.`, "err");
    } else {
      const links = results.reduce((n, r) => n + r.links_checked, 0);
      toast(`${links.toLocaleString()} links re-derived. Record intact.`, "ok");
    }
  } catch (err) { toast(err.message, "err"); }
  finally { btn.disabled = false; }
});

$("term-toggle").addEventListener("click", () => {
  setTerminal($("terminal").hidden);
});

$("term-clear").addEventListener("click", () => {
  $("term-body").innerHTML = "";
  logToConsole("Log cleared.", "info");
});

$("contact-form").addEventListener("submit", (event) => {
  event.preventDefault();
  act(async () => {
    await api("/api/contacts", {
      method: "POST",
      body: JSON.stringify({
        full_name: $("ct-name").value.trim(),
        phone: $("ct-phone").value.trim(),
      }),
    });
    event.target.reset();
  }, "Added to the roster.");
});

$("site-form").addEventListener("submit", (event) => {
  event.preventDefault();
  act(async () => {
    await api("/api/sites", {
      method: "POST",
      body: JSON.stringify({ name: $("site-name").value.trim() }),
    });
    event.target.reset();
  }, "Site added.");
});

$("hook-form").addEventListener("submit", (event) => {
  event.preventDefault();
  act(async () => {
    await api("/api/webhooks", {
      method: "POST",
      body: JSON.stringify({
        kind: $("hook-kind").value,
        target: $("hook-target").value.trim(),
      }),
    });
    event.target.reset();
  }, "Alert channel connected. Send it a test.");
});

/* PagerDuty takes a routing key rather than a URL, and typing a URL into
   that box is the mistake everybody makes once. */
$("hook-kind").addEventListener("change", () => {
  $("hook-target").placeholder = $("hook-kind").value === "pagerduty"
    ? "Events API v2 routing key"
    : "https://hooks.slack.com/services/...";
});

/* The viewer sandbox blocks page-initiated downloads, so the CSV is opened
   in a new tab rather than offered as a download link that would do nothing. */
$("download-csv").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/autopilot/compliance.csv?days=7", {
      headers: { Authorization: `Bearer ${state.token}` },
    });
    if (!res.ok) throw new Error(`Export failed (${res.status})`);
    const text = await res.text();
    const win = window.open("", "_blank");
    if (win) {
      win.document.write(`<pre>${esc(text)}</pre>`);
      win.document.close();
    } else {
      toast("Allow pop-ups to view the export.", "err");
    }
  } catch (err) {
    toast(err.message, "err");
  }
});

$("password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = $("password-form");
  try {
    await api("/api/accounts/me/password", {
      method: "POST",
      body: JSON.stringify({
        current_password: $("pw-current").value,
        new_password: $("pw-new").value,
      }),
    });
    form.reset();
    if (state.user && state.user.must_change_password) {
      state.user.must_change_password = false;
      if (state.user.role !== "viewer") $("role-banner").hidden = true;
    }
    toast("Password changed. Every other session was signed out.");
  } catch (err) {
    toast(err.message || "That password could not be changed.");
  }
});
$("shape-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  let parsed;
  try {
    parsed = JSON.parse($("shape-json").value);
  } catch (err) {
    $("shape-out").className = "read-out no";
    $("shape-out").textContent =
      "That is not valid JSON. Paste it exactly as the vendor shows it.";
    return;
  }
  try {
    renderReading(await api("/api/v1/bridge/preview", {
      method: "POST", body: JSON.stringify({ payload: parsed }),
    }));
  } catch (err) {
    toast(err.message || "That could not be read.");
  }
});
$("terms-accept").addEventListener("click", () => {
  acceptTerms().catch((err) => toast(err.message || "That did not work."));
});
$("run-sweep").addEventListener("click", () => {
  act(async () => {
    const out = await api("/api/autopilot/sweep", { method: "POST" });
    $("autopilot-out").innerHTML =
      `<div class="quote"><div class="quote-label">Sweep complete</div>`
      + `${out.sensors_offline} offline · ${out.open_incidents} open · `
      + `${out.voice_calls_placed} call(s) placed · ${out.actions_taken} action(s)</div>`;
  });
});

$("run-compliance").addEventListener("click", () => {
  act(async () => {
    const r = await api("/api/autopilot/compliance?days=7");
    $("autopilot-out").innerHTML =
      `<div class="quote"><div class="quote-label">Compliance · last 7 days</div>`
      + `${r.overall_compliance_percent === null ? "—" : r.overall_compliance_percent}% in band · `
      + `${r.total_readings_logged} readings · ${r.incidents_opened} incident(s) · `
      + `mean response ${r.mean_response_minutes === null ? "—" : r.mean_response_minutes} min</div>`;
  });
});

$("tx-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const btn = event.target.querySelector("button");
  btn.disabled = true;
  act(async () => {
    const out = await api("/api/v1/bridge/summarize-transcript", {
      method: "POST",
      body: JSON.stringify({
        industry_vertical: $("tx-vertical").value,
        raw_transcript: $("tx-text").value,
      }),
    });

    if (!out.intelligence_report) {
      $("tx-out").innerHTML = `<div class="quote"><div class="quote-label">Not processed</div>`
        + `${esc(out.degraded_reason)}</div>`;
      return;
    }

    const r = out.intelligence_report;
    const actions = r.action_items_assigned || [];
    const items = actions.map((a) =>
      `<li><strong>${esc(a.task)}</strong> — ${esc(a.owner)} (${esc(a.priority)})</li>`).join("");
    $("tx-out").innerHTML =
      `<div class="quote"><div class="quote-label">Summary</div>${esc(r.executive_summary)}</div>`
      + (items ? `<div class="quote"><div class="quote-label">Action items</div>
           <ul class="u-m-0 u-pl-18">${items}</ul></div>` : "")
      + `<div class="quote"><div class="quote-label">Compliance impact</div>
           ${esc(r.industry_compliance_impact)}</div>`
      // Without this the whole feature ends here: a list of what people
      // agreed to, printed once, gone when the tab closes.
      + (actions.length
          ? `<button class="btn btn-sm u-mt-10" id="tx-keep">
               Keep these ${actions.length} job(s)</button>`
          : "");

    const keep = $("tx-keep");
    if (keep) {
      keep.addEventListener("click", () => {
        keep.disabled = true;
        act(async () => {
          const saved = await api("/api/tasks/from-meeting", {
            method: "POST",
            body: JSON.stringify({
              source: "Meeting notes",
              action_items: actions.map((a) => ({
                title: a.task,
                owner: a.owner || "",
                // The model is asked for High/Med and mostly obliges.
                // Anything else would fail validation and lose the lot,
                // so an unfamiliar word becomes the middle one.
                priority: ["High", "Med", "Low"].includes(a.priority)
                  ? a.priority : "Med",
              })),
            }),
          });
          renderTasks(saved.outstanding);
          toast(`${saved.saved} job(s) kept.`, "ok");
          keep.textContent = "Kept";
        }).catch(() => { keep.disabled = false; });
      });
    }
  }).finally(() => { btn.disabled = false; });
});

/* ---------------- getting in ----------------
   The session token is already remembered, so somebody who signed in on
   this browser never sees the gate again. This is about the other two
   cases: the first time, and the time after a sign-out. Both of those
   used to mean typing an email address from scratch. */

const EMAIL_STORAGE = "cyberlogix.email";

function rememberEmail(address) {
  try { localStorage.setItem(EMAIL_STORAGE, address); } catch (_) {}
}

function rememberedEmail() {
  try { return localStorage.getItem(EMAIL_STORAGE) || ""; }
  catch (_) { return ""; }
}

/* Reveal, rather than a second "confirm password" box. The failure this
   prevents is the common one: a long password typed correctly but
   invisibly, rejected, and typed again. */
$("si-see").addEventListener("click", () => {
  const field = $("si-password");
  const shown = field.type === "text";
  field.type = shown ? "password" : "text";
  $("si-see").textContent = shown ? "Show" : "Hide";
  $("si-see").setAttribute("aria-pressed", String(!shown));
  field.focus();
});

/* Land the cursor where there is actually something to type. Autofocus on
   the email box is wrong for the person whose address we already know:
   it puts them one tab away from the only field they have to fill. */
function primeTheGate() {
  const known = rememberedEmail();
  if (known) {
    $("si-email").value = known;
    $("si-password").focus();
  } else {
    $("si-email").focus();
  }
}

/* ---------------- finding things ----------------
   The page is one long column of cards and no map. Filtering beats
   scrolling: pressing People should leave People on the screen, not put
   it somewhere below the fold with everything else still there. */

const finder = { tab: "all", query: "" };

function findableCards() {
  return Array.from(document.querySelectorAll("[data-find]"));
}

function applyFinder() {
  const query = finder.query.trim().toLowerCase();
  const words = query ? query.split(/\s+/) : [];
  let showing = 0;

  findableCards().forEach((card) => {
    const hay = (card.getAttribute("data-find") || "").toLowerCase();
    const inTab = finder.tab === "all"
      || card.getAttribute("data-group") === finder.tab;
    // Every word has to appear somewhere, so "licence food" narrows
    // rather than widening the way an "any word" match would.
    const matches = words.every((word) => hay.includes(word));
    const show = inTab && matches;
    card.hidden = !show;
    if (show) showing += 1;
  });

  const none = $("find-none");
  none.hidden = showing > 0;
  if (!showing) {
    none.textContent = query
      ? `Nothing here matches “${query}”.`
      : "Nothing in this group yet.";
  }
  $("find-clear").hidden = !query;

  document.querySelectorAll(".f-tab").forEach((tab) => {
    tab.setAttribute("aria-pressed",
                     String(tab.getAttribute("data-tab") === finder.tab));
  });
}

/* How many cards each group holds, printed on its tab. A group with a
   number on it is a promise about what pressing it will do. */
function countTheTabs() {
  const counts = {};
  findableCards().forEach((card) => {
    const group = card.getAttribute("data-group");
    counts[group] = (counts[group] || 0) + 1;
  });
  document.querySelectorAll(".f-tab").forEach((tab) => {
    const key = tab.getAttribute("data-tab");
    const n = key === "all" ? findableCards().length : (counts[key] || 0);
    if (!tab.querySelector(".n")) {
      tab.insertAdjacentHTML("beforeend", ` <span class="n">${n}</span>`);
    }
  });
}

document.querySelectorAll(".f-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    finder.tab = tab.getAttribute("data-tab");
    applyFinder();
    // Back to the top of the list, or the first card of a short group
    // renders below where the reader is already looking.
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
});

$("find-q").addEventListener("input", (event) => {
  finder.query = event.target.value;
  applyFinder();
});

$("find-clear").addEventListener("click", () => {
  $("find-q").value = "";
  finder.query = "";
  applyFinder();
  $("find-q").focus();
});

/* "/" to search, the shortcut every search box on the web has. Escape
   gives the page back. Neither fires while something else is being
   typed into. */
document.addEventListener("keydown", (event) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(
    (event.target.tagName || "").toUpperCase());
  if (event.key === "/" && !typing && !$("app").hidden) {
    event.preventDefault();
    $("find-q").focus();
    $("find-q").select();
  } else if (event.key === "Escape" && event.target === $("find-q")) {
    $("find-q").value = "";
    finder.query = "";
    applyFinder();
    $("find-q").blur();
  }
});

/* The finder sticks below the nav, and the nav changes height when its
   chips wrap. Measured rather than guessed: a hard-coded offset leaves
   the bar either floating in a gap or tucked under the nav at exactly
   the widths where the chips rewrap. */
function measureNav() {
  const nav = document.querySelector(".nav");
  if (!nav) return;
  document.documentElement.style.setProperty(
    "--nav-h", `${Math.round(nav.getBoundingClientRect().height)}px`);
}
if (typeof ResizeObserver === "function") {
  const nav = document.querySelector(".nav");
  if (nav) new ResizeObserver(measureNav).observe(nav);
}
window.addEventListener("resize", measureNav);

/* ---------------- boot ---------------- */

countTheTabs();
measureNav();
applyFinder();
initInspectorForm();

let saved = null;
try { saved = localStorage.getItem(TOKEN_STORAGE); } catch (_) {}
if (saved) {
  enter(saved).catch(() => {
    try { localStorage.removeItem(TOKEN_STORAGE); } catch (_) {}
    primeTheGate();
  });
} else {
  primeTheGate();
}

registerWorker();
