"""3.25.0: a calmer console, phone cards that fit, a calculator that picks
the plan, Pilot for visitors, and the small things.

* The full console opened on Everything: forty cards at once. It opens on
  one tab (Alerts when something is not closed, Fridges otherwise). A
  trial saw "At-risk forecast: upgrade to unlock" and "Heading for
  trouble: Not on this plan"; neither is shown on a plan without the
  forecast. The fleet's search, filter and site controls wait until there
  are several units or sites. "Wall" is "TV screen", "Retire" is
  "Remove", "Claim packet" is "Insurance pack".
* At 375px a fridge card's title broke as "Walk- / in / fridge" beside its
  status label; a closed alert's title crowded "was out for 2 minutes".
* The price calculator opened on the rate card ($149 for a café with three
  fridges). It opens on the cheapest plan that fits, and names it
  ("Starter fits you: $59 a month"). No price changed.
* Pilot's welcome on the public pages is a visitor's ("Will it work for my
  café?", "What does it cost?", "See it live"), the tucked-away robot is a
  44px tap target, and a fridge's status label is on its avoid list.
* A wrong password is said under the box; a signed-in visitor's "Sign in"
  on the public pages says "Your console".
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CONSOLE_JS = (STATIC / "console.js").read_text()
CONSOLE_HTML = (STATIC / "console.html").read_text()
THEME = (STATIC / "theme.css").read_text()
PILOT_APP = (STATIC / "pilot-kryozyn.js").read_text()


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    return source[start:source.index("\n}\n", start)]


# --- one tab at a time --------------------------------------------------------

def test_the_full_console_lands_on_one_tab():
    land = _function(CONSOLE_JS, "landOnATab")
    assert 'finder.tab = live ? "alerts" : "fridges";' in land
    # Never over a choice the person made, never on the first day's list
    # or on the TV screen, and once only.
    for guard in ("finder.userTab", "finder.landed", "state.wall", "dayOne(d)",
                  "finder.query.trim()", "finder.reveal.size"):
        assert guard in land
    assert "landOnATab(overview);" in CONSOLE_JS
    assert "finder.userTab = true;" in CONSOLE_JS


def test_no_locked_upsell_tiles():
    assert "upgrade to unlock" not in CONSOLE_JS
    assert ('d.entitlements.predictive_forecasting\n      ? tile("At-risk forecast"'
            in CONSOLE_JS)
    assert ('$("find-what-breaks-next").dataset.empty = '
            'd.entitlements.predictive_forecasting ? "" : "1";') in CONSOLE_JS


def test_fleet_controls_wait_until_there_is_something_to_sort():
    assert "const FLEET_TOOLS_FROM = 6;" in CONSOLE_JS
    for line in ('$("fleet-search").hidden = !many;', '$("fleet-filter").hidden = !many;',
                 '$("fleet-site").hidden = sites < 2;', '$("group-sites").hidden = sites < 2;'):
        assert line in CONSOLE_JS


def test_plain_words_for_the_buttons():
    assert ">TV screen</button>" in CONSOLE_HTML and ">Wall</button>" not in CONSOLE_HTML
    assert "Leave TV screen" in CONSOLE_HTML
    assert ">Remove</button>" in CONSOLE_JS and ">Retire</button>" not in CONSOLE_JS
    assert ">Insurance pack</button>" in CONSOLE_JS
    assert "Claim packet" not in CONSOLE_JS
    assert '<span class="card-title">Insurance packs</span>' in CONSOLE_HTML


# --- phone cards ---------------------------------------------------------------

def test_a_unit_name_keeps_its_hyphenated_words_whole():
    assert "${keepWords(name)}</span>" in CONSOLE_JS
    assert "${keepWords(unitNameFor(i.sensor_id))}</span>" in CONSOLE_JS
    assert ".keep-word { white-space: nowrap; }" in THEME
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = (_function(CONSOLE_JS, "keepWords") + "\n}\n"
              + "const esc = (s) => String(s);\n"
              + 'console.log(keepWords("Walk-in fridge"));')
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
    assert out.stdout.strip() == '<span class="keep-word">Walk-in</span> fridge'


def test_on_a_phone_the_label_has_its_own_row():
    phone = THEME[THEME.rindex("@media (max-width: 560px) {\n  .sensor-top"):]
    assert ".sensor-top { flex-wrap: wrap; flex-direction: column;" in phone
    assert ".sensor-top .sensor-name { flex: 0 0 auto; width: 100%; }" in phone
    assert ".incident-top { flex-direction: column;" in phone


# --- the calculator ------------------------------------------------------------

def _run_pricing(snippet: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = (f"const P = require({json.dumps(str(STATIC / 'pricing.js'))});\n"
              + snippet)
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture()
def terms(api):
    page = api.get("/pricing").text
    form = re.search(r'<form class="calc" id="calc"([^>]*)>', page, re.S).group(1)
    data = dict(re.findall(r'data-([a-z-]+)="([^"]*)"', form))
    return data, page


def test_the_page_carries_starters_terms_and_the_plan_limits(terms):
    data, page = terms
    from domain import PLAN_TIERS
    from pricing import STARTER_MONTHLY_USD, STARTER_VERTICALS

    assert float(data["starter-rate"]) == STARTER_MONTHLY_USD
    assert data["starter-units"] == str(PLAN_TIERS["starter"]["max_sensors"])
    assert data["starter-verticals"] == ",".join(STARTER_VERTICALS)
    assert data["card-plans"] == (
        f'growth:Growth:{PLAN_TIERS["growth"]["max_sensors"]},'
        f'enterprise:Enterprise:{PLAN_TIERS["enterprise"]["max_sensors"]}')
    # The cheapest that fits is the default, and its line is on the page.
    assert re.search(r'name="calc-plan" value="auto" checked', page)
    assert 'id="calc-fit"' in page
    assert "How many pharmacys" not in page and 'data-plural="pharmacies"' in page


def test_a_cafe_with_three_fridges_is_told_starter_fits(terms):
    data, _ = terms
    out = _run_pricing(f"""
