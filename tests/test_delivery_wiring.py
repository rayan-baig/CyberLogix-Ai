"""Delivery records attach to incidents through the whole ladder."""

import pytest

import notifications


@pytest.fixture()
def sent(monkeypatch):
    """Capture what the platform tries to deliver."""
    log = {"sms": [], "voice": []}

    def _sms(to, body, tenant_id=None):
        log["sms"].append({"to": to, "body": body, "tenant_id": tenant_id})
        return {
            "channel": "sms",
            "to": to,
            "delivered": True,
            "status": "queued",
            "provider_sid": f"SM{len(log['sms'])}",
            "detail": "ok",
        }

    def _call(to, spoken, tenant_id=None, action_url=None):
        log["voice"].append({"to": to, "spoken": spoken, "tenant_id": tenant_id,
                            "action_url": action_url})
        return {
            "channel": "voice",
            "to": to,
            "delivered": True,
            "status": "queued",
            "provider_sid": f"CA{len(log['voice'])}",
            "detail": "ok",
        }

    monkeypatch.setattr("telemetry.send_sms", _sms)
    monkeypatch.setattr("voice_dispatch.place_voice_call", _call)
    return log


def breach(api, headers, sensor_factory):
    sensor_factory(headers, sensor_id="RACK-01")
    return api.post(
        "/api/sensor-pulse",
        headers=headers,
        json={"sensor_id": "RACK-01", "temperature_fahrenheit": 94.0},
    ).json()


def test_breach_sends_the_sms_to_the_tenant(api, tenant_factory, sensor_factory, sent):
    headers, _ = tenant_factory()
    body = breach(api, headers, sensor_factory)

    assert body["sms_delivery"]["delivered"] is True
    assert body["sms_delivery"]["provider_sid"] == "SM1"
    assert sent["sms"][0]["to"] == "+1-555-0100"
    assert sent["sms"][0]["body"] == body["dispatched_sms_text"]
    # Usage is attributed, so the cost report can bill it to someone.
    assert sent["sms"][0]["tenant_id"].startswith("TEN-")


def test_sustained_breach_does_not_resend(
    api, tenant_factory, sensor_factory, sent
):
    headers, _ = tenant_factory()
    breach(api, headers, sensor_factory)
    for temp in (95.0, 96.0):
        api.post(
            "/api/sensor-pulse",
            headers=headers,
            json={"sensor_id": "RACK-01", "temperature_fahrenheit": temp},
        )
    assert len(sent["sms"]) == 1


def test_escalation_places_the_call(api, tenant_factory, sensor_factory, sent):
    headers, _ = tenant_factory()
    incident_id = breach(api, headers, sensor_factory)["incident_id"]

    body = api.post(
        f"/api/voice/escalate/{incident_id}?force=true", headers=headers
    ).json()
    assert body["voice_delivery"]["delivered"] is True
    assert sent["voice"][0]["to"] == "+1-555-0100"
    assert sent["voice"][0]["spoken"] == body["voice_script"]


def test_sweep_places_the_call(
    api, tenant_factory, sensor_factory, age_incident, sent
):
    headers, _ = tenant_factory()
    incident_id = breach(api, headers, sensor_factory)["incident_id"]
    age_incident(incident_id, minutes=20)

    body = api.post("/api/autopilot/sweep", headers=headers).json()
    dispatched = [
        a for a in body["actions"] if a["action"] == "voice_escalation_dispatched"
    ][0]
    assert dispatched["voice_delivery"]["delivered"] is True
    assert len(sent["voice"]) == 1


def test_undelivered_alert_is_visible_on_the_incident(
    api, tenant_factory, sensor_factory
):
    """With no Twilio configured the alert is composed but marked unsent."""
    headers, _ = tenant_factory()
    body = breach(api, headers, sensor_factory)

    assert body["sms_delivery"]["delivered"] is False
    assert body["sms_delivery"]["status"] == "not_configured"
    # The alert text still exists — only its delivery failed.
    assert body["dispatched_sms_text"]


def test_health_reports_delivery_mode(api):
    assert api.get("/api/health").json()["message_delivery"] == "dry_run"
