"""Populate the hub with a realistic estate and serve the console.

A demo aid, not part of the product: it writes backdated telemetry straight
into the store so the forecaster has a real time span to fit, then starts
the server. Run it when you want to show the console with something on it.

    python seed_demo.py              # seed and serve on :8080
    python seed_demo.py --print-key  # also print the tenant API key
"""

from __future__ import annotations

import argparse
import os
from datetime import timedelta
from typing import Dict

from store import STORE, evaluate_breach, iso, utc_now

# Sites the demo estate is spread across, and which sensors sit at each.
# A flat list of seven thermometers is not what a chain actually looks at.
SITES = {
    "Blue Harbor Clubhouse": ("Palm Beach, FL", ["CLUB-WALKIN-1", "BLOOD-07"]),
    "Austin Data Hall": ("Austin, TX", ["RACK-A7"]),
    "Store 118 Boca Raton": ("Boca Raton, FL", ["STORE118-WALKIN"]),
    "Teterboro Hangar": ("Teterboro, NJ", ["HANGAR-4", "MY-AURELIA-ENG"]),
}

# Battery and signal, so the fleet shows a sensor about to go dark as well
# as one that is too warm. REEFER-118 is left unreported on purpose.
HEALTH = {
    "CLUB-WALKIN-1": (86.0, 71.0),
    "RACK-A7": (94.0, 88.0),
    "BLOOD-07": (12.0, 64.0),
    "MY-AURELIA-ENG": (38.0, 22.0),
    "HANGAR-4": (77.0, 90.0),
    "STORE118-WALKIN": (61.0, 55.0),
}

# Each row: sensor, vertical, location, optional BYOD serial, temperature run.
# The runs are shaped to show every state the console can render: a walk-in
# that has already failed, a data hall and an engine bay drifting toward
# their limits, and steady assets that should stay quiet.
FLEET = [
    (
        "CLUB-WALKIN-1", "country_club", "Clubhouse Kitchen / Walk-In", None,
        [30.5, 30.1, 29.8, 30.2, 31.0, 33.5, 36.0, 39.2, 42.8, 45.1, 46.9, 47.4],
    ),
    (
        "RACK-A7", "cybersecurity", "Austin DC / Hall B", None,
        [66.2, 66.9, 67.4, 68.0, 68.9, 69.6, 70.2, 71.1, 71.8, 72.6, 73.4, 74.1],
    ),
    (
        "BLOOD-07", "medical_lab", "Mercy Blood Bank / Cooler 3", None,
        [40.1, 40.0, 39.8, 40.2, 39.9, 40.1, 40.3, 39.7, 40.0, 40.2, 39.9, 40.1],
    ),
    (
        "MY-AURELIA-ENG", "superyacht", "M/Y Aurelia / Engine Bay", "MONNIT-4C:11:AE:90",
        [74.0, 75.2, 76.1, 77.0, 78.4, 79.1, 80.3, 81.2, 82.0, 83.1, 84.0, 85.2],
    ),
    (
        "REEFER-118", "logistics", "Trailer 118 / Cold Chain", "ELITECH-00:1B:44:11",
        [33.1, 33.4, 33.0, 33.6, 33.2, 33.8, 33.5, 33.9, 34.1, 33.7, 34.0, 34.2],
    ),
    (
        "HANGAR-4", "private_aviation", "Teterboro / Bay 4", None,
        [61.0, 61.5, 62.0, 61.8, 62.4, 62.1, 62.8, 63.0, 62.6, 63.2, 63.5, 63.1],
    ),
    (
        "STORE118-WALKIN", "restaurant", "Store 118 / Walk-In", "DICKSON-7A:2C:19",
        [28.1, 28.4, 28.0, 29.2, 31.5, 34.0, 37.8, 41.2, 43.0, 40.1, 34.2, 29.8],
    ),
]

# Incidents on these sensors are seeded already answered, so the ROI panel
# shows a real save rather than an empty period.
ANSWERED = {"STORE118-WALKIN"}