const terms = {{ starterRate: {data['starter-rate']}, units: {data['starter-units']},
  verticals: {json.dumps(data['starter-verticals'].split(','))},
  plans: P.parsePlans({json.dumps(data['card-plans'])}) }};
const line = (sector, count, each) => ({{ sector, rate: sector === "restaurant" ? 149 : 199,
  included: 10, count, each }});
const spec = (lines, addOns = []) => ({{ starter: null, lines, addOns, locations: 1,
  extraRate: 15, chain: [[5, 10], [20, 20]] }});
console.log(JSON.stringify([
  P.cheapestPlan(spec([line("restaurant", 1, 3)]), terms),
  P.cheapestPlan(spec([line("restaurant", 1, 4)]), terms),
  P.cheapestPlan(spec([line("restaurant", 2, 3)]), terms),
  P.cheapestPlan(spec([line("pharmacy", 1, 2)]), terms),
  P.cheapestPlan(spec([line("restaurant", 1, 3)], [{{ rate: 99, basis: "flat", starter: "no" }}]), terms),
  P.cheapestPlan(spec([line("restaurant", 60, 1)]), terms),
]));""")
    assert out[0] == {"plan": "starter", "name": "Starter", "monthly": 59}
    assert out[1] == {"plan": "growth", "name": "Growth", "monthly": 149}
    assert out[2]["plan"] == "growth" and out[2]["monthly"] == 298
    assert out[3] == {"plan": "growth", "name": "Growth", "monthly": 199}
    # An extra Starter does not carry rules it out.
    assert out[4]["plan"] == "growth"
    assert out[5]["plan"] == "enterprise"


def test_the_calculator_names_the_plan_above_the_figure():
    js = (STATIC / "pricing.js").read_text()
    assert "`${best.name} fits you: ${money(best.monthly)} a month`" in js
    assert 'const starter = chosenStarter || (mode === "auto" && best.plan === "starter");' in js


def test_no_price_moved():
    """Starter is still $59, a restaurant location $149, a pharmacy $199."""
    from pricing import PRICE_BOOK, STARTER_MONTHLY_USD

    assert STARTER_MONTHLY_USD == 59.0
    assert PRICE_BOOK["restaurant"]["monthly_usd"] == 149.0
    assert PRICE_BOOK["pharmacy"]["monthly_usd"] == 199.0


# --- Pilot ----------------------------------------------------------------------

def test_pilot_greets_a_visitor_on_the_public_pages():
    assert "welcomePills: ownPage ? OWN_PILLS : VISITOR_PILLS," in PILOT_APP
    assert "const ownPage = onConsole || onToday;" in PILOT_APP
    # Run, not read (3.32.0): a visitor's five on a public page, the
    # console's five on the person's own pages.
    for pathname in ("/", "/pricing", "/help"):
        assert _welcome_in_node(pathname) == [("chat", "Will it work for my café?", "answer", True), ("bars", "What does it cost?", "answer", True),
        ("spark", "See it live", "link /demo", True), ("help", "What sensors do I need?", "answer", True),
        ("key", "Add my AI key", "settings", False)], pathname
    for pathname in ("/console", "/today"):
        assert _welcome_in_node(pathname) == [("bars", "Which fridges need me?", "answer", True), ("chat", "Is the food still OK?", "answer", True),
        ("bolt", "How do I connect a sensor?", "answer", True), ("help", "Who gets the texts?", "answer", True),
        ("key", "Add my AI key", "settings", False)], pathname
    # The front page's own chips no longer repeat the welcome.
    front = PILOT_APP[PILOT_APP.index('"/": ['):PILOT_APP.index('"/pricing": [')]
    assert "Will it work for my café?" not in front and "What would it cost me?" not in front


def test_the_welcome_buttons_pass_the_cores_own_check_in_node():
    """Core 1.4.0, 3.32.0: every visitor pill but the key is answered at
    once (the price and the sensors from the real figures) or is a page of
    this site, so none of them waits for a key."""
    pills = _welcome_in_node("/")
    assert [p[1] for p in pills][:3] == ["Will it work for my café?", "What does it cost?", "See it live"]
    assert pills[-1][2] == "settings"
    assert all(kind != "settings" for _i, _l, kind, _m in pills[:-1])
    assert all(icon != "spark" or label == "See it live" for icon, label, _k, _m in pills)


def test_status_labels_are_on_the_avoid_list():
    avoid = PILOT_APP[PILOT_APP.index("avoid: ["):PILOT_APP.index("screen,\n")]
    for selector in ('".sensor-top"', '".incident-top"', '".thing-word"', '".demo-word"',
                     '".badge"'):
        assert selector in avoid


def test_the_tucked_robot_is_a_44px_tap_target():
    """Core 1.3.0 never draws the tucked-away robot under 44px each way
    (it was about 24px on a phone). The app does not shrink it again."""
    mascot = (STATIC / "pilot" / "pilot-mascot.js").read_text()
    assert "TAP_LEAST" in mascot and "keepScale" in mascot
    core = (STATIC / "pilot" / "pilot.css").read_text()
    phone = core[core.index("@media (max-width: 720px)"):]
    scale = float(re.search(r"--pilot-peek-scale: ([\d.]+);", phone).group(1))
    assert 56 * 0.97 * scale >= 44
    # No stylesheet of this app sets the scale (smaller would only make the
    # drawing and the placement disagree).
    for css in STATIC.glob("*.css"):
        assert "--pilot-peek-scale" not in css.read_text(), css.name


# --- the small things -----------------------------------------------------------

def test_a_wrong_password_is_said_under_the_box():
    assert '<p class="field-error" id="si-error" role="alert" hidden></p>' in CONSOLE_HTML
    assert 'aria-describedby="si-error"' in CONSOLE_HTML
    handler = CONSOLE_JS[CONSOLE_JS.index('$("signin-form").addEventListener("submit"'):]
    handler = handler[:handler.index("\n});\n")]
    assert "signInError(err.message);" in handler
    # A toast only when the connection itself failed.
    assert "if (err.offline) toast(err.message" in handler


def test_the_wrong_password_answer_is_one_a_person_can_read(api, operator_factory):
    operator_factory(plan="trial", email="rosa@cafe.example", password="right-password-1")
    r = api.post("/api/accounts/login", json={"email": "rosa@cafe.example",
                                              "password": "wrong-password-1"})
    assert r.status_code == 401
    assert r.json()["detail"] and "Traceback" not in r.text


@pytest.mark.parametrize("path", ["/help", "/help/sensors", "/pricing", "/", "/legal"])
def test_public_pages_load_the_signed_in_script(api, path):
    page = api.get(path).text
    assert re.search(r'<script src="/static/signedin\.js(\?v=[0-9a-f]+)?" defer></script>', page), path


def test_the_signed_in_script_swaps_sign_in_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    js = (STATIC / "signedin.js").read_text()
    assert "fetch(" not in js and "innerHTML" not in js

    def run(session):
        script = f"""
