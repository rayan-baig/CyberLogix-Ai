"""What broke, how often, and when it last happened.

Asked to make this run itself, so that a bug nobody is watching does not
need somebody to notice it. Three things stood between the application
and that, and this file is the first.

An unhandled error used to go to stdout. In a container stdout is gone
at the next restart, so the sequence was: something breaks at 2am, the
process logs a traceback, the container restarts, and the only evidence
that anything happened at all is a customer asking why. Nothing in the
product could answer.

So faults are written down, deduplicated by fingerprint, and counted.
The same traceback a thousand times is one row saying a thousand, not a
thousand rows pushing everything else out of a ring buffer -- which is
the failure mode of every "recent errors" list that has ever been
written, and it hides the rare fault behind the noisy one.

What this does NOT do is fix anything. No program repairs its own logic.
What it can do is make sure the next bug arrives as a dated report with
a traceback and a count, instead of as a mystery.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import traceback as tb
from typing import Any, Dict, List

from store import STORE, iso, utc_now

logger = logging.getLogger("cyberlogix.faults")

KIND = "fault"

# Distinct faults kept. Bounded because this is a self-running deployment
# nobody prunes by hand; deduplication means the bound is on *kinds* of
# failure rather than on occurrences, so a thousand repeats of one bug
# never evict a different bug that happened once.
MAX_DISTINCT = 200


def fingerprint(source: str, exc: BaseException) -> str:
    """One id per kind-of-failure, not per occurrence.

    The last frame inside this application, plus the exception type. The
    message is deliberately excluded: "sensor FRIDGE-2 not found" and
    "sensor FRIDGE-9 not found" are one bug, and counting them as two
    hundred is how a list of recent errors stops being readable.
    """
    frames = tb.extract_tb(exc.__traceback__)
    ours = [f for f in frames if "/site-packages/" not in (f.filename or "")]
    where = ours[-1] if ours else (frames[-1] if frames else None)
    site = f"{where.filename}:{where.lineno}" if where else "unknown"
    seed = f"{source}|{type(exc).__name__}|{site}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


# What a provider's own error names: the number Twilio refused, the address
# a mail host would not take. The list has no business it belongs to and
# is kept until somebody clears it, so a person's details written into it
# outlive that business deleting everything. The message and traceback
# say what went wrong just as well without them.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+\d[\d ().-]{5,}\d|(?<![\w.])\d{10,15}(?![\w.])")


def redact(text: str) -> str:
    """`text` without phone numbers or email addresses in it."""
    return _PHONE.sub("<number>", _EMAIL.sub("<address>", text or ""))


def record(source: str, exc: BaseException, context: str = "") -> Dict[str, Any]:
    """Write a fault down, or count another occurrence of a known one."""
    try:
        return _record(source, exc, context)
    except Exception:  # noqa: BLE001 - recording a fault must never raise
        # If this raised it would replace the real error with its own, and
        # the thing being diagnosed would disappear.
        logger.exception("Could not record a fault from %s.", source)
        return {}


def _record(source: str, exc: BaseException, context: str) -> Dict[str, Any]:
    ident = fingerprint(source, exc)
    now = utc_now()
    detail = clean("".join(
        tb.format_exception(type(exc), exc, exc.__traceback__)
    ))[-4000:]
    message = clean(str(exc))

    with STORE._lock:
        row = STORE._db.get(KIND, ident)
        if row is None:
            _prune_locked()
            row = {
                "fault_id": ident,
                "source": source,
                "exception": type(exc).__name__,
                "message": message[:500],
                "context": clean(context)[:500],
                "traceback": detail,
                "first_seen": iso(now),
                "last_seen": iso(now),
                "seen": 1,
            }
        else:
            row = {
                **row,
                "last_seen": iso(now),
                "seen": int(row.get("seen", 0)) + 1,
                # Keep the newest traceback: the oldest one is from a
                # version of the code that may no longer exist.
                "traceback": detail,
                "message": message[:500],
            }
        STORE._db.put(KIND, ident, row)

    logger.error(
        "Fault %s in %s (%s occurrence(s)): %s: %s",
        ident, source, row["seen"], type(exc).__name__, message,
    )
    return row


def _prune_locked() -> None:
    """Drop the least recently seen faults once the ring is full."""
    rows = STORE._db.all(KIND)
    if len(rows) < MAX_DISTINCT:
        return
    rows.sort(key=lambda r: r.get("last_seen") or "")
    for row in rows[: len(rows) - MAX_DISTINCT + 1]:
        STORE._db.delete(KIND, row["fault_id"])


def recent(limit: int = 50) -> List[Dict[str, Any]]:
    """Distinct faults, most recently seen first."""
    rows = STORE._db.all(KIND)
    rows.sort(key=lambda r: r.get("last_seen") or "", reverse=True)
    return rows[:limit]


def clear(fault_id: str) -> bool:
    """Mark one as dealt with. It comes straight back if it happens again."""
    if STORE._db.get(KIND, fault_id) is None:
        return False
    STORE._db.delete(KIND, fault_id)
    return True


def status() -> Dict[str, Any]:
    """Enough for a health panel and a line in the daily digest."""
    rows = recent(limit=MAX_DISTINCT)
    occurrences = sum(int(r.get("seen", 0)) for r in rows)
    return {
        "distinct": len(rows),
        "occurrences": occurrences,
        "newest": rows[0]["last_seen"] if rows else None,
        "worst": max(rows, key=lambda r: int(r.get("seen", 0)))["source"]
        if rows else None,
        "note": (
            "Deduplicated by where it was raised, so a thousand repeats of "
            "one bug is one row saying a thousand. Nothing here is fixed "
            "automatically -- no program repairs its own logic -- but a "
            "fault that is written down is one somebody can act on."
        ),
    }


# --- the email ---------------------------------------------------------------
#
# The fault list answers "what has broken" for somebody who opens it. A 500
# is a customer looking at an error page right now, so it is also mailed,
# briefly, to CYBERLOGIX_ERRORS_EMAIL. The list stays the record: the email
# carries the same reference code the error page shows and the list is
# keyed on, and says how many times the list has seen it.
#
# What goes in is chosen, not filtered: the time, the version, the method,
# the route (never the query string), the error's type and message and the
# stack trace. The request body, cookies, headers and the caller's address
# are never read, so they cannot leak. The message and the trace are still
# passed through redact() and _scrub_tokens(), because code can put
# anything into an exception's message.

# One email per distinct fault an hour, and no more than this a day, so a
# bug hit by every request is one email an hour rather than a flood.
ERROR_MAIL_EVERY_SECONDS = 3600
ERROR_MAILS_PER_DAY = 20
_SENT_KIND = "fault_mail"  # fault_id -> when it was last mailed
_DAY_KIND = "fault_mail_day"  # YYYY-MM-DD -> how many went that day

# Our own keys and tokens (clx_, cls_, clr_, clj_, clx_snr_, clx_ptr_ ...),
# anything after "Bearer", a value given to something named like a secret,
# and any long run of letters and digits that looks like a random token.
_TOKENS = (
    (re.compile(r"\bcl[a-z]_[A-Za-z0-9_-]{6,}"), "<token>"),
    (re.compile(r"(?i)\bbearer\s+[^\s'\",]+"), "Bearer <token>"),
    (re.compile(r"(?i)\b(\w*(?:key|token|secret|password|passwd|signature|sig))"
                r"(\s*[=:]\s*)['\"]?[^\s'\",&]+"), r"\1\2<token>"),
    (re.compile(r"(?<![\w/.-])(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])"
                r"[A-Za-z0-9_-]{24,}"), "<token>"),
)

# Where this application lives, so a trace names main.py rather than the
# directory the container happened to put it in.
_ROOT = os.path.dirname(os.path.abspath(__file__))
_FILE = re.compile(r'File "([^"]+)"')


def _scrub_tokens(text: str) -> str:
    for pattern, replacement in _TOKENS:
        text = pattern.sub(replacement, text)
    return text


def _relative(path: str) -> str:
    """A source path without the machine's directories in front of it."""
    if path.startswith(_ROOT + os.sep):
        return os.path.relpath(path, _ROOT)
    for marker in ("site-packages" + os.sep, "dist-packages" + os.sep):
        if marker in path:
            return marker + path.split(marker, 1)[1]
    match = re.search(r"python\d+(?:\.\d+)?" + re.escape(os.sep) + r"(.+)$", path)
    if match:
        return "python/" + match.group(1)
    return os.path.basename(path)


