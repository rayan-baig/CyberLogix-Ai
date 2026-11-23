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
    return { word: c.back ? "Back in range: press It's fixed" : c.word,
             cls: WORD_CLASS[c.kind], why: c.line, state: c.state, back: Boolean(c.back),
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
    const back = Boolean(((unit.condition || {}).back_in_range));
    const who = i.state === "acknowledged"
      ? (i.fix_reported_at
          ? `${esc(CLXWords.person(i.fix_reported_by_name, i.fix_reported_by))} said it is fixed. It closes by itself once it is back in range.`
          : `${esc(CLXWords.person(i.acknowledged_by_name, i.acknowledged_by))} is on it.`)
      : CLXWords.nobodyYet(i);
    return `
    <div class="need ${i.state === "open" ? "need-open" : ""}">
      ${back ? `<p class="need-what"><strong>${esc(whichOne(unit, units))}</strong> is
        back in range at ${esc(now)}. Press <b>It's fixed</b> to close the alert.</p>`
      : `<p class="need-what"><strong>${esc(whichOne(unit, units))}</strong> is
        ${esc(now)} and has been out of range for ${esc(CLXWords.outFor(i.out_since || i.opened_at))},
        since ${esc(CLXWords.clock(i.out_since || i.opened_at))}.</p>`}
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
  done: () => { load().then(() => { if (didAsked) loadDid(); }); },
  // After "Thrown away" (3.32.0): the note starts with which unit and how
  // long it was out, and the insurance pack opens in the full console.
  discardNote: (id) => discardNote(id),
});

/* "Thrown away from Walk-in fridge after 2 hours out (worst 52°F): ",
   for the person to finish with what went in the bin (3.32.0). */
function discardNote(id) {
  const data = lastData || {};
  const i = (data.incidents || []).find((x) => x.incident_id === id);
  if (!i) return "Thrown away: ";
  const unit = (data.sensors || []).find((u) => u.sensor_id === i.sensor_id) || { sensor_id: i.sensor_id };
  const long = CLXWords.outFor(i.out_since || i.opened_at, i.out_until || i.resolved_at);
  const worst = i.peak_display != null ? ` (worst ${i.peak_display}°${i.temperature_unit || ""})` : "";
  // A plain string for a text box, never markup.
  return "Thrown away from " + nameOf(unit) + (long ? " after " + long + " out" : "") + worst + ": ";
}

function onAnswerClick(event) {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.ack) CLXAnswer.onIt(button.dataset.ack, button);
  else if (button.dataset.fixed) CLXAnswer.fixed(button.dataset.fixed, button);
  else if (button.dataset.foodKept) CLXAnswer.food(button.dataset.foodKept, "kept", button);
  else if (button.dataset.foodThrown) CLXAnswer.food(button.dataset.foodThrown, "thrown", button);
  else if (button.dataset.foodChange) {
    // "Change" (3.32.0): the two answers, in place of the link.
    const row = button.closest(".answer-row");
    if (row) row.classList.add("changing");
  } else if (button.dataset.lossPack) CLXAnswer.packet(button.dataset.lossPack);
  else if (button.dataset.lossNote) CLXAnswer.lossNote(button.dataset.lossNote);
}
["needs", "verdict", "todo", "month"].forEach((id) => $(id).addEventListener("click", onAnswerClick));

/* The food question (3.30.0): a closed alert nobody has said the food
   was kept or thrown away for, in the last week (food_value.question).
   "Everything is fine... nothing for you to do" was said beside "food at
   risk on 1: check it". Riskiest first. */
function foodQuestions(data) {
  return (data.incidents || []).filter((i) => i.state === "resolved" && !i.site_wide
    && i.food_question && i.food_question.ask_on_today)
    .sort((a, b) => Number(b.food_question.at_risk) - Number(a.food_question.at_risk)
      || String(b.resolved_at).localeCompare(String(a.resolved_at)));
}

