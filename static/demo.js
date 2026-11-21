"use strict";

/* See it live: a pretend kitchen, run entirely in this browser.

   Nothing here talks to a sensor, a phone or a database. The server's
   only part is the story (/api/demo/story): the four units, the readings
   the walk-in will drift through, and the exact words a real alert would
   use, worded by the same code that words real ones. Everything else --
   the clock, the readings, the texts, the calls, "reply 1" -- happens on
   this page and is gone when it is closed.

   A copy of this page with the story written into it (a JSON block with
   the id "demo-story") needs no server at all; that is how the public
   copy on GitHub Pages runs. */

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
            '"': "&quot;", "'": "&#39;" }[c]));

let S = null;          // the story
let run = null;        // this run's state
let timer = null;

function fresh() {
  return {
    t: 0,                 // pretend seconds since the start
    readings: {},         // unit id -> list of readings
    alertOpen: false,
    acked: false,
    fixed: false,
    fixedAt: null,
    calls: [],            // {who, at, state}
    events: new Set(),    // which timeline lines have been written
    step: "fine",
  };
}

/* ---------------- the readings ---------------- */

function steadyReading(unit, t) {
  // A gentle wobble around where it sits. Same t, same reading.
  const seed = unit.id.length * 1.7;
  return Math.round((unit.start_f + Math.sin(t / 7 + seed) * 0.4) * 10) / 10;
}

function readingAt(unit, t) {
  if (unit.id !== S.drifting || t < S.drift_starts_seconds) {
    return steadyReading(unit, t);
  }
  if (run.fixed) {
    const i = Math.floor((t - run.fixedAt) / S.tick_seconds);
    if (i >= S.recover_f.length) return steadyReading(unit, t);
    return S.recover_f[i];
  }
  const i = Math.floor((t - S.drift_starts_seconds) / S.tick_seconds);
  return S.drift_f[Math.min(i, S.drift_f.length - 1)];
}

function alertAt() {
  return S.drift_starts_seconds + S.alert_index * S.tick_seconds;
}

function overAt() {
  return S.drift_starts_seconds + (S.over_index ?? S.alert_index) * S.tick_seconds;
}

/* ---------------- what happens when ---------------- */

function timeline(key, cls, text) {
  if (run.events.has(key)) return;
  run.events.add(key);
  $("alert-empty").hidden = true;
  const li = document.createElement("li");
  li.className = cls;
  li.innerHTML = `${esc(text)} <span class="when">${esc(clockWords(run.t))}</span>`;
  $("timeline").appendChild(li);
}

function bubble(from, text) {
  $("phone-empty").hidden = true;
  const div = document.createElement("div");
  div.className = `msg ${from === "me" ? "msg-me" : "msg-them"}`;
  div.innerHTML = `<span class="msg-from">${from === "me" ? "You" : "CyberLogix"}</span>${esc(text)}`;
  $("thread").appendChild(div);
  $("thread").scrollTop = $("thread").scrollHeight;
}

function setStep(step) {
  run.step = step;
  const order = ["fine", "warming", "alert", "call", "sorted"];
  const at = order.indexOf(step);
  document.querySelectorAll("#steps li").forEach((li) => {
    const i = order.indexOf(li.getAttribute("data-step"));
    li.className = i < at ? "done" : i === at ? "now" : "";
  });
}

function people() {
  return S.people.map((p) => p.name).join(" and ");
}

