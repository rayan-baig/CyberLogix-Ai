"""Shared domain model and in-memory state for the CyberLogix AI hub.

Every router in the suite reads and writes through the single `STORE`
instance defined at the bottom of this module. State is held in memory and
guarded by a re-entrant lock, which is correct for a single Cloud Run
instance. Swapping this class for a Firestore or Postgres adapter is the
one change required to scale horizontally.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional

from db import Database

logger = logging.getLogger("cyberlogix.store")

# Readings retained per sensor. At a 5-minute pulse this is ~41 hours of
# history, which comfortably covers the forecaster's trend window.
MAX_READINGS_PER_SENSOR = 500

# A sensor silent for longer than this is treated as offline by the
# autonomous compliance clerk.
SENSOR_OFFLINE_AFTER_MINUTES = 30

# How long a breach may sit unacknowledged before voice escalation is due.
VOICE_ESCALATION_GRACE_MINUTES = 10


def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def iso(moment: Optional[datetime]) -> Optional[str]:
    """Render a datetime as an ISO-8601 UTC string, passing None through."""
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: Optional[str]) -> Optional[datetime]:
    """Read back a stored ISO-8601 UTC string."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# Readings are stored in Fahrenheit throughout and converted only for
# display, so a tenant switching units never rewrites its own history.
TEMPERATURE_UNITS = ("F", "C")


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


def spoken_temperature(fahrenheit: Optional[float], unit: str = "F") -> str:
    """A reading a text-to-speech engine can say out loud.

    "4.44°C" is read back as gibberish by Twilio's voice, so the emergency
    call spells the unit out.
    """
    value = display_temperature(fahrenheit, unit)
    if value is None:
        return "an unreported temperature"
    return f"{value} degrees {'Celsius' if unit == 'C' else 'Fahrenheit'}"


def hash_password(password: str) -> str:
    """Hash a password with scrypt and a fresh random salt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored scrypt hash, in constant time."""
    try:
        algorithm, salt_hex, digest_hex = stored.split("$")
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=16384,
            r=8,
            p=1,
            dklen=32,
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(digest.hex(), digest_hex)


