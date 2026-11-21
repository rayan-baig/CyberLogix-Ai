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

  /* A time of day as the person's own clock shows it: "3:53 PM". */
  function clock(iso) {
    const d = at(iso);
    if (!d) return "";
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
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
      case "watching":
        return { state: c.state, word: "Watching", kind: "watch",
          line: `${word} since ${clock(c.out_since)}. We'll text at ${clock(c.alert_at)} if it ${stays}.`,
          short: `Watching — we'll text at ${clock(c.alert_at)} if it ${stays}` };
      case "paused":
        return { state: c.state, word, kind: "warm",
          line: `${word} since ${clock(c.out_since)}. Alerts are paused, so nobody is texted.` };
      case "alert":
        return { state: c.state, word, kind: "warm",
          line: `${word} for ${outFor(c.out_since)}, since ${clock(c.out_since)}.` };
      case "offline":
        return { state: c.state, word: "No signal", kind: "quiet",
          line: "We have not heard from its sensor for a while." };
      default:
        return { state: "ok", word: "Fine", kind: "ok", line: "" };
    }
  }

  const api = { minutes, between, outFor, clock, person, condition, roundHalfEven };
  if (typeof module === "object" && module.exports) module.exports = api;
  root.CLXWords = Object.freeze(api);
})(typeof window !== "undefined" ? window : globalThis);
