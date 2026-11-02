"""An alert text that failed is sent again.

The text went out once, when the incident opened. If Twilio was down at
that moment, or briefly refusing, nothing tried again, and a trial has no
voice call after the text, so nobody was told about the breach at all. The
sweep now re-sends at 1, 5 and 15 minutes while nothing has got through,
once per step, claimed and stored so a restart or a second sweep at the
same moment cannot send a step twice.
"""

import threading
import time
from datetime import timedelta

import pytest

import automation
import notifications
from store import STORE


class _Twilio:
    """A Twilio client that can be switched off and on, counting texts."""

    def __init__(self):
        self.down = True
        self.texts = []
        self.delay = 0.0
        outer = self

        class _Messages:
            def create(self, **kwargs):
                if outer.delay:
                    time.sleep(outer.delay)
                if outer.down:
                    raise RuntimeError("HTTP 503: Service Unavailable")
                outer.texts.append(kwargs)
                return type("M", (), {"sid": f"SM-{len(outer.texts)}",
                                      "status": "queued"})()

        class _Calls:
            def create(self, **kwargs):
                return type("C", (), {"sid": "CA-1", "status": "queued"})()

        self.messages = _Messages()
        self.calls = _Calls()
        self.attempts = 0
        original = self.messages.create

        def counted(**kwargs):
            self.attempts += 1
            return original(**kwargs)

        self.messages.create = counted


@pytest.fixture()
def twilio(monkeypatch):
    client = _Twilio()
    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")
    monkeypatch.setattr(notifications, "_client", client)
    monkeypatch.setattr(notifications, "_client_error", None)
    return client


def _breach(api, tenant_factory, sensor_factory, plan="trial"):
    headers, tenant = tenant_factory(plan=plan)
    sensor_factory(headers, sensor_id="RACK-01")
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01", "temperature_fahrenheit": 94.0})
    assert resp.status_code == 200, resp.text
    return headers, STORE.get_tenant(tenant["tenant_id"]), resp.json()["incident_id"]


