"""What a strict review of 3.21.0 found on the screens, fixed and pinned.

- Signing in with an alert open landed on the console, with Acknowledge
  about 3,100px down a phone. Now sign-in goes to /today, and the console
  pins a "Needs you now" bar with both buttons on every view.
- /today, the console card and Problems each had their own formatter and
  their own start time. Now one file (static/words.js) says how long, when
  and who, the same way the server does.
- Setup still felt technical: rare trades in the sign-up's first list, a
  café's add form opening on "Data centers — Custom pricing", Sensor ID
  and serial up front, a browser pop-up for the alert wait, "Three steps"
  on sign-up and "Four" in the console.
- A new sensor said BREACHING, NO FORECAST, "tuned" and OFFLINE before its
  first reading.
- The front page was about 10,500px tall on a phone.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from domain import minutes_words

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text(encoding="utf-8")
CONSOLE_HTML = (STATIC / "console.html").read_text(encoding="utf-8")
SIMPLE_JS = (STATIC / "simple.js").read_text(encoding="utf-8")
SIMPLE_HTML = (STATIC / "simple.html").read_text(encoding="utf-8")
WORDS_JS = (STATIC / "words.js").read_text(encoding="utf-8")
ANSWER_JS = (STATIC / "answer.js").read_text(encoding="utf-8")


def _function(source, name):
    start = source.index(f"function {name}(")
    depth, i = 0, source.index("{", start)
    while True:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
        i += 1


# --- one formatter, the server's own rounding --------------------------------


def test_the_one_formatter_runs_and_says_what_the_server_says():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed here (CI has it)")
    result = subprocess.run([node, "--test", "--test-reporter=tap",
                             str(ROOT / "tests/js/words.test.js")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# fail 0" in result.stdout
    line = next(x for x in result.stdout.splitlines() if "MINUTES " in x)
    table = json.loads(line.split("MINUTES ", 1)[1])
    for value, words in table:
        assert words == minutes_words(value), (value, words, minutes_words(value))


def test_every_page_that_says_how_long_loads_the_one_formatter():
    for html in (CONSOLE_HTML, SIMPLE_HTML):
        words = html.index('<script src="/static/words.js"></script>')
        answer = html.index('<script src="/static/answer.js"></script>')
        own = max(html.index('<script src="/static/console.js"></script>') if "console.js" in html
                  else -1, html.index('<script src="/static/simple.js"></script>')
                  if "simple.js" in html else -1)
        assert words < answer < own
    # Neither page keeps a formatter of its own for an alert's duration.
    assert "function howLong(" not in SIMPLE_JS
    assert "minutesWords(i.minutes_open)" not in CONSOLE_JS
    assert "minutesWords(a.minutes_open)" not in CONSOLE_JS
    assert "CLXWords.outFor(i.out_since" in CONSOLE_JS
    assert "CLXWords.outFor(i.out_since" in SIMPLE_JS


def test_the_card_problems_and_today_read_the_one_condition():
    card = _function(CONSOLE_JS, "sensorCard")
    assert "CLXWords.condition(s)" in card
    assert '"breaching"' not in card and "no forecast" not in card and "tuned" not in card
    assert "CLXWords.condition(x)" in _function(CONSOLE_JS, "watchingRows")
    assert "watchingRows(" in _function(CONSOLE_JS, "renderIncidents")
    assert "CLXWords.condition(unit)" in _function(SIMPLE_JS, "stateOf")
    assert "we'll text at" in WORDS_JS.lower()


# --- an open alert is the first thing on screen ------------------------------


def test_sign_in_goes_to_today_while_an_alert_is_live():
    land = _function(CONSOLE_JS, "maybeLandOnSimple")
    assert "liveAlert(d)" in land and 'location.replace("/today")' in land
    # Before the small-account rule, so it holds for every account.
    assert land.index("liveAlert(d)") < land.index("smallAccount(d)")
    assert "state.fromSignIn = true" in CONSOLE_JS
    # Only "All the detail" (?view=full) keeps the console.
    assert land.index("if (asked) return;") < land.index("liveAlert(d)")


def test_the_console_pins_needs_you_now_with_both_buttons():
    assert '<div id="needs-bar" class="needs-bar" role="alert" hidden></div>' in CONSOLE_HTML
    # Outside <main> and above it: pinned under the nav on every tab.
    assert CONSOLE_HTML.index('id="needs-bar"') < CONSOLE_HTML.index("<main>")
    bar = _function(CONSOLE_JS, "renderNeedsBar")
    assert "I'm on it" in bar and "It's fixed" in bar and "Needs you now" in bar
    assert "renderNeedsBar(d);" in _function(CONSOLE_JS, "render")
    css = (STATIC / "theme.css").read_text()
    assert re.search(r"\.needs-bar \{\s*position: sticky;", css)
    assert ".finder { top: calc(var(--nav-h, 62px) + var(--needs-h, 0px)); }" in css


def test_its_fixed_goes_through_one_flow_with_a_note_to_close_anyway():
    # Every "It's fixed" button on both pages goes through answer.js.
    assert "CLXAnswer.fixed(btn.dataset.resolve, btn)" in CONSOLE_JS
    assert "CLXAnswer.fixed(button.dataset.fixed, button)" in SIMPLE_JS
    assert '"still_out_of_range"' in ANSWER_JS
    assert "close_anyway: true, note:" in ANSWER_JS
    assert "Close anyway" in ANSWER_JS and "note.value.trim().length < 3" in ANSWER_JS
    # No markup strings, so nothing typed into the note is ever parsed.
    assert ".innerHTML" not in ANSWER_JS


def test_screens_show_names_not_addresses():
    assert "esc(i.acknowledged_by ||" not in SIMPLE_JS
    assert "CLXWords.person(i.acknowledged_by_name, i.acknowledged_by)" in SIMPLE_JS
    assert "CLXWords.person(i.acknowledged_by_name, i.acknowledged_by)" in CONSOLE_JS
    assert "acknowledged by ${esc(a.acknowledged_by)}" not in CONSOLE_JS


# --- setup ---------------------------------------------------------------------


def test_sign_up_lists_four_plain_choices_then_something_else():
    js = (STATIC / "signup.js").read_text()
    html = (STATIC / "signup.html").read_text()
    assert 'more.textContent = "Something else"' in js
    assert "Other kinds of business" not in js and "optgroup" not in js
    assert '<select id="industry_other" name="industry_other">' in html
    assert 'payload.industry_vertical === OTHER' in js


def test_sign_up_four_choices_are_the_plain_ones(api):
    common = api.get("/api/signup/sectors").json()["common"]
    assert [k["name"] for k in common] == [
        "Restaurant or café", "Shop, grocery or convenience store", "Pharmacy",
        "Clinic (doctor, dentist, vet or vaccines)"]


def test_the_step_count_is_the_same_on_sign_up_the_console_and_the_front_page():
    signup = (STATIC / "signup.html").read_text()
    landing = (STATIC / "index.html").read_text()
    assert "Four short steps after this form." in signup
    assert "Three steps" not in signup
    assert "Four steps." in CONSOLE_HTML
    assert "Four short steps:" in landing


def test_the_add_form_takes_the_trade_and_folds_the_rare_fields():
    assert 'id="add-trade"' in CONSOLE_HTML and 'id="add-trade-change"' in CONSOLE_HTML
    adv = CONSOLE_HTML[CONSOLE_HTML.index('<details class="add-adv"'):]
    adv = adv[:adv.index("</details>")]
    assert 'id="add-id"' in adv and 'id="add-sn"' in adv
    prefer = _function(CONSOLE_JS, "preferSector")
    assert '$("add-vertical-field").hidden = true' in prefer
    assert 'const ADD_FIRST = ["restaurant"' in CONSOLE_JS


def test_the_add_forms_list_never_starts_with_data_centres(api):
    """The order the console sorts the list in, applied to the real list."""
    industries = api.get("/api/industries").json()["industries"]
    first = ["restaurant", "pharmacy", "medical_lab", "country_club"]
    ordered = sorted(industries, key=lambda i: (
        first.index(i["vertical"]) if i["vertical"] in first else 99,
        i["monthly_usd"] is None, i["name"]))
    assert ordered[0]["vertical"] == "restaurant"
    assert ordered[-1]["monthly_usd"] is None


def test_the_alert_wait_and_the_limit_are_changed_on_the_card_not_in_a_pop_up():
    handler = CONSOLE_JS[CONSOLE_JS.index("  if (btn.dataset.tune) {"):
                         CONSOLE_JS.index("  if (btn.dataset.shortcut) {")]
    assert "prompt(" not in handler
    assert 'openUnitEditor(btn.dataset.wait, "wait")' in handler
    editor = _function(CONSOLE_JS, "openUnitEditor")
    assert 'type="number"' in editor and "data-wait-pick" in editor
    assert "data-edit-save" in editor and "data-edit-cancel" in editor
    assert 'class="wait-edit"' in _function(CONSOLE_JS, "sensorCard")


# --- the front page on a phone ----------------------------------------------


def test_the_front_page_folds_each_section_behind_more(api):
    page = api.get("/").text
    # How, what you need, the hack, the trades, the record, the plans.
    assert page.count('<details class="more" data-open-wide>') >= 6
    # The insurer's report is said once, inside the record section.
    assert page.count("<h2>") <= 8
    assert 'id="insurers"' in page
    assert "Show your insurer proof" not in page
