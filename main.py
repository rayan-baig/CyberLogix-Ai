"""CyberLogix AI — Universal Thermal & Catastrophe Engine.

24/7 IoT sensor telemetry monitoring with automated Gemini AI emergency
SMS dispatch across eight commercial industry verticals.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [cyberlogix] %(message)s",
)
logger = logging.getLogger("cyberlogix")

app = FastAPI(
    title="CyberLogix AI Universal Thermal & Catastrophe Engine",
    description=(
        "24/7 IoT sensor telemetry monitoring and automated Gemini AI "
        "emergency SMS dispatch."
    ),
    version="1.0.0",
)

# Configure secure CORS middleware for frontend-to-backend communication.
# CYBERLOGIX_ALLOWED_ORIGINS accepts a comma-separated origin list in
# production; the permissive default keeps local development frictionless.
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CYBERLOGIX_ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=_ALLOWED_ORIGINS != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize the official Google GenAI Client.
# Automatically looks for the GEMINI_API_KEY environment variable.
try:
    client = genai.Client()
    logger.info("Google GenAI client initialized.")
except Exception as exc:  # noqa: BLE001 - startup must never hard-fail
    client = None
    logger.warning(
        "Google GenAI client unavailable (%s). Breach dispatch will fall back "
        "to the deterministic SMS template.",
        exc,
    )

GEMINI_MODEL = os.environ.get("CYBERLOGIX_GEMINI_MODEL", "gemini-2.5-flash")


def _utc_stamp() -> str:
    """Return the current UTC time as a stable, log-friendly string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class SensorReading(BaseModel):
    """Incoming IoT wireless sensor telemetry packet."""

    sensor_id: str = Field(
        ..., min_length=1, description="Unique hardware sensor identifier, e.g. RACK-01"
    )
    industry_vertical: str = Field(
        ..., min_length=1, description="Target sector vertical code"
    )
    location_name: str = Field(
        ..., min_length=1, description="Human-readable location tag"
    )
    temperature_fahrenheit: float = Field(
        ..., description="Current ambient temperature reading"
    )
    humidity_percent: Optional[float] = Field(
        50.0, ge=0.0, le=100.0, description="Optional relative humidity percentage"
    )


# Master definition of the 8 target industries, their common catastrophes,
# and threshold bounds.
INDUSTRY_PROFILES: Dict[str, Dict[str, Any]] = {
    "cybersecurity": {
        "name": "CyberTech Data Centers",
        "catastrophe": "HVAC Circuit Trip / Cooling Fan Stalled",
        "danger_above": 78.0,
        "danger_below": None,
        "unit": "°F",
    },
    "restaurant": {
        "name": "Franchise Restaurants",
        "catastrophe": "Unlatched Walk-In Freezer Door Gasket Failure",
        "danger_above": 32.0,
        "danger_below": None,
        "unit": "°F",
    },
    "logistics": {
        "name": "High-Stakes Cold-Chain Transport",
        "catastrophe": "Reefer Truck Auxiliary Diesel Engine Stall",
        "danger_above": 40.0,
        "danger_below": None,
        "unit": "°F",
    },
    "solar_infrastructure": {
        "name": "Solar Infrastructure & Storage",
        "catastrophe": "Inverter Thermal Runaway Overload",
        "danger_above": 115.0,
        "danger_below": None,
        "unit": "°F",
    },
    "medical_lab": {
        "name": "Medical Labs & Blood Banks",
        "catastrophe": "Specimen Refrigerator Door Seal Degradation",
        "danger_above": 46.0,
        "danger_below": 36.0,
        "unit": "°F",
    },
    "private_aviation": {
        "name": "Private Aviation Hangars",
        "catastrophe": "Hangar Bay Humidity Moisture Infiltration",
        "danger_above": 85.0,  # Heat/humidity proxy
        "danger_below": None,
        "unit": "°F",
    },
    "superyacht": {
        "name": "Luxury Superyacht Engine Bays",
        "catastrophe": "Engine Room Ventilation Airflow Blockage",
        "danger_above": 90.0,
        "danger_below": None,
        "unit": "°F",
    },
    "country_club": {
        "name": "High-End Country Clubs",
        "catastrophe": "Clubhouse Kitchen Walk-In Compressor Failure",
        "danger_above": 32.0,
        "danger_below": None,
        "unit": "°F",
    },
}


