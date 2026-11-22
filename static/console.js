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
  // Sensor keys shown since this page loaded, by unit: in memory only,
  // for the connect wizard to fill in. Never stored, never sent anywhere.
  issuedKeys: {},
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
  $("switch-btn").textContent = onboarding ? "Sign in" : "Set up a new business";
});

/* A refused sign-in, under the password box, with the box marked. */
function signInError(message) {
  const box = $("si-error");
  box.textContent = message || "";
  box.hidden = !message;
  $("si-password").setAttribute("aria-invalid", message ? "true" : "false");
}
["si-email", "si-password"].forEach((id) =>
  $(id).addEventListener("input", () => { if (!$("si-error").hidden) signInError(""); }));

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
    signInError("");
    // Signing in, not reloading: an open alert sends this to /today.
    state.fromSignIn = true;
    await enter(body.token, body.user);
  } catch (err) {
    if (err.offline) toast(err.message, "err");
    signInError(err.message);
    $("si-password").focus();
    $("si-password").select();
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
    showIssuedKey("this business (master key)", made.api_key, { scroll: false });
    toast("Trial started. Your business key is in Setup if you ever "
          + "connect other software.", "ok");
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
    // No toast: the first thing to read here is the checklist, and a
    // business key matters only to somebody connecting other software.
    showIssuedKey("this business (master key)", handed, { scroll: false });
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
  state.slowKept = null;
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
  if (misses) return Math.min(POLL_MS * 2 ** misses, POLL_MAX_MS);
  // Sooner when a unit's "we'll text at" comes before the next poll, so
  // the card turns into the alert as it opens (static/words.js).
  return CLXWords.pollDelay(((state.data || {}).sensors) || [], POLL_MS);
}

function startPolling() {
  clearTimeout(state.timer);
  // A new chain retires the old one, even if the old one has a request
  // in flight: switching business must not leave two chains polling.
  const chain = (state.pollChain = (state.pollChain || 0) + 1);
  // A new chain (a sign-in, another business) starts from a full load.
  state.slowKept = null;
  const tick = () => {
    refresh({ poll: true }).then(markFresh).catch(markStale).finally(() => {
      if (chain !== state.pollChain || !state.token) return;
      state.timer = setTimeout(tick, nextPollDelay());
    });
  };
  state.timer = setTimeout(tick, nextPollDelay());
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
          ${row.monthly_usd == null ? esc(row.price_label) : `$${row.monthly_usd.toLocaleString()}
          <span class="u-c-muted u-fw-400 u-fs-12">
            / ${esc(row.unit)} / mo</span>`}
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
  // Food and medicine first, the made-to-order trades last: a café's
  // add form opened on "Data centers — Custom pricing".
  const ordered = body.industries.slice().sort((a, b) =>
    ((ADD_FIRST.indexOf(a.vertical) + 1) || 99) - ((ADD_FIRST.indexOf(b.vertical) + 1) || 99)
    || Number(a.monthly_usd == null) - Number(b.monthly_usd == null)
    || a.name.localeCompare(b.name));
  $("add-vertical").innerHTML = ordered
    .map((i) => `<option value="${esc(i.vertical)}">`
      + `${esc(i.name)} — ${i.monthly_usd == null ? esc(i.price_label)
        : `$${i.monthly_usd} / ${esc(i.billing_unit)}`}</option>`)
    .join("");
  const plain = body.industries
    .map((i) => `<option value="${esc(i.vertical)}">${esc(i.name)}</option>`)
    .join("");
  $("tx-vertical").innerHTML = plain;
  showUnitKinds($("add-vertical").value);
  // The overview may already have landed; if it has, this is the call
  // that actually sets the sector.
  preferSector(state.data);
}

// The order of the add form's list when nothing is known yet.
const ADD_FIRST = ["restaurant", "pharmacy", "medical_lab", "country_club"];

$("add-trade-change").addEventListener("click", () => {
  state.tradeOpened = true;
  $("add-trade").hidden = true;
  $("add-vertical-field").hidden = false;
  $("add-vertical").focus();
});

$("add-vertical").addEventListener("change", (event) => {
  showSectorExample(event.target.value);
  showUnitKinds(event.target.value);
});

/* "Fridge or freezer?" for the sectors where the answer sets the limit.
 * A required choice with no default, because a freezer saved as a fridge
 * alarms far too late. Guessed from the name where the name says it
 * ("walk-in freezer", "milk fridge"), and never over a choice made by
 * hand. */
function unitKindDeg(f) {
  const unit = (state.data && state.data.tenant && state.data.tenant.temperature_unit) || "F";
  return unit === "C" ? `${Math.round((f - 32) * 5 / 9)}°C` : `${f}°F`;
}

function unitKindLabel(k) {
  const deg = unitKindDeg;
  const wait = k.alert_after_minutes ? ` for ${k.alert_after_minutes} minutes` : "";
  if (k.danger_below != null) {
    return `outside ${deg(k.danger_below)} to ${deg(k.danger_above)}${wait}`;
  }
  const far = k.alert_at_once_degrees
    ? `, or at once if it reaches ${deg(k.danger_above + k.alert_at_once_degrees)}` : "";
  return `over ${deg(k.danger_above)}${wait}${far}`;
}

const FREEZER_WORDS = /freez|ice ?cream|gelato|frozen|blast chill|sorbet/i;
const FRIDGE_WORDS = /fridge|cooler|chiller|refrig|reach-?in|milk|dairy|drinks|beverage|salad|prep|deli|vaccine/i;

function guessUnitKind(name) {
  if (FREEZER_WORDS.test(name || "")) return "freezer";
  if (FRIDGE_WORDS.test(name || "")) return "fridge";
  return "";
}

function showUnitKinds(vertical) {
  const profile = (state.industries || []).find((i) => i.vertical === vertical);
  const kinds = (profile && profile.unit_kinds) || [];
  const field = $("add-kind-field");
  if (!field) return;
  field.hidden = !kinds.length;
  const previous = $("add-kind").value;
  $("add-kind").innerHTML = `<option value="">Choose one</option>` + kinds
    .map((k) => `<option value="${esc(k.kind)}">${esc(k.label)}</option>`).join("");
  // Required only while it is shown: a hidden required field would
  // block the form for a sector that does not ask.
  $("add-kind").required = kinds.length > 0;
  if (kinds.some((k) => k.kind === previous)) $("add-kind").value = previous;
  else guessKindFromName();
  showUnitKindHint();
}

function guessKindFromName() {
  if (state.kindChosenByHand) return;
  const guess = guessUnitKind($("add-loc").value);
  const offered = Array.from($("add-kind").options).some((o) => o.value === guess);
  $("add-kind").value = offered ? guess : "";
  showUnitKindHint();
}

function showUnitKindHint() {
  const profile = (state.industries || []).find((i) => i.vertical === $("add-vertical").value);
  const kind = ((profile && profile.unit_kinds) || []).find((k) => k.kind === $("add-kind").value);
  $("add-kind-hint").textContent = kind
    ? `We alert if it stays ${unitKindLabel(kind)}, so a door opened for a minute `
      + "wakes nobody. You can change this later."
    : "Choose one: a freezer gets a freezer's limit.";
}

$("add-kind").addEventListener("change", () => {
  state.kindChosenByHand = $("add-kind").value !== "";
  showUnitKindHint();
});
$("add-loc").addEventListener("input", guessKindFromName);

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

  ["add-vertical", "tx-vertical"].forEach((id) => {
    const select = $(id);
    if (select) select.value = wanted;
  });
  // Taken from the business's own trade instead of asked: one line
  // saying which, and "Change" for a unit of another kind.
  const trade = (state.industries || []).find((i) => i.vertical === wanted);
  if (trade && !state.tradeOpened) {
    // A florist or a shop is watched with a restaurant's food limits, and
    // is told so in those words, not "For restaurants & cafés".
    const own = wanted === stated && overview.tenant.limits_words;
    $("add-trade-words").textContent = own ? `${own}.` : `For ${trade.name.toLowerCase()}.`;
    $("add-trade").hidden = false;
    $("add-vertical-field").hidden = true;
  }

  showSectorExample(wanted);
  showUnitKinds(wanted);
  state.sectorChosen = true;
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
  referrals: "referral-body",
  proofReports: "proof-report-body",
  omni: "omni-body",
  aiKey: "aikey-body",
  tasks: "task-body",
  peopleAlerts: "licence-alerts",
  peopleCal: "people-cal",
  staff: "staff-list",
  leavers: "leavers",
  leaving: "pause-body",
};

const FEATURE_WORDS = {
  predictive_forecasting: "warnings before something gets too warm",
  voice_escalation: "phone calls",
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
    // The server names the feature by its key ('predictive_forecasting');
    // a person is told what it does instead.
    const plain = message.slice(0, 160).replace(/'([a-z_]+)'/g,
      (whole, key) => FEATURE_WORDS[key] || key.replace(/_/g, " "));
    el.innerHTML = `<div class="empty u-p-16-0">Not on this plan.
      <span class="sub">${esc(plain)}</span>${nextSteps([["plans", "See the plans"]])}</div>`;
    return;
  }
  el.innerHTML = `<div class="empty u-p-16-0">${
    lapsed ? "Withheld while the licence is unpaid."
           : "Could not be loaded."}
    <span class="sub">${esc(lapsed
      ? "Monitoring and alerting are unaffected."
      : message.slice(0, 120))}</span></div>`;
}

/* Panels whose contents change when somebody changes them (bills, the
   team, the roster, the contract), not minute to minute. A timed poll
   fetches them only every SLOW_EVERY polls and paints the copy it kept
   in between; anything a person does here calls refresh() with no
   argument, which fetches everything at once. Temperatures, problems,
   tasks and the escalation preview are fetched on every poll. Without
   this every open console sent 28 requests every 20 seconds. */
const SLOW_PANELS = new Set([
  "costs", "audit", "roster", "rotations", "team", "businesses", "hooks",
  "invoices", "contract", "pipeline", "forecast", "claims", "peopleAlerts",
  "referrals", "omni", "aiKey", "proofReports", "staff", "peopleCal", "leavers",
  "leaving",
]);
const SLOW_EVERY = 5;

