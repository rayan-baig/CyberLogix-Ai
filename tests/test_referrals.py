"""Bring a friend: the code, the longer trial, and a reward that is earned once.

A business's owner hands out a code. A business that signs up with it gets
REFERRAL_TRIAL_DAYS of trial. When that business pays its first invoice in
full, the referrer gets one month free on one location as a credit on its
next invoice. Not for yourself, not twice, not past the yearly cap, not
for money that was refunded or a business that cancelled first, and not on
top of a reseller partner's commission.
"""

from datetime import timedelta

import pytest

import credits
import pricing
import referrals
from store import STORE, utc_now

SHOP = pricing.PRICE_BOOK["restaurant"]["monthly_usd"]


@pytest.fixture()
def referrer(api, operator_factory, sensor_factory, tenant_factory):
    """A paying restaurant group whose owner hands out the code."""
    owner, tenant, _ = operator_factory(company_name="Harbor Grill",
                                        email="owner@harborgrill.example")
    key = _key_for(api, owner)
    sensor_factory(key, sensor_id="HG-WALKIN", vertical="restaurant", location="Walk-in")
    code = api.get("/api/referrals/me", headers=owner).json()["code"]
    return {"owner": owner, "tenant": tenant, "code": code, "key": key}


def _key_for(api, owner):
    rotated = api.post("/api/licenses/me/rotate-key", headers=owner)
    assert rotated.status_code == 200, rotated.text
    return {"X-CyberLogix-Key": rotated.json()["api_key"]}


def _join(api, code, company="Corner Cafe", email="sam@cornercafe.example",
          phone="+1-555-0177"):
    resp = api.post("/api/signup", json={
        "company_name": company, "full_name": "Sam Lee", "email": email,
        "password": "correct-horse-battery", "contact_phone": phone,
        "industry_vertical": "restaurant", "referral_code": code})
    return resp


def _pay_first_invoice(api, admin_headers, settle, joined, amount=None, reference="WIRE-1"):
    """The referred business moves onto Starter, is invoiced, and pays."""
    token = {"Authorization": f"Bearer {joined['token']}"}
    key = {"X-CyberLogix-Key": joined["api_key"]}
    tenant_id = joined["tenant"]["tenant_id"]
    if not STORE.sensors_for(tenant_id):
        assert api.post("/api/licenses/me/sensors", headers=key, json={
            "sensor_id": f"{tenant_id}-F1", "industry_vertical": "restaurant",
            "location_name": "Display fridge"}).status_code == 201
    if STORE.get_tenant(tenant_id).plan == "trial":
        assert api.post("/api/licenses/me/plan", headers=token,
                        json={"plan": "starter"}).status_code == 200
    issued = api.post("/api/invoices", params={"tenant_id": tenant_id},
                      headers=admin_headers, json={})
    assert issued.status_code == 201, issued.text
    paid = settle(tenant_id, issued.json()["invoice_id"], reference=reference,
                  amount_usd=amount)
    assert paid.status_code == 200, paid.text
    return issued.json()


# ---- the code ---------------------------------------------------------------


def test_the_owner_has_a_code_and_a_link(api, referrer):
    seen = api.get("/api/referrals/me", headers=referrer["owner"]).json()
    assert seen["code"].startswith("FRIEND-") and len(seen["code"]) == 16
    assert seen["link"]["path"] == f"/signup?ref={seen['code']}"
    assert seen["trial_days"] == pricing.REFERRAL_TRIAL_DAYS == 30
    assert seen["rewards_per_year"] == pricing.REFERRAL_REWARDS_PER_YEAR
    # The same code every time.
    assert api.get("/api/referrals/me", headers=referrer["owner"]).json()["code"] == seen["code"]


