"""Everything must survive a restart, exactly.

The store keeps a working set in memory and writes through to SQLite. A
field that is dropped in `to_row`, defaulted differently in `from_row`, or
mutated without a write, is data that vanishes on the next deploy — and
nothing in normal operation reveals it, because the in-memory copy is
right until the process ends.
"""

import pytest

def rebuilt(path):
    from db import Database
    from store import HubStore

    return HubStore(db=Database(path))


def test_every_entity_survives_a_restart_field_for_field(tmp_path):
    path = str(tmp_path / "durable.db")
    first = rebuilt(path)

    tenant = first.create_tenant("Blue Harbor", "Dana", "+15550100",
                                 "ops@x.com", "growth")
    first.set_temperature_unit(tenant, "C")
    site = first.create_site(tenant.tenant_id, "Boca", "Boca Raton, FL")
    sensor = first.register_sensor("FRZ-1", tenant.tenant_id, "restaurant",
                                   "Walk-In", external_device_sn="ELITECH-1")
    first.assign_sensor_to_site(sensor, site.site_id)
    first.set_sensor_overrides(sensor, above=41.0, below=33.0)
    first.record_sensor_health(sensor, battery_percent=17.0, signal_percent=64.0)
    first.record_reading(sensor=sensor, temperature_fahrenheit=44.0,
                         humidity_percent=52.0, breached=True)
    incident = first.open_incident(
        tenant_id=tenant.tenant_id, sensor=sensor,
        temperature_fahrenheit=44.0, breach_details="too warm",
        sms_text="EMERGENCY", sms_dispatch_source="fallback_template")
    first.issue_ack_token(incident)
    contact = first.add_contact(tenant.tenant_id, "Night Manager", "+15550111",
                                escalation_order=2, site_id=site.site_id)
    hook = first.add_webhook(tenant.tenant_id, "pagerduty", "ROUTINGKEY123",
                             label="Rotation", site_id=site.site_id)
    partner = first.create_partner("Coastal", "Ray", "ray@x.com", 25.0)
    first.assign_partner(tenant, partner.partner_id)
    invoice = first.create_invoice(
        tenant, [{"kind": "subscription", "description": "x", "quantity": 2,
                  "unit_price_usd": 999.0, "amount_usd": 1998.0}],
        purchase_order="PO-7")
    first.settle_invoice(invoice, "WIRE-1", 500.0)
    anchor = first.anchor_chain(tenant.tenant_id, "FRZ-1", "a" * 64, 1)
    user = first.create_user(tenant.tenant_id, "o@x.com", "Owner", "owner",
                             "correct-horse-battery")

    second = rebuilt(path)

    t2 = second.get_tenant(tenant.tenant_id)
    assert t2.company_name == "Blue Harbor"
    assert t2.temperature_unit == "C"
    assert t2.partner_id == partner.partner_id
    assert t2.api_key == tenant.api_key

    s2 = second.get_sensor("FRZ-1")
    assert (s2.site_id, s2.external_device_sn) == (site.site_id, "ELITECH-1")
    assert (s2.override_above, s2.override_below) == (41.0, 33.0)
    assert (s2.battery_percent, s2.signal_percent) == (17.0, 64.0)
    assert s2.battery_low is True

    r2 = second.readings_for("FRZ-1")
    assert len(r2) == 1
    assert (r2[0].temperature_fahrenheit, r2[0].humidity_percent,
            r2[0].breached) == (44.0, 52.0, True)
    assert r2[0].reading_id == first.readings_for("FRZ-1")[0].reading_id

    i2 = second.get_incident(incident.incident_id)
    assert i2.breach_details == "too warm"
    assert i2.ack_token == incident.ack_token, "the keypress secret was lost"
    # Timestamps are stored to the second, deliberately: the vault hashes
    # the ISO form, so changing the precision would invalidate every
    # attestation ever issued.
    assert i2.opened_at.replace(microsecond=0) == \
        incident.opened_at.replace(microsecond=0)

    c2 = second.get_contact(contact.contact_id)
    assert (c2.site_id, c2.escalation_order) == (site.site_id, 2)

    h2 = second.get_webhook(hook.webhook_id)
    assert (h2.kind, h2.target, h2.label, h2.site_id) == (
        "pagerduty", "ROUTINGKEY123", "Rotation", site.site_id)

    p2 = second.get_partner(partner.partner_id)
    assert p2.commission_percent == 25.0
    assert second.partner_by_key(partner.api_key) is not None

    v2 = second.get_invoice(invoice.invoice_id)
    assert v2.number == invoice.number
    assert v2.state == "part_paid"
    assert v2.amount_paid_usd == 500.0
    assert v2.balance_usd == 1498.0
    assert v2.purchase_order == "PO-7"
    assert v2.lines[0]["amount_usd"] == 1998.0

    a2 = second.anchors_for_sensor("FRZ-1")
    assert len(a2) == 1 and a2[0].chain_head == anchor.chain_head

    u2 = [u for u in second._users.values() if u.user_id == user.user_id][0]
    assert u2.email == "o@x.com" and u2.role == "owner"
    from store import verify_password

    assert verify_password("correct-horse-battery", u2.password_hash), (
        "the password hash did not survive the restart"
    )


