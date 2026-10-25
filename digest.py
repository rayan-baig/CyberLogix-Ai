"""Two summaries that go out on their own: one to us, one to the customer.

The book at `/book` and the console both answer the right questions and
both require somebody to open a browser. The person who owns this company
is at school for six hours of every working day, which makes "open the
dashboard" a plan that fails five days a week.

**The operator digest** is the book, once a day, in an email: what was
billed, what arrived, who needs a call, and anything about the system
itself that is quietly broken — an unconfigured mail host, a queue that
is not draining, a fleet going dark. It is the difference between a
worklist and a to-do list somebody actually sees.

**The customer report** is the other direction, and it is a retention
tool rather than a courtesy. A monitoring product that works is invisible
by construction: nothing breaks, nobody is called, and at renewal the
customer cannot remember what they are paying for. A weekly line saying
*4,102 readings, two excursions caught, one unit that has stopped
reporting* is the evidence that the invoice is worth paying — and the
offline count is worth more than all of it, because a silent sensor is
the one failure a monitoring product cannot warn you about by itself.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from accounts import require_role
from auth import require_platform_admin, require_tenant, write_audit
from mail import send as send_mail
from store import STORE, Tenant, User, iso, utc_now

logger = logging.getLogger("cyberlogix.digest")

router = APIRouter(prefix="/api/digest", tags=["Digest"])

# Where the operator digest goes. Unset, it is not sent — and the status
# endpoint says so, rather than the digest quietly never existing.
import os  # noqa: E402  - read after the docstring for the same reason as below

OPERATOR_EMAIL = os.environ.get("CYBERLOGIX_OPERATOR_EMAIL", "").strip()

# The hour, UTC, that the digest is allowed to go out. The money pass
# runs hourly, so without this the digest arrives at whatever time the
# process happened to start — which after a deploy at midnight is a
# 3am email nobody reads and everybody learns to ignore.
#
# A floor rather than an exact time: if nothing is running at the hour
# named, the next pass after it sends. Missing a day because the process
# was restarting is the one outcome worse than sending late.
try:
    DIGEST_HOUR_UTC = min(23, max(0, int(
        os.environ.get("CYBERLOGIX_DIGEST_HOUR_UTC", "7")
    )))
except ValueError:
    DIGEST_HOUR_UTC = 7

# How many worklist rows the digest carries. Enough to be a morning's
# work, few enough that the email is read rather than skimmed. The rest
# are counted, not listed.
DIGEST_ROWS = 8

# A weekly report on a Monday, covering the seven days before it.
REPORT_DAYS = 7


def _money_yesterday(now: datetime) -> Dict[str, Any]:
    """What was invoiced and what actually arrived in the last day."""
    since = now - timedelta(days=1)
    issued = []
    paid = []
    for invoice in STORE.all_invoices():
        if invoice.issued_at and invoice.issued_at >= since:
            issued.append(invoice)
        if invoice.paid_at and invoice.paid_at >= since:
            paid.append(invoice)
    return {
        "issued_count": len(issued),
        "issued_usd": round(sum(i.total_usd for i in issued), 2),
        "paid_count": len(paid),
        "paid_usd": round(sum(i.amount_paid_usd for i in paid), 2),
    }


def _system_warnings() -> List[str]:
    """Things wrong with the machine rather than with a customer.

    These are the failures that are invisible precisely because nothing
    raises: mail that cannot be sent looks identical to mail nobody
    needed, right up until a quarter's invoices turn out to have queued.
    """
    from mail import status as mail_status

    warnings = []
    transport = mail_status()
    if not transport["configured"]:
        warnings.append(
            "No mail transport is configured (missing "
            + ", ".join(transport["missing"])
            + "), so nothing below has actually reached anybody — "
            "including the invoices."
        )
    if not transport["payment_details_configured"]:
        warnings.append(
            "No payment details are configured, so every invoice notice "
            "goes out without a way to pay it. Set CYBERLOGIX_REMIT_TO or "
            "CYBERLOGIX_PAY_URL."
        )
    if transport["queued"]:
        warnings.append(
            f"{transport['queued']} message(s) are queued and undelivered."
        )
    if transport["failed"]:
        warnings.append(
            f"{transport['failed']} message(s) were given up on. Check "
            "/api/mail/log."
        )

    # The one that explains why none of the others reached anybody: if
    # the sweep has stopped, this email is the last thing still running.
    from watchdog import warnings as watchdog_warnings

    warnings.extend(watchdog_warnings())

    # Money we have taken and cannot account for. Every line here is a
    # customer who has paid and is still being chased for it.
    from payments import unmatched as unmatched_payments

    stranded = unmatched_payments()
    if stranded:
        total = sum(row.get("amount_usd") or 0 for row in stranded)
        warnings.append(
            f"{len(stranded)} payment(s) totalling ${total:,.2f} could not be "
            "matched to an invoice. Each one is somebody who has paid and is "
            "still being chased. See /api/payments/unmatched."
        )

    # Two different problems wearing the same word. A sensor that has
    # never reported is an installation somebody did not finish; one that
    # reported and then stopped is a unit that has failed, or lost power,
    # or been unplugged by a cleaner. Lumping them together produces a
    # number that is always non-zero and therefore always ignored.
    never = []
    silent = []
    for sensor in STORE.all_sensors():
        if sensor.last_seen is None:
            never.append(sensor)
        elif sensor.offline():
            silent.append(sensor)

    if silent:
        warnings.append(
            f"{len(silent)} sensor(s) reported and then stopped. A silent "
            "sensor cannot raise an alarm, and the customer believes it is "
            "watching."
        )
    if never:
        warnings.append(
            f"{len(never)} registered sensor(s) have never reported at "
            "all — an installation that was started and not finished."
        )
    return warnings


def operator_digest(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Everything the person who owns this company needs before school."""
    from contracts import attention_rows

    now = now or utc_now()
    rows = attention_rows(now)
    money = _money_yesterday(now)

    mrr = 0.0
    from pricing import build_subscription

    # Split, because the headline used to read "$151,716 a year booked"
    # about an estate where nothing had ever been invoiced. An account
    # with no contract is priced by the rate card and billed by nobody:
    # run_billing iterates subscriptions, so there is nothing to bill
    # against. Calling that "booked" in the one email the operator reads
    # every morning is how it stayed invisible.
    for tenant in STORE.list_tenants():
        try:
            if tenant.plan != "trial" and not tenant.suspended:
                if STORE.active_subscription(tenant.tenant_id) is not None:
                    mrr += build_subscription(tenant)["monthly_total_usd"]
        except Exception:  # noqa: BLE001 - a total must not fail on one estate
            logger.exception("Could not price %s for the digest.", tenant.tenant_id)

    unbilled = [r for r in rows if r["kind"] == "unbilled"]
    unbilled_annual = round(sum(r["at_stake_usd"] for r in unbilled), 2)

    return {
        "date": now.strftime("%Y-%m-%d"),
        "mrr_usd": round(mrr, 2),
        "arr_usd": round(mrr * 12, 2),
        "outstanding_usd": round(
            sum(i.balance_usd for i in STORE.open_invoices()), 2
        ),
        "money": money,
        "needs_a_person": len(rows),
        "rows": rows[:DIGEST_ROWS],
        "overflow": max(len(rows) - DIGEST_ROWS, 0),
        "at_risk_usd": round(
            sum(
                r["at_stake_usd"] for r in rows
                if r["kind"] in ("lapsed", "grace", "renewal", "trial_ending")
            ),
            2,
        ),
        "faults": _fault_summary(),
        "people": _people_summary(),
        "unbilled_accounts": len(unbilled),
        "unbilled_annual_usd": unbilled_annual,
        "warnings": _system_warnings(),
    }


