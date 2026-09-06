"""The price book, and what a customer's estate actually costs.

Pricing is per unit, per vertical, per month: a rack is not a reefer truck
and is not priced like one. A customer with three data-hall racks and two
hangar bays is billed 3 x $499 + 2 x $349, and the invoice is derived from
the sensors actually registered rather than from a plan tier.

Plan tiers still exist, but they are about evaluation versus a paid
contract and the seat ceiling that comes with it — they are not what sets
the price.

Rates are set for the pinnacle of each market rather than its volume: an
owner who spends millions a year on a vessel reads a $399 service as a toy,
and prices below the value at risk invite exactly the buyer who will haggle.

`loss_avoided_usd` is the figure the product is sold on: the loss a single
prevented failure represents. Only the verticals where a concrete number
was supplied carry one; the rest are deliberately null rather than guessed,
because an ROI page built on invented figures is worse than one that says
"not quantified".
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import require_tenant
from store import (
    INDUSTRY_PROFILES,
    PLAN_TIERS,
    STORE,
    Tenant,
    iso,
    utc_now,
)

router = APIRouter(prefix="/api/billing", tags=["Pricing & Billing"])

PRICE_BOOK: Dict[str, Dict[str, Any]] = {
    "cybersecurity": {
        "unit": "rack",
        "monthly_usd": 899.0,
        "pitch": (
            "Prevents $50,000+ server meltdowns and SLA contract breaches with "
            "predictive forecasting."
        ),
        "loss_avoided_usd": 50000.0,
    },
    "private_aviation": {
        "unit": "bay",
        "monthly_usd": 1999.0,
        "pitch": (
            "Protects multi-million-dollar avionics and jet components from "
            "high-humidity moisture corrosion."
        ),
        "loss_avoided_usd": None,
    },
    "superyacht": {
        "unit": "vessel",
        "monthly_usd": 4999.0,
        "pitch": (
            "Guards engine rooms against thermal fires and secures $150k charter "
            "guest food freezers at sea."
        ),
        "loss_avoided_usd": 150000.0,
    },
    "solar_infrastructure": {
        "unit": "enclosure",
        "monthly_usd": 899.0,
        "pitch": (
            "Monitors thermal runaway in commercial battery banks to protect "
            "against catastrophic fire liability."
        ),
        "loss_avoided_usd": None,
    },
    "medical_lab": {
        "unit": "vault",
        "monthly_usd": 1499.0,
        "pitch": (
            "Mandated by strict OSHA/FDA chain-of-custody rules; includes "
            "automated audit report generation."
        ),
        "loss_avoided_usd": None,
    },
    "country_club": {
        "unit": "kitchen",
        "monthly_usd": 1499.0,
        "pitch": (
            "Safeguards high-revenue holiday dining inventory (Thanksgiving, "
            "Mother's Day) from compressor failure."
        ),
        "loss_avoided_usd": None,
    },
    "logistics": {
        "unit": "reefer truck",
        "monthly_usd": 749.0,
        "pitch": (
            "Generates tamper-proof transit handover temperature passes to "
            "eliminate dock cargo rejection disputes."
        ),
        "loss_avoided_usd": None,
    },
    "wine_and_art": {
        "unit": "cellar",
        "monthly_usd": 2499.0,
        "pitch": (
            "Certifies unbroken storage conditions for a collection whose "
            "value depends entirely on provenance being unquestioned."
        ),
        "loss_avoided_usd": 250000.0,
    },
    "pharmacy": {
        "unit": "pharmacy",
        "monthly_usd": 1299.0,
        "pitch": (
            "Meets the continuous-monitoring requirement for vaccine storage "
            "and produces the audit record on demand."
        ),
        "loss_avoided_usd": 40000.0,
    },
    "cannabis": {
        "unit": "cultivation room",
        "monthly_usd": 1199.0,
        "pitch": (
            "Documents environmental control to the standard a state licence "
            "renewal is judged against."
        ),
        "loss_avoided_usd": 60000.0,
    },
    "restaurant": {
        "unit": "location",
        "monthly_usd": 999.0,
        "pitch": (
            "Prevents $15,000 walk-in freezer meat/seafood spoilage and "
            "automates health department logs."
        ),
        "loss_avoided_usd": 15000.0,
    },
}

# Every vertical must be priced, or a customer could register a sensor the
# invoice cannot account for.
assert set(PRICE_BOOK) == set(INDUSTRY_PROFILES), (
    "Price book and industry profiles are out of step: "
    f"{set(PRICE_BOOK) ^ set(INDUSTRY_PROFILES)}"
)


# ---- contract terms ---------------------------------------------------
#
# The rate card is only half of what an estate is worth. These are the
# terms that decide the other half, and the ones customers rarely
# negotiate out because each is standard practice in enterprise software.

# Charged once per site at commissioning. It funds acquisition cost on the
# day of signature rather than eleven months later, and a customer who has
# paid to be installed does not churn casually.
SETUP_FEE_PER_SITE_USD = 1500.0

# Paying a year up front is worth more than the ten percent it costs: it
# removes the collections problem and fixes the customer for twelve months.
ANNUAL_PREPAY_DISCOUNT_PERCENT = 10.0

# Written into multi-year contracts. Uncontroversial at signature and
# compounding: three years at five percent is sixteen percent more
# contract value for no additional delivery.
ANNUAL_ESCALATOR_PERCENT = 5.0
MAX_CONTRACT_YEARS = 5

# Flat monthly add-ons. Every one is a fixed fee — nothing here is a share
# of what a customer saved, because a quiet year would then pay nothing
# for the same standing obligation.
ADD_ONS: Dict[str, Dict[str, Any]] = {
    "assurance": {
        "name": "Loss Assurance",
        "basis": "per covered unit",
        "monthly_usd": 149.0,
        "description": (
            "If a breach is recorded and no alert reaches anybody, we "
            "reimburse the deductible for that event up to $25,000. "
            "Exclusions are computed continuously and shown in advance."
        ),
    },
    "vault": {
        "name": "Certified Compliance Vault",
        "basis": "per estate",
        "monthly_usd": 499.0,
        "description": (
            "Hash-chained readings and signed attestations an insurer, "
            "auditor or buyer can verify without trusting either party, "
            "plus unlimited claim evidence packets."
        ),
    },
    "benchmarks": {
        "name": "Sector Benchmarks",
        "basis": "per estate",
        "monthly_usd": 299.0,
        "description": (
            "Where this estate sits against comparable operators on "
            "excursion rate, response time and uptime, refreshed quarterly."
        ),
    },
    "equipment_intelligence": {
        "name": "Equipment Intelligence",
        "basis": "per estate",
        "monthly_usd": 399.0,
        "description": (
            "Failure and drift rates by hardware manufacturer across the "
            "whole fleet, for anyone specifying what to buy next."
        ),
    },
}


def add_on_price(key: str, units: int) -> float:
    """What one add-on costs an estate of this size."""
    entry = ADD_ONS[key]
    multiplier = units if entry["basis"] == "per covered unit" else 1
    return round(entry["monthly_usd"] * multiplier, 2)


def escalated_schedule(
    monthly_usd: float, years: int, escalator_percent: float = ANNUAL_ESCALATOR_PERCENT
) -> List[Dict[str, Any]]:
    """Year-by-year contract value with the escalator applied."""
    schedule = []
    rate = monthly_usd
    for year in range(1, max(1, min(years, MAX_CONTRACT_YEARS)) + 1):
        if year > 1:
            rate = round(rate * (1 + escalator_percent / 100.0), 2)
        schedule.append(
            {
                "year": year,
                "monthly_usd": rate,
                "annual_usd": round(rate * 12, 2),
            }
        )
    return schedule


def unit_price(vertical: str) -> float:
    return PRICE_BOOK[vertical]["monthly_usd"]


def plural(vertical: str, count: int) -> str:
    unit = PRICE_BOOK[vertical]["unit"]
    if count == 1:
        return unit
    return unit + ("es" if unit.endswith(("s", "x", "ch")) else "s")


def build_subscription(tenant: Tenant) -> Dict[str, Any]:
    """Price a tenant's estate from the sensors actually registered."""
    sensors = STORE.sensors_for(tenant.tenant_id)

    counts: Dict[str, int] = {}
    for sensor in sensors:
        counts[sensor.industry_vertical] = counts.get(sensor.industry_vertical, 0) + 1

    lines: List[Dict[str, Any]] = []
    for vertical, count in sorted(
        counts.items(), key=lambda kv: -unit_price(kv[0]) * kv[1]
    ):
        entry = PRICE_BOOK[vertical]
        lines.append(
            {
                "vertical": vertical,
                "industry": INDUSTRY_PROFILES[vertical]["name"],
                "unit": entry["unit"],
                "units": count,
                "unit_price_usd": entry["monthly_usd"],
                "line_total_usd": round(entry["monthly_usd"] * count, 2),
                "description": f"{count} {plural(vertical, count)}",
            }
        )

    mrr = round(sum(line["line_total_usd"] for line in lines), 2)
    tier = tenant.entitlements()
    billable = tenant.plan != "trial"

    # A cluster contract supersedes the rate card: the chain is buying whole
    # branches, and billing both models at once would double-charge it.
    contract = STORE.active_contract(tenant.tenant_id)
    if contract is not None:
        return {
            "tenant_id": tenant.tenant_id,
            "company_name": tenant.company_name,
            "plan": tenant.plan,
            "plan_name": tier["name"],
            "billing_model": "enterprise_volume",
            "billable": billable,
            "units_total": len(sensors),
            "seats_total": tier["max_sensors"],
            "contract": contract.public(),
            "line_items": [
                {
                    "vertical": contract.industry_vertical,
                    "industry": INDUSTRY_PROFILES[contract.industry_vertical]["name"],
                    "unit": "branch",
                    "units": contract.enrolled_branches,
                    "unit_price_usd": contract.effective_rate_per_branch_usd,
                    "line_total_usd": contract.monthly_usd,
                    "description": (
                        f"{contract.enrolled_branches} branches · "
                        f"{contract.tier_label}"
                    ),
                }
            ],
            "monthly_total_usd": contract.monthly_usd,
            "annual_total_usd": contract.annual_contract_value_usd,
            "effective_monthly_usd": 0.0 if not billable else contract.monthly_usd,
            "rate_card_equivalent_usd": mrr,
            "currency": "USD",
            "note": (
                f"Billed on volume contract {contract.account_id} "
                f"({contract.tier_label}); covers every sensor inside an "
                "enrolled branch."
            ),
            "generated_at": iso(utc_now()),
        }

    return {
        "tenant_id": tenant.tenant_id,
        "company_name": tenant.company_name,
        "plan": tenant.plan,
        "plan_name": tier["name"],
        "billing_model": "per_unit",
        "billable": billable,
        "units_total": len(sensors),
        "seats_total": tier["max_sensors"],
        "line_items": lines,
        "monthly_total_usd": mrr,
        "annual_total_usd": round(mrr * 12, 2),
        "effective_monthly_usd": 0.0 if not billable else mrr,
        "currency": "USD",
        "note": (
            "Trial estates are priced but not charged; the figure shows what "
            "the estate would cost on a paid contract."
            if not billable
            else "Priced from the sensors currently registered."
        ),
        "generated_at": iso(utc_now()),
    }


