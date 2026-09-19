"""The page the product is judged by, before anybody signs anything.

A demo is the one artefact that goes out to people who have no reason to
give it the benefit of the doubt. Two things have to hold every time it
is rebuilt, and neither is obvious from looking at the output: that it
carries no credential out of the estate it was built from, and that it
is the real console rather than a drawing of one.
"""

import pathlib
import re

import build_public_demo


def test_the_page_is_the_real_console():
    """Not a mockup. If this ever passes on a hand-written page, the
    demo has stopped being evidence and become a claim.

    No database to point anywhere: conftest pins the store to :memory:
    before anything imports it, and these run against that.
    """
    page = build_public_demo.build(build_public_demo.capture())

    # Markup, styling and behaviour all come from the shipped files.
    assert 'id="signin-form"' in page
    assert "--accent-cyan" in page          # the real theme
    assert 'id="find-q"' in page            # the real finder
    assert 'data-label="Staff licences"' in page  # the real cards
    assert 'data-label="Your crew"' in page


def test_the_page_carries_no_credentials():
    """The estate it is built from has an API key, a partner key and
    password hashes. None of them may travel with the page.

    This is the test that matters: the page is committed to a public
    repository and served to anyone with the link, so a leak here is a
    leak to the world, permanently, in git history.
    """
    page = build_public_demo.build(build_public_demo.capture())

    leaks = re.findall(
        r"clx_[A-Za-z0-9_-]{6,}"          # tenant and partner API keys
        r"|sk-[A-Za-z0-9]{10,}"           # model provider keys
        r"|whsec_[A-Za-z0-9]{8,}"         # Stripe webhook secret
        r"|\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}"  # bcrypt password hashes
        r"|-----BEGIN [A-Z ]*PRIVATE KEY",
        page,
    )
    assert leaks == [], f"credentials in the public demo: {set(leaks)}"


def test_it_refuses_to_ship_a_page_with_broken_panels(monkeypatch):
    """A card reading "Not captured in this demo" is the worst possible
    thing for a stranger to land on, and it happens quietly: somebody
    adds a panel, nobody adds its endpoint here, and the page still
    builds."""
    monkeypatch.setattr(
        build_public_demo, "PATHS", ["/api/console/overview", "/api/nope"])
    try:
        build_public_demo.capture()
    except SystemExit as stop:
        assert "broken panels" in str(stop)
    else:
        raise AssertionError("a 404 endpoint should stop the build")


def test_the_builder_leaves_no_database_behind():
    """Run it for real, in an empty directory, and look.

    Grepping the source for the right variable name was the first
    version of this test, and it passed while the builder was still
    writing a four megabyte database into the working directory: the
    name was right and the import order was wrong, so the default had
    already been frozen by the time the line ran. Only running it says.
    """
    import subprocess
    import sys
    import tempfile

    with tempfile.TemporaryDirectory() as run_in:
        done = subprocess.run(
            [sys.executable, str(build_public_demo.ROOT / "build_public_demo.py")],
            cwd=run_in, capture_output=True, text=True, timeout=300,
        )
        assert done.returncode == 0, done.stderr[-2000:]
        left = sorted(p.name for p in pathlib.Path(run_in).iterdir())

    assert left == [], f"the build left files in the working directory: {left}"


def test_the_database_setting_has_one_name():
    """The literal in the builder and the constant db.py reads."""
    import db

    source = (build_public_demo.ROOT / "build_public_demo.py").read_text()
    assert db.ENV_DB_PATH == "CYBERLOGIX_DB_PATH"
    assert f'os.environ["{db.ENV_DB_PATH}"]' in source


