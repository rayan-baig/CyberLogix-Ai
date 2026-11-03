"""Sweeps run one at a time, on time, and never send anything twice.

The sweep is what turns an unanswered text into a phone call. Measured
under load (tools/load_test.py, docs/LOAD-TEST.md), two things about how
it was scheduled mattered more than how fast it ran:

* Passes could stack. The built-in loop and an outside scheduler, or an
  outside scheduler firing every minute while a pass waited out telephony
  timeouts, ran side by side. Each send was claimed, so nobody was texted
  twice, but every stacked pass held a worker thread for minutes.
* The loop slept a whole interval after each pass, so its period was the
  interval plus the pass, and the first sweep after a deploy came a full
  interval late while the watchdog answered 503.
"""

import asyncio
import threading
import time
from datetime import timedelta

import notifications
import scheduler
import watchdog
from store import STORE, VOICE_ESCALATION_GRACE_MINUTES, utc_now


def _slow_twilio(monkeypatch, gate: threading.Event, calls: list):
    class _Calls:
        def create(self, **kwargs):
            calls.append(kwargs["to"])
            gate.wait(10)
            return type("C", (), {"sid": f"CA-{len(calls)}", "status": "queued"})()

    class _Messages:
        def create(self, **kwargs):
            return type("M", (), {"sid": "SM-1", "status": "queued"})()

    client = type("Client", (), {"messages": _Messages(), "calls": _Calls()})()
    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")
    monkeypatch.setattr(notifications, "_client", client)
    monkeypatch.setattr(notifications, "_client_error", None)


def _due_incident(api, operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="RACK-01")
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01", "temperature_fahrenheit": 94.0})
    assert resp.status_code == 200, resp.text
    incident = STORE.get_incident(resp.json()["incident_id"])
    incident.opened_at = utc_now() - timedelta(minutes=VOICE_ESCALATION_GRACE_MINUTES + 1)
    STORE._db.put("incident", incident.incident_id, incident.to_row())
    return headers, incident


def test_a_second_fleet_sweep_stands_aside_while_one_is_running(
        api, operator_factory, sensor_factory, monkeypatch):
    gate, calls = threading.Event(), []
    _slow_twilio(monkeypatch, gate, calls)
    monkeypatch.setattr(watchdog, "ping", lambda: None)
    _due_incident(api, operator_factory, sensor_factory)

    first: list = []
    runner = threading.Thread(target=lambda: first.append(scheduler.run_one_pass()))
    runner.start()
    deadline = time.time() + 10
    while not calls and time.time() < deadline:
        time.sleep(0.01)
    assert calls, "the first sweep never reached the phone call"
    sweeps_before = (STORE._db.get("meta", "heartbeat") or {}).get("sweeps", 0)

    # The outside scheduler fires again while the first pass is on the phone.
    started = time.time()
    second = api.post("/api/autopilot/sweep-all",
                      headers={"X-CyberLogix-Admin": "test-admin-key"})
    assert time.time() - started < 5, "the second sweep waited for the first"
    assert second.status_code == 200, second.text
    assert second.json()["skipped"] == "another sweep is still running"
    assert second.json()["voice_calls_placed"] == 0
    # Standing aside is not a sweep: it does not tell the watchdog all is well.
    assert (STORE._db.get("meta", "heartbeat") or {}).get("sweeps", 0) == sweeps_before

    gate.set()
    runner.join(timeout=15)
    assert first[0]["voice_calls_placed"] == 1
    assert len(calls) == 1
    # And the next pass, once the first is done, runs as normal.
    assert "skipped" not in scheduler.run_one_pass()
    assert len(calls) == 1


def test_a_sweep_that_dies_releases_the_lock(monkeypatch):
    monkeypatch.setattr(scheduler, "_sweep_every_tenant",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        scheduler.run_one_pass()
    except RuntimeError:
        pass
    monkeypatch.undo()
    monkeypatch.setattr(watchdog, "ping", lambda: None)
    assert "skipped" not in scheduler.run_one_pass()


def test_the_loop_keeps_its_cadence_when_a_pass_is_slow(monkeypatch):
    """A pass that takes most of the interval must not stretch the period."""
    starts: list = []

    def slow_pass():
        starts.append(time.monotonic())
        time.sleep(0.12)
        return {}

    monkeypatch.setattr(scheduler, "run_one_pass", slow_pass)
    monkeypatch.setattr(scheduler, "FIRST_SWEEP_AFTER_SECONDS", 0.0)

    async def drive():
        task = asyncio.create_task(scheduler._loop(0.2))
        while len(starts) < 5:
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(asyncio.wait_for(drive(), timeout=10))
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    # Sleeping a whole interval after each pass made every gap 0.32s.
    assert sum(gaps) / len(gaps) < 0.27, gaps


def test_a_pass_that_overruns_is_followed_at_once_not_by_a_burst(monkeypatch):
    starts: list = []

    def pass_(n=[0]):
        n[0] += 1
        starts.append(time.monotonic())
        time.sleep(0.35 if n[0] == 1 else 0.01)
        return {}

    monkeypatch.setattr(scheduler, "run_one_pass", pass_)
    monkeypatch.setattr(scheduler, "FIRST_SWEEP_AFTER_SECONDS", 0.0)

    async def drive():
        task = asyncio.create_task(scheduler._loop(0.1))
        while len(starts) < 4:
            await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(asyncio.wait_for(drive(), timeout=10))
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    # The pass after the slow one starts straight away; the ones after that
    # are back on the interval, not three passes crammed into the overrun.
    assert gaps[1] > 0.07 and gaps[2] > 0.07, gaps


def test_the_first_sweep_after_a_start_does_not_wait_a_whole_interval(monkeypatch):
    starts: list = []
    monkeypatch.setattr(scheduler, "run_one_pass", lambda: starts.append(1) or {})
    monkeypatch.setattr(scheduler, "FIRST_SWEEP_AFTER_SECONDS", 0.05)

    async def drive():
        task = asyncio.create_task(scheduler._loop(3600))
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert starts == [1]
