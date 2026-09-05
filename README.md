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

The console is at `/`, the machine-readable gateway at `/api`, and
interactive API docs at `/docs`.

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

Thresholds are exclusive: a reading exactly at the limit is nominal.

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
3 × $499 + 2 × $349 = $2,195 a month.

| Sector | Price | Protects against |
|---|---|---|
| CyberTech Data Centers | **$499** / rack / month | $50,000+ server meltdowns and SLA contract breaches |
| Luxury Superyachts | **$399** / vessel / month | Engine-room thermal fires; $150k charter guest freezers at sea |
| Private Aviation Hangars | **$349** / bay / month | Moisture corrosion of multi-million-dollar avionics |
| Solar Infrastructure | **$299** / enclosure / month | Battery-bank thermal runaway and fire liability |
| Medical Labs & Blood Banks | **$199** / vault / month | OSHA/FDA chain-of-custody; automated audit reports |
| High-End Country Clubs | **$199** / kitchen / month | Holiday dining inventory lost to compressor failure |
| Cold-Chain Logistics | **$129** / reefer truck / month | Dock cargo rejection disputes; tamper-proof handover passes |
| Franchise Restaurants | **$450** / location / month | $15,000 walk-in spoilage; health department logs |

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

| Branches | Rate per branch | Example | Monthly |
|---|---|---|---|
| 1–9 | $1,000 | 9 | $9,000 |
| 10–19 | $975 | 19 | $18,525 |
| 20–29 | $950 | 29 | $27,550 |
| 30–39 | $925 | 39 | $36,000 |
| 40–49 | $900 | 49 | $43,750 |
| 50–59 | $875 | 59 | $50,000 (capped) |
| 60–69 | $850 | 69 | $50,000 (capped) |
| 70–79 | $825 | 79 | $50,000 (capped) |
| 80+ | $800 (floor) | 100 | $50,000 (capped) |

The rate drops $25 every ten branches until it floors at $800, which it
reaches at 80 branches.

**A contract is capped at $50,000 a month**, whatever the branch count. The
per-branch rates apply underneath it; the ceiling first bites at 58 branches
($50,000 rather than $50,750), and beyond that a chain pays no more however
far it grows — 200 branches is still $50,000, an effective $250 a branch.

**No estate is charged more than a larger estate would pay.** The rate steps
a whole band at a time, so the card on its own would make 40 branches
($36,000 at $900) cheaper than 39 ($36,075 at $925). The same inversion sits
at 49→50, 59→60, 69→70 and 79→80. Rather than hand a customer that
arithmetic, the smaller estate is billed the lower figure — which is why 39
branches shows $36,000 above, not $36,075. A test asserts the whole curve
never falls as branches rise.

`GET /api/v1/enterprise-billing/tiers` publishes the bands, and every quote
carries a `next_tier` block naming where the next discount lands.

`POST /api/v1/enterprise-billing/provision-cluster` opens a contract;
`/quote` prices both models side by side before anyone signs, since volume
billing wins on multi-sensor sites and loses badly on single ones. An active
contract supersedes the rate card, and the rate-card figure is kept alongside
for comparison rather than charged.



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

## Autopilot

`POST /api/autopilot/sweep` is the unattended watchdog — point Cloud Scheduler
at it every few minutes. One pass flags sensors that have gone silent for over
30 minutes (a sensor that cannot report cannot warn you) and places voice calls
for incidents past the grace window. Pass `auto_escalate=false` to report
without calling.

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

218 tests across the fourteen modules. Gemini and Twilio are both stubbed and
the database is in-memory, so the suite runs without credentials, makes no
network calls and touches no file on disk.

## Deploying to Cloud Run

```bash
gcloud run deploy cyberlogix-hub \
  --source . \
  --region us-central1 \
  --max-instances 1 \
  --min-instances 0 \
  --allow-unauthenticated \
  --set-env-vars GEMINI_API_KEY=your-key,TWILIO_ACCOUNT_SID=AC...,TWILIO_AUTH_TOKEN=...,TWILIO_FROM_NUMBER=+15550100
```

Put the two secrets in Secret Manager rather than plain env vars for a real
deployment.
