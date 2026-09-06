# CyberLogix AI — Master Enterprise Hub

Universal IoT telemetry, license control, autonomous operations, voice
escalation and predictive forecasting, mounted behind one FastAPI service.

Wireless thermal sensors report to the hub. Each reading is scored against
the threshold profile for its industry. A breach opens an incident and sends
a Gemini-drafted SMS; if nobody acknowledges it, the hub escalates to a
spoken phone call. In the background the forecaster projects which sensors
will fail next, and the compliance clerk assembles the paperwork inspectors
ask for.

## Modules

| Module | Prefix | Responsibility |
|---|---|---|
| Universal IoT Telemetry | `/api` | Ingest pulses, detect breaches, open incidents |
| Corporate License Management | `/api/licenses` | Tenants, API keys, plans, seat enforcement |
| Autonomous Compliance Clerk | `/api/autopilot` | Compliance reports, unattended sweeps |
| AI Outbound Voice Escalation | `/api/voice` | Escalation ladder, acknowledgement, resolution |
| Predictive Breakdown Forecaster | `/api/forecast` | Trend fitting, time-to-breach projection |
| BYOD Hardware Bridge | `/api/v1/bridge` | Webhook ingest from off-the-shelf sensors |
| Sector Meeting Intelligence | `/api/v1/bridge` | Transcripts into structured action items |
| Operations Console | `/` | Browser UI over the whole platform |
| Operator Accounts & Audit | `/api/accounts` | People, roles, durable audit trail |
| Spend Controls | `/api/costs` | Caching, daily caps, cost reporting |
| On-Call Roster | `/api/contacts` | Who gets woken, and in what order |
| Pricing & Billing | `/api/billing` | Per-unit rate card, invoice, ROI |
| Enterprise Volume Billing | `/api/v1/enterprise-billing` | Per-branch pricing for chains |
| Sector Shortcuts | `/api/shortcuts` | The one document each industry must produce |
| Sites | `/api/sites` | Locations, so an estate is not a flat list of thermometers |
| Outbound Alert Webhooks | `/api/webhooks` | Slack, Teams, PagerDuty, generic JSON |
| Compliance Vault | `/api/vault` | Hash-chained readings and verifiable attestations |
| Insurance Claim Packets | `/api/claims` | Everything an adjuster asks for, assembled |
| Loss Assurance | `/api/assurance` | The guarantee, and what would void it |
| Industry Benchmarks | `/api/benchmarks` | Where an estate sits against its cohort |
| Invoicing | `/api/invoices` | Numbered, dated, frozen demands for money |
| Reseller Channel | `/api/partners` | A servicer's book of managed accounts |

The console is at `/`, the reseller portal at `/partners`, the
machine-readable gateway at `/api`, and interactive API docs at `/docs`.

## The console

Open `/` in a browser and sign in with your email and password (or onboard a
company from the same card, which creates the tenant, its first owner and
signs you in). The console shows the fleet with a 12-point sparkline per
sensor, headline counts, the incident feed with the exact SMS and voice text
that went out and whether each was delivered, and controls to acknowledge,
resolve or escalate. It polls every 10 seconds.

Sensor cards carry **Send safe pulse** and **Simulate failure** buttons, which
post a real reading through the real pipeline — the fastest way to watch a
breach open an incident end to end. A table view of the fleet is one click
away for screen readers and for copying figures out.

To see it with something on it:

```bash
.venv/bin/python seed_demo.py --print-key
```

That seeds six sensors across six verticals with backdated history — one
walk-in already failed, a data hall and an engine bay drifting toward their
limits — then serves the console on :8080. It prints a login
(`dana@blueharbor.example` / `harbor-demo-2026`). It resets the store, so run
it only against a throwaway instance.

## Industry profiles

