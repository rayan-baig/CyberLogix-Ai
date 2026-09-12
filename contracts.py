"""Signed contracts, automatic billing, and collections.

The gap this closes is the most expensive one in the product.

Everything before this could *quote* beautifully: a three-year term, a
five percent annual escalator, a $1,500 commissioning fee, ten percent off
for paying a year up front. `GET /api/billing/deal` would price all of it
to the cent. And then nothing carried any of it into an invoice. Billing
was a button somebody had to remember to press, at the month-one rate,
forever. On a $48,000-a-year estate signed to a three-year escalating
term, the difference between the deal as quoted and the deal as billed is
$7,320 over the term — and that is before counting the months nobody
remembered to bill at all.

Three things live here:

**The contract.** A `Subscription` is the countersigned deal made durable:
term, escalator, prepay, attached add-ons, and a count of periods already
invoiced.

**The billing run.** Issues what is due and nothing else. The claim on a
period is atomic, so a scheduler tick landing on a manual catch-up, or a
restart mid-pass, cannot bill the same month twice. Billing in advance,
which is what the market does, and what makes non-payment visible before
the service is delivered rather than after.

**Collections.** An invoice nobody chases is a donation. Reminders are
staged, a late fee is issued once as its own numbered document, and a
long-overdue account is marked delinquent.

What delinquency does *not* do is switch off monitoring. A freezer full of
embryos does not stop being somebody's freezer full of embryos because
their finance department is slow, and the company that let a $40,000 loss
happen over a $1,299 invoice would deserve everything that came next.
Alerting, escalation and telemetry run for a delinquent account exactly as
they run for a paid one. What is withheld is the reporting: benchmarks,
attestations, exports — the things whose absence costs money and nothing
else.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from accounts import require_role
from auth import require_tenant, require_tenant_any_state, write_audit
from invoicing import PAYMENT_TERMS_DAYS, build_lines
from store import (
    INDUSTRY_PROFILES,
    MAX_CATCHUP_PERIODS,
    MAX_TERM_YEARS,
    STORE,
    Invoice,
    Subscription,
    Tenant,
    User,
    iso,
    utc_now,
)

logger = logging.getLogger("cyberlogix.contracts")

router = APIRouter(prefix="/api/contracts", tags=["Contracts & Collections"])


# ---- collections policy ------------------------------------------------
#
# Days past due at which each step fires. Chosen to be ordinary rather than
# aggressive: a finance department that is merely slow should never meet
# the late fee, and one that is not paying should meet it on a schedule
# they could have predicted from the contract.
REMINDER_DAYS = (1, 7, 14, 30, 45)

# 1.5% a month is the standard commercial late charge and the ceiling in
# most US states. Applied once, to the balance outstanding at the moment
# it is applied, as a separate numbered invoice — never as an edit to the
# original, because an issued invoice whose total moves is a dispute.
LATE_FEE_MONTHLY_PERCENT = 1.5
LATE_FEE_DAYS = 14
LATE_FEE_MINIMUM_USD = 25.0

# Past this, the account is delinquent. Reporting is withheld; alerting is
# not, and never will be.
DELINQUENT_AFTER_DAYS = 45

REMINDER_TONE = {
    1: (
        "A reminder that invoice {number} for ${balance:,.2f} fell due on "
        "{due}. If it is already in your payment run, please ignore this."
    ),
    2: (
        "Invoice {number} for ${balance:,.2f} is now a week past due. "
        "Could you confirm it has been received and scheduled?"
    ),
    3: (
        "Invoice {number} for ${balance:,.2f} is two weeks past due. A late "
        "charge of {fee_percent}% per month applies from today under the "
        "terms of the agreement."
    ),
    4: (
        "Invoice {number} for ${balance:,.2f} is a month past due. Please "
        "treat this as a formal notice: at {delinquent} days, reporting and "
        "certification features are withheld until the balance is cleared. "
        "Monitoring and alerting are unaffected and will continue."
    ),
    5: (
        "Invoice {number} for ${balance:,.2f} is {days} days past due and "
        "the account is now marked delinquent. Reporting and certification "
        "are withheld. Monitoring and alerting continue unchanged. Please "
        "contact us to arrange settlement."
    ),
}


class SignRequest(BaseModel):
    term_years: int = Field(1, ge=1, le=MAX_TERM_YEARS)
    escalator_percent: float = Field(5.0, ge=0.0, le=25.0)
    annual_prepay: bool = False
    add_ons: List[str] = Field(default_factory=list)
    purchase_order: Optional[str] = Field(None, max_length=64)
    auto_renew: bool = True


class RenewRequest(BaseModel):
    term_years: int = Field(1, ge=1, le=MAX_TERM_YEARS)


class CancelRequest(BaseModel):
    reason: str = Field("", max_length=280)


# ---- billing -----------------------------------------------------------


def arrears_lines(
    tenant: Tenant, sub: Subscription, index: int
) -> List[Dict[str, Any]]:
    """Charge for units that appeared *inside* the period just gone.

    Billing in advance has a hole in it, and it is the largest one in the
    system. The invoice for a period is priced on the day the period
    opens. A customer who signs for five racks and rolls out twenty more
    the following week is monitored on twenty-five and billed for five,
    for the rest of the month — measured, $17,980 of service delivered and
    never charged, from one ordinary rollout.

    So each invoice also carries the part-period owed for anything that
    turned up during the previous one, priced from the day it was
    registered. Nobody is charged for days before their sensor existed,
    and nobody gets a month free for adding it on the 2nd.
    """
    if index <= 0 or sub.started_at is None:
        return []

    from pricing import PRICE_BOOK, plural

    window_open = sub.period_start(index - 1)
    window_close = sub.period_start(index)
    span = (window_close - window_open).total_seconds()
    if span <= 0:
        return []

    # A sensor registered before the window opened was on the invoice that
    # opened it; one registered after it closed belongs to this period,
    # which this invoice already charges in full.
    late: Dict[str, List[float]] = {}
    for sensor in STORE.sensors_for(tenant.tenant_id):
        joined = sensor.registered_at
        if joined is None or joined <= window_open or joined >= window_close:
            continue
        unbilled = (window_close - joined).total_seconds() / span
        # A sensor registered in the last moments of the period owes a
        # fraction of a cent. Counting it inflates the line's unit count
        # against an amount it did not contribute to, which reads on the
        # invoice as an overcharge and invites the dispute.
        if unbilled <= 0:
            continue
        late.setdefault(sensor.industry_vertical, []).append(unbilled)

    multiplier = sub.rate_multiplier(index - 1)
    lines: List[Dict[str, Any]] = []
    for vertical, fractions in sorted(late.items()):
        if vertical not in PRICE_BOOK:
            continue
        rate = PRICE_BOOK[vertical]["monthly_usd"] * multiplier
        # Priced at the *previous* period's rate, because that is the
        # period being caught up on. Using this period's rate would apply
        # an escalator to days before it took effect.
        # Only the ones that actually owe something appear in the count.
        billable = [
            f for f in fractions
            if round(rate * f * sub.months_per_period, 2) >= 0.01
        ]
        amount = round(rate * sum(billable) * sub.months_per_period, 2)
        if amount <= 0:
            continue
        count = len(billable)
        lines.append(
            {
                "kind": "arrears",
                "description": (
                    f"{INDUSTRY_PROFILES[vertical]['name']} — {count} "
                    f"{plural(vertical, count)} added mid-period, "
                    f"part period to {window_close.date().isoformat()}"
                ),
                "quantity": count,
                "unit_price_usd": round(rate, 2),
                "amount_usd": amount,
            }
        )
    return lines


def bill_period(
    tenant: Tenant, sub: Subscription, index: int
) -> Optional[Invoice]:
    """Issue the invoice for one period, or None if there is nothing to bill.

    The period is claimed before the invoice is built, so two runs racing
    cannot both decide the period is due. If building or issuing then
    fails, the claim is handed back — a claim held over a failure is a
    month that is never billed at all, which is the same bug pointing the
    other way.
    """
    if not STORE.claim_billing_period(sub, index):
        return None

    try:
        multiplier = sub.rate_multiplier(index)
        months = sub.months_per_period
        # Not on a re-bill of period 0: the commissioning fee was already
        # charged on the invoice that was voided, or it was not, and
        # `setup_billed` is the record of which.
        include_setup = index == 0 and not sub.setup_billed
        lines = build_lines(
            tenant,
            list(sub.add_ons),
            include_setup,
            rate_multiplier=multiplier,
            months=months,
        )
        lines += arrears_lines(tenant, sub, index)

        if not lines:
            # An estate with nothing registered yet. Not an error, and not
            # a period to burn: hand it back so it bills once there is
            # something to bill for.
            STORE.release_billing_period(sub, index)
            return None

        from pricing import ANNUAL_PREPAY_DISCOUNT_PERCENT

        if sub.annual_prepay:
            recurring = round(
                sum(
                    line["amount_usd"]
                    for line in lines
                    if line["kind"] in ("subscription", "add_on")
                ),
                2,
            )
            discount = round(
                recurring * ANNUAL_PREPAY_DISCOUNT_PERCENT / 100.0, 2
            )
            if discount:
                lines.append(
                    {
                        "kind": "discount",
                        "description": (
                            f"Annual prepayment discount "
                            f"({ANNUAL_PREPAY_DISCOUNT_PERCENT:g}%)"
                        ),
                        "quantity": 1,
                        "unit_price_usd": -discount,
                        "amount_usd": -discount,
                    }
                )

        invoice = STORE.create_invoice(
            tenant=tenant,
            lines=lines,
            period_days=30 * months,
            terms_days=PAYMENT_TERMS_DAYS,
            purchase_order=sub.purchase_order,
            source=sub.subscription_id,
            billing_period=index,
        )
    except Exception:
        STORE.release_billing_period(sub, index)
        raise

    # Re-issued, so it is no longer a hole. Done after the invoice exists:
    # clearing it first and then failing would lose the period for good,
    # which is the bug this list was added to fix.
    STORE.clear_rebill(sub, index)

    if include_setup:
        STORE.mark_setup_billed(sub)

    logger.info(
        "Billed %s period %d (year %d, x%.4f): %s for $%.2f",
        sub.subscription_id,
        index,
        sub.contract_year(index),
        sub.rate_multiplier(index),
        invoice.number,
        invoice.total_usd,
    )
    return invoice


def run_billing(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Issue every invoice that has come due, across every tenant.

    One bad estate must not stop the rest from being billed: this is the
    function that decides whether the company gets paid this month, and it
    runs unattended.
    """
    now = now or utc_now()
    issued: List[str] = []
    total = 0.0
    failures: List[str] = []
    capped: List[str] = []

    for sub in STORE.all_subscriptions():
        if not sub.active:
            continue
        tenant = STORE.get_tenant(sub.tenant_id)
        if tenant is None:
            continue
        try:
            due = sub.due_periods(now)
            if len(due) >= MAX_CATCHUP_PERIODS:
                # Either the clock moved or the contract was backdated.
                # Bill the ceiling and shout, rather than sending a real
                # customer a year of invoices in one morning.
                capped.append(sub.subscription_id)
                logger.error(
                    "Subscription %s is %d+ periods behind; billing the "
                    "first %d only. Check the contract start date.",
                    sub.subscription_id,
                    len(due),
                    MAX_CATCHUP_PERIODS,
                )
            for index in due:
                invoice = bill_period(tenant, sub, index)
                if invoice is not None:
                    issued.append(invoice.number)
                    total += invoice.total_usd
        except Exception as exc:  # noqa: BLE001 - one estate must not stop the rest
            logger.exception(
                "Billing failed for subscription %s (%s).", sub.subscription_id, exc
            )
            failures.append(sub.subscription_id)

    return {
        "invoices_issued": len(issued),
        "numbers": issued,
        "billed_usd": round(total, 2),
        "failed_subscriptions": failures,
        "catchup_capped": capped,
    }


