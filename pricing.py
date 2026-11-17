"""The price book, and what a customer's estate actually costs.

Restaurants and pharmacies are priced per location: $149 a month for a
restaurant and $199 for a pharmacy, each for up to ten fridges and
freezers there, and $15 a month for each unit past ten at the same
location. A small cafe can take Starter instead: $59 a month, flat, for
one location and up to three units. There is no setup fee.

Every other trade is priced by quote. Its rate is still in PRICE_BOOK,
per unit (a rack, a vessel, a tank), because businesses already on it
are invoiced from that figure; no public page shows it. The invoice is
derived from the sensors actually registered rather than from a plan
tier.

Plan tiers still exist, but they are about evaluation versus a paid
contract and the seat ceiling that comes with it — they are not what sets
the price.

`loss_avoided_usd` is the figure the product is sold on: the loss a single
prevented failure represents. Only the verticals where a concrete number
was supplied carry one; the rest are deliberately null rather than guessed,
because an ROI page built on invented figures is worse than one that says
"not quantified".
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import require_tenant
from store import (
    INDUSTRY_PROFILES,
    PLAN_TIERS,
    STORE,
    Tenant,
    iso,
    utc_now,
)

if TYPE_CHECKING:
    from store import Sensor

router = APIRouter(prefix="/api/billing", tags=["Pricing & Billing"])

PRICE_BOOK: Dict[str, Dict[str, Any]] = {
    "cybersecurity": {
        "unit": "rack",
        "monthly_usd": 899.0,
        "custom_quote": True,
        "pitch": (
            "Prevents $50,000+ server meltdowns and SLA contract breaches with "
            "predictive forecasting."
        ),
        "loss_avoided_usd": 50000.0,
    },
    "private_aviation": {
        "unit": "bay",
        "monthly_usd": 1999.0,
        "custom_quote": True,
        "pitch": (
            "Protects multi-million-dollar avionics and jet components from "
            "high-humidity moisture corrosion."
        ),
        "loss_avoided_usd": None,
    },
    "superyacht": {
        "unit": "vessel",
        "monthly_usd": 4999.0,
        "custom_quote": True,
        "pitch": (
            "Guards engine rooms against thermal fires and secures $150k charter "
            "guest food freezers at sea."
        ),
        "loss_avoided_usd": 150000.0,
    },
    "solar_infrastructure": {
        "unit": "enclosure",
        "monthly_usd": 899.0,
        "custom_quote": True,
        "pitch": (
            "Monitors thermal runaway in commercial battery banks to protect "
            "against catastrophic fire liability."
        ),
        "loss_avoided_usd": None,
    },
    "medical_lab": {
        "unit": "vault",
        "monthly_usd": 1499.0,
        "custom_quote": True,
        "pitch": (
            "Mandated by strict OSHA/FDA chain-of-custody rules; includes "
            "automated audit report generation."
        ),
        "loss_avoided_usd": None,
    },
    "cryostorage": {
        "unit": "tank",
        "monthly_usd": 2999.0,
        "custom_quote": True,
        "pitch": (
            "Protects embryos, eggs and cell lines that cannot be replaced "
            "at any price. The evidence trail is the product here: these "
            "losses are settled in court, not on an insurance form."
        ),
        "loss_avoided_usd": None,
    },
    "country_club": {
        "unit": "kitchen",
        "monthly_usd": 1499.0,
        "custom_quote": True,
        "pitch": (
            "Safeguards high-revenue holiday dining inventory (Thanksgiving, "
            "Mother's Day) from compressor failure."
        ),
        "loss_avoided_usd": None,
    },
    "logistics": {
        "unit": "reefer truck",
        "monthly_usd": 749.0,
        "custom_quote": True,
        "pitch": (
            "Generates tamper-proof transit handover temperature passes to "
            "eliminate dock cargo rejection disputes."
        ),
        "loss_avoided_usd": None,
    },
    "wine_and_art": {
        "unit": "cellar",
        "monthly_usd": 2499.0,
        "custom_quote": True,
        "pitch": (
            "Certifies unbroken storage conditions for a collection whose "
            "value depends entirely on provenance being unquestioned."
        ),
        "loss_avoided_usd": 250000.0,
    },
    "pharmacy": {
        "unit": "pharmacy",
        # Per pharmacy, like a restaurant is per shop: $199 for the
        # pharmacy and up to UNITS_PER_LOCATION fridges in it. It was
        # $1,299 and counted each fridge as a "pharmacy", so a pharmacy
        # with a vaccine fridge and a second fridge was billed as two.
        "monthly_usd": 199.0,
        "per_site": True,
        "pitch": (
            "Meets the continuous-monitoring requirement for vaccine storage "
            "and produces the audit record on demand."
        ),
        "loss_avoided_usd": 40000.0,
    },
    "cannabis": {
        "unit": "cultivation room",
        "monthly_usd": 1199.0,
        "custom_quote": True,
        "pitch": (
            "Documents environmental control to the standard a state licence "
            "renewal is judged against."
        ),
        "loss_avoided_usd": 60000.0,
    },
    "restaurant": {
        "unit": "location",
        # Per shop, for up to UNITS_PER_LOCATION fridges and freezers in it.
        # This was once $999 and counted sensors while calling each one a
        # "location", so one sandwich shop with a freezer, a cooler and a
        # reach-in was invoiced "3 locations" at $2,997 a month. Then $249
        # per location with no limit; now $149 for up to ten units, and
        # EXTRA_UNIT_MONTHLY_USD for each one past ten at the same shop.
        # `per_site` is what makes it per location.
        "monthly_usd": 149.0,
        "per_site": True,
        "pitch": (
            "Prevents $15,000 walk-in freezer meat/seafood spoilage and "
            "automates health department logs."
        ),
        "loss_avoided_usd": 15000.0,
    },
}

# Every vertical must be priced, or a customer could register a sensor the
# invoice cannot account for.
assert set(PRICE_BOOK) == set(INDUSTRY_PROFILES), (
    "Price book and industry profiles are out of step: "
    f"{set(PRICE_BOOK) ^ set(INDUSTRY_PROFILES)}"
)


# ---- the published prices, and the trades priced by quote --------------
#
# Restaurants and pharmacies have a price anyone can read. Every other
# trade is "custom_quote": its rate stays in PRICE_BOOK, because the
# invoices of businesses already on it are worked out from that figure,
# but no public page, sheet or unauthenticated API shows the number. They
# say CUSTOM_PRICE_LABEL and offer a way to talk to us instead.
CUSTOM_PRICE_LABEL = "Custom pricing — ask for a quote"


def custom_quote(vertical: str) -> bool:
    """Priced by quote on every public page; billed from PRICE_BOOK."""
    return bool(PRICE_BOOK.get(vertical, {}).get("custom_quote"))


def public_price(vertical: str) -> Optional[float]:
    """The monthly rate a public page may show, or None for a quote."""
    if custom_quote(vertical):
        return None
    return PRICE_BOOK[vertical]["monthly_usd"]


def public_price_label(vertical: str) -> str:
    """"$149 / location / month", or the custom-pricing words."""
    if custom_quote(vertical):
        return CUSTOM_PRICE_LABEL
    entry = PRICE_BOOK[vertical]
    return f"${entry['monthly_usd']:,.0f} / {entry['unit']} / month"


# ---- units at one location ----------------------------------------------
#
# A location's price covers up to UNITS_PER_LOCATION fridges and freezers
# there. Each unit past that at the same location is a flat
# EXTRA_UNIT_MONTHLY_USD a month: $15 is a tenth of $149 rounded up, so
# the eleventh unit costs about what each of the first ten does. One small
# line on the invoice, the same every month, rather than a whole second
# location for one more fridge. Applies to the per-location trades
# (restaurants and pharmacies); Starter has its own three-unit limit.
UNITS_PER_LOCATION = 10
EXTRA_UNIT_MONTHLY_USD = 15.0

# ---- businesses already on the old prices --------------------------------
#
# Every price here went down on PRICES_CHANGED_AT, and an invoice is
# priced from this file on the day it is issued, so a business already
# on the old prices pays the new ones from its next invoice: nothing to
# migrate, and invoices already issued stay exactly as they were sent.
#
# The one way the new card could cost somebody more is a restaurant with
# more than ten units at one shop, which paid a flat $249 for the shop
# before. For a business that signed up before the change, a location's
# extra-unit charge is held so that the location never costs more than
# OLD_LOCATION_PRICE_USD. New businesses pay the card.
PRICES_CHANGED_AT = datetime(2026, 11, 14, tzinfo=timezone.utc)
OLD_LOCATION_PRICE_USD: Dict[str, float] = {"restaurant": 249.0}


def signed_up_before_price_change(tenant: Any) -> bool:
    activated = getattr(tenant, "activated_at", None)
    return activated is not None and activated < PRICES_CHANGED_AT


def units_per_location(vertical: str) -> Optional[int]:
    """How many units one location's price covers, or None for no limit."""
    return UNITS_PER_LOCATION if per_site(vertical) else None


