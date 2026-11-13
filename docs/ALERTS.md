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

### Rotations and quiet hours

**A rotation** takes turns: its members are on call one at a time, for
shifts of a whole number of days, handing over at a set local time. Set
one for the estate, or one per site:

```bash
curl -X PUT localhost:8080/api/contacts/rotations -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{
  "timezone": "America/New_York", "handover": "08:00", "shift_days": 7,
  "starts_on": "2026-11-02", "members": ["CON-000004", "CON-000005"]
}'
```

Without `starts_on`, the first turn starts at the most recent handover,
so the person listed first is on call from the moment it is saved. (It
used to start at today's handover, and a rotation saved before that time
of day had the person listed last on call until then.)

People in a rotation are alerted only on their shift; everybody else on
the roster is alerted as before. A site whose person on shift is off falls
back to the estate-wide contacts, then to the sign-up contact.
`GET /api/contacts/rotations` shows who is on now and the next handovers
in local time; `DELETE /api/contacts/rotations/{estate|site id}` ends one.
Handovers follow the local wall clock: "08:00" is 08:00 in summer and in
winter, a handover at midnight is at midnight, on the night the clocks go
back a handover in the repeated hour happens the first time only, and on
the night they go forward a handover at a time that does not exist (02:30)
happens when the clock jumps past it. A person takes turns in one rotation
at a time; taking somebody off the roster takes them out of its turns, and
removing a site removes its rotation (its people were released to the
whole estate, and go back to being on call all the time).

In the console it is the **On call now** card, on the People tab under
"Who gets called": who is on call now and until when, who is next, the
next six turns in the rotation's own time, who is in no rotation (and so
always on call), each person's quiet hours, and the warnings below.
Owners and operators build a rotation there (where it covers, the time
zone, the handover time, days per turn, the first day, and who takes
turns in which order), change or stop one, and set or clear quiet hours.
A viewer sees the same card with no form and no button on it, and the
server refuses them anyway.

**Quiet hours** are a contact's own, in their own time zone:

```bash
curl -X PATCH localhost:8080/api/contacts/CON-000004 -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"quiet_hours": {"start": "22:00", "end": "07:00", "timezone": "Europe/London"}}'
```

During them that person is not texted while somebody else on the alert
would be, and is called last rather than not at all. **Quiet hours never
silence a breach:** when everyone the text would reach is in quiet hours,
all of them are texted, and the call ladder still works its way to the
people asleep if nobody awake answers.

Warned about in advance, by `warnings` on every roster change and in the
preview: a rotation with nobody in it, a muted person with turns (their
shifts have nobody from the rotation), a person with turns who does not
get texts, and the times everyone who would be texted is in quiet hours.

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

### What the text, the email and the call say

`wording.py` puts every alert together from the reading itself, in the
order somebody half awake reads it: which unit, where, the reading, how
far past its limit, what to check, how to answer.

    ALERT Kitchen walk-in (WALKIN-1) at Main Street: 58F, 26F above the
    32F limit. Check the door and food now. Reply 1 if you are on it.

The text always fits **one SMS segment** (160 GSM-7 characters, no
degree sign); `tests/test_alert_wording.py` holds every trade to that, in
both scales. The "what to check" line is the trade's own (a pharmacy is
told to check the fridge and stock, a data hall the cooling). With a
model key set, the model words that one line and nothing else -- never
the reading, the limit or how to answer -- and a line longer than one
short sentence is not used. If the text runs long, that line goes first,
then the names are shortened; the reading, the limit and "Reply 1" stay.

The same alert goes once by email to the business's own contact address,
with the facts in the subject line so it reads in a notification banner.

The call is short, because Twilio reads it twice: who is calling, which
unit and site, the reading and how far past its limit, how long it has
been out of range, what to check, and "press 1". It only says "press 1"
when pressing it reaches something (see below); otherwise it says to
reply 1 to the text or acknowledge in the console.

### Reply 1 to acknowledge

A reply of `1`, `ACK`, `OK` or `yes` to the alert text acknowledges the
alert and stops the calls. Set `https://<your host>/api/voice/sms-reply`
as the "A message comes in" webhook on the Twilio number that sends the
alerts. A reply counts only when:

* Twilio signed it (the same check as the keypress below; with no
  `TWILIO_AUTH_TOKEN` nothing is trusted);
* it came from a number the alert went to -- active on that business's
  roster for that unit's site, or the business's own number when it has
  no roster;
* and the alert is that business's and still open.