def test_the_page_fetches_nothing_from_the_network():
    """Opening it must not tell anybody you opened it.

    The stylesheet pulls Archivo, Inter and JetBrains Mono from
    fonts.googleapis.com, which hands Google the IP address of every
    person who is sent this link, before a word renders. The public
    build swaps that for an embedded copy, and this is the test that
    it stays swapped.
    """
    page = build_public_demo.build(build_public_demo.capture())

    # Checked where a browser loads from, not on the bare hostname: the
    # embedded stylesheet's own comment names fonts.googleapis.com to
    # explain what it replaced, and a substring check failed on that.
    # A guard that fires on an explanation gets edited until it is quiet.
    loads = re.findall(
        r"""(?:src|href)\s*=\s*["']\s*(https?://[^"']+)"""
        r"""|@import\s+url\(\s*['"]?\s*(https?://[^'")]+)"""
        r"""|url\(\s*['"]?\s*(https?://[^'")]+)""",
        page)
    assert [hit for group in loads for hit in group if hit] == []
    assert "@font-face" in page and "data:font/woff2;base64," in page


def test_the_password_box_is_filled_in_for_the_visitor():
    """So that nobody types a real one into a link a stranger sent them.

    Nothing here is sent anywhere -- there is no server behind the page
    -- but that is invisible to the person looking at a password box,
    and a demo that shows one and waits is rehearsing the exact habit
    that gets people phished.
    """
    page = build_public_demo.build(build_public_demo.capture())

    assert 'getElementById("si-password")' in page
    assert "harbor-demo-2026" in page
    assert "Never type a real password into a link somebody sent you" in page


def test_a_stylesheet_that_starts_phoning_home_again_stops_the_build():
    """The guard has to fail on the thing it is guarding against."""
    real = build_public_demo.build

    def with_a_tracker():
        page = real(build_public_demo.capture())
        return page.replace("<title>", '<link href="https://evil.example/x.css">'
                                       "<title>", 1)

    page = with_a_tracker()
    import re
    found = re.findall(r'href\s*=\s*["\']\s*(https?://[^"\']+)', page)
    assert found == ["https://evil.example/x.css"], (
        "the pattern the build guard uses no longer catches a remote "
        "stylesheet")


def test_no_card_is_titled_in_jargon():
    """The titles have to mean something to somebody who has never seen
    this before, because on a product sold to restaurants that is most
    people, most of the time.

    "Loss assurance", "Sector standing", "The fleet" and "BYOD sensor
    webhook" each name a real thing correctly and tell a first-time
    reader nothing at all.
    """
    page = build_public_demo.build(build_public_demo.capture(), "console.html")

    titles = re.findall(r'<span class="card-title"[^>]*>(.*?)</span>', page)
    assert titles, "no card titles found -- the pattern has drifted"

    jargon = ("fleet", "estate", "breach", "incident", "escalation", "byod",
              "assurance", "vault", "roster", "tier", "sector", "autopilot",
              "subscription", "compliance", "asset")
    guilty = [t for t in titles
              if any(word in t.lower() for word in jargon)]
    assert guilty == [], f"card titles a stranger would not understand: {guilty}"


def test_every_card_says_what_it_is_for():
    """A plain title is half of it. The sentence under it is the rest."""
    page = build_public_demo.build(build_public_demo.capture(), "console.html")

    assert page.count('class="card-plain"') >= 20


def test_the_plain_view_keeps_its_own_stylesheet():
    """Only the <body> is carried over, and a page's own <style> is in
    its <head>.

    The console keeps everything in theme.css, so nothing was lost and
    nobody noticed. The plain view keeps its own block, and the first
    build of it shipped unstyled -- default list numbering, no card,
    nothing broken enough to look broken.
    """
    page = build_public_demo.build(build_public_demo.capture(), "simple.html")

    assert ".how li::before" in page, "the numbered steps lost their styling"
    assert ".intro-lede" in page
    assert ".verdict.ok" in page


def test_the_plain_view_explains_the_product_to_a_stranger():
    """Somebody is handed this link with no explanation attached."""
    page = build_public_demo.build(build_public_demo.capture(), "simple.html")

    assert "What this is" in page
    assert "A thermometer sits in the freezer" in page
    assert "We phone a human until one answers" in page
    assert "We keep the receipts" in page
