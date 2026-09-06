"""The claim packet: everything an adjuster asks for, in one document.

When a freezer fails and $200,000 of product is written off, the money is
not lost in the failure — it is lost in the six weeks afterwards, while
somebody hunts for temperature logs, works out who was called and when,
and tries to prove the response was reasonable. Claims get reduced or
denied because the evidence was assembled badly, not because the loss
wasn't real.

This assembles it the moment it is asked for: the readings around the
event, the alert timeline with delivery receipts, who acknowledged and
how long it took, the safe band that was breached, and a vault attestation
so the adjuster can verify none of it was written after the fact.

Deliberately not priced per recovery. A percentage of the settlement means
a quiet year pays us nothing, and it puts us on the wrong side of the
customer's interests the moment a claim is marginal. It is part of the
flat-rate assurance add-on instead.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import require_tenant
from gemini import safe_generate
from store import (
    INDUSTRY_PROFILES,
    STORE,
    Incident,
    Tenant,
    display_temperature,
    format_temperature,
    iso,
    utc_now,
)

logger = logging.getLogger("cyberlogix.claims")

router = APIRouter(prefix="/api/claims", tags=["Insurance Claim Packets"])

# How much history either side of the event goes in the packet. Wide enough
# to show the run-up and the recovery, narrow enough that an adjuster reads
# it rather than filing it.
WINDOW_HOURS_BEFORE = 12.0
WINDOW_HOURS_AFTER = 12.0


def _load_incident(incident_id: str, tenant: Tenant) -> Incident:
    incident = STORE.get_incident((incident_id or "").strip())
    if incident is None or incident.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found for this tenant.",
        )
    return incident


def _delivery_rows(fanout: List[Dict[str, Any]], single: Optional[Dict[str, Any]]):
    """Delivery attempts as an adjuster needs to read them."""
    rows = fanout or ([single] if single else [])
    return [
        {
            "channel": row.get("channel"),
            "to": row.get("to"),
            "recipient": row.get("contact_name"),
            "delivered": bool(row.get("delivered")),
            "status": row.get("status"),
            "provider_reference": row.get("provider_sid"),
            "detail": row.get("detail"),
        }
        for row in rows
        if row
    ]


def build_packet(tenant: Tenant, incident: Incident) -> Dict[str, Any]:
    """Assemble the whole evidential record for one incident."""
    from vault import attest_sensor, signing_state

    sensor = STORE.get_sensor(incident.sensor_id)
    unit = tenant.temperature_unit
    profile = INDUSTRY_PROFILES[incident.industry_vertical]
    site = (
        STORE.get_site(sensor.site_id) if sensor and sensor.site_id else None
    )

    start = incident.opened_at - timedelta(hours=WINDOW_HOURS_BEFORE)
    end = (incident.resolved_at or incident.acknowledged_at or utc_now()) + timedelta(
        hours=WINDOW_HOURS_AFTER
    )
    window = [
        r
        for r in STORE.readings_for(incident.sensor_id, since=start)
        if r.recorded_at <= end
    ]
    excursions = [r for r in window if r.breached]

    # Where the operator was already told something was wrong before this
    # incident opened — an adjuster will look for prior warnings, and it is
    # far better to surface them than to have them found.
    prior = [
        i
        for i in STORE.incidents_for(tenant.tenant_id)
        if i.sensor_id == incident.sensor_id
        and i.opened_at < incident.opened_at
        and i.opened_at >= incident.opened_at - timedelta(days=90)
    ]

    timeline: List[Dict[str, Any]] = [
        {
            "at": iso(incident.opened_at),
            "event": "Breach detected",
            "detail": (
                f"{format_temperature(incident.temperature_fahrenheit, unit)} — "
                f"{incident.breach_details}"
            ),
        }
    ]
    sms_rows = _delivery_rows(incident.sms_fanout, incident.sms_delivery)
    if sms_rows:
        landed = sum(1 for r in sms_rows if r["delivered"])
        timeline.append(
            {
                "at": iso(incident.opened_at),
                "event": "Alert dispatched by SMS",
                "detail": f"{len(sms_rows)} on the roster texted, {landed} delivered.",
            }
        )
    if incident.voice_escalated_at:
        voice_rows = _delivery_rows(incident.voice_fanout, incident.voice_delivery)
        reached = next((r for r in voice_rows if r["delivered"]), None)
        timeline.append(
            {
                "at": iso(incident.voice_escalated_at),
                "event": "Escalated to a voice call",
                "detail": (
                    f"Reached {reached['recipient'] or reached['to']}."
                    if reached
                    else "No one on the ladder answered."
                ),
            }
        )
    if incident.acknowledged_at:
        timeline.append(
            {
                "at": iso(incident.acknowledged_at),
                "event": "Acknowledged",
                "detail": f"Taken by {incident.acknowledged_by}.",
            }
        )
    if incident.resolved_at:
        timeline.append(
            {"at": iso(incident.resolved_at), "event": "Resolved", "detail": ""}
        )

    minutes_to_ack = (
        round((incident.acknowledged_at - incident.opened_at).total_seconds() / 60, 1)
        if incident.acknowledged_at
        else None
    )

    attestation = (
        attest_sensor(tenant, sensor, since=start) if sensor is not None else None
    )

    return {
        "document": "Loss Event Evidence Packet",
        "packet_for": incident.incident_id,
        "issued_at": iso(utc_now()),
        "insured": {
            "company_name": tenant.company_name,
            "contact_name": tenant.contact_name,
            "contact_email": tenant.contact_email,
            "contact_phone": tenant.contact_phone,
        },
        "location": {
            "site": site.name if site else None,
            "address": site.address if site else None,
            "asset": sensor.location_name if sensor else incident.sensor_id,
            "asset_type": profile["asset_noun"],
            "industry": profile["name"],
        },
        "event": {
            "incident_id": incident.incident_id,
            "opened_at": iso(incident.opened_at),
            "detected_reading": display_temperature(
                incident.temperature_fahrenheit, unit
            ),
            "temperature_unit": unit,
            "breach_details": incident.breach_details,
            "probable_cause": incident.catastrophe,
            "state": (
                "resolved"
                if incident.resolved_at
                else "acknowledged"
                if incident.acknowledged_at
                else "open"
            ),
            "minutes_to_acknowledge": minutes_to_ack,
            "minutes_open": incident.minutes_open(),
        },
        "response": {
            "timeline": timeline,
            "sms_deliveries": sms_rows,
            "voice_deliveries": _delivery_rows(
                incident.voice_fanout, incident.voice_delivery
            ),
            "alert_text_sent": incident.sms_text,
            "call_script": incident.voice_script,
        },
        "evidence": {
            "window_start": iso(start),
            "window_end": iso(end),
            "readings_in_window": len(window),
            "excursions_in_window": len(excursions),
            "first_excursion_at": iso(excursions[0].recorded_at) if excursions else None,
            "last_excursion_at": iso(excursions[-1].recorded_at) if excursions else None,
            "peak_reading": (
                display_temperature(
                    max(r.temperature_fahrenheit for r in window), unit
                )
                if window
                else None
            ),
            "readings": [
                {
                    "at": iso(r.recorded_at),
                    "temperature": display_temperature(r.temperature_fahrenheit, unit),
                    "humidity": r.humidity_percent,
                    "breached": r.breached,
                }
                for r in window
            ],
        },
        "prior_incidents_90_days": [
            {
                "incident_id": i.incident_id,
                "opened_at": iso(i.opened_at),
                "state": "resolved" if i.resolved_at else "open",
                "detail": i.breach_details,
            }
            for i in prior
        ],
        "attestation": attestation,
        "signing": signing_state(),
        "verification": (
            "The readings above are chained: each digest covers its reading "
            "and the digest before it. POST them to /api/vault/verify with "
            "the chain head in this packet to confirm independently that "
            "nothing was altered after the event."
        ),
    }


def build_cover_letter(tenant: Tenant, packet: Dict[str, Any]) -> tuple[str, str]:
    """A plain covering summary for the adjuster, returning (text, source)."""
    event = packet["event"]
    evidence = packet["evidence"]
    location = packet["location"]

    fallback = (
        f"{tenant.company_name} reports an environmental excursion at "
        f"{location['asset']}"
        + (f" ({location['site']})" if location["site"] else "")
        + f" beginning {event['opened_at']}. Continuous monitoring recorded "
        f"{evidence['readings_in_window']} readings across the event window, "
        f"of which {evidence['excursions_in_window']} were outside the "
        f"documented safe band; the probable cause is "
        f"{event['probable_cause'].lower()}. The alert was dispatched "
        "automatically at detection and the response is set out in the "
        "attached timeline"
        + (
            f", with acknowledgement {event['minutes_to_acknowledge']} minutes "
            "after detection."
            if event["minutes_to_acknowledge"] is not None
            else ", which remains open at time of issue."
        )
        + " All readings are hash-chained and independently verifiable."
    )

    prompt = f"""
    You are writing the covering letter that sits at the front of an
    insurance claim evidence packet. The reader is a loss adjuster.

    Insured: {tenant.company_name}
    Location: {location['asset']} at {location['site'] or 'unspecified site'}
    Sector: {location['industry']}
    Event opened: {event['opened_at']}
    Reading at detection: {event['detected_reading']}°{event['temperature_unit']}
    Breach detail: {event['breach_details']}
    Probable cause: {event['probable_cause']}
    Readings in window: {evidence['readings_in_window']}
    Excursions in window: {evidence['excursions_in_window']}
    Minutes to acknowledge: {event['minutes_to_acknowledge']}
    Prior incidents on this asset in 90 days: {len(packet['prior_incidents_90_days'])}

    Write three short paragraphs of plain prose: what happened, how it was
    detected and responded to, and what evidence is attached. State only
    the figures given above. Do not estimate the value of the loss, do not
    argue that the claim should be paid, and do not characterise the
    insured's conduct — an adjuster who catches one invented figure will
    discount the entire packet. No markdown, no headings, no bullet points.
    """

    return safe_generate(
        prompt, fallback, purpose="claim cover letter", tenant_id=tenant.tenant_id
    )


@router.get("/eligible")
def eligible_incidents(
    days: int = Query(365, ge=1, le=1825),
    tenant: Tenant = Depends(require_tenant),
):
    """Incidents a packet could be produced for."""
    since = utc_now() - timedelta(days=days)
    incidents = STORE.incidents_for(tenant.tenant_id, since=since)
    return {
        "count": len(incidents),
        "period_days": days,
        "incidents": [
            {
                "incident_id": i.incident_id,
                "sensor_id": i.sensor_id,
                "opened_at": iso(i.opened_at),
                "catastrophe": i.catastrophe,
                "state": (
                    "resolved"
                    if i.resolved_at
                    else "acknowledged"
                    if i.acknowledged_at
                    else "open"
                ),
                "readings_available": len(STORE.readings_for(i.sensor_id)) > 0,
            }
            for i in incidents
        ],
        "note": (
            "A packet can be produced for any of these. The evidence is "
            "assembled from what was recorded at the time; nothing is "
            "written after the fact."
        ),
    }


@router.post("/{incident_id}/packet")
def claim_packet(
    incident_id: str,
    cover_letter: bool = Query(
        True, description="Include a drafted covering letter for the adjuster."
    ),
    tenant: Tenant = Depends(require_tenant),
):
    """Assemble the evidence packet for one incident."""
    incident = _load_incident(incident_id, tenant)
    packet = build_packet(tenant, incident)

    if cover_letter:
        text, source = build_cover_letter(tenant, packet)
        packet["cover_letter"] = text
        packet["cover_letter_source"] = source

    logger.info(
        "Claim packet assembled: incident=%s tenant=%s readings=%d",
        incident.incident_id,
        tenant.tenant_id,
        packet["evidence"]["readings_in_window"],
    )
    return packet
