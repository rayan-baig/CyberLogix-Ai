"""The unattended watchdog.

Escalation only means anything if something runs the sweep when nobody is
looking. These cover the loop's contract: it sweeps every tenant, one bad
estate cannot silence the others, and it can be switched off for a
deployment that drives the endpoint externally.
"""

import asyncio

import pytest

import scheduler


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setenv("CYBERLOGIX_SWEEP_SECONDS", "1")


def open_breach(api, headers, sensor_factory, age_incident, sensor_id="RACK-01"):
    sensor_factory(headers, sensor_id=sensor_id)
    body = api.post(
        "/api/sensor-pulse",
        headers=headers,
        json={"sensor_id": sensor_id, "temperature_fahrenheit": 94.0},
    ).json()
    age_incident(body["incident_id"], minutes=30)
    return body["incident_id"]


def test_a_pass_escalates_without_anyone_asking(
    api, operator_factory, sensor_factory, age_incident, monkeypatch
):
    """The gap this closes: an unacknowledged 3am breach sat open."""
    placed = []
    monkeypatch.setattr(
        "voice_dispatch.place_voice_call",
        lambda to, spoken, tenant_id=None, action_url=None: (
            placed.append(to)
            or {"channel": "voice", "to": to, "delivered": True,
                "status": "queued", "provider_sid": "CA1", "detail": "ok"}
        ),
    )
    headers, _, _ = operator_factory()
    incident_id = open_breach(api, headers, sensor_factory, age_incident)

    summary = scheduler.run_one_pass()

    assert summary["voice_calls_placed"] == 1
    assert placed, "the watchdog must actually dial"
    from store import STORE

    assert STORE.get_incident(incident_id).voice_escalated_at is not None


def test_one_broken_estate_does_not_silence_the_others(
    api, operator_factory, sensor_factory, age_incident, monkeypatch
):
    first, _, _ = operator_factory(company_name="Acme", email="a@x.com")
    second, _, _ = operator_factory(company_name="Beta", email="b@x.com")
    open_breach(api, first, sensor_factory, age_incident, "RACK-01")
    open_breach(api, second, sensor_factory, age_incident, "RACK-02")

    real = scheduler.sweep_tenant
    seen = []

    def _explode(tenant, auto_escalate=True):
        seen.append(tenant.tenant_id)
        if len(seen) == 1:
            raise RuntimeError("this estate is broken")
        return real(tenant, auto_escalate)

    monkeypatch.setattr(scheduler, "sweep_tenant", _explode)
    summary = scheduler.run_one_pass()

    assert len(summary["failed_tenants"]) == 1
    assert summary["tenants_swept"] == 1
    assert len(seen) == 2, "the second estate must still be swept"


def test_the_loop_can_be_switched_off(monkeypatch):
    """A deployment with Cloud Scheduler must not double-call."""
    monkeypatch.setenv("CYBERLOGIX_SWEEP_SECONDS", "0")
    assert scheduler.status()["enabled"] is False

    async def _check():
        assert scheduler.start() is None
        await scheduler.stop()

    asyncio.run(_check())


def test_a_nonsense_interval_disables_rather_than_crashes(monkeypatch):
    monkeypatch.setenv("CYBERLOGIX_SWEEP_SECONDS", "every minute please")
    assert scheduler.status()["enabled"] is False


def test_the_loop_starts_and_stops_cleanly(enabled):
    async def _run():
        task = scheduler.start()
        assert task is not None
        assert scheduler.start() is task, "starting twice must not spawn two loops"
        assert scheduler.status()["running"] is True
        await scheduler.stop()
        assert scheduler.status()["running"] is False

    asyncio.run(_run())


def test_health_reports_the_scheduler(api):
    body = api.get("/api/health").json()
    assert body["autopilot_scheduler"]["enabled"] is False
    assert "external scheduler" in body["autopilot_scheduler"]["note"]