SPACING_MINUTES = 10

DEMO_EMAIL = "dana@blueharbor.example"
DEMO_PASSWORD = "harbor-demo-2026"


def seed() -> Dict[str, str]:
    """Build the demo tenant, its operator and its history."""
    STORE.reset()

    tenant = STORE.create_tenant(
        company_name="Blue Harbor Group",
        contact_name="Dana Reyes",
        contact_phone="+15550100",
        contact_email="ops@blueharbor.example",
        plan="growth",
    )

    now = utc_now()

    # A walk-in that defrosts on a timer, with two days of history so the
    # rhythm is learnable. Everything else in FLEET carries two hours,
    # which is enough for a slope and nowhere near enough for a schedule
    # -- so without this the classifier has nothing to classify and the
    # feature is invisible in the demo.
    #
    # Deliberately a real nuisance: it crosses the line four times, which
    # is what a real freezer does and what the product has to stop
    # phoning somebody about.
    defroster = STORE.register_sensor(
        sensor_id="BACKBAR-1", tenant_id=tenant.tenant_id,
        industry_vertical="restaurant",
        location_name="Harbour Street / Back Bar Freezer",
        external_device_sn=None,
    )
    series = []
    for cycle_start in range(0, 2400, 480):        # every 8 hours
        for minute in range(0, 480, 10):
            # Twenty minutes over the line, then back down to -2.
            series.append((cycle_start + minute,
                           38.0 if minute < 20 else -2.0))
    # ...and it is mid-cycle right now. That is the case worth showing:
    # a unit genuinely above its limit this minute, climbing at a rate
    # the slope reads as a breach within the hour, which the classifier
    # recognises as the same thing this freezer has done every eight
    # hours for two days. Without it the split never appears, because a
    # unit that is currently cold is not at risk and never reaches the
    # card.
    series += [(2400, 20.0), (2410, 31.0), (2420, 38.0)]
    newest = max(m for m, _ in series)
    for minute, temp in series:
        STORE.record_reading(
            sensor=defroster, temperature_fahrenheit=temp,
            humidity_percent=48.0,
            breached=evaluate_breach("restaurant", temp) is not None,
            at=now - timedelta(minutes=(newest - minute)),
        )

    for sensor_id, vertical, location, serial, temps in FLEET:
        sensor = STORE.register_sensor(
            sensor_id=sensor_id,
            tenant_id=tenant.tenant_id,
            industry_vertical=vertical,
            location_name=location,
            external_device_sn=serial,
        )

        breach_reason = None
        peak_temp = temps[-1]
        for index, temp in enumerate(temps):
            reason = evaluate_breach(vertical, temp)
            # Backdate at write time so the forecaster sees a real slope
            # rather than a column of samples stamped at the same instant.
            STORE.record_reading(
                sensor=sensor,
                temperature_fahrenheit=temp,
                humidity_percent=52.0,
                breached=reason is not None,
                at=now - timedelta(
                    minutes=(len(temps) - 1 - index) * SPACING_MINUTES
                ),
            )
            if reason is not None:
                # Keep the worst excursion, so a run that recovers still
                # leaves the incident it caused on the books.
                breach_reason = reason
                peak_temp = temp

        if breach_reason is not None:
            incident = STORE.open_incident(
                tenant_id=tenant.tenant_id,
                sensor=sensor,
                temperature_fahrenheit=peak_temp,
                breach_details=breach_reason,
                sms_text=(
                    f"EMERGENCY ALERT: {location} sensor {sensor_id} reported "
                    f"critical temperature {peak_temp}F. Immediate physical "
                    "inspection required."
                ),
                sms_dispatch_source="fallback_template",
                # Open long enough that the console shows it as escalation-due.
                opened_at=now - timedelta(minutes=18),
            )
            STORE.record_sms_delivery(
                incident,
                {
                    "channel": "sms",
                    "to": tenant.contact_phone,
                    "delivered": False,
                    "status": "not_configured",
                    "provider_sid": None,
                    "detail": "Twilio is not configured in this demo environment.",
                },
            )
            if sensor_id in ANSWERED:
                STORE.acknowledge_incident(
                    incident, "Marco Diaz <marco@blueharbor.example>"
                )

    for name, (address, sensor_ids) in SITES.items():
        site = STORE.create_site(tenant.tenant_id, name, address)
        for sensor_id in sensor_ids:
            sensor = STORE.get_sensor(sensor_id)
            if sensor is not None:
                STORE.assign_sensor_to_site(sensor, site.site_id)

    for sensor_id, (battery, signal) in HEALTH.items():
        sensor = STORE.get_sensor(sensor_id)
        if sensor is not None:
            STORE.record_sensor_health(sensor, battery, signal)

    # An on-call roster, including one manager scoped to a single store, so
    # the site-scoped alerting is visible rather than theoretical.
    boca = next(
        (s for s in STORE.sites_for(tenant.tenant_id)
         if s.name == "Store 118 Boca Raton"),
        None,
    )
    STORE.add_contact(
        tenant.tenant_id, "Dana Reyes", "+15550100", escalation_order=1
    )
    STORE.add_contact(
        tenant.tenant_id, "Marco Diaz", "+15550102", escalation_order=2
    )
    if boca is not None:
        STORE.add_contact(
            tenant.tenant_id, "Priya Raman", "+15550103",
            escalation_order=1, site_id=boca.site_id,
        )

    owner = STORE.create_user(
        tenant_id=tenant.tenant_id,
        email=DEMO_EMAIL,
        full_name="Dana Reyes",
        role="owner",
        password=DEMO_PASSWORD,
    )
    STORE.create_user(
        tenant_id=tenant.tenant_id,
        email="sam@blueharbor.example",
        full_name="Sam Cole",
        role="operator",
        password="harbor-demo-2026",
    )

    # A little history so the audit trail and cost panel are not empty.
    actor = f"{owner.full_name} <{owner.email}>"
    STORE.record_audit(
        tenant.tenant_id, actor, "owner", "account.bootstrap",
        "First owner created with the tenant API key.",
    )
    STORE.record_audit(
        tenant.tenant_id, actor, "owner", "account.invited",
        "Added sam@blueharbor.example as operator.",
    )
    STORE.record_audit(
        tenant.tenant_id, "Sam Cole <sam@blueharbor.example>", "operator",
        "incident.resolved", "INC-000004 on REEFER-118.",
    )
    STORE.record_audit(
        tenant.tenant_id, "Autopilot", "machine", "sensor.registered",
        "MY-AURELIA-ENG bound to MONNIT-4C:11:AE:90.",
    )

    for field, amount in (
        ("ai_calls", 14),
        ("ai_cache_hits", 22),
        ("sms_sent", 9),
        ("voice_calls", 2),
        ("sms_suppressed", 1),
    ):
        STORE.bump_usage(tenant.tenant_id, field, amount)

    # A crew, with the licence dates a real kitchen has. The estate is
    # not only equipment: the same shift that a freezer sits in has
    # people in it who need a card to be standing there legally, and the
    # console is the only place that holds both halves.
    today = utc_now().date()

    def on(offset: int) -> str:
        return (today + timedelta(days=offset)).strftime("%Y-%m-%d")

    crew = (
        ("Dana Reyes", "Head chef", "+15550100", (
            ("Food protection manager certification", -4, True),
            ("Allergen awareness", 115, False))),
        ("Marco Diaz", "Sous chef", "+15550111", (
            ("Food handler card", 7, True),)),
        ("Priya Nair", "Line cook", "+15550122", (
            ("Food handler card", 30, True), ("First aid", 70, False))),
        ("Tom Ellis", "Driver", "+15550133", (
            ("Commercial driving licence", 41, True),
            ("DOT medical examiner certificate", 14, True))),
        ("Sara Quinn", "Porter", "+15550144", (
            ("Food handler card", 150, True),)),
    )
    staff_ids: Dict[str, str] = {}
    for full_name, role, phone, credentials in crew:
        staff_id = STORE._next_id("STF")
        staff_ids[full_name] = staff_id
        STORE._db.put("staff", staff_id, {
            "staff_id": staff_id, "tenant_id": tenant.tenant_id,
            "full_name": full_name, "role": role, "phone": phone,
            "email": full_name.split()[0].lower() + "@blueharbor.example",
            "active": full_name != "Sara Quinn",
            "added_at": iso(utc_now()),
        })
        for name, offset, required in credentials:
            credential_id = STORE._next_id("CRD")
            STORE._db.put("credential", credential_id, {
                "credential_id": credential_id,
                "tenant_id": tenant.tenant_id, "staff_id": staff_id,
                "name": name, "expires_on": on(offset), "reference": "",
                "required_to_work": required, "added_at": iso(utc_now()),
            })

    # Sara was dismissed three weeks ago. Payroll removed her; the
    # escalation ladder did not, so a 3am call about a failing walk-in
    # still goes to somebody who handed her keys back. That is the thing
    # no HR system can see and no monitoring product thinks to look for,
    # and the demo estate should show it rather than describe it.
    STORE.add_contact(
        tenant.tenant_id, "Sara Quinn", "+1 555-0144", escalation_order=3,
    )
    offboarding_id = STORE._next_id("OFF")
    STORE._db.put("offboarding", offboarding_id, {
        "offboarding_id": offboarding_id, "tenant_id": tenant.tenant_id,
        "staff_id": staff_ids["Sara Quinn"], "full_name": "Sara Quinn",
        "email": "sara@blueharbor.example", "phone": "+15550144",
        "left_on": on(-21), "reason": "Dismissed",
        "opened_at": iso(utc_now()),
    })

    # What the club carried on paying for her. This is the feature: not
    # what is owed to somebody who left -- payroll does that, because the
    # person chases it -- but what keeps being paid for them, which
    # nobody chases. Three weeks in, it is already real money.
    for label, kind, monthly, stopped, refunded in (
        ("Mobile phone plan", "phone", 55.0, None, 0.0),
        ("Health cover", "benefits", 320.0, None, 0.0),
        ("Scheduling software seat", "software", 25.0, on(-7), 18.0),
        ("Parking permit", "parking", 90.0, None, 0.0),
    ):
        cost_id = STORE._next_id("LEK")
        STORE._db.put("leaver_cost", cost_id, {
            "cost_id": cost_id, "tenant_id": tenant.tenant_id,
            "offboarding_id": offboarding_id, "label": label, "kind": kind,
            "monthly_usd": monthly, "billed_by": "", "reference": "",
            "stopped_on": stopped, "refunded_usd": refunded,
            "added_at": iso(utc_now()),
        })

    # A reseller with this estate on their book, so the partner portal has
    # something to render rather than an empty table.
    partner = STORE.create_partner(
        company_name="Coastal Refrigeration Services",
        contact_name="Ray Okafor",
        contact_email="ray@coastalref.example",
        commission_percent=20.0,
    )
    STORE.assign_partner(tenant, partner.partner_id)

    return {
        "api_key": tenant.api_key,
        "email": DEMO_EMAIL,
        "password": DEMO_PASSWORD,
        "partner_key": partner.api_key,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-key", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    parser.add_argument(
        "--seed-only", action="store_true", help="Seed and exit without serving."
    )
    args = parser.parse_args()

    credentials = seed()
    print(f"Demo estate seeded: {len(FLEET)} sensors across 7 verticals.")
    print(f"Sign in: {credentials['email']} / {credentials['password']}")
    print(f"Partner key: {credentials['partner_key']}")
    if args.print_key:
        print(f"API key: {credentials['api_key']}")
    if args.seed_only:
        return

    import uvicorn

    from main import app

    print(f"Landing page: http://{args.host}:{args.port}/")
    print(f"Console: http://{args.host}:{args.port}/console")
    print(f"Partner portal: http://{args.host}:{args.port}/partners")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
