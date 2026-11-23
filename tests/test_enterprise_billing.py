"""Enterprise volume billing: the chain discount tiers, boundaries, and access.

A volume contract is priced with the same tiers as the chain discount on
the rate card (pricing.CHAIN_DISCOUNT_TIERS), so there is one volume
discount in the product, not two.
"""

import pytest


# The platform operator, who now sets a cluster's terms with the owner. A
# cluster contract replaces the rate card, so its sector and branch count are
# the price; an owner alone used to choose both (see the last tests here).
ADMIN = {"X-Kryozyn-Admin": "test-admin-key"}


def provision(api, headers, name="Harbor Grill Group", vertical="restaurant",
              branches=12, email="cfo@harborgrill.example"):
    return api.post(
        "/api/v1/enterprise-billing/provision-cluster",
        headers={**headers, **ADMIN},
        json={
            "corporate_client_name": name,
            "industry_vertical": vertical,
            "total_branch_locations": branches,
            "billing_contact_email": email,
        },
    )


@pytest.mark.parametrize(
    "branches,monthly,discount,tier",
    [
        # branches x $149, less the chain discount.
        (1, 149.0, 0.0, "1–4 locations: list price"),
        (4, 596.0, 0.0, "1–4 locations: list price"),
        (5, 670.5, 10.0, "5–19 locations: 10% off"),
        (17, 2279.7, 10.0, "5–19 locations: 10% off"),
        # Held to the twenty-location price: no chain pays more than a
        # bigger one.
        (19, 2384.0, 10.0, "5–19 locations: 10% off"),
        (20, 2384.0, 20.0, "20+ locations: 20% off"),
        (50, 5960.0, 20.0, "20+ locations: 20% off"),
        (100, 11920.0, 20.0, "20+ locations: 20% off"),
    ],
)
def test_volume_discounts(
    api, operator_factory, branches, monthly, discount, tier
):
    """Restaurants list at $149 a location, less the chain discount."""
    headers, _, _ = operator_factory()
    body = provision(api, headers, branches=branches).json()["financial_summary"]
    assert body["monthly_subscription_usd"] == monthly
    assert body["annual_contract_value_usd"] == round(monthly * 12, 2)
    assert body["volume_discount_percent"] == discount
    assert body["unit_price_usd"] == 149.0
    assert body["pricing_tier_applied"] == "Custom Enterprise Volume Bracket"
    assert body["pricing_bracket"] == tier


def test_each_vertical_prices_from_its_own_rate(api, operator_factory):
    """A vessel contract is not a location contract."""
    headers, _, _ = operator_factory()
    body = provision(
        api, headers, vertical="superyacht", branches=3
    ).json()["financial_summary"]
    assert body["unit_price_usd"] == 4999.0
    assert body["monthly_subscription_usd"] == pytest.approx(14997.0)
    assert body["annual_contract_value_usd"] == pytest.approx(179964.0)


def test_the_next_boundary_is_stated_up_front(api, operator_factory):
    """Tiers step, so a quote must name what the next branch costs."""
    headers, _, _ = operator_factory()
    body = provision(api, headers, branches=12).json()["financial_summary"]
    assert body["volume_discount_percent"] == 10.0
    assert body["chain_discount_percent"] == 10.0
    assert body["monthly_subscription_usd"] == pytest.approx(1609.2)

    step = body["next_tier"]
    assert step["units_until_next_discount"] == 8
    assert step["next_discount_at_units"] == 20
    assert step["next_discount_percent"] == 20.0
    assert step["next_tier_monthly_usd"] == pytest.approx(2384.0)


def test_max_discount_has_no_further_step(api, operator_factory):
    """At the discount ceiling, growth earns no further reduction."""
    headers, _, _ = operator_factory()
    body = provision(api, headers, branches=80).json()["financial_summary"]
    assert body["next_tier"] is None
    assert body["volume_discount_percent"] == 20.0
    assert body["effective_monthly_rate_per_branch_usd"] == pytest.approx(119.2)


