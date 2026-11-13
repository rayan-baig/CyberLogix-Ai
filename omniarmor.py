"""The Kitchen Safety Bundle: each day's fridge record, sent to OmniArmor.

OmniArmor (MTRX Horizyn's food-safety checklist app) keeps a temperature-
log check that somebody has to fill in by hand. With the bundle, this app
fills it in: once a day, after the site's day has ended, it sends OmniArmor
one combined report for the business (`POST /api/connect/cyberlogix/daily`)
and OmniArmor ticks the check on a clean day or leaves a note for a person
on a day something went out of range.

The contract is OmniArmor's docs/CYBERLOGIX-CONNECTION.md, and this module
is built exactly to it. tests/omniarmor_contract.py carries OmniArmor's own
validation, ported, and the tests send every report through it, so a
field that drifts fails here before it fails there.

What it promises:

* **No names.** A report carries temperatures, unit and site names, times
  and roles. Who acknowledged an alarm is sent as a role from OmniArmor's
  fixed list ("on-call staff", "manager"...), never the person; what was
  done, as one of its fixed action codes. A unit or site whose name holds
  somebody's name is sent as "Unit 2" instead.
* **The key is a secret.** Stored encrypted with AES-GCM under
  CYBERLOGIX_CREDENTIAL_KEY, bound to the business it belongs to, never
  logged, never shown again after it is pasted (the last four characters
  are). Without that setting nothing can be connected.
* **One send per day.** Each day is claimed before it is sent, under the
  store lock, with a lease, so two passes cannot both send it. A day sent
  is not sent again unless what it says has changed since (an alarm
  acknowledged after midnight, a corrective action written up the next
  morning): that is a correction, and the contract says a correction
  replaces the earlier report.
* **Patient with OmniArmor.** 429 and 5xx, and a network that fails, are
  retried with backoff, waiting at least as long as Retry-After asks; a
  wait longer than a pass can hold is picked up by the next pass. 401
  stops sending until the owner pastes a new key. 422 is our bug: it is
  logged as a fault for us and not retried as it is.
* **It says how it is doing.** "Last sent 2026-11-12 · OK" in the console,
  each day's result kept, and an email to the owners, once, when sending
  has failed for two days running.

Owner only, every route: the key is a credential and the bundle is on the
bill.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as clock_time, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from accounts import require_role, tenant_of
from auth import write_audit
from store import STORE, Tenant, User, display_temperature, iso, utc_now

logger = logging.getLogger("cyberlogix.omniarmor")

router = APIRouter(prefix="/api/connections/omniarmor", tags=["Kitchen Safety Bundle"])

CONNECTION_KIND = "omniarmor_connection"
DAY_KIND = "omniarmor_day"

PATH = "/api/connect/cyberlogix/daily"

# The secret every stored credential is sealed with. Unset, nothing can be
# connected: a key kept in the clear in the database (and in every backup
# of it) is the thing the contract asks us not to do.
CREDENTIAL_KEY = os.environ.get("CYBERLOGIX_CREDENTIAL_KEY", "").strip()

# ---- the contract (OmniArmor docs/CYBERLOGIX-CONNECTION.md) --------------
KEY_RE = re.compile(r"^clx_[A-Za-z0-9_-]{43}$")
ROLES = ("owner", "manager", "chef", "shift lead", "on-call staff",
         "kitchen staff", "maintenance", "technician")
ACTIONS = ("moved_food", "discarded_food", "checked_food", "closed_door",
           "adjusted_unit", "called_technician", "defrost_cycle", "monitoring",
           "none")
MAX_BODY = 32 * 1024
MAX_UNITS = 500
MAX_BREACHES = 50
MAX_TEXT = 60
MAX_URL = 500
LOWEST, HIGHEST = -100.0, 250.0
OLDEST_DAYS = 31
LONGEST_BREACH = timedelta(hours=48)

# ---- when and how often ---------------------------------------------------
# A few minutes after midnight at the site, as the contract suggests: late
# readings from a slow gateway have arrived by then.
SEND_AFTER_MIDNIGHT = timedelta(minutes=15)
# Days caught up after an outage, newest last. A server down for a weekend
# still sends Friday, Saturday and Sunday.
CATCH_UP_DAYS = 7
# Days looked at again for a change since they were sent.
CORRECTION_DAYS = 2
# Tries per day per pass, and the waits between them.
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (2.0, 4.0, 8.0)
# The longest a pass sleeps for one Retry-After. Longer, and the day is
# left for the pass that comes after the wait.
MAX_WAIT_SECONDS = 30.0
# A claim older than this belongs to a pass that died: it may be taken.
CLAIM_LEASE = timedelta(minutes=10)
# Days of failure before the owners are emailed, once.
FAILED_DAYS_BEFORE_EMAIL = 2
TIMEOUT_SECONDS = 20

# CyberLogix roles onto OmniArmor's. A person's name never leaves; their
# role in the account does.
USER_ROLES = {"owner": "owner", "operator": "manager", "viewer": "kitchen staff"}
DEFAULT_ROLE = "on-call staff"

_pass_lock = threading.Lock()

Transport = Callable[[str, str, bytes], Tuple[int, Dict[str, str], bytes]]


# ---- the key, sealed -------------------------------------------------------


def credential_key_configured() -> bool:
    return bool(CREDENTIAL_KEY)


def _cipher():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not CREDENTIAL_KEY:
        raise RuntimeError("CYBERLOGIX_CREDENTIAL_KEY is not set.")
    key = hashlib.sha256(b"cyberlogix-credential-v1:" + CREDENTIAL_KEY.encode()).digest()
    return AESGCM(key)


def seal(tenant_id: str, secret: str) -> str:
    """The secret encrypted, bound to its business: no other row opens it."""
    nonce = os.urandom(12)
    sealed = _cipher().encrypt(nonce, secret.encode("utf-8"), tenant_id.encode("utf-8"))
    return "v1." + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")


def unseal(tenant_id: str, token: str) -> str:
    if not token.startswith("v1."):
        raise ValueError("Not a sealed credential.")
    raw = base64.urlsafe_b64decode(token[3:].encode("ascii"))
    return _cipher().decrypt(raw[:12], raw[12:], tenant_id.encode("utf-8")).decode("utf-8")


# ---- the connection --------------------------------------------------------


def connection(tenant_id: str) -> Optional[Dict[str, Any]]:
    return STORE._db.get(CONNECTION_KIND, tenant_id)


def bundle_active(tenant_id: str) -> bool:
    """Whether this business is connected, which is what puts the bundle
    on its bill (pricing.auto_add_ons)."""
    row = connection(tenant_id)
    return bool(row and row.get("enabled", True))


def _save(row: Dict[str, Any]) -> None:
    STORE._db.put(CONNECTION_KIND, row["tenant_id"], row)


def normalise_address(raw: str) -> str:
    """OmniArmor's address as scheme and host: https only, public only."""
    import pollers

    value = (raw or "").strip().rstrip("/")
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    if parts.scheme != "https":
        raise ValueError("OmniArmor's address must start https://.")
    if parts.username or parts.password or "@" in parts.netloc:
        raise ValueError("Leave any user name or password out of the address.")
    host = pollers.host_of(value)
    pollers.refuse_a_host_pointing_inward(host)
    port = f":{parts.port}" if parts.port else ""
    return f"https://{host}{port}"


