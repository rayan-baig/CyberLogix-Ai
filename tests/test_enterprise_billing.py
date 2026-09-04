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
        (1, 5000.0, "Up to 5 branches"),
        (5, 5000.0, "Up to 5 branches"),
        (6, 7500.0, "Up to 10 branches"),
        (10, 7500.0, "Up to 10 branches"),
        (11, 12500.0, "Up to 20 branches"),
        (20, 12500.0, "Up to 20 branches"),
        # The card names no rate between 21 and 50, so the last named one holds.
        (21, 12500.0, "21-50 branches (card names no higher rate below 50)"),
        (50, 12500.0, "21-50 branches (card names no higher rate below 50)"),
        (51, 45000.0, "Over 50 branches, flat"),
        (500, 45000.0, "Over 50 branches, flat"),
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
    body = provision(api, headers, branches=50).json()["financial_summary"]
    assert body["effective_monthly_rate_per_branch_usd"] == pytest.approx(250.0)

    step = body["next_tier"]
    assert step["branches_until_next_tier"] == 1
    assert step["next_tier_monthly_usd"] == 45000.0
    # Following the published card literally, 50 -> 51 is a $32,500 step.
    # Adding an explicit 21-50 row to the card is what would soften it.
    assert step["monthly_increase_usd"] == 32500.0


def test_flat_tier_has_no_next_boundary(api, operator_factory):
    headers, _, _ = operator_factory()
    body = provision(api, headers, branches=80).json()["financial_summary"]
    assert body["next_tier"] is None


def test_rate_card_matches_the_published_table(api):
    """The four published tiers and their ACVs, exactly as quoted to clients."""
    rows = {r["tier"]: r for r in api.get("/api/v1/enterprise-billing/tiers").json()["tiers"]}
    published = {
        "Up to 5 branches": (5000.0, 60000.0),
        "Up to 10 branches": (7500.0, 90000.0),
        "Up to 20 branches": (12500.0, 150000.0),
        "Over 50 branches, flat": (45000.0, 540000.0),
    }
    for tier, (monthly, acv) in published.items():
        assert rows[tier]["monthly_usd"] == monthly, tier
        assert rows[tier]["annual_contract_value_usd"] == acv, tier


def test_boundary_steps_are_published(api):
    steps = {
        s["from_branches"]: s["monthly_increase_usd"]
        for s in api.get("/api/v1/enterprise-billing/tiers").json()["boundary_steps"]
    }
    assert steps == {5: 2500.0, 10: 5000.0, 20: 0.0, 50: 32500.0}


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
    assert after["monthly_total_usd"] == 7500.0
    # The rate-card figure is kept for comparison, not charged.
    assert after["rate_card_equivalent_usd"] == pytest.approx(297.0)
    assert after["line_items"][0]["description"] == "6 branches · Up to 10 branches"


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
    assert body["previous_monthly_usd"] == 7500.0
    assert body["financial_summary"]["monthly_subscription_usd"] == 12500.0


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
    assert restored.monthly_usd == 12500.0
