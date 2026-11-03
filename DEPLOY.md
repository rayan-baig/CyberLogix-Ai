# Going live

Four things run this application: a container, a disk, a phone line and
a mail relay. Everything below is one of those.

Run `python readiness.py` at any point. It prints what is still blocking
and exits non-zero while anything is, so it can gate a deploy script.
The same thing is at `/book → Health` and `GET /api/admin/readiness`.

---

## The short way up

`render.yaml` is this document's sections 2 and 3 as a file: the
container, the disk, the generated secrets. Render reads it and hands
back an `https://` address, which is all a sensor needs to report to.

    render.com -> New -> Blueprint -> pick this repository

It declines the free instance type on purpose, and the file says why:
free has no disk and sleeps after fifteen idle minutes, so the database
dies on each deploy and no sweep runs overnight. Roughly $7 a month.

That gets a *monitoring* launch up with no domain and no Twilio — enough
to point real hardware at and watch a reading arrive. Everything below
still applies before a customer depends on it, and sections 1, 4 and 5
are the ones that turn it from reachable into trustworthy.

---

## Decide what you are launching

The readiness check answers two questions, not one, because they have
different answers and very different lead times.

**A monitoring launch** — real customers, real sensors, real alerts, no
money moving. Needs a host, a domain, Twilio and a heartbeat. Achievable
in a few days.

**Taking money** — needs, on top of that, an incorporated entity with a
tax ID and a bank account, and a lawyer who has read the agreements.
Not achievable in a few days, and not a software problem.

There is no switch that makes a deployment trials-only. Any owner can
move their own account off the trial from the console and sign a
contract, whether or not `CYBERLOGIX_PROVISIONING_KEY` is set -- on
purpose, so a finished trial can pay without somebody answering an
email. The key only lets the operator create a paid account directly.
An earlier version of this page said leaving it unset meant no paid
account could be created at all. That was never true. If money must
not move yet, the money checks in `readiness.py` are what to watch.

---

## 1. The domain, first

Do this before anything else. Not because it is hard, but because it is
the only step with a lead time measured in days — DNS propagates slowly
and mail reputation builds slowly. Done on the last day, invoices land
in spam for a fortnight.

**CyberLogix has no domain of its own.** It is a product of MTRX Horizyn,
and MTRX Horizyn owns the one domain: its own site on the apex (and
`www`), each product on a subdomain. CyberLogix lives at
`cyberlogix.<your domain>`, beside `rankup.<your domain>` and
`omniarmor.<your domain>`. Below, `example.com` stands for that domain.

A subdomain, not a path (`example.com/cyberlogix`), on purpose: each
subdomain is its own origin, so a sign-in, the browser's storage and the
installed app's service worker belong to CyberLogix alone and never
reach a sibling. Keep it that way — the app sets no cookie for the
parent domain and, unless `CYBERLOGIX_ALLOWED_ORIGINS` says otherwise,
lets no other site (siblings included) call its API from a browser.

1. **DNS.** At whoever holds the domain's DNS, add one record for the
   subdomain: a `CNAME` named `cyberlogix` pointing at the address the
   host gave you (Render: `cyberlogix.onrender.com` or similar), or an
   `A` record to the server's IP if you run the container yourself.
2. **The host's custom-domain step.** Tell the host the name it will
   answer to. Render: the service → Settings → Custom Domains → add
   `cyberlogix.example.com`, then wait for it to verify. Your own server:
   name `cyberlogix.example.com` in the proxy (Caddy, nginx) in front of
   the container.
3. **HTTPS.** Render issues the certificate itself once DNS resolves;
   Caddy does the same. Do not go on until `https://cyberlogix.example.com`
   loads with a padlock — sensors, Twilio's callback and the installed app
   all need it.
4. **Point the app at its own address.** `PUBLIC_BASE_URL` is
   `https://cyberlogix.example.com` (no trailing slash), then redeploy.
   Leave `CYBERLOGIX_ALLOWED_ORIGINS` unset unless a page on another
   origin genuinely has to call this API; then name that exact origin.
   Never `*`, and never the parent domain or a sibling as a convenience.
5. **MTRX Horizyn.** In MTRX Horizyn's settings, CyberLogix's address is
   `https://cyberlogix.example.com` and its stats address is
   `https://cyberlogix.example.com/api/stats` (MTRX Horizyn's README names
   the two settings; `STATS_KEY` here matches its `MTRX_STATS_KEY`).

### Mail, from the one domain

Every MTRX Horizyn product sends mail as the one domain, so the mail
records are set up once, on the parent domain, by whoever runs it — not
once per app. Your mail provider gives you the exact values:

| Record | Purpose |
|---|---|
| **SPF** (`TXT`) | says which servers may send as you |
| **DKIM** (`TXT`) | signs each message so it cannot be forged |
| **DMARC** (`TXT`) | says what to do with mail that fails the other two |

CyberLogix then sends from an address of its own on that domain, so a
customer (and a spam filter) can tell the products apart:
`MAIL_FROM=no-reply@cyberlogix.example.com`, or an address such as
`cyberlogix@example.com`. A sending subdomain like `cyberlogix.example.com`
needs to be verified with the mail provider too (it adds its DKIM record
there), and is covered by the parent's DMARC policy. Keep
`MAIL_FROM_NAME=CyberLogix AI`; every message already says in its footer
that CyberLogix is a product of MTRX Horizyn.

Without all three records, `MAIL_FROM` is an address the receiving server
has no reason to trust, and the collections ladder in `mail.py` will run
perfectly while nothing arrives.

Check them with any public DMARC checker before you send a real invoice.

---

## 2. The container

The `Dockerfile` targets Cloud Run and is plain enough for anything that
runs a container.

```
docker build -t cyberlogix .
docker run -d --restart=unless-stopped \
  -p 8080:8080 \
  -v /srv/cyberlogix/data:/app/data \
  -v /srv/cyberlogix/backups:/srv/backups \
  --env-file /srv/cyberlogix/.env \
  cyberlogix
```

Three things matter here:

- **`--restart=unless-stopped`.** The application supervises its own
  sweep loop and reopens its own database, but nothing inside a process
  restarts the process. That is the container runtime's job.
- **A volume at `/app/data`.** Without it the database dies with the
  container.
- **A *second* volume for backups**, on a different disk. A snapshot
  beside the thing it is backing up survives a bad migration and not a
  lost disk.

Point the host's health check at `GET /api/health`, never at
`/api/watchdog`. The watchdog answers 503 on purpose when the sweep has
gone quiet; read as "unhealthy" by a platform, that becomes a restart
loop which hides the very stall it reports. Use the watchdog for an
uptime monitor that tells a person (section 4).

**One process, on purpose.** The application keeps its working set in
memory and writes through to SQLite, and the sweep runs in-process. A
second worker or a second instance would hold its own copy of every
record and escalate the same incident again, so neither the sweep setting
below nor anything else makes more than one safe. Growing past one
process means replacing `db.py` with a shared database first.

**Driving the sweep from outside instead** (a platform that sleeps idle
containers, or a scheduler you already run): set
`CYBERLOGIX_SWEEP_SECONDS=0` and have the scheduler call
`POST /api/autopilot/sweep-all` with the `X-CyberLogix-Admin` header every
few minutes, and set `CYBERLOGIX_EXTERNAL_SWEEP_SECONDS` to how often, so
the watchdog knows when it has stopped. That route sweeps every business
and, once an hour, starts the money pass in the background (invoices,
chasing, backups, digests, the mail queue), which the built-in loop
otherwise runs. Not `/api/autopilot/sweep`: that one only sweeps the
business that calls it.

---

## 3. The secrets

Generate the two of your own, and never type them by hand:

```
python - <<'PY'
import secrets
print("CYBERLOGIX_ADMIN_KEY=" + secrets.token_urlsafe(32))
print("CYBERLOGIX_MAIL_SECRET=" + secrets.token_urlsafe(32))
PY
```

Then copy `.env.example` to `.env` and fill in, at minimum:

```
CYBERLOGIX_DB_PATH=/app/data/cyberlogix.db
CYBERLOGIX_BACKUP_DIR=/srv/backups
# Behind any proxy (Caddy, nginx, a load balancer): so each visitor is counted
# as themselves for sign-in limits, not all as the proxy. Render: already set.
CYBERLOGIX_TRUST_PROXY_HEADER=1
CYBERLOGIX_ADMIN_KEY=<generated above>
CYBERLOGIX_MAIL_SECRET=<generated above>
# Leave CYBERLOGIX_ALLOWED_ORIGINS unset: every page is served from this
# same origin. See section 1 before naming one.
PUBLIC_BASE_URL=https://cyberlogix.example.com

TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=

CYBERLOGIX_HEARTBEAT_URL=

SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
MAIL_FROM=no-reply@cyberlogix.example.com
```

Mail is what a locked-out owner's password-reset link travels by, as
well as every invoice. Without `SMTP_HOST` and `MAIL_FROM` those are
written down and queued, not sent; set them within 72 hours and the queue
goes out, after that it is dropped.

`.env` holds the credential that reads every customer's contact details.
It is not a file to commit, paste into a chat, or email to yourself.

**One proxy, not two.** With `CYBERLOGIX_TRUST_PROXY_HEADER=1` the app takes
the visitor's address from the last entry in `X-Forwarded-For`, the one your
own proxy (or Render's) added. If you ever put a second proxy in front, such
as Cloudflare, that last entry becomes Cloudflare's address, every visitor
looks like the same few addresses, and one stranger's wrong passwords can
lock everyone out. Change how the address is read before adding one.

---

## 4. The dead man's switch

The product's promise is that something runs when nobody is looking.
Nothing inside a process can report its own death, so the alarm has to
live outside it.

Sign up for any dead-man's-switch service — they are free at this size
and all of them take a plain `GET`. Put the URL in
`CYBERLOGIX_HEARTBEAT_URL`. The application pings it after every
successful sweep; the service alarms when the pings stop.

`GET /api/watchdog` answers 503 when the sweep has gone quiet, which is
enough for an uptime monitor that pages a person (not for the platform's
health check: see section 2), but only while there is a process left to
answer at all. That is the case the heartbeat exists for.

---

## 5. Prove it end to end

Not "the site loads". One real sensor, one real breach, one real phone.

1. Sign up a trial through the real front door at `/signup`.
2. Register one sensor, or point an off-the-shelf one at the BYOD
   webhook — Elitech, Dickson, Monnit and SensorPush all POST JSON, and
   `hardware_bridge.py` takes it raw.
3. Take the sensor out of its limits for real. A warm hand on a probe is
   enough.
4. Confirm the sequence: the reading lands, the incident opens, the SMS
   arrives, and the call escalates when nobody acknowledges it.
5. Press 1 on the call and confirm the incident acknowledges.

Step 5 is the one that needs `PUBLIC_BASE_URL` set correctly. Without
it the call still goes out and pressing 1 reaches nothing.

Until you have done this once, with real hardware and a real handset,
the product is untested where it counts.

---

## 6. Putting it on a phone

There is no app store step, and that is deliberate. The App Store and
Play Store take **15–30% of every subscription** and put a review queue
between a fix and the customer — for a wrapper around these same pages.
Being a website is what avoids that cut, and this keeps it a website
while still giving people an app.

Once the site is live over HTTPS, a customer installs it from the
browser:

- **iPhone / iPad:** open the console in Safari → Share → *Add to Home
  Screen*. (Safari only — Chrome on iOS cannot install it.)
- **Android:** Chrome offers *Install app* in the menu, or prompts.
- **Desktop:** Chrome and Edge show an install icon in the address bar.

It then opens in its own window with its own icon, no browser chrome.

**It only works over HTTPS.** A service worker is refused on plain HTTP
everywhere except localhost, so nothing installs until the certificate
is in place.

One thing this app deliberately does *not* do offline: show you a
temperature. A cached dashboard answering "everything is fine" to
somebody with no signal is the exact failure the product is sold to
prevent. With no connection it says so, in red, and dims every figure on
the screen. Monitoring and escalation run on the server and are
unaffected — the text and the call still go out whether or not anybody's
phone can reach anything.

---

## 7. Restoring, before you need to

A backup nobody has restored is a file, not a backup. Do this once now,
on purpose, so the first time is not during an incident:

```
curl -s -H "X-CyberLogix-Admin: $KEY" \
  https://cyberlogix.example.com/api/admin/backups
```

Pick a snapshot and verify it:

```
curl -s -H "X-CyberLogix-Admin: $KEY" \
  https://cyberlogix.example.com/api/admin/backups/cyberlogix-....sqlite3/verify
```

Then restore it from `/book → Health`, or:

```
curl -s -X POST -H "X-CyberLogix-Admin: $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"snapshot":"cyberlogix-....sqlite3","confirm":true}' \
  https://cyberlogix.example.com/api/admin/backups/restore
```

It verifies the snapshot, snapshots the database it is about to replace,
copies the snapshot in through SQLite and reloads in place — no restart.
Every write made since the snapshot is discarded, which is why `confirm`
is required and there is no "latest" — except the record of what was
already sent (incidents, mail), so nothing goes out twice. Rehearse on a
copy, never on production, and never with the production settings: the
full ten-minute checklist, for Render and Docker and for a lost disk, is
in [docs/OPERATIONS.md](docs/OPERATIONS.md#restore-checklist-about-ten-minutes).

**Copy the snapshots off the machine on a schedule.** Nothing in this
application does that for you, and it is the difference between surviving
a bad migration and surviving a lost disk. A cron job elsewhere can fetch
each one:

```
curl -fsS -H "X-CyberLogix-Admin: $KEY" -o cyberlogix-....sqlite3 \
  https://cyberlogix.example.com/api/admin/backups/cyberlogix-....sqlite3/download
```

---

## 8. What happens on its own, once it is up

- **The sweep** escalates breaches, hourly passes bill and chase.
- **A dead sweep loop restarts itself**, with backoff, and records why.
- **Unhandled errors are written down**, deduplicated, and counted —
  visible at `/book → Health` and `GET /api/admin/faults`.
- **A daily digest** to `CYBERLOGIX_OPERATOR_EMAIL` says what was
  billed, what arrived, who needs a call, and what broke.
- **Daily verified backups**, pruned to `CYBERLOGIX_BACKUP_KEEP`.

None of this fixes a bug in the code. No program repairs its own logic.
What it does is make sure the next bug arrives as a dated report with a
traceback and a count, instead of as a customer asking why.

---

## Before a paying customer

`python readiness.py` will keep saying these until they are true:

- **A lawyer has read the agreements.** Every document carries a draft
  disclaimer whose own words are *"Do not put it in front of a paying
  customer until one has read it."* Customers are asked to accept them
  by SHA-256, so the acceptance is a real, dated record — of a draft,
  until someone qualified has read it.
- **The invoice says who is asking.** `CYBERLOGIX_LEGAL_NAME`,
  `CYBERLOGIX_ADDRESS`, `CYBERLOGIX_TAX_ID`, `CYBERLOGIX_REMIT_TO`.
  These are the entity's details, not the software's. CyberLogix is a
  product of MTRX Horizyn, so they are MTRX Horizyn's: set
  `CYBERLOGIX_LEGAL_NAME` to its registered name exactly as registered
  (unset, invoices and the agreements say "MTRX Horizyn").
- **`STRIPE_WEBHOOK_SECRET`**, if you take cards. Unset, the webhook
  refuses everything — which is correct, because an unsigned payment
  webhook that trusts its body is a button for marking every invoice in
  the system paid.
  In Stripe: Developers → Webhooks → add endpoint
  `https://cyberlogix.example.com/api/payments/stripe`, subscribe it to
  **`checkout.session.completed`** (what a payment link produces), and copy
  its signing secret here. Make the payment link pass the invoice number as
  `?client_reference_id={invoice}` (that is what `CYBERLOGIX_PAY_URL` is for)
  so each payment finds its invoice; anything that cannot be matched waits at
  `/api/payments/unmatched` rather than being guessed at.
