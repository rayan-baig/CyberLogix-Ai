# Load test, and when to leave SQLite

What happens when many sensors report at once, what broke first, what was
fixed, and the point at which CyberLogix should move from SQLite to
Postgres. Measured on 2026-11-03 on a shared 4-core machine, so **every
number here is rough**: treat a difference of a few milliseconds as noise,
and run the tools again before relying on a figure.

## How it was measured

```bash
python tools/load_test.py                                  # 250, 1000, 3000, 5000 sensors
python tools/load_test.py --sensors 1000 --history 500     # with full reading history
python tools/load_test.py --sensors 1000 --saturate 30     # find the ceiling
python tools/restore_rehearsal.py                          # backup and restore, timed
```

Each step seeds a throwaway database (ten sensors, two sites and two
on-call contacts per business, five console users), then starts the server
the way the image does: `uvicorn`, **one worker**, the sweep on its
production 60-second interval. Twilio is replaced by a recording sink
(`tools/fake_providers.py`) and SMTP by a local mail sink, so nothing
leaves the machine and every text, call and email is timed. Every sensor
posts one reading a minute to `POST /api/sensor-pulse` with its own sensor
key, at its own point in the minute with a second of jitter, as
unsynchronised hardware does. Five console users refresh every 20 seconds
with the same 23 requests the console makes. An uptime check reads
`/api/watchdog` every five seconds. Part way through, ten sensors in
different businesses go out of range and stay there.

## Results

### Every sensor reporting once a minute, fresh database

| Sensors | Readings/s | Median ingest | 95% ingest | 99% ingest | Failed | Server CPU | Sweep took |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 250 | 4 | 5.1 ms | 9.2 ms | 24 ms | 0 | 2% | 0.02 s |
| 1,000 | 15 | 4.6 ms | 8.3 ms | 22 ms | 0 | 5% | 0.03 s |
| 3,000 | 46 | 4.1 ms | 8.4 ms | 23 ms | 0 | 12% | 0.15 s |
| 5,000 | 77 | 4.3 ms | 14.0 ms | 37 ms | 0 | 18% | 0.45 s |

(With every fix below in place. The first run, before them, gave the same
picture within a millisecond or two.)

No reading was lost: every one the server answered 200 for is on disk
(counted by id after the server stopped). No 500s, no "database is
locked" (there is one connection, and the store's lock serialises it), no
error in the server log. A console refresh (23 requests) took 56 to 79 ms
(95%: under 160 ms) at every size.

### The ceiling

Posting back to back from three client processes: **about 400 readings a
second** (405 to 457 across the runs; median about 105 ms and 95% about
200 ms with 48 requests in flight; nothing failed), with the one worker at
75-90% of a core. That is roughly 24,000 sensors reporting once a minute.
One client process could not find it: it ran out of CPU itself at about
220 a second while the server sat at half a core, which is why
`--saturate` uses several. A single SQLite commit took about 0.06 ms
here; the ceiling is Python in one process, not the database.

### Breach to alert

| | Median | Worst |
|---|---:|---:|
| Bad reading to its alert text handed to Twilio | 5 ms | 35 ms |
| Bad reading to the alert email accepted by the mail host | 30-40 ms | 90 ms |
| Grace period over to the escalation call | 45-50 s | 59 s |

The goal is within 60 seconds. The text goes out inside the request that
carried the bad reading, so it is milliseconds at every size. The call is
placed by the sweep, which runs once a minute, so it goes out up to a
minute after the 10-minute grace period ends; that is the sweep interval,
and it did not move with load. (The runs shorten the grace period to one
minute so the call can be watched.)

The watchdog answered 200 throughout once the first sweep had run (five
seconds after start), its age never above 60 seconds (stale is 240), and
the dead man's switch was pinged after every sweep.

### What broke first

**Memory, when the server restarts.** With every sensor holding its full
history -- 500 readings, which at one a minute is reached after about
eight hours -- the server needed this much to start:

| Sensors (500 readings each) | Before: peak memory | After: peak memory | Start-up | Database |
|---:|---:|---:|---:|---:|
| 1,000 | 676 MB | 251 MB | 5 s | 129 MB |
| 3,000 | 1,832 MB | 509 MB | 12 s | 385 MB |
| 5,000 | about 3 GB (projected) | 767 MB | 23 s | 643 MB |

Render's `starter` instance has 512 MB. Before the fix, a deployment with
about 700 sensors ran fine all day and then **could not start again** after
a deploy or a crash: it ran out of memory loading its own readings, and
with it went every alert. Start-up built every reading as a dictionary in
one list, then a second list of objects, then sorted it, and each reading
object carried its own dictionary of fields. Now readings are streamed
from the database in order straight into each sensor's buffer, and a
reading is a slotted object sharing its sensor's id string. Memory is now
about **120 MB plus 0.13 MB per sensor** at full history.

The same change fixed a latent ordering bug: readings in the same second
were ordered by id as text, and `RDG-1000000` sorts before `RDG-999999`.
The counter passes a million in a day at a thousand sensors, and the vault
chains readings in order, so a restart could have changed a chain head and
reported tampering where there was none. They are ordered as numbers now.

