"""Every sector, in both units, end to end.

Each vertical is a different sentence in every message the product
sends: its own name, its own limits, its own document. A new vertical
added to the profile table, or a limit typed in the wrong scale, breaks
exactly one of them and nothing else -- so every one is walked through
the same three steps, in a Fahrenheit account and in a Celsius one:

* the sensor is given limits that make sense for that trade,
* a reading past the limit opens an alert that names the trade and
  speaks the account's unit,
* the trade's own document renders with no placeholder left in it and
  no figure in the other scale.
"""

import re

import pytest

from store import INDUSTRY_PROFILES, STORE, to_celsius

SECTORS = sorted(INDUSTRY_PROFILES)
UNITS = ("F", "C")
OTHER = {"F": "°C", "C": "°F"}

# Bounds a profile's limits must sit inside, in Fahrenheit. The coldest
# thing anything here stores is vapour-phase nitrogen; the hottest is a
# battery enclosure in the sun. A limit outside this was typed in the
# wrong scale or with a slip of the finger.
COLDEST_F, HOTTEST_F = -330.0, 160.0


def _breach_reading_f(vertical):
    """A reading clearly past this sector's limit, in Fahrenheit."""
    profile = INDUSTRY_PROFILES[vertical]
    if profile["danger_above"] is not None:
        return profile["danger_above"] + 12.0
    return profile["danger_below"] - 12.0


def _estate(api, tenant_factory, sensor_factory, vertical, unit):
    headers, tenant = tenant_factory(plan="enterprise",
                                     company_name=f"{vertical} {unit} Co")
    if unit == "C":
        resp = api.post("/api/licenses/me/temperature-unit", headers=headers,
                        json={"temperature_unit": "C"})
        assert resp.status_code == 200, resp.text
    sensor_factory(headers, sensor_id=f"S-{vertical}"[:40], vertical=vertical,
                   location=f"{INDUSTRY_PROFILES[vertical]['asset_noun']} one")
    return headers, tenant


def _no_other_scale(text, unit, where):
    assert OTHER[unit] not in text, f"{where} quotes {OTHER[unit]}: {text!r}"
    # A bare "degrees F" spelt out counts too.
    spelt = "Celsius" if unit == "F" else "Fahrenheit"
    assert spelt not in text, f"{where} spells out {spelt}: {text!r}"


def _clean(text, where):
    assert "None" not in text, f"{where} printed None: {text!r}"
    assert "{" not in text and "}" not in text, (
        f"{where} left a template brace in: {text!r}")
    assert "nan" not in text.lower().split(), f"{where} printed nan"


# ---- the profile itself ---------------------------------------------------


@pytest.mark.parametrize("vertical", SECTORS)
def test_the_profile_limits_are_plausible_for_the_trade(vertical):
    profile = INDUSTRY_PROFILES[vertical]
    above, below = profile["danger_above"], profile["danger_below"]

    assert above is not None or below is not None, (
        f"{vertical} has no limit at all, so nothing in it can ever alert")
    for limit in (above, below):
        if limit is not None:
            assert COLDEST_F <= limit <= HOTTEST_F, (
                f"{vertical} limit {limit}°F is not a temperature this trade "
                "stores at; was it typed in Celsius?")
    if above is not None and below is not None:
        assert below < above, f"{vertical}'s band is upside down"
    for key in ("name", "catastrophe", "asset_noun", "asset_plural",
                "shortcut_name", "shortcut_description"):
        assert profile.get(key), f"{vertical} has no {key}"


# ---- the three steps, per sector and per unit ----------------------------


@pytest.mark.parametrize("unit", UNITS)
@pytest.mark.parametrize("vertical", SECTORS)
def test_a_sector_sensor_gets_its_limits_in_the_accounts_unit(
    api, tenant_factory, sensor_factory, vertical, unit
):
    headers, _ = _estate(api, tenant_factory, sensor_factory, vertical, unit)
    profile = INDUSTRY_PROFILES[vertical]

    body = api.get("/api/licenses/me/sensors", headers=headers).json()
    assert body["temperature_unit"] == unit
    (sensor,) = body["sensors"]

    assert sensor["industry_vertical"] == vertical
    assert sensor["danger_above"] == profile["danger_above"]
    assert sensor["danger_below"] == profile["danger_below"]
    for side in ("above", "below"):
        stored = profile[f"danger_{side}"]
        shown = sensor[f"danger_{side}_display"]
        if stored is None:
            assert shown is None
        elif unit == "C":
            assert shown == pytest.approx(to_celsius(stored), abs=0.01)
        else:
            assert shown == pytest.approx(stored)


