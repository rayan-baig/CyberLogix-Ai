"""Sites, Celsius display, and sensor health."""

def make_site(api, headers, name="Store 118 Boca Raton", address="Boca Raton, FL"):
    resp = api.post("/api/sites", headers=headers,
                    json={"name": name, "address": address})
    assert resp.status_code == 201, resp.text
    return resp.json()


def place(api, headers, site_id, sensor_id):
    return api.post(f"/api/sites/{site_id}/sensors", headers=headers,
                    json={"sensor_id": sensor_id})


# ---- sites ---------------------------------------------------------------


def test_a_sensor_can_be_placed_at_a_site(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    site = make_site(api, headers)
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")

    assert place(api, headers, site["site_id"], "FRZ-1").status_code == 200
    body = api.get(f"/api/sites/{site['site_id']}", headers=headers).json()
    assert body["sensor_count"] == 1
    assert body["sensors"][0]["sensor_id"] == "FRZ-1"


def test_unplaced_sensors_are_listed_separately(
    api, operator_factory, sensor_factory
):
    """A sensor nobody assigned is billable but invisible on a site report."""
    headers, _, _ = operator_factory()
    site = make_site(api, headers)
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    sensor_factory(headers, sensor_id="FRZ-2", vertical="restaurant")
    place(api, headers, site["site_id"], "FRZ-1")

    body = api.get("/api/sites", headers=headers).json()
    assert body["unassigned_count"] == 1
    assert body["unassigned_sensors"][0]["sensor_id"] == "FRZ-2"


def test_deleting_a_site_releases_its_sensors(
    api, operator_factory, sensor_factory
):
    """Removing a location must not delete the readings under it."""
    headers, _, _ = operator_factory()
    site = make_site(api, headers)
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    place(api, headers, site["site_id"], "FRZ-1")

    resp = api.delete(f"/api/sites/{site['site_id']}", headers=headers).json()
    assert resp["sensors_released"] == 1
    still_there = api.get("/api/licenses/me/sensors", headers=headers).json()
    assert still_there["count"] == 1
    assert api.get("/api/sites", headers=headers).json()["unassigned_count"] == 1


def test_reconciliation_flags_a_site_with_no_coverage(
    api, operator_factory, sensor_factory
):
    """Billed but unprotected is the state worth catching."""
    headers, _, _ = operator_factory()
    site = make_site(api, headers)
    body = api.get(f"/api/sites/{site['site_id']}/reconciliation",
                   headers=headers).json()
    assert body["covered"] is False
    assert body["sensors"] == 0


def test_sites_are_scoped_to_the_tenant(api, operator_factory):
    alice, _, _ = operator_factory(company_name="Alice", email="a@example.com")
    bob, _, _ = operator_factory(company_name="Bob", email="b@example.com")
    site = make_site(api, alice)

    assert api.get("/api/sites", headers=bob).json()["count"] == 0
    assert api.get(f"/api/sites/{site['site_id']}", headers=bob).status_code == 404


def test_viewer_cannot_change_sites(api, operator_factory):
    owner, _, _ = operator_factory()
    api.post("/api/accounts/users", headers=owner,
             json={"email": "v@example.com", "full_name": "Vic", "role": "viewer",
                   "password": "a-long-viewer-password"})
    login = api.post("/api/accounts/login",
                     json={"email": "v@example.com",
                           "password": "a-long-viewer-password"}).json()
    viewer = {"Authorization": f"Bearer {login['token']}"}

    assert api.get("/api/sites", headers=viewer).status_code == 200
    assert api.post("/api/sites", headers=viewer,
                    json={"name": "X"}).status_code == 403


# ---- site-scoped alerting ------------------------------------------------


def test_only_the_local_manager_is_woken(
    api, operator_factory, sensor_factory, monkeypatch
):
    """Boca Raton's manager must not be paged for Boynton Beach."""
    sent = []
    monkeypatch.setattr(
        "telemetry.send_sms",
        lambda to, body, tenant_id=None: (
            sent.append(to),
            {"channel": "sms", "to": to, "delivered": True, "status": "queued",
             "provider_sid": "SM1", "detail": "ok"},
        )[1],
    )
    headers, _, _ = operator_factory()
    boca = make_site(api, headers, name="Boca Raton")
    boynton = make_site(api, headers, name="Boynton Beach")

    for site, sensor_id, phone, who in (
        (boca, "BOCA-1", "+15550001", "Boca manager"),
        (boynton, "BOYN-1", "+15550002", "Boynton manager"),
    ):
        sensor_factory(headers, sensor_id=sensor_id, vertical="restaurant")
        place(api, headers, site["site_id"], sensor_id)
        api.post("/api/contacts", headers=headers,
                 json={"full_name": who, "phone": phone,
                       "site_id": site["site_id"]})

    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "BOCA-1", "temperature_fahrenheit": 45.0})
    assert sent == ["+15550001"]


