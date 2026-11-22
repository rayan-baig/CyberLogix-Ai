"""AI outbound voice escalation.

The second rung of the alerting ladder. An SMS that goes unacknowledged
past the grace window means nobody is reading their phone, so the incident
escalates to a spoken phone call: Gemini writes a call script in natural
speech and the response hands it to a telephony provider to place.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from typing import Dict, Optional
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

import usage
from gemini import safe_generate
from auth import (
    actor_label,
    optional_operator,
    require_tenant,
    write_audit,
    require_role_or_machine,
)
from notifications import (
    acknowledgement_url,
    build_gather_reply,
    build_sms_reply,
    place_voice_call,
    same_phone,
    sms_segments,
    verify_twilio_signature,
)
import wording
from domain import minutes_words, plain_temperature
from store import (
    STORE,
    VOICE_ESCALATION_GRACE_MINUTES,
    VOICE_REDIAL_COOLDOWN_MINUTES,
    Incident,
    Tenant,
    User,
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
    note: Optional[str] = Field(
        None, max_length=200,
        description=(
            "One line on what was done. Required with close_anyway; kept "
            "with who closed it, for the inspector's log."
        ),
    )
    close_anyway: bool = Field(
        False,
        description=(
            "Close it although the unit is still out of range. Needs a note. "
            "Without it, an alert on a unit still out of range is not "
            "closed: it closes by itself once the unit is back to normal."
        ),
    )


def _load_incident(incident_id: str, tenant: Tenant) -> Incident:
    incident = STORE.get_incident(incident_id)
    if incident is None or incident.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident '{incident_id}' not found for this tenant.",
        )
    return incident


def build_voice_script(incident: Incident, tenant: Tenant,
                       can_press: bool = True) -> tuple[str, str]:
    """The spoken escalation script, returning (script, source).

    Short, because Twilio reads it twice: who is calling, which unit and
    where, how far out of range and for how long, what to check, and
    "press 1". wording.py puts the facts together; the model words only
    the line saying what to check, and is never trusted with the reading
    or with how to stop the calls. `can_press` is False when the call has
    nowhere to send a keypress, so it does not promise one.
    """
    import wording

    if incident.site_wide:
        # No model line here: the words are fixed, and say the same to
        # everybody on the list.
        return (wording.site_wide_voice_script(tenant, incident, can_press),
                "site_wide_template")
    sensor = STORE.get_sensor(incident.sensor_id)
    if sensor is None:  # pragma: no cover - a retired unit keeps its incidents
        location = "the monitored unit"
        return (
            f"This is CyberLogix AI with a temperature alarm for "
            f"{tenant.company_name}. {location} is at "
            f"{spoken_temperature(incident.temperature_fahrenheit, tenant.temperature_unit)}. "
            "Please check it now.", "fallback_template")
    unit = tenant.temperature_unit
    facts = wording.breach_facts(sensor, incident.temperature_fahrenheit, unit)
    line, source = safe_generate(
        wording.action_prompt(sensor, facts, spoken=True),
        wording.action_for(sensor),
        purpose="voice escalation script",
        tenant_id=tenant.tenant_id,
        business_key=True,
    )
    action = wording.clean_action(line)
    if action is None:
        action, source = wording.action_for(sensor), "fallback_template"
    return (wording.voice_script(tenant, sensor, incident, unit, action, can_press),
            source)


def _notify_hooks(
    tenant: Tenant, incident: Incident, state: str, note: str = "",
    background: bool = False,
) -> None:
    """Tell the chat channels and the rotation what changed.

    `background` posts from a worker and returns at once: what the sweep
    asks for, so a slow channel never holds up the next escalation.

    Imported inside the call: webhooks imports the store and auth helpers,
    and a module-level import would close the cycle.
    """
    from webhooks import dispatch_event, dispatch_event_later

    send = dispatch_event_later if background else dispatch_event
    sensor = STORE.get_sensor(incident.sensor_id)
    if incident.site_wide and incident.group_sensor_ids:
        # The channels covering the first unit's site, and the estate's.
        sensor = STORE.get_sensor(incident.group_sensor_ids[0])
    send(tenant, incident, sensor, state, note)


def dispatch_voice_call(incident: Incident, tenant: Tenant,
                        hooks_in_background: bool = False) -> dict:
    """Draft the script, place the call and stamp the incident.

    Shared by the manual escalate endpoint and the autopilot sweep so both
    routes record delivery identically. The sweep passes
    `hooks_in_background`, so the copy into Slack, Teams or PagerDuty goes
    out without the sweep waiting on it.
    """
    # So the script's closing "press 1 to acknowledge" reaches something --
    # and when nothing can receive the keypress, the script does not say it.
    action_url = acknowledgement_url(
        incident.incident_id, STORE.issue_ack_token(incident)
    )
    script, source = build_voice_script(
        incident, tenant, can_press=action_url is not None)

    # Walk the escalation ladder: stop at the first person actually reached,
    # so a wrong number or a dead line does not end the escalation.
    #
    # A site-wide alert rings everybody on it instead, one after another,
    # each number once: it is meant to reach the whole on-call list and the
    # owner. It stops early only if somebody acknowledges in the meantime.
    ladder = STORE.alert_ladder(tenant, incident)
    fanout = []
    delivery = None
    for contact in ladder:
        if incident.site_wide and fanout and incident.acknowledged_at is not None:
            break
        attempt = dict(
            place_voice_call(contact.phone, script, tenant.tenant_id, action_url),
            contact_id=contact.contact_id,
            contact_name=contact.full_name,
        )
        fanout.append(attempt)
        if delivery is None or not delivery.get("delivered"):
            delivery = attempt
        if attempt["delivered"] and not incident.site_wide:
            break

    STORE.record_voice_escalation(incident, script, source, delivery, fanout)

    _notify_hooks(
        tenant,
        incident,
        "escalated",
        ("Site-wide alert: calling everyone on call now."
         if incident.site_wide else
         f"No acknowledgement after {int(incident.minutes_open())} minutes; "
         "a voice call has been placed."),
        background=hooks_in_background,
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


def _signed_url(request: Request) -> str:
    """The address Twilio signed: the one we gave it.

    Twilio signs the URL it was told to call, which acknowledgement_url()
    builds from PUBLIC_BASE_URL. Behind a proxy that ends TLS the app sees
    http:// and its own host, so checking against request.url refused every
    genuine keypress and the escalation kept phoning people who had already
    pressed 1.
    """
    import notifications

    if notifications.PUBLIC_BASE_URL:
        url = notifications.PUBLIC_BASE_URL + request.url.path
        return url + (f"?{request.url.query}" if request.url.query else "")
    return str(request.url)


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
    if not verify_twilio_signature(_signed_url(request), form, request.headers.get("X-Twilio-Signature", "")):
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
    # `claimed` comes back from the store rather than being read off the
    # incident beforehand: read separately, two handsets pressing 1 in the
    # same second both saw None and both counted as the first responder.
    incident, claimed = STORE.acknowledge_incident(
        incident, f"phone keypad ({caller})"
    )
    if claimed:
        usage.count("acknowledged_keypress")
    tenant = STORE.get_tenant(incident.tenant_id)
    if tenant is not None and claimed:
        write_audit(
            tenant,
            None,
            "incident.acknowledged",
            f"{incident.incident_id} acknowledged by keypad from {caller}.",
        )
    if tenant is not None and claimed:
        # On a worker thread, not here. This route has to be a coroutine
        # because it awaits the request body, so a blocking call in its
        # body sits directly on the event loop and stops the whole
        # process. The fan-out is a courtesy copy into Slack; the person
        # standing at the freezer has already pressed 1, and Twilio gives
        # up on this callback after fifteen seconds. Making them wait on
        # somebody's chat integration risks the acknowledgement itself
        # timing out — and an unacknowledged incident gets called again.
        await asyncio.to_thread(
            _notify_hooks,
            tenant,
            incident,
            "acknowledged",
            f"Acknowledged from {caller}.",
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


# ==============================================================================
#  Acknowledging by text
#
#  The alert text ends "Reply 1 if you are on it", and the reply is the
#  quickest way there is to stop the ladder: no sign-in, no app, one key.
#  Twilio posts the reply here (set this address as the "A message comes
#  in" webhook on the sending number). It counts only when:
#
#    * Twilio signed it -- the same check as the keypress -- so nobody can
#      post a forged "1" from a number they have guessed;
#    * the number that sent it is one the alert went to: on that business's
#      roster for that unit's site (or the business's own number when it
#      has no roster), active, and receiving texts or calls;
#    * and the alert is still that business's, and still open.
#
#  Nothing else is touched. A number on nobody's roster gets no reply at
#  all -- not even confirmation that the number is ours.
# ==============================================================================

_ACK_REPLY = re.compile(
    r"^(?:1|ACK|ACKNOWLEDGE[D]?|OK|OKAY|YES|ON IT)"
    r"(?:\s*#?\s*(?:INC)?-?\s*0*(\d{1,9}))?$"
)


def _ack_words(body: str) -> tuple[bool, Optional[int]]:
    """Whether a reply acknowledges, and the alert number it names if any."""
    text = " ".join(re.sub(r"[^A-Za-z0-9#\- ]", " ", body or "").upper().split())
    found = _ACK_REPLY.match(text)
    if not found:
        return False, None
    return True, int(found.group(1)) if found.group(1) else None


def _alert_number(incident: Incident) -> int:
    """INC-12-QHKX (or, from before 3.19.0, INC-000012) -> 12: what a
    person types back. The number counts this account's alerts only."""
    found = re.match(r"^[A-Za-z]+-0*(\d+)", incident.incident_id)
    return int(found.group(1)) if found else 0


