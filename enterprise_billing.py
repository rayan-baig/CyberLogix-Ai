"""Enterprise cluster billing.

A single-site customer is billed per unit from the rate card in
`pricing.py`. A chain can instead be put on a contract billed by enrolled
branch count, which covers every sensor inside those branches.

There is one volume discount, and it is the chain discount on the rate
card (pricing.CHAIN_DISCOUNT_TIERS: 10% off each location from 5, 20% off
from 20). A contract is priced with the same tiers. This used to have a
ladder of its own (2.5% off for every ten units, up to 10%), so the same
chain could be quoted two different discounts; now it cannot, and a
business on a contract is billed from the contract alone, never the
contract and the rate card's discount together.

Every quote and contract still carries `next_tier`, naming the next
boundary and what crossing it costs.

A tenant on an active contract is invoiced from it; the per-unit rate card
no longer applies. `/quote` prices both models side by side so nobody signs
the wrong one.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from accounts import require_role
from auth import require_platform_admin, require_tenant, write_audit
from pricing import (
    CHAIN_DISCOUNT_TIERS,
    chain_discount_percent,
    chain_locations_price,
    chain_tier_label,
    chain_tiers,
    next_chain_tier,
)
from store import (
    STORE,
    EnterpriseContract,
    Tenant,
    User,
    resolve_vertical,
)

logger = logging.getLogger("cyberlogix.enterprise")

router = APIRouter(
    prefix="/api/v1/enterprise-billing", tags=["Enterprise Cluster Billing"]
)

# A contract this large is almost certainly a typo, and rounding it into
# blocks would quote a number nobody meant.
MAX_BRANCHES = 10000


class EnterpriseOnboardRequest(BaseModel):
    # Unknown fields are refused, not dropped.
    #
    # Pydantic ignores extras by default, which on a model that carries a
    # quantity or an amount is a silent, one-directional loss. Measured:
    # posting {"reference": "STRIPE-1", "amount": 500.0} to /paid — the
    # field a Stripe adapter would naturally use — left `amount_usd` unset,
    # which means "paid in full", so $3,997 of a $4,497 invoice was written
    # off and the invoice closed. Same shape on the cluster endpoint:
    # `enrolled_branches: 12` alongside `total_branch_locations: 40` billed
    # forty.
    #
    # On a money-bearing model a misspelt field has to be a 422.
    model_config = ConfigDict(extra="forbid")
    corporate_client_name: str = Field(
        ..., min_length=1, max_length=200,
        description="Name of the enterprise corporation",
    )
    industry_vertical: str = Field(..., description="Target sector vertical")
    total_branch_locations: int = Field(
        ...,
        gt=0,
        le=MAX_BRANCHES,
        description="Total physical branches enrolled (billed in blocks of 5)",
    )
    billing_contact_email: EmailStr = Field(
        ..., description="C-Suite or Finance billing contact"
    )


class BranchChange(BaseModel):
    # Unknown fields are refused, not dropped.
    #
    # Pydantic ignores extras by default, which on a model that carries a
    # quantity or an amount is a silent, one-directional loss. Measured:
    # posting {"reference": "STRIPE-1", "amount": 500.0} to /paid — the
    # field a Stripe adapter would naturally use — left `amount_usd` unset,
    # which means "paid in full", so $3,997 of a $4,497 invoice was written
    # off and the invoice closed. Same shape on the cluster endpoint:
    # `enrolled_branches: 12` alongside `total_branch_locations: 40` billed
    # forty.
    #
    # On a money-bearing model a misspelt field has to be a 422.
    model_config = ConfigDict(extra="forbid")
    total_branch_locations: int = Field(..., gt=0, le=MAX_BRANCHES)


def cluster_monthly(branches: int, unit_price: float) -> float:
    return chain_locations_price(branches, unit_price)


def _validate_vertical(raw: str) -> str:
    vertical = resolve_vertical(raw)
    if vertical is None:
        from store import INDUSTRY_PROFILES

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid industry_vertical provided. Allowed keys: "
                f"{list(INDUSTRY_PROFILES)}"
            ),
        )
    return vertical


def _load(account_id: str, tenant: Tenant) -> EnterpriseContract:
    contract = STORE.get_contract(account_id)
    if contract is None or contract.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Enterprise account ID not found.",
        )
    return contract


# Both price lookups need a signed-in business. They return the rate card for
# every trade, and the trades that are "custom pricing — ask for a quote" on
# the public pages must not be readable by anyone who asks.
@router.get("/tiers")
def volume_tiers(industry_vertical: str = "restaurant",
                 tenant: Tenant = Depends(require_tenant)):
    """The chain discount tiers, priced against one vertical's rate."""
    from pricing import PRICE_BOOK

    key = _validate_vertical(industry_vertical)
    entry = PRICE_BOOK[key]
    tiers = ", ".join(f"{off:g}% off from {start}" for start, off in CHAIN_DISCOUNT_TIERS)

    return {
        "currency": "USD",
        "industry_vertical": key,
        "unit": entry["unit"],
        "list_price_usd": entry["monthly_usd"],
        "billing": (
            f"${entry['monthly_usd']:,.0f} per {entry['unit']} per month, less "
            f"the chain discount: {tiers}. A contract covers every sensor "
            "inside an enrolled unit."
        ),
        "tiers": [{"from_units": start, "discount_percent": off}
                  for start, off in CHAIN_DISCOUNT_TIERS],
        "bands": [
            {
                "band": (f"{row['from']}+" if row["to"] is None
                         else f"{row['from']}-{row['to']}"),
                "from_units": row["from"],
                "to_units": row["to"],
                "discount_percent": row["percent"],
                "effective_unit_price_usd": row["each_usd"],
                "example_units": row["example_locations"],
                "example_monthly_usd": row["example_monthly_usd"],
                "example_annual_usd": round(row["example_monthly_usd"] * 12, 2),
            }
            for row in chain_tiers(key)
        ],
        "note": (
            "The same tiers as the chain discount on the rate card, so a "
            "business is offered one volume price, not two. No estate is "
            "charged more than a larger estate would pay: just below a tier, "
            "the smaller estate pays the larger one's price."
        ),
    }


