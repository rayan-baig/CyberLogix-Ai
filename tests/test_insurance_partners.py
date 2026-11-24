"""Insurance partners: apply, approval, a code, a share of what is paid.

A broker or insurer applies on a public form (rate limited), the operator
approves in the operator console, and the partner gets a code, a link and
a dashboard key. A business that signs up with the code gets the friend's
welcome (the longer trial). The partner earns a share of what that
business pays for its first months, recorded as owed; the operator records
payouts by hand. A partner never sees anything from inside a business.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

import digest
import insurers
import pricing
from store import STORE, Invoice, iso, utc_now

PASSWORD = "correct-horse-battery"

APPLICATION = {
    "kind": "broker", "company_name": "Northgate Brokers",
    "contact_name": "Alex Moreno", "contact_email": "alex@northgate.example",
    "contact_phone": "+1-555-0199", "website": "northgate.example",
    "message": "We place cover for about 300 restaurants.",
}


def _apply(api, **changes):
    return api.post("/api/insurance-partners/apply", json={**APPLICATION, **changes})


@pytest.fixture()
def inbox(monkeypatch, mailbox):
    monkeypatch.setattr(digest, "OPERATOR_EMAIL", "operator@kryozyn.example")
    return mailbox


@pytest.fixture()
def partner(api, admin_headers, inbox):
    """An approved broker: its id, code and dashboard key."""
    assert _apply(api).status_code == 202
    [row] = STORE._db.all(insurers.PARTNER_KIND)
    approved = api.post(f"/api/insurance-partners/{row['partner_id']}/approve",
                        headers=admin_headers, json={})
    assert approved.status_code == 200, approved.text
    body = approved.json()
    return {"id": row["partner_id"], "code": body["code"], "key": body["key"],
            "headers": {"X-Kryozyn-Insurance-Partner": body["key"]}}


def _join(api, code, company="Corner Cafe", email="sam@cornercafe.example"):
    return api.post("/api/signup", json={
        "company_name": company, "full_name": "Sam Lee", "email": email,
        "password": PASSWORD, "contact_phone": "+1-555-0177",
        "industry_vertical": "restaurant", "referral_code": code})


def _pay(api, admin_headers, settle, joined, amount=None, reference="WIRE-1"):
    """The referred business moves onto Starter, is invoiced, and pays."""
    token = {"Authorization": f"Bearer {joined['token']}"}
    key = {"X-Kryozyn-Key": joined["api_key"]}
    tenant_id = joined["tenant"]["tenant_id"]
    if not STORE.sensors_for(tenant_id):
        assert api.post("/api/licenses/me/sensors", headers=key, json={
            "sensor_id": f"{tenant_id}-F1", "industry_vertical": "restaurant",
            "location_name": "Display fridge"}).status_code == 201
    if STORE.get_tenant(tenant_id).plan == "trial":
        assert api.post("/api/licenses/me/plan", headers=token,
                        json={"plan": "starter", "confirm": True}).status_code == 200
    issued = api.post("/api/invoices", params={"tenant_id": tenant_id},
                      headers=admin_headers, json={})
    assert issued.status_code == 201, issued.text
    paid = settle(tenant_id, issued.json()["invoice_id"], reference=reference,
                  amount_usd=amount)
    assert paid.status_code == 200, paid.text
    return issued.json()


# ---- the money, worked out ---------------------------------------------------


def test_the_share_is_ten_percent_to_the_cent_rounded_half_up():
    assert insurers.DEFAULT_SHARE_PERCENT == 10.0
    assert insurers.DEFAULT_SHARE_MONTHS == 12
    assert insurers.share_of(59.0, 10) == 5.9
    assert insurers.share_of(149.0, 10) == 14.9
    assert insurers.share_of(1490.0, 10) == 149.0
    assert insurers.share_of(0.05, 10) == 0.01           # 0.005, half up
    assert insurers.share_of(33.33, 15) == 5.0           # 4.9995 -> 5.00
    assert insurers.share_of(0.0, 10) == 0.0
    assert insurers.share_of(100.0, 0) == 0.0


def test_twelve_months_from_the_first_payment_lands_on_a_real_day():
    jan31 = datetime(2026, 1, 31, 9, tzinfo=timezone.utc)
    assert insurers.add_months(jan31, 1) == datetime(2026, 2, 28, 9, tzinfo=timezone.utc)
    assert insurers.add_months(jan31, 12) == datetime(2027, 1, 31, 9, tzinfo=timezone.utc)
    assert insurers.add_months(datetime(2027, 11, 30, tzinfo=timezone.utc), 3) == \
        datetime(2028, 2, 29, tzinfo=timezone.utc)


def _invoice(total, payments, source="SUB-1"):
    return Invoice(invoice_id="INV-T", tenant_id="TEN-T", number="CLX-T",
                   company_name="T", lines=[], subtotal_usd=total, total_usd=total,
                   payments=payments, amount_paid_usd=sum(p["amount_usd"] for p in payments),
                   source=source, state="paid")


def test_only_money_paid_inside_the_window_counts_and_never_past_the_total():
    end = datetime(2027, 1, 1, tzinfo=timezone.utc)
    inside = iso(end - timedelta(days=1))
    outside = iso(end + timedelta(days=1))
    assert insurers.counted_paid(_invoice(149.0, [
        {"reference": "a", "amount_usd": 100.0, "at": inside},
        {"reference": "b", "amount_usd": 49.0, "at": outside}]), end) == 100.0
    # An overpayment is nobody's revenue.
    assert insurers.counted_paid(_invoice(149.0, [
        {"reference": "a", "amount_usd": 200.0, "at": inside}]), end) == 149.0


# ---- applying ----------------------------------------------------------------


def test_an_application_waits_and_is_mailed_to_the_operator(api, inbox, caplog):
    with caplog.at_level(logging.INFO, logger="cyberlogix.insurers"):
        resp = _apply(api)
    assert resp.status_code == 202
    assert "reply by email" in resp.json()["message"]
    [row] = STORE._db.all(insurers.PARTNER_KIND)
    assert row["status"] == "applied" and row["code"] is None and not row["active"]
    [mail] = inbox.to("operator@kryozyn.example")
    assert "Northgate Brokers" in mail["Subject"]
    body = mail.get_body(preferencelist=("plain",)).get_content()
    assert "alex@northgate.example" in body and "/book" in body
    # The log names the application, not the person's whole address.
    assert "alex@northgate.example" not in caplog.text
    assert row["partner_id"] in caplog.text


def test_applying_twice_gives_the_same_answer_and_one_application(api, inbox):
    first, second = _apply(api), _apply(api, company_name="Northgate Brokers Ltd")
    assert first.json() == second.json()
    assert len(STORE._db.all(insurers.PARTNER_KIND)) == 1


def test_the_application_form_is_rate_limited(api, inbox):
    for n in range(insurers.APPLY_LIMIT.per_caller):
        assert _apply(api, contact_email=f"p{n}@firm.example").status_code == 202
    refused = _apply(api, contact_email="late@firm.example")
    assert refused.status_code == 429
    assert "Retry-After" in refused.headers
    assert "Too many applications" in refused.json()["detail"]


def test_a_bad_application_is_refused_in_words(api, inbox):
    assert _apply(api, kind="reinsurer").status_code == 422
    assert _apply(api, contact_email="not-an-address").status_code == 422
    assert _apply(api, surprise="field").status_code == 422
    assert STORE._db.all(insurers.PARTNER_KIND) == []


def test_without_an_operator_address_the_application_still_waits(api, mailbox, monkeypatch):
    monkeypatch.setattr(digest, "OPERATOR_EMAIL", "")
    assert _apply(api).status_code == 202
    assert len(STORE._db.all(insurers.PARTNER_KIND)) == 1
    assert len(mailbox) == 0


# ---- approval ----------------------------------------------------------------


def test_only_the_operator_sees_and_decides_applications(api, inbox):
    _apply(api)
    [row] = STORE._db.all(insurers.PARTNER_KIND)
    assert api.get("/api/insurance-partners").status_code == 401
    assert api.get("/api/insurance-partners",
                   headers={"X-Kryozyn-Admin": "wrong"}).status_code == 401
    assert api.post(f"/api/insurance-partners/{row['partner_id']}/approve",
                    json={}).status_code == 401


def test_approval_gives_a_code_a_link_and_a_key_mailed_once(api, admin_headers, inbox):
    _apply(api)
    [row] = STORE._db.all(insurers.PARTNER_KIND)
    listed = api.get("/api/insurance-partners", headers=admin_headers).json()
    assert [a["company_name"] for a in listed["applications"]] == ["Northgate Brokers"]
    assert listed["settings"] == {"share_percent": 10.0, "share_months": 12}

    resp = api.post(f"/api/insurance-partners/{row['partner_id']}/approve",
                    headers=admin_headers, json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"].startswith("PARTNER-") and len(body["code"]) == 17
    assert body["key"].startswith("clx_ins_")
    assert body["link"]["path"] == f"/signup?ref={body['code']}"
    assert body["share_percent"] == 10.0 and body["share_months"] == 12
    assert body["mailed"] is True
    [welcome] = inbox.to("alex@northgate.example")
    text = welcome.get_body(preferencelist=("plain",)).get_content()
    assert body["code"] in text and body["key"] in text
    # Only a fingerprint of the key is kept.
    stored = STORE._db.get(insurers.PARTNER_KIND, row["partner_id"])
    assert body["key"] not in str(stored)
    # Kept as a sensitive message: its body is blanked once sent.
    message = next(m for m in STORE._mail.values() if m.to_address == "alex@northgate.example")
    assert message.sensitive is True
    # Approving twice is refused.
    assert api.post(f"/api/insurance-partners/{row['partner_id']}/approve",
                    headers=admin_headers, json={}).status_code == 409
    listed = api.get("/api/insurance-partners", headers=admin_headers).json()
    assert listed["applications"] == [] and len(listed["partners"]) == 1


def test_the_operator_can_set_a_partners_own_share(api, admin_headers, inbox):
    _apply(api)
    [row] = STORE._db.all(insurers.PARTNER_KIND)
    body = api.post(f"/api/insurance-partners/{row['partner_id']}/approve",
                    headers=admin_headers, json={"share_percent": 15, "share_months": 6}).json()
    assert body["share_percent"] == 15 and body["share_months"] == 6
    assert api.post(f"/api/insurance-partners/{row['partner_id']}/approve",
                    headers=admin_headers, json={"share_percent": 80}).status_code == 422


def test_declining_sends_a_short_note_and_cannot_be_redone(api, admin_headers, inbox):
    _apply(api)
    [row] = STORE._db.all(insurers.PARTNER_KIND)
    resp = api.post(f"/api/insurance-partners/{row['partner_id']}/decline",
                    headers=admin_headers)
    assert resp.status_code == 200 and resp.json()["status"] == "declined"
    assert [m["Subject"] for m in inbox.to("alex@northgate.example")] == [
        "Your Kryozyn partner application"]
    assert api.post(f"/api/insurance-partners/{row['partner_id']}/decline",
                    headers=admin_headers).status_code == 409
    assert api.post("/api/insurance-partners/IPT-999999/approve",
                    headers=admin_headers, json={}).status_code == 404
    # A declined firm may apply again later.
    assert _apply(api).status_code == 202
    assert len(STORE._db.all(insurers.PARTNER_KIND)) == 2


def test_switched_off_and_rotated_keys_stop_working(api, admin_headers, partner):
    me = "/api/insurance-partners/me"
    assert api.get(me, headers=partner["headers"]).status_code == 200
    assert api.get(me).status_code == 401
    assert api.get(me, headers={"X-Kryozyn-Insurance-Partner": "clx_ins_nope"}).status_code == 401

    off = api.post(f"/api/insurance-partners/{partner['id']}/status",
                   headers=admin_headers, json={"active": False})
    assert off.status_code == 200 and off.json()["active"] is False
    assert api.get(me, headers=partner["headers"]).status_code == 403
    # And its code no longer signs anybody up.
    assert _join(api, partner["code"]).status_code == 400
    api.post(f"/api/insurance-partners/{partner['id']}/status",
             headers=admin_headers, json={"active": True})

    rotated = api.post(f"/api/insurance-partners/{partner['id']}/rotate-key",
                       headers=admin_headers)
    assert rotated.status_code == 200
    assert api.get(me, headers=partner["headers"]).status_code == 401
    assert api.get(me, headers={"X-Kryozyn-Insurance-Partner": rotated.json()["key"]}
                   ).status_code == 200


def test_only_an_approved_partner_can_be_switched_paid_or_rekeyed(api, admin_headers, inbox):
    _apply(api)
    [row] = STORE._db.all(insurers.PARTNER_KIND)
    pid = row["partner_id"]
    for path, body in (("status", {"active": True}), ("rotate-key", None),
                       ("payouts", {"amount_usd": 5, "reference": "x"})):
        resp = api.post(f"/api/insurance-partners/{pid}/{path}", headers=admin_headers,
                        json=body)
        assert resp.status_code == 409, path


# ---- referring ---------------------------------------------------------------


def test_a_business_that_uses_the_code_gets_the_friends_welcome(api, partner):
    resp = _join(api, partner["code"].lower())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["referred"] is True
    tenant = STORE.get_tenant(body["tenant"]["tenant_id"])
    days = (tenant.expires_at - tenant.activated_at).total_seconds() / 86400
    assert days == pytest.approx(pricing.REFERRAL_TRIAL_DAYS, abs=0.01)
    row = STORE._db.get(insurers.REFERRAL_KIND, tenant.tenant_id)
    assert row["partner_id"] == partner["id"]


def test_a_partner_cannot_refer_themselves_and_a_made_up_code_is_refused(api, partner):
    mine = _join(api, partner["code"], email="alex@northgate.example")
    assert mine.status_code == 409 and "cannot refer themselves" in mine.json()["detail"]
    assert _join(api, "PARTNER-AAAA-BBBB").status_code == 400
    assert STORE.user_by_email("sam@cornercafe.example") is None


def test_the_dashboard_shows_business_name_and_plan_only(api, partner, sensor_factory):
    joined = _join(api, partner["code"]).json()
    key = {"X-Kryozyn-Key": joined["api_key"]}
    sensor_factory(key, "CC-FRIDGE", "restaurant", "Dessert fridge")
    seen = api.get("/api/insurance-partners/me", headers=partner["headers"])
    assert seen.status_code == 200
    body = seen.json()
    [business] = body["businesses"]
    assert business["business"] == "Corner Cafe"
    assert business["plan"]
    assert set(business) == {"business", "plan", "joined_at", "share_ends_at",
                             "earned_usd", "refunded"}
    text = seen.text
    for private in ("Sam Lee", "cornercafe.example", "0177", "CC-FRIDGE",
                    "Dessert fridge", "temperature", "sensor", joined["tenant"]["tenant_id"]):
        assert private not in text, private


def test_a_partner_key_opens_nothing_inside_any_business(api, partner, sensor_factory):
    joined = _join(api, partner["code"]).json()
    owner = {"Authorization": f"Bearer {joined['token']}"}
    sensor_factory({"X-Kryozyn-Key": joined["api_key"]}, "CC-FRIDGE", "restaurant", "Fridge")
    made = api.post("/api/proof-reports", headers=owner,
                    json={"month": utc_now().strftime("%Y-%m")})
    assert made.status_code == 201, made.text
    rid = made.json()["report_id"]
    key = partner["key"]
    tried = [
        {"X-Kryozyn-Insurance-Partner": key},
        {"X-Kryozyn-Key": key},
        {"Authorization": f"Bearer {key}"},
        {"X-Kryozyn-Partner": key},
    ]
    for headers in tried:
        for path in ("/api/console/overview", "/api/sites", "/api/contacts",
                     "/api/proof-reports", f"/api/proof-reports/{rid}",
                     "/api/autopilot/inspector-log", "/api/partners/me",
                     "/api/invoices"):
            resp = api.get(path, headers=headers)
            assert resp.status_code in (401, 403), (path, headers, resp.status_code)
            assert "CC-FRIDGE" not in resp.text


def test_a_shared_report_link_is_the_only_window_and_it_is_public(api, partner, sensor_factory):
    """Not a partner privilege: whoever the business gives the link to."""
    joined = _join(api, partner["code"]).json()
    owner = {"Authorization": f"Bearer {joined['token']}"}
    sensor_factory({"X-Kryozyn-Key": joined["api_key"]}, "CC-FRIDGE", "restaurant", "Fridge")
    made = api.post("/api/proof-reports", headers=owner,
                    json={"month": utc_now().strftime("%Y-%m")}).json()
    seen = api.get(f"/api/proof-reports/verify/{made['verify_code']}")
    assert seen.status_code == 200 and seen.json()["business"] == "Corner Cafe"
    assert "CC-FRIDGE" not in seen.text
    # Nothing about it reaches the partner's own dashboard.
    assert made["verify_code"] not in api.get("/api/insurance-partners/me",
                                              headers=partner["headers"]).text


# ---- earning -----------------------------------------------------------------


def test_the_partner_earns_ten_percent_of_what_is_paid(api, admin_headers, settle, partner):
    joined = _join(api, partner["code"]).json()
    invoice = _pay(api, admin_headers, settle, joined)
    paid = invoice["total_usd"]
    assert paid > 0
    body = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()
    assert body["earned_usd"] == insurers.share_of(paid, 10)
    assert body["owed_usd"] == body["earned_usd"] and body["paid_usd"] == 0
    [business] = body["businesses"]
    assert business["earned_usd"] == body["earned_usd"]
    assert business["share_ends_at"]
    # The same payment told twice earns once.
    tenant = STORE.get_tenant(joined["tenant"]["tenant_id"])
    insurers.after_settlement(tenant, STORE.get_invoice(invoice["invoice_id"]))
    again = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()
    assert again["earned_usd"] == body["earned_usd"]


def test_part_payments_earn_as_they_arrive(api, admin_headers, settle, partner):
    joined = _join(api, partner["code"]).json()
    invoice = _pay(api, admin_headers, settle, joined, amount=20.0, reference="PART-1")
    me = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()
    assert me["earned_usd"] == 2.0
    rest = round(invoice["total_usd"] - 20.0, 2)
    settle(joined["tenant"]["tenant_id"], invoice["invoice_id"], reference="PART-2",
           amount_usd=rest)
    me = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()
    assert me["earned_usd"] == insurers.share_of(invoice["total_usd"], 10)


def test_nothing_is_earned_after_the_months_run_out(api, admin_headers, settle, partner):
    joined = _join(api, partner["code"]).json()
    tid = joined["tenant"]["tenant_id"]
    _pay(api, admin_headers, settle, joined, reference="FIRST")
    earned = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()["earned_usd"]
    row = STORE._db.get(insurers.REFERRAL_KIND, tid)
    row["share_ends_at"] = iso(utc_now() - timedelta(minutes=1))
    STORE._db.put(insurers.REFERRAL_KIND, tid, row)
    _pay(api, admin_headers, settle, joined, reference="LATER")
    after = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()
    assert after["earned_usd"] == earned


def test_the_window_is_twelve_months_from_the_first_payment(api, admin_headers, settle, partner):
    joined = _join(api, partner["code"]).json()
    _pay(api, admin_headers, settle, joined)
    row = STORE._db.get(insurers.REFERRAL_KIND, joined["tenant"]["tenant_id"])
    first = datetime.fromisoformat(row["first_paid_at"])
    assert datetime.fromisoformat(row["share_ends_at"]) == insurers.add_months(first, 12)


def test_a_late_fee_or_a_reseller_managed_business_earns_nothing(api, admin_headers, settle,
                                                                partner):
    joined = _join(api, partner["code"]).json()
    tenant = STORE.get_tenant(joined["tenant"]["tenant_id"])
    late = _invoice(25.0, [{"reference": "L", "amount_usd": 25.0, "at": iso(utc_now())}],
                    source="late_fee:INV-1")
    late.tenant_id = tenant.tenant_id
    assert insurers.after_settlement(tenant, late) is None

    reseller = STORE.create_partner("Cold Fix Ltd", "Robin", "robin@coldfix.example")
    STORE.assign_partner(tenant, reseller.partner_id)
    _pay(api, admin_headers, settle, joined)
    assert api.get("/api/insurance-partners/me",
                   headers=partner["headers"]).json()["earned_usd"] == 0


def test_a_refund_takes_the_share_back(api, admin_headers, settle, partner):
    joined = _join(api, partner["code"]).json()
    tid = joined["tenant"]["tenant_id"]
    _pay(api, admin_headers, settle, joined)
    earned = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()["earned_usd"]
    resp = api.post(f"/api/referrals/{tid}/refunded", headers=admin_headers, json={"note": "x"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["partner_share_withdrawn_usd"] == earned
    me = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()
    assert me["earned_usd"] == 0 and me["businesses"][0]["refunded"] is True
    # A business that came with no code at all is still a 404.
    plain = _join(api, None, company="Plain Deli", email="pat@deli.example").json()
    assert api.post(f"/api/referrals/{plain['tenant']['tenant_id']}/refunded",
                    headers=admin_headers, json={}).status_code == 404


def test_payouts_are_recorded_by_hand_and_lower_what_is_owed(api, admin_headers, settle,
                                                            partner):
    joined = _join(api, partner["code"]).json()
    _pay(api, admin_headers, settle, joined)
    owed = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()["owed_usd"]
    resp = api.post(f"/api/insurance-partners/{partner['id']}/payouts", headers=admin_headers,
                    json={"amount_usd": 3.5, "reference": "BANK-77", "paid_on": "2026-11-01"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["owed_usd"] == round(owed - 3.5, 2)
    me = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()
    assert me["paid_usd"] == 3.5 and me["owed_usd"] == round(owed - 3.5, 2)
    assert me["payouts"] == [{"paid_on": "2026-11-01", "amount_usd": 3.5,
                              "reference": "BANK-77"}]
    assert api.post(f"/api/insurance-partners/{partner['id']}/payouts", headers=admin_headers,
                    json={"amount_usd": -1, "reference": "x"}).status_code == 422
    assert api.post(f"/api/insurance-partners/{partner['id']}/payouts",
                    json={"amount_usd": 1, "reference": "x"}).status_code == 401
    listed = api.get("/api/insurance-partners", headers=admin_headers).json()
    assert listed["owed_usd"] == round(owed - 3.5, 2)


def test_what_is_owed_outlives_a_business_deleting_itself(api, admin_headers, settle, partner):
    joined = _join(api, partner["code"]).json()
    _pay(api, admin_headers, settle, joined)
    owed = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()["owed_usd"]
    gone = api.post("/api/accounts/business/erase",
                    headers={"Authorization": f"Bearer {joined['token']}"},
                    json={"company_name": "Corner Cafe", "password": PASSWORD})
    assert gone.status_code == 200, gone.text
    me = api.get("/api/insurance-partners/me", headers=partner["headers"]).json()
    assert me["owed_usd"] == owed
    assert me["businesses"] == []          # its name went with it


# ---- the pages ---------------------------------------------------------------


def test_the_partner_page_is_plain_and_promises_no_discount(api):
    page = api.get("/insurance-partners")
    assert page.status_code == 200
    text = page.text
    assert "not an insurer" in text
    assert "10 percent" in text and "12 months" in text
    assert f"{pricing.REFERRAL_TRIAL_DAYS}-day" in text
    lowered = text.lower()
    for promise in ("discount on your premium", "lower premium", "cheaper insurance",
                    "we insure", "save on insurance"):
        assert promise not in lowered
    assert 'id="form"' in text and "/static/insurers.js" in text


def test_the_landing_page_section_is_plain(api):
    text = api.get("/").text
    assert 'id="insurers"' in text
    # Said once, inside the front page's record section (3.22.0).
    assert "Proof for your insurer, every month" in text and "watched 24/7" in text
    assert "not an insurer" in text
    assert "/insurance-partners" in text
    section = text[text.index('id="insurers"'):text.index('id="plans"')].lower()
    for promise in ("discount", "lower premium", "cheaper", "we insure"):
        assert promise not in section


def test_the_dashboard_page_and_the_operator_tab_are_served(api):
    page = api.get("/insurance-partners/dashboard")
    assert page.status_code == 200
    assert "/static/insurance-dashboard.js" in page.text
    assert page.headers["X-Robots-Tag"].startswith("noindex")
    import base64

    book = api.get("/book", headers={"Authorization": "Basic " + base64.b64encode(
        b"op:test-admin-key").decode()}).text
    assert 'data-panel="partners"' in book and 'id="payout-form"' in book
    assert "/api/insurance-partners" in api.get("/static/book.js").text


def test_the_partner_page_is_listed_for_search_and_the_dashboard_is_not(api):
    robots = api.get("/robots.txt").text
    assert "Allow: /insurance-partners$" in robots
    assert "Disallow: /insurance-partners/" in robots
    assert "Disallow: /verify" in robots and "Disallow: /proof-report/" in robots
    sitemap = api.get("/sitemap.xml").text
    assert "/insurance-partners</loc>" in sitemap
    assert "dashboard" not in sitemap
