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
});

test("a table for the server to compare", () => {
  const values = [0, 0.4, 0.5, 0.6, 1.5, 2.5, 6, 6.49, 6.5, 7.5, 20, 59.4, 59.5, 60, 89, 90, 150, 1440];
  console.log("MINUTES " + JSON.stringify(values.map((v) => [v, W.minutes(v)])));
});
