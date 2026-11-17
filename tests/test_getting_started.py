"""From sign-up to a first protected fridge: the checklist and empty tabs.

Getting started is three steps -- add your first sensor, add who gets
alerts, send a test alert -- each ticked from what the account holds,
and the card goes once all three are done. Every console tab a new
business has nothing on yet says in one sentence what will appear there
and offers the next step, so no tab is a blank page.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pages import page_source

ROOT = Path(__file__).resolve().parent.parent
CONSOLE_HTML = (ROOT / "static" / "console.html").read_text()
CONSOLE_JS = (ROOT / "static" / "console.js").read_text()
CONSOLE = page_source(ROOT / "static" / "console.html")


def _function(name):
    start = CONSOLE_JS.index(f"function {name}(")
    return CONSOLE_JS[start:CONSOLE_JS.index("\n}\n", start) + 2]


def _sign_up(api):
    resp = api.post("/api/signup", json={
        "company_name": "Corner Bistro", "full_name": "Sam Reyes",
        "email": "sam@corner.example", "password": "correct-horse-battery",
        "contact_phone": "+15550142", "industry_vertical": "restaurant",
    })
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def _checklist(api, owner):
    return api.get("/api/console/overview", headers=owner).json()["first_day"]


# --- the checklist ticks itself ---------------------------------------------


def test_the_three_steps_tick_one_by_one_and_then_it_is_complete(api):
    owner = _sign_up(api)
    seen = []

    def note():
        fd = _checklist(api, owner)
        seen.append((fd["done"], [s["done"] for s in fd["steps"]], fd["complete"]))

    note()
    api.post("/api/licenses/me/sensors", headers=owner, json={
        "location_name": "Walk-in cooler", "sensor_id": "WALK-IN-1",
        "industry_vertical": "restaurant"}).raise_for_status()
    note()
    # "Add me": the console sends the name and mobile given at sign-up.
    me = api.get("/api/console/overview", headers=owner).json()["tenant"]
    api.post("/api/contacts", headers=owner, json={
        "full_name": me["contact_name"], "phone": me["contact_phone"]}).raise_for_status()
    note()
    # No Twilio here: the test is written as it would be sent (a dry run),
    # which is all a server without texts can do, and it counts.
    sent = api.post("/api/contacts/test-alert", headers=owner)
    assert sent.status_code == 200 and sent.json()["incident_opened"] is False
    note()

    assert seen == [
        (0, [False, False, False], False),
        (1, [True, False, False], False),
        (2, [True, True, False], False),
        (3, [True, True, True], True),
    ]


def test_the_overview_carries_what_add_me_needs(api):
    owner = _sign_up(api)
    tenant = api.get("/api/console/overview", headers=owner).json()["tenant"]
    assert tenant["contact_name"] == "Sam Reyes"
    assert tenant["contact_phone"] == "+15550142"


def test_the_card_offers_add_me_and_a_test_straight_from_the_list():
    render = _function("renderFirstDay")
    assert 'data-go="addme"' in render and "t.contact_phone" in render
    assert "data-test-now" in render
    # Every line is the server's; the page decides nothing is done itself.
    assert "step.done" in render and "localStorage" not in render
    # Gone when complete, with one word to say so.
    assert "fd.complete" in render and "card.hidden = true" in render
    assert "Your first fridge is protected" in render


def test_add_me_posts_the_sign_up_contact():
    add = _function("addMe")
    assert '"/api/contacts"' in add
    assert "t.contact_name" in add and "t.contact_phone" in add


def test_the_test_alert_is_one_press_and_says_it_is_safe():
    send = _function("sendTestAlert")
    assert re.search(r'api\("/api/contacts/test-alert"', send)
    assert "confirm(" not in send, "a safe button needs no are-you-sure"
    assert "if (btn.dataset.testNow) sendTestAlert(btn);" in CONSOLE_JS
    box = CONSOLE_HTML[CONSOLE_HTML.index('id="test-alert-box"'):]
    box = box[:box.index("</div>\n          </div>")]
    assert "<b>TEST</b>" in box and "nobody is chased" in box and "safe" in box


def test_the_card_is_called_getting_started():
    assert '<span class="card-title">Getting started</span>' in CONSOLE_HTML
    assert "Your first day" not in CONSOLE_HTML


def test_going_to_a_step_lands_below_the_sticky_bars():
    go = _function("goTo")
    assert "stickyHeight()" in go
    assert "scrollIntoView" not in go


# --- empty tabs ---------------------------------------------------------------


def test_every_tab_but_setup_has_an_empty_sentence():
    groups = set(re.findall(r'data-group="([a-z]+)"', CONSOLE_HTML))
    cases = set(re.findall(r'case "([a-z]+)":', _function("tabEmptyFor")))
    assert groups - {"setup"} <= cases, groups - cases
    # The five a restaurant owner meets first, by what they hold.
    assert {"fridges", "alerts", "proof", "money", "people"} <= cases


def test_the_empty_sentence_has_a_place_on_the_page_and_follows_the_tabs():
    assert 'id="tab-empty"' in CONSOLE_HTML and 'id="tab-empty-act"' in CONSOLE_HTML
    assert "renderTabEmpty();" in _function("applyFinder")
    assert "renderTabEmpty();" in _function("refresh")


def test_every_next_step_button_goes_somewhere():
    keys = set(re.findall(r'\[\["?([a-z]+)"', _function("tabEmptyFor")))
    keys |= set(re.findall(r'\["([a-z]+)", "', _function("tabEmptyFor")))
    go_map = CONSOLE_JS[CONSOLE_JS.index("const GO = {"):]
    go_map = go_map[:go_map.index("};")]
    handled = set(re.findall(r"^\s+([a-z]+):", go_map, re.M))
    handled |= set(re.findall(r'key === "([a-z]+)"', _function("goTo")))
    assert keys and keys <= handled, keys - handled


def test_an_empty_card_never_hides_behind_a_tab():
    """Devices knocking showed, empty and with no button, on the Fridges
    tab: pressing the tab un-hid it whatever its own content said."""
    assert 'id="find-knocking" data-empty="1"' in CONSOLE_HTML
    assert 'card.dataset.empty !== "1"' in _function("applyFinder")
    assert 'card.dataset.empty = seen.count ? "" : "1";' in _function("renderKnocking")


def test_cards_that_were_dead_ends_now_offer_the_next_step():
    assert 'nextSteps([["plans", "See the plans"]])' in _function("renderInvoices")
    assert "nextSteps(" in _function("renderClaims")
    assert "nextSteps(" in _function("loadBenchmarks")
    # A feature not on the plan is named in words, with the way to it.
    unavailable = _function("panelUnavailable")
    assert "FEATURE_WORDS" in unavailable and '"plans"' in unavailable
    assert "Invoices are raised by the contract" not in CONSOLE_HTML


@pytest.mark.skipif(not shutil.which("node"), reason="needs node to run the page's own code")
def test_a_new_business_sees_a_sentence_and_a_button_on_each_tab():
    """Runs the console's own tabEmptyFor on what a brand-new business's
    console holds, and on one that is set up."""
    script = "\n".join([
        "const state = JSON.parse(process.argv[1]);",
        "const noun = (n) => n === 1 ? 'fridge or freezer' : 'fridges and freezers';",
        _function("tabEmptyFor"),
        "const out = {};",
        "for (const tab of ['fridges','alerts','proof','money','people','licences','setup']) {",
        "  out[tab] = tabEmptyFor(tab);",
        "}",
        "console.log(JSON.stringify(out));",
    ])
    new = {"data": {"sensors": [], "incidents": [],
                    "tenant": {"plan": "trial", "contact_phone": "+15550142"}},
           "counts": {"roster": 0, "bills": 0, "licences": 0}}
    run = subprocess.run(["node", "-e", script, json.dumps(new)],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    tabs = json.loads(run.stdout)
    for tab in ("fridges", "alerts", "proof", "money", "people", "licences"):
        entry = tabs[tab]
        assert entry, tab
        # One plain sentence, and at least one button, the first the main one.
        assert ". " not in entry["text"] and entry["text"].endswith("."), entry["text"]
        assert entry["go"] and entry["go"][0][2] is True, tab
    assert tabs["setup"] is None
    assert "free trial" in tabs["money"]["text"]
    assert tabs["people"]["go"][0][0] == "addme"

    set_up = {"data": {"sensors": [{"sensor_id": "W"}],
                       "incidents": [{"incident_id": "INC-1"}],
                       "tenant": {"plan": "starter"}},
              "counts": {"roster": 1, "bills": 1, "licences": 2}}
    run = subprocess.run(["node", "-e", script, json.dumps(set_up)],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert set(json.loads(run.stdout).values()) == {None}


def test_the_plain_view_is_not_fine_with_nothing_watched():
    """"All 0 of your fridges are fine" is what it used to say."""
    simple = (ROOT / "static" / "simple.js").read_text()
    verdict = simple[simple.index("function verdict("):]
    verdict = verdict[:verdict.index("\n}\n")]
    assert "if (!n)" in verdict and "Nothing is being watched yet" in verdict
    assert verdict.index("if (!n)") < verdict.index("Everything is fine")
    assert '<a class="btn btn-sm" href="/console">Add your first sensor</a>' in simple