def test_tenant_wide_contacts_cover_a_site_with_none_of_its_own(
    api, operator_factory, sensor_factory, monkeypatch
):
    sent = []
    monkeypatch.setattr(
        "telemetry.send_sms",
        lambda to, body, tenant_id=None: (
            sent.append(to),
            {"channel": "sms", "to": to, "delivered": True, "status": "queued",
             "provider_sid": "SM1", "detail": "ok"},
        )[1],
    )
    headers, _, _ = operator_factory()
    site = make_site(api, headers)
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    place(api, headers, site["site_id"], "FRZ-1")
    api.post("/api/contacts", headers=headers,
             json={"full_name": "Head office", "phone": "+15559999"})

    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 45.0})
    assert sent == ["+15559999"]


# ---- Celsius -------------------------------------------------------------


def test_a_european_fleet_reports_in_celsius(
    api, operator_factory, sensor_factory
):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="V-1", vertical="superyacht")

    body = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "V-1", "temperature_celsius": 40.0}).json()
    # 40C is 104F, which is over the 90F engine-bay limit.
    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert "104.0°F" in body["breach_details"]


def test_display_switches_to_celsius_without_rewriting_history(
    api, operator_factory, sensor_factory
):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="V-1", vertical="superyacht")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "V-1", "temperature_fahrenheit": 68.0})

    before = api.get("/api/licenses/me/sensors", headers=headers).json()
    assert before["temperature_unit"] == "F"
    assert before["sensors"][0]["last_temperature_display"] == 68.0

    resp = api.post("/api/licenses/me/temperature-unit", headers=headers,
                    json={"temperature_unit": "C"})
    assert resp.status_code == 200

    after = api.get("/api/licenses/me/sensors", headers=headers).json()
    assert after["temperature_unit"] == "C"
    assert after["sensors"][0]["last_temperature_display"] == 20.0
    # Stored canonically, so nothing was lost or rounded away.
    assert after["sensors"][0]["last_temperature"] == 68.0
    assert after["sensors"][0]["danger_above_display"] == 32.22


def test_a_celsius_tenant_is_alerted_in_celsius(
    api, operator_factory, sensor_factory, stub_gemini, break_gemini
):
    """A manager in Lyon woken at 3am should not have to convert."""
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post("/api/licenses/me/temperature-unit", headers=headers,
             json={"temperature_unit": "C"})

    body = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "FRZ-1", "temperature_celsius": 21.0}).json()

    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert body["temperature_unit"] == "C"
    assert body["current_temperature"] == 21.0
    assert "°C" in body["breach_details"]
    assert "°F" not in body["breach_details"]
    # The reading handed to the model is Celsius, so what it writes is too.
    assert "21.0°C" in stub_gemini.prompts[-1]
    assert "°F" not in stub_gemini.prompts[-1]

    # And the wording used when the model is unreachable.
    break_gemini("outage")
    sensor_factory(headers, sensor_id="FRZ-2", vertical="restaurant")
    offline = api.post("/api/sensor-pulse", headers=headers,
                       json={"sensor_id": "FRZ-2",
                             "temperature_celsius": 21.0}).json()
    assert "21.0°C" in offline["dispatched_sms_text"]


