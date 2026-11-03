"""The inspector's log: every unit's temperatures for chosen dates, and
every time one went out of range, who answered and what they did.

The compliance report gives a percentage. An inspector standing in the
kitchen asks for something else: the log for the walk-in, for these
dates, and for each time it went warm, who knew and what happened to the
food. This is that, one unit to a section, ready to print from the
console in one click (the print view is in console.js and theme.css).

Hourly rows rather than every reading. A five-minute sensor writes 288
readings a day; a log an inspector can read on paper says, for each hour,
the lowest and highest reading and whether any of it was out of range.
Every out-of-range spell is listed in full underneath.

Only this business's units and alerts, ever: everything is read through
the tenant, and nothing here takes an id from the caller.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from contracts import require_current
from store import (
    INDUSTRY_PROFILES,
    STORE,
    Tenant,
    display_temperature,
    iso,
    utc_now,
)

router = APIRouter(prefix="/api/autopilot", tags=["Autonomous Compliance Clerk"])

# A quarter: what an inspection usually asks to see, and a page count that
# still prints.
MAX_DAYS = 92


def _day(value: Optional[str], default: date, name: str) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"{name} must be a date written YYYY-MM-DD.",
        )


def _readings(sensor, start: datetime, end: datetime, incidents) -> List[Dict[str, Any]]:
    """The readings held for this unit in the period, live and kept as evidence.

    Readings are a rolling window, but an alert keeps a copy of the
    readings around it, so an old out-of-range spell still shows on the
    log after the live window has moved on.
    """
    rows: Dict[Any, Dict[str, Any]] = {}
    for reading in STORE.readings_for(sensor.sensor_id, since=start):
        if reading.recorded_at < end:
            rows[reading.reading_id] = {
                "at": reading.recorded_at,
                "f": reading.temperature_fahrenheit,
                "breached": reading.breached,
            }
    for incident in incidents:
        for kept in incident.evidence_readings or []:
            at = datetime.fromisoformat(str(kept.get("recorded_at", "")).replace("Z", "+00:00")) \
                if kept.get("recorded_at") else None
            if at is None or not (start <= at < end) or kept.get("reading_id") in rows:
                continue
            rows[kept.get("reading_id")] = {
                "at": at,
                "f": kept.get("temperature_fahrenheit"),
                "breached": bool(kept.get("breached")),
            }
    return sorted((r for r in rows.values() if r["f"] is not None), key=lambda r: r["at"])


def _hours(readings, unit: str) -> List[Dict[str, Any]]:
    by_hour: Dict[datetime, List[Dict[str, Any]]] = {}
    for row in readings:
        by_hour.setdefault(row["at"].replace(minute=0, second=0, microsecond=0), []).append(row)
    out = []
    for hour in sorted(by_hour):
        rows = by_hour[hour]
        temps = [display_temperature(r["f"], unit) for r in rows]
        out.append({
            "hour": iso(hour),
            "low": min(temps),
            "high": max(temps),
            "readings": len(rows),
            "out_of_range": sum(1 for r in rows if r["breached"]),
        })
    return out


def _days(hours) -> List[Dict[str, Any]]:
    """The hourly rows folded to one line a day: what the printed log shows.

    A min/max log is what a kitchen keeps by hand, so it is the shape an
    inspector reads fastest; the hours stay in the data for anyone who
    wants them, and every out-of-range spell is listed in full anyway.
    """
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for row in hours:
        by_day.setdefault(row["hour"][:10], []).append(row)
    return [{
        "day": day,
        "low": min(r["low"] for r in rows),
        "high": max(r["high"] for r in rows),
        "readings": sum(r["readings"] for r in rows),
        "out_of_range": sum(r["out_of_range"] for r in rows),
        "hours_out_of_range": sum(1 for r in rows if r["out_of_range"]),
        "hours_with_readings": len(rows),
    } for day, rows in sorted(by_day.items())]


def _breach(incident, readings, unit: str, above, below) -> Dict[str, Any]:
    end = incident.resolved_at or utc_now()
    during = [r for r in readings
              if incident.opened_at <= r["at"] <= end and r["breached"]]
    temps = [r["f"] for r in during] or [incident.temperature_fahrenheit]
    # The worst reading is the one furthest past the limit it crossed.
    if above is not None and max(temps) > above:
        worst = max(temps)
    elif below is not None and min(temps) < below:
        worst = min(temps)
    else:
        worst = incident.temperature_fahrenheit
    answered = None
    if incident.acknowledged_at is not None:
        answered = round((incident.acknowledged_at - incident.opened_at).total_seconds() / 60)
    return {
        "incident_id": incident.incident_id,
        "opened_at": iso(incident.opened_at),
        "worst": display_temperature(worst, unit),
        "acknowledged_by": incident.acknowledged_by or "",
        "acknowledged_at": iso(incident.acknowledged_at),
        "minutes_to_acknowledge": answered,
        "closed_at": iso(incident.resolved_at),
        "corrective_action": incident.corrective_action or "",
        "corrective_action_by": incident.corrective_action_by or "",
        "product_disposition": incident.product_disposition or "",
        "reviewed_by": incident.reviewed_by or "",
    }


def build_log(tenant: Tenant, first: date, last: date) -> Dict[str, Any]:
    unit = tenant.temperature_unit
    start = datetime.combine(first, time.min, tzinfo=timezone.utc)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=timezone.utc)
    sites = {s.site_id: s.name for s in STORE.sites_for(tenant.tenant_id)}
    all_incidents = [i for i in STORE.incidents_for(tenant.tenant_id)
                     if i.opened_at < end and (i.resolved_at is None or i.resolved_at >= start)]
    units = []
    for sensor in sorted(STORE.sensors_for(tenant.tenant_id),
                         key=lambda s: (sites.get(s.site_id, ""), s.location_name or "",
                                        s.sensor_id)):
        incidents = sorted((i for i in all_incidents if i.sensor_id == sensor.sensor_id),
                           key=lambda i: i.opened_at)
        readings = _readings(sensor, start, end, incidents)
        hours = _hours(readings, unit)
        above, below = sensor.bounds()
        temps = [display_temperature(r["f"], unit) for r in readings]
        out_of_range = sum(1 for r in readings if r["breached"])
        units.append({
            "sensor_id": sensor.sensor_id,
            "location_name": sensor.location_name or sensor.sensor_id,
            "site_name": sites.get(sensor.site_id, ""),
            "industry_name": INDUSTRY_PROFILES[sensor.industry_vertical]["name"],
            "limit_above": display_temperature(above, unit) if above is not None else None,
            "limit_below": display_temperature(below, unit) if below is not None else None,
            "readings": len(readings),
            "out_of_range_readings": out_of_range,
            "in_range_percent": (round((len(readings) - out_of_range) / len(readings) * 100, 1)
                                 if readings else None),
            "lowest": min(temps) if temps else None,
            "highest": max(temps) if temps else None,
            "hours": hours,
            "days": _days(hours),
            "breaches": [_breach(i, readings, unit, above, below) for i in incidents],
        })
    from automation import retention_coverage

    coverage = retention_coverage(tenant, start)
    return {
        "company_name": tenant.company_name,
        "period_start": first.isoformat(),
        "period_end": last.isoformat(),
        "temperature_unit": unit,
        "generated_at": iso(utc_now()),
        "units": units,
        "breaches": sum(len(u["breaches"]) for u in units),
        "unanswered": sum(1 for u in units for b in u["breaches"] if not b["acknowledged_by"]),
        "covers_the_whole_period": coverage["covers_the_whole_period"],
        "caveat": coverage["caveat"],
        "note": (
            "Hourly lowest and highest readings for each unit, from continuous "
            "sensor readings. Every time a unit went out of range is listed "
            "under it, with who acknowledged it and what was done."
        ),
    }


@router.get("/inspector-log")
def inspector_log(
    start: Optional[str] = Query(None, description="First day, YYYY-MM-DD. Default: 7 days ago."),
    end: Optional[str] = Query(None, description="Last day, YYYY-MM-DD. Default: today."),
    tenant: Tenant = Depends(require_current("The inspector's log")),
):
    """Each unit's temperature log for the dates chosen, with every time it
    went out of range and who acknowledged it. Read-only; any role."""
    today = utc_now().date()
    last = _day(end, today, "end")
    first = _day(start, last - timedelta(days=6), "start")
    if first > last:
        raise HTTPException(status_code=422,
                            detail="The first day is after the last day.")
    if (last - first).days + 1 > MAX_DAYS:
        raise HTTPException(status_code=422,
                            detail=f"Choose {MAX_DAYS} days or fewer.")
    import usage

    usage.count("inspector_logs")
    return build_log(tenant, first, last)
