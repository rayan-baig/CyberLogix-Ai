"""Founding member codes: a launch discount on our own invoices.

The company site's early-access list promises founding members a launch
discount by a code. Elsewhere that code is a card processor's promotion
code at checkout. CyberLogix bills from its own invoices (contracts.py),
with payment links for each invoice, so a processor's promotion code has
nothing to attach to here. The discount is one of our own invoice lines
instead, worked out exactly like the offer to stay (retention.py).

* **The code** is CYBERLOGIX_FOUNDER_CODE, compared ignoring case and
  spaces. Empty means off: nothing is shown and no code is taken.
* **What it gives:** CYBERLOGIX_FOUNDER_PERCENT (20) off the first
  CYBERLOGIX_FOUNDER_MONTHS (3) monthly invoices, as a line of its own on
  each. On a yearly plan it is that percentage of that many months' worth
  of the yearly price (what the customer actually pays, after the two
  free months), taken once, on the first yearly invoice.
* **Until:** CYBERLOGIX_FOUNDER_UNTIL, a date (YYYY-MM-DD), is the last day
  the code can be entered. A business that entered it in time keeps the
  discount after that date. Unset means no end date.
* **Where it is entered:** the code box at sign-up (the same box as a
  friend's or a partner's code), or later on the console's Money tab,
  but only before the business's first paid invoice.
* **Limits:** once per business, ever. It never goes on the same invoice
  as the offer to stay: when both would, the bigger of the two lines goes
  on, and the other is not used up by that invoice.

Each code entered is counted with no business named in it, for the
operator's numbers on /book.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from accounts import require_role
from auth import require_tenant_any_state, write_audit
from store import STORE, Tenant, User, iso, utc_now

logger = logging.getLogger("cyberlogix.founders")

router = APIRouter(prefix="/api/founder-code", tags=["Founding member code"])

FOUNDER_KIND = "founder_code"


# ---- the settings ------------------------------------------------------------


def normalise(code: Optional[str]) -> str:
    """Case and spaces do not matter: 'founder 2026' is 'FOUNDER2026'."""
    return re.sub(r"\s+", "", code or "").upper()


def _setting_code() -> str:
    return normalise(os.environ.get("CYBERLOGIX_FOUNDER_CODE"))


def percent() -> float:
    from retention import _number

    return _number("CYBERLOGIX_FOUNDER_PERCENT", 20.0, 1.0, 90.0)


def months() -> int:
    from retention import _number

    return int(_number("CYBERLOGIX_FOUNDER_MONTHS", 3, 1, 12))


def until() -> Optional[date]:
    """The last day the code can be entered, or None for no end date.

    A date that cannot be read closes the code rather than leaving it open
    for ever, and says so in the log.
    """
    raw = (os.environ.get("CYBERLOGIX_FOUNDER_UNTIL") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        logger.warning("CYBERLOGIX_FOUNDER_UNTIL=%r is not a YYYY-MM-DD date; "
                       "the founding member code is closed.", raw)
        return date.min


def _day(when: Optional[date]) -> str:
    return when.strftime("%d %B %Y").lstrip("0") if when else ""


def configured() -> bool:
    """Whether there is a code at all. Empty means off."""
    return bool(_setting_code())


def is_open(now: Optional[datetime] = None) -> bool:
    """Whether the code can be entered today."""
    if not configured():
        return False
    last = until()
    return last is None or (now or utc_now()).date() <= last


def matches(code: Optional[str]) -> bool:
    """Whether this is the founding member code (whatever the date)."""
    wanted = _setting_code()
    return bool(wanted) and normalise(code) == wanted


def words(pct: Optional[float] = None, count: Optional[int] = None) -> str:
    pct = percent() if pct is None else pct
    count = months() if count is None else count
    span = "month" if count == 1 else f"{count} months"
    return f"Founding member: {pct:g}% off your first {span}"


def yearly_words(pct: Optional[float] = None, count: Optional[int] = None) -> str:
    pct = percent() if pct is None else pct
    count = months() if count is None else count
    span = "1 month's" if count == 1 else f"{count} months'"
    return (f"Paying yearly: {pct:g}% off {span} worth of the yearly price, "
            "taken once on the first yearly bill")


def public_offer(now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """What the sign-up page says about the code, or None when it is off."""
    if not is_open(now):
        return None
    last = until()
    return {"words": words(), "yearly_words": yearly_words(),
            "percent": percent(), "months": months(),
            "until": last.isoformat() if last else None,
            "until_words": f"Can be used until {_day(last)}." if last else ""}


# ---- one business ------------------------------------------------------------


def row_for(tenant_id: str) -> Optional[Dict[str, Any]]:
    return STORE._db.get(FOUNDER_KIND, tenant_id)


def has_paid_invoice(tenant_id: str) -> bool:
    """Whether any money has been paid against this business's invoices."""
    return any(inv.state != "void" and (inv.state == "paid" or inv.amount_paid_usd > 0)
               for inv in STORE.invoices_for(tenant_id))


