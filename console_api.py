"""Read model for the operations console.

The console needs the fleet, its recent history, forecasts, incidents and
headline counts on every refresh. Serving them as one bundle keeps the UI
to a single request per poll instead of one per sensor.
"""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

import food_value
from forecaster import forecast_sensor
from auth import require_tenant_any_state
from claims import closed_how
from records import person_name
from repairs import unit_state
from telemetry import unit_condition
from licenses import require_tenant
from store import (
    PLAN_TIERS,
    INDUSTRY_PROFILES,
    STORE,
    VOICE_ESCALATION_GRACE_MINUTES,
    Tenant,
    _parse,
    display_temperature,
    iso,
    utc_now,
)

router = APIRouter(prefix="/api/console", tags=["Operations Console"])

# Points in a sensor card's sparkline.
SPARKLINE_POINTS = 12



# How close to the end of a term counts as "ending soon". A fortnight is
# long enough to get a purchase order signed and short enough that the
# warning still means something.
#
# Capped at half the term, because a flat fortnight put a 14-day trial in
# its "ending soon" state on the first morning. A banner that is on from
# the moment you arrive is not a warning, it is decoration, and it stops
# being read well before the day it matters.
RENEWAL_WARNING_DAYS = 14


def warning_window(tenant) -> int:
    term = PLAN_TIERS.get(tenant.plan, {}).get("term_days", 365)
    return max(1, min(RENEWAL_WARNING_DAYS, term // 2))


def account_state(tenant, now=None) -> Dict[str, Any]:
    """Whether this estate is about to stop being watched, and what to do.

    The console had no idea about any of this. A trial ran out overnight
    and the only sign was that everything started returning 402 the next
    morning — no warning the day before, no figure for what it would cost
    to carry on, and nothing on the page that would take the money.
    """
    from auth import LICENCE_GRACE_DAYS, grace_days_left, licence_state, under_contract
    from contracts import DELINQUENT_AFTER_DAYS, delinquency
    from mail import pay_link
    from pricing import build_subscription

    now = now or utc_now()
    state = licence_state(tenant, now)
    # Under a running contract the licence's own end date stops nothing
    # (auth.under_contract), so a banner counting down to it was telling a
    # paying customer their monitoring was about to stop when it was not.
    contracted = under_contract(tenant, now)
    # Rounded up, not truncated. Six days minus a few seconds is not five
    # days, and this figure goes straight onto a banner.
    hours_left = (
        (tenant.expires_at - now).total_seconds() / 3600
        if tenant.expires_at
        else None
    )
    days_left = None if hours_left is None else math.ceil(hours_left / 24)
    money = delinquency(tenant.tenant_id, now)
    subscription = build_subscription(tenant)
    contract = STORE.active_subscription(tenant.tenant_id)

    from retention import current_pause, public_pause

    pause = current_pause(tenant.tenant_id, now)
    if state == "suspended":
        headline = "This license is suspended. Get in touch to restore it."
        severity = "critical"
    elif pause is not None:
        # The loudest thing on the page while it lasts: an owner who paused
        # in spring must not think in summer that the freezer is watched.
        headline = public_pause(pause)["words"]
        severity = "critical"
    elif state == "lapsed":
        headline = (
            "This estate is no longer being monitored. Move onto a paid "
            "plan to restore it."
        )
        severity = "critical"
    elif state == "grace":
        left = grace_days_left(tenant, now)
        headline = (
            f"The license expired. Monitoring continues for {left} more "
            f"day{'' if left == 1 else 's'}, then stops. Reporting is "
            "withheld until it is renewed."
        )
        severity = "critical"
    elif (not contracted and days_left is not None
          and days_left <= warning_window(tenant)):
        subject = "The trial" if tenant.plan == "trial" else "This license"
        # Measured in hours, because a day count cannot tell "this evening"
        # from "tomorrow evening" — and those read very differently to
        # somebody deciding whether to deal with it now.
        when = (
            "ends today" if hours_left < 24
            else "ends tomorrow" if hours_left < 48
            else f"ends in {days_left} days"
        )
        headline = (
            f"{subject} {when}. After that there are {LICENCE_GRACE_DAYS} "
            "days of grace, then monitoring stops."
        )
        severity = "warning"
    elif money["delinquent"]:
        headline = (
            f"${money['overdue_usd']:,.2f} is {money['days_overdue']} days "
            "past due. Reporting is withheld until it clears."
        )
        severity = "warning"
    elif money["overdue_count"]:
        # Late, not yet delinquent. The console said nothing here at all:
        # the first a customer saw of a late bill on the page was reports
        # disappearing on day 45, which the chase emails had warned of
        # and the screen they actually look at had not.
        days = money["days_overdue"]
        left = max(1, DELINQUENT_AFTER_DAYS - days)
        when = (
            "fell due today" if days < 1
            else f"is {days} day{'' if days == 1 else 's'} past due"
        )
        headline = (
            f"${money['overdue_usd']:,.2f} {when}. Reports pause if it is "
            f"still unpaid in {left} day{'' if left == 1 else 's'}."
        )
        severity = "warning"
    else:
        headline = None
        severity = "ok"

    # The quickest way to settle what is late: the pay link for the oldest
    # overdue bill, carrying its invoice id so the payment matches itself.
    overdue = sorted(
        (i for i in STORE.open_invoices(tenant.tenant_id) if i.overdue(now)),
        key=lambda i: i.due_at,
    )
    pay_url = pay_link(overdue[0]) if overdue else ""

    return {
        "licence_state": state,
        "under_contract": contracted,
        "pay_url": pay_url,
        "severity": severity,
        "headline": headline,
        "plan": tenant.plan,
        "on_trial": tenant.plan == "trial",
        "days_to_expiry": days_left,
        "warning_window_days": warning_window(tenant),
        "grace_days_left": grace_days_left(tenant, now),
        "expires_at": iso(tenant.expires_at),
        "has_contract": contract is not None,
        # What carrying on would cost, priced from the estate they have
        # actually built rather than from a tier they have to guess at.
        "monthly_if_paid_usd": subscription["monthly_total_usd"],
        "annual_if_paid_usd": subscription["annual_total_usd"],
        "collections": money,
        # Always true, and worth saying on the page rather than only in the
        # agreement: none of this touches the alarm. A pause the owner chose
        # is the one exception, and says so.
        "alerting_continues": state != "lapsed" and pause is None,
        "paused": public_pause(pause) if pause is not None else None,
    }


@router.get("/overview")
def console_overview(
    compliance_days: int = Query(7, ge=1, le=90),
    tenant: Tenant = Depends(require_tenant_any_state),
) -> Dict[str, Any]:
    """Everything the console renders, in one payload.

    Readable even when the licence has lapsed. This is the page that says
    what happened and carries the button that fixes it — refusing it left
    a customer with a blank 402 and no way to work out what to do next.
    """
    now = utc_now()
    entitlements = tenant.entitlements()
    forecasting = entitlements["predictive_forecasting"]
    unit = tenant.temperature_unit
    sites = {s.site_id: s for s in STORE.sites_for(tenant.tenant_id)}

    # The live alert on each unit, found once rather than once a unit.
    live_by_unit: Dict[str, Any] = {}
    # And the newest alert on each unit, live or closed, for the repair
    # offer: a unit somebody marked fixed is not offered a repair (3.28.0).
    latest_by_unit: Dict[str, Any] = {}
    for incident in STORE.incidents_for(tenant.tenant_id):
        if incident.unresolved and incident.sensor_id not in live_by_unit:
            live_by_unit[incident.sensor_id] = incident
        if not incident.site_wide and incident.sensor_id not in latest_by_unit:
            latest_by_unit[incident.sensor_id] = incident

    sensors: List[Dict[str, Any]] = []
    for sensor in sorted(STORE.sensors_for(tenant.tenant_id), key=lambda s: s.sensor_id):
        history = STORE.readings_for(sensor.sensor_id)[-SPARKLINE_POINTS:]
        profile = INDUSTRY_PROFILES[sensor.industry_vertical]

        row = sensor.public(unit)
        row["catastrophe"] = profile["catastrophe"]
        row["breaching"] = bool(history) and history[-1].breached
        # The sparkline is drawn in the tenant's unit; mixing units on one
        # chart is how somebody reads 4° as safe when it is 4°F.
        row["spark"] = [
            display_temperature(r.temperature_fahrenheit, unit) for r in history
        ]
        row["spark_breached"] = [r.breached for r in history]
        row["spark_at"] = [iso(r.recorded_at) for r in history]
        site = sites.get(sensor.site_id) if sensor.site_id else None
        row["site_name"] = site.name if site else None

        projection = None
        if forecasting:
            projection = forecast_sensor(sensor.sensor_id, window_hours=12.0)
            row["risk_level"] = projection["risk_level"]
            row["hours_until_breach"] = projection["hours_until_breach"]
            row["trend_f_per_hour"] = projection.get("trend_f_per_hour")
            row["already_breached"] = bool(projection.get("already_breached"))
        else:
            row["risk_level"] = None
            row["hours_until_breach"] = None
            row["trend_f_per_hour"] = None
        # "Get a repair quote", when the forecaster or the pattern check
        # says this unit is failing or drifting warm (repairs.py), or the
        # request already sent for it. On every plan: it costs nothing.
        row["repair"] = unit_state(tenant, sensor, projection,
                                   latest=latest_by_unit.get(sensor.sensor_id))
        # What every page says about this unit now: waiting for its first
        # reading, fine, watching (out of range, inside its wait), alert,
        # or offline, with the one "out since" and "we text at" they all
        # show (telemetry.unit_condition).
        row["condition"] = unit_condition(
            tenant, sensor, now, incident=live_by_unit.get(sensor.sensor_id))

        sensors.append(row)

    incidents = []
    for incident in STORE.incidents_for(tenant.tenant_id):
        row = incident.public()
        row["temperature_display"] = display_temperature(
            incident.temperature_fahrenheit, unit
        )
        # The first reading out of range and the worst since, in the
        # account's scale (None on an alert from before they were kept).
        row["first_out_display"] = display_temperature(incident.first_out_f, unit)
        row["peak_display"] = display_temperature(incident.peak_f, unit)
        row["temperature_unit"] = unit
        # How it closed, as the pack says it (claims.closed_how, 3.41.0),
        # and the reading it closed on.
        row["closed_how"] = closed_how(incident)
        row["close_display"] = display_temperature(incident.close_temperature_f, unit)
        # On a closed alert: what the food in it was roughly worth, as an
        # estimate (food_value.py, where the owner tunes the figures),
        # only once somebody said it was kept. Until somebody answers, the
        # food question: "Back in range in 12 minutes" or "Food at risk:
        # check it", with Food kept / Thrown away (3.30.0).
        unit_sensor = STORE.get_sensor(incident.sensor_id)
        row["food_protected"] = food_value.protected(incident, unit_sensor)
        row["food_at_risk"] = food_value.at_risk(incident, unit_sensor)
        row["food_question"] = food_value.question(incident, unit_sensor, now)
        incidents.append(row)
    # Food thrown away in the last day (3.33.0): /today's headline says so,
    # calmly, with the insurance pack one tap away, instead of "Everything
    # is fine... nothing for you to do".
    recent_loss = loss_headline(tenant, now)
    # The possible attack or site-wide failure alerts, apart from the
    # units' own: the console shows them as one banner each, above
    # everything, with the units in them. Live ones and the last week's.
    week_ago = now - timedelta(days=7)
    site_wide = []
    for incident in STORE.site_wide_alerts(tenant.tenant_id):
        if not incident.unresolved and incident.opened_at < week_ago:
            continue
        row = incident.public()
        row["temperature_unit"] = unit
        # In the scale the account reads now, whatever it was when raised.
        row["group_units"] = [
            dict(u, temperature_display=display_temperature(u.get("temperature_f"), unit),
                 limit_display=display_temperature(u.get("limit_f"), unit))
            for u in incident.group_units]
        row["sites"] = sorted({u.get("site_name") or "" for u in incident.group_units} - {""})
        site_wide.append(row)
    open_incidents = STORE.open_incidents(tenant.tenant_id)
    # Every alert that is not closed: nobody has answered it yet, or
    # somebody has it and it is not fixed. The "Open alerts" tile said 0
    # while Maya was handling one, because it counted only the first kind.
    live_alerts = [i for i in STORE.incidents_for(tenant.tenant_id) if i.unresolved]
    handled_by = sorted({person_name(i.acknowledged_by) or "somebody"
                         for i in live_alerts if not i.open})
    escalation_due = sum(
        1
        for i in open_incidents
        if i.voice_escalated_at is None
        and i.minutes_open(now) >= VOICE_ESCALATION_GRACE_MINUTES
    )

    since = now - timedelta(days=compliance_days)
    logged, breached, data_since = _week_counts(tenant, since, compliance_days, now)
    # A percentage of five minutes is not a figure: "80%" after one warm
    # reading out of five. Shown once there is a day of readings.
    first = _parse(data_since) if data_since else None
    compliance_ready = first is not None and now - first >= timedelta(
        hours=COMPLIANCE_AFTER_HOURS)

    # The estate's own vocabulary. One vertical means we can speak it
    # throughout; a mixed estate has to fall back to something neutral.
    verticals = {s["industry_vertical"] for s in sensors}
    if len(verticals) == 1:
        only = INDUSTRY_PROFILES[next(iter(verticals))]
        fleet_noun, fleet_plural = only["asset_noun"], only["asset_plural"]
    else:
        fleet_noun, fleet_plural = "asset", "assets"

    # Out of range now is an alert, not a forecast (3.39.0): the At-risk
    # tile counted the fridge already at 46.5°F.
    at_risk = sum(
        1
        for s in sensors
        if s["risk_level"] in {"critical", "high", "elevated"}
        and not s["breaching"] and not s.get("already_breached")
    )

    from costs import calls_offer
    from firstday import checklist
    from helpdesk import support_address
    from pricing import build_roi, build_subscription, growth_terms, starter_terms

    subscription = build_subscription(tenant)
    roi = build_roi(tenant, 30)

    return {
        "generated_at": iso(now),
        "tenant": tenant.public(sensor_count=len(sensors)),
        "account": account_state(tenant, now),
        "subscription": subscription,
        # What the plan buttons on What you pay say, from pricing.py.
        "starter": starter_terms(),
        # Growth as /pricing says it, for the plan buttons (3.30.0): "$149
        # a month a location, up to 10 units a location".
        "plan_prices": growth_terms(),
        "roi": roi,
        "entitlements": {
            "voice_escalation": entitlements["voice_escalation"],
            "predictive_forecasting": forecasting,
            # Whether an alert would ring a phone, and the words for it:
            # the plan's calls, the trial's few, or texts only.
            "calls": calls_offer(tenant),
        },
        "temperature_unit": unit,
        "fleet_noun": fleet_noun,
        "fleet_plural": fleet_plural,
        "sites": [
            site.public(
                sensor_count=sum(
                    1 for s in sensors if s["site_id"] == site.site_id
                ),
                online=sum(
                    1
                    for s in sensors
                    if s["site_id"] == site.site_id and s["online"]
                ),
            )
            for site in sites.values()
        ],
        "summary": {
            "sensors_total": len(sensors),
            "sensors_online": sum(1 for s in sensors if s["online"]),
            "sensors_breaching": sum(1 for s in sensors if s["breaching"]),
            "sensors_watching": sum(1 for s in sensors
                                    if s["condition"]["state"] == "watching"),
            "low_battery": sum(1 for s in sensors if s["battery_low"]),
            "unplaced_sensors": sum(1 for s in sensors if not s["site_id"]),
            "open_incidents": len(live_alerts),
            "unanswered_incidents": len(open_incidents),
            "handled_incidents": len(live_alerts) - len(open_incidents),
            "handled_by": handled_by,
            "escalation_due": escalation_due,
            "at_risk": at_risk,
            "readings_logged": logged,
            "readings_breached": breached,
            "compliance_percent": in_range_percent(
                logged, breached, live=bool(live_alerts) or any(s["breaching"] for s in sensors)),
            "compliance_days": compliance_days,
            "compliance_ready": compliance_ready,
            "compliance_after_hours": COMPLIANCE_AFTER_HOURS,
        },
        "sensors": sensors,
        "incidents": incidents,
        "site_wide_alerts": site_wide,
        "grace_window_minutes": VOICE_ESCALATION_GRACE_MINUTES,
        # The first-day checklist, ticked from what the account holds.
        "first_day": checklist(tenant),
        # "This month: 1 alert, answered in 4 minutes, food kept." for the
        # Simple view (3.28.0), by the same rule as each alert's line.
        "this_month": month_summary(tenant, now),
        # The newest food thrown away in the last 24 hours, or None.
        "recent_loss": recent_loss,
        # Who a text goes to right now, first names only (3.35.0), for
        # Pilot's "Who gets the texts?" to the person signed in: in their
        # browser, never sent anywhere.
        "texted_first_names": texted_first_names(tenant),
        # A short blip's food is recorded safe by itself under this many
        # minutes out (food_value.SHORT_MINUTES, 3.32.0); 0 when it is off.
        "food_safe_minutes": food_value.SHORT_MINUTES,
        # Where the About card tells people to write, or "" for only /help.
        "support_email": support_address(),
    }


def texted_first_names(tenant: Tenant, most: int = 6) -> list:
    """First names of everyone an alert text goes to now, in ladder order,
    each once: ["Maya", "Sam"]. Never a surname, a number or an email."""
    names: list = []
    try:
        people = STORE.sms_recipients(tenant)
    except Exception:  # noqa: BLE001 - a roster problem must not cost the overview
        return names
    for person in people:
        first = (str(getattr(person, "full_name", "") or "").split() or [""])[0].strip()
        if first and first not in names:
            names.append(first)
    return names[:most]


# How long /today leads with food thrown away (3.33.0).
LOSS_HEADLINE_HOURS = 24


def loss_headline(tenant: Tenant, now) -> Optional[Dict[str, Any]]:
    """The newest alert whose food was thrown away in the last day, for
    /today's calm headline: "Food thrown away from Walk-in fridge: make
    the insurance pack". None when there is none."""
    since = now - timedelta(hours=LOSS_HEADLINE_HOURS)
    lost = [i for i in STORE.incidents_for(tenant.tenant_id)
            if not i.site_wide and i.product_disposition == "discarded"
            and i.food_answered_at is not None and i.food_answered_at >= since]
    if not lost:
        return None
    i = max(lost, key=lambda x: x.food_answered_at)
    sensor = STORE.get_sensor(i.sensor_id)
    return {
        "incident_id": i.incident_id,
        # The unit's id too (3.38.0), so /today names it as everywhere else
        # on the page ("Prep fridge, bakery at Juniper Commissary"), not by
        # its raw "Commissary / Prep fridge, bakery".
        "sensor_id": i.sensor_id,
        "unit": (sensor.location_name if sensor and sensor.location_name else i.sensor_id),
        "at": iso(i.food_answered_at),
        "by_name": person_name(i.food_answered_by),
        "note": i.corrective_action or None,
        "until": iso(i.food_answered_at + timedelta(hours=LOSS_HEADLINE_HOURS)),
        "others": len(lost) - 1,
    }


def month_summary(tenant: Tenant, now) -> Dict[str, Any]:
    """This calendar month so far, in one line a café owner can read.

    {"alerts", "answered", "typical_answer_minutes", "food_kept",
    "food_thrown", "food_unanswered", "food_at_risk", "food_usd",
    "food_answers", "words"}. Only the units' own alerts (a site-wide one
    sits on top of them). The food follows the saved answers only
    (food_value.month_total, 3.30.0): "food kept" once somebody tapped
    Food kept, never because the fridge came back quickly.
    `food_answers` is who said what and when, newest first, for /today
    ("Walk-in fridge: Thrown away, Jo, 4:09 PM"), with "Change" for 24
    hours (3.32.0). `words` is one short line since 3.32.0: "This month: 3
    alerts · all answered · 1 food answer missing".
    """
    from statistics import median

    from domain import minutes_words

    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    alerts = [i for i in STORE.incidents_for(tenant.tenant_id, since=start)
              if not i.site_wide and i.opened_at >= start]
    answered = [i for i in alerts if i.acknowledged_at is not None]
    waits = [max(0.0, (i.acknowledged_at - i.opened_at).total_seconds() / 60.0)
             for i in answered]
    typical = round(median(waits), 1) if waits else None
    sensors = {i.sensor_id: STORE.get_sensor(i.sensor_id) for i in alerts}
    food = food_value.month_total((i, sensors[i.sensor_id]) for i in alerts)
    kept, thrown = food["alerts"], food["thrown"]
    risky, unanswered = food["at_risk"], food["unanswered"]

    answers = []
    for i in sorted((i for i in alerts if i.product_disposition and i.food_answered_at),
                    key=lambda i: i.food_answered_at, reverse=True)[:3]:
        sensor = sensors[i.sensor_id]
        until = i.food_change_until()
        answers.append({
            "incident_id": i.incident_id,
            "unit": (sensor.location_name if sensor and sensor.location_name else i.sensor_id),
            "words": i.food_words(),
            "by_name": None if i.food_auto_minutes is not None else person_name(i.food_answered_by),
            "at": iso(i.food_answered_at),
            # 3.32.0: recorded by itself for a short blip; thrown away (the
            # page offers the insurance pack and a note of what went); and
            # whether "Change" still shows (24 hours).
            "auto": i.food_auto_minutes is not None,
            "thrown": i.product_disposition == "discarded",
            "disposition": i.product_disposition,
            "change_until": iso(until),
            "changeable": until is not None and now <= until,
            "has_note": bool(i.corrective_action),
            # The note of what went (3.33.0), shown under the answer.
            "note": i.corrective_action or None,
        })

    # One short line of two items (3.35.0): how many alerts, and the one
    # thing that matters most about them: "2 alerts · 1 food answer
    # missing". Four or five ran to three lines on a phone. Most pressing
    # first: an alert nobody has answered, the food nobody has answered
    # for, food thrown away, how fast it was answered, cold again by
    # itself, the food kept. The figures behind it are below, for the
    # weekly summary, and each answer is its own row under the line.
    n = len(alerts)
    if not n:
        words = "This month: no alerts. Every fridge and freezer stayed in range."
    else:
        waiting = sum(1 for i in alerts if i.acknowledged_at is None and i.resolved_at is None)
        by_itself = sum(1 for i in alerts if i.acknowledged_at is None and i.resolved_at is not None)
        missing = risky + unanswered
        if waiting:
            second = "not answered yet" if waiting == n else f"{waiting} not answered yet"
        elif missing:
            second = f"{missing} food answer{'' if missing == 1 else 's'} missing"
        elif thrown:
            second = "food thrown away" if thrown == n else f"food thrown away from {thrown}"
        elif len(answered) == n:
            second = (f"answered in {minutes_words(typical)}" if n == 1 and typical is not None
                      else "all answered")
        elif by_itself == n:
            second = "cold again by itself" if n == 1 else "all cold again by themselves"
        elif answered:
            second = f"{len(answered)} answered"
        elif kept or food["auto_safe"]:
            second = "food kept" if not food["auto_safe"] else "food safe"
        else:
            second = ""
        words = f"This month: {n} alert{'' if n == 1 else 's'}" + (f" · {second}" if second else "")
    return {
        "alerts": n,
        "answered": len(answered),
        "typical_answer_minutes": typical,
        "food_kept": kept,
        "food_thrown": thrown,
        "food_unanswered": unanswered,
        "food_at_risk": risky,
        "food_usd": food["usd"],
        "food_auto_safe": food["auto_safe"],
        "food_answers": answers,
        "words": words,
    }


def in_range_percent(logged: int, breached: int, live: bool = False) -> Optional[float]:
    """The share of readings in range, to one decimal, that never rounds up
    to 100% while any reading was out of range or anything is out of range
    or alerting now (3.37.0): 1 warm reading in 2,000 is 99.95%, which
    rounded to "100%" beside an open alert. 99.9% is the most it says
    then."""
    if not logged:
        return None
    share = round((logged - breached) / logged * 100, 1)
    if (breached or live) and share >= 100.0:
        return 99.9
    return share


# Hours of readings before the console shows a compliance percentage.
COMPLIANCE_AFTER_HOURS = 24
# How long the console's reading counts are reused before they are counted
# again in full. Counted over every reading kept (a week at one a minute is
# ten thousand a sensor), which is about 15 ms a sensor, and the console
# asks every twenty seconds. In between, the readings that arrived since
# are added from memory, so a new reading shows on the very next poll.
COUNTS_FRESH_SECONDS = 600
_counts: Dict[Any, Any] = {}


def _week_counts(tenant: Tenant, since, days: int, now):
    """(readings, out of range, oldest held) over the console's window."""
    key = (tenant.tenant_id, days)
    hit = _counts.get(key)
    if hit is None or (now - hit[0]).total_seconds() >= COUNTS_FRESH_SECONDS:
        per_sensor: Dict[str, Any] = {}
        for sensor in STORE.sensors_for(tenant.tenant_id):
            # Counted by SQLite over every reading kept, not only the ones
            # in memory, so a busy sensor's week is the whole week.
            stats = STORE.reading_stats(sensor.sensor_id, since=since)
            per_sensor[sensor.sensor_id] = (stats["count"], stats["breached"], stats["oldest"])
        if len(_counts) > 5000:
            _counts.clear()
        hit = (now, per_sensor)
        _counts[key] = hit
    counted_at, per_sensor = hit
    logged = breached = 0
    data_since = None
    for sensor in STORE.sensors_for(tenant.tenant_id):
        base = per_sensor.get(sensor.sensor_id)
        if base is None:                        # added since the count
            base = (0, 0, None)
            fresh = STORE.readings_for(sensor.sensor_id)
        else:
            fresh = [r for r in STORE.readings_for(sensor.sensor_id)
                     if r.recorded_at > counted_at]
        logged += base[0] + len(fresh)
        breached += base[1] + sum(1 for r in fresh if r.breached)
        oldest = base[2] or (iso(fresh[0].recorded_at) if fresh else None)
        if oldest and (data_since is None or oldest < data_since):
            data_since = oldest
    return logged, breached, data_since


def forget_counts() -> None:
    """Drop the reused counts. For tests, which reuse tenant ids."""
    _counts.clear()


def _daily(sensor, unit: str) -> List[Dict[str, Any]]:
    """Lowest, highest and out-of-range count a day, newest first."""
    from store import history_cutoff

    since = history_cutoff()
    days: Dict[str, Dict[str, Any]] = {}

    def add(day, low, high, count, out):
        slot = days.setdefault(day, {"day": day, "low": low, "high": high,
                                     "readings": 0, "out_of_range": 0})
        slot["low"], slot["high"] = min(slot["low"], low), max(slot["high"], high)
        slot["readings"] += count
        slot["out_of_range"] += out

    for hour in STORE.hourly_for(sensor.sensor_id, since):
        add(hour["hour"][:10], hour["min_f"], hour["max_f"], hour["count"], hour["breached"])
    for reading in STORE.readings_for(sensor.sensor_id, since=since, complete=True):
        t = reading.temperature_fahrenheit
        add(iso(reading.recorded_at)[:10], t, t, 1, 1 if reading.breached else 0)
    return [dict(slot, low=display_temperature(slot["low"], unit),
                 high=display_temperature(slot["high"], unit))
            for _, slot in sorted(days.items(), reverse=True)]


# Readings shown on a sensor's own page. Twelve points is enough for a card
# and useless for working out what actually happened overnight.
DETAIL_POINTS = 120


@router.get("/sensor/{sensor_id}")
def sensor_detail(
    sensor_id: str,
    tenant: Tenant = Depends(require_tenant),
) -> Dict[str, Any]:
    """One sensor in full: history, incidents, forecast and health.

    The fleet card answers "is this thing alright". This answers "what
    happened", which is the question anyone asks the moment it is not.
    """
    sensor = STORE.get_sensor((sensor_id or "").strip())
    if sensor is None or sensor.tenant_id != tenant.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sensor '{sensor_id}' is not registered to this tenant.",
        )

    unit = tenant.temperature_unit
    history = STORE.readings_for(sensor.sensor_id)[-DETAIL_POINTS:]
    site = STORE.get_site(sensor.site_id) if sensor.site_id else None
    kept = STORE.reading_stats(sensor.sensor_id)

    incidents = [
        i.public()
        for i in STORE.incidents_for(tenant.tenant_id)
        if i.sensor_id == sensor.sensor_id
    ]

    row = sensor.public(unit)
    row["catastrophe"] = INDUSTRY_PROFILES[sensor.industry_vertical]["catastrophe"]
    row["site_name"] = site.name if site else None

    forecast = None
    if tenant.entitlements()["predictive_forecasting"]:
        forecast = forecast_sensor(sensor.sensor_id, window_hours=12.0)

    return {
        "sensor": row,
        "temperature_unit": unit,
        "readings": [
            {
                "at": iso(r.recorded_at),
                "temperature": display_temperature(r.temperature_fahrenheit, unit),
                "humidity": r.humidity_percent,
                "breached": r.breached,
            }
            for r in history
        ],
        "readings_total": kept["count"],
        "readings_kept_since": kept["oldest"],
        # One line a day for as long as readings are kept: the lowest and
        # highest, from every reading and from the hourly rows kept for
        # older ones. What the inspector asks about "last March".
        "daily": _daily(sensor, unit),
        "breached_count": sum(1 for r in history if r.breached),
        "incidents": incidents,
        # Not closed: unanswered, or somebody has it and it is not fixed.
        "open_incidents": sum(1 for i in incidents if i["state"] != "resolved"),
        "forecast": forecast,
    }
