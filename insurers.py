"""Insurance partners: brokers and insurers who send us businesses.

An insurer or broker pays out when a fridge fails and the stock is lost,
so a business whose fridges are watched around the clock is a better
risk to them. They meet many such businesses. This is how they send them
to us, and what they get for it.

* **Apply.** A broker or insurer fills in a short form at
  /insurance-partners. The application is mailed to the operator
  (CYBERLOGIX_OPERATOR_EMAIL) with the ordinary mail sender, and waits.
* **Approve.** The operator approves it in the operator console (/book,
  Partners). That gives the partner a partner code, a sign-up link and a
  key for their dashboard, mailed to them once.
* **Refer.** A business that signs up with the code gets the same welcome
  as a friend's referral: the longer trial (referrals.give_welcome).
* **Earn.** The partner earns a share of what that business pays us for
  its first months: by default 10% for 12 months from its first payment
  (CYBERLOGIX_INSURANCE_PARTNER_SHARE_PERCENT and _MONTHS). It is recorded
  as an amount owed, invoice by invoice, on money that actually arrived.
* **Get paid.** The operator pays partners by hand and records each
  payout here. Nothing in this file moves money.

What a partner can see, on their dashboard: the businesses that joined
with their code (business name and plan only), what they have earned and
what has been paid. What a partner can never see: anything from inside a
business's account, readings, alerts, units, locations, people or bills.
The partner key works on this module's dashboard route and nowhere else.
The only way a partner sees anything about a business's fridges is a
proof of monitoring report link the business chose to give them
(proofreports.py), and that is open to anybody with the link.

Not the reseller channel (partners.py), whose partners look after
businesses and see their units. These partners do not look after
anything, so they see nothing.
"""

from __future__ import annotations

import calendar
import logging
import os
import re
import secrets
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from auth import require_platform_admin
from domain import mask_email
from ratelimit import Limit
from store import STORE, Invoice, Tenant, _parse, hash_secret, iso, utc_now

logger = logging.getLogger("cyberlogix.insurers")

router = APIRouter(prefix="/api/insurance-partners", tags=["Insurance partners"])

PARTNER_KIND = "insurance_partner"
REFERRAL_KIND = "insurance_referral"
EARNING_KIND = "insurance_earning"
PAYOUT_KIND = "insurance_payout"


