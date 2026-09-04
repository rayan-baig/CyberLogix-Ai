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

from store import STORE, evaluate_breach, utc_now

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

    return {
        "api_key": tenant.api_key,
        "email": DEMO_EMAIL,
        "password": DEMO_PASSWORD,
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
    if args.print_key:
        print(f"API key: {credentials['api_key']}")
    if args.seed_only:
        return

    import uvicorn

    from main import app

    print(f"Console: http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
