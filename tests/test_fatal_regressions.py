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
