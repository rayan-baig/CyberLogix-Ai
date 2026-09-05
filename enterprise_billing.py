"""Enterprise cluster billing.

A single-site customer is billed per unit from the rate card in
`pricing.py`. A chain is billed on volume brackets by enrolled branch
count, and the contract covers every sensor inside those branches — which
is what makes a four-figure branch rate coherent against a single walk-in.

The brackets step rather than taper, so one branch at a boundary can add
five figures. Every quote and contract therefore carries `next_tier`,
naming the boundary and what crossing it costs, rather than letting a
customer discover it on an invoice.

A tenant on an active contract is invoiced from it; the per-unit rate card
no longer applies. `/quote` prices both models side by side so nobody signs
the wrong one.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from accounts import require_role
from auth import require_tenant, write_audit
from store import (
    STORE,
    EnterpriseContract,
    Tenant,
    User,
    calculate_volume_tier_price,
    next_volume_tier,
    resolve_vertical,
    volume_discount_percent,
    volume_tier_label,
)

logger = logging.getLogger("cyberlogix.enterprise")

router = APIRouter(
    prefix="/api/v1/enterprise-billing", tags=["Enterprise Cluster Billing"]
)

# A contract this large is almost certainly a typo, and rounding it into
# blocks would quote a number nobody meant.
MAX_BRANCHES = 10000


class EnterpriseOnboardRequest(BaseModel):
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
    total_branch_locations: int = Field(..., gt=0, le=MAX_BRANCHES)


def cluster_monthly(branches: int, unit_price: float) -> float:
    return calculate_volume_tier_price(branches, unit_price)


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


@router.get("/tiers")
def volume_tiers(industry_vertical: str = "restaurant"):
    """The volume discount ladder, priced against one vertical's rate."""
    from pricing import PRICE_BOOK
    from store import (
        MAX_VOLUME_DISCOUNT_PERCENT,
        VOLUME_DISCOUNT_EVERY_UNITS,
        VOLUME_DISCOUNT_STEP_PERCENT,
        volume_band_table,
    )

    key = _validate_vertical(industry_vertical)
    entry = PRICE_BOOK[key]

    return {
        "currency": "USD",
        "industry_vertical": key,
        "unit": entry["unit"],
        "list_price_usd": entry["monthly_usd"],
        "billing": (
            f"${entry['monthly_usd']:,.0f} per {entry['unit']} per month, less a "
            f"{VOLUME_DISCOUNT_STEP_PERCENT:g}% volume discount for every "
            f"{VOLUME_DISCOUNT_EVERY_UNITS} units, to a "
            f"{MAX_VOLUME_DISCOUNT_PERCENT:g}% maximum. A contract covers every "
            "sensor inside an enrolled unit."
        ),
        "discount_ladder": {
            "step_percent": VOLUME_DISCOUNT_STEP_PERCENT,
            "every_units": VOLUME_DISCOUNT_EVERY_UNITS,
            "max_percent": MAX_VOLUME_DISCOUNT_PERCENT,
        },
        "bands": volume_band_table(entry["monthly_usd"]),
        "note": (
            "No estate is charged more than a larger estate would pay. The "
            "discount deepens a whole band at a time, so the ladder alone "
            "would make 40 units cheaper than 39; the smaller estate gets the "
            "lower figure instead."
        ),
    }


@router.get("/quote")
def quote(
    industry_vertical: str,
    total_branch_locations: int,
    units_per_branch: int = 1,
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

    entry = PRICE_BOOK[vertical]
    units = total_branch_locations * units_per_branch
    per_unit_monthly = round(entry["monthly_usd"] * units, 2)
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
            "units": units,
            "unit_price_usd": entry["monthly_usd"],
            "monthly_usd": per_unit_monthly,
            "annual_usd": round(per_unit_monthly * 12, 2),
        },
        "enterprise_volume": {
            "model": (
                f"${entry['monthly_usd']:,.0f} per {entry['unit']}, less a "
                "volume discount every 10 units"
            ),
            "branches": total_branch_locations,
            "pricing_tier_applied": volume_tier_label(total_branch_locations),
            "volume_discount_percent": volume_discount_percent(
                total_branch_locations
            ),
            "next_tier": next_volume_tier(
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
            "Volume billing covers every sensor inside an enrolled branch, so "
            "it pays off on multi-sensor sites and costs more on single ones. "
            "The volume discount deepens every 10 units; next_tier shows where "
            "the next step lands."
        ),
    }


@router.post("/provision-cluster", status_code=status.HTTP_201_CREATED)
def provision_enterprise_cluster(
    payload: EnterpriseOnboardRequest,
    tenant: Tenant = Depends(require_tenant),
    owner: User = Depends(require_role("owner")),
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
    return {
        "count": len(contracts),
        "active_account_id": (
            STORE.active_contract(tenant.tenant_id).account_id
            if STORE.active_contract(tenant.tenant_id)
            else None
        ),
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