| Vertical key | Sector | Common catastrophe | Breach band |
|---|---|---|---|
| `cybersecurity` | CyberTech Data Centers | HVAC Circuit Trip / Cooling Fan Stalled | above 78 °F |
| `restaurant` | Franchise Restaurants | Unlatched Walk-In Freezer Door Gasket Failure | above 32 °F |
| `logistics` | High-Stakes Cold-Chain Transport | Reefer Truck Auxiliary Diesel Engine Stall | above 40 °F |
| `solar_infrastructure` | Solar Infrastructure & Storage | Inverter Thermal Runaway Overload | above 115 °F |
| `medical_lab` | Medical Labs & Blood Banks | Specimen Refrigerator Door Seal Degradation | above 46 °F or below 36 °F |
| `private_aviation` | Private Aviation Hangars | Hangar Bay Humidity Moisture Infiltration | above 85 °F |
| `superyacht` | Luxury Superyacht Engine Bays | Engine Room Ventilation Airflow Blockage | above 90 °F |
| `country_club` | High-End Country Clubs | Clubhouse Kitchen Walk-In Compressor Failure | above 32 °F |
| `pharmacy` | Retail & Hospital Pharmacies | Vaccine Refrigerator Compressor Failure | above 46 °F or below 36 °F |
| `wine_and_art` | Fine Wine & Art Storage | Cellar Climate Control Failure | above 60 °F or below 50 °F |
| `cannabis` | Licensed Cannabis Cultivation | Drying Room Climate Excursion | above 72 °F or below 58 °F |

Thresholds are exclusive: a reading exactly at the limit is nominal.

Each profile also names what that sector calls the thing being watched —
engine bays, racks, walk-ins, cellars — and the console uses that word
throughout, down to the search placeholder. A customer paying $4,999 a
vessel never reads the word "sensor" on their own screen.

## Fahrenheit or Celsius

Readings are stored in Fahrenheit and rendered in whichever unit the tenant
chose, so switching never rewrites history or loses precision on data
already collected:

```bash
curl -X POST localhost:8080/api/licenses/me/temperature-unit \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"temperature_unit": "C"}'
```

A sensor can report in either unit (`temperature_celsius` or
`temperature_fahrenheit` — exactly one, or the packet is refused), and the
choice follows all the way out to the alert: the breach reason, the
emergency SMS, the prompt handed to the model, the charts, the compliance
documents and the spoken escalation call all render in the customer's unit.
The call spells the unit out, because a text-to-speech engine reads
"21.0°C" as gibberish.

## Sites

A chain does not experience its estate as a flat list of thermometers. It
has stores, and a health inspector visits one of them.

```bash
curl -X POST localhost:8080/api/sites -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "Store 118 Boca Raton", "address": "Boca Raton, FL"}'
```

Sensors are placed at a site, and the on-call roster is scoped the same way:
a contact attached to a site is the only one woken for it, and contacts with
no site cover the whole estate as the fallback. The Boca Raton manager stops
getting a 3am call about a Boynton Beach freezer.

`GET /api/sites/{id}/reconciliation` flags a site that is being billed but
has no working sensor at it — the failure nobody notices until a claim is
denied.

## Sector shortcuts

Every vertical has a piece of paperwork its operators dread, and the platform
already holds the readings it is made of:

| Sector | Document |
|---|---|
| CyberTech Data Centers | Automated CISO Incident Briefing |
| Franchise Restaurants | Health Inspector Log Formatter |
| Cold-Chain Logistics | Reefer Cargo Handover Pass |
| Solar Infrastructure | Grid Thermal Yield Report |
| Medical Labs & Blood Banks | OSHA Cold-Storage Specimen Audit |
| Private Aviation Hangars | Hangar Avionics Humidity Log |
| Luxury Superyachts | Charter Guest Galley Safety Memo |
| High-End Country Clubs | Clubhouse Kitchen Inventory Safe-Guard |
| Retail & Hospital Pharmacies | VFC Storage & Handling Record |
| Fine Wine & Art Storage | Provenance Climate Certificate |
| Licensed Cannabis Cultivation | State Compliance Cultivation Log |

`POST /api/shortcuts/{vertical}?days=30` writes the document from that
tenant's own telemetry. The model is handed the real per-sensor figures and
told that inventing a reading makes the document worthless as evidence; the
response carries the same `evidence` block the document was built from, so
any line can be checked against it.