def build_roi(tenant: Tenant, days: int) -> Dict[str, Any]:
    """Weigh what the subscription costs against what it caught.

    A prevented failure is counted only where that vertical has a supplied
    loss figure. Incidents in the other verticals are still listed, marked
    unquantified, so the total is never inflated by a number nobody gave us.
    """
    from datetime import timedelta

    since = utc_now() - timedelta(days=days)
    incidents = STORE.incidents_for(tenant.tenant_id, since=since)
    subscription = build_subscription(tenant)

    quantified: List[Dict[str, Any]] = []
    unquantified: List[Dict[str, Any]] = []

    for incident in incidents:
        entry = PRICE_BOOK[incident.industry_vertical]
        row = {
            "incident_id": incident.incident_id,
            "sensor_id": incident.sensor_id,
            "industry": INDUSTRY_PROFILES[incident.industry_vertical]["name"],
            "catastrophe": incident.catastrophe,
            "opened_at": iso(incident.opened_at),
            "answered": incident.acknowledged_at is not None,
            "loss_avoided_usd": entry["loss_avoided_usd"],
        }
        (quantified if entry["loss_avoided_usd"] else unquantified).append(row)

    # Only an answered incident is a prevented loss. One nobody responded to
    # is a warning that went unheeded, and claiming it as a save would be a
    # lie the customer can check.
    answered = [row for row in quantified if row["answered"]]
    protected = round(sum(row["loss_avoided_usd"] for row in answered), 2)
    period_cost = round(subscription["monthly_total_usd"] * (days / 30.0), 2)

    return {
        "tenant_id": tenant.tenant_id,
        "company_name": tenant.company_name,
        "period_days": days,
        "incidents_total": len(incidents),
        "incidents_answered": sum(1 for i in incidents if i.acknowledged_at),
        "quantified_saves": len(answered),
        "unquantified_saves": len(unquantified),
        "loss_avoided_usd": protected,
        "subscription_cost_usd": period_cost,
        "net_usd": round(protected - period_cost, 2),
        "return_multiple": (
            round(protected / period_cost, 1) if period_cost > 0 and protected else None
        ),
        "detail": quantified + unquantified,
        "note": (
            "Counts only incidents a person answered, in verticals with a "
            "supplied loss figure. Verticals without one are listed but "
            "excluded from the total rather than estimated."
        ),
    }


