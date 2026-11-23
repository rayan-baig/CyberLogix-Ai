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

This helps the customer claim on their own insurer. It is not us paying
out: there is no guarantee here and we are not an insurer. We hold the
evidence, so we are the cheapest place for it to be assembled, and that
is the whole of the offer.

Deliberately not priced per settlement. A percentage puts us on the wrong
side of the customer the moment a claim is marginal -- we would be
arguing for the bigger number rather than the true one, on a document an
adjuster is reading precisely to find out which it is.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import require_tenant
from gemini import safe_generate
from records import person_name
from store import (
    INDUSTRY_PROFILES,
    STORE,
    Incident,
    Tenant,
    display_temperature,
    format_temperature,
    iso,
    restate_temperatures,
    utc_now,
)

logger = logging.getLogger("cyberlogix.claims")

router = APIRouter(prefix="/api/claims", tags=["Insurance Claim Packets"])

# How much history either side of the event goes in the packet. Wide enough
# to show the run-up and the recovery, narrow enough that an adjuster reads
# it rather than filing it.
def _evidence_window(incident, start, end):
    """The readings for this event, preserved copy merged with live ones.

    Returned as Reading objects so everything downstream — the hash
    chain, the peak, the excursion count — is unchanged. Merged by id,
    because the preserved snapshot and the live buffer overlap while an
    incident is recent.
    """
    from store import Reading

    found = {}
    for row in incident.evidence_readings:
        try:
            found[row["reading_id"]] = Reading.from_row(row)
        except Exception:  # noqa: BLE001 - one malformed row must not
            continue      # empty the evidence for the whole claim
    # Live readings only while the sensor still belongs to this estate. A
    # sensor id is free again once decommissioned, and another customer
    # registering the same id would otherwise have their readings put in
    # this customer's claim, under an attestation.
    live = STORE.get_sensor(incident.sensor_id)
    if live is not None and live.tenant_id == incident.tenant_id:
        for reading in STORE.readings_for(incident.sensor_id, since=start, complete=True, until=end):
            if reading.recorded_at <= end:
                found[reading.reading_id] = reading
    return sorted(
        (r for r in found.values()
         if r.recorded_at is not None and start <= r.recorded_at <= end),
        key=lambda r: (r.recorded_at, r.reading_id),
    )


WINDOW_HOURS_BEFORE = 12.0
WINDOW_HOURS_AFTER = 12.0


def reader_zone(name: Optional[str]):
    """The adjuster-facing clock: the browser's own time zone when it sent
    one ("America/New_York"), else UTC. An unknown name is UTC, never an
    error: the pack must always come out."""
    try:
        return ZoneInfo((name or "").strip()) if (name or "").strip() else timezone.utc
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return timezone.utc


