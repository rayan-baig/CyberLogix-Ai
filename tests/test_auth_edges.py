"""The states where access should stop, and often does not.

A tenant whose contract lapsed, an account somebody suspended, a session
past its expiry, a viewer reaching for a write — each is a moment where
the system has to say no, and each is easy to get wrong because the happy
path never exercises it.
"""

from datetime import timedelta



def test_an_expired_session_is_refused(api, operator_factory):
    from store import STORE

    headers, _, _ = operator_factory()
    assert api.get("/api/console/overview", headers=headers).status_code == 200

    token = headers["Authorization"].split()[1]
    session = STORE._sessions[token]
    session.expires_at -= timedelta(hours=24)

    assert api.get("/api/console/overview", headers=headers).status_code == 401


def test_a_suspended_tenant_cannot_ingest_or_read(api, operator_factory,
                                                  sensor_factory):
    """Suspension has to stop the API, not just the invoice."""
    from store import STORE

    headers, tenant, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    STORE.get_tenant(tenant["tenant_id"]).suspended = True

    for method, path, payload in (
        ("post", "/api/sensor-pulse",
         {"sensor_id": "FRZ-1", "temperature_fahrenheit": 55.0}),
        ("get", "/api/console/overview", None),
        ("get", "/api/licenses/me/sensors", None),
    ):
        kwargs = {"headers": headers}
        if payload is not None:
            kwargs["json"] = payload
        resp = getattr(api, method)(path, **kwargs)
        assert resp.status_code in (401, 402, 403), (
            f"{method.upper()} {path} answered {resp.status_code} for a "
            "suspended tenant"
        )


def test_an_expired_contract_cannot_ingest(api, operator_factory,
                                           sensor_factory):
    from store import STORE

    headers, tenant, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    STORE.get_tenant(tenant["tenant_id"]).expires_at -= timedelta(days=400)

    resp = api.post("/api/sensor-pulse", headers=headers,
                    json={"sensor_id": "FRZ-1", "temperature_fahrenheit": 55.0})
    assert resp.status_code in (401, 402, 403)


def viewer_of(api, operator_factory, **kwargs):
    headers, tenant, _ = operator_factory(**kwargs)
    api.post("/api/accounts/users", headers=headers,
             json={"email": "viewer@example.com", "full_name": "Val",
                   "role": "viewer", "password": "correct-horse-battery"})
    token = api.post("/api/accounts/login",
                     json={"email": "viewer@example.com",
                           "password": "correct-horse-battery"}).json()["token"]
    return headers, {"Authorization": f"Bearer {token}"}, tenant


def test_a_viewer_can_read_but_never_write(api, operator_factory,
                                           sensor_factory):
    owner, viewer, _ = viewer_of(api, operator_factory)
    sensor_factory(owner, sensor_id="FRZ-1", vertical="restaurant")

    assert api.get("/api/console/overview", headers=viewer).status_code == 200

    forbidden = [
        ("post", "/api/sites", {"name": "New Site"}),
        ("post", "/api/contacts", {"full_name": "P", "phone": "+15550001"}),
        ("post", "/api/webhooks", {"kind": "slack",
                                   "target": "https://hooks.slack.com/a/b/c"}),
        ("post", "/api/accounts/users", {"email": "x@example.com",
                                         "full_name": "X", "role": "owner",
                                         "password": "correct-horse-battery"}),
    ]
    for method, path, payload in forbidden:
        resp = getattr(api, method)(path, headers=viewer, json=payload)
        assert resp.status_code == 403, (
            f"a viewer got {resp.status_code} on {method.upper()} {path}"
        )


def test_an_operator_cannot_do_an_owners_work(api, operator_factory,
                                              sensor_factory):
    """Billing and account administration are the owner's, not the shift's."""
    owner, tenant, _ = operator_factory()
    api.post("/api/accounts/users", headers=owner,
             json={"email": "ops@example.com", "full_name": "Sam",
                   "role": "operator", "password": "correct-horse-battery"})
    token = api.post("/api/accounts/login",
                     json={"email": "ops@example.com",
                           "password": "correct-horse-battery"}).json()["token"]
    operator = {"Authorization": f"Bearer {token}"}
    sensor_factory(owner, sensor_id="FRZ-1", vertical="restaurant")

    # Allowed: the operational work.
    assert api.post("/api/sites", headers=operator,
                    json={"name": "A Site"}).status_code == 201
    # Refused: the money and the people.
    #
    # Invoicing is not on this list any more, and that is a stronger
    # statement than it being an owner-only route: issuing an invoice and
    # recording a payment are the *vendor's* side of the transaction, so
    # no tenant role reaches them at all. The check for that lives in
    # test_invoicing.py.
    assert api.post("/api/accounts/users", headers=operator,
                    json={"email": "z@example.com", "full_name": "Z",
                          "role": "owner",
                          "password": "correct-horse-battery"}).status_code == 403


def test_a_logged_out_token_stops_working(api, operator_factory):
    headers, _, _ = operator_factory()
    assert api.get("/api/console/overview", headers=headers).status_code == 200
    api.post("/api/accounts/logout", headers=headers)
    assert api.get("/api/console/overview", headers=headers).status_code == 401


def test_a_forged_or_truncated_token_is_refused(api, operator_factory):
    headers, _, _ = operator_factory()
    real = headers["Authorization"].split()[1]
    for bad in ("", "x", real[:-1], real + "x", real.upper(), "Bearer " + real):
        resp = api.get("/api/console/overview",
                       headers={"Authorization": f"Bearer {bad}"})
        assert resp.status_code == 401, f"{bad[:12]!r} was accepted"


def test_a_malformed_authorization_header_never_crashes(api):
    """An unauthenticated 500 from six characters and a space.

    "Bearer " passes the scheme check and then indexes past the end of a
    one-element list, on the authentication path, on every protected
    endpoint in the application.
    """
    malformed = [
        "Bearer ", "Bearer", "bearer   ", "BEARER ", "Bearer\t",
        "  Bearer  ", "Basic abc", "Bearer  ", "", " ",
    ]
    for header in malformed:
        resp = api.get("/api/console/overview",
                       headers={"Authorization": header})
        assert resp.status_code == 401, (
            f"{header!r} produced {resp.status_code}, not a clean refusal"
        )


def test_another_tenants_api_key_reaches_only_its_own_estate(
    api, tenant_factory, sensor_factory
):
    a_headers, a_tenant = tenant_factory(company_name="Alpha")
    b_headers, b_tenant = tenant_factory(company_name="Beta")
    sensor_factory(a_headers, sensor_id="A-1", vertical="restaurant")

    listed = api.get("/api/licenses/me/sensors", headers=b_headers).json()
    assert listed["count"] == 0
    assert api.post("/api/sensor-pulse", headers=b_headers,
                    json={"sensor_id": "A-1",
                          "temperature_fahrenheit": 55.0}).status_code == 404
