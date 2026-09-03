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
