"""The CSV compliance export."""

import csv
import io


def test_csv_has_a_row_per_sensor_and_a_total(
    api, tenant_factory, sensor_factory
):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="RACK-01")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")

    for temp in (68.0, 70.0, 94.0):
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": "RACK-01", "temperature_fahrenheit": temp})
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 28.0})

    resp = api.get("/api/autopilot/compliance.csv?days=7", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert ".csv" in resp.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(resp.text)))
    header = rows[0]
    assert header[0] == "Sensor"
    assert "Compliance %" in header

    body = {r[0]: r for r in rows[1:] if r and r[0] not in ("", "TOTAL")}
    assert set(body) == {"RACK-01", "FRZ-1"}
    assert body["RACK-01"][3] == "3"      # readings logged
    assert body["RACK-01"][5] == "1"      # excursions
    assert body["RACK-01"][12] == "no"    # not compliant
    assert body["FRZ-1"][12] == "yes"

    total = [r for r in rows if r and r[0] == "TOTAL"][0]
    assert total[3] == "4"                # 4 readings across the estate
    assert total[5] == "1"                # 1 excursion
    assert total[6] == "75.0"


def test_csv_is_empty_but_valid_with_no_sensors(api, tenant_factory):
    headers, _ = tenant_factory()
    resp = api.get("/api/autopilot/compliance.csv", headers=headers)
    assert resp.status_code == 200
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0][0] == "Sensor"
    total = [r for r in rows if r and r[0] == "TOTAL"][0]
    assert total[3] == "0"


def test_csv_requires_authentication(api):
    assert api.get("/api/autopilot/compliance.csv").status_code == 401
