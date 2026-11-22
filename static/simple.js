"use strict";

/* No framework and no build step, same as the console. The whole page is
   one fetch and one render, because a page that shows a person whether
   their food is safe should not be able to fail in a way that leaves
   half of it on the screen. */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
            '"': "&quot;", "'": "&#39;" }[c]));

function token() {
  try { return localStorage.getItem("cyberlogix.session"); }
  catch (_) { return null; }
}

/* Words, not jargon. Every string a person reads on this page comes from
   one of these, so there is one place to check that none of them has
   crept back towards the language of the API. */
function howLongAgo(iso) {
  const then = new Date(iso);
  if (isNaN(then)) return "at an unknown time";
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins} minutes ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return hours === 1 ? "an hour ago" : `${hours} hours ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

/* What a thing is called in words somebody would say out loud. The API
   calls a walk-in freezer an "asset" with a "location_name"; nobody has
   ever walked into a kitchen and pointed at an asset. */
function nameOf(unit) {
  const where = (unit.location_name || "").split("/").pop().trim();
  return where || unit.sensor_id;
}

/* The same name, but enough of it to know which one. Two sites both have
   a thing called "Walk-In", and "Walk-In is too warm" on an estate with
   two of them is not an instruction, it is a riddle. The list below puts
   the site in its own column; a line of prose has to carry it itself. */
function whichOne(unit, all) {
  const name = nameOf(unit);
  const shared = all.filter((other) => nameOf(other) === name).length > 1;
  return shared && unit.site_name ? `${name} at ${unit.site_name}` : name;
}

/* How long until it is a problem, in units a person uses. Rounding
   everything up to "about 1 hour" hid the difference between six minutes
   and fifty, and six minutes is the one you run for. */
function howLongUntil(hours) {
  const mins = Math.round(hours * 60);
  if (mins <= 1) return "within minutes";
  if (mins < 60) return `in about ${mins} minutes`;
  const whole = Math.round(hours);
  return `in about ${whole} hour${whole === 1 ? "" : "s"}`;
}

/* What a unit is doing, from the server's one `condition`, in the same
   words as the console's card and Problems list (static/words.js). */
const WORD_CLASS = { quiet: "w-quiet", ok: "w-ok", watch: "w-soon", warm: "w-warm" };

function stateOf(unit) {
  const c = CLXWords.condition(unit);
  if (c.state === "offline") {
    return { word: "No signal", cls: "w-quiet",
             why: `We have not heard from this one since ${
               howLongAgo(unit.last_seen)}. It might be unplugged, out of
               range, or its battery might be flat. Until it starts
               talking again, nobody is watching that food.` };
  }
  if (c.state === "waiting_first") {
    return { word: "Waiting for its first reading", cls: "w-quiet", why: c.line, first: true };
  }
  if (c.state === "watching" || c.state === "alert" || c.state === "paused") {
    return { word: c.word, cls: WORD_CLASS[c.kind], why: c.line, state: c.state,
             held: Boolean(c.held), overdue: Boolean(c.overdue) };
  }
  if (unit.hours_until_breach != null && unit.hours_until_breach <= 6) {
    return { word: "Heading the wrong way", cls: "w-soon",
             why: `It is still a safe temperature, but at this rate it stops
               being one ${howLongUntil(unit.hours_until_breach)}.` };
  }
  return { word: "Fine", cls: "w-ok", why: "" };
}

/* The alerts still live: nobody has answered (open), or somebody has it
   and it is not fixed yet (acknowledged). Each one is a "Needs you now". */
function liveAlerts(data) {
  return (data.incidents || []).filter((i) => i.state !== "resolved" && !i.site_wide);
}

function renderNeeds(data, units) {
  const live = liveAlerts(data);
  const sitewide = (data.site_wide_alerts || []).filter((i) => i.state !== "resolved");
  $("needs-wrap").hidden = !live.length && !sitewide.length;
  const byId = Object.fromEntries(units.map((u) => [u.sensor_id, u]));
  const cards = sitewide.map((i) => `
    <div class="need">
      <p class="need-what"><strong>Several fridges are warming at once.</strong>
        ${esc(i.breach_details || "")}</p>
      <p class="need-when">Started ${esc(howLongAgo(i.out_since || i.opened_at))}.</p>
      ${needButtons(i)}
    </div>`).concat(live.map((i) => {
    const unit = byId[i.sensor_id] || { sensor_id: i.sensor_id };
    const now = unit.last_temperature_display != null
      ? `${unit.last_temperature_display}°` : `${i.temperature_display}°`;
    const who = i.state === "acknowledged"
      ? (i.fix_reported_at
          ? `${esc(CLXWords.person(i.fix_reported_by_name, i.fix_reported_by))} said it is fixed. It closes by itself once it is back in range.`
          : `${esc(CLXWords.person(i.acknowledged_by_name, i.acknowledged_by))} is on it.`)
      : CLXWords.nobodyYet(i);
    return `
    <div class="need ${i.state === "open" ? "need-open" : ""}">
      <p class="need-what"><strong>${esc(whichOne(unit, units))}</strong> is
        ${esc(now)} and has been out of range for ${esc(CLXWords.outFor(i.out_since || i.opened_at))},
        since ${esc(CLXWords.clock(i.out_since || i.opened_at))}.</p>
      <p class="need-when">${who}</p>
      ${needButtons(i)}
    </div>`;
  })).join("");
  $("needs").innerHTML = cards;
}

function needButtons(i) {
  return `<div class="need-acts">
      ${i.state === "open" ? `<button class="btn btn-primary" type="button"
          data-ack="${esc(i.incident_id)}">I'm on it</button>` : ""}
      <button class="btn" type="button" data-fixed="${esc(i.incident_id)}">It's fixed</button>
    </div>`;
}

/* "I'm on it" and "It's fixed": the same flow as the console
   (static/answer.js). A unit still out of range is not closed; it says
   so, and closing it anyway takes a line saying why. */
CLXAnswer.setup({
  token,
  say: (message) => { $("when").textContent = message; },
  done: () => load(),
});

$("needs").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.ack) CLXAnswer.onIt(button.dataset.ack, button);
  else if (button.dataset.fixed) CLXAnswer.fixed(button.dataset.fixed, button);
});

/* The headline. One sentence, chosen by how bad the worst thing is. */
function verdict(units, jobs, paused, live) {
  const box = $("verdict");
  // Paused by the owner (retention.py): no "everything is fine" while
  // nobody is being texted or called.
  if (paused) {
    box.className = "verdict bad";
    box.innerHTML =
      `<h1>Alerts are paused until ${esc(paused.resume_on)}</h1>
       <p>${esc(paused.words)}</p>`;
    return;
  }
  // An alert that is still live outranks everything else on the page:
  // the person reading this may be the one we texted.
  const unanswered = live.filter((i) => i.state === "open").length;
  if (live.length) {
    box.className = "verdict bad";
    box.innerHTML = unanswered
      ? `<h1>${unanswered === 1 ? "An alert needs" : `${unanswered} alerts need`} you now</h1>
         <p>Nobody has said they are on it yet. Press <b>I'm on it</b> so everyone
            knows somebody has it, then <b>It's fixed</b> once it is cold again.</p>`
      : `<h1>Being dealt with</h1>
         <p>Somebody is on it. Press <b>It's fixed</b> once it is cold again.</p>`;
    return;
  }
  const urgent = jobs.filter((j) => j.now).length;
  if (urgent) {
    box.className = "verdict bad";
    box.innerHTML =
      `<h1>${urgent} thing${urgent === 1 ? "" : "s"} need${
        urgent === 1 ? "s" : ""} you now</h1>
       <p>${jobs.some((j) => j.now && j.mark === "!")
          ? "A fridge or freezer is warmer than its limit. Check it now."
          : "A sensor has stopped reporting, so that food is not being watched."}
          What to do is written under each one.</p>`;
    return;
  }
  // Out of range and not alerted yet (inside its wait, or held after
  // somebody closed it by hand): never "Nothing is spoiling" while a
  // fridge is warm. It says which one, how warm, and when we alert.
  const out = units.filter((u) => (u.condition || {}).state === "watching");
  if (out.length) {
    const c = CLXWords.condition(out[0]);
    const name = whichOne(out[0], units);
    box.className = c.overdue ? "verdict bad" : "verdict watch";
    if (out.length > 1) {
      box.innerHTML =
        `<h1>${out.length} fridges and freezers are out of range</h1>
         <p>Nobody has been texted about them yet. Each one says below when
            we will. Check the doors are shut and nothing warm went in.</p>`;
    } else if (c.held) {
      box.innerHTML =
        `<h1>${esc(name)} is still ${c.word === "Too cold" ? "too cold" : "too warm"}</h1>
         <p>${esc(c.line)}</p>`;
    } else {
      box.innerHTML =
        `<h1>${esc(name)} is ${c.word === "Too cold" ? "too cold" : "too warm"}</h1>
         <p>${esc(c.line)}${c.overdue ? "" : " Nobody has been texted yet: if it comes back in range first, nobody is."}</p>`;
    }
    return;
  }
  if (jobs.length) {
    box.className = "verdict watch";
    box.innerHTML =
      `<h1>Nothing is spoiling</h1>
       <p>${jobs.length} thing${jobs.length === 1 ? "" : "s"} worth sorting
          out when you get a minute. None of it is an emergency.</p>`;
    return;
  }
  const n = units.length;
  // A new business: "all 0 of your fridges are fine" is not fine.
  if (!n) {
    box.className = "verdict watch";
    box.innerHTML =
      `<h1>Nothing is being watched yet</h1>
       <p>Add your first sensor in the console, and this page will say
          whether your food is safe.</p>`;
    return;
  }
  box.className = "verdict ok";
  box.innerHTML = n === 1
    ? `<h1>Everything is fine</h1>
       <p>Your fridge is the right temperature and reporting in. There is
          nothing for you to do.</p>`
    : `<h1>Everything is fine</h1>
       <p>All ${n} of your fridges and freezers are the right temperature and
          reporting in. There is nothing for you to do.</p>`;
}

