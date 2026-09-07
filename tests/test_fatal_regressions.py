"""The bugs that would have killed the business, each pinned by a test.

Every one of these was live. They are collected here rather than scattered
because they share a shape: the system stayed up, answered 200, and was
wrong — which is the only failure mode that matters in a product whose
whole job is to notice when something is wrong.
"""

import json

import pytest


# ---------------------------------------------------------------------------
#  A reading no sensor could have produced
# ---------------------------------------------------------------------------


def test_a_nan_reading_is_never_scored_nominal():
    """NaN compares false against every threshold.

    A sensor emitting one was judged healthy on every reading, forever,
    while the asset behind it failed. This is the single worst bug the
    product can have: silence that looks like safety.
    """
    from store import evaluate_breach

    for bad in (float("nan"), float("inf"), float("-inf")):
        verdict = evaluate_breach("restaurant", bad)
        assert verdict is not None, f"{bad} was scored nominal"
        assert "fault" in verdict.lower()


def test_non_finite_readings_are_refused_at_every_door(
    api, operator_factory, sensor_factory
):
    """Both ingestion routes, and the store underneath them."""
    from store import STORE, ImplausibleReading, require_plausible

    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")

    # The wire form a real device sends: a bare NaN token, which Python's
    # JSON parser accepts and most others do too.
    for raw in (b'{"sensor_id":"FRZ-1","temperature_fahrenheit":NaN}',
                b'{"sensor_id":"FRZ-1","temperature_fahrenheit":Infinity}',
                b'{"sensor_id":"FRZ-1","temperature_fahrenheit":-Infinity}',
                b'{"sensor_id":"FRZ-1","temperature_fahrenheit":1e400}'):
        resp = api.post("/api/sensor-pulse", headers=headers, content=raw)
        assert resp.status_code == 422, raw
        # A clean rejection, not a stack trace: FastAPI's default handler
        # crashes serialising an error that echoes a non-finite value.
        assert resp.json()["detail"]

    assert STORE.readings_for("FRZ-1") == []

    # And the store refuses one even if a caller reaches past the API.
    sensor = STORE.get_sensor("FRZ-1")
    with pytest.raises(ImplausibleReading):
        STORE.record_reading(sensor=sensor, temperature_fahrenheit=float("nan"),
                             humidity_percent=None, breached=False)
    assert require_plausible(30.0) == 30.0


def test_a_physically_impossible_reading_is_refused(
    api, operator_factory, sensor_factory
):
    """-500 °F is below absolute zero: a broken sensor, not a cold asset."""
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "FRZ-1", "temperature_fahrenheit": -500.0})
    assert resp.status_code == 422
    assert "physically possible" in json.dumps(resp.json())


def test_the_database_refuses_to_persist_invalid_json():
    """Python emits bare NaN tokens, which nothing else can read back."""
    from db import Database

    db = Database(":memory:")
    with pytest.raises(ValueError, match="non-JSON-compliant"):
        db.put("reading", "R1", {"temperature_fahrenheit": float("nan")})


# ---------------------------------------------------------------------------
#  Identifier reuse across a restart
# ---------------------------------------------------------------------------


def test_a_restart_never_reissues_an_identifier(tmp_path):
    """One tenant's telemetry silently overwrote another's.

    Ids are the primary key and the counter is global, so a counter that
    came back low made the next write destroy an existing row — belonging
    to whichever tenant happened to hold that id. Readings and audit
    entries were missing from the high-water scan, and they are by far the
    most numerous.
    """
    from db import Database
    from store import HubStore

    path = str(tmp_path / "ids.db")
    first = HubStore(db=Database(path))
    alpha = first.create_tenant("Alpha", "n", "+1", "a@x.com", "growth")
    sensor = first.register_sensor("A-1", alpha.tenant_id, "restaurant", "W")
    for temp in (70.0, 71.0, 72.0):
        first.record_reading(sensor=sensor, temperature_fahrenheit=temp,
                             humidity_percent=50.0, breached=False)
    first.record_audit(alpha.tenant_id, "someone", "owner", "test", "an entry")
    alpha_ids = {r.reading_id for r in first.readings_for("A-1")}
    high_water = first._counter

    second = HubStore(db=Database(path))
    assert second._counter >= high_water, "the counter went backwards"

    beta = second.create_tenant("Beta", "n", "+1", "b@x.com", "growth")
    other = second.register_sensor("B-1", beta.tenant_id, "restaurant", "W")
    second.record_reading(sensor=other, temperature_fahrenheit=12.0,
                          humidity_percent=50.0, breached=False)
    new_id = second.readings_for("B-1")[0].reading_id
    assert new_id not in alpha_ids, f"{new_id} was reissued"

    third = HubStore(db=Database(path))
    assert len(third.readings_for("A-1")) == 3, "Alpha lost a reading"
    assert [r.temperature_fahrenheit for r in third.readings_for("A-1")] == \
        [70.0, 71.0, 72.0]


