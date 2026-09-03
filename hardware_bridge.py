# ==============================================================================
# CyberLogix AI - BYOD Hardware Webhook & Meeting Intelligence Bridge
# File: hardware_bridge.py
# ==============================================================================
"""Bring-your-own-device ingestion and sector meeting intelligence.

Part 1 lets any off-the-shelf commercial sensor (Elitech, Dickson, Monnit,
SensorPush and friends) POST its raw JSON straight to CyberLogix, so a
customer needs no proprietary hardware. Webhook readings run through the
same engine as native pulses, so BYOD estates get incidents, escalation,
forecasting and compliance logging identically.

Part 2 turns staff meeting transcripts and voice memos into structured,
sector-specific operational intelligence.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from gemini import safe_generate
from licenses import require_tenant
from store import INDUSTRY_PROFILES, STORE, Tenant, iso, utc_now
from telemetry import process_reading

logger = logging.getLogger("cyberlogix.bridge")

router = APIRouter(
    prefix="/api/v1/bridge", tags=["BYOD Hardware & Meeting Intelligence"]
)

# Metrics a third-party device may report.
SUPPORTED_METRICS = ("temperature_f", "temperature_c", "humidity_pct")


# ==============================================================================
# PART 1: The BYOD Hardware Webhook Receiver (No Physical Product Needed)
# Allows any 3rd-party commercial sensor/thermostat (Elitech, Dickson, Monnit,
# etc.) to push raw JSON telemetry data straight to the CyberLogix AI backend.
# ==============================================================================


class GenericWebhookPayload(BaseModel):
    device_sn: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="External device serial number or MAC address",
    )
    api_key_token: str = Field(
        ...,
        min_length=1,
        description="Tenant API key. Carried in the body because most "
        "off-the-shelf sensors cannot set custom request headers.",
    )
    reading_value: float = Field(..., description="Raw metric value reported by hardware")
    metric_type: str = Field(
        "temperature_f",
        description="Unit of measurement, e.g. temperature_f, temperature_c, humidity_pct",
    )
    location_label: Optional[str] = Field(
        "Remote Facility Node", description="Site tag"
    )


def _authenticate_webhook(token: str) -> Tenant:
    """Resolve the tenant from the in-body token.

    Mirrors the header dependency's contract: 401 for an unknown token, 402
    for a lapsed license, so a billing problem never masquerades as a bad
    credential.
    """
    tenant = STORE.tenant_by_key(token)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unrecognised api_key_token.",
        )
    if tenant.suspended:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"License for {tenant.company_name} is suspended.",
        )
    if tenant.expired:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"License for {tenant.company_name} expired on "
                f"{tenant.expires_at.date()}."
            ),
        )
    return tenant


@router.post("/sensor-webhook-ingest", status_code=status.HTTP_200_OK)
def ingest_third_party_hardware_webhook(
    payload: GenericWebhookPayload,
    x_signature: Optional[str] = Header(
        None, description="Optional third-party webhook signature"
    ),
):
    """Universal webhook endpoint for off-the-shelf sensors.

    Clients point their existing Wi-Fi or cellular hardware here and this
    app is the high-IQ AI brain. The reading is scored against the industry
    profile of the sensor the device is bound to, not a flat number, so a
    freezer and a hangar are judged by their own rules.
    """
    tenant = _authenticate_webhook(payload.api_key_token)

    metric = payload.metric_type.strip().lower()
    if metric not in SUPPORTED_METRICS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported metric_type. Supported: {list(SUPPORTED_METRICS)}",
        )

    sensor = STORE.sensor_by_device(payload.device_sn)
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Device '{payload.device_sn}' is not bound to a licensed "
                "sensor. Register it via POST /api/licenses/me/sensors with "
                "external_device_sn set to this serial."
            ),
        )

    # Keep the operator's site tag current if the device reports a better one.
    label = (payload.location_label or "").strip()
    if label and label != "Remote Facility Node":
        sensor.location_name = label

    if metric == "humidity_pct":
        # Humidity alone cannot breach a thermal threshold, but it is stored
        # as context and re-scored against the last known temperature.
        if not 0.0 <= payload.reading_value <= 100.0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="humidity_pct must be between 0 and 100.",
            )
        sensor.last_humidity = payload.reading_value
        sensor.last_seen = utc_now()
        return {
            "status": "INGESTION_SUCCESS",
            "byod_architecture": "Active - Zero proprietary hardware required",
            "device_serial": payload.device_sn,
            "bound_sensor_id": sensor.sensor_id,
            "processed_metric": payload.reading_value,
            "metric_unit": metric,
            "alert_triggered": False,
            "action_taken_by_ai": (
                "Humidity recorded as context. Thermal thresholds are scored "
                "on temperature readings."
            ),
            "signature_present": x_signature is not None,
            "ingested_at": iso(utc_now()),
        }

    temperature = payload.reading_value
    if metric == "temperature_c":
        temperature = round(temperature * 9.0 / 5.0 + 32.0, 2)

    result = process_reading(
        tenant=tenant,
        sensor=sensor,
        temperature=temperature,
        humidity=sensor.last_humidity,
    )

    alert_triggered = result["status"] != "nominal"
    if alert_triggered:
        action = (
            f"{result['breach_details']} Incident {result.get('incident_id')} "
            f"{'updated' if result['status'].endswith('ONGOING') else 'opened'} "
            "and the on-call director alerted."
        )
    else:
        action = "Nominal telemetry received."

    logger.info(
        "BYOD ingest: device=%s sensor=%s metric=%s value=%s alert=%s",
        payload.device_sn,
        sensor.sensor_id,
        metric,
        payload.reading_value,
        alert_triggered,
    )

    return {
        "status": "INGESTION_SUCCESS",
        "byod_architecture": "Active - Zero proprietary hardware required",
        "device_serial": payload.device_sn,
        "bound_sensor_id": sensor.sensor_id,
        "processed_metric": payload.reading_value,
        "metric_unit": metric,
        "normalised_temperature_f": temperature,
        "alert_triggered": alert_triggered,
        "action_taken_by_ai": action,
        "signature_present": x_signature is not None,
        "telemetry_result": result,
        "ingested_at": iso(utc_now()),
    }


# ==============================================================================
# PART 2: The Industry-Specific AI Meeting Note & Voice Transcription Summarizer
# ==============================================================================


class VoiceMeetingInput(BaseModel):
    industry_vertical: str = Field(..., description="Target sector vertical key")
    raw_transcript: str = Field(
        ...,
        min_length=1,
        max_length=100_000,
        description="Raw text from a Zoom call, phone memo, or staff meeting",
    )


SECTOR_PROMPTS: Dict[str, str] = {
    "cybersecurity": "Focus on server uptime risks, firewall anomalies, and CISO board action items.",
    "restaurant": "Focus on food spoilage liability, health inspection prep, and kitchen shift tasks.",
    "logistics": "Focus on cold-chain transit handovers, reefer fuel status, and delivery dock deadlines.",
    "solar_infrastructure": "Focus on inverter thermal efficiency, battery wear, and fire safety compliance.",
    "medical_lab": "Focus on OSHA chain-of-custody, vaccine vault temperature logs, and audit readiness.",
    "private_aviation": "Focus on hangar humidity control, avionics storage safety, and FAA environmental logs.",
    "superyacht": "Focus on engine room thermal safety, galley cold-store monitoring, and captain briefings.",
    "country_club": "Focus on clubhouse kitchen inventory protection and holiday dining event safeguards.",
}

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

REQUIRED_REPORT_KEYS = (
    "executive_summary",
    "extracted_operational_decisions",
    "action_items_assigned",
    "industry_compliance_impact",
)


def _parse_intelligence_json(raw: str) -> Optional[Dict[str, Any]]:
    """Parse the model's JSON, tolerating a markdown fence around it."""
    candidate = _FENCE.sub("", raw or "").strip()
    if not candidate:
        return None

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        # Salvage the outermost object if the model added prose around it.
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None

    if not isinstance(parsed, dict):
        return None
    if not all(key in parsed for key in REQUIRED_REPORT_KEYS):
        return None

    # Normalise the collection shapes so clients can iterate without guarding.
    decisions = parsed.get("extracted_operational_decisions")
    if not isinstance(decisions, list):
        parsed["extracted_operational_decisions"] = (
            [str(decisions)] if decisions else []
        )

    actions = parsed.get("action_items_assigned")
    if not isinstance(actions, list):
        parsed["action_items_assigned"] = []
    else:
        cleaned: List[Dict[str, Any]] = []
        for item in actions:
            if isinstance(item, dict):
                cleaned.append(
                    {
                        "task": str(item.get("task", "")).strip(),
                        "owner": str(item.get("owner", "Unassigned")).strip(),
                        "priority": str(item.get("priority", "Med")).strip(),
                    }
                )
            elif item:
                cleaned.append(
                    {"task": str(item), "owner": "Unassigned", "priority": "Med"}
                )
        parsed["action_items_assigned"] = cleaned

    return parsed


