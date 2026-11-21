// The one formatter every page uses: `node --test tests/js/words.test.js`.
// tests/test_setup_and_one_formatter.py runs this file too, and compares
// CLXWords.minutes with the server's own domain.minutes_words.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const W = require(path.join(__dirname, "..", "..", "static", "words.js"));

test("minutes say what the server says", () => {
  assert.equal(W.minutes(0), "under a minute");
  assert.equal(W.minutes(0.49), "under a minute");
  assert.equal(W.minutes(1), "1 minute");
  assert.equal(W.minutes(6.2), "6 minutes");
  assert.equal(W.minutes(2.5), "2 minutes");     // halves to even, as Python rounds
  assert.equal(W.minutes(3.5), "4 minutes");
  assert.equal(W.minutes(59.4), "59 minutes");
  assert.equal(W.minutes(90), "about 2 hours");
});

test("out for runs from out since to closed, never negative", () => {
  assert.equal(W.outFor("2026-11-21T15:00:00Z", "2026-11-21T15:06:00Z"), "6 minutes");
  assert.equal(W.outFor("2026-11-21T15:06:00Z", "2026-11-21T15:00:00Z"), "under a minute");
  assert.equal(W.outFor(null), "");
});

test("a person's name, not an address", () => {
  assert.equal(W.person("Rosa"), "Rosa");
  assert.equal(W.person(null, "Rosa Test <rosa@cafe.example>"), "Rosa");
  assert.equal(W.person(null, "rosa.test@cafe.example"), "Rosa");
  assert.equal(W.person(null, "Mia Lopez, by text reply (+15550111)"), "Mia");
  assert.equal(W.person(null, ""), "Somebody");
});

test("a unit's condition, in calm words", () => {
  W.setNow(() => Date.parse("2026-11-21T15:40:00Z"));   // inside the wait
  assert.equal(W.condition({ condition: { state: "waiting_first" } }).word, "Waiting for its first reading");
  assert.equal(W.condition({ condition: { state: "ok" } }).word, "Fine");
  const watching = W.condition({ condition: { state: "watching", too: "warm",
    out_since: "2026-11-21T15:33:00Z", alert_at: "2026-11-21T15:53:00Z" } });
  assert.equal(watching.word, "Watching");
  assert.match(watching.line, /^Too warm since .+\. We'll text at .+ if it stays warm\.$/);
  assert.match(watching.short, /^Watching — we'll text at .+ if it stays warm$/);
  assert.equal(W.condition({ condition: { state: "alert", too: "cold",
    out_since: "2026-11-21T15:33:00Z" } }).word, "Too cold");
  // Never the capitals of the old card.
  for (const state of ["waiting_first", "ok", "watching", "alert", "offline", "paused"]) {
    const c = W.condition({ condition: { state, too: "warm", out_since: "2026-11-21T15:33:00Z",
      alert_at: "2026-11-21T15:53:00Z" } });
    assert.notEqual(c.word, c.word.toUpperCase(), state);
    assert.ok(!/BREACHING|OFFLINE|NO FORECAST|tuned/.test(c.word + c.line), state);
  }
  W.setNow(null);
});

test("a table for the server to compare", () => {
  const values = [0, 0.4, 0.5, 0.6, 1.5, 2.5, 6, 6.49, 6.5, 7.5, 20, 59.4, 59.5, 60, 89, 90, 150, 1440];
  console.log("MINUTES " + JSON.stringify(values.map((v) => [v, W.minutes(v)])));
});

// --- round 5: the clock, the hold after a close by hand, and padding ---

test("a time never has a leading zero", () => {
  const pm = W.clock("2026-11-21T17:50:00Z");
  assert.ok(!/^0\d:/.test(pm), pm);
  assert.ok(!/(^|\s)0\d:\d\d/.test(W.dateTime("2026-11-21T17:50:00Z")), W.dateTime("2026-11-21T17:50:00Z"));
  assert.ok(!/(^|\s)0\d:\d\d/.test(W.dateTime("2026-11-21T09:05:00Z", { year: true })));
});

test("once the 'we'll text at' time has passed, it says we are alerting now", () => {
  const unit = { condition: { state: "watching", too: "warm",
    out_since: "2026-11-21T17:26:00Z", alert_at: "2026-11-21T17:47:00Z" } };
  W.setNow(() => Date.parse("2026-11-21T17:46:00Z"));
  assert.match(W.condition(unit).line, /We'll text at/);
  W.setNow(() => Date.parse("2026-11-21T17:47:30Z"));
  const late = W.condition(unit);
  assert.ok(!/We'll text at/.test(late.line), late.line);
  assert.match(late.line, /alerting now/);
  assert.equal(late.word, "Too warm");
  W.setNow(null);
});

test("pages ask again just after the alert time, not 20 seconds later", () => {
  const sensors = [{ condition: { state: "watching", alert_at: "2026-11-21T17:47:00Z" } },
                   { condition: { state: "ok" } }];
  W.setNow(() => Date.parse("2026-11-21T17:46:55Z"));
  const ms = W.pollDelay(sensors, 20000);
  assert.ok(ms > 5000 && ms < 8000, String(ms));
  W.setNow(() => Date.parse("2026-11-21T17:47:10Z"));
  assert.ok(W.pollDelay(sensors, 20000) <= 5000);
  W.setNow(() => Date.parse("2026-11-21T17:40:00Z"));
  assert.equal(W.pollDelay(sensors, 20000), 20000);
  assert.equal(W.pollDelay([], 20000), 20000);
  W.setNow(null);
});

test("held after a close by hand: who, when, how warm, and when we alert again", () => {
  W.setNow(() => Date.parse("2026-11-21T17:50:00Z"));
  const c = W.condition({ condition: { state: "watching", too: "warm", held_after_close: true,
    closed_by: "Maya", closed_at: "2026-11-21T17:48:00Z", out_since: "2026-11-21T17:20:00Z",
    alert_at: "2026-11-21T18:08:00Z", temperature_display: 47.6, temperature_unit: "F",
    far_display: 56 } });
  assert.match(c.line, /^Closed by hand by Maya at .+\. Still 47\.6°F\. We'll alert again at .+\.$/);
  assert.ok(!/spoil|emergency/i.test(c.line));
  assert.equal(c.held, true);
  W.setNow(null);
});