/* What happens when nobody answers, in the words of this account's plan.
   "We phone a human until one answers" was printed for a Starter account
   that never places a call. */
function alertWords(calls) {
  if (!calls) return;
  if (calls.mode === "plan") {
    $("how-alert-head").textContent = "We phone a human until one answers.";
    $("how-alert-body").textContent = "When a fridge stays too warm: a text "
      + "first, then an actual phone call, then the next person on the list. "
      + "An alert nobody picks up is the same as no alert, so we keep going "
      + "until somebody does. If it comes back to normal first, nobody is phoned.";
  } else {
    $("how-alert-head").textContent = "We text the people on your list.";
    $("how-alert-body").textContent = "Everyone on call gets a text when a "
      + "fridge stays too warm, saying which fridge and how warm. " + calls.words;
  }
}

/* The forecast is promised only on a plan that has it. */
function watchWords(entitlements) {
  if (!entitlements || !entitlements.predictive_forecasting) return;
  $("how-watch").textContent = "Every reading is checked against the range "
    + "that food has to stay in. Not just too warm now, but also warming up "
    + "faster than it should be, which is the hour before it is a disaster. "
    + "A door opened for a minute is not an alarm.";
}

/* Open the explanation for somebody new. For everyone else it folds to
   one quiet line, "How this works" (3.28.0): the folded box was still a
   big panel at the foot of a returning owner's screen. */
