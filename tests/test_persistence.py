"""State survives a restart."""

import pytest

from db import Database
from store import HubStore


@pytest.fixture()
def shared_db(tmp_path):
    """A real file, so two stores can be opened over the same data."""
    return str(tmp_path / "restart.db")


def build(path):
    return HubStore(db=Database(path))


def test_tenant_and_sensor_survive_a_restart(shared_db):
    first = build(shared_db)
    tenant = first.create_tenant(
        "Blue Harbor", "Dana", "+15550100", "ops@example.com", "growth"
    )
    first.register_sensor(
        "RACK-01", tenant.tenant_id, "cybersecurity", "Hall B", "ELITECH-1"
    )

    second = build(shared_db)
    restored = second.get_tenant(tenant.tenant_id)
    assert restored is not None
    assert restored.company_name == "Blue Harbor"
    assert second.tenant_by_key(tenant.api_key).tenant_id == tenant.tenant_id
    assert second.seat_count(tenant.tenant_id) == 1
    # The BYOD serial index is rebuilt, not just the sensor row.
    assert second.sensor_by_device("ELITECH-1").sensor_id == "RACK-01"


def test_readings_and_incident_survive_with_their_state(shared_db):
    first = build(shared_db)
    tenant = first.create_tenant(
        "Blue Harbor", "Dana", "+15550100", "ops@example.com", "growth"
    )
    sensor = first.register_sensor(
        "RACK-01", tenant.tenant_id, "cybersecurity", "Hall B"
    )
    for temp in (68.0, 70.0, 94.0):
        first.record_reading(sensor, temp, 50.0, temp > 78.0)

    incident = first.open_incident(
        tenant.tenant_id, sensor, 94.0, "too hot", "ALERT", "gemini"
    )
    first.acknowledge_incident(incident, "Dana Reyes")

    second = build(shared_db)
    readings = second.readings_for("RACK-01")
    assert [r.temperature_fahrenheit for r in readings] == [68.0, 70.0, 94.0]
    assert readings[-1].breached is True

    restored = second.get_incident(incident.incident_id)
    assert restored.acknowledged_by == "Dana Reyes"
    assert restored.acknowledged_at is not None
    assert restored.public()["state"] == "acknowledged"
    # Acknowledged means it is no longer awaiting escalation.
    assert second.open_incidents(tenant.tenant_id) == []


def test_identifiers_do_not_collide_after_a_restart(shared_db):
    first = build(shared_db)
    tenant = first.create_tenant("A", "n", "+1", "a@example.com", "trial")
    sensor = first.register_sensor("S1", tenant.tenant_id, "restaurant", "loc")
    one = first.open_incident(tenant.tenant_id, sensor, 40.0, "d", "s", "gemini")

    second = build(shared_db)
    two = second.open_incident(
        tenant.tenant_id, second.get_sensor("S1"), 41.0, "d", "s", "gemini"
    )
    assert two.incident_id != one.incident_id


def test_users_and_audit_survive(shared_db):
    first = build(shared_db)
    tenant = first.create_tenant("A", "n", "+1", "a@example.com", "growth")
    user = first.create_user(
        tenant.tenant_id, "dana@example.com", "Dana", "owner", "correct-horse"
    )
    first.record_audit(tenant.tenant_id, "Dana", "owner", "test.action", "did a thing")

    second = build(shared_db)
    restored = second.user_by_email("dana@example.com")
    assert restored is not None
    assert restored.user_id == user.user_id
    assert restored.role == "owner"
    entries = second.audit_for(tenant.tenant_id)
    assert entries[0].action == "test.action"


def test_expired_sessions_are_dropped_on_load(shared_db):
    from datetime import timedelta

    first = build(shared_db)
    tenant = first.create_tenant("A", "n", "+1", "a@example.com", "growth")
    user = first.create_user(
        tenant.tenant_id, "dana@example.com", "Dana", "owner", "correct-horse"
    )
    session = first.start_session(user)
    # Age the token past its lifetime and write it back.
    session.expires_at = session.issued_at - timedelta(hours=1)
    first._db.put("session", session.token, session.to_row())

    second = build(shared_db)
    assert second.session_by_token(session.token) is None


def test_reading_eviction_prunes_the_database(shared_db, monkeypatch):
    import store as store_module

    monkeypatch.setattr(store_module, "MAX_READINGS_PER_SENSOR", 5)
    first = build(shared_db)
    tenant = first.create_tenant("A", "n", "+1", "a@example.com", "growth")
    sensor = first.register_sensor("S1", tenant.tenant_id, "cybersecurity", "loc")

    for index in range(20):
        first.record_reading(sensor, 60.0 + index, 50.0, False)

    # The ring buffer caps memory; the table must not grow past it either.
    assert len(first.readings_for("S1")) == 5
    assert first._db.count("reading") == 5


def test_decommissioning_removes_persisted_readings(shared_db):
    first = build(shared_db)
    tenant = first.create_tenant("A", "n", "+1", "a@example.com", "growth")
    sensor = first.register_sensor("S1", tenant.tenant_id, "cybersecurity", "loc")
    first.record_reading(sensor, 70.0, 50.0, False)
    assert first._db.count("reading") == 1

    first.remove_sensor("S1")
    assert first._db.count("reading") == 0
    assert build(shared_db).get_sensor("S1") is None


def test_backdated_readings_keep_their_time_across_a_restart(shared_db):
    """A history import must not collapse to one timestamp on reload.

    Regression: the demo seeder used to patch recorded_at onto the returned
    object after the row was already written, so the stored copy kept the
    original time and every forecast read as insufficient_data after a
    restart.
    """
    from datetime import timedelta

    from store import utc_now

    first = build(shared_db)
    tenant = first.create_tenant("A", "n", "+1", "a@example.com", "growth")
    sensor = first.register_sensor("S1", tenant.tenant_id, "cybersecurity", "loc")

    now = utc_now()
    for index, temp in enumerate([68.0, 70.0, 72.0, 74.0]):
        first.record_reading(
            sensor, temp, 50.0, False, at=now - timedelta(minutes=(3 - index) * 10)
        )

    second = build(shared_db)
    times = [r.recorded_at for r in second.readings_for("S1")]
    assert len(set(times)) == 4, "timestamps collapsed on reload"
    span = (times[-1] - times[0]).total_seconds() / 60
    assert round(span) == 30
