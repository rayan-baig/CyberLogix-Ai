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

Buy the domain, point it at the host, then add three records. Your mail
provider gives you the exact values:

| Record | Purpose |
|---|---|
| **SPF** (`TXT`) | says which servers may send as you |
| **DKIM** (`TXT`) | signs each message so it cannot be forged |
| **DMARC** (`TXT`) | says what to do with mail that fails the other two |

Without all three, `MAIL_FROM` is an address the receiving server has no
reason to trust, and the collections ladder in `mail.py` will run
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

**One worker, on purpose.** The sweep runs in-process, so a second
worker escalates the same incident twice. To scale out, set
`CYBERLOGIX_SWEEP_SECONDS=0` and drive `POST /api/autopilot/sweep` from
an external scheduler instead.

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
CYBERLOGIX_ADMIN_KEY=<generated above>
CYBERLOGIX_ALLOWED_ORIGINS=https://your.domain
PUBLIC_BASE_URL=https://your.domain

TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=

CYBERLOGIX_HEARTBEAT_URL=
```

`.env` holds the credential that reads every customer's contact details.
It is not a file to commit, paste into a chat, or email to yourself.

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
enough for a load balancer, but only while there is a process left to
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
  https://your.domain/api/admin/backups
```

Pick a snapshot, verify it, then restore it from `/book → Health`, or:

```
curl -s -X POST -H "X-CyberLogix-Admin: $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"snapshot":"cyberlogix-....sqlite3","confirm":true}' \
  https://your.domain/api/admin/backups/restore
```

It verifies the snapshot, snapshots the database it is about to replace,
swaps the file, and reopens and reloads in place — no restart. Every
write made since the snapshot is discarded, which is why `confirm` is
required and there is no "latest".

**Copy the snapshots off the machine on a schedule.** Nothing in this
application does that, and it is the difference between surviving a bad
migration and surviving a lost disk.

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
  These are the entity's details, not the software's.
- **`STRIPE_WEBHOOK_SECRET`**, if you take cards. Unset, the webhook
  refuses everything — which is correct, because an unsigned payment
  webhook that trusts its body is a button for marking every invoice in
  the system paid.