def test_deleting_the_newest_row_does_not_free_its_identifier(tmp_path):
    """A scan only sees ids that still exist; the counter is persisted."""
    from db import Database
    from store import HubStore

    path = str(tmp_path / "ids2.db")
    first = HubStore(db=Database(path))
    tenant = first.create_tenant("A", "n", "+1", "a@x.com", "growth")
    site = first.create_site(tenant.tenant_id, "Boca")
    taken = site.site_id
    first.remove_site(site.site_id)

    second = HubStore(db=Database(path))
    again = second.create_site(tenant.tenant_id, "Boynton")
    assert again.site_id != taken


# ---------------------------------------------------------------------------
#  A tamper check that could never fail
# ---------------------------------------------------------------------------


def test_the_vault_catches_an_edited_reading(
    api, operator_factory, sensor_factory
):
    """Checking a chain against itself agrees no matter what was edited.

    An operator could rewrite a bad night and the endpoint sold as the
    tamper check would still answer "intact" — the worst possible failure
    for the feature the premium tier is built on.
    """
    from store import STORE

    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    for temp in (28.0, 29.0, 30.0):
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": "FRZ-1", "temperature_fahrenheit": temp})

    # With nothing attested there is nothing to compare against, and it
    # must say so rather than implying a pass.
    unanchored = api.get("/api/vault/verify/FRZ-1", headers=headers).json()
    assert unanchored["verifiable"] is False

    api.get("/api/vault/attestation/FRZ-1", headers=headers)
    honest = api.get("/api/vault/verify/FRZ-1", headers=headers).json()
    assert honest["verifiable"] is True and honest["intact"] is True

    STORE.readings_for("FRZ-1")[1].temperature_fahrenheit = 28.5
    tampered = api.get("/api/vault/verify/FRZ-1", headers=headers).json()
    assert tampered["intact"] is False
    assert tampered["attested_head"] != tampered["rederived_head"]
    assert "altered" in tampered["note"]


def test_an_adjuster_can_follow_the_packets_own_instructions(
    api, operator_factory, sensor_factory
):
    """The packet said to verify it, and the verification could not work.

    The reading rows carried different keys than the verifier requires,
    and the values were converted to the customer's display unit while the
    chain is computed in Fahrenheit. An adjuster following the packet got
    a 400 on a genuine claim.
    """
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    for temp in (28.0, 30.0, 48.0):
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": "FRZ-1", "temperature_fahrenheit": temp})
    api.post("/api/licenses/me/temperature-unit", headers=headers,
             json={"temperature_unit": "C"})

    incident = api.get("/api/voice/incidents", headers=headers).json()["incidents"][0]
    packet = api.post(f"/api/claims/{incident['incident_id']}/packet",
                      headers=headers).json()

    out = api.post("/api/vault/verify", json={
        "readings": packet["evidence"]["verifiable_readings"],
        "chain_head": packet["attestation"]["chain_head"],
    }).json()
    assert out["matches"] is True, out["verdict"]


def test_the_public_verifier_never_returns_a_stack_trace(api):
    """It is unauthenticated and it is the first thing an insurer touches."""
    rows = [{"sensor_id": "S", "at": "2026-01-01T00:00:00Z",
             "temperature_fahrenheit": 30.0, "humidity_percent": None,
             "breached": False}]
    for junk in (123, ["a"], {"x": 1}, "ü" * 64, "not-hex", "abc"):
        resp = api.post("/api/vault/verify",
                        json={"readings": rows, "chain_head": junk})
        assert resp.status_code == 400, f"{junk!r} produced {resp.status_code}"
    assert api.post("/api/vault/verify", json=[]).status_code in (400, 422)


# ---------------------------------------------------------------------------
#  A guarantee that contradicted the dispatch path
# ---------------------------------------------------------------------------


