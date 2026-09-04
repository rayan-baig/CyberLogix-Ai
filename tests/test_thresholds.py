"""Per-sensor threshold overrides."""


def pulse(api, headers, sensor_id, temp):
    return api.post(
        "/api/sensor-pulse",
        headers=headers,
        json={"sensor_id": sensor_id, "temperature_fahrenheit": temp},
    ).json()


def set_bounds(api, headers, sensor_id, above=None, below=None):
    return api.post(
        f"/api/licenses/me/sensors/{sensor_id}/thresholds",
        headers=headers,
        json={"danger_above": above, "danger_below": below},
    )


def test_override_tightens_a_limit(api, tenant_factory, sensor_factory):
    """A freezer held colder than its sector's rule of thumb."""
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")

    # 28F is fine against the sector default of 32F.
    assert pulse(api, headers, "FRZ-1", 28.0)["status"] == "nominal"

    resp = set_bounds(api, headers, "FRZ-1", above=25.0)
    assert resp.status_code == 200
    assert resp.json()["effective_above"] == 25.0

    body = pulse(api, headers, "FRZ-1", 28.0)
    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert "28.0°F > 25.0°F" in body["breach_details"]


def test_override_loosens_a_limit(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="HALL-1", vertical="cybersecurity")
    set_bounds(api, headers, "HALL-1", above=95.0)
    assert pulse(api, headers, "HALL-1", 90.0)["status"] == "nominal"


def test_clearing_restores_the_industry_default(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    set_bounds(api, headers, "FRZ-1", above=25.0)

    cleared = set_bounds(api, headers, "FRZ-1").json()
    assert cleared["effective_above"] == 32.0
    assert cleared["sensor"]["uses_override"] is False
    assert "industry defaults apply" in cleared["message"]
    assert pulse(api, headers, "FRZ-1", 28.0)["status"] == "nominal"


def test_impossible_band_is_rejected(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="LAB-1", vertical="medical_lab")
    resp = set_bounds(api, headers, "LAB-1", above=36.0, below=46.0)
    assert resp.status_code == 400
    assert "can never read in band" in resp.json()["detail"]


def test_override_is_reflected_in_the_console_and_forecast(
    api, tenant_factory, sensor_factory
):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="HALL-1", vertical="cybersecurity")
    set_bounds(api, headers, "HALL-1", above=95.0)

    row = api.get("/api/console/overview", headers=headers).json()["sensors"][0]
    assert row["danger_above"] == 95.0
    assert row["uses_override"] is True

    forecast = api.get("/api/forecast/sensor/HALL-1", headers=headers).json()
    assert forecast["danger_above"] == 95.0


def test_override_survives_a_restart(tmp_path):
    from db import Database
    from store import HubStore

    path = str(tmp_path / "override.db")
    first = HubStore(db=Database(path))
    tenant = first.create_tenant("A", "n", "+1", "a@example.com", "growth")
    sensor = first.register_sensor("FRZ-1", tenant.tenant_id, "restaurant", "loc")
    first.set_sensor_overrides(sensor, above=25.0, below=None)

    second = HubStore(db=Database(path))
    assert second.get_sensor("FRZ-1").bounds() == (25.0, None)


def test_foreign_sensor_cannot_be_retuned(api, tenant_factory, sensor_factory):
    alice, _ = tenant_factory(company_name="Alice")
    bob, _ = tenant_factory(company_name="Bob")
    sensor_factory(alice, sensor_id="ALICE-1")
    assert set_bounds(api, bob, "ALICE-1", above=50.0).status_code == 404