def _fault_summary() -> Dict[str, Any]:
    """What has thrown, for the one email that arrives without being asked.

    An unhandled error is recorded now rather than lost to stdout, but a
    record nobody reads is the same as no record -- and the operator
    console is a page somebody has to remember to open. This is the line
    that arrives anyway.
    """
    try:
        import faults

        return faults.status()
    except Exception:  # noqa: BLE001 - a summary must not stop the digest
        logger.exception("Could not summarise faults for the digest.")
        return {"distinct": 0, "occurrences": 0, "newest": None, "worst": None}


def _people_summary() -> Dict[str, Any]:
    """Licences about to lapse, and leavers still reachable by an alert.

    Fleet-wide, because the operator's digest spans every account and a
    lapsed food handler card is the same class of problem as a freezer
    drifting: a line being crossed with nobody watching the date.
    """
    try:
        import people

        return people.fleet_summary()
    except Exception:  # noqa: BLE001 - a summary must not stop the digest
        logger.exception("Could not summarise people for the digest.")
        return {"expired_and_blocking": 0, "expiring_soon": 0,
                "leavers_still_on_the_roster": 0}


def _render_operator(digest: Dict[str, Any]) -> str:
    money = digest["money"]
    out = [
        f"${digest['arr_usd']:,.0f} a year booked "
        f"(${digest['mrr_usd']:,.2f} a month).",
        f"${digest['outstanding_usd']:,.2f} invoiced and unpaid.",
        f"${digest['at_risk_usd']:,.0f} a year at risk if nobody calls.",
    ]

    people = digest["people"]
    if people["expired_and_blocking"]:
        out.append(
            f"{people['expired_and_blocking']} staff licence(s) required "
            "for the job have expired. Somebody on shift may not be "
            "allowed to be."
        )
    elif people["expiring_soon"]:
        out.append(
            f"{people['expiring_soon']} staff licence(s) expire within 30 "
            "days."
        )
    if people["leavers_still_on_the_roster"]:
        out.append(
            f"{people['leavers_still_on_the_roster']} person(s) who have "
            "left are still on the on-call roster. An alert routed to them "
            "is one nobody answers."
        )

    broke = digest["faults"]
    if broke["distinct"]:
        out.append(
            f"{broke['distinct']} distinct fault(s), "
            f"{broke['occurrences']} occurrence(s). Worst: "
            f"{broke['worst']}. See /api/admin/faults."
        )

    if digest["unbilled_accounts"]:
        count = digest["unbilled_accounts"]
        out.append(
            f"${digest['unbilled_annual_usd']:,.0f} a year is being served "
            f"with no contract, across {count} account"
            f"{'' if count == 1 else 's'}. None of it is being invoiced."
        )

    out += [
        "",
        f"Yesterday: {money['issued_count']} invoice(s) issued for "
        f"${money['issued_usd']:,.2f}; "
        f"${money['paid_usd']:,.2f} arrived.",
    ]

    if digest["warnings"]:
        out += ["", "NEEDS FIXING:"]
        out += [f"  * {w}" for w in digest["warnings"]]

    if not digest["rows"]:
        out += ["", "Nothing needs a person today."]
        return "\n".join(out)

    out += ["", f"NEEDS A PERSON ({digest['needs_a_person']}):"]
    for row in digest["rows"]:
        out += [
            "",
            f"  {row['company_name']} — ${row['at_stake_usd']:,.0f} at stake",
            f"    {row['headline']}",
            f"    {row['action']}",
            f"    {row['contact_name']} · {row['contact_email']} · "
            f"{row['contact_phone']}",
        ]
    if digest["overflow"]:
        out.append("")
        out.append(
            f"  ...and {digest['overflow']} more. The whole book is at /book."
        )
    return "\n".join(out)


