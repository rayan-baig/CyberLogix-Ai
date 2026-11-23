"""A quick door-open texts nobody; a unit that STAYS out of range does.

The review's exact case: a fridge at 37°F, one reading of 43°F, then
37.5°F a minute later. Everyone was texted at once and phoned ten minutes
on, though the add form says "we alert if it stays over 41°F". Now a unit
must stay out of range for its wait (20 minutes for a food fridge) before
anybody is texted, a reading far past the limit still alerts at once, and
an alert nobody answered closes itself, with no phone call, once the unit
is back to normal.

Every sensor here is on the real default rule (alert_after=None), not the
"alert on the first reading" setting most of the suite uses.
"""

from datetime import timedelta

import pytest

import automation
import domain
from store import STORE


@pytest.fixture()
def phone(monkeypatch):
    """Twilio, recording every text and call it is handed."""
    import notifications

    log = {"sms": [], "calls": []}

    class _Sent:
        def __init__(self, sid):
            self.sid = sid
            self.status = "queued"

    class _Endpoint:
        def __init__(self, kind):
            self.kind = kind

        def create(self, **kwargs):
            log[self.kind].append(kwargs)
            return _Sent(f"{self.kind}-{len(log[self.kind])}")

    class _Client:
        messages = _Endpoint("sms")
        calls = _Endpoint("calls")

    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")
    monkeypatch.setattr(notifications, "_client", _Client())
    monkeypatch.setattr(notifications, "_client_error", None)
    return log


@pytest.fixture()
def cafe(tenant_factory, sensor_factory):
    """A Growth-style account (calls included) with one milk fridge."""
    headers, tenant = tenant_factory(plan="growth", company_name="Bean There")
    sensor_factory(headers, sensor_id="MILK-1", vertical="restaurant",
                   location="Counter / milk fridge", kind="fridge",
                   alert_after=None)
    STORE.add_contact(tenant["tenant_id"], "Mia Lopez", "+15550111")
    return headers, STORE.get_tenant(tenant["tenant_id"])


def pulse(api, headers, temp, sensor_id="MILK-1"):
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": sensor_id, "temperature_fahrenheit": temp})
    assert resp.status_code == 200, resp.text
    return resp.json()


def backdate(sensor_id, minutes):
    """Move every reading held for the unit this many minutes earlier."""
    for reading in STORE.readings_for(sensor_id):
        reading.recorded_at -= timedelta(minutes=minutes)
    sensor = STORE.get_sensor(sensor_id)
    sensor.last_seen -= timedelta(minutes=minutes)


def sweep(tenant):
    return automation.sweep_tenant(STORE.get_tenant(tenant.tenant_id))


# --- the defaults ---------------------------------------------------------