def check(code: str, now: Optional[datetime] = None) -> None:
    """Refuse a founding member code that cannot be used today."""
    if not matches(code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="That is not the founding member code.")
    if not is_open(now):
        last = until()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"The founding member code could be used until {_day(last)}, "
                    "and that date has passed."
                    if last and last != date.min
                    else "The founding member code cannot be used now."))


def apply(tenant: Tenant, code: str, via: str,
          now: Optional[datetime] = None) -> Dict[str, Any]:
    """Give this business the founding member discount. Raises if it cannot.

    `via` is "signup" or "console", for the business's log.
    """
    from retention import record_event

    now = now or utc_now()
    check(code, now)
    with STORE._lock:
        if row_for(tenant.tenant_id) is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="This business has already used a founding member code.")
        if has_paid_invoice(tenant.tenant_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=("A founding member code has to be entered before the "
                        "first bill is paid."))
        sub = STORE.active_subscription(tenant.tenant_id)
        started = sub is not None and sub.started_at is not None
        row: Dict[str, Any] = {
            "tenant_id": tenant.tenant_id,
            "percent": percent(),
            "months": months(),
            "entered_at": iso(now),
            "via": via,
            # From the next invoice this contract issues. Entered before any
            # contract (at sign-up, on a trial), it starts with the first.
            "subscription_id": sub.subscription_id if started and sub else None,
            "from_period": sub.periods_billed if started and sub else 0,
            "used": {},
        }
        STORE._db.put(FOUNDER_KIND, tenant.tenant_id, row)
    record_event("founder", int(row["months"]))
    STORE.record_audit(
        tenant_id=tenant.tenant_id, actor="CyberLogix", actor_role="system",
        action="founder_code.used",
        detail=f"Founding member code entered ({via}): {words(row['percent'], row['months'])}.")
    logger.info("Business %s entered the founding member code (%s).", tenant.tenant_id, via)
    return row


def months_left(row: Dict[str, Any]) -> int:
    used = sum(int(v) for v in (row.get("used") or {}).values())
    return max(0, int(row.get("months") or 0) - used)


def state(tenant: Tenant, now: Optional[datetime] = None) -> Dict[str, Any]:
    """What the Money tab shows about the code. `show` is False when off."""
    row = row_for(tenant.tenant_id)
    if row is not None:
        left = months_left(row)
        pct, count = float(row.get("percent") or 0), int(row.get("months") or 0)
        return {
            "show": True, "taken": True, "can_enter": False,
            "words": words(pct, count), "yearly_words": yearly_words(pct, count),
            "months_left": left,
            "note": (f"{left} of {count} month{'' if count == 1 else 's'} still to come off."
                     if left else "Used in full. Thank you for being a founding member."),
        }
    if not is_open(now):
        return {"show": False, "taken": False, "can_enter": False}
    paid = has_paid_invoice(tenant.tenant_id)
    offer = public_offer(now) or {}
    return {
        "show": True, "taken": False, "can_enter": not paid,
        "words": words(), "yearly_words": yearly_words(),
        "months_left": 0,
        "note": ("A founding member code has to be entered before the first bill is paid."
                 if paid else str(offer.get("until_words") or "")),
    }


# ---- the route ---------------------------------------------------------------


class FounderCode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=40)


@router.post("")
def enter_code(payload: FounderCode, tenant: Tenant = Depends(require_tenant_any_state),
               owner: User = Depends(require_role("owner"))):
    """Enter the founding member code from the Money tab (owner only)."""
    if not configured():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="There is no founding member code just now.")
    row = apply(tenant, payload.code, "console")
    write_audit(tenant, owner, "founder_code.entered",
                f"Entered the founding member code: {words(row['percent'], row['months'])}.")
    return {**state(tenant),
            "message": f"Code accepted. {words(row['percent'], row['months'])}, from your next bill."}
