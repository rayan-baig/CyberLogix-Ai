"""The price calculator and pricing.py agree, because one is read from the other.

The /pricing page renders every rate from pricing.py into its markup, and
static/pricing.js reads them back out and adds them up. These tests hold
the three together: the page's numbers are the rate card's, the script
has no price of its own, and the script's sum equals
pricing.quote_monthly -- which is what build_subscription charges for the
same estate.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import pricing
from domain import PLAN_TIERS
from pages import page_source

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "static" / "pricing.js"


def _options(page):
    """Each trade in the calculator: its rate, or None where it is priced
    by quote (data-custom, and no rate in the markup at all)."""
    out = {}
    for m in re.finditer(
            r'<option value="([a-z_]+)" (?:data-rate="([0-9.]+)"|data-custom="yes") '
            r'data-unit="([^"]+)" data-plural="([^"]+)"(?: data-included="(\d+)")?', page):
        out[m.group(1)] = {
            "rate": float(m.group(2)) if m.group(2) else None,
            "unit": m.group(3), "plural": m.group(4),
            "included": int(m.group(5)) if m.group(5) else None,
        }
    return out


def _add_on_boxes(page):
    return {
        m.group(1): float(m.group(2))
        for m in re.finditer(
            r'class="calc-addon" value="([a-z_]+)" data-rate="([0-9.]+)"', page)
    }


def test_the_page_is_public_and_complete(api):
    resp = api.get("/pricing")
    assert resp.status_code == 200
    assert "%%" not in resp.text
    assert 'id="calc"' in resp.text and "/static/pricing.js" in resp.text


def test_every_rate_on_the_page_is_the_rate_card(api):
    page = api.get("/pricing").text
    options = _options(page)
    assert set(options) == set(pricing.PRICE_BOOK)
    for vertical, entry in pricing.PRICE_BOOK.items():
        assert options[vertical]["unit"] == entry["unit"]
        assert options[vertical]["plural"] == pricing.plural(vertical, 2)
        row = re.search(rf'<tr data-vertical="{vertical}"[^>]*>(.*?)</tr>', page).group(1)
        if pricing.custom_quote(vertical):
            # Priced by quote: the words, and no figure anywhere on the page.
            assert options[vertical]["rate"] is None
            assert pricing.CUSTOM_PRICE_LABEL in row
            assert f"${entry['monthly_usd']:,.0f}" not in page, vertical
            assert f'data-rate="{entry["monthly_usd"]!r}"' not in page, vertical
        else:
            assert options[vertical]["rate"] == entry["monthly_usd"], vertical
            assert options[vertical]["included"] == pricing.UNITS_PER_LOCATION
            assert f"${entry['monthly_usd']:,.0f}" in row
            assert (f"up to {pricing.UNITS_PER_LOCATION} units; "
                    f"${pricing.EXTRA_UNIT_MONTHLY_USD:,.0f} a month for each unit past") in row
    assert options["restaurant"]["rate"] == 149.0
    assert options["pharmacy"]["rate"] == 199.0
    assert f'data-extra-rate="{pricing.EXTRA_UNIT_MONTHLY_USD!r}"' in page


def test_a_trade_priced_by_quote_offers_a_way_to_talk_to_us(api, monkeypatch):
    import helpdesk

    monkeypatch.setattr(helpdesk, "SUPPORT_EMAIL", "sales@cyberlogix.example")
    page = api.get("/pricing").text
    assert '<a class="talk-link" href="mailto:sales@cyberlogix.example?subject=' in page
    # The address as text too, so it can be selected and copied.
    assert '<span class="talk-address">sales@cyberlogix.example</span>' in page
    assert 'id="calc-custom" hidden' in page

    monkeypatch.setattr(helpdesk, "SUPPORT_EMAIL", "")
    monkeypatch.setattr("mail.MAIL_REPLY_TO", "")
    page = api.get("/pricing").text
    assert '<a class="talk-link" href="/help#help">Talk to us</a>' in page


def test_every_add_on_and_the_yearly_months_are_the_rate_card(api):
    page = api.get("/pricing").text
    assert _add_on_boxes(page) == {k: v["monthly_usd"] for k, v in pricing.ADD_ONS.items()}
    assert f'data-months-charged="{pricing.YEARLY_MONTHS_CHARGED}"' in page
    assert f'data-months-covered="{pricing.YEARLY_MONTHS_COVERED}"' in page
    tiers = ",".join(f"{a}:{b:g}" for a, b in pricing.CHAIN_DISCOUNT_TIERS)
    assert f'data-chain="{tiers}"' in page
    assert "prepay" not in page


def test_no_setup_fee_and_the_escalator_are_on_the_page(api):
    """The escalator is on a contract's bills (contracts.py), so it is
    stated where a buyer reads the prices; and so is that there is no
    setup fee."""
    page = re.sub(r"\s+", " ", api.get("/pricing").text)
    assert "There is no setup fee on any plan." in page
    assert "No setup fee" in page
    assert "setup charge" not in page and "$1,500" not in page
    assert f"rises {pricing.ANNUAL_ESCALATOR_PERCENT:g}% a year" in page


def test_the_plans_table_is_the_plan_tiers(api):
    page = api.get("/pricing").text
    for tier in PLAN_TIERS.values():
        assert f"<td>{tier['name']}</td><td class=\"num\">{tier['max_sensors']:,}</td>" in page


def test_a_changed_rate_reaches_the_page(api, monkeypatch):
    monkeypatch.setitem(pricing.PRICE_BOOK["restaurant"], "monthly_usd", 321.0)
    assert _options(api.get("/pricing").text)["restaurant"]["rate"] == 321.0


def test_the_script_keeps_no_price_of_its_own():
    """Every price must come from the page. A literal here would drift."""
    source = SCRIPT.read_text()
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.S)
    prices = {entry["monthly_usd"] for entry in pricing.PRICE_BOOK.values()}
    prices |= {entry["monthly_usd"] for entry in pricing.ADD_ONS.values()}
    prices |= {pricing.EXTRA_UNIT_MONTHLY_USD, float(pricing.UNITS_PER_LOCATION)}
    numbers = {float(n) for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", code)}
    assert not numbers & prices, numbers & prices
    assert "dataset.rate" in code


def _run_script(cases):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness = (
        f"const calc = require({json.dumps(str(SCRIPT))});\n"
        f"const cases = {json.dumps(cases)};\n"
        "console.log(JSON.stringify(cases.map((c) => {\n"
        "  const m = calc.monthlyTotal(c.lines, c.addOns);\n"
        "  return { monthly: m, year: calc.yearly(m, c.charged, c.covered) };\n"
        "})));\n"
    )
    done = subprocess.run([node, "-e", harness], capture_output=True, text=True,
                          timeout=20)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


ESTATES = [
    ({"restaurant": 1}, []),
    ({"restaurant": 3, "pharmacy": 2}, ["vault"]),
    ({"pharmacy": 7}, ["vault", "benchmarks"]),
    ({"restaurant": 1}, list(pricing.ADD_ONS)),
    ({"restaurant": 40}, ["equipment_intelligence"]),
    ({"pharmacy": 0}, []),
]


def test_the_calculator_and_pricing_py_agree(api):
    """The script's sum, fed the page's own numbers, is pricing.quote_monthly."""
    page = api.get("/pricing").text
    options, boxes = _options(page), _add_on_boxes(page)
    cases = [
        {"lines": [{"rate": options[v]["rate"], "count": n} for v, n in units.items()],
         "addOns": [{"rate": boxes[k]} for k in add_ons],
         "charged": pricing.YEARLY_MONTHS_CHARGED,
         "covered": pricing.YEARLY_MONTHS_COVERED}
        for units, add_ons in ESTATES
    ]
    for (units, add_ons), got in zip(ESTATES, _run_script(cases)):
        # monthlyTotal is the plain sum, before any chain discount.
        expected = round(sum(pricing.unit_price(v) * n for v, n in units.items())
                         + sum(pricing.ADD_ONS[k]["monthly_usd"] for k in add_ons), 2)
        assert got["monthly"] == pytest.approx(expected, abs=0.001), units
        assert got["year"]["full"] == pytest.approx(expected * 12, abs=0.01)
        assert got["year"]["yearly"] == pytest.approx(pricing.yearly_price(expected), abs=0.01)


def test_quote_monthly_is_what_an_estate_is_billed(api, operator_factory, sensor_factory):
    """The Python half of the agreement: the quote is the invoice's figure."""
    headers, _, _ = operator_factory()
    sensor_factory(headers, sensor_id="RACK-1", vertical="cybersecurity", location="Hall A")
    sensor_factory(headers, sensor_id="RACK-2", vertical="cybersecurity", location="Hall A")
    sensor_factory(headers, sensor_id="PHARM-1", vertical="pharmacy", location="Front")
    billed = api.get("/api/billing", headers=headers).json()["monthly_total_usd"]
    assert billed == pricing.quote_monthly({"cybersecurity": 2, "pharmacy": 1})


