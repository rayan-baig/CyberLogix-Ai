"""What an honest product review found, held to the code (3.19.0).

A café added "Kitchen / Walk-in cooler" and a healthy 37°F reading opened a
critical alarm; the pages implied we sell hardware; the checklist called a
fridge "protected" before it had reported; calls were promised on plans
that never place one; one tap committed a café to a twelve-month plan; a
false alarm was counted as $15,000 of loss avoided. One test or more per
finding, each written so it fails on the code the review saw.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

import costs
import domain
from store import INDUSTRY_PROFILES, STORE

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text(encoding="utf-8")
CONSOLE_HTML = (STATIC / "console.html").read_text(encoding="utf-8")


def _signup(api, email="rosie@cafe.example", company="Rosie's Cafe", kind="restaurant"):
    resp = api.post("/api/signup", json={
        "company_name": company, "full_name": "Rosie Test", "email": email,
        "password": "correct-horse-battery", "contact_phone": "+15550142",
        "industry_vertical": kind})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["tenant"]["tenant_id"]


def _add(api, owner, sensor_id="WALKIN-1", kind=None, vertical="restaurant"):
    body = {"sensor_id": sensor_id, "industry_vertical": vertical,
            "location_name": "Kitchen / Walk-in cooler"}
    if kind:
        body["unit_kind"] = kind
    resp = api.post("/api/licenses/me/sensors", headers=owner, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _pulse(api, owner, sensor_id, temp):
    resp = api.post("/api/sensor-pulse", headers=owner,
                    json={"sensor_id": sensor_id, "temperature_fahrenheit": temp})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- 1. healthy fridges do not alarm ----------------------------------------


def test_a_walk_in_cooler_at_37f_does_not_alarm(api):
    """The review's exact case: Restaurants, a walk-in cooler, 37°F."""
    owner, _ = _signup(api)
    _add(api, owner, kind="fridge")

    assert _pulse(api, owner, "WALKIN-1", 37.0)["status"] == "nominal"
    # Over the limit waits to see if it stays over (a door, a delivery);
    # far over it alerts at once.
    assert _pulse(api, owner, "WALKIN-1", 42.0)["status"] == "WATCHING"
    assert _pulse(api, owner, "WALKIN-1", 57.0)["status"].startswith("CRITICAL")


def test_a_freezer_gets_a_freezers_limit(api):
    owner, _ = _signup(api)
    made = _add(api, owner, sensor_id="FRZ-1", kind="freezer")

    assert made["sensor"]["danger_above"] == 10.0
    assert _pulse(api, owner, "FRZ-1", 2.0)["status"] == "nominal"
    assert _pulse(api, owner, "FRZ-1", 15.0)["status"] == "WATCHING"
    assert _pulse(api, owner, "FRZ-1", 26.0)["status"].startswith("CRITICAL")


def test_with_no_answer_a_restaurant_unit_gets_a_fridges_limit(api):
    owner, _ = _signup(api)
    assert _add(api, owner)["sensor"]["danger_above"] == 41.0
    assert INDUSTRY_PROFILES["restaurant"]["danger_above"] == 41.0
    assert INDUSTRY_PROFILES["country_club"]["danger_above"] == 41.0


def test_an_answer_the_sector_does_not_offer_is_refused(api):
    owner, _ = _signup(api)
    resp = api.post("/api/licenses/me/sensors", headers=owner, json={
        "sensor_id": "X-1", "industry_vertical": "restaurant",
        "location_name": "Somewhere", "unit_kind": "kettle"})
    assert resp.status_code == 400
    assert "fridge" in resp.json()["detail"]


def test_the_sector_list_says_fridge_or_freezer_and_the_form_asks():
    from telemetry import list_industries

    rows = {r["vertical"]: r for r in list_industries()["industries"]}
    assert [k["kind"] for k in rows["restaurant"]["unit_kinds"]] == ["fridge", "freezer"]
    assert rows["cybersecurity"]["unit_kinds"] == []
    assert 'id="add-kind"' in CONSOLE_HTML and "Fridge or freezer?" in CONSOLE_HTML
    assert "unit_kind:" in CONSOLE_JS


