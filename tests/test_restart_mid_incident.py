"""A restart in the middle of an incident changes nothing anybody receives.

Render restarts the process on every deploy and whenever it likes. A
breach that is open across one must not text the roster a second time,
and must not lose its place on the ladder: the call still comes at ten
minutes, once.
"""

from datetime import timedelta

import pytest

import automation
import telemetry
import voice_dispatch
from store import STORE, utc_now


@pytest.fixture()
def sent(monkeypatch):
    log = {"sms": [], "calls": []}

    def fake_sms(to, body, tenant_id=None):
        log["sms"].append(to)
        return {"channel": "sms", "to": to, "delivered": True, "status": "queued",
                "provider_sid": "SM-1", "detail": "ok"}

    def fake_call(to, spoken_text, tenant_id=None, action_url=None):
        log["calls"].append(to)
        return {"channel": "voice", "to": to, "delivered": True, "status": "queued",
                "provider_sid": "CA-1", "detail": "ok"}

    monkeypatch.setattr(telemetry, "send_sms", fake_sms)
    monkeypatch.setattr(automation, "send_sms", fake_sms)
    monkeypatch.setattr(voice_dispatch, "place_voice_call", fake_call)
    return log


def _restart():
    STORE._forget_locked()
    STORE.load()


def _pulse(api, headers, temp):
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01", "temperature_fahrenheit": temp})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _age(incident_id, minutes):
    incident = STORE.get_incident(incident_id)
    incident.opened_at = utc_now() - timedelta(minutes=minutes)
    STORE._db.put("incident", incident.incident_id, incident.to_row())


def test_one_breach_is_one_text_across_a_restart(api, tenant_factory, sensor_factory, sent):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    first = _pulse(api, headers, 94.0)
    assert first["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert len(sent["sms"]) == 1

    _restart()
    again = _pulse(api, headers, 95.0)

    assert again["status"] == "CRITICAL_CATASTROPHE_ONGOING"
    assert again["incident_id"] == first["incident_id"]
    assert len(sent["sms"]) == 1


def test_escalation_carries_on_after_a_restart(api, tenant_factory, sensor_factory, sent):
    headers, tenant = tenant_factory(plan="enterprise")
    sensor_factory(headers)
    incident_id = _pulse(api, headers, 94.0)["incident_id"]
    _age(incident_id, 4)

    # Restarted four minutes in: not due yet, so nothing.
    _restart()
    automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))
    assert sent["calls"] == []

    # Past the grace window, the restarted process places the call.
    _age(incident_id, 11)
    result = automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))
    assert result["voice_calls_placed"] == 1
    assert len(sent["calls"]) == 1

    # And another restart does not place it again.
    _restart()
    automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))
    assert len(sent["calls"]) == 1
    assert STORE.get_incident(incident_id).voice_escalated_at is not None
