"""Sensors you already own, sending what they already send.

The bridge accepted third-party hardware, and its docstring said any
off-the-shelf sensor could "POST its raw JSON straight to CyberLogix".
It could not: the endpoint required device_sn, reading_value and
metric_type, and no Monnit, SensorPush, Elitech or Dickson device has
ever sent those. The customer had to build the translator the claim
promised to remove.

This matters more than a convenience. Every hardware vendor in this
market sells the software that watches their own sensors -- buy Monnit,
use iMonnit -- and the switching cost is the hardware already screwed to
the wall. An estate that can keep its sensors has nothing to cross.

The vendor shapes below are taken from working open-source integrations
and published API models rather than invented, but they are still not a
device on a bench, which is exactly why detection and a
paste-your-own-payload mapping exist alongside the presets.
"""

import pytest

import byod


# --- real shapes, from real integrations ---------------------------------

MONNIT = {
    "SensorID": "556677",
    "SensorName": "Walk-In Freezer",
    "MessageDate": "2026-09-18T21:40:00",
    "DataMessageGUID": "b2a1-44",
    "DataValues": "-2.5",
    "DataTypes": "TemperatureC",
    "Battery": "84",
    "SignalStrength": "72",
    "GatewayID": "9001",
}

SENSORPUSH = {
    "sensor": "SP-11223344",
    "observed": "2026-09-18T21:40:00.000Z",
    "temperature": 38.4,
    "humidity": 52.1,
    "dewpoint": 21.0,
}

# A gateway that wraps the reading, which is the common real-world case.
NESTED = {
    "event": "reading",
    "device": {"serial": "ELI-99", "firmware": "1.4"},
    "data": [{"temp_c": 3.9, "recorded_at": "2026-09-18T21:40:00Z"}],
}


def test_a_monnit_payload_is_understood():
    """Field names from working open-source iMonnit integrations."""
    read = byod.detect(MONNIT)

    assert read["understood"], read["missing"]
    assert read["serial"] == "556677"
    assert read["value"] == -2.5
    assert read["preset"] == "Monnit / iMonnit"


def test_a_sensorpush_payload_is_understood():
    """Field names from the published SensorPush sample model."""
    read = byod.detect(SENSORPUSH)

    assert read["understood"], read["missing"]
    assert read["serial"] == "SP-11223344"
    assert read["value"] == 38.4
    assert read["unit"] == "temperature_f"


def test_a_shape_nobody_wrote_a_preset_for_is_still_read():
    """The preset list will always be behind the market. Detection is
    what makes the claim true for the vendor we have not heard of."""
    read = byod.detect(NESTED)

    assert read["understood"], read["missing"]
    assert read["serial"] == "ELI-99"
    assert read["value"] == 3.9
    assert read["unit"] == "temperature_c", "temp_c names its own unit"
    assert read["source"] == "detected"


def test_a_reading_sent_as_a_string_with_its_unit_inside():
    """Gateways really do send "72.5 F" in one field."""
    read = byod.detect({"sensorId": "A1", "currentReading": "72.5 F"})

    assert read["value"] == 72.5
    assert read["unit"] == "temperature_f"


def test_the_unit_is_never_guessed_from_the_number():
    """4 degrees is a fridge in Celsius and a disaster in Fahrenheit.
    A wrong guess here is a silent one, so there is no guess."""
    read = byod.detect({"serial": "A1", "value": 4.0})

    assert read["value"] == 4.0
    assert read["unit"] is None
    assert "Celsius" in read["note"]


def test_a_non_finite_reading_is_refused():
    """A flat battery mid-conversion is a classic way to emit one, and
    scored against a threshold it reads as nominal."""
    assert byod.as_number(float("nan")) is None
    assert byod.as_number(float("inf")) is None
    read = byod.detect({"serial": "A1", "temperature": "NaN"})
    assert not read["understood"]


def test_a_payload_with_no_serial_says_what_is_missing():
    read = byod.detect({"temperature": 40.0})

    assert not read["understood"]
    assert any("serial" in m for m in read["missing"])


def test_it_says_which_field_it_used():
    """So a mapping that picked the wrong key is visible rather than
    mysterious."""
    read = byod.detect(NESTED)

    assert read["fields_used"]["serial"] == "device.serial"
    assert read["fields_used"]["value"] == "data[0].temp_c"


def test_every_preset_says_what_it_was_checked_against():
    """A preset is a claim about firmware we have never seen. An
    unqualified claim is a trap."""
    for key, preset in byod.PRESETS.items():
        assert preset["checked_against"], key


# --- end to end, through the open endpoint --------------------------------


@pytest.fixture()
def estate(api, tenant_factory, owner_headers):
    headers, tenant = tenant_factory(plan="enterprise", company_name="Harbor")
    owner = owner_headers(headers)
    made = api.post("/api/licenses/me/sensors", headers={**headers, **owner},
                    json={"sensor_id": "FRZ-1", "industry_vertical": "restaurant",
                          "location_name": "Walk-in",
                          "external_device_sn": "556677"})
    assert made.status_code == 201, made.text
    from store import STORE
    return {"X-CyberLogix-Key": STORE.get_tenant(tenant["tenant_id"]).api_key}


def test_a_monnit_device_reaches_the_estate_untranslated(api, estate):
    """The whole claim, end to end: the vendor's own payload, unmodified,
    at a URL, and a reading lands on the sensor."""
    sent = api.post("/api/v1/bridge/any", json={**MONNIT, "DataValues": "-2.5"},
                    headers=estate)

    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["understood_as"]["serial"] == "556677"
    assert body["understood_as"]["using"] == "Monnit / iMonnit"


def test_an_unreadable_payload_is_kept_and_explained(api, estate):
    refused = api.post("/api/v1/bridge/any", json={"hello": "world"},
                       headers=estate)

    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert detail["missing"]
    assert "hello" in detail["looked_at"]


def test_what_the_device_sent_is_kept_even_when_it_failed(api, estate):
    """The answer to "why is my sensor not showing up" should be a fact."""
    api.post("/api/v1/bridge/any", json={"hello": "world"}, headers=estate)

    kept = api.get("/api/v1/bridge/samples", headers=estate).json()

    assert kept["count"] == 1
    assert kept["samples"][0]["payload"] == {"hello": "world"}
    assert kept["samples"][0]["outcome"] == "not_understood"


def test_a_payload_can_be_checked_before_a_device_is_pointed_anywhere(
    api, estate
):
    """Wiring up hardware is a loop of send-and-guess, and every vendor
    makes it worse by showing nothing."""
    seen = api.post("/api/v1/bridge/preview", headers=estate,
                    json={"payload": SENSORPUSH})

    assert seen.status_code == 200, seen.text
    body = seen.json()
    assert body["understood"] is True
    assert body["serial"] == "SP-11223344"
    assert "observed" in body["every_field_seen"]
    assert any(p["key"] == "monnit" for p in body["presets"])


def test_the_open_endpoint_still_needs_a_credential(api):
    assert api.post("/api/v1/bridge/any", json=MONNIT).status_code == 401