A tenant with no sensors in that vertical gets `409`, not an empty document —
a compliance sheet with nothing behind it reads as an attestation.

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
leave. `GET /api/contacts/preview` shows exactly who would be alerted right
now without sending anything — worth checking after editing a rota, since an
unverified rota is how an alert reaches an empty desk.

A tenant with no roster is still alerted: the store falls back to the contact
captured at onboarding, so alerting never depends on setup that hasn't
happened yet.

## Tuning a sensor's limits

The industry profiles are defaults, not rules. A particular freezer may be
held colder than its sector's rule of thumb, and a hangar in Phoenix is not a
hangar in Anchorage:

```bash
curl -X POST localhost:8080/api/licenses/me/sensors/BLOOD-07/thresholds \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"danger_above": 42.0, "danger_below": 38.0}'
```

An override replaces that bound for that sensor everywhere — breach detection,
the forecaster and the console all read the effective value. Sending `null`
restores the sector default. A band where the floor sits above the ceiling is
refused, since the sensor could never read in band.

## Pricing

Priced per unit, per month, by vertical — a rack is not a reefer truck and
is not billed like one. The invoice is derived from the sensors actually
registered, so a customer with three racks and two hangar bays pays
3 × $899 + 2 × $1,999 = $6,695 a month.

| Sector | Price | Protects against |
|---|---|---|
| Luxury Superyachts | **$4,999** / vessel / month | Engine-room thermal fires; $150k charter guest freezers at sea |
| Fine Wine & Art Storage | **$2,499** / cellar / month | Provenance, which is most of what the collection is worth |
| Private Aviation Hangars | **$1,999** / bay / month | Moisture corrosion of multi-million-dollar avionics |
| Medical Labs & Blood Banks | **$1,499** / vault / month | OSHA/FDA chain-of-custody; automated audit reports |
| High-End Country Clubs | **$1,499** / kitchen / month | Holiday dining inventory lost to compressor failure |
| Retail & Hospital Pharmacies | **$1,299** / pharmacy / month | The continuous-monitoring requirement for vaccine storage |
| Licensed Cannabis Cultivation | **$1,199** / room / month | Environmental control a licence renewal is judged against |
| Franchise Restaurants | **$999** / location / month | $15,000 walk-in spoilage; health department logs |
| CyberTech Data Centers | **$899** / rack / month | $50,000+ server meltdowns and SLA contract breaches |
| Solar Infrastructure | **$899** / enclosure / month | Battery-bank thermal runaway and fire liability |
| Cold-Chain Logistics | **$749** / reefer truck / month | Dock cargo rejection disputes; tamper-proof handover passes |

Rates are set for the pinnacle of each market rather than its volume. An
owner who spends millions a year on a vessel reads a $399 service as a toy,
and a price below the value at risk attracts exactly the buyer who haggles.

The rate card is public at `GET /api/billing/pricing` and on the sign-in
screen. `GET /api/billing` itemises a tenant's own estate; registering or
decommissioning a sensor reports what it adds to or removes from the bill.

### Plans are about contract, not price

| Plan | Unit ceiling | Term | Charged |
|---|---|---|---|
| `trial` | 5 | 14 days | no |
| `growth` | 50 | 365 days | yes |
| `enterprise` | 1000 | 365 days | yes |

A trial estate is priced but not charged, so a prospect can see what their
real fleet would cost. Downgrades that would strand units are refused with
`409`; decommission sensors first.

### Enterprise volume brackets

A chain is billed by enrolled branch count rather than per unit, and the
contract covers every sensor inside those branches:

| Units | Volume discount | Restaurant @ $999 | Vessel @ $4,999 |
|---|---|---|---|
| 1–9 | list | $999 | $4,999 |
| 10–19 | −2.5% | $974 | $4,874 |
| 20–29 | −5% | $949 | $4,749 |
| 30–39 | −7.5% | $924 | $4,624 |
| 40+ | −10% | $899 | $4,499 |

