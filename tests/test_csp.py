"""No inline script, no inline style, and a policy that says so.

The Content-Security-Policy used to carry 'unsafe-inline' for script and
style, because every page kept its code in a <script> block. That made
the policy close to decorative: anything that got into a page -- a sensor
named `<img src=x onerror=...>` that one template forgot to escape --
would run. Each page's script and stylesheet is now a file, and the
policy refuses the rest.

These hold every page the app serves to that, so a page cannot quietly
grow an inline block again: the browser would refuse it, and the first
anybody heard would be a dead button.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from main import API_DOCS_POLICY, CONTENT_SECURITY_POLICY, app

STATIC = Path(__file__).resolve().parent.parent / "static"

# A value for each kind of path parameter a page route takes.
SAMPLE = {"vertical": "restaurant", "token": "not-a-real-token"}


def _page_routes():
    """Every GET route outside the API that answers a browser with HTML."""
    paths = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if "GET" not in (getattr(route, "methods", None) or ()):
            continue
        if path.startswith(("/api", "/static")) or path in (
                "/openapi.json", "/sw.js", "/manifest.webmanifest",
                "/robots.txt", "/sitemap.xml", "/favicon.ico"):
            continue
        paths.append(re.sub(r"\{(\w+)\}", lambda m: SAMPLE[m.group(1)], path))
    # And the friendly error page, which is not a route of its own.
    return sorted(set(paths)) + ["/no-such-page"]


PAGES = _page_routes()


class _Inline(HTMLParser):
    """Collects everything in a page the policy would refuse."""

    def __init__(self):
        super().__init__()
        self.found = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            kind = (attrs.get("type") or "").lower()
            # JSON data blocks are never executed, so the policy ignores them.
            if not attrs.get("src") and kind not in ("application/json",
                                                       "application/ld+json"):
                self.found.append("an inline <script>")
        if tag == "style":
            self.found.append("an inline <style>")
        for name, value in attrs.items():
            if name == "style":
                self.found.append(f"a style attribute on <{tag}>")
            if name.startswith("on"):
                self.found.append(f"an inline {name} handler on <{tag}>")
            if name in ("href", "src", "action") and (value or "").strip().lower(
                    ).startswith("javascript:"):
                self.found.append(f"a javascript: URL on <{tag}>")


def _inline_in(markup: str) -> list:
    parser = _Inline()
    parser.feed(markup)
    return parser.found


def test_every_page_route_is_in_the_list():
    """The list is read from the router, so a new page is checked too."""
    for path in ("/", "/console", "/today", "/signup", "/pricing", "/help",
                 "/for", "/for/restaurant", "/legal", "/book", "/partners",
                 "/reset", "/offline", "/proof/not-a-real-token", "/docs",
                 "/redoc"):
        assert path in PAGES


@pytest.mark.parametrize("path", PAGES)
def test_no_served_page_carries_anything_inline(api, path):
    resp = api.get(path, headers={"Accept": "text/html"})
    assert "text/html" in resp.headers["content-type"], path
    assert _inline_in(resp.text) == [], path


@pytest.mark.parametrize("path", PAGES)
def test_every_page_is_sent_a_policy_without_unsafe_inline_script(api, path):
    policy = api.get(path, headers={"Accept": "text/html"}).headers[
        "Content-Security-Policy"]
    directives = dict(d.split(" ", 1) for d in policy.split("; "))
    assert "'unsafe-inline'" not in directives["script-src"], path
    assert "'unsafe-eval'" not in directives["script-src"], path
    if path in ("/docs", "/redoc"):
        # The two API reference pages run Swagger UI and ReDoc from a CDN,
        # and ReDoc writes its styles into <style> elements at run time.
        # Their policy is their own, and only theirs keeps inline style.
        assert policy == API_DOCS_POLICY
    else:
        assert policy == CONTENT_SECURITY_POLICY
        assert "'unsafe-inline'" not in policy, path


def test_the_product_policy_has_no_unsafe_inline_at_all():
    assert "unsafe-inline" not in CONTENT_SECURITY_POLICY
    assert "unsafe-eval" not in CONTENT_SECURITY_POLICY


@pytest.mark.parametrize("path", PAGES)
def test_every_file_a_page_loads_is_served(api, path):
    """A page whose script moved out but was not served would be a page
    with no behaviour, and nothing on it would say why."""
    page = api.get(path, headers={"Accept": "text/html"}).text
    for ref in re.findall(r'(?:src|href)="(/static/[^"#?]+)"', page):
        assert api.get(ref).status_code == 200, f"{path} loads {ref}"


@pytest.mark.parametrize("script", sorted(p.name for p in STATIC.glob("*.js")))
def test_no_script_builds_markup_the_policy_would_refuse(script):
    """Markup a script writes with innerHTML is held to the same policy.

    A style="..." or onclick="..." inside a template string is refused
    by the browser exactly as one in the page is, but only when it is
    drawn -- so it is caught here instead. element.style.x = ... from the
    script itself is allowed, and is how the live-width bars are drawn.
    """
    source = (STATIC / script).read_text(encoding="utf-8")
    # Comments are prose, not markup.
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    code = re.sub(r"(?m)^\s*//.*$", "", code)
    assert not re.search(r"""\sstyle\s*=\s*\\?["'`$]""", code), script
    assert not re.search(r"""\son[a-z]+\s*=\s*\\?["']""", code), script
    assert "setAttribute(\"style\"" not in code and "cssText" not in code, script
    assert "javascript:" not in code, script
    assert not re.search(r"\beval\(|new Function\(", code), script


def test_a_live_width_is_set_from_script_not_markup():
    """The meters in the console carry their figure as data-w, and the
    console sizes them through element.style, which the policy allows."""
    console = (STATIC / "console.js").read_text(encoding="utf-8")
    assert 'data-w="${' in console
    assert "bar.style.width" in console
