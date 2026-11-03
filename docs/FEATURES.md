# What CyberLogix does

A tour of the product, area by area: the console, the industries it knows,
units, sites, the paperwork it writes, several businesses, your own
hardware, forecasts and benchmarks. The module map is in the
[README](../README.md#modules).

## The look

Board black (`#05070D`) under routed circuit traces carrying a cyan → blue
→ violet wash, drawn on a canvas by `static/circuit.js`. The traces are not
borrowed ornament: on a telemetry product they are the signal paths, and
the lights running along them are readings arriving from the estate. About
one in eight runs hot, in the alarm colour.

It is one file, shared by all three surfaces, `position: fixed` at
`z-index: -2` with no pointer events, and it paints a single still frame
and stops for anyone who has asked for reduced motion. A veil over it is
weighted to the middle of the page, where every column of running text
sits, and lifts toward the corners where the board is free to be seen —
that balance was measured, not judged: body copy over a lit trace was
reaching 2.05:1 against a 4.5 floor.

The slogan is *A freezer fails at two. Nobody finds out until seven.*

The landing page is at `/`, the console at `/console`, the reseller portal
at `/partners`, the machine-readable gateway at `/api`, and interactive API
docs at `/docs`.

The first screen of the landing page says who it is for, the lowest rate
and the trial; those figures are substituted when the page is served, from
`pricing.headline()` (the rate card and the plan tiers). Each industry page
at `/for/<sector>` leads with the problem and then the fix, the fix quoting
the limit that sector alarms on.

Beyond that the landing page quotes no prices of its own: the sector rate card and the
plan list are fetched from `/api/industries` and `/api/licenses/plans`, the
same endpoints the console uses, so a marketing page can never advertise a
number the product does not charge. A test asserts it.

## The console

Open `/console` in a browser and sign in with your email and password (or onboard a
company from the same card, which creates the tenant, its first owner and
signs you in). The console shows the fleet with a 12-point sparkline per
sensor, headline counts, the incident feed with the exact SMS and voice text
that went out and whether each was delivered, and controls to acknowledge,
resolve or escalate. It polls every 20 seconds.

A table view of the fleet is one click away for screen readers and for
copying figures out.

A new business sees a **first-day checklist** at the top: add a site, a
sensor, someone to call, and send a test alert. Each box is ticked by the
server from what the account holds (`first_day` in the overview), and the
card is gone once all four are true. Every empty card ends with a button
for the next step. Sign-up at `/signup` lands straight in the console,
signed in, with the business key shown once in the same box as every
other new key. A unit needs only its location: the sensor ID is made from
it when left blank, and its first reading appears within seconds rather
than on the next 20-second poll.

**The simulator** (`/api/simulator`, owner only) puts up to three pretend
units on the console for a demo with no hardware. They read, can be made to
fail, and show the text a real breach would send — marked SIMULATED on every
card and never sent. They live in the server's memory only: never a
reading, an incident, a seat, a compliance line or a vault entry, and a
restart clears them. The old **Send safe pulse** and **Simulate failure**
buttons, which wrote invented readings into the real record, are gone.

To see it with something on it:

```bash
.venv/bin/python seed_demo.py --print-key
```

That seeds six sensors across six verticals with backdated history — one
walk-in already failed, a data hall and an engine bay drifting toward their
limits — then serves the app on :8080. It prints a login
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
| `cryostorage` | IVF Clinics & Cryostorage | Cryogenic Tank Nitrogen Boil-Off / Vapour Space Warming | above −238 °F or below −325 °F |

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

## One estate, one scale

Readings are stored in Fahrenheit and converted for display. The console
did that. The 3am text did that. The compliance export did not.

So a cellar in Bordeaux set its account to Celsius, saw Celsius
everywhere it looked, was texted `25.56°C` when a cave went warm — and
then handed an inspector a document headed **`Min °F`** reading **78.0**
for the same event. Two documents about the same freezer, disagreeing,
which is the exact thing a compliance record exists to prevent. And
"78" in a wine cellar is a catastrophe or an ordinary afternoon
depending only on which scale the reader assumes; the export never said
which.

The compliance CSV and report now render in the account's unit, label it
on every row, and the JSON carries `temperature_unit` so a figure lifted
into a spreadsheet takes its scale with it.

Two things were checked and deliberately left alone. The attestation was
already converting and its safe band already carried an explicit `unit`.
And the vault's hash chain stays in Fahrenheit: its field is literally
named `temperature_fahrenheit`, the hash covers that value, and
converting it would break every chain already exported — including ones
customers have handed to insurers, which is the one thing the feature
promises cannot happen.

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
| IVF Clinics & Cryostorage | Cryostorage Continuity Record |

`POST /api/shortcuts/{vertical}?days=30` writes the document from that
tenant's own telemetry. The model is handed the real per-sensor figures and
told that inventing a reading makes the document worthless as evidence; the
response carries the same `evidence` block the document was built from, so
any line can be checked against it.

A tenant with no sensors in that vertical gets `409`, not an empty document —
a compliance sheet with nothing behind it reads as an attestation.

## Several businesses

One person can run several businesses from one sign-in: an owner with a
restaurant and a pharmacy, or a manager looking after shops that are
separate companies. The company name at the top of the console is the
switcher. It lists every business the person can open, with how many
alerts and offline units each one has, and a red count appears on it when
one of the *other* businesses needs them.

- **Add a business** (owners only) starts it on the usual free trial,
  under the same terms, with you as its owner. It gets its own sensor key,
  shown once. A sign-in can hold up to `CYBERLOGIX_MAX_BUSINESSES` (10).
- **Let somebody with a sign-in join your business** with a join code: an
  owner makes one (choosing the role), gives it to the person directly, and
  they enter it from their own account under *Join a business*. It works
  once and lasts three days. Adding an existing person by email alone is
  refused: both of you have to agree, so no business can attach itself to
  somebody's sign-in by knowing their address. Codes not used yet are
  listed under the form (`GET /api/accounts/businesses/join-codes`, never
  the code itself, which is shown once) and can be withdrawn
  (`DELETE .../join-codes/{code_id}`); a used, withdrawn or expired code
  does nothing, and a code only ever adds the business that made it. The
  owner changes a member's role, or removes them, from *Your team*.
- **Nobody else manages your sign-in.** No business can reset the password
  of a sign-in that is also used for other businesses, the one that happens
  to hold it included; the person resets it themselves from the sign-in
  page. A reset ends their sessions in every business.
- **Each business stays separate.** The person has one record per
  business (`User.linked_to` points at their sign-in), so their role is
  per business and every permission check works exactly as before.
  Switching ends the session on one business and starts one on the other,
  never lasting longer than the session it replaced. Removing somebody
  from a business closes only that business, whichever one their sign-in
  lives in, and the removed business shows them its name and nothing more.
- **Signing in picks up where you left off.** Each business's record of a
  person notes when they last opened it, by signing in or switching, so a
  new sign-in, on any device, opens the business they had open last. One
  they have since been removed from, or that is suspended, is passed over
  for the one before it. A reload of the console stays where it was.
- **Checked with five.** `tests/test_business_switcher.py` gives one
  person five businesses (owner of three, operator at one, viewer at
  another) and after every switch reads the console, alerts, units, team,
  audit trail, on-call list, invoices and sites, looking for any trace of
  the other four; checks the role she holds in each; and checks the
  session she switched from is dead. `tools/console_smoke.js` does the
  same in a browser at phone and desktop size, including the link in the
  monthly summary email, which opens the right business first.

## The first form a customer sees

They tell us their sector at sign-up. The audit trail records it. And
until now every sector picker in the console opened on whatever happened
to be first in the list — for a restaurant that signed up ten seconds
earlier, "CyberTech Data Centers".

That is not a cosmetic default. A walk-in freezer registered under the
data-centre sector is monitored against a **78°F** limit instead of
**32°F**. It alarms long after the food is gone, the product's one
promise fails silently, and the customer cannot see that it has. The
worst version of this bug is the one where nobody notices they picked
wrong.

The console now opens on what they said, falling back to whatever they
have actually registered most of, so an estate that grew past its
sign-up answer still opens on itself. The example unit in the form
follows the sector — a cryostorage customer is shown a tank, not a
walk-in — and follows it again if they change the picker by hand.

Two details that took longer than the fix. It is wired into both the
industry load and the overview load, because those race and the overview
wins: a version hooked only to the overview saw zero industries, matched
nothing, and left the picker exactly where it started, which looks
identical to not having written it. And it applies once per session
rather than on every poll, because `refresh()` runs on a timer and
re-selecting the dropdown every few seconds would yank it back from
somebody halfway through registering a unit in a second sector.

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

## Forecasting

`GET /api/forecast/sensor/{id}` fits a least-squares trend to the sensor's
recent history and projects when it crosses its threshold, returning
`hours_until_breach`, the drift in °F/hour and an r-squared confidence.
Risk bands: `critical` ≤ 1h, `high` ≤ 6h, `elevated` ≤ 24h, then `low`.
Add `narrate=true` for a preventive-maintenance brief.

`GET /api/forecast/fleet` ranks the whole estate riskiest-first.

A forecast needs at least 3 readings spanning 5 minutes; below that it
returns `insufficient_data` rather than guessing.

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

## When somebody shares the link

A product at this size is sold by a link pasted into a Slack channel or
an email to whoever signs things off. A link that renders as a bare URL
has spent the introduction the sender was making.

Every public page (the front page, `/signup`, `/pricing`, `/help`, `/for`
and each industry page, `/legal`) carries a full Open Graph and Twitter
card, and `/static/og.png` is the image. Each also names the favicon
(`/favicon.ico` and the SVG mark), the home-screen icon and the web
manifest, and a test checks that every icon file named exists. The trap is quiet and total: those URLs
are fetched by a scraper with no page to resolve a relative path
against, so a relative one is silently dropped and the card falls back
to nothing. The deployment's own address is the one thing a static file
cannot know, so it is substituted when the page is served — from the
request itself when `PUBLIC_BASE_URL` is unset, so previews work on a
laptop without anybody configuring anything, and from the variable when
it is set, because behind a proxy the request's idea of its own host is
whatever the proxy passed on.

`/sitemap.xml` lists only those public pages. Everything else (the
console, the simple view, the partner portal, the book, the reset and
offline pages, a shared proof link, the API and the demo simulator behind
it) answers with `X-Robots-Tag: noindex`, and so does every error.
`/robots.txt` points at the sitemap and keeps crawlers off those private
paths. None of them is secret — each renders nothing without a credential — but
a search result that lands somebody on a password field has taught them
nothing about the product and asked them for a credential.

## When something goes wrong

A browser that asks for a page that does not exist gets a 404 page in the
site's own look, with a way home, to the console and to help. A fault
gets a 500 page with a reference code (the fault id, which the operator
can look up) and never the error itself. Under `/api`, `/docs` and
`/static` both stay JSON, as a script expects. The 500 is answered from
outside the middleware stack, so the handler adds the security headers
itself.
