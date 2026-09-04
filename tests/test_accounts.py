"""Operator accounts, roles and the audit trail."""


def bootstrap(api, key_headers, email="dana@example.com", password="correct-horse-battery"):
    return api.post(
        "/api/accounts/bootstrap",
        headers=key_headers,
        json={
            "email": email,
            "full_name": "Dana Reyes",
            "password": password,
            "role": "owner",
        },
    )


def test_first_operator_is_created_with_the_api_key(api, tenant_factory):
    key_headers, _ = tenant_factory()
    resp = bootstrap(api, key_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "owner"
    # A password hash must never leave the server.
    assert "password_hash" not in body


def test_bootstrap_only_works_once(api, tenant_factory):
    key_headers, _ = tenant_factory()
    bootstrap(api, key_headers)
    second = bootstrap(api, key_headers, email="other@example.com")
    assert second.status_code == 409
    assert "already has operators" in second.json()["detail"]


def test_login_returns_a_session_token(api, operator_factory):
    headers, tenant, user = operator_factory()
    assert headers["Authorization"].startswith("Bearer cls_")
    me = api.get("/api/accounts/me", headers=headers).json()
    assert me["user"]["email"] == user["email"]
    assert me["tenant"]["company_name"] == tenant["company_name"]


def test_wrong_password_is_rejected(api, tenant_factory):
    key_headers, _ = tenant_factory()
    bootstrap(api, key_headers)
    resp = api.post(
        "/api/accounts/login",
        json={"email": "dana@example.com", "password": "wrong-password"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Email or password is incorrect."


def test_unknown_email_gives_the_same_answer(api):
    resp = api.post(
        "/api/accounts/login",
        json={"email": "nobody@example.com", "password": "whatever-long"},
    )
    assert resp.status_code == 401
    # Identical wording, so the response cannot be used to enumerate accounts.
    assert resp.json()["detail"] == "Email or password is incorrect."


def test_session_token_authenticates_the_whole_platform(api, operator_factory):
    headers, _, _ = operator_factory()
    # No X-CyberLogix-Key anywhere: the bearer token stands in for it.
    assert api.get("/api/licenses/me", headers=headers).status_code == 200
    assert api.get("/api/console/overview", headers=headers).status_code == 200
    assert api.get("/api/costs", headers=headers).status_code == 200


def test_logout_revokes_the_token(api, operator_factory):
    headers, _, _ = operator_factory()
    assert api.post("/api/accounts/logout", headers=headers).status_code == 200
    assert api.get("/api/accounts/me", headers=headers).status_code == 401


def test_owner_can_invite_and_roles_are_enforced(api, operator_factory):
    owner, _, _ = operator_factory()
    created = api.post(
        "/api/accounts/users",
        headers=owner,
        json={
            "email": "sam@example.com",
            "full_name": "Sam Cole",
            "password": "another-long-password",
            "role": "viewer",
        },
    )
    assert created.status_code == 201
    assert created.json()["role"] == "viewer"

    viewer_login = api.post(
        "/api/accounts/login",
        json={"email": "sam@example.com", "password": "another-long-password"},
    ).json()
    viewer = {"Authorization": f"Bearer {viewer_login['token']}"}

    # A viewer cannot invite anyone.
    blocked = api.post(
        "/api/accounts/users",
        headers=viewer,
        json={
            "email": "third@example.com",
            "full_name": "Third",
            "password": "yet-another-password",
            "role": "viewer",
        },
    )
    assert blocked.status_code == 403
    assert "needs the 'owner' role" in blocked.json()["detail"]


def test_duplicate_email_rejected(api, operator_factory):
    owner, _, user = operator_factory()
    resp = api.post(
        "/api/accounts/users",
        headers=owner,
        json={
            "email": user["email"],
            "full_name": "Impostor",
            "password": "some-long-password",
            "role": "operator",
        },
    )
    assert resp.status_code == 409


def test_owner_cannot_demote_or_disable_themselves(api, operator_factory):
    owner, _, user = operator_factory()
    demote = api.post(
        f"/api/accounts/users/{user['user_id']}/role",
        headers=owner,
        json={"role": "viewer"},
    )
    assert demote.status_code == 409
    assert "never left without one" in demote.json()["detail"]

    disable = api.post(
        f"/api/accounts/users/{user['user_id']}/disable", headers=owner
    )
    assert disable.status_code == 409


def test_disabling_a_user_revokes_their_session_immediately(api, operator_factory):
    owner, _, _ = operator_factory()
    api.post(
        "/api/accounts/users",
        headers=owner,
        json={
            "email": "sam@example.com",
            "full_name": "Sam Cole",
            "password": "another-long-password",
            "role": "operator",
        },
    )
    login = api.post(
        "/api/accounts/login",
        json={"email": "sam@example.com", "password": "another-long-password"},
    ).json()
    sam = {"Authorization": f"Bearer {login['token']}"}
    assert api.get("/api/accounts/me", headers=sam).status_code == 200

    api.post(f"/api/accounts/users/{login['user']['user_id']}/disable", headers=owner)
    assert api.get("/api/accounts/me", headers=sam).status_code == 401


def test_password_change_requires_the_current_one(api, operator_factory):
    headers, _, _ = operator_factory()
    wrong = api.post(
        "/api/accounts/me/password",
        headers=headers,
        json={"current_password": "nope", "new_password": "brand-new-password"},
    )
    assert wrong.status_code == 403

    right = api.post(
        "/api/accounts/me/password",
        headers=headers,
        json={
            "current_password": "correct-horse-battery",
            "new_password": "brand-new-password",
        },
    )
    assert right.status_code == 200
    assert api.post(
        "/api/accounts/login",
        json={"email": "dana@example.com", "password": "brand-new-password"},
    ).status_code == 200


def test_short_password_rejected(api, tenant_factory):
    key_headers, _ = tenant_factory()
    resp = api.post(
        "/api/accounts/bootstrap",
        headers=key_headers,
        json={
            "email": "dana@example.com",
            "full_name": "Dana",
            "password": "short",
            "role": "owner",
        },
    )
    assert resp.status_code == 422


def test_actions_are_attributed_to_the_signed_in_person(
    api, operator_factory, sensor_factory
):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="RACK-01")
    incident_id = api.post(
        "/api/sensor-pulse",
        headers=headers,
        json={"sensor_id": "RACK-01", "temperature_fahrenheit": 94.0},
    ).json()["incident_id"]

    # No name in the body: the server uses the operator's own identity.
    body = api.post(
        f"/api/voice/acknowledge/{incident_id}", headers=headers, json={}
    ).json()
    assert body["incident"]["acknowledged_by"] == "Dana Reyes <dana@example.com>"

    trail = api.get("/api/accounts/audit", headers=headers).json()
    actions = [e["action"] for e in trail["entries"]]
    assert "incident.acknowledged" in actions
    assert all(e["actor"] == "Dana Reyes <dana@example.com>" for e in trail["entries"])


def test_machine_credentials_are_audited_as_machines(
    api, tenant_factory, sensor_factory
):
    key_headers, _ = tenant_factory()
    bootstrap(api, key_headers)
    sensor_factory(key_headers, sensor_id="RACK-01")
    incident_id = api.post(
        "/api/sensor-pulse",
        headers=key_headers,
        json={"sensor_id": "RACK-01", "temperature_fahrenheit": 94.0},
    ).json()["incident_id"]

    api.post(
        f"/api/voice/acknowledge/{incident_id}",
        headers=key_headers,
        json={"acknowledged_by": "Night Engineer"},
    )

    login = api.post(
        "/api/accounts/login",
        json={"email": "dana@example.com", "password": "correct-horse-battery"},
    ).json()
    trail = api.get(
        "/api/accounts/audit", headers={"Authorization": f"Bearer {login['token']}"}
    ).json()
    ack = [e for e in trail["entries"] if e["action"] == "incident.acknowledged"][0]
    assert ack["actor"] == "Night Engineer"
    assert ack["actor_role"] == "machine"


def test_audit_is_scoped_to_the_tenant(api, operator_factory):
    alice, _, _ = operator_factory(company_name="Alice Foods", email="a@example.com")
    bob, _, _ = operator_factory(company_name="Bob Labs", email="b@example.com")

    alice_trail = api.get("/api/accounts/audit", headers=alice).json()
    bob_trail = api.get("/api/accounts/audit", headers=bob).json()
    assert all("a@example.com" in e["actor"] for e in alice_trail["entries"])
    assert all("b@example.com" in e["actor"] for e in bob_trail["entries"])


def test_suspended_license_blocks_login(api, tenant_factory):
    key_headers, _ = tenant_factory()
    bootstrap(api, key_headers)
    api.post("/api/licenses/me/suspend", headers=key_headers)

    resp = api.post(
        "/api/accounts/login",
        json={"email": "dana@example.com", "password": "correct-horse-battery"},
    )
    assert resp.status_code == 402