def _default_timezone(tenant: Tenant, site_ids: List[str]) -> str:
    from sitewide import site_timezone

    return site_timezone(tenant, site_ids[0] if site_ids else None, "UTC")


def _zone(name: str):
    import oncall

    try:
        return oncall.zone(name)
    except ValueError:
        return timezone.utc


# ---- what a report says ----------------------------------------------------


def _person_words(tenant: Tenant) -> List[str]:
    """Every person's name this business holds, as words to keep out."""
    import people

    names = [u.full_name for u in STORE.users_for(tenant.tenant_id)]
    names += [c.full_name for c in STORE.contacts_for(tenant.tenant_id)]
    names += [r.get("full_name", "") for r in people.staff_for(tenant.tenant_id)]
    names.append(tenant.contact_name)
    words = set()
    for name in names:
        for word in re.findall(r"[^\W\d_]{3,}", name or ""):
            words.add(word.casefold())
    return sorted(words)


def safe_text(value: Optional[str], words: List[str]) -> Optional[str]:
    """A unit or site name fit to leave: short, one line, and nobody's name.

    None when it cannot be made safe, and the caller sends a neutral name.
    """
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", value or "").strip()
    text = re.sub(r"\s+", " ", text)[:MAX_TEXT].strip()
    if not text:
        return None
    if "@" in text or re.search(r"\d[\d\s().-]{6,}\d", text):
        return None  # an address or a phone number
    found = {w.casefold() for w in re.findall(r"[^\W\d_]{3,}", text)}
    if found & set(words):
        return None
    return text


def role_for(tenant: Tenant, acknowledged_by: Optional[str]) -> Optional[str]:
    """Who acknowledged, as one of OmniArmor's roles. Never a name."""
    if not acknowledged_by:
        return None
    text = acknowledged_by.split(", with site-wide alert")[0]
    users = STORE.users_for(tenant.tenant_id)
    found = re.search(r"<([^>]+)>", text)
    if found:
        email = found.group(1).strip().lower()
        for user in users:
            if (user.email or "").lower() == email:
                return USER_ROLES.get(user.role, DEFAULT_ROLE)
    if "phone keypad" in text or "by text reply" in text:
        return DEFAULT_ROLE
    name = text.strip().casefold()
    for user in users:
        if user.full_name and user.full_name.strip().casefold() == name:
            return USER_ROLES.get(user.role, DEFAULT_ROLE)
    return DEFAULT_ROLE