**Then the disk.** See [Disk](#disk) below: on render.yaml's 1 GB disk,
fourteen daily snapshots fill it at about 450 sensors.

### What else was found and fixed

* **Sweeps could overlap.** The built-in loop and an outside scheduler,
  or an outside scheduler firing again while a pass waited out a Twilio
  outage, ran side by side. Each send was claimed first, so nothing went
  twice, but the passes stacked, each holding a worker thread. One sweep
  runs at a time now; a second stands aside and says so
  (`tests/test_sweep_overlap.py`).
* **The sweep drifted.** It slept a whole minute after each pass, so its
  period was a minute plus the pass, and the first sweep after a deploy
  came a whole minute late while the watchdog answered 503. It runs on a
  fixed cadence now, the first pass within five seconds of a start. A
  sensor is only reported as gone quiet once it has been silent for 30
  minutes while the server was listening, so the first sweep after an
  outage of ours does not text everybody.

## Disk

On disk a reading takes about **256 bytes** (a 170-byte row and its share
of the index), so a sensor at full history is about 0.13 MB. The readings
stop growing at 500 per sensor (`CYBERLOGIX_READINGS_PER_SENSOR`); what
keeps growing is everything kept for years:

| Kept | Size, measured | Estimate per 1,000 sensors (100 businesses) a month |
|---|---:|---:|
| An incident, with the readings around it as evidence | 90 KB (8 h lead-up), up to about 210 KB | 1 a business: 10-20 MB |
| Mail (alerts, digests, weekly reports; bodies trimmed after 400 days) | about 1 KB | 3-4 MB |
| Audit trail | about 260 bytes an entry | 5-6 MB |
| Usage, sessions, vault anchors | 200-400 bytes each | 5-10 MB |

So a **database of about 130 MB per 1,000 sensors, growing 25-40 MB a
month** (the monthly figures are estimates from measured row sizes and an
assumed one incident per business per month; a business with many
incidents grows faster). Readings per day are sensors x 1,440: 1.4 million
a day at 1,000 sensors, almost all of them replacing an older one.

The backups are the larger share. Fourteen daily snapshots plus the live
file is about **15 x the database**: 1,000 sensors at full history is
about 2 GB. On render.yaml's 1 GB disk that is reached at roughly **450
sensors**. Before this release the snapshot was written anyway and a full
disk would have refused every reading; now a snapshot that would leave
less than 100 MB free is not taken and the refusal is recorded as a fault
(`CYBERLOGIX_BACKUP_MIN_FREE_MB`). **Give the disk 5 GB before 400
sensors** (about 2,500 sensors with fourteen snapshots), and copy the
snapshots off the machine (`GET /api/admin/backups/{name}/download`,
[OPERATIONS.md](OPERATIONS.md#backups)).

## Restore

On the 1,000-sensor database at full history (128 MB, 500,000 readings),
`tools/restore_rehearsal.py`: snapshot 1.0 s, list and verify 0.5 s,
download 0.8 s, a fresh copy started on it 4.4 s and checked (sensors,
readings, incidents, vault, audit, a new alert) in 0.2 s, and an in-place
restore through the API 5.6 s, during which the app does not answer.
Nothing was sent twice in the sweeps after it. The steps are in
[OPERATIONS.md](OPERATIONS.md#restore-checklist-about-ten-minutes).

## How readings are kept and pruned

* **Readings:** the newest `CYBERLOGIX_READINGS_PER_SENSOR` (500) per
  sensor, in memory and on disk; each new one deletes the oldest. That is
  about 8 hours at one a minute, 41 hours at one every five. Every
  document that counts readings says what window it covers.
* **Evidence:** before a reading is deleted it is copied onto any
  incident whose 12-hours-either-side window it falls in, so the readings
  around an excursion are kept as long as the incident.
* **Incidents, audit trail, invoices and the vault's anchors:** kept.
* **Mail:** the record is kept, so nothing is sent twice; bodies are
  blanked after 400 days, and sign-in links as soon as they are used.
* **Snapshots:** fourteen, oldest deleted first, never written if they
  would leave the disk without room.

Raising the reading limit costs memory first: about 0.26 KB per reading in
memory and on disk.

## When to move to Postgres

SQLite is not what limits this. It answered every write in well under a
millisecond and never reported a lock; the limits are that **everything
runs in one process, holding every sensor's recent readings in memory**,
and that there can be only one instance. Moving to Postgres means moving
that working set into the database too (`store.py`, not only `db.py`; see
[OPERATIONS.md](OPERATIONS.md#state)), so it is a project, not a setting.
Plan it when the first of these is near:

| Signal | Where it is (one worker, 500 readings a sensor) | What to do first |
|---|---|---|
| Memory | 120 MB + 0.13 MB a sensor: about 2,500 sensors on a 512 MB instance, 14,000 on 2 GB | A bigger instance |
| Start-up time | about 5 s per 1,000 sensors | Nothing until it passes a minute (about 12,000 sensors) |
| Ingest | about 400 readings/s at one core: 24,000 sensors at one a minute | Keep sustained load under half of it: **about 10,000 sensors** |
| Disk | 15 x the database with fourteen snapshots | A bigger disk; copy snapshots off |
| Availability | one instance: a deploy or a crash is a gap of seconds to a minute in ingest | Postgres, and a second instance |

**So: stay on SQLite to about 2,500 sensors on the `starter` instance
(with a 5 GB disk), or about 10,000 on a 2 GB instance; start the move to
Postgres at 5,000 sensors, so it is done before 10,000.** Move sooner if
a customer needs an alerting service that survives one machine failing
without a gap, because one instance is the one limit no setting changes.
