"""Behavioural tests for the CyberLogix thermal catastrophe engine."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


@pytest.fixture()
def api():
    return TestClient(main.app)


class _StubModels:
    """Stands in for client.models, recording the prompt it receives."""

    def __init__(self, text=None, boom=False):
        self._text = text
        self._boom = boom
        self.last_prompt = None
        self.last_model = None

    def generate_content(self, model, contents):
        self.last_model = model
        self.last_prompt = contents
        if self._boom:
            raise RuntimeError("simulated Gemini outage")
        return type("Resp", (), {"text": self._text})()


class _StubClient:
    def __init__(self, models):
        self.models = models


@pytest.fixture()
def stub_gemini(monkeypatch):
    def _install(text="ALERT: rack is cooking. Get to the data hall now.", boom=False):
        models = _StubModels(text=text, boom=boom)
        monkeypatch.setattr(main, "client", _StubClient(models))
        return models

    return _install


def test_health_reports_all_eight_profiles(api):
    body = api.get("/api/health").json()
    assert body["status"] == "online"
    assert body["active_profiles"] == 8
    assert body["engine"] == "CyberLogix Universal Common Catastrophe IoT Engine"


def test_industry_catalogue_lists_every_vertical(api):
    body = api.get("/api/industries").json()
    assert body["count"] == 8
    verticals = {entry["vertical"] for entry in body["industries"]}
    assert verticals == set(main.INDUSTRY_PROFILES)


def test_nominal_reading_returns_stable(api):
    resp = api.post(
        "/api/sensor-pulse",
        json={
            "sensor_id": "RACK-01",
            "industry_vertical": "cybersecurity",
            "location_name": "Austin DC / Hall B",
            "temperature_fahrenheit": 68.4,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "nominal"
    assert body["industry"] == "CyberTech Data Centers"


def test_high_threshold_breach_dispatches_gemini_sms(api, stub_gemini):
    models = stub_gemini()
    resp = api.post(
        "/api/sensor-pulse",
        json={
            "sensor_id": "RACK-01",
            "industry_vertical": "cybersecurity",
            "location_name": "Austin DC / Hall B",
            "temperature_fahrenheit": 94.0,
            "humidity_percent": 61.5,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert body["catastrophe_type"] == "HVAC Circuit Trip / Cooling Fan Stalled"
    assert body["dispatch_source"] == "gemini"
    assert body["dispatched_sms_text"].startswith("ALERT:")
    assert "94.0°F > 78.0°F" in body["breach_details"]
    assert models.last_model == "gemini-2.5-flash"
    assert "61.5%" in models.last_prompt


def test_medical_lab_low_threshold_breach(api, stub_gemini):
    stub_gemini()
    body = api.post(
        "/api/sensor-pulse",
        json={
            "sensor_id": "BLOOD-07",
            "industry_vertical": "medical_lab",
            "location_name": "Mercy Blood Bank / Cooler 3",
            "temperature_fahrenheit": 33.0,
        },
    ).json()
    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert "33.0°F < 36.0°F" in body["breach_details"]


def test_medical_lab_midband_is_nominal(api):
    body = api.post(
        "/api/sensor-pulse",
        json={
            "sensor_id": "BLOOD-07",
            "industry_vertical": "medical_lab",
            "location_name": "Mercy Blood Bank / Cooler 3",
            "temperature_fahrenheit": 40.0,
        },
    ).json()
    assert body["status"] == "nominal"


def test_threshold_boundary_is_not_a_breach(api):
    body = api.post(
        "/api/sensor-pulse",
        json={
            "sensor_id": "FREEZER-02",
            "industry_vertical": "restaurant",
            "location_name": "Store 118 / Walk-In",
            "temperature_fahrenheit": 32.0,
        },
    ).json()
    assert body["status"] == "nominal"


def test_gemini_outage_falls_back_to_template(api, stub_gemini):
    stub_gemini(boom=True)
    body = api.post(
        "/api/sensor-pulse",
        json={
            "sensor_id": "YACHT-ENG-1",
            "industry_vertical": "superyacht",
            "location_name": "M/Y Aurelia / Engine Bay",
            "temperature_fahrenheit": 121.0,
        },
    ).json()
    assert body["dispatch_source"] == "fallback_template"
    assert "YACHT-ENG-1" in body["dispatched_sms_text"]


def test_empty_gemini_response_falls_back(api, stub_gemini):
    stub_gemini(text="   ")
    body = api.post(
        "/api/sensor-pulse",
        json={
            "sensor_id": "HANGAR-4",
            "industry_vertical": "private_aviation",
            "location_name": "Teterboro / Bay 4",
            "temperature_fahrenheit": 99.0,
        },
    ).json()
    assert body["dispatch_source"] == "fallback_template"


def test_uninitialized_client_still_dispatches(api, monkeypatch):
    monkeypatch.setattr(main, "client", None)
    body = api.post(
        "/api/sensor-pulse",
        json={
            "sensor_id": "INV-12",
            "industry_vertical": "solar_infrastructure",
            "location_name": "Mojave Array / String 12",
            "temperature_fahrenheit": 140.0,
        },
    ).json()
    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert body["dispatch_source"] == "fallback_template"


def test_vertical_is_case_and_whitespace_tolerant(api):
    body = api.post(
        "/api/sensor-pulse",
        json={
            "sensor_id": "CLUB-01",
            "industry_vertical": "  Country_Club ",
            "location_name": "Oakmont / Clubhouse Kitchen",
            "temperature_fahrenheit": 20.0,
        },
    ).json()
    assert body["industry"] == "High-End Country Clubs"


def test_unknown_vertical_rejected(api):
    resp = api.post(
        "/api/sensor-pulse",
        json={
            "sensor_id": "X-1",
            "industry_vertical": "casino",
            "location_name": "Nowhere",
            "temperature_fahrenheit": 70.0,
        },
    )
    assert resp.status_code == 400
    assert "Allowed keys" in resp.json()["detail"]


def test_malformed_packet_rejected(api):
    resp = api.post("/api/sensor-pulse", json={"sensor_id": "X-1"})
    assert resp.status_code == 422
