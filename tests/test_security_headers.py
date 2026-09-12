"""The headers a browser can enforce on our behalf.

None of these was set. Each closes something specific, and the two that
matter most here are not generic hygiene:

  * `frame-ancestors` stops the console being loaded invisibly inside
    somebody else's page, where a customer thinks they are clicking one
    thing and are clicking "suspend this licence" on ours.
  * `Referrer-Policy` is the other half of the password-reset fix. The
    reset page scrubs the token out of the address bar; this stops it
    reaching a third party in a Referer header before the scrub, which
    would hand a one-time credential to whatever the page loads next.

The failure mode to guard against is not a missing header. It is a
policy so strict that it blocks the product's own stylesheet, gets
switched off the first time somebody notices, and then protects nothing.
"""

import pytest

from main import CONTENT_SECURITY_POLICY


PAGES = ("/", "/console", "/signup", "/legal", "/book", "/partners", "/reset")


@pytest.mark.parametrize("path", PAGES)
def test_every_page_refuses_to_be_framed(api, path):
    """Clickjacking the console is clickjacking somebody's licence."""
    headers = api.get(path).headers
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


@pytest.mark.parametrize("path", PAGES)
def test_every_page_pins_its_content_types(api, path):
    """So a browser cannot decide a JSON error is really HTML and run it."""
    assert api.get(path).headers["X-Content-Type-Options"] == "nosniff"


def test_the_reset_page_does_not_leak_its_token_in_a_referer(api):
    """The half of the fix the page itself cannot do.

    The token is in the query string until the script scrubs it, and
    anything the page loads in between carries the full URL in Referer
    unless this header says otherwise.
    """
    policy = api.get("/reset").headers["Referrer-Policy"]
    assert policy in ("strict-origin-when-cross-origin", "no-referrer")


def test_the_api_carries_the_headers_too(api):
    """A JSON endpoint is where a sniffed content type does the damage."""
    headers = api.get("/api/health").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"


def test_the_policy_still_allows_the_product_its_own_assets(api):
    """A policy that blocks the stylesheet is one that gets deleted.

    The typefaces come from Google Fonts, so those origins are named
    rather than pretended away.
    """
    assert "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com" in (
        CONTENT_SECURITY_POLICY
    )
    assert "font-src 'self' https://fonts.gstatic.com" in CONTENT_SECURITY_POLICY
    assert "img-src 'self' data:" in CONTENT_SECURITY_POLICY  # the favicons


def test_the_policy_refuses_script_from_anywhere_else(api):
    """'unsafe-inline' is honest about the pages as written; it is not a
    licence for an injected <script src> to reach an attacker's host."""
    directive = next(
        d for d in CONTENT_SECURITY_POLICY.split("; ")
        if d.startswith("script-src")
    )
    assert directive == "script-src 'self' 'unsafe-inline'"
    assert "base-uri 'none'" in CONTENT_SECURITY_POLICY
    assert "object-src 'none'" in CONTENT_SECURITY_POLICY


def test_hsts_is_not_sent_over_plain_http(api):
    """A browser that honours it cannot then reach a development server,
    and a header that makes the product unrunnable locally is one
    somebody deletes rather than fixes."""
    assert "Strict-Transport-Security" not in api.get("/").headers


def test_hsts_is_sent_over_https(api):
    resp = api.get("https://testserver/")
    assert resp.headers["Strict-Transport-Security"].startswith("max-age=31536000")
