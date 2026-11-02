"""A request body has a ceiling.

Nothing limited one. Sign-up, sign-in and the sensor webhook are open to
anybody, and each read the whole body into memory and parsed it before
looking at a single field, so one stranger posting a few hundred megabytes
at a time could take the one process that watches every freezer down.
"""

import json

import main


def _big(n):
    return b'{"email": "' + b"a" * n + b'"}'


def test_a_huge_body_is_refused_before_it_is_read(api):
    body = _big(main.MAX_REQUEST_BYTES + 10)

    resp = api.post("/api/accounts/login", content=body,
                    headers={"Content-Type": "application/json"})

    assert resp.status_code == 413
    assert "too large" in resp.json()["detail"].lower()


def test_a_huge_body_with_no_length_is_cut_off_as_it_arrives(api):
    def chunks():
        piece = b"a" * 65536
        yield b'{"email": "'
        for _ in range(main.MAX_REQUEST_BYTES // len(piece) + 2):
            yield piece
        yield b'"}'

    resp = api.post("/api/v1/bridge/any", content=chunks(),
                    headers={"Content-Type": "application/json"})

    assert resp.status_code == 413


def test_ordinary_requests_are_untouched(api, operator_factory):
    headers, _, _ = operator_factory()
    assert api.get("/api/accounts/me", headers=headers).status_code == 200
    bad = api.post("/api/accounts/login", json={"email": "x@example.com", "password": "nope-nope"})
    assert bad.status_code == 401


def test_the_largest_vault_check_still_fits(api):
    """The public verifier takes up to 20,000 readings in one go, and an
    auditor pasting a full export must not hit the ceiling first."""
    row = {"sensor_id": "WALK-IN-FREEZER-0001", "temperature_fahrenheit": -10.123456,
           "humidity_percent": 55.123456, "breached": False,
           "recorded_at": "2026-09-20T10:00:00Z"}
    body = json.dumps({"readings": [row] * 20000, "chain_head": "0" * 64}).encode()
    assert len(body) < main.MAX_REQUEST_BYTES

    resp = api.post("/api/vault/verify", content=body,
                    headers={"Content-Type": "application/json"})

    assert resp.status_code != 413
