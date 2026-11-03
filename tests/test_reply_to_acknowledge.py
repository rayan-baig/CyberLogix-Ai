"""Acknowledging an alert by replying to the text.

The alert text ends "Reply 1 if you are on it". The reply is the fastest
way there is to stop the calls -- no sign-in, one key -- which is exactly
why it has to be narrow: it counts only when Twilio signed it, only from a
number the alert went to, and only for that business's alert while it is
still open. A stranger who learns the sending number gets nothing, not
even a reply.
"""

import pytest

import notifications
from store import STORE
from automation import sweep_tenant


ROUTE = "/api/voice/sms-reply"
URL = f"http://testserver{ROUTE}"


def _sign(url, form, token="token"):
    from twilio.request_validator import RequestValidator

    return RequestValidator(token).compute_signature(url, form)


def _reply(api, phone, body, sign=True):
    form = {"From": phone, "To": "+15550000", "Body": body, "MessageSid": "SM1"}
    headers = {"X-Twilio-Signature": _sign(URL, form)} if sign else {}
    return api.post(ROUTE, data=form, headers=headers)


@pytest.fixture()
def business(api, operator_factory, sensor_factory, configured_twilio):
    """A business with a two-person roster and a walk-in that has failed."""
    configured_twilio()

    def _make(company="Harbor Grill", email="owner@example.com",
              phones=("+1-555-0111", "+1-555-0112"), sensor_id="WALKIN-1"):
        headers, tenant, _ = operator_factory(company_name=company, email=email)
        for index, phone in enumerate(phones):
            made = api.post("/api/contacts", headers=headers, json={
                "full_name": f"Cook {index + 1}", "phone": phone,
                "escalation_order": index + 1})
            assert made.status_code == 201, made.text
        sensor_factory(headers, sensor_id=sensor_id, vertical="restaurant",
                       location="Kitchen walk-in")
        body = api.post("/api/sensor-pulse", headers=headers, json={
            "sensor_id": sensor_id, "temperature_fahrenheit": 58.0}).json()
        return headers, tenant, body["incident_id"]

    return _make


def test_the_alert_text_says_how_to_reply(business):
    _, _, incident_id = business()
    text = STORE.get_incident(incident_id).sms_text
    assert text.endswith("Reply 1 if you are on it.")


@pytest.mark.parametrize("words", ["1", "ACK", "ack", " Ack. ", "acknowledge", "OK"])
def test_a_reply_from_the_roster_acknowledges_and_stops_the_calls(
        api, business, age_incident, words):
    headers, tenant, incident_id = business()

    resp = _reply(api, "+15550112", words)  # the roster wrote +1-555-0112

    assert resp.status_code == 200
    assert "<Message>" in resp.text and "No more calls" in resp.text
    incident = STORE.get_incident(incident_id)
    assert incident.acknowledged_at is not None
    assert incident.acknowledged_by == "Cook 2, by text reply (+1-555-0112)"
    assert not incident.open

    # And the ladder has stopped: past the grace window, the sweep does
    # not phone anybody about it.
    age_incident(incident_id, minutes=60)
    result = sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))
    assert not [a for a in result.get("actions", [])
                if a.get("incident_id") == incident_id
                and "escalat" in str(a.get("action", ""))]
    assert STORE.get_incident(incident_id).voice_escalated_at is None

    trail = api.get("/api/accounts/audit?limit=5", headers=headers).json()
    assert any("text reply" in row["detail"] for row in trail["entries"])


def test_a_strangers_number_acknowledges_nothing_and_hears_nothing(api, business):
    _, _, incident_id = business()

    resp = _reply(api, "+15559999", "1")

    assert resp.status_code == 200
    assert "<Message>" not in resp.text, "a stranger was told the number is ours"
    assert STORE.get_incident(incident_id).acknowledged_at is None


def test_a_number_on_another_business_cannot_silence_this_one(api, business):
    """Two businesses, two rosters. A cook at the bakery replying 1 only
    reaches the bakery's alert, never the grill's."""
    _, _, grill = business()
    _, _, bakery = business(company="Harbor Bakery", email="baker@example.com",
                            phones=("+1-555-0222",), sensor_id="BAKERY-1")

    resp = _reply(api, "+15550222", "1")

    assert resp.status_code == 200
    assert STORE.get_incident(bakery).acknowledged_at is not None
    assert STORE.get_incident(grill).acknowledged_at is None


def test_a_number_on_a_roster_with_no_open_alert_is_told_so(api, business):
    _, _, grill = business()
    _business_two = business(company="Harbor Bakery", email="baker@example.com",
                             phones=("+1-555-0222",), sensor_id="BAKERY-1")
    STORE.resolve_incident(STORE.get_incident(_business_two[2]), "test")

    resp = _reply(api, "+15550222", "1")

    assert "no open alert" in resp.text
    assert STORE.get_incident(grill).acknowledged_at is None


