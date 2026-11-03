"""The console's About and help card: the plan, the version, and how to get help.

It showed the version and linked /help, and said neither which plan the
business is on nor where to write. The plan was only in the header chip,
and the support address only on the help page.
"""

import re

import helpdesk
from pages import page_source
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _card() -> str:
    page = page_source(ROOT / "static" / "console.html")
    start = page.index('id="find-about"')
    return page[start:page.index("</section>", start)]


def test_the_card_has_a_place_for_each():
    card = _card()
    for element in ("about-version", "about-plan", "about-support", "about-support-link"):
        assert f'id="{element}"' in card, element
    assert 'href="/help"' in card
    assert "MTRX Horizyn" in card
    # The address line is hidden until there is an address to show.
    assert re.search(r'id="about-support"[^>]*\bhidden\b', card)


def test_the_script_fills_each_in():
    js = (ROOT / "static" / "console.js").read_text()
    for element in ("about-version", "about-plan", "about-support", "about-support-link"):
        assert f'$("{element}")' in js, element
    assert "d.support_email" in js and "t.plan_name" in js


def test_the_overview_carries_the_plan_and_the_address(api, operator_factory, monkeypatch):
    headers, _, _ = operator_factory(plan="enterprise")
    body = api.get("/api/console/overview", headers=headers).json()
    assert body["tenant"]["plan_name"]
    assert body["support_email"] == ""

    monkeypatch.setattr(helpdesk, "SUPPORT_EMAIL", "help@cyberlogix.example")
    body = api.get("/api/console/overview", headers=headers).json()
    assert body["support_email"] == "help@cyberlogix.example"


def test_the_version_is_on_health(api):
    from version import __version__

    assert api.get("/api/health").json()["version"] == __version__
