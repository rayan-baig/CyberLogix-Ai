"""What CyberLogix did for you: one screen, for the week or the month.

A monitoring product that works is invisible: nothing breaks, nobody is
called, and a month later the owner cannot say what they pay for. This
is the screen that says it (3.32.0): how many readings were checked, the
alerts and how fast somebody answered, what happened to the food, whether
any sensor has gone quiet, and a link to the printable log for the same
days. /today shows it under the month line; the console has it on the
Proof tab.

From the account's own records only, the same ones the inspector's log
and the monthly email read. During the first week of a free trial there
is not much to say yet, so it also carries a sample, plainly labelled,
of what a week with three fridges looks like.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

import food_value
from domain import minutes_words
from licenses import require_tenant
from store import STORE, Tenant, iso, utc_now

router = APIRouter(prefix="/api/console", tags=["Operations Console"])

PERIODS = {"week": "This week", "month": "This month"}

# How long a trial shows the sample beside its own figures.
SAMPLE_DAYS = 7

# The sample: a café with three fridges and freezers, a week of readings
# every five minutes, one alert answered in four minutes and one short
# blip that came back by itself. Shown only during a trial's first week,
# and always labelled as a sample.
SAMPLE = {
    "units": 3,
    "readings": 6048,
    "in_range_percent": 99.6,
    "alerts": 2,
    "answered": 1,
    "typical_answer_minutes": 4.0,
    "fastest_answer_minutes": 4.0,
    "by_itself": 1,
    "texts": 3,
    "calls": 0,
    "food_kept": 1,
    "food_auto_safe": 1,
    "food_thrown": 0,
    "food_missing": 0,
    "quiet": [],
}


def _window(period: str, now: datetime):
    if period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now - timedelta(days=7)
    return start, now


def _count(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def lines(f: Dict[str, Any]) -> List[str]:
    """The figures as a few short sentences, the same for the real ones
    and the sample."""
    out = []
    units = f["units"]
    out.append(f"Checked {_count(f['readings'], 'reading')} from "
               f"{_count(units, 'fridge or freezer', 'fridges and freezers')}"
               + (f", {f['in_range_percent']:g}% in range." if f["in_range_percent"] is not None else "."))
    n = f["alerts"]
    if not n:
        out.append("No alerts: every fridge and freezer stayed in range.")
    else:
        bits = [_count(n, "alert")]
        if f["answered"]:
            if f["answered"] == 1:
                bits.append(f"1 answered in {minutes_words(f['typical_answer_minutes'])}")
            else:
                bits.append(f"{f['answered']} answered, typically in "
                            f"{minutes_words(f['typical_answer_minutes'])} "
                            f"(fastest {minutes_words(f['fastest_answer_minutes'])})")
        if f["by_itself"]:
            bits.append(f"{f['by_itself']} cold again by {'itself' if f['by_itself'] == 1 else 'themselves'}")
        out.append(", ".join(bits) + ".")
        if f["texts"] or f["calls"]:
            out.append(f"{_count(f['texts'], 'alert text')} delivered, {_count(f['calls'], 'phone call')}.")
        food = []
        # The count first (3.33.0): "1 kept, 1 thrown away", never "thrown away 1".
        if f["food_kept"]:
            food.append(f"{f['food_kept']} kept")
        if f["food_auto_safe"]:
            food.append(f"{f['food_auto_safe']} short blip{'' if f['food_auto_safe'] == 1 else 's'} recorded safe")
        if f["food_thrown"]:
            food.append(f"{f['food_thrown']} thrown away")
        if f["food_missing"]:
            food.append(f"{f['food_missing']} answer{'' if f['food_missing'] == 1 else 's'} missing")
        if food:
            out.append("Food: " + ", ".join(food) + ".")
    quiet = f["quiet"]
    out.append("Every sensor is reporting." if not quiet else
               f"Gone quiet: {', '.join(quiet)}. Check {'it' if len(quiet) == 1 else 'them'}.")
    return out


def summary(tenant: Tenant, period: str = "week", now: Optional[datetime] = None) -> Dict[str, Any]:
    """The week's (or the month's) figures for one business."""
    now = now or utc_now()
    period = period if period in PERIODS else "week"
    start, end = _window(period, now)
    sensors = STORE.sensors_for(tenant.tenant_id)
    readings = breached = 0
    for sensor in sensors:
        # Up to now: the window ends at the latest reading.
        stats = STORE.reading_stats(sensor.sensor_id, since=start)
        readings += stats["count"]
        breached += stats["breached"]
    alerts = [i for i in STORE.incidents_for(tenant.tenant_id, since=start)
              if not i.site_wide and i.opened_at >= start]
    answered = [i for i in alerts if i.acknowledged_at is not None]
    waits = [max(0.0, (i.acknowledged_at - i.opened_at).total_seconds() / 60.0) for i in answered]
    by_id = {s.sensor_id: s for s in sensors}
    food = food_value.month_total((i, by_id.get(i.sensor_id)) for i in alerts)
    figures = {
        "units": len(sensors),
        "readings": readings,
        "in_range_percent": round((readings - breached) / readings * 100, 1) if readings else None,
        "alerts": len(alerts),
        "answered": len(answered),
        "typical_answer_minutes": round(median(waits), 1) if waits else None,
        "fastest_answer_minutes": round(min(waits), 1) if waits else None,
        "by_itself": sum(1 for i in alerts if i.acknowledged_at is None and i.resolved_at is not None),
        # Texts that reached the phone network, never ones only written
        # (a server with texts off).
        "texts": sum(sum(1 for r in i._sms_receipts() if r.get("delivered")) for i in alerts),
        "calls": sum(1 for i in alerts if i.phoned),
        "food_kept": food["alerts"],
        "food_auto_safe": food["auto_safe"],
        "food_thrown": food["thrown"],
        "food_missing": food["at_risk"] + food["unanswered"],
        "quiet": [s.location_name or s.sensor_id for s in sensors
                  if s.last_seen is not None and s.offline(now)],
    }
    first = start.date().isoformat()
    last = end.date().isoformat()
    trial_week = (tenant.plan == "trial" and tenant.activated_at is not None
                  and now - tenant.activated_at < timedelta(days=SAMPLE_DAYS))
    return {
        "period": period,
        "label": PERIODS[period],
        "from": iso(start),
        "to": iso(end),
        "figures": figures,
        "lines": lines(figures),
        # The printable log for the same days (the console opens it).
        "log_link": f"/console?view=full#inspector-log?from={first}&to={last}&open=1",
        "sample": ({"note": ("A sample of what this shows after a week with three fridges "
                             "and freezers. Yours fills in as the readings arrive."),
                    "figures": dict(SAMPLE), "lines": lines(SAMPLE)} if trial_week else None),
    }


@router.get("/did-for-you")
def did_for_you(period: str = Query("week", pattern="^(week|month)$"),
                tenant: Tenant = Depends(require_tenant)) -> Dict[str, Any]:
    """What CyberLogix did for this business this week or this month."""
    return summary(tenant, period)
