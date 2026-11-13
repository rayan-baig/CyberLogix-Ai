"""The month on one page, by email, to each business's owners.

The weekly report says the week went fine. An owner, an auditor or an
insurer asks a different question once a month: how many units were
watched, how much was recorded, every time something went out of range,
who answered it and how quickly, and what is still not sorted. This is
that, short enough to read on a phone, with the inspector's log for the
whole month one link away.

Rules it keeps:

* **Owners only.** It names who answered each alert, and an operator or
  a viewer is not who a business's own record of its staff goes to.
* **Once per month per business.** Each owner's copy is a message with
  its own key, claimed before it is sent (mail.py), and the business's
  month is marked done when the pass has been round its owners. Both are
  rows in the database, so a restart does not send it again, and a
  restore from before it went out carries the mark forward (store.py,
  the delivery ledger).
* **One pass at a time.** The hourly money pass and an operator pressing
  send can meet; the second steps aside, as a second fleet sweep does.
* **Only that business.** Everything is read through the one tenant, and
  a person who owns two businesses gets two reports, one about each.
* **Reports pause with the rest.** A business more than 45 days behind,
  or past its licence's grace, gets no report until it is current, which
  is what the agreement says of reports. Its alerts are untouched.

It goes out from the first of the month, after the digest hour, and for
a week after that, so a server that was down on the first still sends
it; after a week, last month's is left unsent rather than arriving late.
"""

from __future__ import annotations

import logging
import re
import statistics
import threading
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import require_platform_admin
from contracts import delinquency, require_current
from mail import send as send_mail
from store import STORE, Tenant, User, iso, utc_now

logger = logging.getLogger("cyberlogix.monthly")

router = APIRouter(prefix="/api/digest", tags=["Digest"])

KIND = "monthly_report"

# Days into the month during which last month's report may still go out.
SEND_WITHIN_DAYS = 7

# Open problems listed by name; the rest are counted.
LIST_AT_MOST = 8

# One monthly pass at a time, in this process. Not the fleet sweep's own
# lock: a month of reports for every business must never hold up the
# minute-by-minute sweep that phones people about freezers.
_pass_lock = threading.Lock()


# ---- which month --------------------------------------------------------


def previous_month(now: datetime) -> Tuple[date, date]:
    """The first and last day of the calendar month before `now` (UTC)."""
    first_of_this = now.astimezone(timezone.utc).date().replace(day=1)
    last = first_of_this - timedelta(days=1)
    return last.replace(day=1), last


def month_key(first: date) -> str:
    return first.strftime("%Y-%m")


def month_label(first: date) -> str:
    return first.strftime("%B %Y")


def due(now: datetime) -> bool:
    """Whether last month's report may go out now."""
    from digest import DIGEST_HOUR_UTC

    now = now.astimezone(timezone.utc)
    if now.day > SEND_WITHIN_DAYS:
        return False
    return now.day > 1 or now.hour >= DIGEST_HOUR_UTC


def _record_id(tenant_id: str, first: date) -> str:
    return f"{tenant_id}:{month_key(first)}"


def already_sent(tenant_id: str, first: date) -> bool:
    return STORE._db.get(KIND, _record_id(tenant_id, first)) is not None


# ---- who it goes to -----------------------------------------------------


def owners(tenant_id: str) -> List[User]:
    """This business's owners who have not been removed, one per address."""
    seen = set()
    found = []
    for user in STORE.users_for(tenant_id):
        if user.role != "owner" or user.disabled:
            continue
        address = (user.email or "").strip().lower()
        if not address or address in seen:
            continue
        seen.add(address)
        found.append(user)
    return found


# ---- what it says -------------------------------------------------------


def _name(actor: str) -> str:
    """'Robin Vale <robin@...>' is the audit label; the report names people."""
    return re.sub(r"\s*<[^>]*>\s*$", "", actor or "").strip() or "Someone"


