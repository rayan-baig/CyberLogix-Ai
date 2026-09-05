"""Enterprise volume billing: brackets, boundaries, and access."""

import pytest


def provision(api, headers, name="Harbor Grill Group", vertical="restaurant",
              branches=12, email="cfo@harborgrill.example"):
    return api.post(
        "/api/v1/enterprise-billing/provision-cluster",
        headers=headers,
        json={
            "corporate_client_name": name,
            "industry_vertical": vertical,
            "total_branch_locations": branches,
            "billing_contact_email": email,
        },
    )


@pytest.mark.parametrize(
    "branches,monthly,tier",
    [
        (1, 1000.0, "1-9 branches @ $1,000/branch"),
        (9, 9000.0, "1-9 branches @ $1,000/branch"),
        (10, 9750.0, "10-19 branches @ $975/branch"),
        (19, 18525.0, "10-19 branches @ $975/branch"),
        (20, 19000.0, "20-29 branches @ $950/branch"),
        (29, 27550.0, "20-29 branches @ $950/branch"),
        (30, 27750.0, "30-39 branches @ $925/branch"),
        (60, 51000.0, "60-69 branches @ $850/branch"),
        (80, 64000.0, "80+ branches @ $800/branch"),
        (250, 200000.0, "80+ branches @ $800/branch"),
    ],
)
def test_volume_brackets(api, operator_factory, branches, monthly, tier):
    headers, _, _ = operator_factory()
    body = provision(api, headers, branches=branches).json()["financial_summary"]
    assert body["monthly_subscription_usd"] == monthly
    assert body["annual_contract_value_usd"] == monthly * 12
    assert body["pricing_tier_applied"] == "Custom Enterprise Volume Bracket"
    assert body["pricing_bracket"] == tier


def test_the_next_boundary_is_stated_up_front(api, operator_factory):
    """Brackets step, so a quote must name what the next branch costs."""
    headers, _, _ = operator_factory()
    body = provision(api, headers, branches=25).json()["financial_summary"]
    assert body["monthly_subscription_usd"] == 23750.0
    assert body["effective_monthly_rate_per_branch_usd"] == pytest.approx(950.0)

    step = body["next_tier"]
    assert step["branches_until_next_rate"] == 5
    assert step["next_rate_at_branches"] == 30
    assert step["next_rate_per_branch_usd"] == 925.0
    assert step["next_tier_monthly_usd"] == 27750.0


def test_rate_floor_has_no_further_discount(api, operator_factory):
    """Past 80 branches the rate is at its floor, so growth earns nothing."""
    headers, _, _ = operator_factory()
    body = provision(api, headers, branches=80).json()["financial_summary"]
    assert body["next_tier"] is None
    assert body["effective_monthly_rate_per_branch_usd"] == pytest.approx(800.0)


@pytest.mark.parametrize("smaller,larger", [(39, 40), (49, 50), (59, 60), (69, 70), (79, 80)])
def test_a_smaller_estate_never_pays_more_than_a_larger_one(smaller, larger):
    """The rate steps a whole band at once, which alone would invert the bill.

    On the card as written, 40 branches at $900 ($36,000) undercuts 39 at
    $925 ($36,075). The smaller estate gets the lower figure instead.
    """
    from store import branch_rate, calculate_volume_tier_price as price

    naive_smaller = smaller * branch_rate(smaller)
    naive_larger = larger * branch_rate(larger)
    assert naive_larger < naive_smaller, "expected the raw card to invert here"
    assert price(smaller) == price(larger) == naive_larger


def test_rate_card_matches_the_published_bands(api):
    """$1,000 a branch, down $25 every ten, floored at $800."""
    body = api.get("/api/v1/enterprise-billing/tiers").json()
    rates = {r["band"]: r["rate_per_branch_usd"] for r in body["bands"]}
    assert rates == {
        "1-9": 1000.0,
        "10-19": 975.0,
        "20-29": 950.0,
        "30-39": 925.0,
        "40-49": 900.0,
        "50-59": 875.0,
        "60-69": 850.0,
        "70-79": 825.0,
        "80+": 800.0,
    }

    meta = body["rate_per_branch"]
    assert meta["starts_at_usd"] == 1000.0
    assert meta["step_usd"] == 25.0
    assert meta["every_branches"] == 10
    assert meta["floor_usd"] == 800.0
    assert meta["floor_reached_at_branches"] == 80


def test_pricing_is_monotonic_across_the_whole_range():
    """Enrolling one more branch must never reduce the bill."""
    from store import calculate_volume_tier_price as price

    values = [price(b) for b in range(1, 200)]
    assert all(b >= a for a, b in zip(values, values[1:]))


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

    before = api.get("/api/billing", headers=headers).json()
    assert before["billing_model"] == "per_unit"
    assert before["monthly_total_usd"] == pytest.approx(3 * 99.0)

    provision(api, headers, branches=6)
    after = api.get("/api/billing", headers=headers).json()
    assert after["billing_model"] == "enterprise_volume"
    assert after["monthly_total_usd"] == 6000.0
    # The rate-card figure is kept for comparison, not charged.
    assert after["rate_card_equivalent_usd"] == pytest.approx(297.0)
    assert after["line_items"][0]["description"] == (
        "6 branches · 1-9 branches @ $1,000/branch"
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
        headers=headers, json={"total_branch_locations": 11},
    ).json()
    assert body["previous_monthly_usd"] == 6000.0
    assert body["financial_summary"]["monthly_subscription_usd"] == 10725.0


def test_quote_compares_both_models(api, operator_factory):
    headers, _, _ = operator_factory()
    # One walk-in per location: the rate card is far cheaper.
    single = api.get(
        "/api/v1/enterprise-billing/quote"
        "?industry_vertical=restaurant&total_branch_locations=5&units_per_branch=1",
        headers=headers,
    ).json()
    assert single["per_unit"]["monthly_usd"] == pytest.approx(495.0)
    assert single["enterprise_volume"]["monthly_usd"] == 5000.0
    assert single["cheaper_model"] == "per_unit"

    # A dense site flips it.
    dense = api.get(
        "/api/v1/enterprise-billing/quote"
        "?industry_vertical=cybersecurity&total_branch_locations=5&units_per_branch=12",
        headers=headers,
    ).json()
    assert dense["per_unit"]["units"] == 60
    assert dense["per_unit"]["monthly_usd"] == pytest.approx(29940.0)
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
    assert restored.monthly_usd == 11700.0