def send_operator_digest(now: Optional[datetime] = None) -> Dict[str, Any]:
    """One a day, keyed on the date so an hourly pass sends one.

    Returns a result even when there is nowhere to send it: a digest that
    silently does not exist is exactly the failure the digest is for.
    """
    now = now or utc_now()

    # The configuration fault is reported before the transient one. Both
    # are true before seven in the morning, and only one of them will
    # still be true tomorrow: "too early" resolves itself in a few hours,
    # a missing address never does. Checking the hour first hid the
    # permanent fault behind the temporary one for seven hours of every
    # day — including every hour anybody was likely to be looking at a
    # fresh deployment.
    if not OPERATOR_EMAIL:
        return {
            "sent": False,
            "status": "no_operator_address",
            "detail": (
                "Set CYBERLOGIX_OPERATOR_EMAIL and the daily digest starts "
                "arriving. Until then the book only exists in a browser."
            ),
        }
    if now.hour < DIGEST_HOUR_UTC:
        return {
            "sent": False,
            "status": "too_early",
            "detail": (
                f"The digest goes out from {DIGEST_HOUR_UTC:02d}:00 UTC "
                "(CYBERLOGIX_DIGEST_HOUR_UTC)."
            ),
        }

    digest = operator_digest(now)
    urgent = sum(1 for r in digest["rows"] if r["urgency"] == "high")

    # Ordered worst-first and kept short. The first version read
    # "CyberLogix: $35,964 booked, 3 need a person (1 urgent), and
    # something is broken" — folded across two header lines and truncated
    # by every mail client at about the point where it stopped being
    # reassuring. What is wrong goes first, because that is the half that
    # survives the truncation.
    parts = []
    if digest["warnings"]:
        parts.append(f"{len(digest['warnings'])} to fix")
    if urgent:
        parts.append(f"{urgent} urgent")
    elif digest["needs_a_person"]:
        parts.append(f"{digest['needs_a_person']} to call")
    parts.append(f"${digest['arr_usd']:,.0f} booked")
    subject = "CyberLogix: " + ", ".join(parts)

    return send_mail(
        to_address=OPERATOR_EMAIL,
        subject=subject,
        body=_render_operator(digest),
        dedupe_key=f"digest:operator:{digest['date']}",
        # Operational mail about running the business, not marketing at
        # the person running it.
        klass="transactional",
    )


