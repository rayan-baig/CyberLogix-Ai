"""Running more than one server: the external scheduler's route.

With the built-in loop off (CYBERLOGIX_SWEEP_SECONDS=0), DEPLOY.md told
operators to drive POST /api/autopilot/sweep from outside. That route only
sweeps the business that calls it, and recorded no heartbeat, so every
other business went unwatched and nothing noticed when the scheduler
stopped. /api/autopilot/sweep-all sweeps the fleet and records the beat,
and the watchdog holds an external scheduler to a limit once it has fired.
"""

from datetime import timedelta

import pytest

import scheduler
import watchdog
from store import STORE, iso, utc_now

ADMIN = {"X-CyberLogix-Admin": "test-admin-key"}


@pytest.fixture(autouse=True)
def external(monkeypatch):
    monkeypatch.setattr(scheduler, "_interval", lambda: 0)
    monkeypatch.delenv("CYBERLOGIX_EXTERNAL_SWEEP_SECONDS", raising=False)


def test_sweep_all_needs_the_platform_admin_key(api, operator_factory):
    headers, _, _ = operator_factory()
    assert api.post("/api/autopilot/sweep-all").status_code in (401, 403)
    assert api.post("/api/autopilot/sweep-all", headers=headers).status_code in (401, 403)


def test_sweep_all_covers_every_business_and_records_the_beat(api, operator_factory):
    operator_factory(company_name="One Co", email="one@example.com")
    operator_factory(company_name="Two Co", email="two@example.com")
    resp = api.post("/api/autopilot/sweep-all", headers=ADMIN)
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenants_swept"] == 2
    assert STORE._db.get("meta", "heartbeat").get("last_sweep_at")
    assert api.get("/api/watchdog").status_code == 200


def test_an_external_scheduler_that_stops_is_noticed():
    # Never fired yet: not a fault.
    assert watchdog.state()["healthy"] is True
    # Fired, then went quiet past three intervals (default five minutes).
    STORE._db.put("meta", "heartbeat", {
        "last_sweep_at": iso(utc_now() - timedelta(seconds=16 * 60)), "sweeps": 1})
    body = watchdog.state()
    assert body["healthy"] is False
    assert "sweep has not run" in " ".join(body["reasons"])
    # Within the limit is healthy.
    STORE._db.put("meta", "heartbeat", {
        "last_sweep_at": iso(utc_now() - timedelta(seconds=10 * 60)), "sweeps": 1})
    assert watchdog.state()["healthy"] is True
