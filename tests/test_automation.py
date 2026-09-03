"""Autonomous compliance clerk and the operations autopilot sweep."""

from datetime import timedelta

from store import STORE, utc_now


def open_incident(api, headers, sensor_factory, sensor_id="RACK-01"):
    sensor_factory(headers, sensor_id=sensor_id)
    return api.post(
        "/api/sensor-pulse",
        headers=headers,
        json={"sensor_id": sensor_id, "temperature_fahrenheit": 94.0},
    ).json()["incident_id"]


def test_compliance_report_counts_excursions(
    api, tenant_factory, sensor_factory
):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    for temp in (68.0, 70.0, 94.0, 72.0):
        api.post(
            "/api/sensor-pulse",
            headers=headers,
            json={"sensor_id": "RACK-01", "temperature_fahrenheit": temp},
        )

    body = api.get("/api/autopilot/compliance?days=7", headers=headers).json()
    assert body["sensors_monitored"] == 1
    assert body["total_readings_logged"] == 4
    assert body["total_readings_breached"] == 1
    assert body["overall_compliance_percent"] == 75.0
    assert body["incidents_opened"] == 1
    assert body["non_compliant_sensors"] == 1

    row = body["per_sensor"][0]
    assert row["min_temperature"] == 68.0
    assert row["max_temperature"] == 94.0
    assert row["compliant"] is False


def test_clean_period_reports_full_compliance(
    api, tenant_factory, sensor_factory
):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    for temp in (68.0, 69.0, 70.0):
        api.post(
            "/api/sensor-pulse",
            headers=headers,
            json={"sensor_id": "RACK-01", "temperature_fahrenheit": temp},
        )

    body = api.get("/api/autopilot/compliance", headers=headers).json()
    assert body["overall_compliance_percent"] == 100.0
    assert body["per_sensor"][0]["compliant"] is True


def test_compliance_narration_uses_gemini(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    api.post(
        "/api/sensor-pulse",
        headers=headers,
        json={"sensor_id": "RACK-01", "temperature_fahrenheit": 68.0},
    )

    body = api.get(
        "/api/autopilot/compliance?narrate=true", headers=headers
    ).json()
    assert body["summary_source"] == "gemini"
    assert body["executive_summary"].startswith("URGENT:")


def test_sweep_flags_a_silent_sensor(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    sensor = STORE.get_sensor("RACK-01")
    sensor.last_seen = utc_now() - timedelta(hours=3)

    body = api.post("/api/autopilot/sweep", headers=headers).json()
    assert body["sensors_offline"] == 1
    offline = [a for a in body["actions"] if a["action"] == "sensor_offline_flagged"]
    assert offline[0]["sensor_id"] == "RACK-01"


def test_sweep_escalates_an_unanswered_incident(
    api, tenant_factory, sensor_factory, age_incident
):
    headers, _ = tenant_factory()
    incident_id = open_incident(api, headers, sensor_factory)
    age_incident(incident_id, minutes=20)

    body = api.post("/api/autopilot/sweep", headers=headers).json()
    assert body["voice_calls_placed"] == 1
    dispatched = [
        a for a in body["actions"] if a["action"] == "voice_escalation_dispatched"
    ]
    assert dispatched[0]["incident_id"] == incident_id
    assert dispatched[0]["call_to"] == "+1-555-0100"


def test_sweep_never_calls_twice_for_one_incident(
    api, tenant_factory, sensor_factory, age_incident
):
    headers, _ = tenant_factory()
    incident_id = open_incident(api, headers, sensor_factory)
    age_incident(incident_id, minutes=20)

    assert api.post("/api/autopilot/sweep", headers=headers).json()[
        "voice_calls_placed"
    ] == 1
    assert api.post("/api/autopilot/sweep", headers=headers).json()[
        "voice_calls_placed"
    ] == 0


def test_sweep_respects_the_grace_window(
    api, tenant_factory, sensor_factory
):
    headers, _ = tenant_factory()
    open_incident(api, headers, sensor_factory)
    body = api.post("/api/autopilot/sweep", headers=headers).json()
    assert body["voice_calls_placed"] == 0
    assert body["open_incidents"] == 1


def test_sweep_can_report_without_calling(
    api, tenant_factory, sensor_factory, age_incident
):
    headers, _ = tenant_factory()
    incident_id = open_incident(api, headers, sensor_factory)
    age_incident(incident_id, minutes=20)

    body = api.post(
        "/api/autopilot/sweep?auto_escalate=false", headers=headers
    ).json()
    assert body["voice_calls_placed"] == 0
    due = [a for a in body["actions"] if a["action"] == "voice_escalation_due"]
    assert due[0]["incident_id"] == incident_id


def test_sweep_on_trial_plan_reports_the_upgrade_path(
    api, tenant_factory, sensor_factory, age_incident
):
    headers, _ = tenant_factory(plan="trial")
    incident_id = open_incident(api, headers, sensor_factory)
    age_incident(incident_id, minutes=20)

    body = api.post("/api/autopilot/sweep", headers=headers).json()
    assert body["voice_calls_placed"] == 0
    blocked = [
        a for a in body["actions"] if a["action"] == "voice_escalation_blocked"
    ]
    assert blocked[0]["incident_id"] == incident_id
    assert "Upgrade" in blocked[0]["detail"]


def test_acknowledged_incident_is_not_escalated_by_sweep(
    api, tenant_factory, sensor_factory, age_incident
):
    headers, _ = tenant_factory()
    incident_id = open_incident(api, headers, sensor_factory)
    age_incident(incident_id, minutes=20)
    api.post(
        f"/api/voice/acknowledge/{incident_id}",
        headers=headers,
        json={"acknowledged_by": "Night Engineer"},
    )

    body = api.post("/api/autopilot/sweep", headers=headers).json()
    assert body["voice_calls_placed"] == 0
    assert body["open_incidents"] == 0
