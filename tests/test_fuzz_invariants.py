"""Random operation sequences, with the invariants checked after each one.

Hand-written tests find the bugs somebody thought of. This finds the ones
nobody did, because it has no idea what it is testing — it performs
arbitrary operations in arbitrary order and then asserts only the things
that must be true regardless.

It has already earned its place: it found that deleting a site left its
on-call contacts pointing at a dead site, which silently removed them
from every alert while the console still showed them as on call.

Kept small enough to run in the normal suite. The full harness lives
outside it and runs hundreds of rounds at a hundred operations each.
"""

import random

import pytest

VERTICALS = ["restaurant", "cybersecurity", "medical_lab", "superyacht",
             "pharmacy", "wine_and_art", "cannabis"]

NASTY = ["", " ", "'", '"', "<script>", "=cmd", "../..", "a" * 200, "é🍦",
         "NaN", "-1"]


def invariants(STORE):
    """Everything that must hold no matter what just happened."""
    broken = []

    ids = ([s.sensor_id for s in STORE._sensors.values()]
           + list(STORE._incidents) + list(STORE._invoices)
           + list(STORE._sites) + list(STORE._contacts)
           + list(STORE._webhooks) + list(STORE._anchors)
           + [r.reading_id for b in STORE._readings.values() for r in b])
    if len(ids) != len(set(ids)):
        broken.append("an identifier is shared by two records")

    for bucket in STORE._readings.values():
        for reading in bucket:
            value = reading.temperature_fahrenheit
            if value != value or abs(value) == float("inf"):
                broken.append(f"non-finite reading stored ({reading.reading_id})")
                break

    for sensor in STORE._sensors.values():
        if sensor.tenant_id not in STORE._tenants:
            broken.append(f"sensor {sensor.sensor_id} has no tenant")
        if sensor.site_id and sensor.site_id not in STORE._sites:
            broken.append(f"sensor {sensor.sensor_id} points at a dead site")

    for contact in STORE._contacts.values():
        if contact.site_id and contact.site_id not in STORE._sites:
            broken.append(
                f"contact {contact.contact_id} points at a dead site, so it "
                "is in no roster while still showing as on call"
            )

    for hook in STORE._webhooks.values():
        if hook.site_id and hook.site_id not in STORE._sites:
            broken.append(f"webhook {hook.webhook_id} points at a dead site")

    for invoice in STORE._invoices.values():
        if invoice.balance_usd < 0 or invoice.total_usd < 0:
            broken.append(f"{invoice.number} has a negative figure")
        if abs(sum(l["amount_usd"] for l in invoice.lines)
               - invoice.subtotal_usd) > 0.011:
            broken.append(f"{invoice.number} lines do not sum to its subtotal")

    numbers = [i.number for i in STORE._invoices.values()]
    if len(numbers) != len(set(numbers)):
        broken.append("two invoices share a number")

    return broken


@pytest.mark.parametrize("seed", range(12))
def test_random_sequences_hold_every_invariant(seed, api, operator_factory):
    from store import STORE

    rng = random.Random(seed)
    headers, _, _ = operator_factory(
        company_name=f"Fuzz {seed}", email=f"fuzz{seed}@example.com")
    sensors, sites, incidents, invoices, contacts = [], [], [], [], []

    def text():
        return rng.choice(NASTY) if rng.random() < 0.4 else f"N{rng.randint(1, 999)}"

    def register():
        sid = f"S{rng.randint(1, 12)}"
        if api.post("/api/licenses/me/sensors", headers=headers, json={
                "sensor_id": sid, "industry_vertical": rng.choice(VERTICALS),
                "location_name": text() or "Loc"}).status_code == 201:
            sensors.append(sid)

    def pulse():
        if not sensors:
            return
        key = rng.choice(["temperature_fahrenheit", "temperature_celsius"])
        body = api.post("/api/sensor-pulse", headers=headers, json={
            "sensor_id": rng.choice(sensors),
            key: rng.uniform(-80, 200)})
        if body.status_code == 200 and body.json().get("incident_id"):
            incidents.append(body.json()["incident_id"])

    def make_site():
        r = api.post("/api/sites", headers=headers,
                     json={"name": text() or "Site"})
        if r.status_code == 201:
            sites.append(r.json()["site_id"])

    def place():
        if sites and sensors:
            api.post(f"/api/sites/{rng.choice(sites)}/sensors", headers=headers,
                     json={"sensor_id": rng.choice(sensors)})

    def add_contact():
        r = api.post("/api/contacts", headers=headers, json={
            "full_name": text() or "P",
            "phone": "+1555" + str(rng.randint(1000, 9999)),
            "site_id": rng.choice(sites) if sites and rng.random() < 0.6 else None})
        if r.status_code == 201:
            contacts.append(r.json()["contact_id"])

    def add_hook():
        api.post("/api/webhooks", headers=headers, json={
            "kind": rng.choice(["slack", "generic", "pagerduty"]),
            "target": "https://hooks.example.com/" + str(rng.randint(1, 10**6)),
            "site_id": rng.choice(sites) if sites and rng.random() < 0.6 else None})

    def drop_site():
        if sites:
            api.delete(f"/api/sites/{sites.pop(rng.randrange(len(sites)))}",
                       headers=headers)

    def ack():
        if incidents:
            api.post(f"/api/voice/acknowledge/{rng.choice(incidents)}",
                     headers=headers, json={})

    def bill():
        r = api.post("/api/invoices", headers=headers, json={
            "include_add_ons": rng.choice(["", "assurance,vault"]),
            "include_setup": rng.random() < 0.5, "period_days": 30})
        if r.status_code == 201:
            invoices.append(r.json()["invoice_id"])

    def pay():
        if invoices:
            api.post(f"/api/invoices/{rng.choice(invoices)}/paid",
                     headers=headers,
                     json={"reference": "R", "amount_usd": rng.choice(
                         [None, 1.0, 10 ** 6])})

    def retire():
        if sensors:
            api.delete(
                f"/api/licenses/me/sensors/{sensors.pop(rng.randrange(len(sensors)))}",
                headers=headers)

    def read():
        api.get(rng.choice(["/api/console/overview", "/api/sites",
                            "/api/contacts", "/api/invoices", "/api/webhooks",
                            "/api/assurance/cover", "/api/vault/attestation"]),
                headers=headers)

    ops = [register, pulse, make_site, place, add_contact, add_hook,
           drop_site, ack, bill, pay, retire, read]

    for step in range(45):
        rng.choice(ops)()
        broken = invariants(STORE)
        assert not broken, (
            f"seed {seed}, step {step}: " + "; ".join(broken)
        )