# ---- collections -------------------------------------------------------


def _stage_for(days: int) -> int:
    """The highest reminder stage this invoice has earned."""
    stage = 0
    for step, threshold in enumerate(REMINDER_DAYS, start=1):
        if days >= threshold:
            stage = step
    return stage


def issue_late_fee(tenant: Tenant, invoice: Invoice) -> Optional[Invoice]:
    """A late charge, as its own numbered invoice, once."""
    fee = round(invoice.balance_usd * LATE_FEE_MONTHLY_PERCENT / 100.0, 2)
    fee = max(fee, LATE_FEE_MINIMUM_USD)
    placeholder = STORE.create_invoice(
        tenant=tenant,
        lines=[
            {
                "kind": "late_fee",
                "description": (
                    f"Late charge on {invoice.number} "
                    f"({LATE_FEE_MONTHLY_PERCENT:g}% per month on "
                    f"${invoice.balance_usd:,.2f})"
                ),
                "quantity": 1,
                "unit_price_usd": fee,
                "amount_usd": fee,
            }
        ],
        period_days=30,
        terms_days=PAYMENT_TERMS_DAYS,
        purchase_order=invoice.purchase_order,
        source=f"late_fee:{invoice.invoice_id}",
    )
    if not STORE.claim_late_fee(invoice, placeholder.invoice_id):
        # Somebody else got there first. Void ours rather than leaving two
        # late charges against one invoice — the number is spent either
        # way, which is exactly what a gapless sequence requires.
        STORE.void_invoice(placeholder)
        return None
    return placeholder