One ladder serves all eight verticals: the discount is a percentage of that
vertical's own rate, not a fixed dollar step. It caps at 10% deliberately —
at the top of a market a deep discount reads as eagerness, and every point of
it comes off the largest accounts.

There is **no ceiling on a contract**. A cap is a hard stop on revenue from
exactly the accounts worth the most; at these rates a chain of eleven vessels
would have hit a $50,000 cap and every vessel after that would be free.

**No estate is charged more than a larger estate would pay.** The discount
deepens a whole band at a time, so the ladder alone would make 40 units
cheaper than 39; the smaller estate gets the lower figure instead. A test
walks every vertical from 1 to 200 units asserting the bill never falls.

`GET /api/v1/enterprise-billing/tiers` publishes the bands, and every quote
carries a `next_tier` block naming where the next discount lands.

`POST /api/v1/enterprise-billing/provision-cluster` opens a contract;
`/quote` prices both models side by side before anyone signs, since volume
billing wins on multi-sensor sites and loses badly on single ones. An active
contract supersedes the rate card, and the rate-card figure is kept alongside
for comparison rather than charged.



### Add-ons, and why none of them is a share of savings

| Add-on | Basis | Price |
|---|---|---|
| Loss Assurance | per covered unit | $149 / month |
| Certified Compliance Vault | per estate | $499 / month |
| Sector Benchmarks | per estate | $299 / month |
| Equipment Intelligence | per estate | $399 / month |

Every one is a fixed fee. A percentage of what a customer saved sounds
aligned and is not: a quiet year pays nothing for the same standing
obligation, and on a marginal claim it puts the vendor on the wrong side of
the customer. A test asserts no add-on can be priced any other way.

### The terms that decide the other half

The rate card is half of what an estate is worth.

- **$1,500 per site at commissioning**, once. It funds acquisition on the
  day of signature rather than eleven months later, and a customer who paid
  to be installed does not churn casually.
- **10% for annual prepay.** Worth more than it costs: it removes the
  collections problem and fixes the customer for twelve months.
- **A 5% annual escalator** on multi-year contracts. Uncontroversial at
  signature and compounding — three years is 5.1% more contract value for
  no additional delivery.

`GET /api/billing/deal?years=3&annual_prepay=true&include_add_ons=assurance,vault`
returns the whole thing in one figure, so nobody discovers a setup fee or an
escalator afterwards. That is the only way those terms survive a renewal
conversation.

### Return on the subscription

`GET /api/billing/roi?days=30` weighs loss avoided against what the
subscription cost over the period.

Two rules keep that number honest. Only an incident **a person answered**
counts — an alert nobody acted on is a warning that went unheeded, not a
save. And only verticals with a **supplied** loss figure contribute: data
centres ($50,000), superyachts ($150,000) and restaurants ($15,000).
Incidents in the other five are listed and counted separately as
`unquantified_saves`, never folded into the total with an invented number.
Give me figures for those five and they'll count too.

## Authentication

Two credentials reach the same endpoints:

* a **tenant API key** in `X-CyberLogix-Key` identifies a machine — sensors,
  webhooks, the autopilot scheduler — and carries no human identity. It is
  returned exactly once, when the tenant is onboarded, and never echoed again;
* a **bearer session token** in `Authorization` identifies a signed-in person,
  and is what the console uses.

Public endpoints are `/`, `/api`, `/api/health`, `/api/industries`,
`/api/licenses/plans`, `/api/licenses/tenants` and `/api/accounts/login`.

Status codes distinguish the failure modes: `401` for a missing or unknown
credential, `402` for a suspended or expired license, `403` for a plan or role
that lacks the permission. A billing lapse is never reported as a bad
credential, and a wrong password and an unknown email return identical
wording so the endpoint cannot be used to enumerate accounts.

### Operators, roles and the audit trail