def test_only_an_owner_sees_it(api, referrer):
    made = api.post("/api/accounts/users", headers=referrer["owner"], json={
        "email": "op@harborgrill.example", "full_name": "Operator",
        "password": "correct-horse-battery", "role": "operator"})
    assert made.status_code in (200, 201), made.text
    token = api.post("/api/accounts/login", json={
        "email": "op@harborgrill.example", "password": "correct-horse-battery"}).json()["token"]
    assert api.get("/api/referrals/me",
                   headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert api.get("/api/referrals/me", headers=referrer["key"]).status_code == 401


def test_signing_up_with_the_code_gives_a_thirty_day_trial(api, referrer):
    resp = _join(api, referrer["code"].lower())       # typed in lower case still works
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["referred"] is True
    assert "30-day trial" in body["message"]
    tenant = STORE.get_tenant(body["tenant"]["tenant_id"])
    days = (tenant.expires_at - tenant.activated_at).total_seconds() / 86400
    assert days == pytest.approx(30, abs=0.01)
    row = STORE._db.get(referrals.KIND, tenant.tenant_id)
    assert row["referrer_id"] == referrer["tenant"]["tenant_id"]


def test_without_a_code_the_trial_is_the_usual_length(api):
    from domain import PLAN_TIERS

    resp = _join(api, None)
    tenant = STORE.get_tenant(resp.json()["tenant"]["tenant_id"])
    days = (tenant.expires_at - tenant.activated_at).total_seconds() / 86400
    assert days == pytest.approx(PLAN_TIERS["trial"]["term_days"], abs=0.01)
    assert resp.json()["referred"] is False


def test_a_wrong_code_is_said_so_and_nothing_is_made(api):
    resp = _join(api, "FRIEND-AAAA-BBBB")
    assert resp.status_code == 400
    assert "not one we know" in resp.json()["detail"]
    assert STORE.user_by_email("sam@cornercafe.example") is None


def test_the_console_shows_the_business_name_and_nothing_about_the_person(api, referrer):
    _join(api, referrer["code"])
    seen = api.get("/api/referrals/me", headers=referrer["owner"]).json()
    [row] = seen["joined"]
    assert row["business"] == "Corner Cafe"
    assert row["status"] == "joined"
    text = str(seen)
    assert "Sam Lee" not in text and "cornercafe.example" not in text and "0177" not in text


def test_the_signup_page_carries_the_code_from_the_link(api):
    page = api.get("/signup").text
    assert 'name="referral_code"' in page
    sectors = api.get("/api/signup/sectors").json()
    assert sectors["referral_trial_days"] == pricing.REFERRAL_TRIAL_DAYS


# ---- not yourself -------------------------------------------------------------


def test_a_business_cannot_refer_itself_by_its_phone(api, referrer):
    resp = _join(api, referrer["code"], phone="+1 (555) 0100")   # the referrer's number
    assert resp.status_code == 409
    assert "cannot refer itself" in resp.json()["detail"]


def test_a_business_cannot_refer_itself_by_its_email(api, referrer):
    tenant = STORE.get_tenant(referrer["tenant"]["tenant_id"])
    with pytest.raises(Exception) as refused:
        referrals.check_at_signup(referrer["code"], tenant.contact_email, "+15559999")
    assert getattr(refused.value, "status_code", None) == 409


def test_a_business_in_the_same_estate_earns_nothing(api, referrer, admin_headers, settle):
    joined = _join(api, referrer["code"]).json()
    # The referrer's owner turns out to be able to open the new business too.
    owner = next(u for u in STORE.users_for(referrer["tenant"]["tenant_id"]) if u.role == "owner")
    STORE.link_user(STORE.person_of(owner), joined["tenant"]["tenant_id"], "owner")
    _pay_first_invoice(api, admin_headers, settle, joined)
    row = STORE._db.get(referrals.KIND, joined["tenant"]["tenant_id"])
    assert row["status"] == "no_reward_same_estate"
    assert credits.for_tenant(referrer["tenant"]["tenant_id"]) == []


def test_adding_a_business_from_your_own_sign_in_takes_no_code():
    from businesses import NewBusiness

    assert "referral_code" not in NewBusiness.model_fields


# ---- the reward lands on the first paid invoice only --------------------------


def test_nothing_is_earned_until_the_first_invoice_is_paid_in_full(
        api, referrer, admin_headers, settle):
    joined = _join(api, referrer["code"]).json()
    invoice = _pay_first_invoice(api, admin_headers, settle, joined, amount=10.0)
    assert STORE.get_invoice(invoice["invoice_id"]).state == "part_paid"
    assert credits.for_tenant(referrer["tenant"]["tenant_id"]) == []

    settle(joined["tenant"]["tenant_id"], invoice["invoice_id"], reference="WIRE-2")

    [credit] = credits.for_tenant(referrer["tenant"]["tenant_id"])
    assert credit["amount_usd"] == SHOP           # a month on one of its locations
    assert credit["source"] == "referral"
    seen = api.get("/api/referrals/me", headers=referrer["owner"]).json()
    assert seen["joined"][0]["status"] == "rewarded"
    assert seen["earned_usd"] == SHOP
    assert seen["credit_waiting_usd"] == SHOP


def test_one_reward_per_business_however_often_it_pays(api, referrer, admin_headers, settle):
    joined = _join(api, referrer["code"]).json()
    first = _pay_first_invoice(api, admin_headers, settle, joined)
    # The same payment delivered again, and a second invoice paid.
    settle(joined["tenant"]["tenant_id"], first["invoice_id"], reference="WIRE-1")
    _pay_first_invoice(api, admin_headers, settle, joined, reference="WIRE-9")
    assert len(credits.for_tenant(referrer["tenant"]["tenant_id"])) == 1
    # And granting by hand for the same business is refused by the key.
    assert credits.grant(referrer["tenant"]["tenant_id"],
                         f"referral:{joined['tenant']['tenant_id']}", 10.0, "x", "referral") is None


def test_a_payment_through_stripe_earns_it_too(api, referrer, admin_headers):
    import payments

    joined = _join(api, referrer["code"]).json()
    tenant_id = joined["tenant"]["tenant_id"]
    key = {"X-CyberLogix-Key": joined["api_key"]}
    api.post("/api/licenses/me/sensors", headers=key, json={
        "sensor_id": "CC-1", "industry_vertical": "restaurant", "location_name": "Fridge"})
    api.post("/api/licenses/me/plan", headers={"Authorization": f"Bearer {joined['token']}"},
             json={"plan": "starter"})
    invoice = api.post("/api/invoices", params={"tenant_id": tenant_id},
                       headers=admin_headers, json={}).json()
    result = payments.apply_event({"id": "evt_ref", "type": "checkout.session.completed",
                                   "data": {"object": {
                                       "amount_total": int(invoice["total_usd"] * 100),
                                       "currency": "usd", "payment_status": "paid",
                                       "client_reference_id": invoice["invoice_id"]}}})
    assert result["applied"] and result["state"] == "paid"
    assert len(credits.for_tenant(referrer["tenant"]["tenant_id"])) == 1


def test_the_credit_comes_off_the_referrers_next_invoice(api, referrer, admin_headers, settle):
    joined = _join(api, referrer["code"]).json()
    _pay_first_invoice(api, admin_headers, settle, joined)
    tenant_id = referrer["tenant"]["tenant_id"]

    issued = api.post("/api/invoices", params={"tenant_id": tenant_id},
                      headers=admin_headers, json={})
    assert issued.status_code == 201, issued.text
    invoice = issued.json()
    credit_lines = [line for line in invoice["lines"] if line["kind"] == "credit"]
    assert [line["amount_usd"] for line in credit_lines] == [-SHOP]
    assert "bring a friend" in credit_lines[0]["description"]
    assert invoice["total_usd"] == pytest.approx(
        sum(line["amount_usd"] for line in invoice["lines"]), abs=0.001)
    assert credits.balance(tenant_id) == 0.0
    # The following invoice has nothing left to take off.
    again = api.post("/api/invoices", params={"tenant_id": tenant_id},
                     headers=admin_headers, json={}).json()
    assert not [line for line in again["lines"] if line["kind"] == "credit"]


def test_a_credit_larger_than_the_invoice_pays_it_and_carries_the_rest(
        api, tenant_factory, owner_headers, admin_headers):
    headers, tenant = tenant_factory(plan="starter", company_name="Tiny Cafe")
    api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "TC-1", "industry_vertical": "restaurant", "location_name": "Fridge"})
    credits.grant(tenant["tenant_id"], "referral:TEN-X", 100.0, "bring a friend", "referral")
    first = api.post("/api/invoices", params={"tenant_id": tenant["tenant_id"]},
                     headers=admin_headers, json={}).json()
    assert first["total_usd"] == 0.0
    assert first["state"] == "paid"                 # nobody chases $0
    assert credits.balance(tenant["tenant_id"]) == pytest.approx(100.0 - pricing.STARTER_MONTHLY_USD)
    second = api.post("/api/invoices", params={"tenant_id": tenant["tenant_id"]},
                      headers=admin_headers, json={}).json()
    assert second["total_usd"] == pytest.approx(pricing.STARTER_MONTHLY_USD * 2 - 100.0)


