"""The price book, and what a customer's estate actually costs.

Pricing is per unit, per vertical, per month: a rack is not a reefer truck
and is not priced like one. A customer with three data-hall racks and two
hangar bays is billed 3 x $499 + 2 x $349, and the invoice is derived from
the sensors actually registered rather than from a plan tier.

Plan tiers still exist, but they are about evaluation versus a paid
contract and the seat ceiling that comes with it — they are not what sets
the price.

`loss_avoided_usd` is the figure the product is sold on: the loss a single
prevented failure represents. Only the verticals where a concrete number
was supplied carry one; the rest are deliberately null rather than guessed,
because an ROI page built on invented figures is worse than one that says
"not quantified".
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from auth import require_tenant
from store import INDUSTRY_PROFILES, PLAN_TIERS, STORE, Tenant, iso, utc_now

router = APIRouter(prefix="/api/billing", tags=["Pricing & Billing"])

PRICE_BOOK: Dict[str, Dict[str, Any]] = {
    "cybersecurity": {
        "unit": "rack",
        "monthly_usd": 499.0,
        "pitch": (
            "Prevents $50,000+ server meltdowns and SLA contract breaches with "
            "predictive forecasting."
        ),
        "loss_avoided_usd": 50000.0,
    },
    "private_aviation": {
        "unit": "bay",
        "monthly_usd": 349.0,
        "pitch": (
            "Protects multi-million-dollar avionics and jet components from "
            "high-humidity moisture corrosion."
        ),
        "loss_avoided_usd": None,
    },
    "superyacht": {
        "unit": "vessel",
        "monthly_usd": 399.0,
        "pitch": (
            "Guards engine rooms against thermal fires and secures $150k charter "
            "guest food freezers at sea."
        ),
        "loss_avoided_usd": 150000.0,
    },
    "solar_infrastructure": {
        "unit": "enclosure",
        "monthly_usd": 299.0,
        "pitch": (
            "Monitors thermal runaway in commercial battery banks to protect "
            "against catastrophic fire liability."
        ),
        "loss_avoided_usd": None,
    },
    "medical_lab": {
        "unit": "vault",
        "monthly_usd": 199.0,
        "pitch": (
            "Mandated by strict OSHA/FDA chain-of-custody rules; includes "
            "automated audit report generation."
        ),
        "loss_avoided_usd": None,
    },
    "country_club": {
        "unit": "kitchen",
        "monthly_usd": 199.0,
        "pitch": (
            "Safeguards high-revenue holiday dining inventory (Thanksgiving, "
            "Mother's Day) from compressor failure."
        ),
        "loss_avoided_usd": None,
    },
    "logistics": {
        "unit": "reefer truck",
        "monthly_usd": 129.0,
        "pitch": (
            "Generates tamper-proof transit handover temperature passes to "
            "eliminate dock cargo rejection disputes."
        ),
        "loss_avoided_usd": None,
    },
    "restaurant": {
        "unit": "location",
        "monthly_usd": 99.0,
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

    return {
        "tenant_id": tenant.tenant_id,
        "company_name": tenant.company_name,
        "plan": tenant.plan,
        "plan_name": tier["name"],
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
