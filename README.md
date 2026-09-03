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

Interactive API docs are at `/docs`.

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

## Plans

| Plan | Seats | Term | Voice escalation | Forecasting |
|---|---|---|---|---|
| `trial` | 5 | 14 days | no | no |
| `growth` | 50 | 365 days | yes | yes |
| `enterprise` | 1000 | 365 days | yes | yes |

A seat is one registered sensor. Downgrades that would strand seats are
refused with `409`; decommission sensors first.

## Authentication

Every endpoint except `/`, `/api/health`, `/api/industries`,
`/api/licenses/plans` and `/api/licenses/tenants` requires the tenant's API
key in an `X-CyberLogix-Key` header. The key is returned exactly once, when
the tenant is onboarded, and is never echoed afterwards.

Status codes distinguish the failure modes: `401` for a missing or unknown
key, `402` for a suspended or expired license, `403` for a plan that lacks
the requested feature. A billing lapse is never reported as a bad credential.

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

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | Google GenAI credentials, read by the SDK |
| `CYBERLOGIX_GEMINI_MODEL` | `gemini-2.5-flash` | Model for all generated copy |
| `CYBERLOGIX_ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins |
| `PORT` | `8080` | Listen port |

Set real origins before production; browsers reject credentialed requests
against a wildcard, so credentials switch on only once origins are named.

## State

State lives in memory behind a re-entrant lock (`store.py`), which is correct
for a single Cloud Run instance. It does not survive a restart and is not
shared across replicas — pin to one instance (`--max-instances 1`) or swap
`HubStore` for a Firestore or Postgres adapter before scaling out. That class
is the only file that has to change.

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

82 tests across the seven modules. Gemini is stubbed, so the suite runs
without credentials and makes no network calls.

## Deploying to Cloud Run

```bash
gcloud run deploy cyberlogix-hub \
  --source . \
  --region us-central1 \
  --max-instances 1 \
  --allow-unauthenticated \
  --set-env-vars GEMINI_API_KEY=your-key
```