@router.post("/summarize-transcript", status_code=status.HTTP_200_OK)
def process_voice_meeting_summarizer(
    payload: VoiceMeetingInput, tenant: Tenant = Depends(require_tenant)
):
    """Turn a chaotic voice memo into structured, sector-optimised actions."""
    vertical = payload.industry_vertical.strip().lower()
    if vertical not in INDUSTRY_PROFILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Invalid industry_vertical provided. Allowed keys: "
                f"{list(INDUSTRY_PROFILES)}"
            ),
        )

    focus_directive = SECTOR_PROMPTS[vertical]

    prompt = f"""
    You are the CyberLogix AI Executive Sector Intelligence Clerk.
    Target Industry: {vertical.upper()}
    Sector Analytical Directive: {focus_directive}

    Raw Voice/Meeting Transcript:
    "{payload.raw_transcript}"

    Analyze the text and output a strict raw JSON object with these exact keys:
    "executive_summary": "1 sentence core overview",
    "extracted_operational_decisions": ["list of strings"],
    "action_items_assigned": [{{"task": "string", "owner": "string", "priority": "High/Med"}}],
    "industry_compliance_impact": "How this impacts sector-specific risk reduction"

    Record only what the transcript actually contains. If it names no
    decisions or no action items, return empty arrays rather than inventing
    plausible ones. Do not use markdown blocks or HTML formatting. Return
    ONLY raw JSON.
    """

    # The sentinel is never surfaced: an unparseable reply is reported as a
    # degraded read, because inventing meeting minutes nobody agreed to is
    # worse than admitting the transcript could not be processed.
    raw_text, source = safe_generate(
        prompt, fallback="", purpose="meeting intelligence"
    )
    parsed_json = _parse_intelligence_json(raw_text) if source == "gemini" else None

    if parsed_json is None:
        logger.error(
            "Meeting intelligence unavailable for tenant=%s vertical=%s (source=%s)",
            tenant.tenant_id,
            vertical,
            source,
        )
        return {
            "status": "TRANSCRIPT_PROCESSING_DEGRADED",
            "industry_vertical": vertical,
            "intelligence_report": None,
            "degraded_reason": (
                "The intelligence model was unavailable or returned an "
                "unparseable response. No summary was generated. The "
                "transcript was not modified; retry when the model is "
                "reachable."
            ),
            "report_source": source,
            "generated_at": iso(utc_now()),
        }

    return {
        "status": "TRANSCRIPT_PROCESSED_SUCCESSFULLY",
        "industry_vertical": vertical,
        "sector_directive": focus_directive,
        "intelligence_report": parsed_json,
        "report_source": source,
        "transcript_characters": len(payload.raw_transcript),
        "generated_at": iso(utc_now()),
    }


@router.get("/sectors", status_code=status.HTTP_200_OK)
def list_sector_directives():
    """The analytical directive applied to each vertical's transcripts."""
    return {
        "count": len(SECTOR_PROMPTS),
        "sectors": [
            {
                "vertical": key,
                "name": INDUSTRY_PROFILES[key]["name"],
                "directive": directive,
            }
            for key, directive in SECTOR_PROMPTS.items()
        ],
    }