function foodButtons(i, viewer) {
  if (viewer) return "";
  const kept = i.food_question.word === "stock" ? "Stock kept" : "Food kept";
  return `<div class="need-acts food-acts">
      <button class="btn btn-primary" type="button" data-food-kept="${esc(i.incident_id)}">${kept}</button>
      <button class="btn" type="button" data-food-thrown="${esc(i.incident_id)}">Thrown away</button>
    </div>`;
}

function foodWhy(i) {
  return `${CLXWords.foodBack(i.food_question)}. ${i.food_question.note}`;
}

/* The headline. One sentence, chosen by how bad the worst thing is. */
let lossShown = null;
function verdict(units, jobs, paused, live, food, loss) {
  const box = $("verdict");
  lossShown = null;
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
  const byId = Object.fromEntries(units.map((u) => [u.sensor_id, u]));
  const backAgain = live.length && !unanswered && live.every((i) =>
    ((byId[i.sensor_id] || {}).condition || {}).back_in_range);
  if (backAgain) {
    // Somebody has it and the reading is back in range: the one thing
    // left is "It's fixed" (3.30.0), not "once it is cold again".
    box.className = "verdict watch";
    box.innerHTML = `<h1>Back in range: press It's fixed</h1>
       <p>${live.length === 1 ? `${esc(whichOne(byId[live[0].sensor_id], units))} is cold again.`
         : "They are cold again."} Press <b>It's fixed</b> below to close the alert, then
          say what happened to the food.</p>`;
    return false;
  }
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
  const out = units.filter((u) => (u.condition || {}).state === "watching");
  if (food.length && !urgent && !out.length) {
    // "One thing to do: check the food in Walk-in fridge", with the two
    // answers right here (3.30.0).
    const first = food[0];
    const name = whichOne(byId[first.sensor_id] || { sensor_id: first.sensor_id }, units);
    const others = food.length - 1;
    box.className = first.food_question.at_risk ? "verdict bad" : "verdict watch";
    box.innerHTML = `<h1>${food.length === 1 ? "One thing" : `${food.length} things`} to do:
         check the ${esc(first.food_question.word)} in ${esc(name)}</h1>
       <p>${esc(foodWhy(first))}${others ? ` ${others} more below.` : ""}</p>
       ${foodButtons(first, viewerOnly())}`;
    return true;
  }
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
  // Food thrown away in the last day (3.33.0): said calmly, with the
  // insurance pack one tap away, never the all-clear ("nothing for you to
  // do") the morning after a loss.
  if (loss && units.length) {
    lossShown = loss.incident_id;
    const id = esc(loss.incident_id);
    const by = loss.by_name ? `, by ${esc(loss.by_name)}` : "";
    box.className = "verdict calm";
    box.innerHTML = `<h1>Food thrown away from ${esc(loss.unit)}: make the insurance pack</h1>
       <p>Recorded at ${esc(CLXWords.clock(loss.at))}${by}. Your fridges are back to normal. The pack has
          the readings, who was told and when, and ${loss.note ? "your note of what went" : "room for a note of what went"},
          ready to send to your insurer.${jobs.length ? ` ${esc(CLXWords.count(jobs.length, "thing"))} to sort out below.` : ""}</p>
       <div class="need-acts loss-acts">
         <button class="btn btn-primary" type="button" data-loss-pack="${id}">Make the insurance pack</button>
         ${loss.note || viewerOnly() ? "" : `<button class="btn" type="button" data-loss-note="${id}">Add what was thrown away</button>`}
       </div>`;
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

/* Who is signed in, and their own settings (3.30.0): read once. A
   machine key or an older server answers without them, and the page
   works as before. */
let me = null;
async function loadMe() {
  const key = token();
  if (!key) return null;
  try {
    const got = await fetch("/api/accounts/me", { headers: { Authorization: "Bearer " + key } });
    if (got.ok) me = await got.json();
  } catch (_) { me = null; }
  return me;
}

function viewerOnly() {
  return Boolean(me && me.user && me.user.role === "viewer");
}

/* Saved for the person, not the device (3.30.0): whether "What this is"
   is open or folded follows them from the phone to the office computer. */
function savePref(name, value) {
  const key = token();
  if (!key || !me) return;
  fetch("/api/accounts/me/prefs", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + key },
    body: JSON.stringify({ [name]: value }),
  }).catch(() => { /* the next toggle tries again */ });
}