def _number(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        value = default
    return min(high, max(low, value))


# The share a partner earns, and for how long. Set when a partner is
# approved and kept with them: changing these later changes the deal for
# partners approved after that, never for one already approved.
DEFAULT_SHARE_PERCENT = _number("CYBERLOGIX_INSURANCE_PARTNER_SHARE_PERCENT", 10.0, 0.0, 50.0)
DEFAULT_SHARE_MONTHS = int(_number("CYBERLOGIX_INSURANCE_PARTNER_SHARE_MONTHS", 12, 1, 60))

# Easy to read aloud: no 0/O or 1/I/L. The same alphabet as a friend's code.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_PREFIX = "PARTNER-"
_CODE_RE = re.compile(r"^PARTNER-[A-Z0-9]{4}-[A-Z0-9]{4}$")
KEY_PREFIX = "clx_ins_"

KINDS = {"broker": "Insurance broker", "insurer": "Insurer"}

# The public form. A real firm applies once; a script gets nowhere.
APPLY_LIMIT = Limit("insurance-apply", per_caller=3, total=30,
                    window_seconds=3600, words="applications")

STATUS_WORDS = {
    "applied": "Waiting for a decision.",
    "approved": "Approved.",
    "declined": "Declined.",
}


# ---- the money ---------------------------------------------------------------


def _cents(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def share_of(paid_usd: float, percent: float) -> float:
    """The partner's share of an amount paid, to the cent, half up."""
    if paid_usd <= 0 or percent <= 0:
        return 0.0
    return _cents(Decimal(str(paid_usd)) * Decimal(str(percent)) / Decimal(100))


def add_months(moment: datetime, months: int) -> datetime:
    """The same day `months` later, or the month's last day if shorter."""
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def _payments(invoice: Invoice) -> List[Dict[str, Any]]:
    return sorted((p for p in invoice.payments if _parse(p.get("at")) is not None),
                  key=lambda p: str(p.get("at")))


def counted_paid(invoice: Invoice, window_end: datetime) -> float:
    """What this invoice was paid before the window closed, at most its total.

    Money paid over the total is nobody's revenue and earns nothing.
    """
    total = Decimal("0")
    for payment in _payments(invoice):
        at = _parse(payment.get("at"))
        if at is not None and at < window_end:
            total += Decimal(str(payment.get("amount_usd") or 0))
    cap = Decimal(str(max(0.0, invoice.total_usd)))
    return _cents(min(total, cap))


# ---- partners ----------------------------------------------------------------


def _partner(partner_id: str) -> Optional[Dict[str, Any]]:
    return STORE._db.get(PARTNER_KIND, (partner_id or "").strip())


def _partner_or_404(partner_id: str) -> Dict[str, Any]:
    row = _partner(partner_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such partner.")
    return row


def _save(row: Dict[str, Any]) -> None:
    STORE._db.put(PARTNER_KIND, str(row["partner_id"]), row)


def all_partners() -> List[Dict[str, Any]]:
    return sorted(STORE._db.all(PARTNER_KIND), key=lambda r: str(r.get("partner_id")))


def normalise(code: Optional[str]) -> str:
    return re.sub(r"\s+", "", code or "").upper()


def is_partner_code(code: Optional[str]) -> bool:
    return normalise(code).startswith(CODE_PREFIX)


def _new_code() -> str:
    pick = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{CODE_PREFIX}{pick[:4]}-{pick[4:]}"


def partner_by_code(code: Optional[str]) -> Optional[Dict[str, Any]]:
    """An approved, active partner whose code this is."""
    wanted = normalise(code)
    if not _CODE_RE.match(wanted):
        return None
    for row in all_partners():
        if row.get("status") == "approved" and row.get("active") and row.get("code") == wanted:
            return row
    return None


def partner_by_key(key: Optional[str]) -> Optional[Dict[str, Any]]:
    key = (key or "").strip()
    if not key.startswith(KEY_PREFIX):
        return None
    digest = hash_secret(key)
    found = None
    for row in all_partners():
        if secrets.compare_digest(str(row.get("key_hash") or ""), digest):
            found = row
    return found


def link_for(code: str) -> Dict[str, str]:
    from mail import PUBLIC_BASE_URL

    path = f"/signup?ref={code}"
    return {"path": path, "url": f"{PUBLIC_BASE_URL}{path}" if PUBLIC_BASE_URL else ""}


def _dashboard_url() -> str:
    from mail import PUBLIC_BASE_URL

    return f"{PUBLIC_BASE_URL}/insurance-partners/dashboard" if PUBLIC_BASE_URL \
        else "/insurance-partners/dashboard"


# ---- at sign-up --------------------------------------------------------------


def check_at_signup(code: str, email: str) -> Dict[str, Any]:
    """The partner a code belongs to, or a refusal a person can act on."""
    partner = partner_by_code(code)
    if partner is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=("That partner code is not one we know. Check it with the "
                    "broker or insurer who gave it to you, or leave it empty "
                    "to start the usual trial."))
    if (email or "").strip().lower() == str(partner.get("contact_email") or "").lower():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("That partner code belongs to you, and a partner cannot "
                    "refer themselves. Leave the code empty to start the "
                    "usual trial."))
    return partner


def record_signup(referred: Tenant, partner: Dict[str, Any]) -> Dict[str, Any]:
    """The new business's welcome, and who sent it."""
    from referrals import give_welcome

    days = give_welcome(referred)
    row: Dict[str, Any] = {
        "tenant_id": referred.tenant_id,
        "partner_id": partner["partner_id"],
        "business_name": referred.company_name,
        "joined_at": iso(utc_now()),
        "trial_days": days,
        "first_paid_at": None,
        "share_ends_at": None,
        "refunded_at": None,
    }
    STORE._db.put(REFERRAL_KIND, referred.tenant_id, row)
    STORE.record_audit(
        tenant_id=referred.tenant_id, actor="CyberLogix", actor_role="system",
        action="partner_code.used",
        detail=(f"Signed up with the partner code of {partner.get('company_name')}. "
                "They see this business's name and plan, never what is inside it."))
    logger.info("Business %s joined with insurance partner %s's code.",
                referred.tenant_id, partner["partner_id"])
    return row


# ---- when money arrives ------------------------------------------------------


def after_settlement(tenant: Tenant, invoice: Invoice) -> Optional[float]:
    """Record the partner's share of what this invoice was paid.

    Called wherever a payment is recorded, after it is recorded. Never
    raises into the payment. Returns the share recorded, or None.
    """
    try:
        return _record_share(tenant, invoice)
    except Exception:  # noqa: BLE001 - a payment must never fail on this
        logger.exception("Partner share failed for %s.", tenant.tenant_id)
        return None


def _record_share(tenant: Tenant, invoice: Invoice) -> Optional[float]:
    if invoice.state == "void" or invoice.amount_paid_usd <= 0:
        return None
    # A late charge is about lateness, not service, and earns nobody.
    if (invoice.source or "").startswith("late_fee:"):
        return None
    with STORE._lock:
        referral = STORE._db.get(REFERRAL_KIND, tenant.tenant_id)
        if referral is None or referral.get("refunded_at"):
            return None
        partner = _partner(str(referral.get("partner_id") or ""))
        if partner is None or partner.get("status") != "approved":
            return None
        # A business a reseller looks after earns that reseller its
        # commission (partners.py). One business, one share.
        if tenant.partner_id:
            return None
        payments = _payments(invoice)
        if not payments:
            return None
        if not referral.get("first_paid_at"):
            first = _parse(payments[0].get("at"))
            assert first is not None
            referral["first_paid_at"] = iso(first)
            referral["share_ends_at"] = iso(add_months(
                first, int(partner.get("share_months") or DEFAULT_SHARE_MONTHS)))
            STORE._db.put(REFERRAL_KIND, tenant.tenant_id, referral)
        window_end = _parse(referral.get("share_ends_at"))
        assert window_end is not None
        paid = counted_paid(invoice, window_end)
        if paid <= 0:
            return None
        percent = float(partner.get("share_percent") or 0.0)
        amount = share_of(paid, percent)
        existing = STORE._db.get(EARNING_KIND, invoice.invoice_id)
        if existing is not None and (existing.get("withdrawn_at") or (
                float(existing.get("paid_usd") or 0.0) == paid)):
            return float(existing.get("amount_usd") or 0.0)
        now = iso(utc_now())
        row: Dict[str, Any] = {
            "earning_id": invoice.invoice_id,
            "partner_id": partner["partner_id"],
            # The account under its own name, not tenant_id: what we owe a
            # partner outlives a business deleting itself (store.py keeps
            # rows that do not name a business as theirs).
            "referred_account": tenant.tenant_id,
            "invoice_id": invoice.invoice_id,
            "invoice_number": invoice.number,
            "paid_usd": paid,
            "percent": percent,
            "amount_usd": amount,
            "earned_at": (existing or {}).get("earned_at") or now,
            "updated_at": now,
            "withdrawn_at": None,
        }
        STORE._db.put(EARNING_KIND, invoice.invoice_id, row)
    logger.info("Partner %s earned $%.2f on %s.", partner["partner_id"], amount,
                invoice.number)
    return amount


def refunded(tenant_id: str) -> Optional[float]:
    """A referred business was refunded: take back what it earned its partner.

    Returns what was withdrawn, or None when the business did not join with
    a partner code. What was already paid out stays paid; the partner's
    balance shows it.
    """
    with STORE._lock:
        referral = STORE._db.get(REFERRAL_KIND, tenant_id)
        if referral is None:
            return None
        withdrawn = Decimal("0")
        now = iso(utc_now())
        for row in STORE._db.all(EARNING_KIND):
            if row.get("referred_account") != tenant_id or row.get("withdrawn_at"):
                continue
            withdrawn += Decimal(str(row.get("amount_usd") or 0))
            row["withdrawn_at"] = now
            STORE._db.put(EARNING_KIND, str(row["earning_id"]), row)
        referral["refunded_at"] = now
        STORE._db.put(REFERRAL_KIND, tenant_id, referral)
    return _cents(withdrawn)


# ---- the ledger --------------------------------------------------------------


def earnings_for(partner_id: str) -> List[Dict[str, Any]]:
    return sorted((r for r in STORE._db.all(EARNING_KIND)
                   if r.get("partner_id") == partner_id),
                  key=lambda r: str(r.get("earned_at") or ""))


def payouts_for(partner_id: str) -> List[Dict[str, Any]]:
    return sorted((r for r in STORE._db.all(PAYOUT_KIND)
                   if r.get("partner_id") == partner_id),
                  key=lambda r: (str(r.get("paid_on") or ""), str(r.get("payout_id"))))


def balance(partner_id: str) -> Dict[str, float]:
    """Earned, paid and still owed, each summed from the rows as stored."""
    earned = sum((Decimal(str(r.get("amount_usd") or 0)) for r in earnings_for(partner_id)
                  if not r.get("withdrawn_at")), Decimal("0"))
    paid = sum((Decimal(str(r.get("amount_usd") or 0)) for r in payouts_for(partner_id)),
               Decimal("0"))
    return {"earned_usd": _cents(earned), "paid_usd": _cents(paid),
            "owed_usd": _cents(earned - paid)}


def referred_by(partner_id: str) -> List[Dict[str, Any]]:
    """The businesses that joined with this partner's code: name and plan only."""
    from domain import PLAN_TIERS

    earned: Dict[str, Decimal] = {}
    for row in earnings_for(partner_id):
        if not row.get("withdrawn_at"):
            account = str(row.get("referred_account"))
            earned[account] = earned.get(account, Decimal("0")) + Decimal(str(row.get("amount_usd") or 0))
    out = []
    for row in STORE._db.all(REFERRAL_KIND):
        if row.get("partner_id") != partner_id:
            continue
        tenant = STORE.get_tenant(str(row.get("tenant_id")))
        plan = PLAN_TIERS.get(tenant.plan, {}).get("name", tenant.plan) if tenant else ""
        out.append({
            "business": tenant.company_name if tenant else str(row.get("business_name") or ""),
            "plan": plan,
            "joined_at": row.get("joined_at"),
            "share_ends_at": row.get("share_ends_at"),
            "earned_usd": _cents(earned.get(str(row.get("tenant_id")), Decimal("0"))),
            "refunded": bool(row.get("refunded_at")),
        })
    return sorted(out, key=lambda r: str(r.get("joined_at") or ""), reverse=True)


def terms(partner: Dict[str, Any]) -> str:
    return (f"{float(partner.get('share_percent') or 0):g}% of what each business "
            f"you refer pays us for its first {int(partner.get('share_months') or 0)} "
            "months, counted from its first payment. Paid by us by hand; "
            "this page shows what we owe you.")


def admin_view(row: Dict[str, Any]) -> Dict[str, Any]:
    pid = str(row["partner_id"])
    out = {k: row.get(k) for k in (
        "partner_id", "status", "active", "kind", "company_name", "contact_name",
        "contact_email", "contact_phone", "website", "message", "applied_at",
        "decided_at", "code", "share_percent", "share_months")}
    out["kind_words"] = KINDS.get(str(row.get("kind")), "")
    out["status_words"] = STATUS_WORDS.get(str(row.get("status")), "")
    if row.get("status") == "approved":
        out["businesses"] = len(referred_by(pid))
        out.update(balance(pid))
        out["payouts"] = payouts_for(pid)
    return out


# ---- routes: the public form -------------------------------------------------


class Application(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: str = Field(..., pattern="^(broker|insurer)$")
    company_name: str = Field(..., min_length=2, max_length=160)
    contact_name: str = Field(..., min_length=2, max_length=120)
    contact_email: EmailStr
    contact_phone: str = Field("", max_length=40)
    website: str = Field("", max_length=200)
    message: str = Field("", max_length=1000)


@router.post("/apply", status_code=status.HTTP_202_ACCEPTED)
def apply(payload: Application, request: Request):
    """Apply to be an insurance partner. Public; rate limited.

    The application waits for the operator. The same answer whether or not
    this address has applied before, so the form says nothing about who
    else has.
    """
    from digest import OPERATOR_EMAIL
    from mail import send as send_mail

    APPLY_LIMIT.check(request)
    email = str(payload.contact_email).strip().lower()
    answer = {"message": ("Thank you. We read every application and will reply "
                          "by email.")}
    for earlier in all_partners():
        if str(earlier.get("contact_email") or "").lower() == email and \
                earlier.get("status") in ("applied", "approved"):
            return answer
    row: Dict[str, Any] = {
        "partner_id": STORE._next_id("IPT"),
        "status": "applied",
        "active": False,
        "kind": payload.kind,
        "company_name": payload.company_name,
        "contact_name": payload.contact_name,
        "contact_email": email,
        "contact_phone": payload.contact_phone,
        "website": payload.website,
        "message": payload.message,
        "applied_at": iso(utc_now()),
        "decided_at": None,
        "code": None,
        "key_hash": None,
        "share_percent": None,
        "share_months": None,
    }
    _save(row)
    logger.info("Insurance partner application %s from %s.", row["partner_id"],
                mask_email(email))
    if OPERATOR_EMAIL:
        send_mail(
            to_address=OPERATOR_EMAIL,
            subject=f"Partner application: {payload.company_name} ({KINDS[payload.kind]})",
            body=(
                f"{payload.company_name} has applied to be an insurance partner.\n\n"
                f"Kind: {KINDS[payload.kind]}\n"
                f"Contact: {payload.contact_name}, {email}"
                + (f", {payload.contact_phone}" if payload.contact_phone else "") + "\n"
                + (f"Website: {payload.website}\n" if payload.website else "")
                + (f"\nWhat they said:\n{payload.message}\n" if payload.message else "")
                + "\nApprove or decline it in the operator console: /book, "
                  "the Partners tab."),
            dedupe_key=f"insurance-application:{row['partner_id']}",
            klass="transactional",
        )
    else:
        logger.warning("No CYBERLOGIX_OPERATOR_EMAIL, so application %s was "
                       "not mailed. It is waiting in the operator console.",
                       row["partner_id"])
    return answer


# ---- routes: the operator ----------------------------------------------------


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    share_percent: Optional[float] = Field(None, ge=0.0, le=50.0)
    share_months: Optional[int] = Field(None, ge=1, le=60)


class PartnerState(BaseModel):
    active: bool


class Payout(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount_usd: float = Field(..., gt=0, le=1_000_000, allow_inf_nan=False)
    paid_on: Optional[date] = None
    reference: str = Field(..., min_length=1, max_length=120)
    note: str = Field("", max_length=300)


@router.get("", dependencies=[Depends(require_platform_admin)])
def list_partners():
    """Every application and partner, and what each is owed."""
    rows = [admin_view(r) for r in all_partners()]
    return {
        "applications": [r for r in rows if r["status"] == "applied"],
        "partners": [r for r in rows if r["status"] == "approved"],
        "declined": [r for r in rows if r["status"] == "declined"],
        "settings": {"share_percent": DEFAULT_SHARE_PERCENT,
                     "share_months": DEFAULT_SHARE_MONTHS},
        "owed_usd": round(sum(float(r.get("owed_usd") or 0) for r in rows), 2),
        "note": ("Partners are paid by hand. Record each payment here once it "
                 "has gone; nothing here moves money."),
    }


def _issue_key(row: Dict[str, Any]) -> str:
    key = f"{KEY_PREFIX}{secrets.token_urlsafe(30)}"
    row["key_hash"] = hash_secret(key)
    return key


def _mail_key(row: Dict[str, Any], key: str, first: bool) -> bool:
    from mail import send as send_mail

    code = str(row.get("code"))
    link = link_for(code)
    result = send_mail(
        to_address=str(row.get("contact_email")),
        subject=("You are a CyberLogix partner" if first
                 else "Your new CyberLogix partner key"),
        body=(
            f"{row.get('contact_name')},\n\n"
            + ("Your application has been approved.\n\n" if first else
               "Here is a new key for your partner dashboard. The old one no "
               "longer works.\n\n")
            + f"Your partner code: {code}\n"
            f"Sign-up link for the businesses you refer: {link['url'] or link['path']}\n\n"
            f"A business that signs up with it gets a longer free trial. You earn "
            f"{terms(row)}\n\n"
            f"Your dashboard: {_dashboard_url()}\n"
            f"Your dashboard key (keep it private): {key}\n\n"
            "The dashboard shows the businesses that joined with your code, by "
            "name and plan, and what you have earned and been paid. It never "
            "shows what is inside a business's account. If a business wants to "
            "show you its fridges are watched, it can send you a proof of "
            "monitoring report link."),
        dedupe_key=f"insurance-key:{row['partner_id']}:{row['key_hash'][:12]}",
        klass="transactional",
        sensitive=True,
    )
    return bool(result.get("sent") or result.get("status") == "queued")


@router.post("/{partner_id}/approve", dependencies=[Depends(require_platform_admin)])
def approve(partner_id: str, payload: Approval):
    """Approve an application: a code, a link and a dashboard key.

    The key is shown here once and mailed to the partner once.
    """
    row = _partner_or_404(partner_id)
    if row.get("status") == "approved":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="That partner is already approved.")
    code = _new_code()
    while any(r.get("code") == code for r in all_partners()):
        code = _new_code()
    row.update({
        "status": "approved",
        "active": True,
        "decided_at": iso(utc_now()),
        "code": code,
        "share_percent": (payload.share_percent if payload.share_percent is not None
                          else DEFAULT_SHARE_PERCENT),
        "share_months": payload.share_months or DEFAULT_SHARE_MONTHS,
    })
    key = _issue_key(row)
    _save(row)
    mailed = _mail_key(row, key, first=True)
    logger.info("Insurance partner %s approved.", row["partner_id"])
    return {**admin_view(row), "key": key, "mailed": mailed,
            "link": link_for(code),
            "warning": ("The key is shown once. It has been mailed to the "
                        "partner as well." if mailed else
                        "The key is shown once and could not be mailed: "
                        "give it to the partner yourself.")}


@router.post("/{partner_id}/decline", dependencies=[Depends(require_platform_admin)])
def decline(partner_id: str):
    """Decline an application, with a short, polite note to them."""
    from mail import send as send_mail

    row = _partner_or_404(partner_id)
    if row.get("status") != "applied":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Only a waiting application can be declined.")
    row.update({"status": "declined", "active": False, "decided_at": iso(utc_now())})
    _save(row)
    send_mail(
        to_address=str(row.get("contact_email")),
        subject="Your CyberLogix partner application",
        body=(f"{row.get('contact_name')},\n\nThank you for applying to be a "
              "CyberLogix partner. We are not able to take it further at the "
              "moment. You are welcome to apply again later."),
        dedupe_key=f"insurance-declined:{row['partner_id']}",
        klass="transactional",
    )
    return admin_view(row)


@router.post("/{partner_id}/status", dependencies=[Depends(require_platform_admin)])
def set_status(partner_id: str, payload: PartnerState):
    """Switch a partner off, or on again. Off: no dashboard and no new
    sign-ups with the code. What they already earned stays recorded."""
    row = _partner_or_404(partner_id)
    if row.get("status") != "approved":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Only an approved partner can be switched on or off.")
    row["active"] = payload.active
    _save(row)
    logger.warning("Insurance partner %s %s.", row["partner_id"],
                   "switched on" if payload.active else "switched off")
    return admin_view(row)


@router.post("/{partner_id}/rotate-key", dependencies=[Depends(require_platform_admin)])
def rotate_key(partner_id: str):
    """A new dashboard key; the old one stops working at once."""
    row = _partner_or_404(partner_id)
    if row.get("status") != "approved":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Only an approved partner has a key.")
    key = _issue_key(row)
    _save(row)
    mailed = _mail_key(row, key, first=False)
    return {**admin_view(row), "key": key, "mailed": mailed}


@router.post("/{partner_id}/payouts", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_platform_admin)])
def record_payout(partner_id: str, payload: Payout):
    """Write down a payment made to a partner, by hand, outside this app."""
    row = _partner_or_404(partner_id)
    if row.get("status") != "approved":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="Only an approved partner can be paid.")
    payout = {
        "payout_id": STORE._next_id("IPP"),
        "partner_id": row["partner_id"],
        "amount_usd": _cents(Decimal(str(payload.amount_usd))),
        "paid_on": (payload.paid_on or utc_now().date()).isoformat(),
        "reference": payload.reference,
        "note": payload.note,
        "recorded_at": iso(utc_now()),
    }
    STORE._db.put(PAYOUT_KIND, str(payout["payout_id"]), payout)
    logger.info("Payout of $%.2f to insurance partner %s recorded.",
                payout["amount_usd"], row["partner_id"])
    return {**admin_view(row), "payout": payout}


