"""AI outbound voice escalation.

The second rung of the alerting ladder. An SMS that goes unacknowledged
past the grace window means nobody is reading their phone, so the incident
escalates to a spoken phone call: Gemini writes a call script in natural
speech and the response hands it to a telephony provider to place.
"""

from __future__ import annotations

import logging
import secrets
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from gemini import safe_generate
from auth import (
    actor_label,
    optional_operator,
    require_entitlement,
    require_tenant,
    write_audit,
)
from notifications import (
    acknowledgement_url,
    build_gather_reply,
    place_voice_call,
    verify_twilio_signature,
)
from store import (
    INDUSTRY_PROFILES,
    STORE,
    VOICE_ESCALATION_GRACE_MINUTES,
    Incident,
    Tenant,
    User,
    format_temperature,
    spoken_temperature,
    utc_now,
)

logger = logging.getLogger("cyberlogix.voice")

router = APIRouter(prefix="/api/voice", tags=["AI Outbound Voice Escalation"])


class Acknowledgement(BaseModel):
    acknowledged_by: Optional[str] = Field(
        None,
        max_length=120,
        description=(
            "Who is taking the call. Ignored when a signed-in operator makes "
            "the request — their own name is recorded instead."
        ),
    )


class ResolutionNote(BaseModel):
    resolved_by: Optional[str] = Field(None, max_length=120)


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
    unit = tenant.temperature_unit
    spoken = spoken_temperature(incident.temperature_fahrenheit, unit)

    fallback = (
        f"This is an automated emergency call from CyberLogix AI for "
        f"{tenant.company_name}. Sensor {incident.sensor_id} at {location} has "
        f"been reporting a critical temperature of "
        f"{spoken} for {minutes} "
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
    Current Reading: {format_temperature(incident.temperature_fahrenheit, unit)}
    Breach Detail: {incident.breach_details}
    Minutes Unacknowledged: {minutes}

    Write the exact words to be spoken aloud by a text-to-speech voice down
    the phone line. Keep it under 60 spoken words. Open by identifying
    CyberLogix AI so the person knows this is not a scam call, state the
    specific asset at risk and the likely cause, and close by telling them to
    press 1 to acknowledge. Write plain spoken sentences only: no markdown, no
    stage directions, no bullet points, no speaker labels.
    """

    return safe_generate(
        prompt,
        fallback,
        purpose="voice escalation script",
        tenant_id=tenant.tenant_id,
    )


def _notify_hooks(
    tenant: Tenant, incident: Incident, state: str, note: str = ""
) -> None:
    """Tell the chat channels and the rotation what changed.

    Imported inside the call: webhooks imports the store and auth helpers,
    and a module-level import would close the cycle.
    """
    from webhooks import dispatch_event

    dispatch_event(
        tenant, incident, STORE.get_sensor(incident.sensor_id), state, note
    )


def dispatch_voice_call(incident: Incident, tenant: Tenant) -> dict:
    """Draft the script, place the call and stamp the incident.

    Shared by the manual escalate endpoint and the autopilot sweep so both
    routes record delivery identically.
    """
    script, source = build_voice_script(incident, tenant)

    # Walk the escalation ladder: stop at the first person actually reached,
    # so a wrong number or a dead line does not end the escalation.
    sensor = STORE.get_sensor(incident.sensor_id)
    ladder = STORE.voice_ladder(tenant, sensor.site_id if sensor else None)
    # So the script's closing "press 1 to acknowledge" reaches something.
    action_url = acknowledgement_url(
        incident.incident_id, STORE.issue_ack_token(incident)
    )
    fanout = []
    delivery = None
    for contact in ladder:
        attempt = dict(
            place_voice_call(contact.phone, script, tenant.tenant_id, action_url),
            contact_id=contact.contact_id,
            contact_name=contact.full_name,
        )
        fanout.append(attempt)
        delivery = attempt
        if attempt["delivered"]:
            break

    STORE.record_voice_escalation(incident, script, source, delivery, fanout)

    _notify_hooks(
        tenant,
        incident,
        "escalated",
        f"No acknowledgement after {int(incident.minutes_open())} minutes; "
        "a voice call has been placed.",
    )

    logger.critical(
        "Voice escalation: incident=%s sensor=%s tenant=%s attempts=%d reached=%s",
        incident.incident_id,
        incident.sensor_id,
        tenant.tenant_id,
        len(fanout),
        delivery["delivered"] if delivery else False,
    )
    return {
        "script": script,
        "source": source,
        "delivery": delivery,
        "fanout": fanout,
    }


@router.post("/keypress/{incident_id}/{token}", include_in_schema=False)
async def voice_keypress(
    incident_id: str, token: str, request: Request
) -> Response:
    """Twilio posts the digit the callee pressed. 1 acknowledges.

    Called by Twilio, not by an operator, so there is no bearer token to
    check: the request is trusted only if it carries a valid Twilio
    signature *and* the per-incident secret in the URL. Replies are TwiML
    whatever happens — an error page would be read aloud as noise.
    """
    # Parsed by hand rather than via request.form(): Twilio always posts
    # url-encoded, and this keeps python-multipart off the dependency list.
    body = (await request.body()).decode("utf-8", "replace")
    form = dict(parse_qsl(body, keep_blank_values=True))
    if not verify_twilio_signature(str(request.url), form, request.headers.get("X-Twilio-Signature", "")):
        logger.warning(
            "Rejected an unverified keypress callback for %s.", incident_id
        )
        return Response(
            content=build_gather_reply(
                "This call could not be verified. Goodbye."
            ),
            media_type="application/xml",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    incident = STORE.get_incident(incident_id)
    if (
        incident is None
        or not incident.ack_token
        or not secrets.compare_digest(incident.ack_token, token)
    ):
        logger.warning("Keypress callback for unknown incident %s.", incident_id)
        return Response(
            content=build_gather_reply("That alert is no longer active. Goodbye."),
            media_type="application/xml",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if (form.get("Digits") or "").strip() != "1":
        return Response(
            content=build_gather_reply(
                "No acknowledgement received. The escalation stays open."
            ),
            media_type="application/xml",
        )

    caller = (form.get("To") or "the handset").strip()
    already = incident.acknowledged_at is not None
    STORE.acknowledge_incident(incident, f"phone keypad ({caller})")
    tenant = STORE.get_tenant(incident.tenant_id)
    if tenant is not None and not already:
        write_audit(
            tenant,
            None,
            "incident.acknowledged",
            f"{incident.incident_id} acknowledged by keypad from {caller}.",
        )
    if tenant is not None and not already:
        _notify_hooks(
            tenant, incident, "acknowledged", f"Acknowledged from {caller}."
        )
    logger.info(
        "Incident %s acknowledged from the handset (%s).", incident_id, caller
    )
    return Response(
        content=build_gather_reply(
            "Acknowledged. No further calls will be placed for this alert. "
            "Thank you."
        ),
        media_type="application/xml",
    )


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
    operator: Optional[User] = Depends(optional_operator),
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

    outcome = dispatch_voice_call(incident, tenant)
    write_audit(
        tenant,
        operator,
        "incident.escalated",
        f"{incident.incident_id} escalated to a voice call"
        + (" (forced)." if force else "."),
    )

    return {
        "status": "VOICE_ESCALATION_DISPATCHED",
        "incident_id": incident.incident_id,
        "call_to": outcome["delivery"]["to"] if outcome["delivery"] else None,
        "call_recipient": (
            outcome["delivery"].get("contact_name") if outcome["delivery"] else None
        ),
        "escalation_attempts": outcome["fanout"],
        "minutes_unacknowledged": waited,
        "voice_script": outcome["script"],
        "voice_dispatch_source": outcome["source"],
        "voice_delivery": outcome["delivery"],
        "forced": force,
    }


@router.post("/acknowledge/{incident_id}")
def acknowledge_incident(
    incident_id: str,
    payload: Acknowledgement,
    tenant: Tenant = Depends(require_tenant),
    operator: Optional[User] = Depends(optional_operator),
):
    """Stop the escalation ladder: a human has the incident."""
    incident = _load_incident(incident_id, tenant)

    actor = actor_label(operator, payload.acknowledged_by or "API key")
    STORE.acknowledge_incident(incident, actor)
    _notify_hooks(tenant, incident, "acknowledged", f"{actor} has the incident.")
    write_audit(
        tenant,
        operator,
        "incident.acknowledged",
        f"{incident.incident_id} on {incident.sensor_id}.",
        fallback_actor=actor,
    )

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
    operator: Optional[User] = Depends(optional_operator),
):
    """Close an incident once the physical fault is fixed."""
    incident = _load_incident(incident_id, tenant)

    actor = actor_label(operator, payload.resolved_by or "API key")
    STORE.resolve_incident(incident, actor)
    _notify_hooks(tenant, incident, "resolved", f"Closed out by {actor}.")
    write_audit(
        tenant,
        operator,
        "incident.resolved",
        f"{incident.incident_id} on {incident.sensor_id}.",
        fallback_actor=actor,
    )

    return {
        "status": "RESOLVED",
        "resolved_by": actor,
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
