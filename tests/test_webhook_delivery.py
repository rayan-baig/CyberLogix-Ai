"""Outgoing webhooks: patient, but never at the alert's expense.

Slack, Teams, PagerDuty and plain webhooks are courtesy copies of an
alert. They should survive a receiver that blips (one retry, after a
pause), give up within a fixed budget when one hangs, never make the
escalation sweep wait on them, and never show their target -- which is
itself the credential -- in an API response, the audit trail, a log line
or the data export.
"""

import http.server
import logging
import threading
import time
from datetime import timedelta

import pytest

import automation
import webhooks
from store import STORE, VOICE_ESCALATION_GRACE_MINUTES, utc_now

SLACK = "https://hooks.slack.com/services/T0001/B0002/sEcReTtOkEnVaLuE42"
TEAMS = "https://example.webhook.office.com/webhookb2/aaaa/IncomingWebhook/tEaMsSeCrEt99"
GENERIC = "https://alerts.example.com/hook/gEnErIcSeCrEt77"
PAGERDUTY_KEY = "R0UT1NGKEYpagerduty0001"


def _hook(api, headers, kind, target):
    resp = api.post("/api/webhooks", headers=headers,
                    json={"kind": kind, "target": target, "label": kind})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _breach(api, headers, sensor_factory):
    sensor_factory(headers, sensor_id="RACK-01")
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "RACK-01", "temperature_fahrenheit": 94.0})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture()
def quick(monkeypatch):
    monkeypatch.setattr(webhooks, "WEBHOOK_RETRY_PAUSE_SECONDS", 0.05)


# ---- retry sensibly ---------------------------------------------------------


def test_a_receiver_that_blips_gets_one_more_try(api, operator_factory,
                                                sensor_factory, monkeypatch, quick):
    headers, _, _ = operator_factory()
    _hook(api, headers, "slack", SLACK)
    answers = iter([(False, "http_503"), (True, "http_200")])
    tries = []

    def flaky(url, body):
        tries.append(url)
        return next(answers)

    monkeypatch.setattr(webhooks, "_post", flaky)
    fanout = _breach(api, headers, sensor_factory)["webhook_fanout"]
    assert len(tries) == 2
    assert fanout[0]["delivered"] is True


@pytest.mark.parametrize("status", ["http_400", "http_404", "http_403",
                                    "refused_private_address", "refused_insecure_url"])
def test_a_wrong_url_or_a_refusal_is_not_tried_again(api, operator_factory,
                                                     sensor_factory, monkeypatch,
                                                     quick, status):
    headers, _, _ = operator_factory()
    _hook(api, headers, "slack", SLACK)
    tries = []
    monkeypatch.setattr(webhooks, "_post",
                        lambda url, body: (tries.append(url), (False, status))[1])
    fanout = _breach(api, headers, sensor_factory)["webhook_fanout"]
    assert len(tries) == 1
    assert fanout[0]["status"] == status


def test_down_twice_is_recorded_as_a_failure_after_a_retry(api, operator_factory,
                                                           sensor_factory, monkeypatch,
                                                           quick):
    headers, _, _ = operator_factory()
    made = _hook(api, headers, "generic", GENERIC)
    monkeypatch.setattr(webhooks, "_post", lambda url, body: (False, "unreachable"))
    fanout = _breach(api, headers, sensor_factory)["webhook_fanout"]
    assert fanout[0]["delivered"] is False
    assert fanout[0]["status"] == "unreachable_after_retry"
    listed = api.get("/api/webhooks", headers=headers).json()["webhooks"][0]
    assert listed["webhook_id"] == made["webhook_id"]
    assert listed["consecutive_failures"] == 1


def test_no_retry_that_would_run_past_the_budget(api, operator_factory, sensor_factory,
                                                 monkeypatch):
    headers, _, _ = operator_factory()
    _hook(api, headers, "slack", SLACK)
    monkeypatch.setattr(webhooks, "WEBHOOK_FANOUT_BUDGET_SECONDS", 1.0)
    tries = []
    monkeypatch.setattr(webhooks, "_post",
                        lambda url, body: (tries.append(url), (False, "http_502"))[1])
    started = time.monotonic()
    _breach(api, headers, sensor_factory)
    assert len(tries) == 1
    assert time.monotonic() - started < 1.0


# ---- time out --------------------------------------------------------------


class _Hanging(http.server.BaseHTTPRequestHandler):
    """Accepts the connection and then says nothing."""

    release = threading.Event()

    def do_POST(self):
        _Hanging.release.wait(10)

    def log_message(self, *a):
        pass


