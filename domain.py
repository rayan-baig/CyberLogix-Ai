"""The fixed rules the store applies.

Service times, temperature units, the industry profiles and plans, the
breach check: plain values and pure functions, with no
state and no lock. They used to sit at the top of store.py, above three
thousand lines of state; here they can be read, and changed, on their own.

store.py re-exports every name, so `from store import INDUSTRY_PROFILES`
and the rest keep working.
"""

from __future__ import annotations

import hashlib
import re
import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from records import Sensor


# How quickly a breach must turn into a dispatched alert. This is the
# service commitment the agreement states, and it lives here with the
# other two rather than in the add-on that used to quote it.
DISPATCH_SLA_SECONDS = 60

# A sensor silent for longer than this is treated as offline by the
# autonomous compliance clerk.
SENSOR_OFFLINE_AFTER_MINUTES = 30

# How long a breach may sit unacknowledged before voice escalation is due.
VOICE_ESCALATION_GRACE_MINUTES = 10

# "Stays out of range": how long a unit must stay over (or under) its limit
# before anybody is texted. A door held open for a delivery, or a tray of
# warm food going in, pushes a fridge over 41°F for a few minutes and back;
# texting the whole team for that, and phoning them ten minutes later, is
# how alerts get switched off. A fridge that stays over the line is a real
# problem, and that is what pages.
#
# Minutes, by kind of unit. A food freezer gets longer than a fridge: its
# air warms fast when the door opens and the food inside does not. Vaccine
# and lab fridges get less, because the rules for them are stricter.
ALERT_AFTER_MINUTES_FOOD_FRIDGE = 20.0
ALERT_AFTER_MINUTES_FOOD_FREEZER = 30.0
ALERT_AFTER_MINUTES_CLINICAL_FRIDGE = 10.0
ALERT_AFTER_MINUTES_CLINICAL_FREEZER = 15.0
ALERT_AFTER_MINUTES_CRYO = 5.0
ALERT_AFTER_MINUTES_OTHER = 15.0
# What an owner may set a unit to: 0 is "text on the first reading over".
ALERT_AFTER_MINUTES_MAX = 240.0
ALERT_AFTER_READINGS_MAX = 100

# Far over the limit is never a door: a reading this many degrees past it
# alerts at once, without waiting. (A fridge's door open lifts the air a
# few degrees; a fridge at 56°F is broken or something is very wrong.)
ALERT_AT_ONCE_DEGREES_F = 15.0

# "Back to normal" means back inside the limit by at least this much. A
# unit hovering at 40.9°F, 41.2°F, 40.8°F is not recovering, and must not
# restart the wait every time it dips a tenth under the line.
BACK_TO_NORMAL_MARGIN_F = 1.0

_FOOD_SECTORS = frozenset({"restaurant", "country_club"})
_CLINICAL_SECTORS = frozenset({"pharmacy", "medical_lab"})


def unit_kind_of(sensor: "Sensor") -> Optional[str]:
    """"fridge" or "freezer" for the sectors that ask, else None.

    The answer given on the add form, where one was saved; for a unit
    added before it was saved, read from its limit, which the answer set.
    """
    kind = getattr(sensor, "unit_kind", None)
    if kind:
        return kind
    if sensor.industry_vertical not in UNIT_KINDS:
        return None
    above, _ = sensor.bounds()
    if above is None:
        return "fridge"
    # Every freezer limit we offer is far under 20°F, every fridge's over.
    return "freezer" if above <= 20.0 else "fridge"


def default_alert_after_minutes(sensor: "Sensor") -> float:
    """How long this kind of unit may be out of range before we alert."""
    vertical = sensor.industry_vertical
    kind = unit_kind_of(sensor)
    if vertical in _FOOD_SECTORS:
        return (ALERT_AFTER_MINUTES_FOOD_FREEZER if kind == "freezer"
                else ALERT_AFTER_MINUTES_FOOD_FRIDGE)
    if vertical in _CLINICAL_SECTORS:
        return (ALERT_AFTER_MINUTES_CLINICAL_FREEZER if kind == "freezer"
                else ALERT_AFTER_MINUTES_CLINICAL_FRIDGE)
    if vertical == "cryostorage":
        return ALERT_AFTER_MINUTES_CRYO
    return ALERT_AFTER_MINUTES_OTHER


