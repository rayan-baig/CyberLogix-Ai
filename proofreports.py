"""Proof of monitoring: a monthly report a business can hand its insurer.

Insurers and brokers pay out on spoiled stock, so a business that can
show its fridges were watched around the clock is a better risk. This
makes that showing cheap and believable. For one location and one month
it says:

* which sensors were watched, and how much of the time each was online;
* every temperature alert, and how fast somebody acknowledged it;
* the longest gap in the data;
* who gets alerted, by role only (never a name, phone number or email).

The business prints it (or saves it as a PDF) and hands it over. Each
report carries a short verify code and a link. Anybody with the link can
open a small public page that says whether the report is genuine and
unchanged, and shows its headline numbers and the business name. Nothing
else: no readings, no names, no unit or location names.

The rules this file keeps:

**The business decides.** Only an owner makes a report, and a report is
seen by nobody until the owner hands its link to someone. An owner can
switch a link off, and from then on it says so to whoever opens it.
Nothing here lists a business's reports to anybody else, partners
included (insurers.py has no path to them).

**Unguessable.** The verify code is 80 random bits, written as 16
letters and digits. It is the whole credential for the public page.

**Edits show.** The report's contents are frozen when it is made, hashed
(SHA-256 over a canonical form), and the hash is sealed with HMAC-SHA256
under a key the server holds, bound to the report's id and code. Change
one figure in the stored report, or swap in a made-up code, and the check
fails and the public page says "changed" instead of showing numbers.

**Honest about what it cannot see.** Readings are a ring buffer that
forgets the oldest, so time online and gaps come from the gaps written
down as they happen (store.py, "data_gap"). Time before that record
started is not counted, and the report says so.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import math
import os
import re
import secrets
import statistics
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from accounts import require_role, tenant_of
from auth import write_audit
from ratelimit import Limit
from store import (
    GAP_RECORD_MINUTES,
    SENSOR_OFFLINE_AFTER_MINUTES,
    STORE,
    VOICE_ESCALATION_GRACE_MINUTES,
    Tenant,
    User,
    _parse,
    display_temperature,
    iso,
    utc_now,
)

logger = logging.getLogger("cyberlogix.proofreports")

router = APIRouter(prefix="/api/proof-reports", tags=["Proof of monitoring"])

KIND = "proof_report"

# The seal's key. Set it to a long random value and keep it in the secret
# store: then nobody who can only read or edit the database can make an
# edited report pass. Unset, a key is made once and kept in the database,
# which still catches any edit made without it.
PROOF_REPORT_KEY = os.environ.get("CYBERLOGIX_PROOF_REPORT_KEY", "").strip()
_STORED_KEY_ID = "proof_report_key"

# 10 random bytes: 80 bits, written as 16 base32 characters (A-Z, 2-7).
CODE_BYTES = 10
_CODE_RE = re.compile(r"^[A-Z2-7]{16}$")

# The public check. Generous for a person checking a handful of reports,
# far too slow for anybody trying codes.
VERIFY_LIMIT = Limit("proof-verify", per_caller=30, total=1000,
                     window_seconds=600, words="report checks")

# Plain words for each role. Never a person's name.
# The console's own names for the roles (3.42.0): the report said "by the
# manager" and "Managers" where the Team tab says "operator".
_ROLE_WORDS = {"owner": "Owner", "manager": "Operator",
               "kitchen staff": "Viewer", "on-call staff": "On-call person"}
_TEAM_WORDS = {"owner": "Owners", "operator": "Operators", "viewer": "Viewers"}

STATE_WORDS = {
    "genuine": "Genuine and unchanged since it was made.",
    "changed": ("This report has been changed since it was made. Do not rely "
                "on it; ask the business for a fresh one."),
    "revoked": "The business has switched this link off.",
    "unchecked": ("This report is on file, but its seal cannot be checked "
                  "right now. Ask the business for a fresh one."),
    "unknown": ("No report answers to this code. Check it was typed exactly "
                "as printed."),
}

VERDICT = {"genuine": "Genuine", "changed": "Changed", "revoked": "Switched off",
           "unchecked": "Cannot check", "unknown": "Not found"}


# ---- the code and the seal -------------------------------------------------


def new_code() -> str:
    """16 characters of base32 from 80 random bits."""
    return base64.b32encode(secrets.token_bytes(CODE_BYTES)).decode("ascii")


def normalise_code(text: Optional[str]) -> str:
    """What a person typed, as the stored form: no dashes, upper case."""
    return re.sub(r"[^A-Za-z0-9]", "", text or "").upper()


def pretty_code(code: str) -> str:
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def canonical(body: Dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def content_hash(body: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical(body)).hexdigest()


def fingerprint(digest: str) -> str:
    """The first 64 bits of the hash, printed on the report and the page."""
    return pretty_code(digest[:16].upper())


def _stored_key() -> bytes:
    row = STORE._db.get("meta", _STORED_KEY_ID)
    if row and row.get("value"):
        return str(row["value"]).encode("utf-8")
    value = secrets.token_urlsafe(32)
    STORE._db.put("meta", _STORED_KEY_ID, {"value": value})
    return value.encode("utf-8")


def _key(source: str) -> Optional[bytes]:
    if source == "setting":
        return PROOF_REPORT_KEY.encode("utf-8") if PROOF_REPORT_KEY else None
    return _stored_key()


def seal(report_id: str, code: str, digest: str, key: bytes) -> str:
    message = f"{report_id}|{code}|{digest}".encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def check(row: Dict[str, Any]) -> str:
    """genuine, changed, revoked or unchecked: what the public page says."""
    if row.get("revoked_at"):
        return "revoked"
    body = row.get("body")
    if not isinstance(body, dict):
        return "changed"
    digest = content_hash(body)
    if not hmac.compare_digest(digest, str(row.get("content_hash") or "")):
        return "changed"
    source = str(row.get("key_source") or "")
    # With the key set, a seal under the database's own key proves nothing:
    # whoever can edit a report can also write that key, and re-seal the
    # edit under it. Every report made since the key was set is sealed with
    # it, so only those are checked.
    if PROOF_REPORT_KEY and source != "setting":
        return "unchecked"
    key = _key(source)
    if key is None:
        return "unchecked"
    expected = seal(str(row.get("report_id") or ""), str(row.get("code") or ""),
                    digest, key)
    if not hmac.compare_digest(expected, str(row.get("seal") or "")):
        return "changed"
    return "genuine"


def by_code(code: str) -> Optional[Dict[str, Any]]:
    """The report a code belongs to, compared in constant time."""
    code = normalise_code(code)
    if not _CODE_RE.match(code):
        return None
    found = None
    for row in STORE._db.all(KIND):
        if secrets.compare_digest(str(row.get("code") or ""), code):
            found = row
    return found


# ---- the month ---------------------------------------------------------------


def parse_month(text: str) -> date:
    try:
        year, month = (int(part) for part in text.split("-"))
        return date(year, month, 1)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422,
                            detail="Choose a month, written YYYY-MM.")


def _next_month(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1)


def _at(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


# ---- what a report says ------------------------------------------------------


def _overlap(a: datetime, b: datetime, lo: datetime, hi: datetime) -> float:
    """Seconds of [a, b) inside [lo, hi)."""
    start, end = max(a, lo), min(b, hi)
    return max(0.0, (end - start).total_seconds())


# No percentage online for less than an hour watched (see build).
MIN_SECONDS_FOR_PERCENT = 3600.0


def _floor1(value: float) -> float:
    """Down to one decimal, so 99.97% never prints as 100%.

    Not past a rounding error (3.40.0): 100 x 12,060,016 / 12,060,016 came
    out a hair under 100 in floating point, and the demo's report said
    99.9% online beside twenty units each 100% with no time offline."""
    return math.floor(value * 10 + 1e-9) / 10.0


def _minutes(seconds: float) -> int:
    return int(round(seconds / 60.0))


def _sensor_watch(sensor, gaps: List[Tuple[datetime, datetime]], start: datetime,
                  end: datetime, now: datetime) -> Dict[str, Any]:
    """Time watched, time offline and the longest silence, for one sensor.

    Offline is the product's own rule: a sensor not heard from for
    SENSOR_OFFLINE_AFTER_MINUTES. So in a silence of two hours, the first
    half hour counts as online and the rest as offline.
    """
    spans = list(gaps)
    newest = max((r.recorded_at for r in STORE.readings_for(sensor.sensor_id)),
                 default=None)
    contacts = [t for t in (newest, sensor.last_seen) if t is not None]
    silent_from = max(contacts) if contacts else sensor.registered_at
    if silent_from is not None and silent_from < end:
        spans.append((silent_from, max(now, end)))
    grace = timedelta(minutes=SENSOR_OFFLINE_AFTER_MINUTES)
    watched = max(0.0, (end - start).total_seconds())
    offline = sum(_overlap(a + grace, b, start, end) for a, b in spans)
    longest: Optional[Tuple[float, datetime, datetime, bool]] = None
    for a, b in spans:
        seconds = _overlap(a, b, start, end)
        if seconds >= GAP_RECORD_MINUTES * 60 and (longest is None or seconds > longest[0]):
            longest = (seconds, max(a, start), min(b, end), b >= end and a == silent_from)
    return {"watched": watched, "offline": min(watched, offline), "longest": longest}


def _alert_role(tenant: Tenant, acknowledged_by: Optional[str]) -> Optional[str]:
    from omniarmor import role_for

    role = role_for(tenant, acknowledged_by)
    return _ROLE_WORDS.get(role or "", "On-call person") if role else None


def _ordinal(n: int) -> str:
    """1 -> "1st", 2 -> "2nd", 11 -> "11th"."""
    tail = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{tail}"


def _who_is_alerted(tenant: Tenant, site_id: Optional[str],
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    """The on-call list and the team, by role: never a name or a number.

    Phone calls are mentioned only where the plan includes them. This is
    the page an insurer reads, and it used to promise "the phone calls go
    down the list" for a Starter account that never places one.

    Who a real alert reaches comes from the same place the Team tab's "A
    real alert right now" does (3.42.0): STORE.sms_recipients and
    STORE.voice_ladder, so a rotation and quiet hours count. The report
    said "a text to all 3 people on call" while the Team tab said "Text to
    Morgan Hale", the person whose turn it was.
    """
    import oncall

    now = now or utc_now()
    calls = bool(tenant.entitlements().get("voice_escalation"))
    roster = [c for c in STORE.contacts_for(tenant.tenant_id) if c.active]
    if site_id:
        roster = [c for c in roster if c.site_id in (None, site_id)]
    on_call = []
    for position, contact in enumerate(roster, start=1):
        voice = contact.receives_voice and calls
        gets = ("texts and phone calls" if contact.receives_sms and voice
                else "texts" if contact.receives_sms
                else "phone calls" if voice else "nothing at present")
        on_call.append({
            "position": position,
            "role": "On-call person",
            "gets": gets,
            "covers": "this location" if contact.site_id else "every location",
        })
    # The ladder as it runs (3.41.0), in steps, still by position only:
    # "1. On-call person" three times said nothing about what happens.
    # Live, as the dispatcher would run it now (3.42.0): with a rotation,
    # only whoever's turn it is, and "all N" only when there is none.
    def real(people: List[Any]) -> List[Any]:
        return [c for c in people if c.contact_id != "fallback"]

    texted = real(STORE.sms_recipients(tenant, site_id, now=now)) if roster else []
    ladder = real(STORE.voice_ladder(tenant, site_id, now=now)) if roster and calls else []
    turns = [r for r in oncall.rotations_for(STORE._db, tenant.tenant_id)
             if r.members and (r.site_id is None or r.site_id == site_id or site_id is None)]
    rota = bool(turns) and any(c.contact_id in {m for r in turns for m in r.members} for c in texted)
    steps: List[str] = []
    if texted:
        who = ("the person whose turn it is in the on-call rotation" if rota and len(texted) == 1
               else f"the {len(texted)} people on call now (a rotation decides whose turn it is)" if rota
               else "the one person on call" if len(texted) == 1
               else f"all {len(texted)} people on call")
        steps.append(f"At once: a text to {who}.")
    for n, _contact in enumerate(ladder, start=1):
        place = ("person on call" if rota and len(ladder) == 1
                 else f"{_ordinal(n)} person on the list")
        steps.append(
            f"After {VOICE_ESCALATION_GRACE_MINUTES} minutes with nobody acknowledging: "
            f"a phone call to the {place}." if n == 1 else
            f"If that call does not get through: the {place}.")
    if texted and not calls:
        steps.append("No phone calls: this plan sends texts only.")
    team: Dict[str, int] = {}
    for user in STORE.users_for(tenant.tenant_id):
        if user.disabled:
            continue
        team[user.role] = team.get(user.role, 0) + 1
    return {
        "on_call": on_call,
        "steps": steps,
        # With nobody on the list, alerts go to the business's own number.
        "falls_back_to_main_number": not on_call,
        "team": [{"role": _TEAM_WORDS.get(role, role.title()), "count": count}
                 for role, count in sorted(team.items(), key=lambda kv: kv[0])
                 if role in _TEAM_WORDS],
        "calls": calls,
        "rotation": rota,
        "rule": (("Whoever is on call gets a text at once: the on-call "
                  "rotation decides who, and it changes at each handover. "
                  if rota else "Every person on call gets a text at once. ")
                 + (("If nobody acknowledges, the person on call is phoned."
                     if len(ladder) == 1 and rota else
                     "If nobody acknowledges, the phone calls go down the list "
                     "until somebody does.") if calls else
                    "This plan sends texts only; it does not include phone calls.")),
    }


def _zone_name(tenant: Tenant, site_id: Optional[str]) -> str:
    """The IANA name of the business's clock, or "UTC" when the account
    names none."""
    import oncall
    from sitewide import site_timezone

    name = site_timezone(tenant, site_id, "UTC")
    try:
        oncall.zone(name)
    except ValueError:
        return "UTC"
    return name


def build(tenant: Tenant, site_id: Optional[str], first: date,
          now: Optional[datetime] = None) -> Dict[str, Any]:
    """The report's contents, frozen as they are now."""
    from inspector import _breach, _readings
    from omniarmor import _person_words, safe_text

    now = now or utc_now()
    period_start = _at(first)
    month_end = _at(_next_month(first))
    period_end = min(month_end, now)
    words = _person_words(tenant)
    unit_label = tenant.temperature_unit

    site = STORE.get_site(site_id) if site_id else None
    sensors = [s for s in STORE.sensors_for(tenant.tenant_id)
               if (site is None or s.site_id == site.site_id)
               and (s.registered_at is None or s.registered_at < period_end)]
    sensors.sort(key=lambda s: (s.location_name or "", s.sensor_id))

    since = STORE.gap_log_since()
    gap_rows: Dict[str, List[Tuple[datetime, datetime]]] = {}
    for row in STORE.gaps_for(tenant.tenant_id):
        a, b = _parse(row.get("start")), _parse(row.get("end"))
        if a is not None and b is not None:
            gap_rows.setdefault(str(row.get("sensor_id")), []).append((a, b))

    incidents = [i for i in STORE.incidents_for(tenant.tenant_id)
                 if not i.site_wide and period_start <= i.opened_at < period_end]

    units: List[Dict[str, Any]] = []
    alerts: List[Dict[str, Any]] = []
    total_watched = total_offline = 0.0
    longest_gap: Optional[Dict[str, Any]] = None
    for index, sensor in enumerate(sensors, start=1):
        name = safe_text(sensor.location_name, words) or f"Unit {index}"
        start = max(period_start, since,
                    sensor.registered_at or period_start)
        watch = (_sensor_watch(sensor, gap_rows.get(sensor.sensor_id, []),
                               start, period_end, now)
                 if start < period_end else None)
        mine = sorted((i for i in incidents if i.sensor_id == sensor.sensor_id),
                      key=lambda i: i.opened_at)
        online = None
        gap_minutes = None
        if watch is not None and watch["watched"] > 0:
            total_watched += watch["watched"]
            total_offline += watch["offline"]
            # A percentage of almost nothing is not a figure: "100% online"
            # beside "0 hours watched" was the report for a unit added that
            # morning. Under an hour, the hours are the honest number.
            if watch["watched"] >= MIN_SECONDS_FOR_PERCENT:
                online = _floor1(100.0 * (watch["watched"] - watch["offline"]) / watch["watched"])
            if watch["longest"] is not None:
                seconds, g_from, g_to, ongoing = watch["longest"]
                gap_minutes = _minutes(seconds)
                if longest_gap is None or seconds > longest_gap["_seconds"]:
                    longest_gap = {"_seconds": seconds, "unit": name,
                                   "started_at": iso(g_from), "ended_at": iso(g_to),
                                   "minutes": gap_minutes, "still_going": ongoing}
        units.append({
            "name": name,
            "watched_hours": round((watch or {}).get("watched", 0.0) / 3600.0, 1),
            "online_percent": online,
            "longest_gap_minutes": gap_minutes,
            "alerts": len(mine),
        })
        above, below = sensor.bounds()
        for incident in mine:
            end = incident.resolved_at or now
            readings = _readings(sensor, incident.opened_at - timedelta(minutes=1),
                                 end + timedelta(minutes=1), [incident])
            worst = _breach(incident, readings, unit_label, above, below)["worst"]
            minutes = None
            if incident.acknowledged_at is not None:
                minutes = max(0, int(round(
                    (incident.acknowledged_at - incident.opened_at).total_seconds() / 60)))
            alerts.append({
                "unit": name,
                "opened_at": iso(incident.opened_at),
                "worst": worst,
                "limit_above": display_temperature(above, unit_label) if above is not None else None,
                "limit_below": display_temperature(below, unit_label) if below is not None else None,
                "minutes_to_acknowledge": minutes,
                "acknowledged_by_role": _alert_role(tenant, incident.acknowledged_by),
                "closed_at": iso(incident.resolved_at),
            })
    alerts.sort(key=lambda a: a["opened_at"] or "")
    times = [a["minutes_to_acknowledge"] for a in alerts
             if a["minutes_to_acknowledge"] is not None]
    if longest_gap is not None:
        longest_gap.pop("_seconds", None)

    covered = since <= period_start
    location_name = (safe_text(site.name, words) or "One location") if site else "Every location"
    # How many locations an every-location report covers (3.41.0): it said
    # "20 at this location".
    places = len({s.site_id for s in sensors if s.site_id}) if site is None else 1
    return {
        "format": 1,
        "business": tenant.company_name,
        "location": {"name": location_name, "scope": "one" if site else "all", "locations": places},
        "month": first.strftime("%Y-%m"),
        "month_label": first.strftime("%B %Y"),
        "period_start": iso(period_start),
        "period_end": iso(period_end),
        "month_complete": period_end >= month_end,
        "issued_at": iso(now),
        # The business's own clock (3.42.0), for showing the times above
        # and below: they stay UTC here, and the page writes them in this
        # zone, labelled. From the on-call rotation (sitewide.site_timezone).
        "timezone": _zone_name(tenant, site.site_id if site else None),
        "temperature_unit": unit_label,
        "gap_threshold_minutes": GAP_RECORD_MINUTES,
        "summary": {
            "sensors_watched": len(sensors),
            "online_percent": (_floor1(100.0 * (total_watched - total_offline) / total_watched)
                               if total_watched >= MIN_SECONDS_FOR_PERCENT else None),
            # The hours behind the percentage, all sensors together, so the
            # figure is never read without what it is a percentage of.
            "hours_watched": round(total_watched / 3600.0, 1),
            "hours_online": round(max(0.0, total_watched - total_offline) / 3600.0, 1),
            "alerts": len(alerts),
            "alerts_acknowledged": len(times),
            "alerts_not_acknowledged": len(alerts) - len(times),
            "median_minutes_to_acknowledge": (
                round(float(statistics.median(times)), 1) if times else None),
            "slowest_minutes_to_acknowledge": max(times) if times else None,
            "longest_gap_minutes": longest_gap["minutes"] if longest_gap else None,
        },
        "sensors": units,
        "alerts": alerts,
        "longest_gap": longest_gap,
        "alerted": _who_is_alerted(tenant, site.site_id if site else None, now),
        "coverage": {
            "gaps_recorded_since": iso(since),
            "covers_the_whole_period": covered,
            "note": ("" if covered else
                     f"Gaps in data have been written down since "
                     f"{since.strftime('%b')} {since.day}, {since.year}. Time before that is not "
                     "counted in the figures above."),
        },
        "how_measured": (
            f"A sensor counts as online when it has reported in the last "
            f"{SENSOR_OFFLINE_AFTER_MINUTES} minutes. Every silence of "
            f"{GAP_RECORD_MINUTES} minutes or more is written down when it "
            "ends, so the time online and the longest gap do not depend on "
            "how many readings are kept. Alerts are timed from when they "
            "opened to when somebody acknowledged them."),
    }


