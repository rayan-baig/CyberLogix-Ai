"""Spend controls: the cache, the daily caps, and the report."""

import costs
from store import STORE


def breach(api, headers, sensor_factory, sensor_id="RACK-01", temp=94.0):
    if STORE.get_sensor(sensor_id) is None:
        sensor_factory(headers, sensor_id=sensor_id)
    return api.post(
        "/api/sensor-pulse",
        headers=headers,
        json={"sensor_id": sensor_id, "temperature_fahrenheit": temp},
    ).json()


def test_identical_prompt_is_served_from_cache(
    api, tenant_factory, sensor_factory, stub_gemini
):
    """The same failure twice must not pay for the model twice."""
    headers, _ = tenant_factory()
    first = breach(api, headers, sensor_factory)
    assert first["sms_dispatch_source"] == "gemini"
    assert len(stub_gemini.prompts) == 1

    # Resolve, then reproduce the identical breach.
    api.post(
        f"/api/voice/resolve/{first['incident_id']}",
        headers=headers,
        json={"resolved_by": "Ops"},
    )
    second = breach(api, headers, sensor_factory)

    assert second["sms_dispatch_source"] == "cache"
    assert len(stub_gemini.prompts) == 1, "a second model call was made"

    report = api.get("/api/costs", headers=headers).json()
    assert report["totals"]["ai_calls"] == 1
    assert report["totals"]["ai_cache_hits"] == 1
    assert report["ai_cache_hit_rate_percent"] == 50.0
    assert report["estimated_saved_usd"] > 0


def test_cache_survives_a_store_reload(tmp_path, monkeypatch):
    """A redeploy must not throw away paid-for generations."""
    from db import Database
    from store import HubStore

    path = str(tmp_path / "cache.db")
    first = HubStore(db=Database(path))
    first.cache_put("key-1", "generated text")

    second = HubStore(db=Database(path))
    assert second.cache_get("key-1") == "generated text"


def test_ai_cap_falls_back_to_the_template(
    api, tenant_factory, sensor_factory, stub_gemini, monkeypatch
):
    monkeypatch.setattr(costs, "MAX_AI_CALLS_PER_DAY", 1)
    headers, tenant = tenant_factory()

    first = breach(api, headers, sensor_factory, sensor_id="S-1")
    assert first["sms_dispatch_source"] == "gemini"

    # A different sensor produces a different prompt, so no cache hit — and
    # the budget is already spent.
    sensor_factory(headers, sensor_id="S-2", vertical="restaurant")
    second = breach(api, headers, sensor_factory, sensor_id="S-2", temp=45.0)

    assert second["sms_dispatch_source"] == "fallback_template"
    # Crucially the alert still went out; only its wording degraded.
    assert "S-2" in second["dispatched_sms_text"]

    report = api.get("/api/costs", headers=headers).json()
    assert report["totals"]["ai_suppressed"] == 1


def test_sms_cap_suppresses_the_send_but_keeps_the_incident(
    api, tenant_factory, sensor_factory, configured_twilio, monkeypatch
):
    """Past the cap the alert still opens an incident; only the send stops."""
    monkeypatch.setattr(costs, "MAX_SMS_PER_DAY", 1)
    headers, _ = tenant_factory()

    first = breach(api, headers, sensor_factory, sensor_id="S-1")
    assert first["sms_delivery"]["delivered"] is True

    sensor_factory(headers, sensor_id="S-2", vertical="restaurant")
    second = breach(api, headers, sensor_factory, sensor_id="S-2", temp=45.0)

    assert second["sms_delivery"]["delivered"] is False
    assert second["sms_delivery"]["status"] == "budget_exceeded"
    # The incident is still open and still needs answering.
    assert second["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert second["dispatched_sms_text"]

    report = api.get("/api/costs", headers=headers).json()
    assert report["totals"]["sms_sent"] == 1
    assert report["totals"]["sms_suppressed"] == 1


def test_voice_cap_suppresses_the_call(
    api, tenant_factory, sensor_factory, configured_twilio, monkeypatch
):
    monkeypatch.setattr(costs, "MAX_VOICE_CALLS_PER_DAY", 1)
    headers, _ = tenant_factory()

    first = breach(api, headers, sensor_factory, sensor_id="S-1")["incident_id"]
    sensor_factory(headers, sensor_id="S-2", vertical="restaurant")
    second = breach(api, headers, sensor_factory, sensor_id="S-2", temp=45.0)[
        "incident_id"
    ]

    one = api.post(f"/api/voice/escalate/{first}?force=true", headers=headers).json()
    assert one["voice_delivery"]["delivered"] is True

    two = api.post(f"/api/voice/escalate/{second}?force=true", headers=headers).json()
    assert two["voice_delivery"]["status"] == "budget_exceeded"
    # The script was still written, so an operator can read what would have
    # been said.
    assert two["voice_script"]

    report = api.get("/api/costs", headers=headers).json()
    assert report["totals"]["voice_calls"] == 1
    assert report["totals"]["voice_suppressed"] == 1


def test_zero_cap_setting_means_unlimited_ai(
    api, tenant_factory, sensor_factory, stub_gemini, monkeypatch
):
    monkeypatch.setattr(costs, "MAX_AI_CALLS_PER_DAY", 0)
    headers, _ = tenant_factory()
    body = breach(api, headers, sensor_factory)
    assert body["sms_dispatch_source"] == "gemini"


def test_usage_is_attributed_per_tenant(
    api, tenant_factory, sensor_factory, stub_gemini
):
    alice, _ = tenant_factory(company_name="Alice Foods")
    bob, _ = tenant_factory(company_name="Bob Labs")
    breach(api, alice, sensor_factory, sensor_id="ALICE-1")

    alice_report = api.get("/api/costs", headers=alice).json()
    bob_report = api.get("/api/costs", headers=bob).json()
    assert alice_report["totals"]["ai_calls"] == 1
    assert bob_report["totals"]["ai_calls"] == 0
    assert bob_report["estimated_spend_usd"] == 0


def test_report_shape_is_complete(api, tenant_factory):
    headers, _ = tenant_factory()
    report = api.get("/api/costs", headers=headers).json()
    for key in (
        "today",
        "totals",
        "estimated_spend_usd",
        "estimated_saved_usd",
        "daily_caps",
        "unit_rates_usd",
        "ai_cache_entries",
    ):
        assert key in report, key
    assert report["daily_caps"]["sms"] == costs.MAX_SMS_PER_DAY


def test_cache_key_is_stable_and_purpose_scoped():
    assert costs.cache_key("prompt", "sms") == costs.cache_key("prompt", "sms")
    assert costs.cache_key("prompt", "sms") != costs.cache_key("prompt", "voice")
    assert costs.cache_key("a", "sms") != costs.cache_key("b", "sms")
