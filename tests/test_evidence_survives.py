"""Evidence for an event that mattered must outlive the rolling window.

Readings are a ring buffer: the oldest are deleted once a sensor passes
MAX_READINGS_PER_SENSOR, which at a five-minute pulse is under two days.
The claim packet read them live.

So a customer whose freezer failed, and who filed the claim a week later
the way people actually do, got a packet containing five hundred healthy
readings with the breach deleted — and `excursions_in_window: 0` on the
document whose entire purpose is to prove an excursion happened.

Not an empty packet. Somebody would have questioned an empty packet. A
complete-looking one, arguing against the claim it was attached to.
"""

import pytest

from store import MAX_READINGS_PER_SENSOR, STORE


def _pulse(api, headers, temp, sensor="VAX-1"):
    return api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": sensor, "temperature_fahrenheit": temp})


@pytest.fixture()
def failed_freezer(api, tenant_factory, sensor_factory):
    """A drift, a breach, a fix — the shape of a real claim."""
    headers, tenant = tenant_factory(plan="enterprise")
    sensor_factory(headers, "VAX-1", "pharmacy")
    for temp in (38.0, 39.0, 45.0):
        _pulse(api, headers, temp)
    incident = _pulse(api, headers, 78.0).json()["incident_id"]
    api.post(f"/api/voice/resolve/{incident}", headers=headers,
             json={"resolved_by": "Ops"})
    return headers, tenant, incident


def _bury(api, headers, count=None):
    """Enough normal operation to roll the failure out of the buffer."""
    for _ in range(count or MAX_READINGS_PER_SENSOR + 50):
        _pulse(api, headers, 38.0)


def _packet(api, headers, incident):
    resp = api.post(f"/api/claims/{incident}/packet", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["evidence"]


# ---- the bug ------------------------------------------------------------


def test_a_claim_filed_later_still_proves_the_excursion(
    api, failed_freezer
):
    """The assertion that was false, and is the whole point of the file."""
    headers, _, incident = failed_freezer
    _bury(api, headers)

    evidence = _packet(api, headers, incident)

    assert evidence["excursions_in_window"] == 1, (
        "the claim packet reports no excursion for a freezer that breached"
    )
    assert evidence["peak_reading"] == 78.0
    assert evidence["first_excursion_at"] is not None


def test_the_reading_that_caused_the_claim_is_in_the_packet(
    api, failed_freezer
):
    headers, _, incident = failed_freezer
    _bury(api, headers)

    readings = _packet(api, headers, incident)["readings"]
    breaching = [r for r in readings if r["breached"]]

    assert [r["temperature"] for r in breaching] == [78.0]


def test_the_drift_leading_up_to_it_survives_too(api, failed_freezer):
    """An adjuster asks how long it was going wrong before anybody knew.
    The lead-up readings are the first ones the buffer deletes."""
    headers, _, incident = failed_freezer
    _bury(api, headers)

    temps = [r["temperature"] for r in _packet(api, headers, incident)["readings"]]

    for expected in (39.0, 45.0):
        assert expected in temps, f"{expected} was lost from the evidence"


def test_the_hash_chain_still_covers_the_preserved_readings(
    api, failed_freezer
):
    """The packet's verifiable half is what an insurer re-derives. It has
    to cover the same readings the human half shows."""
    headers, _, incident = failed_freezer
    _bury(api, headers)

    evidence = _packet(api, headers, incident)

    assert len(evidence["verifiable_readings"]) == evidence["readings_in_window"]


# ---- how it is preserved ------------------------------------------------


def test_the_evidence_is_copied_onto_the_incident_when_it_opens(
    api, tenant_factory, sensor_factory
):
    """Not at claim time, which is too late — the readings are gone by
    then. Incidents are kept for years; readings are not."""
    headers, tenant = tenant_factory(plan="enterprise")
    sensor_factory(headers, "VAX-1", "pharmacy")
    _pulse(api, headers, 40.0)
    incident_id = _pulse(api, headers, 78.0).json()["incident_id"]

    incident = STORE.get_incident(incident_id)

    assert incident.evidence_readings, "nothing was preserved at open"
    assert any(r["breached"] for r in incident.evidence_readings)


def test_resolving_preserves_the_recovery_as_well(
    api, tenant_factory, sensor_factory
):
    """An adjuster asks when it came back into band.

    Those readings do not exist when the incident opens, so preserving
    only at open would keep the failure and lose the fix.
    """
    headers, _ = tenant_factory(plan="enterprise")
    sensor_factory(headers, "VAX-1", "pharmacy")
    incident_id = _pulse(api, headers, 78.0).json()["incident_id"]

    at_open = len(STORE.get_incident(incident_id).evidence_readings)

    # The engineer arrives and it comes back into band.
    for temp in (60.0, 44.0, 38.5):
        _pulse(api, headers, temp)
    api.post(f"/api/voice/resolve/{incident_id}", headers=headers,
             json={"resolved_by": "Ops"})

    preserved = STORE.get_incident(incident_id).evidence_readings
    temps = [r["temperature_fahrenheit"] for r in preserved]

    assert len(preserved) > at_open, "the recovery was not preserved"
    assert 38.5 in temps, "the reading showing it came back is missing"
    assert preserved == sorted(preserved, key=lambda r: r["recorded_at"])


def test_preserving_twice_does_not_duplicate_a_reading(api, failed_freezer):
    """Open and resolve both preserve, and their windows overlap."""
    _, _, incident_id = failed_freezer
    incident = STORE.get_incident(incident_id)

    STORE.preserve_evidence(incident)
    STORE.preserve_evidence(incident)

    ids = [r["reading_id"] for r in STORE.get_incident(incident_id).evidence_readings]
    assert len(ids) == len(set(ids))


def test_an_incident_from_before_this_existed_still_loads(api):
    """Its evidence is gone and nothing can bring it back. It must not
    crash the packet on the way to saying so."""
    from store import Incident

    row = {
        "incident_id": "INC-000999", "tenant_id": "TEN-000001",
        "sensor_id": "OLD-1", "industry_vertical": "pharmacy",
        "catastrophe": "Cold chain failure", "temperature_fahrenheit": 78.0,
        "breach_details": "breached", "sms_text": "", "sms_dispatch_source": "x",
        "opened_at": "2026-01-01T00:00:00Z",
    }
    restored = Incident.from_row(row)

    assert restored.evidence_readings == []


# ---- the cost of the fix ------------------------------------------------


def test_preservation_is_bounded_and_does_not_disable_the_ring_buffer(
    api, failed_freezer
):
    """The cheap fix would have been to keep every reading forever. This
    keeps the window the document actually shows, on an object that
    already exists — kilobytes per incident rather than unbounded growth
    for every sensor."""
    headers, tenant, incident_id = failed_freezer
    _bury(api, headers)

    sensor = STORE.sensors_for(tenant["tenant_id"])[0]
    live = STORE.readings_for(sensor.sensor_id)
    preserved = STORE.get_incident(incident_id).evidence_readings

    # The buffer still rolls.
    assert len(live) == MAX_READINGS_PER_SENSOR
    # And the incident holds only its own window, not the whole history.
    assert len(preserved) < MAX_READINGS_PER_SENSOR
