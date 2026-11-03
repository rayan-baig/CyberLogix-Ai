# The vault and compliance records: internals

How the tamper-evident record works, the compliance reports built on it,
how evidence is kept past the rolling window of readings, and the
insurance claim packet. The customer's version, in plain words, is
[THE-VAULT.md](THE-VAULT.md).

## The compliance vault

A temperature log is worth what a sceptical third party thinks it is worth.
An insurer settling a six-figure claim, an FDA auditor, a buyer checking a
cellar's provenance — "our database says so" is not an answer to any of
them. And the attack is not an outsider: it is the operator who finds a bad
night and edits the row before the inspector arrives.

So every reading is chained. Each digest covers the reading **and the
digest before it**, which means altering one historical value changes every
digest after it and the chain stops verifying. The customer cannot quietly
rewrite their own history, and neither can we.

```bash
# A signed statement of the whole estate's record
curl -H "Authorization: Bearer $TOKEN" \
  localhost:8080/api/vault/attestation?days=30

# A recipient who trusts neither party re-derives it themselves. No account.
curl -X POST localhost:8080/api/vault/verify -H 'Content-Type: application/json' \
  -d '{"readings": [...], "chain_head": "e3b4cf…"}'
```

`/api/vault/verify` is deliberately unauthenticated — that is the entire
point — and reads nothing and stores nothing. Both the chain and the
verifier derive digests through one function, because two implementations
of the same hash is how a verifier ends up disagreeing with the thing it
verifies.

Set `CYBERLOGIX_ATTESTATION_KEY` to counter-sign. Without it the chain
still verifies on its own and the attestation says so plainly rather than
implying a signature it does not have.

What this is not: a blockchain, a notary, or a legal guarantee. It is a
hash chain plus an attestation, and overselling it is the one thing that
would make it worthless.

The customer's version, with how to check an attestation by hand:
[docs/THE-VAULT.md](THE-VAULT.md).

## Compliance reports

`GET /api/autopilot/compliance?days=7` assembles the inspector-ready
temperature log: readings logged, excursions, per-sensor min/max/mean,
incident counts and mean response time. Add `narrate=true` for a
Gemini-written executive summary.

`GET /api/autopilot/compliance.csv` returns the same figures as a spreadsheet
— one row per sensor plus a totals row — so it can go straight into an audit
pack without anyone re-typing numbers.

### The log for an inspector

`GET /api/autopilot/inspector-log?start=YYYY-MM-DD&end=YYYY-MM-DD` (up to
92 days; a week by default) is what an inspector standing in the kitchen
asks for: each unit's lowest and highest reading per day (and per hour,
in the data), every time it went out of range with its worst reading, who
acknowledged it and how soon, what was done and who reviewed it. Only the
calling business's units and alerts, read through its own credential.
Readings come from the rolling window plus the copies kept on each alert,
so an old excursion still shows after the window has moved on, and the
log says so when the window does not cover the dates asked for.

In the console, *Log for an inspector* (under *Proof*) takes the dates
and opens the log as a document with the print dialog over it: one click
to print or save as PDF. On paper only the document prints, black on
white, with each table's headings repeated on every page and an
out-of-range day in bold.

## Evidence outlives the rolling window

Readings are a ring buffer: the oldest are deleted once a sensor passes
`CYBERLOGIX_READINGS_PER_SENSOR`, which at a five-minute pulse is under
two days. That is a reasonable thing for a bounded store to do. Two
things built on top of it were not.

**The insurance claim packet read readings live.** So a customer whose
freezer failed, and who filed the claim a week later the way people
actually do, got a packet containing five hundred healthy readings with
the breach deleted — and `excursions_in_window: 0` on the one document
whose purpose is to prove an excursion happened. Not an empty packet,
which somebody would have questioned: a complete-looking one, arguing
against the claim it was attached to.

The readings around an incident are now copied onto the incident when it
opens and again when it resolves, so the lead-up and the recovery both
survive. Incidents are kept for years; readings are not. The fix is
deliberately bounded to the window the document shows — kilobytes on an
object that already exists, rather than turning the ring buffer off and
holding every reading for every sensor forever.

**The compliance report presented a truncated period as a whole one.** A
30-day report answered from 41 hours of data is not wrong to exist; it
is wrong to say nothing about it. Every report and export now states the
window it can answer for, and the CSV carries an `INCOMPLETE RECORD` row
on its face so somebody opening it in Excel sees it without going to
ask.

And the privacy statement promised seven years of readings the system
kept for 41 hours. It now says what actually happens.

## Claim packets

The money in a loss event is not lost in the failure. It is lost in the six
weeks afterwards, while somebody hunts for logs and tries to prove the
response was reasonable.

`POST /api/claims/{incident_id}/packet` assembles it: readings either side
of the event, the alert timeline with delivery receipts, who acknowledged
and how fast, prior incidents on the same asset — **disclosed rather than
hidden**, because an adjuster finds them anyway — and a vault attestation
so none of it can have been written after the fact.

A covering letter is drafted for the adjuster, and is forbidden from
estimating the loss, arguing the claim, or characterising the insured's
conduct. One invented figure discredits the entire packet.

The console renders it as a document and prints it: a dedicated print
stylesheet re-sets the whole thing for ink, because the adjuster's office
will print it and the dark console on paper is unreadable.
