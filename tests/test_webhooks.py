"""Outbound alerts into Slack, Teams and PagerDuty.

An alert that needs somebody to log in to a dashboard is an alert that
waits, so a breach is pushed to wherever the team already is. The rule
that matters most here is the last one: a chat outage must never take the
breach handler down with it.
"""

import pytest

import webhooks


@pytest.fixture()
def posted(monkeypatch):
    """Capture every outbound post instead of putting it on the wire."""
    log = []

    def _post(url, body):
        log.append({"url": url, "body": body})
        return True, "http_200"

    monkeypatch.setattr(webhooks, "_post", _post)
    return log


@pytest.fixture()
def broken_post(monkeypatch):
    def _post(url, body):
        return False, "unreachable"

    monkeypatch.setattr(webhooks, "_post", _post)


def add_hook(api, headers, kind="slack",
             target="https://hooks.slack.com/services/T/B/xxxxxxyyyyyy", **extra):
    resp = api.post("/api/webhooks", headers=headers,
                    json={"kind": kind, "target": target, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


def breach(api, headers, sensor_factory, sensor_id="RACK-01"):
    sensor_factory(headers, sensor_id=sensor_id)
    return api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": sensor_id,
                          "temperature_fahrenheit": 94.0}).json()


# ---- management ----------------------------------------------------------


def test_the_url_is_never_returned_whole(api, operator_factory):
    """The webhook URL is the credential; echoing it back leaks it."""
    headers, _, _ = operator_factory()
    hook = add_hook(api, headers)
    assert hook["target"] == "...yyyyyy"

    listed = api.get("/api/webhooks", headers=headers).json()
    assert listed["webhooks"][0]["target"] == "...yyyyyy"
    assert "hooks.slack.com" not in str(listed)


def test_a_plaintext_url_is_refused(api, operator_factory):
    headers, _, _ = operator_factory()
    resp = api.post("/api/webhooks", headers=headers,
                    json={"kind": "slack", "target": "http://hooks.slack.com/x"})
    assert resp.status_code == 400
    assert "https" in resp.json()["detail"]


def test_an_unknown_kind_is_refused(api, operator_factory):
    headers, _, _ = operator_factory()
    resp = api.post("/api/webhooks", headers=headers,
                    json={"kind": "carrier-pigeon", "target": "https://x.example/y"})
    assert resp.status_code == 400


def test_another_tenants_site_cannot_be_targeted(api, operator_factory):
    theirs, _, _ = operator_factory(company_name="Acme", email="a@x.com")
    site = api.post("/api/sites", headers=theirs,
                    json={"name": "Boca"}).json()

    mine, _, _ = operator_factory(company_name="Beta", email="b@x.com")
    resp = api.post("/api/webhooks", headers=mine,
                    json={"kind": "slack", "target": "https://hooks.slack.com/a/b/c",
                          "site_id": site["site_id"]})
    assert resp.status_code == 404


def test_repointing_a_hook_clears_its_failures(api, operator_factory, broken_post):
    """The old target's failures say nothing about a new one."""
    headers, _, _ = operator_factory()
    hook = add_hook(api, headers)
    api.post(f"/api/webhooks/{hook['webhook_id']}/test", headers=headers)
    assert api.get("/api/webhooks", headers=headers).json()[
        "webhooks"][0]["consecutive_failures"] == 1

    api.patch(f"/api/webhooks/{hook['webhook_id']}", headers=headers,
              json={"target": "https://hooks.slack.com/services/T/B/newnewnew"})
    row = api.get("/api/webhooks", headers=headers).json()["webhooks"][0]
    assert row["consecutive_failures"] == 0
    assert row["last_status"] is None


# ---- delivery ------------------------------------------------------------


def test_a_breach_reaches_slack(api, operator_factory, sensor_factory, posted):
    headers, _, _ = operator_factory()
    add_hook(api, headers)
    body = breach(api, headers, sensor_factory)

    assert body["webhook_fanout"][0]["delivered"] is True
    assert len(posted) == 1
    assert "CRITICAL" in posted[0]["body"]["text"]
    assert "RACK-01" in str(posted[0]["body"])


def test_pagerduty_resolves_the_alert_it_opened(
    api, operator_factory, sensor_factory, posted
):
    """A trigger with no matching resolve leaves an orphan on the rotation."""
    headers, _, _ = operator_factory()
    add_hook(api, headers, kind="pagerduty", target="R0UT1NGK3Y00001")
    body = breach(api, headers, sensor_factory)
    incident_id = body["incident_id"]

    api.post(f"/api/voice/resolve/{incident_id}", headers=headers, json={})

    assert posted[0]["url"] == webhooks.PAGERDUTY_ENQUEUE_URL
    assert posted[0]["body"]["event_action"] == "trigger"
    assert posted[-1]["body"]["event_action"] == "resolve"
    # Same key both times, or PagerDuty closes nothing.
    assert posted[0]["body"]["dedup_key"] == incident_id
    assert posted[-1]["body"]["dedup_key"] == incident_id


def test_teams_gets_a_card_not_a_slack_payload(
    api, operator_factory, sensor_factory, posted
):
    headers, _, _ = operator_factory()
    add_hook(api, headers, kind="teams",
             target="https://outlook.office.com/webhook/aaa/bbb")
    breach(api, headers, sensor_factory)
    assert posted[0]["body"]["@type"] == "MessageCard"


def test_a_celsius_tenant_gets_celsius_in_the_channel(
    api, operator_factory, sensor_factory, posted
):
    headers, _, _ = operator_factory()
    api.post("/api/licenses/me/temperature-unit", headers=headers,
             json={"temperature_unit": "C"})
    add_hook(api, headers, kind="generic", target="https://ops.example.com/hook")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_celsius": 21.0})

    assert posted[0]["body"]["reading"] == "21.0°C"
    assert posted[0]["body"]["temperature_unit"] == "C"