def alert_after_minutes(sensor: "Sensor") -> float:
    """The unit's own setting if its owner chose one, else its kind's."""
    own = getattr(sensor, "alert_after_minutes", None)
    return float(own) if own is not None else default_alert_after_minutes(sensor)


def far_out_of_range(sensor: "Sensor", temperature: float) -> bool:
    """So far past the limit that nobody should wait to hear about it."""
    above, below = sensor.bounds()
    return ((above is not None and temperature >= above + ALERT_AT_ONCE_DEGREES_F)
            or (below is not None and temperature <= below - ALERT_AT_ONCE_DEGREES_F))


def back_to_normal(sensor: "Sensor", temperature: Optional[float]) -> bool:
    """Inside the limit with room to spare, not just a tenth under it."""
    if temperature is None:
        return False
    above, below = sensor.bounds()
    if above is not None and temperature > above - BACK_TO_NORMAL_MARGIN_F:
        return False
    if below is not None and temperature < below + BACK_TO_NORMAL_MARGIN_F:
        return False
    return True


def wait_words() -> str:
    """The default waits in one phrase, for every page that explains them."""
    return (f"{ALERT_AFTER_MINUTES_FOOD_FRIDGE:g} minutes for a food fridge, "
            f"{ALERT_AFTER_MINUTES_FOOD_FREEZER:g} for a freezer and "
            f"{ALERT_AFTER_MINUTES_CLINICAL_FRIDGE:g} for a vaccine or lab fridge")


def minutes_words(minutes: float) -> str:
    """"under a minute", "3 minutes", "1 hour 28 minutes", "about 4
    hours": never "0.73 min".

    Under three hours, the hours and minutes (3.42.0): "about 1 hour" was
    said of 88 minutes, which is nearer an hour and a half. Past that, to
    the nearest hour, where the minutes no longer change what anybody does.
    static/words.js `minutes` says the same; a test checks it."""
    whole = int(round(minutes))
    if whole < 1:
        return "under a minute"
    if whole < 60:
        return f"{whole} minute{'' if whole == 1 else 's'}"
    if whole < 180:
        hours, rest = divmod(whole, 60)
        head = f"{hours} hour{'' if hours == 1 else 's'}"
        return f"{head} {rest} minute{'' if rest == 1 else 's'}" if rest else head
    hours = round(minutes / 60.0)
    return f"about {hours} hours"


# When the sweep sends an incident's alert text again, in minutes after the
# incident opened, if every text so far failed. After the last step the
# normal escalation (the voice call, where the plan has one) is all there is.
SMS_RETRY_AFTER_MINUTES = (1, 5, 15)

# Failures that a second try can fix: Twilio down, slow or briefly refusing.
# Not here, on purpose: dry run (no credentials, so nothing is ever sent),
# the daily spending cap (a decision, not a fault) and a number we block.
RETRYABLE_SMS_STATUSES = frozenset(
    {"send_failed", "client_unavailable", "delivery_error"}
)

# When the sweep walks the call ladder again, in minutes after the first
# call, if every call on it failed in a way a retry can fix. The call was
# claimed once and never tried again, so a Twilio outage at the moment of
# escalation meant no phone rang, ever.
VOICE_RETRY_AFTER_MINUTES = (5, 15)
RETRYABLE_VOICE_STATUSES = frozenset(
    {"call_failed", "client_unavailable", "delivery_error"}
)

# When the sweep tries an alert email again that the mail host would not
# take, in minutes after it was first tried, while the alert is open. The
# hourly queue gave it three tries in three hours and then dropped it.
ALERT_MAIL_RETRY_MINUTES = (1, 5, 15, 30, 60, 120, 240, 480)

# How long after a voice call an operator may deliberately place another,
# because nobody picked up. Short enough to be useful; long enough that a
# double-click, a retried request, or a dozen operators reacting to the
# same alert at once all collapse into the one call that was meant.
VOICE_REDIAL_COOLDOWN_MINUTES = 5.0


