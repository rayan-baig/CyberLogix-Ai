"""The Twilio and mail outage drill, automated (docs/OPERATIONS.md).

Twilio failing (errors and timeouts) and the mail host down, at the moment
a freezer fails. What has to hold:

* every alert is retried, or escalates to the next channel;
* nothing is silently lost: the incident says what failed, the operator
  sees a fault, and the owner is told when nobody was reached;
* when the service comes back, what was owed goes out once -- not zero
  times, not twice.

Found by running it: a call that failed at the provider was never tried
again; an alert email that failed waited for the hourly queue and was
dropped after three hours; the owner's copy of the alert did not say the
texts had failed; and nothing told the owner after the last re-send.
"""

from datetime import timedelta

import pytest

import automation
import faults
import mail
import notifications
import scheduler
import telemetry
import watchdog
from domain import VOICE_RETRY_AFTER_MINUTES
from store import STORE, VOICE_ESCALATION_GRACE_MINUTES, utc_now


class _Twilio:
    def __init__(self):
        self.mode = "ok"         # ok | error | timeout
        self.sms, self.calls = [], []
        outer = self

        def _fail():
            if outer.mode == "error":
                raise RuntimeError("HTTP 500: Twilio internal error")
            if outer.mode == "timeout":
                raise TimeoutError("Twilio did not answer within the timeout")

        class _Messages:
            def create(self, **kwargs):
                _fail()
                outer.sms.append(kwargs["to"])
                return type("M", (), {"sid": f"SM-{len(outer.sms)}", "status": "queued"})()

        class _Calls:
            def create(self, **kwargs):
                _fail()
                outer.calls.append(kwargs["to"])
                return type("C", (), {"sid": f"CA-{len(outer.calls)}", "status": "queued"})()

        self.client = type("Client", (), {"messages": _Messages(), "calls": _Calls()})()


@pytest.fixture()
def twilio(monkeypatch):
    fake = _Twilio()
    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")
    monkeypatch.setattr(notifications, "_client", fake.client)
    monkeypatch.setattr(notifications, "_client_error", None)
    monkeypatch.setattr(watchdog, "ping", lambda: None)
    return fake


def _breach(api, operator_factory, sensor_factory, plan="enterprise"):
    headers, tenant, _ = operator_factory(plan=plan)
    api.post("/api/contacts", headers=headers, json={
        "full_name": "Night Engineer", "phone": "+15550801", "escalation_order": 1})
    api.post("/api/contacts", headers=headers, json={
        "full_name": "Supervisor", "phone": "+15550802", "escalation_order": 2})
    sensor_factory(headers, sensor_id="FREEZER-1")
    resp = api.post("/api/sensor-pulse", headers=headers, json={
        "sensor_id": "FREEZER-1", "temperature_fahrenheit": 99.0})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert telemetry.wait_for_alert_emails()
    return headers, STORE.get_tenant(tenant["tenant_id"]), resp.json()["incident_id"]


def _age(incident_id, minutes):
    incident = STORE.get_incident(incident_id)
    incident.opened_at = utc_now() - timedelta(minutes=minutes)
    STORE._db.put("incident", incident_id, incident.to_row())


def _age_mail(key, minutes):
    message = STORE.mail_by_key(key)
    message.created_at = utc_now() - timedelta(minutes=minutes)
    STORE._db.put("mail", message.message_id, message.to_row())


@pytest.mark.parametrize("failure", ["error", "timeout"])
def test_texts_failing_are_retried_said_plainly_and_sent_once_on_recovery(
        api, operator_factory, sensor_factory, twilio, mailbox, failure):
    twilio.mode = failure
    _, tenant, incident_id = _breach(api, operator_factory, sensor_factory)

    incident = STORE.get_incident(incident_id)
    assert incident.alert_texts_failed
    assert {d["status"] for d in incident.sms_fanout} == {"send_failed"}
    # The owner's copy says the texts did not go, in the subject line.
    alert = mailbox.to(tenant.contact_email)[0]
    assert alert["Subject"].startswith("TEXTS FAILED - ALERT:")
    assert "Phone whoever is on call now" in alert.get_body(preferencelist=("plain",)).get_content()
    # And the operator can see Twilio is failing, without reading logs.
    assert any(f["source"] == "notifications.send_sms" for f in faults.recent())

    twilio.mode = "ok"
    _age(incident_id, 1.1)
    automation.sweep_tenant(tenant)
    automation.sweep_tenant(tenant)       # a second sweep sends nothing more
    assert sorted(twilio.sms) == ["+15550801", "+15550802"]
    assert STORE.get_incident(incident_id).texts_landed


