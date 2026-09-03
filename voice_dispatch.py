"""AI outbound voice escalation.

The second rung of the alerting ladder. An SMS that goes unacknowledged
past the grace window means nobody is reading their phone, so the incident
escalates to a spoken phone call: Gemini writes a call script in natural
speech and the response hands it to a telephony provider to place.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from gemini import safe_generate
from licenses import require_entitlement, require_tenant
from store import (
    INDUSTRY_PROFILES,
    STORE,
    VOICE_ESCALATION_GRACE_MINUTES,
    Incident,
    Tenant,
    utc_now,
)

logger = logging.getLogger("cyberlogix.voice")

router = APIRouter(prefix="/api/voice", tags=["AI Outbound Voice Escalation"])


class Acknowledgement(BaseModel):
    acknowledged_by: str = Field(
        ..., min_length=1, max_length=120, description="Who is taking the call"
    )


class ResolutionNote(BaseModel):
    resolved_by: str = Field(..., min_length=1, max_length=120)


def _load_incident(incident_id: str, tenant: Tenant) -> Incident:
    incident = STORE.get_incident(incident_id)
    if incident is None or incident.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found for this tenant.",
        )
    return incident


def build_voice_script(incident: Incident, tenant: Tenant) -> tuple[str, str]:
    """Draft the spoken escalation script, returning (script, source)."""
    profile = INDUSTRY_PROFILES[incident.industry_vertical]
    sensor = STORE.get_sensor(incident.sensor_id)
    location = sensor.location_name if sensor else "the monitored facility"
    minutes = int(incident.minutes_open())

    fallback = (
        f"This is an automated emergency call from CyberLogix AI for "
        f"{tenant.company_name}. Sensor {incident.sensor_id} at {location} has "
        f"been reporting a critical temperature of "
        f"{incident.temperature_fahrenheit} degrees Fahrenheit for {minutes} "
        f"minutes, and the likely cause is {profile['catastrophe']}. Please go "
        f"to the site immediately. This message will repeat."
    )

    prompt = f"""
    You are the CyberLogix AI automated outbound emergency voice operator.
    You are placing a phone call because a critical facility alert was sent by
    SMS {minutes} minutes ago and nobody has acknowledged it.

    Customer: {tenant.company_name}
    Sector Profile: {profile['name']}
    Suspected Root Cause Catastrophe: {profile['catastrophe']}
    Sensor Node ID: {incident.sensor_id}
    Facility Location Tag: {location}
    Current Reading: {incident.temperature_fahrenheit}°F
    Breach Detail: {incident.breach_details}
    Minutes Unacknowledged: {minutes}

    Write the exact words to be spoken aloud by a text-to-speech voice down
    the phone line. Keep it under 60 spoken words. Open by identifying
    CyberLogix AI so the person knows this is not a scam call, state the
    specific asset at risk and the likely cause, and close by telling them to
    press 1 to acknowledge. Write plain spoken sentences only: no markdown, no
    stage directions, no bullet points, no speaker labels.
    """

    return safe_generate(prompt, fallback, purpose="voice escalation script")


@router.get("/pending")
def pending_escalations(tenant: Tenant = Depends(require_tenant)):
    """Incidents past the grace window with nobody yet on the case."""
    now = utc_now()
    open_incidents = STORE.open_incidents(tenant.tenant_id)

    due = [
        incident
        for incident in open_incidents
        if incident.voice_escalated_at is None
        and incident.minutes_open(now) >= VOICE_ESCALATION_GRACE_MINUTES
    ]

    return {
        "grace_window_minutes": VOICE_ESCALATION_GRACE_MINUTES,
        "open_incidents": len(open_incidents),
        "escalation_due": len(due),
        "incidents": [incident.public() for incident in due],
    }


@router.post("/escalate/{incident_id}")
def escalate_to_voice(
    incident_id: str,
    force: bool = False,
    tenant: Tenant = Depends(require_entitlement("voice_escalation")),
):
    """Draft and dispatch the voice call for one unacknowledged incident.

    Refuses early escalation before the grace window unless `force=true`,
    so an operator can override but automation cannot call someone the
    moment an SMS lands.
    """
    incident = _load_incident(incident_id, tenant)

    if incident.acknowledged_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Incident '{incident_id}' was already acknowledged by "
                f"{incident.acknowledged_by}. No call placed."
            ),
        )

    if incident.resolved_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Incident '{incident_id}' is already resolved. No call placed.",
        )

    waited = incident.minutes_open()
    if not force and waited < VOICE_ESCALATION_GRACE_MINUTES:
        raise HTTPException(
            status_code=status.HTTP_425_TOO_EARLY,
            detail=(
                f"Incident has been open {waited:.1f} minutes; the SMS grace "
                f"window is {VOICE_ESCALATION_GRACE_MINUTES} minutes. "
                "Pass force=true to override."
            ),
        )

    script, source = build_voice_script(incident, tenant)
    incident.voice_escalated_at = utc_now()
    incident.voice_script = script
    incident.voice_dispatch_source = source

    logger.critical(
        "Voice escalation: incident=%s sensor=%s tenant=%s unacknowledged=%.1fmin",
        incident.incident_id,
        incident.sensor_id,
        tenant.tenant_id,
        waited,
    )

    return {
        "status": "VOICE_ESCALATION_DISPATCHED",
        "incident_id": incident.incident_id,
        "call_to": tenant.contact_phone,
        "call_recipient": tenant.contact_name,
        "minutes_unacknowledged": waited,
        "voice_script": script,
        "voice_dispatch_source": source,
        "forced": force,
    }


@router.post("/acknowledge/{incident_id}")
def acknowledge_incident(
    incident_id: str,
    payload: Acknowledgement,
    tenant: Tenant = Depends(require_tenant),
):
    """Stop the escalation ladder: a human has the incident."""
    incident = _load_incident(incident_id, tenant)

    if incident.acknowledged_at is None:
        incident.acknowledged_at = utc_now()
        incident.acknowledged_by = payload.acknowledged_by

    return {
        "status": "ACKNOWLEDGED",
        "message": "Escalation ladder halted.",
        "incident": incident.public(),
    }


@router.post("/resolve/{incident_id}")
def resolve_incident(
    incident_id: str,
    payload: ResolutionNote,
    tenant: Tenant = Depends(require_tenant),
):
    """Close an incident once the physical fault is fixed."""
    incident = _load_incident(incident_id, tenant)

    if incident.resolved_at is None:
        now = utc_now()
        if incident.acknowledged_at is None:
            incident.acknowledged_at = now
            incident.acknowledged_by = payload.resolved_by
        incident.resolved_at = now

    return {
        "status": "RESOLVED",
        "resolved_by": payload.resolved_by,
        "incident": incident.public(),
    }


@router.get("/incidents")
def list_incidents(tenant: Tenant = Depends(require_tenant)):
    """Full incident history for the tenant, newest first."""
    incidents = STORE.incidents_for(tenant.tenant_id)
    return {
        "count": len(incidents),
        "open": sum(1 for i in incidents if i.open),
        "incidents": [i.public() for i in incidents],
    }