def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def iso(moment: Optional[datetime]) -> Optional[str]:
    """Render a datetime as an ISO-8601 UTC string, passing None through."""
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_secret(value: str) -> str:
    """How a bearer secret is kept at rest: its SHA-256, never the secret."""
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _parse(value: Optional[str]) -> Optional[datetime]:
    """Read back a stored ISO-8601 UTC string."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def mask_email(address: str) -> str:
    """Render an address for a log line without reprinting it in full.

    The mail log is read by whoever holds the platform key, from a
    browser, over a screen share. It needs to answer "did the chase to
    Northgate go out", not to hand every customer's contact address to
    whoever is looking over a shoulder. Enough of the local part survives
    to recognise an address you already know.
    """
    address = (address or "").strip()
    if "@" not in address:
        return "(no address)" if not address else address[:1] + "***"
    local, _, domain = address.partition("@")
    if len(local) <= 2:
        keep = local[:1]
    else:
        keep = local[:2]
    return f"{keep}{'*' * max(len(local) - len(keep), 1)}@{domain}"


# Readings are stored in Fahrenheit throughout and converted only for
# display, so a tenant switching units never rewrites its own history.
TEMPERATURE_UNITS = ("F", "C")


# Below this a reading is not a cold freezer, it is a broken sensor:
# -459.67 °F is absolute zero, and nothing physical reports beneath it.
# Above it, no monitored asset survives conditions this hot, so a reading
# out here is a fault in the device rather than a fact about the world.
ABSOLUTE_ZERO_F = -459.67
IMPLAUSIBLE_ABOVE_F = 1000.0


class SeatClaimRefused(Exception):
    """A sensor could not be registered, with a reason worth showing.

    Carries `reason` so the HTTP layer can distinguish a duplicate id from
    an exhausted licence without matching on message text.
    """

    def __init__(self, detail: str, reason: str) -> None:
        super().__init__(detail)
        self.detail = detail
        self.reason = reason


class ImplausibleReading(ValueError):
    """A reading no physical sensor could have produced.

    Raised rather than stored. The alternative is worse than it looks: a
    NaN compares false against every threshold, so a sensor emitting one
    is judged nominal forever while the asset behind it fails — and NaN
    also serialises to invalid JSON, which takes out the compliance
    report and the vault attestation for that tenant permanently.
    """


def require_plausible(
    value: Optional[float], field: str = "temperature_fahrenheit"
) -> Optional[float]:
    """Refuse a value that cannot be true, before it reaches storage."""
    if value is None:
        return None
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        raise ImplausibleReading(
            f"{field} must be a finite number; received {value!r}. A "
            "non-finite reading compares false against every threshold, so "
            "storing it would make a failing asset look healthy."
        )
    if field == "temperature_fahrenheit" and not (
        ABSOLUTE_ZERO_F <= value <= IMPLAUSIBLE_ABOVE_F
    ):
        raise ImplausibleReading(
            f"temperature_fahrenheit {value} is outside the physically "
            f"possible range {ABSOLUTE_ZERO_F} to {IMPLAUSIBLE_ABOVE_F}. "
            "Check the device: this is a sensor fault, not a cold asset."
        )
    return value


def to_celsius(fahrenheit: Optional[float]) -> Optional[float]:
    if fahrenheit is None:
        return None
    return round((fahrenheit - 32.0) * 5.0 / 9.0, 2)


def to_fahrenheit(celsius: Optional[float]) -> Optional[float]:
    if celsius is None:
        return None
    return round(celsius * 9.0 / 5.0 + 32.0, 2)


def display_temperature(fahrenheit: Optional[float], unit: str) -> Optional[float]:
    """Render a stored Fahrenheit reading in the tenant's chosen unit."""
    if fahrenheit is None:
        return None
    return to_celsius(fahrenheit) if unit == "C" else round(fahrenheit, 2)


def format_temperature(fahrenheit: Optional[float], unit: str = "F") -> str:
    """A reading written the way the customer reads it: "4.44°C"."""
    value = display_temperature(fahrenheit, unit)
    if value is None:
        return "not reported"
    return f"{value}°{unit}"


def plain_temperature(fahrenheit: Optional[float], unit: str = "F") -> str:
    """"43°F" rather than "43.0°F": the way a person writes it on a log."""
    value = display_temperature(fahrenheit, unit)
    if value is None:
        return "not reported"
    return f"{value:g}°{unit}" if float(value).is_integer() else f"{value}°{unit}"