function advance() {
  const t = run.t;
  const walkIn = S.units.find((u) => u.id === S.drifting);
  const now = readingAt(walkIn, t);

  if (!run.alertOpen && t >= S.drift_starts_seconds && !run.fixed) {
    setStep("warming");
  }

  // Over the limit: watched, not yet alerted. A door left open for a
  // minute goes over and comes back; a fridge that stays over is a fault.
  if (!run.alertOpen && !run.fixed && now > walkIn.limit_f && t >= overAt()) {
    timeline("over", "", `${walkIn.name} is over its ${fmt(walkIn.limit_f)}°F limit `
      + `(${fmt(now)}°F). We wait to see if it stays over: ${S.real_stay_minutes} `
      + `minutes in real life, ${S.stay_ticks * S.tick_seconds} seconds here, so a `
      + `door open for a minute wakes nobody.`);
  }

  // The alert: it has stayed over its limit for the wait.
  if (!run.alertOpen && !run.fixed && now > walkIn.limit_f && t >= alertAt()) {
    run.alertOpen = true;
    setStep("alert");
    timeline("breach", "hit", `${walkIn.name} has stayed too warm: ${fmt(now)}°F, `
      + `over its ${fmt(walkIn.limit_f)}°F limit.`);
    timeline("texted", "hit", `Texted ${people()}.`);
    bubble("them", S.messages.alert_text);
    $("reply-1").disabled = false;
    $("reply-help").textContent = "Reply 1 to say you are on it. That stops the calls.";
  }

  // Nobody answered: the calls, one person at a time.
  if (run.alertOpen && !run.acked) {
    const first = alertAt() + S.demo_wait_seconds;
    S.people.forEach((person, n) => {
      const start = first + n * S.ring_seconds;
      const end = start + S.ring_seconds;
      if (t >= start && t < end) {
        setStep("call");
        timeline(`call-${n}`, "call", n === 0
          ? `Nobody replied in ${S.real_wait_minutes} minutes `
            + `(${S.demo_wait_seconds} seconds here), so it calls ${person.name}.`
          : `${S.people[n - 1].name} did not answer, so it calls ${person.name}.`);
        if (n === 0) showCall(true);
      }
      if (t >= end && n === 0) showCall(false, true);
    });
    const last = first + S.people.length * S.ring_seconds;
    if (t >= last) {
      timeline("nobody", "", `Nobody has answered yet. Reply 1 to the text, `
        + `or press 1 on a call, to say you are on it.`);
    }
  }
}

function showCall(on, missed) {
  const box = $("call");
  if (on) {
    if (!box.hidden) return;
    $("call-said").textContent = `“${S.messages.call_script}”`;
    box.hidden = false;
    return;
  }
  if (box.hidden) return;
  box.hidden = true;
  if (missed && !run.events.has("missed")) {
    run.events.add("missed");
    bubble("them", "Missed call from CyberLogix.");
  }
}

function acknowledge(how) {
  if (run.acked || !run.alertOpen) return;
  run.acked = true;
  $("reply-1").disabled = true;
  showCall(false);
  const who = S.people[0].name;
  if (how === "text") {
    bubble("me", "1");
    bubble("them", S.messages.ack_reply);
    timeline("ack", "ack", `${who} replied 1. Nobody else is called.`);
  } else {
    timeline("ack", "ack", `${who} pressed 1 on the call. Nobody else is called.`);
  }
  $("reply-help").textContent = "Done. In real life this is also written into "
    + "the record your inspector and insurer can see.";
  setStep("sorted");
  $("fix").hidden = false;
  $("end").classList.add("is-ready");
}

function fix() {
  if (run.fixed) return;
  run.fixed = true;
  run.fixedAt = run.t;
  $("fix").hidden = true;
  timeline("fixed", "done", "Fixed: the door was left open. It is cooling back down.");
  $("alert-note").textContent = "sorted";
  // Straight away, not on the next tick: the banner kept offering "Mark it
  // fixed" for several seconds after it was pressed, while the walk-in
  // was still above its limit on the way back down.
  draw();
}

/* ---------------- drawing ---------------- */

function fmt(f) { return (Math.round(f * 10) / 10).toFixed(1).replace(/\.0$/, ""); }

function clockWords(t) {
  const m = Math.floor(t / 60);
  const s = String(t % 60).padStart(2, "0");
  return `${m}:${s}`;
}