def when_words(moment: Optional[datetime], zone=timezone.utc) -> str:
    """"Nov 23, 2026, 1:58 AM EST": a date a loss adjuster reads, never
    "2026-11-23T01:58:35Z" (3.33.0)."""
    if moment is None:
        return "not recorded"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(zone)
    hour = local.hour % 12 or 12
    label = local.tzname() or "UTC"
    return (f"{local.strftime('%b')} {local.day}, {local.year}, "
            f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'} {label}")


def plural(n: int, one: str, many: Optional[str] = None) -> str:
    """"1 reading", "6 readings": the count and the noun that goes with it."""
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def full_name(label: Optional[str]) -> Optional[str]:
    """A person's whole name from an audit label, without the address:
    "Maya Okafor <maya@...>" is "Maya Okafor" on a document an adjuster
    reads. A machine's label is left as it is."""
    if not label:
        return None
    text = str(label).split("<", 1)[0].strip()
    return text or person_name(label)


def asset_label(name: Optional[str], noun: str) -> str:
    """The unit as the kitchen calls it, with its kind only when the name
    doesn't already say it: "Walk-in fridge", never "Walk-in fridge
    (walk-in)" (3.33.0); "Unit 4 (walk-in)" keeps it."""
    shown = (name or "").strip()
    if not shown:
        return noun
    flat = "".join(ch for ch in shown.lower() if ch.isalnum())
    kind = "".join(ch for ch in (noun or "").lower() if ch.isalnum())
    return shown if not kind or kind in flat else f"{shown} ({noun})"


def _breach_words(first_f: Optional[float], sensor, unit: str) -> Optional[str]:
    """"58.6°F: too warm, over the 41°F limit." from the first reading out
    of range and the unit's own limit, or None when either isn't known."""
    from domain import plain_temperature

    if first_f is None or sensor is None:
        return None
    above, below = sensor.bounds()
    if above is not None and first_f > above:
        return f"{format_temperature(first_f, unit)}: too warm, over the {plain_temperature(above, unit)} limit."
    if below is not None and first_f < below:
        return f"{format_temperature(first_f, unit)}: too cold, under the {plain_temperature(below, unit)} limit."
    return None


def food_record(incident: Incident) -> Dict[str, Any]:
    """What happened to the food, for the pack (3.33.0): the answer, who
    gave it and when, the note of what went, and every change, oldest
    first. Nothing is rubbed out: a change adds a row."""
    from records import food_answer_words

    history = []
    for n, row in enumerate(incident.food_history):
        words = food_answer_words(row.get("disposition"), row.get("auto_minutes"))
        history.append({
            "words": words,
            "changed": n > 0,
            "by": ("CyberLogix, automatically" if row.get("auto")
                   else full_name(row.get("by"))),
            "at": row.get("at"),
        })
    auto = incident.food_auto_minutes is not None
    return {
        "answer": incident.food_words(),
        "disposition": incident.product_disposition,
        "thrown": incident.product_disposition == "discarded",
        "by": (("CyberLogix, automatically" if auto else full_name(incident.food_answered_by))
               if incident.product_disposition else None),
        "at": iso(incident.food_answered_at),
        "auto": auto,
        "note": incident.corrective_action or None,
        "note_by": full_name(incident.corrective_action_by) if incident.corrective_action else None,
        "note_at": iso(incident.corrective_action_at) if incident.corrective_action else None,
        "history": history,
    }


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


def build_packet(tenant: Tenant, incident: Incident, zone=timezone.utc) -> Dict[str, Any]:
    """Assemble the whole evidential record for one incident. `zone` is
    the reader's clock, for the dates written out in words."""
    from vault import attest_sensor, signing_state

    sensor = STORE.get_sensor(incident.sensor_id)
    # The same id under another estate is somebody else's asset: its name,
    # its site and its street address are not this customer's to see.
    if sensor is not None and sensor.tenant_id != incident.tenant_id:
        sensor = None
    unit = tenant.temperature_unit
    profile = INDUSTRY_PROFILES[incident.industry_vertical]
    site = (
        STORE.get_site(sensor.site_id) if sensor and sensor.site_id else None
    )

    now = utc_now()
    start = incident.opened_at - timedelta(hours=WINDOW_HOURS_BEFORE)
    # Never past the moment the pack is issued (3.33.0): a window that
    # "ends" twelve hours from now reads to an adjuster as readings
    # promised that nobody could have taken yet.
    end = min(now, (incident.resolved_at or incident.acknowledged_at or now) + timedelta(
        hours=WINDOW_HOURS_AFTER
    ))
    # The preserved copy first, live readings second.
    #
    # Readings roll off: the oldest are deleted once a sensor passes its
    # retention cap, which at a five-minute pulse is under two days. This
    # window was built from live readings alone, so a claim filed a week
    # after the failure assembled a packet full of healthy readings with
    # the breach deleted — and reported zero excursions on the document
    # sent to the insurer to prove one. The evidence is now copied onto
    # the incident when it opens and when it resolves, and the incident
    # is kept for years.
    window = _evidence_window(incident, start, end)
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

    # The reading taken when it went out of range, at that time; the worst
    # one apart, at its own time. "Breach detected" used to print the
    # latest reading beside the time the alert opened.
    first = incident.first_out_f
    # The breach in words from that first reading and the unit's limit
    # (3.33.0): the stored line follows the latest reading, so the pack said
    # "Breach detected — 58.6°F — Too warm: 52.1°F (limit 41°F)".
    breach = _breach_words(first, sensor, unit) or restate_temperatures(incident.breach_details, unit)
    timeline: List[Dict[str, Any]] = [
        {
            "at": iso(incident.started_at),
            "event": "Breach detected",
            "detail": breach,
        }
    ]
    if incident.peak_f is not None and incident.peak_f != first:
        timeline.append({
            "at": iso(incident.peak_at),
            "event": "Worst reading",
            "detail": format_temperature(incident.peak_f, unit),
        })
    sms_rows = _delivery_rows(incident.sms_fanout, incident.sms_delivery)
    if incident.texts_off:
        timeline.append({
            "at": iso(incident.opened_at),
            "event": "Alert written, not sent",
            "detail": "Texts are off on this server, so nobody was texted.",
        })
    elif sms_rows:
        landed = sum(1 for r in sms_rows if r["delivered"])
        timeline.append(
            {
                "at": iso(incident.opened_at),
                "event": "Alert dispatched by SMS",
                "detail": f"{len(sms_rows)} on the roster texted, {landed} delivered.",
            }
        )
    # Re-sends after every text failed are part of what was done to tell
    # people; leaving them out would make the record read as if nobody
    # tried again.
    for attempt in incident.sms_retries:
        rows = _delivery_rows(attempt.get("deliveries") or [], None)
        landed = sum(1 for r in rows if r["delivered"])
        timeline.append(
            {
                "at": attempt.get("at"),
                "event": "Alert text sent again",
                "detail": f"{len(rows)} on the roster texted, {landed} delivered.",
            }
        )
    if incident.voice_escalated_at:
        voice_rows = _delivery_rows(incident.voice_fanout, incident.voice_delivery)
        reached = next((r for r in voice_rows if r["delivered"]), None)
        timeline.append(
            {
                "at": iso(incident.voice_escalated_at),
                "event": ("Escalated to a voice call" if reached or not incident.calls_off
                          else "Call step reached; no call placed"),
                "detail": (
                    f"Reached {reached['recipient'] or reached['to']}."
                    if reached
                    else "Calls are off on this server, so no call was placed."
                    if incident.calls_off
                    else "No call went through to anyone on the ladder."
                ),
            }
        )
    if incident.acknowledged_at:
        timeline.append(
            {
                "at": iso(incident.acknowledged_at),
                "event": "Acknowledged",
                "detail": f"Taken by {full_name(incident.acknowledged_by)}.",
            }
        )
    if incident.resolved_at:
        timeline.append({
            "at": iso(incident.resolved_at), "event": "Closed",
            "detail": ("Back in range by itself." if incident.resolved_note and not incident.resolved_by
                       else f"Closed by {full_name(incident.resolved_by)}." if incident.resolved_by else ""),
        })
    # In the order it happened: a re-send at 15 minutes comes after the
    # call at 10. Stable, so events at one moment keep their order.
    timeline.sort(key=lambda event: event["at"] or "")

    # Clamped for the same reason minutes_open is: "acknowledged -120
    # minutes after detection" does not read to a loss adjuster as a clock
    # bug, it reads as a document somebody made up.
    minutes_to_ack = (
        max(0.0, round(
            (incident.acknowledged_at - incident.opened_at).total_seconds() / 60, 1
        ))
        if incident.acknowledged_at
        else None
    )

    # Bounded at both ends, so the head covers exactly the readings the
    # packet shows. Without the upper bound it also covered readings
    # recorded after the window, and an adjuster re-deriving it would get
    # a different figure and conclude the evidence was false.
    attestation = (
        attest_sensor(tenant, sensor, since=start, until=end, anchor=True)
        if sensor is not None
        else None
    )

    return {
        "document": "Loss Event Evidence Packet",
        "packet_for": incident.incident_id,
        "issued_at": iso(now),
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
            # "Walk-in fridge", with its kind only when the name lacks it.
            "asset_label": asset_label(
                sensor.location_name if sensor else incident.sensor_id, profile["asset_noun"]),
            "industry": profile["name"],
        },
        "event": {
            "incident_id": incident.incident_id,
            "opened_at": iso(incident.opened_at),
            # The first reading out of range, and the worst; for an alert
            # from before both were kept, the latest it held.
            "detected_reading": display_temperature(
                incident.first_out_f if incident.first_out_f is not None
                else incident.temperature_fahrenheit, unit
            ),
            "peak_reading": display_temperature(
                incident.peak_f if incident.peak_f is not None
                else incident.temperature_fahrenheit, unit
            ),
            "temperature_unit": unit,
            "breach_details": breach,
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
            "out_since": iso(incident.started_at),
            "resolved_at": iso(incident.resolved_at),
            "closed_by_itself": bool(incident.resolved_at and incident.resolved_note
                                     and not incident.resolved_by),
        },
        # What happened to the food (3.33.0), the part of the record an
        # adjuster weighs a loss by: the answer, who and when, the note of
        # what went, and every change.
        "food": food_record(incident),
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
            # For a person: in the customer's unit, rounded for reading.
            "readings": [
                {
                    "at": iso(r.recorded_at),
                    "temperature": display_temperature(r.temperature_fahrenheit, unit),
                    "humidity": r.humidity_percent,
                    "breached": r.breached,
                }
                for r in window
            ],
            # For the verifier: the exact fields the chain was computed
            # over, in the unit it was computed in. The two lists are the
            # same readings; the human one is converted and rounded, and
            # posting that to the verifier would never reproduce the head.
            # Keeping both is the only way the packet's own instructions
            # can be followed literally and still work.
            "verifiable_readings": [
                {
                    "sensor_id": r.sensor_id,
                    "at": iso(r.recorded_at),
                    "temperature_fahrenheit": r.temperature_fahrenheit,
                    "humidity_percent": r.humidity_percent,
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
                "detail": restate_temperatures(i.breach_details, unit),
            }
            for i in prior
        ],
        "attestation": attestation,
        "signing": signing_state(),
        # Shown under "For the adjuster's IT team" at the end (3.33.0).
        "verification": (
            "The readings above are chained: each digest covers its reading "
            "and the digest before it. To confirm independently that nothing "
            "was altered after the event, POST the `evidence."
            "verifiable_readings` list — not the display list, which is "
            "converted and rounded — to /api/vault/verify together with "
            "`attestation.chain_head`. That endpoint needs no account and "
            "reads nothing: it only re-derives the digests from what you "
            "send it."
        ),
        "verification_heading": "For the adjuster's IT team",
        # The dates the cover letter writes out, in the reader's clock.
        "clock": getattr(zone, "key", None) or "UTC",
    }