def test_a_site_scoped_rota_keeps_cover_in_force(
    api, operator_factory, sensor_factory
):
    """The recommended configuration reported itself as uncovered.

    Eligibility asked for the tenant-wide roster, which drops every
    site-scoped contact, so a chain that had correctly built a per-site
    rota was told each morning that its guarantee was void — while its
    alerts were in fact reaching exactly the right people.
    """
    from store import STORE

    headers, tenant, _ = operator_factory(plan="enterprise")
    site = api.post("/api/sites", headers=headers,
                    json={"name": "Boca"}).json()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post(f"/api/sites/{site['site_id']}/sensors", headers=headers,
             json={"sensor_id": "FRZ-1"})
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 28.0,
                   "battery_percent": 90.0})
    api.post("/api/contacts", headers=headers,
             json={"full_name": "Night Manager", "phone": "+15550111",
                   "site_id": site["site_id"]})

    cover = api.get("/api/assurance/cover", headers=headers).json()
    assert cover["in_force"] is True
    assert cover["covered_units"] == 1

    # And it agrees with who would actually be texted.
    reached = STORE.sms_recipients(
        STORE.get_tenant(tenant["tenant_id"]), site["site_id"])
    assert [c.full_name for c in reached] == ["Night Manager"]


# ---------------------------------------------------------------------------
#  Delivery that was documented as fail-open and was not
# ---------------------------------------------------------------------------


def test_a_malformed_webhook_url_cannot_break_the_breach_path(
    api, operator_factory, sensor_factory, monkeypatch
):
    """One fat-fingered Slack URL turned every ingest into a 500.

    urlsplit throws on a malformed IPv6 literal, and the throw was outside
    the guard — after the incident row and the SMS were already written,
    so the gateway retried into a handler that would fail again.
    """
    import webhooks

    headers, _, _ = operator_factory()
    monkeypatch.setattr(webhooks, "_validate_target", lambda kind, target: target)
    api.post("/api/webhooks", headers=headers,
             json={"kind": "generic", "target": "https://[::1"})

    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 55.0})
    assert resp.status_code == 200
    assert resp.json()["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert resp.json()["webhook_fanout"][0]["delivered"] is False


def test_a_redirect_cannot_walk_the_webhook_into_our_own_network(monkeypatch):
    """The address check only ever saw the first hop."""
    import urllib.error

    import webhooks

    assert isinstance(webhooks._OPENER.handlers[0], object)
    handler = next(h for h in webhooks._OPENER.handlers
                   if isinstance(h, webhooks._NoRedirects))
    with pytest.raises(urllib.error.URLError, match="refused to follow"):
        handler.redirect_request(None, None, 302, "Found", {},
                                 "http://169.254.169.254/latest/meta-data/")


def test_an_explicit_null_cannot_silently_disable_a_hook(
    api, operator_factory
):
    """A UI that serialises its whole form disabled every hook it touched."""
    headers, _, _ = operator_factory()
    hook = api.post("/api/webhooks", headers=headers,
                    json={"kind": "slack",
                          "target": "https://hooks.slack.com/a/b/cccccc"}).json()

    resp = api.patch(f"/api/webhooks/{hook['webhook_id']}", headers=headers,
                     json={"active": None})
    assert resp.status_code == 400
    listed = api.get("/api/webhooks", headers=headers).json()
    assert listed["webhooks"][0]["active"] is True


# ---------------------------------------------------------------------------
#  Two calls for one incident
# ---------------------------------------------------------------------------


def test_only_one_sweep_can_escalate_an_incident(
    api, operator_factory, sensor_factory, age_incident
):
    """The timer and the console button both saw an unescalated incident.

    A check-then-act with no lock across it: both passed, both dialled,
    and one person got two 3am phone calls about the same freezer.
    """
    from store import STORE

    headers, tenant, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    body = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "FRZ-1",
                          "temperature_fahrenheit": 55.0}).json()
    incident = STORE.get_incident(body["incident_id"])

    assert STORE.claim_voice_escalation(incident) is True
    assert STORE.claim_voice_escalation(incident) is False, (
        "a second caller claimed the same escalation"
    )


def test_one_bad_tenant_cannot_stop_the_sweep(monkeypatch, api,
                                              operator_factory):
    """The guard was one line short of what its docstring promised."""
    import scheduler

    operator_factory(company_name="A", email="a@x.com")
    operator_factory(company_name="B", email="b@x.com")

    seen = []

    def _broken(tenant, auto_escalate=True):
        seen.append(tenant.tenant_id)
        if len(seen) == 1:
            return {"nothing": "useful"}      # missing the expected key
        return {"voice_calls_placed": 0}

    monkeypatch.setattr(scheduler, "sweep_tenant", _broken)
    summary = scheduler.run_one_pass()

    assert len(seen) == 2, "the second tenant was skipped"
    assert len(summary["failed_tenants"]) == 1


# ---------------------------------------------------------------------------
#  Deleting a site quietly removed people from the roster
# ---------------------------------------------------------------------------


