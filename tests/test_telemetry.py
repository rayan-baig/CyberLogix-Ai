"""Telemetry ingestion and breach detection."""


def pulse(api, headers, sensor_id="RACK-01", temp=70.0, humidity=None):
    payload = {"sensor_id": sensor_id, "temperature_fahrenheit": temp}
    if humidity is not None:
        payload["humidity_percent"] = humidity
    return api.post("/api/sensor-pulse", headers=headers, json=payload)


def test_nominal_reading(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    body = pulse(api, headers, temp=68.4).json()
    assert body["status"] == "nominal"
    assert body["industry"] == "CyberTech Data Centers"


def test_breach_opens_incident_with_gemini_sms(
    api, tenant_factory, sensor_factory, stub_gemini
):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    body = pulse(api, headers, temp=94.0, humidity=61.5).json()

    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert body["catastrophe_type"] == "HVAC Circuit Trip / Cooling Fan Stalled"
    assert body["sms_dispatch_source"] == "gemini"
    assert body["dispatched_sms_text"].startswith("URGENT:")
    assert "94.0°F > 78.0°F" in body["breach_details"]
    assert body["incident_id"].startswith("INC-")
    assert "61.5%" in stub_gemini.prompts[0]


def test_sustained_breach_does_not_spam_incidents(
    api, tenant_factory, sensor_factory, stub_gemini
):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    first = pulse(api, headers, temp=94.0).json()

    for temp in (95.0, 96.0, 97.0):
        follow = pulse(api, headers, temp=temp).json()
        assert follow["status"] == "CRITICAL_CATASTROPHE_ONGOING"
        assert follow["incident_id"] == first["incident_id"]

    # One breach, one Gemini draft, one incident on the books.
    assert len(stub_gemini.prompts) == 1
    incidents = api.get("/api/voice/incidents", headers=headers).json()
    assert incidents["count"] == 1
    assert incidents["incidents"][0]["temperature_fahrenheit"] == 97.0


def test_medical_lab_low_threshold_breach(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(
        headers, sensor_id="BLOOD-07", vertical="medical_lab", location="Cooler 3"
    )
    body = pulse(api, headers, sensor_id="BLOOD-07", temp=33.0).json()
    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert "33.0°F < 36.0°F" in body["breach_details"]


def test_medical_lab_midband_is_nominal(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="BLOOD-07", vertical="medical_lab")
    assert pulse(api, headers, "BLOOD-07", temp=40.0).json()["status"] == "nominal"


def test_threshold_boundary_is_not_a_breach(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-02", vertical="restaurant")
    assert pulse(api, headers, "FRZ-02", temp=32.0).json()["status"] == "nominal"


def test_unregistered_sensor_rejected(api, tenant_factory):
    headers, _ = tenant_factory()
    resp = pulse(api, headers, sensor_id="GHOST-1", temp=70.0)
    assert resp.status_code == 404
    assert "not registered" in resp.json()["detail"]


def test_gemini_outage_still_dispatches_an_alert(
    api, tenant_factory, sensor_factory, break_gemini
):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="YACHT-1", vertical="superyacht")
    break_gemini("outage")

    body = pulse(api, headers, "YACHT-1", temp=121.0).json()
    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert body["sms_dispatch_source"] == "fallback_template"
    assert "YACHT-1" in body["dispatched_sms_text"]


def test_missing_gemini_client_still_dispatches(
    api, tenant_factory, sensor_factory, break_gemini
):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="INV-12", vertical="solar_infrastructure")
    break_gemini("missing")

    body = pulse(api, headers, "INV-12", temp=140.0).json()
    assert body["sms_dispatch_source"] == "fallback_template"


def test_empty_gemini_response_falls_back(
    api, tenant_factory, sensor_factory, break_gemini
):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="HANGAR-4", vertical="private_aviation")
    break_gemini("empty")

    body = pulse(api, headers, "HANGAR-4", temp=99.0).json()
    assert body["sms_dispatch_source"] == "fallback_template"


def test_malformed_packet_rejected(api, tenant_factory):
    headers, _ = tenant_factory()
    resp = api.post("/api/sensor-pulse", headers=headers, json={"sensor_id": "X"})
    assert resp.status_code == 422
