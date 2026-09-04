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
    },
    "restaurant": {
        "name": "Franchise Restaurants",
        "catastrophe": "Unlatched Walk-In Freezer Door Gasket Failure",
        "danger_above": 32.0,
        "danger_below": None,
        "unit": "°F",
    },
    "logistics": {
        "name": "High-Stakes Cold-Chain Transport",
        "catastrophe": "Reefer Truck Auxiliary Diesel Engine Stall",
        "danger_above": 40.0,
        "danger_below": None,
        "unit": "°F",
    },
    "solar_infrastructure": {
        "name": "Solar Infrastructure & Storage",
        "catastrophe": "Inverter Thermal Runaway Overload",
        "danger_above": 115.0,
        "danger_below": None,
        "unit": "°F",
    },
    "medical_lab": {
        "name": "Medical Labs & Blood Banks",
        "catastrophe": "Specimen Refrigerator Door Seal Degradation",
        "danger_above": 46.0,
        "danger_below": 36.0,
        "unit": "°F",
    },
    "private_aviation": {
        "name": "Private Aviation Hangars",
        "catastrophe": "Hangar Bay Humidity Moisture Infiltration",
        "danger_above": 85.0,  # Heat/humidity proxy
        "danger_below": None,
        "unit": "°F",
    },
    "superyacht": {
        "name": "Luxury Superyacht Engine Bays",
        "catastrophe": "Engine Room Ventilation Airflow Blockage",
        "danger_above": 90.0,
        "danger_below": None,
        "unit": "°F",
    },
    "country_club": {
        "name": "High-End Country Clubs",
        "catastrophe": "Clubhouse Kitchen Walk-In Compressor Failure",
        "danger_above": 32.0,
        "danger_below": None,
        "unit": "°F",
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


def evaluate_breach(vertical: str, temperature: float) -> Optional[str]:
    """Return a human-readable breach reason, or None when nominal."""
    profile = INDUSTRY_PROFILES[vertical]
    above = profile["danger_above"]
    below = profile["danger_below"]

    if above is not None and temperature > above:
        return (
            f"Thermal high threshold breached: {temperature}°F > {above}°F limit."
        )
    if below is not None and temperature < below:
        return (
            f"Thermal low threshold breached: {temperature}°F < {below}°F limit."
        )
    return None


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
    last_seen: Optional[datetime] = None
    last_temperature: Optional[float] = None
    last_humidity: Optional[float] = None

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
            last_seen=_parse(row.get("last_seen")),
            last_temperature=row.get("last_temperature"),
            last_humidity=row.get("last_humidity"),
        )

    def public(self) -> Dict[str, Any]:
        profile = INDUSTRY_PROFILES[self.industry_vertical]
        return {
            "sensor_id": self.sensor_id,
            "tenant_id": self.tenant_id,
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
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    voice_escalated_at: Optional[datetime] = None
    voice_script: Optional[str] = None
    voice_dispatch_source: Optional[str] = None
    resolved_at: Optional[datetime] = None

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
            "acknowledged_at": iso(self.acknowledged_at),
            "acknowledged_by": self.acknowledged_by,
            "voice_escalated_at": iso(self.voice_escalated_at),
            "voice_script": self.voice_script,
            "voice_dispatch_source": self.voice_dispatch_source,
            "resolved_at": iso(self.resolved_at),
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
            acknowledged_at=_parse(row.get("acknowledged_at")),
            acknowledged_by=row.get("acknowledged_by"),
            voice_escalated_at=_parse(row.get("voice_escalated_at")),
            voice_script=row.get("voice_script"),
            voice_dispatch_source=row.get("voice_dispatch_source"),
            resolved_at=_parse(row.get("resolved_at")),
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

            for row in self._db.all("usage"):
                usage = UsageDay.from_row(row)
                self._usage[usage.key] = usage

            for row in self._db.all("aicache"):
                self._ai_cache[row["key"]] = row["text"]

            # Identifiers are sequential, so resume past the highest one used.
            issued = [
                int(identifier.rsplit("-", 1)[1])
                for identifier in (
                    list(self._tenants) + list(self._incidents) + list(self._users)
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
        self, incident: Incident, delivery: Dict[str, Any]
    ) -> Incident:
        with self._lock:
            incident.sms_delivery = delivery
            return self._save_incident(incident)

    def record_voice_escalation(
        self,
        incident: Incident,
        script: str,
        source: str,
        delivery: Dict[str, Any],
    ) -> Incident:
        with self._lock:
            incident.voice_escalated_at = utc_now()
            incident.voice_script = script
            incident.voice_dispatch_source = source
            incident.voice_delivery = delivery
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


STORE = HubStore()