def test_revenue_is_uncapped(api, operator_factory):
    """A ceiling would make the largest accounts the least profitable."""
    from pricing import chain_locations_price as price

    assert price(500, 4999.0) == pytest.approx(1999600.0)
    assert price(1000, 4999.0) > price(500, 4999.0)


@pytest.mark.parametrize("smaller,larger", [(18, 20), (19, 20)])
def test_a_smaller_estate_never_pays_more_than_a_larger_one(smaller, larger):
    """The discount steps a whole tier at once, which alone would invert
    the bill: 20 locations at 20% off undercut 18 or 19 at 10% off. The
    smaller chain gets the lower figure instead."""
    from pricing import chain_discount_percent, chain_locations_price as price

    rate = 999.0
    naive = lambda n: n * rate * (1 - chain_discount_percent(n) / 100.0)
    naive_smaller, naive_larger = naive(smaller), naive(larger)
    assert naive_larger < naive_smaller, "expected the raw tiers to invert here"
    assert price(smaller, rate) == price(larger, rate) == round(naive_larger, 2)


def test_discount_ladder_is_published_per_vertical(api, tenant_factory):
    """The chain discount tiers, on any rate."""
    key_headers, _ = tenant_factory()
    body = api.get(
        "/api/v1/enterprise-billing/tiers?industry_vertical=superyacht",
        headers=key_headers,
    ).json()
    assert body["list_price_usd"] == 4999.0
    assert body["unit"] == "vessel"

    assert body["tiers"] == [{"from_units": 5, "discount_percent": 10.0},
                             {"from_units": 20, "discount_percent": 20.0}]
    assert "discount_ladder" not in body

    bands = {r["band"]: r["discount_percent"] for r in body["bands"]}
    assert bands == {"1-4": 0.0, "5-19": 10.0, "20+": 20.0}

    top = [r for r in body["bands"] if r["band"] == "20+"][0]
    assert top["effective_unit_price_usd"] == pytest.approx(3999.2)


def test_pricing_is_monotonic_for_every_vertical():
    """Enrolling one more unit must never reduce the bill, at any rate."""
    from pricing import PRICE_BOOK, chain_locations_price as price

    for entry in PRICE_BOOK.values():
        rate = entry["monthly_usd"]
        values = [price(n, rate) for n in range(1, 200)]
        assert all(b >= a for a, b in zip(values, values[1:])), entry["unit"]


def test_provisioning_returns_the_agreed_shape(api, operator_factory):
    headers, _, _ = operator_factory()
    body = provision(api, headers, branches=6).json()
    assert body["status"] == "ENTERPRISE_VOLUME_ACCOUNT_PROVISIONED"
    assert body["pricing_model_applied"] == "Custom Enterprise Volume Bracket"
    summary = body["financial_summary"]
    assert summary["status"] == "active"
    assert summary["enrolled_branches"] == 6
    assert len(summary["contract_renew_date"]) == 10


def test_account_ids_never_collide(api, operator_factory):
    """Two clients sharing four letters in one month must not overwrite."""
    alice, _, _ = operator_factory(company_name="A", email="a@example.com")
    bob, _, _ = operator_factory(company_name="B", email="b@example.com")

    first = provision(api, alice, name="Marriott Hotels").json()["account_id"]
    second = provision(api, bob, name="Marriott Resorts").json()["account_id"]

    assert first != second
    assert first.startswith("ENT-VOL-MARR-")
    assert second.startswith("ENT-VOL-MARR-")
    # Both records survive; neither clobbered the other.
    assert api.get(f"/api/v1/enterprise-billing/account/{first}",
                   headers=alice).json()["company_name"] == "Marriott Hotels"
    assert api.get(f"/api/v1/enterprise-billing/account/{second}",
                   headers=bob).json()["company_name"] == "Marriott Resorts"


def test_zero_and_absurd_branch_counts_rejected(api, operator_factory):
    headers, _, _ = operator_factory()
    assert provision(api, headers, branches=0).status_code == 422
    assert provision(api, headers, branches=-3).status_code == 422
    assert provision(api, headers, branches=99999).status_code == 422