/* Open the explanation for somebody new. For everyone else it folds to
   one quiet line, "How this works" (3.28.0). Which it is now comes from
   the person's own saved choice (3.30.0): opening or folding it is saved
   for them, and somebody who has never chosen sees it open once. */
function foldIntro(open) {
  const box = $("intro");
  box.open = open;
  box.classList.toggle("intro-back", !open);
  box.querySelector("summary").textContent = open
    ? "What this is, and how it works" : "How this works";
}

let introState = null;
function introFor(units) {
  const saved = me && me.prefs ? me.prefs.today_intro : null;
  let open;
  if (saved === "open" || saved === "closed") {
    open = saved === "open";
  } else if (me) {
    open = true;
    // Seen once: folded next time, wherever they sign in.
    savePref("today_intro", "closed");
  } else {
    // No account to save it on (a machine key): this device remembers.
    let seen = false;
    try { seen = localStorage.getItem("cyberlogix.today.seen") === "1"; } catch (_) { /* private */ }
    open = !seen;
    try { localStorage.setItem("cyberlogix.today.seen", "1"); } catch (_) { /* private */ }
  }
  if (!units.length) open = true;
  introState = open;
  foldIntro(open);
}

$("intro").addEventListener("toggle", () => {
  const open = $("intro").open;
  // The page folding it on load fires this too: only a change counts.
  if (open === introState) return;
  introState = open;
  // The label follows (3.32.0): "How this works" folded, "What this is,
  // and how it works" open, whichever way it was toggled.
  foldIntro(open);
  savePref("today_intro", open ? "open" : "closed");
  if (me) me.prefs = Object.assign({}, me.prefs || {}, { today_intro: open ? "open" : "closed" });
});

/* "This month: 3 alerts · all answered · 1 food answer missing" (3.32.0),
   from the server's this_month, by the same rule as each alert's line. */
function monthLine(data, units) {
  const m = data.this_month;
  const box = $("month");
  if (!m || !units.length || !units.some((u) => u.last_seen)) { box.hidden = true; return; }
  $("month-line").textContent = m.words;
  // Who said what about the food, and when (3.30.0): "Walk-in fridge:
  // Thrown away, Jo, 4:09 PM." With "Change" for 24 hours, and after
  // "Thrown away" the insurance pack and the note of what went (3.32.0).
  // The dollar figure only for food somebody said was kept, never for a
  // short blip and never because the fridge came back quickly.
  const answers = m.food_answers || [];
  const many = m.alerts > 1 || answers.length > 1;
  const viewer = viewerOnly();
  const rows = answers.map((a) => {
    const who = [a.words, a.auto ? "recorded automatically" : a.by_name, CLXWords.clock(a.at)]
      .filter(Boolean).join(", ");
    const open = !viewer && a.changeable && CLXWords.changeable(a.change_until);
    const id = esc(a.incident_id);
    return `<div class="answer-row">
      <p class="answer-what">${esc(many ? `${a.unit}: ${who}.` : `${who}.`)}${open
        ? ` <button class="linkish answer-change" type="button" data-food-change="${id}">Change</button>` : ""}</p>
      ${a.note ? `<p class="answer-note">“${esc(a.note)}”</p>` : ""}
      ${open ? `<div class="answer-change-to food-acts">
        <button class="btn btn-sm" type="button" data-food-kept="${id}"${a.disposition === "unaffected" && !a.auto ? ' aria-pressed="true"' : ""}>${a.disposition === "unaffected" && !a.auto ? "✓ " : ""}Food kept</button>
        <button class="btn btn-sm" type="button" data-food-thrown="${id}"${a.thrown ? ' aria-pressed="true"' : ""}>${a.thrown ? "✓ " : ""}Thrown away</button>
      </div>` : ""}
      ${open && a.thrown && a.incident_id !== lossShown ? `<div class="answer-loss">
        <button class="btn btn-sm" type="button" data-loss-pack="${id}">Make the insurance pack</button>
        ${a.has_note ? "" : `<button class="btn btn-sm" type="button" data-loss-note="${id}">Add what was thrown away</button>`}
      </div>` : ""}
    </div>`;
  });
  if (m.food_usd) {
    rows.push(`<p class="answer-what">${esc(`About $${Number(m.food_usd).toLocaleString("en-US")} of food kept: `
      + "an estimate of what a typical fridge or freezer of each kind holds.")}</p>`);
  }
  const sub = $("month-sub");
  sub.hidden = !rows.length;
  sub.innerHTML = rows.join("");
  box.hidden = false;
}

