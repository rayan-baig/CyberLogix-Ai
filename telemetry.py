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
from records import person_name

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


def _hold_broken(sensor, closed: Any) -> bool:
    """Whether a reading since a close by hand ended the hold at once.

    A unit somebody closed while it was still warm ("Close anyway: moved
    the food") waits its usual time again before anybody is alerted. Not
    if it then reads far past its limit (ALERT_AT_ONCE_DEGREES_F, 15°F):
    that is never a door, so the hold ends and we alert straight away.
    The one exception is a unit that was already that far out when the
    person closed it and has stayed that far out since: they saw that
    temperature and closed it anyway, so the same reading again is not
    news. Once it has come back under that line, going far out again is.
    """
    at = closed.resolved_at
    then = closed.close_temperature_f
    came_down = then is None or not far_out_of_range(sensor, then)
    since = []
    for reading in reversed(STORE.readings_for(sensor.sensor_id)):
        # Strictly before: a reading in the same second as the close (times
        # read back from disk are to the second) counts as after it.
        if reading.recorded_at < at:
            break
        since.append(reading)
    for reading in reversed(since):             # oldest first
        if not far_out_of_range(sensor, reading.temperature_fahrenheit):
            came_down = True
        elif came_down:
            return True
    return False


def alert_clock(sensor, now=None) -> Dict[str, Any]:
    """The one clock every page and the alarm itself read for a unit.

    `out_since` is when the current out-of-range spell began: the start
    time every view shows, and the one a new alert records. `alert_at` is
    when we text if it stays out. Normally that is `out_since` plus the
    unit's wait. After a person closed an alert on this unit, the wait
    starts again from when they closed it (`held_after_close`), so "Close
    anyway" while it is still warm is not followed by a new alert and a
    text to everybody a few seconds later. A reading far past the limit
    after that ends the hold at once (_hold_broken).
    """
    from domain import default_alert_after_minutes

    now = now or utc_now()
    began, over = out_of_range_spell(sensor)
    out_since = began or now
    wait = alert_after_minutes(sensor)
    clock_from = out_since
    last = STORE.last_manual_close_incident(sensor.sensor_id, sensor.tenant_id)
    closed = last.resolved_at if last is not None else None
    held = closed is not None and closed > out_since and not _hold_broken(sensor, last)
    if held:
        clock_from = closed
        # "Alert on the first reading" still waits after somebody closed it
        # by hand: the wait its kind of unit would have had.
        wait = wait if wait > 0 else default_alert_after_minutes(sensor)
    minutes = max(0.0, (now - out_since).total_seconds() / 60.0)
    held_for = max(0.0, (now - clock_from).total_seconds() / 60.0)
    return {
        "out_since": out_since,
        "minutes_out": round(minutes, 2),
        "readings_over": over,
        "wait_minutes": wait,
        "clock_from": clock_from,
        "minutes_on_clock": round(held_for, 2),
        "alert_at": clock_from + timedelta(minutes=wait),
        "held_after_close": held,
        "closed_at": closed if held else None,
        "closed_by": person_name(last.resolved_by) if held and last is not None else None,
    }


def alert_due(sensor, temperature: float, now=None) -> Optional[Dict[str, Any]]:
    """Why this out-of-range unit should be alerted on now, or None to wait.

    In order: the owner set it to alert at once; the reading is far past
    the limit (ALERT_AT_ONCE_DEGREES_F), which is never a door; it has been
    out of range continuously for its wait; or, where the owner set one,
    that many readings in a row have been over. Within the wait after a
    person closed an alert on this unit by hand, none of these alert: they
    have just looked at it (alert_clock).
    """
    now = now or utc_now()
    clock = alert_clock(sensor, now)
    wait = clock["wait_minutes"]
    minutes = clock["minutes_out"]
    over = clock["readings_over"]
    base = {"out_since": clock["out_since"], "minutes_out": minutes,
            "readings_over": over, "wait_minutes": wait}
    if clock["held_after_close"]:
        if clock["minutes_on_clock"] >= wait:
            return dict(base, because=(
                f"still out of range {minutes_words(clock['minutes_on_clock'])} "
                "after it was closed by hand"))
        return None
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

    clock = alert_clock(sensor)
    wait = clock["wait_minutes"]
    minutes = clock["minutes_out"]
    above, below = sensor.bounds()
    if above is not None and temperature > above:
        far = plain_temperature(above + ALERT_AT_ONCE_DEGREES_F, unit)
        past = f"goes over {far}"
    else:
        far = plain_temperature((below or 0.0) - ALERT_AT_ONCE_DEGREES_F, unit)
        past = f"drops under {far}"
    left = max(0.0, (clock["alert_at"] - utc_now()).total_seconds() / 60.0)
    # Counted down, not rounded: "out for 1 minute" next to "we alert if it
    # stays out for 1 minute" read as if the alert was already late.
    minutes = math.floor(minutes)
    if clock["held_after_close"]:
        words = (f"Out of range for {minutes_words(minutes)}. It was closed by "
                 f"hand, so we alert again if it is still out "
                 f"{minutes_words(wait)} after that, or at once if it {past}.")
    elif left < 1:
        secs = math.ceil(left * 60)
        more = "a few more seconds" if secs <= 15 else f"{secs} more seconds"
        words = (f"Out of range for {minutes_words(minutes)}. Watching for "
                 f"{more}: we alert if it is still out then, or at once if it "
                 f"{past}.")
    else:
        words = (f"Out of range for {minutes_words(minutes)}. We alert if it "
                 f"stays out for {minutes_words(wait)}, or at once if it "
                 f"{past}, so a door open for a moment wakes nobody.")
    return {
        "minutes_out": clock["minutes_out"],
        "readings_over": clock["readings_over"],
        "wait_minutes": wait,
        "out_since": iso(clock["out_since"]),
        "alert_at": iso(clock["alert_at"]),
        "held_after_close": clock["held_after_close"],
        "words": words,
    }


