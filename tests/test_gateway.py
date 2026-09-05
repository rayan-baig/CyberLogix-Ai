"""Hub gateway and cross-module mounting."""


def test_root_gateway_lists_every_module(api):
    body = api.get("/api").json()
    assert body["system"] == "CyberLogix AI Master Engine"
    assert body["status"] == "fully_operational_stealth_mode"
    assert len(body["modules_active"]) == 14
    for module in ("universal_iot_telemetry", "byod_hardware_bridge"):
        assert module in body["modules_active"]


def test_health_reports_subsystem_state(api):
    body = api.get("/api/health").json()
    assert body["status"] == "online"
    assert body["active_profiles"] == 8
    assert body["modules_active"] == 14
    assert body["plan_tiers"] == ["trial", "growth", "enterprise"]


def test_every_router_is_actually_mounted(api):
    paths = api.get("/openapi.json").json()["paths"]
    for expected in (
        "/api/sensor-pulse",
        "/api/licenses/tenants",
        "/api/autopilot/sweep",
        "/api/voice/pending",
        "/api/forecast/fleet",
        "/api/v1/bridge/sensor-webhook-ingest",
        "/api/v1/bridge/summarize-transcript",
        "/api/console/overview",
        "/api/accounts/login",
        "/api/costs",
        "/api/contacts",
        "/api/billing/pricing",
        "/api/v1/enterprise-billing/provision-cluster",
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


def test_console_html_is_served_at_root(api):
    resp = api.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "CyberLogix" in resp.text