const link = {{ textContent: "Sign in", attrs: {{}}, setAttribute(k, v) {{ this.attrs[k] = v; }} }};
const other = {{ textContent: "Open the console", attrs: {{}}, setAttribute() {{}} }};
globalThis.window = {{ localStorage: {{ getItem: (k) => k === "cyberlogix.session" ? {json.dumps(session)} : null }} }};
globalThis.document = {{ querySelectorAll: (sel) => sel === 'header a[href="/console"]' ? [link, other] : [] }};
{js}
console.log(JSON.stringify([link.textContent, other.textContent]));"""
        out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    assert run("cls_abc") == ["Your console", "Open the console"]
    assert run(None) == ["Sign in", "Open the console"]


def _welcome_in_node(pathname):
    """The config mounted at `pathname` in Node, its welcome pills through
    the core's own cleaning: [(icon, label, kind)], kind "answer", "link
    /path" or the built-in's action (3.32.0: the pills are built in code,
    so they are read by running it, not by pattern)."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    here = Path(__file__).resolve().parent.parent
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = f"""
const vm = require("node:vm"), fs = require("node:fs");
const sb = {{ window: {{}}, document: {{}}, URL }};
vm.runInNewContext(fs.readFileSync({json.dumps(str(here / 'static' / 'pilot' / 'pilot-panel.js'))}, "utf8"), sb);
let given = null;
const path = {json.dumps(pathname)};
vm.runInNewContext(fs.readFileSync({json.dumps(str(here / 'static' / 'pilot-kryozyn.js'))}, "utf8"), {{
  window: {{ Pilot: {{ mount: (c) => {{ given = c; }} }}, location: {{ pathname: path, href: "https://kryozyn.example" + path }},
    localStorage: {{ getItem: () => null }} }},
  document: {{ getElementById: () => ({{ dataset: {{}} }}), title: "" }}, URL }});
const pills = sb.window.PilotPanel.welcomePills(given.welcomePills, "https://kryozyn.example" + path);
console.log(JSON.stringify(pills.map((p) => [p.icon, p.label, p.link ? "link " + new URL(p.link).pathname + new URL(p.link).hash
  : p.answer !== undefined ? "answer" : p.action, Boolean(p.match && p.match.length)])));"""
    out = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr[-1500:]
    return [tuple(row) for row in json.loads(out.stdout)]
