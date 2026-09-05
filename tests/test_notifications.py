"""Twilio delivery: dry-run, success, and failure recording."""

import pytest

import notifications
from notifications import build_twiml, place_voice_call, send_sms


@pytest.fixture()
def configured(monkeypatch):
    """Present credentials and a recording fake Twilio client."""

    class _Msg:
        sid = "SM123"
        status = "queued"

    class _Call:
        sid = "CA456"
        status = "queued"

    class _Fake:
        def __init__(self, boom=False):
            self.boom = boom
            self.sent = []
            self.called = []
            outer = self

            class _Messages:
                def create(self, to, from_, body):
                    if outer.boom:
                        raise RuntimeError("twilio 500")
                    outer.sent.append({"to": to, "from": from_, "body": body})
                    return _Msg()

            class _Calls:
                def create(self, to, from_, twiml):
                    if outer.boom:
                        raise RuntimeError("twilio 500")
                    outer.called.append({"to": to, "from": from_, "twiml": twiml})
                    return _Call()

            self.messages = _Messages()
            self.calls = _Calls()

    def _apply(boom=False):
        fake = _Fake(boom=boom)
        monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC1")
        monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "tok")
        monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")
        monkeypatch.setattr(notifications, "_client", fake)
        monkeypatch.setattr(notifications, "_client_error", None)
        return fake

    return _apply


def test_unconfigured_is_dry_run_not_an_error(monkeypatch):
    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "")
    record = send_sms("+15551234", "freezer is failing")
    assert record["delivered"] is False
    assert record["status"] == "not_configured"
    assert "TWILIO_ACCOUNT_SID" in record["detail"]


def test_missing_recipient_is_reported(configured):
    configured()
    record = send_sms("", "text")
    assert record["status"] == "no_recipient"


def test_sms_is_sent_when_configured(configured):
    fake = configured()
    record = send_sms("+15551234", "freezer is failing")
    assert record["delivered"] is True
    assert record["provider_sid"] == "SM123"
    assert fake.sent[0]["to"] == "+15551234"


def test_sms_failure_is_recorded_not_raised(configured):
    configured(boom=True)
    record = send_sms("+15551234", "freezer is failing")
    assert record["delivered"] is False
    assert record["status"] == "send_failed"


def test_long_sms_is_trimmed(configured):
    fake = configured()
    send_sms("+15551234", "x" * 5000)
    assert len(fake.sent[0]["body"]) == notifications.MAX_SMS_CHARACTERS


def test_voice_call_speaks_the_script_twice(configured):
    fake = configured()
    record = place_voice_call("+15551234", "Go to the walk-in now.")
    assert record["delivered"] is True
    assert record["provider_sid"] == "CA456"
    assert fake.called[0]["twiml"].count("Go to the walk-in now.") == 2


def test_twiml_escapes_model_authored_text():
    """An & or < in a generated script must not produce malformed XML."""
    from xml.etree import ElementTree

    twiml = build_twiml('Check R&D <freezer> "unit 3"')
    assert "&amp;" in twiml and "&lt;" in twiml
    ElementTree.fromstring(twiml)  # raises if the document is malformed


def test_call_failure_is_recorded_not_raised(configured):
    configured(boom=True)
    record = place_voice_call("+15551234", "hello")
    assert record["delivered"] is False
    assert record["status"] == "call_failed"


# ---- "press 1 to acknowledge" -------------------------------------------


def _sign(url, form, token="token"):
    from twilio.request_validator import RequestValidator

    return RequestValidator(token).compute_signature(url, form)


def test_the_call_gathers_a_keypress_when_reachable(monkeypatch):
    """Without a public URL the script's "press 1" goes nowhere."""
    monkeypatch.setattr(notifications, "PUBLIC_BASE_URL", "")
    assert notifications.acknowledgement_url("INC-1", "tok") is None

    monkeypatch.setattr(
        notifications, "PUBLIC_BASE_URL", "https://hub.example.com"
    )
    url = notifications.acknowledgement_url("INC-1", "tok")
    assert url == "https://hub.example.com/api/voice/keypress/INC-1/tok"

    twiml = build_twiml("Press 1 to acknowledge.", url)
    assert "<Gather" in twiml and url in twiml


