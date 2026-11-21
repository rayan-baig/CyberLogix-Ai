""""It's fixed" only closes what is fixed, and every page reads one clock.

The review's cases, from a café's milk fridge:

- "It's fixed" closed an alert while the fridge was still 48.4°F, with no
  question and no note, and 17 seconds later a new alert opened and
  texted everyone again.
- /today said "4 minutes" while the console said "about 1 minute"; an
  alert that closed itself said 6 minutes in its note and 1 minute on its
  card, with a start time 5 minutes late; and during the wait the card
  said BREACHING while Problems said "within its limits".

Now: "It's fixed" on a unit still out of range says so, and closes it
when it is back to normal. "Close anyway" needs a one-line note. Every
close by hand records who and why, and the wait before alerting starts
again from it. And the unit card, Problems, /today and the alert itself
all read the same "out since" and "we text at".
"""

from datetime import timedelta

import pytest

import automation
from domain import _parse, minutes_words
from records import person_name
from store import STORE


@pytest.fixture()
def phone(monkeypatch):
    """Twilio, recording every text it is handed."""
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
def cafe(api, tenant_factory, sensor_factory):
    """Rosa's café: one walk-in fridge on the real 20-minute wait, and Rosa
    signed in as its owner."""
    headers, tenant = tenant_factory(plan="growth", company_name="Rosa's Corner Cafe")
    sensor_factory(headers, sensor_id="WALK-IN", vertical="restaurant",
                   location="Kitchen / Walk-in fridge", kind="fridge",
                   alert_after=None)
    STORE.add_contact(tenant["tenant_id"], "Mia Lopez", "+15550111")
    boot = api.post("/api/accounts/bootstrap", headers=headers, json={
        "email": "rosa@cafe.example", "full_name": "Rosa Test",
        "password": "correct-horse-battery"})
    assert boot.status_code in (200, 201), boot.text
    login = api.post("/api/accounts/login", json={
        "email": "rosa@cafe.example", "password": "correct-horse-battery"})
    assert login.status_code == 200, login.text
    rosa = {"Authorization": "Bearer " + login.json()["token"]}
    return headers, rosa, STORE.get_tenant(tenant["tenant_id"])


def pulse(api, headers, temp, sensor_id="WALK-IN"):
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


def open_alert(api, headers, temp=48.4):
    """In range, then out for the whole 20-minute wait: an alert opens."""
    pulse(api, headers, 37.0)
    backdate("WALK-IN", 30)
    pulse(api, headers, temp)
    backdate("WALK-IN", 22)
    raised = pulse(api, headers, temp)
    assert raised["status"] == "CRITICAL_CATASTROPHE_TRIGGERED", raised
    return raised["incident_id"]


def near(a, b):
    """The same moment, to the second the API writes times in."""
    return abs((a - b).total_seconds()) < 1


def overview(api, rosa):
    resp = api.get("/api/console/overview", headers=rosa)
    assert resp.status_code == 200, resp.text
    return resp.json()


def unit_row(data, sensor_id="WALK-IN"):
    return next(s for s in data["sensors"] if s["sensor_id"] == sensor_id)


# --- "It's fixed" while it is still warm ------------------------------------


def test_its_fixed_on_a_fridge_still_warm_does_not_close_it(api, cafe, phone):
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers)

    resp = api.post(f"/api/voice/resolve/{incident_id}", headers=rosa, json={})

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "still_out_of_range"
    assert detail["message"] == "It's still 48.4°F. We'll close it when it's back under 41°F."
    assert detail["close_anyway_needs_note"] is True
    assert "Moved the food to another fridge" in detail["reasons"]
    incident = STORE.get_incident(incident_id)
    assert incident.resolved_at is None, "an alert on a fridge at 48.4°F was closed"
    # Rosa has it: the calls stop, and it closes itself once it is cold.
    assert incident.acknowledged_by == "Rosa Test <rosa@cafe.example>"
    assert incident.fix_reported_by == "Rosa Test <rosa@cafe.example>"


def test_it_closes_itself_once_back_to_normal_in_her_name(api, cafe, phone):
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers)
    api.post(f"/api/voice/resolve/{incident_id}", headers=rosa, json={})

    back = pulse(api, headers, 38.0)

    incident = STORE.get_incident(incident_id)
    assert incident.resolved_at is not None
    assert incident.resolved_by == "Rosa Test <rosa@cafe.example>"
    assert back["back_to_normal"]["note"].startswith("Rosa said it was fixed. Back to normal (38°F)")
    row = next(i for i in overview(api, rosa)["incidents"] if i["incident_id"] == incident_id)
    assert row["resolved_by_name"] == "Rosa"
    assert row["back_to_normal_on_its_own"] is False


def test_close_anyway_needs_a_note(api, cafe, phone):
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers)

    bare = api.post(f"/api/voice/resolve/{incident_id}", headers=rosa,
                    json={"close_anyway": True})

    assert bare.status_code == 422, bare.text
    assert "note" in bare.json()["detail"]
    assert STORE.get_incident(incident_id).resolved_at is None