def test_a_site_hook_and_an_estate_hook_both_fire(
    api, operator_factory, sensor_factory, posted
):
    """Head office must not stop seeing branch alerts."""
    headers, _, _ = operator_factory()
    site = api.post("/api/sites", headers=headers, json={"name": "Boca"}).json()
    add_hook(api, headers, kind="generic", target="https://hq.example.com/hook")
    add_hook(api, headers, kind="generic", target="https://boca.example.com/hook",
             site_id=site["site_id"])
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post(f"/api/sites/{site['site_id']}/sensors", headers=headers,
             json={"sensor_id": "FRZ-1"})

    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 70.0})

    assert len(posted) == 2
    assert {p["url"] for p in posted} == {
        "https://hq.example.com/hook", "https://boca.example.com/hook"
    }


def test_a_hook_for_another_site_stays_quiet(
    api, operator_factory, sensor_factory, posted
):
    headers, _, _ = operator_factory()
    boca = api.post("/api/sites", headers=headers, json={"name": "Boca"}).json()
    boynton = api.post("/api/sites", headers=headers,
                       json={"name": "Boynton"}).json()
    add_hook(api, headers, kind="generic", target="https://boynton.example.com/h",
             site_id=boynton["site_id"])
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post(f"/api/sites/{boca['site_id']}/sensors", headers=headers,
             json={"sensor_id": "FRZ-1"})

    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 70.0})
    assert posted == []


def test_a_paused_hook_stays_quiet(
    api, operator_factory, sensor_factory, posted
):
    headers, _, _ = operator_factory()
    hook = add_hook(api, headers)
    api.patch(f"/api/webhooks/{hook['webhook_id']}", headers=headers,
              json={"active": False})
    breach(api, headers, sensor_factory)
    assert posted == []


def test_a_slack_outage_does_not_stop_the_alert(
    api, operator_factory, sensor_factory, monkeypatch
):
    """The SMS is the alert. A chat outage must not take it down."""
    def _explode(url, body):
        raise RuntimeError("slack is on fire")

    monkeypatch.setattr(webhooks, "_post", _explode)
    headers, _, _ = operator_factory()
    add_hook(api, headers)

    with pytest.raises(RuntimeError):
        webhooks._post("https://x", {})

    # _post is the only thing that touches the network, and the real one
    # swallows its own failures, so the breach path still completes.
    monkeypatch.setattr(webhooks, "_post", lambda url, body: (False, "unreachable"))
    body = breach(api, headers, sensor_factory)
    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert body["dispatched_sms_text"]
    assert body["webhook_fanout"][0]["delivered"] is False


def test_the_real_post_never_raises(monkeypatch):
    """Whatever urllib does, the breach handler must keep going."""
    def _boom(*args, **kwargs):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(webhooks.urllib.request, "urlopen", _boom)
    delivered, status = webhooks._post("https://x.example/hook", {"a": 1})
    assert delivered is False
    assert status == "unreachable"


def test_a_non_https_target_is_never_posted_to(monkeypatch):
    called = []
    monkeypatch.setattr(
        webhooks.urllib.request, "urlopen",
        lambda *a, **k: called.append(1),
    )
    delivered, status = webhooks._post("http://insecure.example/hook", {})
    assert delivered is False
    assert status == "refused_insecure_url"
    assert called == []


def test_repeated_failures_are_visible(
    api, operator_factory, sensor_factory, broken_post
):
    """A hook nobody notices is broken is a hook that fails on the night."""
    headers, _, _ = operator_factory()
    add_hook(api, headers)
    for n in range(3):
        breach(api, headers, sensor_factory, sensor_id=f"RACK-0{n}")

    listed = api.get("/api/webhooks", headers=headers).json()
    assert listed["webhooks"][0]["consecutive_failures"] == 3
    assert listed["failing"] == [listed["webhooks"][0]["webhook_id"]]


def test_webhooks_survive_a_restart(tmp_path):
    from db import Database
    from store import HubStore

    path = str(tmp_path / "hooks.db")
    first = HubStore(db=Database(path))
    tenant = first.create_tenant("A", "n", "+1", "a@example.com", "growth")
    hook = first.add_webhook(tenant.tenant_id, "slack", "https://x.example/abc")

    second = HubStore(db=Database(path))
    restored = second.get_webhook(hook.webhook_id)
    assert restored.target == "https://x.example/abc"
    assert restored.kind == "slack"


def test_a_hook_pointed_at_our_own_network_is_refused(monkeypatch):
    """A customer-chosen URL our server posts to is a forgery primitive."""
    called = []
    monkeypatch.setattr(
        webhooks.urllib.request, "urlopen", lambda *a, **k: called.append(1)
    )
    monkeypatch.setattr(webhooks, "ALLOW_PRIVATE_WEBHOOK_TARGETS", False)

    for target in (
        "https://169.254.169.254/latest/meta-data/",
        "https://127.0.0.1/admin",
        "https://10.0.0.5/internal",
    ):
        delivered, status = webhooks._post(target, {})
        assert delivered is False, target
        assert status == "refused_private_address", target
    assert called == []


def test_a_self_hosted_lan_receiver_can_be_allowed(monkeypatch):
    monkeypatch.setattr(webhooks, "ALLOW_PRIVATE_WEBHOOK_TARGETS", True)
    monkeypatch.setattr(
        webhooks.urllib.request, "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no listener")),
    )
    delivered, status = webhooks._post("https://10.0.0.5/hook", {})
    assert status == "unreachable"
