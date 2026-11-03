"""Bringing somebody onto the team, end to end.

An owner makes a one-time join code, hands it over themselves, and the
person enters it from their own signed-in account. Both have to act, so
no business can attach itself to a sign-in by knowing an email address.

Walked here in the order it happens, and in the ways it goes wrong: the
code is used, its role is the role they get and can be changed, an
unused code can be withdrawn and a member removed, a code that has run
out or been used does nothing, and a code only ever reaches the business
that made it.
"""

from datetime import timedelta

from store import STORE, utc_now

OWNER = {
    "company_name": "Corner Pharmacy", "full_name": "Sam Ortiz",
    "email": "sam@corner.example", "password": "another-long-password",
    "contact_phone": "+15550199", "industry_vertical": "pharmacy",
}
NEWCOMER = {
    "company_name": "Blue Harbor Club", "full_name": "Dana Reyes",
    "email": "dana@blueharbor.example", "password": "correct-horse-battery",
    "contact_phone": "+15550100", "industry_vertical": "country_club",
}
OTHER = {
    "company_name": "Lee Deli", "full_name": "Lee Park",
    "email": "lee@deli.example", "password": "a-third-long-password",
    "contact_phone": "+15550177", "industry_vertical": "restaurant",
}
CODES = "/api/accounts/businesses/join-codes"
JOIN = "/api/accounts/businesses/join"