# ---- the customer's weekly report --------------------------------------


def customer_report(tenant: Tenant, now: Optional[datetime] = None) -> Dict[str, Any]:
    """What this estate did over the last week, from its own records."""
    now = now or utc_now()
    since = now - timedelta(days=REPORT_DAYS)

    sensors = STORE.sensors_for(tenant.tenant_id)
    readings = 0
    breaches = 0
    for sensor in sensors:
        for reading in STORE.readings_for(sensor.sensor_id, since=since):
            readings += 1
            if reading.breached:
                breaches += 1

    incidents = STORE.incidents_for(tenant.tenant_id, since=since)
    # Same split as the operator digest: "never reported" is an
    # installation to finish, "went quiet" is a unit that has failed.
    # Telling a customer their brand-new sensor "has stopped reporting"
    # reads as our fault, and telling them a failed one is "not set up
    # yet" reads as theirs.
    offline = [s for s in sensors if s.last_seen is not None and s.offline(now)]
    never = [s for s in sensors if s.last_seen is None]

    return {
        "from": iso(since),
        "to": iso(now),
        "units": len(sensors),
        "readings": readings,
        "breaching_readings": breaches,
        "incidents": len(incidents),
        "resolved": sum(1 for i in incidents if i.resolved_at is not None),
        "escalated": sum(1 for i in incidents if i.voice_escalated_at is not None),
        "offline": [
            {"sensor_id": s.sensor_id, "last_seen": iso(s.last_seen)}
            for s in offline
        ],
        "never_reported": [s.sensor_id for s in never],
    }


def _render_report(tenant: Tenant, report: Dict[str, Any]) -> str:
    out = [
        f"{tenant.company_name} — the last {REPORT_DAYS} days",
        "",
        f"  {report['readings']:,} readings from {report['units']} "
        f"unit{'' if report['units'] == 1 else 's'}",
    ]
    if report["incidents"]:
        out.append(
            f"  {report['incidents']} excursion"
            f"{'' if report['incidents'] == 1 else 's'} caught, "
            f"{report['resolved']} resolved"
            + (
                f", {report['escalated']} escalated to a phone call"
                if report["escalated"] else ""
            )
        )
    elif report["readings"]:
        out.append("  No excursions. Everything stayed inside its limits.")
    else:
        # The line this replaces said "everything stayed inside its
        # limits" to an estate that had taken no readings at all. Nothing
        # stayed inside anything; nothing was measured. Telling a customer
        # their unmonitored week went fine is the single most damaging
        # sentence a monitoring product can send.
        out.append(
            "  Nothing was measured this week, so there is nothing here to "
            "tell you. That is not the same as nothing going wrong."
        )

    if report["offline"]:
        out += ["", "UNITS THAT HAVE STOPPED REPORTING:"]
        out += [
            f"  {u['sensor_id']} — last seen {u['last_seen']}"
            for u in report["offline"]
        ]
        out.append(
            "\nA silent sensor cannot warn you about anything, and it looks "
            "exactly like one that is fine. These are worth checking before "
            "they turn out to be the ones that mattered."
        )

    if report["never_reported"]:
        out += ["", "UNITS THAT HAVE NEVER REPORTED:"]
        out += [f"  {sensor_id}" for sensor_id in report["never_reported"]]
        out.append(
            "\nThese are registered but have never sent a reading, so they "
            "are almost certainly an installation that was started and not "
            "finished. Reply and we will walk through it."
        )

    out += [
        "",
        "This is the record that proves the week, not just a summary of "
        "it: every reading behind these figures is in your vault, hashed "
        "in sequence, and exportable for an auditor or an insurer.",
    ]
    return "\n".join(out)