_WRITTEN_TEMPERATURE = re.compile(r"(-?\d+(?:\.\d+)?)°([FC])\b")


def restate_temperatures(text: Optional[str], unit: str = "F") -> str:
    """Rewrite every "40.0°F" in a stored sentence into `unit`.

    A breach is worded once, on the night, in the unit the account had
    then. An account that moves to Celsius afterwards was handed a Celsius
    document with that one line still in Fahrenheit -- the same walk-in at
    40 in one sentence and 4.44 in the next. Only figures written the way
    `format_temperature` writes them are touched.
    """
    if not text:
        return text or ""

    def _swap(match: "re.Match[str]") -> str:
        value, written_in = float(match.group(1)), match.group(2)
        if written_in == unit:
            return match.group(0)
        fahrenheit = value if written_in == "F" else to_fahrenheit(value)
        return format_temperature(fahrenheit, unit)

    return _WRITTEN_TEMPERATURE.sub(_swap, text)


def spoken_temperature(fahrenheit: Optional[float], unit: str = "F") -> str:
    """A reading a text-to-speech engine can say out loud.

    "4.44°C" is read back as gibberish by Twilio's voice, so the emergency
    call spells the unit out.
    """
    value = display_temperature(fahrenheit, unit)
    if value is None:
        return "an unreported temperature"
    return f"{value} degrees {'Celsius' if unit == 'C' else 'Fahrenheit'}"


