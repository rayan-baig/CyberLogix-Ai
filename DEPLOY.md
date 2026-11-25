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

**The disk is 1 GB to start. Make it 5 GB before 400 sensors.** The
database plus fourteen daily backups is about fifteen times the database,
and on 1 GB that runs out at roughly 450 sensors. When a backup would
leave less than 100 MB free it is skipped and reported (`/book → Health`)
rather than filling the disk, but by then you are a day from trouble.
Render: the service → Disks → change the size. A disk can be made bigger,
not smaller. The measurements are in
[docs/LOAD-TEST.md](docs/LOAD-TEST.md#disk).

A second copy for trying changes first is in
[docs/STAGING.md](docs/STAGING.md).

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

**Kryozyn has no domain of its own.** It is a product of MTRX Horizyn,
and MTRX Horizyn owns the one domain: its own site on the apex (and
`www`), each product on a subdomain. Kryozyn lives at
`kryozyn.mtrxhorizyn.com`, beside `rankup.mtrxhorizyn.com` and
`omniarmor.mtrxhorizyn.com`; MTRX Horizyn's own site is `mtrxhorizyn.com`.

A subdomain, not a path (`mtrxhorizyn.com/kryozyn`), on purpose: each
subdomain is its own origin, so a sign-in, the browser's storage and the
installed app's service worker belong to Kryozyn alone and never
reach a sibling. Keep it that way — the app sets no cookie for the
parent domain and, unless `CYBERLOGIX_ALLOWED_ORIGINS` says otherwise,
lets no other site (siblings included) call its API from a browser.

1. **DNS.** At whoever holds the domain's DNS, add one record for the
   subdomain: a `CNAME` named `kryozyn` pointing at the address the
   host gave you (Render: `cyberlogix.onrender.com` or similar: the
   service keeps its old Render name, which nobody sees), or an
   `A` record to the server's IP if you run the container yourself.
2. **The host's custom-domain step.** Tell the host the name it will
   answer to. Render: the service → Settings → Custom Domains → add
   `kryozyn.mtrxhorizyn.com`, then wait for it to verify. Your own server:
   name `kryozyn.mtrxhorizyn.com` in the proxy (Caddy, nginx) in front of
   the container.
3. **HTTPS.** Render issues the certificate itself once DNS resolves;
   Caddy does the same. Do not go on until `https://kryozyn.mtrxhorizyn.com`
   loads with a padlock — sensors, Twilio's callback and the installed app
   all need it.
4. **Point the app at its own address.** `PUBLIC_BASE_URL` is
   `https://kryozyn.mtrxhorizyn.com` (no trailing slash), then redeploy.
   Leave `CYBERLOGIX_ALLOWED_ORIGINS` unset unless a page on another
   origin genuinely has to call this API; then name that exact origin.
   Neither Kryozyn's own console (served from this same address) nor
   MTRX Horizyn's console (which asks `/api/health` and `/api/stats` from
   its server, not from a browser) needs it.
   Never `*`, and never the parent domain or a sibling as a convenience.