/* What Kryozyn did for you (3.32.0): the week or the month, read once
   when the page opens and again when the other is picked. */
let didPeriod = "week";
async function loadDid() {
  const key = token();
  const box = $("did");
  if (!key || !lastData || !(lastData.sensors || []).length) { box.hidden = true; return; }
  let got = null;
  try {
    const res = await fetch(`/api/console/did-for-you?period=${didPeriod}`,
      { headers: { Authorization: "Bearer " + key } });
    if (res.ok) got = await res.json();
  } catch (_) { got = null; }
  if (!got) { box.hidden = true; return; }
  renderDid(got);
  box.hidden = false;
  followDid();
}

let lastDid = null;
function renderDid(got) {
  lastDid = got;
  const f = got.figures || {};
  $("did-teaser").textContent = `${got.label}: ${CLXWords.count(f.readings, "reading")} checked, ${
    f.alerts ? CLXWords.count(f.alerts, "alert") : "no alerts"}.`;
  $("did-lines").innerHTML = (got.lines || []).map((line) => `<li>${esc(line)}</li>`).join("");
  const sample = got.sample;
  $("did-sample").hidden = !sample;
  if (sample) {
    $("did-note").textContent = sample.note;
    $("did-sample-lines").innerHTML = (sample.lines || []).map((line) => `<li>${esc(line)}</li>`).join("");
  }
  $("did-log").href = got.log_link;
  document.querySelectorAll("[data-did]").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.did === got.period)));
}

/* "/today#did" (Pilot's "What Kryozyn did for you") opens it. */
function followDid() {
  if (location.hash !== "#did" || $("did").hidden) return;
  $("did").open = true;
  $("did").scrollIntoView({ block: "start" });
  history.replaceState(null, "", location.pathname + location.search);
}
window.addEventListener("hashchange", followDid);

$("did").addEventListener("click", (event) => {
  const b = event.target.closest("[data-did]");
  if (!b || b.dataset.did === didPeriod) return;
  didPeriod = b.dataset.did;
  loadDid();
});

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
    } else if (state.cls === "w-soon" && state.state !== "alert") {
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

  const food = foodQuestions(data);
  const shownAbove = verdict(units, jobs, (data.account || {}).paused, live, food, data.recent_loss) === true;
  const byId = Object.fromEntries(units.map((u) => [u.sensor_id, u]));
  (shownAbove ? food.slice(1) : food).forEach((i) => {
    const name = whichOne(byId[i.sensor_id] || { sensor_id: i.sensor_id }, units);
    jobs.push({ now: i.food_question.at_risk, mark: "!",
      what: `Check the ${esc(i.food_question.word)} in <strong>${esc(name)}</strong>.`,
      todo: foodWhy(i), acts: foodButtons(i, viewerOnly()) });
  });

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
        ${job.acts || ""}
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
let meAsked = false;
let didAsked = false;
async function tick() {
  clearTimeout(pollTimer);
  if (!meAsked) { meAsked = true; await loadMe(); }
  await load();
  if (!didAsked && lastData) { didAsked = true; loadDid(); }
  pollTimer = setTimeout(tick, CLXWords.pollDelay((lastData || {}).sensors, POLL_MS));
}
tick();

/* What this page last loaded, for Pilot's answers that need no AI key
   (3.27.0): "Which fridges need me?" and the like are read from it in the
   browser, with nothing fetched and nothing sent. */
window.KryozynToday = Object.freeze({ overview: () => lastData,
  // What Kryozyn did for you, as last loaded (3.33.0): Pilot quotes its
  // summary line when asked, with nothing fetched.
  did: () => lastDid });