def _when(value: Optional[str]) -> str:
    """A stamp as a person reads it: 3 Oct, 14:05 UTC."""
    if not value:
        return ""
    at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return f"{at.day} {at.strftime('%b')}, {at.strftime('%H:%M')} UTC"


def _minutes(value: Optional[float]) -> str:
    if value is None:
        return ""
    value = int(round(value))
    if value < 60:
        return f"{value} min"
    hours, minutes = divmod(value, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def inspector_link(tenant: Tenant, first: date, last: date) -> str:
    """The console's inspector log for this month, for this business."""
    from mail import PUBLIC_BASE_URL

    if not PUBLIC_BASE_URL:
        return ""
    return (f"{PUBLIC_BASE_URL}/console#inspector-log?business={tenant.tenant_id}"
            f"&from={first.isoformat()}&to={last.isoformat()}")


def summary(tenant: Tenant, first: date, last: date,
            now: Optional[datetime] = None) -> Dict[str, Any]:
    """The month's figures for one business, from its own records only.

    Readings and out-of-range spells come from the inspector's log for
    the same dates, so the email and the log it links to cannot disagree.
    """
    from inspector import build_log

    now = now or utc_now()
    start = datetime.combine(first, time.min, tzinfo=timezone.utc)
    end = datetime.combine(last + timedelta(days=1), time.min, tzinfo=timezone.utc)
    log = build_log(tenant, first, last)

    readings = sum(u["readings"] for u in log["units"])
    out_of_range = sum(u["out_of_range_readings"] for u in log["units"])

    breaches = []
    for unit in log["units"]:
        for b in unit["breaches"]:
            opened = datetime.fromisoformat(b["opened_at"].replace("Z", "+00:00"))
            if not start <= opened < end:
                continue  # began in another month: that month's report has it
            breaches.append({
                "unit": unit["location_name"],
                "site": unit["site_name"],
                "opened_at": b["opened_at"],
                "worst": b["worst"],
                "acknowledged_by": _name(b["acknowledged_by"]) if b["acknowledged_by"] else "",
                "minutes_to_acknowledge": b["minutes_to_acknowledge"],
                "closed": bool(b["closed_at"]),
            })
    breaches.sort(key=lambda b: b["opened_at"])

    answered = [b for b in breaches if b["acknowledged_by"]
                and b["minutes_to_acknowledge"] is not None]
    people: Dict[str, List[float]] = {}
    for b in answered:
        people.setdefault(b["acknowledged_by"], []).append(b["minutes_to_acknowledge"])
    who = sorted(
        ({"name": name, "acknowledged": len(times),
          "median_minutes": statistics.median(times),
          "slowest_minutes": max(times)} for name, times in people.items()),
        key=lambda row: (-row["acknowledged"], row["name"]),
    )

    site_wide = [
        {"summary": a["summary"], "opened_at": a["opened_at"],
         "units": a["units"], "at_night": a["at_night"],
         "acknowledged_by": _name(a["acknowledged_by"]) if a["acknowledged_by"] else "",
         "minutes_to_acknowledge": a["minutes_to_acknowledge"]}
        for a in log.get("site_wide_alerts", [])
    ]

    sensors = STORE.sensors_for(tenant.tenant_id)
    names = {s.sensor_id: s.location_name or s.sensor_id for s in sensors}
    open_alerts = sorted(
        ({"unit": names.get(i.sensor_id, i.sensor_id), "since": iso(i.opened_at),
          "acknowledged": i.acknowledged_at is not None}
         for i in STORE.incidents_for(tenant.tenant_id) if i.resolved_at is None),
        key=lambda row: row["since"] or "",
    )
    open_alerts += [
        {"unit": i.breach_details.rstrip("."), "since": iso(i.opened_at),
         "acknowledged": i.acknowledged_at is not None, "site_wide": True}
        for i in STORE.site_wide_alerts(tenant.tenant_id) if i.resolved_at is None]
    # Bring a friend: what this business earned in the month, and what is
    # still waiting to come off an invoice (referrals.py, credits.py).
    import credits
    from referrals import earned_between

    earned = earned_between(tenant.tenant_id, iso(start) or "", iso(end) or "")

    offline = [{"unit": names[s.sensor_id], "last_seen": iso(s.last_seen)}
               for s in sensors if s.last_seen is not None and s.offline(now)]
    never = [names[s.sensor_id] for s in sensors if s.last_seen is None]
    low_battery = [names[s.sensor_id] for s in sensors if s.battery_low]

    return {
        "tenant_id": tenant.tenant_id,
        "company_name": tenant.company_name,
        "month": month_key(first),
        "month_label": month_label(first),
        "period_start": first.isoformat(),
        "period_end": last.isoformat(),
        "units": len(sensors),
        "readings": readings,
        "out_of_range_readings": out_of_range,
        "in_range_percent": (round((readings - out_of_range) / readings * 100, 1)
                             if readings else None),
        "breaches": breaches,
        "breach_count": len(breaches),
        "acknowledged_count": len(answered),
        "unanswered_count": sum(1 for b in breaches if not b["acknowledged_by"]),
        "median_minutes_to_acknowledge": (
            statistics.median(b["minutes_to_acknowledge"] for b in answered)
            if answered else None),
        "who_acknowledged": who,
        "site_wide_alerts": site_wide,
        "site_wide_count": len(site_wide),
        "open_problems": {
            "alerts": open_alerts,
            "offline": offline,
            "never_reported": never,
            "low_battery": low_battery,
        },
        "open_problem_count": (len(open_alerts) + len(offline) + len(never)
                               + len(low_battery)),
        "temperature_unit": log["temperature_unit"],
        "covers_the_whole_month": log["covers_the_whole_period"],
        "caveat": log["caveat"],
        "inspector_log": inspector_link(tenant, first, last),
        "credits_earned": earned,
        "credits_earned_usd": round(sum(float(c["amount_usd"]) for c in earned), 2),
        "credit_waiting_usd": credits.balance(tenant.tenant_id),
    }


def subject(report: Dict[str, Any]) -> str:
    out = report["breach_count"]
    problems = report["open_problem_count"]
    return (
        f"{report['company_name']}: {report['month_label']} compliance summary, "
        + (f"{out} time{'' if out == 1 else 's'} out of range" if out
           else "nothing out of range")
        + (f", {problems} open" if problems else "")
    )


def _problem_lines(report: Dict[str, Any]) -> List[str]:
    found = report["open_problems"]
    lines = []
    for row in found["alerts"]:
        if row.get("site_wide"):
            lines.append(
                f"{row['unit']}: site-wide alert since {_when(row['since'])}, "
                + ("acknowledged, not yet closed" if row["acknowledged"]
                   else "nobody has acknowledged it"))
            continue
        lines.append(
            f"{row['unit']}: out of range since {_when(row['since'])}, "
            + ("acknowledged, not yet closed" if row["acknowledged"]
               else "nobody has acknowledged it"))
    for row in found["offline"]:
        lines.append(f"{row['unit']}: stopped reporting, last heard "
                     f"{_when(row['last_seen'])}")
    for unit in found["never_reported"]:
        lines.append(f"{unit}: set up but has never reported")
    for unit in found["low_battery"]:
        lines.append(f"{unit}: battery low")
    return lines


def render_text(report: Dict[str, Any]) -> str:
    units = report["units"]
    lines = [
        f"{report['company_name']}: {report['month_label']}",
        "",
        f"Units watched: {units}",
        f"Readings kept: {report['readings']:,}"
        + (f" ({report['in_range_percent']:g}% in range)"
           if report["in_range_percent"] is not None else ""),
    ]
    count = report["breach_count"]
    if count:
        median = _minutes(report["median_minutes_to_acknowledge"])
        lines.append(
            f"Out of range: {count} time{'' if count == 1 else 's'}, "
            f"{report['acknowledged_count']} acknowledged"
            + (f" (half within {median})" if median else "")
            + (f", {report['unanswered_count']} never acknowledged"
               if report["unanswered_count"] else ""))
        lines += ["", "Who answered:"]
        if report["who_acknowledged"]:
            for row in report["who_acknowledged"]:
                lines.append(
                    f"  {row['name']}: {row['acknowledged']}, typically in "
                    f"{_minutes(row['median_minutes'])}, slowest "
                    f"{_minutes(row['slowest_minutes'])}")
        else:
            lines.append("  Nobody acknowledged any of them.")
        lines += ["", "Each time:"]
        for b in report["breaches"][:LIST_AT_MOST]:
            where = f"{b['unit']}" + (f" ({b['site']})" if b["site"] else "")
            answer = (f"acknowledged by {b['acknowledged_by']} in "
                      f"{_minutes(b['minutes_to_acknowledge'])}"
                      if b["acknowledged_by"] else "never acknowledged")
            lines.append(f"  {_when(b['opened_at'])}: {where}, worst "
                         f"{b['worst']}°{report['temperature_unit']}, {answer}")
        if count > LIST_AT_MOST:
            lines.append(f"  and {count - LIST_AT_MOST} more, all in the log below.")
    elif report["readings"]:
        lines.append("Out of range: never. Every unit stayed inside its limits.")
    else:
        lines.append("Nothing was recorded this month, so there is nothing "
                     "to report. That is not the same as nothing going wrong.")

    if report.get("site_wide_alerts"):
        count = report["site_wide_count"]
        lines += ["", f"Site-wide alerts (many units warming at once): {count}"]
        for a in report["site_wide_alerts"][:LIST_AT_MOST]:
            answer = (f"acknowledged by {a['acknowledged_by']} in "
                      f"{_minutes(a['minutes_to_acknowledge'])}"
                      if a["acknowledged_by"] else "never acknowledged")
            lines.append(f"  {_when(a['opened_at'])}: {a['summary'].rstrip('.')} "
                         f"({', '.join(a['units'])}), {answer}")
        if count > LIST_AT_MOST:
            lines.append(f"  and {count - LIST_AT_MOST} more, all in the log below.")

    if report.get("credits_earned"):
        count = len(report["credits_earned"])
        lines += ["", f"Bring a friend: {count} business{'' if count == 1 else 'es'} "
                      f"you referred paid their first invoice, so you earned "
                      f"${report['credits_earned_usd']:,.2f} of credit (a month free "
                      "on one location each)."]
        if report.get("credit_waiting_usd"):
            lines.append(f"${report['credit_waiting_usd']:,.2f} comes off your next "
                         "invoice.")

    problems = _problem_lines(report)
    lines += ["", "Open now:" if problems else "Open now: nothing."]
    lines += [f"  {line}" for line in problems[:LIST_AT_MOST]]
    if len(problems) > LIST_AT_MOST:
        lines.append(f"  and {len(problems) - LIST_AT_MOST} more in the console.")

    lines.append("")
    if report["inspector_log"]:
        lines.append("The inspector's log for the month, every unit by the "
                     f"day and every alert in full: {report['inspector_log']}")
    else:
        lines.append("The inspector's log for the month is in the console, "
                     f"under Proof: {report['period_start']} to "
                     f"{report['period_end']}.")
    if report["caveat"]:
        lines += ["", report["caveat"]]
    lines += [
        "",
        "This goes to the owners of the business, once a month. How to "
        "stop it is below.",
    ]
    return "\n".join(lines)


# ---- sending ------------------------------------------------------------


def send_for(tenant: Tenant, first: date, last: date,
             now: Optional[datetime] = None) -> Dict[str, Any]:
    """This business's report for one month, to its owners, once."""
    now = now or utc_now()
    if already_sent(tenant.tenant_id, first):
        return {"status": "already_sent", "sent": 0}
    if tenant.suspended:
        return {"status": "suspended", "sent": 0}
    if delinquency(tenant.tenant_id, now)["delinquent"]:
        # Not marked: if it is settled inside the week, the report goes.
        return {"status": "withheld", "sent": 0}
    if not STORE.sensors_for(tenant.tenant_id):
        return {"status": "no_units", "sent": 0}
    recipients = owners(tenant.tenant_id)
    if not recipients:
        return {"status": "no_owner", "sent": 0}

    report = summary(tenant, first, last, now)
    text = render_text(report)
    sent = 0
    statuses = []
    for owner in recipients:
        person = STORE.person_of(owner)
        result = send_mail(
            to_address=owner.email,
            subject=subject(report),
            # The HTML part is made from this text by mail.compose, so the
            # two say the same thing.
            body=f"{owner.full_name},\n\n{text}",
            dedupe_key=f"monthly:{tenant.tenant_id}:{report['month']}:{person.user_id}",
            # Mail we choose to send, so it carries the unsubscribe link
            # and the List-Unsubscribe header, and honours a stop.
            klass="commercial",
            tenant_id=tenant.tenant_id,
        )
        statuses.append(result.get("status"))
        if result.get("sent"):
            sent += 1
    STORE._db.put(KIND, _record_id(tenant.tenant_id, first), {
        "tenant_id": tenant.tenant_id,
        "month": report["month"],
        "owners": len(recipients),
        "sent": sent,
        "at": iso(now),
    })
    return {"status": "done", "sent": sent, "owners": len(recipients),
            "deliveries": statuses}


def run_monthly_reports(now: Optional[datetime] = None, force: bool = False) -> Dict[str, Any]:
    """Last month's report for every business that has not had it.

    `force` sends outside the first week (an operator asking); it never
    sends a business's month twice.
    """
    now = now or utc_now()
    if not force and not due(now):
        return {"skipped": "not the first week of the month", "sent_count": 0}
    if not _pass_lock.acquire(blocking=False):
        logger.warning("A monthly report pass is still running; this one stands aside.")
        return {"skipped": "another pass is still running", "sent_count": 0}
    try:
        first, last = previous_month(now)
        done, sent, failures = [], 0, []
        for tenant in STORE.list_tenants():
            try:
                result = send_for(tenant, first, last, now)
            except Exception as exc:  # noqa: BLE001 - one business must not stop the rest
                logger.exception("Monthly report failed for %s (%s).",
                                 tenant.tenant_id, exc)
                failures.append(tenant.tenant_id)
                continue
            if result["status"] == "done":
                done.append(tenant.tenant_id)
                sent += result["sent"]
        return {"month": month_key(first), "businesses": done,
                "business_count": len(done), "sent_count": sent,
                "failed_tenants": failures}
    finally:
        _pass_lock.release()


# ---- routes -------------------------------------------------------------


@router.get("/monthly")
def preview_monthly(
    month: Optional[str] = Query(
        None, pattern=r"^\d{4}-\d{2}$",
        description="YYYY-MM. Default: last month."),
    tenant: Tenant = Depends(require_current("The monthly report")),
):
    """What this business's monthly summary says, without sending it."""
    now = utc_now()
    if month:
        try:
            first = date(int(month[:4]), int(month[5:]), 1)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail="month must be YYYY-MM.") from None
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    else:
        first, last = previous_month(now)
    report = summary(tenant, first, last, now)
    return {
        **report,
        "subject": subject(report),
        "text": render_text(report),
        "sent": already_sent(tenant.tenant_id, first),
        "goes_to": [o.full_name for o in owners(tenant.tenant_id)],
    }


@router.post("/monthly")
def push_monthly(_: None = Depends(require_platform_admin)):
    """Send last month's reports now, to every business that has not had one."""
    return run_monthly_reports(force=True)