def test_deleting_a_site_does_not_silence_its_on_call_contact():
    """Found by the fuzzer, and it is the fatal shape again.

    Site-scoped contacts match either their own live site or, if they have
    no site, the estate-wide pool. A contact left pointing at a deleted
    site matches neither — so they are dropped from every alert while
    still showing as "on call" in the console. The operator sees a rota
    that is not the rota.
    """
    from db import Database
    from store import HubStore

    store = HubStore(db=Database(":memory:"))
    tenant = store.create_tenant("Chain", "HQ", "+15550100", "hq@x.com",
                                 "enterprise")
    site = store.create_site(tenant.tenant_id, "Boca")
    manager = store.add_contact(tenant.tenant_id, "Night Manager", "+15550111",
                                site_id=site.site_id)
    sensor = store.register_sensor("FRZ-1", tenant.tenant_id, "restaurant", "W")
    store.assign_sensor_to_site(sensor, site.site_id)

    reached = store.sms_recipients(tenant, site.site_id)
    assert [c.full_name for c in reached] == ["Night Manager"]

    store.remove_site(site.site_id)

    # The sensor is released, so it now asks for the estate-wide roster.
    assert store.get_sensor("FRZ-1").site_id is None
    still = store.sms_recipients(tenant, None)
    assert "Night Manager" in [c.full_name for c in still], (
        "the manager was dropped from every alert but still appears on the "
        "roster"
    )
    assert store.get_contact(manager.contact_id).site_id is None


def test_deleting_a_site_releases_its_alert_channel_too():
    """Same shape: a hook scoped to a dead site stops firing."""
    from db import Database
    from store import HubStore

    store = HubStore(db=Database(":memory:"))
    tenant = store.create_tenant("Chain", "HQ", "+15550100", "hq@x.com",
                                 "enterprise")
    site = store.create_site(tenant.tenant_id, "Boca")
    hook = store.add_webhook(tenant.tenant_id, "slack",
                             "https://hooks.slack.com/a/b/c",
                             site_id=site.site_id)

    store.remove_site(site.site_id)

    assert store.get_webhook(hook.webhook_id).site_id is None
    assert hook in store.webhooks_for_site(tenant.tenant_id, None)


# ---------------------------------------------------------------------------
#  A clock that runs backwards
# ---------------------------------------------------------------------------


def test_a_backwards_clock_never_suppresses_escalation():
    """`minutes_open` went negative, so nothing was ever due for a call.

    An incident opened at 03:00, then NTP stepped the host back thirty
    minutes. Every comparison against the escalation grace window is
    `minutes_open() >= GRACE`, and -30 is not >= 20, so the breach sat
    open and silent: the SMS had gone out, nobody had answered it, and the
    phone call that exists precisely for that case was never placed.
    Nothing errored. The incident list showed it open the whole time.
    """
    from datetime import timedelta

    from db import Database
    from store import HubStore

    store = HubStore(db=Database(":memory:"))
    tenant = store.create_tenant("Cold", "Dana", "+15550100", "d@x.com",
                                 "enterprise")
    sensor = store.register_sensor("RACK-01", tenant.tenant_id, "pharmacy",
                                   "Hall B")
    incident = store.open_incident(
        tenant_id=tenant.tenant_id, sensor=sensor,
        temperature_fahrenheit=71.0, breach_details="too warm",
        sms_text="warm", sms_dispatch_source="template",
    )

    stepped_back = incident.opened_at - timedelta(minutes=30)
    assert incident.minutes_open(stepped_back) == 0.0, (
        "a backwards clock made the incident look like it had not started, "
        "so it was never due for escalation"
    )
    assert incident.minutes_open(stepped_back) >= 0.0


def test_a_claim_packet_never_reports_negative_response_time():
    """Same clock, but this number goes to a loss adjuster.

    `minutes_to_acknowledge: -120.0` in a document arguing the team
    responded promptly is worse than no document: it is a reason to
    question everything else in the packet.
    """
    from datetime import timedelta

    from db import Database
    from store import HubStore

    store = HubStore(db=Database(":memory:"))
    tenant = store.create_tenant("Cold", "Dana", "+15550100", "d@x.com",
                                 "enterprise")
    sensor = store.register_sensor("RACK-01", tenant.tenant_id, "pharmacy",
                                   "Hall B")
    incident = store.open_incident(
        tenant_id=tenant.tenant_id, sensor=sensor,
        temperature_fahrenheit=71.0, breach_details="too warm",
        sms_text="warm", sms_dispatch_source="template",
    )
    # Acknowledged "before" it opened, which is what a clock step looks
    # like after the fact.
    incident.acknowledged_at = incident.opened_at - timedelta(hours=2)

    assert incident.minutes_open() == 0.0


