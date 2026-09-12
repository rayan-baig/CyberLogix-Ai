"""Read model for the operations console.

The console needs the fleet, its recent history, forecasts, incidents and
headline counts on every refresh. Serving them as one bundle keeps the UI
to a single request per poll instead of one per sensor.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from forecaster import forecast_sensor
from auth import require_tenant_any_state
from licenses import require_tenant
from store import (
    PLAN_TIERS,
    INDUSTRY_PROFILES,
    STORE,
    VOICE_ESCALATION_GRACE_MINUTES,
    Tenant,
    display_temperature,
    iso,
    utc_now,
)

router = APIRouter(prefix="/api/console", tags=["Operations Console"])

# Points in a sensor card's sparkline.
SPARKLINE_POINTS = 12



# How close to the end of a term counts as "ending soon". A fortnight is
# long enough to get a purchase order signed and short enough that the
# warning still means something.
#
# Capped at half the term, because a flat fortnight put a 14-day trial in
# its "ending soon" state on the first morning. A banner that is on from
# the moment you arrive is not a warning, it is decoration, and it stops
# being read well before the day it matters.
RENEWAL_WARNING_DAYS = 14


def warning_window(tenant) -> int:
    term = PLAN_TIERS.get(tenant.plan, {}).get("term_days", 365)
    return max(1, min(RENEWAL_WARNING_DAYS, term // 2))


def account_state(tenant, now=None) -> Dict[str, Any]:
    """Whether this estate is about to stop being watched, and what to do.

    The console had no idea about any of this. A trial ran out overnight
    and the only sign was that everything started returning 402 the next
    morning — no warning the day before, no figure for what it would cost
    to carry on, and nothing on the page that would take the money.
    """
    from auth import LICENCE_GRACE_DAYS, grace_days_left, licence_state
    from contracts import delinquency
    from pricing import build_subscription

    now = now or utc_now()
    state = licence_state(tenant, now)
    # Rounded up, not truncated. Six days minus a few seconds is not five
    # days, and this figure goes straight onto a banner.
    hours_left = (
        (tenant.expires_at - now).total_seconds() / 3600
        if tenant.expires_at
        else None
    )
    days_left = None if hours_left is None else math.ceil(hours_left / 24)
    money = delinquency(tenant.tenant_id, now)
    subscription = build_subscription(tenant)
    contract = STORE.active_subscription(tenant.tenant_id)

    if state == "suspended":
        headline = "This licence is suspended. Get in touch to restore it."
        severity = "critical"
    elif state == "lapsed":
        headline = (
            "This estate is no longer being monitored. Move onto a paid "
            "plan to restore it."
        )
        severity = "critical"
    elif state == "grace":
        left = grace_days_left(tenant, now)
        headline = (
            f"The licence expired. Monitoring continues for {left} more "
            f"day{'' if left == 1 else 's'}, then stops. Reporting is "
            "withheld until it is renewed."
        )
        severity = "critical"
    elif days_left is not None and days_left <= warning_window(tenant):
        subject = "The trial" if tenant.plan == "trial" else "This licence"
        # Measured in hours, because a day count cannot tell "this evening"
        # from "tomorrow evening" — and those read very differently to
        # somebody deciding whether to deal with it now.
        when = (
            "ends today" if hours_left < 24
            else "ends tomorrow" if hours_left < 48
            else f"ends in {days_left} days"
        )
        headline = (
            f"{subject} {when}. After that there are {LICENCE_GRACE_DAYS} "
            "days of grace, then monitoring stops."
        )
        severity = "warning"
    elif money["delinquent"]:
        headline = (
            f"${money['overdue_usd']:,.2f} is {money['days_overdue']} days "
            "past due. Reporting is withheld until it clears."
        )
        severity = "warning"
    else:
        headline = None
        severity = "ok"

    return {
        "licence_state": state,
        "severity": severity,
        "headline": headline,
        "plan": tenant.plan,
        "on_trial": tenant.plan == "trial",
        "days_to_expiry": days_left,
        "warning_window_days": warning_window(tenant),
        "grace_days_left": grace_days_left(tenant, now),
        "expires_at": iso(tenant.expires_at),
        "has_contract": contract is not None,
        # What carrying on would cost, priced from the estate they have
        # actually built rather than from a tier they have to guess at.
        "monthly_if_paid_usd": subscription["monthly_total_usd"],
        "annual_if_paid_usd": subscription["annual_total_usd"],
        "collections": money,
        # Always true, and worth saying on the page rather than only in the
        # agreement: none of this touches the alarm.
        "alerting_continues": state != "lapsed",
    }


@router.get("/overview")
def console_overview(
    compliance_days: int = Query(7, ge=1, le=90),
    tenant: Tenant = Depends(require_tenant_any_state),
) -> Dict[str, Any]:
    """Everything the console renders, in one payload.

    Readable even when the licence has lapsed. This is the page that says
    what happened and carries the button that fixes it — refusing it left
    a customer with a blank 402 and no way to work out what to do next.
    """
    now = utc_now()
    entitlements = tenant.entitlements()
    forecasting = entitlements["predictive_forecasting"]
    unit = tenant.temperature_unit
    sites = {s.site_id: s for s in STORE.sites_for(tenant.tenant_id)}

    sensors: List[Dict[str, Any]] = []
    for sensor in sorted(STORE.sensors_for(tenant.tenant_id), key=lambda s: s.sensor_id):
        history = STORE.readings_for(sensor.sensor_id)[-SPARKLINE_POINTS:]
        profile = INDUSTRY_PROFILES[sensor.industry_vertical]

        row = sensor.public(unit)
        row["catastrophe"] = profile["catastrophe"]
        row["breaching"] = bool(history) and history[-1].breached
        # The sparkline is drawn in the tenant's unit; mixing units on one
        # chart is how somebody reads 4° as safe when it is 4°F.
        row["spark"] = [
            display_temperature(r.temperature_fahrenheit, unit) for r in history
        ]
        row["spark_breached"] = [r.breached for r in history]
        row["spark_at"] = [iso(r.recorded_at) for r in history]
        site = sites.get(sensor.site_id) if sensor.site_id else None
        row["site_name"] = site.name if site else None

        if forecasting:
            projection = forecast_sensor(sensor.sensor_id, window_hours=12.0)
            row["risk_level"] = projection["risk_level"]
            row["hours_until_breach"] = projection["hours_until_breach"]
            row["trend_f_per_hour"] = projection.get("trend_f_per_hour")
        else:
            row["risk_level"] = None
            row["hours_until_breach"] = None
            row["trend_f_per_hour"] = None

        sensors.append(row)

    incidents = []
    for incident in STORE.incidents_for(tenant.tenant_id):
        row = incident.public()
        row["temperature_display"] = display_temperature(
            incident.temperature_fahrenheit, unit
        )
        row["temperature_unit"] = unit
        incidents.append(row)
    open_incidents = STORE.open_incidents(tenant.tenant_id)
    escalation_due = sum(
        1
        for i in open_incidents
        if i.voice_escalated_at is None
        and i.minutes_open(now) >= VOICE_ESCALATION_GRACE_MINUTES
    )

    since = now - timedelta(days=compliance_days)
    logged = 0
    breached = 0
    for sensor in STORE.sensors_for(tenant.tenant_id):
        readings = STORE.readings_for(sensor.sensor_id, since=since)
        logged += len(readings)
        breached += sum(1 for r in readings if r.breached)

    # The estate's own vocabulary. One vertical means we can speak it
    # throughout; a mixed estate has to fall back to something neutral.
    verticals = {s["industry_vertical"] for s in sensors}
    if len(verticals) == 1:
        only = INDUSTRY_PROFILES[next(iter(verticals))]
        fleet_noun, fleet_plural = only["asset_noun"], only["asset_plural"]
    else:
        fleet_noun, fleet_plural = "asset", "assets"

    at_risk = sum(
        1
        for s in sensors
        if s["risk_level"] in {"critical", "high", "elevated"}
    )

    from pricing import build_roi, build_subscription

    subscription = build_subscription(tenant)
    roi = build_roi(tenant, 30)

    return {
        "generated_at": iso(now),
        "tenant": tenant.public(sensor_count=len(sensors)),
        "account": account_state(tenant, now),
        "subscription": subscription,
        "roi": roi,
        "entitlements": {
            "voice_escalation": entitlements["voice_escalation"],
            "predictive_forecasting": forecasting,
        },
        "temperature_unit": unit,
        "fleet_noun": fleet_noun,
        "fleet_plural": fleet_plural,
        "sites": [
            site.public(
                sensor_count=sum(
                    1 for s in sensors if s["site_id"] == site.site_id
                ),
                online=sum(
                    1
                    for s in sensors
                    if s["site_id"] == site.site_id and s["online"]
                ),
            )
            for site in sites.values()
        ],
        "summary": {
            "sensors_total": len(sensors),
            "sensors_online": sum(1 for s in sensors if s["online"]),
            "sensors_breaching": sum(1 for s in sensors if s["breaching"]),
            "low_battery": sum(1 for s in sensors if s["battery_low"]),
            "unplaced_sensors": sum(1 for s in sensors if not s["site_id"]),
            "open_incidents": len(open_incidents),
            "escalation_due": escalation_due,
            "at_risk": at_risk,
            "readings_logged": logged,
            "readings_breached": breached,
            "compliance_percent": (
                round((logged - breached) / logged * 100, 1) if logged else None
            ),
            "compliance_days": compliance_days,
        },
        "sensors": sensors,
        "incidents": incidents,
        "grace_window_minutes": VOICE_ESCALATION_GRACE_MINUTES,
    }


# Readings shown on a sensor's own page. Twelve points is enough for a card
# and useless for working out what actually happened overnight.
DETAIL_POINTS = 120


@router.get("/sensor/{sensor_id}")
def sensor_detail(
    sensor_id: str,
    tenant: Tenant = Depends(require_tenant),
) -> Dict[str, Any]:
    """One sensor in full: history, incidents, forecast and health.

    The fleet card answers "is this thing alright". This answers "what
    happened", which is the question anyone asks the moment it is not.
    """
    sensor = STORE.get_sensor((sensor_id or "").strip())
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sensor '{sensor_id}' is not registered to this tenant.",
        )

    unit = tenant.temperature_unit
    history = STORE.readings_for(sensor.sensor_id)[-DETAIL_POINTS:]
    site = STORE.get_site(sensor.site_id) if sensor.site_id else None

    incidents = [
        i.public()
        for i in STORE.incidents_for(tenant.tenant_id)
        if i.sensor_id == sensor.sensor_id
    ]

    row = sensor.public(unit)
    row["catastrophe"] = INDUSTRY_PROFILES[sensor.industry_vertical]["catastrophe"]
    row["site_name"] = site.name if site else None

    forecast = None
    if tenant.entitlements()["predictive_forecasting"]:
        forecast = forecast_sensor(sensor.sensor_id, window_hours=12.0)

    return {
        "sensor": row,
        "temperature_unit": unit,
        "readings": [
            {
                "at": iso(r.recorded_at),
                "temperature": display_temperature(r.temperature_fahrenheit, unit),
                "humidity": r.humidity_percent,
                "breached": r.breached,
            }
            for r in history
        ],
        "readings_total": len(STORE.readings_for(sensor.sensor_id)),
        "breached_count": sum(1 for r in history if r.breached),
        "incidents": incidents,
        "open_incidents": sum(1 for i in incidents if i["state"] == "open"),
        "forecast": forecast,
    }