def test_voiding_the_invoice_gives_the_credit_back(api, tenant_factory, admin_headers):
    headers, tenant = tenant_factory(plan="growth", company_name="Void Cafe")
    api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "VC-1", "industry_vertical": "restaurant", "location_name": "Fridge"})
    credits.grant(tenant["tenant_id"], "referral:TEN-Y", 50.0, "bring a friend", "referral")
    invoice = api.post("/api/invoices", params={"tenant_id": tenant["tenant_id"]},
                       headers=admin_headers, json={}).json()
    assert credits.balance(tenant["tenant_id"]) == 0.0
    api.post(f"/api/invoices/{invoice['invoice_id']}/void",
             params={"tenant_id": tenant["tenant_id"]}, headers=admin_headers)
    assert credits.balance(tenant["tenant_id"]) == 50.0


def test_the_billing_run_takes_the_credit_too(api, tenant_factory, owner_headers):
    from contracts import run_billing

    headers, tenant = tenant_factory(plan="growth", company_name="Run Cafe")
    both = {**headers, **owner_headers(headers)}
    api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "RC-1", "industry_vertical": "restaurant", "location_name": "Fridge"})
    credits.grant(tenant["tenant_id"], "referral:TEN-Z", SHOP, "bring a friend", "referral")
    assert api.post("/api/contracts", headers=both, json={"term_years": 1}).status_code == 201
    run_billing()
    [invoice] = STORE.invoices_for(tenant["tenant_id"])
    assert any(line["kind"] == "credit" and line["amount_usd"] == -SHOP for line in invoice.lines)