def test_a_sensor_reporting_from_the_future_is_not_trusted_as_online():
    """`offline()` compares last_seen against now, so a future stamp

    made a sensor that had stopped reporting look permanently healthy —
    the silent-failure case the whole product exists to catch. A sensor
    whose clock is ahead is flagged rather than believed.
    """
    from datetime import timedelta

    from db import Database
    from store import HubStore, utc_now

    store = HubStore(db=Database(":memory:"))
    tenant = store.create_tenant("Cold", "Dana", "+15550100", "d@x.com",
                                 "enterprise")
    sensor = store.register_sensor("RACK-01", tenant.tenant_id, "pharmacy",
                                   "Hall B")

    sensor.last_seen = utc_now() + timedelta(hours=6)
    assert sensor.clock_skewed(), (
        "a sensor six hours ahead was accepted as simply very recent"
    )

    sensor.last_seen = utc_now() - timedelta(seconds=30)
    assert not sensor.clock_skewed()


# ---------------------------------------------------------------------------
#  One customer's slow Slack, everybody's outage
# ---------------------------------------------------------------------------


def test_the_ingest_route_is_not_a_coroutine():
    """Declared `async def`, it held the event loop through every alert.

    Everything under this route blocks: SQLite, the Twilio REST client,
    the webhook fan-out. Starlette runs a sync endpoint on a worker
    thread and an async one on the single event loop thread, so as a
    coroutine this route stopped the entire process for the duration of
    an alert — every other tenant's ingest, the console, and /api/health,
    which is what a load balancer polls before killing the container.

    Measured: three hooks pointing at a receiver that accepted and then
    said nothing produced 15.1 seconds of total platform blackout, and an
    unrelated tenant's pulse went from 1 ms to 14.6 seconds.

    This is a one-keyword regression. Nothing in the route awaits, so
    nothing fails if somebody adds `async` back — it just goes quiet
    again under load.
    """
    import inspect

    import telemetry
    import voice_dispatch

    assert not inspect.iscoroutinefunction(telemetry.process_sensor_pulse), (
        "the hottest path in the app is back on the event loop"
    )

    # The Twilio keypress callback genuinely has to be a coroutine (it
    # awaits the request body), so it is held to the other half of the
    # rule instead: its blocking tail goes to a thread.
    assert inspect.iscoroutinefunction(voice_dispatch.voice_keypress)
    body = inspect.getsource(voice_dispatch.voice_keypress)
    assert "asyncio.to_thread" in body and "_notify_hooks" in body, (
        "the acknowledgement callback dispatches webhooks inline again; a "
        "slow hook now blocks the event loop and Twilio's 15s callback "
        "timeout, so pressing 1 stops acknowledging the incident"
    )


def test_slow_webhooks_cost_one_timeout_between_them_not_one_each(monkeypatch):
    """Serially, five slow hooks cost five timeouts. Concurrently, one."""
    import time

    import webhooks

    monkeypatch.setattr(webhooks, "WEBHOOK_TIMEOUT_SECONDS", 1)

    def _slow(url, body):
        time.sleep(0.6)
        return False, "unreachable"

    monkeypatch.setattr(webhooks, "_post", _slow)

    from db import Database
    from store import HubStore

    store = HubStore(db=Database(":memory:"))
    monkeypatch.setattr(webhooks, "STORE", store)
    tenant = store.create_tenant("Cold", "Dana", "+15550100", "d@x.com",
                                 "enterprise")
    sensor = store.register_sensor("RACK-01", tenant.tenant_id, "pharmacy",
                                   "Hall B")
    for i in range(5):
        store.add_webhook(tenant.tenant_id, "slack",
                          f"https://hooks.slack.com/a/b/{i}")
    incident = store.open_incident(
        tenant_id=tenant.tenant_id, sensor=sensor,
        temperature_fahrenheit=71.0, breach_details="too warm",
        sms_text="warm", sms_dispatch_source="template",
    )

    started = time.monotonic()
    results = webhooks.dispatch_event(tenant, incident, sensor, "opened")
    elapsed = time.monotonic() - started

    assert len(results) == 5, "a hook was dropped from the fan-out"
    assert elapsed < 5 * 0.6 * 0.75, (
        f"five slow hooks took {elapsed:.1f}s; they are being posted one "
        "after another again"
    )