def _parsed(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def cover_facts(packet: Dict[str, Any], zone=timezone.utc) -> Dict[str, str]:
    """The cover letter's facts as sentences' parts, dates in words."""
    event = packet["event"]
    evidence = packet["evidence"]
    food = packet.get("food") or {}
    unit = event["temperature_unit"]

    def at(value: Optional[str]) -> str:
        return when_words(_parsed(value), zone)

    if event["state"] == "resolved":
        closed = (f"The alert closed at {at(event.get('resolved_at'))}"
                  + (", when the unit came back in range by itself." if event.get("closed_by_itself") else "."))
    elif event["state"] == "acknowledged":
        closed = "The alert was still being dealt with when this pack was issued."
    else:
        closed = "The alert was still open when this pack was issued."
    ack = event["minutes_to_acknowledge"]
    answered = ("" if ack is None else
                f" and acknowledged {_minutes(ack)} after detection")
    if food.get("answer"):
        who = food.get("by")
        said = (f"The food was recorded as \u201c{food['answer']}\u201d"
                + (f" by {who}" if who else "")
                + (f" at {at(food.get('at'))}" if food.get("at") else "") + ".")
        if food.get("note"):
            note = food["note"].strip()
            said += f" The note of what went: \u201c{note}\u201d" + ("" if note.endswith((".", "!", "?")) else ".")
    else:
        said = "Nobody had recorded what happened to the food when this pack was issued."
    peak = evidence.get("peak_reading")
    return {
        "started": at(event.get("out_since") or event["opened_at"]),
        "opened": at(event["opened_at"]),
        "window": f"{at(evidence['window_start'])} to {at(evidence['window_end'])}",
        "readings": plural(evidence["readings_in_window"], "reading"),
        "outside": _outside(evidence["readings_in_window"], evidence["excursions_in_window"]),
        "peak": f"{peak}°{unit}" if peak is not None else "",
        "answered": answered,
        "closed": closed,
        "food": said,
    }


def _outside(readings: int, out: int) -> str:
    """"3 of them were outside", "it was outside", "none of them were"."""
    if readings == 1:
        return "it was outside" if out else "it was not outside"
    if not out:
        return "none of them were outside"
    return f"{out:,} of them {'was' if out == 1 else 'were'} outside"


def _minutes(value: float) -> str:
    whole = round(value)
    if value < 1:
        return "under a minute"
    return plural(int(whole), "minute")


def build_cover_letter(tenant: Tenant, packet: Dict[str, Any], zone=timezone.utc) -> tuple[str, str]:
    """A plain covering summary for the adjuster, returning (text, source).

    Dates in words in the reader's clock, plurals right, "closed at ..."
    once the alert closed (never "remains open" for one that did), and
    what happened to the food with the note of what went (3.33.0)."""
    event = packet["event"]
    evidence = packet["evidence"]
    location = packet["location"]
    facts = cover_facts(packet, zone)
    unit_name = location.get("asset_label") or location["asset"]

    fallback = (
        f"{tenant.company_name} reports that {unit_name}"
        + (f" at {location['site']}" if location["site"] else "")
        + f" went out of its safe range at {facts['started']}. "
        + (f"Continuous monitoring recorded {facts['readings']} from {facts['window']}; "
           f"{facts['outside']} the documented safe band"
           + (f", the worst {facts['peak']}" if facts["peak"] else "")
           if evidence["readings_in_window"] else
           f"No readings were kept from {facts['window']}")
        + f". The probable cause is {event['probable_cause'].lower()}. The alert "
        f"was sent automatically at detection{facts['answered']}. "
        + facts["closed"] + " " + facts["food"]
        + " All readings are hash-chained and independently verifiable."
    )

    prompt = f"""
    You are writing the covering letter that sits at the front of an
    insurance claim evidence packet. The reader is a loss adjuster.

    Insured: {tenant.company_name}
    Location: {unit_name} at {location['site'] or 'unspecified site'}
    Sector: {location['industry']}
    Went out of range: {facts['started']}
    Alert opened: {facts['opened']}
    Evidence window: {facts['window']}
    Reading at detection: {event['detected_reading']}°{event['temperature_unit']}
    Breach detail: {event['breach_details']}
    Probable cause: {event['probable_cause']}
    Readings in window: {evidence['readings_in_window']}
    Excursions in window: {evidence['excursions_in_window']}
    Minutes to acknowledge: {event['minutes_to_acknowledge']}
    How it ended: {facts['closed']}
    The food: {facts['food']}
    Prior incidents on this asset in 90 days: {len(packet['prior_incidents_90_days'])}

    Write three short paragraphs of plain prose: what happened, how it was
    detected and responded to and what happened to the food, and what
    evidence is attached. State only the figures given above, and write
    dates exactly as given. Do not estimate the value of the loss, do not
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

    def unit_of(sensor_id: str) -> str:
        # The unit's own name ("Walk-in fridge", 3.33.0), never its sensor
        # id, unless the sensor has gone or belongs to somebody else now.
        sensor = STORE.get_sensor(sensor_id)
        if sensor is None or sensor.tenant_id != tenant.tenant_id:
            return sensor_id
        return (sensor.location_name or "").strip() or sensor_id

    return {
        "count": len(incidents),
        "period_days": days,
        "incidents": [
            {
                "incident_id": i.incident_id,
                "sensor_id": i.sensor_id,
                "unit": unit_of(i.sensor_id),
                "opened_at": iso(i.opened_at),
                "catastrophe": i.catastrophe,
                "food": i.food_words(),
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
    tz: Optional[str] = Query(
        None, max_length=64,
        description="The reader's IANA time zone (America/New_York), for the dates written in words. UTC if absent or unknown.",
    ),
    tenant: Tenant = Depends(require_tenant),
):
    """Assemble the evidence packet for one incident."""
    incident = _load_incident(incident_id, tenant)
    zone = reader_zone(tz)
    packet = build_packet(tenant, incident, zone)

    if cover_letter:
        text, source = build_cover_letter(tenant, packet, zone)
        packet["cover_letter"] = text
        packet["cover_letter_source"] = source

    logger.info(
        "Claim packet assembled: incident=%s tenant=%s readings=%d",
        incident.incident_id,
        tenant.tenant_id,
        packet["evidence"]["readings_in_window"],
    )
    return packet