async function refresh(opts = {}) {
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
    // Not asked for on a plan without it: the 403 it would get back on
    // every load is a known answer, and the card says it the same way.
    forecast: () => (overview.entitlements && overview.entitlements.predictive_forecasting
      ? api("/api/forecast/fleet")
      : Promise.reject(Object.assign(new Error(
        "The forecast — a warning before a unit gets too warm — comes with Growth."),
        { status: 403 }))),
    claims: () => api("/api/claims/eligible"),
    escalation: () => api("/api/contacts/preview"),
    byodSeen: () => api("/api/v1/bridge/samples"),
    knocking: () => api("/api/doorstep"),
    peopleAlerts: () => api("/api/people/alerts"),
    pollers: () => api("/api/pollers"),
    // Owners only. Anyone else is told so on the card, not shown an error.
    referrals: () => api("/api/referrals/me").catch((err) =>
      (err && err.status === 403 ? { notOwner: true } : Promise.reject(err))),
    omni: () => api("/api/connections/omniarmor").catch((err) =>
      (err && err.status === 403 ? { notOwner: true } : Promise.reject(err))),
    aiKey: () => api("/api/ai-key").catch((err) =>
      (err && err.status === 403 ? { notOwner: true } : Promise.reject(err))),
    proofReports: () => api("/api/proof-reports").catch((err) =>
      (err && err.status === 403 ? { notOwner: true } : Promise.reject(err))),
    tasks: () => api("/api/tasks"),
    staff: () => api("/api/people"),
    peopleCal: () => api("/api/people/calendar?days=180"),
    leavers: () => api("/api/people/departures"),
    leaving: () => api("/api/leaving"),
  };
  const kept = state.slowKept;
  const light = Boolean(opts.poll && kept && kept.polls < SLOW_EVERY - 1);
  const keys = Object.keys(wanted).filter((k) => !(light && SLOW_PANELS.has(k)));
  const settled = await Promise.allSettled(keys.map((k) => wanted[k]()));
  const got = light ? Object.assign({}, kept.got) : {};
  const failed = light ? Object.assign({}, kept.failed) : {};
  keys.forEach((k, i) => {
    if (settled[i].status === "fulfilled") got[k] = settled[i].value;
    else failed[k] = settled[i].reason;
  });
  if (light) {
    kept.polls += 1;
  } else {
    const slowGot = {};
    const slowFailed = {};
    SLOW_PANELS.forEach((k) => {
      if (k in got) slowGot[k] = got[k];
      if (k in failed) slowFailed[k] = failed[k];
    });
    state.slowKept = { got: slowGot, failed: slowFailed, polls: 0 };
  }

  state.shortcutCatalogue = got.catalogue || state.shortcutCatalogue;
  state.data = overview;
  preferSector(overview);
  if (got.me) state.user = got.me.user;
  state.unit = overview.temperature_unit || "F";

  render(overview, health);
  // A small account's Advanced cards, and the counts on the tabs, follow
  // the account: a trial that grows past a handful of units sees them all.
  const small = smallAccount(overview);
  const firstDay = dayOne(overview);
  const simple = simpleTabs(overview);
  if (state.wasSmall !== small || state.wasDayOne !== firstDay || state.wasSimple !== simple) {
    state.wasSmall = small;
    state.wasDayOne = firstDay;
    state.wasSimple = simple;
    applyFinder();
  }
  landOnATab(overview);
  countTheTabs();
  maybeLandOnSimple(overview);
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
  paint("referrals", () => renderReferrals(got.referrals));
  paint("omni", () => renderOmni(got.omni));
  paint("aiKey", () => renderAiKey(got.aiKey));
  paint("proofReports", () => renderProofReports(got.proofReports));
  paint("tasks", () => renderTasks(got.tasks));
  paint("peopleAlerts", () => {
    renderLicenceAlerts(got.peopleAlerts);
    renderLeaverAlerts(got.peopleAlerts);
  });
  paint("peopleCal", () => renderPeople(got.peopleCal));
  paint("staff", () => renderStaff(got.staff));
  paint("leavers", () => renderLeavers(got.leavers));
  paint("team", () => renderTeam(got.team));
  paint("leaving", () => {
    renderLeaving(got.leaving);
    renderFounder(got.leaving.founder);
  });

  // The contract card is where the way back lives, so it renders from
  // whatever arrived rather than needing both halves.
  if (got.contract) {
    renderContract(got.contract, got.pipeline || { opportunities: [] },
                   overview && overview.tenant);
  }
  if (state.shortcutCatalogue) {
    renderShortcuts(overview.sensors, state.shortcutCatalogue);
  }
  // What the empty-tab sentence reads, kept from this load.
  state.counts = {
    roster: got.roster ? (got.roster.contacts || []).length : null,
    bills: got.invoices ? (got.invoices.invoices || []).length : null,
    licences: got.peopleCal ? (got.peopleCal.credentials || 0) : null,
  };
  renderTabEmpty();
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
  // The same "from" as the front page and the plans: a trial is free, and
  // the plans start at Starter's price. It used to show the rate card's
  // figure for a trial ($149/mo) beside a front page saying $59.
  const from = d.starter && d.starter.monthly_label;
  $("chip-spend").textContent = t.plan === "trial"
    ? (from ? `Free trial · plans from ${from}/mo` : "Free trial")
    : d.subscription
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
          a.paused ? "Come back sooner" : a.on_trial ? "See what it costs" : "Sort it out"}</button>`;
    account.hidden = false;
    $("account-act").addEventListener("click", () => {
      // A pause is ended on its own card; everything else on the contract.
      $(a.paused ? "pause-body" : "contract-body")
        .scrollIntoView({ behavior: "smooth", block: "center" });
    });
  } else {
    account.hidden = true;
  }

  const banner = $("delivery-banner");
  state.textsOff = health.message_delivery !== "twilio";
  if (health.message_delivery !== "twilio") {
    banner.hidden = false;
    // Plain words for the person reading; the settings for whoever runs
    // the server are on hover, not in a café owner's first screen.
    banner.textContent =
      "Texts and calls are not switched on for this server yet, so alerts "
      + "show here but nobody is texted.";
    banner.title = "For whoever runs it: set TWILIO_ACCOUNT_SID, "
      + "TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER.";
  } else {
    banner.hidden = true;
  }

  $("tiles").innerHTML = [
    tile(`${Noun(2)} online`, `${s.sensors_online}`,
         `of ${s.sensors_total} under watch`),
    tile("Out of range now", `${s.sensors_breaching}`,
         s.sensors_breaching
           ? `${noun(s.sensors_breaching)} too warm or cold`
           : "all within their limits"),
    tile("Open alerts", `${s.open_incidents}`, openAlertsNote(s)),
    // Only where the plan has the forecast: a tile saying "upgrade to
    // unlock" during a trial was an advert in the middle of the figures.
    d.entitlements.predictive_forecasting
      ? tile("At-risk forecast", `${s.at_risk}`, "projected to breach") : "",
    // Readings in range, as a share: shown only after a day of readings,
    // because "80%" after five minutes (one warm reading in five) is not
    // a figure anybody should act on.
    tile("Readings in range",
         s.compliance_ready && s.compliance_percent !== null ? `${s.compliance_percent}%` : "—",
         s.compliance_ready ? `last ${s.compliance_days} days`
           : `shown after ${s.compliance_after_hours || 24} hours of readings`),
    tile("Flat batteries", `${s.low_battery ?? 0}`,
         s.low_battery ? "replace before they go dark" : "all healthy"),
    tile("Readings logged", `${s.readings_logged}`,
         `${s.readings_breached} out of range`),
  ].join("");
  animateFigures($("tiles"));
  // "Heading for trouble" is the forecast's card: not shown at all on a
  // plan without it, rather than "Not on this plan" (the plans card and
  // /pricing say what each plan has).
  $("find-what-breaks-next").dataset.empty = d.entitlements.predictive_forecasting ? "" : "1";
  // The first day: seven tiles of zeros say nothing yet. The checklist
  // and the fridges are the page until it is done.
  $("tiles").hidden = dayOne(d);

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
  // The title already says how many; the note only when a filter hides some.
  $("fleet-note").textContent = shown.length === d.sensors.length
    ? "" : `${shown.length} of ${d.sensors.length} shown`;
  renderFleet(shown);
  renderTable(shown);

  renderFirstDay(d.first_day);
  const owner = Boolean(state.user && state.user.role === "owner");
  // With no fridge yet, the list's search, filters, table view and the
  // Simulate button say nothing: the empty list's own two buttons do.
  const noUnits = !(d.sensors || []).length;
  $("sim-start").hidden = !owner || noUnits;
  $("toggle-table").hidden = noUnits;
  // Search, the status filter and the site controls only once there is
  // something to sort through: several units for the first two, more
  // than one site for the others. For one walk-in they were four controls
  // above one card.
  const many = (d.sensors || []).length >= FLEET_TOOLS_FROM;
  const sites = (d.sites || []).length;
  document.querySelector("#fleet-card .toolbar").hidden = noUnits || (!many && sites < 2);
  $("fleet-search").hidden = !many;
  $("fleet-filter").hidden = !many;
  $("fleet-site").hidden = sites < 2;
  $("group-sites").hidden = sites < 2;
  if (sites < 2 && state.groupBySite) {
    state.groupBySite = false;
    $("group-sites").setAttribute("aria-pressed", "false");
  }
  const canTest = Boolean(state.user && state.user.role !== "viewer");
  $("test-alert-box").hidden = !canTest;
  const calls = (d.entitlements && d.entitlements.calls) || { mode: "none", words: "" };
  $("test-call-wrap").hidden = calls.mode === "none";
  // The card's own promise follows the plan: "we phone" only where a
  // phone would actually ring.
  $("roster-title").textContent = calls.mode === "none" ? "Who gets alerts" : "Who gets called";
  $("roster-plain").textContent = calls.mode === "plan"
    ? "The order we phone people in when something goes wrong at 3am."
    : calls.mode === "trial"
      ? "Who we text when something goes wrong, and the order we phone them in. "
        + calls.words
      : "Who we text when something goes wrong. " + calls.words;

  const open = d.incidents.filter((i) => i.state !== "resolved").length;
  $("incident-note").textContent =
    d.incidents.length ? `${open} active, ${d.incidents.length} total` : "";
  renderSiteWide(d.site_wide_alerts || []);
  renderIncidents(d.incidents, d.entitlements, d.site_wide_alerts || []);
  renderNeedsBar(d);
  renderByod(d.sensors);
}

/* The possible attack or site-wide failure alert: many units warming at
   once (sitewide.py). Its own banner, above everything, and its own card
   type, so it can never be read as one more unit alarm. */
function siteWideUnits(a) {
  const u = a.temperature_unit || state.unit;
  return `<ul class="sitewide-units">${(a.group_units || []).map((x) => {
    const how = x.over_limit && x.limit_display !== null && x.limit_display !== undefined
      ? `over its ${esc(x.limit_display)}°${esc(u)} limit` : "rising";
    return `<li><b>${esc(x.location_name)}</b>`
      + (x.site_name && a.group_scope === "business"
          ? ` <span class="sitewide-site">${esc(x.site_name)}</span>` : "")
      + ` <span class="sitewide-reading">${esc(x.temperature_display)}°${esc(u)}, ${how}</span></li>`;
  }).join("")}</ul>`;
}

function siteWideReach(a) {
  const texted = (a.sms_fanout || []).length;
  const called = (a.voice_fanout || []).length;
  const parts = [`Raised ${esc(stamp(a.opened_at))}`];
  if (texted) parts.push(`texted ${texted} ${texted === 1 ? "person" : "people"}`);
  if (called) parts.push(`called ${called}`);
  if (a.acknowledged_by) parts.push(`${esc(CLXWords.person(a.acknowledged_by_name, a.acknowledged_by))} is on it`);
  if (a.group_night) parts.push("at night");
  return parts.join(" · ");
}

function siteWideActions(a) {
  return `<div class="incident-actions">${
    a.state === "open"
      ? `<button class="btn btn-sm btn-primary" data-ack="${esc(a.incident_id)}">I'm on it</button>` : ""}${
    a.state !== "resolved"
      ? `<button class="btn btn-sm" data-resolve="${esc(a.incident_id)}">It's fixed</button>` : ""}</div>`;
}

const SITE_WIDE_WHY = "This can mean a power cut, a door left open, or someone "
  + "tampering with the fridge controls. Check now. Each unit also has its own alert below.";

function renderSiteWide(alerts) {
  const live = alerts.filter((a) => a.state !== "resolved");
  const banner = $("site-wide-banner");
  if (!live.length) { banner.hidden = true; banner.innerHTML = ""; return; }
  banner.hidden = false;
  banner.innerHTML = live.map((a) => `<div class="sitewide st-${esc(a.state)}">
      <div class="sitewide-kicker">Site-wide alert · ${esc(a.state)}</div>
      <div class="sitewide-title">${esc(a.breach_details)}</div>
      <p class="sitewide-why">${esc(SITE_WIDE_WHY)}</p>
      ${siteWideUnits(a)}
      <div class="sitewide-meta">${siteWideReach(a)}</div>
      ${siteWideActions(a)}
    </div>`).join("");
}

function siteWideCard(a) {
  return `<article class="incident sitewide-card st-${esc(a.state)}">
      <div class="incident-top">
        <span class="badge s-critical"><span class="dot" aria-hidden="true"></span>Site-wide</span>
        ${badge(a.state === "open" ? "critical" : a.state === "acknowledged" ? "elevated" : "low", a.state)}
        <span class="incident-title grow">${esc(a.catastrophe_type)}</span>
        <span class="card-note">${esc((a.group_units || []).length)} units · ${esc(CLXWords.outFor(a.out_since, a.out_until))}</span>
      </div>
      <div class="card-note">${esc(a.breach_details)}</div>
      ${siteWideUnits(a)}
      <div class="quote"><div class="quote-label">Text sent to everyone on call</div>${
        esc(a.dispatched_sms_text)}</div>
      <div class="card-note">${siteWideReach(a)}</div>
      ${siteWideActions(a)}
    </article>`;
}

/* The estate's own words. Every string that would otherwise say "sensor"
   goes through here, so a yacht owner reads "engine bays" and a data
   centre reads "racks" — from the same code, off the same data. */
/* An estate of one industry gets that industry's own word -- coolers,
   walk-ins, hangars -- which is what the people who work there say. An
   estate spanning several falls back to "asset", which is what nobody
   says. "Assets under watch" is a phrase from a spreadsheet, not from a
   kitchen, so the generic case says the plain thing instead. */
// A kitchen calls them fridges and freezers, whatever the sector's word
// for the thing being watched ("walk-in" for every restaurant unit read
// "WALK-INS ONLINE" over a milk fridge).
const GENERIC_NOUNS = new Set(["asset", "assets", "unit", "units", "walk-in", "walk-ins",
  "kitchen", "kitchens"]);

/* "about 1 minute", "3 minutes", "about 2 hours": never "0.73 min" or
   "1 minutes". */
function minutesWords(value) {
  const m = Number(value);
  if (!Number.isFinite(m) || m < 0.5) return "under a minute";
  if (m >= 90) {
    const h = Math.round(m / 60);
    return `about ${h} hour${h === 1 ? "" : "s"}`;
  }
  const n = Math.round(m);
  const about = Math.abs(m - n) > 0.05 ? "about " : "";
  return `${about}${n} minute${n === 1 ? "" : "s"}`;
}

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

/* Under the "Open alerts" figure: who has the ones somebody is handling,
   and how many nobody has answered. Every alert that is not closed is
   counted; the tile said 0 while Maya was handling one. */
function openAlertsNote(s) {
  const handled = s.handled_incidents || 0;
  const unanswered = s.unanswered_incidents ?? (s.open_incidents - handled);
  const parts = [];
  if (unanswered) parts.push(`${unanswered} nobody has answered`);
  if (handled) {
    const names = (s.handled_by || []).map((n) => CLXWords.person(n));
    parts.push(`${handled} being handled by ${names.length === 1 ? names[0]
      : names.length ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : "your team"}`);
  }
  if (s.escalation_due) parts.push(`${s.escalation_due} due for a call`);
  return parts.length ? parts.join(" · ") : "none right now";
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

/* The unit with an alert first, then one being watched, then one gone
   quiet; the calm ones after, in their usual order. */
const URGENCY = { alert: 0, paused: 1, watching: 2, offline: 3 };
function byUrgency(sensors) {
  const rank = (x) => URGENCY[(x.condition || {}).state] ?? 9;
  return sensors.map((x, i) => [x, i]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1])
    .map(([x]) => x);
}

/* How many units before the fleet's search and status filter appear. */
const FLEET_TOOLS_FROM = 6;

function renderFleet(sensors) {
  if (state.editing && document.querySelector(
    `.wait-edit[data-wait-for="${CSS.escape(state.editing.id)}"]:not([hidden])`)) return;
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
        <div class="fleet">${byUrgency(rows).map(sensorCard).join("")}</div>`;
    }).join("");
    return;
  }

  $("fleet").style.display = "";
  $("fleet").innerHTML = byUrgency(sensors).map(sensorCard).join("");
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

/* A unit's limit or alert wait, changed on its own card. The card stays
   put while it is open: the twenty-second refresh would otherwise redraw
   it under somebody's thumb. */
function closeUnitEditor() {
  state.editing = null;
  document.querySelectorAll(".wait-edit").forEach((box) => {
    box.hidden = true;
    box.innerHTML = "";
  });
}

function openUnitEditor(sensorId, which) {
  const sensor = state.data.sensors.find((x) => x.sensor_id === sensorId);
  const box = document.querySelector(`.wait-edit[data-wait-for="${CSS.escape(sensorId)}"]`);
  if (!sensor || !box) return;
  closeUnitEditor();
  state.editing = { id: sensorId, which };
  box.dataset.which = which;
  const name = esc((sensor.location_name || "").trim() || sensor.sensor_id);
  if (which === "wait") {
    const usual = sensor.alert_after_minutes_default;
    const now = sensor.alert_after_minutes_own == null ? usual : sensor.alert_after_minutes_own;
    const picks = [0, 5, 10, 20, 30, 60].map((m) => `<button class="f-tab" type="button"
        data-wait-pick="${m}" aria-pressed="${m === Number(now)}">${
        m === 0 ? "At once" : `${m} min`}${m === Number(usual) ? " (usual)" : ""}</button>`).join("");
    box.innerHTML = `<label for="wait-in-${esc(sensorId)}">How long may ${name} stay out of
        range before we text anybody?</label>
      <div class="wait-chips">${picks}</div>
      <div class="wait-row">
        <input id="wait-in-${esc(sensorId)}" type="number" inputmode="numeric" min="0" max="1440"
               step="1" value="${esc(now)}"> <span>minutes</span>
      </div>
      <p class="wait-help">A door open for a minute or a delivery wakes nobody. A reading far
        past the limit (${esc(sensor.alert_at_once_degrees)}°F over) is always sent at once.</p>
      <p class="wait-help wait-out" role="status"></p>
      <div class="wait-acts">
        <button class="btn btn-sm btn-go" type="button" data-edit-save="${esc(sensorId)}">Save</button>
        <button class="btn btn-sm" type="button" data-edit-default="${esc(sensorId)}">Use the usual ${
          esc(usual)} minutes</button>
        <button class="btn btn-sm" type="button" data-edit-cancel="1">Cancel</button>
      </div>`;
  } else {
    const unit = sensor.temperature_unit || state.unit || "F";
    const shown = sensor.danger_above_display;
    box.innerHTML = `<label for="limit-in-${esc(sensorId)}">Text us if ${name} goes over</label>
      <div class="wait-row">
        <input id="limit-in-${esc(sensorId)}" type="number" inputmode="decimal" step="0.5"
               value="${esc(shown == null ? "" : shown)}"> <span>°${esc(unit)}</span>
      </div>
      <p class="wait-help">The usual limit is right for most ${
        esc(sensor.unit_kind || "unit")}s. Change it only if your inspector or the food needs another.</p>
      <p class="wait-help wait-out" role="status"></p>
      <div class="wait-acts">
        <button class="btn btn-sm btn-go" type="button" data-edit-save="${esc(sensorId)}">Save</button>
        <button class="btn btn-sm" type="button" data-edit-default="${esc(sensorId)}">Use the usual limit</button>
        <button class="btn btn-sm" type="button" data-edit-cancel="1">Cancel</button>
      </div>`;
  }
  box.hidden = false;
  const input = box.querySelector("input");
  if (input) input.focus({ preventScroll: true });
}

/* The calm word for what a unit is doing, from the server's one
   `condition` (CLXWords.condition). A brand-new sensor said BREACHING, NO
   FORECAST, an orange "tuned" and a red OFFLINE before its first reading
   arrived: four alarms about nothing. Now it says "Waiting for its first
   reading". A unit out of range inside its wait says "Watching" and when
   we text, as /today and Problems do. */
const STATE_PILL = { quiet: "s-muted", ok: "s-good", watch: "s-warning", warm: "s-critical" };

function statePill(c) {
  return `<span class="badge plain ${STATE_PILL[c.kind] || "s-muted"}">`
       + `<span class="dot" aria-hidden="true"></span>${esc(c.word)}</span>`;
}

/* The forecast, only where the plan has one and only when it says
   something: "trend pending" and "no forecast" told a new customer
   nothing they could act on. */
function forecastNote(s) {
  if (!s.risk_level || s.risk_level === "unknown") return "";
  if (s.hours_until_breach !== null && s.hours_until_breach > 0
      && ["critical", "high", "elevated"].includes(s.risk_level)) {
    return `<span class="sensor-fore">Could be too warm in about ${esc(s.hours_until_breach)}h</span>`;
  }
  return "";
}

function sensorCard(s) {
  const c = CLXWords.condition(s);
  const unit = s.temperature_unit || state.unit;
  const reading = s.last_temperature_display;
  const temp = (reading === null || reading === undefined)
    ? "—" : reading.toFixed(1);
  const above = s.danger_above_display, below = s.danger_below_display;
  const bound = s.external_device_sn
    ? `Device ${esc(s.external_device_sn)}` : "Sends to us directly";
  const name = (s.location_name || "").trim() || s.sensor_id;
  const loud = c.state === "alert" || c.state === "paused";
  const wait = s.alert_after_minutes
    ? `We text if it stays out ${esc(minutesWords(s.alert_after_minutes))}`
    : "We text at the first reading out";

  return `<article class="sensor ${loud ? "is-breaching" : c.state === "watching" ? "is-watching" : ""}">
      <div class="sensor-top">
        <span class="sensor-name grow clickable" data-detail="${esc(s.sensor_id)}"
              role="button" tabindex="0">${keepWords(name)}</span>
        ${statePill(c)}
      </div>
      <div class="sensor-loc"><span class="sensor-id">${esc(s.sensor_id)}</span>${
        s.site_name ? ` · ${esc(s.site_name)}` : ""}</div>
      <div class="sensor-read">
        <span class="sensor-temp">${esc(temp)}</span>
        <span class="sensor-unit">°${esc(unit)}</span>
        <span class="nav-spacer"></span>
        ${forecastNote(s)}
      </div>
      ${c.line ? `<p class="sensor-state st-${esc(c.kind)}">${esc(c.line)}</p>` : ""}
      ${c.state === "waiting_first" ? "" : `<div class="spark-wrap">${sparkline(s)}</div>`}
      ${healthPills(s)}
      ${repairBlock(s)}
      ${c.state === "offline" ? `<a class="offline-help" href="/help#sensor-offline"
          >No signal: what to check</a>` : ""}
      <div class="sensor-meta">
        <span>${esc(bound)}</span>
        <span>Limit ${below !== null && below !== undefined
            ? esc(below) + "–" : "under "}${esc(above)}°${esc(unit)}${
          s.limit_own ? ' <span class="u-c-muted">(your own)</span>' : ""
        }</span>
        <span>${wait}${
          s.alert_after_minutes_own != null ? ' <span class="u-c-muted">(your own)</span>' : ""}</span>
      </div>
      <div class="sensor-actions">
        <button class="btn btn-sm" data-detail="${esc(s.sensor_id)}">History</button>
        <button class="btn btn-sm" data-tune="${esc(s.sensor_id)}">Change limit</button>
        <button class="btn btn-sm" data-wait="${esc(s.sensor_id)}">Alert wait</button>
        <button class="btn btn-sm" data-rekey="${esc(s.sensor_id)}">New key</button>
        <button class="btn btn-sm" data-retire="${esc(s.sensor_id)}">Remove</button>
      </div>
      <div class="wait-edit" data-wait-for="${esc(s.sensor_id)}" hidden></div>
    </article>`;
}