def test_the_fanout_gives_up_rather_than_waiting_forever(monkeypatch):
    """The per-hook timeout does not cover the name lookup ahead of it.

    `urlopen`'s timeout is a socket timeout; `getaddrinfo` answers to the
    resolver's own clock and blows straight through it. So the fan-out
    carries a wall-clock budget of its own, and a hook that exceeds it is
    abandoned and marked rather than waited on.
    """
    import time

    import webhooks

    monkeypatch.setattr(webhooks, "WEBHOOK_FANOUT_BUDGET_SECONDS", 1)

    def _never_returns(url, body):
        time.sleep(30)
        return True, "http_200"

    monkeypatch.setattr(webhooks, "_post", _never_returns)

    from db import Database
    from store import HubStore

    store = HubStore(db=Database(":memory:"))
    monkeypatch.setattr(webhooks, "STORE", store)
    tenant = store.create_tenant("Cold", "Dana", "+15550100", "d@x.com",
                                 "enterprise")
    sensor = store.register_sensor("RACK-01", tenant.tenant_id, "pharmacy",
                                   "Hall B")
    store.add_webhook(tenant.tenant_id, "slack", "https://hooks.slack.com/a/b/c")
    incident = store.open_incident(
        tenant_id=tenant.tenant_id, sensor=sensor,
        temperature_fahrenheit=71.0, breach_details="too warm",
        sms_text="warm", sms_dispatch_source="template",
    )

    started = time.monotonic()
    results = webhooks.dispatch_event(tenant, incident, sensor, "opened")
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"the fan-out waited {elapsed:.1f}s past its budget"
    assert results[0]["delivered"] is False
    assert results[0]["status"] == "abandoned_slow", (
        "the customer cannot tell which hook is the slow one"
    )


def test_the_twilio_client_cannot_wait_forever():
    """The SDK's default is `timeout=None`, which requests reads as never.

    That is not a slow path, it is a permanently lost worker thread:
    nothing raises, so `never_raises` never fires and no delivery record
    is written. A Twilio incident lasting a few minutes retires one thread
    per alert until Starlette's pool is empty and the platform answers
    nothing at all — during exactly the event it exists for.

    Measured against a socket that accepted and stayed silent: the app's
    client was still blocked after 25 seconds; with a timeout it gave up
    in 8.
    """
    import notifications

    assert notifications.TWILIO_TIMEOUT_SECONDS > 0

    built = {}

    class _FakeHttpClient:
        def __init__(self, timeout=None, **kwargs):
            built["timeout"] = timeout

    class _FakeClient:
        def __init__(self, sid, token, http_client=None, **kwargs):
            built["http_client"] = http_client

    import sys
    import types

    rest = types.ModuleType("twilio.rest")
    rest.Client = _FakeClient
    http_mod = types.ModuleType("twilio.http.http_client")
    http_mod.TwilioHttpClient = _FakeHttpClient

    saved = {k: sys.modules.get(k) for k in ("twilio.rest", "twilio.http.http_client")}
    sys.modules["twilio.rest"] = rest
    sys.modules["twilio.http.http_client"] = http_mod
    saved_client = notifications._client
    saved_error = notifications._client_error
    try:
        notifications._client = None
        notifications._client_error = None
        notifications._get_client()
    finally:
        notifications._client = saved_client
        notifications._client_error = saved_error
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    assert built.get("http_client") is not None, (
        "the Twilio client is built with the SDK default again, which has "
        "no timeout at all"
    )
    assert built["timeout"] == notifications.TWILIO_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
#  Two things at the same moment
# ---------------------------------------------------------------------------
#
# Every bug below is the same mistake: a question asked of the store, an
# answer acted on, and a write — three steps with the lock released twice
# in between. Each one is invisible under a test suite that does one
# thing at a time, and each one shows up the moment a real site has more
# than one sensor reporting.


class _LockWatcher:
    """Wraps the store's lock and counts how often it is fully let go.

    A racing test can only ever hope to observe a race; whether it does
    depends on where the interpreter happens to switch threads, which is
    why the two worst races here were caught against a live server and
    not in-process. So the property is asserted directly instead: a
    check-then-act sequence is safe exactly when the lock is not released
    between the check and the act.

    `full_releases` counts the times the depth returns to zero. One means
    the whole operation was a single atomic section. Two or more means
    the gap is back, whether or not any test thread happened to fall
    into it.
    """

    def __init__(self, real):
        self._real = real
        self.depth = 0
        self.full_releases = 0

    def acquire(self, *args, **kwargs):
        got = self._real.acquire(*args, **kwargs)
        if got:
            self.depth += 1
        return got

    def release(self):
        self._real.release()
        self.depth -= 1
        if self.depth == 0:
            self.full_releases += 1

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def _watch_lock(store):
    watcher = _LockWatcher(store._lock)
    store._lock = watcher
    return watcher


def _pool_map(fn, n, workers=None):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers or n) as pool:
        return list(pool.map(fn, range(n)))


def _store_with_sensor(plan="enterprise"):
    from db import Database
    from store import HubStore

    store = HubStore(db=Database(":memory:"))
    tenant = store.create_tenant("Cold", "Dana", "+15550100", "d@x.com", plan)
    sensor = store.register_sensor("RACE-01", tenant.tenant_id, "pharmacy",
                                   "Hall B")
    return store, tenant, sensor


