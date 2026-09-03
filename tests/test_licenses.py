"""Corporate license management: keys, seats and entitlements."""

from store import STORE


def test_plan_catalogue_is_public(api):
    plans = {p["plan"]: p for p in api.get("/api/licenses/plans").json()["plans"]}
    assert plans["trial"]["max_sensors"] == 5
    assert plans["trial"]["voice_escalation"] is False
    assert plans["enterprise"]["voice_escalation"] is True


def test_onboarding_issues_key_once(api, tenant_factory):
    headers, tenant = tenant_factory(plan="growth")
    assert headers["X-CyberLogix-Key"].startswith("clx_")
    assert tenant["seats_total"] == 50
    assert tenant["seats_used"] == 0

    # The key is never echoed back by a later read.
    me = api.get("/api/licenses/me", headers=headers).json()
    assert "api_key" not in me
    assert me["license_active"] is True


def test_unknown_plan_rejected(api):
    resp = api.post(
        "/api/licenses/tenants",
        json={
            "company_name": "X",
            "contact_name": "Y",
            "contact_phone": "+1-555-0100",
            "contact_email": "ops@example.com",
            "plan": "platinum",
        },
    )
    assert resp.status_code == 400


def test_missing_and_bad_keys_rejected(api):
    assert api.get("/api/licenses/me").status_code == 401
    assert (
        api.get("/api/licenses/me", headers={"X-CyberLogix-Key": "nope"}).status_code
        == 401
    )


def test_seat_cap_enforced_on_trial(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory(plan="trial")
    for index in range(5):
        sensor_factory(headers, sensor_id=f"S-{index}")

    resp = api.post(
        "/api/licenses/me/sensors",
        headers=headers,
        json={
            "sensor_id": "S-overflow",
            "industry_vertical": "cybersecurity",
            "location_name": "Hall B",
        },
    )
    assert resp.status_code == 409
    assert "Seat limit reached" in resp.json()["detail"]


def test_duplicate_sensor_rejected(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="RACK-01")
    resp = api.post(
        "/api/licenses/me/sensors",
        headers=headers,
        json={
            "sensor_id": "RACK-01",
            "industry_vertical": "cybersecurity",
            "location_name": "Hall B",
        },
    )
    assert resp.status_code == 409


def test_bad_vertical_rejected_at_registration(api, tenant_factory):
    headers, _ = tenant_factory()
    resp = api.post(
        "/api/licenses/me/sensors",
        headers=headers,
        json={
            "sensor_id": "S-1",
            "industry_vertical": "casino",
            "location_name": "Floor 2",
        },
    )
    assert resp.status_code == 400
    assert "Allowed keys" in resp.json()["detail"]


def test_downgrade_blocked_when_seats_would_strand(
    api, tenant_factory, sensor_factory
):
    headers, _ = tenant_factory(plan="enterprise")
    for index in range(6):
        sensor_factory(headers, sensor_id=f"S-{index}")

    resp = api.post("/api/licenses/me/plan", headers=headers, json={"plan": "trial"})
    assert resp.status_code == 409
    assert "Cannot downgrade" in resp.json()["detail"]


def test_decommission_frees_a_seat(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory(plan="trial")
    sensor_factory(headers, sensor_id="S-1")
    assert api.get("/api/licenses/me", headers=headers).json()["seats_used"] == 1

    resp = api.delete("/api/licenses/me/sensors/S-1", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["seats_used"] == 0


def test_suspension_locks_the_whole_platform(
    api, tenant_factory, sensor_factory
):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="RACK-01")
    api.post("/api/licenses/me/suspend", headers=headers)

    for path, method in (
        ("/api/licenses/me", "get"),
        ("/api/voice/pending", "get"),
        ("/api/autopilot/sweep", "post"),
    ):
        resp = getattr(api, method)(path, headers=headers)
        assert resp.status_code == 402, path

    resp = api.post(
        "/api/sensor-pulse",
        headers=headers,
        json={"sensor_id": "RACK-01", "temperature_fahrenheit": 70.0},
    )
    assert resp.status_code == 402


def test_expired_license_is_refused(api, tenant_factory):
    headers, tenant = tenant_factory()
    stored = STORE.get_tenant(tenant["tenant_id"])
    stored.expires_at = stored.activated_at

    resp = api.get("/api/licenses/me", headers=headers)
    assert resp.status_code == 402
    assert "expired" in resp.json()["detail"].lower()


def test_tenants_cannot_see_each_others_sensors(
    api, tenant_factory, sensor_factory
):
    alice, _ = tenant_factory(company_name="Alice Foods")
    bob, _ = tenant_factory(company_name="Bob Labs")
    sensor_factory(alice, sensor_id="ALICE-1")

    resp = api.post(
        "/api/sensor-pulse",
        headers=bob,
        json={"sensor_id": "ALICE-1", "temperature_fahrenheit": 95.0},
    )
    assert resp.status_code == 404
