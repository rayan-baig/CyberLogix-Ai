"""Short, honest pages for more trades that keep stock cold.

Each renders on the server with its own title and description, says the
risk, what we do and the price that applies (read from pricing.py, or
"Custom pricing — ask for a quote" where that is how the trade is priced),
and is listed in the sitemap. No made-up numbers and no testimonials.
"""

from __future__ import annotations

import html
import re

import pytest

import industries
import pricing
import trades

NEW = ["florists", "breweries", "ice-cream-shops", "butchers", "school-cafeterias",
       "vaccine-clinics", "grocery-stores"]


def test_the_trades_are_the_ones_without_a_page_already():
    assert trades.known() == sorted(NEW)
    # Restaurants, pharmacies and labs already have a page each.
    for existing in ("restaurant", "pharmacy", "medical_lab"):
        assert existing in industries.known()
    assert not set(trades.known()) & set(industries.known())


@pytest.mark.parametrize("slug", NEW)
def test_each_page_renders_with_its_own_title_and_description(api, slug):
    resp = api.get(f"/for/{slug}")
    assert resp.status_code == 200, resp.text
    page = resp.text
    trade = trades.TRADES[slug]
    title = re.search(r"<title>(.*?)</title>", page).group(1)
    assert title == f"{trade['title']} | Kryozyn"
    description = re.search(r'<meta name="description" content="([^"]*)"', page).group(1)
    assert html.unescape(description) == trade["description"]
    assert 50 < len(trade["description"]) < 200
    assert f'<link rel="canonical" href="http://testserver/for/{slug}">' in page
    for section in ("The risk", "What Kryozyn does", "What it costs"):
        assert section in page
    assert "%%" not in page
    # Strict CSP: nothing inline.
    assert "style=" not in page and "<script>" not in page and " on" + "click=" not in page
    assert resp.headers.get("X-Robots-Tag") is None


@pytest.mark.parametrize("slug", NEW)
def test_each_page_quotes_the_price_that_applies(api, slug):
    page = api.get(f"/for/{slug}").text
    plan = trades.TRADES[slug]["plan"]
    if plan is None:
        assert pricing.CUSTOM_PRICE_LABEL in page and "Talk to us" in page
        assert "$" not in page.split("What it costs")[1].split("</section>")[0]
    else:
        assert pricing.CUSTOM_PRICE_LABEL not in page
        assert f"${pricing.PRICE_BOOK[plan]['monthly_usd']:,.0f} a month per" in page


def test_food_trades_mention_starter_and_the_clinic_is_priced_like_a_pharmacy(api):
    assert "$59 a month, flat" in api.get("/for/butchers").text
    clinic = api.get("/for/vaccine-clinics").text
    assert "$199 a month per pharmacy" in clinic and "$59" not in clinic


def test_no_made_up_numbers_or_testimonials(api):
    for slug in NEW:
        page = api.get(f"/for/{slug}").text
        body = html.unescape(page.split('<main id="page">')[1].split("</main>")[0])
        assert not re.search(r"\d+\s?%", body), slug
        for word in ("testimonial", "customers say", "trusted by", "“", "five stars"):
            assert word not in body.lower(), (slug, word)
        # The only figures are the price, the Starter terms and how long
        # readings are kept, each read from the code.
        import store

        figures = set(re.findall(r"\d[\d,.]*", re.sub(r"<[^>]+>", " ", body)))
        kept = {str(store.KEEP_DETAIL_DAYS), str(store.KEEP_READINGS_MONTHS)}
        assert figures <= {"149", "199", "10", "15", "59", "3"} | kept, (slug, figures)


def test_an_unknown_trade_is_a_404(api):
    assert api.get("/for/candle-makers").status_code == 404


def test_the_pages_are_in_the_sitemap_and_open_to_crawlers(api):
    body = api.get("/sitemap.xml").text
    listed = re.findall(r"<loc>http://testserver([^<]*)</loc>", body)
    for slug in NEW:
        assert f"/for/{slug}" in listed
    assert "/repair-partners" in listed
    robots = api.get("/robots.txt").text
    assert "Allow: /for" in robots and "Allow: /repair-partners$" in robots
    assert "Disallow: /repair-partners/" in robots


def test_the_index_lists_the_trades(api):
    body = api.get("/api/for").json()
    assert [t["slug"] for t in body["trades"]] == sorted(NEW)
    js = api.get("/static/industry.js").text
    assert "More trades with cold stock" in js and "row.slug" in js