# Master definition of the target industries, their common catastrophes,
# and threshold bounds. Thresholds are exclusive: a reading exactly at the
# limit is nominal.
INDUSTRY_PROFILES: Dict[str, Dict[str, Any]] = {
    "cybersecurity": {
        "name": "Data centers",
        "catastrophe": "Cooling failed (a tripped circuit or a stalled fan)",
        "slogan": "A fan stalls at three. Nobody notices until the customer does.",
        "danger_above": 78.0,
        "danger_below": None,
        "unit": "°F",
        "asset_noun": "rack",
        "asset_plural": "racks",
        "shortcut_name": "Automated CISO Incident Briefing",
        "shortcut_description": (
            "Compiles raw server-log alerts into an executive-ready compliance summary for board reviews."
        ),
    },
    "pharmacy": {
        "name": "Retail & Hospital Pharmacies",
        "catastrophe": "Vaccine Refrigerator Compressor Failure",
        "slogan": "A vaccine fridge drifts at two. Nobody checks until seven.",
        "danger_above": 46.0,
        "danger_below": 36.0,
        "unit": "°F",
        "asset_noun": "refrigerator",
        "asset_plural": "refrigerators",
        "shortcut_name": "VFC Storage & Handling Record",
        "shortcut_description": (
            "Produces the continuous temperature record a vaccine program "
            "audit asks for, with every excursion and its response."
        ),
    },
    "wine_and_art": {
        "name": "Fine Wine & Art Storage",
        "catastrophe": "Cellar Climate Control Failure",
        "slogan": "The cellar warms at midnight. Nobody tastes it for ten years.",
        "danger_above": 60.0,
        "danger_below": 50.0,
        "unit": "°F",
        "asset_noun": "cellar",
        "asset_plural": "cellars",
        "shortcut_name": "Provenance Climate Certificate",
        "shortcut_description": (
            "Certifies unbroken storage conditions for a collection, which is "
            "what a buyer, an appraiser and a fine-art insurer each ask for."
        ),
    },
    "cannabis": {
        "name": "Licensed Cannabis Cultivation",
        "catastrophe": "Drying Room Climate Excursion",
        "slogan": (
            "The drying room warms overnight. Nobody knows until the "
            "lab says so."
        ),
        "danger_above": 72.0,
        "danger_below": 58.0,
        "unit": "°F",
        "asset_noun": "room",
        "asset_plural": "rooms",
        "shortcut_name": "State Compliance Cultivation Log",
        "shortcut_description": (
            "Formats environmental records into the log a state cannabis "
            "regulator requires alongside seed-to-sale tracking."
        ),
    },
    "restaurant": {
        "name": "Restaurants & Cafés",
        "catastrophe": "Fridge or freezer warming (door left open, or the cooling failing)",
        "slogan": "A walk-in fails on Friday. Nobody opens it until Monday.",
        # A fridge's limit, because most of what a kitchen watches is a
        # fridge: food kept cold must stay at 41°F or below. A freezer is
        # chosen on the add form and gets its own limit (UNIT_KINDS). This
        # was 32°F, a freezer-ish number, so a healthy walk-in cooler at
        # 37°F opened a critical alarm on its first reading.
        "danger_above": 41.0,
        "danger_below": None,
        "unit": "°F",
        "asset_noun": "walk-in",
        "asset_plural": "walk-ins",
        "shortcut_name": "Health Inspector Temperature Log",
        "shortcut_description": (
            "Formats temperature logs into official local health department audit sheets."
        ),
    },
    "logistics": {
        "name": "High-Stakes Cold-Chain Transport",
        "catastrophe": "Reefer Truck Auxiliary Diesel Engine Stall",
        "slogan": "A reefer stalls in Nebraska. Nobody notices until Ohio.",
        "danger_above": 40.0,
        "danger_below": None,
        "unit": "°F",
        "asset_noun": "reefer",
        "asset_plural": "reefers",
        "shortcut_name": "Reefer Cargo Handover Pass",
        "shortcut_description": (
            "Generates a transit temperature guarantee certificate for delivery dock receivers."
        ),
    },
    "solar_infrastructure": {
        "name": "Solar Infrastructure & Storage",
        "catastrophe": "Inverter Thermal Runaway Overload",
        "slogan": (
            "An inverter runs hot at noon. Nobody is on site until "
            "Thursday."
        ),
        "danger_above": 115.0,
        "danger_below": None,
        "unit": "°F",
        "asset_noun": "enclosure",
        "asset_plural": "enclosures",
        "shortcut_name": "Grid Thermal Yield Report",
        "shortcut_description": (
            "Calculates battery thermal efficiency loss and maps preventative cleaning schedules."
        ),
    },
    "medical_lab": {
        "name": "Medical Labs & Blood Banks",
        "catastrophe": "Specimen Refrigerator Door Seal Degradation",
        "slogan": "A specimen warms overnight. Nobody can take it again.",
        "danger_above": 46.0,
        "danger_below": 36.0,
        "unit": "°F",
        "asset_noun": "cooler",
        "asset_plural": "coolers",
        "shortcut_name": "OSHA Cold-Storage Specimen Audit",
        "shortcut_description": (
            "Creates chain-of-custody temperature validation logs for vaccine and plasma vaults."
        ),
    },
    "cryostorage": {
        "name": "IVF Clinics & Cryostorage",
        "catastrophe": "Cryogenic Tank Nitrogen Boil-Off / Vapour Space Warming",
        "slogan": "The tank warms on Saturday. The phone calls start on Monday.",
        # Vapour-phase storage sits near −190°C. What actually matters is
        # the glass transition of water at about −130°C (−202°F): warmer
        # than that and ice crystals form inside the cells. The alarm is
        # set at −150°C (−238°F), which leaves a real margin to act in
        # rather than a moment to watch it happen.
        "danger_above": -238.0,
        # Nothing inside a nitrogen dewar can be colder than liquid
        # nitrogen at −320.8°F, so a colder reading is not a colder tank —
        # it is a failed probe, and a failed probe on this asset is the
        # dangerous case, because it reads as permanently safe.
        "danger_below": -325.0,
        "unit": "°F",
        "asset_noun": "tank",
        "asset_plural": "tanks",
        "shortcut_name": "Cryostorage Continuity Record",
        "shortcut_description": (
            "Proves every tank held vapour-phase temperature across a given "
            "window, and names who was alerted when one did not."
        ),
    },
    "private_aviation": {
        "name": "Private Aviation Hangars",
        "catastrophe": "Hangar Bay Humidity Moisture Infiltration",
        "slogan": (
            "The hangar sweats all winter. Nobody finds it until the "
            "annual."
        ),
        "danger_above": 85.0,  # Heat/humidity proxy
        "danger_below": None,
        "unit": "°F",
        "asset_noun": "hangar bay",
        "asset_plural": "hangar bays",
        "shortcut_name": "Hangar Avionics Humidity Log",
        "shortcut_description": (
            "Generates FAA-compliant environmental storage logs for sensitive flight computer components."
        ),
    },
    "superyacht": {
        "name": "Luxury Superyacht Engine Bays",
        "catastrophe": "Engine Room Ventilation Airflow Blockage",
        "slogan": (
            "The engine bay heats at anchor. The nearest engineer is a "
            "day away."
        ),
        "danger_above": 90.0,
        "danger_below": None,
        "unit": "°F",
        "asset_noun": "engine bay",
        "asset_plural": "engine bays",
        "shortcut_name": "Charter Guest Galley Safety Memo",
        "shortcut_description": (
            "Compiles engine room thermal safety logs into a digital report for yacht captains and owners."
        ),
    },
    "country_club": {
        "name": "High-End Country Clubs",
        "catastrophe": "Clubhouse kitchen fridge or freezer warming",
        "slogan": (
            "The walk-in dies Sunday night. Two hundred covers are "
            "booked Tuesday."
        ),
        # A fridge's limit, as for restaurants; a freezer is chosen on the
        # add form (UNIT_KINDS).
        "danger_above": 41.0,
        "danger_below": None,
        "unit": "°F",
        "asset_noun": "kitchen",
        "asset_plural": "kitchens",
        "shortcut_name": "Clubhouse Kitchen Inventory Safe-Guard",
        "shortcut_description": (
            "Calculates potential food loss prevention savings for executive club managers."
        ),
    },
}

