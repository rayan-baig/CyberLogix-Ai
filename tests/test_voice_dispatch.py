"""Escalation ladder: SMS grace window, voice calls, acknowledgement."""


def open_incident(api, headers, sensor_factory, sensor_id="RACK-01"):
    sensor_factory(headers, sensor_id=sensor_id)
    body = api.post(
        "/api/sensor-pulse",
        headers=headers,
        json={"sensor_id": sensor_id, "temperature_fahrenheit": 94.0},
    ).json()
    return body["incident_id"]


def test_escalation_refused_inside_grace_window(
    api, tenant_factory, sensor_factory
):
    headers, _ = tenant_factory()
    incident_id = open_incident(api, headers, sensor_factory)

    resp = api.post(f"/api/voice/escalate/{incident_id}", headers=headers)
    assert resp.status_code == 425
    assert "grace window" in resp.json()["detail"]


def test_force_overrides_grace_window(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    incident_id = open_incident(api, headers, sensor_factory)

    body = api.post(
        f"/api/voice/escalate/{incident_id}?force=true", headers=headers
    ).json()
    assert body["status"] == "VOICE_ESCALATION_DISPATCHED"
    assert body["call_to"] == "+1-555-0100"
    assert body["forced"] is True
    assert body["voice_dispatch_source"] == "gemini"


def test_pending_lists_only_incidents_past_the_window(
    api, tenant_factory, sensor_factory, age_incident
):
    headers, _ = tenant_factory()
    incident_id = open_incident(api, headers, sensor_factory)

    assert api.get("/api/voice/pending", headers=headers).json()["escalation_due"] == 0

    age_incident(incident_id, minutes=15)
    pending = api.get("/api/voice/pending", headers=headers).json()
    assert pending["escalation_due"] == 1
    assert pending["incidents"][0]["incident_id"] == incident_id


def test_escalation_after_window_needs_no_force(
    api, tenant_factory, sensor_factory, age_incident
):
    headers, _ = tenant_factory()
    incident_id = open_incident(api, headers, sensor_factory)
    age_incident(incident_id, minutes=15)

    body = api.post(f"/api/voice/escalate/{incident_id}", headers=headers).json()
    assert body["status"] == "VOICE_ESCALATION_DISPATCHED"
    assert body["minutes_unacknowledged"] >= 15


def test_acknowledgement_halts_the_ladder(
    api, tenant_factory, sensor_factory, age_incident
):
    headers, _ = tenant_factory()
    incident_id = open_incident(api, headers, sensor_factory)
    age_incident(incident_id, minutes=15)

    ack = api.post(
        f"/api/voice/acknowledge/{incident_id}",
        headers=headers,
        json={"acknowledged_by": "Night Engineer"},
    ).json()
    assert ack["incident"]["state"] == "acknowledged"

    assert api.get("/api/voice/pending", headers=headers).json()["escalation_due"] == 0

    resp = api.post(f"/api/voice/escalate/{incident_id}?force=true", headers=headers)
    assert resp.status_code == 409
    assert "already acknowledged" in resp.json()["detail"]


def test_resolution_closes_the_incident(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    incident_id = open_incident(api, headers, sensor_factory)

    body = api.post(
        f"/api/voice/resolve/{incident_id}",
        headers=headers,
        json={"resolved_by": "Facilities"},
    ).json()
    assert body["incident"]["state"] == "resolved"
    assert body["incident"]["acknowledged_by"] == "Facilities"


def test_trial_plan_cannot_use_voice_escalation(
    api, tenant_factory, sensor_factory
):
    headers, _ = tenant_factory(plan="trial")
    incident_id = open_incident(api, headers, sensor_factory)

    resp = api.post(f"/api/voice/escalate/{incident_id}?force=true", headers=headers)
    assert resp.status_code == 403
    assert "does not include" in resp.json()["detail"]


def test_voice_script_falls_back_on_outage(
    api, tenant_factory, sensor_factory, break_gemini
):
    headers, _ = tenant_factory()
    incident_id = open_incident(api, headers, sensor_factory)
    break_gemini("outage")

    body = api.post(
        f"/api/voice/escalate/{incident_id}?force=true", headers=headers
    ).json()
    assert body["voice_dispatch_source"] == "fallback_template"
    assert "CyberLogix AI" in body["voice_script"]


def test_incident_from_another_tenant_is_invisible(
    api, tenant_factory, sensor_factory
):
    alice, _ = tenant_factory(company_name="Alice Foods")
    bob, _ = tenant_factory(company_name="Bob Labs")
    incident_id = open_incident(api, alice, sensor_factory, sensor_id="ALICE-1")

    resp = api.post(f"/api/voice/escalate/{incident_id}?force=true", headers=bob)
    assert resp.status_code == 404