def test_a_closed_alert_cannot_be_acknowledged(api, business):
    _, _, incident_id = business()
    incident = STORE.get_incident(incident_id)
    STORE.resolve_incident(incident, "Head chef")

    resp = _reply(api, "+15550111", "1")

    assert resp.status_code == 200
    assert "no open alert" in resp.text
    # Closing it stamped it; the reply changed nothing on top of that.
    assert "text reply" not in (STORE.get_incident(incident_id).acknowledged_by or "")


def test_the_second_reply_is_told_who_has_it(api, business):
    _, _, incident_id = business()
    assert "No more calls" in _reply(api, "+15550111", "1").text

    second = _reply(api, "+15550112", "1")

    assert "already acknowledged by Cook 1" in second.text
    assert STORE.get_incident(incident_id).acknowledged_by.startswith("Cook 1")


def test_an_unsigned_reply_is_refused(api, business):
    """The sending number is printed on every alert; the signature is the lock."""
    _, _, incident_id = business()

    resp = _reply(api, "+15550111", "1", sign=False)

    assert resp.status_code == 403
    assert STORE.get_incident(incident_id).acknowledged_at is None


def test_a_reply_signed_with_the_wrong_key_is_refused(api, business):
    _, _, incident_id = business()
    form = {"From": "+15550111", "To": "+15550000", "Body": "1", "MessageSid": "SM1"}

    resp = api.post(ROUTE, data=form,
                    headers={"X-Twilio-Signature": _sign(URL, form, token="guess")})

    assert resp.status_code == 403
    assert STORE.get_incident(incident_id).acknowledged_at is None


def test_with_no_twilio_token_nothing_is_trusted(api, business, monkeypatch):
    _, _, incident_id = business()
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "")

    assert _reply(api, "+15550111", "1").status_code == 403
    assert STORE.get_incident(incident_id).acknowledged_at is None


def test_the_signature_is_checked_against_the_public_address(
        api, business, monkeypatch):
    """Behind a proxy that ends TLS the app sees its own address; Twilio
    signed the public one, as for the keypress."""
    _, _, incident_id = business()
    monkeypatch.setattr(notifications, "PUBLIC_BASE_URL", "https://hub.example.com")
    form = {"From": "+15550111", "To": "+15550000", "Body": "1", "MessageSid": "SM1"}

    local = api.post(ROUTE, data=form, headers={"X-Twilio-Signature": _sign(URL, form)})
    assert local.status_code == 403
    public = api.post(ROUTE, data=form, headers={
        "X-Twilio-Signature": _sign(f"https://hub.example.com{ROUTE}", form)})
    assert public.status_code == 200
    assert STORE.get_incident(incident_id).acknowledged_at is not None


def test_any_other_reply_changes_nothing(api, business):
    _, _, incident_id = business()

    resp = _reply(api, "+15550111", "what is happening?")

    assert "reply 1" in resp.text
    assert STORE.get_incident(incident_id).acknowledged_at is None


def test_two_open_alerts_ask_which_and_a_number_picks_one(
        api, business, sensor_factory):
    headers, _, first = business()
    sensor_factory(headers, sensor_id="FREEZER-2", vertical="restaurant",
                   location="Bar freezer")
    second = api.post("/api/sensor-pulse", headers=headers, json={
        "sensor_id": "FREEZER-2", "temperature_fahrenheit": 50.0}).json()["incident_id"]

    ask = _reply(api, "+15550111", "1")
    assert "2 alerts are open" in ask.text
    assert STORE.get_incident(first).acknowledged_at is None
    assert STORE.get_incident(second).acknowledged_at is None

    number = int(second.split("-")[1])
    picked = _reply(api, "+15550111", f"ACK {number}")
    assert "No more calls" in picked.text
    assert STORE.get_incident(second).acknowledged_at is not None
    assert STORE.get_incident(first).acknowledged_at is None


def test_an_off_duty_contact_cannot_acknowledge(api, business):
    """Taken off the roster means the alert no longer goes to them, so a
    reply from them is not an answer to it."""
    headers, _, incident_id = business()
    contact = next(c for c in api.get("/api/contacts", headers=headers).json()["contacts"]
                   if c["phone"] == "+1-555-0112")
    off = api.patch(f"/api/contacts/{contact['contact_id']}", headers=headers,
                    json={"active": False})
    assert off.status_code == 200, off.text

    _reply(api, "+15550112", "1")

    assert STORE.get_incident(incident_id).acknowledged_at is None


def test_phone_numbers_match_only_when_they_are_the_same_number():
    same = notifications.same_phone
    assert same("+1-555-0100", "+15550100")
    assert same("(212) 555-0100", "+12125550100")
    assert not same("+15550100", "+15550101")
    assert not same("+442071234567", "+12071234567")
    assert not same("", "+15550100")