def action_for(incident) -> Optional[str]:
    """What was done, as one of OmniArmor's action codes.

    What happened to the food first, because that is what an inspector
    reads the note for; then what was done to the unit. Something written
    down that is none of these is "being monitored"; nothing written down
    on an alarm somebody has is "no action yet"; nothing at all is null.
    """
    text = (incident.corrective_action or "").casefold()
    fate = incident.product_disposition or ""
    if fate == "discarded" or any(w in text for w in ("discard", "destroy", "threw", "binned")):
        return "discarded_food"
    if fate in ("moved", "quarantined") or any(
            w in text for w in ("moved", "move ", "backup", "transfer", "quarantin")):
        return "moved_food"
    if "door" in text:
        return "closed_door"
    if any(w in text for w in ("engineer", "technician", "service", "repair")):
        return "called_technician"
    if "defrost" in text:
        return "defrost_cycle"
    if any(w in text for w in ("adjust", "thermostat", "setting", "fault corrected")):
        return "adjusted_unit"
    if fate in ("unaffected", "sold_or_used") or any(
            w in text for w in ("probe", "checked", "core temp")):
        return "checked_food"
    if text:
        return "monitoring"
    if incident.acknowledged_at is not None:
        return "none"
    return None


def _uom(tenant: Tenant) -> str:
    return "°C" if tenant.temperature_unit == "C" else "°F"


def _temp(fahrenheit: Optional[float], unit: str) -> Optional[float]:
    value = display_temperature(fahrenheit, unit)
    if value is None or not math.isfinite(value):
        return None
    value = round(float(value), 1)
    return value if LOWEST <= value <= HIGHEST else None


def _local_iso(moment: datetime, tz) -> str:
    return moment.astimezone(tz).isoformat(timespec="seconds")


def _sensors_for(tenant: Tenant, row: Dict[str, Any], before: Optional[datetime] = None):
    """The units the report covers: the mapped sites' (or all), and only
    those that existed by the end of the day, so a fridge added this
    morning is not reported as unconfirmed yesterday."""
    wanted = set(row.get("site_ids") or [])
    sensors = STORE.sensors_for(tenant.tenant_id)
    if wanted:
        sensors = [s for s in sensors if s.site_id in wanted]
    if before is not None:
        sensors = [s for s in sensors if s.registered_at is None or s.registered_at < before]
    return sorted(sensors, key=lambda s: (s.location_name or "", s.sensor_id))


def _site_name(tenant: Tenant, row: Dict[str, Any], words: List[str]) -> Optional[str]:
    if row.get("site_label"):
        return safe_text(row["site_label"], words)
    wanted = row.get("site_ids") or []
    sites = {s.site_id: s for s in STORE.sites_for(tenant.tenant_id)}
    names = [sites[i].name for i in wanted if i in sites]
    if not wanted and len(sites) == 1:
        names = [next(iter(sites.values())).name]
    return safe_text(names[0], words) if len(names) == 1 else None


def _came_back(sensor, opened: datetime) -> Optional[datetime]:
    """When the unit next read in range after the breach opened.

    In time order: readings are kept in the order they arrived, and a
    gateway that catches up sends an older one after a newer one.
    """
    later = sorted((r for r in STORE.readings_for(sensor.sensor_id, since=opened)
                    if r.recorded_at > opened), key=lambda r: r.recorded_at)
    for reading in later:
        if not reading.breached:
            return reading.recorded_at
    return None


def _log_url(tenant: Tenant, day: date) -> Optional[str]:
    from monthly import inspector_link

    link = inspector_link(tenant, day, day)
    if not link.startswith("https://") or len(link) > MAX_URL or " " in link:
        return None
    return link