def test_close_anyway_records_who_and_why_for_the_inspector(api, cafe, phone):
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers)

    resp = api.post(f"/api/voice/resolve/{incident_id}", headers=rosa, json={
        "close_anyway": True, "note": "Moved the food to another fridge"})

    assert resp.status_code == 200, resp.text
    assert "we alert again" in resp.json()["message"]
    incident = STORE.get_incident(incident_id)
    assert incident.resolved_by == "Rosa Test <rosa@cafe.example>"
    assert incident.close_note == "Moved the food to another fridge"
    assert incident.closed_out_of_range is True
    assert incident.close_temperature_f == 48.4
    # What was done is the corrective action, when none was written yet.
    assert incident.corrective_action == "Moved the food to another fridge"

    today = incident.resolved_at.date().isoformat()
    log = api.get(f"/api/autopilot/inspector-log?start={today}&end={today}", headers=rosa)
    assert log.status_code == 200, log.text
    body = log.json()
    breach = next(b for u in body["units"] for b in u["breaches"]
                  if b["incident_id"] == incident_id)
    assert breach["closed_by"] == "Rosa Test <rosa@cafe.example>"
    assert breach["close_note"] == "Moved the food to another fridge"
    assert breach["closed_out_of_range"] is True


def test_a_normal_close_records_who_and_the_temperature(api, cafe, phone):
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers)
    api.post(f"/api/voice/acknowledge/{incident_id}", headers=rosa, json={})
    pulse(api, headers, 40.6)     # under the line, not yet back with room

    resp = api.post(f"/api/voice/resolve/{incident_id}", headers=rosa, json={})

    assert resp.status_code == 200, resp.text
    incident = STORE.get_incident(incident_id)
    assert incident.resolved_by == "Rosa Test <rosa@cafe.example>"
    assert incident.close_note == "Fixed, 40.6°F when closed."
    assert incident.closed_out_of_range is False


# --- no new alert seconds after a close by hand -------------------------------


def test_no_new_alert_or_text_seconds_after_close_anyway(api, cafe, phone):
    """The review: closed at 48.4°F, a new alert and texts 17 seconds later."""
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers)
    texts_before = len(phone["sms"])
    api.post(f"/api/voice/resolve/{incident_id}", headers=rosa, json={
        "close_anyway": True, "note": "Called for a repair"})

    still = pulse(api, headers, 48.4)
    again = pulse(api, headers, 49.0)

    assert still["status"] == "WATCHING", still
    assert again["status"] == "WATCHING", again
    assert len(phone["sms"]) == texts_before, "everyone was texted again"
    assert STORE.latest_unresolved_incident("WALK-IN") is None
    assert "closed by hand" in still["message"]
    # A reading far past the limit after the close is a different matter:
    # tests/test_held_close_and_live_counts.py.


def test_it_alerts_again_once_its_wait_has_passed_after_the_close(api, cafe, phone):
    headers, rosa, tenant = cafe
    incident_id = open_alert(api, headers)
    api.post(f"/api/voice/resolve/{incident_id}", headers=rosa, json={
        "close_anyway": True, "note": "Called for a repair"})
    pulse(api, headers, 48.4)
    closed = STORE.get_incident(incident_id)
    row = unit_row(overview(api, rosa))
    assert row["condition"]["state"] == "watching"
    assert near(_parse(row["condition"]["alert_at"]), closed.resolved_at + timedelta(minutes=20))

    # 19 minutes after the close: still waiting, the sweep raises nothing.
    closed.resolved_at -= timedelta(minutes=19)
    assert not [a for a in automation.sweep_tenant(STORE.get_tenant(tenant.tenant_id))
                .get("actions", []) if a.get("action") == "held_alert_raised"]
    assert STORE.latest_unresolved_incident("WALK-IN") is None

    # 21 minutes after: it is alerted on, by the sweep, without a new reading.
    closed.resolved_at -= timedelta(minutes=2)
    automation.sweep_tenant(STORE.get_tenant(tenant.tenant_id))
    again = STORE.latest_unresolved_incident("WALK-IN")
    assert again is not None and again.incident_id != incident_id


def test_a_genuinely_new_failure_after_a_fix_still_alerts_at_once(api, cafe, phone):
    """The hold is for the spell somebody looked at, not the next one."""
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers)
    api.post(f"/api/voice/acknowledge/{incident_id}", headers=rosa, json={})
    pulse(api, headers, 37.0)
    api.post(f"/api/voice/resolve/{incident_id}", headers=rosa, json={})

    later = pulse(api, headers, 60.0)    # far past the limit, a new spell

    assert later["status"] == "CRITICAL_CATASTROPHE_TRIGGERED", later


# --- one clock --------------------------------------------------------------