def send_customer_reports(now: Optional[datetime] = None) -> Dict[str, Any]:
    """One report per paying estate per week.

    Trials are left out: they get the conversion ladder, which already
    carries the same evidence and asks for the order with it. Two emails
    a week to somebody evaluating the product is one too many.
    """
    now = now or utc_now()
    week = now.strftime("%G-W%V")
    sent = []
    failures = []

    for tenant in STORE.list_tenants():
        if tenant.plan == "trial" or tenant.suspended:
            continue
        try:
            report = customer_report(tenant, now)
            if report["units"] == 0:
                continue
            result = send_mail(
                to_address=tenant.contact_email,
                subject=(
                    f"{tenant.company_name}: {report['readings']:,} readings, "
                    + (
                        f"{report['incidents']} caught"
                        if report["incidents"]
                        else "nothing out of range"
                    )
                    + (
                        f", {len(report['offline'])} unit(s) gone quiet"
                        if report["offline"] else ""
                    )
                ),
                body=(
                    f"{tenant.contact_name},\n\n"
                    f"{_render_report(tenant, report)}"
                ),
                dedupe_key=f"report:{tenant.tenant_id}:{week}",
                klass="commercial",
                tenant_id=tenant.tenant_id,
            )
            if result.get("sent"):
                sent.append(tenant.tenant_id)
        except Exception as exc:  # noqa: BLE001 - one estate must not stop the rest
            logger.exception(
                "Weekly report failed for %s (%s).", tenant.tenant_id, exc
            )
            failures.append(tenant.tenant_id)

    return {"sent": sent, "sent_count": len(sent), "failed_tenants": failures}