# Master definition of the 8 target industries, their common catastrophes,
# and threshold bounds. Thresholds are exclusive: a reading exactly at the
# limit is nominal.
INDUSTRY_PROFILES: Dict[str, Dict[str, Any]] = {
    "cybersecurity": {
        "name": "CyberTech Data Centers",
        "catastrophe": "HVAC Circuit Trip / Cooling Fan Stalled",
        "danger_above": 78.0,
        "danger_below": None,
        "unit": "°F",
        "shortcut_name": "Automated CISO Incident Briefing",
        "shortcut_description": (
            "Compiles raw server-log alerts into an executive-ready compliance summary for board reviews."
        ),
    },
    "restaurant": {
        "name": "Franchise Restaurants",
        "catastrophe": "Unlatched Walk-In Freezer Door Gasket Failure",
        "danger_above": 32.0,
        "danger_below": None,
        "unit": "°F",
        "shortcut_name": "Health Inspector Log Formatter",
        "shortcut_description": (
            "Formats temperature logs into official local health department audit sheets."
        ),
    },
    "logistics": {
        "name": "High-Stakes Cold-Chain Transport",
        "catastrophe": "Reefer Truck Auxiliary Diesel Engine Stall",
        "danger_above": 40.0,
        "danger_below": None,
        "unit": "°F",
        "shortcut_name": "Reefer Cargo Handover Pass",
        "shortcut_description": (
            "Generates a transit temperature guarantee certificate for delivery dock receivers."
        ),
    },
    "solar_infrastructure": {
        "name": "Solar Infrastructure & Storage",
        "catastrophe": "Inverter Thermal Runaway Overload",
        "danger_above": 115.0,
        "danger_below": None,
        "unit": "°F",
        "shortcut_name": "Grid Thermal Yield Report",
        "shortcut_description": (
            "Calculates battery thermal efficiency loss and maps preventative cleaning schedules."
        ),
    },
    "medical_lab": {
        "name": "Medical Labs & Blood Banks",
        "catastrophe": "Specimen Refrigerator Door Seal Degradation",
        "danger_above": 46.0,
        "danger_below": 36.0,
        "unit": "°F",
        "shortcut_name": "OSHA Cold-Storage Specimen Audit",
        "shortcut_description": (
            "Creates chain-of-custody temperature validation logs for vaccine and plasma vaults."
        ),
    },
    "private_aviation": {
        "name": "Private Aviation Hangars",
        "catastrophe": "Hangar Bay Humidity Moisture Infiltration",
        "danger_above": 85.0,  # Heat/humidity proxy
        "danger_below": None,
        "unit": "°F",
        "shortcut_name": "Hangar Avionics Humidity Log",
        "shortcut_description": (
            "Generates FAA-compliant environmental storage logs for sensitive flight computer components."
        ),
    },
    "superyacht": {
        "name": "Luxury Superyacht Engine Bays",
        "catastrophe": "Engine Room Ventilation Airflow Blockage",
        "danger_above": 90.0,
        "danger_below": None,
        "unit": "°F",
        "shortcut_name": "Charter Guest Galley Safety Memo",
        "shortcut_description": (
            "Compiles engine room thermal safety logs into a digital report for yacht captains and owners."
        ),
    },
    "country_club": {
        "name": "High-End Country Clubs",
        "catastrophe": "Clubhouse Kitchen Walk-In Compressor Failure",
        "danger_above": 32.0,
        "danger_below": None,
        "unit": "°F",
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


@dataclass
class Tenant:
    """A paying customer organisation."""

    tenant_id: str
    company_name: str
    contact_name: str
    contact_phone: str
    contact_email: str
    plan: str
    api_key: str
    activated_at: datetime
    expires_at: datetime
    suspended: bool = False
    temperature_unit: str = "F"

    @property
    def expired(self) -> bool:
        return utc_now() >= self.expires_at

    @property
    def active(self) -> bool:
        return not self.suspended and not self.expired

    def entitlements(self) -> Dict[str, Any]:
        return PLAN_TIERS[self.plan]

    def to_row(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "company_name": self.company_name,
            "contact_name": self.contact_name,
            "contact_phone": self.contact_phone,
            "contact_email": self.contact_email,
            "plan": self.plan,
            "api_key": self.api_key,
            "activated_at": iso(self.activated_at),
            "expires_at": iso(self.expires_at),
            "suspended": self.suspended,
            "temperature_unit": self.temperature_unit,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Tenant":
        return cls(
            tenant_id=row["tenant_id"],
            company_name=row["company_name"],
            contact_name=row["contact_name"],
            contact_phone=row["contact_phone"],
            contact_email=row["contact_email"],
            plan=row["plan"],
            api_key=row["api_key"],
            activated_at=_parse(row["activated_at"]),
            expires_at=_parse(row["expires_at"]),
            suspended=row.get("suspended", False),
            temperature_unit=row.get("temperature_unit", "F"),
        )

    def public(self, sensor_count: int) -> Dict[str, Any]:
        tier = self.entitlements()
        return {
            "tenant_id": self.tenant_id,
            "company_name": self.company_name,
            "contact_name": self.contact_name,
            "contact_phone": self.contact_phone,
            "contact_email": self.contact_email,
            "plan": self.plan,
            "plan_name": tier["name"],
            "license_active": self.active,
            "suspended": self.suspended,
            "expired": self.expired,
            "activated_at": iso(self.activated_at),
            "expires_at": iso(self.expires_at),
            "temperature_unit": self.temperature_unit,
            "seats_used": sensor_count,
            "seats_total": tier["max_sensors"],
            "seats_remaining": max(0, tier["max_sensors"] - sensor_count),
            "voice_escalation": tier["voice_escalation"],
            "predictive_forecasting": tier["predictive_forecasting"],
        }


@dataclass
class Sensor:
    """A registered physical sensor node, occupying one license seat."""

    sensor_id: str
    tenant_id: str
    industry_vertical: str
    location_name: str
    registered_at: datetime
    external_device_sn: Optional[str] = None
    site_id: Optional[str] = None
    battery_percent: Optional[float] = None
    signal_percent: Optional[float] = None
    override_above: Optional[float] = None
    override_below: Optional[float] = None
    last_seen: Optional[datetime] = None
    last_temperature: Optional[float] = None
    last_humidity: Optional[float] = None

    def bounds(self) -> tuple:
        """The thresholds actually applied to this sensor.

        An override replaces the industry default; where none is set the
        sector's own limit stands.
        """
        profile = INDUSTRY_PROFILES[self.industry_vertical]
        above = (
            self.override_above
            if self.override_above is not None
            else profile["danger_above"]
        )
        below = (
            self.override_below
            if self.override_below is not None
            else profile["danger_below"]
        )
        return above, below

    def offline(self, now: Optional[datetime] = None) -> bool:
        now = now or utc_now()
        if self.last_seen is None:
            return True
        age = (now - self.last_seen).total_seconds() / 60.0
        return age > SENSOR_OFFLINE_AFTER_MINUTES

    def to_row(self) -> Dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "tenant_id": self.tenant_id,
            "industry_vertical": self.industry_vertical,
            "location_name": self.location_name,
            "registered_at": iso(self.registered_at),
            "external_device_sn": self.external_device_sn,
            "site_id": self.site_id,
            "battery_percent": self.battery_percent,
            "signal_percent": self.signal_percent,
            "override_above": self.override_above,
            "override_below": self.override_below,
            "last_seen": iso(self.last_seen),
            "last_temperature": self.last_temperature,
            "last_humidity": self.last_humidity,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Sensor":
        return cls(
            sensor_id=row["sensor_id"],
            tenant_id=row["tenant_id"],
            industry_vertical=row["industry_vertical"],
            location_name=row["location_name"],
            registered_at=_parse(row["registered_at"]),
            external_device_sn=row.get("external_device_sn"),
            site_id=row.get("site_id"),
            battery_percent=row.get("battery_percent"),
            signal_percent=row.get("signal_percent"),
            override_above=row.get("override_above"),
            override_below=row.get("override_below"),
            last_seen=_parse(row.get("last_seen")),
            last_temperature=row.get("last_temperature"),
            last_humidity=row.get("last_humidity"),
        )

    LOW_BATTERY_PERCENT = 20.0

    @property
    def battery_low(self) -> bool:
        """A battery this low will go dark before anyone notices."""
        return (
            self.battery_percent is not None
            and self.battery_percent <= self.LOW_BATTERY_PERCENT
        )

    def public(self, unit: str = "F") -> Dict[str, Any]:
        profile = INDUSTRY_PROFILES[self.industry_vertical]
        above, below = self.bounds()
        return {
            "site_id": self.site_id,
            "battery_percent": self.battery_percent,
            "battery_low": self.battery_low,
            "signal_percent": self.signal_percent,
            "temperature_unit": unit,
            "last_temperature_display": display_temperature(
                self.last_temperature, unit
            ),
            "danger_above_display": display_temperature(above, unit),
            "danger_below_display": display_temperature(below, unit),
            "sensor_id": self.sensor_id,
            "tenant_id": self.tenant_id,
            "danger_above": above,
            "danger_below": below,
            "override_above": self.override_above,
            "override_below": self.override_below,
            "uses_override": self.override_above is not None
            or self.override_below is not None,
            "industry_vertical": self.industry_vertical,
            "industry_name": profile["name"],
            "location_name": self.location_name,
            "external_device_sn": self.external_device_sn,
            "registered_at": iso(self.registered_at),
            "last_seen": iso(self.last_seen),
            "last_temperature": self.last_temperature,
            "last_humidity": self.last_humidity,
            "online": not self.offline(),
        }


@dataclass
class Reading:
    """One telemetry sample."""

    sensor_id: str
    temperature_fahrenheit: float
    humidity_percent: Optional[float]
    breached: bool
    recorded_at: datetime
    reading_id: str = ""

    def to_row(self) -> Dict[str, Any]:
        return {
            "reading_id": self.reading_id,
            "sensor_id": self.sensor_id,
            "temperature_fahrenheit": self.temperature_fahrenheit,
            "humidity_percent": self.humidity_percent,
            "breached": self.breached,
            "recorded_at": iso(self.recorded_at),
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Reading":
        return cls(
            sensor_id=row["sensor_id"],
            temperature_fahrenheit=row["temperature_fahrenheit"],
            humidity_percent=row.get("humidity_percent"),
            breached=row["breached"],
            recorded_at=_parse(row["recorded_at"]),
            reading_id=row.get("reading_id", ""),
        )

    def public(self) -> Dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "temperature_fahrenheit": self.temperature_fahrenheit,
            "humidity_percent": self.humidity_percent,
            "breached": self.breached,
            "recorded_at": iso(self.recorded_at),
        }


@dataclass
class Incident:
    """An open or historical catastrophe event."""

    incident_id: str
    tenant_id: str
    sensor_id: str
    industry_vertical: str
    catastrophe: str
    temperature_fahrenheit: float
    breach_details: str
    sms_text: str
    sms_dispatch_source: str
    opened_at: datetime
    sms_delivery: Optional[Dict[str, Any]] = None
    voice_delivery: Optional[Dict[str, Any]] = None
    sms_fanout: List[Dict[str, Any]] = field(default_factory=list)
    voice_fanout: List[Dict[str, Any]] = field(default_factory=list)
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    voice_escalated_at: Optional[datetime] = None
    voice_script: Optional[str] = None
    voice_dispatch_source: Optional[str] = None
    resolved_at: Optional[datetime] = None
    ack_token: Optional[str] = None

    @property
    def open(self) -> bool:
        return self.resolved_at is None and self.acknowledged_at is None

    def minutes_open(self, now: Optional[datetime] = None) -> float:
        now = now or utc_now()
        end = self.acknowledged_at or self.resolved_at or now
        return round((end - self.opened_at).total_seconds() / 60.0, 2)

    def to_row(self) -> Dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "tenant_id": self.tenant_id,
            "sensor_id": self.sensor_id,
            "industry_vertical": self.industry_vertical,
            "catastrophe": self.catastrophe,
            "temperature_fahrenheit": self.temperature_fahrenheit,
            "breach_details": self.breach_details,
            "sms_text": self.sms_text,
            "sms_dispatch_source": self.sms_dispatch_source,
            "opened_at": iso(self.opened_at),
            "sms_delivery": self.sms_delivery,
            "voice_delivery": self.voice_delivery,
            "sms_fanout": self.sms_fanout,
            "voice_fanout": self.voice_fanout,
            "acknowledged_at": iso(self.acknowledged_at),
            "acknowledged_by": self.acknowledged_by,
            "voice_escalated_at": iso(self.voice_escalated_at),
            "voice_script": self.voice_script,
            "voice_dispatch_source": self.voice_dispatch_source,
            "resolved_at": iso(self.resolved_at),
            "ack_token": self.ack_token,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Incident":
        return cls(
            incident_id=row["incident_id"],
            tenant_id=row["tenant_id"],
            sensor_id=row["sensor_id"],
            industry_vertical=row["industry_vertical"],
            catastrophe=row["catastrophe"],
            temperature_fahrenheit=row["temperature_fahrenheit"],
            breach_details=row["breach_details"],
            sms_text=row["sms_text"],
            sms_dispatch_source=row["sms_dispatch_source"],
            opened_at=_parse(row["opened_at"]),
            sms_delivery=row.get("sms_delivery"),
            voice_delivery=row.get("voice_delivery"),
            sms_fanout=row.get("sms_fanout") or [],
            voice_fanout=row.get("voice_fanout") or [],
            acknowledged_at=_parse(row.get("acknowledged_at")),
            acknowledged_by=row.get("acknowledged_by"),
            voice_escalated_at=_parse(row.get("voice_escalated_at")),
            voice_script=row.get("voice_script"),
            voice_dispatch_source=row.get("voice_dispatch_source"),
            resolved_at=_parse(row.get("resolved_at")),
            ack_token=row.get("ack_token"),
        )

    def public(self) -> Dict[str, Any]:
        profile = INDUSTRY_PROFILES[self.industry_vertical]
        return {
            "incident_id": self.incident_id,
            "tenant_id": self.tenant_id,
            "sensor_id": self.sensor_id,
            "industry_vertical": self.industry_vertical,
            "industry_name": profile["name"],
            "catastrophe_type": self.catastrophe,
            "temperature_fahrenheit": self.temperature_fahrenheit,
            "breach_details": self.breach_details,
            "dispatched_sms_text": self.sms_text,
            "sms_dispatch_source": self.sms_dispatch_source,
            "sms_delivery": self.sms_delivery,
            "voice_delivery": self.voice_delivery,
            "sms_fanout": self.sms_fanout,
            "voice_fanout": self.voice_fanout,
            "notified_count": len(self.sms_fanout) or (1 if self.sms_delivery else 0),
            "opened_at": iso(self.opened_at),
            "acknowledged_at": iso(self.acknowledged_at),
            "acknowledged_by": self.acknowledged_by,
            "voice_escalated_at": iso(self.voice_escalated_at),
            "voice_script": self.voice_script,
            "voice_dispatch_source": self.voice_dispatch_source,
            "resolved_at": iso(self.resolved_at),
            "minutes_open": self.minutes_open(),
            "state": (
                "resolved"
                if self.resolved_at
                else "acknowledged"
                if self.acknowledged_at
                else "open"
            ),
        }


# Operator roles, most privileged first. Each role implies the ones below it.
ROLES = ("owner", "operator", "viewer")
ROLE_RANK = {role: index for index, role in enumerate(ROLES)}

SESSION_TTL_HOURS = 12


@dataclass
class User:
    """A named human operator inside a tenant."""

    user_id: str
    tenant_id: str
    email: str
    full_name: str
    role: str
    password_hash: str
    created_at: datetime
    last_login_at: Optional[datetime] = None
    disabled: bool = False

    def can(self, required: str) -> bool:
        """True when this user's role is at least `required`."""
        return ROLE_RANK[self.role] <= ROLE_RANK[required]

    def to_row(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "password_hash": self.password_hash,
            "created_at": iso(self.created_at),
            "last_login_at": iso(self.last_login_at),
            "disabled": self.disabled,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "User":
        return cls(
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            email=row["email"],
            full_name=row["full_name"],
            role=row["role"],
            password_hash=row["password_hash"],
            created_at=_parse(row["created_at"]),
            last_login_at=_parse(row.get("last_login_at")),
            disabled=row.get("disabled", False),
        )

    def public(self) -> Dict[str, Any]:
        """Never includes the password hash."""
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role,
            "created_at": iso(self.created_at),
            "last_login_at": iso(self.last_login_at),
            "disabled": self.disabled,
        }


@dataclass
class LoginSession:
    """A bearer token issued at login."""

    token: str
    user_id: str
    tenant_id: str
    issued_at: datetime
    expires_at: datetime

    @property
    def expired(self) -> bool:
        return utc_now() >= self.expires_at

    def to_row(self) -> Dict[str, Any]:
        return {
            "token": self.token,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "issued_at": iso(self.issued_at),
            "expires_at": iso(self.expires_at),
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "LoginSession":
        return cls(
            token=row["token"],
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            issued_at=_parse(row["issued_at"]),
            expires_at=_parse(row["expires_at"]),
        )


@dataclass
class AuditEntry:
    """One durable record of who did what.

    Compliance reports are only as good as their provenance, so every state
    change a human causes is written here with the operator's name.
    """

    entry_id: str
    tenant_id: str
    actor: str
    actor_role: str
    action: str
    detail: str
    at: datetime

    def to_row(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "tenant_id": self.tenant_id,
            "actor": self.actor,
            "actor_role": self.actor_role,
            "action": self.action,
            "detail": self.detail,
            "at": iso(self.at),
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "AuditEntry":
        return cls(
            entry_id=row["entry_id"],
            tenant_id=row["tenant_id"],
            actor=row["actor"],
            actor_role=row["actor_role"],
            action=row["action"],
            detail=row["detail"],
            at=_parse(row["at"]),
        )

    def public(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "actor": self.actor,
            "actor_role": self.actor_role,
            "action": self.action,
            "detail": self.detail,
            "at": iso(self.at),
        }


@dataclass
class UsageDay:
    """Metered usage for one tenant on one UTC day.

    Drives both the cost report and the daily spend caps.
    """

    tenant_id: str
    day: str
    ai_calls: int = 0
    ai_cache_hits: int = 0
    ai_suppressed: int = 0
    sms_sent: int = 0
    sms_suppressed: int = 0
    voice_calls: int = 0
    voice_suppressed: int = 0

    @property
    def key(self) -> str:
        return f"{self.tenant_id}|{self.day}"

    def to_row(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "day": self.day,
            "ai_calls": self.ai_calls,
            "ai_cache_hits": self.ai_cache_hits,
            "ai_suppressed": self.ai_suppressed,
            "sms_sent": self.sms_sent,
            "sms_suppressed": self.sms_suppressed,
            "voice_calls": self.voice_calls,
            "voice_suppressed": self.voice_suppressed,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "UsageDay":
        return cls(**row)

    def public(self) -> Dict[str, Any]:
        return self.to_row()


@dataclass
class Site:
    """A physical location: one restaurant, one hangar, one data hall.

    Sensors hang off a site rather than floating in a flat list. Without it
    a chain's compliance report cannot be produced per store — which is the
    only way an inspector ever wants it — and an enterprise contract's
    branch count is a number somebody typed rather than something the fleet
    can be checked against.
    """

    site_id: str
    tenant_id: str
    name: str
    address: str = ""
    created_at: Optional[datetime] = None

    def to_row(self) -> Dict[str, Any]:
        return {
            "site_id": self.site_id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "address": self.address,
            "created_at": iso(self.created_at),
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Site":
        return cls(
            site_id=row["site_id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            address=row.get("address", ""),
            created_at=_parse(row.get("created_at")),
        )

    def public(self, sensor_count: int = 0, online: int = 0) -> Dict[str, Any]:
        return {
            "site_id": self.site_id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "address": self.address,
            "created_at": iso(self.created_at),
            "sensor_count": sensor_count,
            "sensors_online": online,
        }


WEBHOOK_KINDS = ("slack", "teams", "pagerduty", "generic")


@dataclass
class AlertWebhook:
    """An outbound hook so a breach lands where the team already is.

    Most operations teams do not live in another vendor's console. They
    live in a Slack channel or a PagerDuty rotation, and an alert that
    needs somebody to log in to see it is an alert that waits.

    `target` is a webhook URL, or a routing key for PagerDuty. Either way
    it is a credential, so it is never returned whole.
    """

    webhook_id: str
    tenant_id: str
    kind: str
    target: str
    label: str = ""
    active: bool = True
    site_id: Optional[str] = None
    last_status: Optional[str] = None
    last_attempt_at: Optional[datetime] = None
    consecutive_failures: int = 0

    def to_row(self) -> Dict[str, Any]:
        return {
            "webhook_id": self.webhook_id,
            "tenant_id": self.tenant_id,
            "kind": self.kind,
            "target": self.target,
            "label": self.label,
            "active": self.active,
            "site_id": self.site_id,
            "last_status": self.last_status,
            "last_attempt_at": iso(self.last_attempt_at),
            "consecutive_failures": self.consecutive_failures,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "AlertWebhook":
        return cls(
            webhook_id=row["webhook_id"],
            tenant_id=row["tenant_id"],
            kind=row["kind"],
            target=row["target"],
            label=row.get("label", ""),
            active=row.get("active", True),
            site_id=row.get("site_id"),
            last_status=row.get("last_status"),
            last_attempt_at=_parse(row.get("last_attempt_at")),
            consecutive_failures=row.get("consecutive_failures", 0),
        )

    def masked_target(self) -> str:
        """The tail of the credential: enough to tell two apart, no more."""
        tail = self.target[-6:] if len(self.target) > 6 else "******"
        return f"...{tail}"

    def public(self) -> Dict[str, Any]:
        row = self.to_row()
        row["target"] = self.masked_target()
        return row


@dataclass
class Contact:
    """Someone who gets woken when an asset is failing.

    Alerts used to go to one number on the tenant record, which is fine for
    a single-site customer and useless for anyone with a night shift. A
    roster fans SMS out to everyone on it and walks the voice ladder in
    escalation order.
    """

    contact_id: str
    tenant_id: str
    full_name: str
    phone: str
    receives_sms: bool = True
    receives_voice: bool = True
    escalation_order: int = 1
    active: bool = True
    site_id: Optional[str] = None

    def to_row(self) -> Dict[str, Any]:
        return {
            "contact_id": self.contact_id,
            "tenant_id": self.tenant_id,
            "site_id": self.site_id,
            "full_name": self.full_name,
            "phone": self.phone,
            "receives_sms": self.receives_sms,
            "receives_voice": self.receives_voice,
            "escalation_order": self.escalation_order,
            "active": self.active,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Contact":
        return cls(**row)

    def public(self) -> Dict[str, Any]:
        return self.to_row()


@dataclass
class ResetToken:
    """A one-time password reset, issued by an owner.

    The token is shown to the owner once so they can hand it over out of
    band; only its hash is stored, so a database copy cannot be replayed.
    """

    token_hash: str
    user_id: str
    tenant_id: str
    issued_at: datetime
    expires_at: datetime
    used_at: Optional[datetime] = None

    @property
    def spent(self) -> bool:
        return self.used_at is not None or utc_now() >= self.expires_at

    def to_row(self) -> Dict[str, Any]:
        return {
            "token_hash": self.token_hash,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "issued_at": iso(self.issued_at),
            "expires_at": iso(self.expires_at),
            "used_at": iso(self.used_at),
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "ResetToken":
        return cls(
            token_hash=row["token_hash"],
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            issued_at=_parse(row["issued_at"]),
            expires_at=_parse(row["expires_at"]),
            used_at=_parse(row.get("used_at")),
        )


@dataclass
class EnterpriseContract:
    """A volume contract billed by branch rather than by unit.

    A single-site customer is billed per unit from the rate card. A chain
    is billed on volume brackets by enrolled branch count, and the contract
    covers every sensor inside those branches — which is what makes a
    four-figure branch rate coherent against a single walk-in.
    """

    account_id: str
    tenant_id: str
    company_name: str
    industry_vertical: str
    enrolled_branches: int
    billing_contact_email: str
    provisioned_at: datetime
    renews_at: datetime
    active: bool = True

    @property
    def tier_label(self) -> str:
        return volume_tier_label(self.enrolled_branches)

    @property
    def unit_price_usd(self) -> float:
        # Imported late: pricing.py depends on this module.
        from pricing import PRICE_BOOK

        return PRICE_BOOK[self.industry_vertical]["monthly_usd"]

    @property
    def monthly_usd(self) -> float:
        return calculate_volume_tier_price(
            self.enrolled_branches, self.unit_price_usd
        )

    @property
    def annual_contract_value_usd(self) -> float:
        return round(self.monthly_usd * 12, 2)

    @property
    def effective_rate_per_branch_usd(self) -> float:
        return round(self.monthly_usd / self.enrolled_branches, 2)

    @property
    def next_tier(self) -> Optional[Dict[str, Any]]:
        """Where the next discount sits, so growth holds no surprises."""
        return next_volume_tier(self.enrolled_branches, self.unit_price_usd)

    def to_row(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "tenant_id": self.tenant_id,
            "company_name": self.company_name,
            "industry_vertical": self.industry_vertical,
            "enrolled_branches": self.enrolled_branches,
            "billing_contact_email": self.billing_contact_email,
            "provisioned_at": iso(self.provisioned_at),
            "renews_at": iso(self.renews_at),
            "active": self.active,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "EnterpriseContract":
        return cls(
            account_id=row["account_id"],
            tenant_id=row["tenant_id"],
            company_name=row["company_name"],
            industry_vertical=row["industry_vertical"],
            enrolled_branches=row["enrolled_branches"],
            billing_contact_email=row["billing_contact_email"],
            provisioned_at=_parse(row["provisioned_at"]),
            renews_at=_parse(row["renews_at"]),
            active=row.get("active", True),
        )

    def public(self) -> Dict[str, Any]:
        return {
            "account_id": self.account_id,
            "tenant_id": self.tenant_id,
            "company_name": self.company_name,
            "industry_vertical": self.industry_vertical,
            "industry": INDUSTRY_PROFILES[self.industry_vertical]["name"],
            "enrolled_branches": self.enrolled_branches,
            "pricing_tier_applied": "Custom Enterprise Volume Bracket",
            "unit_price_usd": self.unit_price_usd,
            "volume_discount_percent": volume_discount_percent(
                self.enrolled_branches
            ),
            # The specific bracket, alongside the contract label, so the
            # figure can be traced to the row of the table that produced it.
            "pricing_bracket": self.tier_label,
            "next_tier": self.next_tier,
            "monthly_subscription_usd": self.monthly_usd,
            "annual_contract_value_usd": self.annual_contract_value_usd,
            "effective_monthly_rate_per_branch_usd": self.effective_rate_per_branch_usd,
            "billing_contact_email": self.billing_contact_email,
            "status": "active" if self.active else "cancelled",
            "provisioned_at": iso(self.provisioned_at),
            "contract_renew_date": self.renews_at.strftime("%Y-%m-%d"),
        }


class HubStore:
    """Thread-safe in-memory persistence shared by every router."""

    def __init__(self, db: Optional[Database] = None) -> None:
        self._lock = threading.RLock()
        self._tenants: Dict[str, Tenant] = {}
        self._keys: Dict[str, str] = {}
        self._sensors: Dict[str, Sensor] = {}
        self._devices: Dict[str, str] = {}
        self._readings: Dict[str, Deque[Reading]] = {}
        self._incidents: Dict[str, Incident] = {}
        self._users: Dict[str, User] = {}
        self._emails: Dict[str, str] = {}
        self._sessions: Dict[str, LoginSession] = {}
        self._audit: Dict[str, AuditEntry] = {}
        self._sites: Dict[str, Site] = {}
        self._contacts: Dict[str, Contact] = {}
        self._contracts: Dict[str, EnterpriseContract] = {}
        self._resets: Dict[str, ResetToken] = {}
        self._webhooks: Dict[str, AlertWebhook] = {}
        self._usage: Dict[str, UsageDay] = {}
        self._ai_cache: Dict[str, str] = {}
        self._counter = 0
        self._db = db if db is not None else Database()
        self.load()

    # ---- persistence ---------------------------------------------------

    def load(self) -> None:
        """Rebuild the working set from disk. Safe to call on an empty file."""
        with self._lock:
            for row in self._db.all("tenant"):
                tenant = Tenant.from_row(row)
                self._tenants[tenant.tenant_id] = tenant
                self._keys[tenant.api_key] = tenant.tenant_id

            for row in self._db.all("sensor"):
                sensor = Sensor.from_row(row)
                self._sensors[sensor.sensor_id] = sensor
                self._readings.setdefault(
                    sensor.sensor_id, deque(maxlen=MAX_READINGS_PER_SENSOR)
                )
                if sensor.external_device_sn:
                    self._devices[sensor.external_device_sn] = sensor.sensor_id

            readings = [Reading.from_row(row) for row in self._db.all("reading")]
            readings.sort(key=lambda r: r.recorded_at)
            for reading in readings:
                bucket = self._readings.get(reading.sensor_id)
                if bucket is not None:
                    bucket.append(reading)

            for row in self._db.all("incident"):
                incident = Incident.from_row(row)
                self._incidents[incident.incident_id] = incident

            for row in self._db.all("user"):
                user = User.from_row(row)
                self._users[user.user_id] = user
                self._emails[user.email] = user.user_id

            for row in self._db.all("session"):
                session = LoginSession.from_row(row)
                if session.expired:
                    self._db.delete("session", session.token)
                else:
                    self._sessions[session.token] = session

            for row in self._db.all("audit"):
                entry = AuditEntry.from_row(row)
                self._audit[entry.entry_id] = entry

            for row in self._db.all("site"):
                site = Site.from_row(row)
                self._sites[site.site_id] = site

            for row in self._db.all("webhook"):
                hook = AlertWebhook.from_row(row)
                self._webhooks[hook.webhook_id] = hook

            for row in self._db.all("contact"):
                contact = Contact.from_row(row)
                self._contacts[contact.contact_id] = contact

            for row in self._db.all("contract"):
                contract = EnterpriseContract.from_row(row)
                self._contracts[contract.account_id] = contract

            for row in self._db.all("reset"):
                reset = ResetToken.from_row(row)
                if reset.spent:
                    self._db.delete("reset", reset.token_hash)
                else:
                    self._resets[reset.token_hash] = reset

            for row in self._db.all("usage"):
                usage = UsageDay.from_row(row)
                self._usage[usage.key] = usage

            for row in self._db.all("aicache"):
                self._ai_cache[row["key"]] = row["text"]

            # Identifiers are sequential, so resume past the highest one used.
            issued = [
                int(identifier.rsplit("-", 1)[1])
                for identifier in (
                    list(self._tenants)
                    + list(self._incidents)
                    + list(self._users)
                    + list(self._contacts)
                    + list(self._sites)
                    + list(self._webhooks)
                )
                if "-" in identifier and identifier.rsplit("-", 1)[1].isdigit()
            ]
            self._counter = max(issued, default=0)

            if issued or self._sensors:
                logger.info(
                    "Restored %d tenants, %d sensors, %d readings, %d incidents.",
                    len(self._tenants),
                    len(self._sensors),
                    sum(len(b) for b in self._readings.values()),
                    len(self._incidents),
                )

    def reset(self) -> None:
        """Drop all state, on disk as well as in memory. Used by tests."""
        with self._lock:
            self._tenants.clear()
            self._keys.clear()
            self._sensors.clear()
            self._devices.clear()
            self._readings.clear()
            self._incidents.clear()
            self._users.clear()
            self._emails.clear()
            self._sessions.clear()
            self._audit.clear()
            self._sites.clear()
            self._contacts.clear()
            self._webhooks.clear()
            self._contracts.clear()
            self._resets.clear()
            self._usage.clear()
            self._ai_cache.clear()
            self._counter = 0
            self._db.clear()

    def _next_id(self, prefix: str) -> str:
        with self._lock:
            self._counter += 1
            return f"{prefix}-{self._counter:06d}"

    # ---- tenants -------------------------------------------------------

    def create_tenant(
        self,
        company_name: str,
        contact_name: str,
        contact_phone: str,
        contact_email: str,
        plan: str,
    ) -> Tenant:
        with self._lock:
            now = utc_now()
            tenant = Tenant(
                tenant_id=self._next_id("TEN"),
                company_name=company_name,
                contact_name=contact_name,
                contact_phone=contact_phone,
                contact_email=contact_email,
                plan=plan,
                api_key=f"clx_{secrets.token_urlsafe(32)}",
                activated_at=now,
                expires_at=now + timedelta(days=PLAN_TIERS[plan]["term_days"]),
            )
            self._tenants[tenant.tenant_id] = tenant
            self._keys[tenant.api_key] = tenant.tenant_id
            self._db.put("tenant", tenant.tenant_id, tenant.to_row())
            return tenant

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        with self._lock:
            return self._tenants.get(tenant_id)

    def tenant_by_key(self, api_key: str) -> Optional[Tenant]:
        with self._lock:
            tenant_id = self._keys.get(api_key or "")
            return self._tenants.get(tenant_id) if tenant_id else None

    def list_tenants(self) -> List[Tenant]:
        with self._lock:
            return list(self._tenants.values())

    def change_plan(self, tenant: Tenant, plan: str) -> Tenant:
        with self._lock:
            tenant.plan = plan
            tenant.expires_at = utc_now() + timedelta(days=PLAN_TIERS[plan]["term_days"])
            self._db.put("tenant", tenant.tenant_id, tenant.to_row())
            return tenant

    def set_suspended(self, tenant: Tenant, suspended: bool) -> Tenant:
        with self._lock:
            tenant.suspended = suspended
            self._db.put("tenant", tenant.tenant_id, tenant.to_row())
            return tenant

    # ---- sensors -------------------------------------------------------

    def register_sensor(
        self,
        sensor_id: str,
        tenant_id: str,
        industry_vertical: str,
        location_name: str,
        external_device_sn: Optional[str] = None,
    ) -> Sensor:
        with self._lock:
            sensor = Sensor(
                sensor_id=sensor_id,
                tenant_id=tenant_id,
                industry_vertical=industry_vertical,
                location_name=location_name,
                registered_at=utc_now(),
                external_device_sn=external_device_sn,
            )
            self._sensors[sensor_id] = sensor
            if external_device_sn:
                self._devices[external_device_sn] = sensor_id
            self._readings.setdefault(
                sensor_id, deque(maxlen=MAX_READINGS_PER_SENSOR)
            )
            self._db.put("sensor", sensor_id, sensor.to_row())
            return sensor

    def get_sensor(self, sensor_id: str) -> Optional[Sensor]:
        with self._lock:
            return self._sensors.get(sensor_id)

    def sensor_by_device(self, device_sn: str) -> Optional[Sensor]:
        """Resolve a third-party device serial to its licensed sensor.

        Falls back to treating the serial as a sensor_id, so hardware whose
        serial was used directly at registration still resolves.
        """
        with self._lock:
            mapped = self._devices.get((device_sn or "").strip())
            if mapped is not None:
                return self._sensors.get(mapped)
            return self._sensors.get((device_sn or "").strip())

    def device_sn_taken(self, device_sn: str) -> bool:
        with self._lock:
            return (device_sn or "").strip() in self._devices

    def remove_sensor(self, sensor_id: str) -> bool:
        with self._lock:
            if sensor_id not in self._sensors:
                return False
            serial = self._sensors[sensor_id].external_device_sn
            if serial:
                self._devices.pop(serial, None)
            del self._sensors[sensor_id]
            evicted = self._readings.pop(sensor_id, None)
            self._db.delete("sensor", sensor_id)
            if evicted:
                self._db.delete_many("reading", [r.reading_id for r in evicted])
            return True

    def sensors_for(self, tenant_id: str) -> List[Sensor]:
        with self._lock:
            return [
                sensor
                for sensor in self._sensors.values()
                if sensor.tenant_id == tenant_id
            ]

    def seat_count(self, tenant_id: str) -> int:
        return len(self.sensors_for(tenant_id))

    # ---- readings ------------------------------------------------------

    def record_reading(
        self,
        sensor: Sensor,
        temperature_fahrenheit: float,
        humidity_percent: Optional[float],
        breached: bool,
        at: Optional[datetime] = None,
    ) -> Reading:
        """Store one sample.

        `at` backdates the sample, for importing history or seeding a demo.
        It must be supplied here rather than patched onto the returned object,
        which would leave the persisted row holding the wrong time.
        """
        with self._lock:
            now = at or utc_now()
            reading = Reading(
                sensor_id=sensor.sensor_id,
                temperature_fahrenheit=temperature_fahrenheit,
                humidity_percent=humidity_percent,
                breached=breached,
                recorded_at=now,
                reading_id=self._next_id("RDG"),
            )
            bucket = self._readings.setdefault(
                sensor.sensor_id, deque(maxlen=MAX_READINGS_PER_SENSOR)
            )
            # The ring buffer silently drops its oldest entry once full, so
            # take that entry out of the database too rather than letting the
            # table grow without bound.
            evicted = bucket[0] if len(bucket) == MAX_READINGS_PER_SENSOR else None
            bucket.append(reading)
            if evicted is not None:
                self._db.delete("reading", evicted.reading_id)

            sensor.last_seen = now
            sensor.last_temperature = temperature_fahrenheit
            if humidity_percent is not None:
                sensor.last_humidity = humidity_percent

            self._db.put("reading", reading.reading_id, reading.to_row())
            self._db.put("sensor", sensor.sensor_id, sensor.to_row())
            return reading

    def update_sensor_location(self, sensor: Sensor, location_name: str) -> Sensor:
        """Adopt a better site tag reported by third-party hardware."""
        with self._lock:
            sensor.location_name = location_name
            self._db.put("sensor", sensor.sensor_id, sensor.to_row())
            return sensor

    def record_humidity(self, sensor: Sensor, humidity_percent: float) -> Sensor:
        """Store a humidity-only pulse, which cannot breach a thermal bound."""
        with self._lock:
            sensor.last_humidity = humidity_percent
            sensor.last_seen = utc_now()
            self._db.put("sensor", sensor.sensor_id, sensor.to_row())
            return sensor

    def readings_for(
        self, sensor_id: str, since: Optional[datetime] = None
    ) -> List[Reading]:
        with self._lock:
            history = list(self._readings.get(sensor_id, ()))
        if since is None:
            return history
        return [r for r in history if r.recorded_at >= since]

    # ---- incidents -----------------------------------------------------

    def open_incident(
        self,
        tenant_id: str,
        sensor: Sensor,
        temperature_fahrenheit: float,
        breach_details: str,
        sms_text: str,
        sms_dispatch_source: str,
        opened_at: Optional[datetime] = None,
    ) -> Incident:
        with self._lock:
            profile = INDUSTRY_PROFILES[sensor.industry_vertical]
            incident = Incident(
                incident_id=self._next_id("INC"),
                tenant_id=tenant_id,
                sensor_id=sensor.sensor_id,
                industry_vertical=sensor.industry_vertical,
                catastrophe=profile["catastrophe"],
                temperature_fahrenheit=temperature_fahrenheit,
                breach_details=breach_details,
                sms_text=sms_text,
                sms_dispatch_source=sms_dispatch_source,
                opened_at=opened_at or utc_now(),
            )
            self._incidents[incident.incident_id] = incident
            self._db.put("incident", incident.incident_id, incident.to_row())
            return incident

    def _save_incident(self, incident: Incident) -> Incident:
        self._db.put("incident", incident.incident_id, incident.to_row())
        return incident

    def update_incident_breach(
        self, incident: Incident, temperature: float, breach_details: str
    ) -> Incident:
        """Refresh an open incident from a continuing breach."""
        with self._lock:
            incident.temperature_fahrenheit = temperature
            incident.breach_details = breach_details
            return self._save_incident(incident)

    def record_sms_delivery(
        self,
        incident: Incident,
        delivery: Optional[Dict[str, Any]],
        fanout: Optional[List[Dict[str, Any]]] = None,
    ) -> Incident:
        with self._lock:
            incident.sms_delivery = delivery
            incident.sms_fanout = fanout if fanout is not None else (
                [delivery] if delivery else []
            )
            return self._save_incident(incident)

    def issue_ack_token(self, incident: Incident) -> str:
        """A one-incident secret for the keypress callback.

        Incident IDs run in sequence, so without a secret in the URL anyone
        who can guess INC-000007 could silence somebody else's escalation.
        Reused across retries of the same call so a redial still works.
        """
        with self._lock:
            if not incident.ack_token:
                incident.ack_token = secrets.token_urlsafe(24)
                self._save_incident(incident)
            return incident.ack_token

    def record_voice_escalation(
        self,
        incident: Incident,
        script: str,
        source: str,
        delivery: Dict[str, Any],
        fanout: Optional[List[Dict[str, Any]]] = None,
    ) -> Incident:
        with self._lock:
            incident.voice_escalated_at = utc_now()
            incident.voice_script = script
            incident.voice_dispatch_source = source
            incident.voice_delivery = delivery
            incident.voice_fanout = fanout if fanout is not None else [delivery]
            return self._save_incident(incident)

    def acknowledge_incident(self, incident: Incident, actor: str) -> Incident:
        """Record the first acknowledgement; later ones are a no-op."""
        with self._lock:
            if incident.acknowledged_at is None:
                incident.acknowledged_at = utc_now()
                incident.acknowledged_by = actor
                self._save_incident(incident)
            return incident

    def resolve_incident(self, incident: Incident, actor: str) -> Incident:
        """Close an incident, acknowledging it first if nobody had."""
        with self._lock:
            if incident.resolved_at is None:
                now = utc_now()
                if incident.acknowledged_at is None:
                    incident.acknowledged_at = now
                    incident.acknowledged_by = actor
                incident.resolved_at = now
                self._save_incident(incident)
            return incident

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        with self._lock:
            return self._incidents.get(incident_id)

    def incidents_for(
        self, tenant_id: str, since: Optional[datetime] = None
    ) -> List[Incident]:
        with self._lock:
            found = [
                incident
                for incident in self._incidents.values()
                if incident.tenant_id == tenant_id
            ]
        if since is not None:
            found = [i for i in found if i.opened_at >= since]
        return sorted(found, key=lambda i: i.opened_at, reverse=True)

    def open_incidents(self, tenant_id: Optional[str] = None) -> List[Incident]:
        with self._lock:
            found = [i for i in self._incidents.values() if i.open]
        if tenant_id is not None:
            found = [i for i in found if i.tenant_id == tenant_id]
        return sorted(found, key=lambda i: i.opened_at)

    def latest_open_incident(self, sensor_id: str) -> Optional[Incident]:
        with self._lock:
            candidates = [
                i
                for i in self._incidents.values()
                if i.sensor_id == sensor_id and i.open
            ]
        if not candidates:
            return None
        return max(candidates, key=lambda i: i.opened_at)

    # ---- users and sessions --------------------------------------------

    def create_user(
        self,
        tenant_id: str,
        email: str,
        full_name: str,
        role: str,
        password: str,
    ) -> User:
        with self._lock:
            user = User(
                user_id=self._next_id("USR"),
                tenant_id=tenant_id,
                email=email.strip().lower(),
                full_name=full_name,
                role=role,
                password_hash=hash_password(password),
                created_at=utc_now(),
            )
            self._users[user.user_id] = user
            self._emails[user.email] = user.user_id
            self._db.put("user", user.user_id, user.to_row())
            return user

    def get_user(self, user_id: str) -> Optional[User]:
        with self._lock:
            return self._users.get(user_id)

    def user_by_email(self, email: str) -> Optional[User]:
        with self._lock:
            user_id = self._emails.get((email or "").strip().lower())
            return self._users.get(user_id) if user_id else None

    def users_for(self, tenant_id: str) -> List[User]:
        with self._lock:
            return sorted(
                (u for u in self._users.values() if u.tenant_id == tenant_id),
                key=lambda u: u.created_at,
            )

    def set_user_disabled(self, user: User, disabled: bool) -> User:
        with self._lock:
            user.disabled = disabled
            self._db.put("user", user.user_id, user.to_row())
            if disabled:
                # Revoke live tokens so a disabled account loses access now,
                # not whenever its session happens to expire.
                for token in [
                    t for t, s in self._sessions.items() if s.user_id == user.user_id
                ]:
                    self.revoke_session(token)
            return user

    def set_user_role(self, user: User, role: str) -> User:
        with self._lock:
            user.role = role
            self._db.put("user", user.user_id, user.to_row())
            return user

    def set_user_password(self, user: User, password: str) -> User:
        with self._lock:
            user.password_hash = hash_password(password)
            self._db.put("user", user.user_id, user.to_row())
            return user

    def start_session(self, user: User) -> LoginSession:
        with self._lock:
            now = utc_now()
            session = LoginSession(
                token=f"cls_{secrets.token_urlsafe(32)}",
                user_id=user.user_id,
                tenant_id=user.tenant_id,
                issued_at=now,
                expires_at=now + timedelta(hours=SESSION_TTL_HOURS),
            )
            self._sessions[session.token] = session
            user.last_login_at = now
            self._db.put("session", session.token, session.to_row())
            self._db.put("user", user.user_id, user.to_row())
            return session

    def session_by_token(self, token: str) -> Optional[LoginSession]:
        with self._lock:
            session = self._sessions.get(token or "")
            if session is None:
                return None
            if session.expired:
                self.revoke_session(session.token)
                return None
            return session

    def revoke_session(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)
            self._db.delete("session", token)

    # ---- audit trail ----------------------------------------------------

    def record_audit(
        self, tenant_id: str, actor: str, actor_role: str, action: str, detail: str
    ) -> AuditEntry:
        with self._lock:
            entry = AuditEntry(
                entry_id=self._next_id("AUD"),
                tenant_id=tenant_id,
                actor=actor,
                actor_role=actor_role,
                action=action,
                detail=detail,
                at=utc_now(),
            )
            self._audit[entry.entry_id] = entry
            self._db.put("audit", entry.entry_id, entry.to_row())
            return entry

    def audit_for(self, tenant_id: str, limit: int = 100) -> List[AuditEntry]:
        with self._lock:
            entries = [e for e in self._audit.values() if e.tenant_id == tenant_id]
        entries.sort(key=lambda e: e.at, reverse=True)
        return entries[:limit]

    # ---- metered usage --------------------------------------------------

    def usage_for(self, tenant_id: str, day: Optional[str] = None) -> UsageDay:
        """Today's counters for a tenant, created on first touch."""
        day = day or utc_now().strftime("%Y-%m-%d")
        key = f"{tenant_id}|{day}"
        with self._lock:
            usage = self._usage.get(key)
            if usage is None:
                usage = UsageDay(tenant_id=tenant_id, day=day)
                self._usage[key] = usage
                self._db.put("usage", key, usage.to_row())
            return usage

    def bump_usage(self, tenant_id: str, field: str, amount: int = 1) -> UsageDay:
        with self._lock:
            usage = self.usage_for(tenant_id)
            setattr(usage, field, getattr(usage, field) + amount)
            self._db.put("usage", usage.key, usage.to_row())
            return usage

    def usage_history(self, tenant_id: str, days: int = 30) -> List[UsageDay]:
        cutoff = (utc_now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._lock:
            rows = [
                u
                for u in self._usage.values()
                if u.tenant_id == tenant_id and u.day >= cutoff
            ]
        return sorted(rows, key=lambda u: u.day)

    # ---- generated-copy cache -------------------------------------------

    def cache_get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._ai_cache.get(key)

    def cache_put(self, key: str, text: str) -> None:
        with self._lock:
            self._ai_cache[key] = text
            self._db.put("aicache", key, {"key": key, "text": text})

    def cache_size(self) -> int:
        with self._lock:
            return len(self._ai_cache)

    # ---- sites -----------------------------------------------------------

    def create_site(self, tenant_id: str, name: str, address: str = "") -> Site:
        with self._lock:
            site = Site(
                site_id=self._next_id("SITE"),
                tenant_id=tenant_id,
                name=name,
                address=address,
                created_at=utc_now(),
            )
            self._sites[site.site_id] = site
            self._db.put("site", site.site_id, site.to_row())
            return site

    def get_site(self, site_id: str) -> Optional[Site]:
        with self._lock:
            return self._sites.get(site_id)

    def sites_for(self, tenant_id: str) -> List[Site]:
        with self._lock:
            rows = [s for s in self._sites.values() if s.tenant_id == tenant_id]
        return sorted(rows, key=lambda s: s.name.lower())

    def save_site(self, site: Site) -> Site:
        with self._lock:
            self._db.put("site", site.site_id, site.to_row())
            return site

    def remove_site(self, site_id: str) -> bool:
        """Delete a site, releasing its sensors rather than deleting them."""
        with self._lock:
            if site_id not in self._sites:
                return False
            for sensor in list(self._sensors.values()):
                if sensor.site_id == site_id:
                    sensor.site_id = None
                    self._db.put("sensor", sensor.sensor_id, sensor.to_row())
            del self._sites[site_id]
            self._db.delete("site", site_id)
            return True

    def sensors_at_site(self, site_id: str) -> List[Sensor]:
        with self._lock:
            return sorted(
                (s for s in self._sensors.values() if s.site_id == site_id),
                key=lambda s: s.sensor_id,
            )

    def unassigned_sensors(self, tenant_id: str) -> List[Sensor]:
        return [s for s in self.sensors_for(tenant_id) if s.site_id is None]

    def assign_sensor_to_site(
        self, sensor: Sensor, site_id: Optional[str]
    ) -> Sensor:
        with self._lock:
            sensor.site_id = site_id
            self._db.put("sensor", sensor.sensor_id, sensor.to_row())
            return sensor

    # ---- sensor health ---------------------------------------------------

    def record_sensor_health(
        self,
        sensor: Sensor,
        battery_percent: Optional[float] = None,
        signal_percent: Optional[float] = None,
    ) -> Sensor:
        """A battery reported before it dies is a sensor that never goes dark."""
        with self._lock:
            if battery_percent is not None:
                sensor.battery_percent = battery_percent
            if signal_percent is not None:
                sensor.signal_percent = signal_percent
            sensor.last_seen = utc_now()
            self._db.put("sensor", sensor.sensor_id, sensor.to_row())
            return sensor

    # ---- display unit ----------------------------------------------------

    def set_temperature_unit(self, tenant: Tenant, unit: str) -> Tenant:
        with self._lock:
            tenant.temperature_unit = unit
            self._db.put("tenant", tenant.tenant_id, tenant.to_row())
            return tenant

    # ---- on-call roster --------------------------------------------------

    def add_contact(
        self,
        tenant_id: str,
        full_name: str,
        phone: str,
        receives_sms: bool = True,
        receives_voice: bool = True,
        escalation_order: int = 1,
        site_id: Optional[str] = None,
    ) -> Contact:
        with self._lock:
            contact = Contact(
                contact_id=self._next_id("CON"),
                tenant_id=tenant_id,
                full_name=full_name,
                phone=phone,
                receives_sms=receives_sms,
                receives_voice=receives_voice,
                escalation_order=escalation_order,
                site_id=site_id,
            )
            self._contacts[contact.contact_id] = contact
            self._db.put("contact", contact.contact_id, contact.to_row())
            return contact

    def get_contact(self, contact_id: str) -> Optional[Contact]:
        with self._lock:
            return self._contacts.get(contact_id)

    def contacts_for(self, tenant_id: str) -> List[Contact]:
        with self._lock:
            rows = [c for c in self._contacts.values() if c.tenant_id == tenant_id]
        return sorted(rows, key=lambda c: (c.escalation_order, c.contact_id))

    def save_contact(self, contact: Contact) -> Contact:
        with self._lock:
            self._db.put("contact", contact.contact_id, contact.to_row())
            return contact

    def remove_contact(self, contact_id: str) -> bool:
        with self._lock:
            if contact_id not in self._contacts:
                return False
            del self._contacts[contact_id]
            self._db.delete("contact", contact_id)
            return True

    # ---- outbound webhooks -----------------------------------------------

    def add_webhook(
        self,
        tenant_id: str,
        kind: str,
        target: str,
        label: str = "",
        site_id: Optional[str] = None,
    ) -> AlertWebhook:
        with self._lock:
            hook = AlertWebhook(
                webhook_id=self._next_id("HOOK"),
                tenant_id=tenant_id,
                kind=kind,
                target=target,
                label=label,
                site_id=site_id,
            )
            self._webhooks[hook.webhook_id] = hook
            self._db.put("webhook", hook.webhook_id, hook.to_row())
            return hook

    def get_webhook(self, webhook_id: str) -> Optional[AlertWebhook]:
        with self._lock:
            return self._webhooks.get(webhook_id)

    def webhooks_for(self, tenant_id: str) -> List[AlertWebhook]:
        with self._lock:
            rows = [h for h in self._webhooks.values() if h.tenant_id == tenant_id]
        return sorted(rows, key=lambda h: h.webhook_id)

    def webhooks_for_site(
        self, tenant_id: str, site_id: Optional[str]
    ) -> List[AlertWebhook]:
        """Hooks covering a site: its own plus every estate-wide one.

        Unlike the phone roster this does not narrow to the site. A text
        wakes a person, so waking the wrong one matters; a channel post
        costs nothing, and a head office that stops seeing branch alerts
        because somebody added a branch channel is the worse failure.
        """
        return [
            h
            for h in self.webhooks_for(tenant_id)
            if h.active and h.site_id in (None, site_id)
        ]

    def save_webhook(self, hook: AlertWebhook) -> AlertWebhook:
        with self._lock:
            self._db.put("webhook", hook.webhook_id, hook.to_row())
            return hook

    def remove_webhook(self, webhook_id: str) -> bool:
        with self._lock:
            if webhook_id not in self._webhooks:
                return False
            del self._webhooks[webhook_id]
            self._db.delete("webhook", webhook_id)
            return True

    def record_webhook_attempt(
        self, hook: AlertWebhook, delivered: bool, status: str
    ) -> AlertWebhook:
        """Remember how the last post went, so a dead hook is visible."""
        with self._lock:
            hook.last_status = status
            hook.last_attempt_at = utc_now()
            hook.consecutive_failures = (
                0 if delivered else hook.consecutive_failures + 1
            )
            self._db.put("webhook", hook.webhook_id, hook.to_row())
            return hook

    def _roster_for_site(
        self, tenant: Tenant, site_id: Optional[str], channel: str
    ) -> List[Contact]:
        """Contacts covering a site: its own, else the tenant-wide ones.

        A manager in Boca Raton should not be woken for a Boynton Beach
        freezer, so a site's own contacts take precedence when it has any.
        """
        everyone = [
            c
            for c in self.contacts_for(tenant.tenant_id)
            if c.active and getattr(c, f"receives_{channel}")
        ]
        if site_id is not None:
            local = [c for c in everyone if c.site_id == site_id]
            if local:
                return local
        return [c for c in everyone if c.site_id is None]

    def sms_recipients(
        self, tenant: Tenant, site_id: Optional[str] = None
    ) -> List[Contact]:
        """Everyone who should get the text, in escalation order.

        Falls back to the tenant's own contact so a customer who never built
        a roster is still reachable.
        """
        roster = self._roster_for_site(tenant, site_id, "sms")
        if roster:
            return roster
        return [
            Contact(
                contact_id="fallback",
                tenant_id=tenant.tenant_id,
                full_name=tenant.contact_name,
                phone=tenant.contact_phone,
            )
        ]

    def voice_ladder(
        self, tenant: Tenant, site_id: Optional[str] = None
    ) -> List[Contact]:
        """Who to phone, in the order to try them."""
        roster = self._roster_for_site(tenant, site_id, "voice")
        if roster:
            return roster
        return [
            Contact(
                contact_id="fallback",
                tenant_id=tenant.tenant_id,
                full_name=tenant.contact_name,
                phone=tenant.contact_phone,
            )
        ]

    # ---- sensor thresholds ----------------------------------------------

    def set_sensor_overrides(
        self,
        sensor: Sensor,
        above: Optional[float],
        below: Optional[float],
    ) -> Sensor:
        with self._lock:
            sensor.override_above = above
            sensor.override_below = below
            self._db.put("sensor", sensor.sensor_id, sensor.to_row())
            return sensor

    # ---- password resets -------------------------------------------------

    def issue_reset(self, user: User, ttl_hours: int = 24) -> str:
        """Create a reset and return the plaintext token, once."""
        with self._lock:
            token = f"clr_{secrets.token_urlsafe(32)}"
            now = utc_now()
            reset = ResetToken(
                token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                user_id=user.user_id,
                tenant_id=user.tenant_id,
                issued_at=now,
                expires_at=now + timedelta(hours=ttl_hours),
            )
            self._resets[reset.token_hash] = reset
            self._db.put("reset", reset.token_hash, reset.to_row())
            return token

    def redeem_reset(self, token: str, new_password: str) -> Optional[User]:
        """Spend a reset token, returning the user whose password changed."""
        with self._lock:
            digest = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
            reset = self._resets.get(digest)
            if reset is None or reset.spent:
                return None

            user = self._users.get(reset.user_id)
            if user is None or user.disabled:
                return None

            self.set_user_password(user, new_password)
            reset.used_at = utc_now()
            self._resets.pop(digest, None)
            self._db.delete("reset", digest)

            # A reset means the old sessions should not outlive it.
            for tok in [
                s for s, sess in self._sessions.items() if sess.user_id == user.user_id
            ]:
                self.revoke_session(tok)
            return user

    # ---- enterprise contracts -------------------------------------------

    def provision_contract(
        self,
        tenant_id: str,
        company_name: str,
        industry_vertical: str,
        enrolled_branches: int,
        billing_contact_email: str,
        term_days: int = 365,
    ) -> EnterpriseContract:
        """Open a cluster contract with a collision-proof account id.

        The readable prefix is a convenience, not the identity: two clients
        whose names share four letters in the same month would otherwise
        overwrite each other's billing record.
        """
        with self._lock:
            now = utc_now()
            slug = "".join(c for c in company_name.upper() if c.isalnum())[:4] or "ACCT"
            sequence = self._next_id("SEQ").rsplit("-", 1)[1]
            contract = EnterpriseContract(
                account_id=f"ENT-VOL-{slug}-{now.strftime('%m%Y')}-{sequence}",
                tenant_id=tenant_id,
                company_name=company_name,
                industry_vertical=industry_vertical,
                enrolled_branches=enrolled_branches,
                billing_contact_email=billing_contact_email,
                provisioned_at=now,
                renews_at=now + timedelta(days=term_days),
            )
            self._contracts[contract.account_id] = contract
            self._db.put("contract", contract.account_id, contract.to_row())
            return contract

    def get_contract(self, account_id: str) -> Optional[EnterpriseContract]:
        with self._lock:
            return self._contracts.get(account_id)

    def contracts_for(self, tenant_id: str) -> List[EnterpriseContract]:
        with self._lock:
            rows = [c for c in self._contracts.values() if c.tenant_id == tenant_id]
        return sorted(rows, key=lambda c: c.provisioned_at, reverse=True)

    def active_contract(self, tenant_id: str) -> Optional[EnterpriseContract]:
        """The contract that should drive this tenant's invoice, if any."""
        for contract in self.contracts_for(tenant_id):
            if contract.active:
                return contract
        return None

    def save_contract(self, contract: EnterpriseContract) -> EnterpriseContract:
        with self._lock:
            self._db.put("contract", contract.account_id, contract.to_row())
            return contract


STORE = HubStore()
