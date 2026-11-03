"""What an alert says: the text, the email and the phone call.

Written for the person who reads it at 3am on a lock screen, or hears it
half awake. So, in this order: which unit, where, how far out of range,
what to check, and how to say "I have it". Plain words; no sector jargon,
no "catastrophe", no "telemetry".

The facts are put together here, from the reading itself, and never left
to the model. The model (when there is one) words a single line -- what to
check -- and that line is the first thing cut if the text runs long. The
text has to fit one SMS segment (160 GSM-7 characters): a second segment
doubles the cost of the largest delivery line, and a message split in two
can arrive in the wrong order or half of it not at all. Whatever happens
to the wording, the reading and "Reply 1" are always in the text.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from domain import INDUSTRY_PROFILES, display_temperature

# One GSM-7 segment. notifications.send_sms trims to this as a last resort;
# the text is built to fit it, so nothing that matters is what gets trimmed.
SMS_LIMIT = 160

# The words that make a reply count. Kept short and forgiving: a person
# typing with cold hands sends "ack", "Ack.", "1" or "ok".
ACK_WORDS = ("1", "ACK", "ACKNOWLEDGE", "OK", "YES", "ON IT")

SMS_ACK = "Reply 1 if you are on it."

# What to check, per trade, in that trade's own words. Short enough that
# the whole text still fits one segment with a long unit name.
ACTIONS = {
    "cybersecurity": "Check the cooling now.",
    "pharmacy": "Check the fridge and stock.",
    "wine_and_art": "Check the climate control.",
    "cannabis": "Check the room's climate now.",
    "restaurant": "Check the door and food now.",
    "logistics": "Check the reefer unit now.",
    "solar_infrastructure": "Check the enclosure cooling.",
    "medical_lab": "Check the cooler and samples.",
    "cryostorage": "Check the tank's nitrogen now.",
    "private_aviation": "Check the hangar bay now.",
    "superyacht": "Check engine bay airflow now.",
    "country_club": "Check the kitchen walk-in now.",
}
# Thirty characters at most, so a unit and site with ordinary names, the
# reading, the limit and "Reply 1" still leave room for it.
ACTION_ROOM = 30
DEFAULT_ACTION = "Check it now."

# The model's line is used only if it is one short plain sentence.
MAX_ACTION_CHARACTERS = 60


def _number(value: float) -> str:
    """58.0 -> "58", 4.44 -> "4.4": a person reads a number, not a float."""
    return f"{round(value, 1):g}"


def breach_facts(sensor, temperature_f: float, unit: str) -> Dict[str, Any]:
    """The reading against the limit it crossed, in the account's unit."""
    above, below = sensor.bounds()
    reading = display_temperature(temperature_f, unit)
    if above is not None and temperature_f > above:
        limit_f, direction = above, "above"
    elif below is not None and temperature_f < below:
        limit_f, direction = below, "below"
    else:  # pragma: no cover - only called for a breach
        limit_f, direction = (above if above is not None else below), "outside"
    limit = display_temperature(limit_f, unit) if limit_f is not None else None
    delta = abs(reading - limit) if limit is not None else None
    return {
        "reading": reading,
        "limit": limit,
        "delta": delta,
        "direction": direction,
        "unit": unit,
    }


def unit_name(sensor) -> str:
    """The unit as the customer named it, with its id so it can be found."""
    location = (getattr(sensor, "location_name", "") or "").strip()
    if not location or location == sensor.sensor_id:
        return sensor.sensor_id
    return f"{location} ({sensor.sensor_id})"


def place_of(sensor, tenant) -> str:
    """The site, or the business when it has no sites.

    The site is what tells somebody on the roster of several stores which
    one to drive to; with no sites, the business name tells somebody on
    the roster of several businesses which one it is.
    """
    from store import STORE

    site = STORE.get_site(sensor.site_id) if getattr(sensor, "site_id", None) else None
    if site is not None and site.name:
        return site.name
    return tenant.company_name


