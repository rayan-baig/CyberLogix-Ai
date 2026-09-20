"""One page per industry, in that industry's own words.

A restaurant owner and an IVF clinic director both need exactly this
product and neither will read the other's page. The failure mode is
quiet: somebody adds a vertical to store.py, nothing breaks, and that
industry silently has no page while the sales pitch says twelve.
"""

import pytest

import industries
from store import INDUSTRY_PROFILES


def test_every_vertical_the_product_sells_to_has_a_page():
    """The guard. Adding a vertical without words for it should fail
    here rather than be discovered by the first prospect from it."""
    missing = sorted(set(INDUSTRY_PROFILES) - set(industries.CONTEXT))

    assert missing == [], (
        f"these verticals have no page written for them: {missing}")


def test_no_page_is_written_for_an_industry_that_does_not_exist():
    stray = sorted(set(industries.CONTEXT) - set(INDUSTRY_PROFILES))

    assert stray == [], f"pages for verticals nothing sells to: {stray}"


@pytest.mark.parametrize("vertical", sorted(INDUSTRY_PROFILES))
def test_each_page_says_what_is_protected_and_what_is_lost(vertical):
    page = industries.page(vertical)

    for field in ("name", "slogan", "protects", "loses", "asks_after",
                  "catastrophe", "asset_noun", "asset_plural", "band"):
        assert page[field], f"{vertical} has no {field}"
    assert len(page["loses"]) > 60, (
        f"{vertical}'s consequence is too thin to be worth reading")
    assert page["licences"], f"{vertical} lists no licences"


@pytest.mark.parametrize("vertical", sorted(INDUSTRY_PROFILES))
def test_the_band_reads_as_a_sentence_whichever_limits_are_set(vertical):
    """Some verticals have an upper limit only, some have both. A page
    reading "under None degrees" is the kind of thing that only shows up
    in front of the one reader who knows the industry."""
    band = industries.page(vertical)["band"]

    assert "None" not in band
    assert band.startswith(("under ", "above ", "between ", "inside "))


def test_the_pages_are_public(api):
    """This is the front of the product. A page that asks a stranger to
    sign in before it will say what the thing does is not read."""
    assert api.get("/api/for").status_code == 200
    assert api.get("/api/for/restaurant").status_code == 200
    assert api.get("/for").status_code == 200
    assert api.get("/for/restaurant").status_code == 200


def test_an_unknown_industry_is_a_404_that_lists_the_real_ones(api):
    resp = api.get("/api/for/submarines")

    assert resp.status_code == 404
    assert "restaurant" in resp.json()["detail"]


def test_no_page_invents_a_price_or_a_statistic():
    """A number somebody made up is the fastest way to lose the one
    reader who knows the industry."""
    import re

    for vertical in industries.known():
        page = industries.page(vertical)
        prose = " ".join(str(page[k]) for k in ("protects", "loses"))
        assert "$" not in prose, f"{vertical} quotes a price"
        assert not re.search(r"\b\d+\s*(%|percent)", prose), (
            f"{vertical} quotes a statistic")


def test_the_pages_do_not_shadow_the_sector_catalogue(api):
    """Two routers both claimed /api/industries.

    FastAPI does not fail on that; it shadows, first registration wins,
    and the marketing index was quietly served telemetry's pricing
    catalogue instead. It rendered correctly, because both payloads
    happen to carry a name and a slogan -- which is precisely why it
    would never have been noticed by looking.
    """
    catalogue = api.get("/api/industries").json()
    pages = api.get("/api/for").json()

    # The catalogue is the sector selector: it prices things.
    assert "monthly_usd" in catalogue["industries"][0]
    # The pages are the marketing copy: they do not.
    assert "monthly_usd" not in pages["industries"][0]
    assert "protects" in pages["industries"][0]
