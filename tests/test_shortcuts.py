"""Sector shortcuts: the document each vertical has to produce."""

import json

import pytest


def seed(api, headers, sensor_factory, sensor_id="FRZ-1", vertical="restaurant",
         temps=(28.0, 29.0, 45.0)):
    sensor_factory(headers, sensor_id=sensor_id, vertical=vertical)
    for temp in temps:
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": sensor_id, "temperature_fahrenheit": temp})


def test_every_vertical_offers_a_shortcut(api):
    from store import INDUSTRY_PROFILES

    body = api.get("/api/shortcuts").json()
    assert body["count"] == len(INDUSTRY_PROFILES)
    names = {s["vertical"]: s["shortcut_name"] for s in body["shortcuts"]}
    assert names["restaurant"] == "Health Inspector Log Formatter"
    assert names["logistics"] == "Reefer Cargo Handover Pass"
    assert names["medical_lab"] == "OSHA Cold-Storage Specimen Audit"
    assert all(s["description"] for s in body["shortcuts"])


def test_shortcut_is_generated_from_real_readings(
    api, tenant_factory, sensor_factory, stub_gemini
):
    headers, _ = tenant_factory()
    seed(api, headers, sensor_factory)
    stub_gemini._text = "HEALTH DEPARTMENT TEMPERATURE LOG\nStore 118 walk-in..."

    body = api.post("/api/shortcuts/restaurant?days=30", headers=headers).json()
    assert body["status"] == "SHORTCUT_GENERATED"
    assert body["shortcut_name"] == "Health Inspector Log Formatter"
    assert body["document_source"] == "gemini"
    assert body["document"].startswith("HEALTH DEPARTMENT")

    ev = body["evidence"]
    assert ev["sensor_count"] == 1
    assert ev["total_readings"] == 3
    assert ev["total_excursions"] == 1
    assert ev["compliance_percent"] == pytest.approx(66.67, abs=0.01)
    assert ev["sensors"][0]["min_f"] == 28.0
    assert ev["sensors"][0]["max_f"] == 45.0


def test_the_model_is_given_only_real_figures(
    api, tenant_factory, sensor_factory, stub_gemini
):
    """A fabricated reading would make the document worthless as evidence."""
    headers, _ = tenant_factory()
    seed(api, headers, sensor_factory)
    api.post("/api/shortcuts/restaurant", headers=headers)

    prompt = stub_gemini.prompts[-1]
    assert "FRZ-1" in prompt
    assert "inventing a reading" in prompt
    assert "'min_f': 28.0" in prompt and "'max_f': 45.0" in prompt


def test_an_empty_estate_is_refused_not_attested(api, tenant_factory):
    """A document with nothing behind it reads as an attestation."""
    headers, _ = tenant_factory()
    resp = api.post("/api/shortcuts/restaurant", headers=headers)
    assert resp.status_code == 409
    assert "nothing to attest to" in resp.json()["detail"]


def test_only_that_vertical_s_sensors_are_counted(
    api, tenant_factory, sensor_factory, stub_gemini
):
    headers, _ = tenant_factory()
    seed(api, headers, sensor_factory, sensor_id="FRZ-1", vertical="restaurant")
    seed(api, headers, sensor_factory, sensor_id="RACK-1",
         vertical="cybersecurity", temps=(68.0, 70.0))

    ev = api.post("/api/shortcuts/restaurant", headers=headers).json()["evidence"]
    assert ev["sensor_count"] == 1
    assert ev["sensors"][0]["sensor_id"] == "FRZ-1"


def test_fallback_document_still_carries_the_figures(
    api, tenant_factory, sensor_factory, break_gemini
):
    headers, _ = tenant_factory()
    seed(api, headers, sensor_factory)
    break_gemini("outage")

    body = api.post("/api/shortcuts/restaurant", headers=headers).json()
    assert body["document_source"] == "fallback_template"
    document = body["document"]
    assert "Health Inspector Log Formatter" in document
    assert "FRZ-1" in document
    assert "Readings logged: 3" in document
    assert "Excursions outside the safe band: 1" in document


def test_incidents_appear_in_the_record(
    api, tenant_factory, sensor_factory, break_gemini
):
    headers, _ = tenant_factory()
    seed(api, headers, sensor_factory)
    break_gemini("outage")

    body = api.post("/api/shortcuts/restaurant", headers=headers).json()
    assert len(body["evidence"]["incidents"]) == 1
    assert "Incidents:" in body["document"]


def test_unknown_vertical_rejected(api, tenant_factory):
    headers, _ = tenant_factory()
    resp = api.post("/api/shortcuts/casino", headers=headers)
    assert resp.status_code == 400
    assert "Allowed keys" in resp.json()["detail"]


def test_generation_is_audited_under_the_operator(
    api, operator_factory, sensor_factory, stub_gemini
):
    headers, _, _ = operator_factory()
    seed(api, headers, sensor_factory)
    api.post("/api/shortcuts/restaurant", headers=headers)

    trail = api.get("/api/accounts/audit", headers=headers).json()
    entry = [e for e in trail["entries"] if e["action"] == "shortcut.generated"][0]
    assert entry["actor"] == "Dana Reyes <dana@example.com>"
    assert "Health Inspector Log Formatter" in entry["detail"]


def test_shortcuts_require_authentication(api):
    assert api.post("/api/shortcuts/restaurant").status_code == 401
    # The catalogue itself is public — it is a feature list.
    assert api.get("/api/shortcuts").status_code == 200


def test_generation_is_metered(api, tenant_factory, sensor_factory, stub_gemini):
    headers, _ = tenant_factory()
    seed(api, headers, sensor_factory)
    api.post("/api/shortcuts/restaurant", headers=headers)

    costs = api.get("/api/costs", headers=headers).json()
    assert costs["totals"]["ai_calls"] >= 1
