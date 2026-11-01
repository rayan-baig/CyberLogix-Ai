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