5. **MTRX Horizyn.** In MTRX Horizyn's settings, Kryozyn's address is
   `https://kryozyn.mtrxhorizyn.com` and its stats address is
   `https://kryozyn.mtrxhorizyn.com/api/stats` (MTRX Horizyn's README names
   the two settings; `STATS_KEY` here matches its stats key setting (`MTRX_STATS_KEY`
   there, a name kept from before the rename, unless that site has renamed it).

### The names, in one place

The product's name, the company's name, its domain, the app's address and
the checklist app's name are set once, in `brand.py`, and every page,
email, text, agreement and Pilot answer takes them from there
(`tests/test_brand.py` fails if anything else spells them). Each can also
be set without a code change: `CYBERLOGIX_APP_NAME`,
`CYBERLOGIX_COMPANY_NAME`, `CYBERLOGIX_COMPANY_DOMAIN` (the app's address
follows it: `kryozyn.<domain>`), `CYBERLOGIX_APP_HOST`,
`CYBERLOGIX_CHECKLIST_APP` and `CYBERLOGIX_SUPPORT_EMAIL`. The company's
new name is still being chosen; when it is, the change is the two company
defaults in `brand.py` (and the outside steps below for the new domain).

### The old address keeps working

Kryozyn was CyberLogix AI, at `cyberlogix.mtrxhorizyn.com` (and
`staging.cyberlogix.mtrxhorizyn.com`). Keep those names pointing at the
service for as long as anything might still use them: their DNS records
and their Render custom domains stay exactly as they are. The app then
does the rest by itself (`formerly.py`):

* A page opened at the old address (the front page, the console, a proof
  link in an old email, a QR code on a printed sheet) moves to the same
  page at `kryozyn.mtrxhorizyn.com` with a permanent (301) redirect.
* Anything under `/api/`, and every request that is not a plain page visit
  (sensor readings, Twilio's callbacks, Stripe's webhooks), is answered at
  the old address as before, so no sensor, phone number or payment set up
  against it breaks while you move them over.
* `KRYOZYN_OLD_HOSTS` changes the list (`old=new,old2=new2`); set it to
  an empty value to switch the redirect off.

### Settings and headers: the old names still work

Every setting keeps its `CYBERLOGIX_` name, and the values already in the
dashboard keep working with no change. Each may also be set as `KRYOZYN_`
plus the same rest (`KRYOZYN_ADMIN_KEY` for `CYBERLOGIX_ADMIN_KEY`); when
both are set, the `KRYOZYN_` one wins. The API's headers are now
`X-Kryozyn-Key`, `X-Kryozyn-Sensor-Key`, `X-Kryozyn-Admin` and so on;
devices and scripts that send the old `X-CyberLogix-` names keep working,
and when a request sends both, the new one wins. The daily report to
OmniArmor carries its signature under both `X-Kryozyn-Signature` and the
`X-CyberLogix-Signature` OmniArmor has read until now. The Render service,
its disk, the database file, the backup file names and this repository
keep their old names: renaming any of them would mean a new service or a
lost disk, and nobody outside sees them.

### The rename: what the owner does outside the code

The code is renamed. These steps are not code, so only the owner can do
them. In this order:

1. **DNS.** On `mtrxhorizyn.com`, add a `CNAME` named `kryozyn` (and
   `staging.kryozyn` for the staging copy) pointing at the same
   `onrender.com` address as the `cyberlogix` record. Leave the
   `cyberlogix` records in place.
2. **Render custom domains.** On the service add
   `kryozyn.mtrxhorizyn.com`; on the staging service add
   `staging.kryozyn.mtrxhorizyn.com`. Keep the old names on both. Wait for
   each certificate (the padlock).
3. **The app's own address.** Once
   `https://kryozyn.mtrxhorizyn.com/api/health` loads with a padlock, set
   `PUBLIC_BASE_URL=https://kryozyn.mtrxhorizyn.com` (staging:
   `https://staging.kryozyn.mtrxhorizyn.com`) and redeploy. If
   `CYBERLOGIX_ALLOWED_ORIGINS` names the old address, change it.
4. **Mail.** With the mail provider, verify the sending name
   `kryozyn.mtrxhorizyn.com` (it adds its DKIM record; SPF and DMARC stay
   on the parent domain, section "Mail, from the one domain" below). Then
   set `MAIL_FROM=no-reply@kryozyn.mtrxhorizyn.com` and
   `CYBERLOGIX_SUPPORT_EMAIL=help@kryozyn.mtrxhorizyn.com` (make that
   mailbox, or forward it). If `MAIL_FROM_NAME` is set to the old name,
   set it to `Kryozyn` or remove it (Kryozyn is the default). Send yourself
   a test invoice and check it is not in spam.
5. **Stripe.** Add the webhook endpoint
   `https://kryozyn.mtrxhorizyn.com/api/payments/stripe` with the same
   events, put its new signing secret in `STRIPE_WEBHOOK_SECRET`, then
   remove the old endpoint (the old address keeps working until then).
   Change the public business name and statement descriptor to Kryozyn,
   and the product names on any payment links (`CYBERLOGIX_PAY_URL`).
6. **Twilio.** Point the number's messaging and voice webhooks at
   `https://kryozyn.mtrxhorizyn.com/...` (the same paths; section 3,
   "Twilio"). Change the caller name (CNAM) and the messaging service's
   name to Kryozyn, and update the A2P 10DLC campaign description (Kryozyn
   alerts), which the carriers review.
7. **Everything else that knows the old address.** The uptime monitor on
   `/api/health`, the company console's Kryozyn address and stats address,
   Google Search Console (add the new address), and any sensor maker's
   cloud set up with the old address (it keeps working; move it when
   convenient). Customers' Kitchen Safety Bundle connections keep the
   OmniArmor address they pasted, which keeps working for as long as that
   old address does.
8. **When the company's new name is chosen.** Change `COMPANY_NAME` and
   `COMPANY_DOMAIN` in `brand.py` (or set `CYBERLOGIX_COMPANY_NAME` and
   `CYBERLOGIX_COMPANY_DOMAIN`), add the new address to
   `KRYOZYN_OLD_HOSTS`' targets if it moves, and repeat steps 1 to 7 for
   the new domain (buy it; DNS; SPF, DKIM and DMARC for the new mail
   domain; Render; Stripe's business name; Twilio's brand registration).
   If `CYBERLOGIX_LEGAL_NAME` is set, change it too.
9. **Later.** The GitHub repositories keep their names (historical; the
   demo on GitHub Pages keeps its address). Store listings and code
   signing for an installable app, if one is ever made, use the new name.
   When nothing uses the old addresses any more, remove them.

### Mail, from the one domain

Every MTRX Horizyn product sends mail as the one domain, so the mail
records are set up once, on the parent domain, by whoever runs it — not
once per app. Your mail provider gives you the exact values:

| Record | Purpose |
|---|---|
| **SPF** (`TXT`) | says which servers may send as you |
| **DKIM** (`TXT`) | signs each message so it cannot be forged |
| **DMARC** (`TXT`) | says what to do with mail that fails the other two |

Kryozyn then sends from an address of its own on that domain, so a
customer (and a spam filter) can tell the products apart:
`MAIL_FROM=no-reply@kryozyn.mtrxhorizyn.com`. Customers write for help
to `help@kryozyn.mtrxhorizyn.com` (`CYBERLOGIX_SUPPORT_EMAIL`). A sending
subdomain like `kryozyn.mtrxhorizyn.com`
needs to be verified with the mail provider too (it adds its DKIM record
there), and is covered by the parent's DMARC policy. Keep
`MAIL_FROM_NAME=Kryozyn`; every message already says in its footer
that Kryozyn is a product of MTRX Horizyn.

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
`POST /api/autopilot/sweep-all` with the `X-Kryozyn-Admin` header every
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
print("CYBERLOGIX_PROOF_REPORT_KEY=" + secrets.token_urlsafe(32))
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
# Seals proof of monitoring reports; keep it once set (changing it makes
# every report made before read "changed" on its check page).
CYBERLOGIX_PROOF_REPORT_KEY=<generated above>
# Leave CYBERLOGIX_ALLOWED_ORIGINS unset: every page is served from this
# same origin. See section 1 before naming one.
PUBLIC_BASE_URL=https://kryozyn.mtrxhorizyn.com

TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=

CYBERLOGIX_HEARTBEAT_URL=

SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
MAIL_FROM=no-reply@kryozyn.mtrxhorizyn.com
# Where /help tells customers to write. Unset, it uses MAIL_REPLY_TO;
# with neither, it tells them to use the contact on their invoice.
CYBERLOGIX_SUPPORT_EMAIL=help@kryozyn.mtrxhorizyn.com
# You: the daily digest of what was billed, what arrived and what broke.
CYBERLOGIX_OPERATOR_EMAIL=
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

### Twilio: the number, and where it sends replies

Buy a number that can send texts and make calls, and put the account
SID, the auth token and the number in `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN` and `TWILIO_FROM_NUMBER`. Without all three nothing
is sent: every alert is still written and recorded, as a dry run.

Then tell Twilio where a **reply** to an alert text goes. In the Twilio
console: Phone Numbers → your number → Messaging → "A message comes in"
→ Webhook, method `POST`:

    https://kryozyn.mtrxhorizyn.com/api/voice/sms-reply

That is what makes "Reply 1" work. Without it a reply of 1 goes nowhere
and the calls carry on as if nobody had answered. It needs two settings
right: `TWILIO_AUTH_TOKEN`, because a reply is only believed if Twilio
signed it, and `PUBLIC_BASE_URL`, exactly as the address above starts,
because the signature covers the address Twilio was given.

The phone call needs no setting in Twilio: each call tells Twilio where
to send "press 1", built from `PUBLIC_BASE_URL`.

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

Then two more, ten minutes in all (step by step in
[docs/OPERATIONS.md](docs/OPERATIONS.md#if-the-app-is-down)):

- Set `CYBERLOGIX_OPERATOR_EMAIL` and `CYBERLOGIX_OPERATOR_PHONE` to your
  own email and mobile. The app emails and texts you when readings stop
  arriving, the alert job stops, or texts, calls or alert emails start
  failing, and again when each is fixed.
- Add a free uptime monitor (UptimeRobot or Better Stack) on
  `https://<your address>/readyz`, alerting when it is not 200, and a
  second one on `/healthz`. Keep the platform's own health check on
  `/api/health`.

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
6. Do it again, and this time reply `1` to the text instead. The calls
   must not start, and the reply must come back naming who has it.

Steps 5 and 6 are the ones that need `PUBLIC_BASE_URL` set correctly,
and step 6 also needs the Twilio webhook above. Without them the call
still goes out and pressing 1 or replying 1 reaches nothing.

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
curl -s -H "X-Kryozyn-Admin: $KEY" \
  https://kryozyn.mtrxhorizyn.com/api/admin/backups
```

Pick a snapshot and verify it:

```
curl -s -H "X-Kryozyn-Admin: $KEY" \
  https://kryozyn.mtrxhorizyn.com/api/admin/backups/cyberlogix-....sqlite3/verify
```

Then restore it from `/book → Health`, or:

```
curl -s -X POST -H "X-Kryozyn-Admin: $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"snapshot":"cyberlogix-....sqlite3","confirm":true}' \
  https://kryozyn.mtrxhorizyn.com/api/admin/backups/restore
```

It verifies the snapshot, snapshots the database it is about to replace,
copies the snapshot in through SQLite and reloads in place — no restart.
Every write made since the snapshot is discarded, which is why `confirm`
is required and there is no "latest" — except the record of what was
already sent (incidents, mail), so nothing goes out twice. Rehearse on a
copy, never on production, and never with the production settings: the
full ten-minute checklist, for Render and Docker and for a lost disk, is
in [docs/OPERATIONS.md](docs/OPERATIONS.md#restore-checklist-about-ten-minutes).
In short: find the snapshot, put it where the app can see it, verify it,
restore it, then check the health page, one sensor's readings and one
test alert. `python tools/restore_rehearsal.py` does every step on a copy
and times it.

**Copy the snapshots off the machine on a schedule.** Nothing in this
application does that for you, and it is the difference between surviving
a bad migration and surviving a lost disk. A cron job elsewhere can fetch
each one:

```
curl -fsS -H "X-Kryozyn-Admin: $KEY" -o cyberlogix-....sqlite3 \
  https://kryozyn.mtrxhorizyn.com/api/admin/backups/cyberlogix-....sqlite3/download
```

---

## 8. What happens on its own, once it is up

- **The sweep** escalates breaches, hourly passes bill and chase.
- **A dead sweep loop restarts itself**, with backoff, and records why.
- **Unhandled errors are written down**, deduplicated, and counted —
  visible at `/book → Health` and `GET /api/admin/faults`.
- **A daily digest** to `CYBERLOGIX_OPERATOR_EMAIL` says what was
  billed, what arrived, who needs a call, and what broke.
- **A monthly summary to each business's owners**, in the first week of
  the month, after the digest hour (`CYBERLOGIX_DIGEST_HOUR_UTC`): their
  units, every time something went out of range and who answered, and a
  link to the month's inspector log. Once per business, even across a
  restart or a restore. It needs mail, and `PUBLIC_BASE_URL` for the
  link. To send it now: `/book → Health` → "Send last month's summaries
  to owners".
- **Daily verified backups**, pruned to `CYBERLOGIX_BACKUP_KEEP`.
- **Rotations and quiet hours** run on the sweep. There is nothing to set
  on the server: an owner sets them on the console's People tab, in each
  rotation's own time zone. The time-zone rules come from the `tzdata`
  package in `requirements.txt`, so a rebuilt image picks up a country's
  new clock rules.

None of this fixes a bug in the code. No program repairs its own logic.
What it does is make sure the next bug arrives as a dated report with a
traceback and a count, instead of as a customer asking why.

---

## 9. Which version is running

The release number is in the `VERSION` file, and `CHANGELOG.md` says
what changed in it, in plain words. The image will not build without
both. After a deploy, check the live copy is the one you meant:

```
curl -s https://kryozyn.mtrxhorizyn.com/api/health
```

`version` in the answer is the `VERSION` file the image was built from.
Every change that ships bumps `VERSION` and adds a `CHANGELOG.md` entry
together; a test fails if they disagree.

---

## 10. A demo account, never on the live database

For a sales laptop or a staging copy, seed a made-up business into a
**new, empty** database:

```
python demo_account.py --db /tmp/cyberlogix-demo.db
CYBERLOGIX_DB_PATH=/tmp/cyberlogix-demo.db python main.py
```

It makes a restaurant group with three sites, twenty units, a week of
readings and an open alert, and prints the sign-ins. It refuses a
database with anything in it, so it cannot be run into the live one by
mistake. It mails nobody, and its backups go to `demo-backups/` beside
the database, never to `CYBERLOGIX_BACKUP_DIR`. Running a whole second
copy on Render is [docs/STAGING.md](docs/STAGING.md).

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
  These are the entity's details, not the software's. Kryozyn is a
  product of MTRX Horizyn, so they are MTRX Horizyn's: set
  `CYBERLOGIX_LEGAL_NAME` to its registered name exactly as registered
  (unset, invoices and the agreements say "MTRX Horizyn").
- **`STRIPE_WEBHOOK_SECRET`**, if you take cards. Unset, the webhook
  refuses everything — which is correct, because an unsigned payment
  webhook that trusts its body is a button for marking every invoice in
  the system paid.
  In Stripe: Developers → Webhooks → add endpoint
  `https://kryozyn.mtrxhorizyn.com/api/payments/stripe`, subscribe it to
  **`checkout.session.completed`** (what a payment link produces), and copy
  its signing secret here. Make the payment link pass the invoice number as
  `?client_reference_id={invoice}` (that is what `CYBERLOGIX_PAY_URL` is for)
  so each payment finds its invoice; anything that cannot be matched waits at
  `/api/payments/unmatched` rather than being guessed at.