def _alerted(tenant: Tenant, incident: Incident, phone: str):
    """The roster entry this incident's alert went to at `phone`, if any."""
    # Whoever it was actually sent to, first: with a rotation, the person
    # texted at 07:55 is off shift by the time they reply at 08:02, and
    # their "1" is still the answer to the text they were sent.
    for receipt in list(incident._sms_receipts()) + list(incident.voice_fanout):
        if receipt.get("to") and same_phone(receipt["to"], phone):
            contact = STORE.get_contact(receipt.get("contact_id") or "")
            if contact is not None and contact.tenant_id == tenant.tenant_id and contact.active:
                return contact
    # A site-wide alert also goes to the business's own number; the
    # recipients read afresh include it.
    for contact in (STORE.alert_recipients(tenant, incident)
                    + STORE.alert_ladder(tenant, incident)):
        if contact.phone and same_phone(contact.phone, phone):
            return contact
    return None


def _reply_name(incident: Incident) -> str:
    """What a text reply calls an alert: the unit, or the site-wide alert."""
    if incident.site_wide:
        return "the site-wide alert"
    sensor = STORE.get_sensor(incident.sensor_id)
    return sensor.location_name if sensor and sensor.location_name else incident.sensor_id


def _on_any_roster(phone: str) -> bool:
    for tenant in STORE.list_tenants():
        if same_phone(tenant.contact_phone or "", phone):
            return True
        if any(c.active and same_phone(c.phone, phone)
               for c in STORE.contacts_for(tenant.tenant_id)):
            return True
    return False