def test_when_no_text_ever_lands_the_owner_is_told_once(
        api, operator_factory, sensor_factory, twilio, mailbox):
    twilio.mode = "error"
    _, tenant, incident_id = _breach(api, operator_factory, sensor_factory, plan="trial")
    for minutes in (1.1, 5.1, 15.1, 16, 30):
        _age(incident_id, minutes)
        automation.sweep_tenant(tenant)
    told = [m for m in mailbox.to(tenant.contact_email)
            if m["Subject"].startswith("NOBODY HAS BEEN TEXTED")]
    assert len(told) == 1
    assert "Phone whoever is on call now" in told[0].get_body(preferencelist=("plain",)).get_content()
    assert twilio.sms == []


def test_a_call_that_failed_at_the_provider_is_tried_again(
        api, operator_factory, sensor_factory, twilio, mailbox):
    _, tenant, incident_id = _breach(api, operator_factory, sensor_factory)
    twilio.mode = "error"
    _age(incident_id, VOICE_ESCALATION_GRACE_MINUTES + 1)
    automation.sweep_tenant(tenant)
    incident = STORE.get_incident(incident_id)
    assert incident.voice_escalated_at is not None and incident.calls_failed
    # Both people on the ladder were tried, and failed.
    assert len(incident.voice_fanout) == 2
    assert any(f["source"] == "notifications.place_voice_call" for f in faults.recent())

    # Before the first retry step: nothing.
    automation.sweep_tenant(tenant)
    assert twilio.calls == []

    # Twilio is back by the first retry: one call, to the first person.
    twilio.mode = "ok"
    incident.voice_escalated_at = utc_now() - timedelta(minutes=VOICE_RETRY_AFTER_MINUTES[0] + 0.5)
    STORE._db.put("incident", incident_id, incident.to_row())
    result = automation.sweep_tenant(tenant)
    assert twilio.calls == ["+15550801"]
    assert result["voice_calls_placed"] == 1
    # A call that got through is not repeated by later steps.
    incident.voice_escalated_at = utc_now() - timedelta(minutes=VOICE_RETRY_AFTER_MINUTES[-1] + 1)
    STORE._db.put("incident", incident_id, incident.to_row())
    automation.sweep_tenant(tenant)
    assert twilio.calls == ["+15550801"]


def test_a_call_retry_is_not_made_for_an_answered_alert(
        api, operator_factory, sensor_factory, twilio, mailbox):
    headers, tenant, incident_id = _breach(api, operator_factory, sensor_factory)
    twilio.mode = "error"
    _age(incident_id, VOICE_ESCALATION_GRACE_MINUTES + 1)
    automation.sweep_tenant(tenant)
    twilio.mode = "ok"
    assert api.post(f"/api/voice/acknowledge/{incident_id}", headers=headers, json={}).status_code == 200
    incident = STORE.get_incident(incident_id)
    incident.voice_escalated_at = utc_now() - timedelta(minutes=30)
    STORE._db.put("incident", incident_id, incident.to_row())
    automation.sweep_tenant(tenant)
    assert twilio.calls == []


def test_the_alert_email_waits_out_a_mail_outage_and_goes_once(
        api, operator_factory, sensor_factory, twilio, mailbox):
    mailbox.fail_times = 3                 # the first try and two retries
    _, tenant, incident_id = _breach(api, operator_factory, sensor_factory)
    key = f"incident-alert:{incident_id}"
    assert STORE.mail_by_key(key).status == "queued"
    assert mailbox.to(tenant.contact_email) == []
    assert any(f["source"] == "mail.deliver" for f in faults.recent())

    assert mailbox.fail_times == 2        # the first try, from the breach

    # The hourly queue no longer touches it: two senders would be two copies.
    mail.flush_queue()
    assert mailbox.fail_times == 2

    # Not yet due, then due: the sweep tries again on the minute timetable.
    scheduler.run_one_pass()
    assert mailbox.fail_times == 2
    for minutes, left in ((1.1, 1), (5.1, 0)):
        _age_mail(key, minutes)
        scheduler.run_one_pass()
        assert mailbox.fail_times == left
    # Three tries failed, which used to be the end of it; the fourth lands.
    assert STORE.mail_by_key(key).status == "queued"
    _age_mail(key, 15.1)
    scheduler.run_one_pass()
    assert STORE.mail_by_key(key).status == "sent"
    assert len(mailbox.to(tenant.contact_email)) == 1
    _age_mail(key, 600)
    scheduler.run_one_pass()
    assert len(mailbox.to(tenant.contact_email)) == 1


