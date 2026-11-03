"""How often the few things that matter are done, one day at a time.

Somebody running this needs to know whether anybody signs up, adds a
sensor, sends the test alert, answers an alert, prints the inspector's
log or reads the help page. They do not need to know who did. So this
keeps a total per action per day and nothing else: no account, no
person, no address, no cookie, no third party, and nothing the browser
sends. A day's row is a date and some numbers.

Counted where the server already does the thing, never from a script
in the page. The pricing calculator runs entirely in the browser and
never reaches the server, so it is not counted at all rather than
counted by adding a call home to a page that does not need one.

Rows older than KEEP_DAYS are deleted. Counting must never break the
thing being counted, so nothing here raises.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from store import STORE, utc_now

logger = logging.getLogger("cyberlogix.usage")

KIND = "usage_day"

# Long enough to compare a month with the same month last year.
KEEP_DAYS = 400

# What is counted, and what each total means. Acknowledging is counted
# by the way it was done, because "nobody uses the text reply" is the
# kind of thing this is for.
EVENTS: Dict[str, str] = {
    "signups": "Businesses signed up for a trial",
    "sensors_added": "Sensors added",
    "test_alerts": "Test alerts sent",
    "acknowledged_button": "Alerts acknowledged with the console button or the API",
    "acknowledged_text": "Alerts acknowledged by a text reply",
    "acknowledged_keypress": "Alerts acknowledged by pressing 1 on a call",
    "inspector_logs": "Inspector's logs made (the console opens one to print it)",
    "help_views": "Help pages viewed (/help and /help/sensors)",
}


def count(event: str, now: Optional[datetime] = None) -> None:
    """Add one to today's total for `event`. Never raises."""
    if event not in EVENTS:
        logger.error("Not a counted event: %s", event)
        return
    try:
        day = (now or utc_now()).date().isoformat()
        with STORE._lock:
            row = STORE._db.get(KIND, day)
            if row is None:
                # The first count of a new day is when old days go.
                _prune_locked(now)
                row = {"day": day}
            row[event] = int(row.get(event, 0)) + 1
            STORE._db.put(KIND, day, row)
    except Exception:  # noqa: BLE001 - a count must never break the action
        logger.exception("Could not count %s.", event)


def _prune_locked(now: Optional[datetime]) -> int:
    cutoff = ((now or utc_now()) - timedelta(days=KEEP_DAYS)).date().isoformat()
    old = [r["day"] for r in STORE._db.all(KIND) if str(r.get("day", "")) < cutoff]
    if old:
        STORE._db.delete_many(KIND, old)
    return len(old)


def prune(now: Optional[datetime] = None) -> int:
    """Delete days older than KEEP_DAYS. Returns how many went."""
    with STORE._lock:
        return _prune_locked(now)


def last_days(days: int = 30, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Each day's totals for the last `days` days, today included, oldest first.

    Every day is listed, a quiet one with zeros, so a chart of it does not
    quietly join Monday to Thursday.
    """
    today = (now or utc_now()).date()
    rows = {str(r.get("day")): r for r in STORE._db.all(KIND)}
    daily: List[Dict[str, Any]] = []
    for back in range(days - 1, -1, -1):
        day = (today - timedelta(days=back)).isoformat()
        row = rows.get(day, {})
        daily.append({"day": day, **{e: int(row.get(e, 0)) for e in EVENTS}})
    totals = {e: sum(d[e] for d in daily) for e in EVENTS}
    totals["acknowledged"] = (totals["acknowledged_button"]
                              + totals["acknowledged_text"]
                              + totals["acknowledged_keypress"])
    return {
        "days": days,
        "from": daily[0]["day"],
        "to": daily[-1]["day"],
        "totals": totals,
        "daily": daily,
        "meaning": dict(EVENTS),
        "note": (
            "Daily totals only: no account, person, address or cookie is "
            "kept with them, and nothing is sent anywhere else. Days older "
            f"than {KEEP_DAYS} are deleted. The pricing calculator works in "
            "the browser and is not counted."
        ),
    }


# --- routes ----------------------------------------------------------------

from fastapi import APIRouter, Depends  # noqa: E402

from auth import require_platform_admin  # noqa: E402

router = APIRouter(prefix="/api/admin/usage", tags=["Usage"])


@router.get("")
def read_usage(_: None = Depends(require_platform_admin)):
    """The last 30 days of daily totals. The platform operator only."""
    return last_days(30)