def test_an_existing_units_saved_limit_is_not_moved_by_the_new_default(api):
    """A restaurant unit saved before 3.19.0 alarmed over 32°F. It keeps
    32°F, as its own limit, until its owner changes it."""
    owner, _ = _signup(api)
    _add(api, owner)
    row = STORE._db.get("sensor", "WALKIN-1")
    row.pop("defaults_version", None)
    row["override_above"] = None
    STORE._db.put("sensor", "WALKIN-1", row)

    STORE.load()

    sensor = STORE.get_sensor("WALKIN-1")
    assert sensor.override_above == domain.LEGACY_DANGER_ABOVE["restaurant"] == 32.0
    assert sensor.bounds()[0] == 32.0
    # And saved as its own, so it survives the next restart too.
    assert STORE._db.get("sensor", "WALKIN-1")["override_above"] == 32.0


def test_a_new_unit_is_not_pinned_to_the_old_default(api):
    owner, _ = _signup(api)
    _add(api, owner)
    STORE.load()
    assert STORE.get_sensor("WALKIN-1").bounds()[0] == 41.0


def test_the_demo_uses_the_limits_the_product_gives():
    from demo import UNITS

    for unit in UNITS:
        assert unit["limit_f"] == domain.unit_kind_limits("restaurant", unit["kind"])[0]


def test_the_restaurant_page_says_41_for_fridges(api):
    page = api.get("/api/for/restaurant").json()
    assert "under 41°F (fridges)" in page["band"]
    assert "32°F" not in page["band"] + page["fix"]
    js = (STATIC / "industry.js").read_text(encoding="utf-8")
    assert "We format it as" not in js


# --- 2. no hardware implied; what to buy -------------------------------------


@pytest.mark.parametrize("path", ["/", "/pricing", "/help/sensors"])
def test_what_you_need_is_on_the_page(api, path):
    text = api.get(path).text
    assert 'id="what-you-need"' in text
    assert "does not sell sensors" in text
    assert "If the power or internet goes off" in text


def test_nothing_says_we_make_the_sensors():
    for name in ("index.html", "simple.html"):
        text = (STATIC / name).read_text(encoding="utf-8")
        assert "its own separate sensors" not in text
        assert "battery-powered box" not in text


def test_what_you_need_links_to_no_shop():
    from sensor_guides import what_you_need_html

    assert "href=" not in what_you_need_html(2)


def test_help_answers_do_you_sell_sensors():
    import helpdesk

    item = next(i for i in helpdesk.faq() if i["id"] == "hardware")
    assert "We do not sell sensors" in " ".join(item["a"])


# --- 4. calls only where the plan has them; the trial may try a few ---------


def test_calls_are_worded_by_plan(api):
    owner, tenant_id = _signup(api)
    tenant = STORE.get_tenant(tenant_id)
    assert costs.calls_offer(tenant)["mode"] == "trial"
    assert f"{costs.TRIAL_CALLS_TOTAL} phone calls" in costs.calls_offer(tenant)["words"]

    STORE.change_plan(tenant, "starter", extend_term=False)
    offer = costs.calls_offer(tenant)
    assert offer["mode"] == "none" and "not phone calls" in offer["words"]
    preview = api.get("/api/contacts/preview", headers=owner).json()
    assert preview["voice_ladder"] == [] and preview["calls"]["mode"] == "none"

    STORE.change_plan(tenant, "growth", extend_term=False)
    assert costs.calls_offer(tenant)["mode"] == "plan"


def test_the_trial_stops_ringing_after_its_few_calls(api):
    _, tenant_id = _signup(api)
    assert costs.reserve(tenant_id, "voice")[0] is True
    assert costs.calls_allowed(STORE.get_tenant(tenant_id)) is True
    # The rest of the few, used on earlier days of the trial.
    STORE.bump_usage(tenant_id, "voice_calls", costs.TRIAL_CALLS_TOTAL - 1)
    allowed, reason = costs.reserve(tenant_id, "voice")
    assert allowed is False and "have been used" in reason
    assert costs.calls_allowed(STORE.get_tenant(tenant_id)) is False


def test_the_insurers_report_promises_no_calls_on_starter(api):
    import proofreports

    owner, tenant_id = _signup(api)
    tenant = STORE.get_tenant(tenant_id)
    STORE.change_plan(tenant, "starter", extend_term=False)
    api.post("/api/contacts", headers=owner, json={"full_name": "Ana", "phone": "+15550111"})

    who = proofreports._who_is_alerted(tenant, None)
    assert "phone calls go down the list" not in who["rule"]
    assert all("phone" not in p["gets"] for p in who["on_call"])


def test_the_console_and_simple_view_word_calls_from_the_plan():
    simple = (STATIC / "simple.js").read_text(encoding="utf-8")
    assert "entitlements || {}).calls" in simple
    assert 'calls.mode === "none" ? "Who gets alerts" : "Who gets called"' in CONSOLE_JS
    assert "The order we phone people in" not in CONSOLE_HTML


