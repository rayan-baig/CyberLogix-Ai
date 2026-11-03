# Running it in production

Settings, where the state lives, backups, the dead man's switch, deleting
a business, the agreements, and the deployment details behind the short
version in the README. The step-by-step runbook is
[DEPLOY.md](../DEPLOY.md).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | Google GenAI credentials, read by the SDK |
| `TWILIO_ACCOUNT_SID` | — | Twilio account SID; unset means dry-run delivery |
| `TWILIO_AUTH_TOKEN` | — | Twilio auth token |
| `TWILIO_FROM_NUMBER` | — | Sending number in E.164, e.g. `+15550100` |
| `CYBERLOGIX_GEMINI_MODEL` | `gemini-2.5-flash` | Model for all generated copy |
| `CYBERLOGIX_ALLOWED_ORIGINS` | none | Exact origins whose pages may call the API from a browser; unset allows only this site's own pages. Never `*` or a sibling subdomain |
| `PORT` | `8080` | Listen port |
| `CYBERLOGIX_DB_PATH` | `cyberlogix.db` | SQLite file; use a mounted volume in production |
| `CYBERLOGIX_MAX_AI_CALLS_PER_DAY` | `200` | Per-tenant daily cap; `0` = unlimited |
| `CYBERLOGIX_MAX_SMS_PER_DAY` | `100` | Per-tenant daily cap; `0` = unlimited |
| `CYBERLOGIX_MAX_VOICE_CALLS_PER_DAY` | `30` | Per-tenant daily cap; `0` = unlimited |
| `CYBERLOGIX_RATE_AI_CALL` | `0.0012` | USD per model call, for the estimate |
| `CYBERLOGIX_RATE_SMS` | `0.0079` | USD per SMS, for the estimate |
| `CYBERLOGIX_RATE_VOICE_CALL` | `0.0140` | USD per voice call, for the estimate |
| `PUBLIC_BASE_URL` | — | Public URL, `https://cyberlogix.<domain>`: "press 1 to acknowledge" reaches the callback, and link previews and the sitemap use whole addresses. Unset, pages use the address the request arrived on |
| `CYBERLOGIX_SWEEP_SECONDS` | `60` | Unattended sweep interval; `0` disables the in-process loop |
| `CYBERLOGIX_ATTESTATION_KEY` | — | Counter-signs vault attestations |
| `CYBERLOGIX_ADMIN_KEY` | — | Gates partner administration; unset closes it |
| `CYBERLOGIX_ALLOW_PRIVATE_WEBHOOKS` | — | Allow webhook targets on a private network |
| `CYBERLOGIX_LEGAL_NAME` | `MTRX Horizyn` | Issuer name on invoices and in the agreements: MTRX Horizyn's registered name |
| `CYBERLOGIX_ADDRESS` | — | Issuer address on invoices |
| `CYBERLOGIX_TAX_ID` | — | Issuer tax ID on invoices |
| `CYBERLOGIX_REMIT_TO` | — | Remittance details on invoices |
| `CYBERLOGIX_BILLING_EMAIL` | — | Billing contact on invoices |
| `CYBERLOGIX_SUPPORT_EMAIL` | — | Where the help page tells customers to write. Unset, it uses `MAIL_REPLY_TO`; with neither, it points them at the contact on their invoice |

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

The shape of each record (`Tenant`, `Sensor`, `Invoice` and the rest) and
its `to_row`/`from_row` are in `records.py`; the fixed rules it applies
(industry profiles, plans, the breach check, volume pricing, temperature
units) are in `domain.py`. `store.py` re-exports both, so code imports
everything from `store`.

Set `CYBERLOGIX_DB_PATH` to a mounted volume to outlive the container. The
container image defaults it to `/app/data/cyberlogix.db`.

It is still single-writer: pin to one instance (`--max-instances 1`) or
replace the `Database` class with a Postgres adapter before scaling out.
`db.py` is the only file that has to change — nothing above it does SQL, so
the adapter only needs `put`, `get`, `delete`, `all`, `count` and `clear`.

Reading history is capped per sensor by a ring buffer, and the eviction is
mirrored into the database, so the table cannot grow without bound.

## Backups

Every tenant, every sensor, every reading, every invoice and the whole
hash-chained vault live in one SQLite file. It survives a restart. It does
not survive a deleted volume, a mistyped `rm`, or the disk it sits on —
and the vault is the part that hurts, because a customer's evidence that
their freezer held temperature for two years cannot be reconstructed from
anywhere else. An insurer will not accept "we lost it".

A snapshot is taken daily by the same pass that bills, and on demand at
`POST /api/admin/backups`. Three deliberate choices:

* **SQLite's own online backup, never a file copy.** `cp` on a live
  database can catch a torn page or a WAL that has not been checkpointed,
  and produce a file that looks like a backup right up until the
  afternoon somebody needs it.
* **Every snapshot is verified before it counts** — reopened, integrity
  checked, rows counted. An unread backup is a guess.
* **Retention is by count, oldest first**, so the snapshots cannot fill
  the disk the live database is also on and turn a backup policy into an
  outage.

`CYBERLOGIX_BACKUP_DIR` defaults to a directory beside the database, which
survives a bad migration and not a lost disk; `GET /api/admin/backups`
says so out loud when that is the case. Copy them off the machine on a
schedule — a backup that only exists next to the thing it is backing up
is not one.

## The dead man's switch

The product's whole promise is that something runs when nobody is
looking. Nothing checked that it was. A process that died at 2am took
the sweep with it — no escalation, no invoices — and every customer's
console still said ONLINE, because the console reads the database and
the database was fine. A monitoring product that fails silently is worse
than none: the customer has stopped checking the freezer themselves.

