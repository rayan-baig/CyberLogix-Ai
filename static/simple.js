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

function stateOf(unit) {
  if (!unit.online) {
    return { word: "No signal", cls: "w-quiet",
             why: `We have not heard from this one since ${
               howLongAgo(unit.last_seen)}. It might be unplugged, out of
               range, or its battery might be flat. Until it starts
               talking again, nobody is watching that food.` };
  }
  if (unit.breaching) {
    const limit = unit.last_temperature_display > unit.danger_above_display
      ? `should stay under ${unit.danger_above_display}°`
      : `should stay above ${unit.danger_below_display}°`;
    return { word: "Too warm", cls: "w-warm",
             why: `It is ${unit.last_temperature_display}° and it ${limit}.` };
  }
  if (unit.hours_until_breach != null && unit.hours_until_breach <= 6) {
    return { word: "Heading the wrong way", cls: "w-soon",
             why: `It is still a safe temperature, but at this rate it stops
               being one ${howLongUntil(unit.hours_until_breach)}.` };
  }
  return { word: "Fine", cls: "w-ok", why: "" };
}

/* The headline. One sentence, chosen by how bad the worst thing is. */
function verdict(units, jobs) {
  const box = $("verdict");
  const urgent = jobs.filter((j) => j.now).length;
  if (urgent) {
    box.className = "verdict bad";
    box.innerHTML =
      `<h1>${urgent} thing${urgent === 1 ? "" : "s"} need${
        urgent === 1 ? "s" : ""} you now</h1>
       <p>Food could be spoiling while this is on the screen. What to do
          is written under each one.</p>`;
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
  box.className = "verdict ok";
  const n = units.length;
  box.innerHTML =
    `<h1>Everything is fine</h1>
     <p>All ${n} of your fridges and freezers are the right temperature and
        all ${n} are reporting in. There is nothing for you to do.</p>`;
}

function render(data) {
  const units = data.sensors || [];
  const jobs = [];

  units.forEach((unit) => {
    const state = stateOf(unit);
    const name = whichOne(unit, units);
    if (state.cls === "w-warm") {
      jobs.push({ now: true, mark: "!",
        what: `<strong>${esc(name)}</strong> is too warm. ${
          esc(state.why)}`,
        todo: "Check the door is shut and the unit is running. If it is "
            + "broken, move the food somewhere cold now and write down "
            + "what you did — that record is what an inspector or an "
            + "insurer asks for." });
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

  verdict(units, jobs);

  $("todo-wrap").hidden = jobs.length === 0;
  // Urgent first: somebody reading on a phone in a kitchen sees two
  // items, not eight, and the two they see are the two that matter.
  jobs.sort((a, b) => Number(b.now) - Number(a.now));
  $("todo").innerHTML = jobs.map((job) => `
    <div class="todo ${job.now ? "now" : ""}">
      <span class="todo-mark" aria-hidden="true">${job.mark}</span>
      <span class="grow">
        <p class="todo-what">${job.what}</p>
        <p class="todo-do">${esc(job.todo)}</p>
        ${job.help ? `<a class="offline-help" href="${esc(job.help)}"
            >What to check, step by step</a>` : ""}
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
        <span class="thing-where">${esc(unit.site_name || "No place set")}
          · heard from ${esc(howLongAgo(unit.last_seen))}</span>
      </span>
      <span class="thing-word ${state.cls}">${esc(state.word)}</span>
    </div>`;
  }).join("") : `<p class="sub">Nothing is set up yet.</p>`;

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
    render(await got.json());
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
  }
}

load();
setInterval(load, 30000);