def location_unit_counts(sensors: Iterable["Sensor"]) -> List[int]:
    """How many units are at each location, by the same rule as branches().

    Units not filed under a location, where locations exist, are counted
    at none of them: the customer is given the benefit of the doubt, as
    branches() does for the location count.
    """
    sited: Dict[str, int] = {}
    unsited = 0
    for sensor in sensors:
        if sensor.site_id:
            sited[sensor.site_id] = sited.get(sensor.site_id, 0) + 1
        else:
            unsited += 1
    if sited:
        return list(sited.values())
    return [unsited] if unsited else []


def extra_units_charge(vertical: str, counts: Iterable[int],
                       held_to_old_price: bool = False) -> Dict[str, Any]:
    """Units past the allowance at each location, and what they cost.

    `held_to_old_price` is the protection for a business that signed up
    before the change: no location's total goes over its old flat price.
    """
    cap = units_per_location(vertical)
    if cap is None:
        return {"units": 0, "amount_usd": 0.0, "held": False}
    old = OLD_LOCATION_PRICE_USD.get(vertical) if held_to_old_price else None
    units, amount, held = 0, 0.0, False
    for count in counts:
        extra = max(0, int(count) - cap)
        charge = extra * EXTRA_UNIT_MONTHLY_USD
        if old is not None and charge > old - unit_price(vertical):
            charge = max(0.0, old - unit_price(vertical))
            held = True
        units += extra
        amount += charge
    return {"units": units, "amount_usd": round(amount, 2), "held": held}


# ---- contract terms ---------------------------------------------------
#
# The rate card is only half of what an estate is worth. These are the
# terms that decide the other half, and the ones customers rarely
# negotiate out because each is standard practice in enterprise software.

# No setup fee. It was $1,500 a site, charged once at commissioning; the
# owner set it to nothing so the first bill is just the first month. The
# figure stays a named setting, read by the deal, the invoice and the
# contract's first bill, so all three say $0 together; at zero they add
# no setup line at all.
SETUP_FEE_PER_SITE_USD = 0.0

# ---- paying yearly: pay for 10 months, get 12 ----------------------------
#
# The one yearly offer. Paying a year up front costs ten times the monthly
# price and covers twelve months: "2 months free", about 16.7% off. It
# applies to everything recurring on the bill (Starter, the location
# prices, extra units, the Kitchen Safety Bundle and the other extras),
# after any chain discount, so a chain on yearly pays ten months of its
# already-discounted price.
#
# This replaced a 10% annual prepayment discount, which was a setting
# (CYBERLOGIX_ANNUAL_PREPAY_DISCOUNT_PERCENT). It is a fixed rule now, so
# the pricing page, the help page, the sheets, the invoice and the margin
# report all say the same thing: two months free.
#
# One invoice for the year, then nothing until the year renews, apart
# from a prorated charge if a location or units are added part-way
# through (contracts.bill_mid_year_changes). Reminders go out
# YEARLY_RENEWAL_REMINDER_DAYS before the renewal. Switching between
# monthly and yearly happens when what has been paid for runs out.
YEARLY_MONTHS_COVERED = 12
YEARLY_MONTHS_CHARGED = 10
YEARLY_MONTHS_FREE = YEARLY_MONTHS_COVERED - YEARLY_MONTHS_CHARGED
# For the pages and the margin report only; bills use the months above.
YEARLY_DISCOUNT_PERCENT = round(100.0 * YEARLY_MONTHS_FREE / YEARLY_MONTHS_COVERED, 2)
YEARLY_RENEWAL_REMINDER_DAYS = (30, 7)

# What happens when a business cancels, in the words every page uses.
CANCELLATION_RULE = (
    "Cancel any time. Nothing more is billed after you cancel, and the "
    "service carries on to the end of the month or year you have already "
    "paid for. What you have already paid is not refunded, in part or in "
    "full.")


def yearly_price(monthly_usd: float) -> float:
    """A year paid up front: ten months of the monthly price."""
    return round(float(monthly_usd) * YEARLY_MONTHS_CHARGED, 2)


def yearly_saving(monthly_usd: float) -> float:
    """What paying yearly saves against twelve monthly bills."""
    return round(float(monthly_usd) * YEARLY_MONTHS_FREE, 2)


def yearly_label() -> str:
    """'2 months free', from the numbers above."""
    return f"{YEARLY_MONTHS_FREE} months free"


def yearly_terms() -> Dict[str, Any]:
    """The yearly offer as the pages, the console and the API state it."""
    return {
        "months_covered": YEARLY_MONTHS_COVERED,
        "months_charged": YEARLY_MONTHS_CHARGED,
        "months_free": YEARLY_MONTHS_FREE,
        "discount_percent": YEARLY_DISCOUNT_PERCENT,
        "label": yearly_label(),
        "headline": (f"Pay yearly: {YEARLY_MONTHS_COVERED} months for the "
                     f"price of {YEARLY_MONTHS_CHARGED} ({yearly_label()})"),
        "applies_to": ("Starter, every location price, extra units and the "
                       "extras, after any chain discount"),
        "billing": ("One invoice for the year, then nothing until it "
                    "renews. Adding a location or units part-way through "
                    "adds a prorated charge for the rest of the paid year. "
                    "We email "
                    + " and ".join(f"{d}" for d in YEARLY_RENEWAL_REMINDER_DAYS)
                    + " days before it renews."),
        "switching": ("Switch between monthly and yearly at renewal: the "
                      "change starts when what you have paid for runs out."),
        "reminder_days": list(YEARLY_RENEWAL_REMINDER_DAYS),
        "cancellation": CANCELLATION_RULE,
    }


