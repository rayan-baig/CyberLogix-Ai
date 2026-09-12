"""A misspelt field on a money model must be a 422, never a silent loss.

Pydantic drops unknown fields by default. On most models that is harmless.
On one that carries an amount or a quantity it is a one-directional loss,
and the direction is always the same: the figure the caller meant does not
arrive, and the default takes over.
"""

import pytest

from contracts import run_billing
from store import STORE

ADMIN = {"X-CyberLogix-Admin": "test-admin-key"}


@pytest.fixture()
def billed(api, tenant_factory, owner_headers, sensor_factory):
    headers, tenant = tenant_factory(plan="growth")
    owner = owner_headers(headers)
    both = {**headers, **owner}
    for i in range(3):
        sensor_factory(headers, f"FRZ-{i}", "restaurant")
    api.post("/api/contracts", headers=both, json={"term_years": 1})
    run_billing()
    return both, tenant, STORE.invoices_for(tenant["tenant_id"])[0]


def test_a_misspelt_amount_does_not_write_off_the_invoice(api, billed):
    """The measured case, and the worst one.

    `{"reference": "STRIPE-1", "amount": 500.0}` — the field name a Stripe
    adapter would naturally reach for — left `amount_usd` unset, which
    means "paid in full". $3,997 of a $4,497 invoice written off, the
    invoice closed, and a 200 returned.
    """
    _, tenant, invoice = billed
    resp = api.post(
        f"/api/invoices/{invoice.invoice_id}/paid",
        params={"tenant_id": tenant["tenant_id"]},
        headers=ADMIN,
        json={"reference": "STRIPE-1", "amount": 500.0},
    )
    assert resp.status_code == 422
    assert "amount" in str(resp.json()["detail"])

    untouched = STORE.get_invoice(invoice.invoice_id)
    assert untouched.state == "issued"
    assert untouched.amount_paid_usd == 0.0
    assert untouched.balance_usd == invoice.total_usd


def test_the_correctly_named_field_still_works(api, billed):
    _, tenant, invoice = billed
    resp = api.post(
        f"/api/invoices/{invoice.invoice_id}/paid",
        params={"tenant_id": tenant["tenant_id"]},
        headers=ADMIN,
        json={"reference": "WIRE-1", "amount_usd": 500.0},
    )
    assert resp.status_code == 200
    assert STORE.get_invoice(invoice.invoice_id).amount_paid_usd == 500.0
    assert STORE.get_invoice(invoice.invoice_id).state == "part_paid"


def test_a_plausible_quantity_field_cannot_be_ignored_into_an_overcharge(
    api, tenant_factory, owner_headers, sensor_factory
):
    """`enrolled_branches: 12` alongside `total_branch_locations: 40`
    silently billed forty."""
    headers, tenant = tenant_factory(plan="enterprise")
    owner = owner_headers(headers)
    sensor_factory(headers, "FRZ-1", "restaurant")

    resp = api.post(
        "/api/v1/enterprise-billing/provision-cluster",
        headers={**headers, **owner},
        json={
            "corporate_client_name": "Chain Co",
            "industry_vertical": "restaurant",
            "enrolled_branches": 12,
            "total_branch_locations": 40,
            "billing_contact_email": "ap@chain.example",
        },
    )
    assert resp.status_code == 422
    assert "enrolled_branches" in str(resp.json()["detail"])


@pytest.mark.parametrize("path,body", [
    ("/api/contracts", {"term_years": 1, "escalator": 5.0}),
    ("/api/contracts", {"term_years": 1, "prepay": True}),
    ("/api/contracts/renew", {"term_years": 1, "years": 3}),
    ("/api/contracts/add-ons", {"add_ons": ["vault"], "addons": ["assurance"]}),
    ("/api/licenses/me/plan", {"plan": "enterprise", "tier": "growth"}),
])
def test_every_money_model_refuses_a_field_it_does_not_know(
    api, tenant_factory, owner_headers, sensor_factory, path, body
):
    headers, _ = tenant_factory(plan="growth")
    owner = owner_headers(headers)
    sensor_factory(headers, "FRZ-1", "restaurant")
    both = {**headers, **owner}
    if path != "/api/contracts":
        api.post("/api/contracts", headers=both, json={"term_years": 1})

    resp = api.post(path, headers=both, json=body)
    assert resp.status_code == 422, (
        f"{path} accepted a field it does not understand: {resp.status_code}"
    )


def test_the_models_that_take_free_text_are_left_alone(api, tenant_factory,
                                                       owner_headers,
                                                       sensor_factory):
    """This is a targeted rule, not a blanket one.

    Sign-up deliberately drops extras — there is no `plan` field for a
    caller to set, and refusing unknown keys there would break any client
    that sends an analytics tag. The rule is for models carrying an amount
    or a quantity, where dropping one loses money.
    """
    import signup

    parsed = signup.SignupRequest(
        company_name="Blue Harbor", full_name="Dana",
        email="dana@example.com", password="correct-horse-battery",
        contact_phone="+15550100", utm_source="a-newsletter",
    )
    assert not hasattr(parsed, "utm_source")
