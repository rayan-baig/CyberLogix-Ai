"""Read model for the operations console.

The console needs the fleet, its recent history, forecasts, incidents and
headline counts on every refresh. Serving them as one bundle keeps the UI
to a single request per poll instead of one per sensor.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, status

from forecaster import forecast_sensor
from licenses import require_tenant
from store import (
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


@router.get("/overview")
def console_overview(
    compliance_days: int = Query(7, ge=1, le=90),
    tenant: Tenant = Depends(require_tenant),
) -> Dict[str, Any]:
    """Everything the console renders, in one payload."""
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
