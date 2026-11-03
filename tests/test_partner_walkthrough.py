"""A reseller from the first day to the last, through the real endpoints.

The steps docs/PARTNERS.md tells the operator and the partner to take, in
that order, each checked from both sides: what the partner's portal shows,
what the business sees, and what the operator's own list says. The pieces
are tested one at a time in test_partners.py and test_partner_keys.py;
this is the order a real partner meets them in.

Found walking it:

* The operator had no way to find a business's account ID, which is the
  one thing attaching it to a partner needs. The book lists only the
  accounts that need a call, and the console does not show a business
  its own ID. GET /api/partners/businesses now finds one by name or
  sign-up email, and says which partner already has it.
* A managed business could see only a partner ID, never who that was.
  GET /api/licenses/me now names the firm, which matters because that
  firm can see the business's units and alerts.
"""

from __future__ import annotations

import pytest

import auth
from contracts import run_billing
from store import STORE

ADMIN = {"X-CyberLogix-Admin": "walk-admin-key"}
PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def operator_key(monkeypatch):
    monkeypatch.setattr(auth, "PLATFORM_ADMIN_KEY", ADMIN["X-CyberLogix-Admin"])


def _sign_up(api, company, email):
    resp = api.post("/api/signup", json={
        "company_name": company, "full_name": "Sam Ortiz", "email": email,
        "password": PASSWORD, "contact_phone": "+1-555-0100",
        "industry_vertical": "restaurant"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["tenant"]["tenant_id"]


def test_a_partner_from_first_day_to_last(api):
    # 1. The operator makes the partner. The key is shown once.
    made = api.post("/api/partners", headers=ADMIN, json={
        "company_name": "Coldline Service Co", "contact_name": "Ray Diaz",
        "contact_email": "ray@coldline.example", "commission_percent": 15})
    assert made.status_code == 201, made.text
    partner_id, key = made.json()["partner_id"], made.json()["api_key"]
    portal = {"X-CyberLogix-Partner": key}
    assert made.json()["commission_percent"] == 15
    assert key not in api.get("/api/partners", headers=ADMIN).text

    # 2. The partner signs in to an empty book.
    empty = api.get("/api/partners/me", headers=portal).json()
    assert empty["accounts"] == 0 and empty["monthly_commission_usd"] == 0
    assert api.get("/partners").status_code == 200

    # 3. Their customer signs up through the front door, like anyone.
    owner, tenant_id = _sign_up(api, "Bella Vista Trattoria", "sam@bellavista.example")
    _sign_up(api, "Bella Notte Bistro", "lee@bellanotte.example")

    # 4. The operator finds the account the partner means, by name or email.
    found = api.get("/api/partners/businesses", headers=ADMIN,
                    params={"search": "bella vista"}).json()
    assert [b["tenant_id"] for b in found["businesses"]] == [tenant_id]
    assert found["businesses"][0]["partner_id"] is None
    by_email = api.get("/api/partners/businesses", headers=ADMIN,
                       params={"search": "SAM@BELLAVISTA"}).json()
    assert [b["tenant_id"] for b in by_email["businesses"]] == [tenant_id]
    assert api.get("/api/partners/businesses", headers=ADMIN,
                   params={"search": "bella"}).json()["count"] == 2
    # Only the operator can search the customer list.
    assert api.get("/api/partners/businesses", params={"search": "bella"},
                   headers=portal).status_code == 401
    assert api.get("/api/partners/businesses", params={"search": "bella"},
                   headers=owner).status_code == 401

    # 5. ... and attaches it.
    attached = api.post(f"/api/partners/{partner_id}/accounts", headers=ADMIN,
                        json={"tenant_id": tenant_id})
    assert attached.status_code == 200, attached.text
    after = api.get("/api/partners/businesses", headers=ADMIN,
                    params={"search": "bella vista"}).json()["businesses"][0]
    assert after["partner_name"] == "Coldline Service Co"

    # The business can see who looks after it, by name.
    me = api.get("/api/licenses/me", headers=owner).json()
    assert me["partner_id"] == partner_id
    assert me["partner_name"] == "Coldline Service Co"

    # 6. The customer fits sensors; one walk-in goes warm.
    for n in (1, 2):
        resp = api.post("/api/licenses/me/sensors", headers=owner, json={
            "sensor_id": f"WALKIN-{n}", "industry_vertical": "restaurant",
            "location_name": f"Walk-in {n}"})
        assert resp.status_code == 201, resp.text
    api.post("/api/contacts", headers=owner,
             json={"full_name": "Sam Ortiz", "phone": "+15550199"})
    warm = api.post("/api/sensor-pulse", headers=owner,
                    json={"sensor_id": "WALKIN-1", "temperature_fahrenheit": 51.0})
    assert warm.status_code == 200, warm.text

    # 7. The partner sees it needs a visit, and enough to send somebody.
    book = api.get("/api/partners/me", headers=portal).json()
    assert book["accounts"] == 1 and book["units"] == 2
    assert [a["tenant_id"] for a in book["needs_attention"]] == [tenant_id]
    detail = api.get(f"/api/partners/me/accounts/{tenant_id}", headers=portal)
    assert detail.status_code == 200
    assert {u["sensor_id"] for u in detail.json()["units"]} == {"WALKIN-1", "WALKIN-2"}
    assert len(detail.json()["open_incidents"]) == 1
    # Not the roster, not the bills.
    assert "+15550199" not in detail.text and "Sam Ortiz" not in detail.text
    # Not the other Bella either.
    other = STORE.list_tenants()
    stranger = next(t for t in other if t.company_name == "Bella Notte Bistro")
    assert api.get(f"/api/partners/me/accounts/{stranger.tenant_id}",
                   headers=portal).status_code == 404

    # 8. While on trial it is free and has paid nothing, so it has earned
    #    nothing and is worth nothing yet.
    statement = api.get("/api/partners/me/statement", headers=portal).json()
    assert statement["commission_usd"] == 0.0
    assert statement["lines"][0]["run_rate_monthly_usd"] == 0.0

    # 9. The customer moves to a paid plan, signs, is billed and pays.
    assert api.post("/api/licenses/me/plan", headers=owner,
                    json={"plan": "growth"}).status_code == 200
    assert api.post("/api/contracts", headers=owner,
                    json={"term_years": 1}).status_code == 201
    assert api.get("/api/partners/me", headers=portal).json()[
        "book"][0]["run_rate_monthly_usd"] == 249.0
    run_billing()
    invoice = STORE.invoices_for(tenant_id)[0]
    paid = api.post(f"/api/invoices/{invoice.invoice_id}/paid",
                    params={"tenant_id": tenant_id}, headers=ADMIN,
                    json={"reference": "WIRE-77"})
    assert paid.status_code == 200, paid.text

    # 10. Commission on the cash, the same on the partner's statement and
    #     on the operator's list.
    statement = api.get("/api/partners/me/statement", headers=portal).json()
    assert statement["collected_usd"] == invoice.total_usd
    assert statement["commission_usd"] == round(invoice.total_usd * 0.15, 2)
    listing = api.get("/api/partners", headers=ADMIN).json()["partners"]
    row = next(p for p in listing if p["partner_id"] == partner_id)
    assert row["monthly_commission_usd"] == statement["commission_usd"]
    assert row["accounts"] == 1

    # 11. A key that leaked: a new one, and the old stops at once.
    rotated = api.post(f"/api/partners/{partner_id}/rotate-key", headers=ADMIN)
    assert rotated.status_code == 200
    assert api.get("/api/partners/me", headers=portal).status_code == 401
    portal = {"X-CyberLogix-Partner": rotated.json()["api_key"]}
    assert api.get("/api/partners/me", headers=portal).status_code == 200

    # 12. Paused, and back.
    api.post(f"/api/partners/{partner_id}/status", headers=ADMIN, json={"active": False})
    assert api.get("/api/partners/me", headers=portal).status_code == 403
    api.post(f"/api/partners/{partner_id}/status", headers=ADMIN, json={"active": True})
    assert api.get("/api/partners/me", headers=portal).status_code == 200

    # 13. The account comes back in house: gone from the book, and the
    #     business no longer names the partner.
    detached = api.delete(f"/api/partners/{partner_id}/accounts/{tenant_id}",
                          headers=ADMIN)
    assert detached.status_code == 200
    assert api.get("/api/partners/me", headers=portal).json()["accounts"] == 0
    assert api.get(f"/api/partners/me/accounts/{tenant_id}",
                   headers=portal).status_code == 404
    me = api.get("/api/licenses/me", headers=owner).json()
    assert me["partner_id"] is None and me["partner_name"] is None


def test_a_deleted_business_leaves_the_book_cleanly(api):
    """A managed business that deletes everything is simply gone from the
    partner's book; the portal and the statement still answer."""
    made = api.post("/api/partners", headers=ADMIN, json={
        "company_name": "Coldline Service Co", "contact_name": "Ray Diaz",
        "contact_email": "ray@coldline.example"}).json()
    portal = {"X-CyberLogix-Partner": made["api_key"]}
    owner, tenant_id = _sign_up(api, "Short Lived Diner", "kim@shortlived.example")
    api.post(f"/api/partners/{made['partner_id']}/accounts", headers=ADMIN,
             json={"tenant_id": tenant_id})
    assert api.get("/api/partners/me", headers=portal).json()["accounts"] == 1

    gone = api.post("/api/accounts/business/erase", headers=owner,
                    json={"company_name": "Short Lived Diner", "password": PASSWORD})
    assert gone.status_code == 200, gone.text

    assert api.get("/api/partners/me", headers=portal).json()["accounts"] == 0
    assert api.get("/api/partners/me/statement", headers=portal).status_code == 200
    assert api.get("/api/partners/businesses", headers=ADMIN,
                   params={"search": "short lived"}).json()["count"] == 0


def test_the_search_needs_two_characters(api):
    assert api.get("/api/partners/businesses", headers=ADMIN,
                   params={"search": "b"}).status_code == 422
    assert api.get("/api/partners/businesses", headers=ADMIN).status_code == 422