def run_dunning(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Chase everything overdue, and issue the late fees that have fallen due.

    Returns the queue of notices to send. There is no mail transport in
    this system yet, so the notices are recorded, logged and handed back
    rather than delivered — the stage is claimed either way, so wiring a
    sender in later cannot replay a month of chases at a customer.
    """
    now = now or utc_now()
    notices: List[Dict[str, Any]] = []
    fees: List[str] = []
    failures: List[str] = []

    for invoice in STORE.open_invoices():
        try:
            days = invoice.days_overdue(now)
            if days < REMINDER_DAYS[0]:
                continue
            tenant = STORE.get_tenant(invoice.tenant_id)
            if tenant is None:
                continue

            if days >= LATE_FEE_DAYS and invoice.late_fee_invoice_id is None:
                fee_invoice = issue_late_fee(tenant, invoice)
                if fee_invoice is not None:
                    fees.append(fee_invoice.number)

            # The stage is a function of how late the invoice is, not a
            # counter that gets walked. A customer ninety days late who has
            # never been chased gets the notice that fits ninety days —
            # one of them — rather than four milder ones fired in the same
            # afternoon to catch up on a schedule nobody ran.
            stage = _stage_for(days)
            if stage <= invoice.reminders_sent:
                continue
            if not STORE.claim_reminder(invoice, stage):
                continue

            notices.append(
                {
                    "invoice_id": invoice.invoice_id,
                    "number": invoice.number,
                    "tenant_id": tenant.tenant_id,
                    "company_name": tenant.company_name,
                    "to": tenant.contact_email,
                    "stage": stage,
                    "days_overdue": days,
                    "balance_usd": invoice.balance_usd,
                    "message": REMINDER_TONE[stage].format(
                        number=invoice.number,
                        balance=invoice.balance_usd,
                        due=iso(invoice.due_at),
                        days=days,
                        fee_percent=f"{LATE_FEE_MONTHLY_PERCENT:g}",
                        delinquent=DELINQUENT_AFTER_DAYS,
                    ),
                }
            )
            logger.warning(
                "Collections stage %d: %s, %s, $%.2f, %d days overdue.",
                stage,
                tenant.company_name,
                invoice.number,
                invoice.balance_usd,
                days,
            )
        except Exception as exc:  # noqa: BLE001 - one account must not stop the rest
            logger.exception(
                "Dunning failed for invoice %s (%s).", invoice.invoice_id, exc
            )
            failures.append(invoice.invoice_id)

    return {
        "notices": notices,
        "notices_count": len(notices),
        "late_fees_issued": fees,
        "failed_invoices": failures,
        "transport": (
            "No mail transport is configured. Notices are recorded and "
            "returned for sending; each stage is claimed once, so nothing "
            "is chased twice when one is wired in."
        ),
    }


def delinquency(tenant_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """How far behind this account is, and what that withholds.

    Two ways to be behind, and they are withheld the same way: an invoice
    nobody paid, and a licence that ran out. Treating only the first would
    have meant a lapsed trial kept its benchmarks and attestations for
    free during the grace period.
    """
    from auth import grace_days_left, licence_state

    now = now or utc_now()
    tenant = STORE.get_tenant(tenant_id)
    open_invoices = STORE.open_invoices(tenant_id)
    overdue = [i for i in open_invoices if i.overdue(now)]
    worst = max((i.days_overdue(now) for i in overdue), default=0)

    state = licence_state(tenant, now) if tenant is not None else "active"
    lapsed = state in ("grace", "lapsed")
    behind = worst >= DELINQUENT_AFTER_DAYS or lapsed

    return {
        "outstanding_usd": round(sum(i.balance_usd for i in open_invoices), 2),
        "overdue_usd": round(sum(i.balance_usd for i in overdue), 2),
        "overdue_count": len(overdue),
        "days_overdue": worst,
        "licence_state": state,
        "grace_days_left": (
            grace_days_left(tenant, now) if tenant is not None else 0
        ),
        "delinquent": behind,
        "withheld": ["benchmarks", "vault_attestation"] if behind else [],
        "never_withheld": [
            "telemetry ingest",
            "breach detection",
            "SMS alerting",
            "voice escalation",
            "incident acknowledgement",
        ],
    }


def require_current(feature: str):
    """Refuse a reporting feature to a delinquent account.

    Deliberately narrow. This guards documents, not safety: it is on the
    benchmark and attestation routes and it will never be on the ingest,
    alert or escalation path. A monitoring company that stops monitoring
    over an unpaid invoice has sold the wrong product.
    """

    def dependency(tenant: Tenant = Depends(require_tenant)) -> Tenant:
        state = delinquency(tenant.tenant_id)
        if not state["delinquent"]:
            return tenant
        if state["licence_state"] in ("grace", "lapsed"):
            because = (
                f"the licence expired on {tenant.expires_at.date()}"
                if tenant.expires_at
                else "the licence has lapsed"
            )
        else:
            because = (
                f"${state['overdue_usd']:,.2f} is "
                f"{state['days_overdue']} days past due"
            )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"{feature} is withheld while {because}. Monitoring, alerting "
                "and escalation are unaffected and still running."
            ),
        )

    return dependency


# ---- routes ------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
def sign(
    payload: SignRequest,
    tenant: Tenant = Depends(require_tenant_any_state),
    operator: User = Depends(require_role("owner")),
):
    """Countersign a contract. From here the estate bills itself."""
    from pricing import ADD_ONS

    unknown = [k for k in payload.add_ons if k not in ADD_ONS]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown add-on(s) {unknown}. Allowed: {list(ADD_ONS)}",
        )
    if tenant.plan == "trial":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A trial estate is priced but not charged. Move the licence "
                "onto a paid plan before countersigning, or the contract "
                "would start issuing invoices against an evaluation."
            ),
        )
    if STORE.active_subscription(tenant.tenant_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This estate already has a signed contract. Cancel or renew "
                "it rather than signing a second one — two live contracts "
                "would bill the same estate twice."
            ),
        )

    sub = STORE.create_subscription(
        tenant=tenant,
        term_years=payload.term_years,
        escalator_percent=payload.escalator_percent,
        annual_prepay=payload.annual_prepay,
        add_ons=payload.add_ons,
        purchase_order=payload.purchase_order,
        auto_renew=payload.auto_renew,
        signed_by=operator.full_name or operator.email,
    )
    write_audit(
        tenant,
        operator,
        "contract.signed",
        f"{sub.term_years}-year term, {sub.escalator_percent:g}% escalator, "
        f"{'annual prepay' if sub.annual_prepay else 'monthly'}.",
    )
    return {
        "message": "Contract signed. Invoices now issue themselves.",
        "contract": sub.public(),
        "schedule": term_schedule(tenant, sub),
    }


@router.get("")
def read(tenant: Tenant = Depends(require_tenant_any_state)):
    """The live contract, its schedule, and everything signed before it."""
    sub = STORE.active_subscription(tenant.tenant_id)
    return {
        "contract": sub.public() if sub else None,
        "schedule": term_schedule(tenant, sub) if sub else [],
        "history": [s.public() for s in STORE.subscriptions_for(tenant.tenant_id)],
        "collections": delinquency(tenant.tenant_id),
        "note": (
            "Without a signed contract nothing bills automatically and "
            "invoices must be raised by hand."
            if sub is None
            else None
        ),
    }


def term_schedule(tenant: Tenant, sub: Subscription) -> List[Dict[str, Any]]:
    """Year-by-year contract value at the rates this contract will bill."""
    from pricing import ANNUAL_PREPAY_DISCOUNT_PERCENT, build_subscription

    from pricing import ADD_ONS, add_on_price

    priced = build_subscription(tenant)
    base = priced["monthly_total_usd"]
    units = priced["units_total"]
    base += sum(add_on_price(k, units) for k in sub.add_ons if k in ADD_ONS)

    rows = []
    for year in range(1, sub.term_years + 1):
        index = (year - 1) * (12 // sub.months_per_period)
        multiplier = sub.rate_multiplier(index)
        annual = round(base * multiplier * 12, 2)
        discount = (
            round(annual * ANNUAL_PREPAY_DISCOUNT_PERCENT / 100.0, 2)
            if sub.annual_prepay
            else 0.0
        )
        rows.append(
            {
                "year": year,
                "monthly_usd": round(base * multiplier, 2),
                "annual_usd": annual,
                "prepay_discount_usd": discount,
                "payable_usd": round(annual - discount, 2),
            }
        )
    return rows


@router.post("/renew")
def renew(
    payload: RenewRequest,
    tenant: Tenant = Depends(require_tenant_any_state),
    operator: User = Depends(require_role("owner")),
):
    """Start a fresh term from where the last one finished."""
    sub = STORE.active_subscription(tenant.tenant_id)
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="There is no live contract to renew.",
        )
    fresh = STORE.renew_subscription(sub, payload.term_years)
    write_audit(
        tenant,
        operator,
        "contract.renewed",
        f"{payload.term_years}-year term at "
        f"x{fresh.carried_multiplier:g} of the rate card.",
    )
    return {
        "message": f"Renewed for {payload.term_years} year(s).",
        "contract": fresh.public(),
        "schedule": term_schedule(tenant, fresh),
    }


@router.post("/cancel")
def cancel(
    payload: CancelRequest,
    tenant: Tenant = Depends(require_tenant_any_state),
    operator: User = Depends(require_role("owner")),
):
    """End the contract. Invoices already issued stay owed."""
    sub = STORE.active_subscription(tenant.tenant_id)
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="There is no live contract to cancel.",
        )
    STORE.cancel_subscription(sub, payload.reason or "cancelled by customer")
    write_audit(tenant, operator, "contract.cancelled", payload.reason or "")
    outstanding = round(
        sum(i.balance_usd for i in STORE.open_invoices(tenant.tenant_id)), 2
    )
    return {
        "message": "Contract cancelled. Automatic billing has stopped.",
        "contract": sub.public(),
        "still_owed_usd": outstanding,
        "note": (
            "Cancelling stops future invoices. Invoices already issued "
            "remain payable."
        ),
    }


@router.post("/billing-run")
def billing_run(
    tenant: Tenant = Depends(require_tenant_any_state),
    operator: User = Depends(require_role("owner")),
):
    """Bill this estate's outstanding periods now.

    The scheduler does this unattended; this is the same code, on demand,
    for a catch-up or a month-end close. Running it twice issues nothing
    the second time.
    """
    sub = STORE.active_subscription(tenant.tenant_id)
    if sub is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="There is no signed contract for this estate to bill.",
        )
    issued = []
    for index in sub.due_periods():
        invoice = bill_period(tenant, sub, index)
        if invoice is not None:
            issued.append(invoice.public())
    if issued:
        write_audit(
            tenant,
            operator,
            "contract.billed",
            f"{len(issued)} invoice(s): "
            + ", ".join(i["number"] for i in issued),
        )
    return {
        "invoices_issued": len(issued),
        "invoices": issued,
        "next_billing_at": iso(sub.period_start(sub.periods_billed)),
    }


@router.get("/pipeline")
def pipeline(tenant: Tenant = Depends(require_tenant)):
    """Revenue this estate could produce that it currently does not.

    Built only from what the system actually knows: add-ons on the rate
    card the contract does not carry, the escalator step already signed
    for, the value of the term at renewal, and sites carrying no sensor.
    Nothing here is a guess about a customer's budget.
    """
    from pricing import ADD_ONS, add_on_price, build_subscription

    priced = build_subscription(tenant)
    units = priced["units_total"]
    sub = STORE.active_subscription(tenant.tenant_id)
    attached = set(sub.add_ons) if sub else set()

    opportunities = []
    for key, entry in ADD_ONS.items():
        if key in attached:
            continue
        monthly = add_on_price(key, units) if units else entry["monthly_usd"]
        opportunities.append(
            {
                "kind": "add_on",
                "key": key,
                "name": entry["name"],
                "monthly_usd": monthly,
                "annual_usd": round(monthly * 12, 2),
                "why": entry["description"],
            }
        )

    if sub is not None:
        index = sub.periods_billed
        now_mult = sub.rate_multiplier(max(0, index - 1))
        next_year_index = sub.contract_year(max(0, index - 1)) * (
            12 // sub.months_per_period
        )
        next_mult = sub.rate_multiplier(next_year_index)
        if next_mult > now_mult:
            base = priced["monthly_total_usd"]
            opportunities.append(
                {
                    "kind": "escalator",
                    "key": "escalator",
                    "name": f"Year {sub.contract_year(next_year_index)} escalator",
                    "monthly_usd": round(base * (next_mult - now_mult), 2),
                    "annual_usd": round(base * (next_mult - now_mult) * 12, 2),
                    "why": (
                        f"Already signed: {sub.escalator_percent:g}% applies "
                        f"from {iso(sub.period_start(next_year_index))}."
                    ),
                }
            )

    empty_sites = [
        site
        for site in STORE.sites_for(tenant.tenant_id)
        if not STORE.sensors_at_site(site.site_id)
    ]
    for site in empty_sites:
        opportunities.append(
            {
                "kind": "coverage_gap",
                "key": site.site_id,
                "name": f"{site.name} has no sensor",
                "monthly_usd": None,
                "annual_usd": None,
                "why": (
                    "A site on the account with nothing monitoring it. Not "
                    "priced here because the unit count is unknown."
                ),
            }
        )

    quantified = [o for o in opportunities if o["annual_usd"]]
    return {
        "current_mrr_usd": priced["monthly_total_usd"],
        "current_arr_usd": round(priced["monthly_total_usd"] * 12, 2),
        "identified_annual_usd": round(
            sum(o["annual_usd"] for o in quantified), 2
        ),
        "opportunities": sorted(
            opportunities, key=lambda o: -(o["annual_usd"] or 0)
        ),
        "renewal": (
            {
                "ends_at": iso(sub.ends_at),
                "days_away": sub.days_to_renewal(),
                "auto_renew": sub.auto_renew,
                "at_risk_annual_usd": round(
                    priced["monthly_total_usd"]
                    * sub.rate_multiplier(sub.periods_billed)
                    * 12,
                    2,
                ),
            }
            if sub
            else None
        ),
        "collections": delinquency(tenant.tenant_id),
    }
