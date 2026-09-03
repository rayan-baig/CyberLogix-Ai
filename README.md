# CyberLogix AI — Universal Thermal & Catastrophe Engine

24/7 IoT sensor telemetry monitoring with automated Gemini AI emergency SMS
dispatch, covering eight commercial industry verticals.

Wireless thermal sensors POST their readings to the engine. Each reading is
scored against the threshold profile for its vertical. A nominal reading is
acknowledged and logged. A breach escalates immediately: the engine identifies
the likely root-cause catastrophe for that sector and has Gemini draft an
urgent SMS for the on-call facility director.

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

## API

### `GET /api/health`
Liveness probe. Reports profile count and whether Gemini dispatch is live or
running on the fallback template.

### `GET /api/industries`
Full vertical catalogue with names, catastrophes and thresholds — intended for
populating a client-side sector selector.

### `POST /api/sensor-pulse`
Ingest one telemetry packet.

```json
{
  "sensor_id": "RACK-01",
  "industry_vertical": "cybersecurity",
  "location_name": "Austin DC / Hall B",
  "temperature_fahrenheit": 94.0,
  "humidity_percent": 61.5
}
```

`humidity_percent` is optional (defaults to 50.0) and is passed to Gemini as
additional context. `industry_vertical` is matched case-insensitively with
surrounding whitespace trimmed. An unknown vertical returns `400`; a malformed
packet returns `422`.

A nominal response:

```json
{
  "status": "nominal",
  "industry": "CyberTech Data Centers",
  "sensor_id": "RACK-01",
  "current_temperature": 68.4,
  "message": "Telemetry parameters stable within safe operating bounds."
}
```

A breach response carries `status: "CRITICAL_CATASTROPHE_TRIGGERED"`, the
identified `catastrophe_type`, the `breach_details` explaining which bound was
crossed, and the `dispatched_sms_text`. `dispatch_source` is `gemini` when the
model drafted the alert and `fallback_template` when it did not.

## Alerting is fail-open

A breach always produces an alert. If the Gemini client is uninitialized, the
API call fails, or the model returns an empty body, the engine emits a
deterministic SMS template instead and marks `dispatch_source` accordingly. A
credentials problem degrades the wording of an alert — it never suppresses one.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | Google GenAI credentials, read by the SDK |
| `CYBERLOGIX_GEMINI_MODEL` | `gemini-2.5-flash` | Dispatch model |
| `CYBERLOGIX_ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins |
| `PORT` | `8080` | Listen port |

Set real origins in `CYBERLOGIX_ALLOWED_ORIGINS` before going to production;
the wildcard default disables credentialed CORS.

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

The suite stubs the Gemini client, so it runs without credentials and never
makes a network call.

## Deploying to Cloud Run

```bash
gcloud run deploy cyberlogix-engine \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GEMINI_API_KEY=your-key
```

The container listens on `$PORT`, which Cloud Run injects, and runs as an
unprivileged user.