The hard part is that **nothing inside a process can report its own
death**. So there are two halves, and only the second one survives the
machine going away:

* **A heartbeat, published.** Each sweep writes down that it happened,
  and `GET /api/watchdog` answers **503** once that has gone stale. The
  status code is the whole interface, because the free tier of every
  uptime service and a one-line cron running `curl -f` understand a
  status code and nothing else. It is unauthenticated and carries
  timings only — no tenant counts, no revenue — so it is safe in a
  stranger's logs, and nobody has to hand a third party the platform key
  to check we are alive.
* **A ping, outbound.** After every successful sweep the app calls
  `CYBERLOGIX_HEARTBEAT_URL`. The alarm is the *absence* of that call,
  armed by a service that is not this one.

Between them sits the sneakiest failure of the three: the web process
serving requests perfectly while the sweep task inside it has died.
Everything looks alive, `/api/health` is 200, and no alarm has been
raised in six hours. The heartbeat is written by the sweep and never by
a request handler precisely so that this state is visible, and there is
a test that hammers `/api/health` and asserts the watchdog still says
503.

Staleness scales with the configured interval — three missed sweeps,
floored at four minutes — because an alarm that fires on one slow
SQLite checkpoint gets muted, and a muted alarm is the same as no alarm.

Verified against a live process: booted and answered 503 before the
first sweep, 200 after, three pings landed on a real switch, and when
the process was killed the endpoint stopped answering entirely while the
pings stopped dead — which is exactly the state the external service
alarms on.

## Download everything, delete everything

An owner has two buttons on the Setup tab. **Download everything** gives one
JSON file of every record held for the business, with passwords, keys and
tokens left out (`GET /api/accounts/business/export`). **Delete everything**
removes the business, its sensors, readings, people, incidents, mail and
records from the live database at once (`POST /api/accounts/business/erase`).

It asks for the business's name typed out and the owner's password, and it
refuses while a bill is unpaid, so it is never a way to make a debt vanish.
The bills already sent are kept, because a seller has to keep its bills.
A person who also works at another business keeps that one: their sign-in
moves to it. Daily backups hold a copy until they age out, and the privacy
statement says how long. The customer's version of all this:
[docs/YOUR-DATA.md](YOUR-DATA.md).

(There used to be a "Loss Assurance" guarantee here. It was withdrawn; the
claim packet still helps a customer claim on their own insurer.)

## The agreements

`/legal` serves the Master Subscription Agreement, the Service Level
Commitment, the privacy and data-processing
statement, and the acceptable-use policy. Readable without an account,
because terms nobody can read before signing up are a surprise rather than
terms.

They are written in plain words and generated from the code they
describe: the alert times from `domain.py`, the payment terms and late
charge from the billing code, the delinquency threshold from
`contracts.py`, and the backup window from `backup.py`. Change a constant and the
agreement says the new figure the same day, and the SHA-256 of the text
moves so a customer can tell a rewording from a re-pricing.

The clause that matters is the limitation of liability: twelve months of
fees. A cryostorage customer's tank is worth more than this company will
ever earn, and uncapped, one claim ends the company rather than the
contract.

Acceptance records the SHA-256 of the exact text against the tenant's
audit trail, at sign-up and through `POST /api/legal/accept`. An
acceptance stops reading as current once the text moves, so the honest
thing — asking again — is the visible thing.

**Every document is a draft.** It is generated from the running system,
not reviewed by a lawyer, and it says so on its own first line. A
liability cap that turns out to be unenforceable is worse than none,
because it was relied on.

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
so a second replica escalates every incident twice. To scale out, replace
the `Database` class with a Postgres adapter (and move the in-memory working
set behind it), then set `CYBERLOGIX_SWEEP_SECONDS=0` and drive
`POST /api/autopilot/sweep-all` from Cloud Scheduler. Nothing above `db.py`
writes SQL, but `store.py` holds every record in memory and treats that copy
as the truth, so it has to read through the shared database too. The adapter
needs `put`, `get`, `delete`, `all`, `count` and `clear`.

`--min-instances 0` means an idle deployment costs nothing but storage.

## Before the first customer

Things this repository does not do for you, in the order they will bite:

- **Twilio is not live.** Until the three variables are set, every alert is
  composed, recorded and never sent. `/api/health` says `dry_run`, and the
  console shows a banner.
- **Card payments need Stripe set up.** Invoices are issued, chased and
  matched to payments arriving at the Stripe webhook; until
  `STRIPE_WEBHOOK_SECRET` and a payment link are set ([DEPLOY.md](../DEPLOY.md), "Before a
  paying customer"), payments are recorded by hand.
- **Backups stay on the server.** A verified snapshot is taken daily, but
  on the same disk; copying them somewhere else is yours to arrange
  ([DEPLOY.md](../DEPLOY.md) section 7).
- **The company is not incorporated yet.** CyberLogix is a product of MTRX
  Horizyn, so `CYBERLOGIX_LEGAL_NAME` and the other issuer fields are MTRX
  Horizyn's registered details; they go on every invoice, and an invoice
  from an entity that cannot receive money is not a document anyone can
  pay.
- **The agreements have not been read by a lawyer.** They are generated
  from the running system so the figures cannot drift, and every one of
  them says "draft" on its first line. The limitation of liability is the
  clause to have checked first: one that turns out to be unenforceable is
  worse than none, because it was relied on.
- **`CYBERLOGIX_PROVISIONING_KEY` is unset.** You cannot create a paid
  account directly yourself until it is. Customers are not blocked: an owner
  can still move off the trial from the console and be invoiced.
