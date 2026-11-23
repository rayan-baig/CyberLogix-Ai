"""What the food in a fridge is roughly worth: "Food protected: about $X".

Shown on a closed alert in the console and in the monthly email, always
as an estimate, never as a measured figure. It is not what the product is
sold on (that is `loss_avoided_usd` in pricing.py, for a whole failure
across a site); it is the stock one fridge or freezer of this kind usually
holds, so a café owner can see what a caught alert was worth to them.

**The owner tunes the numbers here, and only here.** `STOCK_USD` is what a
typical unit of each kind holds, in US dollars. `SECTOR_STOCK` says which
list a sector uses; a sector not in it gets no estimate at all (no figure
is better than an invented one). docs/ALERTS.md says the same for whoever
runs the server.

When an alert counts (`protected`, 3.30.0): it is a unit's own alert (not
a site-wide one, which sits on top of its units' alerts), it is closed,
and somebody said the food was kept (the "Food kept" answer, or a
corrective action that kept it: unaffected, moved, sold or used). Only
the saved answer counts. Until somebody taps, a closed alert is "food
unanswered" (`question`): it says how long it took to come back ("Back in
range in 12 minutes") and asks, and never "Food kept" or "$800 protected".
3.28.0 counted an answered alert back in range within two hours as kept,
so an alert closed by itself after "It's fixed" said "Food protected:
about $3,000" while nobody had said a word about the food.

A short blip (3.32.0, `auto_safe_minutes`): a food unit back in range
after less than SHORT_MINUTES out, never far past its limit, is recorded
"Food safe: out for 12 minutes" by itself as it closes, with Change for
CHANGE_HOURS, and counts no dollars. Long or severe alerts keep asking.

It never counts when the food was recorded as thrown away. A unit out of
range for longer than two hours, or closed while still out of range, with
nobody saying what happened to the food, is "Food at risk: check it"
(`at_risk`): eight hours at 62.5°F once said "Food protected: about
$3,000".
"""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, Optional

# What a typical unit holds, in US dollars, by kind. A walk-in is any unit
# whose name says so ("Walk-in fridge", "walk in freezer", "cold room").
STOCK_USD: Dict[str, Dict[str, int]] = {
    # A café or restaurant: dairy, meat, prepared food.
    "food": {
        "fridge": 800,
        "freezer": 1200,
        "walk-in fridge": 3000,
        "walk-in freezer": 4000,
    },
    # A pharmacy or a lab: vaccines and medicines kept cold.
    "clinical": {
        "fridge": 5000,
        "freezer": 8000,
        "walk-in fridge": 15000,
        "walk-in freezer": 20000,
    },
}

# Which list each sector uses. Not listed: no estimate.
SECTOR_STOCK: Dict[str, str] = {
    "restaurant": "food",
    "country_club": "food",
    "pharmacy": "clinical",
    "medical_lab": "clinical",
}

# The first word of the line, by list.
LABEL = {"food": "Food protected", "clinical": "Stock protected"}

# How long a unit may be out of range, with somebody answering, and still
# count as caught in time: the widely taught two-hour rule for food above
# its limit. Past it, only an explicit "Food kept" counts.
SAFE_HOURS = 2.0

# What a person can record that means the food was kept.
KEPT = ("unaffected", "moved", "sold_or_used")

# Short blips (3.32.0). A food unit's alert that closes back in range
# after less than this many minutes out, and never went far past its
# limit, records "Food safe: out for 12 minutes" by itself, with a Change
# link: well inside the two-hour rule above, so nobody is asked, nothing
# says "One thing to do", and no dollars are counted for it. Long or
# severe alerts (and stock in a pharmacy or a lab, where a vaccine's own
# rules apply) keep the question. The owner sets it for the whole server
# with CYBERLOGIX_FOOD_SAFE_MINUTES (docs/ALERTS.md); 0 turns it off.
def _short_minutes() -> float:
    raw = os.environ.get("CYBERLOGIX_FOOD_SAFE_MINUTES", "").strip()
    try:
        value = float(raw) if raw else 30.0
    except ValueError:
        value = 30.0
    # Never past the two-hour rule it sits inside.
    return max(0.0, min(value, SAFE_HOURS * 60.0))


SHORT_MINUTES = _short_minutes()

# How far past its upper limit a unit may have gone and still count as a
# blip: the same 15°F that alerts at once (domain.ALERT_AT_ONCE_DEGREES_F),
# and never at room temperature (disposition.DANGEROUSLY_WARM_F).
SEVERE_PAST_LIMIT_F = 15.0
SEVERE_AT_F = 70.0