# ---- routes: the partner's own dashboard ------------------------------------


def require_insurance_partner(
    x_cyberlogix_insurance_partner: Optional[str] = Header(
        None, alias="X-CyberLogix-Insurance-Partner"),
) -> Dict[str, Any]:
    """The partner making the request, by their dashboard key."""
    row = partner_by_key(x_cyberlogix_insurance_partner)
    if row is None or row.get("status") != "approved":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="That partner key was not accepted.")
    if not row.get("active"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="This partner account is switched off. Please get in touch.")
    return row


@router.get("/me")
def dashboard(partner: Dict[str, Any] = Depends(require_insurance_partner)):
    """The partner's dashboard: who joined (name and plan), earned, paid.

    Nothing from inside any business's account.
    """
    pid = str(partner["partner_id"])
    code = str(partner.get("code"))
    return {
        "partner": {"company_name": partner.get("company_name"),
                    "kind": KINDS.get(str(partner.get("kind")), "")},
        "code": code,
        "link": link_for(code),
        "terms": terms(partner),
        "share_percent": partner.get("share_percent"),
        "share_months": partner.get("share_months"),
        "businesses": referred_by(pid),
        **balance(pid),
        "payouts": [{"paid_on": p.get("paid_on"), "amount_usd": p.get("amount_usd"),
                     "reference": p.get("reference")} for p in payouts_for(pid)],
        "privacy": ("You see the name and plan of each business that joined "
                    "with your code, and nothing from inside its account. A "
                    "business can send you a proof of monitoring report link "
                    "if it wants to show you its fridges are watched."),
    }