def test_the_escalation_call_speaks_the_tenants_unit(
    api, operator_factory, sensor_factory, break_gemini
):
    """Twilio's voice reads "21.0°C" as gibberish, so it must be spelled out."""
    from store import STORE
    from voice_dispatch import build_voice_script

    headers, tenant_row, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post("/api/licenses/me/temperature-unit", headers=headers,
             json={"temperature_unit": "C"})
    body = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "FRZ-1", "temperature_celsius": 21.0}).json()

    break_gemini("outage")
    tenant = STORE.get_tenant(tenant_row["tenant_id"])
    incident = STORE.get_incident(body["incident_id"])
    script, _ = build_voice_script(incident, tenant)

    assert "21.0 degrees Celsius" in script
    assert "degrees Fahrenheit" not in script


def test_both_units_at_once_is_rejected(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="V-1", vertical="superyacht")
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "V-1", "temperature_fahrenheit": 68.0,
                          "temperature_celsius": 20.0})
    assert resp.status_code == 422


def test_unknown_unit_rejected(api, operator_factory):
    headers, _, _ = operator_factory()
    resp = api.post("/api/licenses/me/temperature-unit", headers=headers,
                    json={"temperature_unit": "K"})
    assert resp.status_code == 400


# ---- sensor health -------------------------------------------------------


def test_battery_is_recorded_and_low_battery_flagged(
    api, operator_factory, sensor_factory
):
    """A battery reported before it dies is a sensor that never goes dark."""
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")

    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 28.0,
                   "battery_percent": 14.0, "signal_percent": 72.0})

    fleet = api.get("/api/licenses/me/sensors", headers=headers).json()
    sensor = fleet["sensors"][0]
    assert sensor["battery_percent"] == 14.0
    assert sensor["signal_percent"] == 72.0
    assert sensor["battery_low"] is True
    assert fleet["low_battery"] == 1


def test_healthy_battery_is_not_flagged(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 28.0,
                   "battery_percent": 85.0})
    fleet = api.get("/api/licenses/me/sensors", headers=headers).json()
    assert fleet["sensors"][0]["battery_low"] is False
    assert fleet["low_battery"] == 0


def test_byod_hardware_can_report_battery(api, operator_factory):
    headers, _, _ = operator_factory()
    api.post("/api/licenses/me/sensors", headers=headers,
             json={"sensor_id": "FRZ-1", "industry_vertical": "restaurant",
                   "location_name": "Walk-In",
                   "external_device_sn": "ELITECH-9982"})

    body = api.post("/api/v1/bridge/sensor-webhook-ingest", headers=headers,
                    json={"device_sn": "ELITECH-9982", "reading_value": 8.0,
                          "metric_type": "battery_pct"}).json()
    assert body["battery_low"] is True
    assert "replace it" in body["action_taken_by_ai"]


def test_site_reconciliation_reports_flat_batteries(
    api, operator_factory, sensor_factory
):
    headers, _, _ = operator_factory()
    site = make_site(api, headers)
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    place(api, headers, site["site_id"], "FRZ-1")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 28.0,
                   "battery_percent": 5.0})

    body = api.get(f"/api/sites/{site['site_id']}/reconciliation",
                   headers=headers).json()
    assert body["low_battery"] == ["FRZ-1"]
    assert body["covered"] is True


def test_sites_survive_a_restart(tmp_path):
    from db import Database
    from store import HubStore

    path = str(tmp_path / "sites.db")
    first = HubStore(db=Database(path))
    tenant = first.create_tenant("A", "n", "+1", "a@example.com", "growth")
    site = first.create_site(tenant.tenant_id, "Boca Raton", "FL")
    sensor = first.register_sensor("FRZ-1", tenant.tenant_id, "restaurant", "Walk-In")
    first.assign_sensor_to_site(sensor, site.site_id)
    first.set_temperature_unit(tenant, "C")

    second = HubStore(db=Database(path))
    assert second.get_site(site.site_id).name == "Boca Raton"
    assert second.sensors_at_site(site.site_id)[0].sensor_id == "FRZ-1"
    assert second.get_tenant(tenant.tenant_id).temperature_unit == "C"
