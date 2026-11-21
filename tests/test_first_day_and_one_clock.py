"""A lighter first day, a trial priced as the plan that fits, and one clock.

The round-5 review:

- The first day was heavy: the business key sat in the Add-a-fridge form,
  "Fetch from a sensor cloud" and the trial's "5 phone calls" were each
  said twice, every card was on the page, the sensor-cloud card was shown
  to a café, and a florist or caterer had no choice of their own.
- A one-fridge bakery's trial said "What you pay $149/mo" while sign-up
  said Starter is $59.
- The Fridges tab said 2 for one fridge (it counted the cards in the group).
- Times read "05:50 PM"; the console polled every 20 seconds, so it could
  still say "we'll text at 5:47 PM" after the alert had opened.
- /pricing was about 10,200px tall on a phone.
"""

import re
from pathlib import Path

import pricing
from store import STORE

ROOT = Path(__file__).resolve().parent.parent
CONSOLE_HTML = (ROOT / "static/console.html").read_text()
CONSOLE_JS = (ROOT / "static/console.js").read_text()
WORDS_JS = (ROOT / "static/words.js").read_text()
SIMPLE_JS = (ROOT / "static/simple.js").read_text()


def card(html, card_id):
    start = html.index(f'id="{card_id}"')
    return html[start:html.index("</section>", start)]


# --- the first day -----------------------------------------------------------


def test_the_business_key_is_in_setup_not_in_the_add_a_fridge_form():
    add = card(CONSOLE_HTML, "find-add-to-the-estate")
    key = card(CONSOLE_HTML, "find-business-key")
    assert "business key" not in add.lower()
    assert 'id="find-business-key" data-group="setup"' in CONSOLE_HTML
    assert 'id="biz-key-value"' in key and "data-rekey-business" in key
    # A master key goes to its own card; a sensor's key stays in the form.
    show = CONSOLE_JS[CONSOLE_JS.index("function showIssuedKey"):]
    show = show[:show.index("\n}\n")]
    assert '$("biz-key-value").textContent = key;' in show
    assert "Copy your business key from \\\"Add a fridge" not in CONSOLE_JS


def test_nothing_on_the_first_day_is_said_twice():
    # The sensor-cloud card's paragraph is not repeated under it.
    render = CONSOLE_JS[CONSOLE_JS.index("function renderPollers"):]
    render = render[:render.index("\n}\n")]
    assert '$("poller-body").innerHTML = "";' in render
    assert "seen.broken ? `<div" in render
    # The trial's calls are said once, in the roster card's first line.
    escalation = CONSOLE_JS[CONSOLE_JS.index("function renderEscalation"):]
    escalation = escalation[:escalation.index("\n}\n")]
    assert 'mode === "trial"' not in escalation
    assert "calls.words" in CONSOLE_JS[CONSOLE_JS.index('$("roster-plain")'):][:400]


def test_day_one_is_the_checklist_and_the_fridges_with_the_rest_under_more():
    assert 'const DAY_ONE_CARDS = new Set(["fleet-card"]);' in CONSOLE_JS
    short = CONSOLE_JS[CONSOLE_JS.index("function shortList"):]
    short = short[:short.index("\n}\n")]
    assert "if (dayOne()) return true;" in short       # desktop too, not only a phone
    assert '$("tiles").hidden = dayOne(d);' in CONSOLE_JS
    # A checklist button that leads to a folded card still shows it.
    go = CONSOLE_JS[CONSOLE_JS.index("function goTo"):]
    assert "finder.reveal.add(card.id);" in go[:go.index("\n}\n")]


def test_the_sensor_cloud_card_waits_under_advanced():
    assert 'id="find-vendor-clouds" data-advanced="1"' in CONSOLE_HTML
    first = CONSOLE_JS[CONSOLE_JS.index("const FIRST_CARDS"):]
    assert "find-vendor-clouds" not in first[:first.index(";")]