def ack_reply_text(who: str, where: str) -> str:
    """The text sent back to whoever replied 1. The public demo shows it too."""
    return (f"CyberLogix: thanks, {who}. {where[:1].upper()}{where[1:]} is "
            "yours. No more calls for this alert.")


def _sms_answer(text: Optional[str], tenant_id: Optional[str] = None,
                status_code: int = status.HTTP_200_OK) -> Response:
    """Reply by TwiML; a reply is a text too, so it is counted as one."""
    if text and tenant_id:
        from costs import record

        record(tenant_id, "sms_sent")
        record(tenant_id, "sms_segments", sms_segments(text))
    return Response(content=build_sms_reply(text), media_type="application/xml",
                    status_code=status_code)


@router.post("/sms-reply", include_in_schema=False)
async def sms_reply(request: Request) -> Response:
    """Twilio posts a reply to an alert text. "1" or "ACK" acknowledges.

    Async because it awaits the body; the store calls are quick and the
    courtesy copy into Slack goes to a worker thread, as for the keypress.
    """
    body = (await request.body()).decode("utf-8", "replace")
    form = dict(parse_qsl(body, keep_blank_values=True))
    if not verify_twilio_signature(
            _signed_url(request), form, request.headers.get("X-Twilio-Signature", "")):
        logger.warning("Rejected an unverified text reply.")
        return _sms_answer(None, status_code=status.HTTP_403_FORBIDDEN)

    phone = (form.get("From") or "").strip()
    acks, number = _ack_words(form.get("Body") or "")

    # Every alert still not closed whose text went to this number, newest
    # first, across every business that has them on a roster.
    mine = []
    for tenant in STORE.list_tenants():
        for incident in STORE.incidents_for(tenant.tenant_id, include_site_wide=True):
            if incident.resolved_at is not None:
                continue
            contact = _alerted(tenant, incident, phone)
            if contact is not None:
                mine.append((tenant, incident, contact))
    mine.sort(key=lambda row: row[1].opened_at, reverse=True)

    if not mine:
        if _on_any_roster(phone):
            return _sms_answer("CyberLogix: there is no open alert for this "
                               "number to acknowledge.")
        logger.info("A text from a number on no roster was ignored.")
        return _sms_answer(None)

    first_tenant = mine[0][0].tenant_id
    if not acks:
        return _sms_answer("CyberLogix: reply 1 to acknowledge the alert and "
                           "stop the calls.", first_tenant)
    if number is not None:
        mine = [row for row in mine if _alert_number(row[1]) == number]
        if not mine:
            return _sms_answer(f"CyberLogix: no open alert {number} was sent to "
                               "this number.", first_tenant)

    waiting = [row for row in mine if row[1].acknowledged_at is None]
    # In a site-wide problem the same number holds the site-wide alert and
    # the alerts of the units in it. A plain "1" is the answer to the
    # site-wide one, and acknowledging it acknowledges those units too, so
    # it does not ask "which one?" in the middle of an emergency.
    site_wide = [row for row in waiting if row[1].site_wide]
    if number is None and len(site_wide) == 1 and all(
            row[1].site_wide or row[1].sensor_id in site_wide[0][1].group_sensor_ids
            for row in waiting):
        waiting = site_wide
    if not waiting:
        _, incident, _ = mine[0]
        return _sms_answer(f"CyberLogix: already acknowledged by "
                           f"{incident.acknowledged_by}.", first_tenant)
    if len(waiting) > 1:
        listed = "; ".join(
            f"{_alert_number(i)} {_reply_name(i)}" for _, i, _ in waiting[:4])
        return _sms_answer(f"CyberLogix: {len(waiting)} alerts are open: {listed}. "
                           f"Reply ACK and the number, e.g. ACK "
                           f"{_alert_number(waiting[0][1])}.", first_tenant)

    tenant, incident, contact = waiting[0]
    who = contact.full_name or "on-call contact"
    incident, claimed = STORE.acknowledge_incident(
        incident, f"{who}, by text reply ({contact.phone})")
    where = _reply_name(incident)
    if not claimed:
        return _sms_answer(f"CyberLogix: already acknowledged by "
                           f"{incident.acknowledged_by}.", tenant.tenant_id)
    usage.count("acknowledged_text")
    write_audit(tenant, None, "incident.acknowledged",
                f"{incident.incident_id} acknowledged by text reply from {who}.",
                fallback_actor=f"{who} (text reply)")
    await asyncio.to_thread(_notify_hooks, tenant, incident, "acknowledged",
                            f"Acknowledged by text by {who}.")
    logger.info("Incident %s acknowledged by text reply.", incident.incident_id)
    return _sms_answer(ack_reply_text(who, where), tenant.tenant_id)


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