A number on nobody's roster gets no reply at all. With two open alerts
the reply asks which, and `ACK 12` picks alert 12. The acknowledgement is
recorded as "*name*, by text reply (*number*)" in the incident and the
audit trail, and posted to the team's channels.

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

## The site-wide alert

When many units warm up together, CyberLogix raises one urgent alert on
top of each unit's own: a **possible attack or site-wide failure**.

On 11 October 2026 attackers took over the refrigeration controllers at
hundreds of supermarkets in France and switched them to defrost
overnight. A monitor that asks the fridge's own controller hears whatever
the controller is told to say. CyberLogix reads its own, separate sensors,
so it sees the warming however the controls were changed; and it only
reads, so nobody can use it to change a fridge. It cannot stop an attack.
What it does is find out in minutes and call people.

The text, in one segment:

    Possible site-wide problem at Main Street: 5 of 7 units warming at
    once. Power cut, open door or tampered controls? Check now. Reply 1
    if you are on it.

The call says the same: "At Main Street, 5 of 7 units started warming at
the same time. This can mean a power cut, a door left open, or someone
tampering with the fridge controls. Please check the site now." The email
to the business's address lists every unit with its reading.

### When it is raised

The sweep checks every minute (`sitewide.py`). A unit counts as
**warming** when it crossed its upper limit, or rose by 5°F (about 3°C)
or more in the last hour without crossing it yet, and that started in
the last hour. Then:

| Rule | Default | Setting |
|---|---|---|
| Units at one site that started warming within the window | 3 | `CYBERLOGIX_GROUP_ALERT_MIN_UNITS` (`0` turns the alert off) |
| ...or this share of the site's units, never fewer than 2 | 50% | `CYBERLOGIX_GROUP_ALERT_MIN_SHARE_PERCENT` |
| ...at night | 2 units | `CYBERLOGIX_GROUP_ALERT_MIN_UNITS_NIGHT` |
| How close together they started | 20 minutes | `CYBERLOGIX_GROUP_ALERT_WINDOW_MINUTES` |
| A rise that counts before the limit is crossed | 5°F | `CYBERLOGIX_GROUP_ALERT_RISE_F` |
| Across sites: sites of one business with warming units | 2 | `CYBERLOGIX_GROUP_ALERT_MIN_SITES` |
| ...and units in all, by day | 4 | `CYBERLOGIX_GROUP_ALERT_CROSS_SITE_UNITS` |
| ...and units in all, at night | 3 | `CYBERLOGIX_GROUP_ALERT_CROSS_SITE_UNITS_NIGHT` |
| Night, on the site's clock | 22:00-06:00 | `CYBERLOGIX_GROUP_ALERT_NIGHT` |
| The clock when the account names none | UTC | `CYBERLOGIX_GROUP_ALERT_TIMEZONE` |

Why these. Three units at once is rare by chance and common in a power
cut; two can be a delivery with the doors propped. Half of a small site
(two of four) is as telling as three of a large one, but one unit is
never enough: that is one faulty unit, and it has its own alert. Twenty
minutes is wide enough for a freezer and a fridge, which warm at
different speeds, to land together, and narrow enough that two unrelated
faults an hour apart are not joined up. At night a site is shut and
nobody opens doors, so two units warming together is enough. Across
sites, two warming units at each of two sites is a pattern no single
site would show; one at each of three is too, at night.

The account does not know a business's opening hours, so "night" is a
fixed window on the site's clock. A site has no time zone field; its
clock is its on-call rotation's time zone, else the estate's rotation,
else the time zone of a contact's quiet hours, else
`CYBERLOGIX_GROUP_ALERT_TIMEZONE`.

Change a default by setting the variable (see `.env.example`) and
restarting. The help page quotes the current figures.

### What does not count

* **A planned defrost.** A unit in its own learned defrost (on time, no
  warmer than usual; see the ladder above), or one whose learned defrost
  is due now and is warming toward it, is not counted. Many stores time
  a row of cases to defrost together; that is not a site-wide problem.
  A defrost switched on hours early, which is what the attackers did,
  does not fit the rhythm and counts.
* **An outage.** A sensor that has gone silent reports no warming, so it
  never counts. When half or more of a site's sensors have gone silent
  together, that is the gateway, its power or the internet, not warming:
  the site gets no site-wide alert, and the quiet-sensor text (below)
  tells the roster as it always has.
* **An old fault.** A unit warm since before the last hour is a fault
  somebody already knows about.