@pytest.mark.parametrize("unit", UNITS)
@pytest.mark.parametrize("vertical", SECTORS)
def test_a_breach_alert_names_the_sector_and_speaks_the_unit(
    api, tenant_factory, sensor_factory, break_gemini, vertical, unit
):
    """The plain template, which is what goes out whenever the model is
    unavailable -- and so is the one text guaranteed to be sent."""
    break_gemini("missing")
    headers, tenant = _estate(api, tenant_factory, sensor_factory, vertical,
                              unit)
    profile = INDUSTRY_PROFILES[vertical]
    reading_f = _breach_reading_f(vertical)
    packet = {"sensor_id": f"S-{vertical}"[:40]}
    if unit == "C":
        packet["temperature_celsius"] = to_celsius(reading_f)
    else:
        packet["temperature_fahrenheit"] = reading_f

    resp = api.post("/api/sensor-pulse", headers=headers, json=packet)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["status"] == "CRITICAL_CATASTROPHE_TRIGGERED", body
    assert body["temperature_unit"] == unit
    assert body["current_temperature"] == pytest.approx(
        to_celsius(reading_f) if unit == "C" else reading_f, abs=0.02)

    sms = body["dispatched_sms_text"]
    assert profile["name"] in sms, f"the alert does not name {vertical}: {sms}"
    assert f"°{unit}" in sms
    _no_other_scale(sms, unit, "the alert text")
    _clean(sms, "the alert text")

    detail = body["breach_details"]
    assert f"°{unit}" in detail
    _no_other_scale(detail, unit, "the breach detail")
    _clean(detail, "the breach detail")

    incident = STORE.get_incident(body["incident_id"])
    assert incident.industry_vertical == vertical
    assert incident.catastrophe == profile["catastrophe"]

    # The escalation call speaks the same unit, spelt out for the voice.
    from voice_dispatch import build_voice_script

    script, _ = build_voice_script(incident, STORE.get_tenant(tenant["tenant_id"]))
    assert ("Celsius" if unit == "C" else "Fahrenheit") in script
    _no_other_scale(script, unit, "the voice script")
    _clean(script, "the voice script")


@pytest.mark.parametrize("unit", UNITS)
@pytest.mark.parametrize("vertical", SECTORS)
def test_the_sector_document_renders_cleanly_in_the_accounts_unit(
    api, tenant_factory, sensor_factory, break_gemini, vertical, unit
):
    break_gemini("missing")
    headers, _ = _estate(api, tenant_factory, sensor_factory, vertical, unit)
    profile = INDUSTRY_PROFILES[vertical]
    sensor_id = f"S-{vertical}"[:40]
    safe_f = (
        (profile["danger_above"] - 5.0) if profile["danger_above"] is not None
        else profile["danger_below"] + 5.0
    )
    for reading in (safe_f, _breach_reading_f(vertical), safe_f):
        assert api.post("/api/sensor-pulse", headers=headers, json={
            "sensor_id": sensor_id, "temperature_fahrenheit": reading,
        }).status_code == 200

    resp = api.post(f"/api/shortcuts/{vertical}", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    document = body["document"]

    assert body["industry"] == profile["name"]
    assert profile["shortcut_name"] in document
    assert profile["name"] in document
    assert sensor_id in document
    assert f"°{unit}" in document
    _no_other_scale(document, unit, f"the {vertical} document")
    _clean(document, f"the {vertical} document")

    evidence = body["evidence"]
    assert evidence["temperature_unit"] == unit
    (row,) = evidence["sensors"]
    assert row["readings"] == 3 and row["excursions"] == 1
    expected_max = _breach_reading_f(vertical) if profile["danger_above"] is not None else safe_f
    shown_max = to_celsius(expected_max) if unit == "C" else expected_max
    assert row["max"] == pytest.approx(shown_max, abs=0.02)


@pytest.mark.parametrize("vertical", SECTORS)
def test_a_unit_switch_after_an_alert_leaves_no_old_scale_in_the_document(
    api, tenant_factory, sensor_factory, break_gemini, vertical
):
    """The incident line in the document quotes the breach as it was
    worded on the night. An account that moves to Celsius the next week
    then hands an inspector a Celsius document with one line in
    Fahrenheit in the middle of it."""
    break_gemini("missing")
    headers, _ = _estate(api, tenant_factory, sensor_factory, vertical, "F")
    sensor_id = f"S-{vertical}"[:40]
    assert api.post("/api/sensor-pulse", headers=headers, json={
        "sensor_id": sensor_id,
        "temperature_fahrenheit": _breach_reading_f(vertical),
    }).json()["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"

    api.post("/api/licenses/me/temperature-unit", headers=headers,
             json={"temperature_unit": "C"})
    body = api.post(f"/api/shortcuts/{vertical}", headers=headers).json()

    _no_other_scale(body["document"], "C", f"the {vertical} document")
    for incident in body["evidence"]["incidents"]:
        _no_other_scale(incident["detail"], "C", "the incident detail")
        limit = re.findall(r"(-?\d+(?:\.\d+)?)°C limit", incident["detail"])
        above, below = (INDUSTRY_PROFILES[vertical]["danger_above"],
                        INDUSTRY_PROFILES[vertical]["danger_below"])
        assert limit and float(limit[0]) == pytest.approx(
            to_celsius(above if above is not None else below), abs=0.01)


@pytest.mark.parametrize("vertical", SECTORS)
def test_the_public_industry_page_reads_cleanly(api, vertical):
    body = api.get(f"/api/for/{vertical}").json()
    text = " ".join(str(v) for v in body.values())
    _clean(text, f"the {vertical} page")
    assert body["name"] == INDUSTRY_PROFILES[vertical]["name"]