def test_unknown_vertical_rejected(api, operator_factory):
    headers, _, _ = operator_factory()
    resp = provision(api, headers, vertical="casino")
    assert resp.status_code == 400
    assert "Allowed keys" in resp.json()["detail"]


def test_a_second_active_contract_is_refused(api, operator_factory):
    """Two contracts would bill the same estate twice."""
    headers, _, _ = operator_factory()
    provision(api, headers)
    second = provision(api, headers, name="Same Group Again")
    assert second.status_code == 409
    assert "already holds active contract" in second.json()["detail"]


def test_contract_supersedes_the_per_unit_rate_card(
    api, operator_factory, sensor_factory
):
    headers, _, _ = operator_factory()
    for index in range(3):
        sensor_factory(headers, sensor_id=f"FRZ-{index}", vertical="restaurant")
        # Three shops, one freezer each. Restaurants bill per location, so
        # each is filed under its own; unfiled they are one location.
        site = api.post("/api/sites", headers=headers,
                        json={"name": f"Branch {index}"}).json()
        site_id = (site.get("site") or site)["site_id"]
        api.post(f"/api/sites/{site_id}/sensors", headers=headers,
                 json={"sensor_id": f"FRZ-{index}"})

    before = api.get("/api/billing", headers=headers).json()
    assert before["billing_model"] == "per_unit"
    assert before["monthly_total_usd"] == pytest.approx(3 * 149.0)

    provision(api, headers, branches=4)
    after = api.get("/api/billing", headers=headers).json()
    assert after["billing_model"] == "enterprise_volume"
    assert after["monthly_total_usd"] == 4 * 149.0
    # The rate-card figure is kept for comparison, not charged.
    assert after["rate_card_equivalent_usd"] == pytest.approx(3 * 149.0)
    assert after["line_items"][0]["description"] == (
        "4 branches · 1–4 locations: list price"
    )


