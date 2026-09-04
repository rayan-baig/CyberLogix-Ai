"""Predictive breakdown forecasting."""

from datetime import timedelta

import pytest

from store import STORE, evaluate_breach, utc_now


@pytest.fixture()
def seed_readings():
    """Write a backdated temperature history for a sensor."""

    def _seed(sensor_id, temps, spacing_minutes=10):
        sensor = STORE.get_sensor(sensor_id)
        now = utc_now()
        count = len(temps)
        for index, temp in enumerate(temps):
            STORE.record_reading(
                sensor=sensor,
                temperature_fahrenheit=temp,
                humidity_percent=50.0,
                breached=evaluate_breach(sensor.industry_vertical, temp) is not None,
                at=now - timedelta(minutes=(count - 1 - index) * spacing_minutes),
            )

    return _seed


def test_insufficient_history_reports_honestly(
    api, tenant_factory, sensor_factory, seed_readings
):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    seed_readings("RACK-01", [68.0])

    body = api.get("/api/forecast/sensor/RACK-01", headers=headers).json()
    assert body["forecast"] == "insufficient_data"
    assert body["risk_level"] == "unknown"
    assert body["hours_until_breach"] is None


def test_rising_trend_projects_a_breach(
    api, tenant_factory, sensor_factory, seed_readings
):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    # +2°F every 10 minutes = 12°F/hour, ending at 74°F against a 78°F limit.
    seed_readings("RACK-01", [68.0, 70.0, 72.0, 74.0])

    body = api.get("/api/forecast/sensor/RACK-01", headers=headers).json()
    assert body["forecast"] == "breach_projected"
    assert body["trend_f_per_hour"] == pytest.approx(12.0, abs=0.01)
    assert body["hours_until_breach"] == pytest.approx(0.33, abs=0.02)
    assert body["risk_level"] == "critical"
    assert body["confidence"] == pytest.approx(1.0, abs=0.001)


def test_slow_drift_is_elevated_not_critical(
    api, tenant_factory, sensor_factory, seed_readings
):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    # +0.1°F every 10 minutes = 0.6°F/hour, from 68°F to a 78°F limit.
    seed_readings("RACK-01", [67.7, 67.8, 67.9, 68.0])

    body = api.get("/api/forecast/sensor/RACK-01", headers=headers).json()
    assert body["risk_level"] == "elevated"
    assert 16 < body["hours_until_breach"] < 17


def test_flat_history_is_stable(
    api, tenant_factory, sensor_factory, seed_readings
):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    seed_readings("RACK-01", [68.0, 68.0, 68.0, 68.0])

    body = api.get("/api/forecast/sensor/RACK-01", headers=headers).json()
    assert body["forecast"] == "stable"
    assert body["risk_level"] == "stable"


def test_falling_trend_tracks_the_low_bound(
    api, tenant_factory, sensor_factory, seed_readings
):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="BLOOD-07", vertical="medical_lab")
    # Cooling 1°F per 10 minutes from 40°F toward the 36°F floor.
    seed_readings("BLOOD-07", [43.0, 42.0, 41.0, 40.0])

    body = api.get("/api/forecast/sensor/BLOOD-07", headers=headers).json()
    assert body["forecast"] == "breach_projected"
    assert body["trend_f_per_hour"] == pytest.approx(-6.0, abs=0.01)
    assert body["hours_until_breach"] == pytest.approx(0.67, abs=0.02)


def test_already_breached_is_critical_with_zero_lead_time(
    api, tenant_factory, sensor_factory, seed_readings
):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    seed_readings("RACK-01", [80.0, 84.0, 88.0, 92.0])

    body = api.get("/api/forecast/sensor/RACK-01", headers=headers).json()
    assert body["already_breached"] is True
    assert body["risk_level"] == "critical"
    assert body["hours_until_breach"] == 0.0


def test_narration_attaches_a_maintenance_brief(
    api, tenant_factory, sensor_factory, seed_readings, stub_gemini
):
    headers, _ = tenant_factory()
    sensor_factory(headers)
    seed_readings("RACK-01", [68.0, 70.0, 72.0, 74.0])

    body = api.get(
        "/api/forecast/sensor/RACK-01?narrate=true", headers=headers
    ).json()
    assert body["brief_source"] == "gemini"
    assert body["maintenance_brief"].startswith("URGENT:")


def test_fleet_ranks_riskiest_sensor_first(
    api, tenant_factory, sensor_factory, seed_readings
):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="CALM-1")
    sensor_factory(headers, sensor_id="DOOMED-1")
    seed_readings("CALM-1", [68.0, 68.0, 68.0, 68.0])
    seed_readings("DOOMED-1", [68.0, 70.0, 72.0, 74.0])

    body = api.get("/api/forecast/fleet", headers=headers).json()
    assert body["sensors_analysed"] == 2
    assert body["forecasts"][0]["sensor_id"] == "DOOMED-1"
    assert body["risk_tally"]["critical"] == 1
    assert len(body["at_risk"]) == 1


def test_trial_plan_cannot_forecast(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory(plan="trial")
    sensor_factory(headers)
    resp = api.get("/api/forecast/fleet", headers=headers)
    assert resp.status_code == 403


def test_foreign_sensor_is_invisible(api, tenant_factory, sensor_factory):
    alice, _ = tenant_factory(company_name="Alice Foods")
    bob, _ = tenant_factory(company_name="Bob Labs")
    sensor_factory(alice, sensor_id="ALICE-1")

    resp = api.get("/api/forecast/sensor/ALICE-1", headers=bob)
    assert resp.status_code == 404
