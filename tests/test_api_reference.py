"""The API reference at /developers: every public route, and no secret.

It is rendered from the app's own OpenAPI schema, so it cannot fall behind
the router; these check that it does not leave a route out, that the
platform operator's routes stay off it, that it works under the strict
content security policy with no script at all, and that it never prints a
credential -- not the operator's, not a business's, not an example that
looks like one.
"""

from __future__ import annotations

import html
import json
import re

import pytest

import apiref
from main import CONTENT_SECURITY_POLICY, app


def _routes():
    """(method, path) for every route the schema documents, from the router."""
    found = set()
    for route in app.routes:
        inner = getattr(route, "original_router", None)
        routes = inner.routes if inner is not None else [route]
        for r in routes:
            if not getattr(r, "include_in_schema", False):
                continue
            for method in getattr(r, "methods", None) or ():
                if method not in ("HEAD", "OPTIONS"):
                    found.add((method, r.path))
    return found


@pytest.fixture()
def page(api):
    resp = api.get("/developers")
    assert resp.status_code == 200
    return resp.text


def test_every_public_route_is_on_the_page(page):
    schema = app.openapi()
    public = apiref.public_operations(schema)
    assert len(public) > 100
    for item in public:
        marker = f'data-route="{item["method"].upper()} {html.escape(item["path"])}"'
        assert marker in page, f"{item['method'].upper()} {item['path']} is missing"


def test_the_schema_walk_misses_no_route_the_router_has():
    """The page reads the schema; the schema must hold every route."""
    walked = {(i["method"].upper(), i["path"]) for i in apiref.operations(app.openapi())}
    assert _routes() <= walked, _routes() - walked


def test_the_basics_are_there_with_their_credentials(page):
    for route in ("POST /api/signup", "POST /api/sensor-pulse",
                  "POST /api/accounts/login", "GET /api/licenses/me",
                  "GET /api/partners/me", "GET /api/health"):
        assert f'data-route="{route}"' in page, route
    pulse = page.split('data-route="POST /api/sensor-pulse"', 1)[1].split("</section>", 1)[0]
    assert "X-CyberLogix-Sensor-Key" in pulse and "X-CyberLogix-Key" in pulse
    signup = page.split('data-route="POST /api/signup"', 1)[1].split("</section>", 1)[0]
    assert "None. Public." in signup
    assert "company_name" in signup and "Example body" in signup


def test_operator_routes_are_counted_not_listed(page):
    schema = app.openapi()
    internal = [i for i in apiref.operations(schema) if i["internal"]]
    assert internal, "the operator has routes; the filter found none"
    for item in internal:
        assert f'data-route="{item["method"].upper()} {html.escape(item["path"])}"' not in page
    assert f"{len(internal)} more routes can be called only by the platform operator" in page
    for route in ("POST /api/partners", "GET /api/admin/backups",
                  "POST /api/admin/backups/restore", "GET /api/admin/faults",
                  "POST /api/licenses/tenants"):
        assert f'data-route="{route}"' not in page, route
    assert "X-CyberLogix-Admin" not in page
    assert "X-CyberLogix-Provisioning" not in page


def test_no_secret_or_key_is_ever_shown(api, monkeypatch, tenant_factory):
    import auth
    import licenses
    import payments

    secrets = {
        "admin": "admin-secret-value-do-not-print",
        "stripe": "whsec_do_not_print_this_one",
        "stats": "stats-secret-do-not-print",
        "mail": "mail-secret-do-not-print",
    }
    monkeypatch.setattr(auth, "PLATFORM_ADMIN_KEY", secrets["admin"])
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", secrets["stripe"])
    monkeypatch.setenv("STATS_KEY", secrets["stats"])
    monkeypatch.setenv("CYBERLOGIX_MAIL_SECRET", secrets["mail"])
    # The provisioning key the tenant below is created with.
    monkeypatch.setattr(licenses, "PROVISIONING_KEY", "test-provisioning-key")
    headers, _ = tenant_factory()
    business_key = headers["X-CyberLogix-Key"]

    page = api.get("/developers").text
    for value in list(secrets.values()) + [business_key, "test-provisioning-key"]:
        assert value not in page
    # Nothing shaped like one of our keys, or a provider's.
    assert not re.search(r"clx_[A-Za-z0-9_-]{8,}", page)
    assert not re.search(r"\b(sk|pk|rk)_(live|test)_", page)
    assert "whsec_" not in page
    assert not re.search(r"\bAC[0-9a-f]{32}\b", page)


def test_example_bodies_are_json_with_every_required_field(page):
    schema = app.openapi()
    components = schema["components"]["schemas"]
    checked = 0
    for item in apiref.public_operations(schema):
        content = (item["op"].get("requestBody") or {}).get("content", {})
        spec = content.get("application/json")
        if not spec:
            continue
        sample = apiref.example(spec["schema"], components)
        json.dumps(sample)  # serialisable
        resolved = apiref._resolve(spec["schema"], components)
        if resolved.get("type") == "object" or "properties" in resolved:
            assert set(resolved.get("required", [])) <= set(sample), item["path"]
        # Never a value that reads as a real secret.
        for key, value in (sample.items() if isinstance(sample, dict) else ()):
            if re.search(r"key|token|secret|password", key, re.I) and isinstance(value, str):
                assert value.startswith("<your "), (item["path"], key)
        checked += 1
    assert checked > 30


def test_the_page_runs_under_the_strict_policy_with_no_script(api, page):
    resp = api.get("/developers")
    assert resp.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert "<script" not in page
    assert " style=" not in page
    assert "cdn.jsdelivr.net" not in page
    assert "noindex" in resp.headers.get("X-Robots-Tag", "")


def test_help_links_to_the_reference(api):
    assert 'href="/developers"' in api.get("/help").text


def test_the_page_is_escaped(page):
    """Docstrings and patterns go through html.escape: no stray markup."""
    body = page.split("<main", 1)[1].split("</main>", 1)[0]
    assert "<code>/api/for/{vertical}</code>" in body
    assert not re.search(r"<(?!/?(?:a|b|br|code|div|h1|h2|h3|li|main|p|pre|section|span|"
                         r"strong|table|tbody|td|th|thead|tr|ul|footer|nav|header|img)\b)",
                         body)