/* "Get a repair quote", on a unit the forecaster or the pattern check
   says is failing or creeping warmer (repairs.py). Nothing is sent from
   here: the button opens a confirm screen that says exactly what would be
   sent, and to how many companies (one). */
function repairBlock(s) {
  const r = s.repair;
  if (!r) return "";
  if (!r.offer) {
    return `<div class="repair-sent">Repair quote sent to ${esc(r.sent_to || "a repair company")}
      on ${esc(shortDate(r.sent_at))}.</div>`;
  }
  const canAsk = Boolean(state.user && state.user.role !== "viewer");
  const why = r.kind === "failing"
    ? "This unit looks like it is failing."
    : "This unit keeps getting warmer.";
  return `<div class="repair-offer"><span>${esc(why)}</span>${canAsk
    ? `<button class="btn btn-sm btn-primary" type="button"
         data-repair-quote="${esc(s.sensor_id)}">Get a repair quote</button>` : ""}</div>`;
}

async function openRepairQuote(sensorId) {
  let q;
  try {
    q = await api(`/api/repairs/quote/${encodeURIComponent(sensorId)}`);
  } catch (err) {
    toast(err.message, "err");
    return;
  }
  // Not a sensor drawer: the poll must not repaint over a half-filled form.
  state.drawerSensor = null;
  const head = `<div class="drawer-head">
      <div class="grow">
        <div class="drawer-title" id="drawer-title">Get a repair quote</div>
        <div class="drawer-sub">${esc(q.unit_name)} · ${esc(q.business_name)}</div>
      </div>
      <button class="btn btn-sm" id="drawer-close" type="button">Close</button>
    </div>`;
  if (!q.can_send) {
    $("drawer").innerHTML = head + `<p class="card-plain">${esc(q.again_after
      ? `A repair company was already sent this unit. You can ask again after ${shortDate(q.again_after)}.`
      : "This unit looks fine to us right now, so there is nothing to send.")}</p>`;
    showDrawer();
    $("drawer-close").addEventListener("click", closeDrawer);
    return;
  }
  const s = q.suggested || {};
  $("drawer").innerHTML = head + `
    <p class="card-plain">${esc(q.not_a_repairer)}</p>
    <h3 class="sub-head">What we saw</h3>
    <p class="repair-problem">${esc(q.problem)}</p>
    <form id="repair-form">
      <h3 class="sub-head">Who should they contact?</h3>
      <div class="field"><label for="rq-name">Name</label>
        <input id="rq-name" required minlength="2" maxlength="120"
               value="${esc(s.contact_name)}"></div>
      <div class="field"><label for="rq-phone">Phone</label>
        <input id="rq-phone" type="tel" maxlength="40" value="${esc(s.contact_phone)}"></div>
      <div class="field"><label for="rq-email">Email</label>
        <input id="rq-email" type="email" maxlength="200" value="${esc(s.contact_email)}"></div>
      <p class="sub">Leave the phone or the email empty and it is not sent. One of
        them is needed.</p>
      <h3 class="sub-head">Address of this location</h3>
      <div class="field"><label for="rq-street">Street</label>
        <input id="rq-street" required minlength="3" maxlength="200"
               autocomplete="street-address" value="${esc(s.street)}"></div>
      <div class="field"><label for="rq-city">City</label>
        <input id="rq-city" required minlength="2" maxlength="100"
               autocomplete="address-level2"></div>
      <div class="field"><label for="rq-postcode">ZIP or postcode</label>
        <input id="rq-postcode" required minlength="2" maxlength="20"
               autocomplete="postal-code"></div>
      <h3 class="sub-head">What the repair company gets</h3>
      <ul class="repair-list">${(q.what_is_sent || []).map((line) =>
        `<li>${esc(line)}</li>`).join("")}</ul>
      <p class="sub">${esc(q.never_sent)}</p>
      <label class="repair-consent"><input type="checkbox" id="rq-consent" required>
        <span>Send these details to one approved repair company that covers this
        address.</span></label>
      <div class="row">
        <button class="btn btn-sm btn-primary" type="submit">Send to one repair company</button>
        <button class="btn btn-sm" type="button" id="rq-not-now">Not now</button>
      </div>
    </form>`;
  showDrawer();
  $("drawer-close").addEventListener("click", closeDrawer);
  $("rq-not-now").addEventListener("click", closeDrawer);
  $("repair-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const out = await api("/api/repairs/quote", {
        method: "POST",
        body: JSON.stringify({
          sensor_id: q.sensor_id,
          contact_name: $("rq-name").value.trim(),
          contact_phone: $("rq-phone").value.trim(),
          contact_email: $("rq-email").value.trim(),
          street: $("rq-street").value.trim(),
          city: $("rq-city").value.trim(),
          postcode: $("rq-postcode").value.trim(),
          consent: $("rq-consent").checked,
        }),
      });
      const p = out.partner || {};
      $("drawer").innerHTML = head + `<div class="repair-done">
          <p>${esc(out.message)}</p>
          ${p.contact_phone ? `<p>Their phone: ${esc(p.contact_phone)}</p>` : ""}
          ${p.website ? `<p>Their website: ${esc(p.website)}</p>` : ""}
        </div>`;
      $("drawer-close").addEventListener("click", closeDrawer);
      refresh().catch(() => { /* the card catches up on the next poll */ });
    } catch (err) {
      toast(err.message, "err");
      button.disabled = false;
    }
  });
}

/* 12-point sparkline: recessive line, current point in the accent. */
function sparkline(sensor) {
  const values = sensor.spark || [];
  if (values.length < 2) {
    // One reading in: it is shown big above, so the line waits in words
    // that do not contradict it ("awaiting readings" sat under a live
    // 39.0°F, 3.26.0).
    const one = values.length === 1 || (sensor.last_temperature_display !== null
      && sensor.last_temperature_display !== undefined);
    return `<div class="u-h-40 u-d-grid u-place-center u-c-muted u-fs-11-5">
              ${one ? "the line starts with the next reading" : "waiting for its first reading"}</div>`;
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
    ? CLXWords.clock(hit.dataset.at)
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
    ? "" : `${shown.length} of ${state.data.sensors.length} shown`;
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

/* Why a text or call did not go, in words. The raw provider status
   ("NOT_CONFIGURED", "BUDGET_EXCEEDED") is what was shown before. */
const DELIVERY_WORDS = {
  not_configured: "texts are off on this server",
  budget_exceeded: "today's limit was reached",
  call_failed: "the call did not connect",
  send_failed: "the phone network refused it",
  failed: "it did not go through",
  undelivered: "it did not arrive",
  blocked: "that number is blocked",
  invalid_number: "the number looks wrong",
};

function deliveryWords(status) {
  const key = String(status || "").toLowerCase();
  return DELIVERY_WORDS[key] || key.replace(/_/g, " ") || "not sent";
}

function deliveryLine(record, channel) {
  const name = channel === "SMS" ? "Text" : channel === "Voice" ? "Call" : channel;
  if (!record) return `<span class="badge s-muted"><span class="dot"></span>${name} not tried</span>`;
  if (record.delivered) {
    return `<span class="badge s-good"><span class="dot"></span>${name} sent</span>`;
  }
  return `<span class="badge s-warning"><span class="dot"></span>`
       + `${name} not sent: ${esc(deliveryWords(record.status))}</span>`;
}

/* Every alert text failed. Said at the top of the card, because the card
   otherwise reads as if the roster has been told. */
function textsFailedNote(i) {
  // Texts switched off on this server (dry run) is not a failure: the
  // alert was written and nobody was meant to get it.
  if (i.texts_off) {
    return `<div class="card-note texts-off"><span class="badge s-muted">`
         + `<span class="dot"></span>Not sent</span> `
         + "Texts are off on this server, so nobody was texted.</div>";
  }
  if (!i.alert_texts_failed || i.state === "resolved") return "";
  const next = i.next_sms_retry_at && i.state === "open"
    ? ` Sending again at ${CLXWords.clock(i.next_sms_retry_at)}.`
    : " Phone the roster yourself.";
  return `<div class="card-note" role="alert"><span class="badge s-critical">`
       + `<span class="dot"></span>Alert texts failed</span> `
       + `No text has reached anyone.${esc(next)}</div>`;
}

/* What happened, in order. A stack of timestamps buried in JSON is not an
   answer to "why did nobody pick this up", and that is the question asked
   after every incident that goes wrong. */
function incidentTimeline(i) {
  const when = (iso) => (iso ? CLXWords.dateTime(iso) : "");
  const steps = [];
  const u = `°${i.temperature_unit || state.unit}`;
  const first = i.first_out_display ?? i.first_out_fahrenheit;
  const peak = i.peak_display ?? i.peak_fahrenheit;

  // When it went out of range, which with the wait is before the alert
  // opened: the same "out since" as the unit card and /today, with the
  // reading taken then. The worst reading is its own line, at its own
  // time: "Went out of range: 60.5°F, 1:16 AM" was the peak an hour
  // later, beside the time of a 47.2°F reading. An alert from before the
  // two were kept says only when.
  steps.push({ cls: "hit", when: i.out_since || i.opened_at,
    text: first !== null && first !== undefined
      ? `Went out of range: ${first}${u}` : "Went out of range" });
  const peakAt = i.peak_at && Date.parse(i.peak_at);
  const firstAt = Date.parse(i.out_since || i.opened_at);
  if (peak !== null && peak !== undefined && peak !== first) {
    steps.push({ cls: "hit", when: i.peak_at || null,
      text: `${i.resolved_at ? "At its worst" : "Worst so far"}: ${peak}${u}`,
      order: peakAt || firstAt });
  } else if ((first === null || first === undefined)
             && (i.temperature_display ?? i.temperature_fahrenheit) != null) {
    steps.push({ cls: "hit", when: null,
      text: `Last reading out of range: ${i.temperature_display ?? i.temperature_fahrenheit}${u}`,
      order: firstAt });
  }

  const texted = (i.sms_fanout || []).length || (i.sms_delivery ? 1 : 0);
  if (i.texts_off) {
    steps.push({ cls: "hit", when: i.opened_at,
      text: "Alert opened. Not sent: texts are off on this server" });
  } else if (texted) {
    const landed = (i.sms_fanout || []).filter((f) => f.delivered).length
      || (i.sms_delivery && i.sms_delivery.delivered ? 1 : 0);
    steps.push({ cls: "hit", when: i.opened_at,
      text: `Alert sent: texted ${texted} ${texted === 1 ? "person" : "people"} · ${landed} delivered` });
  } else {
    steps.push({ cls: "hit", when: i.opened_at, text: "Alert opened" });
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
        : "Called the list — nobody answered" });
  }
  if (i.acknowledged_at) {
    steps.push({ cls: "ack", when: i.acknowledged_at,
      text: CLXWords.person(i.acknowledged_by_name, i.acknowledged_by) + " said they were on it" });
  }
  if (i.fix_reported_at && !i.resolved_at) {
    steps.push({ cls: "ack", when: i.fix_reported_at,
      text: CLXWords.person(i.fix_reported_by_name, i.fix_reported_by)
        + " said it was fixed. It closes by itself once it is back in range." });
  }
  if (i.resolved_at) {
    const by = i.resolved_by ? CLXWords.person(i.resolved_by_name, i.resolved_by) : "";
    const text = i.back_to_normal_on_its_own || !by
      ? (i.resolved_note || "Closed")
      : `Closed by ${by}${i.closed_out_of_range ? " while still out of range" : ""}`
        + (i.close_note ? `: ${i.close_note}` : "");
    steps.push({ cls: "done", when: i.resolved_at, text });
  }
  if (!i.resolved_at) {
    steps.push({ cls: "", when: null,
      text: `Out of range for ${CLXWords.outFor(i.out_since || i.opened_at)} so far` });
  }

  // In the order things happened: the worst reading can come after the
  // alert went out, or after somebody said they were on it.
  const at = (st) => (st.order !== undefined ? st.order
    : st.when ? Date.parse(st.when) : Infinity);
  steps.forEach((st, n) => { st.n = n; });
  steps.sort((a, b) => (at(a) - at(b)) || (a.n - b.n));
  return `<ul class="timeline">${steps.map((st) =>
    `<li class="${st.cls}">${esc(st.text)}`
    + (st.when ? ` <span class="when">${esc(when(st.when))}</span>` : "")
    + `</li>`).join("")}</ul>`;
}

/* A unit's name as the kitchen says it, for an alert about it. */
/* A unit's name for a heading, escaped, with each short hyphenated word
   kept whole: "Walk-in fridge" broke as "Walk- / in / fridge" beside its
   status label on a phone. */
function keepWords(name) {
  return String(name ?? "").split(/(\s+)/).map((word) =>
    word.includes("-") && word.length <= 24
      ? `<span class="keep-word">${esc(word)}</span>` : esc(word)).join("");
}

function unitNameFor(sensorId) {
  const s = ((state.data && state.data.sensors) || []).find((x) => x.sensor_id === sensorId);
  return s ? ((s.location_name || "").trim() || s.sensor_id) : sensorId;
}

/* Units out of range inside their wait: nobody texted yet, and Problems
   says so in the same words as the card, not "within its limits". */
function watchingRows(sensors) {
  return (sensors || []).filter((x) => x.condition && x.condition.state === "watching")
    .map((x) => {
      const c = CLXWords.condition(x);
      return `<article class="incident st-watching">
        <div class="incident-top">${statePill(c)}
          <span class="incident-title grow">${esc(unitNameFor(x.sensor_id))}</span>
          <span class="card-note">${esc(x.last_temperature_display)}°${esc(x.temperature_unit || state.unit)}</span>
        </div>
        <div class="card-note">${esc(c.line)}</div>
      </article>`;
    }).join("");
}

