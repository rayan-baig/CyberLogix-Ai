"""The assurance add-on: a guarantee, priced flat.

The pitch is simple. If a breach happens and we fail to alert anyone, we
pay the customer's deductible up to a cap. That is a real commitment, and
it is the thing that moves the product out of the software budget and into
the risk budget — nobody negotiates hard over a line item that carries an
indemnity.

Priced as a fixed monthly amount per unit, never as a share of what the
customer saved. A percentage of savings means a quiet year pays us nothing
for the same standing obligation, and it aligns us against the customer
the moment a claim is marginal.

The honest part, and the part that makes it defensible rather than
reckless: an operator can void their own cover without realising. A dead
battery, an empty on-call roster, a sensor offline for a week — each of
those means the guarantee could not have worked. So eligibility is
computed continuously and shown to the customer *before* an event, not
produced as an excuse afterwards. A guarantee whose exclusions only appear
at claim time is a trick; one that tells you what to fix this morning is a
service.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from auth import require_tenant
from store import STORE, Tenant, iso, utc_now

logger = logging.getLogger("cyberlogix.assurance")

router = APIRouter(prefix="/api/assurance", tags=["Loss Assurance"])

# What the add-on costs and what it covers. Flat, per unit, per month.
ASSURANCE_MONTHLY_PER_UNIT_USD = 149.0
ASSURANCE_PAYOUT_CAP_USD = 25000.0

# The commitment. A breach must produce a dispatched alert within this
# window, and someone must be reachable to receive it.
DISPATCH_SLA_SECONDS = 60

# A sensor silent this long cannot warn anybody, so it is outside cover
# until it reports again.
COVER_LAPSES_AFTER_MINUTES = 60


def _unit_check(name: str, passed: bool, detail: str) -> Dict[str, Any]:
    return {"check": name, "passed": passed, "detail": detail}


def evaluate_cover(tenant: Tenant) -> Dict[str, Any]:
    """What is covered right now, and what would void it.

    Every failing check names the specific thing to fix. "Your cover is
    void" is useless; "REEFER-118 has not reported for 3 hours" is
    actionable this morning.
    """
    now = utc_now()
    sensors = STORE.sensors_for(tenant.tenant_id)
    roster = STORE.sms_recipients(tenant)
    has_real_roster = any(c.contact_id != "fallback" for c in roster)

    estate_checks = [
        _unit_check(
            "on_call_roster",
            has_real_roster,
            "Someone is on the roster to receive an alert."
            if has_real_roster
            else "No roster is configured, so alerts fall back to the single "
            "contact captured at onboarding. Add at least one person.",
        ),
        _unit_check(
            "paid_plan",
            tenant.plan != "trial",
            "On a paid plan."
            if tenant.plan != "trial"
            else "Trial estates are not covered; the guarantee applies to "
            "paid contracts.",
        ),
    ]

    covered: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []

    for sensor in sorted(sensors, key=lambda s: s.sensor_id):
        reasons = []
        if sensor.offline(now):
            last = iso(sensor.last_seen) or "never"
            reasons.append(
                f"has not reported since {last}; a silent sensor cannot warn "
                "anybody"
            )
        if sensor.battery_low:
            reasons.append(
                f"battery is at {sensor.battery_percent}% and will go dark "
                "before it is noticed"
            )
        if sensor.last_temperature is None:
            reasons.append("has never reported a reading")

        row = {
            "sensor_id": sensor.sensor_id,
            "location_name": sensor.location_name,
            "site_id": sensor.site_id,
        }
        if reasons:
            excluded.append({**row, "reasons": reasons})
        else:
            covered.append(row)

    estate_ok = all(c["passed"] for c in estate_checks)
    return {
        "in_force": estate_ok and bool(covered),
        "estate_checks": estate_checks,
        "covered_units": len(covered),
        "excluded_units": len(excluded),
        "covered": covered,
        "excluded": excluded,
        "monthly_cost_usd": round(
            ASSURANCE_MONTHLY_PER_UNIT_USD * len(covered), 2
        ),
        "payout_cap_usd": ASSURANCE_PAYOUT_CAP_USD,
        "evaluated_at": iso(now),
    }


def evaluate_performance(tenant: Tenant, days: int) -> Dict[str, Any]:
    """Whether we actually met the commitment over a period.

    Published to the customer whether it flatters us or not. A guarantee
    from a vendor who will not show you their own miss rate is a slogan.
    """
    since = utc_now() - timedelta(days=days)
    incidents = STORE.incidents_for(tenant.tenant_id, since=since)

    met = []
    missed = []
    for incident in incidents:
        rows = incident.sms_fanout or (
            [incident.sms_delivery] if incident.sms_delivery else []
        )
        attempted = bool(rows)
        # "Not configured" is a miss against the commitment, not an excuse:
        # from the customer's side an alert that was composed and never sent
        # is an alert that never happened.
        delivered = any(r and r.get("delivered") for r in rows)
        record = {
            "incident_id": incident.incident_id,
            "sensor_id": incident.sensor_id,
            "opened_at": iso(incident.opened_at),
            "alert_attempted": attempted,
            "alert_delivered": delivered,
        }
        (met if delivered else missed).append(record)

    total = len(incidents)
    return {
        "period_days": days,
        "incidents": total,
        "alerts_delivered": len(met),
        "alerts_missed": len(missed),
        "delivery_rate_percent": (
            round(len(met) / total * 100, 2) if total else None
        ),
        "sla_seconds": DISPATCH_SLA_SECONDS,
        "misses": missed,
        "note": (
            "A miss is any breach where no alert reached anybody, including "
            "ones we composed but could not send. That is the number the "
            "guarantee is written against."
        ),
    }


@router.get("/cover")
def cover(tenant: Tenant = Depends(require_tenant)):
    """What the guarantee covers right now, and what would void it."""
    state = evaluate_cover(tenant)
    return {
        **state,
        "terms": {
            "commitment": (
                "If a breach is recorded on a covered unit and no alert "
                "reaches anyone on the roster, CyberLogix AI reimburses the "
                f"insurance deductible for that event up to "
                f"${ASSURANCE_PAYOUT_CAP_USD:,.0f}."
            ),
            "price": (
                f"${ASSURANCE_MONTHLY_PER_UNIT_USD:,.0f} per covered unit per "
                "month, flat. Not a share of anything saved."
            ),
            "exclusions_are_shown_in_advance": (
                "Every exclusion above is computed continuously and visible "
                "before an event, not produced afterwards."
            ),
        },
    }


@router.get("/performance")
def performance(
    days: int = Query(90, ge=1, le=730),
    tenant: Tenant = Depends(require_tenant),
):
    """Our own record against the commitment, published either way."""
    return evaluate_performance(tenant, days)


@router.get("/quote")
def quote(tenant: Tenant = Depends(require_tenant)):
    """What the add-on would cost this estate."""
    state = evaluate_cover(tenant)
    units = state["covered_units"]
    monthly = round(ASSURANCE_MONTHLY_PER_UNIT_USD * units, 2)
    return {
        "covered_units": units,
        "excluded_units": state["excluded_units"],
        "unit_price_usd": ASSURANCE_MONTHLY_PER_UNIT_USD,
        "monthly_usd": monthly,
        "annual_usd": round(monthly * 12, 2),
        "payout_cap_usd": ASSURANCE_PAYOUT_CAP_USD,
        "blocked_by": [
            c for c in state["estate_checks"] if not c["passed"]
        ],
        "note": (
            "Priced on units that would actually be covered today. Fixing an "
            "exclusion adds a unit to cover and to the price."
        ),
    }
