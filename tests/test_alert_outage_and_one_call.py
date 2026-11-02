"""With Twilio down, the console says so; the call after the grace period is placed once.

Two promises an on-call manager relies on. When no alert text got
through, the card they look at must say that, not read as though the
roster has been told. And the voice call that follows an unanswered alert
rings once: not twice because the timer and the "run sweep now" button
fired together, and not again because the process restarted in between.
"""

import json
import shutil
import subprocess
import threading
from datetime import timedelta
from pathlib import Path

import pytest

import automation
import notifications
import scheduler
from store import STORE, VOICE_ESCALATION_GRACE_MINUTES, utc_now

PAGE = Path(__file__).resolve().parent.parent / "static" / "console.html"


class _Twilio:
    """Twilio that is down for texts and counts every call it is asked to place."""

    def __init__(self):
        self.texts_down = True
        self.text_attempts = 0
        self.calls = []
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.text_attempts += 1
                if outer.texts_down:
                    raise RuntimeError("HTTP 503: Service Unavailable")
                return type("M", (), {"sid": "SM-1", "status": "queued"})()

        class _Calls:
            def create(self, **kwargs):
                outer.calls.append(kwargs["to"])
                return type("C", (), {"sid": f"CA-{len(outer.calls)}",
                                      "status": "queued"})()

        self.messages = _Messages()
        self.calls_api = _Calls()


