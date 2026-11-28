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

from brand import APP_NAME, APP_SHORT, APP_URL, COMPANY_NAME
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from auth import require_role_or_machine, require_tenant
from gemini import safe_generate
from records import person_name, renamed
from store import (
    INDUSTRY_PROFILES,
    STORE,
    Incident,
    Tenant,
    display_temperature,
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


def clock_words(moment: Optional[datetime], zone=timezone.utc, day: Optional[datetime] = None) -> str:
    """"2:18 AM" when `moment` falls on the same day as `day` in the
    reader's clock, else the whole of when_words (3.36.0): the summary
    writes the date and the zone once, on when it went out of range, and
    the times that follow that night as a person would."""
    if moment is None:
        return "not recorded"
    if day is None:
        return when_words(moment, zone)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if day.tzinfo is None:
        day = day.replace(tzinfo=timezone.utc)
    local, ref = moment.astimezone(zone), day.astimezone(zone)
    if local.date() != ref.date():
        return when_words(moment, zone)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def pack_temperature(fahrenheit: Optional[float], unit: str) -> str:
    """A reading on the pack, always with one decimal: "52.0°F", never
    "52°F" beside "52.4°F" (3.36.0)."""
    value = display_temperature(fahrenheit, unit)
    if value is None:
        return "not reported"
    return f"{float(value):.1f}°{unit}"


def plural(n: int, one: str, many: Optional[str] = None) -> str:
    """"1 reading", "6 readings": the count and the noun that goes with it."""
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def full_name(label: Optional[str]) -> Optional[str]:
    """A person's whole name from an audit label, without the address:
    "Maya Okafor <maya@...>" is "Maya Okafor" on a document an adjuster
    reads. A machine's label is left as it is."""
    if not label:
        return None
    text = renamed(str(label).split("<", 1)[0].strip())
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
        return f"{pack_temperature(first_f, unit)}: too warm, over the {plain_temperature(above, unit)} limit."
    if below is not None and first_f < below:
        return f"{pack_temperature(first_f, unit)}: too cold, under the {plain_temperature(below, unit)} limit."
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
            "by": (f"{APP_SHORT}, automatically" if row.get("auto")
                   else full_name(row.get("by"))),
            "at": row.get("at"),
        })
    auto = incident.food_auto_minutes is not None
    return {
        "answer": incident.food_words(),
        "disposition": incident.product_disposition,
        "thrown": incident.product_disposition == "discarded",
        "by": ((f"{APP_SHORT}, automatically" if auto else full_name(incident.food_answered_by))
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


# What each try at a text or a call came to, in words (3.35.0): the pack
# said "sms / not_configured" to a loss adjuster.
CHANNEL_WORDS = {"sms": "Text", "voice": "Phone call", "email": "Email"}
OUTCOME_WORDS = {
    "not_configured": "not switched on",
    "budget_exceeded": "not sent: the day's limit was reached",
    "call_failed": "the call did not connect",
    "send_failed": "the phone network refused it",
    "failed": "did not go through",
    "undelivered": "did not arrive",
    "blocked": "that number is blocked",
    "invalid_number": "the number looks wrong",
    "no-answer": "not answered",
    "no_answer": "not answered",
    "busy": "the line was busy",
}


def channel_words(channel: Optional[str]) -> str:
    """"Text", "Phone call": never "sms"."""
    key = str(channel or "").strip().lower()
    return CHANNEL_WORDS.get(key, key.replace("_", " ").capitalize() or "Message")


def outcome_words(delivered: bool, status_code: Optional[str], channel: Optional[str] = None) -> str:
    """"delivered", "not switched on": never "not_configured"."""
    if delivered:
        return "delivered"
    key = str(status_code or "").strip().lower()
    if not key:
        return "not sent"
    return OUTCOME_WORDS.get(key, key.replace("_", " "))


def _delivery_rows(fanout: List[Dict[str, Any]], single: Optional[Dict[str, Any]]):
    """Delivery attempts as an adjuster needs to read them: the codes, and
    the same in words ("Text", "not switched on") for the page."""
    rows = fanout or ([single] if single else [])
    return [
        {
            "channel": row.get("channel"),
            "channel_words": channel_words(row.get("channel")),
            "outcome_words": outcome_words(bool(row.get("delivered")), row.get("status")),
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


# Where an adjuster's IT team reads how to check the chain (3.35.0): the
# public verifier's entry in the API reference, on the product's address.
VERIFY_DOCS_URL = f"{APP_URL}/developers#post-api-vault-verify"

# What a missing humidity was stored as before 3.35.0.
OLD_HUMIDITY_STAND_IN = 50.0


def humidity_column(window) -> Dict[str, Any]:
    """Whether the pack shows humidity, and why not (3.35.0).

    {"shown", "placeholder", "note"}. Hidden when no reading in the window
    has a humidity. Before 3.35.0 a sensor that sent none was stored as
    50% (db.humidity_optional_since says until when). Those rows are sealed
    and stay exactly as stored, in the verifiable list too; where every one
    of them in the window says exactly 50%, the page shows "—" for them
    and says why, rather than present a figure nobody may have measured.
    """
    from datetime import datetime as _dt

    since = None
    try:
        raw = STORE._db.humidity_optional_since()
        since = _dt.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
    except Exception:  # noqa: BLE001 - no marker: nothing is treated as a stand-in
        since = None
    before = [r for r in window if r.humidity_percent is not None
              and since is not None and r.recorded_at < since]
    after = [r for r in window if r.humidity_percent is not None
             and (since is None or r.recorded_at >= since)]
    placeholder = bool(before) and not after and all(
        r.humidity_percent == OLD_HUMIDITY_STAND_IN for r in before)
    if placeholder:
        return {
            "shown": False, "placeholder": True,
            "note": ("Humidity: not shown. These readings were stored with 50% humidity, which "
                     "is what was written down when a sensor sent none, so it was probably not "
                     "measured. The sealed record keeps the value as it was stored."),
        }
    shown = bool(before or after)
    return {"shown": shown, "placeholder": False,
            "note": None if shown else "Humidity: none was sent with these readings."}


def _notified(incident: Incident, sensor) -> Dict[str, Any]:
    """Who was told, when and whether it arrived, from the alert's own
    delivery record (3.36.0). The summary said "The alert was sent
    automatically at detection" whatever happened, on a server with texts
    switched off too.

    texts: "off" (written, not sent: no text service), "none" (nobody to
    text), "sent" (tried: `delivered` of `texted` arrived). The wait is
    the unit's own rule when it alerted after it ("after the 20-minute
    wait"), None when it alerted at once."""
    from domain import alert_after_minutes

    first = list(incident.sms_fanout) or ([incident.sms_delivery] if incident.sms_delivery else [])
    first = [r for r in first if r]
    if incident.texts_off:
        texts = "off"
    elif not first:
        texts = "none"
    else:
        texts = "sent"
    out_before = max(0.0, (incident.opened_at - incident.started_at).total_seconds() / 60.0)
    rule = alert_after_minutes(sensor) if sensor is not None else None
    if rule and out_before >= rule - 0.5:
        wait: Optional[float] = float(rule)
    else:
        wait = None
    retries = []
    for attempt in incident.sms_retries:
        rows = [r for r in (attempt.get("deliveries") or []) if r]
        retries.append({"at": attempt.get("at"), "texted": len(rows),
                        "delivered": sum(1 for r in rows if r.get("delivered"))})
    call = None
    if incident.voice_escalated_at:
        receipts = [a for a in (list(incident.voice_fanout) or [incident.voice_delivery]) if a]
        reached = next((a for a in receipts if a.get("delivered")), None)
        call = {
            "at": iso(incident.voice_escalated_at),
            "reached": bool(reached),
            "off": incident.calls_off,
            "tried": bool(receipts),
        }
    return {
        "texts": texts,
        "at": iso(incident.opened_at),
        "texted": len(first),
        "delivered": sum(1 for r in first if r.get("delivered")),
        "wait_minutes": wait,
        "out_minutes_before": round(out_before, 1),
        "retries": retries,
        "call": call,
    }


def told_words(packet: Dict[str, Any], zone=timezone.utc, day: Optional[datetime] = None) -> str:
    """How people were told, in a sentence or two, true to the delivery
    record (3.36.0): "The alert was texted to 2 people at 2:18 AM, after
    the 20-minute wait; it was acknowledged 12 minutes later." Texts
    switched off say "written but not delivered: texts are off"; texts
    that failed say they failed."""
    event = packet["event"]
    told = (packet.get("response") or {}).get("notified")
    ack = event.get("minutes_to_acknowledge")

    def at(value):
        return clock_words(_parsed(value), zone, day)

    acked = None if ack is None else f"acknowledged {_minutes(ack)} later"
    if not told:  # a pack built before 3.36.0's record: what is known
        return (f"The alert opened at {at(event['opened_at'])}"
                + (f"; it was {acked}." if acked else "."))
    when = at(told["at"])
    wait = told.get("wait_minutes")
    after = (f", after the {wait:g}-minute wait" if wait
             else "" if (told.get("out_minutes_before") or 0) < 1
             else f", {_minutes(told['out_minutes_before'])} after it went out of range")
    if told["texts"] == "off":
        said = (f"The alert text was written at {when}{after}{',' if after else ''} but not delivered: "
                "texts are off on this server, so nobody was texted.")
    elif told["texts"] == "none":
        said = f"No alert text went out at {when}: nobody was on the list to text."
    else:
        n, landed = told["texted"], told["delivered"]
        people = plural(n, "person", "people")
        if landed == n:
            said = f"The alert was texted to {people} at {when}{after}"
        elif landed:
            said = (f"The alert was texted to {people} at {when}{after}; "
                    f"{landed} went through and {n - landed} failed")
        else:
            said = f"The alert was texted to {people} at {when}{after}{',' if after else ''} and failed: none went through"
        for again in told.get("retries") or []:
            said += (f". It was sent again at {at(again.get('at'))}: "
                     + (f"{again['delivered']} of {again['texted']} went through" if again["delivered"]
                        else "it failed again"))
        said += "."
    call = told.get("call")
    if call:
        if call["reached"]:
            said += f" A phone call got through at {at(call['at'])}."
        elif call["off"]:
            said += f" The call step came at {at(call['at'])}, but calls are off on this server, so no call was placed."
        elif call["tried"]:
            said += f" A phone call was tried at {at(call['at'])} and did not go through."
    if acked:
        if (said.endswith(".") and told["texts"] == "sent" and told["delivered"] == told["texted"]
                and not told.get("retries") and not call):
            said = said[:-1] + f"; it was {acked}."
        else:
            said += f" It was {acked.replace('later', 'after the alert opened')}."
    return said


def closed_words(incident: Incident, unit: str, zone=timezone.utc) -> str:
    """How the alert closed, for the pack's timeline (3.37.0).

    Closed by the reading coming back after somebody said it was fixed:
    "Rosa Diaz said it was fixed at 4:56 AM; it closed by itself at 4:57
    AM, back to 38.2°F." Closed by itself with nobody's word: "Back in
    range by itself." Closed by a person: "Closed by Rosa Diaz." The
    store keeps the person who said it was fixed as the one who closed
    it, for the inspector's log; the pack says what happened."""
    if incident.resolved_at is None:
        return ""
    back = (f", back to {pack_temperature(incident.close_temperature_f, unit)}"
            if incident.close_temperature_f is not None else "")
    if incident.resolved_note:
        if incident.fix_reported_at:
            who = full_name(incident.fix_reported_by) or "Somebody"
            said = clock_words(incident.fix_reported_at, zone, incident.resolved_at)
            closed = clock_words(incident.resolved_at, zone, incident.resolved_at)
            return f"{who} said it was fixed at {said}; it closed by itself at {closed}{back}."
        return f"Back in range by itself{back}."
    if incident.resolved_by:
        return f"Closed by {full_name(incident.resolved_by)}."
    return ""


def unit_phrase(label: Optional[str]) -> str:
    """A unit as a sentence says it (3.37.0): "the Walk-in fridge", never
    "reports that Walk-in fridge went"."""
    name = (label or "").strip() or "unit"
    return name if name.lower().startswith(("the ", "our ", "my ")) else f"the {name}"


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
    # From the first reading, never before it (3.35.0): a window that
    # opened twelve hours before a sensor added an hour earlier said the
    # unit was watched when nothing was there to watch it. The same
    # readings either way, so the chain head below covers exactly these.
    if window and window[0].recorded_at > start:
        start = window[0].recorded_at
    excursions = [r for r in window if r.breached]
    humidity = humidity_column(window)

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
            "detail": pack_temperature(incident.peak_f, unit),
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
            "at": iso(incident.resolved_at),
            "event": "Closed by itself" if incident.resolved_note else "Closed",
            "detail": closed_words(incident, unit, zone),
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

    notified = _notified(incident, sensor)

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
            # The site's own address, else the one the business gave for
            # its packs (3.35.0); None means nobody has said, and the page
            # asks for it.
            "address": ((site.address if site else "") or tenant.site_address or "").strip() or None,
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
            # Closed by the reading coming back, never by a person: also
            # after somebody said "It's fixed" while it was still warm, when
            # their name is kept on it for the log (3.37.0: the pack said
            # "Closed by Rosa" for an alert that closed by itself).
            "closed_by_itself": bool(incident.resolved_at and incident.resolved_note),
            "fix_said_by": (full_name(incident.fix_reported_by)
                            if incident.fix_reported_at and incident.resolved_note else None),
            "fix_said_at": (iso(incident.fix_reported_at)
                            if incident.fix_reported_at and incident.resolved_note else None),
            "closed_at_reading": (pack_temperature(incident.close_temperature_f, unit)
                                  if incident.resolved_note and incident.close_temperature_f is not None
                                  else None),
        },
        # What happened to the food (3.33.0), the part of the record an
        # adjuster weighs a loss by: the answer, who and when, the note of
        # what went, and every change.
        "food": food_record(incident),
        "response": {
            # How people were told, as it happened (3.36.0), for the
            # summary: "texted to 2 people at 2:18 AM, after the 20-minute
            # wait", or "written but not delivered: texts are off".
            "notified": notified,
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
                    # None where nothing was measured (3.35.0), and where
                    # the stored 50% was the old stand-in (humidity_column).
                    "humidity": None if humidity["placeholder"] else r.humidity_percent,
                    "breached": r.breached,
                }
                for r in window
            ],
            # Whether the page shows a humidity column at all, and why not
            # (3.35.0): hidden when no reading has one.
            "humidity_shown": humidity["shown"],
            "humidity_note": humidity["note"],
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
        # Shown under "For the adjuster's IT team" at the end (3.33.0), cut
        # to the chain head and where the check is explained (3.35.0).
        "verification": (
            "Every reading in this pack is sealed in a hash chain, so a "
            "change made after the event would show. The chain head below "
            "is what your IT team checks against; how to check it, with no "
            "account needed, is at the address below."
        ),
        "verification_url": VERIFY_DOCS_URL,
        "verification_heading": "For the adjuster's IT team",
        # Who issued it (3.35.0), from brand.py: the header of every page.
        "issuer": {"product": APP_NAME, "company": COMPANY_NAME, "url": APP_URL},
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

    # The night's later times without the date again (3.36.0).
    day = _parsed(event.get("out_since") or event["opened_at"])

    def later(value: Optional[str]) -> str:
        return clock_words(_parsed(value), zone, day)

    if event["state"] == "resolved":
        back = f", back to {event['closed_at_reading']}" if event.get("closed_at_reading") else ""
        if event.get("closed_by_itself") and event.get("fix_said_at"):
            closed = (f"{event.get('fix_said_by') or 'Somebody'} said it was fixed at {later(event['fix_said_at'])}; "
                      f"the alert closed by itself at {later(event.get('resolved_at'))}{back}.")
        else:
            closed = (f"The alert closed at {later(event.get('resolved_at'))}"
                      + (f", when the unit came back in range by itself{back}." if event.get("closed_by_itself") else "."))
    elif event["state"] == "acknowledged":
        closed = "The alert was still being dealt with when this pack was issued."
    else:
        closed = "The alert was still open when this pack was issued."
    ack = event["minutes_to_acknowledge"]
    answered = ("" if ack is None else
                f" and acknowledged {_minutes(ack)} after detection")
    note = (food.get("note") or "").strip()
    stop = "" if note.endswith((".", "!", "?")) else "."
    if food.get("answer") and note and note_repeats(food["answer"], note):
        # The note already starts "Thrown away" (3.37.0): said once, in
        # the note's own words, never "recorded as “Thrown away”. The note
        # of what went: “Thrown away from ...”".
        who = food.get("by") or "Somebody"
        said = (f"{who} recorded"
                + (f" at {later(food.get('at'))}" if food.get("at") else "")
                + f": \u201c{note}{stop}\u201d")
    elif food.get("answer"):
        who = food.get("by")
        said = (f"The food was recorded as \u201c{food['answer']}\u201d"
                + (f" by {who}" if who else "")
                + (f" at {later(food.get('at'))}" if food.get("at") else "") + ".")
        if note:
            # "of what went" only for food that went (3.37.0): a note on
            # food kept is just the note.
            said += (f" The note of what went: \u201c{note}\u201d" if food.get("thrown")
                     else f" The note: \u201c{note}\u201d") + stop
    else:
        said = "Nobody had recorded what happened to the food when this pack was issued."
    peak = evidence.get("peak_reading")
    return {
        "started": at(event.get("out_since") or event["opened_at"]),
        "opened": at(event["opened_at"]),
        "window": f"{at(evidence['window_start'])} to {at(evidence['window_end'])}",
        "readings": plural(evidence["readings_in_window"], "reading"),
        "outside": _outside(evidence["readings_in_window"], evidence["excursions_in_window"]),
        "peak": f"{float(peak):.1f}°{unit}" if peak is not None else "",
        "answered": answered,
        "told": told_words(packet, zone, day),
        "closed": closed,
        "food": said,
    }


def note_repeats(answer: Optional[str], note: Optional[str]) -> bool:
    """Whether a food note starts with its own answer ("Thrown away from
    the walk-in: ..." under "Thrown away"), so a page says it once."""
    def flat(text: Optional[str]) -> str:
        return " ".join("".join(ch if ch.isalnum() else " " for ch in (text or "").lower()).split())

    a, n = flat(answer), flat(note)
    return bool(a) and (n == a or n.startswith(a + " "))


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
    # "Old Mill / Walk-in freezer at Juniper Old Mill" said the site twice
    # (3.37.0): where the site is named, the unit's own part of its name.
    if location.get("site") and "/" in unit_name:
        unit_name = unit_name.rsplit("/", 1)[1].strip() or unit_name
    # The site's name and its address, or the address alone where the
    # site has no name (3.36.0): the saved address was on the pack but
    # never in its summary.
    where = ", ".join(x for x in (location.get("site"), location.get("address")) if x)

    # Three short paragraphs (3.37.0), as the drafted letter is asked for:
    # what happened, how people were told and what became of the food,
    # and the evidence. It was one block of nine sentences.
    fallback = "\n\n".join([
        f"{tenant.company_name} reports that {unit_phrase(unit_name)}"
        + (f" at {where}" if where else "")
        + f" went out of its safe range at {facts['started']}. "
        + f"The probable cause is {event['probable_cause'].lower()}.",
        f"{facts['told']} {facts['closed']} {facts['food']}",
        (f"Continuous monitoring recorded {facts['readings']} from {facts['window']}; "
         f"{facts['outside']} the documented safe band"
         + (f", the worst {facts['peak']}" if facts["peak"] else "") + "."
         if evidence["readings_in_window"] else
         f"No readings were kept from {facts['window']}.")
        + " All readings are hash-chained and independently verifiable.",
    ])

    prompt = f"""
    You are writing the covering letter that sits at the front of an
    insurance claim evidence packet. The reader is a loss adjuster.

    Insured: {tenant.company_name}
    Location: {unit_name} at {where or 'unspecified site'}
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
    How people were told: {facts['told']}
    How it ended: {facts['closed']}
    The food: {facts['food']}
    Prior incidents on this asset in 90 days: {len(packet['prior_incidents_90_days'])}

    Write three short paragraphs of plain prose: what happened, how it was
    detected and responded to and what happened to the food, and what
    evidence is attached. State only the figures given above, and write
    dates exactly as given. Say how people were told exactly as given:
    never say a text or a call went out, or arrived, unless it says so. Do not estimate the value of the loss, do not
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


class SiteAddress(BaseModel):
    address: str = Field("", max_length=300, description="The street address an insurance pack shows. Empty clears it.")


@router.post("/site-address")
def set_site_address(
    payload: SiteAddress,
    tenant: Tenant = Depends(require_tenant),
    user=Depends(require_role_or_machine("operator")),
):
    """The address an insurance pack shows for this business (3.35.0).

    Optional: asked for in the Insurance packs card and on a pack that has
    none ("Site: not recorded" → "Add the site address"). A location's
    own address, where one is set, comes first.
    """
    from auth import write_audit

    address = " ".join((payload.address or "").split())
    STORE.set_site_address(tenant, address)
    write_audit(tenant, user, "business.site_address",
                "Site address set." if address else "Site address cleared.")
    return {"address": address or None}
