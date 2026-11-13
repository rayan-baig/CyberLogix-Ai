"""Bring a friend: a referral code per business, and what it earns.

Each business has a code (the owner sees it in the console). A new
business that signs up with it gets a longer trial. When that business
pays its first invoice, the business that sent it gets one month free on
one location, as a credit on its next invoice (credits.py).

The rules, each enforced here and each tested:

* **Not yourself.** A code is refused at sign-up for the email or phone
  of the business that owns it, and the reward is refused when the two
  businesses turn out to share a person (one sign-in that can open both:
  the same owner, or the same estate). Adding another business from your
  own sign-in never takes a code at all.
* **Once per business.** The reward's credit is keyed on the business that
  joined, so the same first payment recorded twice, or two payments in
  the same second, can only ever grant one.
* **Only for money that arrived.** Nothing until the first invoice is paid
  in full with real money. A business that cancels before it pays earns
  nothing, and if its payment is refunded the reward is withdrawn (what
  is left of it).
* **A ceiling.** At most REFERRAL_REWARDS_PER_YEAR rewards per referrer in
  any 365 days; the rest are recorded as joined with no reward.
* **Partners are paid by commission, not by this.** A business that a
  reseller partner manages when it pays its first invoice earns its
  partner the partner's commission (partners.py) and earns no referral
  credit for anybody: one business, one reward. A partner-managed
  business can still refer others; its credit lowers what it pays, and so
  what its partner's commission is worked out on, so the two never add up
  on the same money.

The console shows the business that joined by its business name and
nothing else: never the person who signed up.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from accounts import require_role, tenant_of
from auth import require_platform_admin
from store import STORE, Invoice, Tenant, User, iso, utc_now

logger = logging.getLogger("cyberlogix.referrals")

router = APIRouter(prefix="/api/referrals", tags=["Bring a friend"])

CODE_KIND = "referral_code"
KIND = "referral"

# Easy to read aloud and to type from a phone: no 0/O or 1/I/L.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_PREFIX = "FRIEND-"
_CODE_RE = re.compile(r"^FRIEND-[A-Z0-9]{4}-[A-Z0-9]{4}$")

# What the console says for each outcome.
STATUS_WORDS = {
    "joined": "On their trial. You earn when they pay their first invoice.",
    "rewarded": "Paid their first invoice. You earned a month free on one location.",
    "no_reward_partner": "Looked after by one of our partners, who is paid for them instead.",
    "no_reward_same_estate": "Shares a person with your business, so it cannot earn a reward.",
    "no_reward_cancelled": "Cancelled before paying, so nothing was earned.",
    "no_reward_cap": "Joined after this year's rewards were used up.",
    "no_reward_closed": "Your business could not take the reward.",
    "refunded": "Their payment was refunded, so the reward was withdrawn.",
}


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


def normalise(code: Optional[str]) -> str:
    return re.sub(r"\s+", "", (code or "")).upper()


def _new_code() -> str:
    pick = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{CODE_PREFIX}{pick[:4]}-{pick[4:]}"


def code_for(tenant: Tenant) -> str:
    """This business's code, made the first time it is asked for."""
    with STORE._lock:
        for row in STORE._db.all(CODE_KIND):
            if row.get("tenant_id") == tenant.tenant_id:
                return str(row["code"])
        code = _new_code()
        while STORE._db.get(CODE_KIND, code) is not None:
            code = _new_code()
        STORE._db.put(CODE_KIND, code, {
            "code": code,
            "tenant_id": tenant.tenant_id,
            "created_at": iso(utc_now()),
        })
        return code


def referrer_of_code(code: str) -> Optional[Tenant]:
    """The business a code belongs to, if the code is real and it is open."""
    code = normalise(code)
    if not _CODE_RE.match(code):
        return None
    row = STORE._db.get(CODE_KIND, code)
    if row is None:
        return None
    tenant = STORE.get_tenant(row.get("tenant_id") or "")
    if tenant is None or tenant.suspended:
        return None
    return tenant


def link_for(code: str) -> Dict[str, str]:
    from mail import PUBLIC_BASE_URL

    path = f"/signup?ref={code}"
    return {"path": path, "url": f"{PUBLIC_BASE_URL}{path}" if PUBLIC_BASE_URL else ""}


# ---- at sign-up ----------------------------------------------------------