# How long an answer about the food can be changed after it was given
# (3.32.0): "Change" shows for 24 hours, and every change is kept in the
# alert's history.
CHANGE_HOURS = 24

_WALK_IN = ("walk-in", "walk in", "walkin", "cold room", "coldroom")


def kind_of(sensor) -> str:
    """"fridge", "freezer", "walk-in fridge" or "walk-in freezer"."""
    from domain import unit_kind_of

    base = unit_kind_of(sensor)
    if base not in ("fridge", "freezer"):
        above = sensor.bounds()[0] if hasattr(sensor, "bounds") else None
        base = "freezer" if above is not None and above <= 20.0 else "fridge"
    name = f"{getattr(sensor, 'location_name', '') or ''} {getattr(sensor, 'sensor_id', '') or ''}".lower()
    return f"walk-in {base}" if any(w in name for w in _WALK_IN) else base


def estimate(sensor) -> Optional[Dict[str, Any]]:
    """The estimate for one unit, or None where its sector has none."""
    if sensor is None:
        return None
    stock = SECTOR_STOCK.get(getattr(sensor, "industry_vertical", ""))
    if stock is None:
        return None
    kind = kind_of(sensor)
    usd = STOCK_USD[stock].get(kind)
    if not usd:
        return None
    return {"usd": int(usd), "kind": kind, "label": LABEL[stock]}


def _back_in_time(incident) -> bool:
    """Back in range within SAFE_HOURS of going out. A unit closed while
    still out of range never was, as far as anybody knows."""
    if getattr(incident, "closed_out_of_range", False):
        return False
    start = getattr(incident, "out_since", None) or incident.opened_at
    hours = (incident.resolved_at - start).total_seconds() / 3600.0
    return hours <= SAFE_HOURS


def _closed_unit_alert(incident) -> bool:
    return (incident is not None and incident.resolved_at is not None
            and getattr(incident, "kind", "unit") == "unit")


def protected(incident, sensor) -> Optional[Dict[str, Any]]:
    """What a closed alert protected, as an estimate, or None.

    {"usd", "words", "note"}: words is the line itself ("Food protected:
    about $800"), note says it is an estimate and of what.
    """
    if not _closed_unit_alert(incident):
        return None
    fate = getattr(incident, "product_disposition", "") or ""
    if fate not in KEPT:
        return None
    # Recorded by itself for a short blip: nothing was at stake, so no
    # dollars (3.32.0). A person's own "Food kept" counts again.
    if getattr(incident, "food_auto_minutes", None) is not None:
        return None
    guess = estimate(sensor)
    if guess is None:
        return None
    return {
        "usd": guess["usd"],
        "words": f"{guess['label']}: about ${guess['usd']:,}",
        "note": f"An estimate: what a typical {guess['kind']} holds, not a count of this one.",
    }


def at_risk(incident, sensor) -> Optional[Dict[str, Any]]:
    """"Food at risk: check it", on a closed alert that ran too long.

    Out of range for over SAFE_HOURS (or closed while still out), and
    nobody has said what happened to the food. None otherwise, and None
    where the sector has no estimate.
    """
    if not _closed_unit_alert(incident):
        return None
    if getattr(incident, "product_disposition", "") or "":
        return None
    if _back_in_time(incident):
        return None
    guess = estimate(sensor)
    if guess is None:
        return None
    word = "Food" if guess["label"].startswith("Food") else "Stock"
    return {
        "words": f"{word} at risk: check it",
        "note": (f"It was out of range for more than {SAFE_HOURS:g} hours"
                 if not getattr(incident, "closed_out_of_range", False)
                 else "It was closed while still out of range")
                + ". Say what happened to it, for the record.",
    }


def _minutes_words(minutes: float) -> str:
    from domain import minutes_words

    return minutes_words(minutes)


def _too_cold(incident, sensor) -> bool:
    """The alert was for a unit too cold, not too warm."""
    if sensor is None or not hasattr(sensor, "bounds"):
        return False
    above, below = sensor.bounds()
    worst = getattr(incident, "peak_f", None)
    if worst is None:
        worst = getattr(incident, "temperature_fahrenheit", None)
    if worst is None or below is None:
        return False
    return worst < below and (above is None or worst <= above)


def back_words(incident, sensor, minutes: float) -> str:
    """"Cold again after 12 minutes out" (3.32.0), or "Back in range after
    12 minutes out" for a unit that was too cold. The page adds "(since
    9:10 PM)" from `out_since`, in the reader's own clock."""
    head = "Back in range" if _too_cold(incident, sensor) else "Cold again"
    return f"{head} after {_minutes_words(minutes)} out"