def test_one_failing_freezer_opens_one_incident_not_eight():
    """The central promise of the product, under concurrency.

    One failure produces one incident: one text, one phone call, one line
    in the log. That was enforced by reading the open incident, finding
    none, and then writing — two store calls with a gap between them wide
    enough to draft an SMS in.

    A freezer that is genuinely failing is exactly what fills that gap:
    the sensor behind it reports every few seconds. Measured against a
    live server, twenty-four simultaneous breach pulses from one sensor
    opened between three and eight incidents, each with its own fan-out
    to the whole roster and its own escalation to the on-call phone. The
    person on call is woken repeatedly, at night, for one freezer — which
    is how a customer learns to ignore the alerts.
    """
    store, tenant, sensor = _store_with_sensor()

    def open_one(i):
        return store.open_incident_once(
            tenant_id=tenant.tenant_id,
            sensor=sensor,
            temperature_fahrenheit=71.0,
            breach_details="too warm",
            sms_text="warm",
            sms_dispatch_source="template",
        )

    results = _pool_map(open_one, 24)
    created = [inc for inc, was_created in results if was_created]
    ids = {inc.incident_id for inc, _ in results}

    # And the property the race depends on, asserted rather than hoped for:
    # the "is one already open?" read and the write are one atomic section.
    # On a fresh store, so this measures the path that actually creates —
    # the early return when an incident already exists never reaches the
    # write and would look atomic no matter how the method was built.
    fresh, fresh_tenant, fresh_sensor = _store_with_sensor()
    watcher = _watch_lock(fresh)
    fresh.open_incident_once(
        tenant_id=fresh_tenant.tenant_id,
        sensor=fresh_sensor,
        temperature_fahrenheit=71.0,
        breach_details="too warm",
        sms_text="warm",
        sms_dispatch_source="template",
    )
    assert watcher.full_releases == 1, (
        f"the lock was released {watcher.full_releases} times inside "
        "open_incident_once; the check and the write are two operations "
        "again and a second incident can be opened in between"
    )

    assert len(created) == 1, (
        f"{len(created)} incidents opened for one failing freezer -- that is "
        f"{len(created)} SMS fan-outs and {len(created)} phone calls"
    )
    assert len(ids) == 1, "callers were handed different incidents for one fault"
    assert len(store.open_incidents(tenant.tenant_id)) == 1


def test_a_five_seat_licence_cannot_be_talked_into_nine():
    """Counting seats walks every sensor, so the window is wide.

    Measured on a live server against a five-seat trial licence: twenty
    concurrent registrations were granted nine seats. Four sensors
    monitored, and billed for, on a plan that does not include them —
    and the customer is the one who finds out, from their invoice.
    """
    from db import Database
    from store import HubStore, SeatClaimRefused

    store = HubStore(db=Database(":memory:"))
    tenant = store.create_tenant("Small", "Dana", "+15550100", "d@x.com",
                                 "trial")
    cap = tenant.entitlements()["max_sensors"]

    def claim(i):
        try:
            store.claim_sensor_seat(
                sensor_id=f"SEAT-{i:03d}",
                tenant_id=tenant.tenant_id,
                industry_vertical="pharmacy",
                location_name="x",
                max_sensors=cap,
            )
            return True
        except SeatClaimRefused:
            return False

    granted = sum(_pool_map(claim, cap * 6))
    assert granted == cap, (
        f"granted {granted} seats on a {cap}-seat licence"
    )
    assert store.seat_count(tenant.tenant_id) == cap

    # Counting seats and taking one must be a single atomic section.
    store2 = HubStore(db=Database(":memory:"))
    t2 = store2.create_tenant("Small", "D", "+15550100", "d@x.com", "trial")
    watcher = _watch_lock(store2)
    store2.claim_sensor_seat(
        sensor_id="SEAT-X", tenant_id=t2.tenant_id,
        industry_vertical="pharmacy", location_name="x", max_sensors=5,
    )
    assert watcher.full_releases == 1, (
        f"the lock was released {watcher.full_releases} times inside "
        "claim_sensor_seat; the seat count can go stale before the seat "
        "is taken"
    )