People sign in as themselves. Roles are `owner` > `operator` > `viewer`; each
implies the ones below it. Only owners invite users, change roles or disable
accounts, and an owner can neither demote nor disable themselves, so a tenant
is never left without one. Disabling someone revokes their live sessions
immediately rather than waiting for expiry. Passwords are scrypt hashes with
per-user salts and never leave the server.

Bootstrap the first owner with the tenant API key, then invite the rest:

```bash
curl -X POST localhost:8080/api/accounts/bootstrap \
  -H "X-CyberLogix-Key: $KEY" -H 'Content-Type: application/json' -d '{
  "email": "dana@blueharbor.example",
  "full_name": "Dana Reyes",
  "password": "a-long-passphrase"
}'

curl -X POST localhost:8080/api/accounts/login -H 'Content-Type: application/json' \
  -d '{"email": "dana@blueharbor.example", "password": "a-long-passphrase"}'
```

Someone locked out is recovered by an owner issuing a one-time reset
(`POST /api/accounts/users/{id}/reset`), handing the token over out of band,
and the user redeeming it at `POST /api/accounts/reset`. Only the token's hash
is stored, so a copy of the database cannot replay it; it works once, expires
in 24 hours, and redeeming it revokes that user's existing sessions, since a
reset usually means the account was compromised.

Repeated failed sign-ins for one account are throttled — eight attempts in
five minutes, then `429` with a `Retry-After`. The limit is per account, so
one address being attacked cannot lock the rest of the team out.

Every state change a human causes is written to a durable audit trail with
their name against it, readable at `GET /api/accounts/audit`. Acknowledging an
incident while signed in records *you*, not a generic label; the same action
performed with a machine key is recorded with `actor_role: "machine"`. A
compliance report is only as good as its provenance.

## Quick start

```bash
# 1. Onboard, and keep the api_key from the response
curl -X POST localhost:8080/api/licenses/tenants -H 'Content-Type: application/json' -d '{
  "company_name": "Blue Harbor Yacht Club",
  "contact_name": "Dana Reyes",
  "contact_phone": "+1-555-0100",
  "contact_email": "ops@blueharbor.example",
  "plan": "growth"
}'

# 2. Claim a seat for a sensor
curl -X POST localhost:8080/api/licenses/me/sensors \
  -H "X-CyberLogix-Key: $KEY" -H 'Content-Type: application/json' -d '{
  "sensor_id": "CLUB-WALKIN-1",
  "industry_vertical": "country_club",
  "location_name": "Clubhouse Kitchen / Walk-In"
}'

# 3. Pulse it
curl -X POST localhost:8080/api/sensor-pulse \
  -H "X-CyberLogix-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"sensor_id": "CLUB-WALKIN-1", "temperature_fahrenheit": 47.0}'
```

## The escalation ladder

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
`POST /api/autopilot/sweep` instead, **or when running more than one
replica** — otherwise each replica escalates the same incident.
`GET /api/health` reports which it is.

`POST /api/autopilot/sweep` still runs one pass on demand. One pass flags
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

Delivery follows the same rule as SMS and voice: it never raises. Three
failures in a row flag a hook as failing, and the breach handler carries on.

`GET /api/autopilot/compliance?days=7` assembles the inspector-ready
temperature log: readings logged, excursions, per-sensor min/max/mean,
incident counts and mean response time. Add `narrate=true` for a
Gemini-written executive summary.

`GET /api/autopilot/compliance.csv` returns the same figures as a spreadsheet
— one row per sensor plus a totals row — so it can go straight into an audit
pack without anyone re-typing numbers.

## BYOD: bring your own hardware

Customers do not need proprietary hardware. Any commercial Wi-Fi or cellular
sensor that can POST JSON — Elitech, Dickson, Monnit, SensorPush — reports
straight into the platform.

Bind the device's serial to a licensed seat at registration:

```json
{
  "sensor_id": "STORE118-WALKIN",
  "industry_vertical": "restaurant",
  "location_name": "Store 118 / Walk-In",
  "external_device_sn": "ELITECH-00:1B:44:11:3A:B7"
}
```

Then point the vendor's webhook at
`POST /api/v1/bridge/sensor-webhook-ingest`:

```json
{
  "device_sn": "ELITECH-00:1B:44:11:3A:B7",
  "api_key_token": "clx_...",
  "reading_value": 7.0,
  "metric_type": "temperature_c"
}
```

The token travels in the body because most off-the-shelf sensors cannot set
custom request headers; it is validated exactly as the header key is, with
`401` for an unknown token and `402` for a lapsed license.

Webhook readings run through the same engine as native pulses, so a BYOD
estate gets incidents, escalation, forecasting history and compliance
logging identically — a reading is scored against the industry profile of
the sensor it is bound to, never a flat number. 45 °F is a catastrophe in a
walk-in and unremarkable in a hangar.

`temperature_c` is converted to Fahrenheit on arrival. `humidity_pct` is
stored as context and passed to Gemini in alerts; humidity alone cannot
breach a thermal threshold. A serial with no binding is refused with `404`
rather than silently accepted.

## Meeting intelligence

`POST /api/v1/bridge/summarize-transcript` turns a staff meeting or voice
memo into an executive summary, operational decisions, assigned action items
and a sector compliance impact. Each vertical carries its own analytical
directive — a restaurant transcript is read for spoilage liability and
inspection prep, a medical lab's for chain-of-custody and audit readiness.
`GET /api/v1/bridge/sectors` lists them.

The model is asked for raw JSON; markdown fences and surrounding prose are
stripped before parsing. If the reply cannot be parsed, or is missing
required keys, the endpoint returns `TRANSCRIPT_PROCESSING_DEGRADED` with a
null report. It never falls back to invented minutes — fabricated action
items attributed to a real meeting are worse than no summary.

## Forecasting

`GET /api/forecast/sensor/{id}` fits a least-squares trend to the sensor's
recent history and projects when it crosses its threshold, returning
`hours_until_breach`, the drift in °F/hour and an r-squared confidence.
Risk bands: `critical` ≤ 1h, `high` ≤ 6h, `elevated` ≤ 24h, then `low`.
Add `narrate=true` for a preventive-maintenance brief.

`GET /api/forecast/fleet` ranks the whole estate riskiest-first.

A forecast needs at least 3 readings spanning 5 minutes; below that it
returns `insufficient_data` rather than guessing.

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

The spoken script is model-authored, so it is XML-escaped before it reaches
TwiML — an ampersand in a generated sentence would otherwise fail the call.

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

## Loss assurance

The guarantee, at a flat $149 per covered unit per month: if a breach is
recorded on a covered unit and no alert reaches anybody, CyberLogix
reimburses the deductible for that event up to $25,000.

Flat, never a share of what the customer saved. A percentage means a quiet
year pays nothing for the same standing obligation, and it puts the vendor
against the customer the moment a claim is marginal.

What makes it defensible rather than reckless is that an operator can void
their own cover without realising — a dead battery, an empty roster, a
sensor offline for a week. So `GET /api/assurance/cover` computes
eligibility continuously and names exactly what to fix, **before** an event:

> BLOOD-07 battery is at 12.0% and will go dark before it is noticed

A guarantee whose exclusions only appear at claim time is a trick; one that
tells you what to fix this morning is a service.

`GET /api/assurance/performance` publishes our own miss rate, counting an
alert we composed but could not send as a miss. A guarantee from a vendor
who hides that number is a slogan.

## Benchmarks and equipment intelligence

Two products fall out of data already being collected. `GET /api/benchmarks/{vertical}`
tells a customer where they sit against comparable operators on excursion
rate, response time and uptime — a number they cannot get anywhere else,
which changes every quarter. `GET /api/benchmarks` reports failure and drift
rates by hardware manufacturer across the whole fleet.

Both rest entirely on the privacy floor holding. Nothing is published for a
cohort under five operators, or a manufacturer under three operators and
eight units — below that a participant subtracts themselves and reads a
named competitor straight off. Findings are per manufacturer, never per
serial. A benchmark that leaked one chain's performance to a rival would
end the business that produced it.

## Invoicing