def build_report(tenant: Tenant, row: Dict[str, Any], day: date,
                 now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The day's report, exactly as the contract has it, or None if there
    is nothing to report on (no units)."""
    from inspector import _readings

    now = now or utc_now()
    tz = _zone(row.get("timezone") or "UTC")
    start = datetime.combine(day, clock_time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), clock_time.min,
                           tzinfo=tz).astimezone(timezone.utc)
    unit = tenant.temperature_unit
    uom = _uom(tenant)
    words = _person_words(tenant)
    sensors = _sensors_for(tenant, row, end)[:MAX_UNITS]
    if not sensors:
        return None

    incidents = [i for i in STORE.incidents_for(tenant.tenant_id)
                 if not i.site_wide and i.opened_at < end
                 and (i.resolved_at is None or i.resolved_at >= start)]

    in_range = 0
    warmest: Optional[float] = None
    breaches: List[Dict[str, Any]] = []
    for index, sensor in enumerate(sensors, start=1):
        name = safe_text(sensor.location_name, words) or f"Unit {index}"
        mine = sorted((i for i in incidents if i.sensor_id == sensor.sensor_id),
                      key=lambda i: i.opened_at)
        readings = _readings(sensor, start, end, mine)
        if readings and not mine and not any(r["breached"] for r in readings):
            in_range += 1
        for reading in readings:
            if not reading["breached"]:
                shown = _temp(reading["f"], unit)
                if shown is not None and (warmest is None or shown > warmest):
                    warmest = shown
        above, below = sensor.bounds()
        for incident in mine:
            opened = incident.opened_at
            if not day - timedelta(days=1) <= opened.astimezone(tz).date() <= day + timedelta(days=1):
                continue  # began too long before: the unit counts as out of range
            back = _came_back(sensor, opened) or incident.resolved_at
            if back is not None and (back > now or not timedelta(0) <= back - opened <= LONGEST_BREACH):
                back = None
            spell = [r["f"] for r in readings
                     if r["breached"] and opened <= r["at"] <= (back or now)]
            temps = spell or [incident.temperature_fahrenheit]
            if above is not None and max(temps) > above:
                worst, limit = max(temps), above
            elif below is not None and min(temps) < below:
                worst, limit = min(temps), below
            else:
                worst = incident.temperature_fahrenheit
                limit = above if above is not None else below
            peak, edge = _temp(worst, unit), _temp(limit, unit)
            if peak is None or edge is None:
                continue  # a fault reading, not a temperature: out of range, no detail
            breaches.append({
                "unit": name,
                "started_at": _local_iso(opened, tz),
                "ended_at": _local_iso(back, tz) if back is not None else None,
                "peak": peak,
                "limit": edge,
                "unit_of_measure": uom,
                "acknowledged_by_role": role_for(tenant, incident.acknowledged_by),
                "action": action_for(incident),
            })

    breaches.sort(key=lambda b: b["started_at"])
    report: Dict[str, Any] = {
        "date": day.isoformat(),
        "site": _site_name(tenant, row, words),
        "units_total": len(sensors),
        "units_in_range_all_day": in_range,
        "breaches": breaches[:MAX_BREACHES],
    }
    if warmest is not None:
        report["warmest_reading"] = warmest
        report["unit_of_measure"] = uom
    report["log_url"] = _log_url(tenant, day)
    return report


def clean_day_without_reading(report: Dict[str, Any]) -> bool:
    """A clean day must carry its warmest reading; one that cannot is not
    sendable as clean (the contract refuses it), so it is not sent."""
    clean = not report["breaches"] and report["units_in_range_all_day"] == report["units_total"]
    return clean and "warmest_reading" not in report


def fingerprint(report: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


# ---- sending ---------------------------------------------------------------


def _post(url: str, key: str, body: bytes) -> Tuple[int, Dict[str, str], bytes]:
    """One POST to OmniArmor. Raises on a network failure.

    Through the poller's opener, so it follows no redirect (a redirect
    would carry the key to an address nobody checked), uses no proxy, and
    connects only to a public address the name was checked against.
    """
    import pollers

    pollers._resolve_to_public(pollers.host_of(url))
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("Content-Type", "application/json; charset=utf-8")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "CyberLogix-KitchenSafetyBundle/1")
    try:
        with pollers._OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, dict(response.headers), response.read(65536)
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read(65536)


# Replaced by the tests with a fake OmniArmor.
TRANSPORT: Transport = _post


def retry_after_seconds(headers: Dict[str, str], now: Optional[datetime] = None) -> Optional[float]:
    """Retry-After as seconds, whether sent as seconds or as a date."""
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    if value is None:
        return None
    value = str(value).strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or utc_now())).total_seconds())


def deliver(url: str, key: str, report: Dict[str, Any],
            sleep: Callable[[float], None] = time.sleep,
            transport: Optional[Transport] = None) -> Dict[str, Any]:
    """Send one report, retrying what the contract says is safe to retry.

    Returns {"outcome": "ok" | "retry_later" | "key_rejected" | "refused" |
    "failed", ...}. Never raises.
    """
    send = transport or TRANSPORT
    body = json.dumps(report, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    attempts = 0
    last: Dict[str, Any] = {}
    while attempts < MAX_ATTEMPTS:
        attempts += 1
        wait: Optional[float] = None
        try:
            code, headers, raw = send(url, key, body)
        except Exception as exc:  # noqa: BLE001 - a network failure is a Tuesday
            last = {"outcome": "failed", "status": None,
                    "detail": f"Could not reach OmniArmor ({type(exc).__name__})."}
            code, headers, raw = 0, {}, b""
        else:
            try:
                answer = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, ValueError):
                answer = {}
            if not isinstance(answer, dict):
                answer = {}
            if 200 <= code < 300:
                return {"outcome": "ok", "status": code, "attempts": attempts,
                        "answer": {k: answer.get(k) for k in
                                   ("ok", "date", "matched_checks", "ticked", "noted",
                                    "unchanged", "checks", "hint")}}
            if code == 401:
                return {"outcome": "key_rejected", "status": code, "attempts": attempts,
                        "detail": "OmniArmor did not accept the connection key. Make a "
                                  "new one in OmniArmor (Settings, Connections) and "
                                  "paste it here."}
            if code in (400, 405, 413, 415, 422):
                return {"outcome": "refused", "status": code, "attempts": attempts,
                        "detail": str(answer.get("message") or f"HTTP {code}")[:300],
                        "errors": answer.get("errors") or []}
            if code == 429 or code >= 500:
                wait = retry_after_seconds(headers)
                last = {"outcome": "failed", "status": code,
                        "detail": f"OmniArmor answered {code}."}
            else:
                return {"outcome": "failed", "status": code, "attempts": attempts,
                        "detail": f"OmniArmor answered {code}."}
        if attempts >= MAX_ATTEMPTS:
            break
        backoff = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
        if wait is not None and wait > MAX_WAIT_SECONDS:
            return {**last, "outcome": "retry_later", "attempts": attempts,
                    "retry_after": wait}
        sleep(max(backoff, wait or 0.0))
    return {**last, "attempts": attempts}


# ---- one day, claimed ------------------------------------------------------


def _day_id(tenant_id: str, day: date) -> str:
    return f"{tenant_id}:{day.isoformat()}"


def day_row(tenant_id: str, day: date) -> Optional[Dict[str, Any]]:
    return STORE._db.get(DAY_KIND, _day_id(tenant_id, day))


def days_for(tenant_id: str) -> List[Dict[str, Any]]:
    rows = [r for r in STORE._db.all(DAY_KIND) if r.get("tenant_id") == tenant_id]
    return sorted(rows, key=lambda r: r.get("date") or "")


def claim_day(tenant_id: str, day: date, digest: str, now: datetime) -> Optional[str]:
    """Take the right to send this day. Returns "first", "correction", or
    None when it must not be sent (sent already as it stands, being sent by
    another pass, or waiting out a Retry-After)."""
    with STORE._lock:
        row = day_row(tenant_id, day) or {
            "tenant_id": tenant_id, "date": day.isoformat(), "state": "new",
            "attempts": 0, "corrections": 0, "sent_fingerprint": None,
        }
        claimed = row.get("claimed_at")
        if row.get("state") == "sending" and claimed and \
                now - datetime.fromisoformat(claimed.replace("Z", "+00:00")) < CLAIM_LEASE:
            return None
        wait = row.get("next_attempt_at")
        if wait and datetime.fromisoformat(wait.replace("Z", "+00:00")) > now:
            return None
        if row.get("sent_fingerprint") == digest:
            return None
        if row.get("refused_fingerprint") == digest:
            return None  # refused as it stands; a change can still go
        kind = "correction" if row.get("sent_fingerprint") else "first"
        row["state"] = "sending"
        row["claimed_at"] = iso(now)
        STORE._db.put(DAY_KIND, _day_id(tenant_id, day), row)
        return kind


def _finish_day(tenant_id: str, day: date, digest: str, kind: str,
                result: Dict[str, Any], report: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    with STORE._lock:
        row = day_row(tenant_id, day) or {"tenant_id": tenant_id, "date": day.isoformat()}
        row["attempts"] = int(row.get("attempts") or 0) + int(result.get("attempts") or 1)
        row["claimed_at"] = None
        row["last_try_at"] = iso(now)
        row["http_status"] = result.get("status")
        row["next_attempt_at"] = None
        if result["outcome"] == "ok":
            row["state"] = "sent"
            row["sent_fingerprint"] = digest
            row["sent_at"] = iso(now)
            row["detail"] = ""
            row["answer"] = result.get("answer") or {}
            if kind == "correction":
                row["corrections"] = int(row.get("corrections") or 0) + 1
            row["summary"] = {
                "units_total": report["units_total"],
                "units_in_range_all_day": report["units_in_range_all_day"],
                "breaches": len(report["breaches"]),
            }
        else:
            # A correction that fails leaves the day as sent: what OmniArmor
            # holds is the earlier report, and the next pass tries again.
            row["state"] = "sent" if row.get("sent_fingerprint") else "failed"
            row["detail"] = result.get("detail", "")
            if result["outcome"] == "retry_later":
                row["next_attempt_at"] = iso(now + timedelta(seconds=result["retry_after"]))
            if result["outcome"] == "refused":
                row["refused_fingerprint"] = digest
                row["errors"] = result.get("errors") or []
        row["last_outcome"] = result["outcome"]
        STORE._db.put(DAY_KIND, _day_id(tenant_id, day), row)
        return row


def send_day(tenant: Tenant, day: date, now: Optional[datetime] = None,
             sleep: Callable[[float], None] = time.sleep,
             transport: Optional[Transport] = None) -> Dict[str, Any]:
    """Build, claim and send one day's report. Never raises."""
    now = now or utc_now()
    row = connection(tenant.tenant_id)
    if row is None or not row.get("enabled", True):
        return {"status": "not_connected"}
    if row.get("key_rejected_at"):
        return {"status": "key_rejected", "detail": row.get("last_detail", "")}
    report = build_report(tenant, row, day, now)
    if report is None:
        return {"status": "no_units"}
    if clean_day_without_reading(report):
        return {"status": "no_readings"}
    digest = fingerprint(report)
    kind = claim_day(tenant.tenant_id, day, digest, now)
    if kind is None:
        return {"status": "already_sent_or_in_hand"}
    try:
        key = unseal(tenant.tenant_id, row["key_ciphertext"])
        result = deliver(row["base_url"] + PATH, key, report, sleep=sleep, transport=transport)
    except Exception as exc:  # noqa: BLE001 - the claim must be finished either way
        logger.exception("OmniArmor send for %s failed before it went.", tenant.tenant_id)
        result = {"outcome": "failed", "status": None,
                  "detail": f"Could not send ({type(exc).__name__})."}
    done = _finish_day(tenant.tenant_id, day, digest, kind, result, report, now)
    _after(tenant, row, day, kind, result, now)
    return {"status": result["outcome"], "kind": kind, "day": day.isoformat(),
            "result": result, "record": public_day(done)}


