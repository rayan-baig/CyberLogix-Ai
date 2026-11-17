"""Pause instead of cancel, a one-time offer to stay, and the numbers.

When an owner goes to cancel, the console first offers two things, with
the cancel button right there beside them, never hidden:

* **Pause for 1, 2 or 3 months.** For a seasonal business, or one closed
  for a refit. From the moment it starts until the date chosen:
  - no bill: the contract's next billing date, and every one after it,
    moves later by exactly the length of the pause (Subscription.shifts),
    so no month is billed while paused and the days already paid for
    are still there afterwards. Billing here is our own invoices, not a
    card subscription somewhere else, so this is the whole of it.
  - no alerts: readings are still taken and kept, but nothing is texted,
    called or emailed about a unit, and the console says so at the top.
  - nothing is deleted.
  It ends on its own on the chosen date, and billing and alerts carry on.
  Three days before, the owner gets a reminder email. An owner can also
  end it early, which gives back the unused part of the pause.
* **Stay, with a discount.** CYBERLOGIX_SAVE_OFFER_PERCENT (25) off the
  next CYBERLOGIX_SAVE_OFFER_MONTHS (2) months, as a line on each of
  those invoices. Once per business, ever.

Cancelling is the existing contracts.cancel, unchanged: one press.

Every pause, save and cancel is also written down as a numbered event
with no business named in it, so the operator's numbers survive a
business deleting itself.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from accounts import require_role
from auth import require_platform_admin, require_tenant_any_state, write_audit
from store import STORE, Subscription, Tenant, User, _parse, iso, utc_now

logger = logging.getLogger("cyberlogix.retention")

router = APIRouter(prefix="/api/leaving", tags=["Pause or cancel"])

PAUSE_KIND = "account_pause"
OFFER_KIND = "save_offer"
EVENT_KIND = "retention_event"

PAUSE_MONTHS = (1, 2, 3)
REMINDER_DAYS = 3
# At most this many months paused in any twelve, counted as time actually
# spent paused (coming back early gives the rest back).
PAUSE_MONTHS_PER_YEAR = 3
PAUSE_LIMIT_WORDS = (f"You can pause for up to {PAUSE_MONTHS_PER_YEAR} months a year.")


def _number(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return min(high, max(low, value))


SAVE_OFFER_PERCENT = _number("CYBERLOGIX_SAVE_OFFER_PERCENT", 25.0, 1.0, 90.0)
SAVE_OFFER_MONTHS = int(_number("CYBERLOGIX_SAVE_OFFER_MONTHS", 2, 1, 12))


def _cents(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _day(moment: Optional[datetime]) -> str:
    return moment.strftime("%d %B %Y").lstrip("0") if moment else ""


def add_months(moment: datetime, months: int) -> datetime:
    from records import add_months as _add

    result: datetime = _add(moment, months)
    return result


# ---- the numbers -------------------------------------------------------------


def record_event(event: str, months: Optional[int] = None) -> None:
    """One pause, save, cancel or founding member code, with no business
    named in it."""
    STORE._db.put(EVENT_KIND, STORE._next_id("RET"),
                  {"event": event, "at": iso(utc_now()), "months": months})


def numbers(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Pauses, saves, cancels and founding member codes entered: in the
    last 30 days and ever."""
    now = now or utc_now()
    month_ago = now - timedelta(days=30)
    out: Dict[str, Any] = {}
    for event in ("pause", "save", "cancel", "founder"):
        rows = [r for r in STORE._db.all(EVENT_KIND) if r.get("event") == event]
        recent = [r for r in rows if (_parse(r.get("at")) or now) >= month_ago]
        out[event] = {"last_30_days": len(recent), "ever": len(rows)}
    out["paused_now"] = sum(1 for r in STORE._db.all(PAUSE_KIND) if _live(r, now))
    return out


# ---- pauses ------------------------------------------------------------------


def _ends(row: Dict[str, Any]) -> Optional[datetime]:
    return _parse(row.get("ended_at")) or _parse(row.get("resume_at"))