# The limits a sector used to apply by default, before 3.19.0. A sensor
# saved before then with no limit of its own was alarming on these, and
# keeps alarming on them (records.Sensor.from_row pins them) until its
# owner changes it: a new default must never quietly move the line an
# existing fridge alarms at.
LEGACY_DANGER_ABOVE: Dict[str, float] = {
    "restaurant": 32.0,
    "country_club": 32.0,
}

# "Fridge or freezer?" -- what the add form asks, and the limit each answer
# gives. Only the sectors where the answer changes the limit have a list;
# every other sector's unit takes the sector's own limit. The first entry
# is the form's default.
#
# Food: a fridge holds 41°F or below (the food code's cold-holding rule).
# A freezer should sit near 0°F; the alarm is at 10°F so a normal defrost
# does not page anyone, while a freezer that is really failing does.
# Vaccines: a fridge between 36°F and 46°F (2-8°C), a freezer between
# -58°F and 5°F (-50°C to -15°C).
_FOOD_KINDS: List[Dict[str, Any]] = [
    {"kind": "fridge", "label": "Fridge", "danger_above": 41.0, "danger_below": None},
    {"kind": "freezer", "label": "Freezer", "danger_above": 10.0, "danger_below": None},
]
_CLINICAL_KINDS: List[Dict[str, Any]] = [
    {"kind": "fridge", "label": "Fridge", "danger_above": 46.0, "danger_below": 36.0},
    {"kind": "freezer", "label": "Freezer", "danger_above": 5.0, "danger_below": -58.0},
]
UNIT_KINDS: Dict[str, List[Dict[str, Any]]] = {
    "restaurant": _FOOD_KINDS,
    "country_club": _FOOD_KINDS,
    "pharmacy": _CLINICAL_KINDS,
    "medical_lab": _CLINICAL_KINDS,
}


def unit_kinds(vertical: str) -> List[Dict[str, Any]]:
    """The "fridge or freezer?" choices for a sector, or [] for none.

    Each carries how long that kind waits before alerting
    (`alert_after_minutes`), so the add form can say the real rule.
    """
    out = []
    for k in UNIT_KINDS.get(vertical, []):
        clinical = vertical in _CLINICAL_SECTORS
        if k["kind"] == "freezer":
            wait = (ALERT_AFTER_MINUTES_CLINICAL_FREEZER if clinical
                    else ALERT_AFTER_MINUTES_FOOD_FREEZER)
        else:
            wait = (ALERT_AFTER_MINUTES_CLINICAL_FRIDGE if clinical
                    else ALERT_AFTER_MINUTES_FOOD_FRIDGE)
        out.append(dict(k, alert_after_minutes=wait,
                        alert_at_once_degrees=ALERT_AT_ONCE_DEGREES_F))
    return out


