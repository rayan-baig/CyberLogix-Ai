"""The price book, and what a customer's estate actually costs.

Pricing is per unit, per vertical, per month: a rack is not a reefer truck
and is not priced like one. A customer with three data-hall racks and two
hangar bays is billed 3 x $499 + 2 x $349, and the invoice is derived from
the sensors actually registered rather than from a plan tier.

Plan tiers still exist, but they are about evaluation versus a paid
contract and the seat ceiling that comes with it — they are not what sets
the price.

Rates are set for the pinnacle of each market rather than its volume: an
owner who spends millions a year on a vessel reads a $399 service as a toy,
and prices below the value at risk invite exactly the buyer who will haggle.

`loss_avoided_usd` is the figure the product is sold on: the loss a single
prevented failure represents. Only the verticals where a concrete number
was supplied carry one; the rest are deliberately null rather than guessed,
because an ROI page built on invented figures is worse than one that says
"not quantified".
"""

from __future__ import annotations

import os

from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

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
    from datetime import datetime

    from store import Sensor

router = APIRouter(prefix="/api/billing", tags=["Pricing & Billing"])

PRICE_BOOK: Dict[str, Dict[str, Any]] = {
    "cybersecurity": {
        "unit": "rack",
        "monthly_usd": 899.0,
        "pitch": (
            "Prevents $50,000+ server meltdowns and SLA contract breaches with "
            "predictive forecasting."
        ),
        "loss_avoided_usd": 50000.0,
    },
    "private_aviation": {
        "unit": "bay",
        "monthly_usd": 1999.0,
        "pitch": (
            "Protects multi-million-dollar avionics and jet components from "
            "high-humidity moisture corrosion."
        ),
        "loss_avoided_usd": None,
    },
    "superyacht": {
        "unit": "vessel",
        "monthly_usd": 4999.0,
        "pitch": (
            "Guards engine rooms against thermal fires and secures $150k charter "
            "guest food freezers at sea."
        ),
        "loss_avoided_usd": 150000.0,
    },
    "solar_infrastructure": {
        "unit": "enclosure",
        "monthly_usd": 899.0,
        "pitch": (
            "Monitors thermal runaway in commercial battery banks to protect "
            "against catastrophic fire liability."
        ),
        "loss_avoided_usd": None,
    },
    "medical_lab": {
        "unit": "vault",
        "monthly_usd": 1499.0,
        "pitch": (
            "Mandated by strict OSHA/FDA chain-of-custody rules; includes "
            "automated audit report generation."
        ),
        "loss_avoided_usd": None,
    },
    "cryostorage": {
        "unit": "tank",
        "monthly_usd": 2999.0,
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
        "pitch": (
            "Safeguards high-revenue holiday dining inventory (Thanksgiving, "
            "Mother's Day) from compressor failure."
        ),
        "loss_avoided_usd": None,
    },
    "logistics": {
        "unit": "reefer truck",
        "monthly_usd": 749.0,
        "pitch": (
            "Generates tamper-proof transit handover temperature passes to "
            "eliminate dock cargo rejection disputes."
        ),
        "loss_avoided_usd": None,
    },
    "wine_and_art": {
        "unit": "cellar",
        "monthly_usd": 2499.0,
        "pitch": (
            "Certifies unbroken storage conditions for a collection whose "
            "value depends entirely on provenance being unquestioned."
        ),
        "loss_avoided_usd": 250000.0,
    },
    "pharmacy": {
        "unit": "pharmacy",
        "monthly_usd": 1299.0,
        "pitch": (
            "Meets the continuous-monitoring requirement for vaccine storage "
            "and produces the audit record on demand."
        ),
        "loss_avoided_usd": 40000.0,
    },
    "cannabis": {
        "unit": "cultivation room",
        "monthly_usd": 1199.0,
        "pitch": (
            "Documents environmental control to the standard a state licence "
            "renewal is judged against."
        ),
        "loss_avoided_usd": 60000.0,
    },
    "restaurant": {
        "unit": "location",
        # Per shop, however many thermometers are inside it. This was $999
        # and counted sensors while calling each one a "location", so one
        # sandwich shop with a freezer, a cooler and a reach-in was invoiced
        # "3 locations" at $2,997 a month. $249 per location is the price
        # set for a single site; `per_site` is what makes it per location.
        "monthly_usd": 249.0,
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


# ---- contract terms ---------------------------------------------------
#
# The rate card is only half of what an estate is worth. These are the
# terms that decide the other half, and the ones customers rarely
# negotiate out because each is standard practice in enterprise software.

# Charged once per site at commissioning. It funds acquisition cost on the
# day of signature rather than eleven months later, and a customer who has
# paid to be installed does not churn casually.
SETUP_FEE_PER_SITE_USD = 1500.0

# Paying a year up front removes the collections problem on that unit and
# fixes the customer for twelve months. Whether it is worth ten percent
# is a different question, and `margin.prepay_verdict()` answers it with
# arithmetic rather than the assertion this comment used to make: the
# discount buys the bad debt it avoids, the card fees it avoids, and
# whatever holding the cash early is worth. At the current defaults that
# is 5.9%, so ten is four points of pure gift — the largest cost in the
# business and the only one nobody sends an invoice for.
#
# Left at ten, and made a setting instead of a constant, because a
# smaller discount is only a saving if the customer still prepays. That
# is a commercial call, not a code change.
ANNUAL_PREPAY_DISCOUNT_PERCENT = float(
    os.environ.get("CYBERLOGIX_ANNUAL_PREPAY_DISCOUNT_PERCENT", "10.0")
)

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
    "kitchen_safety_bundle": {
        "name": "Kitchen Safety Bundle",
        "basis": "per location",
        "monthly_usd": 29.0,
        "included_in_trial": True,
        "billed_while_connected": True,
        "description": (
            "Each day's fridge and freezer record sent to OmniArmor, which "
            "fills in its temperature-log checks for you. Billed while the "
            "connection is on; free during the trial."
        ),
    },
}

# The bundle's key, named once so the sender and the bill agree on it.
BUNDLE_ADD_ON = "kitchen_safety_bundle"


def bundle_price_label() -> str:
    """The bundle's add-on price as words, for the MTRX Horizyn bundle page."""
    entry = ADD_ONS[BUNDLE_ADD_ON]
    return f"+${entry['monthly_usd']:,.0f} a month per location"


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

    The same rule as the commissioning fee: the sites set up, and one
    location for an estate that has units but never set up sites. Starter
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
STARTER_MONTHLY_USD = 79.0
# A cafe or shop's fridges and freezers, on the restaurant limits. A
# vaccine fridge or a server rack is a different job at a different
# price, and a $79 plan that took either would undercut the rate card by
# more than the plan is worth.
STARTER_VERTICALS = ("restaurant",)
# The extras that fit a one-location plan. The per-estate extras (the
# vault, benchmarks, equipment intelligence) cost more than the plan and
# are for estates; moving to Growth opens them.
STARTER_ADD_ONS = (BUNDLE_ADD_ON,)
# No commissioning fee on Starter. The per-site fee funds an engineer
# placing every unit and proving the alert path; a cafe plugging in three
# sensors itself would meet a $1,500 charge on a $79 plan, which is the
# surprise this plan is meant not to have.
STARTER_SETUP_FEE_PER_SITE_USD = 0.0


def setup_fee_per_site(tenant: Tenant) -> float:
    """The one-time commissioning fee per site for this business's plan."""
    if tenant.plan == STARTER_PLAN:
        return STARTER_SETUP_FEE_PER_SITE_USD
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
    """Year-by-year contract value with the escalator applied."""
    schedule = []
    rate = monthly_usd
    for year in range(1, max(1, min(years, MAX_CONTRACT_YEARS)) + 1):
        if year > 1:
            rate = round(rate * (1 + escalator_percent / 100.0), 2)
        schedule.append(
            {
                "year": year,
                "monthly_usd": rate,
                "annual_usd": round(rate * 12, 2),
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
    """
    vertical, entry = min(PRICE_BOOK.items(), key=lambda kv: kv[1]["monthly_usd"])
    trial = PLAN_TIERS["trial"]
    return {
        "from_usd": entry["monthly_usd"],
        "from_label": f"${entry['monthly_usd']:,.0f}",
        "from_unit": entry["unit"],
        "from_vertical": vertical,
        "trial_days": trial["term_days"],
        "trial_units": trial["max_sensors"],
        "industries": len(PRICE_BOOK),
        # The flat plan for a small cafe or shop, beside the rate card.
        "starter_usd": STARTER_MONTHLY_USD,
        "starter_label": f"${STARTER_MONTHLY_USD:,.0f}",
        "starter_units": PLAN_TIERS[STARTER_PLAN]["max_sensors"],
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
    """
    rates = [
        {
            "vertical": vertical,
            "industry": INDUSTRY_PROFILES[vertical]["name"],
            "unit": entry["unit"],
            "unit_plural": plural(vertical, 2),
            "monthly_usd": entry["monthly_usd"],
            "per_site": per_site(vertical),
        }
        for vertical, entry in sorted(
            PRICE_BOOK.items(),
            key=lambda kv: INDUSTRY_PROFILES[kv[0]]["name"])
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
        "annual_prepay_discount_percent": ANNUAL_PREPAY_DISCOUNT_PERCENT,
    }


def quote_monthly(units: Dict[str, int], add_ons: Iterable[str] = (),
                  plan: Optional[str] = None,
                  locations: Optional[int] = None) -> float:
    """The monthly price of an estate described as {vertical: units}.

    The calculator's sum, in Python: what build_subscription charges for
    the same estate on the rate card, plus add-ons. The page's JavaScript
    is tested against this.

    `plan="starter"` is the flat Starter price for one location instead of
    the rate card, with only the extras Starter takes. `locations` counts
    for an extra priced per location; left out, it is the restaurant
    locations in the estate, or one.
    """
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
            "effective_monthly_usd": 0.0 if not billable else contract.monthly_usd,
            "rate_card_equivalent_usd": mrr,
            "currency": "USD",
            "note": (
                f"Billed on volume contract {contract.account_id} "
                f"({contract.tier_label}); covers every sensor inside an "
                "enrolled branch."
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
    for vertical, group in by_vertical.items():
        count = len(branches(group)) if per_site(vertical) else len(group)
        total += unit_price(vertical) * count
    return round(total, 2)


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
    """The public price list, highest-value vertical first."""
    rows = [
        {
            "vertical": vertical,
            "industry": INDUSTRY_PROFILES[vertical]["name"],
            "unit": entry["unit"],
            "monthly_usd": entry["monthly_usd"],
            "price_label": f"${entry['monthly_usd']:,.0f} / {entry['unit']} / month",
            "pitch": entry["pitch"],
            "protects_against": INDUSTRY_PROFILES[vertical]["catastrophe"],
            "loss_avoided_usd": entry["loss_avoided_usd"],
        }
        for vertical, entry in sorted(
            PRICE_BOOK.items(), key=lambda kv: -kv[1]["monthly_usd"]
        )
    ]
    return {
        "count": len(rows),
        "currency": "USD",
        "billing": "Per unit, per month. Priced from registered sensors.",
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
        False, description="Pay year one up front for a discount."
    ),
    include_add_ons: str = Query(
        "", description="Comma-separated add-on keys, e.g. vault"
    ),
    tenant: Tenant = Depends(require_tenant),
):
    """The whole deal: subscription, add-ons, setup, term and escalator.

    Everything a signature page needs in one figure, so nobody discovers a
    setup fee or an escalator after the fact — which is the only way those
    terms survive a renewal conversation.
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
    total_contract = round(sum(row["annual_usd"] for row in schedule), 2)

    year_one = schedule[0]["annual_usd"]
    prepay_discount = (
        round(year_one * ANNUAL_PREPAY_DISCOUNT_PERCENT / 100.0, 2)
        if annual_prepay
        else 0.0
    )

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
            "note": (
                "Charged once, at commissioning. Covers placing every unit, "
                "proving the alert path end to end, and training the roster."
            ),
        },
        "term": {
            "years": years,
            "escalator_percent": ANNUAL_ESCALATOR_PERCENT if years > 1 else 0.0,
            "schedule": schedule,
            "total_contract_value_usd": total_contract,
        },
        "annual_prepay": {
            "elected": annual_prepay,
            "discount_percent": ANNUAL_PREPAY_DISCOUNT_PERCENT,
            "discount_usd": prepay_discount,
            "year_one_due_usd": round(year_one - prepay_discount + setup, 2),
        },
        "first_invoice_usd": round(
            (year_one - prepay_discount + setup)
            if annual_prepay
            else monthly + setup,
            2,
        ),
        "total_first_year_usd": round(year_one - prepay_discount + setup, 2),
        "currency": "USD",
        "generated_at": iso(utc_now()),
    }