# --- 5. a plan change needs a yes --------------------------------------------


def test_one_tap_no_longer_moves_the_plan(api):
    owner, tenant_id = _signup(api)

    refused = api.post("/api/licenses/me/plan", headers=owner, json={"plan": "growth"})

    assert refused.status_code == 428
    terms = refused.json()["detail"]["terms"]
    assert terms["term_days"] == 365 and "12-month" in terms["term_words"]
    assert "Cancel any time" in terms["cancel_words"]
    assert terms["trial_days_left"] is not None
    assert STORE.get_tenant(tenant_id).plan == "trial"


def test_the_terms_then_a_yes_moves_it(api):
    owner, tenant_id = _signup(api)
    terms = api.get("/api/licenses/me/plan/terms?plan=starter", headers=owner)
    assert terms.status_code == 200
    body = terms.json()
    assert body["name"] == "Starter" and "$59" in body["price_words"]
    assert "days left" in body["trial_words"]

    moved = api.post("/api/licenses/me/plan", headers=owner,
                     json={"plan": "starter", "confirm": True})
    assert moved.status_code == 200, moved.text
    assert STORE.get_tenant(tenant_id).plan == "starter"


def test_the_console_asks_before_it_moves_the_plan():
    handler = CONSOLE_JS[CONSOLE_JS.index('const button = event.target.closest("[data-upgrade]")'):]
    handler = handler[:handler.index("function closePlanConfirm")]
    assert "/api/licenses/me/plan/terms" in handler
    assert '"/api/licenses/me/plan"' not in handler, "a tap still posts the change"
    yes = CONSOLE_JS[CONSOLE_JS.index('$("plan-confirm-yes").addEventListener'):]
    assert "confirm: true" in yes[:900]
    assert 'id="plan-confirm"' in CONSOLE_HTML


# --- 6. a café is not handed the operator's tools ----------------------------


@pytest.mark.parametrize("card", [
    "find-enterprise-tier-calculator", "find-meeting-intelligence", "find-licences",
    "find-people", "find-spend-controls", "find-compliance-vault",
    "find-byod-sensor-webhook"])
def test_operator_cards_wait_under_advanced(card):
    assert re.search(r'<section [^>]*id="%s" data-advanced="1"' % card, CONSOLE_HTML)


def test_delete_everything_is_not_beside_the_add_form():
    add = CONSOLE_HTML.index('id="find-add-to-the-estate"')
    leave = CONSOLE_HTML.index('id="find-leave"')
    about = CONSOLE_HTML.index('id="find-about"')
    assert leave > about > add


def test_a_small_account_opens_on_the_simple_view():
    assert "function maybeLandOnSimple" in CONSOLE_JS
    land = CONSOLE_JS[CONSOLE_JS.index("function maybeLandOnSimple"):]
    land = land[:land.index("\n}\n")]
    assert 'location.replace("/today")' in land and "first_day.complete" in land
    # Leaving aborts the requests in flight; that must not sign anybody out
    # (the browser walk found it: the Simple view opened signed out).
    assert "state.leaving = true" in land
    assert "if (state.leaving) return;" in CONSOLE_JS
    assert "/console?view=full" in (STATIC / "simple.html").read_text(encoding="utf-8")


def test_the_row_forms_are_styled():
    css = (STATIC / "theme.css").read_text(encoding="utf-8")
    assert ".card form .row > input" in css
    assert 'class="btn btn-sm" type="submit">Change password' in CONSOLE_HTML


# --- 7. one "from", and plans that say what each gives ------------------------


def test_every_plan_says_what_it_gives_and_the_extras_cost_extra(api):
    body = api.get("/api/licenses/plans").json()
    lists = [tuple(p["features"]) for p in body["plans"]]
    assert all(lists) and len(set(lists)) == len(lists)
    assert "Optional add-ons" in body["add_ons"]
    hero = api.get("/").text
    assert "From $59 a month" in hero
    assert "plans from ${from}/mo" in CONSOLE_JS


# --- 8. the businesses that actually sign up ----------------------------------


def test_signup_offers_the_common_kinds_first(api):
    sectors = api.get("/api/signup/sectors").json()
    assert [c["key"] for c in sectors["common"]] == ["restaurant", "shop", "pharmacy", "clinic"]
    assert INDUSTRY_PROFILES["cybersecurity"]["name"] == "Data centers"