@pytest.fixture()
def twilio(monkeypatch):
    client = _Twilio()
    # notifications reads `client.calls.create`; keep the list separate.
    shim = type("Client", (), {"messages": client.messages,
                               "calls": client.calls_api})()
    monkeypatch.setattr(notifications, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(notifications, "TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setattr(notifications, "TWILIO_FROM_NUMBER", "+15550000")
    monkeypatch.setattr(notifications, "_client", shim)
    monkeypatch.setattr(notifications, "_client_error", None)
    return client


def _breach(api, headers, sensor_factory):
    sensor_factory(headers, sensor_id="RACK-01")
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01", "temperature_fahrenheit": 94.0})
    assert resp.status_code == 200, resp.text
    return resp.json()["incident_id"]


def _age_to(incident_id, minutes):
    incident = STORE.get_incident(incident_id)
    incident.opened_at = utc_now() - timedelta(minutes=minutes)
    STORE._db.put("incident", incident.incident_id, incident.to_row())


def _restart():
    STORE._forget_locked()
    STORE.load()


def _render_note(row):
    """Run the console's own textsFailedNote over a row the API returned."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    src = PAGE.read_text()
    esc_start = src.index("const esc = ")
    esc_end = src.index("\n\n", esc_start)
    fn_start = src.index("function textsFailedNote(i)")
    fn_end = src.index("\n}\n", fn_start) + 3
    harness = (
        src[esc_start:esc_end] + "\n" + src[fn_start:fn_end]
        + f"\nconsole.log(JSON.stringify(textsFailedNote({json.dumps(row)})));\n"
    )
    done = subprocess.run([node, "-e", harness], capture_output=True, text=True,
                          timeout=20)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ---- the console says the texts failed --------------------------------------


def test_with_twilio_down_the_alerts_card_says_the_texts_failed(
        api, operator_factory, sensor_factory, twilio):
    headers, tenant, _ = operator_factory(plan="trial")
    incident_id = _breach(api, headers, sensor_factory)
    assert twilio.text_attempts == 1

    row = api.get("/api/voice/incidents", headers=headers).json()["incidents"][0]
    assert row["incident_id"] == incident_id
    assert row["alert_texts_failed"] is True
    assert row["sms_delivery"]["delivered"] is False

    html = _render_note(row)
    assert "Alert texts failed" in html
    assert "No text has reached anyone." in html
    assert "Sending again at" in html
    assert "undefined" not in html and "null" not in html and "NaN" not in html

    # Every re-send used up, still down: the card stops promising a retry
    # and tells the reader to pick up the phone.
    for age in (1.1, 5.1, 15.1):
        _age_to(incident_id, age)
        automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))
    row = api.get("/api/voice/incidents", headers=headers).json()["incidents"][0]
    html = _render_note(row)
    assert "Alert texts failed" in html
    assert "Phone the roster yourself." in html
    assert "Sending again" not in html

    # Once it is resolved there is nothing left to warn about.
    assert _render_note({**row, "state": "resolved"}) == ""


def test_a_text_that_lands_on_a_retry_clears_the_warning(
        api, operator_factory, sensor_factory, twilio):
    headers, tenant, _ = operator_factory(plan="trial")
    incident_id = _breach(api, headers, sensor_factory)
    twilio.texts_down = False
    _age_to(incident_id, 1.2)
    automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))

    row = api.get("/api/voice/incidents", headers=headers).json()["incidents"][0]
    assert row["alert_texts_failed"] is False and row["texts_landed"] is True
    assert _render_note(row) == ""


# ---- the voice call after the grace period, exactly once ---------------------


def test_the_call_after_the_grace_period_is_placed_exactly_once(
        api, operator_factory, sensor_factory, twilio, monkeypatch):
    headers, tenant, _ = operator_factory(plan="enterprise")
    incident_id = _breach(api, headers, sensor_factory)
    tenant_id = tenant["tenant_id"]

    # Inside the grace period: two sweeps, a restart, no call.
    _age_to(incident_id, VOICE_ESCALATION_GRACE_MINUTES - 1)
    automation.sweep_tenant(STORE.get_tenant(tenant_id))
    _restart()
    automation.sweep_tenant(STORE.get_tenant(tenant_id))
    assert twilio.calls == []

    # Past it: the timer's sweep and the console's "sweep now" at the same
    # moment, both having seen the incident as due before either claims it.
    _age_to(incident_id, VOICE_ESCALATION_GRACE_MINUTES + 0.5)
    _restart()
    both_decided = threading.Barrier(2, timeout=5)
    claim = STORE.claim_voice_escalation

    def claim_together(incident, *args, **kwargs):
        both_decided.wait()
        return claim(incident, *args, **kwargs)

    monkeypatch.setattr(STORE, "claim_voice_escalation", claim_together)
    results = []

    def by_the_timer():
        results.append(scheduler.run_one_pass()["voice_calls_placed"])

    def by_the_button():
        resp = api.post("/api/autopilot/sweep", headers=headers)
        assert resp.status_code == 200, resp.text
        results.append(resp.json()["voice_calls_placed"])

    threads = [threading.Thread(target=by_the_timer),
               threading.Thread(target=by_the_button)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    monkeypatch.setattr(STORE, "claim_voice_escalation", claim)

    assert sorted(results) == [0, 1]
    assert len(twilio.calls) == 1

    # A restart, then two more sweeps an hour later: still one call.
    _restart()
    _age_to(incident_id, 70)
    automation.sweep_tenant(STORE.get_tenant(tenant_id))
    scheduler.run_one_pass()
    assert len(twilio.calls) == 1
    assert STORE.get_incident(incident_id).voice_escalated_at is not None


def test_the_claim_is_on_disk_before_the_phone_rings(
        api, operator_factory, sensor_factory, twilio, monkeypatch):
    """A process that dies mid-call must not ring the same person again
    when it comes back: the stamp is written before the dial, not after."""
    headers, tenant, _ = operator_factory(plan="enterprise")
    incident_id = _breach(api, headers, sensor_factory)
    _age_to(incident_id, VOICE_ESCALATION_GRACE_MINUTES + 1)

    seen_on_disk = []
    import voice_dispatch

    real = voice_dispatch.place_voice_call

    def dial(to, *args, **kwargs):
        row = STORE._db.get("incident", incident_id)
        seen_on_disk.append(row.get("voice_escalated_at"))
        return real(to, *args, **kwargs)

    monkeypatch.setattr(voice_dispatch, "place_voice_call", dial)
    automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))
    assert seen_on_disk and seen_on_disk[0] is not None

    _restart()
    automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))
    assert len(twilio.calls) == 1
