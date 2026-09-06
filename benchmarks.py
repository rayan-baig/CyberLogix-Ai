"""Anonymised industry benchmarks, and what the fleet knows about hardware.

Two products fall out of data already being collected, at close to zero
marginal cost.

The first is a benchmark: "your walk-ins run 3.2°F warmer than the top
quartile of comparable kitchens." A customer cannot get that anywhere
else, because nobody else is holding the comparison set. It renews on its
own — the number changes every quarter and they want to see it again.

The second is equipment intelligence. Across an estate, one device model
will start drifting or dropping offline before its manufacturer knows.
That is worth real money to a manufacturer and to anyone specifying
hardware, and it is a by-product of running the monitoring.

Both are sold flat — an annual data subscription — not as a share of
anything they save.

The rule that makes this safe to sell: nothing leaves this module that
could identify one customer. A cohort under the k-anonymity floor returns
no figures at all, and says why. A benchmark that leaks one chain's
performance to a competitor would end the business that produced it.
"""

from __future__ import annotations

import logging
import statistics
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import require_tenant
from store import (
    INDUSTRY_PROFILES,
    STORE,
    Tenant,
    display_temperature,
    iso,
    resolve_vertical,
    utc_now,
)

logger = logging.getLogger("cyberlogix.benchmarks")

router = APIRouter(prefix="/api/benchmarks", tags=["Industry Benchmarks"])

# No cohort statistic is published unless this many distinct customers sit
# behind it. With fewer, a participant can subtract themselves and read a
# competitor's numbers straight off.
MIN_COHORT_TENANTS = 5

# Equipment findings need a population too, and one estate running forty
# units of the same model is still one estate's maintenance regime.
MIN_MODEL_TENANTS = 3
MIN_MODEL_UNITS = 8


def _percentile(values: List[float], fraction: float) -> Optional[float]:
    """A percentile that behaves on tiny lists, unlike the naive index."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _tenant_stats(tenant_id: str, vertical: str, since) -> Optional[Dict[str, Any]]:
    """One customer's summary for a vertical, or None if they have no data."""
    sensors = [
        s
        for s in STORE.sensors_for(tenant_id)
        if s.industry_vertical == vertical
    ]
    if not sensors:
        return None

    temps: List[float] = []
    readings = 0
    breached = 0
    for sensor in sensors:
        for reading in STORE.readings_for(sensor.sensor_id, since=since):
            readings += 1
            temps.append(reading.temperature_fahrenheit)
            if reading.breached:
                breached += 1
    if not readings:
        return None

    incidents = [
        i
        for i in STORE.incidents_for(tenant_id, since=since)
        if i.industry_vertical == vertical
    ]
    acks = [
        (i.acknowledged_at - i.opened_at).total_seconds() / 60.0
        for i in incidents
        if i.acknowledged_at is not None
    ]

    return {
        "units": len(sensors),
        "readings": readings,
        "mean_f": statistics.fmean(temps),
        "excursion_rate": breached / readings * 100.0,
        "incidents_per_unit": len(incidents) / len(sensors),
        "mean_minutes_to_acknowledge": statistics.fmean(acks) if acks else None,
        "uptime_percent": (
            sum(1 for s in sensors if not s.offline()) / len(sensors) * 100.0
        ),
    }


def cohort(vertical: str, days: int) -> Dict[str, Any]:
    """Everyone's summary for a vertical, before any figure is published."""
    since = utc_now() - timedelta(days=days)
    rows = []
    for tenant in STORE.list_tenants():
        stats = _tenant_stats(tenant.tenant_id, vertical, since)
        if stats is not None:
            rows.append({"tenant_id": tenant.tenant_id, **stats})
    return {"vertical": vertical, "days": days, "rows": rows}


def _distribution(
    rows: List[Dict[str, Any]], field: str, unit: str = "F"
) -> Optional[Dict[str, Any]]:
    values = [r[field] for r in rows if r.get(field) is not None]
    if not values:
        return None
    convert = field == "mean_f"
    def shown(v):
        return display_temperature(v, unit) if convert else round(v, 2)

    return {
        "best_quartile": shown(_percentile(values, 0.25)),
        "median": shown(_percentile(values, 0.5)),
        "worst_quartile": shown(_percentile(values, 0.75)),
        "cohort_size": len(values),
    }


def _standing(value: Optional[float], values: List[float], lower_is_better=True):
    """Where one customer sits, as a percentile of the cohort."""
    if value is None or not values:
        return None
    below = sum(1 for v in values if v < value)
    percentile = round(below / len(values) * 100)
    if lower_is_better:
        percentile = 100 - percentile
    return {
        "percentile": percentile,
        "reading": (
            "top quartile"
            if percentile >= 75
            else "above average"
            if percentile >= 50
            else "below average"
            if percentile >= 25
            else "bottom quartile"
        ),
    }