def test_two_tenants_cannot_both_register_the_same_sensor_id():
    """Never caught in 480 live races -- and closed anyway.

    Sensor ids are global, and the duplicate check was a separate store
    call from the write. The window is narrow enough that eight
    contenders, sixty times, with the interpreter's switch interval
    turned down, always produced exactly one winner. Narrow is not the
    same as closed, and what is on the other side of it is one company's
    freezer disappearing from their console into another company's
    account.
    """
    from db import Database
    from store import HubStore, SeatClaimRefused

    store = HubStore(db=Database(":memory:"))
    tenants = [
        store.create_tenant(f"Co{i}", "D", "+15550100", f"d{i}@x.com",
                            "enterprise")
        for i in range(8)
    ]

    def claim(i):
        try:
            store.claim_sensor_seat(
                sensor_id="SHARED-01",
                tenant_id=tenants[i].tenant_id,
                industry_vertical="pharmacy",
                location_name="x",
                max_sensors=tenants[i].entitlements()["max_sensors"],
            )
            return tenants[i].tenant_id
        except SeatClaimRefused:
            return None

    winners = [w for w in _pool_map(claim, 8) if w]
    assert len(winners) == 1, f"{len(winners)} tenants registered the same id"
    assert store.get_sensor("SHARED-01").tenant_id == winners[0]


def test_a_serial_number_cannot_be_bound_to_two_sensors_at_once():
    """Same call, the other uniqueness check it now covers."""
    from db import Database
    from store import HubStore, SeatClaimRefused

    store = HubStore(db=Database(":memory:"))
    tenant = store.create_tenant("Co", "D", "+15550100", "d@x.com",
                                 "enterprise")

    def claim(i):
        try:
            store.claim_sensor_seat(
                sensor_id=f"NODE-{i:02d}",
                tenant_id=tenant.tenant_id,
                industry_vertical="pharmacy",
                location_name="x",
                max_sensors=1000,
                external_device_sn="SN-DUPLICATE",
            )
            return True
        except SeatClaimRefused:
            return False

    assert sum(_pool_map(claim, 8)) == 1


def test_twelve_operators_pressing_escalate_place_one_call():
    """The sweep claimed atomically. The button a human presses did not.

    Twelve simultaneous escalations placed twelve calls to the same phone
    for the same freezer. A deliberate re-dial is still possible, after a
    cooldown, because nobody picking up is a real thing that happens —
    but a double-click is not a re-dial.
    """
    from datetime import timedelta

    from store import VOICE_REDIAL_COOLDOWN_MINUTES, utc_now

    store, tenant, sensor = _store_with_sensor()
    incident = store.open_incident(
        tenant_id=tenant.tenant_id, sensor=sensor,
        temperature_fahrenheit=71.0, breach_details="too warm",
        sms_text="warm", sms_dispatch_source="template",
    )

    claims = _pool_map(
        lambda i: store.claim_voice_escalation(
            incident, redial_after_minutes=VOICE_REDIAL_COOLDOWN_MINUTES
        ),
        12,
    )
    assert sum(claims) == 1, f"{sum(claims)} of 12 clicks each placed a call"

    # Automation never re-dials on its own.
    assert store.claim_voice_escalation(incident) is False

    # A person can, once the cooldown has passed.
    later = utc_now() + timedelta(minutes=VOICE_REDIAL_COOLDOWN_MINUTES + 1)
    assert store.claim_voice_escalation(
        incident, redial_after_minutes=VOICE_REDIAL_COOLDOWN_MINUTES, now=later
    ) is True


def test_only_one_person_is_told_they_have_the_incident(
    api, operator_factory, sensor_factory, age_incident
):
    """The store kept the first responder. Nothing else did.

    Twelve people reacting to the same alert each got a 200 reading
    "Escalation ladder halted", a Slack message went out naming each of
    them as the person who had it, and the audit trail — the one that
    ends up in front of a loss adjuster — recorded twelve different
    first responders for one incident.
    """
    headers, _, _ = operator_factory()
    sensor = sensor_factory(headers, sensor_id="ACK-01", vertical="pharmacy")
    opened = api.post(
        "/api/sensor-pulse",
        headers=headers,
        json={"sensor_id": sensor["sensor_id"], "temperature_fahrenheit": 71.0},
    )
    incident_id = opened.json()["incident_id"]

    bodies = [
        api.post(
            f"/api/voice/acknowledge/{incident_id}",
            headers=headers,
            json={"acknowledged_by": f"tech {i}"},
        ).json()
        for i in range(6)
    ]

    halted = [b for b in bodies if b["status"] == "ACKNOWLEDGED"]
    assert len(halted) == 1, (
        f"{len(halted)} people were told they had stopped the escalation"
    )
    losers = [b for b in bodies if b["status"] == "ALREADY_ACKNOWLEDGED"]
    assert len(losers) == 5
    # And each loser is told whose it actually is.
    assert all(b["acknowledged_by"] == halted[0]["incident"]["acknowledged_by"]
               for b in losers)
