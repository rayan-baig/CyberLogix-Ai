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

When an alert counts (`protected`, 3.28.0): it is a unit's own alert (not
a site-wide one, which sits on top of its units' alerts), it is closed,
and either

* somebody said the food was kept (the "Food kept" answer after "It's
  fixed", or a corrective action that kept it: unaffected, moved, sold or
  used), or
* a person answered it ("I'm on it" or "It's fixed") and the unit was back
  in range within `SAFE_HOURS` (two hours) of going out.

It never counts when the food was recorded as thrown away. An alert that
ended on its own with nobody answering is not counted: nobody had to act,
so nothing is claimed. A unit out of range for longer than two hours, or
closed while still out of range, with nobody saying the food was kept, is
"Food at risk: check it" (`at_risk`), never "protected": eight hours at
62.5°F used to say "Food protected: about $3,000".
"""

from __future__ import annotations

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
    if fate == "discarded":
        return None
    answered = (incident.acknowledged_at is not None
                or getattr(incident, "fix_reported_at", None) is not None)
    if fate not in KEPT and not (answered and _back_in_time(incident)):
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

    Out of range for over SAFE_HOURS (or closed while still out), nobody
    said the food was kept, and nobody recorded it thrown away. None
    otherwise, and None where the sector has no estimate.
    """
    if not _closed_unit_alert(incident):
        return None
    fate = getattr(incident, "product_disposition", "") or ""
    if fate == "discarded" or fate in KEPT:
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


def month_total(pairs: Iterable) -> Dict[str, Any]:
    """The month's estimate over (incident, sensor) pairs:
    {"usd", "alerts", "at_risk"}, by the same rule as each alert's line."""
    usd = 0
    alerts = 0
    risky = 0
    for incident, sensor in pairs:
        row = protected(incident, sensor)
        if row:
            usd += row["usd"]
            alerts += 1
        elif at_risk(incident, sensor):
            risky += 1
    return {"usd": usd, "alerts": alerts, "at_risk": risky}
