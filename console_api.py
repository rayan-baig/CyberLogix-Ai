"""Read model for the operations console.

The console needs the fleet, its recent history, forecasts, incidents and
headline counts on every refresh. Serving them as one bundle keeps the UI
to a single request per poll instead of one per sensor.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from forecaster import forecast_sensor
from licenses import require_tenant
from store import (
    INDUSTRY_PROFILES,
    STORE,
    VOICE_ESCALATION_GRACE_MINUTES,
    Tenant,
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

    sensors: List[Dict[str, Any]] = []
    for sensor in sorted(STORE.sensors_for(tenant.tenant_id), key=lambda s: s.sensor_id):
        history = STORE.readings_for(sensor.sensor_id)[-SPARKLINE_POINTS:]
        profile = INDUSTRY_PROFILES[sensor.industry_vertical]

        row = sensor.public()
        row["catastrophe"] = profile["catastrophe"]
        row["breaching"] = bool(history) and history[-1].breached
        row["spark"] = [r.temperature_fahrenheit for r in history]
        row["spark_breached"] = [r.breached for r in history]
        row["spark_at"] = [iso(r.recorded_at) for r in history]

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

    incidents = [i.public() for i in STORE.incidents_for(tenant.tenant_id)]
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

    at_risk = sum(
        1
        for s in sensors
        if s["risk_level"] in {"critical", "high", "elevated"}
    )

    return {
        "generated_at": iso(now),
        "tenant": tenant.public(sensor_count=len(sensors)),
        "entitlements": {
            "voice_escalation": entitlements["voice_escalation"],
            "predictive_forecasting": forecasting,
        },
        "summary": {
            "sensors_total": len(sensors),
            "sensors_online": sum(1 for s in sensors if s["online"]),
            "sensors_breaching": sum(1 for s in sensors if s["breaching"]),
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