@pytest.mark.parametrize("kind,sector", [("shop", "restaurant"), ("clinic", "pharmacy")])
def test_a_common_kind_signs_up_as_its_sector(api, kind, sector):
    _, tenant_id = _signup(api, email=f"{kind}@x.example", kind=kind)
    assert STORE.get_tenant(tenant_id).industry_vertical == sector


# --- 9. no "100% online" of nothing -------------------------------------------


def test_a_proof_report_of_a_unit_added_today_shows_hours_not_100_percent(api):
    import proofreports
    from store import utc_now

    owner, tenant_id = _signup(api)
    _add(api, owner)
    _pulse(api, owner, "WALKIN-1", 37.0)
    tenant = STORE.get_tenant(tenant_id)
    body = proofreports.build(tenant, None, utc_now().date().replace(day=1))

    if body["summary"]["hours_watched"] < 1:
        assert body["summary"]["online_percent"] is None
        assert proofreports.online_words(body["summary"]).startswith("Not enough yet")
    assert "hours_watched" in body["summary"]


def test_the_online_figure_always_carries_its_hours():
    import proofreports

    words = proofreports.online_words({"online_percent": 99.5, "hours_watched": 312.0})
    assert words == "99.5% of 312 hours watched"


# --- 10. the smaller things ---------------------------------------------------


def test_no_forecast_request_on_a_plan_without_it():
    fetch = CONSOLE_JS[CONSOLE_JS.index("    forecast: () =>"):]
    fetch = fetch[:fetch.index("\n    claims:")]
    assert "predictive_forecasting" in fetch


def test_the_operator_page_is_not_shown_to_the_public(api):
    assert api.get("/book").status_code == 404
    assert "Platform admin key" not in api.get("/book").text
    assert api.get("/book?sign-in").status_code == 401
    good = "Basic " + base64.b64encode(b"op:test-admin-key").decode()
    bad = "Basic " + base64.b64encode(b"op:wrong").decode()
    assert api.get("/book", headers={"Authorization": bad}).status_code == 404
    assert api.get("/book", headers={"Authorization": good}).status_code == 200


def test_incident_numbers_count_per_account(api):
    first, _ = _signup(api, email="a@x.example", company="A")
    second, _ = _signup(api, email="b@x.example", company="B")
    _add(api, first, sensor_id="A-1")
    _add(api, second, sensor_id="B-1")
    a = _pulse(api, first, "A-1", 60.0)["incident_id"]
    b = _pulse(api, second, "B-1", 60.0)["incident_id"]
    assert re.fullmatch(r"INC-1-[A-Z]{4}", a) and re.fullmatch(r"INC-1-[A-Z]{4}", b)
    assert a != b


def test_an_old_incident_number_still_reads(api):
    from types import SimpleNamespace

    from voice_dispatch import _alert_number

    assert _alert_number(SimpleNamespace(incident_id="INC-000012")) == 12
    assert _alert_number(SimpleNamespace(incident_id="INC-7-QHKX")) == 7


def test_raw_codes_are_words_in_the_console():
    for raw in ("Native pulse", '" (template fallback)"', '"TRIGGERED"'):
        assert raw not in CONSOLE_JS
    assert "function auditWords" in CONSOLE_JS and "function deliveryWords" in CONSOLE_JS


def test_the_demo_banner_follows_mark_it_fixed_at_once():
    demo = (STATIC / "demo.js").read_text(encoding="utf-8")
    fix = demo[demo.index("function fix()"):]
    fix = fix[:fix.index("\n}\n")]
    assert "draw();" in fix


def test_the_landing_page_has_a_phone_menu_and_shows_without_scrolling():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<details class="menu">' in index and 'href="/pricing"' in index
    motion = (STATIC / "motion.css").read_text(encoding="utf-8")
    reveal = motion[motion.index(".motion [data-reveal] {"):]
    reveal = reveal[:reveal.index("}")]
    assert "opacity: 0" not in reveal


def test_the_small_wording(api):
    assert "Claim a seat" not in api.get("/help/sensors").text
    assert "Nobody finds out until seven" in (STATIC / "signup.html").read_text(encoding="utf-8")


def test_the_operator_page_is_not_an_app_shortcut():
    manifest = (STATIC / "manifest.webmanifest").read_text(encoding="utf-8")
    assert '"/book"' not in manifest


def test_unit_kinds_cover_food_and_medicine():
    assert [k["kind"] for k in domain.unit_kinds("pharmacy")] == ["fridge", "freezer"]
    assert domain.unit_kind_limits("pharmacy", "fridge") == (46.0, 36.0)
    assert domain.unit_kinds("superyacht") == []
