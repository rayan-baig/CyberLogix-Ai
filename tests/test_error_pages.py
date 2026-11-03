"""What a person sees when a page is missing or something breaks.

A browser gets a page in the site's own look. A script calling the API
keeps getting JSON. A 500 never shows the error. And the security
headers are on every one of these answers, including the 500, which
Starlette sends from outside the middleware stack.
"""

import pytest
from fastapi.testclient import TestClient

from main import CONTENT_SECURITY_POLICY, app
from pages import page_source

BROWSER = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
SECRET = "the-database-password-is-hunter2"


@pytest.fixture()
def exploding():
    """Two routes that raise, one a page and one under the API, removed after."""

    def _boom():
        raise RuntimeError(SECRET)

    app.add_api_route("/_test_page_explodes", _boom, include_in_schema=False)
    app.add_api_route("/api/_test_explodes", _boom, include_in_schema=False)
    try:
        # The default test client re-raises server errors instead of letting
        # the handler answer. Nothing in production does that.
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.router.routes = [
            r for r in app.router.routes
            if getattr(r, "path", "") not in ("/_test_page_explodes",
                                               "/api/_test_explodes")
        ]


def _has_security_headers(resp):
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "noindex" in resp.headers["X-Robots-Tag"]


# ---- 404 ------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/no-such-page", "/pricing/extra", "/for/not-a-trade"])
def test_a_browser_gets_a_friendly_404_page(api, path):
    resp = api.get(path, headers=BROWSER)
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")
    assert "Page not found" in resp.text
    assert 'href="/"' in resp.text and 'href="/help"' in resp.text
    # The site's own look, from the shared files.
    assert '/static/theme.css' in resp.text and '/static/pages.css' in resp.text
    assert "%%" not in resp.text


def test_the_404_page_carries_the_security_headers(api):
    _has_security_headers(api.get("/no-such-page", headers=BROWSER))


@pytest.mark.parametrize("path", [
    "/api/no-such-thing", "/api/billing/no-such-thing", "/static/no-such-file.png",
    "/docs/nothing",
])
def test_api_paths_keep_json_errors_even_for_a_browser(api, path):
    resp = api.get(path, headers=BROWSER)
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["detail"]


def test_a_script_that_does_not_ask_for_html_gets_json(api):
    resp = api.get("/no-such-page", headers={"Accept": "application/json"})
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


def test_an_api_404_raised_by_a_route_keeps_its_own_message(api):
    resp = api.get("/api/for/not-a-trade", headers=BROWSER)
    assert resp.status_code == 404
    assert "No page for" in resp.json()["detail"]


def test_other_errors_are_untouched(api):
    """Only a missing page gets the page; a 405 stays as it was."""
    resp = api.post("/legal", headers=BROWSER)
    assert resp.status_code == 405
    assert resp.headers["content-type"].startswith("application/json")


# ---- 500 ------------------------------------------------------------------

def test_a_browser_gets_a_friendly_500_without_the_error(exploding):
    resp = exploding.get("/_test_page_explodes", headers=BROWSER)
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("text/html")
    assert "Something went wrong" in resp.text
    for leak in (SECRET, "RuntimeError", "Traceback", "_boom", "test_error_pages"):
        assert leak not in resp.text, leak


def test_the_500_page_gives_a_reference_that_can_be_looked_up(
        exploding, api, admin_headers):
    resp = exploding.get("/_test_page_explodes", headers=BROWSER)
    listed = api.get("/api/admin/faults", headers=admin_headers).json()["faults"]
    assert listed
    assert any(f["fault_id"] in resp.text for f in listed)


def test_an_api_500_stays_json_and_says_nothing_inside(exploding):
    resp = exploding.get("/api/_test_explodes", headers=BROWSER)
    assert resp.status_code == 500
    body = resp.json()
    assert body["fault_id"]
    assert SECRET not in resp.text and "RuntimeError" not in resp.text


@pytest.mark.parametrize("path", ["/_test_page_explodes", "/api/_test_explodes"])
def test_a_500_carries_the_security_headers(exploding, path):
    """The exception handler runs outside the middleware: it adds them."""
    _has_security_headers(exploding.get(path, headers=BROWSER))


def test_a_500_over_https_carries_hsts(exploding):
    resp = exploding.get("https://testserver/_test_page_explodes", headers=BROWSER)
    assert resp.headers["Strict-Transport-Security"].startswith("max-age=")


def test_the_error_page_has_no_inline_style_or_script():
    from pathlib import Path

    page = page_source(Path(__file__).resolve().parent.parent / "static" / "error.html")
    assert "<style" not in page and "style=" not in page
    assert "<script>" not in page
    assert 'name="robots" content="noindex"' in page
