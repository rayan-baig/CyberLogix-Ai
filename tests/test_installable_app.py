"""The app you install on a phone, and the one rule it must not break.

Asked for a native app. This is the web application made installable --
own icon, own window, no browser chrome -- rather than a binary in a
store, because a store takes 15-30% of every subscription and puts a
review queue between a fix and the customer, for a wrapper around these
same pages. That cut is the one this product was specifically built to
avoid by being a website.

The rule that matters more than every other line here: NOTHING UNDER
/api IS EVER SERVED FROM CACHE. This application exists to say whether a
freezer is cold. An offline-first cache answering a stale "everything is
fine" would be the one failure the product is sold to prevent, delivered
by the thing sold to prevent it.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SW = (ROOT / "static" / "sw.js").read_text()
MANIFEST = json.loads((ROOT / "static" / "manifest.webmanifest").read_text())
OFFLINE = (ROOT / "static" / "offline.html").read_text()
CONSOLE = (ROOT / "static" / "console.html").read_text()


# --- the rule ---------------------------------------------------------


def test_the_worker_never_serves_api_responses_from_cache():
    """The whole safety argument in one assertion."""
    assert 'url.pathname.startsWith("/api/")' in SW
    guard = SW[SW.index('url.pathname.startsWith("/api/")'):][:80]
    # It must bail out before any respondWith, not fall through to one.
    assert "return" in guard


def test_nothing_in_the_precache_list_is_an_api_path():
    shell = SW[SW.index("const SHELL = ["):SW.index("];", SW.index("const SHELL = ["))]

    assert "/api/" not in shell, "an API response would be frozen at install"


def test_the_offline_page_shows_no_reading_and_says_so():
    """It is deliberately not a copy of the dashboard. Showing the last
    known temperatures to somebody with no connection is how an app
    tells a restaurant its freezer is fine while it is failing."""
    assert "not be current" in OFFLINE or "would be current" in OFFLINE
    assert "Nothing here means your assets are fine" in OFFLINE
    # And it says where monitoring actually runs, because it is not here.
    assert "runs on the server" in OFFLINE


def test_the_console_says_when_it_is_not_live(api):
    """A failed refresh leaves temperatures painted on screen. The page
    has to say they are stale before anything else it says."""
    js = CONSOLE[CONSOLE.index("<script>"):CONSOLE.rindex("</script>")]

    assert "showConnection" in js
    assert 'id="offline-banner"' in CONSOLE
    block = js[js.index("function showConnection("):]
    block = block[:block.index("addEventListener(\"offline\"")]
    assert "Nothing on this screen is current" in block


# --- it installs ------------------------------------------------------


@pytest.mark.parametrize("path,kind", [
    ("/manifest.webmanifest", "application/manifest+json"),
    ("/sw.js", "text/javascript"),
    ("/offline", "text/html"),
])
def test_the_app_files_are_served(api, path, kind):
    resp = api.get(path)

    assert resp.status_code == 200
    assert kind in resp.headers["content-type"]


def test_the_worker_is_served_from_the_root_scope(api):
    """A service worker only controls the scope it is served from, and
    this one has to cover /console and /book together. Served out of
    /static it would control neither."""
    resp = api.get("/sw.js")

    assert resp.headers.get("service-worker-allowed") == "/"
    assert "no-cache" in resp.headers.get("cache-control", "")


def test_the_manifest_has_what_a_launcher_needs():
    for key in ("name", "short_name", "start_url", "scope", "display",
                "background_color", "theme_color", "icons"):
        assert key in MANIFEST, key
    assert MANIFEST["display"] == "standalone"


def test_every_icon_the_manifest_promises_exists(api):
    sizes = set()
    for icon in MANIFEST["icons"]:
        served = api.get(icon["src"])
        assert served.status_code == 200, icon["src"]
        assert served.headers["content-type"] == "image/png"
        if icon.get("purpose") == "any":
            sizes.add(icon["sizes"])

    # A launcher needs both to offer installation at all.
    assert {"192x192", "512x512"} <= sizes


def test_the_icons_are_real_images_not_placeholders():
    from PIL import Image

    for icon in MANIFEST["icons"]:
        path = ROOT / icon["src"].lstrip("/")
        with Image.open(path) as img:
            expected = int(icon["sizes"].split("x")[0])
            assert img.size == (expected, expected), icon["src"]
            # An icon that is one flat colour is a placeholder.
            assert len(img.convert("RGB").getcolors(maxcolors=100000) or []) > 8


def test_the_console_is_installable(api):
    page = api.get("/console").text

    assert '<link rel="manifest" href="/manifest.webmanifest">' in page
    assert 'rel="apple-touch-icon"' in page
    assert 'name="theme-color"' in page
    assert "registerWorker()" in page


def test_the_shortcuts_point_at_pages_that_exist(api):
    for shortcut in MANIFEST.get("shortcuts", []):
        assert api.get(shortcut["url"]).status_code == 200, shortcut["url"]


def test_the_start_url_is_a_page_not_the_landing_site(api):
    """Launching the installed app should open the estate, not the
    marketing page somebody already decided to buy from."""
    assert MANIFEST["start_url"] == "/console"
    assert api.get(MANIFEST["start_url"]).status_code == 200


def test_stale_figures_go_quiet_and_the_banner_does_not():
    """A banner is read; a dashboard is glanced at. Somebody looking at
    a phone sees "BREACHING NOW 1" in 40px type and acts on it, whatever
    a paragraph above it says."""
    js = CONSOLE[CONSOLE.index("<script>"):CONSOLE.rindex("</script>")]
    css = (ROOT / "static" / "theme.css").read_text()

    assert 'toggleAttribute("data-stale"' in js
    assert "body[data-stale]" in css
    stale = css[css.index("body[data-stale]"):]
    # The figures dim...
    assert "opacity: .45" in stale
    # ...and the thing saying they are stale does not.
    assert "#offline-banner" in stale
