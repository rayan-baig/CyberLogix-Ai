"""The possible attack or site-wide failure alert.

One unit warming is a door, a gasket or a compressor, and it has its own
alert. Many units at one site warming at about the same time is
something else: a power cut, a door to the whole cold room left open, or
somebody tampering with the refrigeration controls themselves. On
11 October 2026 attackers took over the controllers of hundreds of
supermarkets' fridges and switched them to defrost overnight. A system
that asks the fridge's own controller how it is doing hears whatever the
controller is told to say. CyberLogix reads its own sensors, so it sees
the warming however the controls were changed, and it only reads: it has
no way to change a fridge.

So, on top of each unit's own alert, the sweep raises one urgent alert
when the warming is shared. It goes out straight away, without the usual
grace before a call, to the whole on-call list for the sites involved
and to the business's own number, quiet hours or not.

When it is raised (each figure is a setting, see `settings`):

* **At one site:** at least MIN_UNITS units (3), or at least
  MIN_SHARE_PERCENT of the site's units (50%, and never fewer than 2),
  started warming within WINDOW_MINUTES (20) of each other. At night
  (22:00 to 06:00 at the site) MIN_UNITS_NIGHT (2) is enough: a site is
  shut, nobody is opening doors, and two units warming together is not
  a delivery.
* **Across sites:** units at MIN_SITES (2) or more sites of the same
  business, CROSS_SITE_UNITS (4) of them in all, or CROSS_SITE_UNITS_NIGHT
  (3) at night, started warming within the same window.

"Started warming" means either crossing its upper limit, or rising by
RISE_F (5°F, about 3°C) or more within the last hour without crossing it
yet. It has to have started in the last hour: a unit that has been warm
since yesterday is a fault somebody already knows about, not part of
something happening now.

What does not count, so a single faulty unit or a planned defrost is
not taken for an attack:

* a unit in its own learned defrost (patterns.py), or whose learned
  defrost is due now;
* a unit that has gone silent: a silent sensor reports nothing to warm.
  When half or more of a site's units have gone silent together, that
  is an outage (the gateway, the power to it, the internet), not
  warming, and the quiet-sensor text already covers it, so the site
  gets no site-wide alert;
* warming that started before the last site-wide alert for the same
  site: one event is one alert;
* another business's units. Everything is read through the one tenant.

There is no maintenance window or snooze in the product to respect; when
there is, it belongs in `_excused`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from store import STORE, Sensor, Tenant, iso, utc_now

logger = logging.getLogger("cyberlogix.sitewide")

# How far back a unit's warming may have started and still be news.
RECENT_MINUTES = 60


def _number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        logger.error("%s is not a number; using %s.", name, default)
        return float(default)


def settings() -> Dict[str, Any]:
    """The thresholds, read when asked, so a change needs only a restart."""
    return {
        "min_units": int(_number("CYBERLOGIX_GROUP_ALERT_MIN_UNITS", 3)),
        "min_units_night": int(_number("CYBERLOGIX_GROUP_ALERT_MIN_UNITS_NIGHT", 2)),
        "min_share_percent": _number("CYBERLOGIX_GROUP_ALERT_MIN_SHARE_PERCENT", 50),
        "window_minutes": _number("CYBERLOGIX_GROUP_ALERT_WINDOW_MINUTES", 20),
        "rise_f": _number("CYBERLOGIX_GROUP_ALERT_RISE_F", 5),
        "min_sites": int(_number("CYBERLOGIX_GROUP_ALERT_MIN_SITES", 2)),
        "cross_site_units": int(_number("CYBERLOGIX_GROUP_ALERT_CROSS_SITE_UNITS", 4)),
        "cross_site_units_night": int(
            _number("CYBERLOGIX_GROUP_ALERT_CROSS_SITE_UNITS_NIGHT", 3)),
        "night": (os.environ.get("CYBERLOGIX_GROUP_ALERT_NIGHT") or "22:00-06:00").strip(),
        "timezone": (os.environ.get("CYBERLOGIX_GROUP_ALERT_TIMEZONE") or "UTC").strip(),
    }


@dataclass
class Warming:
    sensor: Sensor
    started_at: datetime
    temperature_f: float
    limit_f: Optional[float]
    over_limit: bool
    rise_f: float


# ---- the clock at the site ---------------------------------------------


def site_timezone(tenant: Tenant, site_id: Optional[str], fallback: str) -> str:
    """The time zone a site keeps, as far as the account says.

    A site has no time zone field of its own; its on-call rotation does,
    and so do its people's quiet hours. The site's rotation first, then
    the estate's, then anybody's quiet hours there, then the setting.
    """
    import oncall

    rotations = oncall.rotations_for(STORE._db, tenant.tenant_id)
    for wanted in (site_id, None):
        for rotation in rotations:
            if rotation.site_id == wanted and rotation.timezone:
                return rotation.timezone
    for contact in STORE.contacts_for(tenant.tenant_id):
        if contact.site_id in (site_id, None) and contact.quiet_hours:
            zone = contact.quiet_hours.get("timezone")
            if zone:
                return zone
    return fallback


def is_night(tenant: Tenant, site_id: Optional[str], at: datetime,
             conf: Optional[Dict[str, Any]] = None) -> bool:
    """Whether `at` falls in the night window, on the site's own clock.

    The account does not know a business's opening hours, so night is a
    fixed window (22:00 to 06:00 unless set otherwise). A window that
    cannot be read is no night at all: it only ever lowers a threshold.
    """
    import oncall

    conf = conf or settings()
    try:
        start_text, end_text = conf["night"].split("-", 1)
        start, end = oncall.clock(start_text), oncall.clock(end_text)
        tz = oncall.zone(site_timezone(tenant, site_id, conf["timezone"]))
    except ValueError:
        return False
    local = at.astimezone(tz).time().replace(second=0, microsecond=0)
    if start == end:
        return False
    if start < end:
        return start <= local < end
    return local >= start or local < end


# ---- which units are warming --------------------------------------------


def _excused(sensor: Sensor, history, above: Optional[float], over: bool,
             now: datetime) -> bool:
    """A planned warm spell: this unit's own learned defrost."""
    import patterns

    if above is None:
        return False
    if over:
        return patterns.defrost_in_progress(history, above) is not None
    return patterns.defrost_due(history, above, now)


