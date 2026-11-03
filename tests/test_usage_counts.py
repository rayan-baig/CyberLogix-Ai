"""How much the product is used: daily totals, and nothing about who.

A few actions are counted where the server does them. A day's row is a
date and some numbers: no account, person, address, cookie or anything
the browser sent. The last 30 days go to the platform operator, at
/api/admin/usage and in the stats MTRX Horizyn's console reads with its
key. Days older than 400 are deleted.
"""

from datetime import timedelta

import pytest

import notifications
import usage
from store import STORE, utc_now


def _today():
    return usage.last_days(1)["daily"][0]


def _totals():
    return usage.last_days(30)["totals"]


def _sign(url, form, token="token"):
    from twilio.request_validator import RequestValidator

    return RequestValidator(token).compute_signature(url, form)


def test_a_sign_up_is_counted(api):
    resp = api.post("/api/signup", json={
        "company_name": "Corner Deli", "full_name": "Ann Petrou",
        "email": "ann@cornerdeli.example", "password": "correct-horse-battery",
        "contact_phone": "+1-555-0133", "industry_vertical": "restaurant"})
    assert resp.status_code == 201
    assert _today()["signups"] == 1
    # A refused one is not.
    again = api.post("/api/signup", json={
        "company_name": "Corner Deli", "full_name": "Ann Petrou",
        "email": "ann@cornerdeli.example", "password": "correct-horse-battery",
        "contact_phone": "+1-555-0133", "industry_vertical": "restaurant"})
    assert again.status_code == 409
    assert _today()["signups"] == 1


def test_sensors_added_and_refused_ones_not(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, "RACK-01")
    sensor_factory(headers, "RACK-02")
    taken = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "RACK-01", "industry_vertical": "cybersecurity",
        "location_name": "Hall B"})
    assert taken.status_code == 409
    assert _today()["sensors_added"] == 2


def test_a_test_alert_is_counted(api, operator_factory, configured_twilio):
    headers, _, _ = operator_factory()
    api.post("/api/contacts", headers=headers,
             json={"full_name": "Night Engineer", "phone": "+15550101"})
    resp = api.post("/api/contacts/test-alert", headers=headers, json={})
    assert resp.status_code == 200, resp.text
    assert _today()["test_alerts"] == 1


@pytest.fixture()
def breach(api, operator_factory, sensor_factory, configured_twilio, monkeypatch):
    """A business with a roster and an open alert."""
    configured_twilio()
    monkeypatch.setattr(notifications, "PUBLIC_BASE_URL", "https://hub.example.com")
    headers, tenant, _ = operator_factory()
    api.post("/api/contacts", headers=headers, json={
        "full_name": "Cook", "phone": "+1-555-0111", "escalation_order": 1})
    api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "WALKIN-1", "industry_vertical": "restaurant",
        "location_name": "Kitchen walk-in"})
    body = api.post("/api/sensor-pulse", headers=headers, json={
        "sensor_id": "WALKIN-1", "temperature_fahrenheit": 58.0}).json()
    return headers, body["incident_id"]


def test_acknowledged_by_the_button(api, breach):
    headers, incident_id = breach
    api.post(f"/api/voice/acknowledge/{incident_id}", headers=headers, json={})
    api.post(f"/api/voice/acknowledge/{incident_id}", headers=headers, json={})
    day = _today()
    assert day["acknowledged_button"] == 1, "the second press acknowledged nothing"
    assert day["acknowledged_text"] == day["acknowledged_keypress"] == 0


def test_acknowledged_by_a_text_reply(api, breach):
    form = {"From": "+15550111", "To": "+15550000", "Body": "1", "MessageSid": "SM1"}
    url = "https://hub.example.com/api/voice/sms-reply"
    resp = api.post("/api/voice/sms-reply", data=form,
                    headers={"X-Twilio-Signature": _sign(url, form)})
    assert resp.status_code == 200
    assert _today()["acknowledged_text"] == 1


def test_acknowledged_by_pressing_one(api, breach, age_incident):
    headers, incident_id = breach
    age_incident(incident_id, minutes=30)
    api.post(f"/api/voice/escalate/{incident_id}", headers=headers)
    token = STORE.get_incident(incident_id).ack_token
    form = {"Digits": "1", "To": "+15550003"}
    url = notifications.acknowledgement_url(incident_id, token)
    resp = api.post(f"/api/voice/keypress/{incident_id}/{token}", data=form,
                    headers={"X-Twilio-Signature": _sign(url, form)})
    assert resp.status_code == 200
    assert _today()["acknowledged_keypress"] == 1
    assert _totals()["acknowledged"] == 1


