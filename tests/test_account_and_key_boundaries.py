"""Who can get in, and to what, after things change.

The failures here are the quiet ones: a switch between businesses that
leaves a session wider than the person's role; somebody removed from a
business who can still walk back in; a partner key that sees a customer
it was never attached to; a key that was rotated because it leaked and
still works; a sensor key that can write another company's readings;
and a password reset that, with mail not set up yet, is lost, reusable
or eternal.
"""

import re
from datetime import timedelta

import pytest

import accounts
import auth
from store import STORE, utc_now

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


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _signup(api, who):
    resp = api.post("/api/signup", json=who)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _join(api, owner_token, joiner_token, role):
    made = api.post("/api/accounts/businesses/join-codes",
                    headers=_bearer(owner_token), json={"role": role})
    assert made.status_code == 201, made.text
    joined = api.post("/api/accounts/businesses/join", headers=_bearer(joiner_token),
                      json={"code": made.json()["code"]})
    assert joined.status_code == 200, joined.text
    return joined.json()


def _switch(api, token, tenant_id):
    return api.post(f"/api/accounts/businesses/{tenant_id}/switch",
                    headers=_bearer(token))


def _record_in(email, tenant_id):
    return STORE.login_in(STORE.user_by_email(email), tenant_id)


# ---- switching business never widens a session ----------------------------


def test_a_switch_gives_exactly_the_role_held_there_and_nothing_of_the_old_one(api):
    dana = _signup(api, DANA)
    sam = _signup(api, SAM)
    _join(api, sam["token"], dana["token"], role="viewer")
    sam_id = sam["tenant"]["tenant_id"]
    dana_id = dana["tenant"]["tenant_id"]

    opened = _switch(api, dana["token"], sam_id)
    assert opened.status_code == 200, opened.text
    as_viewer = _bearer(opened.json()["token"])

    # The session is on Sam's business, as a viewer, and nothing more.
    me = api.get("/api/accounts/me", headers=as_viewer).json()
    assert me["tenant"]["tenant_id"] == sam_id
    assert me["user"]["role"] == "viewer"

    # Owner at her own place; at Sam's she can change nothing.
    for method, path, body in (
        ("post", "/api/licenses/me/sensors",
         {"sensor_id": "X-1", "industry_vertical": "pharmacy", "location_name": "X"}),
        ("post", "/api/contacts", {"full_name": "Intruder", "phone": "+15550123"}),
        ("post", "/api/webhooks", {"kind": "slack",
                                   "target": "https://hooks.slack.com/services/T/B/zzzzzzzz"}),
        ("post", "/api/licenses/me/rotate-key", None),
        ("post", "/api/accounts/businesses/join-codes", {"role": "owner"}),
        ("post", "/api/sensor-pulse", {"sensor_id": "X-1", "temperature_fahrenheit": 40}),
    ):
        resp = getattr(api, method)(path, headers=as_viewer, json=body)
        assert resp.status_code == 403, (path, resp.status_code, resp.text)

    # The session it replaced is gone; it cannot be used to reach either.
    assert api.get("/api/accounts/me", headers=_bearer(dana["token"])).status_code == 401
    # And the new one cannot be pointed at her own business by any parameter.
    seen = api.get("/api/licenses/me", headers=as_viewer,
                   params={"tenant_id": dana_id}).json()
    assert seen["tenant_id"] == sam_id

    # Switching back gives her own owner role again, no later expiry.
    before = STORE.session_by_token(opened.json()["token"]).expires_at
    back = _switch(api, opened.json()["token"], dana_id)
    assert back.status_code == 200
    assert back.json()["user"]["role"] == "owner"
    assert STORE.session_by_token(back.json()["token"]).expires_at <= before


def test_a_session_on_a_suspended_or_missing_business_cannot_switch_anywhere_new(api):
    dana = _signup(api, DANA)
    resp = _switch(api, dana["token"], "TEN-DOES-NOT-EXIST")
    assert resp.status_code == 404
    sam = _signup(api, SAM)
    resp = _switch(api, dana["token"], sam["tenant"]["tenant_id"])
    assert resp.status_code == 404, "a business that is not hers"


# ---- a removed person cannot switch back in --------------------------------


