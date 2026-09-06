"""The compliance vault.

The claim is narrow and has to hold exactly: altering a stored reading
must change the chain head, and a third party must be able to check that
themselves without an account and without trusting us.
"""

import pytest

import vault


def pulse(api, headers, sensor_id, temp):
    return api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": sensor_id, "temperature_fahrenheit": temp})


def estate(api, operator_factory, sensor_factory, temps=(30.0, 30.4, 31.1)):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    for temp in temps:
        pulse(api, headers, "FRZ-1", temp)
    return headers


def test_the_chain_head_fixes_the_whole_run(api, operator_factory, sensor_factory):
    headers = estate(api, operator_factory, sensor_factory)
    body = api.get("/api/vault/attestation/FRZ-1", headers=headers).json()
    assert body["readings"] == 3
    assert len(body["chain_head"]) == 64


def test_editing_a_reading_breaks_the_head(
    api, operator_factory, sensor_factory
):
    """The whole product: an operator cannot quietly fix a bad night."""
    from store import STORE

    headers = estate(api, operator_factory, sensor_factory)
    before = api.get("/api/vault/attestation/FRZ-1", headers=headers).json()

    # Reach past the API and rewrite history, the way an insider would.
    readings = STORE.readings_for("FRZ-1")
    readings[0].temperature_fahrenheit = 28.0

    after = api.get("/api/vault/attestation/FRZ-1", headers=headers).json()
    assert after["chain_head"] != before["chain_head"]


def test_a_later_reading_does_not_rewrite_earlier_links(
    api, operator_factory, sensor_factory
):
    """Appending must extend the chain, not reshuffle it."""
    headers = estate(api, operator_factory, sensor_factory)
    first = api.get("/api/vault/attestation/FRZ-1?include_chain=true",
                    headers=headers).json()["chain"]
    pulse(api, headers, "FRZ-1", 30.9)
    second = api.get("/api/vault/attestation/FRZ-1?include_chain=true",
                     headers=headers).json()["chain"]

    assert len(second) == len(first) + 1
    assert [link["digest"] for link in second[:len(first)]] == \
           [link["digest"] for link in first]


def test_a_third_party_can_verify_without_an_account(api, operator_factory,
                                                     sensor_factory):
    """A recipient who trusts neither party must be able to check it."""
    headers = estate(api, operator_factory, sensor_factory)
    body = api.get("/api/vault/attestation/FRZ-1?include_chain=true",
                   headers=headers).json()

    supplied = [
        {"sensor_id": "FRZ-1", "at": link["at"],
         "temperature_fahrenheit": link["temperature_fahrenheit"],
         "humidity_percent": link["humidity_percent"],
         "breached": link["breached"]}
        for link in body["chain"]
    ]

    # No auth header at all.
    resp = api.post("/api/vault/verify",
                    json={"readings": supplied, "chain_head": body["chain_head"]})
    assert resp.status_code == 200
    assert resp.json()["matches"] is True


def test_a_doctored_record_fails_verification(api, operator_factory,
                                              sensor_factory):
    headers = estate(api, operator_factory, sensor_factory)
    body = api.get("/api/vault/attestation/FRZ-1?include_chain=true",
                   headers=headers).json()
    supplied = [
        {"sensor_id": "FRZ-1", "at": link["at"],
         "temperature_fahrenheit": link["temperature_fahrenheit"],
         "humidity_percent": link["humidity_percent"],
         "breached": link["breached"]}
        for link in body["chain"]
    ]
    supplied[1]["temperature_fahrenheit"] = 29.0  # the doctored night

    out = api.post("/api/vault/verify",
                   json={"readings": supplied,
                         "chain_head": body["chain_head"]}).json()
    assert out["matches"] is False
    assert "do NOT produce" in out["verdict"]


def test_the_verifier_uses_the_same_hash_as_the_chain():
    """Two implementations of one hash is how a verifier drifts."""
    import inspect

    source = inspect.getsource(vault.verify_supplied_chain)
    assert "digest_fields(" in source
    assert "hashlib.sha256" not in source


def test_an_unsigned_attestation_says_so(api, operator_factory, sensor_factory,
                                         monkeypatch):
    """Claiming a signature that isn't there would be the worst outcome."""
    monkeypatch.setattr(vault, "ATTESTATION_KEY", "")
    headers = estate(api, operator_factory, sensor_factory)
    body = api.get("/api/vault/attestation", headers=headers).json()
    assert body["signature"] is None
    assert body["signing"]["counter_signed"] is False
    assert "no counter-signature" in body["signing"]["note"]


def test_a_configured_key_counter_signs(api, operator_factory, sensor_factory,
                                        monkeypatch):
    monkeypatch.setattr(vault, "ATTESTATION_KEY", "test-signing-key")
    headers = estate(api, operator_factory, sensor_factory)
    body = api.get("/api/vault/attestation", headers=headers).json()
    assert body["signature"] and len(body["signature"]) == 64
    assert body["signing"]["counter_signed"] is True


def test_another_tenants_sensor_cannot_be_attested(
    api, operator_factory, sensor_factory
):
    theirs, _, _ = operator_factory(company_name="Acme", email="a@x.com")
    sensor_factory(theirs, sensor_id="THEIRS-1", vertical="restaurant")
    mine, _, _ = operator_factory(company_name="Beta", email="b@x.com")
    assert api.get("/api/vault/attestation/THEIRS-1",
                   headers=mine).status_code == 404


def test_verify_refuses_a_malformed_body(api):
    assert api.post("/api/vault/verify", json={"readings": []}).status_code == 400
    assert api.post("/api/vault/verify",
                    json={"readings": [{"nope": 1}]}).status_code == 400