`POST /api/invoices` issues a numbered, dated invoice and **freezes it**.
The figures are snapshotted at issue rather than recomputed on read: an
invoice whose total moves after it was sent is a dispute the customer is
right to raise.

Numbers are sequential and gapless within a year, derived from what has
already been issued rather than a counter, so a restart cannot reissue one.
Voiding does not free the number — a gap in the sequence is the first thing
an auditor asks about. A short payment is recorded as a short payment
rather than quietly closing the invoice.

The document carries the issuer's legal name, address, tax ID and
remittance details from configuration, and says so plainly when they are
unset rather than printing a line reading "Tax ID:" with nothing after it.

Payment collection is left to a processor. `POST /api/invoices/{id}/paid`
is where a webhook would land, and the lifecycle is complete without one —
which is also how a bank transfer, how most contracts at these sizes are
actually settled, gets recorded.

## The reseller channel

A refrigeration servicer with two hundred restaurant clients already visits
every site and is already called at 2am when a walk-in fails. Selling to
them once puts the product in two hundred kitchens.

Partners are minted by the platform operator (`X-CyberLogix-Admin`, gated
on `CYBERLOGIX_ADMIN_KEY`; unset means those endpoints are **closed**, not
open) and earn a share of what their accounts bill — paid on collected
revenue, so they earn from accounts that stay.

The portal at `/partners` shows their book, what it earns them, which
accounts need an engineer today, and one account in operational detail. A
partner is a principal with narrower rights than a tenant owner: scoped to
their own accounts, and shown what an engineer needs without the customer's
roster or audit trail.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | Google GenAI credentials, read by the SDK |
| `TWILIO_ACCOUNT_SID` | — | Twilio account SID; unset means dry-run delivery |
| `TWILIO_AUTH_TOKEN` | — | Twilio auth token |
| `TWILIO_FROM_NUMBER` | — | Sending number in E.164, e.g. `+15550100` |
| `CYBERLOGIX_GEMINI_MODEL` | `gemini-2.5-flash` | Model for all generated copy |
| `CYBERLOGIX_ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins |
| `PORT` | `8080` | Listen port |
| `CYBERLOGIX_DB_PATH` | `cyberlogix.db` | SQLite file; use a mounted volume in production |
| `CYBERLOGIX_MAX_AI_CALLS_PER_DAY` | `200` | Per-tenant daily cap; `0` = unlimited |
| `CYBERLOGIX_MAX_SMS_PER_DAY` | `100` | Per-tenant daily cap; `0` = unlimited |
| `CYBERLOGIX_MAX_VOICE_CALLS_PER_DAY` | `30` | Per-tenant daily cap; `0` = unlimited |
| `CYBERLOGIX_RATE_AI_CALL` | `0.0012` | USD per model call, for the estimate |
| `CYBERLOGIX_RATE_SMS` | `0.0079` | USD per SMS, for the estimate |
| `CYBERLOGIX_RATE_VOICE_CALL` | `0.0140` | USD per voice call, for the estimate |
| `PUBLIC_BASE_URL` | — | Public URL so "press 1 to acknowledge" reaches the callback |
| `CYBERLOGIX_SWEEP_SECONDS` | `60` | Unattended sweep interval; `0` disables the in-process loop |
| `CYBERLOGIX_ATTESTATION_KEY` | — | Counter-signs vault attestations |
| `CYBERLOGIX_ADMIN_KEY` | — | Gates partner administration; unset closes it |
| `CYBERLOGIX_ALLOW_PRIVATE_WEBHOOKS` | — | Allow webhook targets on a private network |
| `CYBERLOGIX_LEGAL_NAME` | `CyberLogix AI` | Issuer name on invoices |
| `CYBERLOGIX_ADDRESS` | — | Issuer address on invoices |
| `CYBERLOGIX_TAX_ID` | — | Issuer tax ID on invoices |
| `CYBERLOGIX_REMIT_TO` | — | Remittance details on invoices |
| `CYBERLOGIX_BILLING_EMAIL` | — | Billing contact on invoices |

`.env.example` carries the same list with the reasoning next to each one, and
a test fails if a setting is read by the code but missing from that file — or
documented there and read by nothing.

Set real origins before production; browsers reject credentialed requests
against a wildcard, so credentials switch on only once origins are named.

## State

State is durable. `store.py` keeps a fast in-memory working set and writes
through to SQLite (`db.py`) on every change, reloading it at startup, so
tenants, sensors, readings, incidents, users, the audit trail and metered
usage all survive a restart. Because the queries are small per-tenant scans
rather than SQL, entities are stored as JSON documents keyed by `(kind, id)`
— durability without a migration burden.

Set `CYBERLOGIX_DB_PATH` to a mounted volume to outlive the container. The
container image defaults it to `/app/data/cyberlogix.db`.

It is still single-writer: pin to one instance (`--max-instances 1`) or
replace the `Database` class with a Postgres adapter before scaling out.
`db.py` is the only file that has to change — nothing above it does SQL, so
the adapter only needs `put`, `get`, `delete`, `all`, `count` and `clear`.

Reading history is capped per sensor by a ring buffer, and the eviction is
mirrored into the database, so the table cannot grow without bound.

## Running locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export GEMINI_API_KEY=your-key
.venv/bin/python main.py
```

