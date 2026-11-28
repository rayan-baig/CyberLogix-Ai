"""The customer help page, and what to do about a sensor gone quiet.

Every answer here is built from the constants the product actually runs
on: the trial's length from the plan tiers, prices from the rate card,
the escalation timings from domain.py, the test alert's limits from
firstday.py. A help page with its own copy of a number is a help page
that will one day tell a customer something the product does not do,
and tests/test_help_page.py checks each figure against its source.

The offline steps live here too, once, because three places show them:
the help page, the console's sensor drawer (through
GET /api/help/sensor-offline) and docs/SENSOR-OFFLINE.md, which a test
keeps in step with this list.
"""

from __future__ import annotations

from brand import APP_SHORT, CHECKLIST_APP, CONTACT_EMAIL
from urllib.parse import quote
import html
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter

router = APIRouter(prefix="/api/help", tags=["Help"])

# Where a customer writes for help. Unset, the page falls back to the
# reply-to address on our mail, and failing that says where else to look
# rather than inventing an address.
# The setting is CYBERLOGIX_SUPPORT_EMAIL, read in brand.py with the names.
SUPPORT_EMAIL = CONTACT_EMAIL


def support_address() -> str:
    """The address customers are told to write to, or "" if none is set."""
    from mail import MAIL_REPLY_TO

    return SUPPORT_EMAIL or MAIL_REPLY_TO


# What the help page calls the trades with a published price.
TRADE_WORDS = {"restaurant": "Restaurants", "pharmacy": "Pharmacies"}


def _trade_name(vertical: str) -> str:
    from domain import INDUSTRY_PROFILES

    return str(INDUSTRY_PROFILES[vertical]["name"])


def talk_to_us() -> Dict[str, str]:
    """Where a "Talk to us" link goes, for a trade priced by quote.

    There is no demo-booking page, so it is a mail to the support address,
    with the address also printed as text a reader can select and copy
    (a mailto link does nothing on a computer with no mail app). With no
    address set, it is the help page's "How do I get help?".
    """
    address = support_address()
    if address:
        return {"href": f"mailto:{address}?subject=" + quote(f"A quote for {APP_SHORT}"),
                "address": address}
    return {"href": "/help#help", "address": ""}


def talk_to_us_html(text: str = "Talk to us") -> str:
    """The link and the selectable address, escaped, for a page."""
    contact = talk_to_us()
    link = (f'<a class="talk-link" href="{html.escape(contact["href"])}">'
            f'{html.escape(text)}</a>')
    if contact["address"]:
        link += f' <span class="talk-address">{html.escape(contact["address"])}</span>'
    return link


def _money(amount: float) -> str:
    """Whole dollars as "$149", anything else to the cent: "$119.20"."""
    return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"


def _minutes(values: Tuple[int, ...]) -> str:
    """(1, 5, 15) -> "1, 5 and 15"."""
    words = [str(v) for v in values]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


# --- a sensor shows as offline ---------------------------------------------


def offline_steps() -> List[Dict[str, str]]:
    """What to check, in the order it is most likely to be the answer.

    Plain words for the person standing in front of the fridge. Each step
    is something the product can actually see or change, so it names the
    place in the console where the answer shows.
    """
    from records import Sensor

    low = f"{Sensor.LOW_BATTERY_PERCENT:g}%"
    return [
        {
            "title": "Check it has power",
            "detail": (
                "Look at the sensor itself. Is it plugged in, or are its "
                "batteries in and charged? The sensor's card shows its "
                f"battery level when it sends one; at {low} or less it says "
                "to replace it. Power cuts take sensors and fridges down "
                "together, so check the fridge too."
            ),
        },
        {
            "title": "Check the gateway or Wi-Fi",
            "detail": (
                "Most sensors talk through a small hub (a gateway) or your "
                "Wi-Fi. If several sensors at one place went quiet at the "
                "same time, it is almost always this. Make sure the gateway "
                "and the router have power and their lights are on. Turn "
                "them off and on again if they are not."
            ),
        },
        {
            "title": "Check the signal",
            "detail": (
                "The card shows signal strength when the sensor sends it. "
                "A walk-in's metal walls block radio. Move the gateway "
                "closer, or out of a metal cupboard, and keep the walk-in "
                "door shut while you test."
            ),
        },
        {
            "title": "Did you make a new key for it?",
            "detail": (
                "Pressing New key on a sensor's card stops the old key "
                "working straight away. The sensor needs the new key put "
                "into its settings before it can report again."
            ),
        },
        {
            "title": "Sensors from another maker",
            "detail": (
                "If the sensor sends through its maker's app or cloud, open "
                "that app and check the sensor shows there. In the console, "
                "the card Sensor maker's cloud says if we could not "
                "reach the maker's service, and why."
            ),
        },
        {
            "title": "Check the account is paid up",
            "detail": (
                "When a trial or a paid term ends, the sensors are still "
                "watched for a grace period. After that, readings are "
                "turned away until the account moves to a paid plan, and "
                "every sensor shows as offline. A banner at the top of the "
                "console says when this is the case."
            ),
        },
        {
            "title": "Still offline?",
            "detail": (
                "Until it reports again, nobody is watching what is inside "
                "that fridge. Check it by hand with a thermometer and write "
                "the reading down. Then get in touch and give the sensor's "
                "name: the help page says how."
            ),
        },
    ]


