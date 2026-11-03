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
from pages import page_source


CARDED = ("/", "/signup", "/pricing", "/help", "/for", "/for/restaurant", "/legal")


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


def test_the_sitemap_lists_the_pages_worth_finding(api):
    body = api.get("/sitemap.xml")
    assert body.headers["content-type"].startswith("application/xml")
    assert "<loc>http://testserver/</loc>" in body.text
    assert "<loc>http://testserver/signup</loc>" in body.text
    assert "<loc>http://testserver/legal</loc>" in body.text
    # And nothing that needs a credential.
    assert "/console" not in body.text
    assert "/book" not in body.text


def test_the_sitemap_lists_every_industry_page(api):
    """The twelve /for pages were crawlable but missing from the map.

    They are the most findable thing in the product: a restaurant owner
    searches "walk-in freezer alarm", not "IoT thermal monitoring", and
    /for/restaurant is the page that answers them.
    """
    from industries import known

    body = api.get("/sitemap.xml").text

    assert "<loc>http://testserver/for</loc>" in body
    for vertical in known():
        assert f"<loc>http://testserver/for/{vertical}</loc>" in body, vertical


# ---- the landing page says what it does --------------------------------

WORDS = {9: "Nine", 10: "Ten", 11: "Eleven", 12: "Twelve", 13: "Thirteen",
         14: "Fourteen", 15: "Fifteen"}


def _landing():
    from pathlib import Path

    return page_source(Path(__file__).resolve().parent.parent
            / "static" / "index.html")


def test_the_sector_count_in_the_heading_is_the_number_shown():
    """The heading said "Eleven sectors" above a grid of twelve.

    Someone added the twelfth and nothing told them the sentence above it
    was now wrong. Spelled out, so it cannot be computed; checked here, so
    it cannot drift again without a failure.
    """
    from store import INDUSTRY_PROFILES

    word = WORDS[len(INDUSTRY_PROFILES)]
    assert f"<h2>{word} sectors," in _landing(), (
        f"there are {len(INDUSTRY_PROFILES)} sectors; the heading should "
        f"say {word}")


def test_no_plan_button_promises_a_plan_signup_cannot_give():
    """Every plan card links to /signup, and signup only ever starts a
    trial. "Start on Enterprise" produced a 14-day, five-sensor trial with
    nothing on the page to say so."""
    page = _landing()

    assert "Start on ${esc(p.name)}" not in page
    assert 'href="/signup">${p.plan === "trial"' in page


# ---- icons, the sitemap and noindex -----------------------------------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ("/console", "/today", "/partners", "/book", "/reset", "/offline",
           "/proof/anything", "/api", "/api/health", "/api/simulator",
           "/api/help/sensor-offline", "/docs", "/openapi.json")


@pytest.mark.parametrize("path", CARDED)
def test_every_public_page_names_its_icons(api, path):
    page = api.get(path).text
    assert 'rel="icon"' in page
    assert 'rel="apple-touch-icon" href="/static/icons/icon-180.png"' in page
    assert 'rel="manifest" href="/manifest.webmanifest"' in page
    assert re.search(r'<meta property="og:url" content="https?://[^"]+">', page)


def _icon_paths():
    """Every icon, image and manifest a page or the manifest names."""
    found = set()
    for page in (ROOT / "static").glob("*.html"):
        text = page.read_text()
        for tag in re.findall(r"<link [^>]*>", text):
            if re.search(r'rel="(?:icon|apple-touch-icon|manifest|mask-icon)"', tag):
                href = re.search(r'href="([^"]+)"', tag).group(1)
                if not href.startswith("data:"):
                    found.add(href)
        found.update(re.findall(r'<img src="(/[^"]+)"', text))
        found.update(m.replace("%%BASE_URL%%", "") for m in re.findall(
            r'content="(%%BASE_URL%%/static/[^"]+)"', text))
    manifest = json.loads((ROOT / "static" / "manifest.webmanifest").read_text())
    for icon in manifest["icons"]:
        found.add(icon["src"])
    for shortcut in manifest.get("shortcuts", []):
        for icon in shortcut.get("icons", []):
            found.add(icon["src"])
    return sorted(found)


def test_the_icon_list_is_not_empty():
    paths = _icon_paths()
    for expected in ("/favicon.ico", "/static/icons/icon-180.png",
                     "/static/og.png", "/manifest.webmanifest",
                     "/static/logo-mark.svg"):
        assert expected in paths


@pytest.mark.parametrize("path", _icon_paths())
def test_every_icon_named_actually_exists(api, path):
    resp = api.get(path)
    assert resp.status_code == 200, path
    assert len(resp.content) > 100, path


def test_the_favicon_is_a_real_icon(api):
    from io import BytesIO

    from PIL import Image

    resp = api.get("/favicon.ico")
    assert resp.headers["content-type"] == "image/x-icon"
    icon = Image.open(BytesIO(resp.content))
    assert icon.format == "ICO"
    assert (32, 32) in icon.info["sizes"]


def test_the_sitemap_lists_only_public_pages(api):
    from industries import known

    body = api.get("/sitemap.xml").text
    listed = re.findall(r"<loc>http://testserver([^<]*)</loc>", body)
    allowed = {"/", "/signup", "/pricing", "/help", "/for", "/legal"}
    allowed |= {f"/for/{v}" for v in known()}
    assert set(listed) <= allowed, set(listed) - allowed
    for must in ("/", "/pricing", "/for", "/legal"):
        assert must in listed
    for private in PRIVATE:
        assert f"<loc>http://testserver{private}</loc>" not in body


def test_every_page_in_the_sitemap_is_served_and_indexable(api):
    body = api.get("/sitemap.xml").text
    for url in re.findall(r"<loc>([^<]*)</loc>", body):
        resp = api.get(url)
        assert resp.status_code == 200, url
        assert "X-Robots-Tag" not in resp.headers, url


def test_the_sitemap_uses_the_configured_address(api, monkeypatch):
    import main

    monkeypatch.setattr(main, "PUBLIC_BASE_URL", "https://cyberlogix.example")
    body = api.get("/sitemap.xml").text
    assert "<loc>https://cyberlogix.example/pricing</loc>" in body
    assert "testserver" not in body
    robots = api.get("/robots.txt").text
    assert "Sitemap: https://cyberlogix.example/sitemap.xml" in robots


@pytest.mark.parametrize("path", PRIVATE)
def test_private_pages_send_noindex(api, path):
    resp = api.get(path)
    assert "noindex" in resp.headers.get("X-Robots-Tag", ""), path


@pytest.mark.parametrize("name", ["console", "simple", "book", "partner",
                                  "offline", "proof", "reset", "error"])
def test_private_page_files_say_noindex_themselves(name):
    """For copies served without our headers: the public demo is one."""
    page = page_source(ROOT / "static" / f"{name}.html")
    assert '<meta name="robots" content="noindex">' in page


def test_robots_keeps_crawlers_off_every_private_path(api):
    body = api.get("/robots.txt").text
    for private in ("/console", "/today", "/partners", "/book", "/reset",
                    "/offline", "/proof/", "/api/", "/docs"):
        assert f"Disallow: {private}" in body
    for public in ("/pricing", "/help", "/for", "/legal"):
        assert f"Allow: {public}" in body
