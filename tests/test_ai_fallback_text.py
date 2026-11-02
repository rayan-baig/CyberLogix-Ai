"""With no AI key, every piece of generated text is still a sentence.

A deployment that has not set GEMINI_API_KEY yet, or whose key has been
revoked, still sends alerts, places calls and produces documents: each
of them falls back to a plain template built from the real figures. What
must never happen is the template reading "None", "null", "nan", or being
empty -- an alert text that says "sensor None at None" at 3am is worse
than no text, and a compliance document that says "n/a% compliance" is
not one an inspector will accept.
"""

import re
from datetime import timedelta

import pytest

from store import STORE, evaluate_breach, utc_now

JUNK = re.compile(r"\bNone\b|\bnull\b|\bnan\b|\bNaN\b|\bundefined\b|\bn/a\b|\{|\}",
                  re.IGNORECASE)


def _plain(text, where):
    assert isinstance(text, str), f"{where}: {text!r}"
    assert text.strip(), f"{where} is empty"
    found = JUNK.search(text)
    assert found is None, f"{where} says {found.group(0)!r}: {text}"


@pytest.fixture()
def no_ai(break_gemini):
    break_gemini("missing")


def _seed(sensor_id, temps, spacing_minutes=10):
    sensor = STORE.get_sensor(sensor_id)
    now = utc_now()
    for index, temp in enumerate(temps):
        STORE.record_reading(
            sensor=sensor, temperature_fahrenheit=temp, humidity_percent=None,
            breached=evaluate_breach(sensor.industry_vertical, temp) is not None,
            at=now - timedelta(minutes=(len(temps) - 1 - index) * spacing_minutes),
        )


@pytest.mark.parametrize("unit", ["F", "C"])
def test_alert_text_and_voice_script(api, operator_factory, sensor_factory, no_ai, unit):
    headers, _, _ = operator_factory(plan="enterprise")
    api.post("/api/licenses/me/temperature-unit", headers=headers,
             json={"temperature_unit": unit})
    sensor_factory(headers, sensor_id="RACK-01")
    body = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01", "temperature_fahrenheit": 94.0}).json()
    incident = STORE.get_incident(body["incident_id"])
    assert incident.sms_dispatch_source == "fallback_template"
    _plain(incident.sms_text, "the alert text")
    assert "RACK-01" in incident.sms_text

    escalated = api.post(f"/api/voice/escalate/{body['incident_id']}?force=true",
                         headers=headers)
    assert escalated.status_code == 200, escalated.text
    assert escalated.json()["voice_dispatch_source"] == "fallback_template"
    _plain(escalated.json()["voice_script"], "the voice script")


def test_an_adopted_device_with_no_location_still_gets_a_sentence(
        api, operator_factory, no_ai):
    """A sensor that arrived through "send anything" has no location typed."""
    headers, _, _ = operator_factory(plan="enterprise")
    made = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "BARE-1", "industry_vertical": "restaurant",
        "location_name": " "})
    if made.status_code != 201:
        pytest.skip("a blank location is refused at registration")
    body = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "BARE-1", "temperature_fahrenheit": 70.0}).json()
    _plain(STORE.get_incident(body["incident_id"]).sms_text, "the alert text")


def test_compliance_summary_with_and_without_readings(api, operator_factory,
                                                      sensor_factory, no_ai):
    headers, _, _ = operator_factory()
    # A business with sensors and no readings yet: nothing to measure.
    sensor_factory(headers, sensor_id="RACK-01")
    empty = api.get("/api/autopilot/compliance?narrate=true", headers=headers).json()
    assert empty["summary_source"] == "fallback_template"
    _plain(empty["executive_summary"], "the empty compliance summary")

    _seed("RACK-01", [68.0, 70.0, 95.0, 70.0])
    full = api.get("/api/autopilot/compliance?narrate=true", headers=headers).json()
    _plain(full["executive_summary"], "the compliance summary")
    assert "%" in full["executive_summary"]


@pytest.mark.parametrize("temps", [
    [68.0, 70.0, 72.0, 74.0],   # drifting toward the limit
    [80.0, 84.0, 88.0, 92.0],   # already over it
    [95.0],                     # over it, with one reading and no trend
])
def test_maintenance_brief(api, tenant_factory, sensor_factory, no_ai, temps):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="RACK-01")
    _seed("RACK-01", temps)
    body = api.get("/api/forecast/sensor/RACK-01?narrate=true", headers=headers).json()
    if "maintenance_brief" not in body:
        assert body["risk_level"] not in {"critical", "high", "elevated"}
        return
    assert body["brief_source"] == "fallback_template"
    _plain(body["maintenance_brief"], f"the brief for {temps}")
    # Over the limit now is said as that, not as a breach due in 0.0 hours.
    assert "0.0 hours" not in body["maintenance_brief"]
    if body["already_breached"]:
        assert "already past its limit" in body["maintenance_brief"]


def test_claim_cover_letter(api, operator_factory, sensor_factory, no_ai):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    body = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 48.0}).json()
    packet = api.post(f"/api/claims/{body['incident_id']}/packet", headers=headers).json()
    assert packet["cover_letter_source"] == "fallback_template"
    _plain(packet["cover_letter"], "the cover letter")


def test_sector_document(api, operator_factory, sensor_factory, no_ai):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    sensor_factory(headers, sensor_id="FRZ-2", vertical="restaurant")
    _seed("FRZ-1", [36.0, 37.0, 55.0])
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 55.0})
    resp = api.post("/api/shortcuts/restaurant", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _plain(body["document"], "the sector document")


def test_meeting_notes_say_plainly_that_nothing_was_generated(api, tenant_factory, no_ai):
    headers, _ = tenant_factory()
    resp = api.post("/api/v1/bridge/summarize-transcript", headers=headers,
                    json={"industry_vertical": "restaurant",
                          "raw_transcript": "Walk-in door seal is torn, Sam to replace."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "TRANSCRIPT_PROCESSING_DEGRADED"
    _plain(body["degraded_reason"], "the degraded notice")
