"""What a credential inside a freezer is allowed to do.

A tenant API key is a master key: it registers assets, retunes alarm
thresholds and can suspend the licence. Until now it was also the only
credential a sensor could carry, which put all of that inside a box
bolted to the wall of a walk-in — reachable by anyone with a screwdriver
and a serial cable, in a room the public can often walk into.

So a sensor now gets its own key. These tests are about the boundary
between the two: what the scoped key can do, what it cannot, and that
closing the hole did not break the fleet installer that legitimately
holds the master.
"""

import pytest


@pytest.fixture()
def estate(api, operator_factory):
    """One tenant, two sensors, and the key each one was issued."""
    headers, tenant, _ = operator_factory()

    def register(sensor_id, serial=None):
        body = {
            "sensor_id": sensor_id,
            "industry_vertical": "pharmacy",
            "location_name": f"{sensor_id} bay",
        }
        if serial:
            body["external_device_sn"] = serial
        made = api.post("/api/licenses/me/sensors", headers=headers, json=body)
        assert made.status_code == 201, made.text
        return made.json()

    return {
        "operator": headers,
        "tenant": tenant,
        "a": register("FRIDGE-A", serial="SN-A"),
        "b": register("FRIDGE-B", serial="SN-B"),
    }


def test_registration_issues_a_key_and_shows_it_once(api, estate):
    """Like the tenant key: handed over once, never echoed again."""
    key = estate["a"]["ingest_key"]
    assert key and key.startswith("clx_snr_")
    assert key != estate["b"]["ingest_key"]

    listed = api.get("/api/licenses/me/sensors", headers=estate["operator"]).json()
    blob = str(listed)
    assert key not in blob, (
        "the sensor's credential is readable from the sensor list, which "
        "makes it not a credential"
    )
    detail = api.get("/api/console/sensor/FRIDGE-A", headers=estate["operator"])
    assert key not in detail.text