def require_calls(tenant: Tenant = Depends(require_tenant)) -> Tenant:
    """A plan with calls, or a free trial with some of its few calls left."""
    from costs import calls_allowed, calls_offer

    if not calls_allowed(tenant):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=calls_offer(tenant)["words"],
        )
    return tenant


@router.post("/escalate/{incident_id}")
def escalate_to_voice(
    incident_id: str,
    force: bool = False,
    tenant: Tenant = Depends(require_calls),
    operator: Optional[User] = Depends(optional_operator),
    # Dialling somebody is an action, not a view.
    _: object = Depends(require_role_or_machine("operator")),
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

    # Claim the call before placing it. Without this, twelve operators
    # hitting escalate at once — or one operator whose click registered
    # twice, or the unattended sweep firing while somebody is already on
    # it — placed twelve calls to the same phone for the same freezer.
    # The sweep has claimed atomically since it was written; this
    # endpoint, the one a human actually presses, never did.
    if not STORE.claim_voice_escalation(
        incident, redial_after_minutes=VOICE_REDIAL_COOLDOWN_MINUTES
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A call for '{incident_id}' has already been placed. It can "
                f"be re-dialled {VOICE_REDIAL_COOLDOWN_MINUTES:.0f} minutes "
                "after the last attempt."
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
    _: object = Depends(require_role_or_machine("operator")),
):
    """Stop the escalation ladder: a human has the incident.

    Operator, not viewer — and the reasoning is worth writing down,
    because the safer-looking choice is the wrong one. Acknowledging does
    not mean "I have seen this". It means "somebody who can deal with
    this has it", and it stops the product waking anyone else. A viewer
    cannot change a threshold, cannot decommission an asset and cannot
    escalate; letting them halt the ladder halts it on behalf of someone
    who is not able to act.

    The counter-argument — that making acknowledgement harder during an
    emergency is its own hazard — is real, and is why the path that
    actually matters at 3am is untouched: the person on call presses 1 on
    the handset, and that callback is authenticated by Twilio's signature
    and a per-incident secret, not by a role. Nobody is ever locked out
    of acknowledging the call they are being woken by.
    """
    incident = _load_incident(incident_id, tenant)

    actor = actor_label(operator, payload.acknowledged_by or "API key")
    incident, claimed = STORE.acknowledge_incident(incident, actor)

    if not claimed:
        # Somebody already has it. Say whose it is rather than implying
        # this caller stopped the ladder — one incident has one owner,
        # and the second person to press the button needs to know it is
        # not them, not be told "acknowledged" and stand down too.
        return {
            "status": "ALREADY_ACKNOWLEDGED",
            "message": (
                f"{incident.acknowledged_by} acknowledged this incident "
                f"first. No second alert was sent."
            ),
            "acknowledged_by": incident.acknowledged_by,
            "incident": incident.public(),
        }

    usage.count("acknowledged_button")
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
    _: object = Depends(require_role_or_machine("operator")),
):
    """Close an incident once the physical fault is fixed.

    The same reasoning as acknowledging, and one more: resolving writes
    into the compliance record. A read-only account's name should not
    appear in a claim packet as the person who closed out a loss.
    """
    incident = _load_incident(incident_id, tenant)

    if incident.resolved_at is not None:
        # Closed already. Telling the channels and the audit trail again
        # posts a second "resolved" into Slack and records a second closer
        # in the trail a claim packet is built from.
        return {
            "status": "ALREADY_RESOLVED",
            "message": "This incident was already closed. Nothing was changed.",
            "incident": incident.public(),
        }

    actor = actor_label(operator, payload.resolved_by or "API key")
    note = (payload.note or "").strip()
    out = _still_out_of_range(incident, tenant)
    if out is not None and not payload.close_anyway:
        # "It's fixed" while the fridge is still 48°F is not fixed. Say so,
        # and keep the promise: it closes by itself once it is back to
        # normal. Whoever pressed it has it, so the calls stop.
        STORE.report_fixed(incident, actor)
        write_audit(tenant, operator, "incident.fix_reported",
                    f"{incident.incident_id} on {incident.sensor_id}: said fixed "
                    f"at {out['temperature']}, still out of range.",
                    fallback_actor=actor)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "still_out_of_range",
                "message": out["words"],
                "temperature": out["temperature"],
                "limit": out["limit"],
                "close_anyway_needs_note": True,
                "reasons": list(wording.CLOSE_ANYWAY_REASONS),
            },
        )
    if out is not None and len(note) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=("Closing it while it is still out of range needs a short "
                    "note: what was done, in a line."),
        )
    if out is None and not note:
        now_at = out_temperature(incident, tenant)
        note = f"Fixed, {now_at} when closed." if now_at else "Fixed."
    sensor = STORE.get_sensor(incident.sensor_id)
    STORE.resolve_incident(
        incident, actor, note=note, out_of_range=out is not None,
        temperature_f=sensor.last_temperature if sensor is not None else None)
    if out is not None and not incident.corrective_action:
        # What was done is the corrective action an inspector reads, when
        # nobody has written one yet.
        STORE.record_corrective_action(incident, actor, note)
    _notify_hooks(tenant, incident, "resolved", f"Closed out by {actor}.")
    write_audit(
        tenant,
        operator,
        "incident.resolved",
        f"{incident.incident_id} on {incident.sensor_id}"
        + (f", closed while still out of range: {note}" if out is not None
           else f": {note}"),
        fallback_actor=actor,
    )

    message = "Closed."
    if out is not None and sensor is not None:
        from telemetry import alert_clock

        wait = alert_clock(sensor)["wait_minutes"]
        message = (f"Closed with your note. If it is still out of range in "
                   f"{minutes_words(wait)}, we alert again.")
    return {
        "status": "RESOLVED",
        "resolved_by": actor,
        "message": message,
        # "Food kept or thrown away?", asked by the page once it closes
        # (static/answer.js), where the sector has food or stock to ask
        # about and nobody has said yet. None otherwise.
        "ask_food": _food_question(incident, sensor),
        "incident": incident.public(),
    }