# ---- storing and showing -----------------------------------------------------


def _link(code: str) -> Dict[str, str]:
    from mail import PUBLIC_BASE_URL

    path = f"/verify/{pretty_code(code)}"
    return {"path": path, "url": f"{PUBLIC_BASE_URL}{path}" if PUBLIC_BASE_URL else ""}


def make(tenant: Tenant, site_id: Optional[str], first: date,
         now: Optional[datetime] = None) -> Dict[str, Any]:
    """Build, freeze, hash and seal a report, and keep it."""
    body = build(tenant, site_id, first, now)
    # Numbered within this business (PRF-2-QHKX), not across every
    # customer: the id is on a page an insurer reads.
    report_id = STORE.account_id(
        "PRF", tenant.tenant_id,
        lambda: ((r["report_id"], r.get("tenant_id")) for r in STORE._db.all(KIND)),
        lambda candidate: STORE._db.get(KIND, candidate) is not None)
    code = new_code()
    while by_code(code) is not None:  # pragma: no cover - 80 bits
        code = new_code()
    source = "setting" if PROOF_REPORT_KEY else "stored"
    key = _key(source)
    assert key is not None
    digest = content_hash(body)
    row = {
        "report_id": report_id,
        "tenant_id": tenant.tenant_id,
        "site_id": site_id,
        "code": code,
        "created_at": iso(now or utc_now()),
        "revoked_at": None,
        "body": body,
        "content_hash": digest,
        "key_source": source,
        "seal": seal(report_id, code, digest, key),
    }
    STORE._db.put(KIND, report_id, row)
    return row