@router.get("/quote")
def quote(
    industry_vertical: str,
    total_branch_locations: int,
    units_per_branch: int = 1,
    tenant: Tenant = Depends(require_tenant),
):
    """Price both models side by side before anyone signs.

    Cluster billing wins on multi-sensor sites and loses badly on single
    ones, so quoting it blind is how a customer ends up on the wrong
    contract.
    """
    from pricing import PRICE_BOOK

    vertical = _validate_vertical(industry_vertical)
    if total_branch_locations <= 0 or total_branch_locations > MAX_BRANCHES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Branch count must be between 1 and {MAX_BRANCHES}.",
        )
    if units_per_branch <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="units_per_branch must be at least 1.",
        )

    from pricing import extra_units_charge, per_site

    entry = PRICE_BOOK[vertical]
    units = total_branch_locations * units_per_branch
    # What the rate card actually bills on. A per-location sector is billed
    # one price per branch for its allowance of units, so multiplying by
    # sensors per branch quoted a rate card no customer is ever charged --
    # and made the volume contract look like it was saving them money it
    # was not. Units past the allowance at each branch are added as the
    # invoice adds them.
    billed = total_branch_locations if per_site(vertical) else units
    extra = (extra_units_charge(vertical, [units_per_branch] * total_branch_locations)
             if per_site(vertical) else {"units": 0, "amount_usd": 0.0})
    # The rate card takes the chain discount off a per-location trade's
    # location prices, so the comparison does too.
    location_charge = (chain_locations_price(billed, entry["monthly_usd"])
                       if per_site(vertical) else entry["monthly_usd"] * billed)
    per_unit_monthly = round(location_charge + extra["amount_usd"], 2)
    cluster_total = cluster_monthly(total_branch_locations, entry["monthly_usd"])

    cheaper = "per_unit" if per_unit_monthly <= cluster_total else "enterprise_volume"
    # Units per branch at which the volume contract starts winning.
    per_branch_rate_card = entry["monthly_usd"] * total_branch_locations
    breakeven = (
        -(-int(cluster_total) // int(per_branch_rate_card))
        if per_branch_rate_card
        else 0
    )

    return {
        "industry_vertical": vertical,
        "branches": total_branch_locations,
        "units_per_branch": units_per_branch,
        "per_unit": {
            "model": "Per unit from the rate card",
            "unit": entry["unit"],
            "units": billed,
            "unit_price_usd": entry["monthly_usd"],
            "chain_discount_percent": (chain_discount_percent(billed)
                                       if per_site(vertical) else 0.0),
            "extra_units": extra["units"],
            "extra_units_usd": extra["amount_usd"],
            "monthly_usd": per_unit_monthly,
            "annual_usd": round(per_unit_monthly * 12, 2),
        },
        "enterprise_volume": {
            "model": (
                f"${entry['monthly_usd']:,.0f} per {entry['unit']}, less the "
                "chain discount"
            ),
            "branches": total_branch_locations,
            "pricing_tier_applied": chain_tier_label(total_branch_locations),
            "volume_discount_percent": chain_discount_percent(
                total_branch_locations
            ),
            "next_tier": next_chain_tier(
                total_branch_locations, entry["monthly_usd"]
            ),
            "monthly_usd": cluster_total,
            "annual_usd": round(cluster_total * 12, 2),
            "effective_rate_per_branch": round(
                cluster_total / total_branch_locations, 2
            ),
        },
        "cheaper_model": cheaper,
        "monthly_difference_usd": round(abs(per_unit_monthly - cluster_total), 2),
        "cluster_breaks_even_at_units_per_branch": max(breakeven, 1),
        "note": (
            "Volume billing covers every sensor inside an enrolled branch, "
            "with the same chain discount the rate card gives, so the two "
            "differ only in what a branch covers. next_tier shows where the "
            "next discount lands."
        ),
    }


@router.post("/provision-cluster", status_code=status.HTTP_201_CREATED)
def provision_enterprise_cluster(
    payload: EnterpriseOnboardRequest,
    tenant: Tenant = Depends(require_tenant),
    owner: User = Depends(require_role("owner")),
    # The operator's, as well as the owner's. A cluster contract replaces the
    # rate card for all billing, priced on a sector and branch count the
    # customer typed: an owner could bill a ten-sensor superyacht estate as
    # one logistics branch, at about 4% of its price. Setting what a customer
    # pays is the vendor's decision, made with them, not a form they fill in.
    _: None = Depends(require_platform_admin),
):
    """Provision an enterprise volume account for the calling tenant."""
    vertical = _validate_vertical(payload.industry_vertical)

    existing = STORE.active_contract(tenant.tenant_id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{tenant.company_name} already holds active contract "
                f"{existing.account_id}. Change its branch count instead of "
                "opening a second one, or the estate is billed twice."
            ),
        )

    contract = STORE.provision_contract(
        tenant_id=tenant.tenant_id,
        company_name=payload.corporate_client_name,
        industry_vertical=vertical,
        enrolled_branches=payload.total_branch_locations,
        billing_contact_email=str(payload.billing_contact_email),
    )

    logger.info(
        "Enterprise cluster provisioned: account=%s tenant=%s branches=%d mrr=%.2f",
        contract.account_id,
        tenant.tenant_id,
        contract.enrolled_branches,
        contract.monthly_usd,
    )
    write_audit(
        tenant, owner, "billing.cluster_provisioned",
        f"{contract.account_id}: {contract.enrolled_branches} branches, "
        f"${contract.monthly_usd:,.0f}/mo.",
    )

    return {
        "status": "ENTERPRISE_VOLUME_ACCOUNT_PROVISIONED",
        "account_id": contract.account_id,
        "pricing_model_applied": "Custom Enterprise Volume Bracket",
        "pricing_bracket": contract.tier_label,
        "financial_summary": contract.public(),
    }


