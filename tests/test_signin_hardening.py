"""Sign-in fixes from the secrets review.

  * A reset token an owner issued for one of their people could be kept and
    redeemed after that person had joined other businesses: the owner then
    signed in as them and walked into businesses that were not theirs.
  * An invited person's first password is the inviter's choice, so the
    inviter knows it; it must be changed before the sign-in grows.
  * Changing a password left every other session signed in.
  * Eight wrong guesses from anybody locked the real owner out.
  * A bearer header of only odd whitespace was an unauthenticated 500.
"""

import pytest

import accounts
import signup
from store import STORE
from test_businesses import DANA, SAM, _bearer, _join, _login, _own_password, _signup


@pytest.fixture(autouse=True)
def clear_throttle():
    accounts._login_attempts.clear()
    yield
    accounts._login_attempts.clear()


OP = {"email": "op@x.example", "password": "owner-chose-this-1"}


def _invite_op(api, owner_token, role="owner"):
    resp = api.post("/api/accounts/users", headers=_bearer(owner_token),
                    json={"email": OP["email"], "full_name": "Op", "password": OP["password"], "role": role})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _owner_reset(api, owner_token, user_id):
    resp = api.post(f"/api/accounts/users/{user_id}/reset", headers=_bearer(owner_token))
    assert resp.status_code == 200, resp.text
    return resp.json()["reset_token"]


def test_an_invited_person_must_set_their_own_password_before_joining_others(api):
    sam = _signup(api, SAM)
    invited = _invite_op(api, sam["token"])
    assert invited["must_change_password"] is True
    op = _login(api, OP)
    assert op["user"]["must_change_password"] is True

    dana = _signup(api, DANA)
    code = api.post("/api/accounts/businesses/join-codes", headers=_bearer(dana["token"]),
                    json={"role": "owner"}).json()["code"]
    refused = api.post("/api/accounts/businesses/join", headers=_bearer(op["token"]), json={"code": code})
    assert refused.status_code == 409
    assert "Change it" in refused.json()["detail"]
    started = api.post("/api/accounts/businesses", headers=_bearer(op["token"]),
                       json={"company_name": "Op's Own Cafe"})
    assert started.status_code == 409

    _own_password(api, op["token"], OP["password"], "only-op-knows-this-2")
    me = api.get("/api/accounts/me", headers=_bearer(op["token"])).json()
    assert me["user"]["must_change_password"] is False
    _join(api, dana["token"], op["token"], role="owner")


def test_an_old_owner_reset_dies_when_the_password_changes(api):
    """The attack from the review, step by step."""
    sam = _signup(api, SAM)
    invited = _invite_op(api, sam["token"])
    kept = _owner_reset(api, sam["token"], invited["user_id"])    # Sam keeps this
    op = _login(api, OP)
    _own_password(api, op["token"], OP["password"], "only-op-knows-this-2")
    dana = _signup(api, DANA)
    _join(api, dana["token"], op["token"], role="owner")

    resp = api.post("/api/accounts/reset", json={"token": kept, "new_password": "sam-takes-over-123"})
    assert resp.status_code == 400
    assert _login(api, {"email": OP["email"], "password": "only-op-knows-this-2"})["token"]


def test_an_owner_reset_is_rechecked_when_redeemed(api):
    """Even without a password change in between (say the person was linked
    some other way), the token is refused once the sign-in is shared."""
    sam = _signup(api, SAM)
    invited = _invite_op(api, sam["token"])
    kept = _owner_reset(api, sam["token"], invited["user_id"])
    dana = _signup(api, DANA)
    STORE.link_user(STORE.user_by_email(OP["email"]), dana["tenant"]["tenant_id"], "owner")

    resp = api.post("/api/accounts/reset", json={"token": kept, "new_password": "sam-takes-over-123"})
    assert resp.status_code == 400
    # And it is gone, not merely refused this once.
    assert resp.status_code == api.post(
        "/api/accounts/reset", json={"token": kept, "new_password": "sam-takes-over-123"}).status_code


def test_a_password_set_through_an_owner_reset_counts_as_the_owners(api):
    sam = _signup(api, SAM)
    invited = _invite_op(api, sam["token"])
    op = _login(api, OP)
    _own_password(api, op["token"], OP["password"], "only-op-knows-this-2")
    token = _owner_reset(api, sam["token"], invited["user_id"])
    assert api.post("/api/accounts/reset", json={"token": token, "new_password": "chosen-via-reset-3"}).status_code == 200
    again = _login(api, {"email": OP["email"], "password": "chosen-via-reset-3"})
    assert again["user"]["must_change_password"] is True


