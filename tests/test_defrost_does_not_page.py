"""A freezer's own defrost does not wake anybody; a failure still does.

Commercial freezers warm up on a timer to melt the coil, two to four times
a day, and every one crossed the limit and texted the whole roster. The
classifier that tells a defrost from a failure existed, and only the
forecast read it. The breach path now asks it, strictly: a learned,
recovering rhythm, on time, not overrunning, not running hot. Anything
else pages exactly as before.
"""

from datetime import timedelta

import pytest

import telemetry
from store import STORE, utc_now

HOUR = 60


@pytest.fixture()
def texts(monkeypatch):
    sent = []

    def fake(to, body, tenant_id=None):
        sent.append(to)
        return {"channel": "sms", "to": to, "delivered": True, "status": "queued",
                "provider_sid": "SM-1", "detail": "ok"}

    monkeypatch.setattr(telemetry, "send_sms", fake)
    return sent


def _history(spell_started_ago, interval=8 * HOUR, cycles=3, warm=20, hot=40.0,
             current_hot=40.0):
    """Earlier defrosts every `interval` minutes, and the current one so far.

    Minutes are counted back from now. Each earlier spell is `warm`
    minutes over the 32°F line and back to 0°F; the current one started
    `spell_started_ago` minutes ago and is still going.
    """
    sensor = STORE.get_sensor("FRZ-1")
    now = utc_now()
    points = []
    start = spell_started_ago + 26 * HOUR
    for n in range(cycles):
        begins = spell_started_ago + (cycles - n) * interval
        points += [(m, 0.0) for m in range(start, begins, -30) if m > begins]
        points += [(begins - k, hot) for k in range(0, warm + 1, 10)]
        start = begins - warm - 10
    points += [(m, 0.0) for m in range(start, spell_started_ago, -30)
               if m > spell_started_ago]
    points += [(m, current_hot) for m in range(spell_started_ago, 0, -10)]
    for ago, temp in sorted(points, key=lambda p: -p[0]):
        STORE.record_reading(sensor=sensor, temperature_fahrenheit=temp,
                             humidity_percent=None, breached=temp > 32.0,
                             at=now - timedelta(minutes=ago))


def _pulse(api, headers, temp):
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "FRZ-1", "temperature_fahrenheit": temp})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture()
def freezer(tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant",
                   location="Walk-in freezer")
    return headers


def test_a_defrost_on_schedule_pages_nobody(api, freezer, texts):
    _history(spell_started_ago=10)

    body = _pulse(api, freezer, 40.0)

    assert body["status"] == "DEFROST_CYCLE"
    assert "every 8.0 hours" in body["message"]
    assert texts == []
    assert STORE.latest_unresolved_incident("FRZ-1") is None
    # Still on the record as over the line: a compliance log does not get
    # to forget an excursion because it was expected.
    assert STORE.readings_for("FRZ-1")[-1].breached is True


def test_its_very_first_warm_reading_pages_nobody_either(api, freezer, texts):
    """The classifier ignores a spell under four minutes old; this must not."""
    _history(spell_started_ago=0)   # nothing of the current spell yet

    assert _pulse(api, freezer, 40.0)["status"] == "DEFROST_CYCLE"
    assert texts == []


def test_a_defrost_that_does_not_end_pages(api, freezer, texts):
    """Usual length 20 minutes; still warm at an hour is a stuck defrost."""
    _history(spell_started_ago=60)

    body = _pulse(api, freezer, 40.0)

    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert len(texts) == 1


def test_a_cycle_hotter_than_its_defrosts_pages(api, freezer, texts):
    _history(spell_started_ago=10, current_hot=60.0)

    assert _pulse(api, freezer, 60.0)["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert len(texts) == 1


def test_a_warm_spell_at_the_wrong_time_pages(api, freezer, texts):
    """Every eight hours, and this one came after three."""
    _history(spell_started_ago=10)
    # Slide the earlier cycles five hours later: still every eight hours
    # among themselves, but the last one was three hours ago.
    for reading in STORE.readings_for("FRZ-1"):
        if reading.recorded_at < utc_now() - timedelta(minutes=30):
            reading.recorded_at += timedelta(minutes=5 * HOUR)

    assert _pulse(api, freezer, 40.0)["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"


def test_with_no_learned_rhythm_it_pages(api, freezer, texts):
    _history(spell_started_ago=10, cycles=2)

    assert _pulse(api, freezer, 40.0)["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert len(texts) == 1


def test_a_freezer_with_no_history_pages_on_its_first_breach(api, freezer, texts):
    assert _pulse(api, freezer, 40.0)["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
