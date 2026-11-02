"""Every route the app serves is reached by at least one test.

A route nothing calls is either dead or unguarded, and the suite cannot
tell which. This reads what the whole run actually reached -- recorded at
the edge of the app by conftest, so it is the router's own match and not
a guess from the test source -- and names whatever nobody touched.

It runs last, and only means something after the whole suite: a run of
one file skips it.
"""

import pytest

from conftest import ROUTES_HIT, WHOLE_SUITE
from main import app

# Served by the framework, not by this product.
FRAMEWORK = {
    ("GET", "/openapi.json"),
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/redoc"),
}


def _routes(routes):
    """Every (method, path) the app answers, through included routers."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:          # a router included into the app
            yield from _routes(inner.routes)
        elif hasattr(route, "routes"):  # a mount, or a plain sub-router
            yield from _routes(route.routes)
        else:
            for method in getattr(route, "methods", None) or ():
                if method not in ("HEAD", "OPTIONS"):
                    yield method, route.path


def test_the_app_has_routes_to_audit():
    """Guards the walk above: a FastAPI change that hid the included
    routers would otherwise make the audit pass by finding nothing."""
    found = set(_routes(app.routes))
    assert len(found) > 150
    assert ("POST", "/api/sensor-pulse") in found
    assert ("GET", "/api/console/overview") in found


def test_every_route_is_reached_by_a_test(request):
    if not request.config.stash.get(WHOLE_SUITE, False):
        pytest.skip("only meaningful after the whole suite has run")
    missing = sorted(set(_routes(app.routes)) - ROUTES_HIT - FRAMEWORK)
    assert not missing, (
        "No test reaches these routes. Test them, or delete them if "
        "nothing uses them:\n  " + "\n  ".join(f"{m} {p}" for m, p in missing)
    )
