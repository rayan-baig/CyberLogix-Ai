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
from pydantic import BaseModel, Field, model_validator

from gemini import safe_generate
from licenses import require_tenant
from notifications import send_sms
from store import (
    INDUSTRY_PROFILES,
    STORE,
    Tenant,
    evaluate_sensor_breach,
    display_temperature,
    format_temperature,
    to_fahrenheit,
    iso,
    utc_now,
)

logger = logging.getLogger("cyberlogix.telemetry")

router = APIRouter(prefix="/api", tags=["Universal IoT Telemetry"])


class SensorReading(BaseModel):
    """Incoming IoT wireless sensor telemetry packet.

    Send exactly one of `temperature_fahrenheit` or `temperature_celsius`.
    Readings are stored in Fahrenheit and rendered in the tenant's unit, so
    a European fleet reports in Celsius without a second storage format.
    """

    sensor_id: str = Field(
        ..., min_length=1, description="Unique hardware sensor identifier, e.g. RACK-01"
    )
    temperature_fahrenheit: Optional[float] = Field(
        None, description="Current ambient temperature reading, in Fahrenheit"
    )
    temperature_celsius: Optional[float] = Field(
        None, description="Current ambient temperature reading, in Celsius"
    )
    humidity_percent: Optional[float] = Field(
        50.0, ge=0.0, le=100.0, description="Optional relative humidity percentage"
    )
    battery_percent: Optional[float] = Field(
        None, ge=0.0, le=100.0, description="Sensor battery level"
    )
    signal_percent: Optional[float] = Field(
        None, ge=0.0, le=100.0, description="Sensor signal strength"
    )

    @model_validator(mode="after")
    def exactly_one_unit(self) -> "SensorReading":
        """Reject a packet carrying no temperature, or two of them.

        Validated here rather than at use, so a malformed packet fails as a
        422 before the sensor is ever looked up.
        """
        if (self.temperature_fahrenheit is None) == (
            self.temperature_celsius is None
        ):
            raise ValueError(
                "Send exactly one of temperature_fahrenheit or temperature_celsius."
            )
        return self

    def resolved_fahrenheit(self) -> float:
        """The reading in Fahrenheit, whichever unit it arrived in."""
        if self.temperature_celsius is not None:
            return to_fahrenheit(self.temperature_celsius)
        return self.temperature_fahrenheit


@router.get("/industries")
def list_industries():
    """Full vertical catalogue with pricing, for a sector selector."""
    from pricing import PRICE_BOOK

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
                "asset_noun": profile["asset_noun"],
                "asset_plural": profile["asset_plural"],
                "billing_unit": PRICE_BOOK[key]["unit"],
                "monthly_usd": PRICE_BOOK[key]["monthly_usd"],
                "price_label": (
                    f"${PRICE_BOOK[key]['monthly_usd']:,.0f} / "
                    f"{PRICE_BOOK[key]['unit']} / month"
                ),
                "pitch": PRICE_BOOK[key]["pitch"],
            }
            for key, profile in INDUSTRY_PROFILES.items()
        ],
    }


def build_emergency_sms(
    sensor,
    temperature: float,
    humidity: Optional[float],
    tenant_id: str,
    unit: str = "F",
) -> tuple[str, str]:
    """Draft the emergency SMS for a breach, returning (text, source).

    The reading is written in the tenant's unit: a manager in Lyon woken at
    3am should not have to convert Fahrenheit before deciding whether to
    drive in.
    """
    profile = INDUSTRY_PROFILES[sensor.industry_vertical]
    humidity_text = f"{humidity}%" if humidity is not None else "not reported"
    reading = format_temperature(temperature, unit)

    fallback = (
        f"EMERGENCY ALERT: {profile['name']} sensor {sensor.sensor_id} at "
        f"{sensor.location_name} reported critical temperature {reading}. "
        "Immediate physical inspection required."
    )

    prompt = f"""
    You are the CyberLogix AI 24/7 automated Emergency Operations Dispatcher.
    A critical physical facility infrastructure failure has just been detected.

    Sector Profile: {profile['name']}
    Suspected Root Cause Catastrophe: {profile['catastrophe']}
    Sensor Node ID: {sensor.sensor_id}
    Facility Location Tag: {sensor.location_name}
    Telemetry Reading: {reading}
    Relative Humidity: {humidity_text}
    Event Timestamp: {iso(utc_now())}

    Draft an ultra-urgent, 2-sentence emergency SMS alert message to be blasted
    immediately to the on-call facility director's mobile phone. State the
    immediate threat of asset destruction and demand urgent physical
    intervention. Do not use markdown formatting tags. Return ONLY the raw
    emergency SMS text string.
    """

    return safe_generate(
        prompt, fallback, purpose="emergency SMS", tenant_id=tenant_id
    )