def test_inspector_logs_and_help_pages(api, operator_factory):
    headers, _, _ = operator_factory()
    assert api.get("/api/autopilot/inspector-log", headers=headers).status_code == 200
    assert api.get("/help").status_code == 200
    assert api.get("/help/sensors").status_code == 200
    day = _today()
    assert day["inspector_logs"] == 1
    assert day["help_views"] == 2


def test_the_pricing_calculator_is_not_counted():
    """It works in the page and never reaches the server, so there is
    nothing to count -- and no call home is added to make one."""
    from pathlib import Path

    script = (Path(__file__).resolve().parent.parent / "static" / "pricing.js").read_text()
    assert "fetch(" not in script and "XMLHttpRequest" not in script
    assert not any("pricing" in event for event in usage.EVENTS)


def test_a_day_holds_numbers_and_nothing_else(api, tenant_factory, sensor_factory):
    headers, tenant = tenant_factory()
    sensor_factory(headers, "RACK-01")
    api.get("/help", headers={"Cookie": "who=dana@example.com",
                              "User-Agent": "dana-phone"})
    [row] = STORE._db.all(usage.KIND)
    assert set(row) <= {"day"} | set(usage.EVENTS)
    assert all(isinstance(v, int) for k, v in row.items() if k != "day")
    flat = str(row)
    for detail in (tenant["tenant_id"], "RACK-01", "dana", "example.com", "testclient"):
        assert detail not in flat


def test_thirty_days_every_day_listed_oldest_first():
    now = utc_now()
    usage.count("signups", now - timedelta(days=29))
    usage.count("signups", now - timedelta(days=30))  # outside the window
    usage.count("help_views", now)
    body = usage.last_days(30, now)
    assert len(body["daily"]) == 30
    assert body["daily"][0]["day"] == (now - timedelta(days=29)).date().isoformat()
    assert body["daily"][-1]["day"] == now.date().isoformat()
    assert body["daily"][0]["signups"] == 1
    assert body["totals"]["signups"] == 1
    assert body["totals"]["help_views"] == 1
    assert sum(d["signups"] for d in body["daily"][1:]) == 0


def test_older_than_400_days_is_deleted():
    now = utc_now()
    usage.count("signups", now - timedelta(days=401))
    usage.count("signups", now - timedelta(days=399))
    assert STORE._db.count(usage.KIND) == 2
    usage.count("help_views", now)  # the first count of a new day prunes
    days = sorted(r["day"] for r in STORE._db.all(usage.KIND))
    assert days == [(now - timedelta(days=399)).date().isoformat(),
                    now.date().isoformat()]
    assert usage.prune(now + timedelta(days=2)) == 1


def test_survives_a_restart():
    usage.count("signups")
    STORE.load()
    assert _today()["signups"] == 1


def test_only_the_platform_operator_sees_it(api, admin_headers, operator_factory):
    headers, _, _ = operator_factory()
    usage.count("signups")
    assert api.get("/api/admin/usage").status_code == 401
    assert api.get("/api/admin/usage", headers=headers).status_code == 401
    body = api.get("/api/admin/usage", headers=admin_headers).json()
    assert body["days"] == 30 and len(body["daily"]) == 30
    assert body["totals"]["signups"] == 1


def test_the_stats_endpoint_carries_it_behind_its_key(api, monkeypatch):
    usage.count("sensors_added")
    monkeypatch.setenv("STATS_KEY", "s3cret-stats-key")
    assert api.get("/api/stats").status_code == 401
    body = api.get("/api/stats",
                   headers={"Authorization": "Bearer s3cret-stats-key"}).json()
    assert body["usage"]["totals"]["sensors_added"] == 1
    assert len(body["usage"]["daily"]) == 30
    monkeypatch.delenv("STATS_KEY")
    assert api.get("/api/stats").status_code == 404


def test_the_privacy_statement_says_so():
    import legal

    text = legal.privacy_statement()
    assert "daily totals with\nnothing about who did them" in text
    assert f"{usage.KEEP_DAYS} days" in text


def test_counting_never_breaks_the_action(monkeypatch, api):
    def _broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(STORE._db, "put", _broken)
    usage.count("help_views")  # does not raise
    monkeypatch.undo()
    assert api.get("/help").status_code == 200