function spark(list, limit) {
  const pts = list.slice(-14);
  if (pts.length < 2) return "";
  const lo = Math.min(...pts, limit) - 1, hi = Math.max(...pts, limit) + 1;
  const x = (i) => (i / (pts.length - 1)) * 100;
  const y = (v) => 30 - ((v - lo) / (hi - lo)) * 30;
  const line = pts.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return `<svg class="demo-spark" viewBox="0 0 100 30" preserveAspectRatio="none"
      aria-hidden="true"><line x1="0" x2="100" y1="${y(limit).toFixed(1)}"
      y2="${y(limit).toFixed(1)}" class="spark-limit"/><polyline points="${line}"
      class="spark-line"/></svg>`;
}

function unitState(unit, now, list) {
  if (now > unit.limit_f) return { cls: "w-warm", word: "Too warm" };
  const rising = list.length > 3 && list[list.length - 1] - list[list.length - 4] > 1;
  if (rising && unit.id === S.drifting && !run.fixed) {
    return { cls: "w-soon", word: "Warming up" };
  }
  return { cls: "w-ok", word: "Fine" };
}

function draw() {
  let warm = null, soon = null;
  $("units").innerHTML = S.units.map((unit) => {
    const list = run.readings[unit.id] || [];
    const now = list[list.length - 1];
    const st = unitState(unit, now, list);
    if (st.cls === "w-warm") warm = unit;
    if (st.cls === "w-soon") soon = unit;
    return `<article class="demo-unit ${st.cls}">
      <div class="demo-unit-top"><span class="demo-unit-name">${esc(unit.name)}</span>
        <span class="demo-word ${st.cls}">${esc(st.word)}</span></div>
      <div class="demo-unit-temp">${esc(fmt(now))}<span>°F</span></div>
      ${spark(list, unit.limit_f)}
      <div class="demo-unit-limit">Alert above ${esc(fmt(unit.limit_f))}°F</div>
    </article>`;
  }).join("");

  const box = $("verdict");
  if (warm && run.fixed) {
    box.className = "demo-verdict watch";
    box.innerHTML = `<h2>Fixed. ${esc(warm.name)} is cooling back down</h2>
      <p>Marked fixed and written down. It will show as fine once it is back
         under ${esc(fmt(warm.limit_f))}°F.</p>`;
  } else if (warm && !run.alertOpen) {
    box.className = "demo-verdict watch";
    box.innerHTML = `<h2>${esc(warm.name)} is over its limit</h2>
      <p>Nobody is texted yet: we wait to see if it stays over, so a door
         open for a minute wakes nobody.</p>`;
  } else if (warm && !run.acked) {
    box.className = "demo-verdict bad";
    box.innerHTML = `<h2>${esc(warm.name)} is too warm</h2>
      <p>${esc(people())} have been texted. Reply 1 on the phone below to say you are on it.</p>
      <button class="btn btn-sm demo-jump" type="button" data-act="phone">See the text</button>`;
  } else if (warm) {
    box.className = "demo-verdict watch";
    box.innerHTML = `<h2>${esc(S.people[0].name)} is on it</h2>
      <p>The calls have stopped. Once the door is shut and the food checked, mark it fixed.</p>
      <button class="btn btn-sm demo-jump" type="button" data-act="fix">Mark it fixed</button>`;
  } else if (soon) {
    box.className = "demo-verdict watch";
    box.innerHTML = `<h2>${esc(soon.name)} is warming up</h2>
      <p>Still safe, but rising. If it goes over ${esc(fmt(soon.limit_f))}°F and stays there, an alert goes out.</p>`;
  } else if (run.fixed) {
    box.className = "demo-verdict ok";
    box.innerHTML = `<h2>Everything is fine again</h2>
      <p>The alert is closed and written down. That is the whole idea.</p>`;
  } else {
    box.className = "demo-verdict ok";
    box.innerHTML = `<h2>Everything is fine</h2>
      <p>All 4 are the right temperature. Nothing for you to do.</p>`;
  }
  $("alert-note").textContent = run.fixed ? "sorted"
    : run.alertOpen ? (run.acked ? "acknowledged" : "1 open") : "none";
  const left = S.drift_starts_seconds - run.t;
  $("clock").textContent = left > 0
    ? `The walk-in starts warming in ${left} second${left === 1 ? "" : "s"}.`
    : "";
  $("skip").hidden = left <= 3;
}