def _warming(sensor: Sensor, now: datetime, rise_f: float) -> Optional[Warming]:
    """When this unit started warming, if it did in the last hour."""
    from forecaster import PATTERN_WINDOW_HOURS

    if sensor.offline(now) or sensor.last_seen is None:
        return None
    history = STORE.readings_for(
        sensor.sensor_id, since=now - timedelta(hours=PATTERN_WINDOW_HOURS))
    recent_from = now - timedelta(minutes=RECENT_MINUTES)
    recent = [r for r in history if r.recorded_at >= recent_from]
    if not recent:
        return None
    latest = recent[-1]
    above, _ = sensor.bounds()

    starts = []
    over = above is not None and latest.temperature_fahrenheit > above
    if over:
        # Back to the first reading of this spell over the line. Started
        # before the last hour: an old fault, not part of anything now.
        index = len(history) - 1
        while index > 0 and history[index - 1].temperature_fahrenheit > above:
            index -= 1
        if history[index].recorded_at < recent_from:
            return None
        starts.append(history[index].recorded_at)

    # The rise starts where the unit last left the bottom of its range in
    # the last hour, not at the first cold reading of the hour: a unit
    # cold for fifty minutes and warming for ten started ten minutes ago.
    bottom = min(r.temperature_fahrenheit for r in recent)
    rise = latest.temperature_fahrenheit - bottom
    if rise >= rise_f:
        band = bottom + rise_f / 4.0
        left = max((r for r in recent[:-1] if r.temperature_fahrenheit <= band),
                   key=lambda r: r.recorded_at, default=None)
        if left is not None:
            starts.append(left.recorded_at)
    if not starts:
        return None
    if _excused(sensor, history, above, over, now):
        return None
    return Warming(sensor=sensor, started_at=min(starts),
                   temperature_f=latest.temperature_fahrenheit,
                   limit_f=above, over_limit=over, rise_f=round(rise, 1))