def _food_question(incident: Incident, sensor) -> Optional[Dict[str, str]]:
    """{"word": "food" | "stock"} when the page should ask what happened
    to it, or None (a site-wide alert, a sector with no estimate, or it was
    already recorded)."""
    import food_value

    if incident.site_wide or incident.product_disposition:
        return None
    guess = food_value.estimate(sensor)
    if guess is None:
        return None
    return {"word": "food" if guess["label"].startswith("Food") else "stock"}


def out_temperature(incident: Incident, tenant: Tenant) -> Optional[str]:
    """The unit's latest temperature in words, or None if it has none."""
    sensor = STORE.get_sensor(incident.sensor_id)
    if sensor is None or sensor.tenant_id != tenant.tenant_id or sensor.last_temperature is None:
        return None
    return plain_temperature(sensor.last_temperature, tenant.temperature_unit)


def _still_out_of_range(incident: Incident, tenant: Tenant) -> Optional[dict]:
    """The unit's latest reading, if the alert's unit is still out of range.

    None when it is in range, when nothing is known (no reading, the unit
    has gone quiet, it was retired), or for a site-wide alert, which is
    many units and is closed on the word of the person who has it.
    """
    from domain import evaluate_sensor_breach

    if incident.site_wide:
        return None
    sensor = STORE.get_sensor(incident.sensor_id)
    if (sensor is None or sensor.tenant_id != tenant.tenant_id
            or sensor.last_temperature is None or sensor.offline()):
        return None
    unit = tenant.temperature_unit
    if evaluate_sensor_breach(sensor, sensor.last_temperature, unit) is None:
        return None
    above, below = sensor.bounds()
    warm = above is not None and sensor.last_temperature > above
    temperature = plain_temperature(sensor.last_temperature, unit)
    limit = plain_temperature(above if warm else below, unit)
    return {"temperature": temperature, "limit": limit,
            "words": wording.still_out_words(temperature, limit, warm)}