function tick() {
  run.t += 1;
  if (run.t % S.tick_seconds === 0) {
    S.units.forEach((unit) => {
      const list = run.readings[unit.id];
      list.push(readingAt(unit, run.t));
      if (list.length > 30) list.shift();
    });
  }
  advance();
  draw();
}

function start() {
  clearInterval(timer);
  run = fresh();
  S.units.forEach((unit) => {
    run.readings[unit.id] = [];
    for (let t = -26; t <= 0; t += S.tick_seconds) {
      run.readings[unit.id].push(steadyReading(unit, t));
    }
  });
  $("timeline").innerHTML = "";
  $("thread").innerHTML = "";
  $("alert-empty").hidden = false;
  $("phone-empty").hidden = false;
  $("call").hidden = true;
  $("fix").hidden = true;
  $("reply-1").disabled = true;
  $("reply-help").textContent =
    "When the alert text arrives, reply 1 to say you are on it.";
  $("end").classList.remove("is-ready");
  setStep("fine");
  draw();
  timer = setInterval(tick, 1000);
}

/* ---------------- the story ---------------- */

async function loadStory() {
  const embedded = document.getElementById("demo-story");
  if (embedded) return JSON.parse(embedded.textContent);
  const got = await fetch("/api/demo/story", { headers: { Accept: "application/json" } });
  const body = await got.json().catch(() => ({}));
  if (!got.ok) {
    throw new Error(typeof body.detail === "string" ? body.detail
      : "The demo could not start just now. Please try again in a minute.");
  }
  return body;
}

$("skip").addEventListener("click", () => {
  if (!run || run.t >= S.drift_starts_seconds - 3) return;
  // Jump to just before the walk-in starts to warm, keeping the readings
  // so the line does not jump.
  while (run.t < S.drift_starts_seconds - 3) {
    run.t += 1;
    if (run.t % S.tick_seconds === 0) {
      S.units.forEach((unit) => {
        const list = run.readings[unit.id];
        list.push(readingAt(unit, run.t));
        if (list.length > 30) list.shift();
      });
    }
  }
  draw();
});
$("reply-1").addEventListener("click", () => acknowledge("text"));
$("press-1").addEventListener("click", () => acknowledge("call"));
$("fix").addEventListener("click", fix);
// The verdict is redrawn every second, so its buttons are handled here.
$("verdict").addEventListener("click", (event) => {
  const act = event.target.closest("[data-act]");
  if (!act) return;
  if (act.getAttribute("data-act") === "fix") fix();
  if (act.getAttribute("data-act") === "phone") {
    $("phone-title").closest(".card").scrollIntoView({ behavior: "smooth", block: "start" });
  }
});
$("again").addEventListener("click", () => {
  start();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

loadStory().then((story) => {
  S = story;
  $("biz").textContent = `${S.business} · ${S.label}`;
  if (S.signup_href) {
    $("top-cta").href = S.signup_href;
    $("end-cta").href = S.signup_href;
  }
  $("time-note").textContent = `Time is sped up. Here a reading comes every `
    + `${S.tick_seconds} seconds, the ${S.real_stay_minutes}-minute wait to see if a `
    + `fridge stays too warm takes ${S.stay_ticks * S.tick_seconds} seconds, and the `
    + `${S.real_wait_minutes}-minute wait before a call takes ${S.demo_wait_seconds} `
    + `seconds. A real sensor sends a reading every few minutes.`;
  start();
}).catch((err) => {
  $("verdict").className = "demo-verdict watch";
  $("verdict").innerHTML = `<h2>The demo could not start</h2><p>${esc(err.message)}</p>`;
  $("skip").hidden = true;
});
