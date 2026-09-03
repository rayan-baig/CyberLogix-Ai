"""Shared domain model and in-memory state for the CyberLogix AI hub.

Every router in the suite reads and writes through the single `STORE`
instance defined at the bottom of this module. State is held in memory and
guarded by a re-entrant lock, which is correct for a single Cloud Run
instance. Swapping this class for a Firestore or Postgres adapter is the
one change required to scale horizontally.
"""

from __future__ import annotations

import secrets
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional

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


class HubStore:
    """Thread-safe in-memory persistence shared by every router."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tenants: Dict[str, Tenant] = {}
        self._keys: Dict[str, str] = {}
        self._sensors: Dict[str, Sensor] = {}
        self._devices: Dict[str, str] = {}
        self._readings: Dict[str, Deque[Reading]] = {}
        self._incidents: Dict[str, Incident] = {}
        self._counter = 0

    def reset(self) -> None:
        """Drop all state. Used by the test suite."""
        with self._lock:
            self._tenants.clear()
            self._keys.clear()
            self._sensors.clear()
            self._devices.clear()
            self._readings.clear()
            self._incidents.clear()
            self._counter = 0

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
            return tenant

    def set_suspended(self, tenant: Tenant, suspended: bool) -> Tenant:
        with self._lock:
            tenant.suspended = suspended
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
            self._readings.pop(sensor_id, None)
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
    ) -> Reading:
        with self._lock:
            now = utc_now()
            reading = Reading(
                sensor_id=sensor.sensor_id,
                temperature_fahrenheit=temperature_fahrenheit,
                humidity_percent=humidity_percent,
                breached=breached,
                recorded_at=now,
            )
            self._readings.setdefault(
                sensor.sensor_id, deque(maxlen=MAX_READINGS_PER_SENSOR)
            ).append(reading)
            sensor.last_seen = now
            sensor.last_temperature = temperature_fahrenheit
            if humidity_percent is not None:
                sensor.last_humidity = humidity_percent
            return reading

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
                opened_at=utc_now(),
            )
            self._incidents[incident.incident_id] = incident
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


STORE = HubStore()