@router.get("/accounts")
def list_accounts(tenant: Tenant = Depends(require_tenant)):
    """Every cluster contract this tenant holds, newest first."""
    contracts = STORE.contracts_for(tenant.tenant_id)
    # Read once. Asked twice, a contract cancelled between the two reads
    # turned this into an error on `None` instead of an answer.
    active = STORE.active_contract(tenant.tenant_id)
    return {
        "count": len(contracts),
        "active_account_id": active.account_id if active else None,
        "accounts": [c.public() for c in contracts],
    }


@router.get("/account/{account_id}", status_code=status.HTTP_200_OK)
def get_enterprise_account_billing(
    account_id: str, tenant: Tenant = Depends(require_tenant)
):
    """One contract's financials. Scoped to the calling tenant."""
    return _load(account_id, tenant).public()


@router.post("/account/{account_id}/branches")
def change_branch_count(
    account_id: str,
    payload: BranchChange,
    tenant: Tenant = Depends(require_tenant),
    owner: User = Depends(require_role("owner")),
    # Operator as well: the branch count is the price. See provision-cluster.
    _: None = Depends(require_platform_admin),
):
    """Re-enroll a contract at a new branch count."""
    contract = _load(account_id, tenant)
    before = contract.monthly_usd
    contract.enrolled_branches = payload.total_branch_locations
    STORE.save_contract(contract)

    write_audit(
        tenant, owner, "billing.branches_changed",
        f"{contract.account_id}: now {contract.enrolled_branches} branches, "
        f"${before:,.0f} -> ${contract.monthly_usd:,.0f}/mo.",
    )
    return {
        "status": "BRANCH_COUNT_UPDATED",
        "previous_monthly_usd": before,
        "financial_summary": contract.public(),
    }


@router.post("/account/{account_id}/cancel")
def cancel_contract(
    account_id: str,
    tenant: Tenant = Depends(require_tenant),
    owner: User = Depends(require_role("owner")),
):
    """End a cluster contract; the tenant reverts to per-unit pricing."""
    contract = _load(account_id, tenant)
    if not contract.active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{account_id} is already cancelled.",
        )

    contract.active = False
    STORE.save_contract(contract)
    write_audit(
        tenant, owner, "billing.cluster_cancelled",
        f"{contract.account_id} cancelled; reverting to per-unit pricing.",
    )
    return {
        "status": "ENTERPRISE_VOLUME_ACCOUNT_CANCELLED",
        "account_id": account_id,
        "reverts_to": "per-unit rate card",
        "financial_summary": contract.public(),
    }
