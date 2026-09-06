"""The console read model: units, sites, health, and one sensor in full."""


def breach(api, headers, sensor_factory, sensor_id="FRZ-1"):
    sensor_factory(headers, sensor_id=sensor_id, vertical="restaurant")
    return api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": sensor_id, "temperature_fahrenheit": 70.0,
                          "battery_percent": 11.0, "signal_percent": 44.0}).json()


def test_the_overview_speaks_the_tenants_unit(
    api, operator_factory, sensor_factory
):
    """One chart in Celsius and one in Fahrenheit is how 4° reads as safe."""
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 68.0})
    api.post("/api/licenses/me/temperature-unit", headers=headers,
             json={"temperature_unit": "C"})

    body = api.get("/api/console/overview", headers=headers).json()
    assert body["temperature_unit"] == "C"
    row = body["sensors"][0]
    assert row["last_temperature_display"] == 20.0
    assert row["spark"] == [20.0]
    assert body["incidents"][0]["temperature_display"] == 20.0


def test_the_overview_carries_sites_and_health(
    api, operator_factory, sensor_factory
):
    headers, _, _ = operator_factory()
    site = api.post("/api/sites", headers=headers,
                    json={"name": "Boca"}).json()
    breach(api, headers, sensor_factory)
    api.post(f"/api/sites/{site['site_id']}/sensors", headers=headers,
             json={"sensor_id": "FRZ-1"})
    sensor_factory(headers, sensor_id="FRZ-2", vertical="restaurant")

    body = api.get("/api/console/overview", headers=headers).json()
    assert body["summary"]["low_battery"] == 1
    assert body["summary"]["unplaced_sensors"] == 1
    assert body["sites"][0]["name"] == "Boca"
    assert body["sites"][0]["sensor_count"] == 1
    placed = next(s for s in body["sensors"] if s["sensor_id"] == "FRZ-1")
    assert placed["site_name"] == "Boca"
    assert placed["battery_low"] is True


def test_one_sensor_in_full(api, operator_factory, sensor_factory):
    """The card answers "is it alright"; this answers "what happened"."""
    headers, _, _ = operator_factory()
    body = breach(api, headers, sensor_factory)

    detail = api.get("/api/console/sensor/FRZ-1", headers=headers).json()
    assert detail["sensor"]["sensor_id"] == "FRZ-1"
    assert detail["readings"][-1]["breached"] is True
    assert detail["readings_total"] == 1
    assert detail["open_incidents"] == 1
    assert detail["incidents"][0]["incident_id"] == body["incident_id"]
    assert detail["forecast"] is not None


def test_another_tenants_sensor_is_not_readable(
    api, operator_factory, sensor_factory
):
    theirs, _, _ = operator_factory(company_name="Acme", email="a@x.com")
    sensor_factory(theirs, sensor_id="THEIRS-1", vertical="restaurant")

    mine, _, _ = operator_factory(company_name="Beta", email="b@x.com")
    assert api.get("/api/console/sensor/THEIRS-1", headers=mine).status_code == 404


def test_the_estate_is_described_in_its_own_words(
    api, operator_factory, sensor_factory
):
    """A yacht owner paying $4,999 a vessel should never read "sensor"."""
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="ENG-1", vertical="superyacht")
    sensor_factory(headers, sensor_id="ENG-2", vertical="superyacht")

    body = api.get("/api/console/overview", headers=headers).json()
    assert body["fleet_noun"] == "engine bay"
    assert body["fleet_plural"] == "engine bays"
    assert body["sensors"][0]["asset_noun"] == "engine bay"


def test_a_mixed_estate_falls_back_to_something_neutral(
    api, operator_factory, sensor_factory
):
    """Half yachts and half data halls have no shared word but "asset"."""
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="ENG-1", vertical="superyacht")
    sensor_factory(headers, sensor_id="RACK-1", vertical="cybersecurity")

    body = api.get("/api/console/overview", headers=headers).json()
    assert body["fleet_noun"] == "asset"
    assert body["fleet_plural"] == "assets"


def test_every_sector_names_the_thing_it_watches():
    """A missing noun would print "undefined" on somebody's wall display."""
    from store import INDUSTRY_PROFILES

    for key, profile in INDUSTRY_PROFILES.items():
        assert profile["asset_noun"], key
        assert profile["asset_plural"], key
        assert profile["asset_noun"] != profile["asset_plural"], key
