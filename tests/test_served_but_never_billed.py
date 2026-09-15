"""An account being served, on a paid plan, that nobody ever invoices.

Found by seeding the demo estate and opening the new operator console
side by side with the books. The book said $151,716 a year and "1 of 1
accounts paying". The sales ledger said zero invoices had ever been
issued. Nothing anywhere said those two facts disagreed.

The mechanism:

* `run_billing()` iterates `STORE.all_subscriptions()`. A tenant with no
  subscription is therefore never billed. Not late -- never.
* `attention_rows()` raises a row for suspended, lapsed, in-grace,
  trial-ending, overdue, renewal and upsell. There was no row for "this
  account has no contract at all", so the one account that is certainly
  not going to pay was the one the worklist had nothing to say about.
* The book counted it in `paying` and in `mrr_usd` anyway, because both
  were computed from the rate card applied to registered units rather
  than from anything that had been invoiced.

So the failure is silent in all three places at once, and each one
individually looks fine. A customer gets the product free forever, the
founder reads an ARR that will never arrive, and the first sign is a
bank balance that does not match the dashboard.
"""

import pytest

from contracts import attention_rows, run_billing


@pytest.fixture()
def served_but_unbilled(api, tenant_factory, admin_headers):
    """A live enterprise account with units registered and no contract."""
    headers, tenant = tenant_factory(plan="enterprise",
                                     company_name="Bell Street Diner")
    made = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "WALKIN-1", "industry_vertical": "restaurant",
        "location_name": "Walk-in",
    })
    assert made.status_code == 201, made.text
    return tenant


def test_such_an_account_is_never_invoiced(api, served_but_unbilled,
                                           admin_headers):
    """The premise. If this ever stops being true the rest is moot."""
    billed = run_billing()

    assert billed["invoices_issued"] == 0
    ledger = api.get("/api/books/ledger", headers=admin_headers).json()
    assert ledger["count"] == 0


def test_the_worklist_says_nobody_is_billing_them(served_but_unbilled):
    """The fix. This is the row that did not exist."""
    rows = [r for r in attention_rows()
            if r["tenant_id"] == served_but_unbilled["tenant_id"]]

    unbilled = [r for r in rows if r["kind"] == "unbilled"]
    assert unbilled, (
        "an account being served for free raises nothing: "
        f"{[r['kind'] for r in rows]}"
    )
    assert unbilled[0]["urgency"] == "high"
    assert unbilled[0]["at_stake_usd"] > 0


def test_it_outranks_the_upsell_on_the_same_account(served_but_unbilled):
    """Selling an add-on to somebody who is not paying for the base
    product is the wrong call to make first."""
    rows = [r for r in attention_rows()
            if r["tenant_id"] == served_but_unbilled["tenant_id"]]
    kinds = [r["kind"] for r in rows]

    assert "unbilled" in kinds
    if "expansion" in kinds:
        assert kinds.index("unbilled") < kinds.index("expansion")


def test_the_book_does_not_count_them_as_paying(api, served_but_unbilled,
                                                admin_headers):
    """'1 of 1 accounts paying' about an account that has never been
    invoiced is the specific sentence this test exists to prevent."""
    book = api.get("/api/contracts/attention",
                   headers=admin_headers).json()["book"]

    assert book["accounts"] >= 1
    assert book["paying"] == 0, "counted as paying with no contract"


def test_the_revenue_that_is_not_being_billed_is_stated(api,
                                                        served_but_unbilled,
                                                        admin_headers):
    """Not counting it is half the job. An operator needs the figure."""
    book = api.get("/api/contracts/attention",
                   headers=admin_headers).json()["book"]

    assert book["unbilled_annual_usd"] > 0
    assert book["unbilled_accounts"] == 1


def test_a_properly_contracted_account_raises_nothing(api, tenant_factory,
                                                      owner_headers,
                                                      admin_headers):
    """The other half: this must not fire on every healthy account."""
    headers, tenant = tenant_factory(plan="enterprise",
                                     company_name="Harbor Cold Store")
    owner = owner_headers(headers)
    made = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "RACK-1", "industry_vertical": "cybersecurity",
        "location_name": "Rack",
    })
    assert made.status_code == 201, made.text
    made = api.post("/api/contracts", headers={**headers, **owner},
                    json={"term_years": 1})
    assert made.status_code == 201, made.text

    rows = [r for r in attention_rows() if r["tenant_id"] == tenant["tenant_id"]]

    assert not [r for r in rows if r["kind"] == "unbilled"]
    book = api.get("/api/contracts/attention",
                   headers=admin_headers).json()["book"]
    assert book["paying"] == 1