# ---- chain discounts -----------------------------------------------------
#
# A business with many restaurants or pharmacies pays less for each one:
# (from this many locations, percent off each location's price). Self
# serve, worked out from the locations on the account, and shown on the
# invoice as its own line: "Chain discount (20 locations): −20%".
#
# What it applies to, and what it does not:
#   * The location prices of the per-location trades (restaurants and
#     pharmacies), counted together: a business with 12 restaurants and
#     8 pharmacies is a 20-location chain.
#   * Not Starter, which is one location.
#   * Not the $15 extra-unit fee or the extras. "The price of each
#     location drops" is the whole rule, and it is the one a customer can
#     check on the invoice: 20 × $149 × 20%.
#   * Yearly is worked out on the discounted price.
#
# No business pays more than a bigger chain would. Twenty locations at
# 20% off cost less than eighteen or nineteen at 10% off, so those two
# pay the twenty-location price instead (the invoice says so).
#
# This is the only volume discount. It replaced the enterprise ladder
# (2.5% off for every ten units, up to 10%), which priced volume
# contracts: those contracts now use these tiers too, and a business on
# one is billed from the contract alone, so nobody gets both.
CHAIN_DISCOUNT_TIERS: Tuple[Tuple[int, float], ...] = ((5, 10.0), (20, 20.0))


def _half_up(value: float) -> int:
    """Round to a whole number, halves up, as the page's Math.round does."""
    return int(math.floor(value + 0.5 + 1e-9))


def chain_discount_percent(locations: int) -> float:
    """Percent off each location's price for a chain this size."""
    percent = 0.0
    for start, off in CHAIN_DISCOUNT_TIERS:
        if int(locations) >= start:
            percent = off
    return percent


def chain_tier_label(locations: int) -> str:
    """'5–19 locations: 10% off', or the list price below the first tier."""
    starts = [start for start, _ in CHAIN_DISCOUNT_TIERS]
    if int(locations) < starts[0]:
        return f"1–{starts[0] - 1} locations: list price"
    for index, (start, off) in enumerate(CHAIN_DISCOUNT_TIERS):
        nxt = starts[index + 1] if index + 1 < len(starts) else None
        if int(locations) >= start and (nxt is None or int(locations) < nxt):
            span = f"{start}+" if nxt is None else f"{start}–{nxt - 1}"
            return f"{span} locations: {off:g}% off"
    return ""  # unreachable: every count >= the first start is in a tier


def chain_discount(location_total: float, locations: int) -> Dict[str, Any]:
    """The chain discount on `locations` locations costing `location_total`.

    Worked in cents, rounded half up, the same way static/pricing.js
    does, so the page and the invoice agree to the cent. `capped_at` is
    the bigger chain whose price this one was held to, if any.
    """
    locations = max(0, int(locations))
    total_cents = _half_up(float(location_total) * 100)
    percent = chain_discount_percent(locations)
    after = total_cents - _half_up(total_cents * percent / 100.0)
    capped_at: Optional[int] = None
    if locations:
        for start, off in CHAIN_DISCOUNT_TIERS:
            if start <= locations:
                continue
            ceiling = _half_up(total_cents * start * (100.0 - off)
                               / (locations * 100.0))
            if ceiling < after:
                after, capped_at = ceiling, start
    return {
        "locations": locations,
        "percent": percent,
        "amount_usd": round((total_cents - after) / 100.0, 2),
        "after_usd": round(after / 100.0, 2),
        "capped_at": capped_at,
    }


def chain_locations_price(locations: int, location_price: float) -> float:
    """What `locations` locations at `location_price` cost after the chain
    discount. Also what an enterprise volume contract charges per month."""
    if int(locations) <= 0:
        return 0.0
    return chain_discount(location_price * int(locations), locations)["after_usd"]


def chain_line_description(discount: Dict[str, Any]) -> str:
    """'Chain discount (20 locations): −20%', as the invoice prints it."""
    text = (f"Chain discount ({discount['locations']} locations): "
            f"−{discount['percent']:g}%")
    if discount["capped_at"]:
        text += (f", held to the {discount['capped_at']}-location price so "
                 "a bigger chain never pays less")
    return text


def next_chain_tier(locations: int, location_price: float) -> Optional[Dict[str, Any]]:
    """The next chain tier, and what the business would pay there."""
    upcoming = [(start, off) for start, off in CHAIN_DISCOUNT_TIERS
                if start > int(locations)]
    if int(locations) <= 0 or not upcoming:
        return None
    start, off = upcoming[0]
    here = chain_locations_price(locations, location_price)
    there = chain_locations_price(start, location_price)
    return {
        "units_until_next_discount": start - int(locations),
        "next_discount_at_units": start,
        "next_discount_label": chain_tier_label(start),
        "next_discount_percent": off,
        "next_tier_monthly_usd": there,
        "monthly_increase_usd": round(there - here, 2),
    }


def chain_tiers(vertical: str = "restaurant") -> List[Dict[str, Any]]:
    """The tiers as the pricing page's table shows them, priced for one
    trade: each tier's price per location, and a whole-chain example."""
    price = unit_price(vertical)
    starts = [start for start, _ in CHAIN_DISCOUNT_TIERS]
    rows: List[Dict[str, Any]] = [{"from": 1, "to": starts[0] - 1, "percent": 0.0,
                                   "label": chain_tier_label(1)}]
    for index, (start, off) in enumerate(CHAIN_DISCOUNT_TIERS):
        nxt = starts[index + 1] - 1 if index + 1 < len(starts) else None
        rows.append({"from": start, "to": nxt, "percent": off,
                     "label": chain_tier_label(start)})
    for row in rows:
        each = round(price * (1 - row["percent"] / 100.0), 2)
        monthly = chain_locations_price(row["from"], price)
        row.update({
            "vertical": vertical,
            "each_usd": each,
            "each_yearly_usd": yearly_price(each),
            "example_locations": row["from"],
            "example_monthly_usd": monthly,
            "example_yearly_usd": yearly_price(monthly),
        })
    return rows


def chain_example(vertical: str = "restaurant",
                  locations: Optional[int] = None) -> Dict[str, Any]:
    """The worked example the pricing page prints, computed: by default
    the top tier, '20 restaurants: $149 → $119.20 each, $2,384 a month,
    or $23,840 a year'."""
    if locations is None:
        locations = CHAIN_DISCOUNT_TIERS[-1][0]
    price = unit_price(vertical)
    monthly = chain_locations_price(locations, price)
    return {
        "vertical": vertical,
        "locations": locations,
        "plural": plural(vertical, locations),
        "list_usd": price,
        "percent": chain_discount_percent(locations),
        "each_usd": round(monthly / locations, 2) if locations else 0.0,
        "monthly_usd": monthly,
        "yearly_usd": yearly_price(monthly),
        "twelve_monthly_usd": round(monthly * YEARLY_MONTHS_COVERED, 2),
    }

# Written into multi-year contracts. Uncontroversial at signature and
# compounding: three years at five percent is sixteen percent more
# contract value for no additional delivery.
ANNUAL_ESCALATOR_PERCENT = 5.0
MAX_CONTRACT_YEARS = 5

