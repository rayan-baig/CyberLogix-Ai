"""The records the store keeps.

One dataclass per kind of thing that is persisted (tenant, sensor,
reading, incident, user, session, audit entry, site, invoice, contract,
subscription, mail and so on), each with the `to_row` and `from_row` that
carry it to and from the database. The live working set, the lock and
every change to it stay in store.py; these are only the shapes.

store.py re-exports every name, so `from store import Tenant` and the rest
keep working.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from domain import (
    SENSOR_OFFLINE_AFTER_MINUTES,
    SMS_RETRY_AFTER_MINUTES,
    RETRYABLE_SMS_STATUSES,
    utc_now,
    iso,
    hash_secret,
    _parse,
    mask_email,
    display_temperature,
    INDUSTRY_PROFILES,
    PLAN_TIERS,
    volume_discount_percent,
    calculate_volume_tier_price,
    volume_tier_label,
    next_volume_tier,
)


@dataclass
class Tenant:
    """A paying customer organisation."""

    tenant_id: str
    company_name: str
    contact_name: str
    contact_phone: str
    contact_email: str
    plan: str
    # The master key itself is held only in memory, right after it is made or
    # replaced, for the one response that shows it. On disk it is a SHA-256:
    # a copy of the database or a backup used to hold every business's key.
    api_key: str
    activated_at: datetime
    expires_at: datetime
    suspended: bool = False
    temperature_unit: str = "F"
    # The reseller who brought and manages this account, if any. A
    # refrigeration servicer with 200 clients is one relationship for us
    # and 200 accounts on the books.
    partner_id: Optional[str] = None
    # What they said they monitor, captured at sign-up. Nothing is
    # enforced from it — every sensor carries its own vertical and that is
    # what prices and alarms it. This is so the product can talk to them
    # about freezers rather than about "units" before they have registered
    # anything at all.
    industry_vertical: Optional[str] = None
    key_hash: str = ""

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
            "key_hash": self.key_hash,
            "activated_at": iso(self.activated_at),
            "expires_at": iso(self.expires_at),
            "suspended": self.suspended,
            "temperature_unit": self.temperature_unit,
            "partner_id": self.partner_id,
            "industry_vertical": self.industry_vertical,
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
            api_key="",
            key_hash=row.get("key_hash") or (hash_secret(row["api_key"]) if row.get("api_key") else ""),
            activated_at=_parse(row["activated_at"]),
            expires_at=_parse(row["expires_at"]),
            suspended=row.get("suspended", False),
            temperature_unit=row.get("temperature_unit", "F"),
            partner_id=row.get("partner_id"),
            industry_vertical=row.get("industry_vertical"),
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
            "partner_id": self.partner_id,
            "industry_vertical": self.industry_vertical,
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
    # Its own credential, scoped to this one asset. See `claim_sensor_seat`
    # for why a sensor must not be carrying the tenant's key. Like the
    # business key, the key itself lives only in memory after it is made;
    # what is kept is its SHA-256.
    ingest_key: str = ""
    ingest_key_hash: str = ""
    external_device_sn: Optional[str] = None
    site_id: Optional[str] = None
    battery_percent: Optional[float] = None
    signal_percent: Optional[float] = None
    override_above: Optional[float] = None
    override_below: Optional[float] = None
    last_seen: Optional[datetime] = None
    last_temperature: Optional[float] = None
    last_humidity: Optional[float] = None
    # The `last_seen` of the silence somebody was last texted about, so one
    # silence is one text. A new reading moves `last_seen` on, which is what
    # lets the next silence be told about too.
    quiet_alert_for: Optional[str] = None

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
        """True when this sensor has stopped reporting.

        A last contact in the future makes the naive age negative, which
        reads as "just heard from it" — so a sensor that went silent
        during a clock correction would be counted online forever, and a
        silent sensor cannot warn anybody. Skew is reported separately by
        `clock_skewed` rather than being quietly folded into this answer.
        """
        now = now or utc_now()
        if self.last_seen is None:
            return True
        age = (now - self.last_seen).total_seconds() / 60.0
        if age < 0:
            # The clock moved. Treat contact as having happened now, which
            # keeps the sensor online for one more window rather than
            # forever, and let clock_skewed() surface the real problem.
            return False
        return age > SENSOR_OFFLINE_AFTER_MINUTES

    def clock_skewed(self, now: Optional[datetime] = None) -> bool:
        """Last contact is meaningfully in the future.

        Either the device's clock or ours is wrong, and until somebody
        fixes it every age calculation about this sensor is unreliable —
        including the one that decides whether it has gone quiet.
        """
        if self.last_seen is None:
            return False
        ahead = (self.last_seen - (now or utc_now())).total_seconds() / 60.0
        return ahead > self.CLOCK_SKEW_TOLERANCE_MINUTES

    def to_row(self) -> Dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "tenant_id": self.tenant_id,
            "industry_vertical": self.industry_vertical,
            "location_name": self.location_name,
            "registered_at": iso(self.registered_at),
            "ingest_key_hash": self.ingest_key_hash,
            "external_device_sn": self.external_device_sn,
            "site_id": self.site_id,
            "battery_percent": self.battery_percent,
            "signal_percent": self.signal_percent,
            "override_above": self.override_above,
            "override_below": self.override_below,
            "last_seen": iso(self.last_seen),
            "last_temperature": self.last_temperature,
            "last_humidity": self.last_humidity,
            "quiet_alert_for": self.quiet_alert_for,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Sensor":
        return cls(
            sensor_id=row["sensor_id"],
            tenant_id=row["tenant_id"],
            industry_vertical=row["industry_vertical"],
            location_name=row["location_name"],
            registered_at=_parse(row["registered_at"]),
            ingest_key_hash=row.get("ingest_key_hash")
            or (hash_secret(row["ingest_key"]) if row.get("ingest_key") else ""),
            external_device_sn=row.get("external_device_sn"),
            site_id=row.get("site_id"),
            battery_percent=row.get("battery_percent"),
            signal_percent=row.get("signal_percent"),
            override_above=row.get("override_above"),
            override_below=row.get("override_below"),
            last_seen=_parse(row.get("last_seen")),
            last_temperature=row.get("last_temperature"),
            last_humidity=row.get("last_humidity"),
            quiet_alert_for=row.get("quiet_alert_for"),
        )

    # How far ahead of us a device's last contact may sit before we treat
    # the clock as broken rather than the sensor as healthy.
    CLOCK_SKEW_TOLERANCE_MINUTES = 5.0

    LOW_BATTERY_PERCENT = 20.0

    @property
    def battery_low(self) -> bool:
        """A battery this low will go dark before anyone notices."""
        return (
            self.battery_percent is not None
            and self.battery_percent <= self.LOW_BATTERY_PERCENT
        )

    def public(self, unit: str = "F") -> Dict[str, Any]:
        # `ingest_key` is deliberately absent, and must stay absent. It is
        # shown once, at registration, exactly like the tenant key: a
        # credential that any authenticated reader can fetch from a list
        # endpoint is not a credential.
        profile = INDUSTRY_PROFILES[self.industry_vertical]
        above, below = self.bounds()
        return {
            "site_id": self.site_id,
            "battery_percent": self.battery_percent,
            "battery_low": self.battery_low,
            "clock_skewed": self.clock_skewed(),
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
            # What this sector calls the thing being watched. A superyacht
            # customer paying $4,999 a vessel should not read the word
            # "sensor" anywhere on their own screen.
            "asset_noun": profile["asset_noun"],
            "asset_plural": profile["asset_plural"],
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
    # What was DONE about it, and who says so. An inspector reading a
    # temperature log is not checking that the fridge got cold again --
    # they are checking that somebody recorded what they did with the
    # food and that a manager signed it. HACCP principles 4 and 7 make
    # the corrective action and its review the record, and an excursion
    # with neither is the line that gets written up.
    corrective_action: Optional[str] = None
    corrective_action_by: Optional[str] = None
    corrective_action_at: Optional[datetime] = None
    product_disposition: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    # The readings that prove this event, copied onto the incident when it
    # opens and again when it resolves.
    #
    # Readings are a rolling window: the oldest are deleted once a sensor
    # passes MAX_READINGS_PER_SENSOR, which at a five-minute pulse is
    # under two days. The insurance claim packet was reading them live,
    # so a claim filed a week after the failure assembled a window full of
    # healthy readings with the breach deleted — and reported
    # "excursions_in_window: 0" on a document whose entire purpose is to
    # prove an excursion happened. Not an empty packet, which somebody
    # would have questioned. A complete-looking one, arguing against the
    # claim it was attached to.
    #
    # Incidents are kept for years; readings are not. So the evidence for
    # an event that mattered is copied onto the thing that survives.
    evidence_readings: List[Dict[str, Any]] = field(default_factory=list)
    # Re-sends of the alert text after every text failed. `sms_retry_step`
    # is how many steps of SMS_RETRY_AFTER_MINUTES have been claimed; it is
    # stored, so a restart or a second sweep does not send a step twice.
    sms_retry_step: int = 0
    sms_retries: List[Dict[str, Any]] = field(default_factory=list)

    def _sms_receipts(self) -> List[Dict[str, Any]]:
        receipts = list(self.sms_fanout) or (
            [self.sms_delivery] if self.sms_delivery else []
        )
        for attempt in self.sms_retries:
            receipts.extend(attempt.get("deliveries") or [])
        return receipts

    @property
    def texts_landed(self) -> bool:
        """At least one alert text, first try or re-send, reached Twilio."""
        return any(r.get("delivered") for r in self._sms_receipts())

    @property
    def alert_texts_failed(self) -> bool:
        """Texts were tried and not one got through."""
        return bool(self._sms_receipts()) and not self.texts_landed

    def sms_retry_due(self, now: Optional[datetime] = None) -> int:
        """The re-send step this incident is due for now, or 0.

        Only while nobody has it, only when nothing has landed, and only
        when the last attempt failed in a way a second try can fix. Steps
        are counted from when the incident opened; after a restart that
        missed some, the latest step due is the one taken, so a late sweep
        sends once rather than catching up with three texts in a row.
        """
        if not self.open or self.texts_landed:
            return 0
        last = (self.sms_retries[-1].get("deliveries") if self.sms_retries
                else self._sms_receipts())
        if not any(r.get("status") in RETRYABLE_SMS_STATUSES for r in last or []):
            return 0
        age = self.minutes_open(now)
        step = sum(1 for m in SMS_RETRY_AFTER_MINUTES if age >= m)
        return step if step > self.sms_retry_step else 0

    def next_sms_retry_at(self) -> Optional[datetime]:
        """When the next re-send is due, if one is still to come."""
        if not self.open or not self.alert_texts_failed:
            return None
        if self.sms_retry_step >= len(SMS_RETRY_AFTER_MINUTES):
            return None
        return self.opened_at + timedelta(
            minutes=SMS_RETRY_AFTER_MINUTES[self.sms_retry_step]
        )

    @property
    def open(self) -> bool:
        """Nobody has this yet.

        This is the escalation question — whether to keep waking people —
        so an acknowledgement closes it even though the freezer is still
        broken. It is *not* the question of whether the fault is still
        live; see `unresolved`.
        """
        return self.resolved_at is None and self.acknowledged_at is None

    @property
    def unresolved(self) -> bool:
        """The fault is still live, whoever is dealing with it.

        The two are different questions and the difference is easy to
        miss, which is exactly what happened: the ingest path asked for
        the latest *open* incident when deciding whether a breach was
        already known about. The moment somebody acknowledged, the
        incident stopped being open, so the next reading from the same
        still-broken freezer opened a second one — texting the whole
        roster again and starting a fresh ten-minute clock that would
        phone the person who had just said they were on their way.
        """
        return self.resolved_at is None

    def minutes_open(self, now: Optional[datetime] = None) -> float:
        """How long this has been somebody's problem. Never negative.

        A clock that steps backwards — an NTP correction, a VM resuming
        from suspend — makes the naive subtraction negative, and that
        number is read in nine places. The one that matters is the
        escalation gate: an incident whose age reads -30 minutes is not
        past the ten-minute grace window, so it is never listed as due and
        never gets its phone call. A real breach then sits open forever
        because a clock moved.

        The others are almost as bad in their own way: a claim packet
        stating the alert was acknowledged -120 minutes after it fired
        does not read as a clock bug to a loss adjuster, it reads as a
        fabricated document.

        Clamped at zero. A duration cannot be negative, and treating a
        skewed clock as "just opened" fails in the safe direction: the
        incident escalates a little late rather than never.
        """
        now = now or utc_now()
        end = self.acknowledged_at or self.resolved_at or now
        return max(0.0, round((end - self.opened_at).total_seconds() / 60.0, 2))

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
            "corrective_action": self.corrective_action,
            "corrective_action_by": self.corrective_action_by,
            "corrective_action_at": iso(self.corrective_action_at),
            "product_disposition": self.product_disposition,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": iso(self.reviewed_at),
            "evidence_readings": list(self.evidence_readings),
            "sms_retry_step": self.sms_retry_step,
            "sms_retries": list(self.sms_retries),
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
            # Defaulted: incidents written before corrective actions
            # existed simply have none recorded, which is the truth.
            corrective_action=row.get("corrective_action"),
            corrective_action_by=row.get("corrective_action_by"),
            corrective_action_at=_parse(row.get("corrective_action_at")),
            product_disposition=row.get("product_disposition"),
            reviewed_by=row.get("reviewed_by"),
            reviewed_at=_parse(row.get("reviewed_at")),
            # Defaulted, so incidents written before evidence was
            # preserved still load. Theirs is gone; nothing can bring it
            # back, and the packet says so rather than implying otherwise.
            evidence_readings=list(row.get("evidence_readings", [])),
            sms_retry_step=int(row.get("sms_retry_step") or 0),
            sms_retries=list(row.get("sms_retries") or []),
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
            "texts_landed": self.texts_landed,
            "alert_texts_failed": self.alert_texts_failed,
            "sms_retries": list(self.sms_retries),
            "next_sms_retry_at": iso(self.next_sms_retry_at()),
            "opened_at": iso(self.opened_at),
            "acknowledged_at": iso(self.acknowledged_at),
            "acknowledged_by": self.acknowledged_by,
            "voice_escalated_at": iso(self.voice_escalated_at),
            "voice_script": self.voice_script,
            "voice_dispatch_source": self.voice_dispatch_source,
            "resolved_at": iso(self.resolved_at),
            "minutes_open": self.minutes_open(),
            "corrective_action": self.corrective_action,
            "corrective_action_by": self.corrective_action_by,
            "corrective_action_at": iso(self.corrective_action_at),
            "product_disposition": self.product_disposition,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": iso(self.reviewed_at),
            # What an inspector is actually checking. A cold fridge with
            # no record of what happened to the food in it is the line
            # that gets written up, so the gap is named rather than left
            # to be noticed.
            "record_complete": bool(self.corrective_action and self.reviewed_by),
            "record_missing": [
                label for label, present in (
                    ("a corrective action", bool(self.corrective_action)),
                    ("what happened to the product",
                     bool(self.product_disposition)),
                    ("a manager's review", bool(self.reviewed_by)),
                ) if not present
            ],
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
    """A named human operator inside a tenant.

    One person who runs several businesses has one of these per business,
    so that each business keeps its own role for them and nothing about
    one business can leak into another. Only the first is a sign-in: it
    owns the email address and the password. The rest carry `linked_to`,
    the user_id of that first one, cannot be signed into directly, and are
    reached by switching business from a session of the same person.
    """

    user_id: str
    tenant_id: str
    email: str
    full_name: str
    role: str
    password_hash: str
    created_at: datetime
    last_login_at: Optional[datetime] = None
    disabled: bool = False
    linked_to: Optional[str] = None
    # The password was chosen by a business owner (an invite, or a reset the
    # owner issued), so that owner knows it. Cleared when the person sets
    # their own. While it is set the sign-in stays inside that one business:
    # it cannot join or start others, which the owner could then walk into.
    password_from_owner: bool = False

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
            "linked_to": self.linked_to,
            "password_from_owner": self.password_from_owner,
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
            linked_to=row.get("linked_to"),
            password_from_owner=bool(row.get("password_from_owner", False)),
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
            # True when this person signs in with a login that belongs to
            # another business, and reaches this one by switching.
            "linked": self.linked_to is not None,
            "must_change_password": self.password_from_owner,
        }


@dataclass
class LoginSession:
    """A bearer token issued at login.

    `token` is the SHA-256 of the bearer token, and is what the store keys
    sessions by and writes to disk. The bearer token itself is `secret`, set
    only on the object start_session returns, so the one response that hands
    it out can. A copy of the database or a backup used to hold every live
    session in the clear: anybody with one could sign in as anybody.
    """

    token: str
    user_id: str
    tenant_id: str
    issued_at: datetime
    expires_at: datetime
    secret: str = ""

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
    # Twilio bills per segment, not per message. Kept separately from
    # sms_sent so the cap can stay on messages while the cost estimate
    # uses what is actually invoiced.
    sms_segments: int = 0
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
            "sms_segments": self.sms_segments,
            "sms_suppressed": self.sms_suppressed,
            "voice_calls": self.voice_calls,
            "voice_suppressed": self.voice_suppressed,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "UsageDay":
        data = dict(row)
        # Rows written before segments were counted fall back to one
        # segment per message. Zero would report those days as free.
        data.setdefault("sms_segments", data.get("sms_sent", 0))
        return cls(**data)

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


@dataclass
class VaultAnchor:
    """The chain head for one sensor, as it stood when an attestation was
    issued.

    Without this the tamper check has nothing to compare against.
    Re-deriving the chain from the current rows and checking it against
    itself is a tautology — it agrees no matter what was edited. The
    anchor is the only thing that turns "these readings hash to X" into
    "these readings still hash to what we told your insurer they did".
    """

    anchor_id: str
    tenant_id: str
    sensor_id: str
    chain_head: str
    readings: int
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    issued_at: Optional[datetime] = None
    # The exact first and last readings covered, so the run can be found
    # again without guessing from times kept to the second.
    first_reading_id: Optional[str] = None
    last_reading_id: Optional[str] = None

    def to_row(self) -> Dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "first_reading_id": self.first_reading_id,
            "last_reading_id": self.last_reading_id,
            "tenant_id": self.tenant_id,
            "sensor_id": self.sensor_id,
            "chain_head": self.chain_head,
            "readings": self.readings,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "issued_at": iso(self.issued_at),
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "VaultAnchor":
        return cls(
            anchor_id=row["anchor_id"],
            tenant_id=row["tenant_id"],
            sensor_id=row["sensor_id"],
            chain_head=row["chain_head"],
            readings=row.get("readings", 0),
            first_reading_id=row.get("first_reading_id"),
            last_reading_id=row.get("last_reading_id"),
            period_start=row.get("period_start"),
            period_end=row.get("period_end"),
            issued_at=_parse(row.get("issued_at")),
        )

    def public(self) -> Dict[str, Any]:
        return self.to_row()


INVOICE_STATES = ("issued", "part_paid", "paid", "void")


@dataclass
class Invoice:
    """A numbered, dated demand for money, frozen at issue.

    The figures are snapshotted rather than recomputed on read. An invoice
    whose total moves after it was sent is a dispute, and the customer
    would be right to raise one.
    """

    invoice_id: str
    tenant_id: str
    number: str
    company_name: str
    lines: List[Dict[str, Any]]
    subtotal_usd: float
    total_usd: float
    currency: str = "USD"
    state: str = "issued"
    period_days: int = 30
    terms_days: int = 30
    purchase_order: Optional[str] = None
    issued_at: Optional[datetime] = None
    due_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    # The most recent reference, kept for the documents that print one.
    # It is not the record of what was paid — `payments` is. This field
    # alone was the record, and it was overwritten by each payment, so
    # after three part payments the ledger knew the total and the last
    # reference and nothing else. Reconciling that against a bank
    # statement is guesswork.
    payment_reference: Optional[str] = None
    # Every payment applied, in order: reference, amount, when. The
    # reference is the payment's identity, which is what makes applying
    # one twice detectable.
    payments: List[Dict[str, Any]] = field(default_factory=list)
    amount_paid_usd: float = 0.0
    voided_at: Optional[datetime] = None
    # What issued it: a subscription id, or "manual" when a human did.
    source: str = "manual"
    # Collections. An invoice nobody chases is a donation, and a founder
    # who has to remember to chase is a founder who sometimes doesn't.
    reminders_sent: int = 0
    last_reminder_at: Optional[datetime] = None
    # A late fee is a separate, numbered invoice — never an edit to this
    # one. An issued invoice whose total moves is a dispute.
    late_fee_invoice_id: Optional[str] = None
    # Which period of the subscription this invoice covers, when one issued
    # it. Voiding an invoice has to be able to name the period so it can be
    # billed again.
    billing_period: Optional[int] = None

    @property
    def balance_usd(self) -> float:
        """What is still owed. The figure collections actually works from."""
        return round(max(0.0, self.total_usd - self.amount_paid_usd), 2)

    @property
    def overpaid_usd(self) -> float:
        """Money received beyond the total, which nobody is owed."""
        return round(max(0.0, self.amount_paid_usd - self.total_usd), 2)

    @property
    def settled(self) -> bool:
        return self.state == "paid"

    @property
    def open(self) -> bool:
        """Still owed something. A part payment does not close an invoice."""
        return self.state in ("issued", "part_paid")

    def overdue(self, now: Optional[datetime] = None) -> bool:
        if not self.open or self.due_at is None:
            return False
        return (now or utc_now()) > self.due_at

    def days_overdue(self, now: Optional[datetime] = None) -> int:
        """How far past due, or 0 if it is not."""
        if not self.overdue(now):
            return 0
        return int(((now or utc_now()) - self.due_at).total_seconds() // 86400)

    def days_until_due(self, now: Optional[datetime] = None) -> Optional[int]:
        if self.due_at is None:
            return None
        delta = self.due_at - (now or utc_now())
        return int(delta.total_seconds() // 86400)

    def to_row(self) -> Dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "tenant_id": self.tenant_id,
            "number": self.number,
            "company_name": self.company_name,
            "lines": self.lines,
            "subtotal_usd": self.subtotal_usd,
            "total_usd": self.total_usd,
            "currency": self.currency,
            "state": self.state,
            "period_days": self.period_days,
            "terms_days": self.terms_days,
            "purchase_order": self.purchase_order,
            "issued_at": iso(self.issued_at),
            "due_at": iso(self.due_at),
            "paid_at": iso(self.paid_at),
            "payment_reference": self.payment_reference,
            "payments": list(self.payments),
            "amount_paid_usd": self.amount_paid_usd,
            "voided_at": iso(self.voided_at),
            "source": self.source,
            "reminders_sent": self.reminders_sent,
            "last_reminder_at": iso(self.last_reminder_at),
            "late_fee_invoice_id": self.late_fee_invoice_id,
            "billing_period": self.billing_period,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Invoice":
        return cls(
            invoice_id=row["invoice_id"],
            tenant_id=row["tenant_id"],
            number=row["number"],
            company_name=row["company_name"],
            lines=row.get("lines") or [],
            subtotal_usd=row["subtotal_usd"],
            total_usd=row["total_usd"],
            currency=row.get("currency", "USD"),
            state=row.get("state", "issued"),
            period_days=row.get("period_days", 30),
            terms_days=row.get("terms_days", 30),
            purchase_order=row.get("purchase_order"),
            issued_at=_parse(row.get("issued_at")),
            due_at=_parse(row.get("due_at")),
            paid_at=_parse(row.get("paid_at")),
            payment_reference=row.get("payment_reference"),
            # Defaulted, so an invoice written before payments were kept
            # loads rather than exploding. Its history starts empty and
            # its total is still right.
            payments=list(row.get("payments", [])),
            amount_paid_usd=row.get("amount_paid_usd", 0.0),
            voided_at=_parse(row.get("voided_at")),
            source=row.get("source", "manual"),
            reminders_sent=row.get("reminders_sent", 0),
            last_reminder_at=_parse(row.get("last_reminder_at")),
            late_fee_invoice_id=row.get("late_fee_invoice_id"),
            billing_period=row.get("billing_period"),
        )

    def public(self) -> Dict[str, Any]:
        row = self.to_row()
        row["overdue"] = self.overdue()
        row["days_overdue"] = self.days_overdue()
        row["days_until_due"] = self.days_until_due()
        row["balance_usd"] = self.balance_usd
        row["overpaid_usd"] = self.overpaid_usd
        row["open"] = self.open
        return row


# What a reseller earns on the accounts they bring and support. They do
# the installation and first-line support; we do the platform.
DEFAULT_PARTNER_COMMISSION_PERCENT = 20.0


@dataclass
class Partner:
    """A reseller: an equipment servicer, integrator or facilities firm.

    They already visit the sites, already have the relationship, and
    already get called when a freezer fails. Selling through them turns one
    negotiation into a book of accounts, and it is the only channel here
    where acquisition cost does not scale with revenue.
    """

    partner_id: str
    company_name: str
    contact_name: str
    contact_email: str
    # The key itself is held only in memory, right after it is made, so the
    # one response that shows it can. What is stored is its SHA-256 and the
    # last few characters for telling keys apart: a copy of the database or
    # a backup does not open anybody's partner portal.
    api_key: str = ""
    commission_percent: float = DEFAULT_PARTNER_COMMISSION_PERCENT
    active: bool = True
    created_at: Optional[datetime] = None
    key_hash: str = ""
    key_hint: str = ""

    def set_key(self, api_key: str) -> None:
        self.api_key = api_key
        self.key_hash = hash_secret(api_key)
        self.key_hint = api_key[-6:]

    def to_row(self) -> Dict[str, Any]:
        return {
            "partner_id": self.partner_id,
            "company_name": self.company_name,
            "contact_name": self.contact_name,
            "contact_email": self.contact_email,
            "key_hash": self.key_hash,
            "key_hint": self.key_hint,
            "commission_percent": self.commission_percent,
            "active": self.active,
            "created_at": iso(self.created_at),
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Partner":
        partner = cls(
            partner_id=row["partner_id"],
            company_name=row["company_name"],
            contact_name=row["contact_name"],
            contact_email=row["contact_email"],
            commission_percent=row.get(
                "commission_percent", DEFAULT_PARTNER_COMMISSION_PERCENT
            ),
            active=row.get("active", True),
            created_at=_parse(row.get("created_at")),
            key_hash=row.get("key_hash", ""),
            key_hint=row.get("key_hint", ""),
        )
        if row.get("api_key") and not partner.key_hash:
            # Written before keys were hashed: hash it now. The store saves
            # the row back on load, which drops the plain key from disk.
            partner.set_key(row["api_key"])
            partner.api_key = ""
        return partner

    def public(self) -> Dict[str, Any]:
        row = self.to_row()
        # The key is a credential; it is shown once at creation and never
        # echoed back on a listing.
        row.pop("key_hash", None)
        row["api_key"] = f"...{self.key_hint}"
        return row


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
class JoinCode:
    """A one-time code that lets somebody add a business to their sign-in.

    Made by an owner of the business and handed over out of band; entered by
    the person, from their own signed-in account. Both have to act, so no
    business can attach itself to somebody's sign-in by knowing their email.
    Only the hash is stored.
    """

    token_hash: str
    tenant_id: str
    role: str
    issued_by: str
    issued_at: datetime
    expires_at: datetime
    used_at: Optional[datetime] = None

    @property
    def spent(self) -> bool:
        return self.used_at is not None or utc_now() >= self.expires_at

    @property
    def code_id(self) -> str:
        """A name for the code that is not the code: the start of its hash.

        The owner needs to point at one outstanding code to withdraw it,
        and the code itself is shown once and never kept. Twelve hex
        characters of a SHA-256 of 24 random bytes say nothing about it.
        """
        return self.token_hash[:12]

    def public(self, issued_by_name: str = "") -> Dict[str, Any]:
        return {
            "code_id": self.code_id,
            "role": self.role,
            "issued_by": issued_by_name,
            "issued_at": iso(self.issued_at),
            "expires_at": iso(self.expires_at),
        }

    def to_row(self) -> Dict[str, Any]:
        return {
            "token_hash": self.token_hash,
            "tenant_id": self.tenant_id,
            "role": self.role,
            "issued_by": self.issued_by,
            "issued_at": iso(self.issued_at),
            "expires_at": iso(self.expires_at),
            "used_at": iso(self.used_at),
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "JoinCode":
        return cls(
            token_hash=row["token_hash"],
            tenant_id=row["tenant_id"],
            role=row["role"],
            issued_by=row["issued_by"],
            issued_at=_parse(row["issued_at"]),
            expires_at=_parse(row["expires_at"]),
            used_at=_parse(row.get("used_at")),
        )


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
    # Issued by an owner for one of their people, rather than mailed to the
    # person by "forgot password". Rows from before this was recorded are
    # treated as owner-issued: the stricter checks apply to them.
    by_owner: bool = True

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
            "by_owner": self.by_owner,
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
            by_owner=bool(row.get("by_owner", True)),
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


# ---- signed contracts -------------------------------------------------
#
# The gap this closes: the product could *quote* a three-year deal with a
# five percent annual escalator, a setup fee and a prepay discount, and
# then bill a flat month-one rate, by hand, if somebody remembered. The
# quote and the money were two different numbers, and the difference grew
# every year of the term.
#
# A Subscription is the signed deal made durable: the term, the rate, the
# escalator, the attached add-ons and — the part that matters — a count of
# the periods already invoiced, so the billing run can be safely re-run,
# restarted, or caught up after an outage without ever charging the same
# month twice.

# Nobody signs beyond this, and the quote endpoint already caps at it.
MAX_TERM_YEARS = 5

# A catch-up ceiling. If a clock jumps, a database is restored from an old
# backup, or a subscription is backdated by mistake, the billing run must
# not answer by issuing eighty invoices to a real customer. It bills what
# it can, logs loudly, and leaves the rest for a human.
MAX_CATCHUP_PERIODS = 3


def add_months(moment: datetime, months: int) -> datetime:
    """Shift by whole calendar months, clamping the day.

    31 January plus one month is 28 February, not 3 March. Getting this
    wrong drifts a billing date forward a few days every year until an
    invoice dated the 3rd is chasing a contract signed on the 31st.
    """
    total = moment.month - 1 + months
    year = moment.year + total // 12
    month = total % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


@dataclass
class Subscription:
    """A countersigned contract that bills itself."""

    subscription_id: str
    tenant_id: str
    term_years: int
    escalator_percent: float
    annual_prepay: bool
    add_ons: List[str] = field(default_factory=list)
    purchase_order: Optional[str] = None
    auto_renew: bool = True
    periods_billed: int = 0
    setup_billed: bool = False
    started_at: Optional[datetime] = None
    signed_by: str = ""
    cancelled_at: Optional[datetime] = None
    cancellation_reason: str = ""
    last_billed_at: Optional[datetime] = None
    # Where a renewal picks the rate up from. A second term starts at the
    # rate the first one finished on, not back at the year-one number.
    carried_multiplier: float = 1.0
    # Periods whose invoice was voided and which must therefore be issued
    # again. Without this a voided invoice is a month of service that can
    # never be charged for: `periods_billed` is a high-water mark, so the
    # run simply moves past the hole.
    rebill_periods: List[int] = field(default_factory=list)

    @property
    def months_per_period(self) -> int:
        """Paying a year up front means one invoice a year, not twelve."""
        return 12 if self.annual_prepay else 1

    @property
    def active(self) -> bool:
        return self.cancelled_at is None

    def period_start(self, index: int) -> datetime:
        """When period `index` (0-based) begins."""
        return add_months(self.started_at, index * self.months_per_period)

    def contract_year(self, index: int) -> int:
        """Which year of the term period `index` falls in, 1-based."""
        return (index * self.months_per_period) // 12 + 1

    def rate_multiplier(self, index: int) -> float:
        """The escalator, compounded to the contract year of this period.

        Year one is 1.0 — an escalator that applied on day one would be a
        price rise, not an escalator, and the customer signed the year-one
        number.
        """
        return round(
            self.carried_multiplier
            * (1 + self.escalator_percent / 100.0) ** (self.contract_year(index) - 1),
            6,
        )

    @property
    def ends_at(self) -> datetime:
        return add_months(self.started_at, self.term_years * 12)

    def expired(self, now: Optional[datetime] = None) -> bool:
        """Past its term with no automatic renewal. Billing stops here."""
        return not self.auto_renew and (now or utc_now()) >= self.ends_at

    def days_to_renewal(self, now: Optional[datetime] = None) -> int:
        """Days until the term ends; negative once it has.

        Truncated toward zero rather than floored. `//` rounds *down*, so
        a term that ended twenty-five hours ago came back as -2 and the
        worklist said "ended 2 days ago". Toward zero reads correctly in
        both directions: 5.9 days left is "ends in 5 days", which errs
        early, and 1.04 days past is "ended 1 day ago".
        """
        return int((self.ends_at - (now or utc_now())).total_seconds() / 86400)

    def due_periods(self, now: Optional[datetime] = None) -> List[int]:
        """Period indices that have started and have not been invoiced.

        A period is billed in advance: the invoice for the month goes out
        on the day the month begins, which is what every subscription
        contract in this market says and what makes non-payment visible
        before the service has been delivered rather than after.
        """
        now = now or utc_now()
        if not self.active or self.started_at is None:
            return []
        # Holes first. A voided invoice leaves a period that has already
        # been "billed" as far as the counter is concerned, and it has to
        # come back round before the run moves on.
        due = [i for i in sorted(self.rebill_periods) if self.period_start(i) <= now]
        index = self.periods_billed
        while self.period_start(index) <= now:
            if self.expired(self.period_start(index)):
                break
            due.append(index)
            index += 1
            if len(due) >= MAX_CATCHUP_PERIODS:
                break
        return due

    def to_row(self) -> Dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "tenant_id": self.tenant_id,
            "term_years": self.term_years,
            "escalator_percent": self.escalator_percent,
            "annual_prepay": self.annual_prepay,
            "add_ons": list(self.add_ons),
            "purchase_order": self.purchase_order,
            "auto_renew": self.auto_renew,
            "periods_billed": self.periods_billed,
            "setup_billed": self.setup_billed,
            "started_at": iso(self.started_at),
            "signed_by": self.signed_by,
            "cancelled_at": iso(self.cancelled_at),
            "cancellation_reason": self.cancellation_reason,
            "last_billed_at": iso(self.last_billed_at),
            "carried_multiplier": self.carried_multiplier,
            "rebill_periods": sorted(self.rebill_periods),
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Subscription":
        return cls(
            subscription_id=row["subscription_id"],
            tenant_id=row["tenant_id"],
            term_years=row["term_years"],
            escalator_percent=row["escalator_percent"],
            annual_prepay=row.get("annual_prepay", False),
            add_ons=list(row.get("add_ons") or []),
            purchase_order=row.get("purchase_order"),
            auto_renew=row.get("auto_renew", True),
            periods_billed=row.get("periods_billed", 0),
            setup_billed=row.get("setup_billed", False),
            started_at=_parse(row.get("started_at")),
            signed_by=row.get("signed_by", ""),
            cancelled_at=_parse(row.get("cancelled_at")),
            cancellation_reason=row.get("cancellation_reason", ""),
            last_billed_at=_parse(row.get("last_billed_at")),
            carried_multiplier=row.get("carried_multiplier", 1.0),
            rebill_periods=list(row.get("rebill_periods") or []),
        )

    def public(self) -> Dict[str, Any]:
        row = self.to_row()
        row.update(
            {
                "status": "active" if self.active else "cancelled",
                "billing_interval": (
                    "annual" if self.annual_prepay else "monthly"
                ),
                "current_contract_year": self.contract_year(
                    max(0, self.periods_billed - 1)
                ),
                "current_rate_multiplier": self.rate_multiplier(
                    max(0, self.periods_billed - 1)
                ),
                "next_billing_at": iso(self.period_start(self.periods_billed)),
                "ends_at": iso(self.ends_at),
                "days_to_renewal": self.days_to_renewal(),
            }
        )
        return row




# How many times a queued message is attempted before it is given up on.
# Two retries past the first try: enough to ride out a restarting mail
# host, not enough to hammer one that is refusing us.
MAIL_MAX_ATTEMPTS = 3

# A message that has sat in the queue longer than this is never sent. The
# queue exists so that a mail outage does not lose a chase; it must not
# become a machine that fires a month of stale notices at a customer the
# afternoon somebody finally configures SMTP.
MAIL_MAX_AGE_HOURS = 72

# How long the full text of a handled message is kept. After this the row
# keeps who it went to, the subject, when, and whether the mail host took
# it -- enough to answer "we never got that bill" -- and the body goes.
# Without it every invoice, receipt and weekly report ever sent was kept
# word for word for as long as the account lived, in the fastest-growing
# table after readings. A year and a month: an annual invoice's whole
# cycle, plus the thirty days a customer has to query a bill.
MAIL_BODY_KEEP_DAYS = 400

MAIL_BODY_REMOVED = "[Text removed after {days} days. Recipient, subject and delivery are kept.]"


# The categories an expense usually falls into. Not a tax schedule and
# not jurisdiction-specific — a filing cabinet with labelled drawers, so
# that whoever prepares the return can map them onto whatever the local
# form actually calls these things.
EXPENSE_CATEGORIES = (
    "hardware",            # sensors, laptop, phone, test equipment
    "software",            # subscriptions, tooling, hosting bought elsewhere
    "infrastructure",      # servers, domains, certificates
    "telephony",           # Twilio and anything like it
    "ai_and_apis",         # model providers, third-party data
    "professional_fees",   # accountant, lawyer, filing fees
    "marketing",           # advertising, events, printing
    "travel",              # getting to a customer site
    "insurance",
    "bank_and_payment_fees",
    "other",
)


@dataclass
class Expense:
    """Something the business paid for, and the evidence that it did.

    Kept because a deduction you cannot evidence is a deduction you do
    not get. The application already knew what it spent on models and
    telephony; everything bought on a card it never sees — the laptop,
    the accountant, the domain — existed only in somebody's memory at
    filing time, which is the same as not existing.

    `receipt` is deliberately a plain string rather than a file. What
    matters here is that the record points at where the evidence is; a
    document store for receipts is a different job, and pretending to be
    one would be worse than being honest about not being one.
    """

    expense_id: str
    spent_at: datetime
    amount_usd: float
    category: str
    description: str
    supplier: str = ""
    receipt: str = ""
    recorded_at: Optional[datetime] = None

    def to_row(self) -> Dict[str, Any]:
        return {
            "expense_id": self.expense_id,
            "spent_at": iso(self.spent_at),
            "amount_usd": self.amount_usd,
            "category": self.category,
            "description": self.description,
            "supplier": self.supplier,
            "receipt": self.receipt,
            "recorded_at": iso(self.recorded_at),
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Expense":
        return cls(
            expense_id=row["expense_id"],
            spent_at=_parse(row["spent_at"]),
            amount_usd=row["amount_usd"],
            category=row.get("category", "other"),
            description=row.get("description", ""),
            supplier=row.get("supplier", ""),
            receipt=row.get("receipt", ""),
            recorded_at=_parse(row.get("recorded_at")),
        )

    def public(self) -> Dict[str, Any]:
        row = self.to_row()
        row["has_receipt"] = bool(self.receipt.strip())
        return row


SENSITIVE_BODY_REMOVED = "[Removed once handled: this message held a one-time link.]"


@dataclass
class MailMessage:
    """One outbound email, and what became of it.

    Every send is written down before it is attempted. That is what makes
    the transport safe to call from the billing pass: the claim on
    `dedupe_key` happens under the store's lock, so two overlapping passes
    produce one message, and a crash between claiming and sending leaves a
    queued row that the next pass retries rather than a chase nobody has
    any record of.
    """

    message_id: str
    dedupe_key: str
    to_address: str
    subject: str
    body: str
    # "transactional" is mail the customer's own account produced — an
    # invoice, a receipt, a security notice. "commercial" is mail we chose
    # to send them. The distinction is not decoration: it decides whether
    # an unsubscribe stops it.
    klass: str
    created_at: datetime
    status: str = "queued"  # queued | sent | failed
    attempts: int = 0
    detail: str = ""
    sent_at: Optional[datetime] = None
    tenant_id: Optional[str] = None
    # The body holds something that works on its own, such as a password
    # reset link. It is kept only until the message has gone (or never
    # will), then replaced, so the mail log is not a drawer of live links.
    sensitive: bool = False

    def age_hours(self, now: Optional[datetime] = None) -> float:
        now = now or utc_now()
        return (now - self.created_at).total_seconds() / 3600.0

    def to_row(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "dedupe_key": self.dedupe_key,
            "to_address": self.to_address,
            "subject": self.subject,
            "body": self.body,
            "klass": self.klass,
            "created_at": iso(self.created_at),
            "status": self.status,
            "attempts": self.attempts,
            "detail": self.detail,
            "sent_at": iso(self.sent_at) if self.sent_at else None,
            "tenant_id": self.tenant_id,
            "sensitive": self.sensitive,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "MailMessage":
        return cls(
            message_id=row["message_id"],
            dedupe_key=row["dedupe_key"],
            to_address=row["to_address"],
            subject=row["subject"],
            body=row["body"],
            klass=row.get("klass", "transactional"),
            created_at=_parse(row["created_at"]),
            status=row.get("status", "queued"),
            attempts=row.get("attempts", 0),
            detail=row.get("detail", ""),
            sent_at=_parse(row["sent_at"]) if row.get("sent_at") else None,
            tenant_id=row.get("tenant_id"),
            sensitive=bool(row.get("sensitive", False)),
        )

    def public(self) -> Dict[str, Any]:
        """The log line. Deliberately without the body.

        The operator needs to know that a chase went out, to whom, and
        whether it landed. Replaying the full text of every notice we have
        ever sent every customer through one admin endpoint is a much
        larger disclosure than that question needs.
        """
        return {
            "message_id": self.message_id,
            "to": mask_email(self.to_address),
            "subject": self.subject,
            "klass": self.klass,
            "status": self.status,
            "attempts": self.attempts,
            "detail": self.detail,
            "created_at": iso(self.created_at),
            "sent_at": iso(self.sent_at) if self.sent_at else None,
            "tenant_id": self.tenant_id,
        }


@dataclass
class MailSuppression:
    """An address we have stopped writing to, and why.

    Two reasons, and they do not mean the same thing. `unsubscribed` is the
    customer declining mail they did not have to receive; it stops
    commercial mail and nothing else, because an unsubscribe link cannot
    be a way to stop being invoiced. `bounced` is the mail host telling us
    the address does not exist; that stops everything, because continuing
    to send to it is how a sending domain gets blacklisted and then none
    of our mail reaches anybody.
    """

    address: str
    reason: str
    created_at: datetime
    detail: str = ""

    @property
    def blocks_everything(self) -> bool:
        return self.reason == "bounced"

    def to_row(self) -> Dict[str, Any]:
        return {
            "address": self.address,
            "reason": self.reason,
            "created_at": iso(self.created_at),
            "detail": self.detail,
        }

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "MailSuppression":
        return cls(
            address=row["address"],
            reason=row["reason"],
            created_at=_parse(row["created_at"]),
            detail=row.get("detail", ""),
        )

    def public(self) -> Dict[str, Any]:
        return {
            "address": mask_email(self.address),
            "reason": self.reason,
            "created_at": iso(self.created_at),
            "detail": self.detail,
        }