def auto_safe_minutes(incident, sensor) -> Optional[float]:
    """Minutes out, when this closed alert is a short blip whose food is
    recorded safe by itself (3.32.0), else None.

    A unit's own alert in a food sector, closed back in range (not "Close
    anyway" while still out), nobody has said anything about the food yet,
    out for less than SHORT_MINUTES, and never SEVERE_PAST_LIMIT_F past its
    upper limit or at SEVERE_AT_F.
    """
    if not _closed_unit_alert(incident) or SHORT_MINUTES <= 0:
        return None
    if getattr(incident, "product_disposition", "") or "":
        return None
    if getattr(incident, "closed_out_of_range", False):
        return None
    if sensor is None or SECTOR_STOCK.get(getattr(sensor, "industry_vertical", "")) != "food":
        return None
    start = getattr(incident, "out_since", None) or incident.opened_at
    minutes = max(0.0, (incident.resolved_at - start).total_seconds() / 60.0)
    if minutes >= SHORT_MINUTES:
        return None
    worst = getattr(incident, "peak_f", None)
    if worst is None:
        worst = getattr(incident, "temperature_fahrenheit", None)
    above = sensor.bounds()[0] if hasattr(sensor, "bounds") else None
    if worst is not None and (worst >= SEVERE_AT_F
                              or (above is not None and worst - above >= SEVERE_PAST_LIMIT_F)):
        return None
    return round(minutes, 1)


def auto_words(minutes: float, word: str = "food") -> str:
    """"Food safe: out for 12 minutes"."""
    return f"{word.capitalize()} safe: out for {_minutes_words(minutes)}"


# How long /today and the console's "Needs you now" keep asking about the
# food on a closed alert nobody has answered. The alert itself asks for as
# long as it is kept.
ASK_ON_TODAY_DAYS = 7


def question(incident, sensor, now=None) -> Optional[Dict[str, Any]]:
    """The food question on a closed alert nobody has answered (3.30.0).

    {"word": "food" | "stock", "at_risk", "words", "note", "back_minutes",
    "out_since", "ask_on_today"}: words is "Cold again after 12 minutes
    out" when it came back within SAFE_HOURS (the page adds "(since 9:10
    PM)"), else "Food at risk: check it". None once
    somebody answered, on a live alert, and where the sector has no
    estimate (nothing to ask about).
    """
    from datetime import timedelta

    from store import utc_now

    if not _closed_unit_alert(incident):
        return None
    if getattr(incident, "product_disposition", "") or "":
        return None
    guess = estimate(sensor)
    if guess is None:
        return None
    word = "food" if guess["label"].startswith("Food") else "stock"
    start = getattr(incident, "out_since", None) or incident.opened_at
    back = max(0.0, (incident.resolved_at - start).total_seconds() / 60.0)
    risky = at_risk(incident, sensor)
    now = now or utc_now()
    if risky:
        words, note = risky["words"], risky["note"]
    else:
        words = back_words(incident, sensor, back)
        note = f"Nobody has said yet whether the {word} was kept. One tap, for the record."
    return {
        "word": word,
        "at_risk": bool(risky),
        "words": words,
        "note": note,
        "back_minutes": round(back, 1),
        "out_since": start.isoformat() if start else None,
        "ask_on_today": incident.resolved_at >= now - timedelta(days=ASK_ON_TODAY_DAYS),
    }


def month_total(pairs: Iterable) -> Dict[str, Any]:
    """The month's estimate over (incident, sensor) pairs, from the saved
    answers only: {"usd", "alerts" (marked kept), "thrown", "at_risk",
    "unanswered" (closed, nobody said, back within two hours), "auto_safe"
    (short blips recorded safe by themselves, no dollars: 3.32.0)}."""
    usd = 0
    alerts = thrown = risky = unanswered = auto = 0
    for incident, sensor in pairs:
        row = protected(incident, sensor)
        if row:
            usd += row["usd"]
            alerts += 1
            continue
        if not _closed_unit_alert(incident) or estimate(sensor) is None:
            continue
        fate = getattr(incident, "product_disposition", "") or ""
        if fate in KEPT and getattr(incident, "food_auto_minutes", None) is not None:
            auto += 1
        elif fate == "discarded":
            thrown += 1
        elif not fate:
            if at_risk(incident, sensor):
                risky += 1
            else:
                unanswered += 1
    return {"usd": usd, "alerts": alerts, "thrown": thrown, "at_risk": risky,
            "unanswered": unanswered, "auto_safe": auto}