def _together(found: List[Warming], window_minutes: float) -> List[Warming]:
    """The most units whose warming started within one window, latest first."""
    ordered = sorted(found, key=lambda w: w.started_at)
    best: List[Warming] = []
    left = 0
    span = timedelta(minutes=window_minutes)
    for right in range(len(ordered)):
        while ordered[right].started_at - ordered[left].started_at > span:
            left += 1
        if right - left + 1 >= len(best):
            best = ordered[left:right + 1]
    return best


def _last_alert_at(alerts, site_id: Optional[str],
                   business: bool = False) -> Optional[datetime]:
    """When the last site-wide alert covering this site (or, with
    `business`, the last one across sites) was opened."""
    if business:
        marks = [a.opened_at for a in alerts if a.group_scope == "business"]
    else:
        marks = [a.opened_at for a in alerts if site_id in a.group_site_ids]
    return max(marks) if marks else None


def assess(tenant: Tenant, now: Optional[datetime] = None) -> Dict[str, Any]:
    """What a sweep would raise for this business now, without raising it.

    Returns {"groups": [...], "outages": [...], "warming": [...]}; each
    group is {"scope", "site_ids", "units", "totals", "night"}.
    """
    now = now or utc_now()
    conf = settings()
    out: Dict[str, Any] = {"groups": [], "outages": [], "warming": []}
    if conf["min_units"] <= 0:
        return out

    by_site: Dict[Optional[str], List[Sensor]] = {}
    for sensor in STORE.sensors_for(tenant.tenant_id):
        by_site.setdefault(sensor.site_id, []).append(sensor)

    warming: Dict[Optional[str], List[Warming]] = {}
    for site_id, sensors in by_site.items():
        silent = [s for s in sensors if s.last_seen is not None and s.offline(now)]
        if len(sensors) >= 2 and len(silent) * 2 >= len(sensors):
            # Gone quiet together: the gateway or its power, not warming.
            out["outages"].append({"site_id": site_id, "silent": len(silent),
                                   "units": len(sensors)})
            continue
        found = [w for w in (_warming(s, now, conf["rise_f"]) for s in sensors) if w]
        if found:
            warming[site_id] = found
            out["warming"].extend(found)

    alerts = STORE.site_wide_alerts(tenant.tenant_id)
    totals = {site_id: len(sensors) for site_id, sensors in by_site.items()}

    # Across sites first: when it is happening at several, one alert that
    # says so is clearer than one per site.
    business_mark = _last_alert_at(alerts, None, business=True)
    candidates = [w for found in warming.values() for w in found
                  if business_mark is None or w.started_at > business_mark]
    covered: set = set()
    cluster = _together(candidates, conf["window_minutes"])
    sites = sorted({w.sensor.site_id or "" for w in cluster})
    if len(sites) >= max(conf["min_sites"], 2):
        night = all(is_night(tenant, site or None, cluster[0].started_at, conf)
                    for site in sites)
        needed = conf["cross_site_units_night"] if night else conf["cross_site_units"]
        if len(cluster) >= max(needed, 2):
            site_ids = sorted({w.sensor.site_id for w in cluster}, key=lambda s: s or "")
            out["groups"].append({
                "scope": "business", "site_ids": site_ids, "units": cluster,
                "total": sum(totals[s] for s in site_ids), "night": night,
            })
            covered = set(site_ids)

    for site_id, found in warming.items():
        if site_id in covered:
            continue
        mark = _last_alert_at(alerts, site_id)
        cluster = _together([w for w in found if mark is None or w.started_at > mark],
                            conf["window_minutes"])
        if len(cluster) < 2:
            continue
        night = is_night(tenant, site_id, cluster[0].started_at, conf)
        needed = conf["min_units_night"] if night else conf["min_units"]
        share = len(cluster) * 100.0 / max(totals[site_id], 1)
        if len(cluster) >= max(needed, 2) or share >= conf["min_share_percent"]:
            out["groups"].append({
                "scope": "site", "site_ids": [site_id], "units": cluster,
                "total": totals[site_id], "night": night,
            })
    return out


# ---- raising it ----------------------------------------------------------


