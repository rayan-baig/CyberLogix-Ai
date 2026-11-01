"""The tamper check holds up against later attestations, and the public
proof link is not a way to write to the database or see more than the owner
published.

Before: /verify compared the record only with the newest attestation, and
every load of the public proof page made a new one. So after somebody edited
a reading, any later attestation (or any anonymous visit to the proof page)
recorded the edited record as the new truth, and the check said "intact".
"""

from store import STORE


def _estate(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    for temp in (28.0, 29.0, 30.0):
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": "FRZ-1", "temperature_fahrenheit": temp})
    return headers


def _publish(api, headers, period_days=30):
    resp = api.post("/api/certificate", headers=headers, json={"live": True, "period_days": period_days})
    assert resp.status_code == 200, resp.text
    return resp.json()["url"].rsplit("/", 1)[1]


def test_a_later_attestation_does_not_clear_a_tamper(api, operator_factory, sensor_factory):
    headers = _estate(api, operator_factory, sensor_factory)
    api.get("/api/vault/attestation/FRZ-1", headers=headers)
    STORE.readings_for("FRZ-1")[1].temperature_fahrenheit = 28.5
    assert api.get("/api/vault/verify/FRZ-1", headers=headers).json()["intact"] is False

    # Somebody attests again after the edit.
    api.get("/api/vault/attestation/FRZ-1", headers=headers)
    api.get("/api/vault/attestation", headers=headers)
    after = api.get("/api/vault/verify/FRZ-1", headers=headers).json()
    assert after["intact"] is False
    assert after["attestations_broken"] >= 1
    assert "altered" in after["note"]


def test_an_untouched_record_passes_every_attestation(api, operator_factory, sensor_factory):
    headers = _estate(api, operator_factory, sensor_factory)
    api.get("/api/vault/attestation/FRZ-1", headers=headers)
    api.post("/api/sensor-pulse", headers=headers, json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 31.0})
    api.get("/api/vault/attestation/FRZ-1", headers=headers)
    body = api.get("/api/vault/verify/FRZ-1", headers=headers).json()
    assert body["intact"] is True
    assert body["attestations_checked"] == 2
    assert body["rederived_head"] == body["attested_head"]


def test_asking_twice_for_the_same_attestation_records_it_once(api, operator_factory, sensor_factory):
    headers = _estate(api, operator_factory, sensor_factory)
    for _ in range(5):
        api.get("/api/vault/attestation/FRZ-1", headers=headers)
    assert len(STORE.anchors_for_sensor("FRZ-1")) == 1


def test_the_public_proof_page_does_not_write_on_every_visit(api, operator_factory, sensor_factory):
    headers = _estate(api, operator_factory, sensor_factory)
    token = _publish(api, headers)
    for i in range(20):
        # A new reading between visits, so each would otherwise be a new head.
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 30.0 + i / 10})
        assert api.get(f"/api/certificate/public/{token}").status_code == 200
    assert len(STORE.anchors_for_sensor("FRZ-1")) == 1


def test_a_visitor_cannot_see_more_than_was_published(api, operator_factory, sensor_factory):
    headers = _estate(api, operator_factory, sensor_factory)
    token = _publish(api, headers, period_days=30)
    assert api.get(f"/api/certificate/public/{token}?days=365").json()["period_days"] == 30
    assert api.get(f"/api/certificate/public/{token}?days=14").json()["period_days"] == 14
    assert api.get(f"/api/certificate/public/{token}").json()["period_days"] == 30


# ---- second review: what counts as gone, and what counts as changed --------
# Deleting the newest attested reading passed as intact (a short run was
# taken for retention); ordinary retention turnover was called tampering
# (the oldest kept reading was taken for the run's start); and 200 fresh
# attestations pushed the honest one out of what /verify looked at.

from datetime import timedelta  # noqa: E402

import store  # noqa: E402
from store import utc_now  # noqa: E402


def _spaced_estate(api, operator_factory, sensor_factory, n):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    for i in range(n):
        r = api.post("/api/sensor-pulse", headers=headers,
                     json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 28.0 + i / 10})
        assert r.status_code < 300, r.text
    # Space them five minutes apart, as a real sensor would.
    rs = STORE.readings_for("FRZ-1")
    for i, r in enumerate(rs):
        r.recorded_at = utc_now() - timedelta(minutes=5 * (len(rs) - i))
    return headers


def test_retention_turnover_is_not_called_tampering(api, operator_factory, sensor_factory, monkeypatch):
    monkeypatch.setattr(store, "MAX_READINGS_PER_SENSOR", 20)
    headers = _spaced_estate(api, operator_factory, sensor_factory, 10)
    att = api.get("/api/vault/attestation/FRZ-1", headers=headers).json()
    assert att["readings"] == 10
    # Normal operation: 11 more readings arrive; the ring buffer (20) evicts
    # the oldest attested reading. Nobody edits anything.
    for i in range(11):
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 30.0})
    assert len(STORE.readings_for("FRZ-1")) == 20
    body = api.get("/api/vault/verify/FRZ-1", headers=headers).json()
    assert body["attestations_broken"] == 0, "eviction reported as tampering"


def test_many_later_attestations_do_not_hide_a_tamper(api, operator_factory, sensor_factory):
    headers = _spaced_estate(api, operator_factory, sensor_factory, 10)
    api.get("/api/vault/attestation/FRZ-1", headers=headers)      # honest anchor
    STORE.readings_for("FRZ-1")[1].temperature_fahrenheit = 99.0  # the edit
    assert api.get("/api/vault/verify/FRZ-1", headers=headers).json()["intact"] is False
    # The tamperer asks for 200 attestations with different windows; the
    # newest-only dedupe does not stop them and /verify only looks at 200.
    for d in range(1, 201):
        api.get(f"/api/vault/attestation/FRZ-1?days={d}", headers=headers)
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 30.0})
    assert len(STORE.anchors_for_sensor("FRZ-1")) <= store.MAX_ANCHORS_PER_SENSOR_PER_DAY
    body = api.get("/api/vault/verify/FRZ-1", headers=headers).json()
    assert body["intact"] is False, "tampered record verified intact"


def _pulse(api, headers, t):
    api.post("/api/sensor-pulse", headers=headers, json={"sensor_id": "FRZ-1", "temperature_fahrenheit": t})


def test_deleting_the_newest_attested_reading_is_caught(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    for i in range(5):
        _pulse(api, headers, 28.0)
    rs = STORE.readings_for("FRZ-1")
    for i, r in enumerate(rs):
        r.recorded_at = utc_now() - timedelta(minutes=60 - 5 * i)
    api.get("/api/vault/attestation/FRZ-1", headers=headers)          # A0: 5 readings
    for i in range(4):
        _pulse(api, headers, 28.0)
    _pulse(api, headers, 55.0)                                          # the breach
    api.get("/api/vault/attestation/FRZ-1", headers=headers)          # A1: 10 readings, incl. breach
    assert api.get("/api/vault/verify/FRZ-1", headers=headers).json()["intact"] is True
    STORE._readings["FRZ-1"].pop()                                      # someone deletes the breach
    body = api.get("/api/vault/verify/FRZ-1", headers=headers).json()
    assert body["intact"] is False
