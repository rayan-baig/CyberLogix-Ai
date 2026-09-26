"""The people, their licences, and what is owed when one of them leaves.

Three things a health inspector, a labour board and a plaintiff's lawyer
all ask about, and none of which a temperature sensor can see.

**A licence to work expires.** Every vertical this product serves has
staff who legally cannot do the job without a current card: a food
handler certificate, a pharmacy technician registration, a CDL and DOT
medical card on a reefer, an A&P on an airframe. It lapses quietly on a
date nobody diarised, and the first anybody hears is an inspector
asking. That is a shutdown risk sitting in a filing cabinet.

**Somebody has to be told before it goes.** Not on the day -- a renewal
takes weeks. The ladder here is the same shape as the one this product
already runs for a freezer, because it is the same problem: a thing
drifting towards a line, and nobody watching.

**Somebody leaves, and obligations start.** A final paycheque, accrued
time, a benefits notice, equipment back, access revoked. For a big
employer in these industries that is real money on a clock.

And one of those obligations is this application's own business, in a
way no HR system can see: a person who no longer works here is still on
the on-call roster. At 3am the freezer fails and the call goes to
somebody who handed their keys back in March. Nobody is coming. The
estate is dark and the dashboard says it is covered.

Closing that loop is the point of this file. The rest is scaffolding
around it.

WHAT THIS FILE DOES NOT DO
--------------------------
It does not know your jurisdiction. Final-pay deadlines, benefits
continuation windows and what counts as a valid credential differ by
country, by state, and by the terms somebody signed. Every date here is
one the employer sets, and every reminder says it is a reminder rather
than a rule. An application that hardcoded "you must pay within 72
hours" would be wrong somewhere, and confidently wrong is worse than
silent.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accounts import require_role
from models import Finite
from auth import require_tenant, write_audit
from mail import send as send_mail
from store import STORE, Tenant, User, iso, utc_now

logger = logging.getLogger("cyberlogix.people")

router = APIRouter(prefix="/api/people", tags=["People & credentials"])

STAFF_KIND = "staff"
CREDENTIAL_KIND = "credential"
OFFBOARD_KIND = "offboarding"

# How far ahead a lapse is worth raising. A renewal is paperwork and a
# waiting list, not a same-day errand, so the first warning is weeks out.
HORIZONS = (60, 30, 14, 7, 1)

# Credentials these industries actually run on. Offered, not enforced --
# the list will always be behind somebody's local rule, and a field that
# refuses the real answer is a field people work around.
COMMON_CREDENTIALS = {
    "restaurant": [
        "Food handler card", "Food protection manager certification",
        "Allergen awareness", "Alcohol service permit",
    ],
    "medical_lab": [
        "Phlebotomy certification", "CLIA personnel qualification",
        "Bloodborne pathogen training",
    ],
    "pharmacy": [
        "Pharmacist licence", "Pharmacy technician registration",
        "Immunisation certification", "Controlled substance registration",
    ],
    "logistics": [
        "Commercial driving licence", "DOT medical examiner certificate",
        "Hazmat endorsement", "Forklift operator certification",
    ],
    "private_aviation": [
        "Airframe and powerplant licence", "Inspection authorisation",
        "Ramp safety certification",
    ],
    "cryostorage": [
        "Cryogenic handling certification", "Confined space entry",
        "Liquid nitrogen safety",
    ],
}
GENERIC_CREDENTIALS = [
    "First aid", "Fire safety training", "Manual handling",
    "Health and safety induction",
]

COST_KIND = "leaver_cost"

# What a company carries on paying for somebody who does not work there
# any more. Not what it owes them -- that is payroll's job and it gets
# done, because the person chases it. This is the other direction, and
# nobody chases it: the phone plan, the software seat, the insurance,
# the fuel card. Each one is small, each one renews quietly, and the
# invoice it sits on has forty other lines.
#
# Offered as a starting list, because an owner staring at an empty box
# will not remember the parking permit. They add their own.
COMMON_LEAKS = {
    "phone": ("Mobile phone plan", 55.0),
    "software": ("Software seat or licence", 25.0),
    "benefits": ("Health or dental cover", 320.0),
    "vehicle": ("Vehicle lease or insurance", 410.0),
    "fuel": ("Fuel or fleet card", 180.0),
    "parking": ("Parking space or permit", 90.0),
    "uniform": ("Uniform or laundry service", 30.0),
    "gym": ("Gym or wellbeing benefit", 40.0),
    "union": ("Union or professional dues", 35.0),
    "other": ("Something else", 0.0),
}

# What this company takes of what it actually claws back for a customer.
# A share of recovery rather than a fee, because on this one the customer
# has no way to value the work in advance: they do not know the money is
# leaking until it is found.
RECOVERY_SHARE = 0.15


def _today() -> date:
    return utc_now().date()


def _parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").replace(
        tzinfo=timezone.utc
    ).date()


# --- the people ------------------------------------------------------------


def staff_for(tenant_id: str) -> List[Dict[str, Any]]:
    rows = [r for r in STORE._db.all(STAFF_KIND)
            if r.get("tenant_id") == tenant_id]
    return sorted(rows, key=lambda r: r.get("full_name", "").lower())


def credentials_for(tenant_id: str, staff_id: Optional[str] = None):
    rows = [r for r in STORE._db.all(CREDENTIAL_KIND)
            if r.get("tenant_id") == tenant_id
            and (staff_id is None or r.get("staff_id") == staff_id)]
    return sorted(rows, key=lambda r: r.get("expires_on") or "9999")


def days_left(row: Dict[str, Any], today: Optional[date] = None) -> int:
    return (_parse_day(row["expires_on"]) - (today or _today())).days


def state_of(row: Dict[str, Any], today: Optional[date] = None) -> str:
    """Expired, or how close. The vocabulary the console colours by."""
    left = days_left(row, today)
    if left < 0:
        return "expired"
    if left <= 7:
        return "critical"
    if left <= 30:
        return "soon"
    return "current"


def calendar(tenant_id: str, days: int = 180,
             today: Optional[date] = None) -> Dict[str, Any]:
    """Every expiry ahead, in the order they arrive.

    A calendar rather than a list because the question is never "which
    of these is expired" -- it is "what do I have to deal with before
    the end of the month", and that is a shape you read down.
    """
    today = today or _today()
    horizon = today + timedelta(days=days)
    people = {row["staff_id"]: row for row in staff_for(tenant_id)}

    rows = []
    for cred in credentials_for(tenant_id):
        person = people.get(cred["staff_id"])
        if person is None or not person.get("active", True):
            continue
        when = _parse_day(cred["expires_on"])
        if when > horizon:
            continue
        rows.append({
            **cred,
            "full_name": person["full_name"],
            "role": person.get("role", ""),
            "days_left": (when - today).days,
            "state": state_of(cred, today),
            # The bit that makes it operational rather than informational.
            "stops_them_working": bool(cred.get("required_to_work")),
        })

    rows.sort(key=lambda r: (r["expires_on"], r["full_name"]))
    expired = [r for r in rows if r["state"] == "expired"]
    blocking = [r for r in expired if r["stops_them_working"]]

    # Grouped by month, because that is how a rota gets planned.
    months: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        months.setdefault(row["expires_on"][:7], []).append(row)

    return {
        "generated_at": iso(utc_now()),
        "horizon_days": days,
        "people": len(people),
        "credentials": len(rows),
        "expired": len(expired),
        "expired_and_blocking": len(blocking),
        "expiring_within_30_days": len(
            [r for r in rows if 0 <= r["days_left"] <= 30]
        ),
        "by_month": [
            {"month": month, "entries": entries}
            for month, entries in sorted(months.items())
        ],
        "entries": rows,
        "note": (
            f"{len(blocking)} person-credential(s) have lapsed on work that "
            "needs them. Somebody on shift today may not be allowed to be."
            if blocking else
            "Nothing has lapsed on work that requires it."
        ),
    }


def due_warnings(tenant_id: str, today: Optional[date] = None) -> List[str]:
    """Lines for the daily digest and the console band.

    One per person-credential, at each horizon it crosses, worded so the
    reader knows whether it stops somebody working.
    """
    today = today or _today()
    people = {row["staff_id"]: row for row in staff_for(tenant_id)}
    lines = []
    for cred in credentials_for(tenant_id):
        person = people.get(cred["staff_id"])
        if person is None or not person.get("active", True):
            continue
        left = days_left(cred, today)
        if left < 0:
            lines.append(
                f"{person['full_name']}'s {cred['name']} expired "
                f"{abs(left)} day(s) ago"
                + (" and is required for their job."
                   if cred.get("required_to_work") else ".")
            )
        elif left in HORIZONS:
            lines.append(
                f"{person['full_name']}'s {cred['name']} expires in "
                f"{left} day(s)"
                + (" and is required for their job."
                   if cred.get("required_to_work") else ".")
            )
    return lines


def fleet_summary(today: Optional[date] = None) -> Dict[str, Any]:
    """The same counts as `calendar`, across every tenant, in one pass.

    The obvious implementation calls calendar() once per tenant, and that
    was the one here: each call scans the staff table and the credential
    table in full, so the hourly digest did that work per account and the
    test suite went from under two minutes to not finishing. Reading each
    table once and grouping gives the same answer for a fraction of the
    work.
    """
    today = today or _today()
    people_by_tenant: Dict[str, Dict[str, Any]] = {}
    for row in STORE._db.all(STAFF_KIND):
        people_by_tenant.setdefault(row["tenant_id"], {})[row["staff_id"]] = row

    expired_blocking = soon = 0
    for cred in STORE._db.all(CREDENTIAL_KIND):
        person = people_by_tenant.get(cred["tenant_id"], {}).get(cred["staff_id"])
        if person is None or not person.get("active", True):
            continue
        left = days_left(cred, today)
        if left < 0 and cred.get("required_to_work"):
            expired_blocking += 1
        elif 0 <= left <= 30:
            soon += 1

    contacts_by_tenant: Dict[str, set] = {}
    leavers = 0
    for row in STORE._db.all(OFFBOARD_KIND):
        tenant_id = row["tenant_id"]
        if tenant_id not in contacts_by_tenant:
            contacts_by_tenant[tenant_id] = {
                _digits(c.phone) for c in STORE.contacts_for(tenant_id)
                if c.active
            } - {""}
        if _digits(row.get("phone")) in contacts_by_tenant[tenant_id]:
            leavers += 1

    return {
        "expired_and_blocking": expired_blocking,
        "expiring_soon": soon,
        "leavers_still_on_the_roster": leavers,
    }


# --- when somebody leaves --------------------------------------------------


def offboarding_for(tenant_id: str) -> List[Dict[str, Any]]:
    rows = [r for r in STORE._db.all(OFFBOARD_KIND)
            if r.get("tenant_id") == tenant_id]
    return sorted(rows, key=lambda r: r.get("left_on") or "", reverse=True)


def still_on_the_roster(tenant_id: str, person: Dict[str, Any]) -> List[str]:
    """Whether a departed person would still be called about a freezer.

    This is the check no HR system can run and no monitoring product
    thinks to: the two halves live in different applications, so a
    leaver is removed from payroll and left on the escalation ladder.

    Matched on phone, because that is what the ladder dials -- a roster
    contact has a number and a name and nothing else. An earlier version
    of this also compared an email address, which a Contact has never
    had; every test passed because the phone matched first and the
    branch was never reached, and the first real roster with somebody
    else on it raised a 500.
    """
    phone = _digits(person.get("phone"))
    if not phone:
        return []
    return [
        contact.contact_id
        for contact in STORE.contacts_for(tenant_id)
        if contact.active and _digits(contact.phone) == phone
    ]


def possibly_on_the_roster(tenant_id: str, person: Dict[str, Any]):
    """Same name, different number. Worth raising, not worth asserting.

    Somebody can sit on the ladder under a personal mobile that never
    reached their staff record, so a name match is a question for the
    operator rather than an answer.
    """
    name = (person.get("full_name") or "").strip().lower()
    if not name:
        return []
    certain = set(still_on_the_roster(tenant_id, person))
    return [
        contact.contact_id
        for contact in STORE.contacts_for(tenant_id)
        if contact.active
        and contact.contact_id not in certain
        and contact.full_name.strip().lower() == name
    ]


def _digits(value: Optional[str]) -> str:
    """Compare numbers by their digits.

    "+1 555-0100" and "+15550100" are one phone, and a roster entry
    typed by a different person on a different day is the normal case.
    """
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def costs_for(tenant_id: str, offboarding_id: Optional[str] = None):
    rows = [r for r in STORE._db.all(COST_KIND)
            if r.get("tenant_id") == tenant_id
            and (offboarding_id is None
                 or r.get("offboarding_id") == offboarding_id)]
    return sorted(rows, key=lambda r: -float(r.get("monthly_usd") or 0.0))


def _months_between(start: str, end: Optional[date] = None) -> float:
    """Whole days, expressed in months, never negative.

    Days rather than calendar months because the bill does not care which
    month it is, and never negative because a leaving date typed as next
    year must not produce a credit.
    """
    try:
        began = _parse_day(start)
    except (ValueError, TypeError):
        return 0.0
    days = ((end or _today()) - began).days
    return max(0.0, days / 30.4375)


def cost_state(row: Dict[str, Any], left_on: str) -> Dict[str, Any]:
    """One line item: what it costs, how long it ran, what it is worth.

    "Wasted" is money already spent and usually gone. "Saving" is the
    monthly bleeding that stops the day it is cancelled. They are kept
    apart on purpose: adding a year of future saving to a recovery
    figure is how a number stops being true.
    """
    monthly = float(row.get("monthly_usd") or 0.0)
    stopped_on = row.get("stopped_on")
    running_months = _months_between(
        left_on, _parse_day(stopped_on) if stopped_on else None)
    return {
        **row,
        "still_running": not stopped_on,
        "months_running": round(running_months, 1),
        # Paid out since they left, for somebody who was not there.
        "wasted_usd": round(monthly * running_months, 2),
        # What stops, per month, once it is cancelled.
        "monthly_saving_usd": monthly if not stopped_on else 0.0,
        # Money actually returned. Only the employer can know this, so it
        # is entered rather than calculated, and it is the only figure
        # the share below is taken from.
        "refunded_usd": round(float(row.get("refunded_usd") or 0.0), 2),
    }


def recovery_for(tenant_id: str) -> Dict[str, Any]:
    """The whole picture for one account, and what this company earns.

    Three numbers that people conflate and must not be:

      still_bleeding_monthly -- going out every month, right now
      wasted_to_date         -- already spent since they left, mostly gone
      refunded               -- actually clawed back into the account

    The share is taken from `refunded` alone. Billing 15% of a projected
    annual saving would invoice a customer for money they have not seen,
    on a cancellation they could reverse next week.
    """
    leavers = offboarding_for(tenant_id)
    by_person = {row["offboarding_id"]: row for row in leavers}

    bleeding = wasted = refunded = 0.0
    stopped_monthly = 0.0
    lines: List[Dict[str, Any]] = []
    for raw in costs_for(tenant_id):
        person = by_person.get(raw.get("offboarding_id"))
        if person is None:
            continue
        line = cost_state(raw, person.get("left_on", ""))
        line["full_name"] = person.get("full_name", "")
        lines.append(line)
        wasted += line["wasted_usd"]
        refunded += line["refunded_usd"]
        if line["still_running"]:
            bleeding += float(raw.get("monthly_usd") or 0.0)
        else:
            stopped_monthly += float(raw.get("monthly_usd") or 0.0)

    return {
        "lines": sorted(lines, key=lambda r: -r["wasted_usd"]),
        "still_bleeding_monthly_usd": round(bleeding, 2),
        "still_bleeding_yearly_usd": round(bleeding * 12, 2),
        "wasted_to_date_usd": round(wasted, 2),
        "stopped_monthly_usd": round(stopped_monthly, 2),
        "refunded_usd": round(refunded, 2),
        "our_share_percent": RECOVERY_SHARE * 100,
        "our_share_usd": round(refunded * RECOVERY_SHARE, 2),
        "note": (
            "Our share is taken from money actually refunded into the "
            "account, never from a projected saving. A cancellation can "
            "be reversed next week; a refund cannot."
        ),
    }


RECOVERY_LINE_KIND = "recovery_share"


def recovery_share_line(tenant) -> Optional[Dict[str, Any]]:
    """The share owed on refunds not billed yet, as one invoice line.

    `our_share_usd` above was computed and shown, and nothing ever billed
    it: no invoice line anywhere referred to it. This is that line.

    What it bills, and on what:

    - **Refunds only.** Money the customer has typed in as actually
      returned -- never a projected saving, never a cancellation that
      could be reversed next week. The same rule `recovery_for` states.
    - **Each refund once.** What has been billed is read back from the
      invoices themselves, every recovery line carrying the refunds it
      covered as `basis_usd`. There is no separate counter to fall out of
      step with the ledger: a voided invoice stops counting, so its
      refunds come due again; an invoice that failed to be created never
      counted at all. Callers hold the store lock from this call until
      the invoice exists, so two invoices cannot both claim the same
      refund.
    - **Never on a trial.** A trial is not charged for anything, and this
      is no exception.
    - **Never negative.** A refund corrected downward after it was billed
      bills nothing further until refunds pass what was already billed.
      There is deliberately no credit note to issue.
    """
    if getattr(tenant, "plan", "trial") == "trial":
        return None

    refunded = recovery_for(tenant.tenant_id)["refunded_usd"]
    already = round(sum(
        float(line.get("basis_usd") or 0.0)
        for invoice in STORE.invoices_for(tenant.tenant_id)
        if invoice.state != "void"
        for line in invoice.lines
        if line.get("kind") == RECOVERY_LINE_KIND
    ), 2)
    new_refunds = round(refunded - already, 2)
    share = round(new_refunds * RECOVERY_SHARE, 2)
    if share <= 0:
        # Includes a refund too small to round to a cent: it is not
        # recorded as billed, so it waits and is billed once it adds up.
        return None
    return {
        "kind": RECOVERY_LINE_KIND,
        "description": (
            f"Recovery share: {RECOVERY_SHARE * 100:g}% of "
            f"${new_refunds:,.2f} refunded for staff who have left"
        ),
        "quantity": 1,
        "unit_price_usd": share,
        "amount_usd": share,
        "basis_usd": new_refunds,
    }


def offboarding_state(tenant_id: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """One leaver, and what they are still costing."""
    person = {"phone": row.get("phone"), "email": row.get("email"),
              "full_name": row.get("full_name")}
    on_roster = still_on_the_roster(tenant_id, person)
    maybe = possibly_on_the_roster(tenant_id, person)

    lines = [cost_state(raw, row.get("left_on", ""))
             for raw in costs_for(tenant_id, row["offboarding_id"])]
    running = [line for line in lines if line["still_running"]]

    return {
        **row,
        "costs": lines,
        "still_running_count": len(running),
        "monthly_usd": round(sum(line["monthly_saving_usd"]
                                 for line in running), 2),
        "wasted_usd": round(sum(line["wasted_usd"] for line in lines), 2),
        "refunded_usd": round(sum(line["refunded_usd"] for line in lines), 2),
        "still_on_the_roster": on_roster,
        "possibly_on_the_roster": maybe,
        "urgent": (
            "This person has left and alerts would still be routed to "
            "them. Nobody is coming when that phone rings."
            if on_roster else
            "Somebody of this name is still on the on-call roster under a "
            "different number. Worth checking it is not them."
            if maybe else ""
        ),
    }


# --- telling the owner, without being asked --------------------------------

# The horizons above answer "how far ahead is a lapse worth raising". This
# adds the day itself, which is not a warning about the future but is
# certainly news.
ALERT_DAYS = tuple(sorted(set(HORIZONS) | {0}, reverse=True))


def _alert_horizon(left: int) -> Optional[int]:
    """The tightest rung this credential has reached, or None.

    Deliberately not `left in ALERT_DAYS`. An exact match only fires if a
    pass happens to run on the exact day, so a process that was restarting
    on the 30th never sends the 30-day notice and nothing ever notices the
    hole. Taking the tightest rung at or above `left` fires on the first
    pass after the rung is crossed instead, and the mail layer's dedupe
    key -- one per credential and rung -- keeps the days in between quiet.

    It also means an outage does not deliver a backlog: a licence that was
    at 31 days when the process died and 13 when it came back sends the
    14-day notice and never the stale 30-day one.
    """
    return min((day for day in ALERT_DAYS if left <= day), default=None)


def _nag_stamp(days_overdue: int, today: date) -> str:
    """How often a problem that has not been fixed is worth repeating.

    Daily for the first week, then every seven days. Somebody working on
    an expired licence is worth an email every morning -- but only for so
    long. After a week the daily email has stopped being an alarm and
    become the thing you filter, and a filtered alarm is worse than a
    quieter one, because it takes the next real alert with it.

    Counted in seven-day blocks from the day the problem started, not by
    the calendar week. The calendar version shipped and was wrong: on a
    Sunday and the Monday after it, two "weekly" emails went out one day
    apart, because the week rolled over between them. Where the boundary
    falls in the month has nothing to do with how long somebody has been
    working without a licence.
    """
    if days_overdue <= 7:
        return today.isoformat()
    return f"week-{days_overdue // 7}"


def alerts_due(today: Optional[date] = None) -> Dict[str, Dict[str, Any]]:
    """What each owner should be told today, keyed by tenant.

    Fleet-wide in one pass, for the reason written on `fleet_summary`.

    Two lanes, because they are not the same message. **Advisory** is a
    rung crossed on the way down: sent once, ever, per credential per
    rung. **Urgent** is a line already crossed -- somebody who may not
    legally be doing the job they are rostered for today, or an alert
    routed to a phone whose owner handed their keys back -- and it
    repeats on the ladder in `_nag_stamp` until it is dealt with.

    Every item carries its own dedupe key, so the caller sends without
    having to remember what it sent yesterday. That is the only reason
    this is safe to run hourly.
    """
    today = today or _today()
    out: Dict[str, Dict[str, Any]] = {}

    def lane(tenant_id: str, name: str) -> List[Dict[str, Any]]:
        return out.setdefault(
            tenant_id, {"urgent": [], "advisory": []}
        )[name]

    people_by_tenant: Dict[str, Dict[str, Any]] = {}
    for row in STORE._db.all(STAFF_KIND):
        people_by_tenant.setdefault(row["tenant_id"], {})[row["staff_id"]] = row

    for cred in STORE._db.all(CREDENTIAL_KIND):
        tenant_id = cred["tenant_id"]
        person = people_by_tenant.get(tenant_id, {}).get(cred["staff_id"])
        # Somebody who has left does not need their first aid certificate
        # renewed, and saying so is how an alert list loses its reader.
        if person is None or not person.get("active", True):
            continue
        left = days_left(cred, today)
        blocking = bool(cred.get("required_to_work"))

        if left < 0 and blocking:
            lane(tenant_id, "urgent").append({
                "kind": "licence_expired",
                "credential_id": cred["credential_id"],
                "staff_id": cred["staff_id"],
                "person": person["full_name"],
                "credential": cred["name"],
                "days": left,
                "dedupe": (f"licence-expired:{cred['credential_id']}:"
                           f"{_nag_stamp(abs(left), today)}"),
                "line": (
                    f"{person['full_name']}'s {cred['name']} expired "
                    f"{abs(left)} day(s) ago. It is required for the job, "
                    "so they may not be allowed on shift until it is "
                    "renewed."
                ),
            })
            continue

        # Reached by anything not caught above: a rung on the way down, and
        # also a credential that has already lapsed but does not stop the
        # job. `_alert_horizon` answers 0 for both the expiry day and any
        # day after it, so an expiry the pass slept through still sends
        # the one notice it owes, under the key the day itself would have
        # used -- one notice, not two, and not none.
        horizon = _alert_horizon(left)
        if horizon is None:
            continue
        if left < 0:
            when = f"expired {abs(left)} day(s) ago"
        elif left == 0:
            when = "expires today"
        else:
            when = f"expires in {left} day(s)"
        lane(tenant_id, "advisory").append({
            "kind": "licence_expiring",
            "credential_id": cred["credential_id"],
            "staff_id": cred["staff_id"],
            "person": person["full_name"],
            "credential": cred["name"],
            "days": left,
            "horizon": horizon,
            "dedupe": f"licence:{cred['credential_id']}:{horizon}",
            "line": (
                f"{person['full_name']}'s {cred['name']} {when}"
                + (" and is required for the job."
                   if blocking else ".")
            ),
        })

    # The roster is read once per tenant and kept, rather than asking
    # `still_on_the_roster` per leaver: that helper queries the contacts
    # table on every call, which is fine for one departure on one screen
    # and is a full scan per leaver in an hourly fleet-wide pass.
    rosters: Dict[str, Dict[str, str]] = {}
    for row in STORE._db.all(OFFBOARD_KIND):
        tenant_id = row["tenant_id"]
        if tenant_id not in rosters:
            roster: Dict[str, str] = {}
            for contact in STORE.contacts_for(tenant_id):
                digits = _digits(contact.phone)
                if contact.active and digits:
                    roster.setdefault(digits, contact.phone)
            rosters[tenant_id] = roster
        phone = _digits(row.get("phone"))
        # No number on the staff record is not a clean roster, it is an
        # unanswerable question, and a guess either way is worse than
        # the offboarding checklist the owner already has.
        if not phone or phone not in rosters[tenant_id]:
            continue
        numbers = [rosters[tenant_id][phone]]
        try:
            gone = max(0, (today - _parse_day(row["left_on"])).days)
        except (KeyError, ValueError):
            gone = 0
        lane(tenant_id, "urgent").append({
            "kind": "leaver_on_roster",
            "offboarding_id": row["offboarding_id"],
            "person": row.get("full_name", "Someone"),
            "days": gone,
            "dedupe": (f"leaver-roster:{row['offboarding_id']}:"
                       f"{_nag_stamp(gone, today)}"),
            "line": (
                f"{row.get('full_name', 'Someone')} left "
                f"{gone} day(s) ago and is still on the on-call roster "
                f"({', '.join(numbers)}). An alert sent there is an alert "
                "nobody answers."
            ),
        })

    return out


# A message the mail layer took but has not delivered: the host is not
# configured yet, or an attempt failed and it will be retried. Neither is
# a send, and neither is a failure -- it is the state where the alerter
# looks fine from here and nobody is actually being warned.
WAITING_ON_THE_MAIL_HOST = ("not_configured", "queued")


def _render_alert(tenant: Tenant, items: List[Dict[str, Any]],
                  urgent: bool) -> str:
    lead = (
        "These need attention today:"
        if urgent else
        "A heads-up on paperwork with a date on it:"
    )
    body = [f"{tenant.contact_name},", "", lead, ""]
    body += [f"  - {item['line']}" for item in items]
    body += [
        "",
        "Renewals, dates and who is on the roster are all on the "
        "Licences & people card in your console.",
    ]
    if urgent:
        body += [
            "",
            "This is the only email of its kind you will get today. If it "
            "is still true next week it will arrive weekly rather than "
            "daily -- not because it stopped mattering, but because a "
            "daily email nobody can act on is the one that gets filtered.",
        ]
    return "\n".join(body)


def send_licence_alerts(now: Optional[datetime] = None,
                        hour_utc: Optional[int] = None) -> Dict[str, Any]:
    """Email each owner what is lapsing on their account.

    The calendar and the console card both answer this, and both need
    somebody to open a browser and think to look. A licence nobody
    renewed is not discovered by looking; it is discovered by an
    inspector, or by an insurer reading the roster after a claim. So it
    arrives on its own.

    `hour_utc` is a floor, not an exact time, and is passed in rather than
    read here so that this and the daily digest cannot drift apart. A
    notice that somebody cannot legally work, delivered at 3am, reads as
    an emergency and is not one.
    """
    now = now or utc_now()
    if hour_utc is not None and now.hour < hour_utc:
        return {"sent": [], "sent_count": 0, "queued": [], "queued_count": 0,
                "skipped": "before_send_hour", "failed_tenants": []}

    due = alerts_due(now.date())
    sent: List[str] = []
    queued: List[str] = []
    failures: List[str] = []

    for tenant in STORE.list_tenants():
        # Suspended accounts are not being monitored, and a suspended
        # account is one we are already in a conversation with.
        if tenant.suspended:
            continue
        lanes = due.get(tenant.tenant_id)
        if not lanes:
            continue
        for name, urgent in (("urgent", True), ("advisory", False)):
            items = lanes[name]
            if not items:
                continue
            try:
                # The key is the whole set, so a new problem on the same
                # day sends a second email rather than being swallowed by
                # the first one's key.
                key = "|".join(sorted(item["dedupe"] for item in items))
                result = send_mail(
                    to_address=tenant.contact_email,
                    subject=(
                        f"{tenant.company_name}: "
                        + (
                            f"{len(items)} thing(s) needing attention today"
                            if urgent else
                            f"{len(items)} licence(s) coming up for renewal"
                        )
                    ),
                    body=_render_alert(tenant, items, urgent),
                    dedupe_key=f"licence-alert:{tenant.tenant_id}:{key}",
                    klass="transactional",
                    tenant_id=tenant.tenant_id,
                )
                if result.get("sent"):
                    sent.append(f"{tenant.tenant_id}:{name}")
                elif result.get("status") in WAITING_ON_THE_MAIL_HOST:
                    # Counted apart from sent, not folded into it. With no
                    # mail host configured every message queues, and a
                    # single "sent" figure that includes them reads as a
                    # working alerter right up to the day somebody asks
                    # why nobody was warned.
                    queued.append(f"{tenant.tenant_id}:{name}")
            except Exception as exc:  # noqa: BLE001 - one account must not stop the rest
                logger.exception(
                    "Licence alert failed for %s (%s).", tenant.tenant_id, exc
                )
                failures.append(tenant.tenant_id)

    return {"sent": sent, "sent_count": len(sent),
            "queued": queued, "queued_count": len(queued),
            "skipped": None, "failed_tenants": failures}


# --- routes ----------------------------------------------------------------


class StaffMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(..., min_length=1, max_length=120)
    role: str = Field("", max_length=120)
    email: str = Field("", max_length=254)
    phone: str = Field("", max_length=40)


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staff_id: str
    name: str = Field(..., min_length=2, max_length=160)
    expires_on: str = Field(..., description="YYYY-MM-DD.")
    reference: str = Field("", max_length=160,
                           description="Certificate or licence number.")
    required_to_work: bool = Field(
        True,
        description="Whether this person may not do the job without it.",
    )

    @field_validator("expires_on")
    @classmethod
    def a_real_date(cls, value: str) -> str:
        try:
            _parse_day(value)
        except ValueError:
            raise ValueError("expires_on must be a date, as YYYY-MM-DD")
        return value


class Departure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staff_id: str
    left_on: str = Field(..., description="YYYY-MM-DD.")
    reason: str = Field("", max_length=200)

    @field_validator("left_on")
    @classmethod
    def a_real_date(cls, value: str) -> str:
        try:
            _parse_day(value)
        except ValueError:
            raise ValueError("left_on must be a date, as YYYY-MM-DD")
        return value


class LeakingCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offboarding_id: str
    label: str = Field(..., min_length=2, max_length=160,
                       description="What is still being paid for.")
    monthly_usd: Finite = Field(..., ge=0, le=1_000_000,
                                description="What it costs a month.")
    kind: str = Field("other", max_length=40)
    billed_by: str = Field("", max_length=160,
                           description="Who sends the invoice.")
    reference: str = Field("", max_length=160,
                           description="Account or contract number.")


class StopCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stopped_on: Optional[str] = Field(
        None, description="YYYY-MM-DD. Leave out to mark it running again.")
    refunded_usd: Finite = Field(
        0.0, ge=0, le=10_000_000,
        description="Money actually returned, not the saving going forward.")

    @field_validator("stopped_on")
    @classmethod
    def a_real_date(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        try:
            _parse_day(value)
        except ValueError:
            raise ValueError("stopped_on must look like 2026-03-14")
        return value


@router.get("")
def list_people(tenant: Tenant = Depends(require_tenant)):
    """Everyone on the books, with what is current and what is not."""
    people = staff_for(tenant.tenant_id)
    creds = credentials_for(tenant.tenant_id)
    by_person: Dict[str, List[Dict[str, Any]]] = {}
    for cred in creds:
        by_person.setdefault(cred["staff_id"], []).append(
            {**cred, "days_left": days_left(cred), "state": state_of(cred)}
        )
    return {
        "count": len(people),
        "people": [
            {**person, "credentials": by_person.get(person["staff_id"], [])}
            for person in people
        ],
        "suggested_credentials": sorted(set(
            sum((COMMON_CREDENTIALS.get(s.industry_vertical, [])
                 for s in STORE.sensors_for(tenant.tenant_id)), [])
        )) or GENERIC_CREDENTIALS,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def add_person(
    payload: StaffMember,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Put somebody on the books."""
    staff_id = STORE._next_id("STF")
    row = {
        "staff_id": staff_id, "tenant_id": tenant.tenant_id,
        "full_name": payload.full_name.strip(),
        "role": payload.role.strip(),
        "email": payload.email.strip(),
        "phone": payload.phone.strip(),
        "active": True, "added_at": iso(utc_now()),
    }
    STORE._db.put(STAFF_KIND, staff_id, row)
    write_audit(tenant, operator, "staff.added", payload.full_name)
    return {"person": row}


