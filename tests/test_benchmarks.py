"""Anonymised benchmarks and equipment intelligence.

The commercial value depends entirely on the privacy floor holding. A
benchmark that lets one participant read a named competitor's figures
would end the business that produced it, so most of these test the
refusal rather than the number.
"""


def operator_with_readings(api, operator_factory, sensor_factory, index,
                           vertical="restaurant", temps=(28.0, 29.0),
                           serial=None):
    headers, _, _ = operator_factory(
        company_name=f"Chain {index}", email=f"op{index}@example.com")
    payload = {"sensor_id": f"FRZ-{index}", "industry_vertical": vertical,
               "location_name": f"Store {index} / Walk-In"}
    if serial:
        payload["external_device_sn"] = serial
    api.post("/api/licenses/me/sensors", headers=headers, json=payload)
    for temp in temps:
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": f"FRZ-{index}",
                       "temperature_fahrenheit": temp})
    return headers


def test_a_thin_cohort_publishes_nothing(
    api, operator_factory, sensor_factory
):
    """With four operators, a participant subtracts themselves and reads
    a competitor straight off."""
    heads = [operator_with_readings(api, operator_factory, sensor_factory, i)
             for i in range(4)]
    body = api.get("/api/benchmarks/restaurant", headers=heads[0]).json()

    assert body["available"] is False
    assert body["cohort_size"] == 4
    assert body["minimum_cohort"] == 5
    assert "cohort" not in body
    assert "work out a named competitor" in body["reason"]


def test_a_full_cohort_publishes_a_distribution(
    api, operator_factory, sensor_factory
):
    heads = [operator_with_readings(api, operator_factory, sensor_factory, i)
             for i in range(6)]
    body = api.get("/api/benchmarks/restaurant", headers=heads[0]).json()

    assert body["available"] is True
    assert body["cohort_size"] == 6
    assert body["cohort"]["mean_temperature"]["median"] is not None
    assert body["you"]["units"] == 1
    assert body["you"]["standing"]["uptime"]["percentile"] is not None


def test_no_customer_is_ever_named(api, operator_factory, sensor_factory):
    """The one thing that must never appear in this response."""
    heads = [operator_with_readings(api, operator_factory, sensor_factory, i)
             for i in range(6)]
    raw = api.get("/api/benchmarks/restaurant", headers=heads[0]).text

    for index in range(6):
        assert f"Chain {index}" not in raw
        assert f"op{index}@example.com" not in raw
    assert "TEN-" not in raw


def test_a_customer_sees_where_they_sit(api, operator_factory, sensor_factory):
    """The reason it renews: a number they cannot get anywhere else."""
    for i in range(5):
        operator_with_readings(api, operator_factory, sensor_factory, i,
                               temps=(28.0, 28.2))
    # A sixth running warm, and breaching.
    warm = operator_with_readings(api, operator_factory, sensor_factory, 9,
                                  temps=(28.0, 45.0))

    body = api.get("/api/benchmarks/restaurant", headers=warm).json()
    assert body["you"]["excursion_rate_percent"] == 50.0
    assert body["you"]["standing"]["excursion_rate"]["reading"] in {
        "bottom quartile", "below average"
    }


def test_an_unknown_vertical_is_refused(api, operator_factory):
    headers, _, _ = operator_factory()
    assert api.get("/api/benchmarks/submarines",
                   headers=headers).status_code == 400


def test_equipment_findings_need_a_population(
    api, operator_factory, sensor_factory
):
    """One estate's maintenance regime is not a finding about a product."""
    heads = [
        operator_with_readings(api, operator_factory, sensor_factory, i,
                               serial=f"ELITECH-00:1B:44:{i:02d}")
        for i in range(2)
    ]
    body = api.get("/api/benchmarks", headers=heads[0]).json()
    assert body["makes_published"] == 0
    assert body["makes_withheld"] == 1


def test_equipment_findings_publish_once_the_population_is_there(
    api, operator_factory, sensor_factory
):
    heads = []
    for i in range(4):
        headers, _, _ = operator_factory(
            company_name=f"Chain {i}", email=f"op{i}@example.com")
        heads.append(headers)
        for unit in range(3):
            api.post("/api/licenses/me/sensors", headers=headers,
                     json={"sensor_id": f"FRZ-{i}-{unit}",
                           "industry_vertical": "restaurant",
                           "location_name": "Walk-In",
                           "external_device_sn": f"ELITECH-{i}{unit}:AA:BB"})
            api.post("/api/sensor-pulse", headers=headers,
                     json={"sensor_id": f"FRZ-{i}-{unit}",
                           "temperature_fahrenheit": 28.0})

    body = api.get("/api/benchmarks", headers=heads[0]).json()
    assert body["makes_published"] == 1
    row = body["makes"][0]
    assert row["make"] == "ELITECH"
    assert row["units_observed"] == 12
    assert row["operators"] == 4
    # Serials are the identifying part and must never be echoed.
    assert "AA:BB" not in api.get("/api/benchmarks", headers=heads[0]).text
