"""A retired sensor takes its open incident with it.

Sensor ids are unique across the whole service while registered, and are
free again once retired. Incidents were matched to a breach by sensor id
alone, so an incident still open when its sensor was retired caught every
later breach from any sensor that took the id: the new freezer's failure
was folded into the old incident, nobody was texted, and if a different
business had taken the id, its readings were written onto the first
business's incident and its sensor was told that business's alert text.
"""

from store import STORE


def _breach(api, headers, sensor_id, temp=60.0):
    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": sensor_id, "temperature_fahrenheit": temp})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_another_business_taking_the_id_gets_its_own_alert(api, tenant_factory, sensor_factory):
    first, _ = tenant_factory(company_name="First Foods")
    sensor_factory(first, sensor_id="FRZ-1", vertical="restaurant")
    old = _breach(api, first, "FRZ-1")
    assert old["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    assert api.delete("/api/licenses/me/sensors/FRZ-1", headers=first).status_code == 200

    second, second_tenant = tenant_factory(company_name="Second Foods")
    sensor_factory(second, sensor_id="FRZ-1", vertical="restaurant")
    new = _breach(api, second, "FRZ-1")

    assert new["status"] == "CRITICAL_CATASTROPHE_TRIGGERED", new
    assert new["incident_id"] != old["incident_id"]
    assert STORE.get_incident(new["incident_id"]).tenant_id == second_tenant["tenant_id"]
    # The first business's record is not touched by the second's readings.
    assert STORE.get_incident(old["incident_id"]).temperature_fahrenheit == 60.0


def test_a_replacement_under_the_same_id_is_alerted_on(api, tenant_factory, sensor_factory):
    """Acknowledged and never resolved is common: somebody pressed the button
    on the text, fixed the door and moved on."""
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    old = _breach(api, headers, "FRZ-1")
    STORE.acknowledge_incident(STORE.get_incident(old["incident_id"]), "Dana")
    api.delete("/api/licenses/me/sensors/FRZ-1", headers=headers)

    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    new = _breach(api, headers, "FRZ-1", temp=61.0)

    assert new["status"] == "CRITICAL_CATASTROPHE_TRIGGERED", new


def test_retiring_a_sensor_closes_its_open_incident(api, tenant_factory, sensor_factory):
    """Otherwise the sweep phones somebody about a freezer that is gone."""
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    old = _breach(api, headers, "FRZ-1")

    api.delete("/api/licenses/me/sensors/FRZ-1", headers=headers)

    incident = STORE.get_incident(old["incident_id"])
    assert incident.resolved_at is not None
    # Closed, but nobody is recorded as having answered it: a claim packet
    # must not show an acknowledgement that no person made.
    assert incident.acknowledged_at is None
    assert STORE.open_incidents() == []
    trail = api.get("/api/accounts/audit", headers=headers)
    if trail.status_code == 200:
        assert any("retired" in e["detail"] for e in trail.json()["entries"])
