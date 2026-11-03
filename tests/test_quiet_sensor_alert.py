"""A sensor that stops reporting gets somebody told.

The agreement says: "If a sensor goes quiet for 30 minutes, we tell you it
is offline." Until this, nothing did. The sweep noticed and wrote it in its
own result, which nobody reads at 3am, and the next mention was the weekly
email. A power cut is when a freezer fails and also when its sensor or its
gateway goes dark, so a silent sensor is exactly the one to text about.
"""

from datetime import timedelta

import pytest

import automation
from store import STORE, SENSOR_OFFLINE_AFTER_MINUTES, utc_now


@pytest.fixture()
def texts(monkeypatch):
    sent = []

    def fake_send(to, body, tenant_id=None):
        sent.append({"to": to, "body": body, "tenant_id": tenant_id})
        return {"channel": "sms", "to": to, "delivered": True, "status": "queued",
                "provider_sid": "SM-1", "detail": "ok"}

    monkeypatch.setattr(automation, "send_sms", fake_send)
    return sent


def _quiet(sensor_id, minutes=SENSOR_OFFLINE_AFTER_MINUTES + 15):
    sensor = STORE.get_sensor(sensor_id)
    sensor.last_seen = utc_now() - timedelta(minutes=minutes)
    return sensor


def _pulse(api, headers, sensor_id, temp=30.0):
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": sensor_id, "temperature_fahrenheit": temp})
    assert resp.status_code == 200, resp.text


def test_a_sensor_gone_quiet_is_texted_about_once(api, tenant_factory, sensor_factory, texts):
    headers, tenant = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant", location="Kitchen")
    _pulse(api, headers, "FRZ-1")
    _quiet("FRZ-1")
    t = STORE.get_tenant(tenant["tenant_id"])

    first = automation.sweep_tenant(t)

    assert len(texts) == 1
    assert texts[0]["to"] == "+1-555-0100"
    assert "FRZ-1" in texts[0]["body"]
    assert any(a["action"] == "sensor_offline_alerted" for a in first["actions"])

    # Still quiet on the next pass: not texted again.
    automation.sweep_tenant(t)
    assert len(texts) == 1


def test_it_is_told_again_after_the_next_silence(api, tenant_factory, sensor_factory, texts):
    headers, tenant = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    _pulse(api, headers, "FRZ-1")
    _quiet("FRZ-1")
    t = STORE.get_tenant(tenant["tenant_id"])
    automation.sweep_tenant(t)

    _pulse(api, headers, "FRZ-1")
    automation.sweep_tenant(t)
    assert len(texts) == 1          # back, so nothing to say
    _quiet("FRZ-1", minutes=SENSOR_OFFLINE_AFTER_MINUTES + 5)
    automation.sweep_tenant(t)
    assert len(texts) == 2


def test_the_once_survives_a_restart(api, tenant_factory, sensor_factory, texts):
    headers, tenant = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    _pulse(api, headers, "FRZ-1")
    sensor = _quiet("FRZ-1")
    STORE._db.put("sensor", sensor.sensor_id, sensor.to_row())
    automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))
    assert len(texts) == 1

    STORE._forget_locked()
    STORE.load()
    automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))
    assert len(texts) == 1


def test_a_sensor_that_never_reported_is_not_texted_about(tenant_factory, sensor_factory, texts):
    """Registered and not yet installed is not "gone quiet"."""
    headers, tenant = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")

    automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))

    assert texts == []


def test_a_suspended_account_is_not_texted_about(api, tenant_factory, sensor_factory, texts):
    """Its readings are refused, so every sensor on it goes quiet."""
    headers, tenant = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    _pulse(api, headers, "FRZ-1")
    _quiet("FRZ-1")
    t = STORE.get_tenant(tenant["tenant_id"])
    STORE.set_suspended(t, True)

    automation.sweep_tenant(t)

    assert texts == []


def test_a_whole_site_going_dark_is_one_text_not_one_per_sensor(api, tenant_factory,
                                                                 sensor_factory, texts):
    """A gateway losing power takes every sensor behind it with it."""
    headers, tenant = tenant_factory()
    for n in range(5):
        sensor_factory(headers, sensor_id=f"FRZ-{n}", vertical="restaurant")
        _pulse(api, headers, f"FRZ-{n}")
        _quiet(f"FRZ-{n}")

    automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))

    assert len(texts) == 1
    assert "5" in texts[0]["body"]
    assert len(texts[0]["body"]) <= 160


def test_our_own_downtime_is_not_read_as_the_sensors_going_quiet(
        api, tenant_factory, sensor_factory, texts, monkeypatch):
    """After a deploy or an outage of ours, every sensor's last reading is
    old because nothing was here to take one. The first sweep, now seconds
    after a start, must not text every business about it; a sensor still
    silent half an hour after we are back is texted as usual."""
    import scheduler

    headers, tenant = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant", location="Kitchen")
    _pulse(api, headers, "FRZ-1")
    _quiet("FRZ-1", minutes=120)          # we were down for two hours
    t = STORE.get_tenant(tenant["tenant_id"])

    monkeypatch.setattr(scheduler, "_listening_since", utc_now() - timedelta(seconds=5))
    result = automation.sweep_tenant(t)
    assert texts == []
    # Still shown as offline on the console: only the text waits.
    assert result["sensors_offline"] == 1

    monkeypatch.setattr(scheduler, "_listening_since",
                        utc_now() - timedelta(minutes=SENSOR_OFFLINE_AFTER_MINUTES + 1))
    automation.sweep_tenant(t)
    assert len(texts) == 1
