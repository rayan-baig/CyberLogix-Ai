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

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "static" / "pricing.js"


def _options(page):
    return {
        m.group(1): {"rate": float(m.group(2)), "unit": m.group(3), "plural": m.group(4)}
        for m in re.finditer(
            r'<option value="([a-z_]+)" data-rate="([0-9.]+)" '
            r'data-unit="([^"]+)" data-plural="([^"]+)"', page)
    }


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
        assert options[vertical]["rate"] == entry["monthly_usd"], vertical
        assert options[vertical]["unit"] == entry["unit"]
        assert options[vertical]["plural"] == pricing.plural(vertical, 2)
        # And the table row says the same.
        row = re.search(rf'<tr data-vertical="{vertical}">(.*?)</tr>', page).group(1)
        assert f"${entry['monthly_usd']:,.0f}" in row


def test_every_add_on_and_the_discount_are_the_rate_card(api):
    page = api.get("/pricing").text
    assert _add_on_boxes(page) == {k: v["monthly_usd"] for k, v in pricing.ADD_ONS.items()}
    assert (f'data-prepay-discount="{pricing.ANNUAL_PREPAY_DISCOUNT_PERCENT:g}"'
            in page)


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
        "  return { monthly: m, year: calc.yearly(m, c.discount) };\n"
        "})));\n"
    )
    done = subprocess.run([node, "-e", harness], capture_output=True, text=True,
                          timeout=20)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


ESTATES = [
    ({"restaurant": 1}, []),
    ({"restaurant": 3, "pharmacy": 2}, ["vault"]),
    ({"cybersecurity": 7, "superyacht": 1, "wine_and_art": 2}, ["vault", "benchmarks"]),
    ({vertical: 1 for vertical in pricing.PRICE_BOOK}, list(pricing.ADD_ONS)),
    ({"logistics": 40}, ["equipment_intelligence"]),
    ({"medical_lab": 0}, []),
]


def test_the_calculator_and_pricing_py_agree(api):
    """The script's sum, fed the page's own numbers, is pricing.quote_monthly."""
    page = api.get("/pricing").text
    options, boxes = _options(page), _add_on_boxes(page)
    cases = [
        {"lines": [{"rate": options[v]["rate"], "count": n} for v, n in units.items()],
         "addOns": [{"rate": boxes[k]} for k in add_ons],
         "discount": pricing.ANNUAL_PREPAY_DISCOUNT_PERCENT}
        for units, add_ons in ESTATES
    ]
    for (units, add_ons), got in zip(ESTATES, _run_script(cases)):
        expected = pricing.quote_monthly(units, add_ons)
        assert got["monthly"] == pytest.approx(expected, abs=0.001), units
        assert got["year"]["full"] == pytest.approx(expected * 12, abs=0.01)
        assert got["year"]["prepaid"] == pytest.approx(
            expected * 12 * (1 - pricing.ANNUAL_PREPAY_DISCOUNT_PERCENT / 100), abs=0.01)


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
                         "addOns": [], "discount": 0}])[0]["monthly"] == 20


def test_pricing_is_linked_from_the_landing_page_and_listed(api):
    assert 'href="/pricing"' in api.get("/").text
    assert "<loc>http://testserver/pricing</loc>" in api.get("/sitemap.xml").text


def test_the_pricing_page_has_no_inline_style_or_script():
    page = (ROOT / "static" / "pricing.html").read_text()
    assert "<style" not in page and "style=" not in page
    assert "<script>" not in page
