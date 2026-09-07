"""The compliance CSV is opened in Excel by the person it is meant to
reassure.

A cell beginning =, +, -, @ or whitespace is a formula to a spreadsheet.
`=HYPERLINK("http://evil","Click for your refund")` in a sensor name
becomes a live phishing link inside our own compliance document, handed
to an auditor by us. The DDE forms go further.
"""

import csv
import io


TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def export(api, headers, days=7):
    resp = api.get(f"/api/autopilot/compliance.csv?days={days}", headers=headers)
    assert resp.status_code == 200
    return list(csv.reader(io.StringIO(resp.text)))


def test_no_cell_can_execute_in_a_spreadsheet(api, operator_factory):
    payloads = {
        "S1": '=HYPERLINK("http://evil.example","Click for your refund")',
        "S2": "+1234567890",
        "S3": "-2+3+cmd|' /c calc'!A0",
        "S4": "@SUM(1+9)*cmd|' /c calc'!A0",
        "S5": "\t=1+1",
        "S6": "\r=1+1",
    }
    headers, _, _ = operator_factory(company_name="=cmd|'/c calc'!A1")
    for sensor_id, name in payloads.items():
        api.post("/api/licenses/me/sensors", headers=headers,
                 json={"sensor_id": sensor_id, "industry_vertical": "restaurant",
                       "location_name": name})
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": sensor_id, "temperature_fahrenheit": 28.0})

    live = [
        cell for row in export(api, headers)
        for cell in row if cell[:1] in TRIGGERS
    ]
    assert not live, f"cells a spreadsheet would execute: {live}"


def test_the_original_text_is_still_readable(api, operator_factory):
    """Neutralised, not censored: the inspector must see the real name."""
    headers, _, _ = operator_factory()
    api.post("/api/licenses/me/sensors", headers=headers,
             json={"sensor_id": "S1", "industry_vertical": "restaurant",
                   "location_name": "=Walk-In Freezer"})
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "S1", "temperature_fahrenheit": 28.0})

    cells = [c for row in export(api, headers) for c in row]
    assert "'=Walk-In Freezer" in cells


def test_numbers_are_left_alone(api, operator_factory, sensor_factory):
    """Quoting a temperature would break the arithmetic the file is for.

    A negative reading is the case that matters: it begins with a minus,
    which is a formula trigger, and freezers report negative numbers all
    day.
    """
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": -12.5})

    rows = export(api, headers)
    data = next(r for r in rows if r and r[0] == "FRZ-1")
    # Min/mean/max sit at indices 7, 8, 9 and must still parse as numbers.
    for index in (7, 8, 9):
        assert float(data[index]) == -12.5, (
            f"column {index} is {data[index]!r}, which Excel would read as text"
        )


def test_an_ordinary_export_is_unchanged(api, operator_factory,
                                         sensor_factory):
    """The mitigation must not touch a normal file."""
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 28.0})

    rows = export(api, headers)
    data = next(r for r in rows if r and r[0] == "FRZ-1")
    assert "'" not in data[0] and "'" not in data[1]