# ---- the limits ------------------------------------------------------------------


def test_the_yearly_cap(api, referrer, admin_headers, settle, monkeypatch):
    monkeypatch.setattr(pricing, "REFERRAL_REWARDS_PER_YEAR", 1)
    one = _join(api, referrer["code"], "Cafe One", "one@cafe.example", "+1-555-0181").json()
    two = _join(api, referrer["code"], "Cafe Two", "two@cafe.example", "+1-555-0182").json()
    _pay_first_invoice(api, admin_headers, settle, one)
    _pay_first_invoice(api, admin_headers, settle, two, reference="WIRE-2")
    assert len(credits.for_tenant(referrer["tenant"]["tenant_id"])) == 1
    assert STORE._db.get(referrals.KIND, two["tenant"]["tenant_id"])["status"] == "no_reward_cap"
    seen = api.get("/api/referrals/me", headers=referrer["owner"]).json()
    assert seen["rewards_left"] == 0


def test_the_cap_counts_the_last_365_days(api, referrer):
    tenant_id = referrer["tenant"]["tenant_id"]
    credits.grant(tenant_id, "referral:OLD", 10.0, "old", "referral")
    row = credits.get("referral:OLD")
    row["created_at"] = (utc_now() - timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
    STORE._db.put(credits.KIND, "referral:OLD", row)
    assert referrals.rewards_in_year(tenant_id) == 0


def test_cancelled_before_paying_earns_nothing(api, referrer, admin_headers, settle):
    joined = _join(api, referrer["code"]).json()
    tenant_id = joined["tenant"]["tenant_id"]
    token = {"Authorization": f"Bearer {joined['token']}"}
    key = {"X-CyberLogix-Key": joined["api_key"]}
    api.post("/api/licenses/me/sensors", headers=key, json={
        "sensor_id": "CX-1", "industry_vertical": "restaurant", "location_name": "Fridge"})
    api.post("/api/licenses/me/plan", headers=token, json={"plan": "starter"})
    assert api.post("/api/contracts", headers={**key, **token},
                    json={"term_years": 1}).status_code == 201
    from contracts import run_billing

    run_billing()
    assert api.post("/api/contracts/cancel", headers={**key, **token},
                    json={"reason": "closing"}).status_code == 200
    [invoice] = STORE.invoices_for(tenant_id)
    settle(tenant_id, invoice.invoice_id)            # pays what it owed after cancelling
    assert STORE._db.get(referrals.KIND, tenant_id)["status"] == "no_reward_cancelled"
    assert credits.for_tenant(referrer["tenant"]["tenant_id"]) == []


def test_a_refund_withdraws_what_is_left_of_the_reward(api, referrer, admin_headers, settle):
    joined = _join(api, referrer["code"]).json()
    _pay_first_invoice(api, admin_headers, settle, joined)
    tenant_id = joined["tenant"]["tenant_id"]
    resp = api.post(f"/api/referrals/{tenant_id}/refunded", headers=admin_headers,
                    json={"note": "refunded by wire"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["withdrawn_usd"] == SHOP
    assert credits.balance(referrer["tenant"]["tenant_id"]) == 0.0
    seen = api.get("/api/referrals/me", headers=referrer["owner"]).json()
    assert seen["joined"][0]["status"] == "refunded"
    assert seen["earned_usd"] == 0.0
    # Nobody else can say so.
    assert api.post(f"/api/referrals/{tenant_id}/refunded", headers=referrer["owner"],
                    json={}).status_code in (401, 403)


def test_a_business_refunded_before_its_reward_was_decided_earns_nothing(
        api, referrer, admin_headers, settle):
    joined = _join(api, referrer["code"]).json()
    tenant_id = joined["tenant"]["tenant_id"]
    api.post(f"/api/referrals/{tenant_id}/refunded", headers=admin_headers, json={})
    _pay_first_invoice(api, admin_headers, settle, joined)
    assert credits.for_tenant(referrer["tenant"]["tenant_id"]) == []


# ---- partners do not stack ------------------------------------------------------


def test_a_partner_managed_business_earns_its_partner_not_a_referral(
        api, referrer, admin_headers, settle):
    joined = _join(api, referrer["code"]).json()
    tenant_id = joined["tenant"]["tenant_id"]
    partner = api.post("/api/partners", headers=admin_headers, json={
        "company_name": "Coldline Service", "contact_name": "Ray",
        "contact_email": "ray@coldline.example", "commission_percent": 20}).json()
    api.post(f"/api/partners/{partner['partner_id']}/accounts", headers=admin_headers,
             json={"tenant_id": tenant_id})
    _pay_first_invoice(api, admin_headers, settle, joined)

    assert STORE._db.get(referrals.KIND, tenant_id)["status"] == "no_reward_partner"
    assert credits.for_tenant(referrer["tenant"]["tenant_id"]) == []
    statement = api.get("/api/partners/me/statement",
                        headers={"X-CyberLogix-Partner": partner["api_key"]}).json()
    assert statement["commission_usd"] > 0           # the partner is paid instead
    seen = api.get("/api/referrals/me", headers=referrer["owner"]).json()
    assert "partners" in seen["joined"][0]["status_words"]


# ---- the monthly report ------------------------------------------------------------


def test_the_monthly_report_mentions_the_credit(api, referrer, admin_headers, settle):
    import monthly

    joined = _join(api, referrer["code"]).json()
    _pay_first_invoice(api, admin_headers, settle, joined)
    tenant = STORE.get_tenant(referrer["tenant"]["tenant_id"])
    today = utc_now().date()
    report = monthly.summary(tenant, today.replace(day=1), today)
    assert report["credits_earned_usd"] == SHOP
    text = monthly.render_text(report)
    assert "Bring a friend" in text and f"${SHOP:,.2f}" in text
    assert "comes off your next invoice" in text
