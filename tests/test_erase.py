"""An owner can download everything and then delete everything.

What these guard: the delete really removes the business from the live
database (not just from memory), keeps only the bills, never touches
another business, never locks a person out of their other businesses,
and cannot be done by a slip, by a non-owner, or to dodge a bill.
"""

import json

from store import STORE

ADMIN = {"X-CyberLogix-Admin": "test-admin-key"}
PASSWORD = "correct-horse-battery"


def _estate(api, operator_factory, sensor_factory, company, email, sensor_id):
    headers, tenant, user = operator_factory(company_name=company, email=email)
    sensor_factory(headers, sensor_id=sensor_id, vertical="restaurant")
    for temp in (30.0, 31.0, 55.0):
        api.post("/api/sensor-pulse", headers=headers,
                 json={"sensor_id": sensor_id, "temperature_fahrenheit": temp})
    return headers, tenant


def _rows_naming(tenant_id):
    found = []
    for kind in STORE._db.kinds():
        for rec_id, data in STORE._db.rows(kind):
            if rec_id == tenant_id or data.get("tenant_id") == tenant_id:
                found.append(kind)
    return found


def _erase(api, headers, company, password=PASSWORD):
    return api.post("/api/accounts/business/erase", headers=headers,
                    json={"company_name": company, "password": password})


def test_the_download_has_everything_and_no_secrets(api, operator_factory, sensor_factory):
    headers, tenant = _estate(api, operator_factory, sensor_factory,
                              "Acme Cold Storage", "dana@example.com", "FRZ-1")
    resp = api.get("/api/accounts/business/export", headers=headers)
    assert resp.status_code == 200, resp.text
    assert "attachment" in resp.headers["content-disposition"]
    body = resp.json()
    assert body["tenant_id"] == tenant["tenant_id"]
    records = body["records"]
    assert len(records["reading"]) == 3
    assert records["sensor"][0]["sensor_id"] == "FRZ-1"
    assert records["user"][0]["email"] == "dana@example.com"
    text = json.dumps(body)
    for secret in ("password_hash", "key_hash", "token_hash", "ingest_key_hash", "scrypt"):
        assert secret not in text, secret


def test_a_wrong_name_or_password_deletes_nothing(api, operator_factory, sensor_factory):
    headers, tenant = _estate(api, operator_factory, sensor_factory,
                              "Acme Cold Storage", "dana@example.com", "FRZ-1")
    assert _erase(api, headers, "Acme").status_code == 400
    assert _erase(api, headers, "Acme Cold Storage", "guess").status_code == 403
    assert STORE.get_tenant(tenant["tenant_id"]) is not None
    assert len(STORE.readings_for("FRZ-1")) == 3


def test_only_an_owner_can(api, operator_factory, sensor_factory):
    headers, tenant = _estate(api, operator_factory, sensor_factory,
                              "Acme Cold Storage", "dana@example.com", "FRZ-1")
    api.post("/api/accounts/users", headers=headers, json={
        "email": "sam@example.com", "full_name": "Sam Cole", "password": "another-long-password",
        "role": "operator"})
    sam = api.post("/api/accounts/login",
                   json={"email": "sam@example.com", "password": "another-long-password"})
    sam_headers = {"Authorization": f"Bearer {sam.json()['token']}"}
    assert api.get("/api/accounts/business/export", headers=sam_headers).status_code == 403
    assert _erase(api, sam_headers, "Acme Cold Storage", "another-long-password").status_code == 403
    assert STORE.get_tenant(tenant["tenant_id"]) is not None


def test_delete_removes_it_all_and_leaves_the_neighbour_alone(api, operator_factory, sensor_factory):
    headers, tenant = _estate(api, operator_factory, sensor_factory,
                              "Acme Cold Storage", "dana@example.com", "FRZ-1")
    other_headers, other = _estate(api, operator_factory, sensor_factory,
                                   "Other Foods", "olu@example.com", "FRZ-9")
    assert _rows_naming(tenant["tenant_id"])

    resp = _erase(api, headers, "  acme cold storage ")
    assert resp.status_code == 200, resp.text
    assert resp.json()["records_removed"] > 0

    # Gone from memory and from the database itself.
    assert STORE.get_tenant(tenant["tenant_id"]) is None
    assert STORE.get_sensor("FRZ-1") is None
    assert STORE.readings_for("FRZ-1") == []
    assert STORE._db.rows("reading") and all(
        d["sensor_id"] != "FRZ-1" for _, d in STORE._db.rows("reading"))
    assert _rows_naming(tenant["tenant_id"]) == []
    # The session that did it is over, and the email no longer signs in.
    assert api.get("/api/accounts/me", headers=headers).status_code == 401
    assert api.post("/api/accounts/login", json={
        "email": "dana@example.com", "password": PASSWORD}).status_code == 401

    # The neighbour is untouched.
    assert STORE.get_tenant(other["tenant_id"]) is not None
    assert len(STORE.readings_for("FRZ-9")) == 3
    assert api.get("/api/accounts/me", headers=other_headers).status_code == 200


