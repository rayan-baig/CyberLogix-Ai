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

# The unattended sweep loop must not run under the suite: a background task
# placing calls between tests is exactly the kind of flake that takes a day
# to track down. The scheduler is tested directly instead.
os.environ["CYBERLOGIX_SWEEP_SECONDS"] = "0"

# Provisioning a paid plan needs the key. The suite provisions Enterprise
# tenants constantly, so it sets one — and the fact that every one of those
# calls now has to carry it is the point: without the header they are
# refused, which is what the test for that asserts.
os.environ["CYBERLOGIX_PROVISIONING_KEY"] = "test-provisioning-key"

# The platform operator's own credential. Issuing an invoice and recording
# a payment are the vendor's side of the transaction, so the suite has to
# hold this to exercise them at all — which is the point of the change
# that introduced it.
os.environ["CYBERLOGIX_ADMIN_KEY"] = "test-admin-key"

import gemini  # noqa: E402
import store as _store_module  # noqa: E402
from main import app  # noqa: E402
from store import STORE  # noqa: E402

# Password hashing at a fraction of its real cost. The suite hashes and
# checks some two thousand passwords; at the production cost of about
# 50 ms each that was half of the whole run, spent proving nothing about
# the code under test. Hashing and checking both read this one value, so
# every test still signs in through the real scrypt path.
# test_accounts.py checks the production value has not been lowered.
_store_module.SCRYPT_N = 2**4

# Every (method, route) a test reaches, for test_every_route_is_tested.
# Recorded at the edge of the app so it sees exactly what the router
# matched, whichever client or helper sent the request.
ROUTES_HIT = set()


class _RecordRoutes:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        try:
            await self.inner(scope, receive, send)
        finally:
            route = scope.get("route") if scope.get("type") == "http" else None
            if route is not None and getattr(route, "path", None):
                ROUTES_HIT.add((scope["method"], route.path))


app.add_middleware(_RecordRoutes)

ROUTE_AUDIT = "test_every_route_is_tested.py"
WHOLE_SUITE = pytest.StashKey[bool]()


def pytest_collection_modifyitems(session, config, items):
    """The route audit runs last, and only knows anything after a whole run.

    It reads what every other test reached, so it is moved to the end, and
    it is told whether this run is the whole suite: a run of one file
    reaches a handful of routes and says nothing about the rest.
    """
    audit = [i for i in items if i.path.name == ROUTE_AUDIT]
    rest = [i for i in items if i.path.name != ROUTE_AUDIT]
    items[:] = rest + audit
    on_disk = {p.name for p in Path(__file__).parent.glob("test_*.py")}
    collected = {i.path.name for i in items}
    narrowed = bool(config.option.keyword or config.option.markexpr
                    or getattr(config.option, "deselect", None)
                    or getattr(config.option, "lf", False))
    config.stash[WHOLE_SUITE] = collected == on_disk and not narrowed



@pytest.fixture(autouse=True)
def clean_store():
    STORE.reset()
    yield
    STORE.reset()


@pytest.fixture(autouse=True)
def clean_rate_limits():
    """The sign-up window is process-global, not per-store.

    STORE.reset() does not touch it, so without this the fourth test in a
    file that signs anybody up starts getting 429s from the third test's
    traffic — and the failure surfaces as a KeyError on the response body,
    miles from the cause.
    """
    import firstday
    import signup
    import simulator

    signup.reset_rate_limits()
    firstday.forget()
    simulator.forget()
    yield
    signup.reset_rate_limits()
    firstday.forget()
    simulator.forget()


@pytest.fixture()
def api():
    return TestClient(app)


@pytest.fixture()
def admin_headers():
    """The platform operator, who acts for the vendor rather than a tenant."""
    return {"X-CyberLogix-Admin": "test-admin-key"}


@pytest.fixture()
def settle(api, admin_headers):
    """Record a payment the way the vendor does: with the operator key."""

    def _settle(tenant_id, invoice_id, reference="WIRE-1", amount_usd=None):
        body = {"reference": reference}
        if amount_usd is not None:
            body["amount_usd"] = amount_usd
        return api.post(
            f"/api/invoices/{invoice_id}/paid",
            params={"tenant_id": tenant_id},
            headers=admin_headers,
            json=body,
        )

    return _settle


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
            headers={"X-CyberLogix-Provisioning": "test-provisioning-key"},
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


@pytest.fixture()
def owner_headers(api):
    """Bearer headers for a named owner on an already-onboarded tenant.

    Suspending a licence and moving a plan are the two routes that will
    not accept the tenant API key: turning off a company's monitoring is
    not an action whose audit record should read "API key". Tests that
    need those say so through this.
    """

    def _make(key_headers, email="owner@example.com"):
        made = api.post(
            "/api/accounts/bootstrap",
            headers=key_headers,
            json={
                "email": email,
                "full_name": "Owner",
                "password": "correct-horse-battery",
                "role": "owner",
            },
        )
        if made.status_code == 409:  # already bootstrapped by the caller
            pass
        signed_in = api.post(
            "/api/accounts/login",
            json={"email": email, "password": "correct-horse-battery"},
        )
        assert signed_in.status_code == 200, signed_in.text
        return {"Authorization": f"Bearer {signed_in.json()['token']}"}

    return _make


@pytest.fixture()
def mailbox(monkeypatch):
    """A configured mail transport that captures instead of sending.

    Everything above the socket runs for real: the claim, the suppression
    check, the header assembly, the retry accounting. Only the last
    function — the one that opens the connection — is replaced, so a test
    that says a chase went out is asserting on the message that would
    actually have left the building.
    """
    import mail

    sent = []

    class _Mailbox(list):
        def __init__(self):
            super().__init__()
            self.fail_with = None
            self.fail_times = 0

        def subjects(self):
            return [m["Subject"] for m in self]

        def bodies(self):
            # The plain-text part: the whole message, or the text half of
            # one sent with an HTML alternative.
            return [m.get_body(preferencelist=("plain",)).get_content()
                    for m in self]

        def to(self, address):
            return [m for m in self if m["To"] == address]

    box = _Mailbox()

    def _capture(message):
        if box.fail_times:
            box.fail_times -= 1
            raise box.fail_with or OSError("simulated mail outage")
        box.append(message)
        sent.append(message)

    monkeypatch.setattr(mail, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(mail, "MAIL_FROM", "billing@cyberlogix.example")
    monkeypatch.setattr(mail, "PUBLIC_BASE_URL", "https://hub.example")
    monkeypatch.setattr(mail, "_transport", _capture)
    return box


@pytest.fixture()
def no_mail_transport(monkeypatch):
    """A deployment where nobody has configured SMTP yet."""
    import mail

    monkeypatch.setattr(mail, "SMTP_HOST", "")
    monkeypatch.setattr(mail, "MAIL_FROM", "")
    return mail
