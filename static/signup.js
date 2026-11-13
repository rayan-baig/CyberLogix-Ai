"use strict";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));

const $ = (id) => document.getElementById(id);
const errorEl = $("error");

function fail(message) {
  errorEl.hidden = false;
  errorEl.textContent = message;
  errorEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

async function boot() {
  try {
    const resp = await fetch("/api/signup/sectors");
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const info = await resp.json();

    // A link from a friend carries their code: fill it in, and say the
    // longer trial it brings. The server checks the code either way.
    const ref = new URLSearchParams(location.search).get("ref") || "";
    if (ref) $("referral_code").value = ref.slice(0, 40);
    const days = ref ? info.referral_trial_days : info.trial_days;
    $("sub").textContent =
      `${days} days, up to ${info.max_sensors} sensors, no card.`;
    $("ref-hint").textContent =
      `A friend's code makes the trial ${info.referral_trial_days} days.`;
    if (info.starter) {
      $("after-trial").textContent =
        `After the trial, a small café or shop can stay on Starter: `
        + `${info.starter.monthly_label} a month for one location and up to `
        + `${info.starter.max_units} fridges or freezers, texts and the `
        + `site-wide alert included. Bigger estates use the rate card.`;
    }
    $("pw-hint").textContent =
      `At least ${info.min_password_length} characters.`;
    $("password").minLength = info.min_password_length;

    const select = $("industry_vertical");
    for (const sector of info.sectors) {
      const option = document.createElement("option");
      // textContent, not innerHTML: an <option> is still a place a tag
      // would be parsed.
      option.value = sector.key;
      option.textContent = sector.name;
      select.appendChild(option);
    }
  } catch (err) {
    $("sub").textContent = "";
    fail(`The sector list could not be loaded (${err.message}). The form `
       + "still works — leave the sector blank and set it later.");
  }
}

function stepsHtml(next) {
  return (next.steps || []).map((step, i) => `
    <div class="step">
      <div class="n">STEP ${i + 1}</div>
      <h3>${esc(step.title)}</h3>
      <p>${esc(step.detail)}</p>
      <pre>${esc(step.command)}</pre>
    </div>`).join("");
}

// The console's own storage keys, spelled identically. A test ties these to
// TOKEN_STORAGE and EMAIL_STORAGE in console.html, because a one-letter
// drift here would bring the original bug back without an error anywhere.
const SESSION_KEY = "cyberlogix.session";
const EMAIL_KEY = "cyberlogix.email";
// Where the console looks, once, for the business key it should show.
// sessionStorage, so it dies with the tab; the console deletes it on read.
const NEW_KEY = "cyberlogix.newkey";

function showDone(body) {
  // The response says "You are signed in", and it was not true in the
  // browser: the session came back and was dropped, so "Open the console"
  // landed on the sign-in form. Kept now, the way the console keeps its
  // own. Storage can be refused (a private window, blocked site data); if
  // it is, the console simply asks for the password they just chose, and
  // the email is at least filled in if that part was allowed.
  let kept = false;
  try {
    if (body.token) { localStorage.setItem(SESSION_KEY, body.token); kept = true; }
  } catch (_) {}
  try {
    if (body.user && body.user.email) localStorage.setItem(EMAIL_KEY, body.user.email);
  } catch (_) {}

  // Straight into the console, signed in. This page used to stop here on
  // a panel of four curl commands and an "Open the console" button: a
  // page and a click between signing up and the first thing to do, for
  // instructions a restaurant cannot run. The console shows the business
  // key, once, in the same box it shows every other new key, and its
  // checklist says what to do next. Only when the browser refuses
  // storage -- and so would arrive at a sign-in form, without the key --
  // does the old panel stay, so the key is never lost.
  let handed = false;
  try { sessionStorage.setItem(NEW_KEY, body.api_key); handed = true; } catch (_) {}
  if (kept && handed) {
    location.replace("/console");
    return;
  }

  const next = body.next_steps || {};
  $("panel").classList.add("done");
  $("panel").innerHTML = `
    <h2>${esc(body.tenant.company_name)} is live</h2>
    <p class="sub">${esc(body.message)}</p>

    <div class="keybox">
      <p class="lbl">Your API key — shown once, and never again. Copy it now.</p>
      <code id="apikey">${esc(body.api_key)}</code>
      <button class="go" type="button" id="copy">Copy the key</button>
    </div>

    <p class="sub">${esc(next.summary || "")}</p>
    ${stepsHtml(next)}
    <p class="small">${esc(next.then || "")}</p>
    <button class="go" type="button" id="open-console">Open the console</button>`;

  $("copy").addEventListener("click", async () => {
    const key = $("apikey").textContent;
    try {
      await navigator.clipboard.writeText(key);
      $("copy").textContent = "Copied";
    } catch (err) {
      // Clipboard access is refused in plenty of ordinary situations —
      // an insecure origin, a browser setting. Select it so the key is
      // still one keystroke away rather than telling them it failed.
      const range = document.createRange();
      range.selectNodeContents($("apikey"));
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      $("copy").textContent = "Selected — press Ctrl/Cmd+C";
    }
  });

  $("open-console").addEventListener("click", () => {
    location.href = "/console";
  });
}

$("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  errorEl.hidden = true;
  $("go").disabled = true;
  $("go").textContent = "Starting…";

  const form = new FormData(event.target);
  const payload = Object.fromEntries(form.entries());
  if (!payload.industry_vertical) delete payload.industry_vertical;
  if (!String(payload.referral_code || "").trim()) delete payload.referral_code;

  try {
    const resp = await fetch("/api/signup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await resp.json();
    if (!resp.ok) {
      const detail = body.detail;
      throw new Error(
        typeof detail === "string"
          ? detail
          : (Array.isArray(detail) && detail[0] && detail[0].msg)
            || `Sign-up failed (HTTP ${resp.status}).`
      );
    }
    showDone(body);
  } catch (err) {
    fail(err.message);
    $("go").disabled = false;
    $("go").textContent = "Start the trial";
  }
});

boot();