function introFor(units) {
  let seen = false;
  try { seen = localStorage.getItem("cyberlogix.today.seen") === "1"; } catch (_) { /* private */ }
  if (!seen || !units.length) {
    $("intro").open = true;
  } else {
    $("intro").classList.add("intro-back");
    $("intro").querySelector("summary").textContent = "How this works";
  }
  try { localStorage.setItem("cyberlogix.today.seen", "1"); } catch (_) { /* private */ }
}

/* "This month: 1 alert, answered in 4 minutes, food kept." (3.28.0), from
   the server's this_month, by the same rule as each alert's line. */
function monthLine(data, units) {
  const m = data.this_month;
  const box = $("month");
  if (!m || !units.length || !units.some((u) => u.last_seen)) { box.hidden = true; return; }
  $("month-line").textContent = m.words;
  const sub = $("month-sub");
  sub.hidden = !m.food_usd;
  if (m.food_usd) {
    sub.textContent = `About $${Number(m.food_usd).toLocaleString("en-US")} of food protected: `
      + "an estimate of what a typical fridge or freezer of each kind holds.";
  }
  box.hidden = false;
}

let introDone = false;

function render(data) {
  const units = data.sensors || [];
  const jobs = [];
  const live = liveAlerts(data);
  const alerted = new Set(live.map((i) => i.sensor_id));
  alertWords((data.entitlements || {}).calls);
  watchWords(data.entitlements);
  if (!introDone) { introFor(units); introDone = true; }
  renderNeeds(data, units);

  units.forEach((unit) => {
    const state = stateOf(unit);
    const name = whichOne(unit, units);
    // Out of range inside its wait: nobody has been texted yet, and this
    // says exactly when we will, as the console's card does.
    if (state.state === "watching" && state.held) {
      jobs.push({ now: false, mark: "!",
        what: `<strong>${esc(name)}</strong>: ${esc(state.why)}`,
        todo: "Keep the food somewhere cold until it is back in range. It "
            + "closes by itself once it is cold, and a reading far past its "
            + "limit alerts everyone at once." });
    } else if (state.state === "watching") {
      jobs.push({ now: Boolean(state.overdue), mark: "!",
        what: `<strong>${esc(name)}</strong>: ${state.overdue ? "" : "watching. "}${esc(state.why)}`,
        todo: "Check the door is shut and nothing warm went in. If it comes "
            + "back in range first, nobody is texted." });
    } else if (state.cls === "w-warm" && !alerted.has(unit.sensor_id)
               && state.state !== "alert") {
      jobs.push({ now: true, mark: "!",
        what: `<strong>${esc(name)}</strong> is too warm. ${
          esc(state.why)}`,
        todo: "Check the door is shut and the unit is running. If it stays "
            + "too warm we text everyone on call. If it is broken, move the "
            + "food somewhere cold now and write down what you did — that "
            + "record is what an inspector or an insurer asks for." });
    } else if (state.first) {
      jobs.push({ now: false, mark: "…",
        what: `<strong>${esc(name)}</strong> is waiting for its first reading.`,
        todo: "Once its sensor is switched on and connected, its temperature "
            + "shows here. Nothing is wrong.",
        help: "/help/sensors" });
    } else if (state.cls === "w-quiet") {
      jobs.push({ now: true, mark: "?",
        what: `<strong>${esc(name)}</strong> has gone quiet. ${
          esc(state.why)}`,
        todo: "Go and look at it. A sensor that stopped talking is the one "
            + "problem this app cannot warn you about by itself.",
        help: "/help#sensor-offline" });
    } else if (state.cls === "w-soon") {
      jobs.push({ now: false, mark: "↑",
        what: `<strong>${esc(name)}</strong> is warming up. ${
          esc(state.why)}`,
        todo: "Worth a look before it becomes a problem. Often it is a "
            + "door left open or something warm put in recently." });
    }
    if (unit.battery_low) {
      jobs.push({ now: false, mark: "▫",
        what: `The sensor in <strong>${esc(name)}</strong> has
               ${Math.round(unit.battery_percent)}% battery left.`,
        todo: "Change the battery in the next week or so. When it dies, "
            + "that fridge stops being watched." });
    }
  });

  verdict(units, jobs, (data.account || {}).paused, live);

  $("todo-wrap").hidden = jobs.length === 0;
  // Urgent first: somebody reading on a phone in a kitchen sees two
  // items, not eight, and the two they see are the two that matter.
  // Then a unit being watched (we may text soon) before the calm ones.
  jobs.sort((a, b) => Number(b.now) - Number(a.now)
    || Number(b.mark === "!") - Number(a.mark === "!"));
  $("todo").innerHTML = jobs.map((job) => `
    <div class="todo ${job.now ? "now" : ""}">
      <span class="todo-mark" aria-hidden="true">${job.mark}</span>
      <span class="grow">
        <p class="todo-what">${job.what}</p>
        <p class="todo-do">${esc(job.todo)}</p>
        ${job.help ? `<a class="offline-help" href="${esc(job.help)}"
            >${job.help === "/help/sensors" ? "How to connect a sensor"
              : "What to check, step by step"}</a>` : ""}
      </span>
    </div>`).join("");

  $("things").innerHTML = units.length ? units.map((unit) => {
    const state = stateOf(unit);
    return `<div class="thing">
      <span class="thing-temp">${
        unit.last_temperature_display == null ? "—"
          : esc(unit.last_temperature_display) + "°"}</span>
      <span class="grow">
        <span class="thing-name">${esc(nameOf(unit))}</span><br>
        <span class="thing-where">${unit.site_name ? `${esc(unit.site_name)} · ` : ""}${
          unit.last_seen ? `heard from ${esc(howLongAgo(unit.last_seen))}` : "no reading yet"}</span>
      </span>
      <span class="thing-word ${state.cls}">${esc(state.word)}</span>
    </div>`;
  }).join("") : `<p class="sub">Your fridges and freezers will show here once
      the first sensor is added.</p>
      <a class="btn btn-sm" href="/console">Add your first sensor</a>`;

  monthLine(data, units);
  $("when").textContent = "Checked " + howLongAgo(new Date().toISOString());
}