def test_a_field_edited_through_the_api_is_written_through(tmp_path, api,
                                                           operator_factory):
    """The in-memory copy is right until the process ends, which is exactly
    how a missing write escapes notice."""
    from store import STORE

    headers, tenant, _ = operator_factory()
    site = api.post("/api/sites", headers=headers,
                    json={"name": "Original"}).json()
    api.patch(f"/api/sites/{site['site_id']}", headers=headers,
              json={"name": "Renamed", "address": "New Address"})
    contact = api.post("/api/contacts", headers=headers,
                       json={"full_name": "P", "phone": "+15550001"}).json()
    api.patch(f"/api/contacts/{contact['contact_id']}", headers=headers,
              json={"escalation_order": 9, "active": False})

    # Read the persisted rows directly, bypassing the in-memory copy.
    row = STORE._db.get("site", site["site_id"])
    assert row["name"] == "Renamed" and row["address"] == "New Address"
    row = STORE._db.get("contact", contact["contact_id"])
    assert row["escalation_order"] == 9 and row["active"] is False


def test_eviction_is_mirrored_into_the_database(tmp_path):
    """The ring buffer drops its oldest entry; the table must not keep it."""
    from store import MAX_READINGS_PER_SENSOR

    path = str(tmp_path / "evict.db")
    store = rebuilt(path)
    tenant = store.create_tenant("A", "n", "+1", "a@x.com", "growth")
    sensor = store.register_sensor("S", tenant.tenant_id, "restaurant", "W")
    for n in range(MAX_READINGS_PER_SENSOR + 25):
        store.record_reading(sensor=sensor, temperature_fahrenheit=30.0,
                             humidity_percent=None, breached=False)

    assert len(store.readings_for("S")) == MAX_READINGS_PER_SENSOR
    assert store._db.count("reading") == MAX_READINGS_PER_SENSOR, (
        "evicted rows were left behind and the table grows without bound"
    )
    assert len(rebuilt(path).readings_for("S")) == MAX_READINGS_PER_SENSOR