def test_an_alert_answered_while_the_mail_host_was_down_is_not_emailed_late(
        api, operator_factory, sensor_factory, twilio, mailbox):
    mailbox.fail_times = 1
    headers, tenant, incident_id = _breach(api, operator_factory, sensor_factory)
    api.post(f"/api/voice/acknowledge/{incident_id}", headers=headers, json={})
    _age_mail(f"incident-alert:{incident_id}", 2)
    scheduler.run_one_pass()
    message = STORE.mail_by_key(f"incident-alert:{incident_id}")
    assert message.status == "failed"
    assert "answered before the mail host came back" in message.detail
    assert mailbox.to(tenant.contact_email) == []


def test_everything_down_then_everything_back(
        api, operator_factory, sensor_factory, twilio, mailbox):
    """Twilio and the mail host both down: the incident is still opened and
    says what failed, and when both come back every owed text, call and
    email goes out once."""
    twilio.mode = "error"
    mailbox.fail_times = 99
    headers, tenant, incident_id = _breach(api, operator_factory, sensor_factory)
    for minutes in (1.1, 5.1, VOICE_ESCALATION_GRACE_MINUTES + 0.5, 15.1, 16):
        _age(incident_id, minutes)
        _age_mail(f"incident-alert:{incident_id}", minutes)
        scheduler.run_one_pass()
    row = api.get("/api/voice/incidents", headers=headers).json()["incidents"][0]
    assert row["alert_texts_failed"] is True
    assert STORE.get_incident(incident_id).calls_failed
    assert STORE.mail_by_key(f"incident-undelivered:{incident_id}").status == "queued"

    twilio.mode = "ok"
    mailbox.fail_times = 0
    incident = STORE.get_incident(incident_id)
    incident.voice_escalated_at = utc_now() - timedelta(minutes=VOICE_RETRY_AFTER_MINUTES[0] + 0.5)
    STORE._db.put("incident", incident_id, incident.to_row())
    _age_mail(f"incident-undelivered:{incident_id}", 2)
    _age_mail(f"incident-alert:{incident_id}", 31)
    scheduler.run_one_pass()
    scheduler.run_one_pass()

    assert twilio.calls == ["+15550801"]
    subjects = [m["Subject"] for m in mailbox.to(tenant.contact_email)]
    assert len(subjects) == 2
    assert any(s.startswith("TEXTS FAILED - ALERT:") for s in subjects)
    assert any(s.startswith("NOBODY HAS BEEN TEXTED") for s in subjects)


def test_a_refused_number_is_not_kept_in_the_fault_list(monkeypatch):
    """Twilio names the number it refused ("The 'To' number +1555... is
    not a valid phone number"), and every failed text and call is now
    recorded as a fault for the operator. The fault list has no owner
    business and is kept for good, so somebody's phone number written
    into it outlived the business deleting everything -- and a mail
    host's refusal names the address the same way."""
    import json

    class _Refusing:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError(
                    "HTTP 400 error: Unable to create record: The 'To' number "
                    f"{kwargs['to']} is not a valid phone number.")

        calls = messages

    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")
    monkeypatch.setattr(notifications, "_client", _Refusing())
    monkeypatch.setattr(notifications, "_client_error", None)

    out = notifications.send_sms("+15557654321", "ALERT test")
    assert out["delivered"] is False
    faults.record("mail.deliver", RuntimeError(
        "550 mailbox unavailable for night.manager@harbor.example"))

    kept = json.dumps(faults.recent())
    assert "notifications.send_sms" in kept
    assert "7654321" not in kept
    assert "night.manager@harbor.example" not in kept
