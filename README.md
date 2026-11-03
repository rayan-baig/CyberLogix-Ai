# CyberLogix AI — Master Enterprise Hub

CyberLogix is a product of MTRX Horizyn. It watches the temperature of
things that must not get warm — walk-in freezers, server racks, vaccine
fridges, wine cellars, engine bays — and makes sure a person finds out
in minutes, not hours.

Universal IoT telemetry, license control, autonomous operations, voice
escalation and predictive forecasting, mounted behind one FastAPI service.
In production it runs on its own subdomain of MTRX Horizyn's domain,
`cyberlogix.<your domain>`, beside the company's other products;
[DEPLOY.md](DEPLOY.md) section 1 sets that up.

Wireless thermal sensors report to the hub. Each reading is scored against
the threshold profile for its industry. A breach opens an incident and texts
the on-call roster which unit, where and how far out of range; a reply of
"1" acknowledges it. If nobody does, the hub escalates to a spoken phone
call, where pressing 1 does the same. In the background the forecaster projects which sensors
will fail next, and the compliance clerk assembles the paperwork inspectors
ask for.

The slogan is *A freezer fails at two. Nobody finds out until seven.*

**For customers**, in plain words:

* [Your data](docs/YOUR-DATA.md): how to download everything, or delete
  everything, what is kept afterwards and why, and what happens to backups.
* [The vault](docs/THE-VAULT.md): what the tamper-evident record is, what
  an attestation proves and what it does not, and how anyone can check one.
* [A sensor is offline](docs/SENSOR-OFFLINE.md): what offline means, what
  we do about it, and what to check, in order.

The app has the same answers at `/help`, and every rate with a price
calculator at `/pricing`. [CHANGELOG.md](CHANGELOG.md) says what changed in
each version; the number in use is in `VERSION` and at `/api/health`.

## Where the docs are

| Document | What is in it |
|---|---|
| [docs/FEATURES.md](docs/FEATURES.md) | The console, the industry profiles and their limits, Fahrenheit or Celsius, sites, sector paperwork, several businesses, your own hardware (BYOD), forecasting, meeting summaries, benchmarks, link previews |
| [docs/ALERTS.md](docs/ALERTS.md) | Who gets alerted, the escalation ladder and "press 1", the sweep, Slack/Teams/PagerDuty channels, Twilio delivery and retries, what a message costs, spend caps |
| [docs/API.md](docs/API.md) | The three credentials, roles and the audit trail, password resets, provisioning paid accounts, security headers, numbers the API refuses |
| [docs/BILLING.md](docs/BILLING.md) | The rate card, plans, volume pricing, add-ons, ROI, invoices, contracts that bill themselves, collections, taking payment, the books, margins, resellers |
| [docs/MAIL.md](docs/MAIL.md) | The mail transport and its guards, trial-conversion emails, the daily and weekly digests |
| [docs/VAULT-INTERNALS.md](docs/VAULT-INTERNALS.md) | How the hash-chained vault works, compliance reports, evidence kept past the rolling window, insurance claim packets |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Every setting, where the state lives, backups, the dead man's switch, deleting a business, the agreements, deployment details, what to do before the first customer |
| [docs/TESTING.md](docs/TESTING.md) | The suite, the type check on the money code, the fuzzer, the browser smoke test, mutation testing |
| [DEPLOY.md](DEPLOY.md) | The step-by-step runbook for going live |
| [docs/YOUR-DATA.md](docs/YOUR-DATA.md), [docs/THE-VAULT.md](docs/THE-VAULT.md), [docs/SENSOR-OFFLINE.md](docs/SENSOR-OFFLINE.md) | The customer guides above |

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
| Operations Console | `/console` | Browser UI over the whole platform |
| Public Front Door | `/` | What the product is, who it is for, what it costs |
| Operator Accounts & Audit | `/api/accounts` | People, roles, durable audit trail |
| Several Businesses | `/api/accounts/businesses` | One sign-in, several businesses, and a switcher between them |
| Spend Controls | `/api/costs` | Caching, daily caps, cost reporting |
| On-Call Roster | `/api/contacts` | Who gets woken, and in what order |
| Pricing & Billing | `/api/billing` | Per-unit rate card, invoice, ROI |
| Enterprise Volume Billing | `/api/v1/enterprise-billing` | Per-branch pricing for chains |
| Sector Shortcuts | `/api/shortcuts` | The one document each industry must produce |
| Sites | `/api/sites` | Locations, so an estate is not a flat list of thermometers |
| Outbound Alert Webhooks | `/api/webhooks` | Slack, Teams, PagerDuty, generic JSON |
| Compliance Vault | `/api/vault` | Hash-chained readings and verifiable attestations |
| Insurance Claim Packets | `/api/claims` | Everything an adjuster asks for, assembled |
| Leaving | `/api/accounts/business` | Download everything, or delete everything |
| First Day | `/api/contacts/test-alert` | A checklist that ticks itself, and a test text to the roster |
| Simulator | `/api/simulator` | Pretend units for a demo, never recorded |
| Industry Benchmarks | `/api/benchmarks` | Where an estate sits against its cohort |
| Invoicing | `/api/invoices` | Numbered, dated, frozen demands for money |
| Reseller Channel | `/api/partners` | A servicer's book of managed accounts |
| Self-Serve Sign-up | `/api/signup` | A stranger becomes a customer without asking anyone |
| Contracts & Collections | `/api/contracts` | The signed term, billing itself, and chasing what is late |
| Agreements | `/api/legal` | Terms generated from the code they describe |
| Revenue Worklist | `/book` | The operator's own view: who to call, and what it is worth |
| Outbound Mail | `/api/mail` | The transport, the queue, and who has asked us to stop |
| Trial Conversion | `/api/conversion` | The trial asks for the order itself |
| Digests & Reports | `/api/digest` | The book by email, and the customer's weekly evidence |
| Backups | `/api/admin` | Verified snapshots of the one file that is the company |
| Watchdog | `/api/watchdog` | Whether the thing that watches the freezers is running |
| Discovery | `/robots.txt` | Link previews, the sitemap, and what a crawler may index |
| Help | `/help`, `/api/help` | Customer questions answered from the code, and what to check when a sensor is offline |
| Pricing page | `/pricing` | Every rate and a calculator, rendered from the rate card |


