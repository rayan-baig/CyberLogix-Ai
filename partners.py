"""The reseller channel: one relationship, a book of accounts.

A refrigeration servicer with two hundred restaurant clients already
visits every site, already has the buying relationship, and is already the
one called at 2am when a walk-in fails. Selling to them once puts the
product in two hundred kitchens; selling to the kitchens takes two hundred
negotiations.

They earn a commission on what their accounts bill, and they get a portal
scoped to exactly the estates they manage — never the rest of the book.
That last part is the whole security model of this module: a partner is a
principal with narrower rights than a tenant owner, not a superuser.

Commission is a share of revenue we actually collect, which is not the
same as a success fee: it is paid on billings, so a partner is rewarded
for accounts that stay, not for a loss that happened to be large.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from store import (
    DEFAULT_PARTNER_COMMISSION_PERCENT,
    STORE,
    Partner,
    Tenant,
    iso,
    utc_now,
)

logger = logging.getLogger("cyberlogix.partners")

router = APIRouter(prefix="/api/partners", tags=["Reseller Channel"])

# Creating a partner is an act of the platform operator, not of a
# customer, so it sits behind a separate root credential rather than any
# tenant's key.
import os

PLATFORM_ADMIN_KEY = os.environ.get("CYBERLOGIX_ADMIN_KEY", "").strip()


class PartnerCreate(BaseModel):
    company_name: str = Field(..., min_length=1, max_length=160)
    contact_name: str = Field(..., min_length=1, max_length=120)
    contact_email: EmailStr
    commission_percent: float = Field(
        DEFAULT_PARTNER_COMMISSION_PERCENT, ge=0.0, le=50.0
    )


class TenantLink(BaseModel):
    tenant_id: str = Field(..., min_length=1)


def require_admin(
    x_cyberlogix_admin: Optional[str] = Header(None, alias="X-CyberLogix-Admin"),
) -> None:
    """Gate the platform-operator endpoints.

    With no admin key configured the endpoints are closed rather than open.
    A deployment that forgot to set the variable must not hand out the
    ability to mint resellers.
    """
    if not PLATFORM_ADMIN_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No platform admin key is configured, so partner "
                "administration is closed. Set CYBERLOGIX_ADMIN_KEY."
            ),
        )
    if not x_cyberlogix_admin or x_cyberlogix_admin != PLATFORM_ADMIN_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid X-CyberLogix-Admin header is required.",
        )


def require_partner(
    x_cyberlogix_partner: Optional[str] = Header(
        None, alias="X-CyberLogix-Partner"
    ),
) -> Partner:
    """Identify the reseller making the request."""
    partner = STORE.partner_by_key((x_cyberlogix_partner or "").strip())
    if partner is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid X-CyberLogix-Partner key is required.",
        )
    if not partner.active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Partner {partner.company_name} is suspended.",
        )
    return partner


def _owned_tenant(partner: Partner, tenant_id: str) -> Tenant:
    """A tenant this partner manages, refusing anyone else's.

    Checked on every read. A partner portal that returns another partner's
    account once is a partner portal nobody will put their book into.
    """
    tenant = STORE.get_tenant((tenant_id or "").strip())
    if tenant is None or tenant.partner_id != partner.partner_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account '{tenant_id}' is not managed by this partner.",
        )
    return tenant


def _account_row(tenant: Tenant, commission_percent: float) -> Dict[str, Any]:
    from pricing import build_subscription

    subscription = build_subscription(tenant)
    monthly = subscription["effective_monthly_usd"]
    sensors = STORE.sensors_for(tenant.tenant_id)
    open_incidents = STORE.open_incidents(tenant.tenant_id)

    return {
        "tenant_id": tenant.tenant_id,
        "company_name": tenant.company_name,
        "plan": tenant.plan,
        "active": tenant.active,
        "units": len(sensors),
        "units_offline": sum(1 for s in sensors if s.offline()),
        "low_battery": sum(1 for s in sensors if s.battery_low),
        "open_incidents": len(open_incidents),
        "monthly_usd": monthly,
        "commission_usd": round(monthly * commission_percent / 100.0, 2),
    }


# ==============================================================================
#  Platform operator
# ==============================================================================


@router.post("", status_code=status.HTTP_201_CREATED)
def create_partner(payload: PartnerCreate, _: None = Depends(require_admin)):
    """Mint a reseller. The key is shown once and never again."""
    partner = STORE.create_partner(
        company_name=payload.company_name,
        contact_name=payload.contact_name,
        contact_email=str(payload.contact_email),
        commission_percent=payload.commission_percent,
    )
    logger.info(
        "Partner created: %s (%s) at %.1f%%.",
        partner.company_name,
        partner.partner_id,
        partner.commission_percent,
    )
    return {
        **partner.public(),
        "api_key": partner.api_key,
        "warning": (
            "This key is shown once. It authenticates the whole partner "
            "portal, so store it somewhere a departing employee cannot read."
        ),
    }


@router.get("", dependencies=[Depends(require_admin)])
def list_partners():
    """Every reseller and what their book is worth."""
    rows = []
    for partner in STORE.list_partners():
        accounts = STORE.tenants_for_partner(partner.partner_id)
        billings = sum(
            _account_row(t, partner.commission_percent)["monthly_usd"]
            for t in accounts
        )
        rows.append(
            {
                **partner.public(),
                "accounts": len(accounts),
                "monthly_billings_usd": round(billings, 2),
                "monthly_commission_usd": round(
                    billings * partner.commission_percent / 100.0, 2
                ),
            }
        )
    return {"count": len(rows), "partners": rows}


@router.post("/{partner_id}/accounts", dependencies=[Depends(require_admin)])
def attach_account(partner_id: str, payload: TenantLink):
    """Put an existing account under a reseller."""
    partner = STORE.get_partner(partner_id)
    if partner is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Partner '{partner_id}' not found.",
        )
    tenant = STORE.get_tenant(payload.tenant_id.strip())
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tenant '{payload.tenant_id}' not found.",
        )
    if tenant.partner_id and tenant.partner_id != partner_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{tenant.company_name} is already managed by "
                f"{tenant.partner_id}. Detach it first."
            ),
        )

    STORE.assign_partner(tenant, partner_id)
    return {
        "message": f"{tenant.company_name} is now managed by {partner.company_name}.",
        "tenant_id": tenant.tenant_id,
        "partner_id": partner_id,
    }


@router.delete("/{partner_id}/accounts/{tenant_id}", dependencies=[Depends(require_admin)])
def detach_account(partner_id: str, tenant_id: str):
    """Take an account back in house."""
    tenant = STORE.get_tenant(tenant_id)
    if tenant is None or tenant.partner_id != partner_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Account '{tenant_id}' is not managed by '{partner_id}'.",
        )
    STORE.assign_partner(tenant, None)
    return {
        "message": f"{tenant.company_name} is now managed directly.",
        "tenant_id": tenant_id,
    }


# ==============================================================================
#  Partner portal
# ==============================================================================


@router.get("/me")
def partner_me(partner: Partner = Depends(require_partner)):
    """The reseller's own book: every account, and what it earns them."""
    accounts = [
        _account_row(t, partner.commission_percent)
        for t in STORE.tenants_for_partner(partner.partner_id)
    ]
    billings = round(sum(a["monthly_usd"] for a in accounts), 2)
    commission = round(sum(a["commission_usd"] for a in accounts), 2)

    return {
        "partner": partner.public(),
        "accounts": len(accounts),
        "units": sum(a["units"] for a in accounts),
        "monthly_billings_usd": billings,
        "monthly_commission_usd": commission,
        "annual_commission_usd": round(commission * 12, 2),
        "needs_attention": [
            a
            for a in accounts
            if a["open_incidents"] or a["units_offline"] or a["low_battery"]
        ],
        "book": accounts,
        "commission_basis": (
            f"{partner.commission_percent:.0f}% of what these accounts bill, "
            "paid monthly on collected revenue."
        ),
        "generated_at": iso(utc_now()),
    }


