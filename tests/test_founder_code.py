"""Founding member codes: a launch discount on our own invoices.

The code is CYBERLOGIX_FOUNDER_CODE, ignoring case. It takes
CYBERLOGIX_FOUNDER_PERCENT (20) off the first CYBERLOGIX_FOUNDER_MONTHS (3)
monthly invoices as a line of its own, or that many months' worth once on
a yearly invoice. Once per business, entered at sign-up or on the Money
tab before the first paid invoice, never on the same invoice as the offer
to stay, and off when the setting is empty.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import founders
import retention
from contracts import recurring_total, run_billing
from store import STORE, utc_now

CODE = "Founding-Launch"

SIGNUP = {
    "company_name": "Pier Bakery",
    "full_name": "Robin Hale",
    "email": "robin@pierbakery.example",
    "password": "correct-horse-battery",
    "contact_phone": "+15550199",
    "industry_vertical": "restaurant",
}


@pytest.fixture()
def code_on(monkeypatch):
    monkeypatch.setenv("CYBERLOGIX_FOUNDER_CODE", CODE)
    monkeypatch.delenv("CYBERLOGIX_FOUNDER_UNTIL", raising=False)
    monkeypatch.delenv("CYBERLOGIX_FOUNDER_PERCENT", raising=False)
    monkeypatch.delenv("CYBERLOGIX_FOUNDER_MONTHS", raising=False)
    return monkeypatch


@pytest.fixture()
def shop(api, operator_factory, sensor_factory):
    """A restaurant with one freezer and no contract or invoice yet."""
    headers, tenant, _ = operator_factory(company_name="Harbor Grill",
                                          email="owner@harborgrill.example")
    sensor_factory(headers, "HG-F1", "restaurant", "Walk-in freezer")
    return {"headers": headers, "tenant_id": tenant["tenant_id"]}


def _sign(api, shop, yearly=False):
    signed = api.post("/api/contracts", headers=shop["headers"],
                      json={"term_years": 1, "escalator_percent": 0.0,
                            "annual_prepay": yearly})
    assert signed.status_code == 201, signed.text
    run_billing()
    return STORE.active_subscription(shop["tenant_id"])


def _enter(api, shop, code=CODE):
    return api.post("/api/founder-code", headers=shop["headers"], json={"code": code})


def _invoices(tenant_id):
    return [i for i in sorted(STORE.invoices_for(tenant_id), key=lambda i: i.number)
            if i.state != "void"]


def _bill_through(sub, last_index):
    for index in range(1, last_index + 1):
        run_billing(sub.period_start(index) + timedelta(minutes=1))


def _founder_lines(invoice):
    return [line for line in invoice.lines if line["description"].startswith("Founding member")
            or line["description"].startswith("Paying yearly:")]


# ---- entering the code ------------------------------------------------------


def test_the_code_is_accepted_at_signup_whatever_the_case(api, code_on):
    resp = api.post("/api/signup", json={**SIGNUP, "referral_code": " founding-LAUNCH "})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["founder"] is True
    assert "Founding member: 20% off your first 3 months." in body["message"]
    row = founders.row_for(body["tenant"]["tenant_id"])
    assert row is not None and row["percent"] == 20.0 and row["months"] == 3
    assert row["via"] == "signup"


def test_the_code_is_accepted_on_the_money_tab_whatever_the_case(api, shop, code_on):
    before = api.get("/api/leaving", headers=shop["headers"]).json()["founder"]
    assert before["show"] and before["can_enter"] and not before["taken"]
    assert before["words"] == "Founding member: 20% off your first 3 months"

    resp = _enter(api, shop, "founding-launch")
    assert resp.status_code == 200, resp.text
    assert resp.json()["taken"] is True
    assert "Code accepted" in resp.json()["message"]


def test_a_wrong_code_on_the_money_tab_is_refused(api, shop, code_on):
    resp = _enter(api, shop, "NOT-IT")
    assert resp.status_code == 400
    assert founders.row_for(shop["tenant_id"]) is None


def test_only_an_owner_can_enter_it(api, shop, code_on):
    added = api.post("/api/accounts/users", headers=shop["headers"], json={
        "email": "op@harborgrill.example", "full_name": "Ola Op",
        "password": "correct-horse-battery", "role": "operator"})
    assert added.status_code == 201, added.text
    login = api.post("/api/accounts/login", json={
        "email": "op@harborgrill.example", "password": "correct-horse-battery"}).json()
    op = {"Authorization": f"Bearer {login['token']}"}
    assert api.post("/api/founder-code", headers=op, json={"code": CODE}).status_code == 403


def test_the_signup_page_mentions_it_only_while_it_is_on(api, code_on):
    on = api.get("/api/signup/sectors").json()["founder"]
    assert on["words"] == "Founding member: 20% off your first 3 months"
    code_on.setenv("CYBERLOGIX_FOUNDER_CODE", "")
    assert api.get("/api/signup/sectors").json()["founder"] is None


# ---- once -------------------------------------------------------------------


def test_the_code_works_once_per_business(api, shop, code_on):
    assert _enter(api, shop).status_code == 200
    again = _enter(api, shop)
    assert again.status_code == 409
    assert "already used a founding member code" in again.json()["detail"]
    # Not after signing a contract, cancelling and signing again either.
    _sign(api, shop)
    assert api.post("/api/contracts/cancel", headers=shop["headers"], json={}).status_code == 200
    assert _enter(api, shop).status_code == 409


def test_a_code_from_signup_cannot_be_entered_again_later(api, code_on):
    body = api.post("/api/signup", json={**SIGNUP, "referral_code": CODE}).json()
    headers = {"Authorization": f"Bearer {body['token']}"}
    state = api.get("/api/leaving", headers=headers).json()["founder"]
    assert state["taken"] and not state["can_enter"]
    assert api.post("/api/founder-code", headers=headers,
                    json={"code": CODE}).status_code == 409


def test_not_after_the_first_bill_is_paid(api, shop, code_on, settle):
    _sign(api, shop)
    (first,) = _invoices(shop["tenant_id"])
    assert settle(shop["tenant_id"], first.invoice_id).status_code == 200
    state = api.get("/api/leaving", headers=shop["headers"]).json()["founder"]
    assert state["show"] and not state["can_enter"]
    assert "before the first bill is paid" in state["note"]
    resp = _enter(api, shop)
    assert resp.status_code == 409 and "before the first bill is paid" in resp.json()["detail"]


# ---- the date limit ---------------------------------------------------------


def test_the_code_cannot_be_used_after_its_last_day(api, shop, code_on):
    yesterday = (utc_now() - timedelta(days=1)).date().isoformat()
    code_on.setenv("CYBERLOGIX_FOUNDER_UNTIL", yesterday)
    assert api.get("/api/signup/sectors").json()["founder"] is None
    assert api.get("/api/leaving", headers=shop["headers"]).json()["founder"]["show"] is False
    resp = _enter(api, shop)
    assert resp.status_code == 400 and "that date has passed" in resp.json()["detail"]
    late = api.post("/api/signup", json={**SIGNUP, "referral_code": CODE})
    assert late.status_code == 400 and "that date has passed" in late.json()["detail"]
    assert STORE.user_by_email(SIGNUP["email"]) is None


def test_the_last_day_itself_still_counts_and_the_discount_outlives_it(api, shop, code_on):
    code_on.setenv("CYBERLOGIX_FOUNDER_UNTIL", utc_now().date().isoformat())
    assert api.get("/api/signup/sectors").json()["founder"]["until_words"].startswith(
        "Can be used until")
    assert _enter(api, shop).status_code == 200
    code_on.setenv("CYBERLOGIX_FOUNDER_UNTIL", (utc_now() - timedelta(days=30)).date().isoformat())
    _sign(api, shop)
    (first,) = _invoices(shop["tenant_id"])
    assert first.total_usd == 119.2


def test_a_date_that_cannot_be_read_closes_the_code(api, shop, code_on):
    code_on.setenv("CYBERLOGIX_FOUNDER_UNTIL", "next spring")
    assert founders.is_open() is False
    assert _enter(api, shop).status_code == 400


# ---- the right months -------------------------------------------------------


def test_the_first_three_monthly_invoices_get_the_line_and_the_fourth_does_not(
        api, shop, code_on):
    assert _enter(api, shop).status_code == 200
    sub = _sign(api, shop)
    _bill_through(sub, 3)
    invoices = _invoices(shop["tenant_id"])
    assert [i.total_usd for i in invoices] == [119.2, 119.2, 119.2, 149.0]
    descriptions = [[line["description"] for line in _founder_lines(i)] for i in invoices]
    assert descriptions == [
        ["Founding member: 20% off your first 3 months (month 1 of 3)"],
        ["Founding member: 20% off your first 3 months (month 2 of 3)"],
        ["Founding member: 20% off your first 3 months (month 3 of 3)"],
        [],
    ]
    (line,) = _founder_lines(invoices[0])
    assert line["kind"] == "discount" and line["amount_usd"] == -29.8
    assert founders.months_left(founders.row_for(shop["tenant_id"])) == 0


def test_entered_after_an_unpaid_first_bill_it_starts_with_the_next(api, shop, code_on):
    sub = _sign(api, shop)
    assert _enter(api, shop).status_code == 200
    _bill_through(sub, 4)
    assert [i.total_usd for i in _invoices(shop["tenant_id"])] == [
        149.0, 119.2, 119.2, 119.2, 149.0]


def test_the_percentage_is_of_the_price_after_any_chain_discount(api, shop, code_on):
    """The same base the offer to stay uses: the recurring lines, chain
    discount included, so the chain discount applies first."""
    assert _enter(api, shop).status_code == 200
    _sign(api, shop)
    (first,) = _invoices(shop["tenant_id"])
    (line,) = _founder_lines(first)
    base = recurring_total([ln for ln in first.lines if ln is not line])
    assert line["amount_usd"] == -round(base * 0.20, 2)


def test_a_voided_discounted_month_gets_the_same_line_and_never_a_fourth(
        api, shop, code_on, admin_headers):
    assert _enter(api, shop).status_code == 200
    sub = _sign(api, shop)
    _bill_through(sub, 1)
    second = _invoices(shop["tenant_id"])[-1]
    api.post(f"/api/invoices/{second.invoice_id}/void", params={"tenant_id": shop["tenant_id"]},
             headers=admin_headers, json={"reason": "typo"})
    run_billing(sub.period_start(1) + timedelta(minutes=2))
    _bill_through(sub, 4)
    assert [i.total_usd for i in _invoices(shop["tenant_id"])] == [
        119.2, 119.2, 119.2, 149.0, 149.0]


def test_the_settings_change_the_percentage_and_months(api, shop, code_on):
    code_on.setenv("CYBERLOGIX_FOUNDER_PERCENT", "10")
    code_on.setenv("CYBERLOGIX_FOUNDER_MONTHS", "1")
    assert _enter(api, shop).json()["words"] == "Founding member: 10% off your first month"
    sub = _sign(api, shop)
    _bill_through(sub, 1)
    assert [i.total_usd for i in _invoices(shop["tenant_id"])] == [134.1, 149.0]


# ---- yearly -----------------------------------------------------------------


def test_yearly_takes_the_months_worth_once_and_says_so(api, shop, code_on):
    assert _enter(api, shop).status_code == 200
    sub = _sign(api, shop, yearly=True)
    assert sub.months_per_period == 12
    _bill_through(sub, 1)
    first, second = _invoices(shop["tenant_id"])
    (line,) = _founder_lines(first)
    # Twelve months of $149, of which 20% of three months comes off: $89.40.
    assert line["amount_usd"] == -89.4
    assert line["description"] == ("Paying yearly: 20% off 3 months' worth, taken "
                                   "once on the first yearly bill")
    # The two free months of a yearly plan are still there, as before.
    assert first.total_usd == round(149.0 * 10 - 89.4, 2)
    assert _founder_lines(second) == []
    assert second.total_usd == 1490.0


def test_yearly_is_capped_at_the_months_even_when_more_are_set(api, shop, code_on):
    code_on.setenv("CYBERLOGIX_FOUNDER_MONTHS", "40")   # capped at 12
    assert founders.months() == 12
    code_on.setenv("CYBERLOGIX_FOUNDER_MONTHS", "6")
    assert _enter(api, shop).status_code == 200
    sub = _sign(api, shop, yearly=True)
    _bill_through(sub, 1)
    first, second = _invoices(shop["tenant_id"])
    (line,) = _founder_lines(first)
    assert line["amount_usd"] == -round(149.0 * 6 * 0.20, 2)
    assert _founder_lines(second) == []


# ---- never with the offer to stay -------------------------------------------


def test_never_on_the_same_invoice_as_the_offer_to_stay_and_the_bigger_wins(
        api, shop, code_on):
    sub = _sign(api, shop)
    assert _enter(api, shop).status_code == 200
    assert api.post("/api/leaving/stay", headers=shop["headers"]).status_code == 200
    _bill_through(sub, 6)
    invoices = _invoices(shop["tenant_id"])
    for invoice in invoices:
        assert len([ln for ln in invoice.lines if ln["kind"] == "discount"]) <= 1
    # Periods 1 and 2: the offer to stay (25%) beats the founder line (20%).
    # The founder months are not used up by them, so they follow: 3, 4, 5.
    assert [i.total_usd for i in invoices] == [
        149.0, 111.75, 111.75, 119.2, 119.2, 119.2, 149.0]
    assert "staying" in invoices[1].lines[-1]["description"]
    assert invoices[3].lines[-1]["description"].startswith("Founding member")


def test_when_the_founder_line_is_bigger_it_goes_on_and_the_offer_waits(api, shop, code_on):
    code_on.setenv("CYBERLOGIX_FOUNDER_PERCENT", "50")
    code_on.setenv("CYBERLOGIX_FOUNDER_MONTHS", "1")
    sub = _sign(api, shop)
    assert _enter(api, shop).status_code == 200
    assert api.post("/api/leaving/stay", headers=shop["headers"]).status_code == 200
    _bill_through(sub, 4)
    assert [i.total_usd for i in _invoices(shop["tenant_id"])] == [
        149.0, 74.5, 111.75, 111.75, 149.0]
    assert retention.offer_row(shop["tenant_id"])["used"]


# ---- off --------------------------------------------------------------------


def test_off_when_the_setting_is_empty(api, shop, monkeypatch):
    monkeypatch.setenv("CYBERLOGIX_FOUNDER_CODE", "")
    assert api.get("/api/signup/sectors").json()["founder"] is None
    assert api.get("/api/leaving", headers=shop["headers"]).json()["founder"] == {
        "show": False, "taken": False, "can_enter": False}
    assert _enter(api, shop, "anything").status_code == 404
    # At sign-up it is just a code we do not know, said so, nothing made.
    resp = api.post("/api/signup", json={**SIGNUP, "referral_code": CODE})
    assert resp.status_code in (400, 404, 409)
    assert STORE.user_by_email(SIGNUP["email"]) is None
    sub = _sign(api, shop)
    _bill_through(sub, 1)
    assert [i.total_usd for i in _invoices(shop["tenant_id"])] == [149.0, 149.0]


def test_a_discount_already_given_carries_on_when_the_code_is_switched_off(
        api, shop, code_on):
    assert _enter(api, shop).status_code == 200
    code_on.setenv("CYBERLOGIX_FOUNDER_CODE", "")
    sub = _sign(api, shop)
    _bill_through(sub, 1)
    assert [i.total_usd for i in _invoices(shop["tenant_id"])] == [119.2, 119.2]
    assert api.get("/api/leaving", headers=shop["headers"]).json()["founder"]["taken"]


# ---- the numbers ------------------------------------------------------------


def test_codes_used_are_counted_for_the_book_with_no_business_named(
        api, shop, code_on, admin_headers):
    assert _enter(api, shop).status_code == 200
    assert api.post("/api/signup", json={**SIGNUP, "referral_code": CODE}).status_code == 201
    body = api.get("/api/leaving/numbers", headers=admin_headers).json()
    assert body["founder"] == {"last_30_days": 2, "ever": 2}
    assert body["settings"]["founder_code_on"] is True
    assert body["settings"]["founder_percent"] == 20.0
    for row in STORE._db.all(retention.EVENT_KIND):
        assert set(row) == {"event", "at", "months"}


def test_the_book_and_the_console_show_it():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    book = (root / "static" / "book.js").read_text()
    assert "Founder codes used" in book and "k.founder" in book
    console = (root / "static" / "console.js").read_text()
    assert "function renderFounder(f)" in console and "/api/founder-code" in console
    assert 'id="founder-box"' in (root / "static" / "console.html").read_text()
