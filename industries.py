"""One page per industry, in the words that industry actually uses.

The console explains the product. This explains the point of it, twelve
times, to twelve people who have nothing in common except that something
they own has to stay at a temperature.

A restaurant owner and an IVF clinic director both need exactly this
product and neither will read the other's page. "Walk-in" means nothing
in a hangar. "Straw" means nothing in a kitchen. So the profile in
store.py -- which already knows each vertical's name, its likely
catastrophe and what it calls its own equipment -- is joined here to the
part it does not know: what is lost when it goes wrong, who turns up
afterwards asking questions, and what paperwork the people working there
have to hold.

No prices and no invented statistics. Every line is either a fact about
the product or a description of the failure, because a page of numbers
somebody made up is the fastest way to lose the one reader who knows the
industry.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, status

from store import INDUSTRY_PROFILES

# Per vertical: what is being protected, what is lost, who asks for the
# records afterwards, and what the people on shift need to hold.
CONTEXT: Dict[str, Dict[str, Any]] = {
    "restaurant": {
        "protects": "the food in your walk-ins and prep fridges",
        "loses": (
            "A walk-in full of stock, and the next day's service with it. "
            "The freezer does not announce itself: it fails on Friday "
            "night and somebody opens the door on Monday morning."
        ),
        "asks_after": "the health inspector",
        "licences": ["Food handler cards", "Food protection manager",
                     "Allergen awareness", "Alcohol service permits"],
    },
    "pharmacy": {
        "protects": "vaccines, insulin and anything else in the cold chain",
        "loses": (
            "A fridge of vaccine is thousands of doses that cannot be "
            "given, and every one has to be accounted for. The stock is "
            "replaceable. The record of what happened to it is what "
            "decides whether anybody pays for the replacement."
        ),
        "asks_after": "the board of pharmacy, and the manufacturer",
        "licences": ["Pharmacist licence", "Pharmacy technician registration",
                     "Immunisation certification",
                     "Controlled substance registration"],
    },
    "medical_lab": {
        "protects": "blood, samples and reagents",
        "loses": (
            "Samples cannot be recollected. A patient is re-stuck, a "
            "result is late, and for blood products the unit is simply "
            "destroyed. There is no version of this where you buy "
            "another one."
        ),
        "asks_after": "your accrediting body, and the hospital you supply",
        "licences": ["Phlebotomy certification",
                     "CLIA personnel qualification",
                     "Bloodborne pathogen training"],
    },
    "cryostorage": {
        "protects": "embryos, eggs and tissue in liquid nitrogen",
        "loses": (
            "Nothing here is replaceable and nothing here is insurable in "
            "any way that matters to the family it belonged to. This is "
            "the one industry on this list where the word 'loss' is not "
            "a financial term."
        ),
        "asks_after": "your licensing authority, and the families",
        "licences": ["Cryogenic handling certification",
                     "Confined space entry", "Liquid nitrogen safety"],
    },
    "logistics": {
        "protects": "whatever is in the reefer while it is moving",
        "loses": (
            "A rejected load. The receiver refuses it at the dock, the "
            "product is written off, and the argument about whose fault "
            "it was is settled by whoever has the temperature record for "
            "the journey."
        ),
        "asks_after": "the receiver, the shipper and the insurer",
        "licences": ["Commercial driving licence",
                     "DOT medical examiner certificate",
                     "Hazmat endorsement", "Forklift operator certification"],
    },
    "cannabis": {
        "protects": "the grow rooms and the drying rooms",
        "loses": (
            "A crop, and possibly the licence. This is one of the most "
            "closely watched industries there is, and a gap in the "
            "environmental record is a compliance problem before it is a "
            "money problem."
        ),
        "asks_after": "the state regulator",
        "licences": ["Cultivation agent badge", "Responsible vendor training"],
    },
    "wine_and_art": {
        "protects": "the cellar and the stores",
        "loses": (
            "A collection does not spoil loudly. It degrades, quietly, "
            "over a week of the wrong temperature, and nobody finds out "
            "until it is valued or opened. By then the only question is "
            "whether you can prove the conditions it was kept in."
        ),
        "asks_after": "the owner, the insurer and the auction house",
        "licences": ["Handling and conservation training"],
    },
    "cybersecurity": {
        "protects": "the halls, the racks and the rooms they sit in",
        "loses": (
            "Hardware throttles, then fails, then takes whatever was "
            "running on it. The cost is not the server: it is the outage, "
            "and the service credits you owe the customers who were on it."
        ),
        "asks_after": "your customers, under the uptime you promised them",
        "licences": ["Data centre technician certification",
                     "Electrical safety training"],
    },
    "solar_infrastructure": {
        "protects": "battery storage and inverter rooms",
        "loses": (
            "A battery bank run hot loses life permanently and, at the "
            "extreme, is a fire. This is the one on the list where the "
            "temperature is not protecting the product -- the temperature "
            "is the hazard."
        ),
        "asks_after": "the fire authority and your insurer",
        "licences": ["Electrical licence", "Arc flash training",
                     "Working at height"],
    },
    "private_aviation": {
        "protects": "the hangar, and what is parked in it",
        "loses": (
            "Airframes, avionics and paint all have environmental limits, "
            "and the maintenance record is expected to show they were "
            "kept. An aircraft with a gap in its record is worth less "
            "than one without."
        ),
        "asks_after": "the aviation authority, and the next buyer",
        "licences": ["Airframe and powerplant licence",
                     "Inspection authorisation", "Ramp safety certification"],
    },
    "superyacht": {
        "protects": "engine spaces and the provisions store",
        "loses": (
            "An engine room running hot at sea is a problem you cannot "
            "walk away from, and the guest food store is a charter "
            "cancelled. Both happen while everybody is asleep."
        ),
        "asks_after": "the flag state, the charter broker and the owner",
        "licences": ["STCW basic safety training", "Engineering certificate of competency"],
    },
    "country_club": {
        "protects": "the clubhouse kitchens and the wine store",
        "loses": (
            "A members' club is judged on the one night it gets wrong. "
            "The food is replaceable; the Saturday it ruined is a "
            "conversation at the next committee meeting."
        ),
        "asks_after": "the health inspector, and the membership",
        "licences": ["Food handler cards", "Food protection manager",
                     "Alcohol service permit"],
    },
}


def known() -> List[str]:
    """Verticals with both a profile and a page written for them."""
    return sorted(set(INDUSTRY_PROFILES) & set(CONTEXT))


def page(vertical: str) -> Dict[str, Any]:
    """Everything one industry's page needs, joined from both halves."""
    profile = INDUSTRY_PROFILES[vertical]
    extra = CONTEXT[vertical]
    above, below = profile.get("danger_above"), profile.get("danger_below")

    if above is not None and below is not None:
        band = f"between {below}° and {above}°"
    elif above is not None:
        band = f"under {above}°"
    elif below is not None:
        band = f"above {below}°"
    else:
        band = "inside the range you set"

    return {
        "vertical": vertical,
        "name": profile["name"],
        "slogan": profile["slogan"],
        "catastrophe": profile["catastrophe"],
        "asset_noun": profile["asset_noun"],
        "asset_plural": profile["asset_plural"],
        "band": band,
        "unit": profile.get("unit", "°F"),
        "paperwork": profile.get("shortcut_name", ""),
        "paperwork_detail": profile.get("shortcut_description", ""),
        **extra,
    }


def index() -> List[Dict[str, str]]:
    return [
        {"vertical": v, "name": INDUSTRY_PROFILES[v]["name"],
         "slogan": INDUSTRY_PROFILES[v]["slogan"],
         "protects": CONTEXT[v]["protects"]}
        for v in known()
    ]


# --- routes ----------------------------------------------------------------

# Not /api/industries: telemetry.py has owned that since long before
# this, and it serves a different thing to a different caller -- the
# full catalogue with pricing, for the sector selector. Registering the
# same path twice does not fail, it shadows, and the marketing index was
# quietly being served the pricing catalogue instead. It rendered,
# because both happen to carry a name and a slogan, which is exactly why
# nobody would have noticed.
router = APIRouter(prefix="/api/for", tags=["Industry pages"])


@router.get("")
def list_industries():
    """Every industry with a page, for the index.

    Public: this is the front of the product, and a page that asks a
    stranger to sign in before it will say what the thing does is a page
    that does not get read.
    """
    return {"count": len(known()), "industries": index()}


@router.get("/{vertical}")
def one_industry(vertical: str):
    """One industry's page content."""
    if vertical not in CONTEXT or vertical not in INDUSTRY_PROFILES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No page for '{vertical}'. Available: {known()}",
        )
    return page(vertical)