The landing page is at `/`, the console at `/console`, the reseller portal
at `/partners`, the machine-readable gateway at `/api`, and interactive API
docs at `/docs`.

## Running locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -c constraints.txt
export GEMINI_API_KEY=your-key   # optional: without it, alerts use plain templates
.venv/bin/python main.py
```

It listens on port 8080. Nothing else needs to be set: without Twilio
credentials every alert is composed and recorded but not sent (dry run),
and without a model key the wording comes from fixed templates. The
settings, with the reasoning next to each, are in `.env.example`; the
table is in [docs/OPERATIONS.md](docs/OPERATIONS.md#configuration).

To see the console with something on it:

```bash
.venv/bin/python seed_demo.py --print-key
```

That seeds six sensors across six verticals with backdated history — one
walk-in already failed, a data hall and an engine bay drifting toward their
limits — then serves the app on :8080. It prints a login
(`dana@blueharbor.example` / `harbor-demo-2026`). It resets the store, so run
it only against a throwaway instance.

## Quick start

```bash
# 1. Start a trial. Public, rate limited, and it returns a working session
#    as well as the api_key — no second call to bootstrap an owner.
curl -X POST localhost:8080/api/signup -H 'Content-Type: application/json' -d '{
  "company_name": "Blue Harbor Yacht Club",
  "full_name": "Dana Reyes",
  "email": "dana@blueharbor.example",
  "password": "correct-horse-battery",
  "contact_phone": "+1-555-0100",
  "industry_vertical": "country_club"
}'

# 2. Claim a seat for a sensor
curl -X POST localhost:8080/api/licenses/me/sensors \
  -H "X-CyberLogix-Key: $KEY" -H 'Content-Type: application/json' -d '{
  "sensor_id": "CLUB-WALKIN-1",
  "industry_vertical": "country_club",
  "location_name": "Clubhouse Kitchen / Walk-In"
}'

# 3. Pulse it. 28F is inside the limit, so nothing happens: one reading
#    recorded, no incident, nobody woken.
curl -X POST localhost:8080/api/sensor-pulse \
  -H "X-CyberLogix-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"sensor_id": "CLUB-WALKIN-1", "temperature_fahrenheit": 28.0}'

# 4. Now break it. 61F is past the 32F country-club limit, so this opens
#    an incident and texts the roster.
curl -X POST localhost:8080/api/sensor-pulse \
  -H "X-CyberLogix-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"sensor_id": "CLUB-WALKIN-1", "temperature_fahrenheit": 61.0}'
```

The sign-up response carries the same four commands with the key already
filled in, so this is the same walkthrough the product hands a customer.


`$KEY` is the `api_key` from step 1. Creating a paid account directly,
roles, and the other credentials are in [docs/API.md](docs/API.md).

## Deploying

> Running it for real rather than trying it out? **[DEPLOY.md](DEPLOY.md)**
> is the runbook: domain and DNS, the container, the secrets, the dead
> man's switch, and the end-to-end test with real hardware. `python
> readiness.py` tells you what is still blocking at any point.

The short version:

* It ships as one container (`Dockerfile`), running as an unprivileged
  user. `render.yaml` sets it up on Render in one step; the Cloud Run
  command is in [docs/OPERATIONS.md](docs/OPERATIONS.md#deploying).
* **Run exactly one instance.** The working set is held in memory and the
  escalation sweep runs inside the process, so a second replica would
  escalate every incident twice.
* Put the SQLite file (`CYBERLOGIX_DB_PATH`) on a disk that outlives the
  container, and copy the daily backups somewhere else.
* Keep secrets (Twilio token, attestation key, admin key, model key) in
  the host's secret store, not in plain environment settings or the
  repository.
* Read [Before the first customer](docs/OPERATIONS.md#before-the-first-customer)
  — what this repository does not do for you, in the order it will bite.

## Tests

```bash
.venv/bin/pip install pytest httpx
.venv/bin/python -m pytest tests/ -q
```

About 2,010 tests. Gemini, Twilio and SMTP are stubbed and the database is
in memory, so the suite needs no credentials, makes no network calls and
touches no file on disk. CI runs the suite on Python 3.11 (what the image
ships) and again on 3.13, and also runs pyflakes, a type check on the money
code (`python -m mypy`), a dependency audit and a build of the image. The
fuzzer, the browser smoke test and the rest are in
[docs/TESTING.md](docs/TESTING.md).

## How the code is laid out

`main.py` mounts every router. `store.py` holds the live state behind one
lock and writes every change through to SQLite (`db.py`); the records it
keeps are defined in `records.py`, and the fixed rules (industry profiles,
plans, the breach check, volume pricing, temperature units) in
`domain.py`. Each feature area has its own module, named for what it does
(`invoicing.py`, `contracts.py`, `vault.py`, `mail.py` and so on). More in
[docs/OPERATIONS.md](docs/OPERATIONS.md#state).