def _after(tenant: Tenant, row: Dict[str, Any], day: date, kind: str,
           result: Dict[str, Any], now: datetime) -> None:
    """Write down what happened on the connection, and tell who needs it."""
    with STORE._lock:
        live = connection(tenant.tenant_id) or row
        live["last_try_at"] = iso(now)
        live["last_outcome"] = result["outcome"]
        live["last_detail"] = result.get("detail", "")
        if result["outcome"] == "ok":
            if not live.get("last_sent_date") or day.isoformat() >= live["last_sent_date"]:
                live["last_sent_date"] = day.isoformat()
            live["last_sent_at"] = iso(now)
        if result["outcome"] == "key_rejected":
            live["key_rejected_at"] = iso(now)
        _save(live)
    if result["outcome"] == "refused":
        # The contract calls this our bug. It is written down as a fault so
        # the error report reaches the team, and not retried as it stands.
        import faults

        faults.record("omniarmor.daily",
                      ValueError(f"OmniArmor refused the {day.isoformat()} report: "
                                 f"{json.dumps(result.get('errors'))[:500]}"),
                      context=f"tenant {tenant.tenant_id}, {kind}")
        logger.error("OmniArmor refused %s's report for %s (%s): %s",
                     tenant.tenant_id, day, result.get("status"), result.get("errors"))