def test_removed_while_inside_the_business_ends_that_session_and_the_way_back(api):
    dana = _signup(api, DANA)
    sam = _signup(api, SAM)
    _join(api, sam["token"], dana["token"], role="operator")
    sam_id = sam["tenant"]["tenant_id"]

    # Dana is inside Sam's business when Sam removes her.
    inside = _switch(api, dana["token"], sam_id).json()["token"]
    linked = _record_in(DANA["email"], sam_id)
    assert api.post(f"/api/accounts/users/{linked.user_id}/disable",
                    headers=_bearer(sam["token"])).status_code == 200

    assert api.get("/api/accounts/me", headers=_bearer(inside)).status_code == 401
    assert api.get("/api/licenses/me", headers=_bearer(inside)).status_code == 401

    # Signing in again lands her in her own business, and from there the
    # switch back into Sam's is refused, however it is asked.
    fresh = api.post("/api/accounts/login",
                     json={"email": DANA["email"], "password": DANA["password"]})
    assert fresh.status_code == 200, fresh.text
    token = fresh.json()["token"]
    assert fresh.json()["tenant"]["tenant_id"] == dana["tenant"]["tenant_id"]
    assert _switch(api, token, sam_id).status_code == 404
    # Including after a restart, which rebuilds every record from disk.
    STORE._forget_locked()
    STORE.load()
    fresh = api.post("/api/accounts/login",
                     json={"email": DANA["email"], "password": DANA["password"]})
    assert _switch(api, fresh.json()["token"], sam_id).status_code == 404


def test_a_join_code_already_spent_does_not_bring_a_removed_person_back(api):
    dana = _signup(api, DANA)
    sam = _signup(api, SAM)
    made = api.post("/api/accounts/businesses/join-codes", headers=_bearer(sam["token"]),
                    json={"role": "operator"}).json()
    assert api.post("/api/accounts/businesses/join", headers=_bearer(dana["token"]),
                    json={"code": made["code"]}).status_code == 200
    linked = _record_in(DANA["email"], sam["tenant"]["tenant_id"])
    api.post(f"/api/accounts/users/{linked.user_id}/disable", headers=_bearer(sam["token"]))

    again = api.post("/api/accounts/businesses/join", headers=_bearer(dana["token"]),
                     json={"code": made["code"]})
    assert again.status_code in (400, 404, 409, 410)
    assert _record_in(DANA["email"], sam["tenant"]["tenant_id"]).disabled


# ---- partner keys see only their own accounts -------------------------------


@pytest.fixture()
def admin(monkeypatch):
    monkeypatch.setattr(auth, "PLATFORM_ADMIN_KEY", "root-key")
    return {"X-CyberLogix-Admin": "root-key"}


def _partner(api, admin, name):
    resp = api.post("/api/partners", headers=admin,
                    json={"company_name": name, "contact_name": "Ray",
                          "contact_email": f"{name.split()[0].lower()}@partner.example"})
    assert resp.status_code == 201, resp.text
    return resp.json()["partner_id"], {"X-CyberLogix-Partner": resp.json()["api_key"]}


def test_a_partner_key_reaches_its_own_accounts_and_nothing_else(
        api, admin, tenant_factory, sensor_factory):
    mine_id, mine = _partner(api, admin, "Mine Refrigeration")
    theirs_id, theirs = _partner(api, admin, "Their Refrigeration")
    my_headers, my_customer = tenant_factory(company_name="My Customer")
    their_headers, their_customer = tenant_factory(company_name="Their Customer")
    _, nobodys = tenant_factory(company_name="Nobody's Customer")
    sensor_factory(my_headers, sensor_id="MINE-1")
    sensor_factory(their_headers, sensor_id="THEIRS-1")
    for pid, tid in ((mine_id, my_customer["tenant_id"]),
                     (theirs_id, their_customer["tenant_id"])):
        assert api.post(f"/api/partners/{pid}/accounts", headers=admin,
                        json={"tenant_id": tid}).status_code == 200

    book = api.get("/api/partners/me", headers=mine)
    assert book.status_code == 200
    assert "My Customer" in book.text
    assert "Their Customer" not in book.text and "Nobody's Customer" not in book.text
    assert "THEIRS-1" not in book.text

    ok = api.get(f"/api/partners/me/accounts/{my_customer['tenant_id']}", headers=mine)
    assert ok.status_code == 200 and "MINE-1" in ok.text
    for other in (their_customer["tenant_id"], nobodys["tenant_id"], "TEN-NOPE"):
        resp = api.get(f"/api/partners/me/accounts/{other}", headers=mine)
        assert resp.status_code == 404, other
    statement = api.get("/api/partners/me/statement", headers=mine).text
    assert "Their Customer" not in statement

    # A partner key is not a business key, an admin key or a session.
    for path in ("/api/licenses/me", "/api/partners", "/api/accounts/me",
                 "/api/voice/incidents"):
        assert api.get(path, headers=mine).status_code in (401, 403), path
    assert api.get("/api/licenses/me",
                   headers={"X-CyberLogix-Key": mine["X-CyberLogix-Partner"]}
                   ).status_code == 401

    # Detached from the account, the partner loses it at once.
    assert api.delete(f"/api/partners/{mine_id}/accounts/{my_customer['tenant_id']}",
                      headers=admin).status_code == 200
    assert api.get(f"/api/partners/me/accounts/{my_customer['tenant_id']}",
                   headers=mine).status_code == 404