def _live(row: Dict[str, Any], now: datetime) -> bool:
    start, end = _parse(row.get("paused_at")), _ends(row)
    return start is not None and end is not None and start <= now < end


def current_pause(tenant_id: str, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The pause this business is in right now, if any."""
    now = now or utc_now()
    for row in STORE._db.all(PAUSE_KIND):
        if row.get("tenant_id") == tenant_id and _live(row, now):
            return row
    return None


def is_paused(tenant_id: str, now: Optional[datetime] = None) -> bool:
    """Whether alerts and billing are stopped for this business now.

    Worked out from the dates alone, so a pause ends on time even if
    nothing has run since.
    """
    return current_pause(tenant_id, now) is not None


def public_pause(row: Dict[str, Any]) -> Dict[str, Any]:
    resume = _ends(row)
    return {
        "pause_id": row.get("pause_id"),
        "months": row.get("months"),
        "paused_at": row.get("paused_at"),
        "resume_at": iso(resume) if resume else None,
        "resume_on": _day(resume),
        "words": (f"Paused until {_day(resume)}. No bills and no alerts until "
                  "then: we are not texting or calling anyone about your units. "
                  "Readings are still kept."),
    }


def pause_months_used(tenant_id: str, until: Optional[datetime] = None) -> float:
    """Months spent paused in the twelve months up to `until` (default now).

    Each pause counts by its booked months, scaled to the part of it that
    was actually used and that falls inside the window.
    """
    until = until or utc_now()
    window_start = add_months(until, -12)
    used = 0.0
    for row in STORE._db.all(PAUSE_KIND):
        if row.get("tenant_id") != tenant_id:
            continue
        start, end = _parse(row.get("paused_at")), _ends(row)
        booked_end = _parse(row.get("resume_at"))
        if start is None or end is None or booked_end is None or booked_end <= start:
            continue
        inside = (min(end, until) - max(start, window_start)).total_seconds()
        if inside <= 0:
            continue
        booked = (booked_end - start).total_seconds()
        used += int(row.get("months") or 0) * inside / booked
    return round(used, 4)


def months_allowed(tenant_id: str, months: int, now: Optional[datetime] = None) -> bool:
    """Whether a pause of `months` from `now` keeps within the yearly limit:
    the twelve months up to its end hold no more than PAUSE_MONTHS_PER_YEAR."""
    now = now or utc_now()
    used = pause_months_used(tenant_id, add_months(now, months))
    return used + months <= PAUSE_MONTHS_PER_YEAR + 1e-6


def _first_unstarted_period(sub: Subscription, now: datetime) -> int:
    index = sub.periods_billed
    while sub.period_start(index) <= now:
        index += 1
    return index


def _set_shift(row: Dict[str, Any], seconds: float) -> None:
    """Make the pause's shift on its contract this long."""
    sub_id = row.get("subscription_id")
    if not sub_id:
        return
    with STORE._lock:
        sub = STORE.get_subscription(str(sub_id))
        if sub is None:
            return
        for shift in sub.shifts:
            if shift.get("pause_id") == row.get("pause_id"):
                shift["seconds"] = max(0.0, round(seconds, 3))
        STORE.save_subscription(sub)


def start_pause(tenant: Tenant, months: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Pause a business from now for `months` months. Raises if it cannot."""
    now = now or utc_now()
    if months not in PAUSE_MONTHS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="A pause is for 1, 2 or 3 months.")
    if tenant.plan == "trial":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A free trial cannot be paused. It ends on its own, with nothing to pay.")
    resume = add_months(now, months)
    with STORE._lock:
        if current_pause(tenant.tenant_id, now) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="This business is already paused.")
        if not months_allowed(tenant.tenant_id, months, now):
            used = pause_months_used(tenant.tenant_id, now)
            room = [m for m in PAUSE_MONTHS if months_allowed(tenant.tenant_id, m, now)]
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(f"{PAUSE_LIMIT_WORDS} This business has been paused for "
                        f"about {used:.1f} of the last 12 months, so "
                        + (f"the longest pause it can take now is {max(room)} "
                           f"month{'' if max(room) == 1 else 's'}."
                           if room else "it cannot pause again just yet.")))
        pause_id = STORE._next_id("PAU")
        sub = STORE.active_subscription(tenant.tenant_id)
        from_period = None
        if sub is not None and sub.started_at is not None:
            from_period = _first_unstarted_period(sub, now)
            sub.shifts.append({"from_period": from_period,
                               "seconds": round((resume - now).total_seconds(), 3),
                               "pause_id": pause_id})
            STORE.save_subscription(sub)
        row: Dict[str, Any] = {
            "pause_id": pause_id,
            "tenant_id": tenant.tenant_id,
            "months": months,
            "paused_at": iso(now),
            "resume_at": iso(resume),
            "ended_at": None,
            "ended_why": "",
            "subscription_id": sub.subscription_id if sub is not None else None,
            "from_period": from_period,
            "reminded_at": None,
            "resumed_noted_at": None,
        }
        STORE._db.put(PAUSE_KIND, pause_id, row)
    record_event("pause", months)
    logger.info("Business %s paused for %d month(s), until %s.", tenant.tenant_id,
                months, iso(resume))
    return row


def end_pause(tenant: Tenant, why: str, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """End a pause early. The unused part of it is given back: the next
    bill moves only as far as the time actually spent paused."""
    now = now or utc_now()
    with STORE._lock:
        row = current_pause(tenant.tenant_id, now)
        if row is None:
            return None
        start = _parse(row.get("paused_at")) or now
        row["ended_at"] = iso(now)
        row["ended_why"] = why
        STORE._db.put(PAUSE_KIND, str(row["pause_id"]), row)
        _set_shift(row, (now - start).total_seconds())
    logger.info("Pause %s for %s ended early (%s).", row["pause_id"], tenant.tenant_id, why)
    return row


def carry_pause(old: Subscription, fresh: Subscription,
                now: Optional[datetime] = None) -> None:
    """Hand a pause still running on `old` over to `fresh`, its successor.

    A contract renewed (or switched between monthly and yearly) during a
    pause starts where the old one's paid time ends, and that end already
    carries the whole pause. Moved here as a shift of its own, coming back
    early (end_pause) shortens it on the contract that now bills, so the
    unused part of the pause is not left as free days.
    """
    now = now or utc_now()
    with STORE._lock:
        row = current_pause(old.tenant_id, now)
        if row is None or row.get("subscription_id") != old.subscription_id:
            return
        shift = next((s for s in old.shifts if s.get("pause_id") == row.get("pause_id")), None)
        if shift is None or int(shift.get("from_period") or 0) > old.periods_billed:
            return
        live = STORE.get_subscription(fresh.subscription_id) or fresh
        seconds = float(shift.get("seconds") or 0.0)
        if (live.started_at is None or seconds <= 0
                or live.started_at != old.period_start(old.periods_billed)):
            return
        live.started_at = live.started_at - timedelta(seconds=seconds)
        live.shifts.append({"from_period": 0, "seconds": seconds,
                            "pause_id": row.get("pause_id")})
        STORE.save_subscription(live)
        if live is not fresh:
            fresh.started_at, fresh.shifts = live.started_at, [dict(s) for s in live.shifts]
        row["subscription_id"] = live.subscription_id
        row["from_period"] = 0
        STORE._db.put(PAUSE_KIND, str(row["pause_id"]), row)


def _remind(tenant: Tenant, row: Dict[str, Any]) -> bool:
    from mail import send as send_mail

    resume = _ends(row)
    sub = STORE.active_subscription(tenant.tenant_id)
    billing = (f" Your next bill is on {_day(sub.period_start(sub.periods_billed))}."
               if sub is not None and sub.started_at is not None else "")
    result = send_mail(
        to_address=tenant.contact_email,
        subject=f"CyberLogix comes back on {_day(resume)}",
        body=(f"{tenant.contact_name},\n\n"
              f"The pause for {tenant.company_name} ends on {_day(resume)}. From "
              "then we watch your fridges and freezers again and text and call your "
              f"team if one goes out of range.{billing}\n\n"
              "Nothing to do if that suits you. To come back sooner, or to cancel, "
              "open the console and go to Money, then Pause or cancel."),
        dedupe_key=f"pause-reminder:{row['pause_id']}",
        klass="transactional",
        tenant_id=tenant.tenant_id,
    )
    return bool(result.get("sent") or result.get("status") == "queued")


def run_pauses(now: Optional[datetime] = None) -> Dict[str, Any]:
    """The hourly pass: a reminder three days before each pause ends, and a
    line in the business's log when it has ended. The pause itself ends on
    its date whether or not this runs."""
    now = now or utc_now()
    reminded, resumed = 0, 0
    for row in STORE._db.all(PAUSE_KIND):
        tenant = STORE.get_tenant(str(row.get("tenant_id")))
        resume = _parse(row.get("resume_at"))
        if tenant is None or resume is None or row.get("ended_at"):
            continue
        try:
            if not row.get("reminded_at") and resume - timedelta(days=REMINDER_DAYS) <= now < resume:
                _remind(tenant, row)
                row["reminded_at"] = iso(now)
                STORE._db.put(PAUSE_KIND, str(row["pause_id"]), row)
                reminded += 1
            if now >= resume and not row.get("resumed_noted_at"):
                row["resumed_noted_at"] = iso(now)
                STORE._db.put(PAUSE_KIND, str(row["pause_id"]), row)
                STORE.record_audit(
                    tenant_id=tenant.tenant_id, actor="CyberLogix", actor_role="system",
                    action="pause.ended",
                    detail="The pause ended on its date. Alerts and billing are back on.")
                resumed += 1
        except Exception:  # noqa: BLE001 - one business must not stop the rest
            logger.exception("Pause pass failed for %s.", row.get("pause_id"))
    return {"reminded": reminded, "resumed": resumed}


# ---- the offer to stay -------------------------------------------------------


def offer_row(tenant_id: str) -> Optional[Dict[str, Any]]:
    return STORE._db.get(OFFER_KIND, tenant_id)


def _candidate(row: Dict[str, Any], sub: Subscription, index: int,
               lines: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """What one offer would take off the invoice for period `index`.

    Nothing is written: the caller records only the line it puts on.
    """
    from contracts import recurring_total

    if row.get("subscription_id") == sub.subscription_id and index < int(row.get("from_period") or 0):
        return None
    used: Dict[str, Any] = dict(row.get("used") or {})
    key = f"{sub.subscription_id}:{index}"
    again = key in used
    if again:
        months = int(used[key])
    else:
        left = int(row.get("months") or 0) - sum(int(v) for v in used.values())
        months = min(left, sub.months_per_period)
    if months <= 0:
        return None
    # What the customer actually pays for the recurring lines: on a yearly
    # invoice that is after its two free months, so "25% off 2 months" is
    # a quarter of two twelfths of the real yearly price.
    paid = recurring_total(lines) + sum(float(line["amount_usd"]) for line in lines
                                        if line.get("yearly_free"))
    base = Decimal(str(round(paid, 2)))
    percent = Decimal(str(row.get("percent") or 0))
    amount = _cents(base * percent / Decimal(100) * Decimal(months)
                    / Decimal(sub.months_per_period))
    if amount <= 0:
        return None
    total = int(row.get("months") or 0)
    done = sum(int(v) for k, v in used.items() if k != key) + months
    return {"key": key, "months": months, "amount": amount, "again": again,
            "percent": float(percent), "total": total, "done": done}


def _save_words(c: Dict[str, Any], sub: Subscription) -> str:
    which = (f"month {c['done']} of {c['total']}" if c["months"] == 1
             else f"{c['months']} of {c['total']} months")
    return f"Thank you for staying: {c['percent']:g}% off ({which})"


def _founder_words(c: Dict[str, Any], sub: Subscription) -> str:
    from founders import words, yearly_words

    if sub.annual_prepay:
        return yearly_words(c["percent"], c["months"])
    which = (f"month {c['done']} of {c['total']}" if c["months"] == 1
             else f"months {c['done'] - c['months'] + 1} to {c['done']} of {c['total']}")
    return f"{words(c['percent'], c['total'])} ({which})"


def discount_line(tenant: Tenant, sub: Subscription, index: int,
                  lines: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The one offer line on the invoice for period `index`, if any.

    Two offers make a line here: the offer to stay, and a founding member
    code (founders.py). They never both go on one invoice. When both
    would, the bigger line goes on, and the other is not used up by this
    invoice: it carries on to the next one.

    Called by contracts.bill_period with the store lock held, after any
    chain discount (the percentage is of the recurring lines as they stand
    after it). Which periods used an offer, and how many months each, is
    written down by period, so a voided invoice issued again gets the same
    line, and never an extra month. On a yearly invoice the months are
    months' worth of the twelve, taken at once.
    """
    from founders import FOUNDER_KIND, row_for

    choices = []
    for kind, row, describe in (
            (FOUNDER_KIND, row_for(tenant.tenant_id), _founder_words),
            (OFFER_KIND, offer_row(tenant.tenant_id), _save_words)):
        if row is None:
            continue
        found = _candidate(row, sub, index, lines)
        if found is not None:
            choices.append((kind, row, describe, found))
    if not choices:
        return None
    # A re-issued invoice keeps the line it had; otherwise the bigger one.
    again = [c for c in choices if c[3]["again"]]
    kind, row, describe, found = (again[0] if again
                                  else max(choices, key=lambda c: c[3]["amount"]))
    used: Dict[str, Any] = dict(row.get("used") or {})
    used[found["key"]] = found["months"]
    row["used"] = used
    STORE._db.put(kind, tenant.tenant_id, row)
    amount = found["amount"]
    return {
        "kind": "discount",
        "description": describe(found, sub),
        "quantity": 1,
        "unit_price_usd": -amount,
        "amount_usd": -amount,
    }


def accept_offer(tenant: Tenant) -> Dict[str, Any]:
    """Take the offer to stay. Once per business, ever."""
    sub = STORE.active_subscription(tenant.tenant_id)
    if sub is None or sub.started_at is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="The offer is for a business with a contract that bills it.")
    with STORE._lock:
        if offer_row(tenant.tenant_id) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="This business has already had the offer to stay.")
        row = {
            "tenant_id": tenant.tenant_id,
            "percent": SAVE_OFFER_PERCENT,
            "months": SAVE_OFFER_MONTHS,
            "accepted_at": iso(utc_now()),
            "subscription_id": sub.subscription_id,
            "from_period": sub.periods_billed,
            "used": {},
        }
        STORE._db.put(OFFER_KIND, tenant.tenant_id, row)
    record_event("save", SAVE_OFFER_MONTHS)
    logger.info("Business %s took the offer to stay.", tenant.tenant_id)
    return row