* **The same event twice.** Warming that started before the last
  site-wide alert for that site is not counted again, and while one is
  unresolved no second one opens for the same site.
* **Another business.** Everything is read through the one account.

There is no maintenance window or snooze in the product today. When there
is, it belongs in `sitewide._excused`, beside the defrost.

### What it does

* **Straight away, to everyone.** It goes to the whole on-call list for
  the sites involved and the estate-wide people, whoever's turn it is in
  a rotation, and to the business's own number from sign-up (the owner),
  and by email to the business's address. Muted people and people who
  opted out of texts or calls are left out, as everywhere.
* **No grace.** On a plan with calls, the same sweep phones every person
  on that list, one after another, rather than waiting ten minutes and
  stopping at the first answer. It stops early only if somebody
  acknowledges.
* **Quiet hours do not silence it.** Everyone is texted, asleep or not;
  people in quiet hours are rung after those awake, never left out.
* **The same send path.** Texts and calls go through `send_sms` and
  `place_voice_call`, so the daily spend caps, the one-segment rule, the
  premium-number refusals, dry run, the 1/5/15-minute text re-sends, the
  call-ladder retries and the owner's `TEXTS FAILED` and
  `NOBODY HAS BEEN TEXTED` emails all apply as for a unit.
* **Claimed once.** The alert is opened under the store's lock, and only
  the sweep that opened it sends anything, so two sweeps at once (the
  timer and "run sweep now") send it once. The call is claimed like any
  other. The fleet sweep lock is unchanged.
* **Acknowledging.** A reply of `1` (from anybody it reached, the owner
  included), the keypress, or the Acknowledge button stops it, and also
  acknowledges the open alerts of the units in it: whoever has the
  site-wide alert is on the way to all of them. With a site-wide alert
  and its units' alerts open, a plain `1` answers the site-wide one
  rather than asking which.
* **Shown apart.** The console shows a red banner above everything with
  the units in it and its own buttons, and a "Site-wide" card at the top
  of the incident list. The inspector's log lists it in its own section,
  and the monthly summary counts it separately. It is not an excursion of
  its own, so it is never in the compliance figures, the claim packet,
  the ROI figure or the benchmarks: each unit's own incident is.

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

The loop runs on a fixed cadence, not "sleep a minute, then sweep": the
next pass is due an interval after the last one started, so a slow pass
does not push every escalation later, and the first pass after a start or
deploy runs within five seconds rather than a whole interval later. One
fleet sweep runs at a time: a second one started while the first is still
going (an outside scheduler firing on top of the loop, or firing again
while a pass waits out a telephony outage) stands aside and says so, and
does not write the heartbeat. Every text, call and quiet-sensor text is
also claimed before it is sent, so even two passes over one business (the
timer and the console's "run sweep now") send each once.

`POST /api/autopilot/sweep` runs one pass for the calling business on demand. One pass flags
sensors that have gone silent for over 30 minutes (a sensor that cannot
report cannot warn you) and places voice calls for incidents past the grace
window. Pass `auto_escalate=false` to report without calling.

What a customer should check when a sensor shows as offline, in plain
words, is [SENSOR-OFFLINE.md](SENSOR-OFFLINE.md). The same list is on the
help page (`/help#sensor-offline`) and in the console's sensor drawer,
which reads it from `GET /api/help/sensor-offline`; all three come from
`helpdesk.offline_steps()`, and a test keeps the doc in step.

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

A call that failed is placed again. When every call on the ladder failed
at the provider (Twilio down, refusing or not answering), the sweep walks
the ladder again 5 and 15 minutes after the first call, claimed and stored
before dialling like the first. A call that got through is never repeated
by the sweep; an operator can still re-dial by hand.

The alert email is retried on the sweep's timetable, not the hourly mail
queue's: 1, 5, 15, 30 minutes and on to 8 hours after it was first tried,
while the alert is open, so it goes out within minutes of the mail host
coming back. One answered in the meantime is not sent late. When texts
fail, the owner's copy says so in its subject (`TEXTS FAILED - ALERT:
...`); when the last re-send has failed too, the owner gets one email,
`NOBODY HAS BEEN TEXTED: ...`, retried the same way. Each provider
failure is also recorded as a fault (`/book` -> Health, the daily digest),
counted, so the operator sees Twilio or the mail host failing without
reading logs. The drill that checks all of this is in
[OPERATIONS.md](OPERATIONS.md#outage-drill-twilio-and-mail).

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