@router.get("/{vertical}")
def sector_benchmark(
    vertical: str,
    days: int = Query(90, ge=7, le=730),
    tenant: Tenant = Depends(require_tenant),
):
    """Where this customer sits against comparable operators."""
    key = resolve_vertical(vertical)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown vertical. Allowed keys: {list(INDUSTRY_PROFILES)}",
        )

    data = cohort(key, days)
    rows = data["rows"]
    unit = tenant.temperature_unit
    profile = INDUSTRY_PROFILES[key]

    if len(rows) < MIN_COHORT_TENANTS:
        return {
            "vertical": key,
            "industry": profile["name"],
            "period_days": days,
            "available": False,
            "cohort_size": len(rows),
            "minimum_cohort": MIN_COHORT_TENANTS,
            "reason": (
                f"Only {len(rows)} operators in this sector have reported in "
                f"the period. Below {MIN_COHORT_TENANTS} a participant could "
                "work out a named competitor's figures, so nothing is "
                "published."
            ),
        }

    mine = next((r for r in rows if r["tenant_id"] == tenant.tenant_id), None)
    return {
        "vertical": key,
        "industry": profile["name"],
        "asset_plural": profile["asset_plural"],
        "period_days": days,
        "available": True,
        "cohort_size": len(rows),
        "temperature_unit": unit,
        "cohort": {
            "mean_temperature": _distribution(rows, "mean_f", unit),
            "excursion_rate_percent": _distribution(rows, "excursion_rate"),
            "incidents_per_unit": _distribution(rows, "incidents_per_unit"),
            "minutes_to_acknowledge": _distribution(
                rows, "mean_minutes_to_acknowledge"
            ),
            "unit_uptime_percent": _distribution(rows, "uptime_percent"),
        },
        "you": (
            {
                "units": mine["units"],
                "mean_temperature": display_temperature(mine["mean_f"], unit),
                "excursion_rate_percent": round(mine["excursion_rate"], 2),
                "minutes_to_acknowledge": (
                    round(mine["mean_minutes_to_acknowledge"], 1)
                    if mine["mean_minutes_to_acknowledge"] is not None
                    else None
                ),
                "standing": {
                    "excursion_rate": _standing(
                        mine["excursion_rate"],
                        [r["excursion_rate"] for r in rows],
                    ),
                    "minutes_to_acknowledge": _standing(
                        mine["mean_minutes_to_acknowledge"],
                        [
                            r["mean_minutes_to_acknowledge"]
                            for r in rows
                            if r["mean_minutes_to_acknowledge"] is not None
                        ],
                    ),
                    "uptime": _standing(
                        mine["uptime_percent"],
                        [r["uptime_percent"] for r in rows],
                        lower_is_better=False,
                    ),
                },
            }
            if mine
            else None
        ),
        "privacy": (
            f"Aggregated across {len(rows)} operators. No figure is published "
            f"for a cohort under {MIN_COHORT_TENANTS}, and no customer is "
            "named anywhere in this response."
        ),
        "generated_at": iso(utc_now()),
    }


def _model_of(serial: Optional[str]) -> Optional[str]:
    """The manufacturer and model out of a device serial.

    Serials arrive as MAKE-MODEL-ADDRESS or MAKE-ADDRESS; the leading token
    is the make, which is the level any finding is reported at. Anything
    finer risks identifying the estate it came from.
    """
    if not serial or "-" not in serial:
        return None
    return serial.split("-", 1)[0].upper()


@router.get("")
def equipment_intelligence(
    days: int = Query(180, ge=30, le=730),
    tenant: Tenant = Depends(require_tenant),
):
    """How hardware makes behave across the whole fleet.

    Useful to a customer choosing what to buy next, and saleable to the
    manufacturers themselves — who will typically learn a model is failing
    early from this before their own returns data shows it.
    """
    since = utc_now() - timedelta(days=days)
    makes: Dict[str, Dict[str, Any]] = {}

    for tenant_row in STORE.list_tenants():
        for sensor in STORE.sensors_for(tenant_row.tenant_id):
            make = _model_of(sensor.external_device_sn)
            if make is None:
                continue
            bucket = makes.setdefault(
                make,
                {
                    "make": make,
                    "units": 0,
                    "tenants": set(),
                    "offline": 0,
                    "low_battery": 0,
                    "readings": 0,
                    "breached": 0,
                },
            )
            bucket["units"] += 1
            bucket["tenants"].add(tenant_row.tenant_id)
            if sensor.offline():
                bucket["offline"] += 1
            if sensor.battery_low:
                bucket["low_battery"] += 1
            for reading in STORE.readings_for(sensor.sensor_id, since=since):
                bucket["readings"] += 1
                if reading.breached:
                    bucket["breached"] += 1

    published = []
    withheld = 0
    for bucket in makes.values():
        tenants = len(bucket["tenants"])
        if tenants < MIN_MODEL_TENANTS or bucket["units"] < MIN_MODEL_UNITS:
            withheld += 1
            continue
        published.append(
            {
                "make": bucket["make"],
                "units_observed": bucket["units"],
                "operators": tenants,
                "offline_rate_percent": round(
                    bucket["offline"] / bucket["units"] * 100, 2
                ),
                "low_battery_rate_percent": round(
                    bucket["low_battery"] / bucket["units"] * 100, 2
                ),
                "excursion_rate_percent": (
                    round(bucket["breached"] / bucket["readings"] * 100, 2)
                    if bucket["readings"]
                    else None
                ),
            }
        )

    published.sort(key=lambda r: -r["offline_rate_percent"])
    return {
        "period_days": days,
        "makes_published": len(published),
        "makes_withheld": withheld,
        "makes": published,
        "thresholds": {
            "minimum_operators": MIN_MODEL_TENANTS,
            "minimum_units": MIN_MODEL_UNITS,
        },
        "privacy": (
            "Reported per manufacturer, never per model or serial, and only "
            f"where at least {MIN_MODEL_TENANTS} operators and "
            f"{MIN_MODEL_UNITS} units sit behind the figure. One estate's "
            "maintenance regime is not a finding about a product."
        ),
        "generated_at": iso(utc_now()),
    }