def clean(text: str) -> str:
    """No addresses, numbers or tokens: what may go into an error report."""
    return _scrub_tokens(redact(text))


def report_trace(exc: BaseException) -> str:
    trace = "".join(tb.format_exception(type(exc), exc, exc.__traceback__))
    trace = _FILE.sub(lambda m: f'File "{_relative(m.group(1))}"', trace)
    return clean(trace)[-6000:]


def errors_address() -> str:
    """Where 500s are mailed, or "" for nowhere."""
    return os.environ.get("CYBERLOGIX_ERRORS_EMAIL", "").strip()


def _may_mail(fault_id: str, now) -> int:
    """Take a place in today's allowance for this fault: its number, or 0.

    In the database, so a restart in the middle of a bad hour does not
    start the count again, and under the store's lock, so two requests
    failing at once send one email between them.
    """
    from datetime import timedelta

    day = now.date().isoformat()
    # iso() writes one fixed format, so the strings compare as the times do.
    stale = iso(now - timedelta(seconds=ERROR_MAIL_EVERY_SECONDS))
    with STORE._lock:
        last = STORE._db.get(_SENT_KIND, fault_id)
        if last is not None and str(last.get("sent_at", "")) > stale:
            return 0
        today = STORE._db.get(_DAY_KIND, day) or {"day": day, "sent": 0}
        if int(today.get("sent", 0)) >= ERROR_MAILS_PER_DAY:
            return 0
        if int(today.get("sent", 0)) == 0:
            # A new day: yesterday's tallies are not needed any more.
            STORE._db.delete_many(_DAY_KIND, [
                r["day"] for r in STORE._db.all(_DAY_KIND) if r.get("day") != day])
            STORE._db.delete_many(_SENT_KIND, [
                r["fault_id"] for r in STORE._db.all(_SENT_KIND)
                if str(r.get("sent_at", "")) < stale])
        today["sent"] = int(today.get("sent", 0)) + 1
        STORE._db.put(_DAY_KIND, day, today)
        STORE._db.put(_SENT_KIND, fault_id, {"fault_id": fault_id, "sent_at": iso(now)})
    return int(today["sent"])