# Flat monthly add-ons. Every one is a fixed fee — nothing here is a share
# of what a customer saved, because a quiet year would then pay nothing
# for the same standing obligation.
ADD_ONS: Dict[str, Dict[str, Any]] = {
    "vault": {
        "name": "Certified Compliance Vault",
        "basis": "per estate",
        "monthly_usd": 499.0,
        "description": (
            "Hash-chained readings and signed attestations an insurer, "
            "auditor or buyer can verify without trusting either party, "
            "plus unlimited claim evidence packets."
        ),
    },
    "benchmarks": {
        "name": "Sector Benchmarks",
        "basis": "per estate",
        "monthly_usd": 299.0,
        "description": (
            "Where this estate sits against comparable operators on "
            "excursion rate, response time and uptime, refreshed quarterly."
        ),
    },
    "equipment_intelligence": {
        "name": "Equipment Intelligence",
        "basis": "per estate",
        "monthly_usd": 399.0,
        "description": (
            "Failure and drift rates by hardware manufacturer across the "
            "whole fleet, for anyone specifying what to buy next."
        ),
    },
    # The CyberLogix half of the Kitchen Safety Bundle with OmniArmor
    # (omniarmor.py). Billed while the connection is switched on rather
    # than chosen from a list, so nobody pays for a connection they took
    # off and nobody runs one that is not on the bill. Free on a trial,
    # like everything else, so it can be tried before it is bought.
    #
    # It includes OmniArmor Pro for each location: OmniArmor waives its own
    # Pro fee for a workspace whose daily reports arrive signed by a
    # CyberLogix server (KITCHEN_BUNDLE_SECRET, omniarmor.py), which is
    # how only a connection on this bill unlocks it.
    "kitchen_safety_bundle": {
        "name": "Kitchen Safety Bundle",
        "basis": "per location",
        "monthly_usd": 29.0,
        "included_in_trial": True,
        "billed_while_connected": True,
        "includes": "OmniArmor Pro for that location",
        "description": (
            "Each day's fridge and freezer record sent to OmniArmor, which "
            "fills in its temperature-log checks for you, and OmniArmor Pro "
            "for that location included. Billed while the connection is "
            "on; free during the trial."
        ),
    },
}

# The bundle's key, named once so the sender and the bill agree on it.
BUNDLE_ADD_ON = "kitchen_safety_bundle"


def bundle_price_label() -> str:
    """The bundle's add-on price as words, for the MTRX Horizyn bundle page."""
    entry = ADD_ONS[BUNDLE_ADD_ON]
    return f"+${entry['monthly_usd']:,.0f} a month per location"


def bundle_includes_label() -> str:
    """What the bundle includes besides the daily report, as words."""
    return f"Includes {ADD_ONS[BUNDLE_ADD_ON]['includes']}"


def add_on_price(key: str, units: int, locations: int = 1) -> float:
    """What one add-on costs an estate of this size.

    `locations` matters only to an add-on priced per location (the
    Kitchen Safety Bundle); everything else is per estate or per unit.
    """
    entry = ADD_ONS[key]
    if entry["basis"] == "per covered unit":
        multiplier = units
    elif entry["basis"] == "per location":
        multiplier = max(0, int(locations))
    else:
        multiplier = 1
    return round(entry["monthly_usd"] * multiplier, 2)


def locations_billed(tenant: Tenant) -> int:
    """How many locations a per-location charge counts for this estate.

    The sites set up, and one location for an estate that has units but
    never set up sites. Starter
    covers one location and cannot set up a second (sites.py).
    """
    if not STORE.sensors_for(tenant.tenant_id):
        return 0
    return max(len(STORE.sites_for(tenant.tenant_id)), 1)


def auto_add_ons(tenant: Tenant) -> List[str]:
    """Add-ons billed because something is switched on, not because they
    were picked: today, the Kitchen Safety Bundle while OmniArmor is
    connected. Imported late: omniarmor imports this module."""
    if tenant.plan == "trial":
        return []  # included in the trial, so it can be tried first
    import omniarmor

    return [BUNDLE_ADD_ON] if omniarmor.bundle_active(tenant.tenant_id) else []


# ---- the Starter plan -------------------------------------------------
#
# A flat price for a small cafe or shop: one location, up to three fridges
# or freezers, texts and the site-wide alert included. The seat count and
# the one-location limit are in domain.PLAN_TIERS["starter"]; the price,
# and which trades and extras it is for, are here so the owner changes one
# file. The fourth unit or a second location asks them to move to Growth
# (licenses.py, sites.py) and charges nothing on its own.
STARTER_PLAN = "starter"
STARTER_MONTHLY_USD = 59.0
# A cafe or shop's fridges and freezers, on the restaurant limits. A
# vaccine fridge or a server rack is a different job at a different
# price, and a $59 plan that took either would undercut the rate card by
# more than the plan is worth.
STARTER_VERTICALS = ("restaurant",)
# The extras that fit a one-location plan. The per-estate extras (the
# vault, benchmarks, equipment intelligence) cost more than the plan and
# are for estates; moving to Growth opens them.
STARTER_ADD_ONS = (BUNDLE_ADD_ON,)


def setup_fee_per_site(tenant: Tenant) -> float:
    """The one-time setup fee per site: none, on every plan."""
    return SETUP_FEE_PER_SITE_USD


def starter_terms() -> Dict[str, Any]:
    """Starter as the pages and the console describe it, from the code."""
    tier = PLAN_TIERS[STARTER_PLAN]
    return {
        "plan": STARTER_PLAN,
        "name": tier["name"],
        "monthly_usd": STARTER_MONTHLY_USD,
        "monthly_label": f"${STARTER_MONTHLY_USD:,.0f}",
        "max_units": tier["max_sensors"],
        "max_locations": tier["max_locations"],
        "verticals": list(STARTER_VERTICALS),
        "add_ons": list(STARTER_ADD_ONS),
        "voice_escalation": tier["voice_escalation"],
        "predictive_forecasting": tier["predictive_forecasting"],
        "includes": [
            "Text alerts to everyone on call",
            "The site-wide alert when several units warm at once",
            "The inspector's log and the monthly report",
            "Tamper-evident readings and claim packets",
        ],
        "moves_to": "growth",
    }


# ---- bring a friend ---------------------------------------------------
#
# A business that signs up with another's referral code gets a longer
# trial. When it pays its first invoice, the business that referred it
# gets one month free on one location, as a credit on its next invoice
# (referrals.py, credits.py).
REFERRAL_TRIAL_DAYS = 30
REFERRAL_REWARDS_PER_YEAR = 12


def referral_credit_usd(tenant: Tenant) -> float:
    """One month free on one location, for this referrer, today.

    Starter: the plan's price, which is one location. A business billed per
    location: that location's rate. A business billed per unit (a rack, a
    tank) has no location price, so the reward is its cheapest unit for a
    month. An estate with nothing billable yet gets the cheapest location
    on the rate card, so the reward is never zero.
    """
    if tenant.plan == STARTER_PLAN:
        return STARTER_MONTHLY_USD
    verticals = {s.industry_vertical for s in STORE.sensors_for(tenant.tenant_id)
                 if s.industry_vertical in PRICE_BOOK}
    by_location = [unit_price(v) for v in verticals if per_site(v)]
    if by_location:
        return round(min(by_location), 2)
    if verticals:
        return round(min(unit_price(v) for v in verticals), 2)
    return round(min(e["monthly_usd"] for v, e in PRICE_BOOK.items() if per_site(v)), 2)