@router.get("/incidents")
def list_incidents(tenant: Tenant = Depends(require_tenant)):
    """Full incident history for the tenant, newest first."""
    incidents = STORE.incidents_for(tenant.tenant_id)
    return {
        "count": len(incidents),
        "open": sum(1 for i in incidents if i.open),
        "incidents": [i.public() for i in incidents],
    }


# ==============================================================================
#  What was done about it
#
#  A temperature log is not what a health inspector is reading. HACCP
#  principles 4 and 7 make the *corrective action* and its review the
#  record: what happened to the product, who did it, and which manager
#  signed. An excursion with a temperature and no entry is the line that
#  gets written up.
#
#  This is also where the two halves of this market do not meet. The
#  hardware vendors log temperatures and stop. The food-safety workflow
#  platforms capture corrective actions but cannot see a freezer, so
#  somebody types the readings in by hand -- which is the step that gets
#  skipped on a bad night, and the gap the inspector finds.
# ==============================================================================


# Written for the person holding the phone at 6am, not for a form. Free
# text stays available because the real answer is often none of these.
CORRECTIVE_ACTIONS = {
    "restaurant": [
        "Moved product to another unit",
        "Discarded affected product",
        "Door found open and closed",
        "Called refrigeration engineer",
        "Unit defrosted and recovered",
    ],
    "medical_lab": [
        "Moved samples to backup storage",
        "Samples quarantined pending review",
        "Samples discarded per protocol",
        "Called service engineer",
    ],
    "pharmacy": [
        "Stock moved to backup fridge",
        "Stock quarantined pending manufacturer guidance",
        "Stock destroyed and recorded",
        "Called service engineer",
    ],
}
DEFAULT_ACTIONS = [
    "Fault corrected on site",
    "Called an engineer",
    "Load moved elsewhere",
    "No action needed; reading was transient",
]

DISPOSITIONS = (
    "unaffected", "moved", "quarantined", "discarded", "sold_or_used",
    "not_applicable",
)


