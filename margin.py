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

    bad_debt = round(invoiced * BAD_DEBT_PERCENT / 100, 2)
    collected = round(invoiced - bad_debt, 2)
    card = round(collected * CARD_PERCENT / 100 + CARD_FIXED_USD * units, 2)
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
            + price * (BAD_DEBT_PERCENT + CARD_PERCENT) / 100
            + CARD_FIXED_USD,
            2,
        ),
        "note": (
            "Before tax, deliberately. Tax is larger than every line here "
            "put together and is not something this file should have an "
            "opinion about."
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
        "statement": statement(vertical, units),
        "per_unit_year": per_unit_year(vertical),
        "annual_prepay": prepay_verdict(),
        "fixed_costs_are_shared": (
            f"${FIXED_MONTHLY_USD:,.2f} a month in total, not per customer. "
            "One instance serves the first few hundred units, which is why "
            "the margin barely moves between one customer and two hundred."
        ),
    }