@router.post("/credentials", status_code=status.HTTP_201_CREATED)
def add_credential(
    payload: Credential,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("operator")),
):
    """Record a licence and the date it runs out."""
    person = STORE._db.get(STAFF_KIND, payload.staff_id)
    if person is None or person.get("tenant_id") != tenant.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such person on this account.")
    credential_id = STORE._next_id("CRD")
    row = {
        "credential_id": credential_id, "tenant_id": tenant.tenant_id,
        "staff_id": payload.staff_id, "name": payload.name.strip(),
        "expires_on": payload.expires_on, "reference": payload.reference.strip(),
        "required_to_work": payload.required_to_work,
        "added_at": iso(utc_now()),
    }
    STORE._db.put(CREDENTIAL_KIND, credential_id, row)
    write_audit(tenant, operator, "credential.added",
                f"{person['full_name']}: {payload.name} to {payload.expires_on}")
    return {"credential": {**row, "days_left": days_left(row),
                           "state": state_of(row)}}


@router.get("/calendar")
def expiry_calendar(
    days: int = Query(180, ge=7, le=730),
    tenant: Tenant = Depends(require_tenant),
):
    """Every licence expiry ahead, by month."""
    return calendar(tenant.tenant_id, days=days)


@router.get("/alerts")
def pending_alerts(tenant: Tenant = Depends(require_tenant)):
    """What would be emailed about this account today, and why.

    An alerter the owner cannot inspect is one they have to trust. This
    is the same function the sender runs, so the page and the inbox
    cannot disagree: if a line is not here, no email carries it.
    """
    lanes = alerts_due().get(tenant.tenant_id) or {"urgent": [], "advisory": []}
    return {
        "urgent": lanes["urgent"],
        "advisory": lanes["advisory"],
        "count": len(lanes["urgent"]) + len(lanes["advisory"]),
        "sent_to": tenant.contact_email,
        "note": (
            "Anything urgent is emailed daily for a week, then weekly, "
            "until it is dealt with. A renewal notice is sent once per "
            "licence at 60, 30, 14, 7 and 1 days out, and once on the "
            "day itself."
        ),
    }