def test_a_sensor_key_can_report_for_its_own_asset(api, estate):
    resp = api.post(
        "/api/sensor-pulse",
        headers={"X-CyberLogix-Sensor-Key": estate["a"]["ingest_key"]},
        json={"sensor_id": "FRIDGE-A", "temperature_fahrenheit": 39.0},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "nominal"


def test_a_sensor_key_cannot_report_for_another_asset(api, estate):
    """The whole point of scoping it.

    Without this, one device token lifted off one freezer could write a
    comfortable 39°F for every other freezer in the estate — including
    one that is actually failing. That is not a data-integrity problem,
    it is a way to switch the alarm off silently.
    """
    resp = api.post(
        "/api/sensor-pulse",
        headers={"X-CyberLogix-Sensor-Key": estate["a"]["ingest_key"]},
        json={"sensor_id": "FRIDGE-B", "temperature_fahrenheit": 39.0},
    )
    assert resp.status_code == 403
    assert "FRIDGE-A" in resp.json()["detail"]


def test_a_sensor_key_cannot_touch_anything_but_readings(api, estate):
    """It reports. That is the entire list."""
    key = {"X-CyberLogix-Sensor-Key": estate["a"]["ingest_key"]}
    attempts = {
        "suspend the licence": api.post("/api/licenses/me/suspend", headers=key),
        "change the plan": api.post("/api/licenses/me/plan", headers=key,
                                    json={"plan": "trial"}),
        "register another sensor": api.post(
            "/api/licenses/me/sensors", headers=key,
            json={"sensor_id": "SNEAK-01", "industry_vertical": "pharmacy",
                  "location_name": "x"}),
        "retune the alarm": api.post(
            "/api/licenses/me/sensors/FRIDGE-A/thresholds", headers=key,
            json={"danger_above": 200.0}),
        "decommission itself": api.delete(
            "/api/licenses/me/sensors/FRIDGE-A", headers=key),
        "read the estate": api.get("/api/licenses/me/sensors", headers=key),
    }
    allowed = {what: r.status_code for what, r in attempts.items()
               if r.status_code not in (401, 403)}
    assert not allowed, f"a sensor key was able to: {allowed}"


def test_third_party_hardware_gets_the_same_boundary(api, estate):
    """The BYOD token travels in a request body and lands in a vendor's
    logs, so it should be worth as little as possible if it leaks."""
    own = api.post("/api/v1/bridge/sensor-webhook-ingest", json={
        "api_key_token": estate["a"]["ingest_key"], "device_sn": "SN-A",
        "metric_type": "temperature_f", "reading_value": 39.0})
    assert own.status_code == 200, own.text

    other = api.post("/api/v1/bridge/sensor-webhook-ingest", json={
        "api_key_token": estate["a"]["ingest_key"], "device_sn": "SN-B",
        "metric_type": "temperature_f", "reading_value": 39.0})
    assert other.status_code == 403, (
        "one leaked device token could write a comfortable reading for "
        "every other asset in the estate"
    )


def test_rotating_a_key_retires_the_old_one_immediately(api, estate):
    """For the day a device is replaced, sold on, or found with its case
    open. Re-keying one freezer, not an entire estate."""
    old = estate["a"]["ingest_key"]
    rotated = api.post("/api/licenses/me/sensors/FRIDGE-A/rotate-key",
                       headers=estate["operator"])
    assert rotated.status_code == 200, rotated.text
    new = rotated.json()["ingest_key"]
    assert new != old

    dead = api.post("/api/sensor-pulse", headers={"X-CyberLogix-Sensor-Key": old},
                    json={"sensor_id": "FRIDGE-A", "temperature_fahrenheit": 39.0})
    assert dead.status_code == 401

    live = api.post("/api/sensor-pulse", headers={"X-CyberLogix-Sensor-Key": new},
                    json={"sensor_id": "FRIDGE-A", "temperature_fahrenheit": 39.0})
    assert live.status_code == 200


def test_decommissioning_a_sensor_kills_its_key(api, estate):
    """A retired asset whose credential still resolves is a retired asset
    that can still write readings."""
    key = estate["a"]["ingest_key"]
    gone = api.delete("/api/licenses/me/sensors/FRIDGE-A",
                      headers=estate["operator"])
    assert gone.status_code == 200

    resp = api.post("/api/sensor-pulse", headers={"X-CyberLogix-Sensor-Key": key},
                    json={"sensor_id": "FRIDGE-A", "temperature_fahrenheit": 39.0})
    assert resp.status_code == 401


def test_the_fleet_installer_still_works(api, tenant_factory):
    """Closing the hole must not push people back to the master key by
    another route. A provisioning script holding the tenant key can still
    register and pulse anything in the estate."""
    key_headers, _ = tenant_factory()
    for i in range(3):
        made = api.post("/api/licenses/me/sensors", headers=key_headers,
                        json={"sensor_id": f"BULK-{i}",
                              "industry_vertical": "pharmacy",
                              "location_name": "Bay"})
        assert made.status_code == 201
    for i in range(3):
        resp = api.post("/api/sensor-pulse", headers=key_headers,
                        json={"sensor_id": f"BULK-{i}",
                              "temperature_fahrenheit": 39.0})
        assert resp.status_code == 200


def test_a_key_from_another_tenant_is_not_recognised(api, operator_factory):
    """The oldest question in the app, asked of the newest credential."""
    alpha, _, _ = operator_factory(email="alpha@example.com",
                                   company_name="Alpha Cold")
    beta, _, _ = operator_factory(email="beta@example.com",
                                  company_name="Beta Cold")
    a = api.post("/api/licenses/me/sensors", headers=alpha,
                 json={"sensor_id": "A-01", "industry_vertical": "pharmacy",
                       "location_name": "x"}).json()
    b = api.post("/api/licenses/me/sensors", headers=beta,
                 json={"sensor_id": "B-01", "industry_vertical": "pharmacy",
                       "location_name": "x"}).json()

    crossed = api.post(
        "/api/sensor-pulse",
        headers={"X-CyberLogix-Sensor-Key": a["ingest_key"]},
        json={"sensor_id": "B-01", "temperature_fahrenheit": 39.0})
    assert crossed.status_code == 403

    # And Beta's key cannot be rotated by Alpha.
    assert api.post("/api/licenses/me/sensors/B-01/rotate-key",
                    headers=alpha).status_code == 404
    assert b["ingest_key"]


def test_the_key_survives_a_restart(tmp_path):
    """An index rebuilt on boot, or a credential that stops working the
    first time the process is redeployed."""
    from db import Database
    from store import HubStore

    path = str(tmp_path / "keys.db")
    first = HubStore(db=Database(path))
    tenant = first.create_tenant("Acme", "D", "+15550100", "d@x.com",
                                 "enterprise")
    sensor = first.register_sensor("F-01", tenant.tenant_id, "pharmacy", "x")
    key = sensor.ingest_key
    assert key
    del first

    second = HubStore(db=Database(path))
    found = second.sensor_by_ingest_key(key)
    assert found is not None and found.sensor_id == "F-01"
    assert second.sensor_by_ingest_key("clx_snr_not-a-real-key") is None


def test_retiring_sensors_does_not_leak_the_key_index():
    """Not a security hole — a slow one.

    Removing a sensor already stops its key resolving, because the lookup
    goes through the sensor table. But the key -> sensor index is a
    separate dict, and an entry left in it is never read and never freed.
    A customer who cycles hardware through a site for a few years grows a
    map of dead credentials that only a restart clears.
    """
    from db import Database
    from store import HubStore

    store = HubStore(db=Database(":memory:"))
    tenant = store.create_tenant("Acme", "D", "+15550100", "d@x.com",
                                 "enterprise")
    for i in range(30):
        sensor = store.register_sensor(f"F-{i}", tenant.tenant_id, "pharmacy", "x")
        store.rotate_ingest_key(sensor)      # churn, as a replacement would
        store.remove_sensor(f"F-{i}")

    assert store.seat_count(tenant.tenant_id) == 0
    assert len(store._ingest_keys) == 0, (
        f"{len(store._ingest_keys)} dead credentials still indexed after "
        "30 sensors came and went"
    )