def test_cancelling_reverts_to_per_unit(api, operator_factory, sensor_factory):
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="FRZ-1", vertical="restaurant")
    account = provision(api, headers).json()["account_id"]

    resp = api.post(f"/api/v1/enterprise-billing/account/{account}/cancel",
                    headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ENTERPRISE_VOLUME_ACCOUNT_CANCELLED"
    assert api.get("/api/billing", headers=headers).json()["billing_model"] == "per_unit"

    # Cancelling twice is a conflict, not a silent no-op.
    assert api.post(f"/api/v1/enterprise-billing/account/{account}/cancel",
                    headers=headers).status_code == 409


def test_branch_count_can_be_changed(api, operator_factory):
    headers, _, _ = operator_factory()
    account = provision(api, headers, branches=6).json()["account_id"]
    body = api.post(
        f"/api/v1/enterprise-billing/account/{account}/branches",
        headers={**headers, **ADMIN}, json={"total_branch_locations": 11},
    ).json()
    assert body["previous_monthly_usd"] == pytest.approx(6 * 149.0 * 0.9)
    assert body["financial_summary"]["monthly_subscription_usd"] == pytest.approx(1475.1)


def test_quote_compares_both_models(api, operator_factory):
    headers, _, _ = operator_factory()
    # Restaurants bill per location on both models, with the same chain
    # discount -- so within the allowance the two cost the same.
    single = api.get(
        "/api/v1/enterprise-billing/quote"
        "?industry_vertical=restaurant&total_branch_locations=5&units_per_branch=1",
        headers=headers,
    ).json()
    assert single["per_unit"]["monthly_usd"] == pytest.approx(670.5)
    assert single["per_unit"]["chain_discount_percent"] == 10.0
    assert single["enterprise_volume"]["monthly_usd"] == pytest.approx(670.5)
    assert single["cheaper_model"] == "per_unit"

    # A dense site flips it.
    dense = api.get(
        "/api/v1/enterprise-billing/quote"
        "?industry_vertical=cybersecurity&total_branch_locations=5&units_per_branch=12",
        headers=headers,
    ).json()
    assert dense["per_unit"]["units"] == 60
    assert dense["per_unit"]["monthly_usd"] == pytest.approx(53940.0)
    assert dense["cheaper_model"] == "enterprise_volume"


def test_contracts_are_scoped_to_the_tenant(api, operator_factory):
    alice, _, _ = operator_factory(company_name="Alice", email="a@example.com")
    bob, _, _ = operator_factory(company_name="Bob", email="b@example.com")
    account = provision(api, alice).json()["account_id"]

    # Bob cannot read or alter Alice's financials by guessing the id.
    assert api.get(f"/api/v1/enterprise-billing/account/{account}",
                   headers=bob).status_code == 404
    assert api.post(f"/api/v1/enterprise-billing/account/{account}/cancel",
                    headers=bob).status_code == 404
    assert api.get("/api/v1/enterprise-billing/accounts",
                   headers=bob).json()["count"] == 0


def test_provisioning_requires_authentication(api):
    resp = api.post(
        "/api/v1/enterprise-billing/provision-cluster",
        json={"corporate_client_name": "X", "industry_vertical": "restaurant",
              "total_branch_locations": 5, "billing_contact_email": "a@example.com"},
    )
    assert resp.status_code == 401


def test_only_owners_provision_or_cancel(api, operator_factory):
    owner, _, _ = operator_factory()
    api.post("/api/accounts/users", headers=owner,
             json={"email": "op@example.com", "full_name": "Op", "role": "operator",
                   "password": "a-long-operator-pass"})
    login = api.post("/api/accounts/login",
                     json={"email": "op@example.com",
                           "password": "a-long-operator-pass"}).json()
    operator = {"Authorization": f"Bearer {login['token']}"}

    assert provision(api, operator).status_code == 403
    # An operator can still read the contract.
    account = provision(api, owner).json()["account_id"]
    assert api.get(f"/api/v1/enterprise-billing/account/{account}",
                   headers=operator).status_code == 200


def test_contract_survives_a_restart(tmp_path):
    from db import Database
    from store import HubStore

    path = str(tmp_path / "contract.db")
    first = HubStore(db=Database(path))
    tenant = first.create_tenant("A", "n", "+1", "a@example.com", "enterprise")
    contract = first.provision_contract(
        tenant.tenant_id, "Harbor Grill", "restaurant", 12, "cfo@example.com"
    )

    second = HubStore(db=Database(path))
    restored = second.active_contract(tenant.tenant_id)
    assert restored is not None
    assert restored.account_id == contract.account_id
    assert restored.enrolled_branches == 12
    assert restored.monthly_usd == pytest.approx(12 * 149.0 * 0.9)


def test_an_owner_alone_cannot_set_their_own_cluster_price(api, operator_factory):
    """Found in review: an owner billed a ten-sensor superyacht estate as one
    logistics branch, about 4% of its price, by filling in this form."""
    headers, _, _ = operator_factory()
    resp = api.post(
        "/api/v1/enterprise-billing/provision-cluster", headers=headers,
        json={"corporate_client_name": "Cheap Co", "industry_vertical": "logistics",
              "total_branch_locations": 1, "billing_contact_email": "a@example.com"},
    )
    assert resp.status_code in (401, 403)
    account = provision(api, headers, branches=6).json()["account_id"]
    lowered = api.post(f"/api/v1/enterprise-billing/account/{account}/branches",
                       headers=headers, json={"total_branch_locations": 1})
    assert lowered.status_code in (401, 403)


def test_price_lookups_need_a_signed_in_business(api, tenant_factory):
    """The custom trades are "ask for a quote" on every public page, so their
    rates must not be readable by anyone who calls these addresses."""
    for path in ("/api/v1/enterprise-billing/tiers?industry_vertical=superyacht",
                 "/api/v1/enterprise-billing/quote?industry_vertical=superyacht"
                 "&total_branch_locations=3"):
        assert api.get(path).status_code == 401, path
        key_headers, _ = tenant_factory()
        assert api.get(path, headers=key_headers).status_code == 200, path
