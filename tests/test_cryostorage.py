"""The first asset the product watches below zero.

Every other vertical is a fridge, a cellar or a room — all of them
comfortably positive in Fahrenheit. Cryostorage sits near −196°C, which
means a stray `max(0, ...)`, an `abs()`, or an assumption that a colder
reading is a safer one would pass every other test in this suite and be
wrong only here, on the one asset that cannot be replaced at any price.
"""

import pytest

from store import INDUSTRY_PROFILES, display_temperature, evaluate_breach

# −130°C is where water in a cell stops being glass and starts being ice.
GLASS_TRANSITION_F = -202.0
LIQUID_NITROGEN_F = -320.8


def test_the_alarm_leaves_room_to_act_before_ice_forms():
    """A threshold set at the point of damage is a threshold set too late."""
    profile = INDUSTRY_PROFILES["cryostorage"]
    alarm = profile["danger_above"]

    assert alarm < GLASS_TRANSITION_F, (
        "the alarm fires at or after the temperature that destroys the "
        "samples, which leaves nobody any time to do anything about it"
    )
    margin_c = (GLASS_TRANSITION_F - alarm) * 5 / 9
    assert margin_c >= 15, f"only {margin_c:.0f}°C of warning"


def test_a_healthy_tank_is_not_scored_as_a_breach():
    """Liquid nitrogen is colder than anything else the product has seen."""
    assert evaluate_breach("cryostorage", LIQUID_NITROGEN_F) is None
    assert evaluate_breach("cryostorage", -260.0) is None
    assert evaluate_breach("cryostorage", -239.0) is None


def test_a_warming_tank_is():
    verdict = evaluate_breach("cryostorage", -200.0)
    assert verdict is not None
    assert "high" in verdict.lower()


def test_a_probe_reading_colder_than_liquid_nitrogen_is_a_fault():
    """The dangerous failure here reads as permanently safe.

    Nothing inside a nitrogen dewar can be colder than liquid nitrogen at
    −320.8°F. A probe reporting −400°F has failed, and a failed probe on
    this asset is worse than a warm one, because every screen stays green
    while the tank does whatever it likes.
    """
    assert evaluate_breach("cryostorage", -400.0) is not None
    assert evaluate_breach("cryostorage", LIQUID_NITROGEN_F) is None


def test_the_numbers_survive_the_trip_into_celsius():
    """An embryology lab does not think in Fahrenheit.

    Conversion is signed arithmetic, so this is where an `abs()` would
    show itself.
    """
    profile = INDUSTRY_PROFILES["cryostorage"]
    assert display_temperature(LIQUID_NITROGEN_F, "C") == pytest.approx(-196.0, abs=0.05)
    assert display_temperature(profile["danger_above"], "C") == pytest.approx(-150.0, abs=0.05)
    assert display_temperature(-200.0, "C") == pytest.approx(-128.89, abs=0.05)


def test_a_tank_runs_the_whole_ladder(api, operator_factory, sensor_factory,
                                      break_gemini):
    """End to end, in the unit the customer actually uses.

    Gemini is deliberately switched off, so the alert is the deterministic
    template rather than a stubbed sentence. That template is the one that
    actually goes out on the night the model is unavailable, and it is the
    only one whose wording this project controls.
    """
    break_gemini("outage")
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="DEWAR-01", vertical="cryostorage",
                   location="Cryo room / Tank 1")
    assert api.post("/api/licenses/me/temperature-unit", headers=headers,
                    json={"temperature_unit": "C"}).status_code == 200

    calm = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "DEWAR-01",
                          "temperature_fahrenheit": LIQUID_NITROGEN_F})
    assert calm.json()["status"] == "nominal"
    assert calm.json()["current_temperature"] == pytest.approx(-196.0, abs=0.05)

    breach = api.post("/api/sensor-pulse", headers=headers,
                      json={"sensor_id": "DEWAR-01",
                            "temperature_fahrenheit": -200.0}).json()
    assert breach["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert breach["temperature_unit"] == "C"
    # The number the lab is woken with must be the one they recognise.
    assert breach["current_temperature"] == pytest.approx(-128.89, abs=0.05)
    text = breach["dispatched_sms_text"]
    assert "-128" in text, f"the alert does not name the reading: {text!r}"
    assert "°C" in text, "the alert quotes a unit the lab does not use"
    assert "IVF" in text and "DEWAR-01" in text


def test_the_sector_is_priced_and_named_like_the_others(api):
    """A vertical the rate card does not know about cannot be sold."""
    from pricing import PRICE_BOOK

    assert set(PRICE_BOOK) == set(INDUSTRY_PROFILES), (
        "a vertical exists with no price, or a price with no vertical"
    )
    entry = PRICE_BOOK["cryostorage"]
    assert entry["unit"] == "tank"
    assert entry["monthly_usd"] > 0

    listed = api.get("/api/industries").json()["industries"]
    row = next(i for i in listed if i["vertical"] == "cryostorage")
    assert row["name"] == "IVF Clinics & Cryostorage"
    assert row["asset_noun"] == "tank"
    assert row["monthly_usd"] == entry["monthly_usd"]


def test_every_vertical_is_complete(api):
    """Adding the twelfth should not leave a hole any of the eleven filled.

    Written generically on purpose: it is the thirteenth that this is
    really for.
    """
    from pricing import PRICE_BOOK

    required = ("name", "catastrophe", "unit", "asset_noun", "asset_plural",
                "shortcut_name", "shortcut_description")
    for key, profile in INDUSTRY_PROFILES.items():
        missing = [f for f in required if not profile.get(f)]
        assert not missing, f"{key} is missing {missing}"
        assert profile.get("danger_above") is not None or \
               profile.get("danger_below") is not None, \
               f"{key} has no threshold at all, so nothing can ever breach"
        above, below = profile.get("danger_above"), profile.get("danger_below")
        if above is not None and below is not None:
            assert below < above, f"{key} thresholds are inverted"
        assert key in PRICE_BOOK, f"{key} has no price"