@router.get("/pricing")
def price_book():
    """The public price list, highest-value vertical first."""
    rows = [
        {
            "vertical": vertical,
            "industry": INDUSTRY_PROFILES[vertical]["name"],
            "unit": entry["unit"],
            "monthly_usd": entry["monthly_usd"],
            "price_label": f"${entry['monthly_usd']:,.0f} / {entry['unit']} / month",
            "pitch": entry["pitch"],
            "protects_against": INDUSTRY_PROFILES[vertical]["catastrophe"],
            "loss_avoided_usd": entry["loss_avoided_usd"],
        }
        for vertical, entry in sorted(
            PRICE_BOOK.items(), key=lambda kv: -kv[1]["monthly_usd"]
        )
    ]
    return {
        "count": len(rows),
        "currency": "USD",
        "billing": "Per unit, per month. Priced from registered sensors.",
        "plans": [
            {
                "plan": key,
                "name": tier["name"],
                "max_units": tier["max_sensors"],
                "term_days": tier["term_days"],
                "charged": key != "trial",
            }
            for key, tier in PLAN_TIERS.items()
        ],
        "pricing": rows,
    }


@router.get("")
def subscription(tenant: Tenant = Depends(require_tenant)):
    """What this estate costs a month, itemised by vertical."""
    return build_subscription(tenant)


