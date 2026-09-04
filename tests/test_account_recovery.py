"""Password reset and login throttling."""

import pytest

import accounts


@pytest.fixture(autouse=True)
def clear_throttle():
    accounts._login_attempts.clear()
    yield
    accounts._login_attempts.clear()


def invite(api, owner, email="sam@example.com", password="a-long-sam-password"):
    resp = api.post(
        "/api/accounts/users",
        headers=owner,
        json={"email": email, "full_name": "Sam Cole", "role": "operator",
              "password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_owner_issues_a_reset_and_the_user_redeems_it(api, operator_factory):
    owner, _, _ = operator_factory()
    sam = invite(api, owner)

    issued = api.post(
        f"/api/accounts/users/{sam['user_id']}/reset", headers=owner
    ).json()
    assert issued["reset_token"].startswith("clr_")

    redeemed = api.post(
        "/api/accounts/reset",
        json={"token": issued["reset_token"], "new_password": "brand-new-password"},
    )
    assert redeemed.status_code == 200

    # The new password works and the old one does not.
    assert api.post(
        "/api/accounts/login",
        json={"email": "sam@example.com", "password": "brand-new-password"},
    ).status_code == 200
    assert api.post(
        "/api/accounts/login",
        json={"email": "sam@example.com", "password": "a-long-sam-password"},
    ).status_code == 401


def test_a_reset_token_works_only_once(api, operator_factory):
    owner, _, _ = operator_factory()
    sam = invite(api, owner)
    token = api.post(
        f"/api/accounts/users/{sam['user_id']}/reset", headers=owner
    ).json()["reset_token"]

    api.post("/api/accounts/reset",
             json={"token": token, "new_password": "first-new-password"})
    second = api.post("/api/accounts/reset",
                      json={"token": token, "new_password": "second-new-password"})
    assert second.status_code == 400
    assert "already used" in second.json()["detail"]


def test_reset_revokes_existing_sessions(api, operator_factory):
    """A reset usually means the account is compromised."""
    owner, _, _ = operator_factory()
    sam = invite(api, owner)
    login = api.post(
        "/api/accounts/login",
        json={"email": "sam@example.com", "password": "a-long-sam-password"},
    ).json()
    sam_headers = {"Authorization": f"Bearer {login['token']}"}
    assert api.get("/api/accounts/me", headers=sam_headers).status_code == 200

    token = api.post(
        f"/api/accounts/users/{sam['user_id']}/reset", headers=owner
    ).json()["reset_token"]
    api.post("/api/accounts/reset",
             json={"token": token, "new_password": "brand-new-password"})

    assert api.get("/api/accounts/me", headers=sam_headers).status_code == 401


def test_garbage_token_rejected(api):
    resp = api.post("/api/accounts/reset",
                    json={"token": "clr_nonsense", "new_password": "some-long-password"})
    assert resp.status_code == 400


def test_only_owners_issue_resets(api, operator_factory):
    owner, _, _ = operator_factory()
    sam = invite(api, owner)
    login = api.post(
        "/api/accounts/login",
        json={"email": "sam@example.com", "password": "a-long-sam-password"},
    ).json()
    sam_headers = {"Authorization": f"Bearer {login['token']}"}

    resp = api.post(f"/api/accounts/users/{sam['user_id']}/reset", headers=sam_headers)
    assert resp.status_code == 403


def test_reset_across_tenants_is_refused(api, operator_factory):
    alice, _, _ = operator_factory(company_name="Alice", email="a@example.com")
    bob, _, _ = operator_factory(company_name="Bob", email="b@example.com")
    victim = invite(api, alice, email="victim@example.com")

    resp = api.post(f"/api/accounts/users/{victim['user_id']}/reset", headers=bob)
    assert resp.status_code == 404


def test_repeated_failures_are_throttled(api, operator_factory):
    operator_factory()
    for _ in range(accounts.MAX_LOGIN_ATTEMPTS):
        resp = api.post(
            "/api/accounts/login",
            json={"email": "dana@example.com", "password": "wrong-password"},
        )
        assert resp.status_code == 401

    blocked = api.post(
        "/api/accounts/login",
        json={"email": "dana@example.com", "password": "wrong-password"},
    )
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers

    # Even the correct password is refused while the window is open.
    assert api.post(
        "/api/accounts/login",
        json={"email": "dana@example.com", "password": "correct-horse-battery"},
    ).status_code == 429


def test_a_success_clears_the_failure_count(api, operator_factory):
    operator_factory()
    for _ in range(accounts.MAX_LOGIN_ATTEMPTS - 1):
        api.post("/api/accounts/login",
                 json={"email": "dana@example.com", "password": "wrong"})

    assert api.post(
        "/api/accounts/login",
        json={"email": "dana@example.com", "password": "correct-horse-battery"},
    ).status_code == 200
    # The counter reset, so a fresh mistake is a 401 rather than a 429.
    assert api.post(
        "/api/accounts/login",
        json={"email": "dana@example.com", "password": "wrong"},
    ).status_code == 401


def test_throttling_is_per_account(api, operator_factory):
    operator_factory(email="a@example.com")
    operator_factory(company_name="Bob", email="b@example.com")

    for _ in range(accounts.MAX_LOGIN_ATTEMPTS + 1):
        api.post("/api/accounts/login",
                 json={"email": "a@example.com", "password": "wrong"})

    # One account being attacked must not lock everybody else out.
    assert api.post(
        "/api/accounts/login",
        json={"email": "b@example.com", "password": "correct-horse-battery"},
    ).status_code == 200
