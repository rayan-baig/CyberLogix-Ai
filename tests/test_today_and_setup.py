"""The Simple view tells the truth about live alerts, and setup asks plainly.

The review found /today saying "Everything is fine. There is nothing for
you to do" with an alert open and a call already placed; sign-up listing
"Restaurant or café" and "Restaurants & Cafés" side by side; and a sensor
guide that opened on HTTP headers.
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
SIMPLE_HTML = (STATIC / "simple.html").read_text(encoding="utf-8")
SIMPLE_JS = (STATIC / "simple.js").read_text(encoding="utf-8")
CONSOLE_JS = (STATIC / "console.js").read_text(encoding="utf-8")


# --- /today: "Needs you now" ----------------------------------------------


def test_the_answer_comes_before_any_explanation():
    verdict = SIMPLE_HTML.index('id="verdict"')
    needs = SIMPLE_HTML.index('id="needs-wrap"')
    intro = SIMPLE_HTML.index('id="intro"')
    assert verdict < needs < intro
    assert "Needs you now" in SIMPLE_HTML


def test_a_live_alert_outranks_everything_is_fine():
    body = SIMPLE_JS[SIMPLE_JS.index("function verdict("):]
    live_check = body.index("if (live.length)")
    fine = body.index("Everything is fine")
    assert live_check < fine, "a live alert must be checked before 'Everything is fine'"
    # The two buttons, through the real routes.
    assert "I'm on it" in SIMPLE_JS and "It's fixed" in SIMPLE_JS
    # Through the shared flow the console uses too (static/answer.js).
    answer = (STATIC / "answer.js").read_text(encoding="utf-8")
    assert "/api/voice/acknowledge/" in answer and "/api/voice/resolve/" in answer
    assert "CLXAnswer.onIt(" in SIMPLE_JS and "CLXAnswer.fixed(" in SIMPLE_JS
    # Unit, temperature and how long, by the one formatter.
    assert "has been out of range" in SIMPLE_JS and "CLXWords.outFor(i.out_since" in SIMPLE_JS


def test_the_overview_carries_what_the_card_needs(api, tenant_factory, sensor_factory):
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="MILK-1", vertical="restaurant", kind="fridge")
    opened = api.post("/api/sensor-pulse", headers=headers,
                      json={"sensor_id": "MILK-1", "temperature_fahrenheit": 47.0}).json()
    assert opened["status"] == "CRITICAL_CATASTROPHE_TRIGGERED"
    body = api.get("/api/console/overview", headers=headers).json()
    (alert,) = [i for i in body["incidents"] if i["state"] != "resolved"]
    for field in ("incident_id", "sensor_id", "state", "out_since", "temperature_display",
                  "acknowledged_by", "voice_escalated_at"):
        assert field in alert, field
    assert "site_wide_alerts" in body


def test_the_forecast_is_promised_only_where_the_plan_has_it():
    assert "warming up faster than it should be" not in SIMPLE_HTML
    watch = SIMPLE_JS[SIMPLE_JS.index("function watchWords("):]
    assert watch.index("predictive_forecasting") < watch.index("faster than it should be")


def test_no_all_one_of_your_fridges():
    assert "Your fridge is the right temperature" in SIMPLE_JS
    assert "Your ${noun(1)} is within its limits." in CONSOLE_JS


# --- sign-up: each trade once ---------------------------------------------


def test_each_trade_is_offered_once(api):
    body = api.get("/api/signup/sectors").json()
    covered = {kind["sector"] for kind in body["common"]}
    assert {"restaurant", "pharmacy"} <= covered
    signup_js = (STATIC / "signup.js").read_text(encoding="utf-8")
    assert "!listed.has(sector.key)" in signup_js


# --- the add form: fridge or freezer, required, guessed from the name -------


def test_fridge_or_freezer_is_a_choice_with_no_silent_default():
    assert '`<option value="">Choose one</option>`' in CONSOLE_JS
    assert '$("add-kind").required = kinds.length > 0;' in CONSOLE_JS
    freezer = re.search(r"const FREEZER_WORDS = /(.+)/i;", CONSOLE_JS).group(1)
    fridge = re.search(r"const FRIDGE_WORDS = /(.+)/i;", CONSOLE_JS).group(1)
    for name in ("Walk-in freezer", "Ice cream cabinet", "Gelato case"):
        assert re.search(freezer, name, re.I), name
    for name in ("Milk fridge", "Bar cooler", "Salad chiller"):
        assert not re.search(freezer, name, re.I) and re.search(fridge, name, re.I), name
    assert not re.search(freezer, "Back room", re.I) and not re.search(fridge, "Back room", re.I)


def test_the_kinds_say_how_long_they_wait(api):
    body = api.get("/api/industries").json()
    restaurant = next(i for i in body["industries"] if i["vertical"] == "restaurant")
    waits = {k["kind"]: k["alert_after_minutes"] for k in restaurant["unit_kinds"]}
    assert waits == {"fridge": 20.0, "freezer": 30.0}


# --- the sensor guide: the easy way first ----------------------------------


def test_the_sensor_guide_opens_with_the_no_coding_way(api, monkeypatch):
    import helpdesk

    monkeypatch.setattr(helpdesk, "SUPPORT_EMAIL", "help@example.com")
    page = api.get("/help/sensors").text
    easiest = page.index('id="easiest"')
    tech = page.index("For tech-savvy setups")
    first_guide = page.index('id="native"')
    assert easiest < tech < first_guide
    assert "We&#x27;ll help you set it up" in page or "We'll help you set it up" in page
    assert "mailto:help@example.com?subject=Help%20setting%20up%20a%20sensor" in page
    section = page[easiest:tech]
    for brand in ("Elitech", "Dickson", "Monnit", "SensorPush"):
        assert brand not in section, brand


def test_no_sensor_is_sold_and_none_is_pushed():
    console = (STATIC / "console.html").read_text(encoding="utf-8")
    assert "You do not have to buy ours" not in console