# ---- a rotated key stops at once --------------------------------------------


def test_a_rotated_business_key_stops_working_everywhere_at_once(
        api, operator_factory, sensor_factory):
    headers, tenant, _ = operator_factory()
    # The key a script or device holds: the one the business was issued.
    old_key = STORE.rotate_api_key(STORE.get_tenant(tenant["tenant_id"]))
    sensor_factory({"X-CyberLogix-Key": old_key}, sensor_id="FRZ-1")

    rotated = api.post("/api/licenses/me/rotate-key", headers=headers)
    assert rotated.status_code == 200, rotated.text
    new_key = rotated.json()["api_key"]
    assert new_key != old_key

    old = {"X-CyberLogix-Key": old_key}
    reading = {"sensor_id": "FRZ-1", "temperature_fahrenheit": 38.0}
    assert api.get("/api/licenses/me", headers=old).status_code == 401
    assert api.post("/api/sensor-pulse", headers=old, json=reading).status_code == 401
    assert api.post("/api/v1/bridge/sensor-webhook-ingest", json={
        "api_key_token": old_key, "device_sn": "FRZ-1", "reading_value": 38.0,
    }).status_code == 401
    assert api.post("/api/v1/bridge/any", params={"k": old_key},
                    json={"sensor": "FRZ-1", "temp_f": 38.0}).status_code == 401

    # Still dead after a restart; the new one works before and after.
    new = {"X-CyberLogix-Key": new_key}
    assert api.post("/api/sensor-pulse", headers=new, json=reading).status_code == 200
    STORE._forget_locked()
    STORE.load()
    assert api.get("/api/licenses/me", headers=old).status_code == 401
    assert api.get("/api/licenses/me", headers=new).status_code == 200


def test_a_rotated_sensor_key_stops_working_on_every_ingest_route(
        api, operator_factory):
    headers, _, _ = operator_factory()
    made = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "FRZ-1", "industry_vertical": "pharmacy",
        "location_name": "Back room", "external_device_sn": "SN-FRZ-1"})
    assert made.status_code == 201, made.text
    old_key = made.json()["ingest_key"]

    rotated = api.post("/api/licenses/me/sensors/FRZ-1/rotate-key", headers=headers)
    assert rotated.status_code == 200
    new_key = rotated.json()["ingest_key"]

    reading = {"sensor_id": "FRZ-1", "temperature_fahrenheit": 38.0}
    assert api.post("/api/sensor-pulse", headers={"X-CyberLogix-Sensor-Key": old_key},
                    json=reading).status_code == 401
    assert api.post("/api/v1/bridge/sensor-webhook-ingest", json={
        "api_key_token": old_key, "device_sn": "SN-FRZ-1", "reading_value": 38.0,
    }).status_code == 401
    assert api.post("/api/v1/bridge/any", params={"k": old_key},
                    json={"sensor": "SN-FRZ-1", "temp_f": 38.0}).status_code == 401
    STORE._forget_locked()
    STORE.load()
    assert api.post("/api/sensor-pulse", headers={"X-CyberLogix-Sensor-Key": old_key},
                    json=reading).status_code == 401
    assert api.post("/api/sensor-pulse", headers={"X-CyberLogix-Sensor-Key": new_key},
                    json=reading).status_code == 200


# ---- a sensor key cannot post to another business's sensor ------------------