def test_the_wait_depends_on_the_kind_of_unit(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    fridge = sensor_factory(headers, sensor_id="F-1", vertical="restaurant",
                            kind="fridge", alert_after=None)
    freezer = sensor_factory(headers, sensor_id="Z-1", vertical="restaurant",
                             kind="freezer", alert_after=None)
    vaccine = sensor_factory(headers, sensor_id="V-1", vertical="pharmacy",
                             kind="fridge", alert_after=None)
    waits = {s["sensor_id"]: STORE.get_sensor(s["sensor_id"]).public()["alert_after_minutes"]
             for s in (fridge, freezer, vaccine)}
    assert waits == {"F-1": 20.0, "Z-1": 30.0, "V-1": 10.0}


def test_a_unit_added_before_its_kind_was_kept_is_read_from_its_limit(
        api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="Z-OLD", vertical="restaurant",
                   kind="freezer", alert_after=None)
    sensor = STORE.get_sensor("Z-OLD")
    sensor.unit_kind = None          # as saved before 3.21.0
    assert domain.unit_kind_of(sensor) == "freezer"
    assert domain.alert_after_minutes(sensor) == 30.0


# --- the review's case: a door open for a minute ---------------------------


def test_a_door_open_spike_texts_nobody_and_phones_nobody(api, cafe, phone):
    headers, tenant = cafe
    assert pulse(api, headers, 37.0)["status"] == "nominal"
    backdate("MILK-1", 2)
    held = pulse(api, headers, 43.0)
    assert held["status"] == "WATCHING"
    assert "stays out for 20 minutes" in held["message"]
    backdate("MILK-1", 1)
    assert pulse(api, headers, 37.5)["status"] == "nominal"

    assert phone["sms"] == []
    assert STORE.latest_unresolved_incident("MILK-1") is None

    # Ten, then twenty-five minutes later the sweeps find nothing to do.
    backdate("MILK-1", 10)
    sweep(tenant)
    backdate("MILK-1", 15)
    result = sweep(tenant)
    assert result["voice_calls_placed"] == 0
    assert phone["sms"] == [] and phone["calls"] == []
    # Still on the record as over the line: the log keeps the blip.
    assert [r.breached for r in STORE.readings_for("MILK-1")] == [False, True, False]


def test_a_reading_a_hair_under_the_line_does_not_restart_the_wait(api, cafe, phone):
    """A unit hovering at 40.9, 41.3, 40.8 is not recovering."""
    headers, _ = cafe
    pulse(api, headers, 43.0)
    backdate("MILK-1", 12)
    pulse(api, headers, 40.8)           # in range, but only just
    backdate("MILK-1", 9)
    body = pulse(api, headers, 41.6)
    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED", body
    assert len(phone["sms"]) == 1


# --- a unit that stays out -------------------------------------------------


def test_a_sustained_rise_waits_then_alerts_at_the_grace_time(api, cafe, phone):
    headers, _ = cafe
    pulse(api, headers, 37.0)
    backdate("MILK-1", 1)
    assert pulse(api, headers, 44.0)["status"] == "WATCHING"
    backdate("MILK-1", 19)
    assert pulse(api, headers, 45.0)["status"] == "WATCHING"   # 19 minutes
    assert phone["sms"] == []

    backdate("MILK-1", 1)                                       # 20 minutes
    body = pulse(api, headers, 45.5)
    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert body["alerted_because"] == "out of range for 20 minutes"
    assert len(phone["sms"]) == 1
    incident = STORE.get_incident(body["incident_id"])
    # "How long" is from when it went out, not from when we alerted.
    assert (incident.opened_at - incident.out_since) >= timedelta(minutes=20)


def test_the_sweep_alerts_on_time_even_if_the_sensor_sends_nothing_more(
        api, cafe, phone):
    """A sensor that reports every fifteen minutes, or goes quiet once warm."""
    headers, tenant = cafe
    pulse(api, headers, 46.0)
    backdate("MILK-1", 15)
    assert sweep(tenant)["actions"] == [] or phone["sms"] == []
    assert phone["sms"] == []

    backdate("MILK-1", 6)                                       # 21 minutes
    result = sweep(tenant)
    raised = [a for a in result["actions"] if a["action"] == "held_alert_raised"]
    assert len(raised) == 1 and raised[0]["sensor_id"] == "MILK-1"
    assert len(phone["sms"]) == 1
    # And only once.
    sweep(tenant)
    assert len(phone["sms"]) == 1


def test_a_big_jump_alerts_at_once(api, cafe, phone):
    headers, _ = cafe
    pulse(api, headers, 37.0)
    body = pulse(api, headers, 41.0 + domain.ALERT_AT_ONCE_DEGREES_F)
    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert body["alerted_because"] == "far past its limit"
    assert len(phone["sms"]) == 1


def test_too_cold_far_past_the_lower_limit_alerts_at_once(
        api, tenant_factory, sensor_factory, phone):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="VAX-1", vertical="pharmacy", kind="fridge",
                   alert_after=None)
    assert pulse(api, headers, 34.0, "VAX-1")["status"] == "WATCHING"
    assert pulse(api, headers, 20.0, "VAX-1")["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"


# --- back to normal before anybody answered -------------------------------


def test_recovery_before_the_call_cancels_it_and_closes_the_alert(
        api, cafe, phone, age_incident):
    headers, tenant = cafe
    opened = pulse(api, headers, 60.0)                 # far over: texted at once
    incident_id = opened["incident_id"]
    assert len(phone["sms"]) == 1
    age_incident(incident_id, 3)
    STORE.get_incident(incident_id).out_since -= timedelta(minutes=3)

    back = pulse(api, headers, 37.5)
    assert back["status"] == "nominal"
    incident = STORE.get_incident(incident_id)
    assert incident.resolved_at is not None
    assert incident.acknowledged_by is None         # nobody did
    # 19°F past its limit (3.33.0): not "probably the door", however short.
    assert incident.resolved_note.startswith("Back to normal after 3 minutes, on its own. It went far past its limit")
    assert back["message"] == incident.resolved_note
    # The people texted are told it is over, so nobody drives in.
    assert len(phone["sms"]) == 2
    assert "back to 37.5F after 3 minutes" in phone["sms"][1]["body"]

    # Ten minutes on, the sweep that would have phoned does not.
    age_incident(incident_id, 10)
    result = sweep(tenant)
    assert result["voice_calls_placed"] == 0
    assert phone["calls"] == []
    assert STORE.claim_voice_escalation(incident) is False


def test_just_inside_the_line_holds_the_call_but_keeps_the_alert(
        api, cafe, phone, age_incident):
    headers, tenant = cafe
    incident_id = pulse(api, headers, 60.0)["incident_id"]
    pulse(api, headers, 40.6)                       # in range, by a hair
    assert STORE.get_incident(incident_id).resolved_at is None
    age_incident(incident_id, 11)
    result = sweep(tenant)
    assert result["voice_calls_placed"] == 0
    assert any(a["action"] == "voice_escalation_held" for a in result["actions"])


def test_an_alert_somebody_took_is_theirs_to_close(api, cafe, phone):
    headers, _ = cafe
    incident_id = pulse(api, headers, 60.0)["incident_id"]
    api.post(f"/api/voice/acknowledge/{incident_id}", headers=headers,
             json={"acknowledged_by": "Mia"})
    pulse(api, headers, 37.0)
    incident = STORE.get_incident(incident_id)
    assert incident.resolved_at is None and incident.resolved_note is None


# --- the owner's own setting ----------------------------------------------


def test_an_owner_can_shorten_or_lengthen_the_wait(api, cafe, phone):
    headers, _ = cafe
    resp = api.post("/api/licenses/me/sensors/MILK-1/alert-rule", headers=headers,
                    json={"alert_after_minutes": 5})
    assert resp.status_code == 200, resp.text
    assert resp.json()["alert_after_minutes"] == 5
    assert resp.json()["alert_after_minutes_default"] == 20

    pulse(api, headers, 44.0)
    backdate("MILK-1", 5)
    assert pulse(api, headers, 44.0)["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"

    # Null puts the default back.
    cleared = api.post("/api/licenses/me/sensors/MILK-1/alert-rule",
                       headers=headers, json={"alert_after_minutes": None})
    assert cleared.json()["alert_after_minutes"] == 20


def test_n_readings_in_a_row_can_alert_sooner(api, cafe, phone):
    headers, _ = cafe
    api.post("/api/licenses/me/sensors/MILK-1/alert-rule", headers=headers,
             json={"alert_after_readings": 3})
    assert pulse(api, headers, 44.0)["status"] == "WATCHING"
    assert pulse(api, headers, 44.0)["status"] == "WATCHING"
    body = pulse(api, headers, 44.0)
    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert body["alerted_because"] == "3 readings in a row out of range"


def test_zero_alerts_on_the_first_reading(api, cafe, phone):
    headers, _ = cafe
    api.post("/api/licenses/me/sensors/MILK-1/alert-rule", headers=headers,
             json={"alert_after_minutes": 0})
    assert pulse(api, headers, 42.0)["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"


@pytest.mark.parametrize("body", [{"alert_after_minutes": -1},
                                  {"alert_after_minutes": 10_000},
                                  {"alert_after_readings": 0}])
def test_a_setting_out_of_bounds_is_refused(api, cafe, body):
    headers, _ = cafe
    resp = api.post("/api/licenses/me/sensors/MILK-1/alert-rule",
                    headers=headers, json=body)
    assert resp.status_code == 400


def test_the_setting_is_only_for_your_own_units(api, cafe, tenant_factory):
    other, _ = tenant_factory(company_name="Someone Else")
    resp = api.post("/api/licenses/me/sensors/MILK-1/alert-rule",
                    headers=other, json={"alert_after_minutes": 0})
    assert resp.status_code == 404


def test_the_setting_survives_a_restart(api, cafe):
    headers, _ = cafe
    api.post("/api/licenses/me/sensors/MILK-1/alert-rule", headers=headers,
             json={"alert_after_minutes": 45, "alert_after_readings": 4})
    STORE.forget()
    STORE.load()
    sensor = STORE.get_sensor("MILK-1")
    assert (sensor.alert_after_minutes, sensor.alert_after_readings,
            sensor.unit_kind) == (45, 4, "fridge")


# --- the whole alarm path, end to end, on the real rule -------------------


def test_the_alarm_path_still_works_end_to_end(api, cafe, phone, age_incident):
    """Warm and staying warm: texted at the wait, phoned ten minutes on
    when nobody answers, and answered."""
    headers, tenant = cafe
    pulse(api, headers, 38.0)
    backdate("MILK-1", 1)
    pulse(api, headers, 47.0)
    backdate("MILK-1", 21)
    body = pulse(api, headers, 48.0)
    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert len(phone["sms"]) == 1
    assert phone["sms"][0]["to"] == "+15550111"
    assert body["breach_details"] == "Too warm: 48°F (limit 41°F)."

    # Still warm, nobody answers: the sweep phones after the call wait.
    pulse(api, headers, 49.0)
    age_incident(body["incident_id"], domain.VOICE_ESCALATION_GRACE_MINUTES + 1)
    result = sweep(tenant)
    assert result["voice_calls_placed"] == 1
    assert len(phone["calls"]) == 1
    # Still one alert, one text.
    assert len(phone["sms"]) == 1

    answered = api.post(f"/api/voice/acknowledge/{body['incident_id']}",
                        headers=headers, json={"acknowledged_by": "Mia"})
    assert answered.json()["status"] == "ACKNOWLEDGED"


def test_a_paused_account_is_not_alerted_by_the_sweep_either(api, cafe, phone):
    headers, tenant = cafe
    import retention

    pulse(api, headers, 46.0)
    backdate("MILK-1", 25)
    original = retention.current_pause
    try:
        retention.current_pause = lambda *a, **k: {"resume_at": "2099-01-01"}
        sweep(tenant)
    finally:
        retention.current_pause = original
    assert phone["sms"] == []
