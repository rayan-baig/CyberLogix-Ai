"""OmniArmor's side of the Kitchen Safety Bundle, ported, for the tests.

`validate` below is OmniArmor's own `validate` from
omniarmor_app/connections.py, copied with its limits and lists, minus the
Flask parts. The fake server answers the way that module's
`cyberlogix_daily` answers: the key, the content type, the body size,
the JSON, the rate limit, the validation, and "unchanged" for the same
report twice. Every report the tests send goes through it, so a field
this app builds wrong fails here.

Drift is caught two ways: tests/test_omniarmor_bundle.py checks that this
copy's lists and limits are omniarmor.py's, and, where OmniArmor's code
sits beside this repository (a developer machine with both checked out),
that they are still OmniArmor's own.
"""

import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit

KEY_PREFIX = "clx_"

MAX_BODY = 32 * 1024
MAX_BREACHES = 50
MAX_UNITS = 500
MAX_TEXT = 60
MAX_URL = 500
LOWEST_TEMP, HIGHEST_TEMP = -100, 250
OLDEST_DAYS = 31
LONGEST_BREACH = timedelta(hours=48)

UNITS_OF_MEASURE = {"°F": "°F", "F": "°F", "°C": "°C", "C": "°C"}
ROLES = ("owner", "manager", "chef", "shift lead", "on-call staff", "kitchen staff", "maintenance", "technician")
ACTIONS = {
    "moved_food": "food moved to another unit",
    "discarded_food": "food discarded",
    "checked_food": "food temperature checked",
    "closed_door": "door closed",
    "adjusted_unit": "unit settings adjusted",
    "called_technician": "technician called",
    "defrost_cycle": "defrost cycle, no action needed",
    "monitoring": "being monitored",
    "none": "no action yet",
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class PayloadError(ValueError):
    def __init__(self, problems):
        super().__init__("; ".join(f"{f}: {p}" for f, p in problems))
        self.problems = problems


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(value, field, problems, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        problems.append((field, "required text" if required else "text or null"))
        return None
    value = value.strip()
    if len(value) > MAX_TEXT:
        problems.append((field, f"at most {MAX_TEXT} characters"))
    elif any(ord(c) < 32 or ord(c) == 127 for c in value):
        problems.append((field, "no line breaks or control characters"))
    return value


def _temperature(value, field, problems):
    if not _is_number(value):
        problems.append((field, "a number"))
        return None
    if not LOWEST_TEMP <= value <= HIGHEST_TEMP:
        problems.append((field, f"between {LOWEST_TEMP} and {HIGHEST_TEMP}"))
        return None
    return round(float(value), 1)


def _uom(value, field, problems):
    if value not in UNITS_OF_MEASURE:
        problems.append((field, 'one of "°F", "°C", "F" or "C"'))
        return None
    return UNITS_OF_MEASURE[value]


def _moment(value, field, problems, required=True):
    if value is None and not required:
        return None
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) and len(value) <= 40 else None
    except ValueError:
        moment = None
    if moment is None or "T" not in value or moment.tzinfo is None:
        problems.append((field, "a date and time with its UTC offset, like 2026-11-12T02:10:00-05:00"))
        return None
    return moment


def _log_url(value, problems):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_URL or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        problems.append(("log_url", f"an https link of at most {MAX_URL} characters, or null"))
        return None
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc:
        problems.append(("log_url", "an https link, with no user name or password in it"))
        return None
    return value


def validate(body, today):
    """OmniArmor's validate, unchanged in substance."""
    if not isinstance(body, dict):
        raise PayloadError([("body", "a JSON object")])
    problems = []
    day = body.get("date")
    if not isinstance(day, str) or not DATE_RE.match(day):
        problems.append(("date", "required, as YYYY-MM-DD"))
        day = None
    else:
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            parsed = None
            problems.append(("date", "not a real date"))
        if parsed is not None and not today - timedelta(days=OLDEST_DAYS) <= parsed <= today + timedelta(days=1):
            problems.append(("date", f"from {OLDEST_DAYS} days ago to tomorrow, in the workspace's timezone"))
    site = _text(body.get("site"), "site", problems, required=False)

    total, in_range = body.get("units_total"), body.get("units_in_range_all_day")
    if not _is_int(total) or not 1 <= total <= MAX_UNITS:
        problems.append(("units_total", f"a whole number from 1 to {MAX_UNITS}"))
        total = None
    if not _is_int(in_range) or in_range < 0 or (total is not None and in_range > total):
        problems.append(("units_in_range_all_day", "a whole number from 0 to units_total"))
        in_range = None

    breaches_in = body.get("breaches")
    breaches = []
    if not isinstance(breaches_in, list):
        problems.append(("breaches", "a list (empty when there were none)"))
        breaches_in = []
    elif len(breaches_in) > MAX_BREACHES:
        problems.append(("breaches", f"at most {MAX_BREACHES}"))
        breaches_in = []
    for n, b in enumerate(breaches_in):
        at = f"breaches[{n}]"
        if not isinstance(b, dict):
            problems.append((at, "an object"))
            continue
        before = len(problems)
        unit = _text(b.get("unit"), f"{at}.unit", problems)
        started = _moment(b.get("started_at"), f"{at}.started_at", problems)
        ended = _moment(b.get("ended_at"), f"{at}.ended_at", problems, required=False)
        peak = _temperature(b.get("peak"), f"{at}.peak", problems)
        limit = _temperature(b.get("limit"), f"{at}.limit", problems)
        uom = _uom(b.get("unit_of_measure"), f"{at}.unit_of_measure", problems)
        role = b.get("acknowledged_by_role")
        if role is not None and role not in ROLES:
            problems.append((f"{at}.acknowledged_by_role", "null or one of: " + ", ".join(ROLES)))
        action = b.get("action")
        if action is not None and action not in ACTIONS:
            problems.append((f"{at}.action", "null or one of: " + ", ".join(ACTIONS)))
        if started is not None and day is not None and len(problems) == before:
            try:
                start_day = started.date()
                report_day = date.fromisoformat(day)
            except ValueError:
                start_day = report_day = None
            if report_day and not report_day - timedelta(days=1) <= start_day <= report_day + timedelta(days=1):
                problems.append((f"{at}.started_at", "on the report's date (a day either side at most)"))
            if ended is not None and not timedelta(0) <= ended - started <= LONGEST_BREACH:
                problems.append((f"{at}.ended_at", "after started_at, and at most 48 hours later"))
        if len(problems) == before:
            breaches.append({"unit": unit, "started_at": started.isoformat(),
                             "ended_at": ended.isoformat() if ended else None, "peak": peak, "limit": limit,
                             "unit_of_measure": uom, "acknowledged_by_role": role, "action": action})

    warmest, warmest_uom = body.get("warmest_reading"), body.get("unit_of_measure")
    clean = not breaches_in and total is not None and in_range == total
    if warmest is None and warmest_uom is None:
        if clean:
            problems.append(("warmest_reading", "required when every unit stayed in range all day (it is the reading"
                                                " the temperature check gets)"))
    else:
        warmest = _temperature(warmest, "warmest_reading", problems)
        warmest_uom = _uom(warmest_uom, "unit_of_measure", problems)
    if breaches_in and total is not None and in_range is not None:
        if in_range == total:
            problems.append(("units_in_range_all_day", "less than units_total when there are breaches"))
        elif len({b["unit"].casefold() for b in breaches}) > total - in_range:
            problems.append(("breaches", "more units with breaches than units out of range"))

    log_url = _log_url(body.get("log_url"), problems)
    if problems:
        raise PayloadError(problems)
    return {"date": day, "site": site, "units_total": total, "units_in_range_all_day": in_range,
            "breaches": breaches, "warmest_reading": warmest, "unit_of_measure": warmest_uom, "log_url": log_url}


def fingerprint(report):
    return hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class FakeOmniArmor:
    """POST /api/connect/cyberlogix/daily, as OmniArmor answers it.

    Use it as omniarmor.TRANSPORT. `script` is a list of (status, headers)
    to answer with before answering properly, for retries; `today` is the
    workspace's date.
    """

    def __init__(self, key, today, script=None, host="https://omniarmor.example.com"):
        self.key = key
        self.today = today
        self.script = list(script or [])
        self.host = host
        self.calls = []          # every request, as received
        self.accepted = []       # the validated reports that changed something
        self.reports = {}        # date -> fingerprint, as OmniArmor keeps them

    def __call__(self, url, key, body):
        self.calls.append({"url": url, "key": key, "body": body})
        assert url == self.host + "/api/connect/cyberlogix/daily", url
        if self.script:
            status, headers = self.script.pop(0)
            return status, headers, json.dumps({"ok": False, "error": f"http_{status}"}).encode()
        if key != self.key or not key.startswith(KEY_PREFIX):
            return 401, {"WWW-Authenticate": 'Bearer realm="omniarmor"'}, json.dumps(
                {"ok": False, "error": "unauthorized", "message": "wrong key"}).encode()
        if len(body) > MAX_BODY:
            return 413, {}, json.dumps({"ok": False, "error": "payload_too_large"}).encode()
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return 400, {}, json.dumps({"ok": False, "error": "invalid_json"}).encode()
        try:
            report = validate(parsed, self.today)
        except PayloadError as exc:
            return 422, {}, json.dumps({
                "ok": False, "error": "invalid_payload",
                "message": "Some fields are missing or out of range; nothing was changed.",
                "errors": [{"field": f, "problem": p} for f, p in exc.problems]}).encode()
        digest = fingerprint(report)
        if self.reports.get(report["date"]) == digest:
            result = "unchanged"
        else:
            self.reports[report["date"]] = digest
            self.accepted.append(report)
            clean = not report["breaches"] and report["units_in_range_all_day"] == report["units_total"]
            result = "ticked" if clean else "note"
        return 200, {}, json.dumps({
            "ok": True, "date": report["date"], "matched_checks": 1,
            "ticked": int(result == "ticked"), "noted": int(result == "note"),
            "unchanged": int(result == "unchanged"),
            "checks": [{"industry": "RestoArmor", "check": "cold_holding",
                        "title": "Cold holding at 41°F or below", "result": result}]}).encode()
