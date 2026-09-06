"""Claim evidence packets.

The packet is only worth anything if an adjuster can trust it, so the
tests care most about two things: it contains what was actually recorded,
and it never quietly invents anything.
"""


def breached_estate(api, operator_factory, sensor_factory, age_incident):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    for temp in (28.0, 29.5, 31.0):
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": "FRZ-1", "temperature_fahrenheit": temp})
    body = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "FRZ-1",
                          "temperature_fahrenheit": 48.0}).json()
    return headers, body["incident_id"]


def test_the_packet_carries_the_record_and_the_response(
    api, operator_factory, sensor_factory, age_incident
):
    headers, incident_id = breached_estate(
        api, operator_factory, sensor_factory, age_incident)
    api.post(f"/api/voice/acknowledge/{incident_id}", headers=headers,
             json={"acknowledged_by": "Marco Diaz"})

    packet = api.post(f"/api/claims/{incident_id}/packet",
                      headers=headers).json()

    assert packet["event"]["incident_id"] == incident_id
    assert packet["event"]["detected_reading"] == 48.0
    assert packet["evidence"]["readings_in_window"] == 4
    assert packet["evidence"]["excursions_in_window"] == 1
    assert packet["evidence"]["peak_reading"] == 48.0
    events = [step["event"] for step in packet["response"]["timeline"]]
    assert "Breach detected" in events
    assert "Alert dispatched by SMS" in events
    assert "Acknowledged" in events
    assert packet["event"]["minutes_to_acknowledge"] is not None


def test_the_packet_carries_a_verifiable_attestation(
    api, operator_factory, sensor_factory, age_incident
):
    """An adjuster must be able to check nothing was written afterwards."""
    headers, incident_id = breached_estate(
        api, operator_factory, sensor_factory, age_incident)
    packet = api.post(f"/api/claims/{incident_id}/packet",
                      headers=headers).json()

    head = packet["attestation"]["chain_head"]
    supplied = [
        {"sensor_id": "FRZ-1", "at": r["at"],
         "temperature_fahrenheit": r["temperature"],
         "humidity_percent": r["humidity"], "breached": r["breached"]}
        for r in packet["evidence"]["readings"]
    ]
    out = api.post("/api/vault/verify",
                   json={"readings": supplied, "chain_head": head}).json()
    assert out["matches"] is True


def test_prior_incidents_are_disclosed_not_hidden(
    api, operator_factory, sensor_factory, age_incident
):
    """An adjuster will find earlier warnings; better we surface them."""
    headers, first = breached_estate(
        api, operator_factory, sensor_factory, age_incident)
    api.post(f"/api/voice/resolve/{first}", headers=headers, json={})
    second = api.post("/api/sensor-pulse", headers=headers,
                      json={"sensor_id": "FRZ-1",
                            "temperature_fahrenheit": 52.0}).json()["incident_id"]

    packet = api.post(f"/api/claims/{second}/packet", headers=headers).json()
    prior = [p["incident_id"] for p in packet["prior_incidents_90_days"]]
    assert first in prior


def test_the_cover_letter_never_invents_a_loss_figure(
    api, operator_factory, sensor_factory, age_incident, break_gemini
):
    """One invented number and an adjuster discounts the whole packet."""
    break_gemini("outage")
    headers, incident_id = breached_estate(
        api, operator_factory, sensor_factory, age_incident)
    packet = api.post(f"/api/claims/{incident_id}/packet",
                      headers=headers).json()

    letter = packet["cover_letter"]
    assert packet["cover_letter_source"] == "fallback_template"
    assert "$" not in letter
    assert packet["location"]["asset"] in letter
    assert "hash-chained" in letter


def test_a_celsius_customer_gets_a_celsius_packet(
    api, operator_factory, sensor_factory, age_incident
):
    headers, incident_id = breached_estate(
        api, operator_factory, sensor_factory, age_incident)
    api.post("/api/licenses/me/temperature-unit", headers=headers,
             json={"temperature_unit": "C"})

    packet = api.post(f"/api/claims/{incident_id}/packet",
                      headers=headers).json()
    assert packet["event"]["temperature_unit"] == "C"
    assert packet["event"]["detected_reading"] == 8.89


def test_another_tenants_incident_is_not_packable(
    api, operator_factory, sensor_factory, age_incident
):
    theirs, incident_id = breached_estate(
        api, operator_factory, sensor_factory, age_incident)
    mine, _, _ = operator_factory(company_name="Beta", email="b@x.com")
    assert api.post(f"/api/claims/{incident_id}/packet",
                    headers=mine).status_code == 404


def test_eligible_lists_what_can_be_claimed(
    api, operator_factory, sensor_factory, age_incident
):
    headers, incident_id = breached_estate(
        api, operator_factory, sensor_factory, age_incident)
    body = api.get("/api/claims/eligible", headers=headers).json()
    assert body["count"] == 1
    assert body["incidents"][0]["incident_id"] == incident_id
    assert body["incidents"][0]["readings_available"] is True