def check_at_signup(code: str, email: str, phone: str) -> Tenant:
    """The referring business, or a refusal a person can act on."""
    from pricing import REFERRAL_TRIAL_DAYS

    referrer = referrer_of_code(code)
    if referrer is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "That referral code is not one we know. Check it with the "
                "business that gave it to you, or leave it empty to start the "
                f"usual trial instead of the {REFERRAL_TRIAL_DAYS}-day one."),
        )
    email = (email or "").strip().lower()
    theirs = {(u.email or "").strip().lower() for u in STORE.users_for(referrer.tenant_id)}
    theirs.add((referrer.contact_email or "").strip().lower())
    phones = {_digits(referrer.contact_phone)}
    phones |= {_digits(c.phone) for c in STORE.contacts_for(referrer.tenant_id)}
    phones.discard("")
    if email in theirs or (_digits(phone) and _digits(phone) in phones):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "That referral code belongs to your own business, and a "
                "business cannot refer itself. Leave the code empty to start "
                "the usual trial."),
        )
    return referrer


def record_signup(referred: Tenant, referrer: Tenant, code: str) -> Dict[str, Any]:
    """Give the new business its longer trial and write down who sent it."""
    from domain import PLAN_TIERS
    from pricing import REFERRAL_TRIAL_DAYS

    usual = PLAN_TIERS[referred.plan]["term_days"]
    if REFERRAL_TRIAL_DAYS > usual:
        STORE.extend_licence(referred.tenant_id, REFERRAL_TRIAL_DAYS - usual)
    row = {
        "tenant_id": referred.tenant_id,
        "referrer_id": referrer.tenant_id,
        "referred_name": referred.company_name,
        "code": normalise(code),
        "joined_at": iso(utc_now()),
        "trial_days": max(REFERRAL_TRIAL_DAYS, usual),
        "status": "joined",
        "decided_at": None,
        "credit_id": None,
        "refunded_at": None,
    }
    STORE._db.put(KIND, referred.tenant_id, row)
    STORE.record_audit(
        tenant_id=referrer.tenant_id, actor="CyberLogix", actor_role="system",
        action="referral.joined",
        detail=f"{referred.company_name} started a trial with your referral code.",
    )
    return row


# ---- when money arrives --------------------------------------------------


def _shares_a_person(a: str, b: str) -> bool:
    """Whether one sign-in can open both businesses."""
    for user in STORE.users_for(a):
        if any(login.tenant_id == b for login in STORE.logins_of(user)):
            return True
    return False


def _cancelled(tenant_id: str) -> bool:
    subs = STORE.subscriptions_for(tenant_id)
    return bool(subs) and STORE.active_subscription(tenant_id) is None and any(
        s.cancelled_at is not None for s in subs)


def rewards_in_year(referrer_id: str) -> int:
    """Rewards granted to this business in the last 365 days."""
    import credits

    since = iso(utc_now() - timedelta(days=365)) or ""
    return sum(1 for row in credits.for_tenant(referrer_id)
               if row.get("source") == "referral" and (row.get("created_at") or "") >= since)


def after_settlement(tenant: Tenant, invoice: Invoice) -> Optional[str]:
    """Decide the reward the first time a referred business pays in full.

    Called wherever a payment is recorded. Never raises into the payment:
    recording money that arrived matters more than a referral credit.
    Returns the referral's new status, or None if nothing was decided.
    """
    try:
        return _decide(tenant, invoice)
    except Exception:  # noqa: BLE001 - a payment must never fail on this
        logger.exception("Referral decision failed for %s.", tenant.tenant_id)
        return None


def _decide(tenant: Tenant, invoice: Invoice) -> Optional[str]:
    import credits
    from pricing import REFERRAL_REWARDS_PER_YEAR, referral_credit_usd

    if invoice.state != "paid" or invoice.amount_paid_usd <= 0:
        return None
    if (invoice.source or "").startswith("late_fee:"):
        return None
    with STORE._lock:
        row = STORE._db.get(KIND, tenant.tenant_id)
        if row is None or row.get("status") != "joined":
            return None
        referrer = STORE.get_tenant(row.get("referrer_id") or "")
        if referrer is None or referrer.suspended:
            outcome = "no_reward_closed"
        elif tenant.partner_id:
            outcome = "no_reward_partner"
        elif _shares_a_person(referrer.tenant_id, tenant.tenant_id):
            outcome = "no_reward_same_estate"
        elif tenant.suspended or _cancelled(tenant.tenant_id):
            outcome = "no_reward_cancelled"
        elif rewards_in_year(referrer.tenant_id) >= REFERRAL_REWARDS_PER_YEAR:
            outcome = "no_reward_cap"
        else:
            outcome = "rewarded"
        if outcome == "rewarded" and referrer is not None:
            credit_id = f"referral:{tenant.tenant_id}"
            granted = credits.grant(
                referrer.tenant_id, credit_id, referral_credit_usd(referrer),
                reason=f"bring a friend, {row.get('referred_name') or 'a business'} joined",
                source="referral",
                referred_tenant_id=tenant.tenant_id,
                referred_name=row.get("referred_name") or "",
            )
            row["credit_id"] = credit_id if granted is not None else row.get("credit_id")
        row["status"] = outcome
        row["decided_at"] = iso(utc_now())
        row["first_invoice"] = invoice.number
        STORE._db.put(KIND, tenant.tenant_id, row)
    if outcome == "rewarded" and referrer is not None:
        STORE.record_audit(
            tenant_id=referrer.tenant_id, actor="CyberLogix", actor_role="system",
            action="referral.rewarded",
            detail=(f"{row.get('referred_name')} paid its first invoice: a month "
                    "free on one location comes off your next invoice."),
        )
    logger.info("Referral for %s decided: %s.", tenant.tenant_id, outcome)
    return outcome


