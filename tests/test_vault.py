"""The compliance vault.

The claim is narrow and has to hold exactly: altering a stored reading
must change the chain head, and a third party must be able to check that
themselves without an account and without trusting us.
"""

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


def _estate_with_history(api, tenant_factory, owner_headers, sensor_factory,
                         readings=(-320.0, -318.0, -315.0, -100.0)):
    headers, tenant = tenant_factory(plan="growth")
    sensor_factory(headers, "TANK-1", "cryostorage")
    for temperature in readings:
        api.post("/api/sensor-pulse", headers=headers, json={
            "sensor_id": "TANK-1", "temperature_fahrenheit": temperature})
    return headers, tenant


def test_the_exported_chain_verifies_in_the_public_verifier(
    api, tenant_factory, owner_headers, sensor_factory
):
    """The whole promise of the vault, end to end.

    `build_chain` omitted `sensor_id`, which `digest_reading` hashes. So a
    recipient was handed a document missing one of the inputs to its own
    hash, and /api/vault/verify answered 400 on the very packet
    /api/vault/attestation had just produced. A recipient who trusts
    neither party could not do the arithmetic — which is the only thing
    the feature is for.
    """
    headers, _ = _estate_with_history(
        api, tenant_factory, owner_headers, sensor_factory
    )
    attested = api.get("/api/vault/attestation/TANK-1?include_chain=true",
                       headers=headers).json()

    # No credentials at all: this is the insurer, not the customer.
    checked = api.post("/api/vault/verify", json={
        "readings": attested["chain"],
        "chain_head": attested["chain_head"],
    })
    assert checked.status_code == 200, checked.text
    assert checked.json()["matches"] is True
    assert checked.json()["links_checked"] == len(attested["chain"])


def test_every_link_carries_every_input_to_its_own_digest(
    api, tenant_factory, owner_headers, sensor_factory
):
    """A link missing a hashed field is a link nobody can re-derive."""
    headers, _ = _estate_with_history(
        api, tenant_factory, owner_headers, sensor_factory
    )
    chain = api.get("/api/vault/attestation/TANK-1?include_chain=true",
                    headers=headers).json()["chain"]
    for link in chain:
        for field in ("sensor_id", "at", "temperature_fahrenheit",
                      "humidity_percent", "breached", "previous", "digest"):
            assert field in link, f"the chain omits {field}, which is hashed"


def test_a_doctored_packet_is_rejected_by_the_public_verifier(
    api, tenant_factory, owner_headers, sensor_factory
):
    headers, _ = _estate_with_history(
        api, tenant_factory, owner_headers, sensor_factory
    )
    attested = api.get("/api/vault/attestation/TANK-1?include_chain=true",
                       headers=headers).json()
    doctored = [dict(link) for link in attested["chain"]]
    doctored[1]["temperature_fahrenheit"] = -320.0

    checked = api.post("/api/vault/verify", json={
        "readings": doctored, "chain_head": attested["chain_head"]}).json()
    assert checked["matches"] is False
    assert "NOT produce" in checked["verdict"]


def test_swapping_a_link_to_another_sensor_is_caught(
    api, tenant_factory, owner_headers, sensor_factory
):
    """Now that sensor_id travels with the link, it has to be checked."""
    headers, _ = _estate_with_history(
        api, tenant_factory, owner_headers, sensor_factory
    )
    attested = api.get("/api/vault/attestation/TANK-1?include_chain=true",
                       headers=headers).json()
    doctored = [dict(link) for link in attested["chain"]]
    doctored[2]["sensor_id"] = "TANK-2"

    checked = api.post("/api/vault/verify", json={
        "readings": doctored, "chain_head": attested["chain_head"]}).json()
    assert checked["matches"] is False
