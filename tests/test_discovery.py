"""What happens when somebody shares the link.

A B2B product is sold by a link pasted into a Slack channel, a WhatsApp
group, or an email to the person who signs things off. A link that
renders as a bare URL in all three has spent the introduction the sender
was making for us.

The trap here is quiet and total: `og:image` and `og:url` are fetched by
a scraper that has no page to resolve a relative path against, so a
relative one is dropped and the card falls back to nothing. The
deployment's own address is the one thing a static file cannot know, so
it is substituted at serve time — from the request itself when nothing
is configured, so previews work on a laptop without anybody setting a
variable.
"""

import re

import pytest


CARDED = ("/", "/signup")


@pytest.mark.parametrize("path", CARDED)
def test_every_public_page_has_a_link_preview(api, path):
    page = api.get(path).text
    for tag in (
        'property="og:title"',
        'property="og:description"',
        'property="og:image"',
        'name="twitter:card"',
        'rel="canonical"',
    ):
        assert tag in page, f"{path} is missing {tag}"


@pytest.mark.parametrize("path", CARDED)
def test_no_preview_url_is_left_relative(api, path):
    """The whole failure mode, in one assertion."""
    page = api.get(path).text
    urls = re.findall(
        r'(?:property|name)="(?:og:(?:image|url)|twitter:image)" '
        r'content="([^"]+)"',
        page,
    )
    assert urls, f"{path} has no preview URLs at all"
    for url in urls:
        assert url.startswith("http://") or url.startswith("https://"), url


@pytest.mark.parametrize("path", CARDED)
def test_the_placeholder_never_reaches_a_browser(api, path):
    assert "%%BASE_URL%%" not in api.get(path).text


def test_the_preview_uses_the_address_the_request_arrived_on(api):
    """So it works on a laptop, and on a preview deployment, unconfigured."""
    page = api.get("/", headers={"Host": "hub.example.com"}).text
    assert "http://hub.example.com/static/og.png" in page


def test_a_configured_base_url_wins_over_the_request(api, monkeypatch):
    """Behind a proxy, the request's idea of its own host is the proxy's."""
    import main

    monkeypatch.setattr(main, "PUBLIC_BASE_URL", "https://cyberlogix.example")
    main._page_cache.clear()

    page = api.get("/", headers={"Host": "internal-8080.cluster.local"}).text
    assert "https://cyberlogix.example/static/og.png" in page
    assert "internal-8080" not in page


def test_the_browser_chrome_colour_comes_from_the_stylesheet(api):
    """`theme-color` will not read a CSS variable, so it is substituted.

    The alternative was two pages each keeping their own copy of the
    background colour, which is exactly the drift the shared stylesheet
    exists to prevent.
    """
    page = api.get("/").text
    css = api.get("/static/theme.css").text

    found = re.search(r'<meta name="theme-color" content="(#[0-9A-Fa-f]{6})">', page)
    assert found, "the page ships no browser-chrome colour"
    assert f"--bg:        {found.group(1)}" in css


def test_the_card_image_is_actually_there(api):
    """A preview pointing at a 404 is worse than no preview."""
    resp = api.get("/static/og.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    # 1200x630 of dark gradient. A few KB would mean something went wrong.
    assert len(resp.content) > 20_000


def test_robots_keeps_crawlers_off_the_password_fields(api):
    """None of them is secret; all of them are a bad first impression.

    A search result that lands somebody on a login form has taught them
    nothing about the product and asked them for a credential.
    """
    body = api.get("/robots.txt").text
    for private in ("/console", "/partners", "/book", "/api/"):
        assert f"Disallow: {private}" in body
    assert "Allow: /signup" in body


def test_robots_points_at_the_sitemap_absolutely(api):
    body = api.get("/robots.txt", headers={"Host": "hub.example.com"}).text
    assert "Sitemap: http://hub.example.com/sitemap.xml" in body


def test_the_sitemap_lists_the_three_pages_worth_finding(api):
    body = api.get("/sitemap.xml")
    assert body.headers["content-type"].startswith("application/xml")
    assert "<loc>http://testserver/</loc>" in body.text
    assert "<loc>http://testserver/signup</loc>" in body.text
    assert "<loc>http://testserver/legal</loc>" in body.text
    # And nothing that needs a credential.
    assert "/console" not in body.text
    assert "/book" not in body.text
