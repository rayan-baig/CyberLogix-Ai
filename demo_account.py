"""A believable demo business, into an empty database, and nothing else.

    python demo_account.py --db /tmp/cyberlogix-demo.db

Seeds one made-up restaurant group, Juniper & Vine Kitchens: three
sites, twenty units (walk-in freezers and coolers, prep fridges, bar
fridges, an ice-cream freezer, a blast chiller), a week of readings every
half hour, one breach that was handled (acknowledged in minutes, a
corrective action written down, closed), one problem still open, an
on-call rotation, a signed contract and a paid invoice. Then it prints
how to sign in.

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
    "freezer": {"above": None, "below": None, "normal": -1.0, "spread": 1.6},
    "ice_cream": {"above": 10.0, "below": None, "normal": 2.0, "spread": 1.4},
    "cooler": {"above": 41.0, "below": 32.0, "normal": 36.5, "spread": 1.0},
    "fridge": {"above": 41.0, "below": 32.0, "normal": 37.5, "spread": 1.2},
    "chiller": {"above": 41.0, "below": None, "normal": 35.0, "spread": 1.5},
}

HANDLED = "OML-WIF-1"      # the breach somebody dealt with, three days ago
STILL_OPEN = "COM-PREP-2"  # the problem that is open right now

DAYS = 7
EVERY_MINUTES = 30

CONTACTS = [
    ("Morgan Hale", "+15550141"),
    ("Priya Lund", "+15550142"),
    ("Ade Okoro", "+15550143"),
]


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


def _temperature(rng: random.Random, kind: str, step: int) -> float:
    """A healthy unit: its own level, a slow daily swell, a little noise,
    and a door opening now and then."""
    spec = KINDS[kind]
    daily = math.sin(step / (24 * 60 / EVERY_MINUTES) * 2 * math.pi) * spec["spread"] * 0.5
    door = spec["spread"] * 1.2 if rng.random() < 0.04 else 0.0
    return round(spec["normal"] + daily + door + rng.uniform(-0.4, 0.4) * spec["spread"], 1)


def seed(now: Optional[datetime] = None, password: Optional[str] = None,
         seed_value: int = 2026) -> Dict[str, Any]:
    """Build the demo business in the store. Raises RefusedDatabase first
    if the store holds anything at all."""
    import oncall
    from contracts import bill_period
    from domain import evaluate_breach
    from store import STORE, iso, utc_now

    found = what_is_there()
    if found:
        raise RefusedDatabase(
            "This database is not empty (it holds " + ", ".join(found) + "). "
            "The demo goes into a new database only: give --db a path that "
            "does not exist yet.")

    now = now or utc_now()
    rng = random.Random(seed_value)
    owner_password = password or secrets.token_urlsafe(12)
    operator_password = secrets.token_urlsafe(12)

    # Marked first, so that even a seed that fails half way leaves a
    # database that knows it is a demo and backs up accordingly.
    STORE._db.put(*DEMO_MARK, {"seeded_at": iso(now), "company": COMPANY})

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

    owner = STORE.create_user(tid, OWNER[1], OWNER[0], "owner", owner_password)
    STORE.create_user(tid, OPERATOR[1], OPERATOR[0], "operator", operator_password)
    STORE.create_user(tid, VIEWER[1], VIEWER[0], "viewer", secrets.token_urlsafe(16))

    sites = {name: STORE.create_site(tid, f"{COMPANY.split(' &')[0]} {name}", address)
             for name, address in SITES}

    steps = DAYS * 24 * 60 // EVERY_MINUTES
    first = now - timedelta(minutes=EVERY_MINUTES * (steps - 1))
    handled_at = steps - 3 * 24 * 60 // EVERY_MINUTES   # three days ago
    sensors = {}
    for site_name, sensor_id, where, kind in UNITS:
        sensor = STORE.register_sensor(
            sensor_id=sensor_id, tenant_id=tid, industry_vertical="restaurant",
            location_name=f"{site_name} / {where}")
        STORE.assign_sensor_to_site(sensor, sites[site_name].site_id)
        spec = KINDS[kind]
        if spec["above"] is not None or spec["below"] is not None:
            STORE.set_sensor_overrides(sensor, spec["above"], spec["below"])
        above, below = sensor.bounds()
        for step in range(steps):
            temp = _temperature(rng, kind, step)
            if sensor_id == HANDLED and handled_at <= step < handled_at + 3:
                # Door left open after a delivery: three readings warm.
                temp = (36.5, 41.0, 37.0)[step - handled_at]
            if sensor_id == STILL_OPEN and step >= steps - 4:
                # Warming now, and still warm at the last reading.
                temp = (40.0, 43.5, 45.0, 46.5)[step - (steps - 4)]
            breached = evaluate_breach("restaurant", temp, above, below) is not None
            STORE.record_reading(sensor, temp, round(rng.uniform(40, 60), 1),
                                 breached, at=first + timedelta(minutes=EVERY_MINUTES * step))
        STORE.record_sensor_health(sensor, round(rng.uniform(55, 98)), round(rng.uniform(60, 95)))
        sensors[sensor_id] = sensor

    # The breach that was handled.
    sensor = sensors[HANDLED]
    opened = first + timedelta(minutes=EVERY_MINUTES * (handled_at + 1))
    incident = STORE.open_incident(
        tenant_id=tid, sensor=sensor, temperature_fahrenheit=41.0,
        breach_details="Thermal high threshold breached: 41.0°F > 32.0°F limit.",
        sms_text=f"{sensor.location_name} at {COMPANY} is 41°F, 9°F above the limit.",
        sms_dispatch_source="fallback_template", opened_at=opened)
    who = f"{OPERATOR[0]} <{OPERATOR[1]}>"
    incident, _ = STORE.acknowledge_incident(incident, who)
    incident.acknowledged_at = opened + timedelta(minutes=7)
    STORE.record_corrective_action(
        incident, who,
        "Door left ajar after the morning delivery. Closed it, checked the "
        "gasket, probed the stock: everything still frozen solid. Kept.")
    incident = STORE.resolve_incident(incident, who)
    incident.resolved_at = opened + timedelta(minutes=52)
    STORE._db.put("incident", incident.incident_id, incident.to_row())

    # The problem still open: warm for the last hour and a half, and
    # nobody has answered yet.
    sensor = sensors[STILL_OPEN]
    STORE.open_incident(
        tenant_id=tid, sensor=sensor, temperature_fahrenheit=46.5,
        breach_details="Thermal high threshold breached: 46.5°F > 41.0°F limit.",
        sms_text=f"{sensor.location_name} at {COMPANY} is 46.5°F, 5.5°F above the limit.",
        sms_dispatch_source="fallback_template",
        opened_at=now - timedelta(minutes=EVERY_MINUTES * 2))

    # Who gets called, taking turns a week at a time.
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
                       "contract.signed", "1-year term, 5% escalator, monthly.")
    STORE.record_audit(tid, who, "operator", "incident.resolved",
                       f"{incident.incident_id} on {HANDLED}.")

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