## Tests

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest tests/ -q
```

216 tests across the fourteen modules. Gemini and Twilio are both stubbed and
the database is in-memory, so the suite runs without credentials, makes no
network calls and touches no file on disk.

## Deploying

The image is a two-stage build: the wheel tooling stays in the build stage,
so what ships is the interpreter, the installed packages and the
application. It runs as an unprivileged user, and copies the tree rather
than naming modules — a hand-written manifest had already fallen thirteen
modules behind the application, and the container would have crashed on
import the first time anybody deployed it. `tests/test_deployment.py` is
what makes that impossible to repeat.

```bash
gcloud run deploy cyberlogix-hub \
  --source . \
  --region us-central1 \
  --max-instances 1 \
  --min-instances 0 \
  --allow-unauthenticated \
  --set-secrets GEMINI_API_KEY=gemini-key:latest,\
TWILIO_AUTH_TOKEN=twilio-token:latest,\
CYBERLOGIX_ATTESTATION_KEY=attestation-key:latest,\
CYBERLOGIX_ADMIN_KEY=admin-key:latest \
  --set-env-vars TWILIO_ACCOUNT_SID=AC...,TWILIO_FROM_NUMBER=+15550100,\
PUBLIC_BASE_URL=https://cyberlogix-hub-xxxx.run.app
```

Secrets belong in Secret Manager, not `--set-env-vars`. Four matter: the
Gemini key, the Twilio token, the attestation signing key, and the platform
admin key that gates minting resellers.

**`--max-instances 1` is not incidental.** Two things assume a single
process: SQLite is single-writer, and the unattended sweep runs in-process,
so a second replica escalates every incident twice. To scale out, set
`CYBERLOGIX_SWEEP_SECONDS=0`, drive `POST /api/autopilot/sweep` from Cloud
Scheduler, and replace the `Database` class with a Postgres adapter —
`db.py` is the only file that has to change, since nothing above it writes
SQL. The adapter needs `put`, `get`, `delete`, `all`, `count` and `clear`.

`--min-instances 0` means an idle deployment costs nothing but storage.

### Before the first customer

Things this repository does not do for you, in the order they will bite:

- **Twilio is not live.** Until the three variables are set, every alert is
  composed, recorded and never sent. `/api/health` says `dry_run`, and the
  console shows a banner.
- **Payments are not collected.** Invoices are issued and tracked; a
  processor has to be wired to `POST /api/invoices/{id}/paid`.
- **Nothing is backed up.** The SQLite file on a mounted volume survives a
  restart, not a deleted volume.
- **The LLC does not exist yet.** `CYBERLOGIX_LEGAL_NAME` and the other
  issuer fields go on every invoice; an invoice from an entity that cannot
  receive money is not a document anyone can pay.
