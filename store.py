"""Shared domain model and in-memory state for the CyberLogix AI hub.

Every router in the suite reads and writes through the single `STORE`
instance defined at the bottom of this module. State is held in memory and
guarded by a re-entrant lock, which is correct for a single Cloud Run
instance. Swapping this class for a Firestore or Postgres adapter is the
one change required to scale horizontally.

The record shapes (Tenant, Sensor, Invoice and the rest) live in
records.py, and the fixed rules (industry profiles, plans, the breach
check, volume pricing, temperature units) in domain.py. Both are
re-exported from here, so every existing `from store import ...` works.
Settings that tests adjust on this module (MAX_READINGS_PER_SENSOR,
MAX_CACHE_ENTRIES, SCRYPT_N and the like) stay here, beside the code that
reads them.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
from collections import OrderedDict, deque
from itertools import chain, islice
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, List, Optional, Tuple

from domain import (
    DISPATCH_SLA_SECONDS,
    SENSOR_OFFLINE_AFTER_MINUTES,
    VOICE_ESCALATION_GRACE_MINUTES,
    SMS_RETRY_AFTER_MINUTES,
    RETRYABLE_SMS_STATUSES,
    VOICE_REDIAL_COOLDOWN_MINUTES,
    utc_now,
    iso,
    hash_secret,
    _parse,
    mask_email,
    TEMPERATURE_UNITS,
    ABSOLUTE_ZERO_F,
    IMPLAUSIBLE_ABOVE_F,
    SeatClaimRefused,
    ImplausibleReading,
    require_plausible,
    to_celsius,
    to_fahrenheit,
    display_temperature,
    format_temperature,
    _WRITTEN_TEMPERATURE,
    restate_temperatures,
    spoken_temperature,
    INDUSTRY_PROFILES,
    PLAN_TIERS,
    resolve_vertical,
    evaluate_breach,
    evaluate_sensor_breach,
    VOLUME_DISCOUNT_STEP_PERCENT,
    VOLUME_DISCOUNT_EVERY_UNITS,
    MAX_VOLUME_DISCOUNT_PERCENT,
    ENTERPRISE_MONTHLY_CAP,
    volume_discount_percent,
    _raw_total,
    calculate_volume_tier_price,
    volume_band,
    volume_tier_label,
    next_volume_tier,
    volume_band_table,
)
from records import (
    Tenant,
    Sensor,
    Reading,
    Incident,
    ROLES,
    ROLE_RANK,
    SESSION_TTL_HOURS,
    User,
    LoginSession,
    AuditEntry,
    UsageDay,
    Site,
    VaultAnchor,
    INVOICE_STATES,
    Invoice,
    DEFAULT_PARTNER_COMMISSION_PERCENT,
    Partner,
    WEBHOOK_KINDS,
    AlertWebhook,
    Contact,
    JoinCode,
    ResetToken,
    EnterpriseContract,
    MAX_TERM_YEARS,
    MAX_CATCHUP_PERIODS,
    add_months,
    Subscription,
    MAIL_MAX_ATTEMPTS,
    MAIL_MAX_AGE_HOURS,
    MAIL_BODY_KEEP_DAYS,
    MAIL_BODY_REMOVED,
    EXPENSE_CATEGORIES,
    Expense,
    SENSITIVE_BODY_REMOVED,
    MailMessage,
    MailSuppression,
)

from db import Database

logger = logging.getLogger("cyberlogix.store")

# Readings retained per sensor, newest first; older ones are dropped from
# memory and deleted from the database.
#
# The default covers the forecaster's trend window and little else. At a
# five-minute pulse 500 readings is about 41 hours — so a compliance
# report asked for "the last 30 days" is answered from under two days of
# data, and used to say so nowhere. That is the dangerous part, not the
# cap itself: a bounded ring buffer is a reasonable thing to have, and a
# document that presents a truncated record as a complete one is not.
#
# Every artifact that counts readings now states the retained window and
# flags a period it cannot actually cover. Raise this to keep more, at
# the cost of memory — every retained reading is held in a deque as well
# as on disk, so a thousand sensors at 50,000 each is fifty million
# objects in RAM, not merely a larger file.
try:
    MAX_READINGS_PER_SENSOR = max(
        1, int(os.environ.get("CYBERLOGIX_READINGS_PER_SENSOR", "500"))
    )
except ValueError:
    MAX_READINGS_PER_SENSOR = 500

# How many generated answers are kept. Every distinct prompt is a new
# key, so this is the fastest-growing table in the product, and a cache
# is a saving rather than a record: losing the least recently wanted
# entry costs one model call, and keeping every entry ever costs a
# database that never stops growing.
MAX_CACHE_ENTRIES = 5000
# And how long any one of them may be. The count alone bounded the rows
# but not the bytes: the model's answer has no length limit, so 5000
# entries could each be a page long. An alert or a summary is a few
# hundred characters; anything past this is answered and not kept.
MAX_CACHE_TEXT_CHARS = 8000


# scrypt's cost factor. 2**14 with r=8 is the standard figure for an
# interactive sign-in: about 50 ms and 16 MB for every guess. The test
# suite lowers it in tests/conftest.py, because it hashes two thousand
# passwords and at full cost that was half of its running time; a test
# there holds this default where it is.
SCRYPT_N = 16384  # 2**14


def hash_password(password: str) -> str:
    """Hash a password with scrypt and a fresh random salt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=8, p=1, dklen=32
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
            n=SCRYPT_N,
            r=8,
            p=1,
            dklen=32,
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(digest.hex(), digest_hex)


MAX_ANCHORS_PER_SENSOR_PER_DAY = 48


def _mirror_payment(caller: "Invoice", live: "Invoice") -> None:
    """Copy the settled state back onto whatever object the caller holds.

    Callers routinely keep the Invoice they were handed and read it after
    settling. The store works on its own live object, so without this the
    caller sees stale figures and reports a payment that did or did not
    happen based on a copy.
    """
    if caller is live:
        return
    caller.payments = list(live.payments)
    caller.amount_paid_usd = live.amount_paid_usd
    caller.payment_reference = live.payment_reference
    caller.paid_at = live.paid_at
    caller.state = live.state


# Mail that is part of an alert rather than of the business: retried by
# the sweep, on the minute, while the alert is open.
ALERT_MAIL_KEYS = ("incident-alert:", "incident-undelivered:")


