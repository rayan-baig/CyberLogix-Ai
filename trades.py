"""Short pages for more trades that keep stock cold.

industries.py has one page per vertical the product is priced for. Many
trades that need exactly this product are not a vertical of their own: a
florist, a brewery, an ice-cream shop, a butcher, a school kitchen, a
vaccine clinic, a grocery store. Each gets a short page here, at
/for/<slug>, in its own words: the risk in that trade, what CyberLogix
does about it, and the price that applies.

The price is read from pricing.py, never written here. A food business
signs up on the restaurant plan, so its page shows the restaurant rate
(and Starter, the small-shop plan, which is restaurant-only). A vaccine
clinic signs up on the pharmacy plan. A trade that is neither is priced
by quote, and its page says "Custom pricing — ask for a quote" with a way
to talk to us, the way pricing.py says every other trade is priced.

Rendered on the server, so a search engine reads the whole page, with a
title and a description of its own. No made-up statistics, no
testimonials, no other company named: every line is either what the
product does or a plain description of the failure.
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

# Per trade: the page's own words. `plan` is the vertical a business in
# this trade signs up on, and so the price it pays; None is priced by quote.
TRADES: Dict[str, Dict[str, Any]] = {
    "florists": {
        "name": "Florists",
        "plan": None,
        "title": "Flower cooler alarms for florists",
        "description": ("A text, then a call on Growth, when your flower cooler "
                        "stays too warm or starts to freeze, day or night. Temperature "
                        "alarms for florists, with every reading kept."),
        "lede": "We watch your flower coolers, and wake somebody up when one goes wrong.",
        "risk": (
            "Cut flowers are kept just above freezing. If a cooler stops "
            "cooling overnight, or ices up, the stock for a wedding or a week "
            "of orders can wilt or brown before anyone opens the door in the "
            "morning. There is rarely time to buy it all again."
        ),
        "asset": "cooler",
    },
    "breweries": {
        "name": "Breweries",
        "plan": None,
        "title": "Cold room and keg cooler alarms for breweries",
        "description": ("Temperature alarms for brewery cold rooms and keg "
                        "coolers: a text when one stays too warm, then a call, "
                        "and the record of every reading."),
        "lede": "We watch your cold rooms and keg coolers, all night.",
        "risk": (
            "Finished beer is stored cold, and a cold room or keg cooler that "
            "fails over a weekend means beer that has sat warm for days. Some "
            "of it may still be fine; you will want to know how long and how "
            "warm before you decide what to sell."
        ),
        "asset": "cold room",
    },
    "ice-cream-shops": {
        "name": "Ice-cream shops",
        "plan": "restaurant",
        "title": "Freezer alarms for ice-cream shops",
        "description": ("A text, then a call on Growth, when a dipping cabinet or "
                        "storage freezer stays too warm, day or night. Freezer alarms "
                        "for ice-cream shops."),
        "lede": "We watch your freezers and dipping cabinets, and call you if one fails.",
        "risk": (
            "Ice cream that softens and freezes again is spoiled for texture, "
            "even if it never looked melted. A storage freezer or dipping "
            "cabinet that fails after closing can cost you every tub in it by "
            "the time you open."
        ),
        "asset": "freezer",
    },
    "butchers": {
        "name": "Butchers",
        "plan": "restaurant",
        "title": "Cold room and display fridge alarms for butchers",
        "description": ("Temperature alarms for butchers' cold rooms, freezers "
                        "and display counters: a text when one stays too warm, "
                        "then a call, and a record ready for the inspector."),
        "lede": "We watch your cold room, freezers and counters, day and night.",
        "risk": (
            "Meat has to stay cold from delivery to the counter. A cold room "
            "that fails over a weekend means stock thrown away, and an "
            "inspector will ask to see your temperature records for the days "
            "around it."
        ),
        "asset": "cold room",
    },
    "school-cafeterias": {
        "name": "School cafeterias",
        "plan": "restaurant",
        "title": "Freezer and fridge alarms for school cafeterias",
        "description": ("A text, then a call on Growth, when a school kitchen's "
                        "freezer or fridge stays too warm, including over weekends and "
                        "holidays. Every reading kept for your records."),
        "lede": "We watch the kitchen's fridges and freezers, even when school is out.",
        "risk": (
            "School kitchens close for weekends and holidays, which is when a "
            "freezer can fail with nobody in the building. The first person "
            "to notice is often the cook on Monday morning, with lunch to "
            "serve and a freezer of food to throw away."
        ),
        "asset": "freezer",
    },
    "vaccine-clinics": {
        "name": "Vaccine clinics",
        "plan": "pharmacy",
        "title": "Vaccine fridge alarms for clinics",
        "description": ("Vaccine fridge monitoring for clinics: a text, then a call, "
                        "when a fridge stays out of its range, and a continuous "
                        "temperature record for your vaccine programme."),
        "lede": "We watch your vaccine fridges, and alert somebody when one drifts out of range.",
        "risk": (
            "Vaccine fridges have to stay inside a narrow range. Too warm or "
            "frozen, and doses may have to be thrown away and patients asked "
            "back. Programmes expect a continuous temperature record, and a "
            "fridge that drifts overnight is noticed the next morning."
        ),
        "asset": "vaccine fridge",
    },
    "grocery-stores": {
        "name": "Grocery stores",
        "plan": "restaurant",
        "title": "Fridge and freezer alarms for grocery stores",
        "description": ("Temperature alarms for grocery store cases, walk-ins and "
                        "freezers: a text when one stays too warm, then a call, "
                        "and every reading kept."),
        "lede": "We watch every case, walk-in and freezer, all night.",
        "risk": (
            "A store runs many cases, walk-ins and freezers through the night. "
            "One that fails after closing is stock in the bin before opening, "
            "and a power cut can take several at once."
        ),
        "asset": "case",
    },
}


def known() -> List[str]:
    return sorted(TRADES)


def price(slug: str) -> Dict[str, Any]:
    """The price that applies to this trade, from pricing.py."""
    from industries import price as vertical_price
    from pricing import STARTER_VERTICALS, starter_terms

    plan: Optional[str] = TRADES[slug]["plan"]
    if plan is None:
        from helpdesk import talk_to_us
        from pricing import CUSTOM_PRICE_LABEL

        talk = talk_to_us()
        return {"price_custom": True, "price_label": CUSTOM_PRICE_LABEL,
                "price_note": "Priced for what you run. No setup fee.",
                "talk_href": talk["href"], "talk_address": talk["address"],
                "starter": ""}
    out = vertical_price(plan)
    starter = ""
    if plan in STARTER_VERTICALS:
        terms = starter_terms()
        starter = (f"One small shop with up to {terms['max_units']} units can take "
                   f"{terms['name']} instead: {terms['monthly_label']} a month, flat.")
    return {**out, "starter": starter}


def index() -> List[Dict[str, str]]:
    return [{"slug": s, "name": TRADES[s]["name"], "lede": TRADES[s]["lede"]}
            for s in known()]


def render(slug: str) -> Dict[str, str]:
    """The page's title, description and body, the body already escaped."""
    from store import KEEP_DETAIL_DAYS, KEEP_READINGS_MONTHS

    trade = TRADES[slug]
    cost = price(slug)
    e = html.escape
    asset = trade["asset"]
    if cost["price_custom"]:
        talk = f'<a class="talk-link" href="{e(cost["talk_href"])}">Talk to us</a>'
        if cost["talk_address"]:
            talk += f' <span class="talk-address">{e(cost["talk_address"])}</span>'
        next_step = f"<p>{talk}</p>"
    else:
        next_step = '<p><a href="/pricing">Work out your price</a></p>'
    starter = f"<p>{e(cost['starter'])}</p>" if cost.get("starter") else ""
    body = f"""
    <div class="eyebrow">{e(trade['name'])}</div>
    <h1 class="lede">{e(trade['lede'])}</h1>
    <p class="standfirst">{e(trade['description'])}</p>

    <section class="slab bad" id="risk">
      <h2>The risk</h2>
      <p>{e(trade['risk'])}</p>
    </section>

    <section class="slab" id="fix">
      <h2>What CyberLogix does</h2>
      <ol class="does">
        <li><b>We watch every {e(asset)}, all night.</b>
          A small sensor reads the temperature every few minutes and sends it
          to us. You set the range each one should stay in.</li>
        <li><b>We text, then phone a person until one answers.</b>
          A text to everyone on call when a {e(asset)} stays out of range.
          On Growth and up, if nobody answers, a real phone call, then the
          next name on the list.</li>
        <li><b>We tell a real fault from a normal one.</b>
          A door opened for a minute alerts nobody: it has to stay out of
          range first. Defrost cycles are learned for each {e(asset)}, so the
          call you get means something.</li>
        <li><b>We keep the record.</b>
          Every reading for {KEEP_DETAIL_DAYS} days, then hourly lows and highs
          for {KEEP_READINGS_MONTHS} months, so you can show what happened and
          when.</li>
      </ol>
    </section>

    <section class="slab" id="cost">
      <h2>What it costs</h2>
      <p class="price-line"><strong>{e(cost['price_label'])}</strong></p>
      <p>{e(cost['price_note'])}</p>
      {starter}
      {next_step}
    </section>

    <div class="foot">
      <a class="btn btn-sm btn-primary u-w-auto" href="/signup">Start watching</a>
      <a class="btn btn-sm" href="/for">Another trade</a>
    </div>"""
    return {"title": f"{trade['title']} | CyberLogix AI",
            "description": trade["description"], "body": body}