def run_digests(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Both summaries, guarded separately. Called from the money pass.

    Each is keyed on its own period — the operator's on the date, the
    customer's on the ISO week — so an hourly pass produces one a day and
    one a week rather than twenty-four and a hundred and sixty-eight.
    """
    now = now or utc_now()
    operator = {"sent": False, "status": "error"}
    reports = {"sent_count": 0}
    try:
        operator = send_operator_digest(now)
    except Exception as exc:  # noqa: BLE001 - the watchdog must not die
        logger.exception("Operator digest failed (%s).", exc)
    try:
        reports = send_customer_reports(now)
    except Exception as exc:  # noqa: BLE001 - one summary must not stop the other
        logger.exception("Customer reports failed (%s).", exc)
    return {"operator": operator, "customer_reports": reports}


# ---- routes -------------------------------------------------------------


@router.get("/operator")
def read_operator_digest(_: None = Depends(require_platform_admin)):
    """The digest as JSON, without sending it."""
    return {
        "digest": operator_digest(),
        "delivery": (
            f"Sent daily to {OPERATOR_EMAIL}." if OPERATOR_EMAIL
            else "CYBERLOGIX_OPERATOR_EMAIL is not set, so it is not sent."
        ),
    }


@router.post("/operator")
def push_operator_digest(_: None = Depends(require_platform_admin)):
    """Send today's digest now, whatever the hour, if it has not gone.

    The hour is a floor on the *unattended* pass, not a rule about what
    the operator may ask for. Somebody pressing this at six in the
    morning wants it at six in the morning.
    """
    return send_operator_digest(now=utc_now().replace(hour=23))


@router.post("/reports")
def push_customer_reports(_: None = Depends(require_platform_admin)):
    """Send this week's customer reports now, to whoever has not had one."""
    return send_customer_reports()


# ---- the week in one text ------------------------------------------------
#
# The weekly report above is an email with numbers in it, and it is read
# by whoever reads email. The person who owns the restaurant reads texts.
#
# A monitoring product that works is invisible by construction: nothing
# breaks, nobody is called, and at renewal the owner cannot remember what
# they are paying for. One text a week is the cheapest defence against
# that, and it is the same reason the weekly email exists -- except this
# one gets read.
#
# Opt-in, because an unasked-for text is worse than no text, and because
# each one costs money on a wire. Off unless somebody turned it on.

REASSURANCE_KIND = "weekly_text"


def wants_weekly_text(tenant_id: str) -> Optional[str]:
    """The number to text, or None. Absent means off."""
    row = STORE._db.get(REASSURANCE_KIND, tenant_id)
    if not row or not row.get("enabled"):
        return None
    return (row.get("phone") or "").strip() or None


def set_weekly_text(tenant_id: str, phone: str, enabled: bool) -> Dict[str, Any]:
    row = {"tenant_id": tenant_id, "phone": phone.strip(),
           "enabled": bool(enabled), "changed_at": iso(utc_now())}
    STORE._db.put(REASSURANCE_KIND, tenant_id, row)
    return row


def weekly_text_body(tenant: Tenant, now: Optional[datetime] = None) -> str:
    """Short, plain, and specific enough to be worth the interruption.

    Deliberately not "everything is fine": a text that says the same six
    words every week is one nobody reads by the third month. It carries
    the counts, so a quiet week still shows the product doing something,
    and a licence about to lapse rides along where it will actually be
    seen.
    """
    now = now or utc_now()
    report = customer_report(tenant, now)
    bits = [f"{tenant.company_name}: {report['readings']:,} checks this week"]

    if report["incidents"]:
        bits.append(f"{report['incidents']} caught and dealt with")
    else:
        bits.append("nothing went out of range")

    if report["offline"]:
        bits.append(f"{len(report['offline'])} unit(s) not reporting - "
                    "worth a look")

    try:
        import people

        lapsing = people.fleet_summary()
        if lapsing["expired_and_blocking"]:
            bits.append(f"{lapsing['expired_and_blocking']} staff licence(s) "
                        "expired")
        elif lapsing["expiring_soon"]:
            bits.append(f"{lapsing['expiring_soon']} licence(s) due soon")
    except Exception:  # noqa: BLE001 - a summary must not stop the text
        logger.exception("Could not read licences for the weekly text.")

    return ". ".join(bits) + "."


def send_weekly_texts(now: Optional[datetime] = None) -> Dict[str, Any]:
    """One text per opted-in estate per week."""
    from notifications import send_sms

    now = now or utc_now()
    week = now.strftime("%G-W%V")
    sent: List[str] = []
    failures: List[str] = []

    for tenant in STORE.list_tenants():
        if tenant.plan == "trial" or tenant.suspended:
            continue
        phone = wants_weekly_text(tenant.tenant_id)
        if not phone:
            continue
        # One per estate per week, enforced here rather than trusted to
        # the hourly pass not running twice.
        stamp = STORE._db.get(REASSURANCE_KIND, tenant.tenant_id) or {}
        if stamp.get("last_week") == week:
            continue
        try:
            result = send_sms(phone, weekly_text_body(tenant, now),
                              tenant_id=tenant.tenant_id)
            STORE._db.put(REASSURANCE_KIND, tenant.tenant_id,
                          {**stamp, "last_week": week})
            if result.get("status") == "sent":
                sent.append(tenant.tenant_id)
        except Exception as exc:  # noqa: BLE001 - one estate must not stop the rest
            logger.exception("Weekly text failed for %s (%s).",
                             tenant.tenant_id, exc)
            failures.append(tenant.tenant_id)

    return {"sent": sent, "sent_count": len(sent), "failed_tenants": failures}


class WeeklyText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field("", max_length=40)
    enabled: bool = True


@router.get("/weekly-text", tags=["Digest"])
def weekly_text_status(tenant: Tenant = Depends(require_tenant)):
    """Whether the weekly text is on, and what it would say today."""
    row = STORE._db.get(REASSURANCE_KIND, tenant.tenant_id) or {}
    return {
        "enabled": bool(row.get("enabled")),
        "phone": row.get("phone", ""),
        "preview": weekly_text_body(tenant),
        "note": (
            "One text a week, on the same day as the emailed report. Off "
            "unless you turn it on, because an unasked-for text is worse "
            "than none."
        ),
    }


@router.post("/weekly-text", tags=["Digest"])
def set_weekly_text_route(
    payload: WeeklyText,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("owner")),
):
    """Turn the weekly text on or off, and say where it goes."""
    if payload.enabled and not payload.phone.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A number is needed to send a text to.",
        )
    row = set_weekly_text(tenant.tenant_id, payload.phone, payload.enabled)
    write_audit(tenant, operator, "digest.weekly_text",
                "on" if payload.enabled else "off")
    return {"enabled": row["enabled"], "phone": row["phone"],
            "preview": weekly_text_body(tenant)}