def test_other_not_listed_signs_up_on_the_food_limits(api):
    offer = api.get("/api/signup/sectors").json()
    assert offer["other"]["key"] == "other"
    assert offer["other"]["name"].startswith("Other / not listed")
    js = (ROOT / "static/signup.js").read_text()
    assert "if (info.other) rest.push(info.other);" in js

    resp = api.post("/api/signup", json={
        "company_name": "Rowe Flowers", "full_name": "Ada Rowe",
        "email": "ada@roweflowers.example", "password": "correct-horse-battery",
        "contact_phone": "+15550199", "industry_vertical": "other"})
    assert resp.status_code == 201, resp.text
    tenant = STORE.get_tenant(resp.json()["tenant"]["tenant_id"])
    assert tenant.industry_vertical == "restaurant"


# --- What you pay on a trial -------------------------------------------------


def test_a_one_fridge_trial_is_shown_starter_not_the_location_price(
        api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory(plan="trial", company_name="Maya's Bakery")
    sensor_factory(headers, sensor_id="WALK-IN", vertical="restaurant", kind="fridge")
    sub = api.get("/api/billing", headers=headers).json()

    after = sub["after_trial"]
    assert after["plan"] == "starter"
    assert after["monthly_total_usd"] == pricing.STARTER_MONTHLY_USD == 59.0
    assert "1 unit fits Starter" in after["words"]
    # Every price is unchanged: the rate card figure is still the rate card.
    assert sub["monthly_total_usd"] == pricing.PRICE_BOOK["restaurant"]["monthly_usd"]
    assert "const after = subscription.after_trial;" in CONSOLE_JS


def test_a_trial_too_big_for_starter_is_shown_the_rate_card(
        api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory(plan="trial")
    for n in range(4):
        sensor_factory(headers, sensor_id=f"F-{n}", vertical="restaurant", kind="fridge")
    assert api.get("/api/billing", headers=headers).json()["after_trial"] is None
    other, _ = tenant_factory(plan="trial", company_name="Ortiz Pharmacy")
    sensor_factory(other, sensor_id="VAX-1", vertical="pharmacy")
    assert api.get("/api/billing", headers=other).json()["after_trial"] is None


# --- the tabs ----------------------------------------------------------------


def test_the_fridges_tab_counts_fridges_not_cards():
    count = CONSOLE_JS[CONSOLE_JS.index("function countTheTabs"):]
    count = count[:count.index("\n}\n")]
    assert "const figure = { fridges: units, alerts: live };" in count
    assert "const n = figure[key];" in count
    assert "card.getAttribute(\"data-group\")" not in count.split("const n = figure[key];")[1]


# --- one clock ---------------------------------------------------------------


def test_no_time_is_written_with_a_padded_hour():
    for name in ("console.js", "simple.js", "answer.js", "proof-report.js"):
        js = (ROOT / "static" / name).read_text()
        assert not re.search(r'hour:\s*"2-digit"', js), name
    assert "function dateTime(iso, opts)" in WORDS_JS
    assert "CLXWords.dateTime(" in CONSOLE_JS


def test_the_pages_ask_again_at_the_alert_time():
    assert "CLXWords.pollDelay(" in CONSOLE_JS
    assert "CLXWords.pollDelay(" in SIMPLE_JS
    assert "setInterval(load, 30000)" not in SIMPLE_JS
    assert "It stayed out, so we are alerting now." in WORDS_JS


# --- /pricing ----------------------------------------------------------------


def test_pricing_folds_its_long_sections(api):
    page = api.get("/pricing").text
    main = page[page.index("<main"):page.index("</main>")]
    folds = re.findall(r'<details class="fold" data-open-wide>\s*<summary>([^<]+)</summary>', main)
    assert len(folds) == 6, folds
    for title in ("Every rate", "Chains pay less for each location", "Optional extras"):
        assert title in folds
    # Said once: the chain and yearly terms are not repeated in the lede.
    lede = main[main.index('<p class="lede">'):main.index("</p>", main.index('<p class="lede">'))]
    assert "Chains pay less" not in lede and "months for the" not in lede
    assert main.count("2 months free") <= 3
    assert 'document.querySelectorAll("details[data-open-wide]")' in (
        ROOT / "static/pricing.js").read_text()