@router.get("/roi")
def roi(days: int = 30, tenant: Tenant = Depends(require_tenant)):
    """Loss avoided against subscription cost over a period."""
    days = max(1, min(days, 365))
    return build_roi(tenant, days)


@router.get("/add-ons")
def list_add_ons(tenant: Tenant = Depends(require_tenant)):
    """The add-on catalogue, priced for this estate."""
    units = STORE.seat_count(tenant.tenant_id)
    return {
        "count": len(ADD_ONS),
        "units": units,
        "add_ons": [
            {
                "key": key,
                **entry,
                "monthly_for_this_estate_usd": add_on_price(key, units),
            }
            for key, entry in ADD_ONS.items()
        ],
        "note": (
            "Every add-on is a fixed monthly fee. None is a share of what "
            "the customer saved: a quiet year would then pay nothing for the "
            "same standing obligation."
        ),
    }


@router.get("/deal")
def full_deal(
    years: int = Query(1, ge=1, le=MAX_CONTRACT_YEARS),
    annual_prepay: bool = Query(
        False, description="Pay year one up front for a discount."
    ),
    include_add_ons: str = Query(
        "", description="Comma-separated add-on keys, e.g. assurance,vault"
    ),
    tenant: Tenant = Depends(require_tenant),
):
    """The whole deal: subscription, add-ons, setup, term and escalator.

    Everything a signature page needs in one figure, so nobody discovers a
    setup fee or an escalator after the fact — which is the only way those
    terms survive a renewal conversation.
    """
    subscription = build_subscription(tenant)
    units = subscription["units_total"]
    base_monthly = subscription["monthly_total_usd"]

    wanted = [
        key.strip()
        for key in (include_add_ons or "").split(",")
        if key.strip()
    ]
    unknown = [key for key in wanted if key not in ADD_ONS]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown add-on(s) {unknown}. Allowed: {list(ADD_ONS)}",
        )

    add_on_lines = [
        {
            "key": key,
            "name": ADD_ONS[key]["name"],
            "basis": ADD_ONS[key]["basis"],
            "monthly_usd": add_on_price(key, units),
        }
        for key in wanted
    ]
    add_on_monthly = round(sum(line["monthly_usd"] for line in add_on_lines), 2)
    monthly = round(base_monthly + add_on_monthly, 2)

    sites = len(STORE.sites_for(tenant.tenant_id))
    # An estate with no sites recorded is still commissioned somewhere; one
    # location is the floor rather than a free installation.
    billable_sites = max(sites, 1) if units else 0
    setup = round(SETUP_FEE_PER_SITE_USD * billable_sites, 2)

    schedule = escalated_schedule(monthly, years)
    total_contract = round(sum(row["annual_usd"] for row in schedule), 2)

    year_one = schedule[0]["annual_usd"]
    prepay_discount = (
        round(year_one * ANNUAL_PREPAY_DISCOUNT_PERCENT / 100.0, 2)
        if annual_prepay
        else 0.0
    )

    return {
        "company_name": tenant.company_name,
        "units": units,
        "sites": sites,
        "subscription_monthly_usd": base_monthly,
        "add_ons": add_on_lines,
        "add_ons_monthly_usd": add_on_monthly,
        "monthly_usd": monthly,
        "setup": {
            "per_site_usd": SETUP_FEE_PER_SITE_USD,
            "sites_billed": billable_sites,
            "one_time_usd": setup,
            "note": (
                "Charged once, at commissioning. Covers placing every unit, "
                "proving the alert path end to end, and training the roster."
            ),
        },
        "term": {
            "years": years,
            "escalator_percent": ANNUAL_ESCALATOR_PERCENT if years > 1 else 0.0,
            "schedule": schedule,
            "total_contract_value_usd": total_contract,
        },
        "annual_prepay": {
            "elected": annual_prepay,
            "discount_percent": ANNUAL_PREPAY_DISCOUNT_PERCENT,
            "discount_usd": prepay_discount,
            "year_one_due_usd": round(year_one - prepay_discount + setup, 2),
        },
        "first_invoice_usd": round(
            (year_one - prepay_discount + setup)
            if annual_prepay
            else monthly + setup,
            2,
        ),
        "total_first_year_usd": round(year_one - prepay_discount + setup, 2),
        "currency": "USD",
        "generated_at": iso(utc_now()),
    }