def test_readings_in_the_same_second_keep_their_order_across_a_restart(tmp_path):
    """Otherwise the vault reports tampering that never happened.

    Timestamps are second-granular, so a burst of readings inside one
    second is a run of ties. The vault chains readings in order, so if a
    reload resolves those ties differently the chain head changes and a
    perfectly honest record fails its own attestation.
    """
    from vault import chain_head

    path = str(tmp_path / "ties.db")
    first = rebuilt(path)
    tenant = first.create_tenant("A", "n", "+1", "a@x.com", "growth")
    sensor = first.register_sensor("S", tenant.tenant_id, "restaurant", "W")

    stamp = None
    for temp in (30.0, 31.0, 32.5, 33.0, 34.0, 35.0):
        reading = first.record_reading(
            sensor=sensor, temperature_fahrenheit=temp,
            humidity_percent=None, breached=False, at=stamp)
        stamp = stamp or reading.recorded_at      # every one in one second

    before = [r.temperature_fahrenheit for r in first.readings_for("S")]
    head_before = chain_head(first.readings_for("S"))

    second = rebuilt(path)
    after = [r.temperature_fahrenheit for r in second.readings_for("S")]
    head_after = chain_head(second.readings_for("S"))

    assert after == before, f"order changed on reload: {before} -> {after}"
    assert head_after == head_before, (
        "the chain head moved without anybody touching a reading, which "
        "would report an honest record as tampered with"
    )


def test_this_sessions_new_store_methods_survive_a_restart(tmp_path):
    """Atomicity that only holds in memory is not atomicity.

    `claim_sensor_seat`, `open_incident_once` and the escalation claim
    all decide by reading the working set. If any of what they read were
    not written through, a restart would hand back a seat that is taken,
    open a second incident for a fault already being handled, or ring the
    on-call phone again for a call that was already placed.
    """
    from db import Database
    from store import VOICE_REDIAL_COOLDOWN_MINUTES, HubStore, SeatClaimRefused

    path = str(tmp_path / "restart.db")

    first = HubStore(db=Database(path))
    tenant = first.create_tenant("Acme", "Dana", "+15550100", "d@x.com",
                                 "enterprise")
    sensor = first.claim_sensor_seat(
        sensor_id="FRIDGE-01", tenant_id=tenant.tenant_id,
        industry_vertical="pharmacy", location_name="Vaccine fridge",
        max_sensors=1000, external_device_sn="SN-XYZ")
    incident, created = first.open_incident_once(
        tenant_id=tenant.tenant_id, sensor=sensor, temperature_fahrenheit=71.0,
        breach_details="too warm", sms_text="warm",
        sms_dispatch_source="template")
    assert created
    assert first.claim_voice_escalation(
        incident, redial_after_minutes=VOICE_REDIAL_COOLDOWN_MINUTES)
    assert first.acknowledge_incident(incident, "Dana Reyes <d@x.com>")[1]

    small = first.create_tenant("Tiny", "D", "+15550100", "t@x.com", "trial")
    cap = small.entitlements()["max_sensors"]
    for i in range(cap):
        first.claim_sensor_seat(
            sensor_id=f"T-{i}", tenant_id=small.tenant_id,
            industry_vertical="pharmacy", location_name="x", max_sensors=cap)
    probe_before = int(first._next_id("PROBE").split("-")[1])
    del first

    second = HubStore(db=Database(path))

    reopened = second.get_incident(incident.incident_id)
    assert reopened.acknowledged_by == "Dana Reyes <d@x.com>"
    assert reopened.voice_escalated_at is not None

    # The claims still refuse a second taker on the other side of a restart.
    assert second.acknowledge_incident(reopened, "Somebody Else")[1] is False
    assert second.claim_voice_escalation(reopened) is False
    assert second.open_incident_once(
        tenant_id=tenant.tenant_id, sensor=second.get_sensor("FRIDGE-01"),
        temperature_fahrenheit=72.0, breach_details="still warm",
        sms_text="warm", sms_dispatch_source="template")[1] is False

    # The seat cap counts what is on disk, not what this process happens
    # to remember.
    with pytest.raises(SeatClaimRefused):
        second.claim_sensor_seat(
            sensor_id="T-OVERFLOW", tenant_id=small.tenant_id,
            industry_vertical="pharmacy", location_name="x", max_sensors=cap)
    with pytest.raises(SeatClaimRefused):
        second.claim_sensor_seat(
            sensor_id="T-0", tenant_id=small.tenant_id,
            industry_vertical="pharmacy", location_name="x", max_sensors=cap)

    assert int(second._next_id("PROBE").split("-")[1]) > probe_before