def failing_days(tenant_id: str) -> List[str]:
    """Days not delivered since the last day that was."""
    rows = days_for(tenant_id)
    last_ok = max((r["date"] for r in rows if r.get("sent_fingerprint")), default="")
    return [r["date"] for r in rows
            if not r.get("sent_fingerprint") and r.get("last_outcome")
            and r.get("last_outcome") != "ok" and r["date"] > last_ok]


def maybe_email_owners(tenant: Tenant, now: Optional[datetime] = None) -> bool:
    """Two days not delivered: tell the owners, once for this run of failures."""
    from mail import send as send_mail
    from monthly import owners

    failed = failing_days(tenant.tenant_id)
    if len(failed) < FAILED_DAYS_BEFORE_EMAIL:
        return False
    streak = failed[0]
    row = connection(tenant.tenant_id)
    if row is None or row.get("failure_email_for") == streak:
        return False
    detail = row.get("last_detail") or "OmniArmor did not take the report."
    sent = False
    for owner in owners(tenant.tenant_id):
        person = STORE.person_of(owner)
        result = send_mail(
            to_address=owner.email,
            subject=f"{tenant.company_name}: the daily report to OmniArmor is not getting through",
            body=(
                f"{owner.full_name},\n\n"
                f"CyberLogix has not been able to send OmniArmor the daily "
                f"fridge and freezer report for {len(failed)} days "
                f"({', '.join(failed[:7])}).\n\n"
                f"The last answer: {detail}\n\n"
                "Your alarms are not affected: texts and calls carry on as "
                "normal. What is missing is OmniArmor's temperature-log check "
                "being filled in for you.\n\n"
                "Open the console, Setup, OmniArmor connection, to see each "
                "day. If OmniArmor no longer accepts the key, make a new one "
                "in OmniArmor (Settings, Connections) and paste it there. "
                "Every day missed is sent once it gets through.\n\n"
                "You get this once for this run of failures."),
            dedupe_key=f"omniarmor-failing:{tenant.tenant_id}:{streak}:{person.user_id}",
            klass="transactional",
            tenant_id=tenant.tenant_id,
        )
        sent = sent or bool(result.get("sent") or result.get("status") == "queued")
    with STORE._lock:
        live = connection(tenant.tenant_id)
        if live is not None:
            live["failure_email_for"] = streak
            live["failure_email_at"] = iso(now or utc_now())
            _save(live)
    return True


def local_today(row: Dict[str, Any], now: datetime) -> Tuple[date, datetime]:
    tz = _zone(row.get("timezone") or "UTC")
    local = now.astimezone(tz)
    return local.date(), local


def days_due(row: Dict[str, Any], now: datetime) -> List[date]:
    """The finished days this connection still owes, oldest first."""
    today, local = local_today(row, now)
    midnight = datetime.combine(today, clock_time.min, tzinfo=local.tzinfo)
    newest = today - timedelta(days=1)
    if local - midnight < SEND_AFTER_MIDNIGHT:
        newest = today - timedelta(days=2)
    connected = datetime.fromisoformat(
        str(row.get("connected_at") or iso(now)).replace("Z", "+00:00")
    ).astimezone(local.tzinfo).date()
    oldest = max(today - timedelta(days=CATCH_UP_DAYS), connected - timedelta(days=1))
    days = []
    day = oldest
    while day <= newest:
        days.append(day)
        day += timedelta(days=1)
    return days