def test_the_api_key_stops_working(api, tenant_factory, sensor_factory):
    key_headers, tenant = tenant_factory(company_name="Keyed Co")
    api.post("/api/accounts/bootstrap", headers=key_headers, json={
        "email": "k@example.com", "full_name": "K", "password": PASSWORD, "role": "owner"})
    token = api.post("/api/accounts/login",
                     json={"email": "k@example.com", "password": PASSWORD}).json()["token"]
    assert _erase(api, {"Authorization": f"Bearer {token}"}, "Keyed Co").status_code == 200
    assert api.get("/api/licenses/me", headers=key_headers).status_code == 401


def test_bills_are_kept_and_an_unpaid_one_blocks_it(api, operator_factory, sensor_factory):
    headers, tenant = _estate(api, operator_factory, sensor_factory,
                              "Acme Cold Storage", "dana@example.com", "FRZ-1")
    issued = api.post("/api/invoices", params={"tenant_id": tenant["tenant_id"]}, headers=ADMIN,
                      json={"include_add_ons": "", "include_setup": False, "period_days": 30})
    assert issued.status_code == 201, issued.text
    invoice = issued.json()

    blocked = _erase(api, headers, "Acme Cold Storage")
    assert blocked.status_code == 409
    assert "owes" in blocked.json()["detail"]
    assert STORE.get_tenant(tenant["tenant_id"]) is not None

    paid = api.post(f"/api/invoices/{invoice['invoice_id']}/paid",
                    params={"tenant_id": tenant["tenant_id"]}, headers=ADMIN,
                    json={"reference": "WIRE-1"})
    assert paid.status_code == 200, paid.text
    assert _erase(api, headers, "Acme Cold Storage").status_code == 200
    assert _rows_naming(tenant["tenant_id"]) == ["invoice"]
    kept = STORE.invoices_for(tenant["tenant_id"])
    assert len(kept) == 1 and kept[0].company_name == "Acme Cold Storage"


DANA = {
    "company_name": "Blue Harbor Club", "full_name": "Dana Reyes",
    "email": "dana@blueharbor.example", "password": PASSWORD,
    "contact_phone": "+15550100", "industry_vertical": "country_club",
}


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_deleting_your_first_business_keeps_your_others(api):
    first = api.post("/api/signup", json=DANA).json()
    bakery = api.post("/api/accounts/businesses", headers=_bearer(first["token"]),
                      json={"company_name": "Harbor Bakery"}).json()
    club_id = first["tenant"]["tenant_id"]
    back = api.post(f"/api/accounts/businesses/{club_id}/switch",
                    headers=_bearer(bakery["token"])).json()

    assert _erase(api, _bearer(back["token"]), "Blue Harbor Club").status_code == 200

    # Same email, same password, straight into the bakery.
    login = api.post("/api/accounts/login",
                     json={"email": DANA["email"], "password": PASSWORD})
    assert login.status_code == 200, login.text
    assert login.json()["tenant"]["company_name"] == "Harbor Bakery"
    listing = api.get("/api/accounts/businesses", headers=_bearer(login.json()["token"])).json()
    assert [b["company_name"] for b in listing["businesses"]] == ["Harbor Bakery"]


def test_deleting_a_later_business_keeps_the_first(api):
    first = api.post("/api/signup", json=DANA).json()
    bakery = api.post("/api/accounts/businesses", headers=_bearer(first["token"]),
                      json={"company_name": "Harbor Bakery"}).json()
    assert _erase(api, _bearer(bakery["token"]), "Harbor Bakery").status_code == 200
    login = api.post("/api/accounts/login",
                     json={"email": DANA["email"], "password": PASSWORD})
    assert login.status_code == 200
    assert login.json()["tenant"]["company_name"] == "Blue Harbor Club"
    listing = api.get("/api/accounts/businesses", headers=_bearer(login.json()["token"])).json()
    assert [b["company_name"] for b in listing["businesses"]] == ["Blue Harbor Club"]


def test_the_download_hides_vendor_passwords_hook_urls_and_reset_links(api, operator_factory):
    headers, tenant, _ = operator_factory()
    tid = tenant["tenant_id"]
    STORE._db.put("poll_source", "SRC-1", {"source_id": "SRC-1", "tenant_id": tid,
                  "url": "https://vendor.example/api", "headers": {"Authorization": "Bearer vendor-secret"}})
    hook = api.post("/api/webhooks", headers=headers, json={
        "kind": "generic", "target": "https://hooks.example.com/very-secret-path", "label": "Ops"})
    assert hook.status_code in (200, 201), hook.text
    STORE._db.put("mail", "MSG-X", {"message_id": "MSG-X", "tenant_id": tid, "sensitive": True,
                  "to_address": "sam@example.com", "subject": "Reset", "body": "https://x/reset?t=abc123"})
    text = api.get("/api/accounts/business/export", headers=headers).text
    for secret in ("vendor-secret", "very-secret-path", "abc123"):
        assert secret not in text, secret
    assert "Authorization" in text