@router.get("/me/accounts/{tenant_id}")
def partner_account(
    tenant_id: str, partner: Partner = Depends(require_partner)
):
    """One managed account, at the level a servicer needs.

    Deliberately operational rather than complete: enough to dispatch an
    engineer, not the customer's roster, audit trail or billing detail.
    """
    tenant = _owned_tenant(partner, tenant_id)
    sensors = sorted(
        STORE.sensors_for(tenant.tenant_id), key=lambda s: s.sensor_id
    )
    unit = tenant.temperature_unit
    sites = {s.site_id: s for s in STORE.sites_for(tenant.tenant_id)}

    return {
        "tenant_id": tenant.tenant_id,
        "company_name": tenant.company_name,
        "plan": tenant.plan,
        "temperature_unit": unit,
        "units": [
            {
                "sensor_id": s.sensor_id,
                "location_name": s.location_name,
                "site": (
                    sites[s.site_id].name if s.site_id in sites else None
                ),
                "online": not s.offline(),
                "last_seen": iso(s.last_seen),
                "battery_percent": s.battery_percent,
                "battery_low": s.battery_low,
                "signal_percent": s.signal_percent,
                "last_temperature_display": s.public(unit)[
                    "last_temperature_display"
                ],
                "external_device_sn": s.external_device_sn,
            }
            for s in sensors
        ],
        "open_incidents": [
            {
                "incident_id": i.incident_id,
                "sensor_id": i.sensor_id,
                "opened_at": iso(i.opened_at),
                "detail": i.breach_details,
                "minutes_open": i.minutes_open(),
            }
            for i in STORE.open_incidents(tenant.tenant_id)
        ],
        "service_note": (
            "Shows what an engineer needs to arrive prepared. The account's "
            "on-call roster, audit trail and billing stay with the customer."
        ),
    }


@router.get("/me/statement")
def partner_statement(partner: Partner = Depends(require_partner)):
    """This month's commission, account by account."""
    accounts = [
        _account_row(t, partner.commission_percent)
        for t in STORE.tenants_for_partner(partner.partner_id)
    ]
    billings = round(sum(a["monthly_usd"] for a in accounts), 2)
    commission = round(sum(a["commission_usd"] for a in accounts), 2)
    return {
        "partner": partner.company_name,
        "commission_percent": partner.commission_percent,
        "lines": [
            {
                "company_name": a["company_name"],
                "monthly_usd": a["monthly_usd"],
                "commission_usd": a["commission_usd"],
            }
            for a in sorted(accounts, key=lambda a: -a["monthly_usd"])
        ],
        "billings_usd": billings,
        "commission_usd": commission,
        "note": (
            "Paid on revenue collected from these accounts, not on any loss "
            "avoided. A partner earns from accounts that stay."
        ),
        "issued_at": iso(utc_now()),
    }
