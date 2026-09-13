"""Who watches the thing that watches the freezers.

The product's whole promise is that something runs when nobody is
looking, and nothing checked that it was. A process that died at 2am
took the sweep with it: no escalation, no invoices, and every customer's
console still reading ONLINE, because the console reads the database and
the database was fine.

The tests that matter here are about the failure modes an ordinary
health check misses:

  * the web process serving requests perfectly while the sweep task
    inside it is dead — alive to `/api/health`, useless to every freezer;
  * a heartbeat written by whatever last touched the app, rather than by
    the sweep, which would paper over exactly that;
  * an alarm so twitchy that somebody mutes it, which is the same as
    having none.
"""

from datetime import timedelta

import pytest

import scheduler
import watchdog
from store import STORE, iso, utc_now


@pytest.fixture(autouse=True)
def sweeping(monkeypatch):
    """A deployment whose in-process loop is meant to be running."""
    monkeypatch.setattr(scheduler, "_interval", lambda: 60)
    return 60


def _swept_ago(seconds):
    STORE._db.put("meta", "heartbeat", {
        "last_sweep_at": iso(utc_now() - timedelta(seconds=seconds)),
        "sweeps": 1,
    })


# ---- the state it reports ----------------------------------------------


def test_a_process_that_has_never_swept_is_not_healthy(api):
    """A fresh process is not a working one until it has done the job."""
    body = api.get("/api/watchdog")

    assert body.status_code == 503
    assert body.json()["healthy"] is False
    assert "has not run at all" in body.json()["reasons"][0]


def test_a_recent_sweep_is_healthy(api):
    _swept_ago(30)

    body = api.get("/api/watchdog")

    assert body.status_code == 200
    assert body.json()["healthy"] is True
    assert body.json()["reasons"] == []


def test_a_stale_sweep_answers_503(api):
    """The status code is the whole interface.

    The free tier of every uptime service, and a one-line cron running
    `curl -f`, understand a status code and nothing else. A liveness
    endpoint that needs the checker to parse JSON will not be checked.
    """
    _swept_ago(watchdog.stale_after_seconds() + 60)

    body = api.get("/api/watchdog")

    assert body.status_code == 503
    assert "escalation has stopped" in body.json()["reasons"][0]


def test_one_slow_pass_does_not_cry_wolf(api):
    """An alarm that fires on a slow SQLite checkpoint gets muted, and a
    muted alarm is the same as no alarm."""
    _swept_ago(90)  # a minute and a half on a sixty-second cadence

    assert api.get("/api/watchdog").status_code == 200


def test_the_threshold_scales_with_the_configured_interval(api, monkeypatch):
    """A deployment sweeping every five minutes is not dead at six."""
    monkeypatch.setattr(scheduler, "_interval", lambda: 300)
    _swept_ago(600)

    assert api.get("/api/watchdog").status_code == 200

    _swept_ago(1200)
    assert api.get("/api/watchdog").status_code == 503


def test_an_external_scheduler_is_not_called_dead(api, monkeypatch):
    """With the loop off, something outside drives the sweep and may
    simply not have fired yet. Alarming on that is alarming on a
    configuration, not on a fault."""
    monkeypatch.setattr(scheduler, "_interval", lambda: 0)

    assert api.get("/api/watchdog").status_code == 200


def test_a_late_billing_pass_is_reported_separately(api):
    """Nothing about being an hour late on an invoice is an emergency.
    A day late is."""
    _swept_ago(10)
    state = STORE._db.get("meta", "heartbeat")
    state["last_money_pass_at"] = iso(
        utc_now() - timedelta(seconds=watchdog.MONEY_STALE_AFTER_SECONDS + 60)
    )
    STORE._db.put("meta", "heartbeat", state)

    body = api.get("/api/watchdog")

    assert body.status_code == 503
    assert any("nothing is being invoiced" in r for r in body.json()["reasons"])


# ---- what it must not leak ---------------------------------------------