class HubStore:
    """Thread-safe in-memory persistence shared by every router."""

    def __init__(self, db: Optional[Database] = None) -> None:
        self._lock = threading.RLock()
        self._tenants: Dict[str, Tenant] = {}
        self._keys: Dict[str, str] = {}
        self._sensors: Dict[str, Sensor] = {}
        self._devices: Dict[str, str] = {}
        # ingest key -> sensor_id. A sensor's own credential.
        self._ingest_keys: Dict[str, str] = {}
        self._readings: Dict[str, Deque[Reading]] = {}
        self._incidents: Dict[str, Incident] = {}
        self._users: Dict[str, User] = {}
        self._emails: Dict[str, str] = {}
        self._sessions: Dict[str, LoginSession] = {}
        self._audit: Dict[str, AuditEntry] = {}
        self._sites: Dict[str, Site] = {}
        self._contacts: Dict[str, Contact] = {}
        self._contracts: Dict[str, EnterpriseContract] = {}
        self._subscriptions: Dict[str, Subscription] = {}
        self._resets: Dict[str, ResetToken] = {}
        self._join_codes: Dict[str, JoinCode] = {}
        self._webhooks: Dict[str, AlertWebhook] = {}
        self._invoices: Dict[str, Invoice] = {}
        self._anchors: Dict[str, VaultAnchor] = {}
        self._partners: Dict[str, Partner] = {}
        self._partner_keys: Dict[str, str] = {}
        self._usage: Dict[str, UsageDay] = {}
        self._ai_cache: "OrderedDict[str, str]" = OrderedDict()
        self._expenses: Dict[str, Expense] = {}
        self._mail: Dict[str, MailMessage] = {}
        # dedupe key -> message id. The index that makes a send idempotent.
        self._mail_keys: Dict[str, str] = {}
        self._suppressions: Dict[str, MailSuppression] = {}
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
                self._keys[tenant.key_hash] = tenant.tenant_id
                if "api_key" in row:
                    # Written before keys were hashed: drop the plain key.
                    self._db.put("tenant", tenant.tenant_id, tenant.to_row())

            for row in self._db.all("sensor"):
                sensor = Sensor.from_row(row)
                self._sensors[sensor.sensor_id] = sensor
                self._readings.setdefault(
                    sensor.sensor_id, deque(maxlen=MAX_READINGS_PER_SENSOR)
                )
                if sensor.external_device_sn:
                    self._devices[sensor.external_device_sn] = sensor.sensor_id
                if sensor.ingest_key_hash:
                    self._ingest_keys[sensor.ingest_key_hash] = sensor.sensor_id
                if "ingest_key" in row:
                    self._db.put("sensor", sensor.sensor_id, sensor.to_row())

            # Ordered by time, then by id. The id tiebreak is not
            # cosmetic: timestamps are stored to the second, so two
            # readings from the same second are tied, and a tie resolved
            # by whatever order the rows came back in would put them in a
            # different sequence after a restart. The vault chains
            # readings in order, so that reordering silently changes the
            # chain head — the attestation stops reproducing and the
            # customer is told their record was tampered with when nothing
            # touched it. Reading ids are monotonic, so they settle it --
            # as numbers, which the database compares (db.py).
            #
            # Streamed into the buckets rather than built as a list first,
            # and each reading shares its sensor's id string rather than
            # holding its own copy: see docs/LOAD-TEST.md for what the
            # old way cost at start-up.
            buckets = self._readings

            def _take(row: Dict[str, Any]) -> None:
                bucket = buckets.get(row["sensor_id"])
                if bucket is None:
                    return
                reading = Reading.from_row(row)
                sensor = self._sensors.get(reading.sensor_id)
                if sensor is not None:
                    reading.sensor_id = sensor.sensor_id
                bucket.append(reading)

            self._db.each_reading_in_order(_take)

            for row in self._db.all("incident"):
                incident = Incident.from_row(row)
                self._incidents[incident.incident_id] = incident

            for row in self._db.all("user"):
                user = User.from_row(row)
                self._users[user.user_id] = user
                if user.linked_to is None:
                    self._emails[user.email] = user.user_id

            for row in self._db.all("session"):
                session = LoginSession.from_row(row)
                if session.token.startswith("cls_"):
                    # Written before tokens were hashed: keep the session,
                    # lose the plain token from disk.
                    self._db.delete("session", session.token)
                    session.token = hash_secret(session.token)
                    if not session.expired:
                        self._db.put("session", session.token, session.to_row())
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

            for row in self._db.all("invoice"):
                invoice = Invoice.from_row(row)
                self._invoices[invoice.invoice_id] = invoice

            for row in self._db.all("anchor"):
                anchor = VaultAnchor.from_row(row)
                self._anchors[anchor.anchor_id] = anchor

            for row in self._db.all("partner"):
                partner = Partner.from_row(row)
                self._partners[partner.partner_id] = partner
                self._partner_keys[partner.key_hash] = partner.partner_id
                if "api_key" in row:
                    self._db.put("partner", partner.partner_id, partner.to_row())

            for row in self._db.all("webhook"):
                hook = AlertWebhook.from_row(row)
                self._webhooks[hook.webhook_id] = hook

            for row in self._db.all("contact"):
                contact = Contact.from_row(row)
                self._contacts[contact.contact_id] = contact

            for row in self._db.all("contract"):
                contract = EnterpriseContract.from_row(row)
                self._contracts[contract.account_id] = contract

            for row in self._db.all("subscription"):
                sub = Subscription.from_row(row)
                self._subscriptions[sub.subscription_id] = sub

            for row in self._db.all("reset"):
                reset = ResetToken.from_row(row)
                if reset.spent:
                    self._db.delete("reset", reset.token_hash)
                else:
                    self._resets[reset.token_hash] = reset

            for row in self._db.all("join_code"):
                code = JoinCode.from_row(row)
                if code.spent:
                    self._db.delete("join_code", code.token_hash)
                else:
                    self._join_codes[code.token_hash] = code

            for row in self._db.all("usage"):
                usage = UsageDay.from_row(row)
                self._usage[usage.key] = usage

            for row in self._db.all("aicache"):
                self._ai_cache[row["key"]] = row["text"]
            # A database written before the cap existed can hold more
            # than the cap allows, so trim on the way in rather than
            # waiting for the next write to notice.
            self._evict_cache_locked()

            for row in self._db.all("expense"):
                expense = Expense.from_row(row)
                self._expenses[expense.expense_id] = expense

            for row in self._db.all("mail"):
                message = MailMessage.from_row(row)
                self._mail[message.message_id] = message
                self._mail_keys[message.dedupe_key] = message.message_id

            for row in self._db.all("mailblock"):
                block = MailSuppression.from_row(row)
                self._suppressions[block.address] = block

            # Identifiers are sequential and share one counter, so a restart
            # must resume past the highest one ever issued. Getting this
            # wrong is not a cosmetic bug: ids are the primary key, so a
            # reused id makes the next write silently overwrite an existing
            # row — and because the counter is global, the row it destroys
            # can belong to a different tenant.
            #
            # Two sources, and the larger wins:
            #
            #  1. The counter as persisted. Authoritative, and the only
            #     thing that survives an entity being deleted after it took
            #     the high-water mark.
            #  2. A scan of every id actually present. The fallback for a
            #     database written before the counter was persisted, and a
            #     check on the first.
            #
            # The scan must cover EVERY kind that draws from the counter.
            # Readings and audit entries were missing from this list, and
            # they are by far the most numerous — the counter came back
            # near zero and tenants overwrote each other's telemetry.
            # A generator, not a list: the readings alone are half a
            # million ids at a thousand sensors.
            identifiers = chain(
                self._tenants, self._incidents, self._users, self._contacts,
                self._sites, self._webhooks, self._partners, self._invoices,
                self._subscriptions, self._anchors, self._audit, self._mail,
                self._expenses,
                (r.reading_id for bucket in self._readings.values() for r in bucket),
            )
            issued = False
            highest = 0
            for identifier in identifiers:
                tail = identifier.rsplit("-", 1)[-1]
                if "-" in identifier and tail.isdigit():
                    issued = True
                    highest = max(highest, int(tail))
            persisted = (self._db.get("meta", "counter") or {}).get("value", 0)
            self._counter = max(persisted, highest)

            if issued or self._sensors:
                logger.info(
                    "Restored %d tenants, %d sensors, %d readings, %d incidents.",
                    len(self._tenants),
                    len(self._sensors),
                    sum(len(b) for b in self._readings.values()),
                    len(self._incidents),
                )

    def forget(self) -> None:
        """Drop the in-memory working set, leaving the database alone.

        Split out of reset() for the restore path, which replaces the
        file underneath and then has to rebuild from what is now there
        rather than from what this process remembers.
        """
        with self._lock:
            self._forget_locked()

    def _forget_locked(self) -> None:
        # The lock is an RLock, so taking it again here is free and this
        # stays correct whichever way it is reached.
        with self._lock:
            self._tenants.clear()
            self._keys.clear()
            self._sensors.clear()
            self._devices.clear()
            self._ingest_keys.clear()
            self._readings.clear()
            self._incidents.clear()
            self._users.clear()
            self._emails.clear()
            self._sessions.clear()
            self._audit.clear()
            self._sites.clear()
            self._contacts.clear()
            self._webhooks.clear()
            self._invoices.clear()
            self._anchors.clear()
            self._partners.clear()
            self._partner_keys.clear()
            self._contracts.clear()
            self._subscriptions.clear()
            self._resets.clear()
            self._join_codes.clear()
            self._usage.clear()
            self._ai_cache.clear()
            self._expenses.clear()
            self._mail.clear()
            self._mail_keys.clear()
            self._suppressions.clear()
            self._counter = 0

    def reset(self) -> None:
        """Drop all state, on disk as well as in memory. Used by tests."""
        with self._lock:
            self._forget_locked()
            self._db.clear()
            # clear() drops the meta row along with everything else, so the
            # counter is genuinely back to zero rather than merely in memory.
            # That also drops the install stamp, and a deployment with no
            # stamp cannot say which months it paid for hosting — so a
            # wiped database is stamped again, as the new deployment it is.
            self._db._note_install()

    def _next_id(self, prefix: str) -> str:
        """Allocate the next identifier, and remember that it was taken.

        Persisted on every allocation rather than reconstructed at startup:
        a scan can only ever see ids that still exist, so deleting the
        newest row would otherwise hand its id straight back out.
        """
        with self._lock:
            self._counter += 1
            self._db.put("meta", "counter", {"value": self._counter})
            return f"{prefix}-{self._counter:06d}"

    # ---- tenants -------------------------------------------------------

    def create_tenant(
        self,
        company_name: str,
        contact_name: str,
        contact_phone: str,
        contact_email: str,
        plan: str,
        industry_vertical: Optional[str] = None,
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
                api_key=(key := f"clx_{secrets.token_urlsafe(32)}"),
                key_hash=hash_secret(key),
                activated_at=now,
                expires_at=now + timedelta(days=PLAN_TIERS[plan]["term_days"]),
                industry_vertical=industry_vertical,
            )
            self._tenants[tenant.tenant_id] = tenant
            self._keys[tenant.key_hash] = tenant.tenant_id
            self._db.put("tenant", tenant.tenant_id, tenant.to_row())
            return tenant

    def delete_tenant(self, tenant_id: str) -> bool:
        """Remove a tenant that was never really created.

        Narrow on purpose: this exists to undo a sign-up that failed
        half-way, so it refuses any tenant that has grown anything. A
        tenant with no users cannot be signed into and is otherwise dead
        weight in the fleet forever; a tenant with users, sensors or
        invoices is a customer, and deleting one of those is not something
        an exception handler should be able to do.
        """
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if tenant is None:
                return False
            has_history = (
                any(u.tenant_id == tenant_id for u in self._users.values())
                or any(s.tenant_id == tenant_id for s in self._sensors.values())
                or any(i.tenant_id == tenant_id for i in self._invoices.values())
            )
            if has_history:
                logger.error(
                    "Refusing to delete tenant %s: it already has users, "
                    "sensors or invoices.", tenant_id,
                )
                return False
            self._keys.pop(tenant.key_hash, None)
            self._tenants.pop(tenant_id, None)
            self._db.delete("tenant", tenant_id)
            return True

    # ---- a business leaving: everything it has, and deleting it -----------

    # Kinds that never hold one business's data: the platform's own
    # settings, its expenses and error log, the AI wording cache (keyed by
    # wording, shared), resellers, payments nobody could match, and the
    # list of addresses that asked never to be mailed again — deleting an
    # address from that list would mean writing to it again.
    _NOT_A_BUSINESS_KIND = frozenset({
        "meta", "aicache", "mailblock", "partner", "expense", "fault",
        "unmatched_payment", "erasure",
    })
    # Kept after a business deletes itself: the bills it was sent. Tax law
    # makes a seller keep them for years, and each one already carries the
    # company name it was issued to.
    KEPT_AFTER_ERASE = frozenset({"invoice"})
    # Never handed out in an export, even to the owner.
    _SECRET_FIELDS = frozenset({
        "password_hash", "key_hash", "api_key", "token", "token_hash",
        "secret", "ingest_key", "ingest_key_hash",
    })

    def _rows_of_tenant_locked(self, tenant_id: str) -> List[Tuple[str, str, Dict[str, Any]]]:
        sensors = {s.sensor_id for s in self._sensors.values() if s.tenant_id == tenant_id}
        users = {u.user_id for u in self._users.values() if u.tenant_id == tenant_id}
        sites = {s.site_id for s in self._sites.values() if s.tenant_id == tenant_id}
        incidents = {i.incident_id for i in self._incidents.values() if i.tenant_id == tenant_id}
        found: List[Tuple[str, str, Dict[str, Any]]] = []
        for kind in self._db.kinds():
            if kind in self._NOT_A_BUSINESS_KIND:
                continue
            for rec_id, data in self._db.rows(kind):
                owner = data.get("tenant_id")
                if owner is not None:
                    # A row that names its business belongs to that one,
                    # whatever else it points at.
                    mine = owner == tenant_id
                else:
                    mine = (
                        rec_id == tenant_id
                        or data.get("sensor_id") in sensors
                        or data.get("user_id") in users
                        or data.get("site_id") in sites
                        or data.get("incident_id") in incidents
                    )
                if mine:
                    found.append((kind, rec_id, data))
        return found

    def export_tenant(self, tenant_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """Everything stored for one business, by kind, minus secrets."""
        with self._lock:
            rows = self._rows_of_tenant_locked(tenant_id)
        out: Dict[str, List[Dict[str, Any]]] = {}
        for kind, _rec_id, data in rows:
            clean = {k: v for k, v in data.items() if k not in self._SECRET_FIELDS}
            if kind == "poll_source" and isinstance(clean.get("headers"), dict):
                # A vendor password. The console never shows it back; nor
                # does the download.
                clean["headers"] = {name: "(hidden)" for name in clean["headers"]}
            if kind == "webhook" and clean.get("target"):
                clean["target"] = AlertWebhook.from_row(data).masked_target()
            if kind == "mail" and clean.get("sensitive"):
                # A reset or sign-in link not yet scrubbed. Handing it to an
                # owner would let them into that person's other businesses.
                clean["body"] = "(hidden: it held a sign-in link)"
            out.setdefault(kind, []).append(clean)
        return out

    def erase_tenant(self, tenant_id: str) -> Dict[str, int]:
        """Delete a business and everything it has, except the bills.

        A person who also works at other businesses keeps those: their
        sign-in (email and password) moves to their record at the next
        business, so deleting one business never locks anybody out of
        another. Returns how many records of each kind were deleted.
        """
        with self._lock:
            if tenant_id not in self._tenants:
                return {}
            for person in [u for u in self._users.values()
                           if u.tenant_id == tenant_id and u.linked_to is None]:
                elsewhere = sorted(
                    (u for u in self._users.values()
                     if u.linked_to == person.user_id and u.tenant_id != tenant_id),
                    key=lambda u: (u.disabled, u.created_at),
                )
                if not elsewhere:
                    continue
                heir, rest = elsewhere[0], elsewhere[1:]
                heir.linked_to = None
                heir.email = person.email
                heir.password_hash = person.password_hash
                heir.password_from_owner = person.password_from_owner
                heir.last_login_at = person.last_login_at
                self._db.put("user", heir.user_id, heir.to_row())
                for other in rest:
                    other.linked_to = heir.user_id
                    self._db.put("user", other.user_id, other.to_row())

            doomed: Dict[str, List[str]] = {}
            for kind, rec_id, _data in self._rows_of_tenant_locked(tenant_id):
                if kind not in self.KEPT_AFTER_ERASE:
                    doomed.setdefault(kind, []).append(rec_id)
            for kind, ids in doomed.items():
                self._db.delete_many(kind, ids)
            # That it happened, and when, so a backup restored later can be
            # stopped from bringing it back. Kept as a fingerprint of the id
            # rather than the id: nothing left on file names the business.
            self._db.put("erasure", hash_secret(tenant_id),
                         {"erased_at": iso(utc_now())})
            # Rebuild the working set from what is left on disk, the same
            # way a restore does, so no index anywhere still points at it.
            self._forget_locked()
            self.load()
            return {kind: len(ids) for kind, ids in doomed.items()}

    def erasures(self) -> Dict[str, Dict[str, Any]]:
        """Every business that deleted itself: fingerprint of its id -> when."""
        with self._lock:
            return dict(self._db.rows("erasure"))

    def apply_erasures(self, erased: Dict[str, Dict[str, Any]]) -> List[str]:
        """Delete again any of these businesses that are present, and keep
        the record of every one. Returns the ids deleted again."""
        again: List[str] = []
        with self._lock:
            for print_, row in erased.items():
                self._db.put("erasure", print_, row)
            for tenant_id in list(self._tenants):
                if hash_secret(tenant_id) in erased:
                    self.erase_tenant(tenant_id)
                    again.append(tenant_id)
        return again

    # ---- what has already been sent, kept through a restore ----------------

    # Rows that are a record of something done in the world rather than
    # data a restore should roll back: the weekly text's "sent this week".
    _LEDGER_KINDS = ("weekly_text",)

    def delivery_ledger(self) -> Dict[str, Any]:
        """What this database knows has been sent, to carry past a restore.

        Restoring yesterday's snapshot rolled back the record of today's
        texts, calls and mail along with everything else, so the sweep and
        the hourly pass did them again: an incident already acknowledged
        rang the on-call phone again, the day's digest and the week's
        reports went out twice, and every sensor whose last reading in the
        snapshot was hours old was texted about as having gone quiet.
        """
        with self._lock:
            return {
                "incidents": [i.to_row() for i in self._incidents.values()],
                "mail": [m.to_row() for m in self._mail.values()],
                "sensors": {
                    s.sensor_id: {
                        "last_seen": s.last_seen,
                        "last_temperature": s.last_temperature,
                        "last_humidity": s.last_humidity,
                        "quiet_alert_for": s.quiet_alert_for,
                    }
                    for s in self._sensors.values()
                },
                "rows": {kind: self._db.rows(kind) for kind in self._LEDGER_KINDS},
                "heartbeat": self._db.get("meta", "heartbeat"),
            }

    def carry_forward(self, ledger: Dict[str, Any]) -> Dict[str, int]:
        """Put back, over a restored snapshot, the record of what was sent.

        Incidents and mail are kept whole, as the database being replaced
        had them: an incident is a breach that happened and was alerted on,
        and a message is one that went out, whatever the snapshot says.
        Sensors keep their latest contact, so a restore is not read as
        every sensor going quiet at once. Anything belonging to a business
        that is not in the snapshot is left out.
        """
        counts = {"incidents": 0, "mail": 0, "sensors": 0}
        with self._lock:
            for row in ledger.get("incidents", []):
                if row.get("tenant_id") not in self._tenants:
                    continue
                incident = Incident.from_row(row)
                self._incidents[incident.incident_id] = incident
                self._db.put("incident", incident.incident_id, incident.to_row())
                counts["incidents"] += 1
            for row in ledger.get("mail", []):
                owner = row.get("tenant_id")
                if owner is not None and owner not in self._tenants:
                    continue
                message = MailMessage.from_row(row)
                known = self._mail_keys.get(message.dedupe_key)
                if known is not None and known != message.message_id:
                    continue
                self._mail[message.message_id] = message
                self._mail_keys[message.dedupe_key] = message.message_id
                self._db.put("mail", message.message_id, message.to_row())
                counts["mail"] += 1
            for sensor_id, seen in ledger.get("sensors", {}).items():
                sensor = self._sensors.get(sensor_id)
                when = seen.get("last_seen")
                if sensor is None or when is None:
                    continue
                if sensor.last_seen is not None and sensor.last_seen >= when:
                    continue
                sensor.last_seen = when
                sensor.last_temperature = seen.get("last_temperature")
                sensor.last_humidity = seen.get("last_humidity")
                sensor.quiet_alert_for = seen.get("quiet_alert_for")
                self._db.put("sensor", sensor.sensor_id, sensor.to_row())
                counts["sensors"] += 1
            for kind, rows in ledger.get("rows", {}).items():
                for rec_id, data in rows:
                    if data.get("tenant_id", rec_id) in self._tenants:
                        self._db.put(kind, rec_id, data)
            if ledger.get("heartbeat"):
                self._db.put("meta", "heartbeat", ledger["heartbeat"])
        return counts

    def id_counter(self) -> int:
        with self._lock:
            return self._counter

    def raise_id_counter(self, floor: int) -> None:
        """Never hand out an id at or below `floor` again."""
        with self._lock:
            if floor > self._counter:
                self._counter = floor
                self._db.put("meta", "counter", {"value": self._counter})

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        with self._lock:
            return self._tenants.get(tenant_id)

    def tenant_by_key(self, api_key: str) -> Optional[Tenant]:
        if not api_key:
            return None
        with self._lock:
            tenant_id = self._keys.get(hash_secret(api_key))
            return self._tenants.get(tenant_id) if tenant_id else None

    def list_tenants(self) -> List[Tenant]:
        with self._lock:
            return list(self._tenants.values())

    def change_plan(self, tenant: Tenant, plan: str, extend_term: bool = True) -> Tenant:
        """Move a tenant to a plan. `extend_term` also starts the plan's term."""
        with self._lock:
            tenant.plan = plan
            if extend_term:
                tenant.expires_at = utc_now() + timedelta(days=PLAN_TIERS[plan]["term_days"])
            self._db.put("tenant", tenant.tenant_id, tenant.to_row())
            return tenant

    def grant_grace(self, tenant: Tenant, days: int) -> None:
        """An expired licence runs again until `days` from now. Never shortens one."""
        with self._lock:
            floor = utc_now() + timedelta(days=days)
            if tenant.expires_at < utc_now():
                tenant.expires_at = floor
                self._db.put("tenant", tenant.tenant_id, tenant.to_row())

    def extend_licence(self, tenant_id: str, days: int) -> None:
        """Paid time: the licence runs on by `days` from today or its end, later."""
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if tenant is None or days <= 0:
                return
            start = max(tenant.expires_at, utc_now())
            tenant.expires_at = start + timedelta(days=days)
            self._db.put("tenant", tenant.tenant_id, tenant.to_row())

    def set_suspended(self, tenant: Tenant, suspended: bool) -> Tenant:
        with self._lock:
            tenant.suspended = suspended
            self._db.put("tenant", tenant.tenant_id, tenant.to_row())
            return tenant

    # ---- sensors -------------------------------------------------------

    def claim_sensor_seat(
        self,
        sensor_id: str,
        tenant_id: str,
        industry_vertical: str,
        location_name: str,
        max_sensors: int,
        external_device_sn: Optional[str] = None,
    ) -> Sensor:
        """Check the seat count and register, without letting go in between.

        The route used to ask three questions — is this id taken, is this
        serial taken, is there a seat left — and only then write. Each
        question takes the lock and gives it back, so under concurrent
        registration every caller can be told yes before any of them
        writes.

        The seat count is the one that actually breaks, because counting
        seats walks every sensor the tenant owns: a long stretch of pure
        Python, and therefore a long stretch of chances to be preempted.
        Measured on a five-seat trial licence, twenty concurrent
        registrations: nine were granted. Four sensors monitored, and
        billed for, on a plan that does not include them.

        The id and serial checks are folded in here for the same reason,
        though those windows are narrow enough that 480 attempts never
        caught one. A missed seat is revenue; a lost id would be one
        company's freezer disappearing out of their console into another
        company's account, so it does not get to depend on being narrow.

        Raises SeatClaimRefused with a reason the caller can surface.
        """
        with self._lock:
            if sensor_id in self._sensors:
                raise SeatClaimRefused(
                    f"Sensor '{sensor_id}' is already registered.", "id_taken"
                )
            # A serial that is some sensor's id would capture that sensor's
            # readings: the device posting it resolved here first, and the
            # other company's freezer went quiet in their console while its
            # readings were held for the company that claimed the serial.
            if external_device_sn and (external_device_sn in self._devices
                                       or external_device_sn in self._sensors):
                raise SeatClaimRefused(
                    f"Device serial '{external_device_sn}' is already bound "
                    "to a sensor.",
                    "serial_taken",
                )
            used = sum(1 for s in self._sensors.values() if s.tenant_id == tenant_id)
            if max_sensors > 0 and used >= max_sensors:
                raise SeatClaimRefused(
                    f"Seat limit reached: this plan allows {max_sensors} "
                    "sensors. Upgrade to add more.",
                    "no_seats",
                )
            return self.register_sensor(
                sensor_id=sensor_id,
                tenant_id=tenant_id,
                industry_vertical=industry_vertical,
                location_name=location_name,
                external_device_sn=external_device_sn,
            )

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
                ingest_key=(key := f"clx_snr_{secrets.token_urlsafe(24)}"),
                ingest_key_hash=hash_secret(key),
                external_device_sn=external_device_sn,
            )
            self._sensors[sensor_id] = sensor
            self._ingest_keys[sensor.ingest_key_hash] = sensor_id
            if external_device_sn:
                self._devices[external_device_sn] = sensor_id
            self._readings.setdefault(
                sensor_id, deque(maxlen=MAX_READINGS_PER_SENSOR)
            )
            self._db.put("sensor", sensor_id, sensor.to_row())
            return sensor

    def sensor_by_ingest_key(self, key: str) -> Optional[Sensor]:
        """Resolve a sensor's own credential.

        A tenant API key is a master key: it speaks for the whole estate,
        and it was the only thing a sensor could carry. That put a
        credential able to register assets, retune alarm thresholds and
        suspend the entire licence inside a box bolted to the wall of a
        walk-in freezer, reachable by anyone with a screwdriver and a
        serial cable. This key does one thing: report readings, for one
        asset.
        """
        if not key:
            return None
        with self._lock:
            sensor_id = self._ingest_keys.get(hash_secret(key))
            return self._sensors.get(sensor_id) if sensor_id else None

    def rotate_api_key(self, tenant: Tenant) -> str:
        """Issue the business a new master key; the old one stops at once."""
        with self._lock:
            self._keys.pop(tenant.key_hash, None)
            tenant.api_key = f"clx_{secrets.token_urlsafe(32)}"
            tenant.key_hash = hash_secret(tenant.api_key)
            self._keys[tenant.key_hash] = tenant.tenant_id
            self._db.put("tenant", tenant.tenant_id, tenant.to_row())
            return tenant.api_key

    def rotate_ingest_key(self, sensor: Sensor) -> str:
        """Issue a new key for one sensor and retire the old one."""
        with self._lock:
            if sensor.ingest_key_hash:
                self._ingest_keys.pop(sensor.ingest_key_hash, None)
            sensor.ingest_key = f"clx_snr_{secrets.token_urlsafe(24)}"
            sensor.ingest_key_hash = hash_secret(sensor.ingest_key)
            self._ingest_keys[sensor.ingest_key_hash] = sensor.sensor_id
            self._db.put("sensor", sensor.sensor_id, sensor.to_row())
            return sensor.ingest_key

    def get_sensor(self, sensor_id: str) -> Optional[Sensor]:
        with self._lock:
            return self._sensors.get(sensor_id)

    def sensor_by_device(self, device_sn: str, tenant_id: Optional[str] = None) -> Optional[Sensor]:
        """Resolve a third-party device serial to its licensed sensor.

        Falls back to treating the serial as a sensor_id, so hardware whose
        serial was used directly at registration still resolves. Given a
        tenant, only that tenant's sensors count: another company's serial
        mapping can never stand in front of this company's own sensor.
        """
        with self._lock:
            key = (device_sn or "").strip()
            candidates = [self._sensors.get(self._devices.get(key, "")), self._sensors.get(key)]
            for sensor in candidates:
                if sensor is not None and (tenant_id is None or sensor.tenant_id == tenant_id):
                    return sensor
            return None

    def device_sn_taken(self, device_sn: str) -> bool:
        with self._lock:
            return (device_sn or "").strip() in self._devices

    def remove_sensor(self, sensor_id: str) -> List[str]:
        """Retire a sensor. Returns the ids of incidents closed with it.

        An incident still unresolved when its sensor goes would otherwise
        stay live for good: the sweep phones somebody about a freezer that
        is gone, and, because ids are free again once retired, every breach
        from whatever sensor takes the id next is folded into it, with no
        text sent. Closed here rather than left, with no acknowledgement
        recorded, because nobody answered it.
        """
        with self._lock:
            if sensor_id not in self._sensors:
                return []
            doomed = self._sensors[sensor_id]
            closed = []
            for incident in list(self._incidents.values()):
                if (incident.sensor_id == sensor_id and incident.unresolved
                        and incident.tenant_id == doomed.tenant_id):
                    incident.resolved_at = utc_now()
                    self._save_incident(incident)
                    # Before the readings go, so the evidence keeps them.
                    self.preserve_evidence(incident)
                    closed.append(incident.incident_id)
            if doomed.external_device_sn:
                self._devices.pop(doomed.external_device_sn, None)
            # The key dies with the sensor. A decommissioned asset whose
            # credential still resolves is a decommissioned asset that can
            # still write readings.
            if doomed.ingest_key_hash:
                self._ingest_keys.pop(doomed.ingest_key_hash, None)
            del self._sensors[sensor_id]
            evicted = self._readings.pop(sensor_id, None)
            self._db.delete("sensor", sensor_id)
            if evicted:
                self._db.delete_many("reading", [r.reading_id for r in evicted])
            return closed

    def all_sensors(self) -> List[Sensor]:
        """Every sensor in the fleet, across every tenant."""
        with self._lock:
            return list(self._sensors.values())

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
        # Checked here as well as at the API edge: every ingestion route and
        # the demo seeder come through this one door, and a non-finite value
        # that gets past it corrupts the stored JSON irreversibly.
        temperature_fahrenheit = require_plausible(temperature_fahrenheit)
        humidity_percent = require_plausible(humidity_percent, "humidity_percent")

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
                self._keep_as_evidence_locked(evicted, sensor.tenant_id)
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

    def open_incident_once(
        self,
        *,
        tenant_id: str,
        sensor: "Sensor",
        temperature_fahrenheit: float,
        breach_details: str,
        sms_text: str,
        sms_dispatch_source: str,
    ) -> "tuple[Incident, bool]":
        """Open an incident for this sensor, unless one is already open.

        Returns (incident, created). A False means somebody else opened it
        while this caller was drafting the message, and this caller must
        not send a second one.

        The product's central promise is that one failure produces one
        incident: one text, one phone call, one line in the log. That
        promise was enforced by reading the open incident, finding none,
        and then writing — two store calls with a gap between them wide
        enough to draft an SMS in. A freezer that is genuinely failing is
        exactly the situation that fills that gap, because the sensor
        behind it reports every few seconds and a site full of sensors
        reports at once.

        Measured, twenty-four simultaneous breach pulses from one sensor:
        between three and eight incidents opened, each with its own SMS
        fan-out to the whole roster and its own escalation to the on-call
        phone. The person on call is woken repeatedly, at night, for one
        freezer — which is how a customer learns to ignore the alerts.

        The lock is reentrant, so the read and the write happen inside one
        acquisition and no other caller can land between them.
        """
        with self._lock:
            existing = self.latest_unresolved_incident(sensor.sensor_id, tenant_id)
            if existing is not None:
                return existing, False
            return (
                self.open_incident(
                    tenant_id=tenant_id,
                    sensor=sensor,
                    temperature_fahrenheit=temperature_fahrenheit,
                    breach_details=breach_details,
                    sms_text=sms_text,
                    sms_dispatch_source=sms_dispatch_source,
                ),
                True,
            )

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
            # The lead-up, captured now. The readings that show a freezer
            # drifting out of band are the evidence an adjuster asks for,
            # and they are the first ones the ring buffer will delete.
            self.preserve_evidence(incident)
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

    def claim_sms_retry(self, incident: Incident, step: int) -> bool:
        """Take the right to send re-send `step` of this incident's alert.

        Under the lock and stored before anything is sent, the way the
        voice call and the quiet-sensor text are claimed: the timed sweep
        and the sweep button at the same moment send one re-send between
        them, and a restart does not send a step that was already sent.
        """
        with self._lock:
            live = self._incidents.get(incident.incident_id, incident)
            if not live.open or live.sms_retry_step >= step:
                return False
            live.sms_retry_step = step
            self._save_incident(live)
            return True

    def claim_voice_retry(self, incident: Incident, step: int) -> bool:
        """Take the right to walk the call ladder again, as claim_sms_retry."""
        with self._lock:
            live = self._incidents.get(incident.incident_id, incident)
            if not live.open or live.voice_retry_step >= step:
                return False
            live.voice_retry_step = step
            self._save_incident(live)
            return True

    def mail_by_key(self, dedupe_key: str) -> Optional[MailMessage]:
        with self._lock:
            message_id = self._mail_keys.get(dedupe_key)
            return self._mail.get(message_id) if message_id else None

    def record_sms_retry(
        self,
        incident: Incident,
        step: int,
        deliveries: List[Dict[str, Any]],
        at: Optional[datetime] = None,
    ) -> Incident:
        with self._lock:
            live = self._incidents.get(incident.incident_id, incident)
            live.sms_retries.append({
                "step": step,
                "at": iso(at or utc_now()),
                "deliveries": deliveries,
            })
            return self._save_incident(live)

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

    def claim_voice_escalation(
        self,
        incident: Incident,
        redial_after_minutes: float = 0.0,
        now: Optional[datetime] = None,
    ) -> bool:
        """Take the right to place the call, atomically.

        The sweep reads `voice_escalated_at`, decides, and only later
        stamps it. Between those two the timer-driven sweep and an
        operator pressing "run sweep now" can both see None and both dial
        — one incident, two 3am phone calls to the same person, from a
        single process. Claiming the stamp under the lock closes the
        window; the loser gets False and stands down.

        `redial_after_minutes` is how an operator legitimately calls
        again when nobody picked up. Zero means never call twice, which
        is what the unattended sweep wants: automation should not decide
        on its own to keep ringing someone. A positive value lets a
        person re-dial once that many minutes have passed, and still
        collapses a double-click — or twelve simultaneous clicks — into
        exactly one call.
        """
        with self._lock:
            stamped = incident.voice_escalated_at
            if stamped is not None:
                if redial_after_minutes <= 0:
                    return False
                waited = ((now or utc_now()) - stamped).total_seconds() / 60.0
                if waited < redial_after_minutes:
                    return False
            incident.voice_escalated_at = now or utc_now()
            self._save_incident(incident)
            return True

    def record_voice_escalation(
        self,
        incident: Incident,
        script: str,
        source: str,
        delivery: Dict[str, Any],
        fanout: Optional[List[Dict[str, Any]]] = None,
    ) -> Incident:
        with self._lock:
            # Already stamped by claim_voice_escalation on the path that
            # uses it; set here too so the manual escalate endpoint, which
            # has its own conflict checks, still records a time.
            incident.voice_escalated_at = incident.voice_escalated_at or utc_now()
            incident.voice_script = script
            incident.voice_dispatch_source = source
            incident.voice_delivery = delivery
            incident.voice_fanout = fanout if fanout is not None else [delivery]
            return self._save_incident(incident)

    def acknowledge_incident(
        self, incident: Incident, actor: str
    ) -> "tuple[Incident, bool]":
        """Record the first acknowledgement; later ones are a no-op.

        Returns (incident, claimed). The store has always kept the first
        actor and quietly ignored the rest, which is right — but it did
        not say so, and the caller could not tell. So every one of twelve
        people who reacted to the same alert got a 200 reading
        "Escalation ladder halted", a Slack message went out naming each
        of them as the person who had it, and the audit trail — the one
        that ends up in a claim packet — recorded twelve different first
        responders for a single incident.
        """
        with self._lock:
            if incident.acknowledged_at is not None:
                return incident, False
            incident.acknowledged_at = utc_now()
            incident.acknowledged_by = actor
            self._save_incident(incident)
            return incident, True

    EVIDENCE_HOURS_EACH_SIDE = 12.0

    @staticmethod
    def _evidence_row(reading: Reading) -> Dict[str, Any]:
        return {
            "reading_id": reading.reading_id,
            "sensor_id": reading.sensor_id,
            "temperature_fahrenheit": reading.temperature_fahrenheit,
            "humidity_percent": reading.humidity_percent,
            "breached": reading.breached,
            "recorded_at": iso(reading.recorded_at),
        }

    def _keep_as_evidence_locked(self, reading: Reading, tenant_id: str) -> None:
        """Copy a reading about to roll off onto any incident it belongs to.

        The evidence was copied when an incident opened and when it closed,
        and nothing in between. A breach that outlasted the buffer lost its
        middle -- the peak, usually, and the first thing an adjuster asks
        about -- and so did the recovery after it closed, which had not
        happened yet when the closing copy was taken. The moment a reading
        is about to be deleted is the one moment that catches all of it.
        """
        if reading.recorded_at is None:
            return
        at = reading.recorded_at
        margin = timedelta(hours=self.EVIDENCE_HOURS_EACH_SIDE)
        for incident in self._incidents.values():
            if incident.sensor_id != reading.sensor_id or incident.tenant_id != tenant_id:
                continue
            closed = incident.resolved_at or incident.acknowledged_at
            # Every excursion while it ran, and everything either side of
            # its opening and its close. Not every in-band reading of an
            # incident somebody forgot to close for a month: that would be
            # a month of readings rewritten onto one row every few minutes.
            during = incident.opened_at <= at <= (closed or utc_now())
            near_open = abs(at - incident.opened_at) <= margin
            near_close = closed is not None and closed <= at <= closed + margin
            if not (near_open or near_close or (during and reading.breached)):
                continue
            if any(r.get("reading_id") == reading.reading_id
                   for r in incident.evidence_readings):
                continue
            incident.evidence_readings = sorted(
                incident.evidence_readings + [self._evidence_row(reading)],
                key=lambda r: (r["recorded_at"], r["reading_id"]),
            )
            self._save_incident(incident)

    def preserve_evidence(
        self, incident: Incident, before_hours: float = 12.0,
        after_hours: float = 12.0,
    ) -> Incident:
        """Copy the readings around this incident onto the incident itself.

        Called when it opens and again when it resolves, so the lead-up
        and the recovery are both kept. Merged by reading id, because the
        two calls overlap and an adjuster counting the same reading twice
        is its own problem.

        Bounded by the same window the claim packet uses, so this stores
        what the document will show and not the whole history: a handful
        of kilobytes on an object that already exists, rather than
        turning off the ring buffer and holding every reading for every
        sensor forever.
        """
        with self._lock:
            live = self._incidents.get(incident.incident_id, incident)
            start = live.opened_at - timedelta(hours=before_hours)
            end = (
                live.resolved_at or live.acknowledged_at or utc_now()
            ) + timedelta(hours=after_hours)

            kept = {r.get("reading_id"): r for r in live.evidence_readings}
            for reading in self._readings.get(live.sensor_id, ()):
                if reading.recorded_at is None:
                    continue
                if start <= reading.recorded_at <= end:
                    kept[reading.reading_id] = self._evidence_row(reading)

            live.evidence_readings = sorted(
                kept.values(), key=lambda r: (r["recorded_at"], r["reading_id"])
            )
            self._save_incident(live)
            incident.evidence_readings = list(live.evidence_readings)
            return live

    def record_corrective_action(
        self, incident: Incident, actor: str, action: str,
        disposition: str = "",
    ) -> Incident:
        """What was done about the excursion, and by whom.

        HACCP principle 4 makes this the record, not the temperature: an
        inspector reading a log is checking that somebody wrote down what
        happened to the food. A cold fridge with no entry is the line
        that gets written up.

        Kept append-only in spirit -- a second call replaces the text but
        the audit trail holds both, because a corrective action edited
        after an inspection is worth nothing.
        """
        with self._lock:
            now = utc_now()
            incident.corrective_action = action.strip()
            incident.corrective_action_by = actor
            incident.corrective_action_at = now
            if disposition.strip():
                incident.product_disposition = disposition.strip()
            self._save_incident(incident)
        return incident

    def review_incident(self, incident: Incident, actor: str) -> Incident:
        """A manager's sign-off, which principle 7 asks for separately.

        Separate from the corrective action on purpose: the person who
        moved the product and the person who signs that it was handled
        are usually not the same, and a log where they always are is one
        an inspector reads twice.
        """
        with self._lock:
            incident.reviewed_by = actor
            incident.reviewed_at = utc_now()
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
                # The recovery is evidence too: an adjuster asks how long
                # it was out of band and when it came back.
                self.preserve_evidence(incident)
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

    def latest_unresolved_incident(
        self, sensor_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Incident]:
        """The incident a fresh breach on this sensor belongs to.

        `unresolved`, not `open`. One fault is one incident until somebody
        closes it out, whether or not it has been acknowledged — otherwise
        acknowledging an alert makes the product louder rather than
        quieter, which is the opposite of what the button says.
        """
        with self._lock:
            # And the same business: sensor ids are free again once retired,
            # so the id alone can name another company's old incident.
            candidates = [
                i
                for i in self._incidents.values()
                if i.sensor_id == sensor_id and i.unresolved
                and (tenant_id is None or i.tenant_id == tenant_id)
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
        password_from_owner: bool = False,
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
                password_from_owner=password_from_owner,
            )
            self._users[user.user_id] = user
            self._emails[user.email] = user.user_id
            self._db.put("user", user.user_id, user.to_row())
            return user

    def get_user(self, user_id: str) -> Optional[User]:
        with self._lock:
            return self._users.get(user_id)

    # ---- one person, several businesses ---------------------------------

    def link_user(self, person: User, tenant_id: str, role: str) -> User:
        """Give a person a place in another business, under their own login.

        The new record has the person's name and email for the team list,
        but no password of its own and no entry in the email index: the
        email still signs in to the person's first login and nothing else.
        """
        with self._lock:
            person = self.person_of(person)
            user = User(
                user_id=self._next_id("USR"),
                tenant_id=tenant_id,
                email=person.email,
                full_name=person.full_name,
                role=role,
                # Never matches anything: this record is not a sign-in.
                password_hash=hash_password(secrets.token_urlsafe(32)),
                created_at=utc_now(),
                linked_to=person.user_id,
            )
            self._users[user.user_id] = user
            self._db.put("user", user.user_id, user.to_row())
            return user

    def usage_counts(self, days: int = 30) -> Dict[str, int]:
        """People with a sign-in, and how many signed in lately. Counts only."""
        cutoff = utc_now() - timedelta(days=days)
        with self._lock:
            people = [u for u in self._users.values() if u.linked_to is None]
            return {
                "users": len(people),
                "active_users": sum(1 for u in people if u.last_login_at and u.last_login_at >= cutoff),
            }

    def person_of(self, user: User) -> User:
        """The sign-in behind a user record: itself, or the one it links to."""
        with self._lock:
            if user.linked_to is None:
                return user
            return self._users.get(user.linked_to) or user

    def person_active(self, user: User) -> bool:
        """False when this person has been removed from this business.

        Only this record decides. The sign-in record also stands for its
        person's place in whichever business they joined first, so treating
        "removed from that business" as "this person is switched off" let one
        business's owner lock somebody out of every other business they had.
        """
        return not user.disabled and self._users.get(self.person_of(user).user_id) is not None

    def open_logins_of(self, user: User) -> List[User]:
        """The person's records that have not been removed, first login first."""
        return [r for r in self.logins_of(user) if not r.disabled]

    def logins_of(self, user: User) -> List[User]:
        """Every business record of the person behind `user`, first login first."""
        with self._lock:
            person = self.person_of(user)
            linked = sorted(
                (u for u in self._users.values() if u.linked_to == person.user_id),
                key=lambda u: u.created_at,
            )
            return [person] + linked

    def login_in(self, user: User, tenant_id: str) -> Optional[User]:
        """This person's record in one particular business, if they have one."""
        for record in self.logins_of(user):
            if record.tenant_id == tenant_id:
                return record
        return None

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
                # Revoke live tokens so a removed person loses access to this
                # business now, not whenever the session happens to expire.
                # Only this business: their places elsewhere are not this
                # business's to close.
                closing = {user.user_id}
                for token in [
                    t for t, s in self._sessions.items() if s.user_id in closing
                ]:
                    self.revoke_session(token)
            return user

    def set_user_role(self, user: User, role: str) -> User:
        with self._lock:
            user.role = role
            self._db.put("user", user.user_id, user.to_row())
            return user

    def set_user_password(self, user: User, password: str, from_owner: bool = False) -> User:
        """Set a sign-in's password. Every reset still outstanding for the
        person stops working: it was issued for the password just replaced."""
        with self._lock:
            user.password_hash = hash_password(password)
            user.password_from_owner = from_owner
            self._db.put("user", user.user_id, user.to_row())
            self._cancel_resets({r.user_id for r in self.logins_of(user)})
            return user

    def _cancel_resets(self, user_ids) -> None:
        for digest in [d for d, r in self._resets.items() if r.user_id in user_ids]:
            self._resets.pop(digest, None)
            self._db.delete("reset", digest)

    def revoke_sessions_of(self, user: User, keep: Optional[str] = None) -> int:
        """Sign the person out everywhere, in every business, except `keep`."""
        with self._lock:
            records = {r.user_id for r in self.logins_of(user)}
            doomed = [t for t, sess in self._sessions.items() if sess.user_id in records and t != keep]
            for tok in doomed:
                self.revoke_session(tok)
            return len(doomed)

    def start_session(self, user: User, not_after: Optional[datetime] = None) -> LoginSession:
        """A new session. `not_after` caps it, for a switch between businesses."""
        with self._lock:
            now = utc_now()
            expires = now + timedelta(hours=SESSION_TTL_HOURS)
            if not_after is not None:
                expires = min(expires, not_after)
            secret = f"cls_{secrets.token_urlsafe(32)}"
            session = LoginSession(
                token=hash_secret(secret),
                user_id=user.user_id,
                tenant_id=user.tenant_id,
                issued_at=now,
                expires_at=expires,
                secret=secret,
            )
            self._sessions[session.token] = session
            user.last_login_at = now
            self._db.put("session", session.token, session.to_row())
            self._db.put("user", user.user_id, user.to_row())
            return session

    def session_by_token(self, token: str) -> Optional[LoginSession]:
        """The session for a bearer token, as the caller sent it."""
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(hash_secret(token))
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
            text = self._ai_cache.get(key)
            if text is not None:
                # Touch it, so eviction drops what nobody asks for rather
                # than whatever happened to be written first. Dicts keep
                # insertion order, so moving to the end is the whole of
                # the LRU.
                self._ai_cache.move_to_end(key)
            return text

    def cache_put(self, key: str, text: str) -> None:
        if len(text) > MAX_CACHE_TEXT_CHARS:
            return
        with self._lock:
            self._ai_cache[key] = text
            self._ai_cache.move_to_end(key)
            self._db.put("aicache", key, {"key": key, "text": text})
            self._evict_cache_locked()

    def _evict_cache_locked(self) -> None:
        """Keep the cache to a size, in memory and on disk.

        This was the only collection in the codebase without a bound --
        readings cap at 500, BYOD samples at 25, faults at 200 -- and it
        is the one that grows fastest, because every distinct prompt is
        a new key. Unbounded it fills memory and the database with the
        answers to questions nobody will ask again.

        Evicted from both at once. Dropping it from memory alone leaves
        the row on disk to be loaded back at the next restart, which is
        not eviction, it is a leak with extra steps.
        """
        overflow = len(self._ai_cache) - MAX_CACHE_ENTRIES
        if overflow <= 0:
            return
        # islice, not `next(iter(...))` in a loop: iter() restarts every
        # time, so that took the same first key `overflow` times and
        # removed exactly one entry. It passed every test that wrote one
        # at a time -- overflow is 1 there, and one key is the right
        # answer -- and failed only on the bulk path, which is a restart
        # loading a database bigger than the cap.
        stale = list(islice(self._ai_cache, overflow))
        for key in stale:
            self._ai_cache.pop(key, None)
        self._db.delete_many("aicache", stale)

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
        """Delete a site, releasing everything attached to it.

        Sensors were already released. Contacts were not, and that was
        worse than it looks: `_roster_for_site` matches a contact either
        to a live site or to the estate-wide pool of contacts whose
        site_id is None. A contact left pointing at a deleted site matches
        neither, so they are silently dropped from every alert while still
        appearing on the roster in the console. The operator sees "Night
        Manager, on call" and Night Manager is never texted again.

        Releasing them to estate-wide is the safe direction: somebody
        whose site disappeared should receive more alerts than they
        expected, never fewer.
        """
        with self._lock:
            if site_id not in self._sites:
                return False
            for sensor in list(self._sensors.values()):
                if sensor.site_id == site_id:
                    sensor.site_id = None
                    self._db.put("sensor", sensor.sensor_id, sensor.to_row())
            for contact in list(self._contacts.values()):
                if contact.site_id == site_id:
                    contact.site_id = None
                    self._db.put("contact", contact.contact_id, contact.to_row())
            for hook in list(self._webhooks.values()):
                if hook.site_id == site_id:
                    hook.site_id = None
                    self._db.put("webhook", hook.webhook_id, hook.to_row())
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

    def claim_quiet_alert(self, sensor: Sensor) -> bool:
        """Take the right to text about this sensor's current silence.

        Under the lock, so the timed sweep and somebody pressing the sweep
        button at the same moment send one text between them, not two.
        """
        with self._lock:
            live = self._sensors.get(sensor.sensor_id)
            if live is None or live.last_seen is None:
                return False
            silence = iso(live.last_seen)
            if live.quiet_alert_for == silence:
                return False
            live.quiet_alert_for = silence
            self._db.put("sensor", live.sensor_id, live.to_row())
            return True

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
        import oncall

        with self._lock:
            if contact_id not in self._contacts:
                return False
            tenant_id = self._contacts[contact_id].tenant_id
            del self._contacts[contact_id]
            self._db.delete("contact", contact_id)
            # Out of any rotation too. A turn held by somebody who is no
            # longer on the roster is a shift with nobody on it.
            for rotation in oncall.rotations_for(self._db, tenant_id):
                if contact_id in rotation.members:
                    rotation.members = [m for m in rotation.members if m != contact_id]
                    self._db.put(oncall.ROTATION_KIND, rotation.rec_id, rotation.to_row())
            return True

    # ---- vault anchors --------------------------------------------------

    def anchor_chain(
        self,
        tenant_id: str,
        sensor_id: str,
        chain_head: str,
        readings: int,
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
        first_reading_id: Optional[str] = None,
        last_reading_id: Optional[str] = None,
    ) -> VaultAnchor:
        """Record the chain head handed out in an attestation."""
        with self._lock:
            recent = [a for a in self.anchors_for_sensor(sensor_id)
                      if a.issued_at and utc_now() - a.issued_at < timedelta(days=1)]
            # The same head over the same run is already on record: asking
            # again should not write a second row.
            for existing in recent:
                if (existing.tenant_id, existing.chain_head, existing.readings,
                        existing.period_start, existing.period_end) == (
                        tenant_id, chain_head, readings, period_start, period_end):
                    return existing
            # A ceiling per sensor per day. Every head is checked by /verify,
            # so a flood of new ones costs work, and nobody needs more than
            # one an hour or so. Past it, the newest stands for this one.
            if len(recent) >= MAX_ANCHORS_PER_SENSOR_PER_DAY:
                return recent[0]
            anchor = VaultAnchor(
                first_reading_id=first_reading_id,
                last_reading_id=last_reading_id,
                anchor_id=self._next_id("ANC"),
                tenant_id=tenant_id,
                sensor_id=sensor_id,
                chain_head=chain_head,
                readings=readings,
                period_start=period_start,
                period_end=period_end,
                issued_at=utc_now(),
            )
            self._anchors[anchor.anchor_id] = anchor
            self._db.put("anchor", anchor.anchor_id, anchor.to_row())
            return anchor

    def anchors_for_sensor(self, sensor_id: str) -> List[VaultAnchor]:
        """Every head ever issued for a sensor, newest first."""
        with self._lock:
            rows = [a for a in self._anchors.values() if a.sensor_id == sensor_id]
        return sorted(rows, key=lambda a: a.anchor_id, reverse=True)

    # ---- invoices -----------------------------------------------------------

    def _next_invoice_number(self, year: int) -> str:
        """Sequential and gapless within a year.

        Derived from what has already been issued rather than a counter,
        so a restart cannot reissue a number — a duplicate invoice number
        is the kind of thing that fails an audit.
        """
        prefix = f"CLX-{year}-"
        issued = [
            int(inv.number[len(prefix):])
            for inv in self._invoices.values()
            if inv.number.startswith(prefix)
            and inv.number[len(prefix):].isdigit()
        ]
        # The highest number ever issued, kept apart from the invoices so a
        # restore from an older backup cannot hand a number out twice.
        floor = self.invoice_number_floors().get(str(year), 0)
        number = max(max(issued, default=0), floor) + 1
        self.raise_invoice_number_floors({str(year): number})
        return f"{prefix}{number:04d}"

    def invoice_number_floors(self) -> Dict[str, int]:
        """The highest invoice number issued, per year."""
        row = self._db.get("meta", "invoice_numbers") or {}
        return {k: int(v) for k, v in row.items() if str(v).isdigit()}

    def raise_invoice_number_floors(self, floors: Dict[str, int]) -> None:
        """Never issue an invoice number at or below these again."""
        with self._lock:
            current = self.invoice_number_floors()
            changed = False
            for year, value in floors.items():
                if int(value) > current.get(year, 0):
                    current[year] = int(value)
                    changed = True
            if changed:
                self._db.put("meta", "invoice_numbers", current)

    def create_invoice(
        self,
        tenant: Tenant,
        lines: List[Dict[str, Any]],
        period_days: int = 30,
        terms_days: int = 30,
        purchase_order: Optional[str] = None,
        source: str = "manual",
        billing_period: Optional[int] = None,
    ) -> Invoice:
        with self._lock:
            now = utc_now()
            subtotal = round(sum(line["amount_usd"] for line in lines), 2)
            invoice = Invoice(
                invoice_id=self._next_id("INV"),
                tenant_id=tenant.tenant_id,
                number=self._next_invoice_number(now.year),
                company_name=tenant.company_name,
                # Copied, not referenced: the caller must not be able to
                # mutate a line after the invoice is issued.
                lines=[dict(line) for line in lines],
                subtotal_usd=subtotal,
                total_usd=subtotal,
                period_days=period_days,
                terms_days=terms_days,
                purchase_order=purchase_order,
                issued_at=now,
                due_at=now + timedelta(days=terms_days),
                source=source,
                billing_period=billing_period,
            )
            self._invoices[invoice.invoice_id] = invoice
            self._db.put("invoice", invoice.invoice_id, invoice.to_row())
            return invoice

    def get_invoice(self, invoice_id: str) -> Optional[Invoice]:
        with self._lock:
            return self._invoices.get(invoice_id)

    def invoices_for(self, tenant_id: str) -> List[Invoice]:
        with self._lock:
            rows = [i for i in self._invoices.values() if i.tenant_id == tenant_id]
        return sorted(rows, key=lambda i: i.number, reverse=True)

    def all_invoices(self) -> List[Invoice]:
        """Every invoice on the books, for the fleet-wide passes."""
        with self._lock:
            return list(self._invoices.values())

    def claim_reminder(self, invoice: Invoice, stage: int) -> bool:
        """Reserve the right to send reminder `stage` for this invoice.

        Same shape as the billing claim, for the same reason: two dunning
        passes overlapping must not chase the customer twice.

        The stage may jump. An invoice that went ninety days without a pass
        — the process was off, the deployment was down — has earned the
        notice that fits ninety days, not a march up through the four
        milder ones it slept through. Anything at or below the stage
        already sent is refused, so the sequence only ever moves forward.
        """
        with self._lock:
            live = self._invoices.get(invoice.invoice_id)
            if live is None or not live.open:
                return False
            if stage <= live.reminders_sent:
                return False
            live.reminders_sent = stage
            live.last_reminder_at = utc_now()
            self._db.put("invoice", live.invoice_id, live.to_row())
            invoice.reminders_sent = live.reminders_sent
            invoice.last_reminder_at = live.last_reminder_at
            return True

    def claim_late_fee(self, invoice: Invoice, fee_invoice_id: str) -> bool:
        """Attach a late-fee invoice, once and only once."""
        with self._lock:
            live = self._invoices.get(invoice.invoice_id)
            if live is None or live.late_fee_invoice_id is not None:
                return False
            live.late_fee_invoice_id = fee_invoice_id
            self._db.put("invoice", live.invoice_id, live.to_row())
            invoice.late_fee_invoice_id = fee_invoice_id
            return True

    def open_invoices(self, tenant_id: Optional[str] = None) -> List[Invoice]:
        """Every invoice still owed something, oldest first."""
        with self._lock:
            rows = [
                i
                for i in self._invoices.values()
                if i.open and (tenant_id is None or i.tenant_id == tenant_id)
            ]
        return sorted(rows, key=lambda i: i.issued_at or utc_now())

    def settle_invoice(
        self, invoice: Invoice, reference: str, amount_usd: Optional[float] = None
    ) -> "tuple[Invoice, bool]":
        """Record a payment, in full or in part. Returns (invoice, applied).

        A part payment must not close the invoice. The comment here used
        to say exactly that while the code did the opposite: state went to
        "paid" regardless, the shortfall was written into a prose string,
        and the invoice dropped out of `outstanding_usd` and
        `overdue_count`. Paying $1 against $48,000 stopped the remaining
        $47,999 from ever being chased, and the only record of it was
        English inside a reference field.

        **The reference is the payment's identity, and the same one is
        never applied twice.** Every payment processor delivers webhooks
        at least once — a retry is ordinary, not exceptional — and this
        endpoint is where a Stripe adapter lands. Measured before the
        fix: the same $100 event delivered three times recorded $300, so
        an invoice went to "paid" on a third of the money and dropped
        out of collections for good. That is the same hole as a customer
        writing off their own invoice, dug from our side.

        `applied` is False for a repeat. The caller answers 200 anyway,
        because a webhook told it did not work retries, and retrying is
        the thing that caused this.
        """
        with self._lock:
            live = self._invoices.get(invoice.invoice_id, invoice)
            reference = (reference or "").strip()
            if any(p.get("reference") == reference for p in live.payments):
                logger.info(
                    "Payment %s against %s was already recorded; ignoring the "
                    "repeat.", reference, live.number,
                )
                _mirror_payment(invoice, live)
                return live, False

            # No amount means "the rest has arrived": what is still owed,
            # not the face value again, which after a part payment recorded
            # more money than was ever received.
            paid = live.balance_usd if amount_usd is None else round(amount_usd, 2)
            now = utc_now()
            payments = list(live.payments) + [{
                "reference": reference,
                "amount_usd": paid,
                "at": iso(now),
            }]
            total_paid = round(live.amount_paid_usd + paid, 2)
            # A hair's rounding either way still settles it; a real
            # shortfall leaves it open and chaseable.
            state = "paid" if total_paid + 0.005 >= live.total_usd else "part_paid"

            # Persisted before it is committed to the live object, for the
            # same reason as the sensor overrides above: a write the
            # database refuses must not leave the ledger holding a figure
            # that cannot be stored. Infinity through `amount_usd` did
            # exactly that, and every read of the invoice — and of the
            # customer's whole invoice list — answered 500 afterwards.
            candidate = replace(
                live,
                payments=payments,
                amount_paid_usd=total_paid,
                payment_reference=reference,
                paid_at=now,
                state=state,
            )
            self._db.put("invoice", candidate.invoice_id, candidate.to_row())

            was_paid = live.state == "paid"
            live.payments = payments
            live.amount_paid_usd = total_paid
            live.payment_reference = reference
            live.paid_at = now
            live.state = state
            _mirror_payment(invoice, live)
            # The licence runs on from money that has arrived: an invoice paid
            # in full buys its period. Nothing else lengthens a paid term,
            # and a late charge buys nothing: it carries a period like any
            # invoice, so paying a $25 fee used to buy thirty days.
            buys_service = not (live.source or "").startswith("late_fee:")
            if state == "paid" and not was_paid and buys_service:
                self.extend_licence(live.tenant_id, live.period_days)
            return live, True

    def void_invoice(self, invoice: Invoice) -> Invoice:
        """Void an invoice, and hand its billing period back to be re-issued.

        Voiding used to be the end of the story, and that quietly gave the
        month away. `periods_billed` is a high-water mark, so once period 4
        had been billed the run moved to period 5 whether or not the
        invoice for 4 still existed. A single void — a typo in the PO, a
        disputed line, a wrong address — meant that month's service was
        delivered and could never be charged for again.

        The period goes on a re-bill list rather than rewinding the
        counter, because the voided invoice is not always the newest one:
        rewinding would re-issue every period after it as well.
        """
        with self._lock:
            # Voiding twice must not queue the period twice: after the
            # first re-bill clears it, a second void would put it back and
            # the same month would be invoiced again.
            if invoice.state == "void":
                return invoice
            invoice.state = "void"
            invoice.voided_at = utc_now()
            self._db.put("invoice", invoice.invoice_id, invoice.to_row())

            if invoice.billing_period is not None:
                sub = self._subscriptions.get(invoice.source)
                if sub is not None and invoice.billing_period not in sub.rebill_periods:
                    sub.rebill_periods.append(invoice.billing_period)
                    self._db.put("subscription", sub.subscription_id, sub.to_row())
                    logger.info(
                        "Voided %s; subscription %s will re-bill period %d.",
                        invoice.number, sub.subscription_id, invoice.billing_period,
                    )
            return invoice

    def clear_rebill(self, sub: "Subscription", index: int) -> None:
        """Take a period off the re-bill list once it has been re-issued."""
        with self._lock:
            live = self._subscriptions.get(sub.subscription_id)
            if live is None or index not in live.rebill_periods:
                return
            live.rebill_periods.remove(index)
            self._db.put("subscription", live.subscription_id, live.to_row())
            if index in sub.rebill_periods:
                sub.rebill_periods.remove(index)

    # ---- partners ---------------------------------------------------------

    def create_partner(
        self,
        company_name: str,
        contact_name: str,
        contact_email: str,
        commission_percent: float = DEFAULT_PARTNER_COMMISSION_PERCENT,
    ) -> Partner:
        with self._lock:
            partner = Partner(
                partner_id=self._next_id("PTR"),
                company_name=company_name,
                contact_name=contact_name,
                contact_email=contact_email,
                commission_percent=commission_percent,
                created_at=utc_now(),
            )
            partner.set_key(f"clx_ptr_{secrets.token_urlsafe(30)}")
            self._partners[partner.partner_id] = partner
            self._partner_keys[partner.key_hash] = partner.partner_id
            self._db.put("partner", partner.partner_id, partner.to_row())
            return partner

    def get_partner(self, partner_id: str) -> Optional[Partner]:
        with self._lock:
            return self._partners.get(partner_id)

    def partner_by_key(self, api_key: str) -> Optional[Partner]:
        if not api_key:
            return None
        with self._lock:
            partner_id = self._partner_keys.get(hash_secret(api_key))
            return self._partners.get(partner_id) if partner_id else None

    def rotate_partner_key(self, partner: Partner) -> str:
        """A new key for a partner. The old one stops working at once."""
        with self._lock:
            self._partner_keys.pop(partner.key_hash, None)
            partner.set_key(f"clx_ptr_{secrets.token_urlsafe(30)}")
            self._partner_keys[partner.key_hash] = partner.partner_id
            self._db.put("partner", partner.partner_id, partner.to_row())
            return partner.api_key

    def list_partners(self) -> List[Partner]:
        with self._lock:
            return sorted(self._partners.values(), key=lambda p: p.partner_id)

    def save_partner(self, partner: Partner) -> Partner:
        with self._lock:
            self._db.put("partner", partner.partner_id, partner.to_row())
            return partner

    def tenants_for_partner(self, partner_id: str) -> List[Tenant]:
        with self._lock:
            rows = [
                t for t in self._tenants.values() if t.partner_id == partner_id
            ]
        return sorted(rows, key=lambda t: t.company_name)

    def assign_partner(self, tenant: Tenant, partner_id: Optional[str]) -> Tenant:
        with self._lock:
            tenant.partner_id = partner_id
            self._db.put("tenant", tenant.tenant_id, tenant.to_row())
            return tenant

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
        self, tenant: Tenant, site_id: Optional[str], channel: str,
        now: Optional[datetime] = None,
    ) -> List[Contact]:
        """Contacts covering a site: its own, else the tenant-wide ones.

        A manager in Boca Raton should not be woken for a Boynton Beach
        freezer, so a site's own contacts take precedence when it has any.
        Somebody in a rotation counts only on their shift (oncall.py), so
        a site whose person on shift is off falls back to the estate.
        """
        import oncall

        everyone = oncall.on_duty(
            [c for c in self.contacts_for(tenant.tenant_id)
             if c.active and getattr(c, f"receives_{channel}")],
            oncall.rotations_for(self._db, tenant.tenant_id),
            now or utc_now(),
        )
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
        import oncall

        now = utc_now()
        roster = self._roster_for_site(tenant, site_id, "sms", now)
        if roster:
            # Quiet hours narrow the text, never to nobody: with everyone
            # asleep, everyone is texted.
            awake, asleep = oncall.awake_first(roster, now)
            return awake or asleep
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
        """Who to phone, in the order to try them.

        Anybody in quiet hours is moved to the end rather than left off:
        an alert nobody awake answers still works its way to them.
        """
        import oncall

        now = utc_now()
        roster = self._roster_for_site(tenant, site_id, "voice", now)
        if roster:
            awake, asleep = oncall.awake_first(roster, now)
            return awake + asleep
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
        """Retune one sensor's limits, or restore the sector defaults.

        Written to disk before it is committed to the live object. The
        order matters more than it looks: this used to mutate first, and
        a value the database refused — infinity arrived through the API
        as `1e400` — left the sensor poisoned in memory while disk stayed
        clean. Every subsequent read of that sensor answered 500, and so
        did `/api/sensor-pulse` for it, so the device kept reporting into
        nothing and the freezer was silently unmonitored until somebody
        restarted the process.

        The boundary now refuses non-finite numbers, which is the real
        fix. This is the second lock on the same door: whatever the
        reason a write fails — a full disk, a value nobody anticipated —
        the working set must not be left holding something that cannot be
        persisted.
        """
        with self._lock:
            candidate = replace(sensor, override_above=above, override_below=below)
            self._db.put("sensor", candidate.sensor_id, candidate.to_row())
            sensor.override_above = above
            sensor.override_below = below
            return sensor

    # ---- password resets -------------------------------------------------

    def issue_join_code(self, owner: User, role: str, ttl_hours: int = 72) -> str:
        """A one-time code to join this owner's business, returned once."""
        with self._lock:
            token = f"clj_{secrets.token_urlsafe(24)}"
            now = utc_now()
            code = JoinCode(
                token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
                tenant_id=owner.tenant_id,
                role=role,
                issued_by=owner.user_id,
                issued_at=now,
                expires_at=now + timedelta(hours=ttl_hours),
            )
            self._join_codes[code.token_hash] = code
            self._db.put("join_code", code.token_hash, code.to_row())
            return token

    def peek_join_code(self, token: str) -> Optional[JoinCode]:
        """The join code this token names, if usable, without spending it."""
        with self._lock:
            digest = hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()
            code = self._join_codes.get(digest)
            return None if code is None or code.spent else code

    def redeem_join_code(self, token: str) -> Optional[JoinCode]:
        """Spend a join code. None if it is unknown, used or expired."""
        with self._lock:
            digest = hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()
            code = self._join_codes.get(digest)
            if code is None or code.spent:
                return None
            code.used_at = utc_now()
            self._join_codes.pop(digest, None)
            self._db.delete("join_code", digest)
            return code

    def join_codes_for(self, tenant_id: str) -> List[JoinCode]:
        """The codes for this business still waiting to be used, newest first."""
        with self._lock:
            live = [c for c in self._join_codes.values()
                    if c.tenant_id == tenant_id and not c.spent]
            return sorted(live, key=lambda c: c.issued_at, reverse=True)

    def revoke_join_code(self, tenant_id: str, code_id: str) -> Optional[JoinCode]:
        """Withdraw one unused code of this business. None if there is none.

        Matched on the business as well as the id, so an owner of one
        business cannot withdraw -- or learn anything about -- another's.
        """
        with self._lock:
            for digest, code in list(self._join_codes.items()):
                if (code.tenant_id == tenant_id and code.code_id == code_id
                        and not code.spent):
                    self._join_codes.pop(digest, None)
                    self._db.delete("join_code", digest)
                    return code
            return None

    def issue_reset(self, user: User, ttl_hours: int = 24, by_owner: bool = False) -> str:
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
                by_owner=by_owner,
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
            if user is None or not self.open_logins_of(user):
                return None
            if reset.by_owner and (
                user.linked_to is not None
                or user.tenant_id != reset.tenant_id
                or len(self.logins_of(user)) > 1
            ):
                # The owner was allowed to issue this because the sign-in
                # belonged to their business alone. Since then the person has
                # joined or started another, so the password is no longer
                # that owner's to set: the same check as at issue, made again
                # at the moment it matters.
                self._resets.pop(digest, None)
                self._db.delete("reset", digest)
                return None

            # Whoever holds an owner-issued token may be the owner, so the
            # password it sets counts as one the owner knows.
            reset.used_at = utc_now()
            self.set_user_password(user, new_password, from_owner=reset.by_owner)

            # A reset means the old sessions should not outlive it, in any of
            # the person's businesses: a stolen session in one used to stay
            # live, and could switch into the others.
            self.revoke_sessions_of(user)
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

    # ---- signed subscriptions -----------------------------------------

    def create_subscription(
        self,
        *,
        tenant: Tenant,
        term_years: int,
        escalator_percent: float,
        annual_prepay: bool,
        add_ons: List[str],
        purchase_order: Optional[str],
        auto_renew: bool,
        signed_by: str,
        started_at: Optional[datetime] = None,
        carried_multiplier: float = 1.0,
    ) -> Subscription:
        with self._lock:
            sub = Subscription(
                subscription_id=self._next_id("SUB"),
                tenant_id=tenant.tenant_id,
                term_years=max(1, min(int(term_years), MAX_TERM_YEARS)),
                escalator_percent=round(float(escalator_percent), 4),
                annual_prepay=bool(annual_prepay),
                add_ons=sorted(set(add_ons)),
                purchase_order=purchase_order,
                auto_renew=bool(auto_renew),
                signed_by=signed_by,
                started_at=started_at or utc_now(),
                carried_multiplier=round(float(carried_multiplier), 6),
            )
            self._subscriptions[sub.subscription_id] = sub
            self._db.put("subscription", sub.subscription_id, sub.to_row())
            return sub

    def get_subscription(self, subscription_id: str) -> Optional[Subscription]:
        with self._lock:
            return self._subscriptions.get(subscription_id)

    def subscriptions_for(self, tenant_id: str) -> List[Subscription]:
        with self._lock:
            rows = [
                s for s in self._subscriptions.values() if s.tenant_id == tenant_id
            ]
        return sorted(rows, key=lambda s: s.started_at or utc_now(), reverse=True)

    def active_subscription(self, tenant_id: str) -> Optional[Subscription]:
        """The one contract that bills this tenant, if there is one."""
        for sub in self.subscriptions_for(tenant_id):
            if sub.active:
                return sub
        return None

    def all_subscriptions(self) -> List[Subscription]:
        with self._lock:
            return list(self._subscriptions.values())

    def save_subscription(self, sub: Subscription) -> Subscription:
        with self._lock:
            self._db.put("subscription", sub.subscription_id, sub.to_row())
            return sub

    def claim_billing_period(self, sub: Subscription, index: int) -> bool:
        """Reserve period `index` for invoicing. False means somebody else has it.

        Billing is the one place in this system where doing the work twice
        costs the customer money, so the decision to bill and the record
        that it was billed happen inside a single lock acquisition. Without
        this, two billing runs overlapping — a scheduler tick landing on a
        manual catch-up, or a restart mid-pass — each read
        `periods_billed == 4`, each decided period 4 was due, and the
        customer received two invoices for the same month.

        The counter only ever advances, so a claim for a period already
        billed is refused rather than reordered.
        """
        with self._lock:
            live = self._subscriptions.get(sub.subscription_id)
            if live is None or not live.active:
                return False
            # A period on the re-bill list is behind the counter by
            # definition — its invoice was voided — so it is claimed
            # without moving the counter at all.
            if index in live.rebill_periods:
                return True
            if index != live.periods_billed:
                return False
            live.periods_billed = index + 1
            live.last_billed_at = utc_now()
            self._db.put("subscription", live.subscription_id, live.to_row())
            # The caller holds a reference; keep it in step so it does not
            # go on to bill from a stale count.
            sub.periods_billed = live.periods_billed
            sub.last_billed_at = live.last_billed_at
            return True

    def release_billing_period(self, sub: Subscription, index: int) -> None:
        """Give a claimed period back after the invoice failed to issue.

        A claim that is never released is a month nobody is ever billed
        for — the failure mode this whole mechanism exists to avoid, just
        pointing the other way.

        A *re-bill* claim is the exception, and getting that wrong was a
        real bug. Claiming a period off the re-bill list does not move the
        counter, because the period was already billed once; so releasing
        it must not move the counter either. It did, and the counter went
        backwards: an estate whose invoice was voided and whose sensors
        were then all decommissioned came back with periods_billed=0 and
        period 0 still on the re-bill list — the same month queued twice,
        from both ends.

        Found by the fuzzer at seed 377, after twenty-one operations.
        """
        with self._lock:
            live = self._subscriptions.get(sub.subscription_id)
            if live is None:
                return
            if index in live.rebill_periods:
                # The claim never advanced anything. There is nothing to
                # hand back, and the period stays on the list for the next
                # run to try again.
                return
            if live.periods_billed != index + 1:
                return
            live.periods_billed = index
            self._db.put("subscription", live.subscription_id, live.to_row())
            sub.periods_billed = index

    def mark_setup_billed(self, sub: Subscription) -> None:
        with self._lock:
            live = self._subscriptions.get(sub.subscription_id)
            if live is not None:
                live.setup_billed = True
                self._db.put("subscription", live.subscription_id, live.to_row())
            sub.setup_billed = True

    # ---- expenses ---------------------------------------------------------

    def record_expense(
        self,
        *,
        spent_at: datetime,
        amount_usd: float,
        category: str,
        description: str,
        supplier: str = "",
        receipt: str = "",
    ) -> Expense:
        with self._lock:
            expense = Expense(
                expense_id=self._next_id("EXP"),
                spent_at=spent_at,
                amount_usd=round(amount_usd, 2),
                category=category,
                description=description,
                supplier=supplier,
                receipt=receipt,
                recorded_at=utc_now(),
            )
            self._db.put("expense", expense.expense_id, expense.to_row())
            self._expenses[expense.expense_id] = expense
            return expense

    def delete_expense(self, expense_id: str) -> bool:
        with self._lock:
            if expense_id not in self._expenses:
                return False
            self._expenses.pop(expense_id)
            self._db.delete("expense", expense_id)
            return True

    def expenses_between(
        self, start: datetime, end: datetime
    ) -> List[Expense]:
        with self._lock:
            found = [
                e for e in self._expenses.values()
                if e.spent_at is not None and start <= e.spent_at < end
            ]
        return sorted(found, key=lambda e: (e.spent_at, e.expense_id))

    # ---- outbound mail --------------------------------------------------

    def is_suppressed(self, address: str, klass: str) -> Optional[MailSuppression]:
        """The block that stops this message, if there is one.

        An unsubscribe stops commercial mail. A bounce stops everything —
        including the invoice, because an address that does not exist
        cannot receive one, and continuing to send at it is how the
        sending domain ends up on a blocklist and the mail that *does*
        matter stops arriving for every other customer too.
        """
        with self._lock:
            block = self._suppressions.get((address or "").strip().lower())
        if block is None:
            return None
        if block.blocks_everything or klass == "commercial":
            return block
        return None

    def suppress_address(
        self, address: str, reason: str, detail: str = ""
    ) -> MailSuppression:
        """Stop writing to an address. Idempotent, and it never downgrades.

        An address that bounced and then unsubscribes stays bounced: the
        stronger block wins, because the weaker one would quietly start
        letting invoices through to a mailbox that does not exist.
        """
        address = (address or "").strip().lower()
        with self._lock:
            existing = self._suppressions.get(address)
            if existing is not None and existing.blocks_everything:
                return existing
            block = MailSuppression(
                address=address,
                reason=reason,
                created_at=utc_now(),
                detail=detail,
            )
            self._suppressions[address] = block
            self._db.put("mailblock", address, block.to_row())
            return block

    def unsuppress_address(self, address: str) -> bool:
        """Let an address back in. The operator's undo, not a customer's."""
        address = (address or "").strip().lower()
        with self._lock:
            if address not in self._suppressions:
                return False
            self._suppressions.pop(address)
            self._db.delete("mailblock", address)
            return True

    def list_suppressions(self) -> List[MailSuppression]:
        with self._lock:
            return sorted(
                self._suppressions.values(),
                key=lambda b: b.created_at,
                reverse=True,
            )

    def claim_message(
        self,
        *,
        dedupe_key: str,
        to_address: str,
        subject: str,
        body: str,
        klass: str,
        tenant_id: Optional[str] = None,
        sensitive: bool = False,
    ) -> Optional[MailMessage]:
        """Reserve the right to send this exact message, once.

        Returns None if the key has been claimed before — by an earlier
        pass, by another thread, or by the run that crashed before it got
        to the mail host. The caller must treat None as "already handled",
        not as an error: the whole point is that a second billing pass
        cannot produce a second copy of the same chase.

        The claim and the row are one operation under one lock. Checking
        for the key and then writing it would leave the window that two
        hourly passes on a slow disk actually hit.
        """
        with self._lock:
            if dedupe_key in self._mail_keys:
                return None
            message = MailMessage(
                message_id=self._next_id("msg"),
                dedupe_key=dedupe_key,
                to_address=(to_address or "").strip(),
                subject=subject,
                body=body,
                klass=klass,
                created_at=utc_now(),
                tenant_id=tenant_id,
                sensitive=sensitive,
            )
            self._mail[message.message_id] = message
            self._mail_keys[dedupe_key] = message.message_id
            self._db.put("mail", message.message_id, message.to_row())
            return message

    def record_attempt(
        self, message: MailMessage, status: str, detail: str = ""
    ) -> MailMessage:
        """Write down what the mail host said.

        `queued` means try again later; the attempt count is what stops
        that being forever.
        """
        with self._lock:
            live = self._mail.get(message.message_id) or message
            live.attempts += 1
            live.status = status
            live.detail = detail
            if status == "sent":
                live.sent_at = utc_now()
            if live.sensitive and status in ("sent", "failed"):
                live.body = SENSITIVE_BODY_REMOVED
                message.body = live.body
            self._db.put("mail", live.message_id, live.to_row())
            message.attempts = live.attempts
            message.status = live.status
            message.detail = live.detail
            message.sent_at = live.sent_at
            return live

    def scrub_stale_sensitive_mail(self, now: Optional[datetime] = None) -> int:
        """Blank the body of a sensitive message that will never be sent."""
        now = now or utc_now()
        scrubbed = 0
        with self._lock:
            for m in self._mail.values():
                if m.sensitive and m.body != SENSITIVE_BODY_REMOVED and (
                    m.status != "queued" or m.attempts >= MAIL_MAX_ATTEMPTS
                    or m.age_hours(now) > MAIL_MAX_AGE_HOURS
                ):
                    m.body = SENSITIVE_BODY_REMOVED
                    self._db.put("mail", m.message_id, m.to_row())
                    scrubbed += 1
        return scrubbed

    def trim_old_mail(self, now: Optional[datetime] = None) -> int:
        """Drop the text of handled mail older than MAIL_BODY_KEEP_DAYS.

        The row stays, so its dedupe key still stops the same message
        going out twice, and the log still says it was sent.
        """
        now = now or utc_now()
        removed = MAIL_BODY_REMOVED.format(days=MAIL_BODY_KEEP_DAYS)
        trimmed = 0
        with self._lock:
            for m in self._mail.values():
                if m.body in (removed, SENSITIVE_BODY_REMOVED):
                    continue
                if m.age_hours(now) <= MAIL_BODY_KEEP_DAYS * 24:
                    continue
                # A queued message this old is never going to be tried
                # again (pending_mail stops at MAIL_MAX_AGE_HOURS).
                m.body = removed
                self._db.put("mail", m.message_id, m.to_row())
                trimmed += 1
        return trimmed

    def pending_mail(self, now: Optional[datetime] = None) -> List[MailMessage]:
        """Messages still worth another attempt, oldest first.

        Age and attempt count are both checked here rather than at the
        send site so that there is one answer to "will this ever go out",
        and the queue cannot grow a class of message that is retried
        forever.
        """
        now = now or utc_now()
        with self._lock:
            waiting = [
                m for m in self._mail.values()
                if m.status == "queued"
                and m.attempts < MAIL_MAX_ATTEMPTS
                and m.age_hours(now) <= MAIL_MAX_AGE_HOURS
                # Alert mail is the sweep's, on a minutes timetable
                # (mail.retry_alert_mail); two senders of one message
                # would be two copies.
                and not m.dedupe_key.startswith(ALERT_MAIL_KEYS)
            ]
        return sorted(waiting, key=lambda m: m.created_at)

    def queued_alert_mail(self) -> List[MailMessage]:
        """Alert mail the mail host has not yet taken, oldest first."""
        with self._lock:
            waiting = [m for m in self._mail.values()
                       if m.status == "queued" and m.dedupe_key.startswith(ALERT_MAIL_KEYS)]
        return sorted(waiting, key=lambda m: m.created_at)

    def mail_log(self, limit: int = 100) -> List[MailMessage]:
        with self._lock:
            everything = sorted(
                self._mail.values(), key=lambda m: m.created_at, reverse=True
            )
        return everything[: max(limit, 0)]

    def mail_counts(self) -> Dict[str, int]:
        with self._lock:
            counts: Dict[str, int] = {}
            for message in self._mail.values():
                counts[message.status] = counts.get(message.status, 0) + 1
            return counts


    def set_subscription_add_ons(
        self, sub: Subscription, add_ons: List[str]
    ) -> Subscription:
        """Replace the attached add-ons. The whole set, never a delta.

        A delta would let two people clicking at once land on a contract
        neither of them chose; replacing the set makes the last write the
        one that stands, which is at least a state somebody asked for.
        """
        with self._lock:
            live = self._subscriptions.get(sub.subscription_id) or sub
            live.add_ons = sorted(set(add_ons))
            self._db.put("subscription", live.subscription_id, live.to_row())
            sub.add_ons = list(live.add_ons)
            return live

    def cancel_subscription(self, sub: Subscription, reason: str) -> Subscription:
        with self._lock:
            live = self._subscriptions.get(sub.subscription_id) or sub
            live.cancelled_at = utc_now()
            live.cancellation_reason = reason
            self._db.put("subscription", live.subscription_id, live.to_row())
            return live

    def renew_subscription(self, sub: Subscription, term_years: int) -> Subscription:
        """Start a fresh term at the rate the last one ended on.

        A renewal that reset to the year-one rate would hand back every
        point of the escalator the term was signed to earn, which is the
        single most expensive thing a renewal can quietly do.
        """
        with self._lock:
            live = self._subscriptions.get(sub.subscription_id) or sub
            carried = live.rate_multiplier(max(0, live.periods_billed - 1))
            now = utc_now()
            # The new term picks up where the old one's invoices stop.
            # Starting it "now" on an early renewal billed again the part
            # of this month the last invoice already covered -- nineteen
            # days twice, renewing three weeks out. A renewal after a gap
            # starts today, never back-dated into months nobody was served.
            covered_to = live.period_start(live.periods_billed)
            live.cancelled_at = now
            live.cancellation_reason = "superseded by renewal"
            self._db.put("subscription", live.subscription_id, live.to_row())
        tenant = self.get_tenant(live.tenant_id)
        fresh = self.create_subscription(
            tenant=tenant,
            term_years=term_years,
            escalator_percent=live.escalator_percent,
            annual_prepay=live.annual_prepay,
            add_ons=list(live.add_ons),
            purchase_order=live.purchase_order,
            auto_renew=live.auto_renew,
            signed_by=live.signed_by,
            started_at=max(covered_to, now),
            carried_multiplier=carried,
        )
        # Commissioning was done once, for the estate, and charged then. A
        # renewal is the same equipment carrying on; charging it again was
        # a second $1,500 for nothing.
        if live.setup_billed:
            self.mark_setup_billed(fresh)
        return fresh


