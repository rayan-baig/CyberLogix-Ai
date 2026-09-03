"""Predictive breakdown forecasting.

Fits a least-squares trend line to a sensor's recent temperature history
and projects when that trend crosses the vertical's danger threshold. The
value is lead time: a walk-in drifting up half a degree an hour is silent
and perfectly in-band right now, and will ruin its contents before dawn.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from gemini import safe_generate
from licenses import require_entitlement
from store import INDUSTRY_PROFILES, STORE, Reading, Tenant, utc_now

logger = logging.getLogger("cyberlogix.forecaster")

router = APIRouter(prefix="/api/forecast", tags=["Predictive Breakdown Forecaster"])

# A trend needs enough samples over enough time to mean anything.
MIN_READINGS_FOR_TREND = 3
MIN_TREND_SPAN_MINUTES = 5.0

# Drift slower than this is treated as flat rather than a trend.
NEGLIGIBLE_SLOPE_F_PER_HOUR = 0.05


def _risk_band(hours: Optional[float]) -> str:
    if hours is None:
        return "stable"
    if hours <= 1:
        return "critical"
    if hours <= 6:
        return "high"
    if hours <= 24:
        return "elevated"
    return "low"


def _linear_trend(readings: List[Reading]) -> Optional[Dict[str, float]]:
    """Least-squares slope in °F per hour, or None if not enough signal."""
    if len(readings) < MIN_READINGS_FOR_TREND:
        return None

    origin = readings[0].recorded_at
    xs = [(r.recorded_at - origin).total_seconds() / 3600.0 for r in readings]
    ys = [r.temperature_fahrenheit for r in readings]

    span_minutes = (xs[-1] - xs[0]) * 60.0
    if span_minutes < MIN_TREND_SPAN_MINUTES:
        return None

    n = float(len(xs))
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance == 0:
        return None

    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = covariance / variance

    # Coefficient of determination, so callers can weigh a noisy trend.
    ss_total = sum((y - mean_y) ** 2 for y in ys)
    intercept = mean_y - slope * mean_x
    ss_residual = sum(
        (y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys)
    )
    r_squared = 1.0 - (ss_residual / ss_total) if ss_total > 0 else 1.0

    return {
        "slope_f_per_hour": slope,
        "r_squared": max(0.0, min(1.0, r_squared)),
        "span_minutes": span_minutes,
    }


def forecast_sensor(sensor_id: str, window_hours: float) -> Dict[str, Any]:
    """Build the forecast payload for one sensor."""
    sensor = STORE.get_sensor(sensor_id)
    profile = INDUSTRY_PROFILES[sensor.industry_vertical]
    since = utc_now() - timedelta(hours=window_hours)
    readings = STORE.readings_for(sensor_id, since=since)

    base: Dict[str, Any] = {
        "sensor_id": sensor_id,
        "location_name": sensor.location_name,
        "industry_vertical": sensor.industry_vertical,
        "industry_name": profile["name"],
        "likely_catastrophe": profile["catastrophe"],
        "current_temperature": sensor.last_temperature,
        "danger_above": profile["danger_above"],
        "danger_below": profile["danger_below"],
        "window_hours": window_hours,
        "readings_analysed": len(readings),
    }

    trend = _linear_trend(readings)
    if trend is None:
        base.update(
            {
                "forecast": "insufficient_data",
                "risk_level": "unknown",
                "hours_until_breach": None,
                "trend_f_per_hour": None,
                "confidence": None,
                "summary": (
                    f"Need at least {MIN_READINGS_FOR_TREND} readings spanning "
                    f"{MIN_TREND_SPAN_MINUTES:.0f} minutes to model a trend; "
                    f"have {len(readings)} in the last {window_hours}h."
                ),
            }
        )
        return base

    slope = trend["slope_f_per_hour"]
    current = readings[-1].temperature_fahrenheit

    # Pick the bound this trend is actually heading toward.
    target: Optional[float] = None
    if slope > NEGLIGIBLE_SLOPE_F_PER_HOUR:
        target = profile["danger_above"]
    elif slope < -NEGLIGIBLE_SLOPE_F_PER_HOUR:
        target = profile["danger_below"]

    hours_until: Optional[float] = None
    if target is not None:
        remaining = target - current
        # Same sign means the trend is closing the gap.
        if (remaining > 0) == (slope > 0):
            hours_until = round(abs(remaining / slope), 2)

    already_breached = (
        profile["danger_above"] is not None and current > profile["danger_above"]
    ) or (
        profile["danger_below"] is not None and current < profile["danger_below"]
    )

    if already_breached:
        risk = "critical"
        hours_until = 0.0
    else:
        risk = _risk_band(hours_until)

    base.update(
        {
            "forecast": "breach_projected" if hours_until is not None else "stable",
            "risk_level": risk,
            "hours_until_breach": hours_until,
            "projected_breach_at": (
                (utc_now() + timedelta(hours=hours_until)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
                if hours_until
                else None
            ),
            "trend_f_per_hour": round(slope, 3),
            "confidence": round(trend["r_squared"], 3),
            "trend_span_minutes": round(trend["span_minutes"], 1),
            "already_breached": already_breached,
        }
    )
    return base


@router.get("/sensor/{sensor_id}")
def sensor_forecast(
    sensor_id: str,
    window_hours: float = Query(12.0, gt=0, le=48),
    narrate: bool = Query(
        False, description="Have Gemini write a plain-English maintenance brief."
    ),
    tenant: Tenant = Depends(require_entitlement("predictive_forecasting")),
):
    """Project when one sensor will cross its danger threshold."""
    sensor = STORE.get_sensor(sensor_id)
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sensor '{sensor_id}' is not registered to this tenant.",
        )

    result = forecast_sensor(sensor_id, window_hours)

    if narrate and result["risk_level"] in {"critical", "high", "elevated"}:
        fallback = (
            f"{result['industry_name']} sensor {sensor_id} at "
            f"{result['location_name']} is drifting "
            f"{result['trend_f_per_hour']}°F per hour and is projected to breach "
            f"in {result['hours_until_breach']} hours. Likely cause: "
            f"{result['likely_catastrophe']}. Schedule preventive maintenance."
        )
        prompt = f"""
        You are the CyberLogix AI predictive maintenance analyst.

        Sector: {result['industry_name']}
        Sensor: {sensor_id} at {result['location_name']}
        Current temperature: {result['current_temperature']}°F
        Measured drift: {result['trend_f_per_hour']}°F per hour
        Trend confidence (r-squared): {result['confidence']}
        Projected hours until threshold breach: {result['hours_until_breach']}
        Most likely underlying catastrophe: {result['likely_catastrophe']}

        Write a 2-to-3 sentence preventive maintenance brief for the facility
        manager. Say what the equipment is probably doing mechanically to
        produce this drift, and give one specific thing to physically check
        before the threshold is crossed. No markdown, no bullet points. Return
        only the brief.
        """
        narrative, source = safe_generate(
            prompt, fallback, purpose="maintenance brief"
        )
        result["maintenance_brief"] = narrative
        result["brief_source"] = source

    return result


@router.get("/fleet")
def fleet_forecast(
    window_hours: float = Query(12.0, gt=0, le=48),
    tenant: Tenant = Depends(require_entitlement("predictive_forecasting")),
):
    """Rank the whole fleet by how soon each sensor is projected to fail."""
    sensors = STORE.sensors_for(tenant.tenant_id)
    forecasts = [forecast_sensor(s.sensor_id, window_hours) for s in sensors]

    order = {
        "critical": 0,
        "high": 1,
        "elevated": 2,
        "low": 3,
        "stable": 4,
        "unknown": 5,
    }
    forecasts.sort(
        key=lambda f: (
            order.get(f["risk_level"], 9),
            f["hours_until_breach"] if f["hours_until_breach"] is not None else 1e9,
        )
    )

    tally: Dict[str, int] = {}
    for entry in forecasts:
        tally[entry["risk_level"]] = tally.get(entry["risk_level"], 0) + 1

    return {
        "tenant_id": tenant.tenant_id,
        "company_name": tenant.company_name,
        "window_hours": window_hours,
        "sensors_analysed": len(forecasts),
        "risk_tally": tally,
        "at_risk": [
            f
            for f in forecasts
            if f["risk_level"] in {"critical", "high", "elevated"}
        ],
        "forecasts": forecasts,
    }