def test_bad_counts_count_as_nothing():
    assert _run_script([{"lines": [{"rate": 10, "count": -4}, {"rate": 10, "count": "x"},
                                   {"rate": 10, "count": 2.7}],
                         "addOns": [], "charged": 0, "covered": 0}])[0]["monthly"] == 20


def test_pricing_is_linked_from_the_landing_page_and_listed(api):
    assert 'href="/pricing"' in api.get("/").text
    assert "<loc>http://testserver/pricing</loc>" in api.get("/sitemap.xml").text


def test_the_pricing_page_has_no_inline_style_or_script():
    page = page_source(ROOT / "static" / "pricing.html")
    assert "<style" not in page and "style=" not in page
    assert "<script>" not in page


# ---- units past the allowance, and the trades priced by quote ------------


def _quote(cases):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness = (f"const calc = require({json.dumps(str(SCRIPT))});\n"
               f"console.log(JSON.stringify({json.dumps(cases)}.map((c) => calc.quote(c))));\n")
    done = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


EXTRA_ESTATES = [
    # (locations per trade, units at each location, add-ons, locations for extras)
    ({"restaurant": 1}, {"restaurant": 10}, [], None),
    ({"restaurant": 1}, {"restaurant": 11}, [], None),
    ({"restaurant": 2}, {"restaurant": 14}, ["kitchen_safety_bundle"], 2),
    ({"restaurant": 3, "pharmacy": 1}, {"restaurant": 12, "pharmacy": 25}, ["vault"], 4),
    ({"pharmacy": 2}, {"pharmacy": 3}, [], None),
]


