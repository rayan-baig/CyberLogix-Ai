"""Shared fixtures. Every test runs against a freshly emptied store."""

import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gemini  # noqa: E402
from main import app  # noqa: E402
from store import STORE  # noqa: E402


@pytest.fixture(autouse=True)
def clean_store():
    STORE.reset()
    yield
    STORE.reset()


@pytest.fixture()
def api():
    return TestClient(app)


class _StubModels:
    """Stands in for client.models, recording the prompts it receives."""

    def __init__(self, text=None, boom=False):
        self._text = text
        self._boom = boom
        self.prompts = []

    def generate_content(self, model, contents):
        self.model = model
        self.prompts.append(contents)
        if self._boom:
            raise RuntimeError("simulated Gemini outage")
        return type("Resp", (), {"text": self._text})()


@pytest.fixture(autouse=True)
def stub_gemini(monkeypatch):
    """Default every test to a working, offline Gemini stub."""
    models = _StubModels(text="URGENT: equipment failing, attend the site now.")
    monkeypatch.setattr(gemini, "client", type("C", (), {"models": models})())
    return models


@pytest.fixture()
def break_gemini(monkeypatch):
    def _apply(mode="outage"):
        if mode == "missing":
            monkeypatch.setattr(gemini, "client", None)
            return None
        models = _StubModels(boom=(mode == "outage"), text="  ")
        monkeypatch.setattr(gemini, "client", type("C", (), {"models": models})())
        return models

    return _apply


@pytest.fixture()
def tenant_factory(api):
    """Onboard a tenant and return (auth headers, tenant payload)."""

    def _make(plan="enterprise", company_name="Acme Cold Storage"):
        resp = api.post(
            "/api/licenses/tenants",
            json={
                "company_name": company_name,
                "contact_name": "Dana Reyes",
                "contact_phone": "+1-555-0100",
                "contact_email": "ops@example.com",
                "plan": plan,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        return {"X-CyberLogix-Key": body["api_key"]}, body["tenant"]

    return _make


@pytest.fixture()
def sensor_factory(api):
    """Register a sensor against a tenant."""

    def _make(
        headers,
        sensor_id="RACK-01",
        vertical="cybersecurity",
        location="Austin DC / Hall B",
    ):
        resp = api.post(
            "/api/licenses/me/sensors",
            headers=headers,
            json={
                "sensor_id": sensor_id,
                "industry_vertical": vertical,
                "location_name": location,
            },
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["sensor"]

    return _make


@pytest.fixture()
def age_incident():
    """Backdate an incident so grace-window logic can be exercised."""

    def _age(incident_id, minutes):
        incident = STORE.get_incident(incident_id)
        incident.opened_at -= timedelta(minutes=minutes)
        return incident

    return _age
