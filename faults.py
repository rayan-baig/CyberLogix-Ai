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
    detail = "".join(
        tb.format_exception(type(exc), exc, exc.__traceback__)
    )[-4000:]

    with STORE._lock:
        row = STORE._db.get(KIND, ident)
        if row is None:
            _prune_locked()
            row = {
                "fault_id": ident,
                "source": source,
                "exception": type(exc).__name__,
                "message": str(exc)[:500],
                "context": context[:500],
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
                "message": str(exc)[:500],
            }
        STORE._db.put(KIND, ident, row)

    logger.error(
        "Fault %s in %s (%s occurrence(s)): %s: %s",
        ident, source, row["seen"], type(exc).__name__, exc,
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