def escalated_schedule(
    monthly_usd: float, years: int, escalator_percent: float = ANNUAL_ESCALATOR_PERCENT
) -> List[Dict[str, Any]]:
    """Year-by-year contract value with the escalator applied.

    Worked the way a signed contract bills (Subscription.rate_multiplier
    and contracts.term_schedule): the escalator compounded on the year-one
    price, not on last year's rounded figure, so the quote and the
    contract agree to the cent in every year.
    """
    schedule = []
    for year in range(1, max(1, min(years, MAX_CONTRACT_YEARS)) + 1):
        multiplier = round((1 + escalator_percent / 100.0) ** (year - 1), 6)
        schedule.append(
            {
                "year": year,
                "monthly_usd": round(monthly_usd * multiplier, 2),
                "annual_usd": round(monthly_usd * multiplier * 12, 2),
                "multiplier": multiplier,
            }
        )
    return schedule


def unit_price(vertical: str) -> float:
    return PRICE_BOOK[vertical]["monthly_usd"]


def headline() -> Dict[str, Any]:
    """The three facts the front page has to state in one screen.

    The lowest rate on the card and what it is per, and the trial's length
    and size. Read from the rate card and the plan tiers at serve time, so
    the front page cannot quote a number the product does not charge.
    Only published rates count: a trade priced by quote has no "from".
    """
    vertical, entry = min(((v, e) for v, e in PRICE_BOOK.items()
                           if not custom_quote(v)),
                          key=lambda kv: kv[1]["monthly_usd"])
    trial = PLAN_TIERS["trial"]
    return {
        "from_usd": entry["monthly_usd"],
        "from_label": f"${entry['monthly_usd']:,.0f}",
        "from_unit": entry["unit"],
        "from_vertical": vertical,
        "trial_days": trial["term_days"],
        "trial_units": trial["max_sensors"],
        "industries": len(PRICE_BOOK),
        "custom_industries": sum(1 for v in PRICE_BOOK if custom_quote(v)),
        "units_per_location": UNITS_PER_LOCATION,
        "extra_unit_usd": EXTRA_UNIT_MONTHLY_USD,
        "setup_fee_usd": SETUP_FEE_PER_SITE_USD,
        # The flat plan for a small cafe or shop, beside the rate card.
        "starter_usd": STARTER_MONTHLY_USD,
        "starter_label": f"${STARTER_MONTHLY_USD:,.0f}",
        "starter_units": PLAN_TIERS[STARTER_PLAN]["max_sensors"],
        # Paying yearly, and the chain discounts, for the front page.
        "yearly_label": yearly_label(),
        "chain_top_percent": CHAIN_DISCOUNT_TIERS[-1][1],
        "chain_first_locations": CHAIN_DISCOUNT_TIERS[0][0],
        # Signing up takes no payment details: SignupRequest has no field
        # for one. Stated here so the page says it from the code that
        # makes it true.
        "card_required": False,
    }


def calculator_rates() -> Dict[str, Any]:
    """Everything the public price calculator works from.

    The /pricing page renders these into its markup and static/pricing.js
    reads them back out, so the calculator has no number of its own: change
    a rate here and the page, the calculator and the invoice all move
    together. tests/test_pricing_page.py holds the three to each other.

    A trade priced by quote is listed with no figure at all
    (`monthly_usd` None, `custom` True): this is published, so its rate
    must not be in it.
    """
    rates = [
        {
            "vertical": vertical,
            "industry": INDUSTRY_PROFILES[vertical]["name"],
            "unit": entry["unit"],
            "unit_plural": plural(vertical, 2),
            "monthly_usd": public_price(vertical),
            "custom": custom_quote(vertical),
            "per_site": per_site(vertical),
            "units_per_location": units_per_location(vertical),
        }
        # The published prices first, then the trades priced by quote.
        for vertical, entry in sorted(
            PRICE_BOOK.items(),
            key=lambda kv: (custom_quote(kv[0]), INDUSTRY_PROFILES[kv[0]]["name"]))
    ]
    return {
        "currency": "USD",
        "rates": rates,
        "add_ons": [
            {"key": key, "name": entry["name"], "basis": entry["basis"],
             "monthly_usd": entry["monthly_usd"],
             "description": entry["description"],
             "on_starter": key in STARTER_ADD_ONS,
             "included_in_trial": bool(entry.get("included_in_trial"))}
            for key, entry in ADD_ONS.items()
        ],
        "starter": starter_terms(),
        "yearly": yearly_terms(),
        "chain_tiers": [{"from": start, "percent": off}
                        for start, off in CHAIN_DISCOUNT_TIERS],
        "units_per_location": UNITS_PER_LOCATION,
        "extra_unit_usd": EXTRA_UNIT_MONTHLY_USD,
        "custom_label": CUSTOM_PRICE_LABEL,
    }


def quote_monthly(units: Dict[str, int], add_ons: Iterable[str] = (),
                  plan: Optional[str] = None,
                  locations: Optional[int] = None,
                  units_at_each_location: Optional[Dict[str, int]] = None,
                  yearly: bool = False) -> float:
    """The monthly price of an estate described as {vertical: units}.

    The calculator's sum, in Python: what build_subscription charges for
    the same estate on the rate card, plus add-ons. The page's JavaScript
    is tested against this.

    `plan="starter"` is the flat Starter price for one location instead of
    the rate card, with only the extras Starter takes. `locations` counts
    for an extra priced per location; left out, it is the restaurant
    locations in the estate, or one.

    `units_at_each_location` is, for a per-location trade, how many units
    each of its locations has; past UNITS_PER_LOCATION, each one adds
    EXTRA_UNIT_MONTHLY_USD, as it does on the invoice. Left out, every
    location is within its allowance.

    The chain discount comes off the per-location trades' location prices
    together, as build_subscription takes it off. `yearly=True` is the
    price of a year paid up front instead: ten of these months.
    """
    if yearly:
        return yearly_price(quote_monthly(units, add_ons, plan=plan,
                                          locations=locations,
                                          units_at_each_location=units_at_each_location))
    estate = sum(max(0, int(count)) for count in units.values())
    wanted = list(add_ons)
    if plan == STARTER_PLAN:
        refused = [key for key in wanted if key not in STARTER_ADD_ONS]
        if refused:
            raise ValueError(f"Starter does not take {refused}.")
        total = STARTER_MONTHLY_USD
        total += sum(add_on_price(key, estate, 1) for key in wanted)
        return round(total, 2)
    if locations is None:
        locations = sum(max(0, int(count)) for vertical, count in units.items()
                        if per_site(vertical)) or (1 if estate else 0)
    total = sum(unit_price(vertical) * max(0, int(count))
                for vertical, count in units.items())
    chain_places = sum(max(0, int(count)) for vertical, count in units.items()
                       if per_site(vertical))
    total -= chain_discount(
        sum(unit_price(vertical) * max(0, int(count))
            for vertical, count in units.items() if per_site(vertical)),
        chain_places)["amount_usd"]
    for vertical, each in (units_at_each_location or {}).items():
        places = max(0, int(units.get(vertical, 0)))
        total += extra_units_charge(vertical, [max(0, int(each))] * places)["amount_usd"]
    total += sum(add_on_price(key, estate, locations) for key in wanted)
    return round(total, 2)


def per_site(vertical: str) -> bool:
    """Billed per location rather than per sensor.

    Every other sector is priced by the asset -- a rack, a vessel, a tank --
    and each of those carries one sensor. A restaurant is priced by the
    shop, and a shop has several.
    """
    return bool(PRICE_BOOK.get(vertical, {}).get("per_site"))