def owner_view(row: Dict[str, Any], with_body: bool = True) -> Dict[str, Any]:
    body = row.get("body") or {}
    out: Dict[str, Any] = {
        "report_id": row["report_id"],
        "month": body.get("month"),
        "month_label": body.get("month_label"),
        "location": (body.get("location") or {}).get("name"),
        "created_at": row.get("created_at"),
        "verify_code": pretty_code(str(row.get("code") or "")),
        "link": _link(str(row.get("code") or "")),
        "fingerprint": fingerprint(str(row.get("content_hash") or "")),
        "state": check(row),
        "revoked_at": row.get("revoked_at"),
        "summary": body.get("summary"),
    }
    out["state_words"] = STATE_WORDS[out["state"]]
    if with_body:
        out["report"] = body
    return out


def public_view(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What the public page may show: the business name and the numbers.

    Never a unit or location name, a person, a role list or a reading.
    """
    if row is None:
        return {"state": "unknown", "words": STATE_WORDS["unknown"]}
    state = check(row)
    out: Dict[str, Any] = {"state": state, "words": STATE_WORDS[state]}
    if state != "genuine":
        return out
    body = row["body"]
    summary = body.get("summary") or {}
    out.update({
        "business": body.get("business"),
        "month_label": body.get("month_label"),
        "period_start": body.get("period_start"),
        "period_end": body.get("period_end"),
        "month_complete": body.get("month_complete"),
        "issued_at": body.get("issued_at"),
        "covers": "one location" if (body.get("location") or {}).get("scope") == "one"
                  else "every location",
        "fingerprint": fingerprint(str(row.get("content_hash") or "")),
        "covers_the_whole_period": (body.get("coverage") or {}).get("covers_the_whole_period"),
        "summary": {key: summary.get(key) for key in (
            "sensors_watched", "online_percent", "hours_watched", "hours_online",
            "alerts", "alerts_acknowledged",
            "alerts_not_acknowledged", "median_minutes_to_acknowledge",
            "longest_gap_minutes")},
    })
    return out


def verify(code: str, request: Request) -> Dict[str, Any]:
    """The public check, rate limited, for the page and the API alike."""
    VERIFY_LIMIT.check(request)
    return public_view(by_code(code))


def _mins(value: Any) -> str:
    if value is None:
        return "—"
    # Half up, as the printed report (JavaScript) rounds, so 8.5 minutes is
    # "9 min" on both the paper and this page.
    value = int(math.floor(float(value) + 0.5))
    if value < 60:
        return f"{value} min"
    hours, minutes = divmod(value, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def _hours(value: Any) -> str:
    """"3,353.6", as the printed report writes hours (3.42.0), never
    "3353.6"; a whole number without its ".0"."""
    text = f"{float(value):,.1f}"
    return text[:-2] if text.endswith(".0") else text


def online_words(summary: Dict[str, Any]) -> str:
    """"Time online" with the hours it is a share of, never a bare 100%."""
    hours = summary.get("hours_watched")
    pct = summary.get("online_percent")
    if pct is None:
        if hours is None:
            return "—"
        return f"Not enough yet: {_hours(hours)} hours watched so far"
    if hours is None:  # a report made before the hours were written down
        return f"{pct:g}%"
    return f"{pct:g}% of {_hours(hours)} hours watched"


def render_verify_html(result: Dict[str, Any], code: str) -> str:
    """The public page's result, as escaped HTML."""
    e = html.escape
    state = result["state"]
    shown = pretty_code(normalise_code(code))[:23] if code else ""
    head = (f'<div class="verdict v-{e(state)}"><p class="verdict-word">'
            f'{e(VERDICT[state])}</p><p>{e(result["words"])}</p>'
            f'<p class="code">Code {e(shown)}</p></div>')
    if state != "genuine":
        return head
    s = result["summary"]
    online = online_words(s)
    gap = ("under " + str(GAP_RECORD_MINUTES) + " min"
           if s["longest_gap_minutes"] is None else _mins(s["longest_gap_minutes"]))
    acknowledged = (f'{s["alerts_acknowledged"]} of {s["alerts"]}'
                    if s["alerts"] else "No alerts")
    rows = [
        ("Business", result["business"]),
        ("Month", result["month_label"] + ("" if result["month_complete"] else " (so far)")),
        ("Covers", result["covers"]),
        ("Sensors watched", str(s["sensors_watched"])),
        ("Time online", online),
        ("Temperature alerts", str(s["alerts"])),
        ("Acknowledged", acknowledged),
        ("Typical time to acknowledge", _mins(s["median_minutes_to_acknowledge"])),
        ("Longest gap in data", gap),
        ("Report fingerprint", result["fingerprint"]),
    ]
    table = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in rows)
    note = ("" if result.get("covers_the_whole_period") else
            '<p class="small">Part of this month came before gaps in data were '
            "written down, so the time online covers only the rest of it.</p>")
    return (head + f'<table class="facts">{table}</table>' + note +
            '<p class="small">The fingerprint is printed at the foot of the '
            "report. If it matches, the numbers on the paper are the ones "
            "shown here.</p>")


