"""A believable demo business, into an empty database, and nothing else.

    python demo_account.py --db /tmp/cyberlogix-demo.db

Seeds one made-up restaurant group, Juniper & Vine Kitchens: three
sites, twenty units (walk-in freezers and coolers, prep fridges, bar
fridges, an ice-cream freezer, a blast chiller), a week of readings every
half hour, one breach that was handled (acknowledged in minutes, a
corrective action written down, closed), one problem still open, an
on-call rotation, a signed contract and a paid invoice. Then it prints
how to sign in. While it is served, the sweep keeps every unit reporting
(feed below), so the demo is as fresh an hour later as at once.

Unlike seed_demo.py, which empties whatever store it is pointed at, this
refuses a database that holds anything: a business, a person, an
invoice. It is for a new file, a sales laptop or a staging box.

It marks the database as a demo, and a demo database never backs up into
the live backup folder. A sister product once ran its demo with the
production settings, and the demo's daily snapshots went into the live
backups folder, where keeping the newest fourteen pruned the real ones
away. Here a demo's snapshots go in demo-backups/ beside the demo
database whatever CYBERLOGIX_BACKUP_DIR says, and if that would be the
live folder after all, it takes none (backup.py).

Every name, address and number in it is made up: people's names that
belong to nobody in particular, addresses on the reserved .example
domain, and phone numbers in the 555-01xx range that is never assigned.
Nothing is ever mailed to them: the addresses are on the do-not-mail list
from the start.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import secrets
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

DEMO_MARK = ("meta", "demo_database")

COMPANY = "Juniper & Vine Kitchens"
DOMAIN = "juniper-vine.example"
OWNER = ("Morgan Hale", f"morgan@{DOMAIN}")
OPERATOR = ("Priya Lund", f"priya@{DOMAIN}")
VIEWER = ("Theo Marsh", f"theo@{DOMAIN}")

SITES = [
    ("Harbourside", "1 Quay Lane, Harbourside"),
    ("Old Mill", "40 Mill Road, Eastbank"),
    ("Commissary", "7 Depot Street, Northfield"),
]

# (site, sensor id, where it is, kind). Kinds set the limits and how the
# temperature behaves; see KINDS.
UNITS = [
    ("Harbourside", "HBR-WIF-1", "Walk-in freezer", "freezer"),
    ("Harbourside", "HBR-WIC-1", "Walk-in cooler", "cooler"),
    ("Harbourside", "HBR-PREP-1", "Prep fridge, hot line", "fridge"),
    ("Harbourside", "HBR-PREP-2", "Prep fridge, pastry", "fridge"),
    ("Harbourside", "HBR-BAR-1", "Bar fridge", "fridge"),
    ("Harbourside", "HBR-ICE-1", "Ice-cream freezer", "ice_cream"),
    ("Harbourside", "HBR-BLAST-1", "Blast chiller", "chiller"),
    ("Old Mill", "OML-WIF-1", "Walk-in freezer", "freezer"),
    ("Old Mill", "OML-WIC-1", "Walk-in cooler", "cooler"),
    ("Old Mill", "OML-PREP-1", "Prep fridge, grill", "fridge"),
    ("Old Mill", "OML-PREP-2", "Prep fridge, salads", "fridge"),
    ("Old Mill", "OML-BAR-1", "Bar fridge", "fridge"),
    ("Old Mill", "OML-DES-1", "Dessert display", "fridge"),
    ("Commissary", "COM-WIF-1", "Walk-in freezer 1", "freezer"),
    ("Commissary", "COM-WIF-2", "Walk-in freezer 2", "freezer"),
    ("Commissary", "COM-WIC-1", "Walk-in cooler", "cooler"),
    ("Commissary", "COM-PREP-1", "Prep fridge, sauces", "fridge"),
    ("Commissary", "COM-PREP-2", "Prep fridge, bakery", "fridge"),
    ("Commissary", "COM-BLAST-1", "Blast chiller", "chiller"),
    ("Commissary", "COM-DRY-1", "Dairy cooler", "cooler"),
]

# Limits in Fahrenheit (None leaves the restaurant default, 32F above, for
# a freezer), and the temperature a healthy unit sits at.
KINDS = {
    # A freezer alarms over 10°F, as the product sets one (3.37.0): with no
    # limit of its own the demo's walk-in freezer took the sector's 41°F,
    # so its "door left open" alert had no reading out of range in its
    # own insurance pack.
    "freezer": {"above": 10.0, "below": None, "normal": -1.0, "spread": 1.6},
    "ice_cream": {"above": 10.0, "below": None, "normal": 2.0, "spread": 1.4},
    "cooler": {"above": 41.0, "below": 32.0, "normal": 36.5, "spread": 1.0},
    "fridge": {"above": 41.0, "below": 32.0, "normal": 37.5, "spread": 1.2},
    "chiller": {"above": 41.0, "below": None, "normal": 35.0, "spread": 1.5},
}

HANDLED = "OML-WIF-1"      # the breach somebody dealt with, three days ago
STILL_OPEN = "COM-PREP-2"  # the problem that is open right now
HANDLED_FOR = 90           # minutes the handled freezer was warm
WARMING_FOR = 90           # minutes of the open problem's warming, to now


def _handled_shape(minute: int) -> float:
    """The handled freezer, `minute` minutes after it went out: 12.5°F,
    up to its worst of 21.0°F half an hour in, down to 11.5°F an hour in,
    and still just over its 10°F limit until an hour and a half."""
    if minute <= 30:
        temp = 12.5 + (21.0 - 12.5) * minute / 30
    elif minute <= 60:
        temp = 21.0 - (21.0 - 11.5) * (minute - 30) / 30
    else:
        temp = 11.5 - 1.0 * (minute - 60) / 30
    return round(temp, 1)


def _warming_shape(minute: int) -> float:
    """The open problem, `minute` minutes from now (0 or less): in range
    and creeping up to 40.0°F an hour and a half ago... 40.8°F, then
    43.5°F an hour ago when its alert opened, 45.0°F half an hour ago, and
    its worst, 46.5°F, now."""
    if minute < -60:
        return round(40.0 + 0.8 * (minute + 90) / 25, 1)
    if minute <= -30:
        return round(43.5 + 1.5 * (minute + 60) / 30, 1)
    return round(45.0 + 1.5 * (minute + 30) / 30, 1)

DAYS = 7
# The made-up week, a reading every 5 minutes (3.41.0), as often as a
# running demo is fed: the proof of monitoring report says "No sensor went
# 10 minutes or more without sending data", which a reading every half
# hour made untrue.
EVERY_MINUTES = 5
# A door opened now and then: this often in each half hour, whatever the
# cadence.
DOOR_CHANCE_HALF_HOUR = 0.04

# While the server runs, every demo unit gets a fresh reading this often
# (3.40.0; feed() below, called by the sweep). Half an hour after it was
# seeded the demo's /today said "19 things need you now", one "has gone
# quiet" card per unit, because nothing wrote a reading after the seed.
# Under GAP_RECORD_MINUTES, so a running demo writes no gaps in its data,
# and well under the half hour after which a unit counts as quiet.
LIVE_EVERY_MINUTES = 5

# Full-length made-up numbers in the 555-01xx fiction range (3.38.0): the
# pack printed "+15550141", which no phone has ever had. Spaced as a trial
# business types its own (3.39.0): "+1 555 010 0141", as the sign-up and
# Who gets alerts boxes suggest.
CONTACTS = [
    ("Morgan Hale", "+1 555 010 0141"),
    ("Priya Lund", "+1 555 010 0142"),
    ("Ade Okoro", "+1 555 010 0143"),
]

# The demo's text receipts are made up, and say so (3.39.0): /today said
# "Texts are not switched on" while the alert's timeline said "texted 1
# person · 1 delivered". Every made-up receipt carries `demo`, and the
# console, the pack and Pilot call it a pretend text.


class RefusedDatabase(RuntimeError):
    """The database holds something; the demo will not go into it."""


def what_is_there() -> List[str]:
    """Anything in the store that means this is not an empty database."""
    from store import STORE

    found = []
    if STORE._db.get(*DEMO_MARK) is not None:
        found.append("the demo already")
    for kind, one, many in (("tenant", "business", "businesses"),
                            ("user", "person", "people"),
                            ("invoice", "invoice", "invoices"),
                            ("partner", "reseller", "resellers"),
                            ("sensor", "unit", "units"),
                            ("reading", "reading", "readings")):
        count = len(STORE._db.rows(kind))
        if count:
            found.append(f"{count:,} {one if count == 1 else many}")
    return found


def _temperature(rng: random.Random, kind: str, step: float,
                 door_chance: float = DOOR_CHANCE_HALF_HOUR * EVERY_MINUTES / 30) -> float:
    """A healthy unit: its own level, a slow daily swell, a little noise,
    and a door opening now and then."""
    spec = KINDS[kind]
    daily = math.sin(step / (24 * 60 / EVERY_MINUTES) * 2 * math.pi) * spec["spread"] * 0.5
    door = spec["spread"] * 1.2 if rng.random() < door_chance else 0.0
    return round(spec["normal"] + daily + door + rng.uniform(-0.4, 0.4) * spec["spread"], 1)


def seed(now: Optional[datetime] = None, password: Optional[str] = None,
         seed_value: int = 2026) -> Dict[str, Any]:
    """Build the demo business in the store. Raises RefusedDatabase first
    if the store holds anything at all."""
    import firstday
    import legal
    import oncall
    from contracts import bill_period
    from domain import evaluate_breach
    from store import GAP_KIND, STORE, iso, utc_now

    found = what_is_there()
    if found:
        raise RefusedDatabase(
            "This database is not empty (it holds " + ", ".join(found) + "). "
            "The demo goes into a new database only: give --db a path that "
            "does not exist yet.")

    # To the second (3.41.0): the record keeps readings' times to the
    # second, and at a reading every 5 minutes the week is read from it, so
    # the alerts open at the very time of their readings.
    now = (now or utc_now()).replace(microsecond=0)
    rng = random.Random(seed_value)
    owner_password = password or secrets.token_urlsafe(12)
    operator_password = secrets.token_urlsafe(12)

    steps = DAYS * 24 * 60 // EVERY_MINUTES
    first = now - timedelta(minutes=EVERY_MINUTES * (steps - 1))

    # Marked first, so that even a seed that fails half way leaves a
    # database that knows it is a demo and backs up accordingly.
    mark = {"seeded_at": iso(now), "company": COMPANY, "history_from": iso(first)}
    STORE._db.put(*DEMO_MARK, mark)
    # Gaps in data are counted from the week's first reading (3.40.0), not
    # from whenever the store first asked: the proof report reads from it.
    STORE._db.put("meta", "gap_log_since", {"at": iso(first)})
    STORE._gap_since = None

    # Nothing is ever mailed to a made-up address.
    for _, address in (OWNER, OPERATOR, VIEWER):
        STORE.suppress_address(address, "bounced", "Demo address: never mailed.")
    STORE.suppress_address(f"accounts@{DOMAIN}", "bounced", "Demo address: never mailed.")

    tenant = STORE.create_tenant(
        company_name=COMPANY, contact_name=OWNER[0], contact_phone=CONTACTS[0][1],
        contact_email=f"accounts@{DOMAIN}", plan="growth",
        industry_vertical="restaurant",
    )
    tid = tenant.tenant_id
    STORE._db.put(*DEMO_MARK, dict(mark, tenant_id=tid))

    owner = STORE.create_user(tid, OWNER[1], OWNER[0], "owner", owner_password)
    STORE.create_user(tid, OPERATOR[1], OPERATOR[0], "operator", operator_password)
    STORE.create_user(tid, VIEWER[1], VIEWER[0], "viewer", secrets.token_urlsafe(16))

    sites = {name: STORE.create_site(tid, f"{COMPANY.split(' &')[0]} {name}", address)
             for name, address in SITES}

    handled_at = steps - 3 * 24 * 60 // EVERY_MINUTES   # three days ago
    sensors = {}
    for site_name, sensor_id, where, kind in UNITS:
        sensor = STORE.register_sensor(
            sensor_id=sensor_id, tenant_id=tid, industry_vertical="restaurant",
            location_name=f"{site_name} / {where}")
        # Watched since the week began (3.40.0): registered "now", its
        # proof of monitoring said "0.2 hours watched" beside a week of
        # readings.
        sensor.registered_at = first
        STORE._db.put("sensor", sensor_id, sensor.to_row())
        STORE.assign_sensor_to_site(sensor, sites[site_name].site_id)
        spec = KINDS[kind]
        if spec["above"] is not None or spec["below"] is not None:
            STORE.set_sensor_overrides(sensor, spec["above"], spec["below"])
        above, below = sensor.bounds()
        for step in range(steps):
            temp = _temperature(rng, kind, step)
            if sensor_id == HANDLED and handled_at <= step < handled_at + HANDLED_FOR // EVERY_MINUTES:
                # Door left open after a delivery: an hour and a half warm,
                # the stock still frozen solid.
                temp = _handled_shape((step - handled_at) * EVERY_MINUTES)
            elif sensor_id == HANDLED and handled_at + HANDLED_FOR // EVERY_MINUTES <= step < handled_at + 2 * HANDLED_FOR // EVERY_MINUTES:
                # Then back down over the next hour and a half, not at once.
                temp = round(min(temp + 9.0 * (1 - (step - handled_at - HANDLED_FOR // EVERY_MINUTES) * EVERY_MINUTES / HANDLED_FOR), 9.5), 1)
            if sensor_id == STILL_OPEN and step >= steps - WARMING_FOR // EVERY_MINUTES - 1:
                # Warming now, and still warm at the last reading.
                temp = _warming_shape((step - (steps - 1)) * EVERY_MINUTES)
            breached = evaluate_breach("restaurant", temp, above, below) is not None
            STORE.record_reading(sensor, temp, round(rng.uniform(40, 60), 1),
                                 breached, at=first + timedelta(minutes=EVERY_MINUTES * step))
        STORE.record_sensor_health(sensor, round(rng.uniform(55, 98)), round(rng.uniform(60, 95)))
        sensors[sensor_id] = sensor

    # The week's history is made up at a reading every half hour, which is
    # not a silence (3.40.0): each half hour was written down as a gap in
    # data, some 6,700 of them. None of it was a real silence, so none is
    # kept. A running demo is fed more often than gaps are written (feed).
    for rec_id, row in STORE._db.rows(GAP_KIND):
        if row.get("tenant_id") == tid:
            STORE._db.delete(GAP_KIND, rec_id)

    # The breach that was handled, opened at its first reading out of
    # range (3.39.0): it opened at the second, 21°F, so "out since 9:54"
    # sat beside a 9:24 reading of 12.5°F marked out of range. 21°F is its
    # worst.
    sensor = sensors[HANDLED]
    opened = first + timedelta(minutes=EVERY_MINUTES * handled_at)
    incident = STORE.open_incident(
        tenant_id=tid, sensor=sensor, temperature_fahrenheit=12.5,
        breach_details="Too warm: 12.5°F (limit 10°F).",
        sms_text=f"{sensor.location_name} at {COMPANY} is 12.5°F, 2.5°F above the limit.",
        sms_dispatch_source="fallback_template", opened_at=opened)
    # Texted to the people on call, as a real alert is (3.37.0): the pack
    # said "nobody was on the list to text" beside an alert answered in 7
    # minutes. Made-up receipts for the made-up numbers below.
    incident.out_since = opened
    incident.peak_f, incident.peak_at = 21.0, opened + timedelta(minutes=30)
    incident.sms_fanout = [
        {"channel": "sms", "contact_name": name, "to": phone, "delivered": True,
         "status": "delivered", "provider_sid": f"DEMO-SM-{n:04d}", "demo": True}
        for n, (name, phone) in enumerate(CONTACTS, 1)]
    who = f"{OPERATOR[0]} <{OPERATOR[1]}>"
    incident, _ = STORE.acknowledge_incident(incident, who)
    incident.acknowledged_at = opened + timedelta(minutes=7)
    # The note says the stock was kept, so the answer is recorded as kept
    # with it (3.37.0): the demo's /today asked "Food kept or Thrown
    # away?" about a freezer whose own note already said "Kept."
    STORE.record_corrective_action(
        incident, who,
        "Door left ajar after the morning delivery. Closed it, checked the "
        "gasket, probed the stock: everything still frozen solid. Kept.",
        disposition="unaffected")
    incident = STORE.resolve_incident(incident, who)
    # Closed just after its first reading back in range, an hour and a half
    # after it went out (3.39.0: opened half an hour earlier, at the first
    # reading out, it would otherwise close before its last warm one).
    incident.resolved_at = opened + timedelta(minutes=92)
    # Written the moment it closed, not the moment the demo was seeded.
    answered = opened + timedelta(minutes=90)
    incident.corrective_action_at = answered
    incident.food_answered_at = answered
    incident.food_history = [dict(row, at=iso(answered)) for row in incident.food_history]
    STORE._db.put("incident", incident.incident_id, incident.to_row())

    # Who gets called, taking turns a week at a time. Before the open
    # alert (3.38.0): opened with nobody on the list, its pack said "nobody
    # was on the list to text" while "Who was told" named the person
    # phoned.
    contacts = [STORE.add_contact(tid, name, phone, escalation_order=n)
                for n, (name, phone) in enumerate(CONTACTS, 1)]
    tz = oncall.zone("America/Chicago")
    starts = now.astimezone(tz).date()
    if oncall.handover_at(starts, oncall.clock("08:00"), tz) > now:
        starts -= timedelta(days=1)
    rotation = oncall.Rotation(
        tenant_id=tid, timezone="America/Chicago", handover="08:00", shift_days=7,
        starts_on=starts.isoformat(), members=[c.contact_id for c in contacts],
        updated_at=iso(now))
    STORE._db.put(oncall.ROTATION_KIND, rotation.rec_id, rotation.to_row())

    # The problem still open: warm for the last hour, and nobody has
    # answered yet. Texted to whoever a real alert texts now, the person on
    # call this week, as the handled one was. Opened at the reading taken
    # then (3.39.0): it said "Reading at detection 46.5°F" beside its own
    # 43.5°F at that time. 46.5°F is its worst, the latest.
    sensor = sensors[STILL_OPEN]
    still = STORE.open_incident(
        tenant_id=tid, sensor=sensor, temperature_fahrenheit=43.5,
        breach_details="Too warm: 43.5°F (limit 41°F).",
        sms_text=f"{sensor.location_name} at {COMPANY} is 43.5°F, 2.5°F above the limit.",
        sms_dispatch_source="fallback_template",
        opened_at=now - timedelta(minutes=60))
    still.out_since = still.opened_at
    still.peak_f, still.peak_at = 46.5, now
    still.sms_fanout = [
        {"channel": "sms", "contact_name": person.full_name, "to": person.phone,
         "delivered": True, "status": "delivered", "provider_sid": f"DEMO-SM-{n:04d}", "demo": True}
        for n, person in enumerate(STORE.sms_recipients(tenant), len(CONTACTS) + 1)]
    STORE._db.put("incident", still.incident_id, still.to_row())

    # A signed contract, its first month billed, and paid.
    sub = STORE.create_subscription(
        tenant=tenant, term_years=1, escalator_percent=5.0, annual_prepay=False,
        add_ons=[], purchase_order="JV-PO-1042", auto_renew=True,
        signed_by=OWNER[0], started_at=now - timedelta(days=DAYS + 1))
    invoice = bill_period(tenant, sub, 0)
    if invoice is None:
        raise RuntimeError("The demo's first invoice could not be billed.")
    invoice, _ = STORE.settle_invoice(invoice, "DEMO-WIRE-0001")

    STORE.record_audit(tid, f"{owner.full_name} <{owner.email}>", "owner",
                       "contract.signed", "1-year term, prices rise 5% a year, monthly.")
    STORE.record_audit(tid, who, "operator", "incident.resolved",
                       f"{incident.incident_id} on {HANDLED}.")

    # A business a week in has done its first day (3.38.0): the demo
    # still said "Getting started 3 of 4" and "Agreements to accept"
    # beside a signed contract and a paid invoice. The owner accepted the
    # terms live today, by their hashes, as the console's Accept button
    # records them, and sent a test alert on day one.
    owner_label = f"{owner.full_name} <{owner.email}>"
    signed_at = now - timedelta(days=DAYS + 1)
    accepted = ", ".join(f"{slug}@{legal.document(slug)['sha256'][:12]}"
                         for slug in sorted(legal.DOCUMENTS))
    for action, detail, at in (
            ("legal.accepted", f"{OWNER[0]} accepted v{legal.TERMS_VERSION}: {accepted}", signed_at),
            (firstday.SENT, f"Test alert: {len(CONTACTS)} of {len(CONTACTS)} sent. "
                            "No incident was opened.", first + timedelta(hours=2))):
        entry = STORE.record_audit(tid, owner_label, "owner", action, detail)
        entry.at = at
        STORE._db.put("audit", entry.entry_id, entry.to_row())

    return {
        "tenant_id": tid,
        "company_name": COMPANY,
        "owner_email": OWNER[1],
        "owner_password": owner_password,
        "operator_email": OPERATOR[1],
        "operator_password": operator_password,
        "units": len(UNITS),
        "sites": len(SITES),
        "readings": len(UNITS) * steps,
        "invoice": invoice.number,
        "invoice_state": invoice.state,
    }

# The still-open alert's unit stays warm while its alert is open, at or
# under its worst reading so far, so "46.5°F is its worst" stays true.
STILL_OPEN_WARM = (45.6, 46.5)
# Once somebody says it is fixed it cools this much a reading (3.41.0),
# never more: it went from 46.5°F to 37.8°F in five minutes.
COOLS_PER_READING = 2.5

_feed_rng = random.Random()


def feed(now: Optional[datetime] = None) -> int:
    """A fresh reading for every demo unit that is due one (3.40.0).

    Called by every sweep (scheduler.py) on a demo database only, so the
    demo is as fresh half an hour after it was seeded as at once. Written
    straight into the store, never through the reading routes: they open
    alerts and text people, and these readings are part of the made-up
    story, not news. No reading is billed (a plan is priced by units, and
    nothing here counts usage), no alert opens, and nobody is texted.

    The healthy units carry on their daily swell. The unit whose alert is
    still open stays warm while it is open and nobody has said it is
    fixed. Once somebody has, it cools, and its alert closes by itself as
    the product's own does, with no texts (a demo database sends none:
    notifications.py). Returns how many readings were written.
    """
    from domain import back_to_normal, evaluate_breach
    from store import STORE, utc_now

    mark = STORE._db.get(*DEMO_MARK)
    if not mark:
        return 0
    tenant = STORE.get_tenant(str(mark.get("tenant_id") or ""))
    if tenant is None:
        tenant = next((t for t in STORE.list_tenants() if t.company_name == COMPANY), None)
    if tenant is None:
        return 0
    now = now or utc_now()
    try:
        history_from = datetime.fromisoformat(str(mark.get("history_from")).replace("Z", "+00:00"))
    except ValueError:
        history_from = now
    step = (now - history_from).total_seconds() / 60.0 / EVERY_MINUTES
    due = timedelta(minutes=LIVE_EVERY_MINUTES)
    written = 0
    for _, sensor_id, _, kind in UNITS:
        sensor = STORE.get_sensor(sensor_id)
        if sensor is None or sensor.tenant_id != tenant.tenant_id:
            continue
        if sensor.last_seen is not None and now - sensor.last_seen < due:
            continue
        open_alert = None
        if sensor_id == STILL_OPEN:
            open_alert = STORE.latest_unresolved_incident(sensor_id, tenant.tenant_id)
        if open_alert is not None and open_alert.fix_reported_at is None:
            temp = round(_feed_rng.uniform(*STILL_OPEN_WARM), 1)
        else:
            temp = _temperature(_feed_rng, kind, step,
                                door_chance=DOOR_CHANCE_HALF_HOUR * LIVE_EVERY_MINUTES / 30)
            # Said to be fixed, it comes down over a few readings
            # (3.41.0), never from 46.5°F to 37.8°F at once.
            if open_alert is not None and sensor.last_temperature is not None:
                temp = round(max(temp, sensor.last_temperature - COOLS_PER_READING), 1)
        above, below = sensor.bounds()
        breached = evaluate_breach("restaurant", temp, above, below) is not None
        STORE.record_reading(sensor, temp, round(_feed_rng.uniform(40, 60), 1),
                             breached, at=now)
        written += 1
        if open_alert is not None and open_alert.fix_reported_at is not None \
                and back_to_normal(sensor, temp):
            from telemetry import close_if_back_to_normal

            close_if_back_to_normal(tenant, sensor, temp)
    return written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed a made-up restaurant group into a new, empty database.")
    parser.add_argument(
        "--db", required=True,
        help="Path of the database to create. Must be new or empty; a "
             "database holding anything is refused.")
    parser.add_argument(
        "--password",
        help="The owner's password. Without it one is generated and printed.")
    parser.add_argument("--url", default=os.environ.get("PUBLIC_BASE_URL", "")
                        or "http://127.0.0.1:8080",
                        help="Where the app will be served, for the printed link.")
    args = parser.parse_args(argv)
    if args.password is not None and len(args.password) < 12:
        parser.error("--password must be at least 12 characters.")

    # Before anything imports the store: db.py fixes its path at import.
    os.environ["CYBERLOGIX_DB_PATH"] = os.path.abspath(args.db)

    from store import STORE

    try:
        made = seed(password=args.password)
    except RefusedDatabase as refused:
        print(f"Refused: {refused}", file=sys.stderr)
        return 2
    finally:
        STORE._db.close()

    print(f"Seeded {made['company_name']} into {args.db}:")
    print(f"  {made['sites']} sites, {made['units']} units, "
          f"{made['readings']:,} readings over {DAYS} days")
    print("  one breach handled, one problem open now, an on-call rotation,")
    print(f"  and invoice {made['invoice']} ({made['invoice_state']}).")
    print()
    print("Serve it with this database, then sign in:")
    print(f"  CYBERLOGIX_DB_PATH={os.path.abspath(args.db)} python main.py")
    print(f"  {args.url.rstrip('/')}/console")
    print(f"  Owner:    {made['owner_email']}  password: {made['owner_password']}")
    print(f"  Operator: {made['operator_email']}  password: {made['operator_password']}")
    print()
    print("Its backups go to demo-backups/ beside it, never to "
          "CYBERLOGIX_BACKUP_DIR.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