function renderIncidents(incidents, entitlements, siteWide) {
  const groups = (siteWide || []).map(siteWideCard).join("");
  const watching = watchingRows(state.data && state.data.sensors);
  if (!incidents.length && (groups || watching)) {
    $("incidents").innerHTML = groups + watching;
    return;
  }
  if (!incidents.length) {
    const total = (state.data && state.data.sensors.length) || 0;
    $("incidents").innerHTML = `<div class="empty">${
      total
        ? (total === 1 ? `Your ${noun(1)} is within its limits.`
          : `All ${total} ${noun(total)} are within their limits.`)
        : `Nothing to report yet.<span class="sub">Anything that goes out of
             its safe range shows up here, once something is being
             watched.</span>` + nextSteps([["sensor", `Add a ${noun(1)}`]])
    }</div>`;
    return;
  }

  $("incidents").innerHTML = groups + watching + incidents.map((i) => {
    const canEscalate = i.state === "open" && !i.voice_escalated_at
                        && entitlements.voice_escalation;
    const who = CLXWords.person(i.acknowledged_by_name, i.acknowledged_by);
    const pill = i.state === "open"
      ? { kind: "warm", word: "Nobody has answered" }
      : i.state === "acknowledged" ? { kind: "watch", word: `${who} is on it` }
        : { kind: "ok", word: "Closed" };
    const outWords = CLXWords.outFor(i.out_since || i.opened_at, i.out_until);

    return `<article class="incident st-${esc(i.state)}">
      <div class="incident-top">
        ${statePill(pill)}
        <span class="incident-title grow">${keepWords(unitNameFor(i.sensor_id))}</span>
        <span class="card-note incident-for">${i.state === "resolved" ? "was out" : "out"} for ${esc(outWords)}</span>
      </div>
      ${i.state === "resolved" && (i.peak_display ?? i.peak_fahrenheit) != null
        ? "" : `<div class="card-note">${esc(i.breach_details)}</div>`}
      ${textsFailedNote(i)}
      ${i.state === "resolved" && i.food_protected
        ? `<p class="food-saved"><b>${esc(i.food_protected.words)}</b>
             <span class="sub">${esc(i.food_protected.note)}</span></p>` : ""}
      ${incidentTimeline(i)}

      <details class="incident-more">
        <summary>The text we ${i.texts_off ? "wrote" : "sent"}${i.voice_script ? " and the call" : ""}</summary>
        <div class="quote">
          <div class="quote-label">The text we ${i.texts_off ? "wrote (not sent: texts are off here)" : "sent"}</div>
          ${esc(i.dispatched_sms_text)}
        </div>
        ${i.voice_script ? `<div class="quote">
          <div class="quote-label">What the phone call said</div>${esc(i.voice_script)}</div>` : ""}
        <div class="row u-gap-6">
          ${deliveryLine(i.texts_landed && !(i.sms_delivery && i.sms_delivery.delivered)
            ? { delivered: true, provider_sid: "on a re-send" } : i.sms_delivery, "SMS")}
          ${i.voice_escalated_at ? deliveryLine(i.voice_delivery, "Call") : ""}
        </div>
      </details>

      <div class="incident-actions">
        ${i.state === "open"
          ? `<button class="btn btn-sm btn-primary" data-ack="${esc(i.incident_id)}">I'm on it</button>` : ""}
        ${i.state !== "resolved"
          ? `<button class="btn btn-sm" data-resolve="${esc(i.incident_id)}">It's fixed</button>` : ""}
        ${canEscalate
          ? `<button class="btn btn-sm" data-escalate="${esc(i.incident_id)}">Call now</button>` : ""}
        <button class="btn btn-sm" data-packet="${esc(i.incident_id)}">Insurance pack</button>
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

/* An alert nobody has dealt with, pinned at the top of every view of the
   console until it is. Signing in with an alert open put Acknowledge
   about 3,100px down a phone, under Getting started, the tiles and the
   fleet. Now the two buttons are the first thing on the screen, on every
   tab, with the unit, how warm and for how long. */
function renderNeedsBar(d) {
  const bar = $("needs-bar");
  if (!bar) return;
  const live = (d.incidents || []).filter((i) => i.state !== "resolved" && !i.site_wide);
  const wide = (d.site_wide_alerts || []).filter((i) => i.state !== "resolved");
  const all = wide.concat(live).sort((a, b) =>
    (a.state === "open" ? 0 : 1) - (b.state === "open" ? 0 : 1)
    || String(a.opened_at).localeCompare(String(b.opened_at)));
  if (!all.length) {
    bar.hidden = true;
    bar.innerHTML = "";
    measureNav();
    return;
  }
  const i = all[0];
  const unit = (d.sensors || []).find((x) => x.sensor_id === i.sensor_id);
  const deg = unit && unit.last_temperature_display != null
    ? `${unit.last_temperature_display}°${unit.temperature_unit || state.unit}`
    : `${i.temperature_display ?? i.temperature_fahrenheit}°${i.temperature_unit || state.unit}`;
  const warm = !(unit && unit.condition && unit.condition.too === "cold");
  const what = i.site_wide
    ? `<b>Several fridges are warming at once.</b> ${esc(i.breach_details || "")}`
    : `<b>${esc(unitNameFor(i.sensor_id))}</b> is ${esc(deg)}, too ${warm ? "warm" : "cold"} for ${
        esc(CLXWords.outFor(i.out_since || i.opened_at))}.`;
  const who = i.state === "acknowledged"
    ? (i.fix_reported_at
        ? `${esc(CLXWords.person(i.fix_reported_by_name, i.fix_reported_by))} said it is fixed; it closes once it is back in range.`
        : `${esc(CLXWords.person(i.acknowledged_by_name, i.acknowledged_by))} is on it.`)
    : (i.voice_escalated_at ? "Nobody has answered, and we have phoned." : "Nobody has answered yet.");
  const more = all.length - 1;
  bar.innerHTML = `<div class="needs-bar-in">
      <span class="needs-bar-kicker">Needs you now</span>
      <p class="needs-bar-what">${what} <span class="needs-bar-who">${who}</span></p>
      <span class="needs-bar-acts">
        ${i.state === "open" ? `<button class="btn btn-sm btn-primary" type="button"
            data-ack="${esc(i.incident_id)}">I'm on it</button>` : ""}
        <button class="btn btn-sm" type="button" data-resolve="${esc(i.incident_id)}">It's fixed</button>
        ${more ? `<button class="btn btn-sm linkish" type="button" data-needs-more="1">${
          more} more</button>` : ""}
      </span>
    </div>`;
  bar.hidden = false;
  measureNav();
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

/* "alert.test_sent" -> "Alert test sent": the trail's own codes, read as
   words. */
const AUDIT_NAMES = { "legal.accepted": "Agreements accepted",
  "alert.test_sent": "Test alert", "alert.test_failed": "Test alert did not go out" };

function auditWords(action) {
  if (AUDIT_NAMES[action]) return AUDIT_NAMES[action];
  const words = String(action || "").replace(/[._]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "";
}

/* What an activity line says, in words (3.26.0). The record keeps each
   agreement's fingerprint ("acceptable-use@3b70…"), which is what makes it
   evidence; the line on the screen says what was agreed to, and the exact
   record is on hover. */
const AGREEMENTS = {
  "acceptable-use": "the acceptable-use rules",
  privacy: "the privacy notice",
  sla: "the service levels",
  terms: "the subscription agreement",
};

function auditDetail(e) {
  const detail = String(e.detail || "");
  if (e.action !== "legal.accepted") return detail;
  const version = (detail.match(/accepted v([0-9.]+)/) || [])[1];
  const names = Array.from(detail.matchAll(/([a-z-]+)@[0-9a-f]{6,}/g))
    .map((m) => AGREEMENTS[m[1]] || m[1].replace(/-/g, " "));
  if (!names.length) return detail;
  const list = names.length === 1 ? names[0]
    : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  const when = /at sign-up/.test(detail) ? " at sign-up" : "";
  return `Agreed to ${list}${version ? ` (version ${version})` : ""}${when}.`;
}

function renderAudit(trail) {
  if (!trail.entries.length) {
    $("audit-body").innerHTML = `<div class="empty">No activity recorded yet.</div>`;
    return;
  }
  $("audit-body").innerHTML = trail.entries.map((e) => {
    const when = CLXWords.dateTime(e.at);
    return `<div class="u-p-7-0 u-bb">
      <div class="u-d-flex u-gap-8 u-justify-between">
        <span class="u-fs-12-5 u-fw-500">${esc(auditWords(e.action))}</span>
        <span class="u-fs-11-5 u-c-muted u-ws-nowrap">${esc(when)}</span>
      </div>
      <div class="u-fs-12 u-c-ink-2 u-mt-2"${auditDetail(e) !== e.detail
        ? ` title="${esc(e.detail)}"` : ""}>${esc(auditDetail(e))}</div>
      <div class="u-fs-11-5 u-c-muted" title="${esc(e.actor)}">${esc(CLXWords.person(null, e.actor))}</div>
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
         <span class="u-c-muted">${tenant.voice_escalation
           ? "Phone-call escalation and the forecast are on."
           : "Texts and the site-wide alert are on."} Your first invoice
           follows by email.</span>
       </div>`;
  }
  const starter = (state.data && state.data.starter) || null;
  if (tenant && tenant.plan === "starter") return starterBlock(tenant, starter);
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
        Growth and Enterprise add phone calls and the 12-hour forecast;
        Starter keeps texts. You are invoiced afterwards — nothing is
        charged here and no card is asked for.</div>
      <div class="u-d-flex u-gap-8 u-flex-wrap">
        ${starter ? `<button class="btn btn-sm" data-upgrade="starter">
          Starter · ${esc(starter.monthly_label)} a month · 1 location,
          ${esc(starter.max_units)} units</button>` : ""}
        <button class="btn btn-sm btn-primary" data-upgrade="growth">
          Growth · 50 units</button>
        <button class="btn btn-sm" data-upgrade="enterprise">
          Enterprise · 1000 units</button>
      </div>
      ${starter ? `<div class="u-fs-11-5 u-c-muted u-mt-7">Starter is for a
        caf&eacute; or shop: texts and the site-wide alert, no phone
        calls.</div>` : ""}
      <div id="upgrade-out" class="u-fs-11-5 u-mt-7"></div>
    </div>`;
}

/* Starter's way up. The fourth unit and the second location are refused
   with the same words; this is where the owner can act on them. */
function starterBlock(tenant, starter) {
  const units = starter ? starter.max_units : 3;
  const price = starter ? starter.monthly_label : "";
  const summary = `<div class="u-fs-12-5 u-c-ink-2 u-mb-3">Starter${
      price ? ` · ${esc(price)} a month` : ""}</div>
    <div class="u-fs-11-5 u-c-muted u-mb-8">One location and up to
      ${esc(units)} fridges or freezers, texts and the site-wide alert
      included. A fourth unit or a second location needs Growth, which adds
      phone calls and the forecast. Moving bills the rate card from your
      next invoice, for the days after the move only; nothing is charged
      here.</div>`;
  if (!(state.user && state.user.role === "owner")) {
    return `<div class="u-bt u-mt-9 u-pt-10">${summary}
      <div class="u-fs-11-5 u-c-muted">An owner can move the account to
        Growth from here.</div></div>`;
  }
  return `<div class="u-bt u-mt-9 u-pt-10">${summary}
      <button class="btn btn-sm btn-primary" data-upgrade="growth">Move to
        Growth · 50 units</button>
      <div id="upgrade-out" class="u-fs-11-5 u-mt-7"></div>
    </div>`;
}

/* A change of plan is shown before it is made: the plan, the price, the
   term, what cancelling means and the trial days left. One tap used to
   commit a café to a twelve-month plan; now nothing changes until "Yes,
   move", and the server refuses the change without that yes as well. */
let pendingPlan = null;

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-upgrade]");
  if (!button) return;
  const plan = button.getAttribute("data-upgrade");
  const out = $("upgrade-out");
  let terms;
  try {
    terms = await api(`/api/licenses/me/plan/terms?plan=${encodeURIComponent(plan)}`);
  } catch (err) {
    if (out) out.innerHTML = `<span class="s-warning">${esc(err.message)}</span>`;
    else toast(err.message, "err");
    return;
  }
  pendingPlan = plan;
  $("plan-confirm-title").textContent = `Move to ${terms.name}?`;
  const rows = [
    ["Plan", `${terms.name}: up to ${terms.max_units.toLocaleString()} units, `
      + (terms.calls ? "texts and phone calls." : "texts, no phone calls.")],
    ["Price", terms.price_words],
    ["How long", terms.term_words],
    ["If you cancel", terms.cancel_words],
  ];
  if (terms.trial_words) rows.push(["Your free trial", terms.trial_words]);
  $("plan-confirm-terms").innerHTML = rows.map(([k, v]) =>
    `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");
  $("plan-confirm-out").textContent = "";
  $("plan-confirm-yes").disabled = false;
  $("plan-confirm-yes").textContent = `Yes, move to ${terms.name}`;
  const dialog = $("plan-confirm");
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
});

function closePlanConfirm() {
  pendingPlan = null;
  const dialog = $("plan-confirm");
  if (typeof dialog.close === "function" && dialog.open) dialog.close();
  else dialog.removeAttribute("open");
}

$("plan-confirm-no").addEventListener("click", closePlanConfirm);

$("plan-confirm-yes").addEventListener("click", async () => {
  const plan = pendingPlan;
  if (!plan) return;
  $("plan-confirm-yes").disabled = true;
  $("plan-confirm-out").textContent = "Moving…";
  try {
    await api("/api/licenses/me/plan", {
      method: "POST",
      body: JSON.stringify({ plan, confirm: true }),
    });
    closePlanConfirm();
    // Long enough to be read, short enough not to become a permanent banner.
    state.justUpgradedTo = plan;
    setTimeout(() => { state.justUpgradedTo = null; }, 30000);
    logToConsole(`Plan changed to ${plan}.`, "ok");
    await refresh();
  } catch (err) {
    // Left open with the reason: a seat-count refusal is something they
    // can act on, and the dialog still says what they were agreeing to.
    $("plan-confirm-yes").disabled = false;
    $("plan-confirm-out").textContent = err.message;
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

  // A trial that fits Starter is shown Starter's flat price: a one-fridge
  // bakery was shown "$149/mo", the restaurant location price it would
  // only pay on Growth, while sign-up said Starter is $59.
  const after = subscription.after_trial;
  if (after) {
    $("bill-body").innerHTML = `
      <div class="u-d-flex u-justify-between u-gap-10 u-p-5-0 u-fs-12-5">
        <span class="u-c-ink-2">${esc(after.plan_name)}
          <span class="u-c-muted">· ${esc(after.description)}</span></span>
        <span class="u-tnum">$${after.monthly_total_usd.toLocaleString()}</span>
      </div>
      <div class="u-bt u-mt-8 u-pt-8 u-d-flex u-justify-between u-items-baseline">
        <span class="u-fs-12-5 u-c-ink-2">After the trial, a month</span>
        <span class="u-fs-20 u-fw-650 u-ls-n01em">$${after.monthly_total_usd.toLocaleString()}</span>
      </div>
      <div class="u-ta-right u-fs-11-5 u-c-muted">or $${after.yearly_total_usd.toLocaleString()}
        paid yearly (${esc(after.yearly_label || "")})</div>
      <p class="card-plain u-mt-8">${esc(after.words)}</p>` + upgradeBlock(tenant);
    return;
  }

  const lines = subscription.line_items.map((l) => l.chain_discount ? `
    <div class="u-d-flex u-justify-between u-gap-10 u-p-5-0 u-fs-12-5">
      <span class="s-good">${esc(l.description)}</span>
      <span class="u-tnum s-good">−$${Math.abs(l.line_total_usd).toLocaleString()}</span>
    </div>` : `
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
           ${roi.quantified_saves} real failure${roi.quantified_saves === 1 ? "" : "s"}
           caught in time (false alarms and short blips are not counted)${
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
         $${subscription.annual_total_usd.toLocaleString()} a year paid monthly${
           subscription.yearly_total_usd !== undefined
             ? `, or $${subscription.yearly_total_usd.toLocaleString()} paid yearly
                (${esc(subscription.yearly_label || "")})` : ""}</div>`
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

  const yearly = (contract.yearly || {});
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
           ${esc(yearly.headline || "Pay yearly")}${yearly.yearly_usd
             ? ` — ${money(yearly.yearly_usd)} a year instead of ${
                 money(yearly.twelve_months_usd)}` : ""}, one invoice a year
         </label>
         <div class="u-fs-11-5 u-c-muted u-mt-7">${esc(yearly.cancellation || "")}</div>
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
  const isYearly = c.billing_interval === "annual";
  const other = isYearly ? "monthly" : "yearly";
  const switching = c.switching_to
    ? `<div class="card-note u-mt-9">Switching to ${esc(c.switching_to)} billing on
         ${esc((c.switches_at || "").slice(0, 10))}, when what you have paid
         for runs out.</div>`
    : "";
  body.innerHTML = `
    <div class="u-fs-12-5 u-c-muted u-lh-1-6">
      ${esc(isYearly ? `Billed yearly (${yearly.label || ""})` : "Billed monthly")},
      ${c.escalator_percent}% a year, ${
        c.auto_renew ? "renews automatically" : "does not auto-renew"}.
      Next invoice ${esc((c.next_billing_at || "").slice(0, 10))}.
    </div>
    ${switching}
    <table class="term-table">
      <thead><tr><th>Term</th><th>Rate</th><th>Months free</th><th>Payable</th></tr></thead>
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
      <button class="btn btn-sm" id="contract-interval">${
        c.switching_to ? `Stay ${isYearly ? "yearly" : "monthly"}`
                       : `Switch to ${other} at renewal`}</button>
    </div>
    <div class="u-fs-11-5 u-c-muted u-mt-7">${esc(yearly.cancellation || "")}</div>`;

  wireAddOnButtons(c);

  $("contract-interval").addEventListener("click", async () => {
    const wanted = c.switching_to ? (isYearly ? "yearly" : "monthly") : other;
    try {
      const out = await api("/api/contracts/billing-interval", {
        method: "POST", body: JSON.stringify({ billing_interval: wanted }) });
      toast(out.message, "ok");
      refresh();
    } catch (err) { toast(err.message, "err"); }
  });

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

/* Pause or cancel (retention.py). The two offers come first, and the
   cancel button is right under them, always: never behind another screen. */
function renderLeaving(d) {
  const owner = Boolean(state.user && state.user.role === "owner");
  const body = $("pause-body");
  $("pause-note").textContent = d.paused ? "paused" : "";
  if (d.paused) {
    body.innerHTML = `<div class="paused-box">${esc(d.paused.words)}</div>
      ${owner ? `<div class="row">
        <button class="btn btn-sm btn-primary" type="button" data-resume-now="1">Come back now</button>
        ${d.cancel.possible ? `<button class="btn btn-sm danger-btn" type="button"
            data-cancel-plan="1">Cancel my plan</button>` : ""}
      </div>` : `<p class="card-plain">Only an owner can end the pause or cancel.</p>`}`;
    return;
  }
  if (d.on_trial) {
    body.innerHTML = `<div class="empty u-p-16-0">Nothing to pause or cancel during a
      free trial.<span class="sub">It ends on its own, and there is nothing to pay.</span></div>`;
    return;
  }
  const options = (d.pause_options || []).map((o) => `
    <button class="btn btn-sm" type="button" data-pause-months="${esc(o.months)}"
            ${owner ? "" : "disabled"}>
      <span>Pause for ${esc(o.label)}</span>
      <span class="u-c-muted">back on ${esc(o.resume_on)}</span>
    </button>`).join("");
  const offer = d.offer || {};
  const stay = offer.available ? `<div class="stay-offer">
      <b>Or stay, with ${esc(offer.words)}.</b>
      ${offer.saving_usd ? ` That is about ${money(offer.saving_usd)} off.` : ""}
      Once only.
      <div class="u-mt-8"><button class="btn btn-sm btn-primary" type="button" data-stay="1"
        ${owner ? "" : "disabled"}>Stay with ${esc(offer.percent)}% off</button></div>
    </div>` : "";
  const cancel = d.cancel.possible
    ? `<div class="cancel-line"><span class="grow">${esc(d.cancel.rule)}</span>
        <button class="btn btn-sm danger-btn" type="button" data-cancel-plan="1"
          ${owner ? "" : "disabled"}>Cancel my plan</button></div>`
    : `<div class="cancel-line">${esc(d.cancel.words)}</div>`;
  const limit = d.pause_limit_words ? ` ${esc(d.pause_limit_words)}` : "";
  const none = options ? "" : `<p class="card-plain">No pause is left in the last
    12 months just now.</p>`;
  body.innerHTML = `<p class="card-plain">${esc(d.pause_words)}${limit}</p>
    <div class="pause-options">${options}</div>${none}${stay}${cancel}
    ${owner ? "" : `<p class="card-plain u-mt-8">Only an owner can pause or cancel.</p>`}`;
}

/* The founding member code (founders.py), on the Bills card. Shown only
   while there is a code, or once this business has used one. It can be
   entered by an owner until the first bill is paid. */
function renderFounder(f) {
  const box = $("founder-box");
  if (!f || !f.show) { box.hidden = true; box.innerHTML = ""; return; }
  const owner = Boolean(state.user && state.user.role === "owner");
  box.hidden = false;
  if (f.taken) {
    box.innerHTML = `<b>${esc(f.words)}.</b> <span class="u-c-muted">${esc(f.note)}</span>
      <div class="card-plain">${esc(f.yearly_words)}.</div>`;
    return;
  }
  const form = f.can_enter && owner
    ? `<div class="row u-mt-8">
        <input id="founder-code" maxlength="40" autocomplete="off"
               placeholder="Founding member code">
        <button class="btn btn-sm btn-primary" type="button" data-founder-use="1">Use code</button>
      </div>`
    : (f.can_enter ? `<div class="card-plain">Only an owner can enter it.</div>` : "");
  box.innerHTML = `<b>Have a founding member code?</b> ${esc(f.words)}.
    <span class="u-c-muted">${esc(f.note)}</span>
    <div class="card-plain">${esc(f.yearly_words)}.</div>${form}`;
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
  // A café on a trial or Starter was shown "Not on this contract — $14,364
  // a year" of estate add-ons. That list is for estates.
  if (smallAccount()) return "";
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
         No bills yet.
         <span class="sub">${state.data && state.data.tenant
             && state.data.tenant.plan === "trial"
           ? "You are on the free trial, so there is nothing to pay. Your bills will show here once you choose a plan."
           : "Your bills will show here, each with what it covers and whether it is paid."}</span>
         ${nextSteps([["plans", "See the plans"]])}
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
  // Empty, it is not shown on any tab (applyFinder reads data-empty).
  card.dataset.empty = seen.count ? "" : "1";
  card.hidden = !seen.count || !cardInFinder(card);
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

  // Nothing connected yet: the card's own paragraph already says what
  // this is for, so the server's note is not printed under it again.
  if (!seen.count) {
    $("poller-body").innerHTML = "";
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
    // Only the broken-source warning: the card's paragraph already says
    // what this is for.
    + (seen.broken ? `<div class="sub u-mt-8">${esc(seen.note)}</div>` : "");
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

/* ---------------- bring a friend ----------------
   Owners only (the server refuses anyone else with 403, drawn here as a
   sentence rather than an error). The business that joined is shown by
   its name and nothing else. */

function renderReferrals(seen) {
  const body = $("referral-body");
  if (!seen || seen.notOwner) {
    $("referral-note").textContent = "Owners only";
    body.innerHTML = `<div class="sub">Only an owner can see the referral
      code and what it has earned.</div>`;
    return;
  }
  $("referral-note").textContent = seen.earned_usd
    ? `${money(seen.earned_usd)} earned`
    : `${seen.joined.length} joined`;
  // The configured address when there is one; this page's own otherwise,
  // and the bare path where the page has no web address of its own.
  const link = seen.link.url || (/^https?:$/.test(location.protocol)
    ? location.origin + seen.link.path : seen.link.path);
  const joined = seen.joined.length
    ? seen.joined.map((j) => `
      <div class="fc-row">
        <span class="grow">
          <span class="fc-id">${esc(j.business)}</span>
          <span class="sub">${esc(j.status_words)}</span>
        </span>
        <span class="sub">${esc(String(j.joined_at || "").slice(0, 10))}</span>
      </div>`).join("")
    : `<div class="sub">Nobody has joined with your code yet.</div>`;
  const earned = seen.earned.map((c) => `
      <div class="fc-row">
        <span class="fc-when">${money(c.amount_usd)}</span>
        <span class="grow">
          <span class="sub">${esc(c.reason)} · ${esc(c.state)}${
            c.used_on.length ? ` · ${c.used_on.map((u) => esc(u.number)).join(", ")}` : ""}</span>
        </span>
      </div>`).join("");
  body.innerHTML = `
    <div class="u-d-flex u-gap-8 u-items-center u-flex-wrap u-mb-8">
      <code class="u-ff-mono u-fs-16 u-fw-650" id="referral-code">${esc(seen.code)}</code>
      <button class="btn btn-sm" type="button" id="referral-copy"
              data-link="${esc(link)}">Copy the link</button>
    </div>
    <div class="u-fs-12 u-c-muted u-wb-break-all u-mb-8">${esc(link)}</div>
    <div class="u-fs-12 u-c-ink-2 u-mb-8">They get a ${esc(seen.trial_days)}-day
      trial. You get ${money(seen.reward_usd)} off, one month on one location,
      when they pay their first invoice. ${esc(seen.rewards_left)} of
      ${esc(seen.rewards_per_year)} rewards left this year.</div>
    <div class="u-fs-12-5 u-c-ink-2 u-mt-10 u-mb-3">Who joined</div>${joined}
    ${earned ? `<div class="u-fs-12-5 u-c-ink-2 u-mt-10 u-mb-3">What you earned</div>${earned}` : ""}
    ${seen.credit_waiting_usd ? `<div class="sub u-mt-8">${money(seen.credit_waiting_usd)}
      comes off your next invoice.</div>` : ""}`;
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("#referral-copy");
  if (!button) return;
  try {
    await navigator.clipboard.writeText(button.dataset.link);
    toast("Copied the link.", "ok");
  } catch (_) {
    toast(button.dataset.link);
  }
});

/* ---------------- proof of monitoring ----------------
   Owners only (the server refuses anyone else with 403, drawn here as a
   sentence). A report opens on its own page to print; its link is the
   one thing an owner hands to an insurer, and can be switched off. */

function monthValue(date) {
  return date.toISOString().slice(0, 7);
}

function proofLink(report) {
  return report.link.url || (/^https?:$/.test(location.protocol)
    ? location.origin + report.link.path : report.link.path);
}

function renderProofReports(seen) {
  const body = $("proof-report-body");
  const form = $("proof-report-form");
  if (!seen || seen.notOwner) {
    $("proof-report-note").textContent = "Owners only";
    form.hidden = true;
    body.innerHTML = `<div class="sub">Only an owner can make and share a proof
      of monitoring report.</div>`;
    return;
  }
  form.hidden = false;
  const site = $("proof-report-site");
  const chosen = site.value;
  site.innerHTML = `<option value="">Every location</option>` + seen.locations.map(
    (l) => `<option value="${esc(l.site_id)}">${esc(l.name)}</option>`).join("");
  if (chosen) site.value = chosen;
  const month = $("proof-report-month");
  const now = new Date();
  month.max = monthValue(now);
  month.min = seen.earliest_month;
  if (!month.value) {
    const last = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1));
    month.value = monthValue(last) < seen.earliest_month ? monthValue(now) : monthValue(last);
  }
  $("proof-report-note").textContent = seen.reports.length
    ? `${seen.reports.length} made` : "none yet";
  const rows = seen.reports.map((r) => {
    const live = r.state === "genuine";
    return `<div class="u-p-7-0 u-bb">
      <span class="u-d-block u-fs-13 u-fw-500">${esc(r.month_label)} · ${esc(r.location)}</span>
      <span class="u-d-block u-fs-11-5 u-c-muted">${live ? "Link on" : esc(r.state_words)}
        · code ${esc(r.verify_code)}</span>
      <div class="u-d-flex u-gap-8 u-flex-wrap u-mt-8">
        <a class="btn btn-sm" href="/proof-report/${encodeURIComponent(r.report_id)}"
           target="_blank" rel="noopener">Open</a>
        ${live ? `<button class="btn btn-sm proof-copy" type="button"
           data-link="${esc(proofLink(r))}">Copy link</button>
        <button class="btn btn-sm proof-revoke" type="button"
           data-id="${esc(r.report_id)}">Switch off</button>` : ""}
      </div>
    </div>`;
  }).join("");
  body.innerHTML = (rows || `<div class="sub u-mt-8">No reports yet.</div>`)
    + `<div class="sub u-mt-8">${esc(seen.note)}</div>`;
}

document.addEventListener("submit", async (event) => {
  if (event.target.id !== "proof-report-form") return;
  event.preventDefault();
  const button = $("proof-report-make");
  button.disabled = true;
  try {
    const made = await api("/api/proof-reports", {
      method: "POST",
      body: JSON.stringify({
        month: $("proof-report-month").value,
        site_id: $("proof-report-site").value || null,
      }),
    });
    toast(`Report made. Code ${made.verify_code}.`, "ok");
    renderProofReports(await api("/api/proof-reports"));
  } catch (err) {
    toast(err.message, "err");
  } finally {
    button.disabled = false;
  }
});

document.addEventListener("click", async (event) => {
  const copy = event.target.closest(".proof-copy");
  if (copy) {
    try {
      await navigator.clipboard.writeText(copy.dataset.link);
      toast("Copied the link.", "ok");
    } catch (_) {
      toast(copy.dataset.link);
    }
    return;
  }
  const off = event.target.closest(".proof-revoke");
  if (!off) return;
  if (!window.confirm("Switch this link off? Anyone who opens it will be told "
                      + "it was switched off. This cannot be undone.")) return;
  off.disabled = true;
  try {
    await api(`/api/proof-reports/${encodeURIComponent(off.dataset.id)}/revoke`,
              { method: "POST" });
    toast("Switched off.", "ok");
    renderProofReports(await api("/api/proof-reports"));
  } catch (err) {
    toast(err.message, "err");
    off.disabled = false;
  }
});

/* ---------------- the OmniArmor connection ----------------
   The form is filled from the server only when what the server holds has
   changed and nobody is typing in it, so the poll never wipes a half
   pasted key. The key itself never comes back: only its last four. */

let omniSignature = null;

/* What the OmniArmor link costs, said once it is connected (3.26.0). While
   a business is still setting up on the free trial it is not offered a
   paid extra: "+$29 a month per location" sat in the middle of Setup on
   the first day. Connected, or on a paid plan, the price is always shown. */
function omniPriceLine(seen, price) {
  const trial = state.data && state.data.tenant && state.data.tenant.plan === "trial";
  if (!seen.connected && (trial || dayOne())) return "";
  return `<div class="sub u-mb-8">${esc(price.label)}${price.free_now
    ? ", free during your trial."
    : ` · ${money(price.monthly_for_you_usd)} a month for you while connected.`}</div>`;
}

function renderOmni(seen) {
  const owner = Boolean(seen && !seen.notOwner);
  $("omni-owner").hidden = !owner;
  $("omni-not-owner").hidden = owner;
  if (!owner) {
    $("omni-note").textContent = "Owners only";
    return;
  }
  $("omni-note").textContent = seen.connected ? seen.status : "Not connected";
  const signature = [seen.connected, seen.address, seen.key_hint, seen.site_label,
    seen.timezone, (seen.site_ids || []).join(","),
    seen.sites.map((s) => s.site_id).join(",")].join("|");
  if (signature !== omniSignature && !$("omni-form").contains(document.activeElement)) {
    omniSignature = signature;
    $("omni-address").value = seen.address || "";
    $("omni-site-label").value = seen.site_label || "";
    $("omni-tz").value = seen.timezone || "";
    $("omni-key").value = "";
    $("omni-key").placeholder = seen.key_hint
      ? `Saved key ends ${seen.key_hint}. Paste a new one to replace it.`
      : "clx_\u2026 from OmniArmor: Settings, Connections";
    $("omni-sites").innerHTML = seen.sites.length > 1
      ? `<div class="u-fs-12 u-c-muted u-mb-3">Sites to include (none ticked:
           every unit, in one report)</div>`
        + seen.sites.map((s) => `
          <label class="u-d-flex u-gap-6 u-items-center u-fs-12-5">
            <input type="checkbox" class="omni-site" value="${esc(s.site_id)}"${
              (seen.site_ids || []).includes(s.site_id) ? " checked" : ""}>
            ${esc(s.name)}</label>`).join("")
      : "";
  }
  // Nothing to fill in on a server that cannot keep the key (3.26.0): the
  // card says so in one line instead of offering a form that is refused.
  $("omni-form").hidden = !seen.credential_key_configured && !seen.connected;
  $("omni-test").disabled = !seen.connected;
  $("omni-remove").disabled = !seen.connected;
  const price = seen.price;
  const days = seen.days.map((d) => {
    const good = d.state === "sent";
    const said = d.summary
      ? `${esc(d.summary.units_in_range_all_day)} of ${esc(d.summary.units_total)}
         in range all day${d.summary.breaches ? ` · ${esc(d.summary.breaches)} out of range` : ""}`
      : esc(d.detail);
    return `
      <div class="fc-row">
        <span class="fc-when ${good ? "" : "high"}">${esc(good ? "sent" : (d.outcome || d.state))}</span>
        <span class="grow">
          <span class="fc-id">${esc(d.date)}</span>
          <span class="sub">${said}${d.corrections ? ` · corrected ${esc(d.corrections)}\u00D7` : ""}</span>
        </span>
      </div>`;
  }).join("");
  $("omni-body").innerHTML = `
    <div class="u-fs-12-5 u-mb-8 ${seen.ok ? "s-good" : (seen.connected ? "s-warning" : "u-c-muted")}">${
      esc(seen.connected ? seen.status : "Not connected yet")}</div>
    ${seen.credential_key_configured ? "" : `<div class="sub u-mb-8">Not switched
      on for this server yet. Ask your provider to switch this on.</div>`}
    ${omniPriceLine(seen, price)}
    ${days}`;
}

$("omni-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = {
    address: $("omni-address").value.trim(),
    site_label: $("omni-site-label").value.trim(),
    site_ids: [...document.querySelectorAll(".omni-site:checked")].map((b) => b.value),
  };
  if ($("omni-key").value.trim()) body.key = $("omni-key").value.trim();
  if ($("omni-tz").value.trim()) body.timezone = $("omni-tz").value.trim();
  try {
    const seen = await api("/api/connections/omniarmor", {
      method: "PUT", body: JSON.stringify(body),
    });
    $("omni-key").value = "";
    omniSignature = null;
    renderOmni(seen);
    toast("Saved. The key is stored encrypted; only its last four are shown.", "ok");
  } catch (err) {
    toast(err.message, "err");
  }
});

$("omni-test").addEventListener("click", async () => {
  const button = $("omni-test");
  button.disabled = true;
  try {
    const out = await api("/api/connections/omniarmor/test", { method: "POST" });
    renderOmni(out.connection);
    toast(out.message, out.status === "ok" || out.status === "already_sent" ? "ok" : "err");
  } catch (err) {
    toast(err.message, "err");
  } finally {
    button.disabled = false;
  }
});

$("omni-remove").addEventListener("click", async () => {
  if (!confirm("Disconnect OmniArmor? The key is deleted and nothing more is sent.")) return;
  try {
    const out = await api("/api/connections/omniarmor", { method: "DELETE" });
    omniSignature = null;
    renderOmni(out);
    toast(out.message, "ok");
  } catch (err) {
    toast(err.message, "err");
  }
});

/* ---------------- the business's own AI key for alert wording ----------------
   alertkey.py. The key never comes back: only its last four. Without the
   server's encryption secret the form is hidden and the card says why. */

function renderAiKey(seen) {
  const owner = Boolean(seen && !seen.notOwner);
  $("aikey-not-owner").hidden = owner;
  $("aikey-unavailable").hidden = !owner || seen.available;
  $("aikey-owner").hidden = !owner || !seen.available;
  if (!owner) {
    $("aikey-note").textContent = "Owners only";
    return;
  }
  if (!seen.available) {
    $("aikey-note").textContent = "Not available here";
    $("aikey-unavailable").textContent = seen.why_not;
    return;
  }
  $("aikey-note").textContent = seen.saved ? `Key ends ${seen.key_hint}` : "Using ours";
  if (!$("aikey-form").contains(document.activeElement)) {
    $("aikey-key").placeholder = seen.saved
      ? `Saved key ends ${seen.key_hint}. Paste a new one to replace it.`
      : `Your ${seen.provider} API key`;
  }
  $("aikey-test").disabled = !seen.saved;
  $("aikey-remove").disabled = !seen.saved;
  const last = seen.last_result;
  $("aikey-body").innerHTML = `
    <div class="u-fs-12-5 u-mb-8 ${seen.saved ? "s-good" : "u-c-muted"}">${esc(seen.saved
      ? `Your key (ending ${seen.key_hint}) words your alerts' what-to-check line.`
      : "No key saved: the line is worded on ours, or by the plain template.")}</div>
    ${seen.model_set ? "" : `<div class="sub u-mb-8">No AI model is set on this
      server, so every alert uses the plain wording for now, with or without
      a key.</div>`}
    ${last ? `<div class="sub u-mb-8 ${last.worked ? "" : "s-warning"}">${esc(last.worked
      ? "The last line on your key worked."
      : "The last try on your key did not work, so that alert used the plain wording.")}</div>` : ""}
    <div class="sub">${esc(seen.note)}</div>`;
}

$("aikey-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const key = $("aikey-key").value.trim();
  if (!key) { toast("Paste your key first.", "err"); return; }
  try {
    const seen = await api("/api/ai-key", { method: "PUT", body: JSON.stringify({ key }) });
    $("aikey-key").value = "";
    $("aikey-key").blur();
    renderAiKey(seen);
    toast(seen.message, "ok");
  } catch (err) {
    toast(err.message, "err");
  }
});

$("aikey-test").addEventListener("click", async () => {
  const button = $("aikey-test");
  button.disabled = true;
  try {
    const out = await api("/api/ai-key/test", { method: "POST" });
    renderAiKey(out);
    toast(`${out.message} “${out.line}”`, out.worked ? "ok" : "err");
  } catch (err) {
    toast(err.message, "err");
  } finally {
    button.disabled = false;
  }
});

$("aikey-remove").addEventListener("click", async () => {
  if (!confirm("Remove your AI key? It is deleted, and alerts go back to our wording.")) return;
  try {
    const out = await api("/api/ai-key", { method: "DELETE" });
    renderAiKey(out);
    toast(out.message, "ok");
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
  return isNaN(at) ? String(value) : CLXWords.dateTime(value);
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
         Nothing is trending out of range.
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
    const none = !((state.data && state.data.sensors) || []).length;
    $("claims-body").innerHTML =
      `<div class="sub">${none
        ? "If food is ever lost, the paperwork your insurer asks for will be ready here."
        : `Nothing to claim for in the last ${esc(eligible.period_days)} days.`}</div>`
      + (none ? nextSteps([["sensor", `Add a ${noun(1)}`]]) : "");
    return;
  }
  $("claims-body").innerHTML =
    `<div class="sub u-mb-8">${esc(eligible.count)
      } alert(s) an insurance pack can be made for:</div>`
    + eligible.incidents.map((i) => `<div class="fc-row">
        <span class="grow">
          <span class="fc-id">${esc(i.sensor_id)}</span>
          <span class="sub">${esc(shortDate(i.opened_at))} · ${
            esc(i.catastrophe)} · ${esc(i.state)}${
            i.readings_available ? "" : " · no readings kept"}</span>
        </span>
        <button class="btn btn-sm" data-packet="${esc(i.incident_id)}"
                type="button">Insurance pack</button>
      </div>`).join("");
}

/* ---------------- who a real alert reaches ----------------
   The roster is what was configured. This is what the dispatcher would
   do with it, which is not always the same thing -- a contact with both
   channels muted is on the roster and reaches nobody. */
function renderEscalation(preview) {
  // The trial's few calls are said once, in the card's own first line
  // (roster-plain), not again under this.
  const texted = preview.sms_recipients || [];
  const ladder = preview.voice_ladder || [];
  const names = (rows) => rows.map((r) => esc(r.name)).join(", ");

  $("escalation-body").innerHTML = `
    <div class="sub u-bt u-pt-10">
      <strong>A real alert right now.</strong><br>
      Text to ${texted.length ? names(texted) : "nobody"}.
      ${ladder.length
        ? "Then it calls " + names(ladder) + ", stopping at whoever answers."
        : (preview.calls && preview.calls.mode === "none"
          ? "No phone call: " + esc(preview.calls.words)
          : "No call ladder, so an unanswered text is the end of it.")}
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
      <td>${r.breached ? "out of range" : "in range"}</td>
    </tr>`).join("");

  $("drawer").innerHTML = `
    <div class="drawer-head">
      <div class="grow">
        <div class="doc-kicker">Insurance pack: loss event evidence</div>
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
  return CLXWords.dateTime(iso, { year: true });
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
            <td>${esc(stamp(b.out_since || b.opened_at))}</td>
            <td class="num">${deg(b.worst)}</td>
            <td>${b.acknowledged_by
              ? `${esc(b.acknowledged_by)}<span class="log-sub">${
                  esc(stamp(b.acknowledged_at))}${b.minutes_to_acknowledge !== null
                  ? ` · after ${esc(minutesWords(b.minutes_to_acknowledge))}` : ""}</span>`
              : `<strong>Nobody</strong>`}</td>
            <td>${b.corrective_action
              ? `${esc(b.corrective_action)}${b.product_disposition
                  ? `<span class="log-sub">Product: ${esc(b.product_disposition)}</span>` : ""}`
              : `<strong>Not recorded</strong>`}</td>
            <td>${esc(b.reviewed_by || "—")}</td>
            <td>${b.closed_at ? `${esc(stamp(b.closed_at))}${b.closed_by
                ? `<span class="log-sub">by ${esc(b.closed_by)}${b.closed_out_of_range
                    ? ", still out of range" : ""}</span>` : ""}${b.close_note
                ? `<span class="log-sub">${esc(b.close_note)}</span>` : ""}`
              : "still open"}</td>
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

  const groups = (log.site_wide_alerts || []);
  const groupSection = groups.length ? `<section class="log-unit log-sitewide">
      <h3 class="log-unit-name">Site-wide alerts</h3>
      <div class="log-meta">Many units warming at once. Each unit is also listed
        below with its own entry.</div>
      <div class="scrollx"><table class="log-table">
        <thead><tr><th>When</th><th>What</th><th>Units</th>
          <th>Acknowledged by</th><th>Closed</th></tr></thead>
        <tbody>${groups.map((g) => `<tr class="log-out">
          <td>${esc(stamp(g.opened_at))}${g.at_night ? `<span class="log-sub">at night</span>` : ""}</td>
          <td>${esc(g.summary)}</td>
          <td>${esc(g.units.join(", "))}</td>
          <td>${g.acknowledged_by
            ? `${esc(g.acknowledged_by)}${g.minutes_to_acknowledge !== null
                ? `<span class="log-sub">after ${esc(minutesWords(g.minutes_to_acknowledge))}</span>` : ""}`
            : `<strong>Nobody</strong>`}</td>
          <td>${g.closed_at ? esc(stamp(g.closed_at)) : "still open"}</td>
        </tr>`).join("")}</tbody>
      </table></div>
    </section>` : "";

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
      ${groups.length ? `<div><span class="k">Site-wide alerts</span><span class="v">${
        esc(groups.length)}</span></div>` : ""}
    </div>
    ${log.covers_the_whole_period ? "" : `<div class="read-out no u-mt-10">${esc(log.caveat)}</div>`}
    ${groupSection}
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
      `<div class="card-note">Once your sensors have a few days of readings,
         you will see how you compare with similar businesses here.</div>`
      + nextSteps([["sensor", `Add a ${noun(1)}`]]);
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
  // Not published yet (too few businesses in the sector to hide each
  // one's figures): the card is not shown at all (3.26.0). "Only 1
  // operators in this sector" was the platform's business, not the
  // café's; the reason stays in the API for the operator.
  const card = $("find-sector-standing");
  const was = card.dataset.empty;
  card.dataset.empty = body.available ? "" : "1";
  if (was !== card.dataset.empty) applyFinder();
  if (!body.available) {
    $("bench-note").textContent = "";
    $("bench-body").innerHTML = "";
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

/* The card's first line: whether texts go out from this server. The
   Slack and Teams copies below it are optional extras, so an empty list
   of them is not "nothing connected" (3.26.0). */
function paintReach() {
  $("hook-note").textContent = state.textsOff ? "texts off on this server" : "texts on";
  $("reach-texts").textContent = state.textsOff
    ? "Texts and calls are off on this server, so alerts show here but nobody "
      + "is texted. Ask your provider to switch them on."
    : "We text the people under Who gets called within a minute of an alert, "
      + "and phone them on plans with calls.";
}

function renderHooks(hooks) {
  paintReach();
  if (!hooks.webhooks.length) {
    $("hook-body").innerHTML = "";
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
      <td>${esc(CLXWords.dateTime(r.at))}</td>
      <td class="num${r.breached ? " u-c-critical u-fw-650" : ""}">
        ${esc(r.temperature)}°${esc(unit)}</td>
      <td class="num">${r.humidity === null ? "—" : esc(r.humidity) + "%"}</td>
      <td>${r.breached ? "out of range" : "in range"}</td>
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
        esc(!s.last_seen ? "waiting for its first reading" : s.online ? "online" : "no signal")}</span></div>
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
    ${s.online || !s.last_seen ? "" : `<details class="offline-box" id="offline-box" open>
      <summary>No signal. What to check</summary>
      <div id="offline-box-body"><p class="card-plain">Loading the steps…</p></div>
    </details>`}

    <div class="card-head u-mt-18">
      <span class="card-title">Recent history</span>
      <span class="card-note">${esc(detail.readings.length)} points ·
        ${esc(detail.breached_count)} out of range</span>
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
    ;
  // The business key is replaced from its own card in Setup, by an owner.
  $("biz-key-act").hidden = !(state.user && state.user.role === "owner");
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

/* ---------------- getting started ----------------
   An empty console used to say what was missing and stop there. Every
   empty card now ends with the button for the next thing to do, and the
   checklist at the top says which of the three first things are done --
   as the server counts them, never as this page guesses. */

/* Where each next step lives: the card, and the field to put the cursor in. */
const GO = {
  site:   { card: "find-sites", field: "site-name" },
  sensor: { card: "find-add-to-the-estate", field: "add-loc" },
  oncall: { card: "find-on-call-roster", field: "ct-name" },
  test:   { card: "find-on-call-roster", field: "test-alert" },
  plans:  { card: "find-subscription", field: "" },
  alerts: { card: "find-incidents", field: "" },
  pollers: { card: "find-vendor-clouds", field: "poller-name" },
};
const GO_LABEL = {
  site: "Add a site", sensor: "Add a sensor", oncall: "Add someone",
  test: "Send a test",
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

/* The sticky bars at the top of the page, so a card scrolled to is not
   tucked underneath them. */
function stickyHeight() {
  let height = 0;
  document.querySelectorAll(".nav, .needs-bar, .finder").forEach((bar) => {
    if (getComputedStyle(bar).position === "sticky") {
      height += bar.getBoundingClientRect().height;
    }
  });
  return height;
}

function goTo(key) {
  if (key === "simulate") { simAdd(); return; }
  if (key === "addme") { addMe(); return; }
  if (key === "demo") { location.href = "/demo"; return; }
  if (key === "start") {
    finder.tab = "all";
    applyFinder();
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
  const where = GO[key];
  const card = where && $(where.card);
  if (!card) return;
  if (card.hidden) {
    // On another tab, or behind a search: bring the whole page back first.
    // Folded behind "More" on a short list: show that one card as well.
    // With the simple tabs (no Everything to go back to) the tab stays and
    // the card is shown on it.
    if (!simpleTabs()) finder.tab = "all";
    finder.query = "";
    $("find-q").value = "";
    finder.reveal.add(card.id);
    applyFinder();
  }
  const top = card.getBoundingClientRect().top + window.scrollY - stickyHeight() - 10;
  window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
  const field = where.field && $(where.field);
  if (field) setTimeout(() => field.focus({ preventScroll: true }), 450);
}

/* "Add me": the person who signed up is usually the first person who
   should get the text, and they already gave their mobile. One tap puts
   them on the list instead of typing it all again. */
async function addMe() {
  const t = (state.data && state.data.tenant) || {};
  if (!t.contact_phone) { goTo("oncall"); return; }
  await act(async () => {
    await api("/api/contacts", {
      method: "POST",
      body: JSON.stringify({ full_name: t.contact_name || "Owner",
                             phone: t.contact_phone }),
    });
  }, "Added you. Alerts will reach the mobile you signed up with.");
}

function renderFirstDay(fd) {
  const card = $("first-day");
  const canAct = Boolean(state.user && state.user.role !== "viewer");
  // Gone once all three are done, and never shown to somebody who could
  // not do any of them, or on a wall.
  if (!fd || fd.complete || !canAct || state.wall) {
    // Finished while it was on screen: say so once, as it goes.
    if (fd && fd.complete && !card.hidden) {
      // Only now: "complete" includes a reading that actually arrived.
      toast(state.textsOff
        ? "All done. Readings are arriving and alerts show here. Texts are off "
          + "on this server, so nobody is texted yet."
        : "All done. Readings are arriving and your team will be alerted: "
          + "your first fridge is protected.", "ok");
    }
    card.hidden = true;
    return;
  }
  card.hidden = false;
  $("first-day-note").textContent = `${fd.done} of ${fd.total} done`;
  const t = (state.data && state.data.tenant) || {};
  const actions = (step) => {
    if (step.done) return `<span class="sr">done</span>`;
    if (step.key === "oncall" && t.contact_phone) {
      return `<span class="checklist-acts">
        <button class="btn btn-sm btn-go" type="button" data-go="addme"
                title="${esc(t.contact_phone)}">Add me</button>
        <button class="btn btn-sm" type="button" data-go="oncall">Someone else</button>
      </span>`;
    }
    if (step.key === "test") {
      return `<button class="btn btn-sm btn-go" type="button"
                      data-test-now="1">Send a test</button>`;
    }
    if (step.key === "reading") {
      return `<button class="btn btn-sm btn-go" type="button" data-connect="1">How to connect it</button>`;
    }
    return `<button class="btn btn-sm btn-go" type="button" data-go="${esc(step.key)}">${
      esc(GO_LABEL[step.key] || "Do it")}</button>`;
  };
  $("first-day-list").innerHTML = fd.steps.map((step) => `
    <li class="${step.done ? "is-done" : ""}">
      <span class="tick" aria-hidden="true">${step.done ? "&#10003;" : ""}</span>
      <span class="grow"><b>${esc(step.label)}</b>
        <span class="sub">${esc(step.detail)}</span></span>
      ${actions(step)}
    </li>`).join("");
}

/* ---------------- a tab with nothing on it yet ----------------
   One sentence saying what will appear there, and the next thing to do.
   Read from what the account holds, the same as the checklist. */
function tabEmptyFor(tab) {
  const d = state.data;
  if (!d) return null;
  const sensors = (d.sensors || []).length;
  const c = state.counts || {};
  const unit = noun(1);
  switch (tab) {
    case "fridges":
      return sensors ? null : {
        text: `Your ${noun(2)} will show here, with how cold each one is right now.`,
        go: [["sensor", "Add your first sensor", true]] };
    case "alerts":
      if ((d.incidents || []).length) return null;
      return sensors
        ? { text: `When a ${unit} gets too warm, the alert and who answered it `
                  + "will show here.",
            go: [["test", "Send a test alert", true]] }
        : { text: `When a ${unit} gets too warm, the alert and who answered it `
                  + "will show here.",
            go: [["sensor", "Add your first sensor", true],
                 ["demo", "Watch a pretend alert"]] };
    case "proof":
      return sensors ? null : {
        text: "Your temperature records, the log for an inspector and a monthly "
              + "report for your insurer will show here once a sensor is sending readings.",
        go: [["sensor", "Add your first sensor", true]] };
    case "money":
      if (c.bills === null || c.bills === undefined || c.bills) return null;
      return {
        text: d.tenant && d.tenant.plan === "trial"
          ? "Your plan and bills will show here; on the free trial there is "
            + "nothing to pay."
          : "Your plan and bills will show here.",
        go: [["plans", "See the plans", true]] };
    case "people":
      if (c.roster === null || c.roster === undefined || c.roster) return null;
      return {
        text: "The people who get alerts, and who can sign in, will show here.",
        go: d.tenant && d.tenant.contact_phone
          ? [["addme", "Add me", true], ["oncall", "Add someone else"]]
          : [["oncall", "Add who gets alerts", true]] };
    case "licences":
      if (c.licences === null || c.licences === undefined || c.licences) return null;
      return {
        text: "Staff certificates, like a food handler card, will show here with "
              + "the date each one runs out.",
        go: [["start", "Back to getting started", true]] };
    default:
      return null;
  }
}

/* A tab whose own card already says the same, with the same button: the
   fridges list ("Nothing is being watched yet", Add, a simulated one) and
   What you pay (the trial, nothing to pay, the plans). The sentence above
   it was the same thing twice (3.26.0). */
const SAYS_IT_ITSELF = { fridges: "fleet-card", money: "find-subscription" };

function renderTabEmpty() {
  const box = $("tab-empty");
  if (!box) return;
  const own = $(SAYS_IT_ITSELF[finder.tab] || "");
  const empty = finder.query.trim() || (own && !own.hidden) ? null : tabEmptyFor(finder.tab);
  box.hidden = !empty;
  if (!empty) return;
  $("tab-empty-text").textContent = empty.text;
  $("tab-empty-act").innerHTML = nextSteps(empty.go);
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
        // The connect box says it itself, with a tick, when it is open.
        if (!$("connect").open) toast("First reading in.", "ok");
      }
    } catch (_) { /* the ordinary poll reports trouble */ }
  }, 2500);
}

/* ---------------- "How to connect it" ----------------
   Pick the sensor's brand, and see only its few steps with this unit's
   address, key and serial already in them (sensor_guides.WIZARD, from
   /api/sensor-guides/wizard). Then it waits for the first reading, here,
   and ticks the moment it lands. The key is the one shown when the unit
   was added, kept in this tab's memory only; if it is gone, "Make a new
   key" issues another (safe: nothing has reported with the old one). */
const connect = { brands: null, unit: null, brand: null, timer: null, until: 0 };

function connectUnits() {
  return ((state.data && state.data.sensors) || []);
}

function waitingUnits() {
  return connectUnits().filter((s) => s.last_temperature_display === null
    || s.last_temperature_display === undefined);
}

async function openConnect(sensorId) {
  const dialog = $("connect");
  if (!connect.brands) {
    try {
      connect.brands = (await api("/api/sensor-guides/wizard")).brands;
    } catch (err) {
      toast(err.message, "err");
      location.href = "/help/sensors";
      return;
    }
  }
  const units = connectUnits();
  const waiting = waitingUnits();
  connect.unit = (sensorId && units.find((s) => s.sensor_id === sensorId))
    || waiting[waiting.length - 1] || units[units.length - 1] || null;
  connect.brand = null;
  renderConnect();
  if (typeof dialog.showModal === "function") { if (!dialog.open) dialog.showModal(); }
  else dialog.setAttribute("open", "");
  if (connect.unit) watchConnect();
}

function closeConnect() {
  clearInterval(connect.timer);
  connect.timer = null;
  const dialog = $("connect");
  if (typeof dialog.close === "function" && dialog.open) dialog.close();
  else dialog.removeAttribute("open");
}

$("connect-close").addEventListener("click", closeConnect);
$("connect").addEventListener("close", () => { clearInterval(connect.timer); connect.timer = null; });

function connectUnitName(s) {
  return ((s && s.location_name) || "").trim() || (s && s.sensor_id) || "";
}

function renderConnect() {
  const u = connect.unit;
  const units = connectUnits();
  const unitBox = $("connect-unit");
  if (!u) {
    // Nothing to connect yet: the one thing to do first.
    unitBox.innerHTML = `<p class="connect-ask">Add the ${esc(noun(1))} first: say where it
        is, and you get the key its sensor needs.</p>
      <button class="btn btn-primary" type="button" data-connect-add="1">Add a ${esc(noun(1))}</button>`;
    $("connect-pick").hidden = true;
    $("connect-steps").hidden = true;
    $("connect-wait").hidden = true;
    return;
  }
  unitBox.innerHTML = units.length > 1
    ? `<label class="connect-for">For
         <select id="connect-which">${units.map((s) => `<option value="${esc(s.sensor_id)}"${
           s.sensor_id === u.sensor_id ? " selected" : ""}>${esc(connectUnitName(s))}</option>`).join("")}
         </select></label>`
    : `<p class="connect-for">For <b>${esc(connectUnitName(u))}</b></p>`;
  const which = $("connect-which");
  if (which) {
    which.addEventListener("change", () => {
      connect.unit = connectUnits().find((s) => s.sensor_id === which.value) || connect.unit;
      renderConnect();
      watchConnect();
    });
  }
  const picking = !connect.brand;
  $("connect-pick").hidden = !picking;
  $("connect-steps").hidden = picking;
  if (picking) {
    $("connect-brands").innerHTML = connect.brands.map((b) => `
      <button class="connect-brand" type="button" data-connect-brand="${esc(b.id)}">
        <b>${esc(b.label)}</b><span>${esc(b.hint)}</span></button>`).join("");
  } else {
    $("connect-steps").innerHTML = connectStepsHtml(connect.brand, u);
  }
  paintConnectWait();
}

/* One value to copy: the address, the key, the serial. */
function connectValue(label, value, id) {
  return `<div class="connect-val-row"><span class="connect-val-label">${esc(label)}</span>
      <code class="connect-val" id="${id}">${esc(value)}</code>
      <button class="btn btn-sm" type="button" data-connect-copy="${id}">Copy</button></div>`;
}

function connectStepsHtml(b, u) {
  const key = state.issuedKeys[u.sensor_id] || "";
  const serial = u.external_device_sn || "";
  const keyWords = key || "(your sensor key)";
  const address = b.address.replace("{key}", key || "(your sensor key)");
  const fill = (text) => esc(text)
    .replace("{key}", `<code>${esc(keyWords)}</code>`)
    .replace("{serial}", `<code>${esc(serial || "(its serial)")}</code>`)
    .replace("{sensor_id}", `<code>${esc(u.sensor_id)}</code>`);
  const parts = [`<button class="linkish connect-back" type="button" data-connect-back="1">&larr; Another kind of sensor</button>
    <h3 class="connect-brand-title">${esc(b.label)}</h3>`];
  if (b.needs_serial) {
    parts.push(serial
      ? connectValue("Its serial", serial, "connect-serial-val")
      : `<form class="connect-serial" id="connect-serial">
           <label for="connect-serial-in">Its serial <span class="sub">${esc(b.serial)}</span></label>
           <span class="connect-serial-row">
             <input id="connect-serial-in" autocomplete="off" maxlength="120" required>
             <button class="btn btn-sm btn-primary" type="submit">Save</button>
           </span>
           <span class="connect-serial-out" id="connect-serial-out" role="status"></span>
         </form>`);
  }
  if (b.needs_key) {
    // A key that travels in the address (?k=) is copied with it.
    const inAddress = b.address.includes("{key}");
    parts.push(key
      ? (inAddress ? "" : connectValue("Sensor key", key, "connect-key-val"))
      : `<div class="connect-nokey"><p>Its sensor key was shown once, when it was added.
           If you no longer have it, make a new one: the old one stops working, and
           nothing has reported with it yet.</p>
           <button class="btn btn-sm" type="button" data-connect-newkey="1">Make a new key</button></div>`);
    if (b.address) parts.push(connectValue("Address", address, "connect-address-val"));
  }
  parts.push(`<ol class="steps connect-list">${b.steps.map((st) => `<li>${fill(st)}</li>`).join("")}</ol>`);
  if (b.id === "cloud") {
    parts.push(`<button class="btn btn-sm btn-primary" type="button" data-connect-go="pollers">Open Fetch from a sensor cloud</button>`);
  }
  return parts.join("");
}

/* After "Received!", the step that is left (3.26.0): send a test alert,
   or, with nobody to text yet, add who gets alerts. Gone once the test has
   been sent. */
function connectNext() {
  const steps = ((state.data && state.data.first_day) || {}).steps || [];
  const done = (key) => steps.some((st) => st.key === key && st.done);
  if (!state.user || state.user.role === "viewer" || done("test")) return "";
  const go = done("oncall") ? ["test", "Next: send a test"] : ["oncall", "Next: add who gets alerts"];
  return `<button class="btn btn-sm btn-primary connect-next" type="button"
    data-connect-go="${go[0]}">${go[1]}</button>`;
}

function paintConnectWait() {
  const box = $("connect-wait");
  const u = connect.unit;
  if (!u || !connect.brand && (u.last_temperature_display === null
      || u.last_temperature_display === undefined)) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const reading = u.last_temperature_display;
  if (reading !== null && reading !== undefined) {
    box.className = "connect-wait is-in";
    box.innerHTML = `<span class="connect-tick" aria-hidden="true">&#10003;</span>
      <span><b>Received!</b> ${esc(reading.toFixed(1))}°${esc(u.temperature_unit || state.unit)}${
        u.last_seen ? ` at ${esc(CLXWords.clock(u.last_seen))}` : ""}. ${esc(connectUnitName(u))} is being watched.</span>
      ${connectNext()}`;
    return;
  }
  const late = Date.now() > connect.until;
  box.className = `connect-wait${late ? " is-late" : ""}`;
  box.innerHTML = late
    ? `<span><b>Nothing yet.</b> Check the address and the key, and that the sensor is online.
       Every message that reached us is listed under <b>Use a sensor you already own</b>
       in the Advanced tab, with what was missing.</span>`
    : `<span class="connect-spin" aria-hidden="true"></span>
       <span><b>Waiting for the first reading…</b> This ticks the moment it arrives, usually
       within a few minutes.</span>`;
}

/* Asks only for the overview, every three seconds, while the box is open
   and for fifteen minutes at most. */
function watchConnect() {
  clearInterval(connect.timer);
  connect.until = Date.now() + 15 * 60 * 1000;
  const id = connect.unit && connect.unit.sensor_id;
  if (!id) return;
  const have = () => connect.unit && connect.unit.last_temperature_display !== null
    && connect.unit.last_temperature_display !== undefined;
  if (have()) return;
  connect.timer = setInterval(async () => {
    if (!$("connect").open || !state.token || !connect.unit || connect.unit.sensor_id !== id) {
      clearInterval(connect.timer);
      return;
    }
    try {
      const fresh = await api("/api/console/overview");
      const now = (fresh.sensors || []).find((s) => s.sensor_id === id);
      if (now) connect.unit = now;
      if (have()) {
        clearInterval(connect.timer);
        connect.timer = null;
        // Said in the box, with a tick; the page's own watch stands down
        // so it is not said twice.
        clearInterval(state.firstWatch);
        await refresh();
      }
    } catch (_) { /* the ordinary poll reports trouble */ }
    paintConnectWait();
  }, 3000);
}

$("connect").addEventListener("click", async (event) => {
  const el = event.target.closest("button, a");
  if (!el) return;
  if (el.dataset.connectBrand) {
    connect.brand = connect.brands.find((b) => b.id === el.dataset.connectBrand) || null;
    renderConnect();
    const title = document.querySelector(".connect-brand-title");
    if (title) title.scrollIntoView({ block: "nearest" });
  } else if (el.dataset.connectBack) {
    connect.brand = null;
    renderConnect();
  } else if (el.dataset.connectAdd) {
    closeConnect();
    goTo("sensor");
  } else if (el.dataset.connectGo) {
    closeConnect();
    goTo(el.dataset.connectGo);
  } else if (el.dataset.connectCopy) {
    const value = $(el.dataset.connectCopy);
    try {
      await navigator.clipboard.writeText(value.textContent);
      toast("Copied.", "ok");
    } catch (_) {
      const range = document.createRange();
      range.selectNodeContents(value);
      const sel = getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      toast("Clipboard blocked: it is selected, copy it by hand.", "err");
    }
  } else if (el.dataset.connectNewkey) {
    el.disabled = true;
    try {
      const id = connect.unit.sensor_id;
      const made = await api(`/api/licenses/me/sensors/${encodeURIComponent(id)}/rotate-key`,
        { method: "POST" });
      state.issuedKeys[id] = made.ingest_key;
      renderConnect();
      toast("New key made. The old one no longer works.", "ok");
    } catch (err) {
      el.disabled = false;
      toast(err.message, "err");
    }
  }
});

$("connect").addEventListener("submit", async (event) => {
  if (event.target.id !== "connect-serial") return;
  event.preventDefault();
  const out = $("connect-serial-out");
  const value = $("connect-serial-in").value.trim();
  if (!value) return;
  try {
    const id = connect.unit.sensor_id;
    const res = await api(`/api/licenses/me/sensors/${encodeURIComponent(id)}/serial`, {
      method: "POST", body: JSON.stringify({ external_device_sn: value }),
    });
    connect.unit = Object.assign({}, connect.unit, res.sensor);
    renderConnect();
    refresh();
  } catch (err) {
    out.textContent = err.message;
    out.className = "connect-serial-out s-warning";
  }
});

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

/* Safe to press, and said so beside the button: the text starts TEST and
   says nothing is wrong, no problem is opened and nobody is chased. So
   it goes at once, with no "are you sure" in the way. The server limits
   how often (one every ten minutes, five a day). */
async function sendTestAlert(button) {
  const call = $("test-call").checked && !$("test-call-wrap").hidden;
  button.disabled = true;
  const outs = [$("test-alert-out"), $("first-day-out")].filter(Boolean);
  try {
    const out = await api("/api/contacts/test-alert", {
      method: "POST", body: JSON.stringify({ call }),
    });
    const html = `<div class="test-result">
        <p>${esc(out.message)}</p>
        <ul>${out.results.map((r) => `<li>
          ${badge(r.delivered ? "low" : (r.status === "not_configured" ? "unknown" : "critical"),
                  r.channel === "voice" ? "call" : "text")}
          ${esc(r.name)} <span class="sub">${esc(r.phone)} &middot; ${esc(
            r.delivered ? "handed to the network" : r.status === "not_configured"
              ? "recorded, not texted" : r.detail || r.status)}</span></li>`).join("")}
        </ul></div>`;
    $("test-alert-out").innerHTML = html;
    // Texts off on this server: it was recorded, and nobody's phone rang.
    // "Test sent" said otherwise (3.26.0).
    toast(out.dry_run ? "Recorded, not texted: texts are off on this server."
      : "Test sent. It said TEST, and no problem was opened.", out.dry_run ? "warn" : "ok");
    await refresh();
  } catch (err) {
    outs.forEach((box) => {
      box.hidden = false;
      box.innerHTML = `<div class="test-result"><p class="s-warning">${esc(err.message)}</p></div>`;
    });
  } finally {
    button.disabled = false;
  }
}

$("test-alert").addEventListener("click", () => sendTestAlert($("test-alert")));

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

  if (btn.dataset.repairQuote) {
    openRepairQuote(btn.dataset.repairQuote);
    return;
  }

  if (btn.dataset.pauseMonths) {
    act(async () => {
      const out = await api("/api/leaving/pause", {
        method: "POST", body: JSON.stringify({ months: Number(btn.dataset.pauseMonths) }),
      });
      toast(out.message, "ok");
    });
  }

  if (btn.dataset.resumeNow) {
    act(async () => {
      const out = await api("/api/leaving/resume", { method: "POST" });
      toast(out.message, "ok");
    });
  }

  if (btn.dataset.founderUse) {
    act(async () => {
      const out = await api("/api/founder-code", {
        method: "POST", body: JSON.stringify({ code: $("founder-code").value.trim() }),
      });
      toast(out.message, "ok");
    });
  }

  if (btn.dataset.stay) {
    act(async () => {
      const out = await api("/api/leaving/stay", { method: "POST" });
      toast(out.message, "ok");
    });
  }

  if (btn.dataset.cancelPlan) {
    act(async () => {
      const out = await api("/api/contracts/cancel", {
        method: "POST", body: JSON.stringify({ reason: "cancelled from the console" }),
      });
      toast(out.message, "ok");
    });
  }

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

  // "I'm on it" and "It's fixed", the same here, in the pinned bar and
  // on /today (static/answer.js): a unit still out of range is not closed.
  if (btn.dataset.ack) {
    CLXAnswer.onIt(btn.dataset.ack, btn);
  }

  if (btn.dataset.resolve) {
    CLXAnswer.fixed(btn.dataset.resolve, btn);
  }

  if (btn.dataset.needsMore) {
    goTo("alerts");
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
    if (!confirm(`Remove ${id}? Its readings are deleted and the seat is freed.`)) return;
    act(() => api(`/api/licenses/me/sensors/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }), `${id} retired.`);
  }

  if (btn.dataset.rekeyBusiness) {
    if (!confirm(
      "Make a new business key?\n\n"
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

  // The limit and the alert wait are changed on the card itself, not in
  // a browser pop-up (openUnitEditor).
  if (btn.dataset.tune) {
    openUnitEditor(btn.dataset.tune, "limit");
    return;
  }

  if (btn.dataset.wait) {
    openUnitEditor(btn.dataset.wait, "wait");
    return;
  }

  if (btn.dataset.editCancel) {
    closeUnitEditor();
    return;
  }

  if (btn.dataset.waitPick !== undefined) {
    const box = btn.closest(".wait-edit");
    box.querySelector("input").value = btn.dataset.waitPick;
    box.querySelectorAll("[data-wait-pick]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b === btn)));
    return;
  }

  if (btn.dataset.editSave || btn.dataset.editDefault) {
    const id = btn.dataset.editSave || btn.dataset.editDefault;
    const sensor = state.data.sensors.find((x) => x.sensor_id === id);
    const box = btn.closest(".wait-edit");
    if (!sensor || !box) return;
    const which = box.dataset.which;
    const raw = btn.dataset.editDefault ? "" : box.querySelector("input").value.trim();
    const value = raw === "" ? null : Number(raw);
    const out = box.querySelector(".wait-out");
    if (value !== null && !(Number.isFinite(value) && (which === "limit" || value >= 0))) {
      out.textContent = which === "wait" ? "Type a number of minutes, 0 or more." : "Type a temperature.";
      return;
    }
    if (which === "wait") {
      act(() => api(
        `/api/licenses/me/sensors/${encodeURIComponent(sensor.sensor_id)}/alert-rule`,
        { method: "POST", body: JSON.stringify({
            alert_after_minutes: value,
            alert_after_readings: sensor.alert_after_readings ?? null }) }
      ), value === null ? "Back to the usual wait."
        : value === 0 ? "We text at the first reading out of range."
          : `We text once it has been out of range ${minutesWords(value)}.`);
    } else {
      // Asked in the account's own unit, the one the card shows; the API
      // holds limits in Fahrenheit, whatever the account reads in.
      const unit = sensor.temperature_unit || state.unit || "F";
      const fahrenheit = value === null || unit !== "C"
        ? value : Math.round((value * 9 / 5 + 32) * 100) / 100;
      act(() => api(
        `/api/licenses/me/sensors/${encodeURIComponent(sensor.sensor_id)}/thresholds`,
        { method: "POST", body: JSON.stringify({
            danger_above: fahrenheit, danger_below: sensor.override_below ?? null }) }
      ), value === null ? "Back to the usual limit." : `Limit set to ${value}°${unit}.`);
    }
    closeUnitEditor();
    return;
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
  if (btn.dataset.connect) openConnect(btn.dataset.connect === "1" ? "" : btn.dataset.connect);
  if (btn.dataset.testNow) sendTestAlert(btn);
  if (btn.id === "sim-add") simAdd();
  if (btn.dataset.simBreak) simAct(`/units/${encodeURIComponent(btn.dataset.simBreak)}/break`, "POST",
    "It is warming. Nothing is sent to anybody.");
  if (btn.dataset.simFix) simAct(`/units/${encodeURIComponent(btn.dataset.simFix)}/fix`, "POST");
  if (btn.dataset.simRemove) simAct(`/units/${encodeURIComponent(btn.dataset.simRemove)}`, "DELETE");
});

/* Show an issued ingest key, once, somewhere it will stay put until it
   has been dealt with. The response is otherwise thrown away and the key
   is unrecoverable — rotation would be the only way back to one. */
const DEVICE_KEY_NOTE = "Next: put this key in your sensor's settings, so its "
  + "readings reach us. It is shown once — if it is lost, make a new one with "
  + "New key on the sensor's card. It can only send readings for this one "
  + "fridge, so it is safe to keep on the device.";
const MASTER_KEY_NOTE = "Only for connecting other software to your account. "
  + "Most kitchens never need it, so you can press Done. It is shown once — "
  + "if it is lost, make a new one from Setup. Sensors get keys of their own.";

function showIssuedKey(sensorId, key, options = {}) {
  if (!key) return;
  const master = /master key/.test(sensorId);
  if (master) {
    // In Setup, in its own card: not in the form for adding a fridge.
    $("biz-key-value").textContent = key;
    $("biz-key").hidden = false;
    if (options.scroll !== false) {
      finder.reveal.add("find-business-key");
      applyFinder();
      $("biz-key-value").focus();
    }
    return;
  }
  state.issuedKeys[sensorId] = key;
  $("issued-guide").dataset.connectFor = sensorId;
  $("issued-for").textContent = sensorId;
  $("issued-for").hidden = master;
  $("issued-kind").textContent = master ? "Your business key" : "Sensor key for";
  $("issued-guide").hidden = master;
  $("issued-value").textContent = key;
  $("issued-note").textContent = master ? MASTER_KEY_NOTE : DEVICE_KEY_NOTE;
  $("issued-key").hidden = false;
  // Arriving from sign-up, the first screen is the checklist, not a key
  // box half way down the page: the key waits where "Add a sensor" leads.
  $("issued-value").focus({ preventScroll: options.scroll === false });
}

/* "How to put the key in your sensor": the wizard, for this unit, with
   the key just shown already in its steps. The link still goes to
   /help/sensors if the script cannot open it. */
$("issued-guide").addEventListener("click", (event) => {
  event.preventDefault();
  openConnect($("issued-guide").dataset.connectFor || "");
});

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

$("biz-key-copy").addEventListener("click", async () => {
  const key = $("biz-key-value").textContent;
  try {
    await navigator.clipboard.writeText(key);
    toast("Key copied.");
  } catch (err) {
    const range = document.createRange();
    range.selectNodeContents($("biz-key-value"));
    const sel = getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    toast("Clipboard blocked — the key is selected, copy it by hand.", "err");
  }
});

$("biz-key-done").addEventListener("click", () => {
  $("biz-key").hidden = true;
  $("biz-key-value").textContent = "";
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
        unit_kind: $("add-kind-field").hidden ? null : ($("add-kind").value || null),
      }),
    });
    event.target.reset();
    state.kindChosenByHand = false;
    // reset() puts the picker back on the first sector in the list; the
    // next unit is far likelier to be in the same one as this.
    $("add-vertical").value = made.sensor.industry_vertical;
    showUnitKinds(made.sensor.industry_vertical);
    showIssuedKey(made.sensor.sensor_id, made.ingest_key);
  }, "Sensor registered.").then(watchForFirstReading);
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

/* ---------------- where a small account lands ----------------
   A café's owner opening the app wants one answer: is anything wrong?
   For a small account (smallAccount) that is set up, the Simple view
   (/today) is where the console opens. Setting up happens here, so an
   account still on Getting started stays. "All the detail" on the Simple
   view comes back with ?view=full, which holds for the rest of the
   session; a link to a card (#...) is always followed here. */
function liveAlert(d) {
  return (d.incidents || []).some((i) => i.state !== "resolved")
    || (d.site_wide_alerts || []).some((i) => i.state !== "resolved");
}

function maybeLandOnSimple(d) {
  if (state.landingDecided) return;
  state.landingDecided = true;
  if (state.wall || location.hash) return;
  let chosen = null;
  let asked = false;
  try {
    asked = new URLSearchParams(location.search).get("view") === "full";
    if (asked) sessionStorage.setItem("cyberlogix.view", "full");
    chosen = sessionStorage.getItem("cyberlogix.view")
      || localStorage.getItem("cyberlogix.view");
  } catch (_) { /* storage blocked: decide from the account alone */ }
  if (asked) return;
  // An alert open or unanswered, on any account: whoever is signing in
  // may be the person we just texted, and /today puts "I'm on it" first.
  // Signing in goes there; a reload, or a console opened from a saved
  // session, keeps the console, with the alert pinned at the top.
  if (liveAlert(d) && state.fromSignIn) {
    state.leaving = true;
    location.replace("/today");
    return;
  }
  if (chosen === "full") return;
  if (!smallAccount(d) || !d.first_day || !d.first_day.complete) return;
  state.leaving = true;
  location.replace("/today");
}

/* ---------------- finding things ----------------
   The page is one long column of cards and no map. Filtering beats
   scrolling: pressing People should leave People on the screen, not put
   it somewhere below the fold with everything else still there. */

const finder = { tab: "all", query: "", reveal: new Set(), expanded: false };
try { finder.expanded = sessionStorage.getItem("cyberlogix.tabs") === "all"; } catch (_) { /* storage blocked */ }

/* A small account: a trial or Starter with a handful of units. A café was
   handed a wall of estate and operator tools (a volume price matrix,
   meeting notes into jobs, staff licences, spending limits, hashes,
   business keys). For a small account those cards wait under Advanced,
   and a search still finds every one of them. */
const SMALL_PLANS = new Set(["trial", "starter"]);
const SMALL_MAX_UNITS = 5;

function smallAccount(d = state.data) {
  const t = d && d.tenant;
  if (!t) return false;
  return SMALL_PLANS.has(t.plan) && (d.sensors || []).length <= SMALL_MAX_UNITS;
}

/* On a phone, a small account's Everything list is the few cards that
   get a new business going, with the rest behind "More": it was thirteen
   thousand pixels of cards on the first day. Search and the tabs still
   show everything. */
const FIRST_CARDS = new Set(["fleet-card", "find-incidents", "find-add-to-the-estate",
  "find-on-call-roster", "find-about"]);

/* The first day (the Getting started checklist is not finished yet), on a
   phone or a desktop: only the checklist and the fridges, everything else
   behind "More". A checklist button that leads to a card shows that card
   too (finder.reveal). */
const DAY_ONE_CARDS = new Set(["fleet-card"]);

/* Simple tabs (3.26.0): until a small account watches a few fridges, the
   finder offers three tabs, Fridges, Alerts and Team, and More, with no
   search box. Nine tabs and a search for one fridge was a control panel
   for an estate the café does not have. More shows every tab and the
   search for the rest of the visit. */
const SIMPLE_TABS_BELOW = 3;
const SIMPLE_TABS = new Set(["fridges", "alerts", "people"]);

function simpleTabs(d = state.data) {
  if (!d || !smallAccount(d) || finder.expanded || state.wall) return false;
  return (d.sensors || []).length < SIMPLE_TABS_BELOW;
}

function dayOne(d = state.data) {
  return Boolean(d && d.first_day && !d.first_day.complete && smallAccount(d)
    && state.user && state.user.role !== "viewer" && !state.wall);
}

function shortList(searching) {
  if (finder.more || searching || finder.tab !== "all" || !smallAccount()) return false;
  if (dayOne()) return true;
  try { return window.matchMedia("(max-width: 720px)").matches; } catch (_) { return false; }
}

function inShortList(card) {
  if (finder.reveal && finder.reveal.has(card.id)) return true;
  if (dayOne()) {
    // Problems joins the first day's list the moment something is warm.
    const d = state.data || {};
    const warm = (d.incidents || []).some((i) => i.state !== "resolved")
      || (d.sensors || []).some((x) => x.condition && x.condition.state === "watching");
    return DAY_ONE_CARDS.has(card.id) || (warm && card.id === "find-incidents");
  }
  return FIRST_CARDS.has(card.id);
}

/* Whether a card belongs in the tab being shown. */
function inTab(card, searching) {
  const advanced = card.getAttribute("data-advanced") === "1";
  if (finder.tab === "advanced") return advanced;
  // A card a button just led to is shown, wherever it is filed.
  if ((finder.tab === "all" || simpleTabs()) && finder.reveal.has(card.id)) return true;
  const hiddenAsAdvanced = advanced && smallAccount() && !searching;
  if (hiddenAsAdvanced) return false;
  if (shortList(searching) && !inShortList(card)) return false;
  return finder.tab === "all" || card.getAttribute("data-group") === finder.tab;
}

function cardInFinder(card) {
  const words = finder.query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hay = (card.getAttribute("data-find") || "").toLowerCase();
  return inTab(card, words.length > 0) && words.every((word) => hay.includes(word));
}

function findableCards() {
  return Array.from(document.querySelectorAll("[data-find]"));
}

function applyFinder() {
  const query = finder.query.trim().toLowerCase();
  const words = query ? query.split(/\s+/) : [];
  let showing = 0;

  findableCards().forEach((card) => {
    const hay = (card.getAttribute("data-find") || "").toLowerCase();
    // Every word has to appear somewhere, so "licence food" narrows
    // rather than widening the way an "any word" match would.
    const matches = words.every((word) => hay.includes(word));
    const show = inTab(card, words.length > 0) && matches && card.dataset.empty !== "1";
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
  // "More": how many cards the short list is leaving out, if any.
  const short = shortList(words.length > 0);
  let folded = 0;
  if (short) {
    finder.more = true;
    folded = findableCards().filter((card) => inTab(card, false)
      && card.dataset.empty !== "1").length - showing;
    finder.more = false;
  }
  $("more-cards").hidden = !short || folded <= 0;
  $("more-cards-btn").textContent = `More (${folded} other things you can do)`;
  const advNote = $("adv-note");
  if (advNote) advNote.hidden = !smallAccount() || finder.tab !== "all" || Boolean(query) || dayOne() || state.wall;
  renderTabEmpty();
  balanceLayout();
  const simple = simpleTabs();
  $("finder-search").hidden = simple;
  $("tabs-more").hidden = !simple;
  $("finder").classList.toggle("finder-simple", simple);

  document.querySelectorAll(".f-tab").forEach((tab) => {
    tab.setAttribute("aria-pressed",
                     String(tab.getAttribute("data-tab") === finder.tab));
  });
}

/* The page is two columns, the cards' and a side column. A tab that has
   nothing in one of them gives the other the whole width (theme.css,
   .layout.solo-main / .solo-side), so it is never one column beside an
   empty one. */
function balanceLayout() {
  const layout = document.querySelector("main .layout");
  if (!layout) return;
  const shown = (box) => Boolean(box) && Array.from(box.querySelectorAll("[data-find]"))
    .some((card) => !card.hidden);
  const main = shown(layout.querySelector(":scope > div"));
  const side = shown(layout.querySelector(":scope > aside"));
  layout.classList.toggle("solo-main", main && !side);
  layout.classList.toggle("solo-side", side && !main);
}

/* Which tabs to offer, and the one number worth printing on a tab: how
   many fridges and freezers are being watched on Fridges, and how many
   alerts are not closed on Alerts. It used to count the cards in each
   group, so Fridges said 2 (the list and the add form) for one fridge. */
function countTheTabs() {
  const counts = {};
  const small = smallAccount();
  findableCards().forEach((card) => {
    const isAdvanced = card.getAttribute("data-advanced") === "1";
    // On a small account an Advanced card is counted under Advanced only.
    if (isAdvanced && small) return;
    const group = card.getAttribute("data-group");
    counts[group] = (counts[group] || 0) + 1;
  });
  const d = state.data || {};
  const units = (d.sensors || []).length;
  const live = (d.summary || {}).open_incidents || 0;
  const figure = { fridges: units, alerts: live };
  document.querySelectorAll(".f-tab").forEach((tab) => {
    const key = tab.getAttribute("data-tab");
    // A group with nothing in it but Advanced cards (Licences, for a café)
    // is not offered as a tab of its own; Advanced holds them.
    tab.hidden = (small && key !== "all" && key !== "advanced" && !(counts[key] || 0))
      || (simpleTabs() && !SIMPLE_TABS.has(key));
    const n = figure[key];
    const show = n !== undefined && n > 0;
    let badge = tab.querySelector(".n");
    if (!show) { if (badge) badge.remove(); return; }
    if (!badge) {
      tab.insertAdjacentHTML("beforeend", ' <span class="n"></span>');
      badge = tab.querySelector(".n");
    }
    badge.textContent = String(n);
    badge.classList.toggle("n-live", key === "alerts");
  });
}

/* The full console opens on one tab, not on Everything: Alerts when
   something is not closed, Fridges otherwise. Everything was every card
   at once (forty of them on a desktop), and pressing a tab is what people
   do anyway. Once only, and never over a choice the person made: a tab
   they pressed, a button that led to a card, a search. The first day
   keeps its own short list (dayOne) and is decided when it is done. */
function landOnATab(d) {
  if (finder.userTab || finder.landed || state.wall || (dayOne(d) && !simpleTabs(d))) return;
  if (finder.tab !== "all" || finder.query.trim() || finder.reveal.size) return;
  finder.landed = true;
  const live = (d.incidents || []).some((i) => i.state !== "resolved")
    || (d.site_wide_alerts || []).some((i) => i.state !== "resolved");
  finder.tab = live ? "alerts" : "fridges";
  applyFinder();
}

document.querySelectorAll(".f-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    finder.userTab = true;
    finder.tab = tab.getAttribute("data-tab");
    // A card a checklist button brought out goes back where it is filed.
    finder.reveal.clear();
    applyFinder();
    // Back to the top of the list, or the first card of a short group
    // renders below where the reader is already looking.
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
});

$("tabs-more").addEventListener("click", () => {
  finder.expanded = true;
  try { sessionStorage.setItem("cyberlogix.tabs", "all"); } catch (_) { /* this visit only */ }
  applyFinder();
  countTheTabs();
});

$("more-cards-btn").addEventListener("click", () => {
  finder.more = true;
  applyFinder();
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
  // The pinned "Needs you now" bar sits under the nav, and the finder's
  // tabs under it, so neither covers the other.
  const bar = document.getElementById("needs-bar");
  const barH = bar && !bar.hidden ? Math.round(bar.getBoundingClientRect().height) : 0;
  document.documentElement.style.setProperty("--needs-h", `${barH}px`);
}
if (typeof ResizeObserver === "function") {
  const nav = document.querySelector(".nav");
  if (nav) new ResizeObserver(measureNav).observe(nav);
  const bar = document.getElementById("needs-bar");
  if (bar) new ResizeObserver(measureNav).observe(bar);
}
window.addEventListener("resize", measureNav);

/* "I'm on it" and "It's fixed" (static/answer.js), with this page's
   session, its toasts, and a fresh look once something changed. */
CLXAnswer.setup({
  token: () => state.token,
  say: (message, kind) => toast(message, kind || ""),
  done: () => refresh().catch(() => { /* the next poll tries again */ }),
});

/* ---------------- Pilot ----------------
   What Pilot, the assistant (static/pilot-cyberlogix-pack.js, docs/PILOT.md),
   may do on this page: through this page's own api(), with the signed-in
   person's own session and permissions, so the server checks every call
   exactly as it checks a button press here. Pilot reads the units and the
   alerts; it acknowledges or closes an alert, adds a unit or makes a proof
   report only after the person presses Allow on its card. Nothing here
   changes the plan, deletes anything, changes who gets called, or sends a
   text or a call. */
window.CyberLogixConsole = Object.freeze({
  signedIn: () => Boolean(state.token),
  api: (path, options) => api(path, options),
  tab: () => {
    const on = document.querySelector('.f-tab[aria-pressed="true"]');
    return on ? on.textContent.trim() : "";
  },
  // The sector new units go in, and its "fridge or freezer?" choices.
  sector: () => {
    const vertical = $("add-vertical").value;
    const profile = (state.industries || []).find((i) => i.vertical === vertical);
    return {
      vertical,
      name: profile ? profile.name : "",
      kinds: ((profile && profile.unit_kinds) || []).map((k) => ({ kind: k.kind, alarm: unitKindLabel(k) })),
    };
  },
  // The same as the Add a fridge or freezer form. The sensor's key is shown
  // once in that card's key box, as always, and never handed to Pilot.
  async addSensor(location, kind) {
    const vertical = $("add-vertical").value;
    const profile = (state.industries || []).find((i) => i.vertical === vertical);
    const kinds = (profile && profile.unit_kinds) || [];
    const made = await api("/api/licenses/me/sensors", {
      method: "POST",
      body: JSON.stringify({
        sensor_id: sensorIdFrom(location),
        industry_vertical: vertical,
        location_name: location,
        external_device_sn: null,
        unit_kind: kinds.length ? kind : null,
      }),
    });
    showIssuedKey(made.sensor.sensor_id, made.ingest_key);
    watchForFirstReading();
    refresh().catch(() => { /* the next poll paints it */ });
    return {
      added: made.sensor.location_name,
      sensor_id: made.sensor.sensor_id,
      alarm_above: made.sensor.danger_above_display,
      alarm_below: made.sensor.danger_below_display,
      units_used: `${made.seats_used} of ${made.seats_total}`,
      adds_to_monthly_bill_usd: made.billing ? made.billing.adds_monthly_usd : null,
    };
  },
  refresh: () => refresh().catch(() => { /* the next poll paints it */ }),
});

/* ---------------- boot ---------------- */

countTheTabs();
measureNav();
applyFinder();
initInspectorForm();

let saved = null;
try { saved = localStorage.getItem(TOKEN_STORAGE); } catch (_) {}
if (saved) {
  enter(saved).catch(() => {
    // Leaving for the Simple view aborts the requests still in flight;
    // that is not a session that failed, and must not sign anybody out.
    if (state.leaving) return;
    try { localStorage.removeItem(TOKEN_STORAGE); } catch (_) {}
    primeTheGate();
  });
} else {
  primeTheGate();
}

registerWorker();