def _signup(api, who):
    resp = api.post("/api/signup", json=who)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _invite(api, token, role="operator"):
    resp = api.post(CODES, headers=_h(token), json={"role": role})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _open(api, token, tenant_id):
    resp = api.post(f"/api/accounts/businesses/{tenant_id}/switch", headers=_h(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _member(api, owner_token, email):
    users = api.get("/api/accounts/users", headers=_h(owner_token)).json()["users"]
    return next((u for u in users if u["email"] == email), None)


def test_invite_accept_and_the_role_is_the_one_on_the_code(api):
    sam, dana = _signup(api, OWNER), _signup(api, NEWCOMER)
    made = _invite(api, sam["token"], role="viewer")

    # The owner sees the code is waiting, but never the code itself again.
    waiting = api.get(CODES, headers=_h(sam["token"])).json()
    assert [c["code_id"] for c in waiting["codes"]] == [made["code_id"]]
    assert waiting["codes"][0]["role"] == "viewer"
    assert waiting["codes"][0]["issued_by"] == "Sam Ortiz"
    assert made["code"] not in str(waiting)

    joined = api.post(JOIN, headers=_h(dana["token"]), json={"code": made["code"]})
    assert joined.status_code == 200, joined.text
    assert joined.json()["role"] == "viewer"
    assert joined.json()["tenant_id"] == sam["tenant"]["tenant_id"]

    # Used, so no longer waiting; and Dana is on the team, as a viewer.
    assert api.get(CODES, headers=_h(sam["token"])).json()["codes"] == []
    assert _member(api, sam["token"], NEWCOMER["email"])["role"] == "viewer"
    trail = api.get("/api/accounts/audit?limit=10", headers=_h(sam["token"])).json()
    assert any(e["action"] == "account.joined" for e in trail["entries"])

    # A viewer reads but cannot change anything.
    there = _open(api, dana["token"], sam["tenant"]["tenant_id"])
    assert api.get("/api/console/overview", headers=_h(there)).status_code == 200
    refused = api.post("/api/contacts", headers=_h(there),
                       json={"full_name": "X", "phone": "+15550123"})
    assert refused.status_code == 403
    assert api.post(CODES, headers=_h(there), json={"role": "owner"}).status_code == 403


def test_the_owner_changes_the_role_and_it_takes_effect(api):
    sam, dana = _signup(api, OWNER), _signup(api, NEWCOMER)
    code = _invite(api, sam["token"], role="viewer")["code"]
    api.post(JOIN, headers=_h(dana["token"]), json={"code": code})
    member = _member(api, sam["token"], NEWCOMER["email"])

    changed = api.post(f"/api/accounts/users/{member['user_id']}/role",
                       headers=_h(sam["token"]), json={"role": "operator"})
    assert changed.status_code == 200, changed.text

    there = _open(api, dana["token"], sam["tenant"]["tenant_id"])
    allowed = api.post("/api/contacts", headers=_h(there),
                       json={"full_name": "Night pharmacist", "phone": "+15550123"})
    assert allowed.status_code == 201, allowed.text
    # And the role is this business's only: Dana's own business is untouched.
    home = _open(api, there, dana["tenant"]["tenant_id"])
    assert _member(api, home, NEWCOMER["email"])["role"] == "owner"


def test_an_unused_code_can_be_withdrawn(api):
    sam, dana = _signup(api, OWNER), _signup(api, NEWCOMER)
    made = _invite(api, sam["token"])

    withdrawn = api.delete(f"{CODES}/{made['code_id']}", headers=_h(sam["token"]))
    assert withdrawn.status_code == 200, withdrawn.text
    assert api.get(CODES, headers=_h(sam["token"])).json()["codes"] == []

    late = api.post(JOIN, headers=_h(dana["token"]), json={"code": made["code"]})
    assert late.status_code == 404
    assert _member(api, sam["token"], NEWCOMER["email"]) is None
    # Twice is nothing to withdraw.
    assert api.delete(f"{CODES}/{made['code_id']}",
                      headers=_h(sam["token"])).status_code == 404


def test_a_member_can_be_removed_and_cannot_come_back_on_the_same_code(api):
    sam, dana = _signup(api, OWNER), _signup(api, NEWCOMER)
    code = _invite(api, sam["token"])["code"]
    api.post(JOIN, headers=_h(dana["token"]), json={"code": code})
    member = _member(api, sam["token"], NEWCOMER["email"])

    assert api.post(f"/api/accounts/users/{member['user_id']}/disable",
                    headers=_h(sam["token"])).status_code == 200
    assert api.post(f"/api/accounts/businesses/{sam['tenant']['tenant_id']}/switch",
                    headers=_h(dana["token"])).status_code == 404
    assert api.post(JOIN, headers=_h(dana["token"]),
                    json={"code": code}).status_code in (404, 409)


def test_an_expired_code_does_nothing(api):
    sam, dana = _signup(api, OWNER), _signup(api, NEWCOMER)
    made = _invite(api, sam["token"])
    for code in STORE.join_codes_for(sam["tenant"]["tenant_id"]):
        code.expires_at = utc_now() - timedelta(minutes=1)

    assert api.get(CODES, headers=_h(sam["token"])).json()["codes"] == []
    resp = api.post(JOIN, headers=_h(dana["token"]), json={"code": made["code"]})
    assert resp.status_code == 404
    assert "not valid" in resp.json()["detail"]
    assert _member(api, sam["token"], NEWCOMER["email"]) is None
    assert api.delete(f"{CODES}/{made['code_id']}",
                      headers=_h(sam["token"])).status_code == 404


def test_a_code_works_once_and_never_for_a_second_person(api):
    sam, dana, lee = _signup(api, OWNER), _signup(api, NEWCOMER), _signup(api, OTHER)
    code = _invite(api, sam["token"])["code"]
    assert api.post(JOIN, headers=_h(dana["token"]), json={"code": code}).status_code == 200

    reused = api.post(JOIN, headers=_h(lee["token"]), json={"code": code})

    assert reused.status_code == 404
    assert _member(api, sam["token"], OTHER["email"]) is None


def test_a_code_reaches_only_the_business_that_made_it(api):
    """Entered from inside Dana's own business, Sam's code adds Sam's
    business -- not Dana's, and nothing of Dana's changes hands."""
    sam, dana = _signup(api, OWNER), _signup(api, NEWCOMER)
    code = _invite(api, sam["token"])["code"]

    joined = api.post(JOIN, headers=_h(dana["token"]), json={"code": code}).json()

    assert joined["tenant_id"] == sam["tenant"]["tenant_id"]
    assert _member(api, dana["token"], OWNER["email"]) is None
    assert {u["email"] for u in api.get("/api/accounts/users",
                                        headers=_h(dana["token"])).json()["users"]} == {
        NEWCOMER["email"]}


def test_another_businesss_owner_cannot_see_or_withdraw_the_code(api):
    sam, lee = _signup(api, OWNER), _signup(api, OTHER)
    made = _invite(api, sam["token"])

    assert api.get(CODES, headers=_h(lee["token"])).json()["codes"] == []
    assert api.delete(f"{CODES}/{made['code_id']}",
                      headers=_h(lee["token"])).status_code == 404
    # Still there, still Sam's to give.
    assert [c["code_id"] for c in api.get(CODES, headers=_h(sam["token"])).json()["codes"]] == [
        made["code_id"]]


def test_only_an_owner_lists_or_withdraws_codes(api):
    sam, dana = _signup(api, OWNER), _signup(api, NEWCOMER)
    first = _invite(api, sam["token"], role="operator")
    api.post(JOIN, headers=_h(dana["token"]), json={"code": first["code"]})
    second = _invite(api, sam["token"])
    there = _open(api, dana["token"], sam["tenant"]["tenant_id"])

    assert api.get(CODES, headers=_h(there)).status_code == 403
    assert api.delete(f"{CODES}/{second['code_id']}", headers=_h(there)).status_code == 403


def test_a_code_is_for_somebody_who_has_set_their_own_password(api):
    """Somebody added with a password the owner chose has to choose their
    own before joining anything else, or that owner could follow them."""
    sam, lee = _signup(api, OWNER), _signup(api, OTHER)
    api.post("/api/accounts/users", headers=_h(sam["token"]), json={
        "email": "temp@corner.example", "full_name": "Temp", "role": "operator",
        "password": "owner-chosen-pass-1"})
    temp = api.post("/api/accounts/login", json={
        "email": "temp@corner.example", "password": "owner-chosen-pass-1"}).json()
    code = _invite(api, lee["token"])["code"]

    resp = api.post(JOIN, headers=_h(temp["token"]), json={"code": code})

    assert resp.status_code in (403, 409), resp.text
    # The code was not spent on the refusal.
    assert len(api.get(CODES, headers=_h(lee["token"])).json()["codes"]) == 1