def unit_kind_limits(
    vertical: str, kind: Optional[str]
) -> Optional[Tuple[Optional[float], Optional[float]]]:
    """(above, below) for one answer to "fridge or freezer?".

    None when the sector has no such choice or the answer is not one of
    its choices, so the caller can refuse it rather than guess.
    """
    for entry in UNIT_KINDS.get(vertical, []):
        if entry["kind"] == (kind or "").strip().lower():
            return entry["danger_above"], entry["danger_below"]
    return None


# Commercial plan tiers. Seat count is the number of registered sensors.
#
# `max_locations` is the number of sites a plan covers; None is no limit.
# Only Starter sets one. Its price is not here: every price lives in
# pricing.py (STARTER_MONTHLY_USD), so the owner changes one file.
PLAN_TIERS: Dict[str, Dict[str, Any]] = {
    "trial": {
        "name": "Trial",
        "max_sensors": 5,
        "max_locations": None,
        "term_days": 14,
        "voice_escalation": False,
        "predictive_forecasting": False,
    },
    # A small cafe or shop's first yes: one location, up to three fridges
    # or freezers, a flat monthly price. Texts, the site-wide alert, the
    # inspector's log and the monthly report are all on it, because the
    # things that keep food safe are not upsells. Phone calls and the
    # forecast are what moving up to Growth adds.
    "starter": {
        "name": "Starter",
        "max_sensors": 3,
        "max_locations": 1,
        "term_days": 365,
        "voice_escalation": False,
        "predictive_forecasting": False,
    },
    "growth": {
        "name": "Growth",
        "max_sensors": 50,
        "max_locations": None,
        "term_days": 365,
        "voice_escalation": True,
        "predictive_forecasting": True,
    },
    "enterprise": {
        "name": "Enterprise",
        "max_sensors": 1000,
        "max_locations": None,
        "term_days": 365,
        "voice_escalation": True,
        "predictive_forecasting": True,
    },
}


def resolve_vertical(raw: str) -> Optional[str]:
    """Normalise a caller-supplied vertical key, or None if unknown."""
    key = (raw or "").strip().lower()
    return key if key in INDUSTRY_PROFILES else None


def evaluate_breach(
    vertical: str,
    temperature: float,
    above: Optional[float] = None,
    below: Optional[float] = None,
    unit: str = "F",
) -> Optional[str]:
    """Return a human-readable breach reason, or None when nominal.

    `above` and `below` override the industry defaults for one sensor — a
    particular freezer may be held colder than its sector's rule of thumb.
    Thresholds are held in Fahrenheit and written in `unit`, so a German
    kitchen reads a limit it recognises without a second stored copy.
    """
    profile = INDUSTRY_PROFILES[vertical]
    if above is None:
        above = profile["danger_above"]
    if below is None:
        below = profile["danger_below"]

    # NaN compares false against everything, so without this a sensor
    # emitting one would be scored nominal on every reading forever. A
    # reading we cannot judge is a fault to be raised, never a pass.
    if math.isnan(temperature) or math.isinf(temperature):
        return (
            f"Sensor fault: reported {temperature}, which is not a "
            "temperature. The asset is unmonitored until the device is "
            "checked."
        )

    if above is not None and temperature > above:
        return (
            f"Too warm: {plain_temperature(temperature, unit)} "
            f"(limit {plain_temperature(above, unit)})."
        )
    if below is not None and temperature < below:
        return (
            f"Too cold: {plain_temperature(temperature, unit)} "
            f"(limit {plain_temperature(below, unit)})."
        )
    return None


def evaluate_sensor_breach(
    sensor: "Sensor", temperature: float, unit: str = "F"
) -> Optional[str]:
    """Score a reading against the sensor's own effective bounds."""
    above, below = sensor.bounds()
    return evaluate_breach(
        sensor.industry_vertical, temperature, above, below, unit
    )


# Volume pricing for chains lives in pricing.py (CHAIN_DISCOUNT_TIERS), the
# one place every price is set. The enterprise ladder that used to be here
# (2.5% off for every ten units, up to 10%) was replaced by it.