@router.post("/departures", status_code=status.HTTP_201_CREATED)
def record_departure(
    payload: Departure,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("owner")),
):
    """Record that somebody has left, and open what is owed.

    Owner only: a departure starts obligations that cost money, and
    marks a person inactive across the account.
    """
    person = STORE._db.get(STAFF_KIND, payload.staff_id)
    if person is None or person.get("tenant_id") != tenant.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such person on this account.")
    STORE._db.put(STAFF_KIND, payload.staff_id, {**person, "active": False})
    row = {
        "offboarding_id": STORE._next_id("OFF"),
        "tenant_id": tenant.tenant_id,
        "staff_id": payload.staff_id,
        "full_name": person["full_name"],
        "email": person.get("email", ""),
        "phone": person.get("phone", ""),
        "left_on": payload.left_on,
        "reason": payload.reason.strip(),
        "opened_at": iso(utc_now()),
        "completed": {},
    }
    STORE._db.put(OFFBOARD_KIND, row["offboarding_id"], row)
    write_audit(tenant, operator, "staff.departed",
                f"{person['full_name']} on {payload.left_on}")
    return {"offboarding": offboarding_state(tenant.tenant_id, row)}


@router.get("/departures")
def list_departures(tenant: Tenant = Depends(require_tenant)):
    """Everyone who has left, and what they are still costing."""
    rows = [offboarding_state(tenant.tenant_id, r)
            for r in offboarding_for(tenant.tenant_id)]
    reachable = [r for r in rows if r["still_on_the_roster"]]
    return {
        "count": len(rows),
        "departures": rows,
        "recovery": recovery_for(tenant.tenant_id),
        "still_reachable_by_alerts": len(reachable),
        "note": (
            f"{len(reachable)} person(s) who have left are still on the "
            "on-call roster. An alert routed to somebody who handed their "
            "keys back is an alert nobody answers."
            if reachable else
            "Nobody who has left is still on the on-call roster."
        ),
    }


