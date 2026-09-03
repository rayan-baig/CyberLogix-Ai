"""Universal IoT telemetry ingestion.

Receives sensor pulses, scores each against its vertical's threshold
profile, and opens a catastrophe incident with a Gemini-drafted SMS when a
bound is crossed. A sensor that keeps breaching updates its existing open
incident rather than opening a new one, so a failing freezer produces one
alert to act on instead of a pager storm.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from gemini import safe_generate
from licenses import require_tenant
from store import (
    INDUSTRY_PROFILES,
    STORE,
    Tenant,
    evaluate_breach,
    iso,
    resolve_vertical,
    utc_now,
)

logger = logging.getLogger("cyberlogix.telemetry")

router = APIRouter(prefix="/api", tags=["Universal IoT Telemetry"])


class SensorReading(BaseModel):
    """Incoming IoT wireless sensor telemetry packet."""

    sensor_id: str = Field(
        ..., min_length=1, description="Unique hardware sensor identifier, e.g. RACK-01"
    )
    temperature_fahrenheit: float = Field(
        ..., description="Current ambient temperature reading"
    )
    humidity_percent: Optional[float] = Field(
        50.0, ge=0.0, le=100.0, description="Optional relative humidity percentage"
    )


@router.get("/industries")
def list_industries():
    """Full vertical catalogue, for populating a sector selector."""
    return {
        "count": len(INDUSTRY_PROFILES),
        "industries": [
            {
                "vertical": key,
                "name": profile["name"],
                "catastrophe": profile["catastrophe"],
                "danger_above": profile["danger_above"],
                "danger_below": profile["danger_below"],
                "unit": profile["unit"],
            }
            for key, profile in INDUSTRY_PROFILES.items()
        ],
    }


def build_emergency_sms(
    sensor, temperature: float, humidity: Optional[float]
) -> tuple[str, str]:
    """Draft the emergency SMS for a breach, returning (text, source)."""
    profile = INDUSTRY_PROFILES[sensor.industry_vertical]
    humidity_text = f"{humidity}%" if humidity is not None else "not reported"

    fallback = (
        f"EMERGENCY ALERT: {profile['name']} sensor {sensor.sensor_id} at "
        f"{sensor.location_name} reported critical temperature {temperature}°F. "
        "Immediate physical inspection required."
    )

    prompt = f"""
    You are the CyberLogix AI 24/7 automated Emergency Operations Dispatcher.
    A critical physical facility infrastructure failure has just been detected.

    Sector Profile: {profile['name']}
    Suspected Root Cause Catastrophe: {profile['catastrophe']}
    Sensor Node ID: {sensor.sensor_id}
    Facility Location Tag: {sensor.location_name}
    Telemetry Reading: {temperature}°F
    Relative Humidity: {humidity_text}
    Event Timestamp: {iso(utc_now())}

    Draft an ultra-urgent, 2-sentence emergency SMS alert message to be blasted
    immediately to the on-call facility director's mobile phone. State the
    immediate threat of asset destruction and demand urgent physical
    intervention. Do not use markdown formatting tags. Return ONLY the raw
    emergency SMS text string.
    """

    return safe_generate(prompt, fallback, purpose="emergency SMS")


@router.post("/sensor-pulse")
async def process_sensor_pulse(
    reading: SensorReading, tenant: Tenant = Depends(require_tenant)
):
    """Ingest one telemetry packet from a registered sensor."""
    sensor = STORE.get_sensor(reading.sensor_id.strip())
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Sensor '{reading.sensor_id}' is not registered to this tenant. "
                "Claim a seat via POST /api/licenses/me/sensors first."
            ),
        )

    profile = INDUSTRY_PROFILES[sensor.industry_vertical]
    temp = reading.temperature_fahrenheit
    breach_reason = evaluate_breach(sensor.industry_vertical, temp)

    STORE.record_reading(
        sensor=sensor,
        temperature_fahrenheit=temp,
        humidity_percent=reading.humidity_percent,
        breached=breach_reason is not None,
    )

    if breach_reason is None:
        logger.info(
            "Nominal pulse: sensor=%s vertical=%s temp=%s°F",
            sensor.sensor_id,
            sensor.industry_vertical,
            temp,
        )
        return {
            "status": "nominal",
            "industry": profile["name"],
            "sensor_id": sensor.sensor_id,
            "current_temperature": temp,
            "message": "Telemetry parameters stable within safe operating bounds.",
        }

    logger.critical(
        "Catastrophe breach: sensor=%s vertical=%s temp=%s°F reason=%s",
        sensor.sensor_id,
        sensor.industry_vertical,
        temp,
        breach_reason,
    )

    # Collapse a sustained breach onto the incident already open for this
    # sensor. One failure produces one incident to acknowledge.
    existing = STORE.latest_open_incident(sensor.sensor_id)
    if existing is not None:
        existing.temperature_fahrenheit = temp
        existing.breach_details = breach_reason
        return {
            "status": "CRITICAL_CATASTROPHE_ONGOING",
            "incident_id": existing.incident_id,
            "industry": profile["name"],
            "catastrophe_type": existing.catastrophe,
            "sensor_id": sensor.sensor_id,
            "location": sensor.location_name,
            "current_temperature": temp,
            "breach_details": breach_reason,
            "dispatched_sms_text": existing.sms_text,
            "sms_dispatch_source": existing.sms_dispatch_source,
            "minutes_open": existing.minutes_open(),
            "message": "Breach ongoing; existing incident updated, no duplicate alert sent.",
        }

    sms_text, sms_source = build_emergency_sms(
        sensor, temp, reading.humidity_percent
    )
    incident = STORE.open_incident(
        tenant_id=tenant.tenant_id,
        sensor=sensor,
        temperature_fahrenheit=temp,
        breach_details=breach_reason,
        sms_text=sms_text,
        sms_dispatch_source=sms_source,
    )

    payload = incident.public()
    payload["status"] = "CRITICAL_CATASTROPHE_TRIGGERED"
    payload["location"] = sensor.location_name
    payload["current_temperature"] = temp
    return payload