def test_a_self_service_reset_still_works_for_somebody_with_several_businesses(api):
    dana = _signup(api, DANA)
    _signup(api, SAM)
    sam = _login(api, SAM)
    _join(api, sam["token"], dana["token"])
    person = STORE.user_by_email(DANA["email"])
    token = STORE.issue_reset(person)          # what "forgot password" mails
    assert api.post("/api/accounts/reset", json={"token": token, "new_password": "dana-new-password-9"}).status_code == 200
    assert _login(api, {"email": DANA["email"], "password": "dana-new-password-9"})["user"]["must_change_password"] is False


def test_changing_a_password_signs_out_every_other_session(api):
    dana = _signup(api, DANA)
    other = _login(api, DANA)
    _own_password(api, dana["token"], DANA["password"], "a-brand-new-password")
    assert api.get("/api/accounts/me", headers=_bearer(other["token"])).status_code == 401
    assert api.get("/api/accounts/me", headers=_bearer(dana["token"])).status_code == 200


def test_changing_a_password_cancels_outstanding_resets(api):
    dana = _signup(api, DANA)
    token = STORE.issue_reset(STORE.user_by_email(DANA["email"]))
    _own_password(api, dana["token"], DANA["password"], "a-brand-new-password")
    assert api.post("/api/accounts/reset", json={"token": token, "new_password": "attacker-pass-1234"}).status_code == 400


def test_somebody_else_guessing_does_not_lock_the_owner_out(api, monkeypatch):
    _signup(api, DANA)
    caller = {"ip": "203.0.113.9"}
    monkeypatch.setattr(signup, "caller_key", lambda request: caller["ip"])
    for _ in range(accounts.MAX_LOGIN_ATTEMPTS):
        api.post("/api/accounts/login", json={"email": DANA["email"], "password": "wrong-guess"})
    blocked = api.post("/api/accounts/login", json={"email": DANA["email"], "password": DANA["password"]})
    assert blocked.status_code == 429          # the guesser is stopped...
    caller["ip"] = "198.51.100.7"
    assert api.post("/api/accounts/login",
                    json={"email": DANA["email"], "password": DANA["password"]}).status_code == 200  # ...the owner is not


def test_one_caller_spraying_many_addresses_is_stopped(api, monkeypatch):
    monkeypatch.setattr(signup, "caller_key", lambda request: "203.0.113.9")
    codes = [api.post("/api/accounts/login", json={"email": f"u{i}@x.example", "password": "Summer2026!"}).status_code
             for i in range(accounts.MAX_LOGIN_ATTEMPTS_PER_CALLER + 2)]
    assert codes[-1] == 429
    assert codes.count(401) == accounts.MAX_LOGIN_ATTEMPTS_PER_CALLER


def test_many_callers_guessing_one_address_are_capped(api, monkeypatch):
    _signup(api, DANA)
    n = {"i": 0}

    def rotating(request):
        n["i"] += 1
        return f"10.0.{n['i'] // 250}.{n['i'] % 250}"

    monkeypatch.setattr(signup, "caller_key", rotating)
    codes = [api.post("/api/accounts/login", json={"email": DANA["email"], "password": f"guess-{i}"}).status_code
             for i in range(accounts.MAX_LOGIN_ATTEMPTS_PER_ADDRESS + 2)]
    assert codes[-1] == 429


@pytest.mark.parametrize("header", ["Bearer \xa0", "Bearer \x85", "Bearer ", "bearer"])
def test_odd_bearer_headers_are_refused_not_crashes(api, header):
    resp = api.post("/api/accounts/logout", headers={"Authorization": header.encode("latin-1")})
    assert resp.status_code == 401


def test_a_sent_reset_email_is_not_kept_with_its_link(api, mailbox):
    _signup(api, DANA)
    accounts.reset_rate_limits()
    assert api.post("/api/accounts/forgot", json={"email": DANA["email"]}).status_code == 200
    reset_mail = [m for m in mailbox if "Reset" in m["Subject"]]
    assert reset_mail and "clr_" in reset_mail[0].get_content()     # the person got the link
    stored = [m for m in STORE.mail_log() if "Reset" in m.subject]
    assert stored and "clr_" not in stored[0].body                   # the log did not keep it
    assert "clr_" not in stored[0].dedupe_key


def test_a_reset_email_that_never_went_is_scrubbed_too(api, monkeypatch):
    import mail
    from datetime import timedelta
    from store import utc_now

    monkeypatch.setattr(mail, "configured", lambda: False)
    _signup(api, DANA)
    accounts.reset_rate_limits()
    api.post("/api/accounts/forgot", json={"email": DANA["email"]})
    queued = [m for m in STORE.mail_log() if "Reset" in m.subject][0]
    assert "clr_" in queued.body                                     # still needed to send it
    STORE.scrub_stale_sensitive_mail(now=utc_now() + timedelta(days=30))
    assert "clr_" not in queued.body
