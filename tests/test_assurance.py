"""The loss assurance add-on.

A guarantee is only defensible if its exclusions are visible before an
event rather than produced afterwards, and only honest if we publish our
own miss rate. Both are what these cover.
"""


def paid_estate(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 28.0,
                   "battery_percent": 90.0})
    api.post("/api/contacts", headers=headers,
             json={"full_name": "Dana Reyes", "phone": "+15550100"})
    return headers


def test_cover_is_in_force_for_a_healthy_estate(
    api, operator_factory, sensor_factory
):
    headers = paid_estate(api, operator_factory, sensor_factory)
    body = api.get("/api/assurance/cover", headers=headers).json()
    assert body["in_force"] is True
    assert body["covered_units"] == 1
    assert body["excluded_units"] == 0
    assert body["monthly_cost_usd"] == 149.0


def test_a_flat_battery_is_excluded_and_says_why(
    api, operator_factory, sensor_factory
):
    """The point of the product: told this morning, not at claim time."""
    headers = paid_estate(api, operator_factory, sensor_factory)
    sensor_factory(headers, sensor_id="FRZ-2", vertical="restaurant")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-2", "temperature_fahrenheit": 28.0,
                   "battery_percent": 8.0})

    body = api.get("/api/assurance/cover", headers=headers).json()
    excluded = {row["sensor_id"]: row["reasons"] for row in body["excluded"]}
    assert "FRZ-2" in excluded
    assert any("battery" in reason for reason in excluded["FRZ-2"])
    assert body["covered_units"] == 1


def test_an_empty_roster_blocks_the_whole_estate(
    api, operator_factory, sensor_factory
):
    """An alert nobody receives cannot be guaranteed."""
    headers, _, _ = operator_factory(plan="enterprise")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 28.0})

    body = api.get("/api/assurance/cover", headers=headers).json()
    roster = next(c for c in body["estate_checks"]
                  if c["check"] == "on_call_roster")
    assert roster["passed"] is False
    assert "Add at least one person" in roster["detail"]
    assert body["in_force"] is False


def test_a_trial_estate_is_not_covered(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory(plan="trial")
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 28.0})
    api.post("/api/contacts", headers=headers,
             json={"full_name": "Dana", "phone": "+15550100"})

    body = api.get("/api/assurance/cover", headers=headers).json()
    assert body["in_force"] is False
    assert any(c["check"] == "paid_plan" and not c["passed"]
               for c in body["estate_checks"])


def test_the_price_is_flat_not_a_share_of_savings(
    api, operator_factory, sensor_factory
):
    """The whole reason this shape was chosen: a quiet year still pays."""
    headers = paid_estate(api, operator_factory, sensor_factory)
    for n in range(3):
        sensor_factory(headers, sensor_id=f"EXTRA-{n}", vertical="restaurant")
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": f"EXTRA-{n}",
                       "temperature_fahrenheit": 28.0, "battery_percent": 90.0})

    quote = api.get("/api/assurance/quote", headers=headers).json()
    assert quote["covered_units"] == 4
    assert quote["monthly_usd"] == 4 * 149.0
    assert quote["annual_usd"] == 4 * 149.0 * 12
    # No incidents at all, and it still bills.
    assert quote["monthly_usd"] > 0


def test_we_publish_our_own_miss_rate(api, operator_factory, sensor_factory):
    """A guarantee from a vendor who hides their misses is a slogan."""
    headers = paid_estate(api, operator_factory, sensor_factory)
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 55.0})

    body = api.get("/api/assurance/performance", headers=headers).json()
    assert body["incidents"] == 1
    # Twilio is not configured in the suite, so this alert never left.
    assert body["alerts_missed"] == 1
    assert body["delivery_rate_percent"] == 0.0
    assert body["misses"][0]["alert_attempted"] is True


def test_a_delivered_alert_counts_as_met(
    api, operator_factory, sensor_factory, configured_twilio
):
    configured_twilio()
    headers = paid_estate(api, operator_factory, sensor_factory)
    api.post("/api/sensor-pulse", headers=headers,
             json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 55.0})

    body = api.get("/api/assurance/performance", headers=headers).json()
    assert body["alerts_delivered"] == 1
    assert body["delivery_rate_percent"] == 100.0