def test_the_endpoint_carries_no_business_information(api, tenant_factory):
    """It is read by an uptime service that holds no credential of ours,
    so what it returns has to be safe in a stranger's logs.

    How long ago something ran is not a secret. How many customers there
    are is.
    """
    tenant_factory(plan="growth", company_name="Northgate Foods")
    _swept_ago(10)

    text = api.get("/api/watchdog").text

    assert "Northgate" not in text
    for leak in ("tenant", "revenue", "invoice", "customer", "mrr"):
        assert leak not in text.lower(), f"the liveness endpoint mentions {leak}"


def test_it_needs_no_credential(api):
    """Handing a third party the platform admin key so it could check we
    were alive would be a far worse trade than publishing timings."""
    assert api.get("/api/watchdog").status_code in (200, 503)


# ---- the heartbeat is written by the sweep -----------------------------


def test_the_sweep_writes_the_heartbeat(api):
    scheduler.run_one_pass()

    assert api.get("/api/watchdog").status_code == 200


def test_a_request_does_not_write_the_heartbeat(api):
    """The sneakiest failure: HTTP fine, sweep task dead.

    If merely being asked counted as being alive, the one state the
    watchdog exists to catch would be invisible — and it is the state
    where everything looks perfect and no freezer is being watched.
    """
    _swept_ago(watchdog.stale_after_seconds() + 60)

    for _ in range(5):
        assert api.get("/api/health").status_code == 200

    assert api.get("/api/watchdog").status_code == 503


def test_the_money_pass_records_itself(api):
    scheduler.run_money_pass()

    stored = STORE._db.get("meta", "heartbeat")
    assert stored["last_money_pass_at"]


# ---- the outbound ping, which is the half that actually saves you ------


def test_a_sweep_pings_the_dead_mans_switch(api, monkeypatch):
    called = []
    monkeypatch.setattr(watchdog, "HEARTBEAT_URL", "https://switch.example/abc")
    monkeypatch.setattr(
        watchdog.urllib.request, "urlopen",
        lambda request, timeout=None: called.append(request.full_url)
        or _FakeResponse(),
    )

    scheduler.run_one_pass()

    assert called == ["https://switch.example/abc"]


class _FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_failing_ping_cannot_break_the_sweep(api, monkeypatch, tenant_factory):
    """The freezers matter more than the telemetry about the freezers."""
    tenant_factory(plan="growth")
    monkeypatch.setattr(watchdog, "HEARTBEAT_URL", "https://switch.example/abc")

    def _explode(request, timeout=None):
        raise OSError("the switch is unreachable")

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", _explode)

    result = scheduler.run_one_pass()  # must not raise

    assert result["tenants_swept"] == 1
    assert watchdog.ping()["status"] == "failed"


def test_a_url_that_is_not_http_is_refused_rather_than_fetched(monkeypatch):
    monkeypatch.setattr(watchdog, "HEARTBEAT_URL", "file:///etc/passwd")

    assert watchdog.ping() == {"pinged": False, "status": "bad_url"}


def test_no_url_is_reported_rather_than_silently_skipped(monkeypatch):
    monkeypatch.setattr(watchdog, "HEARTBEAT_URL", "")

    assert watchdog.ping()["status"] == "not_configured"


# ---- the digest says so ------------------------------------------------


def test_the_digest_warns_that_nothing_is_watching_the_watcher(monkeypatch):
    """The joke that is not one: this warning is written by the process
    it is warning about."""
    monkeypatch.setattr(watchdog, "HEARTBEAT_URL", "")
    _swept_ago(10)

    from digest import operator_digest

    warnings = operator_digest()["warnings"]
    assert any("nothing will tell you" in w for w in warnings)


def test_the_digest_reports_a_stopped_sweep(monkeypatch):
    monkeypatch.setattr(watchdog, "HEARTBEAT_URL", "https://switch.example/abc")
    _swept_ago(watchdog.stale_after_seconds() + 60)

    from digest import operator_digest

    warnings = operator_digest()["warnings"]
    assert any("escalation has stopped" in w for w in warnings)
