"""The one-page sales sheet says what the code does, for every industry.

A printed sheet outlives the price it quotes: once it is in a drawer in a
kitchen, nobody can correct it. So every figure on it is read from the
code at serve time -- the rate card, the plans, the alarm limits, the
alert timings, and an example alert written by the function that writes
the real ones -- and these hold each one to its source. That it fits one
page of A4 and of Letter is checked in a real browser by
tools/sheet_print.js (docs/TESTING.md).
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

import domain
import pricing
from industries import known

ROOT = Path(__file__).resolve().parent.parent


def _money(amount: float) -> str:
    return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"


def _sheet(api, vertical: str) -> str:
    resp = api.get(f"/for/{vertical}/sheet")
    assert resp.status_code == 200, resp.text
    return resp.text


def _text(page: str) -> str:
    """The sheet's words, as a reader sees them."""
    body = page.split("<main>", 1)[1]
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)))


@pytest.mark.parametrize("vertical", known())
def test_every_industry_has_a_sheet_with_its_own_price(api, vertical):
    words = _text(_sheet(api, vertical))
    rate = pricing.PRICE_BOOK[vertical]
    assert f"{_money(rate['monthly_usd'])} a month per {rate['unit']}" in words
    assert domain.INDUSTRY_PROFILES[vertical]["name"] in words
    assert domain.INDUSTRY_PROFILES[vertical]["slogan"] in words
    # Another trade's price is not on it.
    others = {_money(r["monthly_usd"]) for v, r in pricing.PRICE_BOOK.items()
              if r["monthly_usd"] != rate["monthly_usd"]}
    big = words.split("What it costs", 1)[1].split("Pay a year ahead", 1)[0]
    assert not [o for o in others if o in big]


@pytest.mark.parametrize("vertical", known())
def test_the_alarm_limits_are_the_profiles_own(api, vertical):
    words = _text(_sheet(api, vertical))
    profile = domain.INDUSTRY_PROFILES[vertical]
    for limit in (profile["danger_above"], profile["danger_below"]):
        if limit is None:
            continue
        fahrenheit = f"{round(limit, 1):g}°F"
        celsius = f"{round(domain.to_celsius(limit), 1):g}°C"
        assert f"{fahrenheit} ({celsius})" in words, (vertical, fahrenheit)


@pytest.mark.parametrize("vertical", known())
def test_the_example_alert_is_the_one_the_product_writes(api, vertical):
    from salessheet import example_alert

    words = _text(_sheet(api, vertical))
    alert = example_alert(vertical)
    assert alert["sms"] in words
    assert alert["email_subject"] in words
    assert alert["call"] in words
    # Out of range, in one text message, with the way to answer it.
    assert len(alert["sms"]) <= 160
    assert "Reply 1" in alert["sms"]
    assert ("above the" in alert["sms"]) or ("below the" in alert["sms"])


def test_the_timings_are_the_ones_the_sweep_runs_on(api):
    words = _text(_sheet(api, "restaurant"))
    assert f"within {domain.DISPATCH_SLA_SECONDS} seconds" in words
    assert f"Nobody answers in {domain.VOICE_ESCALATION_GRACE_MINUTES} minutes" in words
    retries = [str(m) for m in domain.SMS_RETRY_AFTER_MINUTES]
    assert f"sent again {', '.join(retries[:-1])} and {retries[-1]} minutes later" in words
    assert f"silent for {domain.SENSOR_OFFLINE_AFTER_MINUTES} minutes" in words


def test_the_plans_terms_and_trial_are_the_codes(api):
    words = _text(_sheet(api, "pharmacy"))
    trial = domain.PLAN_TIERS["trial"]
    assert (f"Free for {trial['term_days']} days on up to "
            f"{trial['max_sensors']} sensors") in words
    assert "No card needed" in words
    assert not trial["voice_escalation"]
    assert "phone calls start on a paid plan" in words
    for tier in domain.PLAN_TIERS.values():
        assert f"{tier['name']} {tier['max_sensors']:,} " in words
    assert f"{pricing.ANNUAL_PREPAY_DISCOUNT_PERCENT:g}% off that year" in words
    assert f"{_money(pricing.SETUP_FEE_PER_SITE_USD)} per site, once" in words
    assert f"rise {pricing.ANNUAL_ESCALATOR_PERCENT:g}% a year" in words
    for add_on in pricing.ADD_ONS.values():
        assert f"{add_on['name']}: {_money(add_on['monthly_usd'])} a month" in words


def test_a_per_location_price_says_so_and_a_per_unit_one_says_so(api):
    assert "however many sensors it has" in _text(_sheet(api, "restaurant"))
    assert pricing.per_site("restaurant")
    words = _text(_sheet(api, "cybersecurity"))
    assert not pricing.per_site("cybersecurity")
    assert "Each sensor is billed as one rack." in words


def test_a_changed_price_or_timing_changes_the_sheet(api, monkeypatch):
    """Read at serve time: there is no copy of a figure to go stale."""
    book = {k: dict(v) for k, v in pricing.PRICE_BOOK.items()}
    book["restaurant"]["monthly_usd"] = 251.0
    monkeypatch.setattr(pricing, "PRICE_BOOK", book)
    monkeypatch.setattr(domain, "VOICE_ESCALATION_GRACE_MINUTES", 7)
    monkeypatch.setattr(pricing, "SETUP_FEE_PER_SITE_USD", 1234.0)
    words = _text(_sheet(api, "restaurant"))
    assert "$251 a month per location" in words
    assert "Nobody answers in 7 minutes" in words
    assert "$1,234 per site" in words


def test_the_sheet_names_this_deployment_and_the_company(api, monkeypatch):
    import main

    monkeypatch.setattr(main, "PUBLIC_BASE_URL", "https://cyberlogix.example.com")
    words = _text(_sheet(api, "logistics"))
    assert "https://cyberlogix.example.com/signup" in words
    assert "https://cyberlogix.example.com/for/logistics" in words
    assert "a product of MTRX Horizyn" in words
    assert f"Version {main.__version__}" in words


def test_the_template_holds_no_figure_of_its_own():
    source = (ROOT / "static" / "sheet.html").read_text(encoding="utf-8")
    body = source.split("<body", 1)[1]
    assert "$" not in body and "°" not in body
    assert not re.search(r"\d", re.sub(r"%%\w+%%", "", body))


def test_no_sheet_for_an_unknown_trade(api):
    assert api.get("/for/not-a-trade/sheet").status_code == 404
    page = api.get("/for/not-a-trade/sheet", headers={"Accept": "text/html"})
    assert page.status_code == 404 and "text/html" in page.headers["content-type"]


def test_the_sheet_is_not_listed_for_search_engines(api):
    resp = api.get("/for/restaurant/sheet")
    assert "noindex" in resp.headers.get("X-Robots-Tag", "")
    assert "/sheet" not in api.get("/sitemap.xml").text


def test_the_industry_page_links_to_its_sheet():
    script = (ROOT / "static" / "industry.js").read_text(encoding="utf-8")
    assert "/for/${esc(d.vertical)}/sheet" in script