# Everything store.py defines, and everything it re-exports from
# domain.py and records.py, so `from store import X` works for every name
# that used to live here. Listing them also tells pyflakes the
# re-exports are deliberate.
__all__ = [
    "ALERT_MAIL_KEYS",
    "DISPATCH_SLA_SECONDS",
    "SENSOR_OFFLINE_AFTER_MINUTES",
    "VOICE_ESCALATION_GRACE_MINUTES",
    "SMS_RETRY_AFTER_MINUTES",
    "RETRYABLE_SMS_STATUSES",
    "VOICE_REDIAL_COOLDOWN_MINUTES",
    "utc_now",
    "iso",
    "hash_secret",
    "_parse",
    "mask_email",
    "TEMPERATURE_UNITS",
    "ABSOLUTE_ZERO_F",
    "IMPLAUSIBLE_ABOVE_F",
    "SeatClaimRefused",
    "ImplausibleReading",
    "require_plausible",
    "to_celsius",
    "to_fahrenheit",
    "display_temperature",
    "format_temperature",
    "_WRITTEN_TEMPERATURE",
    "restate_temperatures",
    "spoken_temperature",
    "INDUSTRY_PROFILES",
    "PLAN_TIERS",
    "resolve_vertical",
    "evaluate_breach",
    "evaluate_sensor_breach",
    "VOLUME_DISCOUNT_STEP_PERCENT",
    "VOLUME_DISCOUNT_EVERY_UNITS",
    "MAX_VOLUME_DISCOUNT_PERCENT",
    "ENTERPRISE_MONTHLY_CAP",
    "volume_discount_percent",
    "_raw_total",
    "calculate_volume_tier_price",
    "volume_band",
    "volume_tier_label",
    "next_volume_tier",
    "volume_band_table",
    "Tenant",
    "Sensor",
    "Reading",
    "Incident",
    "ROLES",
    "ROLE_RANK",
    "SESSION_TTL_HOURS",
    "User",
    "LoginSession",
    "AuditEntry",
    "UsageDay",
    "Site",
    "VaultAnchor",
    "INVOICE_STATES",
    "Invoice",
    "DEFAULT_PARTNER_COMMISSION_PERCENT",
    "Partner",
    "WEBHOOK_KINDS",
    "AlertWebhook",
    "Contact",
    "JoinCode",
    "ResetToken",
    "EnterpriseContract",
    "MAX_TERM_YEARS",
    "MAX_CATCHUP_PERIODS",
    "add_months",
    "Subscription",
    "MAIL_MAX_ATTEMPTS",
    "MAIL_MAX_AGE_HOURS",
    "MAIL_BODY_KEEP_DAYS",
    "MAIL_BODY_REMOVED",
    "EXPENSE_CATEGORIES",
    "Expense",
    "SENSITIVE_BODY_REMOVED",
    "MailMessage",
    "MailSuppression",
    "MAX_READINGS_PER_SENSOR",
    "MAX_CACHE_ENTRIES",
    "MAX_CACHE_TEXT_CHARS",
    "SCRYPT_N",
    "hash_password",
    "verify_password",
    "MAX_ANCHORS_PER_SENSOR_PER_DAY",
    "_mirror_payment",
    "HubStore",
    "STORE",
]

STORE = HubStore()