# ---- routes ------------------------------------------------------------------


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: str = Field(..., pattern=r"^\d{4}-\d{2}$",
                       description="The month to report on, YYYY-MM.")
    site_id: Optional[str] = Field(
        None, max_length=40,
        description="One location. Leave it out for every location.")


def _own(report_id: str, tenant: Tenant) -> Dict[str, Any]:
    row = STORE._db.get(KIND, (report_id or "").strip())
    if row is None or row.get("tenant_id") != tenant.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such report for this business.")
    return row


@router.get("")
def list_reports(owner: User = Depends(require_role("owner"))):
    """This business's reports, newest first, and the locations it has."""
    tenant = tenant_of(owner)
    rows = [r for r in STORE._db.all(KIND) if r.get("tenant_id") == tenant.tenant_id]
    rows.sort(key=lambda r: (r.get("created_at") or "", r["report_id"]), reverse=True)
    return {
        "reports": [owner_view(r, with_body=False) for r in rows],
        "locations": [{"site_id": s.site_id, "name": s.name}
                      for s in STORE.sites_for(tenant.tenant_id)],
        "earliest_month": tenant.activated_at.strftime("%Y-%m"),
        "note": ("Only you can see this list. A report is seen by nobody "
                 "until you hand someone its link, and you can switch a link "
                 "off at any time."),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def create_report(payload: ReportRequest, owner: User = Depends(require_role("owner"))):
    """Make a proof of monitoring report for one location and one month."""
    from contracts import refuse_if_behind

    tenant = tenant_of(owner)
    refuse_if_behind(tenant, "A proof of monitoring report")
    first = parse_month(payload.month)
    now = utc_now()
    if first > now.date().replace(day=1):
        raise HTTPException(status_code=422,
                            detail="That month has not started yet.")
    if first < tenant.activated_at.date().replace(day=1):
        raise HTTPException(status_code=422,
                            detail="That month is before this business joined.")
    site_id = (payload.site_id or "").strip() or None
    if site_id:
        site = STORE.get_site(site_id)
        if site is None or site.tenant_id != tenant.tenant_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="No such location for this business.")
    row = make(tenant, site_id, first, now)
    if not row["body"]["summary"]["sensors_watched"]:
        STORE._db.delete(KIND, row["report_id"])
        raise HTTPException(status_code=422,
                            detail="There were no sensors at that location in that month.")
    write_audit(tenant, owner, "proof_report.created",
                f"Proof of monitoring report {row['report_id']} for "
                f"{row['body']['location']['name']}, {row['body']['month_label']}.")
    logger.info("Proof report %s made for %s.", row["report_id"], tenant.tenant_id)
    return owner_view(row)


@router.get("/verify/{code}")
def verify_report(code: str, request: Request):
    """Whether a report is genuine and unchanged. Public; rate limited.

    Answers with the business name and the headline numbers, never a
    reading, a person or a location's name.
    """
    result = verify(code, request)
    codes = {"genuine": 200, "unchecked": 200, "changed": 409,
             "revoked": 410, "unknown": 404}
    return JSONResponse(status_code=codes[result["state"]], content=result)


@router.get("/{report_id}")
def read_report(report_id: str, owner: User = Depends(require_role("owner"))):
    """One report in full, to print."""
    return owner_view(_own(report_id, tenant_of(owner)))


@router.post("/{report_id}/revoke")
def revoke_report(report_id: str, owner: User = Depends(require_role("owner"))):
    """Switch a report's link off. Whoever opens it is told so."""
    tenant = tenant_of(owner)
    row = _own(report_id, tenant)
    if not row.get("revoked_at"):
        row["revoked_at"] = iso(utc_now())
        STORE._db.put(KIND, row["report_id"], row)
        write_audit(tenant, owner, "proof_report.revoked",
                    f"Switched off the link to proof report {row['report_id']}.")
    return {**owner_view(row, with_body=False),
            "message": "Switched off. The link now says so to anyone who opens it."}