def test_pressing_one_acknowledges_the_incident(
    api, operator_factory, sensor_factory, age_incident, monkeypatch,
    configured_twilio,
):
    configured_twilio()
    monkeypatch.setattr(
        notifications, "PUBLIC_BASE_URL", "https://hub.example.com"
    )
    from store import STORE

    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="RACK-01")
    body = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01",
                          "temperature_fahrenheit": 94.0}).json()
    incident_id = body["incident_id"]
    age_incident(incident_id, minutes=30)
    api.post(f"/api/voice/escalate/{incident_id}", headers=headers)

    token = STORE.get_incident(incident_id).ack_token
    assert token, "escalating must mint a keypress secret"

    path = f"/api/voice/keypress/{incident_id}/{token}"
    url = f"http://testserver{path}"
    form = {"Digits": "1", "To": "+15550003"}
    resp = api.post(path, data=form,
                    headers={"X-Twilio-Signature": _sign(url, form)})

    assert resp.status_code == 200
    assert "Acknowledged" in resp.text
    incident = STORE.get_incident(incident_id)
    assert incident.acknowledged_at is not None
    assert incident.acknowledged_by == "phone keypad (+15550003)"


def test_an_unsigned_keypress_is_refused(
    api, operator_factory, sensor_factory, age_incident, configured_twilio
):
    """The URL is all a stranger would need, so the signature is the lock."""
    configured_twilio()
    from store import STORE

    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="RACK-01")
    body = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01",
                          "temperature_fahrenheit": 94.0}).json()
    age_incident(body["incident_id"], minutes=30)
    api.post(f"/api/voice/escalate/{body['incident_id']}", headers=headers)
    token = STORE.get_incident(body["incident_id"]).ack_token

    resp = api.post(f"/api/voice/keypress/{body['incident_id']}/{token}",
                    data={"Digits": "1"})
    assert resp.status_code == 403
    assert STORE.get_incident(body["incident_id"]).acknowledged_at is None


def test_a_wrong_token_is_refused(
    api, operator_factory, sensor_factory, age_incident, configured_twilio
):
    """Incident IDs run in sequence, so the ID alone must not be enough."""
    configured_twilio()
    from store import STORE

    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="RACK-01")
    body = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01",
                          "temperature_fahrenheit": 94.0}).json()
    incident_id = body["incident_id"]
    age_incident(incident_id, minutes=30)
    api.post(f"/api/voice/escalate/{incident_id}", headers=headers)

    path = f"/api/voice/keypress/{incident_id}/guessed-token"
    url = f"http://testserver{path}"
    form = {"Digits": "1"}
    resp = api.post(path, data=form,
                    headers={"X-Twilio-Signature": _sign(url, form)})
    assert resp.status_code == 404
    assert STORE.get_incident(incident_id).acknowledged_at is None


def test_any_other_digit_leaves_the_escalation_open(
    api, operator_factory, sensor_factory, age_incident, configured_twilio
):
    configured_twilio()
    from store import STORE

    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="RACK-01")
    body = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01",
                          "temperature_fahrenheit": 94.0}).json()
    incident_id = body["incident_id"]
    age_incident(incident_id, minutes=30)
    api.post(f"/api/voice/escalate/{incident_id}", headers=headers)
    token = STORE.get_incident(incident_id).ack_token

    path = f"/api/voice/keypress/{incident_id}/{token}"
    url = f"http://testserver{path}"
    form = {"Digits": "7"}
    resp = api.post(path, data=form,
                    headers={"X-Twilio-Signature": _sign(url, form)})
    assert resp.status_code == 200
    assert "stays open" in resp.text
    assert STORE.get_incident(incident_id).acknowledged_at is None
