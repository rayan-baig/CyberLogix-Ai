"""Shared fixtures. Every test runs against a freshly emptied store."""

import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must be set before the store module builds its Database at import time, so
# the suite never touches a real file on disk.
os.environ["CYBERLOGIX_DB_PATH"] = ":memory:"

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
def operator_factory(api, tenant_factory):
    """Onboard a tenant, bootstrap its owner, and sign in.

    Returns (bearer headers, tenant payload, user payload).
    """

    def _make(plan="enterprise", company_name="Acme Cold Storage",
              email="dana@example.com", full_name="Dana Reyes",
              password="correct-horse-battery"):
        key_headers, tenant = tenant_factory(plan=plan, company_name=company_name)
        created = api.post(
            "/api/accounts/bootstrap",
            headers=key_headers,
            json={
                "email": email,
                "full_name": full_name,
                "password": password,
                "role": "owner",
            },
        )
        assert created.status_code == 201, created.text

        signed_in = api.post(
            "/api/accounts/login", json={"email": email, "password": password}
        )
        assert signed_in.status_code == 200, signed_in.text
        body = signed_in.json()
        return (
            {"Authorization": f"Bearer {body['token']}"},
            tenant,
            body["user"],
        )

    return _make


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


@pytest.fixture()
def configured_twilio(monkeypatch):
    """Present Twilio credentials backed by a fake client that always sends."""
    import notifications

    class _Result:
        sid = "SM-TEST"
        status = "queued"

    class _Endpoint:
        def create(self, **kwargs):
            return _Result()

    class _Client:
        messages = _Endpoint()
        calls = _Endpoint()

    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")
    monkeypatch.setattr(notifications, "_client", _Client())
    monkeypatch.setattr(notifications, "_client_error", None)
    return _Client