def action_for(sensor) -> str:
    return ACTIONS.get(sensor.industry_vertical, DEFAULT_ACTION)


def clean_action(text: Optional[str]) -> Optional[str]:
    """A model's line, if it is one short plain sentence; otherwise None."""
    from notifications import to_gsm7

    line = to_gsm7((text or "").strip().strip('"'))
    if not line or len(line) > MAX_ACTION_CHARACTERS or "\n" in (text or "").strip():
        return None
    if not line.endswith((".", "!")):
        line += "."
    return line


def action_prompt(sensor, facts: Dict[str, Any], spoken: bool = False) -> str:
    profile = INDUSTRY_PROFILES[sensor.industry_vertical]
    medium = ("one short sentence to be read aloud on a phone call"
              if spoken else "one short sentence for a text message")
    return f"""
    A temperature alarm has gone off. Write {medium}, at most 45
    characters, telling the person on call what to check first.

    Trade: {profile['name']}
    Equipment: {profile['asset_noun']}
    A common cause: {profile['catastrophe']}
    The reading is {facts['direction']} its limit.

    Plain everyday words. No jargon, no exclamation marks, no markdown,
    no quotation marks. Return only the sentence.
    """


def alert_sms(tenant, sensor, temperature_f: float, unit: str,
              action: Optional[str] = None) -> str:
    """The alert text, facts first, inside one segment.

    ALERT Kitchen walk-in (WALKIN-1) at Harbor Grill: 58F, 26F above the
    32F limit. Check the door and the food now. Reply 1 if you are on it.
    """
    from notifications import to_gsm7

    facts = breach_facts(sensor, temperature_f, unit)
    reading = f"{_number(facts['reading'])}{unit}"
    if facts["limit"] is not None:
        out_by = (f"{_number(facts['delta'])}{unit} {facts['direction']} the "
                  f"{_number(facts['limit'])}{unit} limit")
    else:  # pragma: no cover - every profile has a limit on one side
        out_by = "out of range"
    name, place = unit_name(sensor), place_of(sensor, tenant)
    tail = SMS_ACK
    # Measured in GSM-7 characters, not Python ones: [ ] { } | ^ ~ backslash and
    # the euro sign are sent as two each, so a 160-character text with one
    # of them in it is billed as two segments.
    for what in (action or action_for(sensor), action_for(sensor), DEFAULT_ACTION, ""):
        text = to_gsm7(f"ALERT {name} at {place}: {reading}, {out_by}. {what} {tail}")
        if _septets(text) <= SMS_LIMIT:
            return text
    # Still too long: the names are what give. The reading, how far out,
    # and how to answer stay whole.
    fixed = to_gsm7(f"ALERT : {reading}, {out_by}. {tail}")
    room = max(SMS_LIMIT - _septets(fixed) - len(" at "), 12)
    name = to_gsm7(name)
    place = to_gsm7(place)
    keep_name = min(_septets(name), max(room - min(_septets(place), room // 3), room // 2))
    keep_place = max(room - keep_name, 0)
    text = to_gsm7(f"ALERT {_cut(name, keep_name)} at {_cut(place, keep_place)}: "
                   f"{reading}, {out_by}. {tail}")
    return _cut(text, SMS_LIMIT, mark="")


def _septets(text: str) -> int:
    """Length as GSM-7 sends it: the extension characters count twice."""
    from notifications import _GSM7_EXT

    return sum(2 if ch in _GSM7_EXT else 1 for ch in text)


def _cut(text: str, room: int, mark: str = ".") -> str:
    """`text` in at most `room` GSM-7 characters, marked where it was cut.

    The mark is a plain full stop: the "~" it replaced is itself one of
    the characters that count twice.
    """
    if _septets(text) <= room:
        return text
    kept = ""
    for ch in text:
        if _septets(kept + ch) + len(mark) > room:
            break
        kept += ch
    return (kept + mark) if kept else mark[:max(room, 0)]


def _spoken(value: float, unit: str) -> str:
    return f"{_number(value)} degrees {'Celsius' if unit == 'C' else 'Fahrenheit'}"


def voice_script(tenant, sensor, incident, unit: str,
                 action: Optional[str] = None, can_press: bool = True) -> str:
    """The words said down the phone. Short, because it is read twice.

    Who is calling, which unit, where, how far out of range and for how
    long, what to check, and how to stop the calls. "Press 1" is only said
    when pressing 1 reaches something: with no public address for the
    keypress to go to, the call says how else to acknowledge instead.
    """
    facts = breach_facts(sensor, incident.temperature_fahrenheit, unit)
    location = (getattr(sensor, "location_name", "") or "").strip() or sensor.sensor_id
    place = place_of(sensor, tenant)
    minutes = int(incident.minutes_open())
    lines = [
        f"This is CyberLogix AI with a temperature alarm for {tenant.company_name}.",
        f"{location}, at {place}, is at {_spoken(facts['reading'], unit)}.",
    ]
    if facts["limit"] is not None:
        lines.append(
            f"That is {_spoken(facts['delta'], unit)} {facts['direction']} "
            f"its limit of {_spoken(facts['limit'], unit)}.")
    lines.append(
        f"It has been out of range for {minutes} "
        f"minute{'' if minutes == 1 else 's'}, and nobody has answered the text.")
    lines.append(action or action_for(sensor))
    if can_press:
        lines.append("Press 1 now to say you are dealing with it, "
                     "and the calls will stop.")
    else:
        lines.append("To stop these calls, reply 1 to the alert text, "
                     "or acknowledge it in the console.")
    return " ".join(lines)


def alert_email(tenant, sensor, incident, unit: str,
                action: Optional[str] = None, console_url: str = "") -> Tuple[str, str]:
    """The same alert by email, for the business's own address.

    The subject carries the facts, so it reads in a notification banner
    without being opened. The body adds what the text has no room for.
    """
    from store import VOICE_ESCALATION_GRACE_MINUTES, iso

    facts = breach_facts(sensor, incident.temperature_fahrenheit, unit)
    location = (getattr(sensor, "location_name", "") or "").strip() or sensor.sensor_id
    place = place_of(sensor, tenant)
    reading = f"{_number(facts['reading'])}°{unit}"
    limit = f"{_number(facts['limit'])}°{unit}" if facts["limit"] is not None else ""
    out_by = (f"{_number(facts['delta'])}°{unit} {facts['direction']} the limit"
              if facts["limit"] is not None else "out of range")
    subject = f"ALERT: {location} at {place} is {reading}, {out_by}"
    profile = INDUSTRY_PROFILES[sensor.industry_vertical]
    # Sent after the texts were tried, so it can say when none got through:
    # the owner reading this must not assume the team already knows.
    # Not in dry run, where no text is ever sent and every alert would
    # say so.
    texts_failed = bool(getattr(incident, "alert_texts_failed", False)) and any(
        (r or {}).get("status") != "not_configured" for r in incident._sms_receipts())
    if texts_failed:
        subject = "TEXTS FAILED - " + subject
    lines = [
        *(["We could not text your on-call team about this: the text message "
           "service failed. Phone whoever is on call now. We keep trying the "
           "texts.", ""] if texts_failed else []),
        f"{location} ({sensor.sensor_id}) at {place} is reading {reading}.",
        (f"Its limit is {limit}, so it is {out_by}." if limit else ""),
        "",
        f"What to do: {action or action_for(sensor)}",
        f"A common cause in this trade: {profile['catastrophe'].lower()}.",
        "",
        "To stop the alerts, reply 1 to the text message, or acknowledge it "
        "in the console" + (f": {console_url}" if console_url else "."),
        f"If nobody does within {VOICE_ESCALATION_GRACE_MINUTES} minutes, "
        "we phone the people on call.",
        "",
        f"Alert {incident.incident_id}, opened {iso(incident.opened_at)} (UTC).",
    ]
    return subject, "\n".join(line for line in lines if line is not None)
