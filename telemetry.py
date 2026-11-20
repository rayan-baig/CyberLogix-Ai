"""Universal IoT telemetry ingestion.

Receives sensor pulses, scores each against its unit's limit, and opens
an alert, with a text to everyone on call, when the unit STAYS out of
range: over its limit continuously for its wait (20 minutes for a food
fridge, 30 for a freezer, 10 for a vaccine fridge; each unit can be set
by its owner), or for a set number of readings in a row where the owner
asked for that. A reading far past the limit alerts at once, and so does
a sensor going quiet (automation.py). A door held open for a minute
alerts nobody. A unit that comes back to normal before anybody answers
closes its own alert, with a plain note, and nobody is phoned.

A sensor that keeps breaching updates its existing open incident rather
than opening a new one, so a failing freezer produces one alert to act on
instead of a pager storm.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator

from models import Finite

from gemini import safe_generate
from auth import IngestPrincipal, require_ingest
from notifications import send_sms
from domain import (
    alert_after_minutes,
    back_to_normal,
    far_out_of_range,
    minutes_words,
    plain_temperature,
)
from store import (
    INDUSTRY_PROFILES,
    STORE,
    Tenant,
    ImplausibleReading,
    evaluate_sensor_breach,
    display_temperature,
    iso,
    require_plausible,
    to_fahrenheit,
    utc_now,
)

logger = logging.getLogger("cyberlogix.telemetry")

# An alert roster is texted concurrently rather than one number at a time.
# Bounded, because the number of contacts on a site is the customer's
# choice and a hundred-person roster should not open a hundred sockets.
SMS_FANOUT_WORKERS = 6

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
    temperature_fahrenheit: Optional[Finite] = Field(
        None, description="Current ambient temperature reading, in Fahrenheit"
    )
    temperature_celsius: Optional[Finite] = Field(
        None, description="Current ambient temperature reading, in Celsius"
    )
    humidity_percent: Optional[Finite] = Field(
        50.0, ge=0.0, le=100.0, description="Optional relative humidity percentage"
    )
    battery_percent: Optional[Finite] = Field(
        None, ge=0.0, le=100.0, description="Sensor battery level"
    )
    signal_percent: Optional[Finite] = Field(
        None, ge=0.0, le=100.0, description="Sensor signal strength"
    )

    @field_validator(
        "temperature_fahrenheit", "temperature_celsius", "humidity_percent",
        "battery_percent", "signal_percent",
    )
    @classmethod
    def finite_only(cls, value: Optional[float]) -> Optional[float]:
        """Refuse NaN and infinity at the door.

        Pydantic accepts them as floats, and everything downstream then
        misbehaves quietly: NaN compares false against every threshold, so
        the asset is scored nominal forever, and it serialises to invalid
        JSON, which takes out the compliance report and the attestation for
        that tenant permanently. A device sending one deserves an error it
        can see, not a reading nobody can trust.
        """
        if value is not None and (math.isnan(value) or math.isinf(value)):
            raise ValueError(
                "must be a finite number; a non-finite reading cannot be "
                "scored against a threshold"
            )
        return value

    @model_validator(mode="after")
    def physically_possible(self) -> "SensorReading":
        """Refuse a reading no sensor could have produced."""
        try:
            require_plausible(self.resolved_fahrenheit())
        except ImplausibleReading as exc:
            raise ValueError(str(exc)) from exc
        return self

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
    """Full vertical catalogue with pricing, for a sector selector.

    Public, so only published rates carry a figure: a trade priced by
    quote has `monthly_usd` null and says so in `price_label`.
    """
    from domain import unit_kinds
    from pricing import PRICE_BOOK, custom_quote, public_price, public_price_label

    return {
        "count": len(INDUSTRY_PROFILES),
        "industries": [
            {
                "vertical": key,
                "name": profile["name"],
                "catastrophe": profile["catastrophe"],
                # The line that gets said out loud in that trade's own
                # words. Served from here rather than written into the
                # marketing page, so the copy and the sector it describes
                # cannot drift apart.
                "slogan": profile["slogan"],
                "danger_above": profile["danger_above"],
                "danger_below": profile["danger_below"],
                "unit": profile["unit"],
                "asset_noun": profile["asset_noun"],
                "asset_plural": profile["asset_plural"],
                # "Fridge or freezer?" and the limit each answer gives;
                # empty for a sector whose units all take its own limit.
                "unit_kinds": unit_kinds(key),
                "billing_unit": PRICE_BOOK[key]["unit"],
                "monthly_usd": public_price(key),
                "custom_pricing": custom_quote(key),
                "price_label": public_price_label(key),
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
    """The alert text for a breach, returning (text, source).

    Facts first, in one segment: which unit, where, the reading, how far
    past its limit, what to check, and "Reply 1". wording.py builds it.
    The model words only the line saying what to check -- the facts and
    the way to acknowledge are never left to it -- and its line is used
    only if it is one short plain sentence. Source is that line's source.

    The reading is written in the tenant's unit: a manager in Lyon woken at
    3am should not have to convert Fahrenheit before deciding whether to
    drive in.
    """
    import wording

    tenant = STORE.get_tenant(tenant_id)
    facts = wording.breach_facts(sensor, temperature, unit)
    # No reading and no timestamp in the prompt, so the answer cache can
    # serve the same failure on the same kind of unit twice.
    line, source = safe_generate(
        wording.action_prompt(sensor, facts), wording.action_for(sensor),
        purpose="emergency SMS", tenant_id=tenant_id, business_key=True,
    )
    action = wording.clean_action(line)
    if action is None:
        action, source = wording.action_for(sensor), "fallback_template"
    return wording.alert_sms(tenant, sensor, temperature, unit, action), source


_email_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="alert-mail")
_email_futures: list = []


def _email_later(tenant: Tenant, sensor, incident) -> None:
    future = _email_pool.submit(send_alert_email, tenant, sensor, incident)
    _email_futures[:] = [f for f in _email_futures if not f.done()] + [future]


def wait_for_alert_emails(timeout: float = 30.0) -> bool:
    """Wait for alert emails still being sent. For tests."""
    from concurrent.futures import wait

    _, pending = wait(list(_email_futures), timeout=timeout)
    return not pending


def send_alert_email(tenant: Tenant, sensor, incident) -> dict:
    """The same alert to the business's own address, once per incident.

    The roster is phone numbers; the business's contact email is where the
    owner reads everything else from us, and an alarm that only ever went
    to whoever is on call tonight is one the owner hears about tomorrow.
    Never raises: a mail host having a bad night must not touch the text
    that has already gone out.
    """
    import wording
    from mail import send as send_mail
    from notifications import PUBLIC_BASE_URL

    try:
        console_url = f"{PUBLIC_BASE_URL}/console" if PUBLIC_BASE_URL else ""
        if incident.site_wide:
            subject, body = wording.site_wide_email(tenant, incident, console_url)
        else:
            subject, body = wording.alert_email(
                tenant, sensor, incident, tenant.temperature_unit,
                console_url=console_url,
            )
        return send_mail(
            to_address=tenant.contact_email or "",
            subject=subject,
            body=body,
            dedupe_key=f"incident-alert:{incident.incident_id}",
            klass="transactional",
            tenant_id=tenant.tenant_id,
        )
    except Exception:  # noqa: BLE001 - the email is a copy; the text is the alert
        logger.exception("Alert email for %s failed.", incident.incident_id)
        return {"sent": False, "status": "failed"}


def _routine_defrost(sensor, temperature: float):
    """The routine defrost this reading is part of, or None to page."""
    import patterns
    from forecaster import PATTERN_WINDOW_HOURS

    above, _ = sensor.bounds()
    if above is None or temperature <= above:
        return None
    history = STORE.readings_for(
        sensor.sensor_id,
        since=utc_now() - timedelta(hours=PATTERN_WINDOW_HOURS),
    )
    return patterns.defrost_in_progress(history, above)


def out_of_range_spell(sensor) -> "tuple[Optional[datetime], int]":
    """When the current out-of-range spell began, and its readings over.

    Walked back from the newest reading until one is back to normal (in
    range by BACK_TO_NORMAL_MARGIN_F). A reading a hair inside the line is
    neither: it does not count as over, and it does not restart the wait,
    so a unit hovering at its limit is still alerted on.
    """
    began: Optional[datetime] = None
    over = 0
    for reading in reversed(STORE.readings_for(sensor.sensor_id)):
        if back_to_normal(sensor, reading.temperature_fahrenheit):
            break
        if reading.breached:
            began = reading.recorded_at
            over += 1
    return began, over


def alert_due(sensor, temperature: float, now=None) -> Optional[Dict[str, Any]]:
    """Why this out-of-range unit should be alerted on now, or None to wait.

    In order: the owner set it to alert at once; the reading is far past
    the limit (ALERT_AT_ONCE_DEGREES_F), which is never a door; it has been
    out of range continuously for its wait; or, where the owner set one,
    that many readings in a row have been over.
    """
    now = now or utc_now()
    wait = alert_after_minutes(sensor)
    began, over = out_of_range_spell(sensor)
    began = began or now
    minutes = max(0.0, (now - began).total_seconds() / 60.0)
    base = {"out_since": began, "minutes_out": round(minutes, 2),
            "readings_over": over, "wait_minutes": wait}
    if wait <= 0:
        return dict(base, because="set to alert on the first reading over")
    if far_out_of_range(sensor, temperature):
        return dict(base, because="far past its limit")
    if minutes >= wait:
        return dict(base, because=f"out of range for {minutes_words(minutes)}")
    count = sensor.alert_after_readings
    if count and over >= count:
        return dict(base, because=f"{over} readings in a row out of range")
    return None


def _waiting(sensor, temperature: float, unit: str) -> Dict[str, Any]:
    """What a held, out-of-range unit is waiting for, in plain words."""
    from domain import ALERT_AT_ONCE_DEGREES_F

    wait = alert_after_minutes(sensor)
    began, over = out_of_range_spell(sensor)
    minutes = max(0.0, (utc_now() - (began or utc_now())).total_seconds() / 60.0)
    above, below = sensor.bounds()
    if above is not None and temperature > above:
        far = plain_temperature(above + ALERT_AT_ONCE_DEGREES_F, unit)
        past = f"goes over {far}"
    else:
        far = plain_temperature((below or 0.0) - ALERT_AT_ONCE_DEGREES_F, unit)
        past = f"drops under {far}"
    return {
        "minutes_out": round(minutes, 2),
        "readings_over": over,
        "wait_minutes": wait,
        "alert_at": iso((began or utc_now()) + timedelta(minutes=wait)),
        "words": (f"Out of range for {minutes_words(minutes)}. We alert if it "
                  f"stays out for {minutes_words(wait)}, or at once if it "
                  f"{past}, so a door open for a moment wakes nobody."),
    }


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
        recovered = (close_if_back_to_normal(tenant, sensor, temperature)
                     if back_to_normal(sensor, temperature) else None)
        return {
            "status": "nominal",
            "industry": profile["name"],
            "sensor_id": sensor.sensor_id,
            "current_temperature": display_temperature(temperature, unit),
            "temperature_unit": unit,
            "message": (recovered or {}).get("note")
            or "In range.",
            "back_to_normal": recovered,
        }

    # Paused by the owner (retention.py): the reading is kept, and nobody
    # is texted, called or emailed until the pause ends on its date.
    from retention import current_pause

    pause = current_pause(tenant.tenant_id)
    if pause is not None:
        logger.info("Out of range while paused, not alerted: sensor=%s temp=%s°F",
                    sensor.sensor_id, temperature)
        return {
            "status": "PAUSED",
            "industry": profile["name"],
            "sensor_id": sensor.sensor_id,
            "current_temperature": display_temperature(temperature, unit),
            "temperature_unit": unit,
            "breach_details": breach_reason,
            "message": ("Out of range, and kept. Alerts are paused until "
                        f"{pause['resume_at'][:10]}, so nobody was alerted."),
        }

    logger.warning(
        "Out of range: sensor=%s vertical=%s temp=%s°F reason=%s",
        sensor.sensor_id,
        sensor.industry_vertical,
        temperature,
        breach_reason,
    )

    # Collapse a sustained breach onto the incident already open for this
    # sensor. One failure produces one incident to acknowledge.
    #
    # Checked twice, deliberately. The cheap check here skips drafting a
    # message for the overwhelmingly common case of a breach already
    # known about. The authoritative one is inside `open_incident_once`
    # below, which does the read and the write under a single lock — the
    # gap between these two calls is where a genuinely failing freezer,
    # reporting every few seconds, opened up to eight incidents for one
    # fault and rang the on-call phone eight times.
    existing = STORE.latest_unresolved_incident(sensor.sensor_id, tenant.tenant_id)

    # A freezer's own defrost, on its own schedule, is not a reason to wake
    # anybody: two or three of those a day and the alerts get ignored,
    # which is how the real one is missed. Held only when this unit has a
    # learned, recovering rhythm and this spell fits it (patterns.py says
    # exactly when); the reading is still recorded as over the line, and
    # the moment the spell overruns or runs hot the next reading pages.
    defrost = _routine_defrost(sensor, temperature) if existing is None else None
    if defrost is not None:
        logger.info("Defrost cycle held, not paged: sensor=%s temp=%s°F",
                    sensor.sensor_id, temperature)
        return {
            "status": "DEFROST_CYCLE",
            "industry": profile["name"],
            "sensor_id": sensor.sensor_id,
            "location": sensor.location_name,
            "current_temperature": display_temperature(temperature, unit),
            "temperature_unit": unit,
            "breach_details": breach_reason,
            "pattern": defrost,
            "message": defrost["because"],
        }

    # Stays out of range: a door held open for a delivery pushes a fridge
    # over its line for a few minutes and back. Nobody is texted until the
    # unit has stayed out for its wait, unless it is far past its limit.
    # The sweep (alert_held_breaches) raises it on time even if the sensor
    # sends nothing more in the meantime.
    due = alert_due(sensor, temperature) if existing is None else None
    if existing is None and due is None:
        waiting = _waiting(sensor, temperature, unit)
        logger.info("Out of range, waiting to see if it stays: sensor=%s temp=%s°F",
                    sensor.sensor_id, temperature)
        return {
            "status": "WATCHING",
            "industry": profile["name"],
            "sensor_id": sensor.sensor_id,
            "location": sensor.location_name,
            "current_temperature": display_temperature(temperature, unit),
            "temperature_unit": unit,
            "breach_details": breach_reason,
            "waiting": waiting,
            "message": waiting["words"],
        }

    return raise_alert(tenant, sensor, temperature, humidity, breach_reason,
                       existing=existing, due=due)


def raise_alert(tenant: Tenant, sensor, temperature: float,
                humidity: Optional[float], breach_reason: str,
                existing=None, due: Optional[Dict[str, Any]] = None) -> dict:
    """Open the alert and text everyone on call, or update the open one."""
    profile = INDUSTRY_PROFILES[sensor.industry_vertical]
    unit = tenant.temperature_unit
    if existing is None:
        sms_text, sms_source = build_emergency_sms(
            sensor, temperature, humidity, tenant.tenant_id, unit
        )
        incident, created = STORE.open_incident_once(
            tenant_id=tenant.tenant_id,
            sensor=sensor,
            temperature_fahrenheit=temperature,
            breach_details=breach_reason,
            sms_text=sms_text,
            sms_dispatch_source=sms_source,
        )
        if not created:
            # Somebody else opened it while this call was drafting. Their
            # SMS has gone out; a second one would be the duplicate this
            # whole path exists to prevent.
            existing = incident
        elif due is not None:
            STORE.note_out_since(incident, due.get("out_since"))

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

    # Everyone on the roster gets the text, not just one number on file —
    # and they get it at the same time, not one after another. Serially,
    # a roster of six during a Twilio slowdown costs six timeouts stacked
    # end to end, and the sensor waiting on this response gives up and
    # retries long before the last person on the list is texted. Sent
    # concurrently, the whole roster costs what the slowest one costs.
    #
    # `send_sms` is decorated to never raise, so `map` cannot break here,
    # and `map` yields in submission order, so the roster's own priority
    # ordering survives into the delivery record.
    recipients = STORE.sms_recipients(tenant, sensor.site_id)
    fanout: list[dict] = []
    if recipients:
        with ThreadPoolExecutor(
            max_workers=min(len(recipients), SMS_FANOUT_WORKERS),
            thread_name_prefix="sms-fanout",
        ) as pool:
            fanout = [
                dict(
                    receipt,
                    contact_id=contact.contact_id,
                    contact_name=contact.full_name,
                )
                for contact, receipt in zip(
                    recipients,
                    pool.map(
                        lambda c: send_sms(c.phone, sms_text, tenant.tenant_id),
                        recipients,
                    ),
                )
            ]
    STORE.record_sms_delivery(incident, fanout[0] if fanout else None, fanout)
    # On a worker: the mail host may take its full timeout to answer, and
    # the sensor waiting on this response should not.
    _email_later(tenant, sensor, incident)

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
    if due is not None:
        payload["alerted_because"] = due["because"]
    return payload


def close_if_back_to_normal(tenant: Tenant, sensor, temperature: float):
    """Close the unanswered alert on a unit that came back to normal.

    Never phone about a fridge that is fine: the pending call is cancelled
    by closing the alert before the sweep reaches it (the claim on the call
    refuses a closed alert). An alert somebody acknowledged is theirs to
    close, with what they did, so it is left alone. The people texted get
    a short all-clear, so nobody drives in for nothing.
    """
    import wording
    from voice_dispatch import _notify_hooks

    incident = STORE.latest_unresolved_incident(sensor.sensor_id, tenant.tenant_id)
    if incident is None or not incident.open:
        return None
    began = incident.out_since or incident.opened_at
    minutes = max(0.0, (utc_now() - began).total_seconds() / 60.0)
    called = incident.voice_escalated_at is not None
    note = wording.back_to_normal_note(minutes, called=called)
    if not STORE.resolve_back_to_normal(incident, note):
        return None
    logger.info("Back to normal, alert %s closed on its own: sensor=%s",
                incident.incident_id, sensor.sensor_id)
    told = []
    if incident.texts_landed:
        text = wording.back_to_normal_sms(tenant, sensor, temperature,
                                          tenant.temperature_unit, minutes)
        for contact in STORE.alert_recipients(tenant, incident):
            told.append(dict(send_sms(contact.phone, text, tenant.tenant_id),
                             contact_id=contact.contact_id))
    try:
        from auth import write_audit

        write_audit(tenant, None, "incident.resolved",
                    f"{incident.incident_id} on {incident.sensor_id}: {note}",
                    fallback_actor="CyberLogix (back to normal)")
    except Exception:  # noqa: BLE001 - the record of it must not stop the close
        logger.exception("Audit entry for %s failed.", incident.incident_id)
    _notify_hooks(tenant, incident, "resolved", note, background=True)
    return {"incident_id": incident.incident_id, "note": note,
            "minutes_out": round(minutes, 2), "all_clear_texts": told}


def alert_held_breaches(tenant: Tenant, now=None) -> List[Dict[str, Any]]:
    """Raise, on time, the alerts that were waiting to see if a unit stays out.

    A sensor that reports every fifteen minutes, or goes quiet after going
    warm, sends nothing at the moment its wait runs out; this is called by
    every sweep so the alert goes when it is due, not at the next reading.
    The unit's newest reading decides: if it is out of range and has been
    for the unit's wait, the alert opens exactly as a reading would open it.
    """
    from auth import licence_state
    from retention import current_pause
    from store import evaluate_sensor_breach

    now = now or utc_now()
    if licence_state(tenant, now) not in ("active", "grace"):
        return []
    if current_pause(tenant.tenant_id, now) is not None:
        return []
    actions: List[Dict[str, Any]] = []
    for sensor in STORE.sensors_for(tenant.tenant_id):
        temperature = sensor.last_temperature
        if temperature is None or sensor.clock_skewed(now):
            continue
        reason = evaluate_sensor_breach(sensor, temperature, tenant.temperature_unit)
        if reason is None:
            continue
        if STORE.latest_unresolved_incident(sensor.sensor_id, tenant.tenant_id):
            continue
        if _routine_defrost(sensor, temperature) is not None:
            continue
        due = alert_due(sensor, temperature, now)
        if due is None:
            continue
        payload = raise_alert(tenant, sensor, temperature, sensor.last_humidity,
                              reason, due=due)
        actions.append({
            "action": "held_alert_raised",
            "sensor_id": sensor.sensor_id,
            "incident_id": payload.get("incident_id"),
            "detail": f"Alerted: {due['because']}.",
        })
    return actions


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
def process_sensor_pulse(
    reading: SensorReading, principal: IngestPrincipal = Depends(require_ingest)
):
    """Ingest one telemetry packet from a registered sensor.

    Deliberately a plain `def`, not `async def`. Everything below it is
    blocking — SQLite, the Twilio REST client, the webhook fan-out — and
    Starlette hands a sync endpoint to a worker thread while an async one
    runs on the single event loop thread. Declared `async`, this route
    held the loop for the entire duration of an alert, so one customer
    whose Slack receiver had stopped answering took the whole platform
    off the air: not just their own ingest, but every other tenant's
    sensors, the console, and `/api/health` — which is what the load
    balancer polls before deciding the container is dead. Measured at
    15.1 seconds of total blackout from three slow hooks on one site.

    There is nothing to await here, so there is nothing to gain by being
    a coroutine, and everything to lose.
    """
    # A per-sensor key may only speak for its own asset; the tenant key
    # may speak for any of them.
    principal.authorise(reading.sensor_id)
    tenant = principal.tenant
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