async function load() {
  const key = token();
  if (!key) { location.href = "/console"; return; }
  try {
    const got = await fetch("/api/console/overview",
                            { headers: { Authorization: "Bearer " + key } });
    if (got.status === 401) { location.href = "/console"; return; }
    if (!got.ok) throw new Error("could not read your fridges");
    lastData = await got.json();
    render(lastData);
  } catch (err) {
    // Never a blank page and never a stale one presented as current: the
    // dangerous failure here is a screen that looks fine because it is
    // showing what was true an hour ago.
    $("verdict").className = "verdict watch";
    $("verdict").innerHTML =
      `<h1>Cannot check right now</h1>
       <p>The app could not reach your fridges just now, so nothing on this
          page is up to date. It will keep trying.</p>`;
    $("things").innerHTML = "";
    $("todo-wrap").hidden = true;
    $("needs-wrap").hidden = true;
  }
}

/* Asked again every 30 seconds, and just after any "we'll text at" time,
   so the page turns into the alert as it opens, not half a minute later. */
const POLL_MS = 30000;
let pollTimer = null;
let lastData = null;
async function tick() {
  clearTimeout(pollTimer);
  await load();
  pollTimer = setTimeout(tick, CLXWords.pollDelay((lastData || {}).sensors, POLL_MS));
}
tick();

/* What this page last loaded, for Pilot's answers that need no AI key
   (3.27.0): "Which fridges need me?" and the like are read from it in the
   browser, with nothing fetched and nothing sent. */
window.CyberLogixToday = Object.freeze({ overview: () => lastData });
