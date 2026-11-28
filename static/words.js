"use strict";

/* One way of saying how long, when and who, for every page.

   /today said "4 minutes" while the console said "about 1 minute" about
   the same alert, because each page had its own little formatter and
   started counting from a different moment. Now the server works out the
   one "out since" (and, during the wait, the one "we text at"), and every
   page says it with these. `minutes` is written to give exactly what the
   server's own domain.minutes_words gives, rounding included, so an
   alert's note and its card say the same number; a test checks it. */
(function (root) {
  /* Python's round(): halves go to the even neighbour. */
  function roundHalfEven(x) {
    const r = Math.round(x);
    return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
  }

  /* "under a minute", "1 minute", "6 minutes", "about 2 hours". */
  function minutes(value) {
    const m = Number(value);
    if (!Number.isFinite(m)) return "";
    const whole = roundHalfEven(m);
    if (whole < 1) return "under a minute";
    if (whole < 60) return `${whole} minute${whole === 1 ? "" : "s"}`;
    const hours = roundHalfEven(m / 60);
    return `about ${hours} hour${hours === 1 ? "" : "s"}`;
  }

  function at(iso) {
    const d = iso ? new Date(iso) : null;
    return d && !isNaN(d) ? d : null;
  }

  /* Minutes from one moment to another (or to now). Never negative. */
  function between(fromIso, toIso) {
    const from = at(fromIso);
    if (!from) return null;
    const to = at(toIso) || new Date();
    return Math.max(0, (to.getTime() - from.getTime()) / 60000);
  }

  /* How long it was out: "6 minutes", from "out since" to closed or now. */
  function outFor(fromIso, toIso) {
    const m = between(fromIso, toIso);
    return m === null ? "" : minutes(m);
  }

  /* A time of day as the person's own clock shows it: "3:53 PM", never
     "03:53 PM". Some browsers and locales pad the hour even when asked
     for "numeric", so a leading zero before AM/PM is taken off here,
     once. A 24-hour clock ("09:05") is left as its reader expects. */
  function unpad(text) {
    return String(text).replace(/(^|[^\d:])0(\d:\d\d)(?=\s*[AaPp]\.?\s?[Mm])/g, "$1$2");
  }

  /* The time of day alone: "5:54 PM". */
  function timeOf(d) {
    return unpad(d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
  }

  /* A moment as the reader's own clock says it, with the day whenever it
     is not today (3.37.0): "5:54 PM" today, "Sun 5:54 PM" within the week
     either side, else "Nov 22, 5:54 PM". A bare "5:54 PM" on Tuesday
     about Sunday read as an alert from an hour ago. */
  function clock(iso) {
    const d = at(iso);
    if (!d) return "";
    const today = new Date(now());
    const midnight = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const days = Math.round(Math.abs(midnight(d) - midnight(today)) / 86400000);
    if (days === 0) return timeOf(d);
    if (days < 7) return `${d.toLocaleDateString([], { weekday: "short" })} ${timeOf(d)}`;
    return unpad(d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }));
  }

  /* A day and a time: "Nov 21, 5:50 PM" (and the year with `year`). For
     the logs, the audit trail and the readings table, which each had
     their own "Nov 21, 05:50 PM". */
  function dateTime(iso, opts) {
    const d = at(iso);
    if (!d) return "";
    const o = { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
    if (opts && opts.year) o.year = "numeric";
    return unpad(d.toLocaleString([], o));
  }

  /* "Rosa", from the name the server gives, or from a raw label
     ("Rosa Test <rosa@...>") when that is all an older answer has. */
  function person(name, label) {
    if (name) return String(name);
    const text = String(label || "").split(",")[0].split("(")[0].trim();
    if (!text) return "Somebody";
    const bare = text.includes("<") ? (text.split("<")[0].trim()
      || text.split("<")[1].split(">")[0]) : text;
    if (bare.includes("@") && !bare.includes(" ")) {
      const local = bare.split("@")[0].replace(/[._]/g, " ").trim().split(" ")[0];
      return local ? local.charAt(0).toUpperCase() + local.slice(1) : bare;
    }
    return bare.split(" ")[0];
  }

  /* What a unit is doing right now, in words, from the server's
     `condition` (telemetry.unit_condition). The unit card, the Problems
     list, the "Needs you now" bar and /today all call this, so they say
     the same thing. `kind` is a calm class name for the colour. */
  function condition(unit) {
    const c = (unit && unit.condition) || {};
    const warm = c.too !== "cold";
    const word = warm ? "Too warm" : "Too cold";
    const stays = warm ? "stays warm" : "stays cold";
    switch (c.state) {
      case "waiting_first":
        return { state: c.state, word: "Waiting for its first reading", kind: "quiet",
          line: "Nothing has arrived from its sensor yet. It shows here as soon as it does." };
      case "watching": {
        // Its wait is over and the alert is on its way (the page asks the
        // server again at once): never "we'll text at 5:47 PM" at 5:48.
        const due = at(c.alert_at);
        if (due && due.getTime() <= now()) {
          return { state: c.state, word, kind: "warm", overdue: true,
            line: `${word} since ${clock(c.out_since)}. It stayed out, so we are alerting now.`,
            short: `${word} — alerting now` };
        }
        const temp = c.temperature_display != null ? `${c.temperature_display}°${c.temperature_unit || "F"}` : "";
        const far = c.far_display != null ? `, or at once if it reaches ${c.far_display}°${c.temperature_unit || "F"}` : "";
        if (c.held_after_close) {
          // After "Close anyway": who closed it and when, how warm it is
          // now, and when we alert again. Not "nothing is spoiling".
          return { state: c.state, word, kind: "watch", held: true,
            line: `Closed by hand by ${person(c.closed_by)} at ${clock(c.closed_at)}.${
              temp ? ` Still ${temp}.` : ""} We'll alert again at ${clock(c.alert_at)}${far}.`,
            short: `Closed by hand — still ${temp || word.toLowerCase()}; we alert again at ${clock(c.alert_at)}` };
        }
        return { state: c.state, word: "Watching", kind: "watch",
          line: `${word} since ${clock(c.out_since)}. We'll text at ${clock(c.alert_at)} if it ${stays}${far}.`,
          short: `Watching — we'll text at ${clock(c.alert_at)} if it ${stays}` };
      }
      case "paused":
        return { state: c.state, word, kind: "warm",
          line: `${word} since ${clock(c.out_since)}. Alerts are paused, so nobody is texted.` };
      case "alert":
        // The alert is still open but the latest reading is back in range
        // (3.30.0): "Back in range: press It's fixed", never "Too warm"
        // beside 38°.
        if (c.back_in_range) {
          return { state: c.state, word: "Back in range", kind: "watch", back: true,
            line: "Back in range: press It's fixed." };
        }
        return { state: c.state, word, kind: "warm",
          line: `${word} for ${outFor(c.out_since)}, since ${clock(c.out_since)}.` };
      case "offline":
        return { state: c.state, word: "No signal", kind: "quiet",
          line: "We have not heard from its sensor for a while." };
      default:
        return { state: "ok", word: "Fine", kind: "ok", line: "" };
    }
  }

  /* How long a page should wait before asking the server again, at most
     `base` ms: until just after the soonest "we'll text at" among units
     being watched, so the screen turns into the alert as it opens rather
     than up to twenty seconds later. A wait already over is asked about
     again in a few seconds. */
  function pollDelay(sensors, base) {
    let soonest = base;
    for (const unit of sensors || []) {
      const c = (unit && unit.condition) || {};
      const due = c.state === "watching" ? at(c.alert_at) : null;
      if (!due) continue;
      const ms = due.getTime() - now();
      soonest = Math.min(soonest, ms > 0 ? ms + 1500 : 4000);
    }
    return Math.max(1000, soonest);
  }

  /* "Nobody has answered yet", and what the calls did, from the alert's
     own record (3.28.0). "and we have phoned" only when a call really went
     out (`phoned`): it was said on a server with calls switched off. */
  function nobodyYet(i) {
    const x = i || {};
    if (!x.voice_escalated_at) return "Nobody has answered yet.";
    if (x.phoned) return "Nobody has answered yet, and we have phoned.";
    if (x.calls_off) return "Nobody has answered yet. We would have phoned the list now, but calls are off on this server.";
    return "Nobody has answered yet, and the call did not go through.";
  }

  /* The food question's first line (3.32.0): "Cold again after 2 minutes
     out (since 9:10 PM)", the clock in the reader's own time. "Food at
     risk: check it" as it is. The same on /today, the console and Pilot. */
  function foodBack(q) {
    if (!q) return "";
    const since = !q.at_risk && q.out_since ? clock(q.out_since) : "";
    return since ? `${q.words} (since ${since})` : String(q.words || "");
  }

  /* "Change" shows until this answer's change_until (24 hours, 3.32.0). */
  function changeable(untilIso) {
    const until = at(untilIso);
    return Boolean(until) && until.getTime() >= now();
  }

  /* "1 reading", "6 readings", "1,240 readings checked" (3.33.0): the
     count and the noun that goes with it, on every page, never "1
     readings" or "alert(s)". `many` for a plural that isn't one + "s". */
  function count(n, one, many) {
    const value = Number(n) || 0;
    return `${value.toLocaleString("en-US")} ${value === 1 ? one : (many || `${one}s`)}`;
  }

  let clockNow = () => Date.now();
  function now() { return clockNow(); }
  /* For the tests: read the time from here instead of the computer. */
  function setNow(fn) { clockNow = typeof fn === "function" ? fn : () => Date.now(); }

  const api = { minutes, between, outFor, clock, dateTime, person, condition, pollDelay, setNow, roundHalfEven, nobodyYet,
    foodBack, changeable, count };
  if (typeof module === "object" && module.exports) module.exports = api;
  root.CLXWords = Object.freeze(api);
})(typeof window !== "undefined" ? window : globalThis);