def test_the_calculator_charges_units_past_ten_as_pricing_py_does(api):
    page = api.get("/pricing").text
    options, boxes = _options(page), _add_on_boxes(page)
    extra = float(re.search(r'data-extra-rate="([0-9.]+)"', page).group(1))
    basis = dict(re.findall(r'class="calc-addon" value="([a-z_]+)" data-rate="[0-9.]+" '
                            r'data-name="[^"]*" data-basis="([^"]+)"', page))
    cases = [{
        "starter": None, "extraRate": extra, "locations": locations,
        "lines": [{"rate": options[v]["rate"], "count": n,
                   "included": options[v]["included"], "each": each[v]}
                  for v, n in units.items()],
        "addOns": [{"rate": boxes[k], "basis": basis[k]} for k in add_ons],
    } for units, each, add_ons, locations in EXTRA_ESTATES]
    for (units, each, add_ons, locations), got in zip(EXTRA_ESTATES, _quote(cases)):
        expected = pricing.quote_monthly(units, add_ons, locations=locations,
                                         units_at_each_location=each)
        assert got == pytest.approx(expected, abs=0.001), (units, each)
    got = _quote(cases)
    assert got[0] == 149.0               # ten units: the location price
    assert got[1] == 149.0 + 15.0        # the eleventh is $15
    assert got[4] == 2 * 199.0           # within the allowance


def test_a_trade_priced_by_quote_makes_the_calculator_say_custom(api):
    page = api.get("/pricing").text
    options = _options(page)
    custom = [v for v in pricing.PRICE_BOOK if pricing.custom_quote(v)]
    assert len(custom) == 10
    cases = [
        {"starter": None, "extraRate": 15, "locations": 1,
         "lines": [{"custom": True, "count": 3, "rate": 0}], "addOns": []},
        {"starter": None, "extraRate": 15, "locations": 1,
         "lines": [{"rate": options["restaurant"]["rate"], "count": 1, "included": 10, "each": 4},
                   {"custom": True, "count": 1, "rate": 0}], "addOns": []},
        # A custom row set to none does not stop the published price.
        {"starter": None, "extraRate": 15, "locations": 1,
         "lines": [{"rate": options["restaurant"]["rate"], "count": 1, "included": 10, "each": 4},
                   {"custom": True, "count": 0, "rate": 0}], "addOns": []},
    ]
    assert _quote(cases) == [None, None, 149.0]
    script = SCRIPT.read_text()
    assert "Ask for a quote" in script and 'getElementById("calc-custom")' in script
