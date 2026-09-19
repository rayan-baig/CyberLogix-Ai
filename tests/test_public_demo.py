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
    assert 'data-label="Licences"' in page  # the real cards
    assert 'data-label="People"' in page


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