def test_during_the_wait_every_view_says_watching_with_one_time(api, cafe, phone):
    headers, rosa, _ = cafe
    pulse(api, headers, 37.0)
    backdate("WALK-IN", 10)
    pulse(api, headers, 44.0)
    first_over = STORE.readings_for("WALK-IN")[-1].recorded_at
    backdate("WALK-IN", 6)
    first_over -= timedelta(minutes=6)
    pulse(api, headers, 44.5)

    data = overview(api, rosa)
    row = unit_row(data)
    cond = row["condition"]
    assert cond["state"] == "watching"
    assert near(_parse(cond["out_since"]), first_over)
    assert near(_parse(cond["alert_at"]), first_over + timedelta(minutes=20))
    # No alert yet, so Problems has nothing open and must read the same row.
    assert not [i for i in data["incidents"] if i["state"] != "resolved"]
    assert data["summary"]["sensors_watching"] == 1
    # The reading's own answer agrees to the second.
    held = pulse(api, headers, 44.5)
    assert held["waiting"]["out_since"] == cond["out_since"]
    assert held["waiting"]["alert_at"] == cond["alert_at"]


def test_the_alert_the_card_and_today_share_one_out_since(api, cafe, phone):
    headers, rosa, _ = cafe
    pulse(api, headers, 37.0)
    backdate("WALK-IN", 30)
    pulse(api, headers, 48.4)
    first_over = STORE.readings_for("WALK-IN")[-1].recorded_at
    backdate("WALK-IN", 22)
    first_over -= timedelta(minutes=22)
    incident_id = pulse(api, headers, 48.4)["incident_id"]

    data = overview(api, rosa)
    alert = next(i for i in data["incidents"] if i["incident_id"] == incident_id)
    row = unit_row(data)
    assert row["condition"]["state"] == "alert"
    assert row["condition"]["incident_id"] == incident_id
    # One start time, the first reading over, not the moment it opened.
    assert near(_parse(alert["out_since"]), first_over)
    assert row["condition"]["out_since"] == alert["out_since"]
    assert _parse(alert["opened_at"]) > _parse(alert["out_since"])
    assert 21.5 <= alert["out_minutes"] <= 23.0


def test_an_alert_that_closed_itself_says_the_same_minutes_on_note_and_card(
        api, cafe, phone):
    """The note said 6 minutes; the card said 1 and started 5 minutes late."""
    headers, rosa, _ = cafe
    pulse(api, headers, 37.0)
    backdate("WALK-IN", 10)
    pulse(api, headers, 60.0)     # far past: alerts at once
    incident = STORE.latest_unresolved_incident("WALK-IN")
    # Out since 6 minutes ago, alert opened 1 minute ago.
    incident.out_since = incident.opened_at - timedelta(minutes=5)
    incident.out_since -= timedelta(minutes=1)
    incident.opened_at -= timedelta(minutes=1)

    pulse(api, headers, 37.0)

    closed = next(i for i in overview(api, rosa)["incidents"]
                  if i["incident_id"] == incident.incident_id)
    assert closed["back_to_normal_on_its_own"] is True
    assert f"after {minutes_words(closed['out_minutes'])}" in closed["resolved_note"]
    assert minutes_words(closed["out_minutes"]) == "6 minutes"
    assert closed["out_until"] == closed["resolved_at"]


# --- calm words for a new unit, names not addresses ---------------------------


def test_a_new_sensor_is_waiting_for_its_first_reading_and_not_tuned(
        api, tenant_factory, sensor_factory):
    headers, tenant = tenant_factory()
    sensor_factory(headers, sensor_id="NEW-1", vertical="restaurant",
                   location="Walk-in freezer", kind="freezer", alert_after=None)
    data = api.get("/api/console/overview", headers=headers).json()
    row = unit_row(data, "NEW-1")
    assert row["condition"]["state"] == "waiting_first"
    assert row["breaching"] is False
    assert row["limit_own"] is False, "the freezer's own limit is not a tuning"

    tuned = api.post("/api/licenses/me/sensors/NEW-1/thresholds",
                     headers=headers, json={"danger_above": 5.0})
    assert tuned.status_code == 200, tuned.text
    assert unit_row(api.get("/api/console/overview", headers=headers).json(),
                    "NEW-1")["limit_own"] is True


def test_screens_get_a_first_name_not_an_address(api, cafe, phone):
    headers, rosa, _ = cafe
    incident_id = open_alert(api, headers)
    api.post(f"/api/voice/acknowledge/{incident_id}", headers=rosa, json={})

    alert = next(i for i in overview(api, rosa)["incidents"]
                 if i["incident_id"] == incident_id)

    assert alert["acknowledged_by"] == "Rosa Test <rosa@cafe.example>"   # the record
    assert alert["acknowledged_by_name"] == "Rosa"                       # the screen


@pytest.mark.parametrize("label, name", [
    ("Rosa Test <rosa@cafe.example>", "Rosa"),
    ("rosa.test@cafe.example", "Rosa"),
    ("Mia Lopez, by text reply (+15550111)", "Mia"),
    ("phone keypad (+15550111)", "Somebody on the phone"),
    ("API key", "API key"),
    (None, None),
])
def test_person_name(label, name):
    assert person_name(label) == name