def _breach_signed_in(api, operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory(plan="trial")
    sensor_factory(headers, sensor_id="RACK-01")
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01", "temperature_fahrenheit": 94.0})
    assert resp.status_code == 200, resp.text
    return headers, STORE.get_tenant(tenant["tenant_id"]), resp.json()["incident_id"]


def _age_to(incident_id, minutes):
    """Set the incident's age, and write it down as a restart would see it."""
    incident = STORE.get_incident(incident_id)
    incident.opened_at = automation.utc_now() - timedelta(minutes=minutes)
    STORE._db.put("incident", incident.incident_id, incident.to_row())
    return incident


def _retried(result):
    return [a for a in result["actions"] if a["action"] == "alert_text_retried"]


def test_twilio_down_then_back_the_text_is_sent_again(api, tenant_factory,
                                                     sensor_factory, twilio):
    headers, tenant, incident_id = _breach(api, tenant_factory, sensor_factory)
    assert twilio.attempts == 1 and twilio.texts == []

    # Before the first step: nothing.
    _age_to(incident_id, 0.5)
    assert _retried(automation.sweep_tenant(tenant)) == []
    assert twilio.attempts == 1

    # The console says so, and says when it tries again.
    row = api.get("/api/voice/incidents", headers=headers).json()["incidents"][0]
    assert row["alert_texts_failed"] is True
    assert row["texts_landed"] is False
    assert row["next_sms_retry_at"] is not None

    # 1 minute: re-sent, still down.
    _age_to(incident_id, 1.2)
    first = _retried(automation.sweep_tenant(tenant))
    assert len(first) == 1 and first[0]["step"] == 1 and first[0]["delivered"] == 0
    assert twilio.attempts == 2
    # The same step is not sent twice.
    automation.sweep_tenant(tenant)
    assert twilio.attempts == 2

    # 5 minutes: Twilio is back, and the text lands.
    twilio.down = False
    _age_to(incident_id, 5.5)
    second = _retried(automation.sweep_tenant(tenant))
    assert len(second) == 1 and second[0]["step"] == 2 and second[0]["delivered"] == 1
    assert len(twilio.texts) == 1
    assert twilio.texts[0]["to"] == "+1-555-0100"
    assert twilio.texts[0]["body"] == STORE.get_incident(incident_id).sms_text

    # Landed, so no third step.
    _age_to(incident_id, 30)
    automation.sweep_tenant(tenant)
    assert len(twilio.texts) == 1

    row = api.get("/api/voice/incidents", headers=headers).json()["incidents"][0]
    assert row["alert_texts_failed"] is False
    assert row["texts_landed"] is True
    assert [r["step"] for r in row["sms_retries"]] == [1, 2]

    # And the claim packet shows the re-sends, in order.
    from claims import build_packet

    timeline = build_packet(tenant, STORE.get_incident(incident_id))["response"]["timeline"]
    events = [e["event"] for e in timeline]
    assert events.count("Alert text sent again") == 2
    assert [e["at"] for e in timeline] == sorted(e["at"] for e in timeline)


def test_three_steps_then_it_stops_and_says_nobody_knows(api, tenant_factory,
                                                        sensor_factory, twilio):
    _, tenant, incident_id = _breach(api, tenant_factory, sensor_factory)
    for age in (1.1, 5.1, 15.1):
        _age_to(incident_id, age)
        automation.sweep_tenant(tenant)
    assert twilio.attempts == 4

    _age_to(incident_id, 60)
    result = automation.sweep_tenant(tenant)
    assert twilio.attempts == 4
    assert any(a["action"] == "alert_texts_undelivered" for a in result["actions"])
    assert STORE.get_incident(incident_id).next_sms_retry_at() is None


def test_a_restart_mid_backoff_does_not_send_a_step_again(api, tenant_factory,
                                                          sensor_factory, twilio):
    _, tenant, incident_id = _breach(api, tenant_factory, sensor_factory)
    _age_to(incident_id, 1.5)
    automation.sweep_tenant(tenant)
    assert twilio.attempts == 2

    STORE._forget_locked()
    STORE.load()
    tenant = STORE.get_tenant(tenant.tenant_id)
    automation.sweep_tenant(tenant)
    assert twilio.attempts == 2
    assert STORE.get_incident(incident_id).sms_retry_step == 1

    _age_to(incident_id, 5.5)
    automation.sweep_tenant(tenant)
    assert twilio.attempts == 3


def test_a_restart_that_missed_steps_sends_once_not_three_times(api, tenant_factory,
                                                                sensor_factory, twilio):
    """Down from minute 0 to minute 20: one re-send on the way back up."""
    _, tenant, incident_id = _breach(api, tenant_factory, sensor_factory)
    _age_to(incident_id, 20)
    STORE._forget_locked()
    STORE.load()
    tenant = STORE.get_tenant(tenant.tenant_id)

    retried = _retried(automation.sweep_tenant(tenant))
    assert len(retried) == 1 and retried[0]["step"] == 3
    assert twilio.attempts == 2
    automation.sweep_tenant(tenant)
    assert twilio.attempts == 2


def test_two_sweeps_at_once_send_one_retry(api, tenant_factory, sensor_factory,
                                           twilio, monkeypatch):
    """The timer and the sweep button, both past the "is it due" check."""
    _, tenant, incident_id = _breach(api, tenant_factory, sensor_factory)
    _age_to(incident_id, 1.5)
    twilio.delay = 0.2
    # Both sweeps reach the claim having both seen the step as due, which
    # is the window the claim exists to close.
    both_decided = threading.Barrier(2, timeout=5)
    claim = STORE.claim_sms_retry

    def claim_together(incident, step):
        both_decided.wait()
        return claim(incident, step)

    monkeypatch.setattr(STORE, "claim_sms_retry", claim_together)
    results = []

    def sweep():
        results.append(automation.sweep_tenant(tenant))

    threads = [threading.Thread(target=sweep) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert twilio.attempts == 2
    assert sum(len(_retried(r)) for r in results) == 1


def test_an_acknowledged_incident_is_not_texted_again(api, operator_factory,
                                                      sensor_factory, twilio):
    headers, tenant, incident_id = _breach_signed_in(api, operator_factory, sensor_factory)
    resp = api.post(f"/api/voice/acknowledge/{incident_id}", headers=headers,
                    json={"acknowledged_by": "Night Engineer"})
    assert resp.status_code == 200, resp.text
    _age_to(incident_id, 2)
    automation.sweep_tenant(tenant)
    assert twilio.attempts == 1


def test_dry_run_is_not_retried(api, tenant_factory, sensor_factory):
    """No credentials means nothing is ever sent; trying again is noise."""
    _, tenant, incident_id = _breach(api, tenant_factory, sensor_factory)
    _age_to(incident_id, 2)
    assert _retried(automation.sweep_tenant(tenant)) == []
    assert STORE.get_incident(incident_id).sms_retries == []


def test_a_report_only_sweep_does_not_send(api, tenant_factory, sensor_factory, twilio):
    _, tenant, incident_id = _breach(api, tenant_factory, sensor_factory)
    _age_to(incident_id, 2)
    result = automation.sweep_tenant(tenant, auto_escalate=False)
    assert twilio.attempts == 1
    assert any(a["action"] == "alert_text_retry_due" for a in result["actions"])
    # Withheld, not spent: the next real sweep still sends it.
    automation.sweep_tenant(tenant)
    assert twilio.attempts == 2


def test_a_corrected_number_is_the_one_tried(api, operator_factory, sensor_factory, twilio):
    headers, tenant, incident_id = _breach_signed_in(api, operator_factory, sensor_factory)
    resp = api.post("/api/contacts", headers=headers, json={
        "full_name": "Night Engineer", "phone": "+15550199", "escalation_order": 1,
    })
    assert resp.status_code in (200, 201), resp.text
    twilio.down = False
    _age_to(incident_id, 1.5)
    automation.sweep_tenant(tenant)
    assert [t["to"] for t in twilio.texts] == ["+15550199"]