_LOOK_UP = object()


def unit_condition(tenant: Tenant, sensor, now=None,
                   incident: Any = _LOOK_UP) -> Dict[str, Any]:
    """What every page says about one unit right now, worked out once.

    The unit card, the Problems list, /today and the "Needs you now" bar
    all read this, so they cannot disagree: one `state`, one `out_since`
    and, while we are waiting to see if it stays out, one `alert_at`.

    States, calmest first: "waiting_first" (no reading has arrived yet),
    "ok", "watching" (out of range, inside its wait: nobody texted yet),
    "paused" (out of range while the owner paused alerts), "alert" (an
    alert is open on it), and "offline" (it has gone quiet).
    """
    from retention import current_pause

    now = now or utc_now()
    above, _ = sensor.bounds()
    temp = sensor.last_temperature
    too = None
    if temp is not None and evaluate_sensor_breach(
            sensor, temp, tenant.temperature_unit) is not None:
        too = "warm" if above is not None and temp > above else "cold"
    if incident is _LOOK_UP:
        incident = STORE.latest_unresolved_incident(sensor.sensor_id, tenant.tenant_id)
    out: Dict[str, Any] = {"state": "ok", "too": too, "out_since": None,
                           "alert_at": None, "incident_id": None}
    if sensor.last_seen is None:
        out["state"] = "waiting_first"
        return out
    if incident is not None:
        out.update(state="alert", incident_id=incident.incident_id,
                   out_since=iso(incident.started_at))
        return out
    if sensor.offline(now):
        out["state"] = "offline"
        return out
    if too is None:
        return out
    clock = alert_clock(sensor, now)
    out["out_since"] = iso(clock["out_since"])
    if current_pause(tenant.tenant_id, now) is not None:
        out["state"] = "paused"
        return out
    from domain import ALERT_AT_ONCE_DEGREES_F

    _, below = sensor.bounds()
    # Where it stops waiting and alerts at once, 15°F past the limit.
    far_f = (above + ALERT_AT_ONCE_DEGREES_F if too == "warm" and above is not None
             else (below - ALERT_AT_ONCE_DEGREES_F if below is not None else None))
    out.update(state="watching", alert_at=iso(clock["alert_at"]),
               held_after_close=clock["held_after_close"],
               temperature_display=display_temperature(temp, tenant.temperature_unit),
               temperature_unit=tenant.temperature_unit,
               far_display=(round(display_temperature(far_f, tenant.temperature_unit), 1)
                            if far_f is not None else None))
    if clock["held_after_close"]:
        # "Closed by hand by Maya at 5:48. Still 47.6°F. We'll alert again
        # at 6:08." The pages say it in those words (static/words.js).
        out.update(closed_at=iso(clock["closed_at"]), closed_by=clock["closed_by"])
    return out


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
    # Every reply to a sensor says in words what happened (3.26.0): a
    # far-too-warm reading came back with no message at all, which a
    # device's log, or the connect wizard, printed as "undefined".
    payload["message"] = _alert_reply_words(fanout, payload.get("breach_details"))
    return payload


def _alert_reply_words(fanout: list, breach: Optional[str]) -> str:
    """What the sensor is told when its reading opened an alert."""
    went = sum(1 for r in fanout if r.get("delivered"))
    off = bool(fanout) and all(r.get("status") == "not_configured" for r in fanout)
    first = (breach.strip().rstrip(".") + ". ") if breach else "Out of range. "
    if not fanout:
        return first + "Alert opened. Nobody is on the list to text yet."
    if off:
        return first + "Alert opened. Texts are off on this server, so nobody was texted."
    if went:
        return first + f"Alert opened and texted to {went} of {len(fanout)}."
    return first + "Alert opened. The texts did not go through; see the console."


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
    if incident is None:
        return None
    reported = incident.fix_reported_at is not None and not incident.open
    if not incident.open and not reported:
        return None
    minutes = incident.out_minutes()
    called = incident.voice_escalated_at is not None
    if reported:
        note = wording.fixed_and_back_note(
            minutes, person_name(incident.fix_reported_by) or "Somebody",
            plain_temperature(temperature, tenant.temperature_unit))
    else:
        note = wording.back_to_normal_note(minutes, called=called)
    if not STORE.resolve_back_to_normal(incident, note, fix_reported=reported):
        return None
    if reported:
        # Whoever said it was fixed is the one who closed it, on the
        # reading that proved it: theirs, for the inspector's log.
        STORE.note_manual_close(incident, incident.fix_reported_by, note, temperature)
        try:
            from auth import write_audit

            write_audit(tenant, None, "incident.resolved",
                        f"{incident.incident_id} on {incident.sensor_id}: {note}",
                        fallback_actor=incident.fix_reported_by or "CyberLogix")
        except Exception:  # noqa: BLE001 - the record of it must not stop the close
            logger.exception("Audit entry for %s failed.", incident.incident_id)
        _notify_hooks(tenant, incident, "resolved", note, background=True)
        return {"incident_id": incident.incident_id, "note": note,
                "minutes_out": round(minutes, 2), "all_clear_texts": []}
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