class CorrectiveAction(BaseModel):
    """What a person did about an excursion."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(..., min_length=3, max_length=500)
    product_disposition: Optional[str] = Field(
        None, description=f"One of {DISPOSITIONS}."
    )

    @field_validator("product_disposition")
    @classmethod
    def known_disposition(cls, value):
        if value and value not in DISPOSITIONS:
            raise ValueError(f"product_disposition must be one of {DISPOSITIONS}")
        return value


@router.post("/corrective-action/{incident_id}")
def record_corrective_action(
    incident_id: str,
    payload: CorrectiveAction,
    tenant: Tenant = Depends(require_tenant),
    operator: Optional[User] = Depends(optional_operator),
    _: object = Depends(require_role_or_machine("operator")),
):
    """Write down what was done, against the excursion it answers."""
    incident = _load_incident(incident_id, tenant)
    actor = operator.full_name if operator else "API key"
    STORE.record_corrective_action(
        incident, actor, payload.action, payload.product_disposition or ""
    )
    if operator is not None:
        write_audit(tenant, operator, "incident.corrective_action",
                    f"{incident_id}: {payload.action[:200]}")
    return {
        "message": "Recorded against the excursion.",
        "incident": incident.public(),
    }


# "Food kept" or "Thrown away", the quick question after "It's fixed"
# (3.28.0): what happened to the food, as one of DISPOSITIONS. It decides
# whether the closed alert says "Food protected" (food_value.py).
FOOD_ANSWERS = {"kept": "unaffected", "thrown": "discarded"}


class FoodAnswer(BaseModel):
    """What happened to the food in a unit after its alert."""

    model_config = ConfigDict(extra="forbid")

    food: str = Field(..., description="'kept' or 'thrown'.")

    @field_validator("food")
    @classmethod
    def known_answer(cls, value):
        if value not in FOOD_ANSWERS:
            raise ValueError("food must be 'kept' or 'thrown'")
        return value


@router.post("/food/{incident_id}")
def record_food(
    incident_id: str,
    payload: FoodAnswer,
    tenant: Tenant = Depends(require_tenant),
    operator: Optional[User] = Depends(optional_operator),
    _: object = Depends(require_role_or_machine("operator")),
):
    """Say what happened to the food: kept, or thrown away.

    Written as the alert's product disposition, with who said so in the
    audit trail. An inspector's log reads it as what happened to the
    product; the console reads it to say "Food protected" honestly.
    """
    incident = _load_incident(incident_id, tenant)
    if incident.site_wide:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Say what happened to the food on each fridge's own alert.",
        )
    actor = actor_label(operator, "API key")
    fate = FOOD_ANSWERS[payload.food]
    STORE.record_disposition(incident, fate)
    write_audit(tenant, operator, "incident.food",
                f"{incident_id}: food {'kept' if payload.food == 'kept' else 'thrown away'}.",
                fallback_actor=actor)
    return {
        "message": ("Noted: food kept." if payload.food == "kept"
                    else "Noted: food thrown away. That goes in the record."),
        "incident": incident.public(),
    }


@router.post("/review/{incident_id}")
def review_incident(
    incident_id: str,
    tenant: Tenant = Depends(require_tenant),
    operator: Optional[User] = Depends(optional_operator),
    _: object = Depends(require_role_or_machine("owner")),
):
    """A manager's sign-off on the record.

    Owner-only, and no machine credential: principle 7 asks for a named
    person's review, and a signature attributed to "API key" is not one.
    """
    if operator is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "A review has to be signed by a named person, so it "
                "cannot be made with a machine credential."
            ),
        )
    incident = _load_incident(incident_id, tenant)
    if not incident.corrective_action:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "There is no corrective action to review yet. Record what "
                "was done first."
            ),
        )
    STORE.review_incident(incident, operator.full_name)
    write_audit(tenant, operator, "incident.reviewed", incident_id)
    return {
        "message": f"Reviewed by {operator.full_name}.",
        "incident": incident.public(),
    }


@router.get("/corrective-actions")
def suggested_actions(tenant: Tenant = Depends(require_tenant)):
    """The handful of answers that cover most nights, by sector."""
    verticals = {s.industry_vertical for s in STORE.sensors_for(tenant.tenant_id)}
    offered = []
    for vertical in sorted(verticals):
        offered.extend(CORRECTIVE_ACTIONS.get(vertical, []))
    return {
        "suggestions": sorted(set(offered)) or DEFAULT_ACTIONS,
        "dispositions": list(DISPOSITIONS),
        "note": (
            "Suggestions, not a fixed list. The real answer is often none "
            "of these, and a log where every entry is identical is one an "
            "inspector reads twice."
        ),
    }