def refunded(tenant_id: str) -> Dict[str, Any]:
    """The referred business's payment was refunded: no reward stands."""
    import credits

    with STORE._lock:
        row = STORE._db.get(KIND, tenant_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="That business did not join with a referral code.")
        withdrawn = 0.0
        if row.get("credit_id"):
            credit = credits.withdraw(row["credit_id"], "the referred business was refunded")
            withdrawn = float((credit or {}).get("withdrawn_usd") or 0.0)
        row["status"] = "refunded"
        row["refunded_at"] = iso(utc_now())
        STORE._db.put(KIND, tenant_id, row)
    return {"status": "refunded", "withdrawn_usd": round(withdrawn, 2)}


# ---- what the console shows ----------------------------------------------


def joined_with(referrer_id: str) -> List[Dict[str, Any]]:
    rows = [r for r in STORE._db.all(KIND) if r.get("referrer_id") == referrer_id]
    return sorted(rows, key=lambda r: r.get("joined_at") or "", reverse=True)


def overview(tenant: Tenant) -> Dict[str, Any]:
    import credits
    from pricing import (
        REFERRAL_REWARDS_PER_YEAR,
        REFERRAL_TRIAL_DAYS,
        referral_credit_usd,
    )

    code = code_for(tenant)
    earned = [credits.public(r) for r in credits.for_tenant(tenant.tenant_id)
              if r.get("source") == "referral"]
    used = rewards_in_year(tenant.tenant_id)
    return {
        "code": code,
        "link": link_for(code),
        "trial_days": REFERRAL_TRIAL_DAYS,
        "reward": "One month free on one location, taken off your next invoice.",
        "reward_usd": referral_credit_usd(tenant),
        "rewards_this_year": used,
        "rewards_per_year": REFERRAL_REWARDS_PER_YEAR,
        "rewards_left": max(0, REFERRAL_REWARDS_PER_YEAR - used),
        # The business name only, never who signed up for it.
        "joined": [
            {"business": r.get("referred_name") or "",
             "joined_at": r.get("joined_at"),
             "status": r.get("status"),
             "status_words": STATUS_WORDS.get(r.get("status") or "", "")}
            for r in joined_with(tenant.tenant_id)
        ],
        "earned": earned,
        "earned_usd": round(sum(float(c["amount_usd"]) for c in earned
                                if not c["withdrawn"]), 2),
        "credit_waiting_usd": credits.balance(tenant.tenant_id),
        "rules": [
            f"The business you refer gets a {REFERRAL_TRIAL_DAYS}-day trial.",
            "You earn when it pays its first invoice, once per business.",
            f"Up to {REFERRAL_REWARDS_PER_YEAR} rewards a year.",
            "Not for your own business or one that shares a person with it.",
            "A business looked after by one of our partners earns its partner "
            "a commission instead.",
        ],
    }


def earned_between(tenant_id: str, start: str, end: str) -> List[Dict[str, Any]]:
    """Referral credits granted to a business in [start, end), for reports."""
    import credits

    return [credits.public(r) for r in credits.for_tenant(tenant_id)
            if r.get("source") == "referral"
            and start <= (r.get("created_at") or "") < end]


@router.get("/me")
def my_referrals(owner: User = Depends(require_role("owner"))):
    """Your referral code and link, who joined with it, and what you earned.

    Owners only: it is about what the business is billed.
    """
    return overview(tenant_of(owner))


class Refund(BaseModel):
    note: str = Field("", max_length=200)


@router.post("/{tenant_id}/refunded")
def mark_refunded(tenant_id: str, payload: Refund,
                  _: None = Depends(require_platform_admin)):
    """Record that a referred business's payment was given back.

    Platform operator only, beside recording the refund itself. What is
    left of the referrer's reward is withdrawn; what was already taken off
    an invoice stays taken.
    """
    result = refunded(tenant_id.strip())
    logger.info("Referral for %s marked refunded (%s).", tenant_id, payload.note[:80])
    return result