def offline_summary() -> str:
    """The one paragraph above the steps: what offline means and what we do."""
    from domain import SENSOR_OFFLINE_AFTER_MINUTES

    return (
        f"A sensor shows as offline when we have heard nothing from it for "
        f"{SENSOR_OFFLINE_AFTER_MINUTES} minutes. We text everyone on call "
        "once when that happens, because a sensor that cannot report cannot "
        "warn you. As soon as it sends a reading again, it shows as online."
    )


@router.get("/sensor-offline")
def sensor_offline_help():
    """The offline troubleshooting steps, for the console's sensor drawer.

    Public, and holds nothing about any business: it is the same list the
    help page prints.
    """
    return {
        "summary": offline_summary(),
        "steps": offline_steps(),
        "page": "/help#sensor-offline",
    }


# --- the questions -----------------------------------------------------------


def faq() -> List[Dict[str, Any]]:
    """Each question, its anchor, and its answer as paragraphs of HTML.

    Everything interpolated is escaped. The figures are read here, at
    call time, so a test that changes a constant sees the page change.
    """
    from auth import LICENCE_GRACE_DAYS
    from costs import TRIAL_CALLS_TOTAL
    from patterns import DEFROST_MAX_MINUTES
    from domain import (
        ALERT_AT_ONCE_DEGREES_F,
        DISPATCH_SLA_SECONDS,
        PLAN_TIERS,
        SENSOR_OFFLINE_AFTER_MINUTES,
        SMS_RETRY_AFTER_MINUTES,
        VOICE_ESCALATION_GRACE_MINUTES,
    )
    from domain import wait_words as _wait_words
    from firstday import TEST_ALERT_COOLDOWN_SECONDS, TEST_ALERTS_PER_DAY
    from pricing import (
        ADD_ONS,
        CANCELLATION_RULE,
        CUSTOM_PRICE_LABEL,
        YEARLY_MONTHS_CHARGED,
        YEARLY_MONTHS_COVERED,
        YEARLY_RENEWAL_REMINDER_DAYS,
        EXTRA_UNIT_MONTHLY_USD,
        PRICE_BOOK,
        REFERRAL_REWARDS_PER_YEAR,
        REFERRAL_TRIAL_DAYS,
        SETUP_FEE_PER_SITE_USD,
        UNITS_PER_LOCATION,
        chain_example,
        chain_words,
        custom_quote,
        starter_terms,
        yearly_label,
    )
    from sitewide import settings as site_wide_settings
    from store import KEEP_DETAIL_DAYS, KEEP_READINGS_MONTHS

    sw = site_wide_settings()
    e = html.escape
    trial = PLAN_TIERS["trial"]
    with_calls = [t["name"] for t in PLAN_TIERS.values() if t["voice_escalation"]]
    without_calls = [t["name"] for t in PLAN_TIERS.values() if not t["voice_escalation"]]
    published = [v for v in PRICE_BOOK if not custom_quote(v)]
    add_on_names = ", ".join(
        f"{e(a['name'])} ({_money(a['monthly_usd'])} a month"
        + (" per location" if a["basis"] == "per location" else "") + ")"
        for a in ADD_ONS.values())
    starter = starter_terms()
    bundle = next((a for a in ADD_ONS.values() if a.get("billed_while_connected")), None)
    cooldown_minutes = TEST_ALERT_COOLDOWN_SECONDS // 60
    contact = support_address()

    if contact:
        help_answer = [
            f'Email <a href="mailto:{e(contact)}">{e(contact)}</a>. Say which '
            "business you are, and if it is about a sensor, its name.",
        ]
    else:
        help_answer = [
            "Write to us at the address on your latest invoice or "
            "agreement. Say which business you are, and if it is about a "
            "sensor, its name.",
        ]
    help_answer.append(
        "If a page showed an error with a reference code, include the code: "
        "it lets us find exactly what went wrong.")

    # The published rates, in words: "Restaurants: $149 a month per
    # location", read from the rate card. The rest are priced by quote,
    # and the page never names their figures.
    rate_words = "; ".join(
        f"{e(TRADE_WORDS.get(v, _trade_name(v)))}: "
        f"{_money(PRICE_BOOK[v]['monthly_usd'])} a month per {e(PRICE_BOOK[v]['unit'])}"
        for v in sorted(published, key=lambda v: PRICE_BOOK[v]["monthly_usd"]))
    setup_words = ("There is no setup fee." if not SETUP_FEE_PER_SITE_USD
                   else f"A one-time setup fee of {_money(SETUP_FEE_PER_SITE_USD)} "
                        "per site is on the first bill.")

    return [
        {
            "id": "trial",
            "q": "How long is the free trial, and do I need a card?",
            "a": [
                f"The trial lasts {trial['term_days']} days and covers up to "
                f"{trial['max_sensors']} sensors. You do not give us a card "
                "to start it, and nothing is charged while it runs.",
                f"When it ends, your sensors are still watched for "
                f"{LICENCE_GRACE_DAYS} more days. To keep going after that, "
                "the owner picks a paid plan under What you pay, in the "
                "Money tab.",
                f"Started with a friend's referral code? Your trial is "
                f"{REFERRAL_TRIAL_DAYS} days. Your own code is under Bring a "
                "friend, in the Money tab (owners only): when a business that "
                "used it pays its first invoice, you get a month free on one "
                f"location, up to {REFERRAL_REWARDS_PER_YEAR} times a year.",
            ],
        },
        {
            "id": "hardware",
            "q": "Do I need to buy sensors? Do you sell them?",
            "a": [
                "We do not sell sensors. You buy a wireless temperature "
                "sensor for each fridge or freezer from a sensor maker, "
                "and set it to send its readings to us. "
                "<a href=\"/help/sensors#what-you-need\">What you need, "
                "roughly what it costs, and how to set it up</a>.",
            ],
        },
        {
            "id": "price",
            "q": "What does it cost?",
            "a": [
                f"{rate_words}. Each price covers up to "
                f"{UNITS_PER_LOCATION} fridges and freezers at that location; "
                f"each one past {UNITS_PER_LOCATION} at the same location is "
                f"{_money(EXTRA_UNIT_MONTHLY_USD)} a month.",
                f"A small café or shop can take {e(starter['name'])} instead: "
                f"{e(starter['monthly_label'])} a month for one location and "
                f"up to {starter['max_units']} fridges or freezers.",
                f"Every other trade: {e(CUSTOM_PRICE_LABEL.lower())}. "
                f"{talk_to_us_html()}.",
                f"{e(chain_words())} For example, {chain_example()['locations']} "
                f"restaurants pay {_money(chain_example()['each_usd'])} each, "
                f"{_money(chain_example()['monthly_usd'])} a month. It comes "
                "off automatically, on its own line of the invoice.",
                f"{setup_words} The published prices are on the "
                '<a href="/pricing">pricing page</a>, with a calculator that '
                "works out your monthly or yearly price.",
            ],
        },
        {
            "id": "yearly",
            "q": "Can I pay yearly, and what if I cancel?",
            "a": [
                f"Yes. Paying yearly costs {YEARLY_MONTHS_CHARGED} times the "
                f"monthly price and covers {YEARLY_MONTHS_COVERED} months: "
                f"{e(yearly_label())}. It covers everything on the bill, after "
                "any chain discount. You get one invoice for the year and "
                "nothing until it renews, except a prorated charge for the "
                "rest of the year if you add a location or units.",
                "We email "
                + " and ".join(str(d) for d in YEARLY_RENEWAL_REMINDER_DAYS)
                + " days before the year renews. You can switch between "
                "monthly and yearly at renewal: an owner changes it under "
                "Money, Contract, and the change starts when what you have "
                "paid for runs out.",
                f"<b>Canceling:</b> {e(CANCELLATION_RULE)}",
            ],
        },
        {
            "id": "extras",
            "q": "Are there extras, or a share of what you save me?",
            "a": [
                "Never a share of savings. The optional extras are flat "
                f"monthly fees: {add_on_names}.",
                *([f"The {e(bundle['name'])} sends each day's fridge and "
                   f"freezer record to {CHECKLIST_APP}, which fills in its "
                   "temperature-log checks, and includes "
                   f"{e(bundle['includes'])}. Connect it under Setup, "
                   f"{CHECKLIST_APP} connection. It is free during the trial and "
                   "billed while it is connected."] if bundle else []),
                f"Paying yearly makes the extras {e(yearly_label())} too, "
                "like everything else on the bill.",
            ],
        },
        {
            "id": "plans",
            "q": "What is the difference between the plans?",
            "a": [
                "Plans set how many sensors you can run and whether we phone "
                "people. " + " ".join(
                    f"{e(t['name'])}: up to {t['max_sensors']:,} sensors"
                    + (", with phone calls." if t["voice_escalation"]
                       else ", text alerts only.")
                    for t in PLAN_TIERS.values()),
                f"{e(starter['name'])} is {e(starter['monthly_label'])} a month "
                f"for one location, with texts and the site-wide alert. A "
                f"unit past {starter['max_units']}, or a second location, asks "
                "you to move to Growth first; nothing is charged until you do, "
                "and then only from the day you move.",
            ],
        },
        {
            "id": "alerts",
            "q": "What happens when a fridge goes out of range?",
            "a": [
                "Every reading is checked against the safe range for your "
                "trade, or the one you set for that unit. A door opened for "
                "a minute does not alert anybody: a unit has to STAY out of "
                f"range, {_wait_words()}, before we open one problem for it "
                f"and text everyone on call, within {DISPATCH_SLA_SECONDS} "
                "seconds. A reading far past the limit "
                f"({ALERT_AT_ONCE_DEGREES_F:g}°F or more over) is sent at "
                "once. More readings from the same fridge update that problem "
                "instead of sending more texts.",
                "If it comes back to normal before anybody answers, the alert "
                "closes on its own with a note saying so, the people texted "
                "get a short all-clear, and nobody is phoned. You can change "
                "how long a unit waits on its card in the console.",
                "If your team uses Slack, Teams or PagerDuty, a copy goes "
                "there as well.",
            ],
        },
        {
            "id": "escalation",
            "q": "What if nobody answers the text?",
            "a": [
                f"If nobody acknowledges the problem within "
                f"{VOICE_ESCALATION_GRACE_MINUTES} minutes, we phone the "
                "people on call, in the order you set, until one of them "
                "answers and presses 1. Phone calls come with the "
                f"{e(' and '.join(with_calls))} plans; the "
                f"{e(' and '.join(without_calls))} "
                f"plan{'s send' if len(without_calls) > 1 else ' sends'} texts only. "
                f"The free trial includes {TRIAL_CALLS_TOTAL} phone calls in "
                "all (about a minute each), so you can hear what an alert "
                "call sounds like.",
                "If a text fails to send, we try it again "
                f"{_minutes(SMS_RETRY_AFTER_MINUTES)} minutes after the "
                "problem opened.",
            ],
        },
        {
            "id": "test-alert",
            "q": "How do I check the alerts really reach us?",
            "a": [
                "Send a test alert. In the console, open the Team tab, "
                "find Who gets called and press Send a test alert. Everyone "
                "on the list gets a text that starts “TEST” and says nothing "
                "is wrong. On a plan with phone calls, or with one of the "
                "trial's few calls, you can also ring the first person. It "
                "is also the last step of Getting started, at the top of "
                "the console.",
                "A test opens no problem and chases nobody. Owners and "
                f"operators can send one every {cooldown_minutes} minutes, "
                f"up to {TEST_ALERTS_PER_DAY} a day.",
            ],
        },
        {
            "id": "offline",
            "q": "A sensor says offline. What do I do?",
            "a": [
                f"We have not heard from it for {SENSOR_OFFLINE_AFTER_MINUTES} "
                "minutes. Follow the <a href=\"#sensor-offline\">steps "
                "below</a>: power, gateway, signal, key, and account.",
            ],
        },
        {
            "id": "defrost",
            "q": "My freezer warmed up during defrost. Why no alert?",
            "a": [
                "Freezers warm up for a while on a timer to defrost. Once we "
                "have seen a unit do this regularly, a warm spell that comes "
                "on time and is no warmer than usual is recorded but does "
                "not wake anybody. If it lasts much longer than its usual "
                "defrosts, or longer than "
                f"{int(DEFROST_MAX_MINUTES)} minutes at all, or gets warmer "
                "than usual, the alert goes out as normal: a defrost "
                "stretched on purpose, by someone who has taken over the "
                "fridge controls, still alerts.",
            ],
        },
        {
            "id": "site-wide",
            "q": "What if many fridges warm up at once?",
            "a": [
                "When several units at one site start warming at about the "
                f"same time (by default {sw['min_units']} or more, or half of "
                f"the site's units, within {sw['window_minutes']:g} minutes, "
                f"and {sw['min_units_night']} at night), or units at several "
                "of your sites do, we send one urgent site-wide alert on top "
                "of each unit's own. It can mean a power cut, a door left "
                "open, or someone tampering with the fridge controls.",
                "It goes straight away to everyone on call for that site and "
                "to the business's own number, quiet hours or not, and on a "
                "plan with calls we phone every one of them straight away "
                f"rather than after the usual {VOICE_ESCALATION_GRACE_MINUTES} "
                "minutes. Reply 1 "
                "or press Acknowledge to stop it.",
                "We read separate sensors (ones you buy from a sensor maker), "
                "not the fridge's own controls, and never control a fridge, "
                "so we still see the warming if the fridge's own controls "
                "have been taken over, and nobody can use us to change one. We find out "
                "in minutes and call your team; we cannot stop an attack.",
            ],
        },
        {
            "id": "units",
            "q": "Can I see temperatures in Celsius?",
            "a": [
                "Yes. Press the °F button at the top of the console to "
                "switch between Fahrenheit and Celsius. Reports and "
                "documents use the unit you pick.",
            ],
        },
        {
            "id": "export",
            "q": "How do I download our data?",
            "a": [
                "An owner opens the Setup tab, finds Download or delete "
                "everything, and presses Download everything. You get one "
                "file with every record we keep for the business. Passwords "
                "and keys are never included.",
                f"We keep every reading for {KEEP_DETAIL_DAYS} days, then "
                "each hour's lowest, highest and average reading, until it is "
                f"{KEEP_READINGS_MONTHS} months old. The readings around "
                "every alert are kept in full with the alert. The download "
                "holds all of it.",
            ],
        },
        {
            "id": "delete",
            "q": "How do I delete our account and data?",
            "a": [
                "An owner opens the Setup tab, finds Download or delete "
                "everything, types the business's name and their password, "
                "and presses Delete everything. It happens straight away and "
                "cannot be undone, so download a copy first.",
                "Anything owed must be paid first. We keep only the bills "
                "already sent, because the law says we must.",
            ],
        },
        {
            "id": "password",
            "q": "I forgot my password.",
            "a": [
                'On the sign-in screen, choose “Forgotten your password?”, or '
                'go to <a href="/reset">the reset page</a>. We email you a '
                "link to set a new one. An owner can also send you a reset "
                "link from the Team tab.",
            ],
        },
        {
            "id": "help",
            "q": "How do I get help?",
            "a": help_answer,
        },
    ]


def render_faq() -> str:
    """The questions as HTML, each one an expandable section with an anchor."""
    out = []
    for item in faq():
        paragraphs = "".join(f"<p>{p}</p>" for p in item["a"])
        out.append(
            f'<details class="qa" id="{html.escape(item["id"])}">'
            f'<summary>{html.escape(item["q"])}</summary>'
            f'<div class="qa-body">{paragraphs}</div></details>')
    return "\n".join(out)


def _sentence(title: str) -> str:
    """A step's title as a lead-in: a full stop, unless it asks a question."""
    return title if title.endswith("?") else title + "."


def render_offline() -> str:
    """The offline steps as an ordered list, under their summary."""
    steps = "".join(
        f'<li><strong>{html.escape(_sentence(s["title"]))}</strong> '
        f'{html.escape(s["detail"])}</li>'
        for s in offline_steps())
    return (f'<p>{html.escape(offline_summary())}</p>'
            f'<ol class="steps">{steps}</ol>')
