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

from fastapi import APIRouter, Depends

from auth import require_platform_admin
from mail import send as send_mail
from store import STORE, Tenant, iso, utc_now

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

    for tenant in STORE.list_tenants():
        try:
            if tenant.plan != "trial" and not tenant.suspended:
                mrr += build_subscription(tenant)["monthly_total_usd"]
        except Exception:  # noqa: BLE001 - a total must not fail on one estate
            logger.exception("Could not price %s for the digest.", tenant.tenant_id)

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
        "warnings": _system_warnings(),
    }


def _render_operator(digest: Dict[str, Any]) -> str:
    money = digest["money"]
    out = [
        f"${digest['arr_usd']:,.0f} a year booked "
        f"(${digest['mrr_usd']:,.2f} a month).",
        f"${digest['outstanding_usd']:,.2f} invoiced and unpaid.",
        f"${digest['at_risk_usd']:,.0f} a year at risk if nobody calls.",
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
    if now.hour < DIGEST_HOUR_UTC:
        return {
            "sent": False,
            "status": "too_early",
            "detail": (
                f"The digest goes out from {DIGEST_HOUR_UTC:02d}:00 UTC "
                "(CYBERLOGIX_DIGEST_HOUR_UTC)."
            ),
        }
    if not OPERATOR_EMAIL:
        return {
            "sent": False,
            "status": "no_operator_address",
            "detail": (
                "Set CYBERLOGIX_OPERATOR_EMAIL and the daily digest starts "
                "arriving. Until then the book only exists in a browser."
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
