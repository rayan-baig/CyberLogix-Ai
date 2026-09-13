"""What a dollar of invoice is actually worth, and where the rest went.

"Cut costs" is easy to say and hard to aim, because the costs that show
up in a bill are rarely the expensive ones. Run this module's numbers
against the rate card and the ranking is not the one anybody guesses:

    Annual prepay discount (10%)    $1,198.80    10.00% of revenue
    Bad debt (3%)                   $  359.64     3.00%
    Card fees (2.9% + 30c)          $  351.25     2.93%
    Metered usage (AI, SMS, voice)  $    5.60     0.05%

Per restaurant location, per year. The discount we *choose* to give away
costs more than every real cost combined, and it does not appear in any
statement — nobody invoices you for revenue you never charged. The
metered usage everyone worries about, and which this codebase has a
whole spend-control module for, is a rounding error at 0.05%.

Two things this deliberately does not do.

It does not change anybody's prices. The prepay discount is a commercial
decision with a revenue consequence — a smaller discount is only a
saving if the customer still prepays — so it is a setting with the
current value as its default, and the arithmetic is published next to it
rather than acted on.

It does not model the largest line of all. Tax is bigger than every
operating cost put together and is not something to be clever about;
that is a conversation with an accountant, not a constant in a file.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from auth import require_platform_admin
from store import utc_now

router = APIRouter(prefix="/api/margin", tags=["Margin"])


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# What the card processor takes. Set both to zero for a deployment that
# collects only by bank transfer, which is how contracts this size are
# usually settled anyway.
CARD_PERCENT = _env_float("CYBERLOGIX_CARD_FEE_PERCENT", 2.9)
CARD_FIXED_USD = _env_float("CYBERLOGIX_CARD_FEE_FIXED_USD", 0.30)

# Invoices that are never collected. The default is the middle of the
# ordinary B2B range; a deployment that knows its own figure should set
# it, because this line drives the whole collections argument.
BAD_DEBT_PERCENT = _env_float("CYBERLOGIX_BAD_DEBT_PERCENT", 3.0)

# Everything the company pays whether it has one customer or two hundred.
# Small enough to list honestly rather than estimate.
FIXED_MONTHLY_USD = _env_float("CYBERLOGIX_FIXED_MONTHLY_USD", 14.65)

# What holding a customer's cash six months early is worth. Zero is the
# right default: a company with no capital needs and margins like these
# is not short of money, and pretending otherwise is how a discount
# justifies itself.
COST_OF_CAPITAL_PERCENT = _env_float("CYBERLOGIX_COST_OF_CAPITAL_PERCENT", 0.0)


# Below this many settled invoices, a measured rate is noise. Three
# customers where one paid late is not a 33% bad-debt rate; it is three
# customers. Under the floor the configured assumption is used and the
# report says so.
MIN_INVOICES_TO_MEASURE = 20

# A payment recorded by the Stripe webhook carries the event id as its
# reference. That is how a card payment is told from a wire without
# asking anybody to categorise anything.
CARD_REFERENCE_PREFIX = "evt_"


def measured_rates() -> Dict[str, Any]:
    """Bad debt and card share, computed from the ledger rather than assumed.

    The two largest lines in this model were both guesses in a settings
    file, which is fine on day one and indefensible once there is a year
    of invoices sitting in the database. Worse, a guess is the one number
    that can be improved by editing it — and editing it improves nothing
    except the dashboard.

    So they are measured where there is enough history to measure, and
    the report says plainly which figure it used and why.
    """
    from store import STORE

    now = utc_now()
    invoices = STORE.all_invoices()

    # Only invoices old enough to have been paid by a normal customer.
    # Counting last week's invoices as bad debt would make the rate a
    # function of how recently anybody was billed.
    from contracts import DELINQUENT_AFTER_DAYS
    from invoicing import PAYMENT_TERMS_DAYS

    ripe_after = PAYMENT_TERMS_DAYS + DELINQUENT_AFTER_DAYS
    ripe = [
        inv for inv in invoices
        if inv.issued_at is not None
        and (now - inv.issued_at).days >= ripe_after
    ]

    billed = round(sum(inv.total_usd for inv in ripe), 2)
    uncollected = round(
        sum(
            inv.total_usd - inv.amount_paid_usd
            for inv in ripe
            if inv.state in ("issued", "part_paid", "void")
        ),
        2,
    )

    collected = 0.0
    by_card = 0.0
    for invoice in invoices:
        for payment in invoice.payments:
            amount = float(payment.get("amount_usd") or 0)
            collected += amount
            if str(payment.get("reference") or "").startswith(
                CARD_REFERENCE_PREFIX
            ):
                by_card += amount

    enough = len(ripe) >= MIN_INVOICES_TO_MEASURE and billed > 0
    measured_bad_debt = (
        round(max(0.0, uncollected) / billed * 100, 2) if enough else None
    )
    card_share = (
        round(by_card / collected * 100, 2) if collected > 0 else None
    )

    return {
        "invoices_old_enough_to_judge": len(ripe),
        "minimum_needed": MIN_INVOICES_TO_MEASURE,
        "measured_bad_debt_percent": measured_bad_debt,
        "assumed_bad_debt_percent": BAD_DEBT_PERCENT,
        "bad_debt_percent_used": (
            measured_bad_debt if measured_bad_debt is not None
            else BAD_DEBT_PERCENT
        ),
        "measuring_bad_debt": measured_bad_debt is not None,
        "collected_usd": round(collected, 2),
        "card_share_percent": card_share,
        "why": (
            f"Measured from {len(ripe)} invoices past "
            f"{ripe_after} days."
            if enough else
            f"Only {len(ripe)} invoice(s) are old enough to judge; "
            f"{MIN_INVOICES_TO_MEASURE} are needed before a measured rate "
            "is anything but noise. Using the configured assumption until "
            "then."
        ),
    }


def effective_card_percent() -> float:
    """The card fee weighted by how many customers actually use a card.

    Invoices lead with bank transfer, and most contracts at these sizes
    settle by wire — but assuming that is as wrong as assuming the
    opposite. Where there is payment history, the fee applies to the
    share that actually arrived through the card processor.
    """
    rates = measured_rates()
    share = rates["card_share_percent"]
    if share is None:
        return CARD_PERCENT
    return round(CARD_PERCENT * share / 100, 4)


def _usage_per_unit_month() -> float:
    """Metered spend for one monitored unit in a deliberately busy month.

    Twelve incidents — a breach every two and a half days — each texting
    a roster of three and escalating once. A real estate is far quieter,
    so this overstates the only cost that scales with customers.
    """
    from costs import RATE_AI_CALL, RATE_SMS, RATE_VOICE_CALL

    return round(
        12 * RATE_AI_CALL + 36 * RATE_SMS + 12 * RATE_VOICE_CALL, 4
    )


def per_unit_year(vertical: str = "restaurant") -> Dict[str, Any]:
    """Every cost against one unit of one sector, for a year, ranked."""
    from pricing import ANNUAL_PREPAY_DISCOUNT_PERCENT, PRICE_BOOK

    price = PRICE_BOOK[vertical]["monthly_usd"]
    revenue = round(price * 12, 2)

    lines: List[Dict[str, Any]] = [
        {
            "line": f"Annual prepay discount ({ANNUAL_PREPAY_DISCOUNT_PERCENT:g}%)",
            "usd": round(revenue * ANNUAL_PREPAY_DISCOUNT_PERCENT / 100, 2),
            "chosen": True,
            "note": (
                "Only paid on contracts that take it. It is here because "
                "it is the largest number on the page and the only one "
                "nobody invoices you for."
            ),
        },
        {
            "line": f"Bad debt ({BAD_DEBT_PERCENT:g}%)",
            "usd": round(revenue * BAD_DEBT_PERCENT / 100, 2),
            "chosen": False,
            "note": "What collections is for. Every point is real money.",
        },
        {
            "line": f"Card fees ({CARD_PERCENT:g}% + "
                    f"${CARD_FIXED_USD:g}/invoice)",
            "usd": round(revenue * CARD_PERCENT / 100 + CARD_FIXED_USD * 12, 2),
            "chosen": True,
            "note": (
                "Avoidable in full. Contracts at this size settle by bank "
                "transfer; card is a convenience being paid for at 2.9%."
            ),
        },
        {
            "line": "Metered usage (AI, SMS, voice)",
            "usd": round(_usage_per_unit_month() * 12, 2),
            "chosen": False,
            "note": (
                "The cost everyone tries to cut, and the one that does not "
                "matter. Cutting it further trades alerting for pennies."
            ),
        },
    ]
    lines.sort(key=lambda row: -row["usd"])
    for row in lines:
        row["percent_of_revenue"] = round(row["usd"] / revenue * 100, 3)

    return {
        "vertical": vertical,
        "monthly_usd": price,
        "annual_revenue_usd": revenue,
        "lines": lines,
        "total_cost_usd": round(sum(row["usd"] for row in lines), 2),
    }


def prepay_verdict() -> Dict[str, Any]:
    """Does the annual prepay discount pay for itself?

    A discount is a purchase: you are buying certainty and cash with
    margin. It is worth what it removes — the bad debt on that unit, the
    card fees, and whatever holding the money early is worth to you. If
    the discount is larger than that sum, the difference is simply given
    away, and because nobody invoices for it, it is the one cost that can
    grow for years without anybody noticing.
    """
    from pricing import ANNUAL_PREPAY_DISCOUNT_PERCENT

    avoided_bad_debt = BAD_DEBT_PERCENT
    avoided_card = CARD_PERCENT
    # Half a year of float, at whatever the money is worth.
    financing = COST_OF_CAPITAL_PERCENT * 0.5
    break_even = round(avoided_bad_debt + avoided_card + financing, 2)
    current = ANNUAL_PREPAY_DISCOUNT_PERCENT

    return {
        "current_percent": current,
        "break_even_percent": break_even,
        "worth_it": current <= break_even,
        "overpaying_by_points": round(max(0.0, current - break_even), 2),
        "buys": {
            "bad_debt_avoided_percent": avoided_bad_debt,
            "card_fees_avoided_percent": avoided_card,
            "financing_value_percent": financing,
        },
        "note": (
            f"At {current:g}% the discount is "
            + (
                "inside what it buys."
                if current <= break_even
                else f"{round(current - break_even, 2):g} points more than "
                     "what it buys. On a $999 location that is "
                     f"${round(999 * 12 * (current - break_even) / 100, 2):,.2f} "
                     "a year, per unit, given away."
            )
            + " Financing value is zero unless CYBERLOGIX_COST_OF_CAPITAL_"
            "PERCENT says otherwise, because a company with these margins "
            "and no capital needs is not short of cash. This is a "
            "commercial decision, not a bug: a smaller discount only saves "
            "anything if the customer still prepays."
        ),
    }


def statement(vertical: str = "restaurant", units: int = 1) -> Dict[str, Any]:
    """What reaches the bank, for a given number of units."""
    from pricing import PRICE_BOOK

    price = PRICE_BOOK[vertical]["monthly_usd"]
    invoiced = round(price * units, 2)

    rates = measured_rates()
    bad_debt_percent = rates["bad_debt_percent_used"]
    card_percent = effective_card_percent()
    card_share = rates["card_share_percent"]

    bad_debt = round(invoiced * bad_debt_percent / 100, 2)
    collected = round(invoiced - bad_debt, 2)
    # The fixed per-transaction fee applies only to the card share too.
    card_units = units * ((card_share or 0) / 100 if card_share is not None else 1)
    card = round(collected * card_percent / 100 + CARD_FIXED_USD * card_units, 2)
    usage = round(_usage_per_unit_month() * units, 2)
    kept = round(collected - card - usage - FIXED_MONTHLY_USD, 2)

    return {
        "vertical": vertical,
        "units": units,
        "invoiced_usd": invoiced,
        "bad_debt_usd": bad_debt,
        "card_fees_usd": card,
        "metered_usage_usd": usage,
        "fixed_costs_usd": FIXED_MONTHLY_USD,
        "before_tax_usd": kept,
        "before_tax_percent": round(kept / invoiced * 100, 2) if invoiced else 0.0,
        "marginal_cost_of_one_more_unit_usd": round(
            _usage_per_unit_month()
            + price * (bad_debt_percent + card_percent) / 100,
            2,
        ),
        "rates": rates,
        "note": (
            "Before tax, deliberately. Tax is larger than every line here "
            "put together and is not something this file should have an "
            "opinion about."
        ),
    }


# What a deployment is aiming at, pre-tax. Pre-tax because tax is not a
# cost the business controls, and a target that moves with the tax code
# tells you nothing about how the business is being run.
TARGET_PERCENT = _env_float("CYBERLOGIX_TARGET_MARGIN_PERCENT", 95.0)


def unpaid_accounts(limit: int = 10) -> List[Dict[str, Any]]:
    """Who the bad debt actually is, largest first.

    "Bad debt is 6.67%" is a statistic. "These three customers owe you
    $8,991 and the oldest is 214 days late" is a morning's work. The
    whole reason to measure the rate is to be able to do something about
    it, and nobody collects from a percentage.
    """
    from contracts import DELINQUENT_AFTER_DAYS
    from invoicing import PAYMENT_TERMS_DAYS
    from store import STORE

    now = utc_now()
    ripe_after = PAYMENT_TERMS_DAYS + DELINQUENT_AFTER_DAYS

    owed: Dict[str, Dict[str, Any]] = {}
    for invoice in STORE.all_invoices():
        if invoice.issued_at is None:
            continue
        age = (now - invoice.issued_at).days
        if age < ripe_after or invoice.state not in ("issued", "part_paid"):
            continue
        tenant = STORE.get_tenant(invoice.tenant_id)
        row = owed.setdefault(invoice.tenant_id, {
            "tenant_id": invoice.tenant_id,
            "company_name": tenant.company_name if tenant else "(deleted)",
            "contact_email": tenant.contact_email if tenant else "",
            "contact_phone": tenant.contact_phone if tenant else "",
            "owed_usd": 0.0,
            "invoices": 0,
            "oldest_days": 0,
        })
        row["owed_usd"] = round(row["owed_usd"] + invoice.balance_usd, 2)
        row["invoices"] += 1
        row["oldest_days"] = max(row["oldest_days"], age)

    return sorted(owed.values(), key=lambda r: -r["owed_usd"])[:limit]


def bad_debt_ceiling_for_target(vertical: str = "restaurant",
                                units: int = 1) -> float:
    """The bad-debt rate at which the target is met, everything else held.

    A target without a number to aim at is a mood. This is the number:
    get collections under it and the margin arrives on its own.
    """
    figures = statement(vertical, units)
    invoiced = figures["invoiced_usd"] or 1.0
    # Everything that is not bad debt, as a share of revenue.
    others = (
        figures["card_fees_usd"] + figures["metered_usage_usd"]
        + figures["fixed_costs_usd"]
    ) / invoiced * 100
    return round(max(0.0, 100 - TARGET_PERCENT - others), 2)


def target(vertical: str = "restaurant", units: int = 1) -> Dict[str, Any]:
    """Are we at the target, and if not, what is actually in the way?

    Deliberately names one thing. A list of four levers is a list nobody
    acts on, and three of these four are already too small to matter —
    at a thousand-dollar unit, metered usage is forty-seven cents.
    """
    figures = statement(vertical, units)
    invoiced = figures["invoiced_usd"] or 1.0
    achieved = figures["before_tax_percent"]

    levers = [
        ("bad debt", figures["bad_debt_usd"],
         "Collect better. The dunning ladder, the invoice on issue and "
         "the receipt all exist to move this one, and it is the only "
         "line here big enough to be worth moving."),
        ("card fees", figures["card_fees_usd"],
         "Ask to be paid by bank transfer. Invoices already lead with "
         "it; this is what remains of the customers who still use a card."),
        ("fixed costs", figures["fixed_costs_usd"],
         "Shared across every customer, so this shrinks on its own with "
         "the next one. Cutting it means cutting the hosting that runs "
         "the thing being sold."),
        ("metered usage", figures["metered_usage_usd"],
         "Already a rounding error. Cutting it trades alerting for pennies."),
    ]
    levers.sort(key=lambda row: -row[1])
    biggest, cost, advice = levers[0]

    return {
        "target_percent": TARGET_PERCENT,
        "achieved_percent": achieved,
        "met": achieved >= TARGET_PERCENT,
        "gap_points": round(max(0.0, TARGET_PERCENT - achieved), 2),
        "biggest_lever": {
            "line": biggest,
            "usd": cost,
            "percent_of_revenue": round(cost / invoiced * 100, 2),
            "what_to_do": advice,
        },
        "bad_debt_ceiling_percent": bad_debt_ceiling_for_target(vertical, units),
        # Nobody collects from a percentage.
        "who_owes_it": unpaid_accounts(),
        "note": (
            f"{achieved:.2f}% before tax against a {TARGET_PERCENT:g}% "
            "target. "
            + (
                "Met."
                if achieved >= TARGET_PERCENT
                else f"The gap is {TARGET_PERCENT - achieved:.2f} points and "
                     f"the largest single line is {biggest}."
            )
            + " Measured from the ledger where there is enough history, "
            "and from the configured assumptions until there is — a "
            "target hit by editing an assumption is not hit."
        ),
    }


@router.get("")
def read_margin(
    vertical: str = "restaurant",
    units: int = 1,
    _: None = Depends(require_platform_admin),
):
    """The whole picture: what a unit costs, what reaches the bank, and
    whether the discount is worth what it buys."""
    return {
        "target": target(vertical, units),
        "statement": statement(vertical, units),
        "per_unit_year": per_unit_year(vertical),
        "annual_prepay": prepay_verdict(),
        "fixed_costs_are_shared": (
            f"${FIXED_MONTHLY_USD:,.2f} a month in total, not per customer. "
            "One instance serves the first few hundred units, which is why "
            "the margin barely moves between one customer and two hundred."
        ),
    }