def _unit_row(tenant: Tenant, w: Warming, site_names: Dict[str, str]) -> Dict[str, Any]:
    from store import display_temperature

    unit = tenant.temperature_unit
    return {
        "sensor_id": w.sensor.sensor_id,
        "location_name": w.sensor.location_name or w.sensor.sensor_id,
        "site_id": w.sensor.site_id,
        "site_name": site_names.get(w.sensor.site_id or "", "") or tenant.company_name,
        "industry_vertical": w.sensor.industry_vertical,
        "temperature_f": round(w.temperature_f, 2),
        "limit_f": w.limit_f,
        "temperature_display": display_temperature(w.temperature_f, unit),
        "limit_display": (display_temperature(w.limit_f, unit)
                          if w.limit_f is not None else None),
        "over_limit": w.over_limit,
        "rise_f": w.rise_f,
        "started_at": iso(w.started_at),
    }


def _send_texts(tenant: Tenant, recipients, text: str) -> List[Dict[str, Any]]:
    """Text everyone at once, through the one send path (caps and all)."""
    from concurrent.futures import ThreadPoolExecutor

    import telemetry

    if not recipients:
        return []
    with ThreadPoolExecutor(
        max_workers=min(len(recipients), telemetry.SMS_FANOUT_WORKERS),
        thread_name_prefix="sitewide-sms",
    ) as pool:
        receipts = pool.map(
            lambda c: telemetry.send_sms(c.phone, text, tenant.tenant_id), recipients)
        return [dict(r, contact_id=c.contact_id, contact_name=c.full_name)
                for c, r in zip(recipients, receipts)]


def raise_alerts(tenant: Tenant, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Open and send any site-wide alert this business needs now.

    Called from every sweep. Each alert is claimed in the store before a
    single text goes, so two sweeps at once open and send it once. The
    call that follows is the sweep's own, straight after this, with no
    grace (automation.sweep_tenant).
    """
    from auth import licence_state

    import wording

    now = now or utc_now()
    if licence_state(tenant, now) not in ("active", "grace"):
        return []
    found = assess(tenant, now)
    actions: List[Dict[str, Any]] = []
    for outage in found["outages"]:
        actions.append({
            "action": "site_wide_outage_not_warming",
            "site_id": outage["site_id"],
            "detail": (f"{outage['silent']} of {outage['units']} units went silent "
                       "together. That is an outage (power or the gateway), not "
                       "warming, so no site-wide alert was raised; the quiet-sensor "
                       "text covers it."),
        })
    if not found["groups"]:
        return actions

    site_names = {s.site_id: s.name for s in STORE.sites_for(tenant.tenant_id)}
    for group in found["groups"]:
        units = [_unit_row(tenant, w, site_names) for w in group["units"]]
        lead = max(group["units"], key=lambda w: w.temperature_f - (w.limit_f or 0)).sensor
        names = [site_names.get(s or "", "") or tenant.company_name
                 for s in group["site_ids"]]
        text = wording.site_wide_sms(tenant, group["scope"], len(units),
                                     group["total"], names)
        summary = wording.site_wide_summary(group["scope"], len(units),
                                            group["total"], names)
        incident, created = STORE.open_site_wide_once(
            tenant=tenant, lead=lead, scope=group["scope"],
            site_ids=group["site_ids"], units=units, total_units=group["total"],
            night=group["night"],
            temperature_fahrenheit=max(w.temperature_f for w in group["units"]),
            summary=summary, sms_text=text,
        )
        if not created:
            continue
        recipients = STORE.site_wide_recipients(tenant, group["site_ids"], "sms")
        fanout = _send_texts(tenant, recipients, text)
        STORE.record_sms_delivery(incident, fanout[0] if fanout else None, fanout)
        _email_owner(tenant, lead, incident)
        from webhooks import dispatch_event_later

        dispatch_event_later(tenant, incident, lead, "opened", summary)
        logger.critical("Site-wide alert %s for tenant %s: %s",
                        incident.incident_id, tenant.tenant_id, summary)
        actions.append({
            "action": "site_wide_alert_raised",
            "incident_id": incident.incident_id,
            "scope": group["scope"],
            "site_ids": group["site_ids"],
            "sensor_ids": [u["sensor_id"] for u in units],
            "night": group["night"],
            "text": text,
            "texted": len(fanout),
            "delivered": sum(1 for d in fanout if d.get("delivered")),
        })
    return actions


def _email_owner(tenant: Tenant, lead: Sensor, incident) -> None:
    import telemetry

    telemetry._email_later(tenant, lead, incident)
