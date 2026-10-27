"""One person, several businesses.

The feature is a convenience; what these tests guard is that it stays
only that. The failures that would matter:

  * a session for one business reading or changing another, or a switch
    landing somebody in a business that is not theirs;
  * a role carrying across businesses, so an owner of one is an owner of
    the next;
  * one business's owner able to take over somebody's sign-in, which is
    their way into every other business they have;
  * a person switched off at their sign-in still able to switch around.
"""

from store import STORE

DANA = {
    "company_name": "Blue Harbor Club",
    "full_name": "Dana Reyes",
    "email": "dana@blueharbor.example",
    "password": "correct-horse-battery",
    "contact_phone": "+15550100",
    "industry_vertical": "country_club",
}
SAM = {
    "company_name": "Corner Pharmacy",
    "full_name": "Sam Ortiz",
    "email": "sam@corner.example",
    "password": "another-long-password",
    "contact_phone": "+15550199",
    "industry_vertical": "pharmacy",
}


def _signup(api, who):
    resp = api.post("/api/signup", json=who)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _login(api, who):
    resp = api.post(
        "/api/accounts/login", json={"email": who["email"], "password": who["password"]}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _list(api, token):
    resp = api.get("/api/accounts/businesses", headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---- adding a business, and moving between them ------------------------


def test_an_owner_adds_a_second_business_and_lands_in_it(api):
    first = _signup(api, DANA)
    added = api.post(
        "/api/accounts/businesses",
        headers=_bearer(first["token"]),
        json={"company_name": "Harbor Bakery", "industry_vertical": "restaurant"},
    )
    assert added.status_code == 201, added.text
    body = added.json()
    assert body["tenant"]["company_name"] == "Harbor Bakery"
    assert body["tenant"]["plan"] == "trial"
    assert body["user"]["role"] == "owner"
    assert body["api_key"]

    me = api.get("/api/accounts/me", headers=_bearer(body["token"])).json()
    assert me["tenant"]["company_name"] == "Harbor Bakery"
    # The session it came from is closed, not left open on the old business.
    assert api.get("/api/accounts/me", headers=_bearer(first["token"])).status_code == 401


def test_the_list_shows_every_business_and_which_is_open(api):
    first = _signup(api, DANA)
    added = api.post(
        "/api/accounts/businesses",
        headers=_bearer(first["token"]),
        json={"company_name": "Harbor Bakery"},
    ).json()
    listing = _list(api, added["token"])
    names = [b["company_name"] for b in listing["businesses"]]
    assert names == ["Blue Harbor Club", "Harbor Bakery"]
    assert [b["current"] for b in listing["businesses"]] == [False, True]
    assert listing["person"]["email"] == DANA["email"]


def test_switching_opens_the_other_business_and_only_that(api):
    first = _signup(api, DANA)
    club_id = first["tenant"]["tenant_id"]
    bakery = api.post(
        "/api/accounts/businesses",
        headers=_bearer(first["token"]),
        json={"company_name": "Harbor Bakery"},
    ).json()

    back = api.post(
        f"/api/accounts/businesses/{club_id}/switch", headers=_bearer(bakery["token"])
    )
    assert back.status_code == 200, back.text
    token = back.json()["token"]
    assert api.get("/api/accounts/me", headers=_bearer(token)).json()["tenant"][
        "tenant_id"
    ] == club_id
    assert api.get("/api/accounts/me", headers=_bearer(bakery["token"])).status_code == 401


def test_one_sign_in_reaches_both(api):
    """The email signs in once; the second business is not a second login."""
    first = _signup(api, DANA)
    api.post(
        "/api/accounts/businesses",
        headers=_bearer(first["token"]),
        json={"company_name": "Harbor Bakery"},
    )
    session = _login(api, DANA)
    assert session["tenant"]["company_name"] == "Blue Harbor Club"
    assert _list(api, session["token"])["count"] == 2


def test_the_overview_counts_what_needs_you_in_each(api):
    first = _signup(api, DANA)
    key = {"X-CyberLogix-Key": first["api_key"]}
    assert api.post(
        "/api/licenses/me/sensors", headers=key,
        json={"sensor_id": "UNIT-1", "industry_vertical": "country_club",
              "location_name": "Main site"},
    ).status_code == 201
    breach = api.post(
        "/api/sensor-pulse", headers=key,
        json={"sensor_id": "UNIT-1", "temperature_fahrenheit": 90.0},
    )
    assert breach.json()["status"].startswith("CRITICAL")

    bakery = api.post(
        "/api/accounts/businesses",
        headers=_bearer(first["token"]),
        json={"company_name": "Harbor Bakery"},
    ).json()
    listing = _list(api, bakery["token"])
    club, shop = listing["businesses"]
    assert club["open_alerts"] == 1 and club["sensors"] == 1 and club["needs_attention"]
    assert shop["open_alerts"] == 0 and shop["sensors"] == 0 and not shop["needs_attention"]
    assert listing["totals"]["open_alerts"] == 1


# ---- the walls between businesses ----------------------------------------


def test_a_switch_into_somebody_elses_business_is_refused(api):
    dana = _signup(api, DANA)
    sam = _signup(api, SAM)
    resp = api.post(
        f"/api/accounts/businesses/{sam['tenant']['tenant_id']}/switch",
        headers=_bearer(dana["token"]),
    )
    assert resp.status_code == 404
    # The same answer as a business that does not exist at all, so the
    # route cannot be used to find out which tenant ids are real.
    nowhere = api.post(
        "/api/accounts/businesses/TEN-NOPE/switch", headers=_bearer(dana["token"])
    )
    assert nowhere.status_code == 404
    assert resp.json()["detail"] == nowhere.json()["detail"]


def test_data_stays_with_its_business_after_a_switch(api):
    first = _signup(api, DANA)
    key = {"X-CyberLogix-Key": first["api_key"]}
    api.post(
        "/api/licenses/me/sensors", headers=key,
        json={"sensor_id": "UNIT-1", "industry_vertical": "country_club",
              "location_name": "Main site"},
    )
    bakery = api.post(
        "/api/accounts/businesses",
        headers=_bearer(first["token"]),
        json={"company_name": "Harbor Bakery"},
    ).json()
    seen = api.get("/api/licenses/me", headers=_bearer(bakery["token"])).json()
    assert seen["tenant_id"] == bakery["tenant"]["tenant_id"]
    assert STORE.sensors_for(bakery["tenant"]["tenant_id"]) == []


def test_a_role_does_not_cross_from_one_business_to_another(api):
    """Owner of your own place, viewer at the one you were invited into."""
    dana = _signup(api, DANA)
    sam = _signup(api, SAM)
    invited = api.post(
        "/api/accounts/users", headers=_bearer(sam["token"]),
        json={"email": DANA["email"], "full_name": "Dana Reyes",
              "password": "ignored-for-existing", "role": "viewer"},
    )
    assert invited.status_code == 201, invited.text
    assert invited.json()["linked"] is True

    opened = api.post(
        f"/api/accounts/businesses/{sam['tenant']['tenant_id']}/switch",
        headers=_bearer(dana["token"]),
    ).json()
    assert opened["user"]["role"] == "viewer"
    # A viewer cannot invite people, even though they own another business.
    resp = api.post(
        "/api/accounts/users", headers=_bearer(opened["token"]),
        json={"email": "new@x.example", "full_name": "New", "password":
              "long-enough-password", "role": "viewer"},
    )
    assert resp.status_code == 403
    # And cannot start new businesses from somebody else's company.
    resp = api.post(
        "/api/accounts/businesses", headers=_bearer(opened["token"]),
        json={"company_name": "Sneaky Trial"},
    )
    assert resp.status_code == 403


def test_adding_an_existing_person_keeps_their_password(api):
    _signup(api, DANA)
    sam = _signup(api, SAM)
    api.post(
        "/api/accounts/users", headers=_bearer(sam["token"]),
        json={"email": DANA["email"], "full_name": "Somebody Else",
              "password": "attacker-chosen-password", "role": "operator"},
    )
    # The password typed by the other business's owner opens nothing.
    wrong = api.post(
        "/api/accounts/login",
        json={"email": DANA["email"], "password": "attacker-chosen-password"},
    )
    assert wrong.status_code == 401
    assert _login(api, DANA)["user"]["full_name"] == "Dana Reyes"


def test_adding_the_same_person_twice_is_refused(api):
    _signup(api, DANA)
    sam = _signup(api, SAM)
    body = {"email": DANA["email"], "full_name": "Dana", "password":
            "whatever-long-one", "role": "viewer"}
    assert api.post("/api/accounts/users", headers=_bearer(sam["token"]), json=body).status_code == 201
    assert api.post("/api/accounts/users", headers=_bearer(sam["token"]), json=body).status_code == 409


def test_another_business_cannot_reset_your_password(api):
    """Their reset would be a key to every business you have."""
    _signup(api, DANA)
    sam = _signup(api, SAM)
    linked = api.post(
        "/api/accounts/users", headers=_bearer(sam["token"]),
        json={"email": DANA["email"], "full_name": "Dana", "password":
              "whatever-long-one", "role": "operator"},
    ).json()
    resp = api.post(
        f"/api/accounts/users/{linked['user_id']}/reset", headers=_bearer(sam["token"])
    )
    assert resp.status_code == 409
    assert _login(api, DANA)["token"]


def test_removing_someone_from_your_business_leaves_their_own_alone(api):
    dana = _signup(api, DANA)
    sam = _signup(api, SAM)
    linked = api.post(
        "/api/accounts/users", headers=_bearer(sam["token"]),
        json={"email": DANA["email"], "full_name": "Dana", "password":
              "whatever-long-one", "role": "operator"},
    ).json()
    assert api.post(
        f"/api/accounts/users/{linked['user_id']}/disable", headers=_bearer(sam["token"])
    ).status_code == 200

    # Dana is still fine at her own business...
    assert api.get("/api/accounts/me", headers=_bearer(dana["token"])).status_code == 200
    # ...but can no longer open Sam's.
    resp = api.post(
        f"/api/accounts/businesses/{sam['tenant']['tenant_id']}/switch",
        headers=_bearer(dana["token"]),
    )
    assert resp.status_code == 404
    row = [b for b in _list(api, dana["token"])["businesses"]
           if b["tenant_id"] == sam["tenant"]["tenant_id"]][0]
    assert row["removed"] and not row["can_open"]


def test_switching_off_a_sign_in_closes_every_business_it_reaches(api):
    first = _signup(api, DANA)
    bakery = api.post(
        "/api/accounts/businesses",
        headers=_bearer(first["token"]),
        json={"company_name": "Harbor Bakery"},
    ).json()
    person = STORE.user_by_email(DANA["email"])
    STORE.set_user_disabled(person, True)
    assert api.get("/api/accounts/me", headers=_bearer(bakery["token"])).status_code == 401
    assert api.get("/api/licenses/me", headers=_bearer(bakery["token"])).status_code == 401


def test_a_password_change_from_any_business_is_the_one_password(api):
    first = _signup(api, DANA)
    bakery = api.post(
        "/api/accounts/businesses",
        headers=_bearer(first["token"]),
        json={"company_name": "Harbor Bakery"},
    ).json()
    resp = api.post(
        "/api/accounts/me/password", headers=_bearer(bakery["token"]),
        json={"current_password": DANA["password"], "new_password": "brand-new-password-1"},
    )
    assert resp.status_code == 200, resp.text
    assert _login(api, {**DANA, "password": "brand-new-password-1"})["token"]


def test_only_an_owner_can_add_a_business(api):
    sam = _signup(api, SAM)
    api.post(
        "/api/accounts/users", headers=_bearer(sam["token"]),
        json={"email": "op@corner.example", "full_name": "Op", "password":
              "operator-password-1", "role": "operator"},
    )
    op = _login(api, {"email": "op@corner.example", "password": "operator-password-1"})
    resp = api.post(
        "/api/accounts/businesses", headers=_bearer(op["token"]),
        json={"company_name": "Side Hustle"},
    )
    assert resp.status_code == 403


def test_there_is_a_ceiling_on_businesses(api, monkeypatch):
    import businesses

    monkeypatch.setattr(businesses, "MAX_BUSINESSES_PER_PERSON", 2)
    first = _signup(api, DANA)
    second = api.post(
        "/api/accounts/businesses", headers=_bearer(first["token"]),
        json={"company_name": "Two"},
    )
    assert second.status_code == 201
    third = api.post(
        "/api/accounts/businesses", headers=_bearer(second.json()["token"]),
        json={"company_name": "Three"},
    )
    assert third.status_code == 409


def test_a_linked_record_survives_a_restart(api):
    """The email index must still point at the sign-in after reloading."""
    first = _signup(api, DANA)
    api.post(
        "/api/accounts/businesses", headers=_bearer(first["token"]),
        json={"company_name": "Harbor Bakery"},
    )
    STORE.forget()
    STORE.load()
    person = STORE.user_by_email(DANA["email"])
    assert person.linked_to is None
    assert len(STORE.logins_of(person)) == 2


def test_a_switched_off_sign_in_is_refused_even_with_a_live_session(api):
    """Belt and braces: disabling revokes sessions, but a session that
    outlived that (restored from a backup taken before, say) must still
    not open a linked business."""
    first = _signup(api, DANA)
    bakery = api.post(
        "/api/accounts/businesses",
        headers=_bearer(first["token"]),
        json={"company_name": "Harbor Bakery"},
    ).json()
    STORE.user_by_email(DANA["email"]).disabled = True  # no revocation
    assert api.get("/api/accounts/me", headers=_bearer(bakery["token"])).status_code == 401
