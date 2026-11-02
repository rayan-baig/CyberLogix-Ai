# Alerts and escalation

How a breach turns into a text, a call and a message in the team's own
channels; how the unattended sweep keeps that working; what each message
costs, and how the costs are kept down.

## Who gets alerted

Alerts go to an on-call roster, not one number. The text goes to **everyone**
on it; the call walks the ladder in escalation order and stops at the first
person actually reached, so a wrong number or a dead line doesn't end the
escalation.

```bash
curl -X POST localhost:8080/api/contacts -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
  "full_name": "Night Engineer", "phone": "+15550001", "escalation_order": 1
}'
```

Each entry can opt out of either channel and be muted while someone is on
leave. A change that leaves nobody on the roster to text or call (for a
channel, or for a site that has sensors) is answered with `warnings`, and
the account's contact email is told once, so a gap is found before an
alert needs it. `GET /api/contacts/preview` shows exactly who would be alerted right
now without sending anything, with the same warnings — worth checking after editing a rota, since an
unverified rota is how an alert reaches an empty desk.

A tenant with no roster is still alerted: the store falls back to the contact
captured at onboarding, so alerting never depends on setup that hasn't
happened yet.

### A test alert

`POST /api/contacts/test-alert` (the **Send a test alert** button under "Who
gets called") texts everyone on the roster — each number once, across every
site — a message that starts `TEST from CyberLogix: nothing is wrong`. With
`{"call": true}`, on a plan with calls, it also rings the first person on the
ladder with a spoken test. Owners and operators only. It opens no incident,
posts to no Slack/Teams/PagerDuty channel and writes nothing to the
compliance record; it goes through the same send functions as a real alert,
so it counts against the day's spend caps and is refused the same premium
numbers. One every ten minutes and five a day, counted from the audit trail
so a restart does not reset it. A sent test is what ticks the last box of
the console's first-day checklist.

## The escalation ladder

A freezer's own defrost is not a breach anyone is paged for. When a unit
has a learned rhythm (at least three earlier warm spells, each back down,
on a regular 4 to 18 hour timer) and a new spell starts on time, no hotter
than its cycles get, the reading is recorded as over the line and answered
`DEFROST_CYCLE` instead of opening an incident. If it is still warm past
twice its usual length, or runs hotter, the next reading pages as normal.
With no learned rhythm every breach pages.

1. A breach opens an incident and sends the SMS.
2. A sustained breach **updates that incident** rather than opening another,
   so a failing freezer produces one alert instead of a pager storm. Follow-up
   pulses return `CRITICAL_CATASTROPHE_ONGOING`.
3. After `VOICE_ESCALATION_GRACE_MINUTES` (10) with no acknowledgement, the
   incident becomes eligible for a voice call. `GET /api/voice/pending` lists
   them; `POST /api/voice/escalate/{id}` places one. Escalating early returns
   `425` unless `force=true`.
4. `POST /api/voice/acknowledge/{id}` halts the ladder;
   `POST /api/voice/resolve/{id}` closes the incident.

### Press 1 to acknowledge

The call script closes by telling the callee to press 1, and that reaches
something: the call wraps the script in a TwiML `<Gather>` pointed at a
callback that acknowledges the incident and stops the ladder.

The callback cannot carry a bearer token, because Twilio is the caller. It
is gated on a valid Twilio signature **and** a per-incident secret in the
URL — incident IDs run in sequence, so the ID alone would let a stranger
silence somebody else's alarm. Set `PUBLIC_BASE_URL` to switch it on;
without it the call behaves as it always did rather than advertising a
keypress that goes nowhere.

## The watchdog

Escalation is the product's whole promise, and it only holds if something
runs the sweep when nobody is watching. A plain asyncio loop inside the API
process sweeps every tenant on `CYBERLOGIX_SWEEP_SECONDS` (default 60) — one
loop, no broker, no second service to pay for. A failure on one estate
cannot stop the others.

Set `CYBERLOGIX_SWEEP_SECONDS=0` when an external scheduler drives
`POST /api/autopilot/sweep-all` (with the platform admin key, every few minutes; set `CYBERLOGIX_EXTERNAL_SWEEP_SECONDS` to how often, so the watchdog knows when it has stopped) instead; that route then also starts the hourly money pass (billing,
collections, backups, digests, the mail queue). Either way, run **one
process**: records are held in memory, so a second replica escalates the
same incident again.
`GET /api/health` reports which it is. If the sweep itself stops, the
dead man's switch says so from outside the process: see
[OPERATIONS.md](OPERATIONS.md#the-dead-mans-switch).

`POST /api/autopilot/sweep` runs one pass for the calling business on demand. One pass flags
sensors that have gone silent for over 30 minutes (a sensor that cannot
report cannot warn you) and places voice calls for incidents past the grace
window. Pass `auto_escalate=false` to report without calling.

## Alert channels

An alert that needs somebody to log in to a dashboard is an alert that
waits. Breaches are pushed to wherever the team already is — alongside the
SMS and the call, never instead of them.

```bash
curl -X POST localhost:8080/api/webhooks -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
  "kind": "slack", "target": "https://hooks.slack.com/services/...",
  "label": "#ops-alerts"
}'
```

Four kinds: Slack and Teams incoming webhooks, PagerDuty Events API v2 (a
routing key rather than a URL), and a generic JSON receiver. Each gets the
envelope it actually accepts — a Slack body posted to Teams is a silent 400.
PagerDuty triggers and resolves on the same dedup key, so acknowledging an
incident closes the alert it opened rather than leaving an orphan on the
rotation.

Hooks can be scoped to a site, but unlike the phone roster a site hook
**adds to** the estate-wide ones rather than replacing them: waking the
wrong person matters, while head office silently losing branch alerts is
worse.

The target is a credential, so it is stored whole and never returned whole.
Plain `http` is refused, and so is any target that resolves inside our own
network — a customer-supplied URL this server posts to is a request-forgery
primitive. Self-hosted deployments with a receiver on their own LAN can opt
back in with `CYBERLOGIX_ALLOW_PRIVATE_WEBHOOKS=1`.

Delivery follows the same rule as SMS and voice: it never raises. Each post
has a five-second timeout and the whole fan-out a twelve-second budget; a
receiver that is unreachable, overloaded (5xx) or asks us to slow down
(408, 429) gets one more try after a second, if it still fits in the budget,
and a wrong URL or key (any other 4xx) does not. Copies sent by the
escalation sweep go out from a worker, so a slow channel never delays the
next business's phone call. Three failures in a row flag a hook as failing,
and the breach handler carries on. Targets are masked in every response,
audit entry, log line and data export.

## Message delivery

Alerts are delivered over Twilio: an SMS when an incident opens, and an
outbound call speaking the escalation script (twice, with a pause) when one
goes unacknowledged past the grace window. Set `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN` and `TWILIO_FROM_NUMBER` to go live.

Without those, the platform runs in **dry run**: alerts are still composed,
incidents still open and escalate, and every attempt is recorded on the
incident as `not_configured`. `GET /api/health` reports `message_delivery` as
`twilio` or `dry_run`, and the console shows a banner when delivery is off.

Delivery never raises. A telephony outage is recorded on the incident as an
undelivered attempt rather than killing the breach handler, so an operator can
see that an alert was written but not sent. Both `sms_delivery` and
`voice_delivery` carry `delivered`, `status`, `provider_sid` and a detail
string.

A text that failed is sent again. When every alert text for an open,
unacknowledged incident failed in a way a retry can fix (Twilio down or
refusing, not dry run or the daily cap), the sweep re-sends it 1, 5 and 15
minutes after the incident opened, to the roster as it is now, until one
lands. Each step is claimed and stored before sending, so a restart or two
sweeps at once never send a step twice; a sweep that comes back after
missing steps sends once. After the last step the normal escalation is all
that is left, and the console card says plainly that no text reached anyone.

The spoken script is model-authored, so it is XML-escaped before it reaches
TwiML — an ampersand in a generated sentence would otherwise fail the call.

## What a message costs

Twilio bills per **segment**, and a segment is 160 characters only while
every character is in the GSM-7 alphabet. One character outside it — a
degree sign, a curly quote, an em dash — switches the whole message to
UCS-2 and the segment drops to 70.

Every alert quoted the reading as `71.0°F`. That one degree sign took a
158-character message from one segment to three, on the line that is the
large majority of delivery cost, and the spend report counted messages so
it was invisible from inside the product.

Outbound SMS is therefore folded into GSM-7 and trimmed to a single
segment in `send_sms` — the one choke point every message passes through,
including the model-written ones whose length and punctuation nothing
upstream controls. Usage records `sms_segments` alongside `sms_sent`, and
the cost estimate bills on segments, because that is what the carrier
invoices.

## AI generation is fail-open

Every Gemini call routes through `safe_generate`, which never raises. If the
client is uninitialized, the API errors, or the model returns an empty body,
the caller gets a deterministic template instead and the response reports
`dispatch_source: "fallback_template"`. A credentials problem degrades the
wording of an alert; it never suppresses one.

Meeting intelligence is the deliberate exception. An alert with clumsier
wording is still a true alert, but an invented meeting summary is a
falsehood, so that endpoint reports degradation instead of substituting
content.

## Cutting the running costs

Three levers, in order of how much they save:

1. **Generated copy is cached.** A walk-in that fails on Monday and again on
   Friday produces the same prompt, so the second alert is served from cache
   for nothing. Alerts are highly repetitive by nature — same sensor, same
   catastrophe, same severity band — so the hit rate climbs with use. The
   cache is persisted, so a redeploy does not throw away generations you have
   already paid for.
2. **Daily spend is capped per tenant.** A sensor stuck in a breach loop, or a
   leaked key, cannot run up an unbounded bill. Past the cap an alert falls
   back to the deterministic template — still sent, just not model-written —
   and extra messages are suppressed with a recorded reason. The incident
   still opens and still needs answering; only the spend stops.
3. **Everything is metered.** `GET /api/costs` reports usage, estimated spend,
   what the cache saved and what the caps prevented, before any of it reaches
   an invoice. The console shows the same figures.

Two smaller ones: a sustained breach updates its open incident instead of
opening a new one, so one failure costs one generation and one SMS however
long it lasts; and the console polls every 20 seconds rather than continuously,
because each poll keeps a Cloud Run instance warm and billing.

Set caps with `CYBERLOGIX_MAX_AI_CALLS_PER_DAY`, `CYBERLOGIX_MAX_SMS_PER_DAY`
and `CYBERLOGIX_MAX_VOICE_CALLS_PER_DAY`; `0` means unlimited. Unit rates for
the estimate are configurable and are list prices for capacity planning, not a
bill.

On the infrastructure side, deploy with `--min-instances 0` so an idle
deployment costs nothing but storage; SQLite on the instance disk avoids a
managed database entirely at this scale.
