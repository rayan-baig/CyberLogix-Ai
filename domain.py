"""The fixed rules the store applies.

Service times, temperature units, the industry profiles and plans, the
breach check and volume pricing: plain values and pure functions, with no
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
from typing import TYPE_CHECKING, Any, Dict, List, Optional

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
        "name": "CyberTech Data Centers",
        "catastrophe": "HVAC Circuit Trip / Cooling Fan Stalled",
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
            "Produces the continuous temperature record a vaccine programme "
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
        "name": "Franchise Restaurants",
        "catastrophe": "Unlatched Walk-In Freezer Door Gasket Failure",
        "slogan": "A walk-in fails on Friday. Nobody opens it until Monday.",
        "danger_above": 32.0,
        "danger_below": None,
        "unit": "°F",
        "asset_noun": "walk-in",
        "asset_plural": "walk-ins",
        "shortcut_name": "Health Inspector Log Formatter",
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
        "catastrophe": "Clubhouse Kitchen Walk-In Compressor Failure",
        "slogan": (
            "The walk-in dies Sunday night. Two hundred covers are "
            "booked Tuesday."
        ),
        "danger_above": 32.0,
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

# Commercial plan tiers. Seat count is the number of registered sensors.
PLAN_TIERS: Dict[str, Dict[str, Any]] = {
    "trial": {
        "name": "Trial",
        "max_sensors": 5,
        "term_days": 14,
        "voice_escalation": False,
        "predictive_forecasting": False,
    },
    "growth": {
        "name": "Growth",
        "max_sensors": 50,
        "term_days": 365,
        "voice_escalation": True,
        "predictive_forecasting": True,
    },
    "enterprise": {
        "name": "Enterprise",
        "max_sensors": 1000,
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
            f"Thermal high threshold breached: "
            f"{format_temperature(temperature, unit)} > "
            f"{format_temperature(above, unit)} limit."
        )
    if below is not None and temperature < below:
        return (
            f"Thermal low threshold breached: "
            f"{format_temperature(temperature, unit)} < "
            f"{format_temperature(below, unit)} limit."
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


# Enterprise volume pricing.
#
# A contract is the vertical's own unit price times the enrolled count, less
# a volume discount that deepens every ten units. The discount is a
# percentage rather than a fixed dollar step, so one ladder serves all eight
# verticals whatever their rate.
#
# The cap is deliberately shallow. At the top of a market a deep discount
# reads as eagerness, and every point of it comes straight off the largest
# accounts — the ones worth the most.
VOLUME_DISCOUNT_STEP_PERCENT = 2.5
VOLUME_DISCOUNT_EVERY_UNITS = 10
MAX_VOLUME_DISCOUNT_PERCENT = 10.0

# A flat ceiling on a contract, or None for no ceiling. A cap is a hard stop
# on revenue from exactly the accounts worth most: at these rates a chain of
# eleven vessels would hit $50,000 and every vessel after that would be free.
ENTERPRISE_MONTHLY_CAP: Optional[float] = None


def volume_discount_percent(units: int) -> float:
    """The volume discount an estate of this size earns."""
    if units <= 0:
        return 0.0
    steps = units // VOLUME_DISCOUNT_EVERY_UNITS
    return min(MAX_VOLUME_DISCOUNT_PERCENT, VOLUME_DISCOUNT_STEP_PERCENT * steps)


def _raw_total(units: int, unit_price: float) -> float:
    return units * unit_price * (1 - volume_discount_percent(units) / 100.0)


def calculate_volume_tier_price(units: int, unit_price: float) -> float:
    """Monthly price for an enrolled unit count at a vertical's rate.

    The discount deepens a whole band at a time, so the ladder alone is not
    monotonic: 40 units at 10% off can undercut 39 at 7.5% off. A customer
    would find that and rightly demand the lower figure, so no estate is
    charged more than a larger estate would pay.
    """
    if units <= 0:
        return 0.0
    horizon = units + 2 * VOLUME_DISCOUNT_EVERY_UNITS
    best = min(_raw_total(n, unit_price) for n in range(units, horizon + 1))
    if ENTERPRISE_MONTHLY_CAP is not None:
        best = min(best, ENTERPRISE_MONTHLY_CAP)
    return round(best, 2)


def volume_band(units: int) -> tuple:
    """The (low, high) unit counts sharing this estate's discount."""
    if units <= 0:
        return (0, 0)
    plateau = int(
        MAX_VOLUME_DISCOUNT_PERCENT / VOLUME_DISCOUNT_STEP_PERCENT
    ) * VOLUME_DISCOUNT_EVERY_UNITS
    if units >= plateau:
        return (plateau, None)
    index = units // VOLUME_DISCOUNT_EVERY_UNITS
    low = 1 if index == 0 else index * VOLUME_DISCOUNT_EVERY_UNITS
    return (low, index * VOLUME_DISCOUNT_EVERY_UNITS + 9)


def volume_tier_label(units: int) -> str:
    """Which discount band a unit count lands in."""
    if units <= 0:
        return "no units enrolled"
    low, high = volume_band(units)
    discount = volume_discount_percent(units)
    span = f"{low}+" if high is None else f"{low}-{high}"
    if discount == 0:
        return f"{span} units at list"
    return f"{span} units, {discount:g}% volume discount"


def next_volume_tier(units: int, unit_price: float) -> Optional[Dict[str, Any]]:
    """The next discount step, and what the estate would pay there."""
    if units <= 0 or volume_discount_percent(units) >= MAX_VOLUME_DISCOUNT_PERCENT:
        return None

    _, high = volume_band(units)
    boundary = high + 1
    here = calculate_volume_tier_price(units, unit_price)
    there = calculate_volume_tier_price(boundary, unit_price)
    return {
        "units_until_next_discount": boundary - units,
        "next_discount_at_units": boundary,
        "next_discount_label": volume_tier_label(boundary),
        "next_discount_percent": volume_discount_percent(boundary),
        "next_tier_monthly_usd": there,
        "monthly_increase_usd": round(there - here, 2),
    }


def volume_band_table(unit_price: float) -> List[Dict[str, Any]]:
    """The discount ladder, priced against one vertical's rate."""
    bands = int(MAX_VOLUME_DISCOUNT_PERCENT / VOLUME_DISCOUNT_STEP_PERCENT) + 1
    rows = []
    for index in range(bands):
        low = 1 if index == 0 else index * VOLUME_DISCOUNT_EVERY_UNITS
        plateau = index * VOLUME_DISCOUNT_STEP_PERCENT >= MAX_VOLUME_DISCOUNT_PERCENT
        high = None if plateau else index * VOLUME_DISCOUNT_EVERY_UNITS + 9
        example = high if high is not None else low
        rows.append(
            {
                "band": f"{low}+" if high is None else f"{low}-{high}",
                "from_units": low,
                "to_units": high,
                "discount_percent": volume_discount_percent(low),
                "effective_unit_price_usd": round(
                    unit_price * (1 - volume_discount_percent(low) / 100.0), 2
                ),
                "example_units": example,
                "example_monthly_usd": calculate_volume_tier_price(
                    example, unit_price
                ),
                "example_annual_usd": round(
                    calculate_volume_tier_price(example, unit_price) * 12, 2
                ),
            }
        )
    return rows