def _leaver_or_404(offboarding_id: str, tenant: Tenant) -> Dict[str, Any]:
    row = STORE._db.get(OFFBOARD_KIND, offboarding_id)
    if row is None or row.get("tenant_id") != tenant.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such departure on this account.")
    return row


@router.get("/recovery")
def money_going_out(tenant: Tenant = Depends(require_tenant)):
    """What people who left are still costing, and what has been clawed back."""
    return recovery_for(tenant.tenant_id)


@router.get("/recovery/common")
def things_to_check(tenant: Tenant = Depends(require_tenant)):
    """A starting list, because nobody remembers the parking permit.

    Typical monthly figures, offered so a blank form has something in it.
    Every one is overwritten by what the customer actually pays.
    """
    return {
        "common": [
            {"kind": key, "label": label, "typical_monthly_usd": amount}
            for key, (label, amount) in COMMON_LEAKS.items()
        ],
        "note": (
            "Typical amounts, not yours. Put in the real figure from the "
            "invoice -- the whole number this produces is only as good as "
            "what goes in."
        ),
    }


@router.post("/recovery", status_code=status.HTTP_201_CREATED)
def record_cost(
    payload: LeakingCost,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("owner")),
):
    """Record something still being paid for somebody who has left."""
    leaver = _leaver_or_404(payload.offboarding_id, tenant)
    cost_id = STORE._next_id("LEK")
    row = {
        "cost_id": cost_id,
        "tenant_id": tenant.tenant_id,
        "offboarding_id": payload.offboarding_id,
        "label": payload.label.strip(),
        "kind": payload.kind,
        "monthly_usd": float(payload.monthly_usd),
        "billed_by": payload.billed_by.strip(),
        "reference": payload.reference.strip(),
        "stopped_on": None,
        "refunded_usd": 0.0,
        "added_at": iso(utc_now()),
    }
    STORE._db.put(COST_KIND, cost_id, row)
    write_audit(tenant, operator, "recovery.found",
                f"{leaver['full_name']}: {row['label']} at "
                f"${row['monthly_usd']:.2f}/month")
    return {"cost": cost_state(row, leaver.get("left_on", ""))}


@router.post("/recovery/{cost_id}/stop")
def stop_cost(
    cost_id: str,
    payload: StopCost,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("owner")),
):
    """Mark it cancelled, and record anything actually refunded.

    The refund is typed in rather than worked out, because only the
    person reading the credit note knows what came back. Our share is
    taken from this figure and from nothing else.
    """
    row = STORE._db.get(COST_KIND, cost_id)
    if row is None or row.get("tenant_id") != tenant.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such cost on this account.")
    leaver = _leaver_or_404(row["offboarding_id"], tenant)
    row = {**row, "stopped_on": payload.stopped_on,
           "refunded_usd": float(payload.refunded_usd)}
    STORE._db.put(COST_KIND, cost_id, row)
    write_audit(tenant, operator, "recovery.stopped",
                f"{leaver['full_name']}: {row['label']}"
                + (f", ${row['refunded_usd']:.2f} refunded"
                   if row["refunded_usd"] else ""))
    return {"cost": cost_state(row, leaver.get("left_on", "")),
            "account": recovery_for(tenant.tenant_id)}