@app.get("/api/health", status_code=status.HTTP_200_OK)
def health_check():
    return {
        "status": "online",
        "engine": "CyberLogix Universal Common Catastrophe IoT Engine",
        "active_profiles": len(INDUSTRY_PROFILES),
        "gemini_dispatch": "ready" if client is not None else "fallback_template",
        "timestamp": _utc_stamp(),
    }


@app.get("/api/industries", status_code=status.HTTP_200_OK)
def list_industries():
    """Expose the full vertical catalogue so clients can build selectors."""
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


@app.post("/api/sensor-pulse", status_code=status.HTTP_200_OK)
async def process_sensor_pulse(reading: SensorReading):
    vertical = reading.industry_vertical.strip().lower()

    if vertical not in INDUSTRY_PROFILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid industry_vertical provided. Allowed keys: "
                f"{list(INDUSTRY_PROFILES.keys())}"
            ),
        )

    profile = INDUSTRY_PROFILES[vertical]
    temp = reading.temperature_fahrenheit

    # Evaluate breach conditions.
    is_breached = False
    breach_reason = ""

    if profile["danger_above"] is not None and temp > profile["danger_above"]:
        is_breached = True
        breach_reason = (
            f"Thermal high threshold breached: {temp}°F > "
            f"{profile['danger_above']}°F limit."
        )
    elif profile["danger_below"] is not None and temp < profile["danger_below"]:
        is_breached = True
        breach_reason = (
            f"Thermal low threshold breached: {temp}°F < "
            f"{profile['danger_below']}°F limit."
        )

    if not is_breached:
        logger.info(
            "Nominal pulse: sensor=%s vertical=%s temp=%s°F",
            reading.sensor_id,
            vertical,
            temp,
        )
        return {
            "status": "nominal",
            "industry": profile["name"],
            "sensor_id": reading.sensor_id,
            "current_temperature": temp,
            "message": "Telemetry parameters stable within safe operating bounds.",
        }

    # CRITICAL CATASTROPHE BREACH TRIGGERED:
    # Dispatch the Gemini AI Emergency Operator.
    logger.critical(
        "Catastrophe breach: sensor=%s vertical=%s temp=%s°F reason=%s",
        reading.sensor_id,
        vertical,
        temp,
        breach_reason,
    )

    event_time = _utc_stamp()
    humidity = (
        f"{reading.humidity_percent}%"
        if reading.humidity_percent is not None
        else "not reported"
    )

    fallback_sms = (
        f"EMERGENCY ALERT: {profile['name']} sensor {reading.sensor_id} at "
        f"{reading.location_name} reported critical temperature {temp}°F. "
        "Immediate physical inspection required."
    )

    emergency_prompt = f"""
    You are the CyberLogix AI 24/7 automated Emergency Operations Dispatcher.
    A critical physical facility infrastructure failure has just been detected.

    Sector Profile: {profile['name']}
    Suspected Root Cause Catastrophe: {profile['catastrophe']}
    Sensor Node ID: {reading.sensor_id}
    Facility Location Tag: {reading.location_name}
    Telemetry Reading: {temp}°F
    Relative Humidity: {humidity}
    Event Timestamp: {event_time}

    Draft an ultra-urgent, 2-sentence emergency SMS alert message to be blasted
    immediately to the on-call facility director's mobile phone. State the
    immediate threat of asset destruction and demand urgent physical
    intervention. Do not use markdown formatting tags. Return ONLY the raw
    emergency SMS text string.
    """

    dispatch_source = "gemini"

    if client is None:
        logger.error(
            "Google GenAI client uninitialized; using deterministic SMS template. "
            "Verify the GEMINI_API_KEY environment variable."
        )
        dispatched_sms = fallback_sms
        dispatch_source = "fallback_template"
    else:
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=emergency_prompt,
            )
            dispatched_sms = (response.text or "").strip()
            if not dispatched_sms:
                raise ValueError("Gemini returned an empty dispatch body.")
        except Exception as exc:  # noqa: BLE001 - an alert must always go out
            logger.exception("Gemini dispatch failed (%s); falling back.", exc)
            dispatched_sms = fallback_sms
            dispatch_source = "fallback_template"

    return {
        "status": "CRITICAL_CATASTROPHE_TRIGGERED",
        "industry": profile["name"],
        "catastrophe_type": profile["catastrophe"],
        "sensor_id": reading.sensor_id,
        "location": reading.location_name,
        "current_temperature": temp,
        "breach_details": breach_reason,
        "dispatched_sms_text": dispatched_sms,
        "dispatch_source": dispatch_source,
        "timestamp": event_time,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )
