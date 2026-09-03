"""Hub gateway and cross-module mounting."""


def test_root_gateway_lists_all_five_modules(api):
    body = api.get("/").json()
    assert body["system"] == "CyberLogix AI Master Engine"
    assert body["status"] == "fully_operational_stealth_mode"
    assert len(body["modules_active"]) == 5
    assert "universal_iot_telemetry" in body["modules_active"]


def test_health_reports_subsystem_state(api):
    body = api.get("/api/health").json()
    assert body["status"] == "online"
    assert body["active_profiles"] == 8
    assert body["modules_active"] == 5
    assert body["plan_tiers"] == ["trial", "growth", "enterprise"]


def test_every_router_is_actually_mounted(api):
    paths = api.get("/openapi.json").json()["paths"]
    for expected in (
        "/api/sensor-pulse",
        "/api/licenses/tenants",
        "/api/autopilot/sweep",
        "/api/voice/pending",
        "/api/forecast/fleet",
    ):
        assert expected in paths, f"{expected} is not mounted"


def test_industry_catalogue_exposes_eight_verticals(api):
    body = api.get("/api/industries").json()
    assert body["count"] == 8
    medical = next(
        i for i in body["industries"] if i["vertical"] == "medical_lab"
    )
    assert medical["danger_above"] == 46.0
    assert medical["danger_below"] == 36.0