def test_a_receiver_that_hangs_is_cut_off_by_the_socket_timeout(monkeypatch):
    _Hanging.release.clear()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Hanging)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(webhooks, "WEBHOOK_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(webhooks, "ALLOWED_SCHEMES", ("http",))
    monkeypatch.setattr(webhooks, "_resolves_privately", lambda host: False)
    try:
        started = time.monotonic()
        delivered, status = webhooks._post(
            f"http://127.0.0.1:{server.server_address[1]}/hook", {"text": "x"})
        assert time.monotonic() - started < 3
        assert (delivered, status) == (False, "unreachable")
    finally:
        _Hanging.release.set()
        server.shutdown()
        server.server_close()


def test_the_breach_handler_waits_no_longer_than_the_fanout_budget(
        api, operator_factory, sensor_factory, monkeypatch):
    headers, _, _ = operator_factory()
    for n in range(3):
        _hook(api, headers, "generic", f"{GENERIC}{n}")
    monkeypatch.setattr(webhooks, "WEBHOOK_FANOUT_BUDGET_SECONDS", 0.5)
    gate = threading.Event()
    monkeypatch.setattr(webhooks, "_post",
                        lambda url, body: (gate.wait(5), (True, "http_200"))[1])
    started = time.monotonic()
    fanout = _breach(api, headers, sensor_factory)["webhook_fanout"]
    gate.set()
    assert time.monotonic() - started < 2.5
    assert {f["status"] for f in fanout} == {"abandoned_slow"}


# ---- never block the sweep --------------------------------------------------


def test_the_escalation_sweep_does_not_wait_for_a_slow_channel(
        api, operator_factory, sensor_factory, monkeypatch):
    headers, tenant, _ = operator_factory(plan="enterprise")
    _hook(api, headers, "pagerduty", PAGERDUTY_KEY)
    posted = []
    gate = threading.Event()

    def slow(url, body):
        if body["payload"]["custom_details"]["state"] == "escalated":
            gate.wait(10)
        posted.append(body)
        return True, "http_202"

    monkeypatch.setattr(webhooks, "_post", slow)
    incident_id = _breach(api, headers, sensor_factory)["incident_id"]
    incident = STORE.get_incident(incident_id)
    incident.opened_at = utc_now() - timedelta(minutes=VOICE_ESCALATION_GRACE_MINUTES + 1)

    started = time.monotonic()
    result = automation.sweep_tenant(STORE.get_tenant(tenant["tenant_id"]))
    took = time.monotonic() - started
    assert result["voice_calls_placed"] == 1
    assert took < 1.0, f"the sweep waited {took:.1f}s on a chat channel"

    # The copy still goes out, from a worker.
    gate.set()
    assert webhooks.wait_for_background(10)
    assert [b["payload"]["custom_details"]["state"] for b in posted] == [
        "opened", "escalated"]


# ---- masked everywhere -------------------------------------------------------


SECRETS = ("sEcReTtOkEnVaLuE42", "tEaMsSeCrEt99", "gEnErIcSeCrEt77",
           "R0UT1NGKEYpagerduty0001", "/services/T0001/B0002",
           "/webhookb2/aaaa", "/hook/gEnErIc")


def _assert_masked(text, where):
    for secret in SECRETS:
        # The last six characters are the deliberate "which one is this"
        # tail; nothing longer than that may appear.
        assert secret not in text, f"{secret!r} appeared in {where}"


def test_targets_stay_masked_in_responses_audit_logs_and_export(
        api, operator_factory, sensor_factory, monkeypatch, caplog, quick):
    headers, _, _ = operator_factory()
    made = [_hook(api, headers, "slack", SLACK),
            _hook(api, headers, "teams", TEAMS),
            _hook(api, headers, "generic", GENERIC),
            _hook(api, headers, "pagerduty", PAGERDUTY_KEY)]
    for hook in made:
        _assert_masked(str(hook), "the create response")

    # A real post attempt that fails in every way a log line might quote
    # the URL: refused as private, and an exception that carries it.
    def resolves_privately(host):
        return host == "alerts.example.com"

    monkeypatch.setattr(webhooks, "_resolves_privately", resolves_privately)

    class Boom(webhooks.urllib.request.OpenerDirector):
        def open(self, request, timeout=None):
            raise ValueError(f"cannot post to {request.full_url}")

    monkeypatch.setattr(webhooks, "_OPENER", Boom())
    with caplog.at_level(logging.DEBUG):
        _breach(api, headers, sensor_factory)
        for hook in made:
            api.post(f"/api/webhooks/{hook['webhook_id']}/test", headers=headers)
            api.patch(f"/api/webhooks/{hook['webhook_id']}", headers=headers,
                      json={"label": "renamed"})
        api.delete(f"/api/webhooks/{made[0]['webhook_id']}", headers=headers)
        listed = api.get("/api/webhooks", headers=headers)
        audit = api.get("/api/accounts/audit", headers=headers)
        export = api.get("/api/accounts/business/export", headers=headers)

    _assert_masked(listed.text, "the webhook list")
    _assert_masked(audit.text, "the audit trail")
    assert "webhook.added" in audit.text
    _assert_masked(caplog.text, "the logs")
    assert "hooks.slack.com" in caplog.text or "alerts.example.com" in caplog.text
    assert export.status_code == 200, export.text
    assert "webhook" in export.text
    _assert_masked(export.text, "the data export")
    # And the stored row itself is what still holds it, so posting works.
    assert STORE.get_webhook(made[1]["webhook_id"]).target == TEAMS