def branches(sensors: Iterable["Sensor"]) -> Dict[str, Optional["datetime"]]:
    """The locations one sector's sensors cover, and when each first appeared.

    A location is a site. Sensors not assigned to one are counted as a
    single location between them -- which is what a one-shop customer who
    never set up sites is -- but only when none of the sector's sensors has
    a site. Once any do, an unassigned sensor is taken to belong to a shop
    already counted and is not billed as a new one: charging a customer
    for a location because a thermometer was not filed under it is the
    wrong way round to be mistaken.

    Returned as {location key: earliest registration}, so the part-period
    billing can tell a new shop from a new thermometer in an old one.
    """
    sited: Dict[str, List[Optional[datetime]]] = {}
    unsited: List[Optional[datetime]] = []
    for sensor in sensors:
        if sensor.site_id:
            sited.setdefault(sensor.site_id, []).append(sensor.registered_at)
        else:
            unsited.append(sensor.registered_at)
    groups = sited or ({"": unsited} if unsited else {})
    return {
        key: min((t for t in times if t is not None), default=None)
        for key, times in groups.items()
    }


def unassigned_alongside_sites(sensors: List["Sensor"], tenant_has_sites: bool = False) -> int:
    """Sensors not filed under a location, where locations exist to file them.

    Named on the invoice when the account has sites set up -- whether or
    not any of this sector's sensors are in one -- because that is the
    case where the location count can be lower than the shops the
    customer actually has, and they should be able to see why.
    """
    if not (tenant_has_sites or any(s.site_id for s in sensors)):
        return 0
    return sum(1 for s in sensors if not s.site_id)


def plural(vertical: str, count: int) -> str:
    unit = PRICE_BOOK[vertical]["unit"]
    if count == 1:
        return unit
    return unit + ("es" if unit.endswith(("s", "x", "ch")) else "s")


def build_subscription(tenant: Tenant) -> Dict[str, Any]:
    """Price a tenant's estate from the sensors actually registered."""
    sensors = STORE.sensors_for(tenant.tenant_id)

    by_vertical: Dict[str, List[Any]] = {}
    for sensor in sensors:
        by_vertical.setdefault(sensor.industry_vertical, []).append(sensor)

    # Starter is one flat price for the cafe's fridges and freezers. Those
    # sensors are taken out of the rate card here and billed as one line
    # below; anything else (an operator-led change can leave one) is still
    # priced at the card, so nothing is served for free.
    starter_group: List[Any] = []
    if tenant.plan == STARTER_PLAN:
        for vertical in STARTER_VERTICALS:
            starter_group += by_vertical.pop(vertical, [])

    # What each sector is billed on: its locations, or its sensors.
    counts: Dict[str, int] = {
        vertical: len(branches(group)) if per_site(vertical) else len(group)
        for vertical, group in by_vertical.items()
    }

    lines: List[Dict[str, Any]] = []
    if starter_group:
        cap = PLAN_TIERS[STARTER_PLAN]["max_sensors"]
        vertical = starter_group[0].industry_vertical
        lines.append(
            {
                "vertical": vertical,
                "industry": INDUSTRY_PROFILES[vertical]["name"],
                "unit": "location",
                "units": 1,
                "unit_price_usd": STARTER_MONTHLY_USD,
                "line_total_usd": STARTER_MONTHLY_USD,
                "plan": STARTER_PLAN,
                "description": (
                    f"Starter plan: 1 location, {len(starter_group)} of "
                    f"{cap} units"),
            }
        )
    for vertical, count in sorted(
        counts.items(), key=lambda kv: -unit_price(kv[0]) * kv[1]
    ):
        entry = PRICE_BOOK[vertical]
        description = f"{count} {plural(vertical, count)}"
        if per_site(vertical):
            group = by_vertical[vertical]
            description += (
                f" ({len(group)} sensor{'' if len(group) == 1 else 's'})")
            loose = unassigned_alongside_sites(
                group, tenant_has_sites=bool(STORE.sites_for(tenant.tenant_id)))
            if loose:
                # Said on the invoice rather than billed or silently
                # dropped, so the customer can see why the count is what
                # it is and file the sensor under its shop.
                description += (
                    f", {loose} not assigned to a {entry['unit']}")
        lines.append(
            {
                "vertical": vertical,
                "industry": INDUSTRY_PROFILES[vertical]["name"],
                "unit": entry["unit"],
                "units": count,
                "unit_price_usd": entry["monthly_usd"],
                "line_total_usd": round(entry["monthly_usd"] * count, 2),
                "description": description,
            }
        )
        if per_site(vertical):
            extra = extra_units_charge(
                vertical, location_unit_counts(by_vertical[vertical]),
                held_to_old_price=signed_up_before_price_change(tenant))
            if extra["units"] and extra["amount_usd"] > 0:
                lines.append(extra_units_line(vertical, extra))

    # The chain discount, on its own line, off the per-location trades'
    # location prices counted together (not Starter, not extra units).
    chain = chain_discount_line(counts)
    if chain is not None:
        lines.append(chain)

    mrr = round(sum(line["line_total_usd"] for line in lines), 2)
    tier = tenant.entitlements()
    billable = tenant.plan != "trial"

    # A cluster contract supersedes the rate card: the chain is buying whole
    # branches, and billing both models at once would double-charge it.
    contract = STORE.active_contract(tenant.tenant_id)
    if contract is not None:
        return {
            "tenant_id": tenant.tenant_id,
            "company_name": tenant.company_name,
            "plan": tenant.plan,
            "plan_name": tier["name"],
            "billing_model": "enterprise_volume",
            "billable": billable,
            "units_total": len(sensors),
            "seats_total": tier["max_sensors"],
            "contract": contract.public(),
            "line_items": [
                {
                    "vertical": contract.industry_vertical,
                    "industry": INDUSTRY_PROFILES[contract.industry_vertical]["name"],
                    "unit": "branch",
                    "units": contract.enrolled_branches,
                    "unit_price_usd": contract.effective_rate_per_branch_usd,
                    "line_total_usd": contract.monthly_usd,
                    "description": (
                        f"{contract.enrolled_branches} branches · "
                        f"{contract.tier_label}"
                    ),
                }
            ],
            "monthly_total_usd": contract.monthly_usd,
            "annual_total_usd": contract.annual_contract_value_usd,
            "yearly_total_usd": yearly_price(contract.monthly_usd),
            "yearly_label": yearly_label(),
            "effective_monthly_usd": 0.0 if not billable else contract.monthly_usd,
            "rate_card_equivalent_usd": mrr,
            "currency": "USD",
            "note": (
                f"Billed on volume contract {contract.account_id} "
                f"({contract.tier_label}); covers every sensor inside an "
                "enrolled branch. Its price already carries the chain "
                "discount, so no second discount is added."
            ),
            "generated_at": iso(utc_now()),
        }

    return {
        "tenant_id": tenant.tenant_id,
        "company_name": tenant.company_name,
        "plan": tenant.plan,
        "plan_name": tier["name"],
        "billing_model": "starter" if starter_group else "per_unit",
        "billable": billable,
        "units_total": len(sensors),
        "seats_total": tier["max_sensors"],
        "line_items": lines,
        "monthly_total_usd": mrr,
        "annual_total_usd": round(mrr * 12, 2),
        # The same estate paid yearly: ten months for twelve.
        "yearly_total_usd": yearly_price(mrr),
        "yearly_label": yearly_label(),
        "effective_monthly_usd": 0.0 if not billable else mrr,
        "currency": "USD",
        "note": (
            "Trial estates are priced but not charged; the figure shows what "
            "the estate would cost on a paid contract."
            if not billable
            else (f"Starter: one flat price for one location and up to "
                  f"{PLAN_TIERS[STARTER_PLAN]['max_sensors']} units."
                  if starter_group
                  else "Priced from the sensors currently registered.")
        ),
        "generated_at": iso(utc_now()),
    }


