# Running it in production

Settings, what to do if the app is down, where the state lives, backups,
the dead man's switch, error reports, usage counts, deleting a business, the agreements, and the deployment details behind the short
version in the README. The step-by-step runbook is
[DEPLOY.md](../DEPLOY.md).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | Google GenAI credentials, read by the SDK |
| `TWILIO_ACCOUNT_SID` | — | Twilio account SID; unset means dry-run delivery |
| `TWILIO_AUTH_TOKEN` | — | Twilio auth token |
| `TWILIO_FROM_NUMBER` | — | Sending number in E.164, e.g. `+15550100` |
| `CYBERLOGIX_GEMINI_MODEL` | — | Model for all generated copy, by its name in the provider's current list. Unset, every message uses its template |
| `CYBERLOGIX_AI_TIMEOUT_SECONDS` | `6` | How long an alert waits for the model's line before it goes out with the template line |
| `CYBERLOGIX_ALLOWED_ORIGINS` | none | Exact origins whose pages may call the API from a browser; unset allows only this site's own pages. Never `*` or a sibling subdomain |
| `PORT` | `8080` | Listen port |
| `CYBERLOGIX_DB_PATH` | `cyberlogix.db` | SQLite file; use a mounted volume in production |
| `CYBERLOGIX_CREDENTIAL_KEY` | none | Seals the connection keys businesses paste (the OmniArmor key) with AES-GCM; unset, nothing can be connected. Keep it in the secret store; changing it makes saved keys unreadable |
| `KITCHEN_BUNDLE_SECRET` | none | Signs each daily Kitchen Safety Bundle report to OmniArmor (`X-CyberLogix-Signature`, HMAC-SHA256 over the timestamp and the exact body). The same value as OmniArmor's; OmniArmor waives its Pro fee for signed reports. Unset, reports go unsigned. A secret: keep it in the secret store |
| `CYBERLOGIX_MAX_AI_CALLS_PER_DAY` | `200` | Per-tenant daily cap; `0` = unlimited |
| `CYBERLOGIX_MAX_SMS_PER_DAY` | `100` | Per-tenant daily cap; `0` = unlimited |
| `CYBERLOGIX_MAX_VOICE_CALLS_PER_DAY` | `30` | Per-tenant daily cap; `0` = unlimited |
| `CYBERLOGIX_RATE_AI_CALL` | `0.0012` | USD per model call, for the estimate |
| `CYBERLOGIX_RATE_SMS` | `0.0079` | USD per SMS, for the estimate |
| `CYBERLOGIX_RATE_VOICE_CALL` | `0.0140` | USD per voice call, for the estimate |
| `PUBLIC_BASE_URL` | — | Public URL (live: `https://cyberlogix.mtrxhorizyn.com`): "press 1 to acknowledge" reaches the callback, and link previews and the sitemap use whole addresses. Unset, pages use the address the request arrived on |
| `CYBERLOGIX_SWEEP_SECONDS` | `60` | Unattended sweep interval; `0` disables the in-process loop |
| `CYBERLOGIX_GROUP_ALERT_MIN_UNITS` | `3` | Units at one site warming together that raise the site-wide alert; `0` turns it off. The other `CYBERLOGIX_GROUP_ALERT_*` settings are in [ALERTS.md](ALERTS.md#the-site-wide-alert) |
| `CYBERLOGIX_ATTESTATION_KEY` | — | Counter-signs vault attestations |
| `CYBERLOGIX_PROOF_REPORT_KEY` | — | Seals each proof of monitoring report (HMAC-SHA256), so an edited one reads "changed" at `/verify`. Unset, a key is made once and kept in the database. Keep it once set: changing it makes earlier reports read "changed". See [PROOF-OF-MONITORING.md](PROOF-OF-MONITORING.md) |
| `CYBERLOGIX_INSURANCE_PARTNER_SHARE_PERCENT` | `10` | What an insurance partner earns of what a business they referred pays us, in percent (0 to 50). Kept with each partner at approval. See [INSURANCE-PARTNERS.md](INSURANCE-PARTNERS.md) |
| `CYBERLOGIX_INSURANCE_PARTNER_SHARE_MONTHS` | `12` | For how many months from that business's first payment (1 to 60) |
| `CYBERLOGIX_REPAIR_LEAD_FEE_USD` | `35` | What a repair partner pays us for each lead delivered to them, in dollars (0 to 1000). Kept with each partner at approval. See [REPAIR-PARTNERS.md](REPAIR-PARTNERS.md) |
| `CYBERLOGIX_SAVE_OFFER_PERCENT` | `25` | The one-time offer to stay, shown when an owner goes to cancel: this percentage off (1 to 90). See [BILLING.md](BILLING.md#pause-instead-of-cancel-and-the-offer-to-stay) |
| `CYBERLOGIX_SAVE_OFFER_MONTHS` | `2` | ...of this many months of their invoices (1 to 12). Once per business, ever |
| `CYBERLOGIX_FOUNDER_CODE` | — | The founding member code, compared ignoring case and spaces. Empty: off, and nothing about it is shown. See [BILLING.md](BILLING.md#founding-member-codes) |
| `CYBERLOGIX_FOUNDER_PERCENT` | `20` | What the founding member code takes off, in percent (1 to 90)... |
| `CYBERLOGIX_FOUNDER_MONTHS` | `3` | ...of the first this-many monthly invoices (1 to 12), or this many months' worth once on a yearly invoice. Once per business, ever |
| `CYBERLOGIX_FOUNDER_UNTIL` | — | The last day the code can be entered (YYYY-MM-DD, UTC). Unset: no end date. A date that cannot be read closes the code |
| `CYBERLOGIX_ADMIN_KEY` | — | Gates partner administration; unset closes it |
| `CYBERLOGIX_ALLOW_PRIVATE_WEBHOOKS` | — | Allow webhook targets on a private network |
| `CYBERLOGIX_LEGAL_NAME` | `MTRX Horizyn` | Issuer name on invoices and in the agreements: MTRX Horizyn's registered name |
| `CYBERLOGIX_ADDRESS` | — | Issuer address on invoices |
| `CYBERLOGIX_TAX_ID` | — | Issuer tax ID on invoices |
| `CYBERLOGIX_REMIT_TO` | — | Remittance details on invoices |
| `CYBERLOGIX_BILLING_EMAIL` | — | Billing contact on invoices |
| `CYBERLOGIX_BACKUP_KEEP` | `14` | Snapshots kept, oldest deleted first |
| `CYBERLOGIX_BACKUP_MIN_FREE_MB` | `100` | A snapshot that would leave less free on its disk is refused and reported |
| `CYBERLOGIX_READINGS_PER_SENSOR` | `500` | Readings kept per sensor, in memory and on disk; see [LOAD-TEST.md](LOAD-TEST.md) for what it costs |
| `CYBERLOGIX_OPERATOR_EMAIL` | — | Your own email. The daily digest goes here, and so does "alerts have stopped" (see [If the app is down](#if-the-app-is-down)) |
| `CYBERLOGIX_OPERATOR_PHONE` | — | Your own mobile, in E.164 (`+1...`). Gets a text when alerts stop and when they are fixed, so you hear even if email is down |
| `CYBERLOGIX_ALARM_WATCH_SECONDS` | `60` | How often the alarm-path watch looks. `0` turns it off |
| `CYBERLOGIX_INGEST_STALE_MINUTES` | `20` | How long every sensor on the platform has to be silent at once before "readings have stopped" is raised (at least 5) |
| `CYBERLOGIX_HEARTBEAT_URL` | — | The dead man's switch: pinged after every sweep. See [The dead man's switch](#the-dead-mans-switch) |
| `CYBERLOGIX_ERRORS_EMAIL` | — | Where a short report of each unexpected server error (500) is mailed. Empty sends nothing. See [Error reports](#error-reports) |
| `CYBERLOGIX_SUPPORT_EMAIL` | — | Where the help page tells customers to write, and where "Talk to us" sends someone asking for a quote. Unset, it uses `MAIL_REPLY_TO`; with neither, it points them at the help page |
| `CYBERLOGIX_NOINDEX` | — | `1` on a copy that is not the live product (staging, a demo): every page asks search engines not to list it, robots.txt closes, the sitemap is empty. See [STAGING.md](STAGING.md) |

`.env.example` carries the same list with the reasoning next to each one, and
a test fails if a setting is read by the code but missing from that file — or
documented there and read by nothing.

Set real origins before production; browsers reject credentialed requests
against a wildcard, so credentials switch on only once origins are named.

## If the app is down

Short version: two free checks watch the app from outside, and the app
watches its own alarm path from inside. If anything stops, you get an
email and a text. This section says what to set up once, and what each
alert means.

### Set up once, before the first customer

1. **Your email and phone.** Set `CYBERLOGIX_OPERATOR_EMAIL` and
   `CYBERLOGIX_OPERATOR_PHONE` (your mobile, starting `+1`). The app
   tells you there when alerts stop flowing, and again when they are
   fixed. One message each way, not one a minute.
2. **A free uptime monitor.** UptimeRobot or Better Stack (free plan)
   both work. Add an HTTP monitor:
   - URL: `https://cyberlogix.mtrxhorizyn.com/readyz`
   - Check every: as often as the free plan allows (a few minutes is fine)
   - Alert when: the status is not 200
   - Alert to: your phone (their app, or SMS) and your email
3. **A second monitor, for "is it up at all":** the same, with
   `https://cyberlogix.mtrxhorizyn.com/healthz`.
4. **The dead man's switch** (`CYBERLOGIX_HEARTBEAT_URL`, see below and
   DEPLOY.md section 4). It is the only check that still works when the
   whole server is gone, because it alarms when the pings stop.

Do not point Render's own health check at `/readyz`. Render restarts the
app when its check fails, and a restart does not fix Twilio being down;
it only hides it. Render's check stays on `/api/health`.

### The two checks

* **`/healthz`** — is the app running. Answers `{"status": "ok"}`, and
  touches nothing else. If this fails, the app is down.
* **`/readyz`** — can alerts get out right now. 200 means yes. 503 means
  no, and `problems` says why in a sentence. It also shows each part:

| Part | `ok` means | Other answers |
|---|---|---|
| `database` | readings can be saved | `failing` |
| `alert_job` | the job that sends follow-up calls ran in the last few minutes | `stopped` |
| `sensor_readings` | some sensor reported in the last 20 minutes | `stopped`; `no_sensors_yet` (fine) |
| `texts`, `calls` | the last tries got through | `failing` (3 in a row refused), `off` (Twilio not set up), `no_sends_yet` (fine) |
| `email` | the last alert emails got through | `failing`, `off`, `no_sends_yet` |
| `webhooks` | customers' chat hooks are working | `failing` is shown but never makes it 503: it is usually one customer's hook set up wrong |
| `outside_alarm` | the dead man's switch is set | `not_set` |

It never shows names, phone numbers, how many customers there are,
versions or error messages, so it is safe to give to an outside monitor.
It is worked out at most every 5 seconds, however often it is asked.

### What each alert means, and what to do

**The uptime monitor says `/healthz` is down.** The app is not running.
1. Open Render, the `cyberlogix` service, **Logs**. Look at the last
   lines before it stopped.
2. Restart the service from Render's dashboard. If it will not stay up,
   deploy the last version that worked.
3. When `/healthz` answers again, check `/readyz` is 200.

**The heartbeat service says the pings stopped.** Same as above: the
app or the server is gone. Same steps.

**`/readyz` is 503, or you got an email or text starting "Problem:".**
Read the sentence. Each one, and what to do:

* **"The alert job has stopped."** The app is up but follow-up calls are
  not going out. Restart the service in Render. If it happens again
  within the hour, look in the logs for "Autopilot sweep pass failed".
* **"No sensor readings have arrived from any business for N minutes."**
  Every sensor went quiet at once, so the problem is on our side or the
  internet's, not one customer's fridge. Check the app answers at its
  address. Check Render's logs for errors on `/api/sensor-pulse`. If one
  sensor brand stopped (a vendor cloud), see `/book` -> Health. With
  only one or two sensors on the platform, this also fires when that one
  sensor is unplugged.
* **"Alert texts are failing" / "Alert phone calls are failing".**
  Twilio refused or did not answer 3 times in a row. Open the Twilio
  console: is the account paid up and not suspended, and is the sending
  number still yours? Check status.twilio.com. A failed alert text is
  tried again at 1, 5 and 15 minutes and a failed call at 5 and 15, then
  the customer's console says to phone the roster. So if Twilio is down
  for longer and a customer has an open alert, phone them.
* **"Alert emails are failing".** The mail server refused or did not
  answer. Check the mail provider's dashboard and `SMTP_HOST`,
  `SMTP_USERNAME`, `SMTP_PASSWORD`. Alert emails are tried again for
  about eight hours while the alert is open. (This one reaches you by text, since email is down.)
* **"Texts and calls are switched off".** `TWILIO_ACCOUNT_SID`,
  `TWILIO_AUTH_TOKEN` and `TWILIO_FROM_NUMBER` are not all set. No alert
  reaches a phone until they are. (On a staging copy with
  `CYBERLOGIX_NOINDEX=1` this is expected and does not make it 503.)
* **"The database is not answering".** Usually a full disk. In Render,
  look at the disk's usage. See [Backups](#backups) for old snapshots to
  delete, and [LOAD-TEST.md](LOAD-TEST.md#disk) for how big to make it.

You get a **"Fixed:"** email and text when each one clears. If you never
get one, it is still broken.

### Timeouts on everything that leaves the app

Nothing the app sends can hold up an alert for long:

| Outgoing | Gives up after | Tries again |
|---|---|---|
| Texts and calls (Twilio) | 8 s (`TWILIO_TIMEOUT_SECONDS`) | A failed alert text at 1, 5 and 15 minutes; a failed call at 5 and 15. Each try is claimed first, so one failure is one re-send, never two |
| Email | 20 s (`SMTP_TIMEOUT_SECONDS`) | Alert emails at 1, 5, 15, 30 minutes and on to 8 hours while the alert is open; other mail hourly. An address the mail server says does not exist is not tried again |
| Customers' chat hooks | 5 s each, 12 s for all of them | Once, for a 5xx, 408, 429 or no answer; never for a wrong address |
| The model that words the alert's "what to check" line | 6 s (`CYBERLOGIX_AI_TIMEOUT_SECONDS`) | No: the alert goes out with the plain template line instead |
| Sensor clouds we fetch from | 20 s | On the next sweep |
| OmniArmor daily report | 20 s | With backoff, as their contract allows |
| The dead man's switch ping | 5 s | On the next sweep |

The model line used to have no time limit at all: a model service that
took the connection and said nothing held the alert text back for as long
as it liked. That is fixed (`tests/test_alarm_path.py`).


## State

State is durable. `store.py` keeps a fast in-memory working set and writes
through to SQLite (`db.py`) on every change, reloading it at startup, so
tenants, sensors, readings, incidents, users, the audit trail and metered
usage all survive a restart. Because the queries are small per-tenant scans
rather than SQL, entities are stored as JSON documents keyed by `(kind, id)`
— durability without a migration burden.

The shape of each record (`Tenant`, `Sensor`, `Invoice` and the rest) and
its `to_row`/`from_row` are in `records.py`; the fixed rules it applies
(industry profiles, plans, the breach check, temperature
units) are in `domain.py`. `store.py` re-exports both, so code imports
everything from `store`.

Set `CYBERLOGIX_DB_PATH` to a mounted volume to outlive the container. The
container image defaults it to `/app/data/cyberlogix.db`.

It is still single-writer: pin to one instance (`--max-instances 1`) or
replace the `Database` class with a Postgres adapter before scaling out.
`db.py` is the only file that has to change — nothing above it does SQL, so
the adapter only needs `put`, `get`, `delete`, `all`, `count` and `clear`.

Reading history is capped per sensor by a ring buffer, and the eviction is
mirrored into the database, so the table cannot grow without bound. When
to move to Postgres, with the numbers measured under load, is in
[LOAD-TEST.md](LOAD-TEST.md).

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
* **Retention is by count, oldest first** (`CYBERLOGIX_BACKUP_KEEP`, 14),
  and a snapshot that would leave less than `CYBERLOGIX_BACKUP_MIN_FREE_MB`
  (100) free on its disk is not taken: it is refused and recorded as a
  fault. Count alone did not keep the disk safe. Fourteen snapshots of a
  few hundred sensors' database fill render.yaml's 1 GB disk
  ([LOAD-TEST.md](LOAD-TEST.md#disk)), and a full disk is not a missed
  backup: SQLite cannot write, so every reading would be refused.

`CYBERLOGIX_BACKUP_DIR` defaults to a directory beside the database, which
survives a bad migration and not a lost disk; `GET /api/admin/backups`
says so out loud when that is the case, and says how much room is left
(`free_bytes`, `space_for_another`). Copy them off the machine on a
schedule — a backup that only exists next to the thing it is backing up
is not one. This is the command, for a cron job anywhere else:

```bash
KEY=...   # CYBERLOGIX_ADMIN_KEY
HOST=https://cyberlogix.mtrxhorizyn.com
NAME=$(curl -fsS -H "X-CyberLogix-Admin: $KEY" $HOST/api/admin/backups | python3 -c 'import json,sys; print(json.load(sys.stdin)["latest"])')
curl -fsS -H "X-CyberLogix-Admin: $KEY" -o "$NAME" "$HOST/api/admin/backups/$NAME/download"
```

The download is verified on the server before it is sent.

### Restore checklist (about ten minutes)

Rehearsed on a 1,000-sensor database at full history (500,000 readings,
130 MB): snapshot, list, verify, download, start a fresh copy, check it
and restore in place took about 15 seconds of machine time between them
(`python tools/restore_rehearsal.py` does every step and times it; rough
numbers in [LOAD-TEST.md](LOAD-TEST.md#restore)). The rest of the ten
minutes is reading and typing. Set `KEY` and `HOST` as above first.

**1. Decide which case it is (1 min).**

* *The data is wrong, the disk is fine* (a bad import, a mistake): restore
  in place, step 3. Every write since the snapshot is discarded, except
  what has already been sent (see "What a restore keeps").
* *The disk is gone* (a new service, a new server): put a copy kept off
  the machine back first, step 2, then step 3.
* *A rehearsal*: never on production. Step 5.

**2. Put the snapshot where the app can see it (2 min, only if the disk is
gone).**

* Docker: `cp cyberlogix-<stamp>.sqlite3 /srv/cyberlogix/backups/`, then
  start the container as usual (`docker start cyberlogix`, or the
  `docker run` in DEPLOY.md section 2). It starts on an empty database.
* Render: deploy the service (it gets an empty disk), open an SSH session
  from the service's Connect menu, and copy the file onto the disk, into
  `/app/data/backups/` (Render's SSH supports `scp`; the address is on the
  same menu).

**3. Check and restore (3 min).**

```bash
curl -fsS -H "X-CyberLogix-Admin: $KEY" $HOST/api/admin/backups          # pick NAME
curl -fsS -H "X-CyberLogix-Admin: $KEY" $HOST/api/admin/backups/$NAME/verify
curl -fsS -X POST -H "X-CyberLogix-Admin: $KEY" -H 'Content-Type: application/json' \
  -d "{\"snapshot\":\"$NAME\",\"confirm\":true}" $HOST/api/admin/backups/restore
```

`verify` must say `"ok": true`. The restore snapshots the database it
replaces first (`-pre-restore`), copies the snapshot in through SQLite
(the connection never closes and no stale write-ahead log can be read
into it) and reloads, with no restart. The app does not answer while it
reloads: about 6 seconds at 1,000 sensors with full history, longer in
proportion. The same commands work on Render and Docker.

**4. Check it worked (3 min).**

* `curl -fsS $HOST/api/health` answers 200 with the version, and
  `curl -fsS $HOST/api/watchdog` answers 200 within a minute (the sweep
  is running).
* Sign in as an owner: the sensors are there with their charts, the
  Alerts tab has the incidents, Setup has the audit trail, and the vault
  check on a sensor says intact.
* Send one reading out of range from a test sensor (or the "Send a test
  alert" button): the text arrives.

**5. Rehearsing on a copy, never on production.** Run the image on your
own machine with the downloaded snapshot and **without** the production
settings: no `--env-file`, so no Twilio and no mail host (dry run: alerts
are recorded, never sent), and the sweep off. A copy started with the
production `.env` texts and phones the real roster about the snapshot's
open incidents and sends the day's digests again.

```bash
mkdir -p /tmp/rehearsal/data /tmp/rehearsal/backups
cp cyberlogix-<stamp>.sqlite3 /tmp/rehearsal/backups/
docker run --rm -d --name rehearsal -p 8081:8080 \
  -v /tmp/rehearsal/data:/app/data -v /tmp/rehearsal/backups:/srv/backups \
  -e CYBERLOGIX_BACKUP_DIR=/srv/backups -e CYBERLOGIX_ADMIN_KEY=rehearsal \
  -e CYBERLOGIX_SWEEP_SECONDS=0 cyberlogix
KEY=rehearsal HOST=http://127.0.0.1:8081    # then steps 3 and 4
docker stop rehearsal
```

**If the app will not start at all**, put the file in place by hand with
it stopped: `cp` the snapshot to the database path (`/app/data/cyberlogix.db`)
and **delete `cyberlogix.db-wal` and `cyberlogix.db-shm` beside it** before
starting. A write-ahead log left from the old database is read as part of
the new one.

### What a restore keeps

Restoring the record does not un-send a text, a call or an email. So a
restore keeps, from the database it replaces: every incident as it stands
(acknowledged, escalated, resolved, and the ones opened since the
snapshot), every mail it sent or still has queued (the digests, the weekly
reports, the alert emails), each sensor's last contact, and the weekly
text's "sent this week". Without this, a restored snapshot re-escalated an
incident already answered, sent the day's digest again, and texted every
business that each of its sensors had gone quiet (in the snapshot, the
last reading was hours old). Businesses that deleted themselves are
deleted again; ids and invoice numbers never go backwards. Invoices issued
since the snapshot are **not** kept: check the billing worklist after a
restore so nobody is billed twice for one period.

### What was wrong in the steps before

Found by following them on a big database:

* "Pick a snapshot, verify it" had no command for verifying it.
* "Copy the snapshots off the machine" had no way to do it but a shell on
  the server; there is a download now.
* Restoring onto a new disk (the case backups exist for) was not covered,
  and a copy started for a rehearsal with the production settings would
  have alerted the real roster.
* The restore swapped the database file under a write-ahead log that
  anything else holding the file open could leave behind.
* A restore re-sent alerts and digests that had already gone out.

### A demo database never backs up into the live folder

`python demo_account.py --db PATH` seeds a made-up restaurant group into
a new database and marks it as a demo. A demo's snapshots go in
`demo-backups/` beside the demo database, whatever
`CYBERLOGIX_BACKUP_DIR` says; if that folder would be a live backup
folder after all, no snapshot is taken. A sister product once ran its
demo with the production settings, and the demo's daily snapshots went
into the live folder, where keeping the newest fourteen pruned the real
ones away. `tests/test_demo_account.py` takes a snapshot of a seeded demo
in its own process with `CYBERLOGIX_BACKUP_DIR` set, and checks the live
folder is untouched.

## Outage drill: Twilio and mail

Twice a year, and after any change to how alerts are sent. On a staging
copy, never on production. `tests/test_outage_drill.py` runs the same
checks on every push; this is the live version. About 30 minutes, most of
it waiting for the re-send steps.

1. **Set it up.** A staging deploy with real Twilio test credentials, one
   business on a plan with calls, two people on its roster with your own
   phones, a test sensor, and `CYBERLOGIX_OPERATOR_EMAIL` set to you.
2. **Break Twilio with errors.** Set `TWILIO_AUTH_TOKEN` to a wrong value
   and redeploy (every text and call is refused with an error).
3. **Break mail.** Set `SMTP_HOST` to a host that does not answer, e.g.
   `127.0.0.1` with `SMTP_PORT=9`, and redeploy.
4. **Breach.** Post one reading out of range. Check:
   - [ ] the reading answers 200 and the incident opens;
   - [ ] the Alerts card says the texts failed, with the next re-send time;
   - [ ] `/book` -> Health shows a fault for `notifications.send_sms` and,
         after the alert email's first try, `mail.deliver`, each counted.
5. **Wait 16 minutes.** Check:
   - [ ] the incident shows re-sends at about 1, 5 and 15 minutes, all failed;
   - [ ] after 10 minutes the call was tried and failed, and again at 15;
   - [ ] the console card now says "Phone the roster yourself."
6. **Timeouts too.** Repeat 4 with Twilio unreachable rather than refusing
   (point the service at a network that drops it, or block
   `api.twilio.com`): the reading still answers within about 10 seconds
   (`TWILIO_TIMEOUT_SECONDS`, 8) and everything above holds.
7. **Bring mail back** (restore `SMTP_HOST`, redeploy). Within the next
   alert-email step (at most 30 minutes after the breach, sooner earlier
   on), check:
   - [ ] the owner gets the alert email once, subject `TEXTS FAILED - ALERT: ...`;
   - [ ] and one `NOBODY HAS BEEN TEXTED: ...` email;
   - [ ] the operator's daily digest lists the faults.
8. **Bring Twilio back** (the right token). Post a new out-of-range
   reading on another sensor and check:
   - [ ] the text arrives at once, and nothing is sent twice for the first
         incident (its re-sends stopped at the last step; its call retry
         stops once a call gets through);
   - [ ] no duplicate emails arrived at any point.
9. **Clean up.** Acknowledge and resolve both incidents; clear the faults.

What the first run of this drill found, now fixed: a call that failed at
the provider was never tried again; the alert email waited for the hourly
mail queue and was dropped after three hours; the owner's copy of the
alert did not say the texts had failed; nothing told the owner when the
last re-send failed; and nothing told the operator that Twilio was
failing for everybody at once.

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

## Error reports

Every unexpected error is written to the fault list (`/book` -> Health,
or `GET /api/admin/faults`), counted once per kind however often it
happens. A list somebody has to open is not much use at 2am, so a 500 is
also mailed to `CYBERLOGIX_ERRORS_EMAIL` when that is set. Empty, nothing
is sent and the fault list works as before.

**In the report:** the time, the version, the method and the route as
written in the code (`/api/voice/keypress/{incident_id}/{token}`, never
the address it was called at or its query string), the error's type and
message, the reference code the error page showed the customer (the same
code the fault list is keyed on, so the two can be matched), how many
times it has happened, and the stack trace with paths relative to the
app.

**Never in it:** the request body, cookies, headers or the caller's IP
are never read. The message and the trace go through the same redaction
as the fault list (email addresses and phone numbers out) and through
token scrubbing (our keys and tokens, anything after "Bearer", and
anything given to a name like key, token, secret or password). The
fault list is scrubbed of tokens the same way. A test sends an email
address in the query string, a form, a header and a cookie of a request
that fails, and checks that none of it reaches the report.

**How many:** one email per distinct error an hour, and at most 20 a
day, counted in the database so a restart does not start the count
again. The email is sent after the error page has gone out, through the
ordinary mail queue, so a mail host that is down gets it on the next
pass rather than never.

## Usage counts

`usage.py` counts a few actions as daily totals: businesses signed up,
sensors added, test alerts sent, alerts acknowledged (by the console
button or API, by text reply, and by pressing 1 on a call, counted
apart), inspector's logs made (the console opens one to print it) and
help pages viewed. A day is a date and some numbers: no account, person,
address, cookie or IP, and nothing goes to anyone else. The pricing
calculator runs in the browser and never reaches the server, so it is
not counted. Days older than 400 are deleted.

The last 30 days are at `/book` -> Health, at `GET /api/admin/usage`
(admin key), and in `/api/stats`, behind `STATS_KEY`, for MTRX Horizyn's
console. The privacy statement says so in one sentence.

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