def test_a_trial_is_not_an_unbilled_account(api, tenant_factory,
                                            admin_headers):
    """A trial has no contract on purpose; it already has its own rows."""
    headers, tenant = tenant_factory(plan="trial", company_name="Trying It")
    made = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "T-1", "industry_vertical": "restaurant",
        "location_name": "Test",
    })
    assert made.status_code == 201, made.text

    rows = [r for r in attention_rows() if r["tenant_id"] == tenant["tenant_id"]]

    assert not [r for r in rows if r["kind"] == "unbilled"]


# --- the same figure in the email the operator actually reads -----------


def test_the_digest_does_not_call_it_booked(served_but_unbilled):
    """The console is opened when somebody thinks to open it. The daily
    digest arrives whether or not they do, which makes its headline the
    sentence that matters most -- and it read "$151,716 a year booked"
    about an estate that had never issued an invoice."""
    from digest import operator_digest

    d = operator_digest()

    assert d["arr_usd"] == 0, "counted as booked with no contract"
    assert d["unbilled_annual_usd"] > 0
    assert d["unbilled_accounts"] == 1


def test_the_digest_says_it_in_words(served_but_unbilled):
    """A figure in a JSON body nobody reads is not a warning."""
    from digest import _render_operator, operator_digest

    text = _render_operator(operator_digest())

    assert "no contract" in text
    assert "None of it is being invoiced." in text


def test_a_contracted_account_is_still_booked(api, tenant_factory,
                                              owner_headers):
    """And the headline must not go to zero for a real customer."""
    from digest import operator_digest

    headers, tenant = tenant_factory(plan="enterprise",
                                     company_name="Harbor Cold Store")
    owner = owner_headers(headers)
    made = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "RACK-1", "industry_vertical": "cybersecurity",
        "location_name": "Rack",
    })
    assert made.status_code == 201, made.text
    signed = api.post("/api/contracts", headers={**headers, **owner},
                      json={"term_years": 1})
    assert signed.status_code == 201, signed.text

    d = operator_digest()

    assert d["arr_usd"] > 0
    assert d["unbilled_accounts"] == 0


# --- the two ways to end up uncontracted --------------------------------


def test_a_cancelled_contract_is_not_called_never_invoiced(
    api, tenant_factory, owner_headers
):
    """A customer who cancelled is still served until the licence runs
    out, and they have a drawer full of invoices. Telling them "nothing
    has ever been invoiced" would be false to the person who signed the
    original order -- and it is the same `sub is None` the never-sold
    case produces, so one branch had to become two."""
    from contracts import attention_rows, run_billing
    from store import STORE

    headers, tenant = tenant_factory(plan="enterprise",
                                     company_name="Harbor Cold Store")
    owner = owner_headers(headers)
    made = api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "RACK-1", "industry_vertical": "cybersecurity",
        "location_name": "Rack",
    })
    assert made.status_code == 201, made.text
    signed = api.post("/api/contracts", headers={**headers, **owner},
                      json={"term_years": 1})
    assert signed.status_code == 201, signed.text
    run_billing()
    assert STORE.invoices_for(tenant["tenant_id"]), "the premise"

    # They cancel, through the endpoint a customer actually uses. The
    # licence still has time on it, so the service keeps running.
    off = api.post("/api/contracts/cancel", headers={**headers, **owner},
                   json={"reason": "moving suppliers"})
    assert off.status_code == 200, off.text
    assert STORE.active_subscription(tenant["tenant_id"]) is None

    rows = [r for r in attention_rows()
            if r["tenant_id"] == tenant["tenant_id"]
            and r["kind"] == "unbilled"]

    assert rows, "nobody is billing them and nothing says so"
    assert "Contract ended" in rows[0]["headline"]
    assert "never" not in rows[0]["headline"].lower()


def test_a_never_sold_account_says_it_has_never_been_invoiced(
    served_but_unbilled
):
    """And the other branch keeps its wording."""
    from contracts import attention_rows

    rows = [r for r in attention_rows()
            if r["tenant_id"] == served_but_unbilled["tenant_id"]
            and r["kind"] == "unbilled"]

    assert "Nothing has ever been invoiced" in rows[0]["headline"]