def render_report(row: Dict[str, Any], exc: BaseException, method: str,
                  path: str, now) -> "tuple[str, str]":
    """The subject and body of one error report."""
    from version import __version__

    fault_id = str(row.get("fault_id", ""))
    kind = type(exc).__name__
    where = clean(f"{method} {path}")
    seen = int(row.get("seen", 1))
    subject = f"CyberLogix error {fault_id}: {kind} on {where}"
    body = "\n".join([
        "The server answered a request with an error (500).",
        "",
        f"Time: {iso(now)}",
        f"Version: {__version__}",
        f"Request: {where}",
        f"Error: {kind}",
        f"Message: {clean(str(exc))[:500] or '(none)'}",
        f"Reference: {fault_id}",
        f"Seen: {seen} time{'' if seen == 1 else 's'} since "
        f"{row.get('first_seen', iso(now))}",
        "",
        "The error page showed the customer the same reference. Every "
        "occurrence is in the fault list: GET /api/admin/faults with the "
        "admin key.",
        "",
        "Stack trace:",
        "",
        report_trace(exc),
    ])
    return subject, body


def mail_report(row: Dict[str, Any], exc: BaseException, method: str,
                path: str) -> Dict[str, Any]:
    """Mail one 500 to CYBERLOGIX_ERRORS_EMAIL, within the limits. Never raises.

    Through the ordinary mail queue, so a mail host that is down gets the
    report later rather than never.
    """
    try:
        to = errors_address()
        fault_id = str(row.get("fault_id", ""))
        if not to or not fault_id:
            return {"sent": False, "status": "off"}
        now = utc_now()
        number = _may_mail(fault_id, now)
        if not number:
            return {"sent": False, "status": "limited"}
        subject, body = render_report(row, exc, method, path, now)
        from mail import send

        return send(
            to_address=to,
            subject=subject,
            body=body,
            # The day and its count: a new key for every report allowed.
            dedupe_key=f"error-report:{now.date().isoformat()}:{number}",
            klass="transactional",
        )
    except Exception:  # noqa: BLE001 - reporting an error must never raise
        logger.exception("Could not mail the error report.")
        return {"sent": False, "status": "error"}


# --- routes ----------------------------------------------------------------

from fastapi import APIRouter, Depends, HTTPException  # noqa: E402

from auth import require_platform_admin  # noqa: E402

router = APIRouter(prefix="/api/admin/faults", tags=["Faults"])


@router.get("")
def read_faults(limit: int = 50, _: None = Depends(require_platform_admin)):
    """What has broken, deduplicated, most recently seen first."""
    return {
        "generated_at": iso(utc_now()),
        **status(),
        "faults": recent(max(1, min(limit, MAX_DISTINCT))),
    }


@router.delete("/{fault_id}")
def dismiss(fault_id: str, _: None = Depends(require_platform_admin)):
    """Mark one as dealt with. It comes straight back if it happens again."""
    if not clear(fault_id):
        raise HTTPException(status_code=404, detail="No such fault.")
    return {"cleared": fault_id}