def estimated_saving(tenant: Tenant, sub: Optional[Subscription]) -> float:
    if sub is None or sub.started_at is None:
        return 0.0
    from contracts import monthly_recurring

    from pricing import YEARLY_MONTHS_CHARGED, YEARLY_MONTHS_COVERED

    monthly = Decimal(str(monthly_recurring(tenant, sub, sub.periods_billed)))
    if sub.annual_prepay:
        # A month of a yearly plan costs ten twelfths of a monthly one.
        monthly = monthly * Decimal(YEARLY_MONTHS_CHARGED) / Decimal(YEARLY_MONTHS_COVERED)
    return _cents(monthly * Decimal(str(SAVE_OFFER_PERCENT)) / Decimal(100)
                  * Decimal(SAVE_OFFER_MONTHS))


def offer_words() -> str:
    months = "month" if SAVE_OFFER_MONTHS == 1 else f"{SAVE_OFFER_MONTHS} months"
    return f"{SAVE_OFFER_PERCENT:g}% off your next {months}"


# ---- routes ------------------------------------------------------------------


class PauseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    months: int = Field(..., ge=1, le=3)


def state(tenant: Tenant, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Everything the Pause or cancel screen shows."""
    from founders import state as founder_state
    from pricing import CANCELLATION_RULE

    now = now or utc_now()
    sub = STORE.active_subscription(tenant.tenant_id)
    pause = current_pause(tenant.tenant_id, now)
    offer = offer_row(tenant.tenant_id)
    has_contract = sub is not None and sub.started_at is not None
    trial = tenant.plan == "trial"
    return {
        "on_trial": trial,
        "has_contract": has_contract,
        "paused": public_pause(pause) if pause else None,
        "pause_options": ([] if pause or trial else [
            {"months": m, "resume_on": _day(add_months(now, m)),
             "label": f"{m} month{'' if m == 1 else 's'}"} for m in PAUSE_MONTHS
            if months_allowed(tenant.tenant_id, m, now)]),
        "pause_limit_words": PAUSE_LIMIT_WORDS,
        "pause_words": ("No bills and no alerts until the date you pick: we stop "
                        "texting and calling about your units, and keep all your "
                        "data. Everything starts again on its own on that date."),
        "offer": {
            "available": has_contract and offer is None and not trial,
            "taken": offer is not None,
            "percent": SAVE_OFFER_PERCENT,
            "months": SAVE_OFFER_MONTHS,
            "words": offer_words(),
            "saving_usd": estimated_saving(tenant, sub) if offer is None else 0.0,
        },
        "cancel": {
            "possible": has_contract,
            "rule": CANCELLATION_RULE,
            "words": ("Nothing to cancel: nothing bills this business on its own."
                      if not has_contract else ""),
        },
        "next_billing_at": (iso(sub.period_start(sub.periods_billed))
                            if has_contract and sub is not None else None),
        # The founding member code (founders.py), shown on the same tab.
        "founder": founder_state(tenant, now),
    }


@router.get("")
def leaving(tenant: Tenant = Depends(require_tenant_any_state)):
    """What the Pause or cancel screen offers this business now."""
    return state(tenant)


@router.post("/pause")
def pause(payload: PauseRequest, tenant: Tenant = Depends(require_tenant_any_state),
          owner: User = Depends(require_role("owner"))):
    """Pause for 1, 2 or 3 months: no bills, no alerts, nothing deleted."""
    row = start_pause(tenant, payload.months)
    write_audit(tenant, owner, "pause.started",
                f"Paused for {payload.months} month(s), until {_day(_ends(row))}. "
                "No bills and no alerts until then.")
    return {**state(tenant), "message": public_pause(row)["words"]}


@router.post("/resume")
def resume(tenant: Tenant = Depends(require_tenant_any_state),
           owner: User = Depends(require_role("owner"))):
    """End a pause now: alerts back on at once, and billing carries on."""
    row = end_pause(tenant, "resumed early")
    if row is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This business is not paused.")
    write_audit(tenant, owner, "pause.ended", "Resumed early. Alerts are back on.")
    return {**state(tenant), "message": "Welcome back. Alerts are on again from now."}


@router.post("/stay")
def stay(tenant: Tenant = Depends(require_tenant_any_state),
         owner: User = Depends(require_role("owner"))):
    """Take the one-time offer to stay."""
    row = accept_offer(tenant)
    write_audit(tenant, owner, "offer.accepted",
                f"Took the offer to stay: {row['percent']:g}% off the next "
                f"{row['months']} month(s).")
    return {**state(tenant),
            "message": f"Thank you for staying. {offer_words()} comes off your bills."}


@router.get("/numbers", dependencies=[Depends(require_platform_admin)])
def operator_numbers():
    """Pauses, saves, cancels and founding member codes used, across every
    business, for the book."""
    import founders

    return {**numbers(), "settings": {"save_offer_percent": SAVE_OFFER_PERCENT,
                                      "save_offer_months": SAVE_OFFER_MONTHS,
                                      "pause_months": list(PAUSE_MONTHS),
                                      "founder_code_on": founders.is_open(),
                                      "founder_percent": founders.percent(),
                                      "founder_months": founders.months()}}