def test_a_sensor_key_cannot_write_another_businesss_sensor(api, operator_factory):
    mine, _, _ = operator_factory(company_name="Mine", email="mine@x.example")
    theirs, _, _ = operator_factory(company_name="Theirs", email="theirs@x.example")

    def register(headers, sensor_id, serial):
        made = api.post("/api/licenses/me/sensors", headers=headers, json={
            "sensor_id": sensor_id, "industry_vertical": "pharmacy",
            "location_name": "Bay", "external_device_sn": serial})
        assert made.status_code == 201, made.text
        return made.json()["ingest_key"]

    my_key = register(mine, "MINE-1", "SN-MINE-1")
    register(theirs, "THEIRS-1", "SN-THEIRS-1")

    hot = {"sensor_id": "THEIRS-1", "temperature_fahrenheit": 99.0}
    resp = api.post("/api/sensor-pulse", headers={"X-CyberLogix-Sensor-Key": my_key},
                    json=hot)
    assert resp.status_code in (403, 404), resp.text

    for body in ({"api_key_token": my_key, "device_sn": "SN-THEIRS-1", "reading_value": 99.0},
                 {"api_key_token": my_key, "device_sn": "THEIRS-1", "reading_value": 99.0}):
        resp = api.post("/api/v1/bridge/sensor-webhook-ingest", json=body)
        assert resp.status_code in (403, 404), resp.text
    # Through "send anything": a payload with its unit, and one without
    # (which would otherwise be held at the door under that serial).
    for payload in ({"SensorID": "SN-THEIRS-1", "DataValues": "37.0",
                     "DataTypes": "TemperatureC"},
                    {"sensor": "SN-THEIRS-1", "temperature": 99.0}):
        resp = api.post("/api/v1/bridge/any", params={"k": my_key}, json=payload)
        assert resp.status_code in (403, 404), resp.text

    # Nothing was written to theirs, and nothing was held at anyone's door
    # under their serial on the strength of my key.
    assert STORE.readings_for("THEIRS-1") == []
    assert STORE.incidents_for(STORE.get_sensor("THEIRS-1").tenant_id) == []
    import doorstep
    for sensor_id in ("MINE-1", "THEIRS-1"):
        assert doorstep.waiting_for(STORE.get_sensor(sensor_id).tenant_id) == []


# ---- password reset, end to end, with no mail set up ------------------------


@pytest.fixture()
def no_smtp(monkeypatch):
    import mail

    monkeypatch.setattr(mail, "SMTP_HOST", "")
    monkeypatch.setattr(mail, "MAIL_FROM", "")
    monkeypatch.setattr(mail, "PUBLIC_BASE_URL", "https://hub.example")
    accounts.reset_rate_limits()
    yield mail
    accounts.reset_rate_limits()


def test_password_reset_with_mail_off_is_queued_single_use_expiring_and_signs_out(
        api, no_smtp, monkeypatch, admin):
    owner = _signup(api, DANA)
    elsewhere = api.post("/api/accounts/login",
                         json={"email": DANA["email"], "password": DANA["password"]})
    other_session = elsewhere.json()["token"]

    asked = api.post("/api/accounts/forgot", json={"email": DANA["email"]})
    assert asked.status_code == 200

    # Mail is not set up, so the link is queued, not lost.
    queued = [m for m in STORE.pending_mail() if m.to_address == DANA["email"]
              and m.subject == "Reset your CyberLogix password"]
    assert len(queued) == 1
    found = re.search(r"/reset\?token=(clr_[A-Za-z0-9_-]+)", queued[0].body)
    assert found, queued[0].body
    token = found.group(1)
    # The operator can see that it is waiting, but not read the link.
    log = api.get("/api/mail/log", headers=admin).text
    assert "Reset your CyberLogix password" in log
    assert token not in log

    # Once SMTP is configured the queue delivers it.
    sent = []
    monkeypatch.setattr(no_smtp, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(no_smtp, "MAIL_FROM", "hub@example.com")
    monkeypatch.setattr(no_smtp, "_transport", sent.append)
    assert no_smtp.flush_queue()["sent"] >= 1
    assert any(token in m.get_content() for m in sent)

    # It works once, and ends every other session.
    used = api.post("/api/accounts/reset",
                    json={"token": token, "new_password": "a-brand-new-password"})
    assert used.status_code == 200, used.text
    assert api.get("/api/accounts/me", headers=_bearer(other_session)).status_code == 401
    assert api.get("/api/accounts/me", headers=_bearer(owner["token"])).status_code == 401
    again = api.post("/api/accounts/reset",
                     json={"token": token, "new_password": "yet-another-password"})
    assert again.status_code == 400
    assert api.post("/api/accounts/login", json={
        "email": DANA["email"], "password": "a-brand-new-password"}).status_code == 200

    # A second link, left a day and a bit, has expired.
    accounts.reset_rate_limits()
    api.post("/api/accounts/forgot", json={"email": DANA["email"]})
    links = {found.group(1) for m in sent
             for found in [re.search(r"/reset\?token=(clr_[A-Za-z0-9_-]+)",
                                     m.get_content())] if found}
    links.discard(token)
    assert len(links) == 1
    late = links.pop()
    for reset in STORE._resets.values():
        if reset.used_at is None:
            reset.expires_at = utc_now() - timedelta(minutes=1)
    expired = api.post("/api/accounts/reset",
                       json={"token": late, "new_password": "too-late-password"})
    assert expired.status_code == 400
    assert api.post("/api/accounts/login", json={
        "email": DANA["email"], "password": "too-late-password"}).status_code == 401