def process_reading(
    tenant: Tenant,
    sensor,
    temperature: float,
    humidity: Optional[float],
) -> dict:
    """Score one reading and drive the incident lifecycle.

    Shared by native sensor pulses and third-party BYOD webhooks, so both
    ingestion routes get identical breach detection, incident collapsing,
    escalation, forecasting history and compliance logging.
    """
    profile = INDUSTRY_PROFILES[sensor.industry_vertical]
    unit = tenant.temperature_unit
    breach_reason = evaluate_sensor_breach(sensor, temperature, unit)

    STORE.record_reading(
        sensor=sensor,
        temperature_fahrenheit=temperature,
        humidity_percent=humidity,
        breached=breach_reason is not None,
    )

    if breach_reason is None:
        logger.info(
            "Nominal pulse: sensor=%s vertical=%s temp=%s°F",
            sensor.sensor_id,
            sensor.industry_vertical,
            temperature,
        )
        return {
            "status": "nominal",
            "industry": profile["name"],
            "sensor_id": sensor.sensor_id,
            "current_temperature": display_temperature(temperature, unit),
            "temperature_unit": unit,
            "message": "Telemetry parameters stable within safe operating bounds.",
        }

    logger.critical(
        "Catastrophe breach: sensor=%s vertical=%s temp=%s°F reason=%s",
        sensor.sensor_id,
        sensor.industry_vertical,
        temperature,
        breach_reason,
    )

    # Collapse a sustained breach onto the incident already open for this
    # sensor. One failure produces one incident to acknowledge.
    existing = STORE.latest_open_incident(sensor.sensor_id)
    if existing is not None:
        STORE.update_incident_breach(existing, temperature, breach_reason)
        return {
            "status": "CRITICAL_CATASTROPHE_ONGOING",
            "incident_id": existing.incident_id,
            "industry": profile["name"],
            "catastrophe_type": existing.catastrophe,
            "sensor_id": sensor.sensor_id,
            "location": sensor.location_name,
            "current_temperature": display_temperature(temperature, unit),
            "temperature_unit": unit,
            "breach_details": breach_reason,
            "dispatched_sms_text": existing.sms_text,
            "sms_dispatch_source": existing.sms_dispatch_source,
            "minutes_open": existing.minutes_open(),
            "message": (
                "Breach ongoing; existing incident updated, no duplicate alert sent."
            ),
        }

    sms_text, sms_source = build_emergency_sms(
        sensor, temperature, humidity, tenant.tenant_id, unit
    )
    incident = STORE.open_incident(
        tenant_id=tenant.tenant_id,
        sensor=sensor,
        temperature_fahrenheit=temperature,
        breach_details=breach_reason,
        sms_text=sms_text,
        sms_dispatch_source=sms_source,
    )
    # Everyone on the roster gets the text, not just one number on file.
    recipients = STORE.sms_recipients(tenant, sensor.site_id)
    fanout = [
        dict(
            send_sms(contact.phone, sms_text, tenant.tenant_id),
            contact_id=contact.contact_id,
            contact_name=contact.full_name,
        )
        for contact in recipients
    ]
    STORE.record_sms_delivery(incident, fanout[0] if fanout else None, fanout)

    # Push a copy into whatever the team already has open. Imported here
    # rather than at module scope: webhooks imports the store and the auth
    # helpers, and a top-level import would close the cycle.
    from webhooks import dispatch_event

    webhook_results = dispatch_event(tenant, incident, sensor, "opened")

    payload = incident.public()
    payload["status"] = "CRITICAL_CATASTROPHE_TRIGGERED"
    payload["location"] = sensor.location_name
    payload["current_temperature"] = display_temperature(temperature, unit)
    payload["temperature_unit"] = unit
    payload["webhook_fanout"] = webhook_results
    return payload


def resolve_owned_sensor(tenant: Tenant, sensor_id: str):
    """Fetch a sensor, refusing one that belongs to another tenant."""
    sensor = STORE.get_sensor((sensor_id or "").strip())
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Sensor '{sensor_id}' is not registered to this tenant. "
                "Claim a seat via POST /api/licenses/me/sensors first."
            ),
        )
    return sensor


@router.post("/sensor-pulse")
async def process_sensor_pulse(
    reading: SensorReading, tenant: Tenant = Depends(require_tenant)
):
    """Ingest one telemetry packet from a registered sensor."""
    sensor = resolve_owned_sensor(tenant, reading.sensor_id)
    if reading.battery_percent is not None or reading.signal_percent is not None:
        STORE.record_sensor_health(
            sensor, reading.battery_percent, reading.signal_percent
        )
    return process_reading(
        tenant=tenant,
        sensor=sensor,
        temperature=reading.resolved_fahrenheit(),
        humidity=reading.humidity_percent,
    )