def run_daily(now: Optional[datetime] = None,
              sleep: Callable[[float], None] = time.sleep,
              transport: Optional[Transport] = None) -> Dict[str, Any]:
    """Send every connected business the days it is owed, and corrections.

    One pass at a time in this process; each day is also claimed in the
    database, so even two processes could not both send it.
    """
    now = now or utc_now()
    if not _pass_lock.acquire(blocking=False):
        logger.warning("An OmniArmor pass is still running; this one stands aside.")
        return {"skipped": "another pass is still running", "sent": 0}
    try:
        sent = corrected = failed = 0
        for row in list(STORE._db.all(CONNECTION_KIND)):
            tenant = STORE.get_tenant(row.get("tenant_id") or "")
            if tenant is None or not row.get("enabled", True) or tenant.suspended:
                continue
            try:
                from contracts import delinquency

                if delinquency(tenant.tenant_id, now)["delinquent"]:
                    continue  # a report, withheld like the others; alarms are not
                due = days_due(row, now)
                done = {r["date"]: r for r in days_for(tenant.tenant_id)}
                recheck = set(d.isoformat() for d in due[-CORRECTION_DAYS:])
                for day in due:
                    have = done.get(day.isoformat())
                    if have and have.get("sent_fingerprint") and day.isoformat() not in recheck:
                        continue
                    if row.get("key_rejected_at"):
                        _record_unsent(tenant.tenant_id, day, "key_rejected",
                                       row.get("last_detail", ""), now)
                        continue
                    result = send_day(tenant, day, now, sleep=sleep, transport=transport)
                    if result.get("status") == "ok":
                        if result.get("kind") == "correction":
                            corrected += 1
                        else:
                            sent += 1
                    elif result.get("status") in ("failed", "retry_later", "refused",
                                                  "key_rejected"):
                        failed += 1
                    row = connection(tenant.tenant_id) or row
                maybe_email_owners(tenant, now)
            except Exception as exc:  # noqa: BLE001 - one business must not stop the rest
                logger.exception("OmniArmor pass failed for %s (%s).", tenant.tenant_id, exc)
                failed += 1
        return {"sent": sent, "corrections": corrected, "failed": failed}
    finally:
        _pass_lock.release()


def _record_unsent(tenant_id: str, day: date, outcome: str, detail: str,
                   now: datetime) -> None:
    """A day that could not even be tried (the key was refused) still
    counts as a day not delivered, for the console and the email."""
    with STORE._lock:
        row = day_row(tenant_id, day) or {"tenant_id": tenant_id, "date": day.isoformat(),
                                          "attempts": 0, "corrections": 0,
                                          "sent_fingerprint": None}
        if row.get("sent_fingerprint"):
            return
        row["state"] = "failed"
        row["last_outcome"] = outcome
        row["detail"] = detail
        row["last_try_at"] = iso(now)
        STORE._db.put(DAY_KIND, _day_id(tenant_id, day), row)


# ---- what the console shows ------------------------------------------------


def public_day(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "date": row.get("date"),
        "state": row.get("state"),
        "outcome": row.get("last_outcome"),
        "sent_at": row.get("sent_at"),
        "corrections": row.get("corrections", 0),
        "detail": row.get("detail", ""),
        "summary": row.get("summary"),
        "checks": [(c or {}).get("result") for c in
                   ((row.get("answer") or {}).get("checks") or [])],
    }


def status_line(row: Optional[Dict[str, Any]], tenant_id: str) -> str:
    """"Last sent 2026-11-12 · OK", or what is wrong, in one line."""
    if row is None:
        return "Not connected"
    if row.get("key_rejected_at"):
        return "OmniArmor refused the key · paste a new one"
    failed = failing_days(tenant_id)
    sent = row.get("last_sent_date")
    if failed:
        return (f"Last sent {sent} · " if sent else "") + f"not sent since {failed[0]}"
    if sent:
        return f"Last sent {sent} · OK"
    return "Connected · the first report goes after the site's day ends"


def overview(tenant: Tenant) -> Dict[str, Any]:
    from pricing import ADD_ONS, BUNDLE_ADD_ON, bundle_price_label, locations_billed

    row = connection(tenant.tenant_id)
    entry = ADD_ONS[BUNDLE_ADD_ON]
    places = max(locations_billed(tenant), 1)
    return {
        "connected": row is not None,
        "address": row.get("base_url") if row else None,
        "key_hint": row.get("key_hint") if row else None,
        "site_ids": row.get("site_ids", []) if row else [],
        "site_label": row.get("site_label", "") if row else "",
        "timezone": row.get("timezone") if row else None,
        "status": status_line(row, tenant.tenant_id),
        "last_sent_date": row.get("last_sent_date") if row else None,
        "ok": bool(row and row.get("last_sent_date") and not failing_days(tenant.tenant_id)
                   and not row.get("key_rejected_at")),
        "key_rejected": bool(row and row.get("key_rejected_at")),
        "days": [public_day(r) for r in days_for(tenant.tenant_id)[-10:]][::-1],
        "sites": [{"site_id": s.site_id, "name": s.name}
                  for s in STORE.sites_for(tenant.tenant_id)],
        "credential_key_configured": credential_key_configured(),
        "price": {
            "label": bundle_price_label(),
            "monthly_usd": entry["monthly_usd"],
            "locations": places,
            "monthly_for_you_usd": round(entry["monthly_usd"] * places, 2),
            "free_now": tenant.plan == "trial",
        },
    }


# ---- routes ----------------------------------------------------------------


class ConnectionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    address: str = Field(..., min_length=4, max_length=200,
                         description="OmniArmor's address, e.g. https://omniarmor.example.com")
    key: Optional[str] = Field(None, max_length=60,
                               description="The connection key from OmniArmor (clx_...). "
                                           "Leave out to keep the one already saved.")
    site_ids: List[str] = Field(default_factory=list, max_length=50,
                                description="Sites to report on. Empty: every unit.")
    site_label: str = Field("", max_length=MAX_TEXT,
                            description="The site name OmniArmor shows. Optional.")
    timezone: Optional[str] = Field(None, max_length=64,
                                    description="The site's time zone, for when its day ends.")