def chain_discount_line(counts: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """The invoice line for the chain discount, or None below the first tier.

    `counts` is what each trade is billed on; only the per-location trades
    count, and only their location prices are discounted.
    """
    places = sum(n for vertical, n in counts.items() if per_site(vertical))
    total = sum(unit_price(vertical) * n for vertical, n in counts.items()
                if per_site(vertical))
    discount = chain_discount(total, places)
    if discount["amount_usd"] <= 0:
        return None
    return {
        "vertical": None,
        "industry": "Chain discount",
        "unit": "location",
        "units": places,
        "unit_price_usd": -round(discount["amount_usd"] / places, 2),
        "line_total_usd": -discount["amount_usd"],
        "chain_discount": True,
        "percent": discount["percent"],
        "capped_at": discount["capped_at"],
        "description": chain_line_description(discount),
    }


def extra_units_line(vertical: str, extra: Dict[str, Any]) -> Dict[str, Any]:
    """The invoice line for units past the allowance at a location.

    Its own line, so the location line keeps reading "2 locations" at the
    location price and the customer sees exactly what the extra units add.
    """
    count = extra["units"]
    cap = units_per_location(vertical)
    description = (f"{count} extra unit{'' if count == 1 else 's'} past "
                   f"{cap} at a {PRICE_BOOK[vertical]['unit']}, "
                   f"${EXTRA_UNIT_MONTHLY_USD:,.0f} each")
    if extra["held"]:
        description += (
            f", held so no {PRICE_BOOK[vertical]['unit']} costs more than "
            f"its old ${OLD_LOCATION_PRICE_USD[vertical]:,.0f}")
    return {
        "vertical": vertical,
        "industry": INDUSTRY_PROFILES[vertical]["name"],
        "unit": "extra unit",
        "units": count,
        "unit_price_usd": EXTRA_UNIT_MONTHLY_USD,
        "line_total_usd": extra["amount_usd"],
        "extra_units": True,
        "description": description,
    }


def rate_card_monthly(sensors: Iterable["Sensor"]) -> float:
    """What these sensors cost a month on the rate card, ignoring the plan.

    Used to price the part of a period after a business moves off Starter
    (contracts.arrears_lines), from the units it had at that moment.
    """
    by_vertical: Dict[str, List[Any]] = {}
    for sensor in sensors:
        if sensor.industry_vertical in PRICE_BOOK:
            by_vertical.setdefault(sensor.industry_vertical, []).append(sensor)
    total = 0.0
    counts: Dict[str, int] = {}
    for vertical, group in by_vertical.items():
        count = len(branches(group)) if per_site(vertical) else len(group)
        counts[vertical] = count
        total += unit_price(vertical) * count
        if per_site(vertical):
            total += extra_units_charge(vertical, location_unit_counts(group))["amount_usd"]
    chain = chain_discount_line(counts)
    if chain is not None:
        total += chain["line_total_usd"]
    return round(total, 2)


def chain_factor(counts: Dict[str, int]) -> float:
    """What share of the location price this estate pays after the chain
    discount: 0.8 for a twenty-location chain, 1.0 below five. Used to
    price a part period for a location added mid-month at the rate the
    estate is actually billed."""
    total = sum(unit_price(v) * n for v, n in counts.items() if per_site(v))
    if total <= 0:
        return 1.0
    places = sum(n for v, n in counts.items() if per_site(v))
    return chain_discount(total, places)["after_usd"] / total


def build_roi(tenant: Tenant, days: int) -> Dict[str, Any]:
    """Weigh what the subscription costs against what it caught.

    A prevented failure is counted only where that vertical has a supplied
    loss figure. Incidents in the other verticals are still listed, marked
    unquantified, so the total is never inflated by a number nobody gave us.
    """
    from datetime import timedelta

    since = utc_now() - timedelta(days=days)
    incidents = STORE.incidents_for(tenant.tenant_id, since=since)
    subscription = build_subscription(tenant)

    quantified: List[Dict[str, Any]] = []
    unquantified: List[Dict[str, Any]] = []

    for incident in incidents:
        entry = PRICE_BOOK[incident.industry_vertical]
        row = {
            "incident_id": incident.incident_id,
            "sensor_id": incident.sensor_id,
            "industry": INDUSTRY_PROFILES[incident.industry_vertical]["name"],
            "catastrophe": incident.catastrophe,
            "opened_at": iso(incident.opened_at),
            "answered": incident.acknowledged_at is not None,
            "loss_avoided_usd": entry["loss_avoided_usd"],
        }
        (quantified if entry["loss_avoided_usd"] else unquantified).append(row)

    # Only an answered incident is a prevented loss. One nobody responded to
    # is a warning that went unheeded, and claiming it as a save would be a
    # lie the customer can check.
    answered = [row for row in quantified if row["answered"]]
    protected = round(sum(row["loss_avoided_usd"] for row in answered), 2)
    period_cost = round(subscription["monthly_total_usd"] * (days / 30.0), 2)

    return {
        "tenant_id": tenant.tenant_id,
        "company_name": tenant.company_name,
        "period_days": days,
        "incidents_total": len(incidents),
        "incidents_answered": sum(1 for i in incidents if i.acknowledged_at),
        "quantified_saves": len(answered),
        "unquantified_saves": len(unquantified),
        "loss_avoided_usd": protected,
        "subscription_cost_usd": period_cost,
        "net_usd": round(protected - period_cost, 2),
        "return_multiple": (
            round(protected / period_cost, 1) if period_cost > 0 and protected else None
        ),
        "detail": quantified + unquantified,
        "note": (
            "Counts only incidents a person answered, in verticals with a "
            "supplied loss figure. Verticals without one are listed but "
            "excluded from the total rather than estimated."
        ),
    }


@router.get("/pricing")
def price_book():
    """The public price list: the published rates, highest first, then the
    trades priced by quote, which carry no figure (`monthly_usd` null)."""
    rows = [
        {
            "vertical": vertical,
            "industry": INDUSTRY_PROFILES[vertical]["name"],
            "unit": entry["unit"],
            "monthly_usd": public_price(vertical),
            "custom_pricing": custom_quote(vertical),
            "price_label": public_price_label(vertical),
            "units_per_location": units_per_location(vertical),
            "extra_unit_usd": (EXTRA_UNIT_MONTHLY_USD if per_site(vertical)
                               else None),
            "pitch": entry["pitch"],
            "protects_against": INDUSTRY_PROFILES[vertical]["catastrophe"],
            "loss_avoided_usd": entry["loss_avoided_usd"],
        }
        for vertical, entry in sorted(
            PRICE_BOOK.items(),
            key=lambda kv: (custom_quote(kv[0]),
                            -(public_price(kv[0]) or 0.0),
                            INDUSTRY_PROFILES[kv[0]]["name"]))
    ]
    return {
        "count": len(rows),
        "currency": "USD",
        "billing": (
            "Restaurants and pharmacies: per location, per month, for up to "
            f"{UNITS_PER_LOCATION} units there, and ${EXTRA_UNIT_MONTHLY_USD:,.0f} "
            "a month for each unit past that. Every other trade: "
            f"{CUSTOM_PRICE_LABEL.lower()}. No setup fee. "
            + chain_words() + " " + yearly_terms()["headline"] + "."),
        "yearly": yearly_terms(),
        "chain_discounts": chain_tiers("restaurant"),
        "plans": [
            {
                "plan": key,
                "name": tier["name"],
                "max_units": tier["max_sensors"],
                "max_locations": tier.get("max_locations"),
                "term_days": tier["term_days"],
                "charged": key != "trial",
                # A flat price only where the plan has one: Starter. The
                # others are priced by the rate card below.
                "monthly_usd": STARTER_MONTHLY_USD if key == STARTER_PLAN else None,
            }
            for key, tier in PLAN_TIERS.items()
        ],
        "starter": starter_terms(),
        "pricing": rows,
    }


def chain_words() -> str:
    """'Chain discounts: 10% off each location from 5 locations, 20% off
    from 20.' Read from CHAIN_DISCOUNT_TIERS."""
    parts = [f"{off:g}% off each location from {start} locations"
             if index == 0 else f"{off:g}% off from {start}"
             for index, (start, off) in enumerate(CHAIN_DISCOUNT_TIERS)]
    return "Chain discounts for restaurants and pharmacies: " + ", ".join(parts) + "."


@router.get("")
def subscription(tenant: Tenant = Depends(require_tenant)):
    """What this estate costs a month, itemised by vertical."""
    return build_subscription(tenant)


@router.get("/roi")
def roi(days: int = 30, tenant: Tenant = Depends(require_tenant)):
    """Loss avoided against subscription cost over a period."""
    days = max(1, min(days, 365))
    return build_roi(tenant, days)


@router.get("/add-ons")
def list_add_ons(tenant: Tenant = Depends(require_tenant)):
    """The add-on catalogue, priced for this estate."""
    units = STORE.seat_count(tenant.tenant_id)
    places = max(locations_billed(tenant), 1)
    return {
        "count": len(ADD_ONS),
        "units": units,
        "add_ons": [
            {
                "key": key,
                **entry,
                "monthly_for_this_estate_usd": add_on_price(key, units, places),
                "on_your_plan": (tenant.plan != STARTER_PLAN
                                 or key in STARTER_ADD_ONS),
            }
            for key, entry in ADD_ONS.items()
        ],
        "note": (
            "Every add-on is a fixed monthly fee. None is a share of what "
            "the customer saved: a quiet year would then pay nothing for the "
            "same standing obligation."
        ),
    }


@router.get("/deal")
def full_deal(
    years: int = Query(1, ge=1, le=MAX_CONTRACT_YEARS),
    annual_prepay: bool = Query(
        False, description=(
            "Pay yearly: each year up front, ten months' price for twelve."),
    ),
    include_add_ons: str = Query(
        "", description="Comma-separated add-on keys, e.g. vault"
    ),
    tenant: Tenant = Depends(require_tenant),
):
    """The whole deal: subscription, add-ons, setup, term and escalator.

    Everything a signature page needs in one figure, so nobody discovers an
    escalator after the fact — which is the only way that term survives a
    renewal conversation. The setup block is still here, at $0: there is
    no setup fee, and the deal says so rather than leaving it unsaid.
    """
    subscription = build_subscription(tenant)
    units = subscription["units_total"]
    base_monthly = subscription["monthly_total_usd"]

    wanted = [
        key.strip()
        for key in (include_add_ons or "").split(",")
        if key.strip()
    ]
    unknown = [key for key in wanted if key not in ADD_ONS]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown add-on(s) {unknown}. Allowed: {list(ADD_ONS)}",
        )

    if tenant.plan == STARTER_PLAN:
        refused = [key for key in wanted if key not in STARTER_ADD_ONS]
        if refused:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Starter takes only {[ADD_ONS[k]['name'] for k in STARTER_ADD_ONS]}. "
                    "Move to Growth for the others."),
            )
    places = locations_billed(tenant)
    add_on_lines = [
        {
            "key": key,
            "name": ADD_ONS[key]["name"],
            "basis": ADD_ONS[key]["basis"],
            "monthly_usd": add_on_price(key, units, places),
        }
        for key in wanted
    ]
    add_on_monthly = round(sum(line["monthly_usd"] for line in add_on_lines), 2)
    monthly = round(base_monthly + add_on_monthly, 2)

    sites = len(STORE.sites_for(tenant.tenant_id))
    # An estate with no sites recorded is still commissioned somewhere; one
    # location is the floor rather than a free installation.
    billable_sites = max(sites, 1) if units else 0
    fee = setup_fee_per_site(tenant)
    setup = round(fee * billable_sites, 2)

    schedule = escalated_schedule(monthly, years)
    # Paying yearly is every year of the term up front at ten months for
    # twelve, as the contract bills it (contracts.term_schedule), so each
    # year carries its own two free months and the total is what is paid.
    for row in schedule:
        multiplier = row.pop("multiplier")
        discount = (round(row["annual_usd"] - yearly_price(monthly * multiplier), 2)
                    if annual_prepay else 0.0)
        row["prepay_discount_usd"] = discount
        row["payable_usd"] = round(row["annual_usd"] - discount, 2)
    total_contract = round(sum(row["payable_usd"] for row in schedule), 2)
    total_list = round(sum(row["annual_usd"] for row in schedule), 2)

    year_one = schedule[0]["annual_usd"]
    prepay_discount = schedule[0]["prepay_discount_usd"]

    return {
        "company_name": tenant.company_name,
        "units": units,
        "sites": sites,
        "subscription_monthly_usd": base_monthly,
        "add_ons": add_on_lines,
        "add_ons_monthly_usd": add_on_monthly,
        "monthly_usd": monthly,
        "setup": {
            "per_site_usd": fee,
            "sites_billed": billable_sites,
            "one_time_usd": setup,
            "note": "No setup fee." if not setup else (
                "Charged once, on the first bill."),
        },
        "term": {
            "years": years,
            "escalator_percent": ANNUAL_ESCALATOR_PERCENT if years > 1 else 0.0,
            "schedule": schedule,
            "total_contract_value_usd": total_contract,
            # The same term at twelve monthly bills a year, before the
            # yearly discount; equal to the total when paying monthly.
            "total_list_value_usd": total_list,
        },
        "annual_prepay": {
            "elected": annual_prepay,
            "months_charged": YEARLY_MONTHS_CHARGED,
            "months_free": YEARLY_MONTHS_FREE,
            "label": yearly_label(),
            "discount_percent": YEARLY_DISCOUNT_PERCENT,
            "discount_usd": prepay_discount,
            "term_discount_usd": round(total_list - total_contract, 2),
            "year_one_due_usd": round(year_one - prepay_discount + setup, 2),
        },
        "first_invoice_usd": round(
            (year_one - prepay_discount + setup)
            if annual_prepay
            else monthly + setup,
            2,
        ),
        "total_first_year_usd": round(year_one - prepay_discount + setup, 2),
        "cancellation": CANCELLATION_RULE,
        "currency": "USD",
        "generated_at": iso(utc_now()),
    }
