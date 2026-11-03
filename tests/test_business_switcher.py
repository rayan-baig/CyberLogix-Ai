"""The business switcher with five businesses, as one person uses it.

Dana owns three businesses, is an operator at a fourth that Sam owns and
a viewer at a fifth that Kim owns. Each business has its own units, its
own alert, its own on-call list and its own team. After every switch the
console's reads must show that business and nothing of the other four,
Dana's role must be the one she holds there, and the session she
switched from must be dead. Signing in again lands where she left off.

What the browser does with all of this at phone size (the switcher
drawer, the reload, nothing sideways) is in tools/console_smoke.js.
"""

from __future__ import annotations

import pytest

import signup
from store import STORE

PASSWORD = "correct-horse-battery"

PEOPLE = {
    "dana": {"full_name": "Dana Reyes", "email": "dana@harbor.example"},
    "sam": {"full_name": "Sam Ortiz", "email": "sam@ortiz.example"},
    "kim": {"full_name": "Kim Rowe", "email": "kim@rowe.example"},
}

# name, who owns it, Dana's role there, its one unit
BUSINESSES = [
    ("Harbor Grill", "dana", "owner", "GRILL-WALKIN"),
    ("Harbor Bakery", "dana", "owner", "BAKERY-PROOF"),
    ("Harbor Deli", "dana", "owner", "DELI-COUNTER"),
    ("Ortiz Pharmacy", "sam", "operator", "ORTIZ-VACCINE"),
    ("Rowe Florist", "kim", "viewer", "ROWE-COOLER"),
]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _signup(api, who, company):
    resp = api.post("/api/signup", json={
        "company_name": company, "password": PASSWORD,
        "contact_phone": "+15550100", "industry_vertical": "restaurant",
        **PEOPLE[who]})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(api, who):
    resp = api.post("/api/accounts/login", json={
        "email": PEOPLE[who]["email"], "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()


class Estate:
    def __init__(self, api):
        self.api = api
        self.ids = {}       # company -> tenant_id
        self.keys = {}      # company -> business key headers
        self.tokens = {}    # who -> latest token

    def remember(self, company, body):
        self.ids[company] = body["tenant"]["tenant_id"]
        self.keys[company] = {"X-CyberLogix-Key": body["api_key"]}

    def build(self):
        api = self.api
        made = _signup(api, "dana", "Harbor Grill")
        self.remember("Harbor Grill", made)
        token = made["token"]
        for company in ("Harbor Bakery", "Harbor Deli"):
            added = api.post("/api/accounts/businesses", headers=_bearer(token),
                             json={"company_name": company,
                                   "industry_vertical": "restaurant"})
            assert added.status_code == 201, added.text
            self.remember(company, added.json())
            token = added.json()["token"]
        self.tokens["dana"] = token

        for owner, company in (("sam", "Ortiz Pharmacy"), ("kim", "Rowe Florist")):
            made = _signup(api, owner, company)
            self.remember(company, made)
            self.tokens[owner] = made["token"]
            role = next(b[2] for b in BUSINESSES if b[0] == company)
            code = api.post("/api/accounts/businesses/join-codes",
                            headers=_bearer(made["token"]), json={"role": role})
            assert code.status_code == 201, code.text
            joined = api.post("/api/accounts/businesses/join",
                              headers=_bearer(self.tokens["dana"]),
                              json={"code": code.json()["code"]})
            assert joined.status_code == 200, joined.text

        # Each business: one unit, a reading, an alert, its own on-call
        # person and its own team member.
        for company, _, _, unit in BUSINESSES:
            key = self.keys[company]
            assert api.post("/api/licenses/me/sensors", headers=key, json={
                "sensor_id": unit, "industry_vertical": "restaurant",
                "location_name": f"{company} cold room"}).status_code == 201
            assert api.post("/api/sensor-pulse", headers=key, json={
                "sensor_id": unit, "temperature_fahrenheit": 55.0}).status_code == 200
            STORE.add_contact(self.ids[company], f"{company} night manager",
                              "+15550123")
        return self

    def switch(self, company):
        resp = self.api.post(f"/api/accounts/businesses/{self.ids[company]}/switch",
                             headers=_bearer(self.tokens["dana"]))
        assert resp.status_code == 200, resp.text
        old = self.tokens["dana"]
        self.tokens["dana"] = resp.json()["token"]
        return old, resp.json()


@pytest.fixture()
def estate(api, monkeypatch):
    monkeypatch.setattr(signup, "SIGNUPS_PER_CALLER_PER_HOUR", 50)
    return Estate(api).build()


READS = (
    "/api/accounts/me",
    "/api/console/overview",
    "/api/voice/incidents",
    "/api/licenses/me/sensors",
    "/api/accounts/users",
    "/api/accounts/audit",
    "/api/contacts",
    "/api/invoices",
    "/api/sites",
)


def test_five_businesses_listed_with_the_role_in_each(api, estate):
    listed = api.get("/api/accounts/businesses",
                     headers=_bearer(estate.tokens["dana"])).json()
    assert listed["count"] == 5
    roles = {b["company_name"]: b["role"] for b in listed["businesses"]}
    assert roles == {name: role for name, _, role, _ in BUSINESSES}
    assert sum(1 for b in listed["businesses"] if b["current"]) == 1
    assert listed["totals"]["open_alerts"] == 5


@pytest.mark.parametrize("company", [b[0] for b in BUSINESSES])
def test_after_a_switch_every_read_is_that_business_and_no_other(api, estate, company):
    old, opened = estate.switch(company)
    headers = _bearer(estate.tokens["dana"])
    mine = next(b for b in BUSINESSES if b[0] == company)
    others = [b for b in BUSINESSES if b[0] != company]

    assert opened["tenant"]["tenant_id"] == estate.ids[company]
    assert opened["user"]["role"] == mine[2]
    # The session switched from is over.
    assert api.get("/api/accounts/me", headers=_bearer(old)).status_code == 401

    me = api.get("/api/accounts/me", headers=headers).json()
    assert me["tenant"]["tenant_id"] == estate.ids[company]
    assert me["user"]["role"] == mine[2]

    overview = api.get("/api/console/overview", headers=headers).json()
    assert [s["sensor_id"] for s in overview["sensors"]] == [mine[3]]
    assert [i["sensor_id"] for i in overview["incidents"]] == [mine[3]]

    for path in READS:
        resp = api.get(path, headers=headers)
        assert resp.status_code == 200, (path, resp.text)
        text = resp.text
        for name, _, _, unit in others:
            assert name not in text, (company, path, name)
            assert unit not in text, (company, path, unit)
            assert estate.ids[name] not in text, (company, path, name)

    # Asking for another business's unit by its id: not found, as if it
    # did not exist.
    for _, _, _, unit in others:
        assert api.get(f"/api/console/sensor/{unit}", headers=headers).status_code == 404


def test_the_role_is_the_one_held_in_that_business(api, estate):
    # Owner at the grill: makes join codes, acknowledges alerts.
    estate.switch("Harbor Grill")
    owner = _bearer(estate.tokens["dana"])
    assert api.post("/api/accounts/businesses/join-codes", headers=owner,
                    json={"role": "viewer"}).status_code == 201

    # Operator at the pharmacy: acknowledges, but cannot make join codes
    # or invite people.
    estate.switch("Ortiz Pharmacy")
    operator = _bearer(estate.tokens["dana"])
    (incident,) = STORE.open_incidents(estate.ids["Ortiz Pharmacy"])
    assert api.post(f"/api/voice/acknowledge/{incident.incident_id}",
                    headers=operator, json={}).status_code == 200
    assert api.post("/api/accounts/businesses/join-codes", headers=operator,
                    json={"role": "viewer"}).status_code == 403
    assert api.post("/api/accounts/users", headers=operator, json={
        "email": "x@ortiz.example", "full_name": "X", "password": PASSWORD,
        "role": "owner"}).status_code == 403

    # Viewer at the florist: reads, and changes nothing.
    estate.switch("Rowe Florist")
    viewer = _bearer(estate.tokens["dana"])
    (incident,) = STORE.open_incidents(estate.ids["Rowe Florist"])
    assert api.post(f"/api/voice/acknowledge/{incident.incident_id}",
                    headers=viewer, json={}).status_code == 403
    assert api.post("/api/sensor-pulse", headers=viewer, json={
        "sensor_id": "ROWE-COOLER", "temperature_fahrenheit": 30.0}).status_code == 403
    assert api.get("/api/console/overview", headers=viewer).status_code == 200


def test_a_switch_into_a_business_that_is_not_hers_is_refused(api, estate):
    """Sam's other business, or one that does not exist: the same answer."""
    sam_second = api.post("/api/accounts/businesses",
                          headers=_bearer(estate.tokens["sam"]),
                          json={"company_name": "Ortiz Clinic"})
    assert sam_second.status_code == 201
    hidden = sam_second.json()["tenant"]["tenant_id"]
    headers = _bearer(estate.tokens["dana"])
    for target in (hidden, "TEN-999999"):
        resp = api.post(f"/api/accounts/businesses/{target}/switch", headers=headers)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "That is not one of your businesses."
    # And the session she asked with still works.
    assert api.get("/api/accounts/me", headers=headers).status_code == 200


# ---- signing in where you left off ------------------------------------------


def test_signing_in_again_opens_the_business_she_had_open(api, estate):
    for company in ("Ortiz Pharmacy", "Harbor Bakery", "Rowe Florist"):
        estate.switch(company)
        api.post("/api/accounts/logout", headers=_bearer(estate.tokens["dana"]))
        session = _login(api, "dana")
        assert session["tenant"]["tenant_id"] == estate.ids[company]
        assert session["user"]["role"] == next(b[2] for b in BUSINESSES
                                               if b[0] == company)
        estate.tokens["dana"] = session["token"]


def test_it_is_remembered_through_a_restart(api, estate):
    estate.switch("Harbor Deli")
    STORE._forget_locked()
    STORE.load()
    assert _login(api, "dana")["tenant"]["tenant_id"] == estate.ids["Harbor Deli"]


def test_a_business_she_can_no_longer_open_is_passed_over(api, estate, admin_headers):
    """Removed from the last one she had open: she lands in the one before."""
    estate.switch("Harbor Bakery")
    estate.switch("Ortiz Pharmacy")
    users = api.get("/api/accounts/users", headers=_bearer(estate.tokens["sam"])).json()
    dana_there = next(u for u in users["users"] if u["email"] == PEOPLE["dana"]["email"])
    removed = api.post(f"/api/accounts/users/{dana_there['user_id']}/disable",
                       headers=_bearer(estate.tokens["sam"]))
    assert removed.status_code == 200, removed.text

    session = _login(api, "dana")
    assert session["tenant"]["tenant_id"] == estate.ids["Harbor Bakery"]


def test_a_suspended_business_is_passed_over_too(api, estate):
    estate.switch("Harbor Bakery")
    estate.switch("Rowe Florist")
    tenant = STORE.get_tenant(estate.ids["Rowe Florist"])
    STORE.set_suspended(tenant, True)
    assert _login(api, "dana")["tenant"]["tenant_id"] == estate.ids["Harbor Bakery"]


def test_somebody_with_one_business_still_lands_in_it(api, estate):
    assert _login(api, "kim")["tenant"]["tenant_id"] == estate.ids["Rowe Florist"]


def test_what_one_business_holds_about_her_names_no_other(api, estate):
    """Each business's own record of Dana says when she last opened that
    business, and nothing about which of her others she went to."""
    estate.switch("Ortiz Pharmacy")
    estate.switch("Harbor Grill")
    exported = STORE.export_tenant(estate.ids["Ortiz Pharmacy"])
    text = str(exported)
    for name, _, _, _ in BUSINESSES:
        if name != "Ortiz Pharmacy":
            assert name not in text and estate.ids[name] not in text