def _owner_tenant(owner: User) -> Tenant:
    return tenant_of(owner)


@router.get("")
def read_connection(owner: User = Depends(require_role("owner"))):
    """The OmniArmor connection: where it sends, how it is doing, what it
    costs. The key is never shown, only its last four characters."""
    return overview(_owner_tenant(owner))


@router.put("")
def save_connection(payload: ConnectionSettings,
                    owner: User = Depends(require_role("owner"))):
    """Connect to OmniArmor, or change the connection. Owner only."""
    import oncall

    tenant = _owner_tenant(owner)
    if not credential_key_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("This server cannot store a connection key yet: "
                    "CYBERLOGIX_CREDENTIAL_KEY is not set. Ask whoever runs it."))
    try:
        base_url = normalise_address(payload.address)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=str(exc)) from None
    existing = connection(tenant.tenant_id)
    key = (payload.key or "").strip()
    if key and not KEY_RE.match(key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=("That is not an OmniArmor connection key. It starts clx_ and "
                    "is 47 characters; copy it from OmniArmor, Settings, "
                    "Connections."))
    if not key and existing is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Paste the connection key from OmniArmor.")
    own_sites = {s.site_id for s in STORE.sites_for(tenant.tenant_id)}
    unknown = [s for s in payload.site_ids if s not in own_sites]
    if unknown:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Not sites of this business: {unknown}")
    zone_name = (payload.timezone or "").strip()
    if zone_name:
        try:
            oncall.zone(zone_name)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail=f"'{zone_name}' is not a time zone.") from None
    else:
        zone_name = (existing or {}).get("timezone") or _default_timezone(
            tenant, list(payload.site_ids))
    now = utc_now()
    row = dict(existing or {})
    row.update({
        "tenant_id": tenant.tenant_id,
        "base_url": base_url,
        "site_ids": list(dict.fromkeys(payload.site_ids)),
        "site_label": payload.site_label,
        "timezone": zone_name,
        "enabled": True,
        "updated_at": iso(now),
    })
    row.setdefault("connected_at", iso(now))
    if key:
        row["key_ciphertext"] = seal(tenant.tenant_id, key)
        row["key_hint"] = key[-4:]
        row["key_rejected_at"] = None
        row["key_saved_at"] = iso(now)
    _save(row)
    write_audit(tenant, owner, "omniarmor.connected" if existing is None else "omniarmor.updated",
                f"OmniArmor connection {'made' if existing is None else 'changed'} "
                f"({base_url}){'; new key ending ' + key[-4:] if key else ''}.")
    return overview(tenant)


@router.delete("")
def remove_connection(owner: User = Depends(require_role("owner"))):
    """Disconnect. Nothing more is sent, the key is deleted, and the bundle
    comes off the bill from the next invoice."""
    tenant = _owner_tenant(owner)
    if connection(tenant.tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="There is no OmniArmor connection to remove.")
    STORE._db.delete(CONNECTION_KIND, tenant.tenant_id)
    write_audit(tenant, owner, "omniarmor.disconnected",
                "OmniArmor connection removed; its key was deleted.")
    return {"message": "Disconnected. The key is deleted and nothing more is sent.",
            **overview(tenant)}


@router.post("/test")
def send_test_report(owner: User = Depends(require_role("owner"))):
    """Send the last finished day's real report now, to see it arrive.

    OmniArmor has no test mode, so this is the real report for that day;
    sending it again is safe (OmniArmor answers "unchanged"), and the
    daily job will not send it a second time.
    """
    tenant = _owner_tenant(owner)
    row = connection(tenant.tenant_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Connect to OmniArmor first.")
    if row.get("key_rejected_at"):
        # The owner pressing the button is asking us to try the key again.
        with STORE._lock:
            row["key_rejected_at"] = None
            _save(row)
    today, _ = local_today(row, utc_now())
    day = today - timedelta(days=1)
    existing = day_row(tenant.tenant_id, day)
    result = send_day(tenant, day)
    if result["status"] == "already_sent_or_in_hand" and existing:
        result = {"status": "already_sent", "day": day.isoformat(),
                  "record": public_day(existing)}
    words = {
        "ok": f"Sent the report for {day.isoformat()}. OmniArmor took it.",
        "already_sent": f"The report for {day.isoformat()} was already sent, and nothing in it has changed.",
        "no_units": "There are no units to report on yet.",
        "no_readings": f"No readings arrived on {day.isoformat()}, so there is nothing to send for it.",
        "key_rejected": "OmniArmor did not accept the key. Make a new one there and paste it here.",
        "refused": "OmniArmor refused the report. We have been told and will fix it.",
        "retry_later": "OmniArmor asked us to wait. The daily job will send it.",
        "failed": "OmniArmor could not be reached. The daily job keeps trying.",
    }
    return {**result, "message": words.get(result["status"], result["status"]),
            **{"connection": overview(tenant)}}
