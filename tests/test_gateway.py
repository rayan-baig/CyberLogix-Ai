"""Hub gateway and cross-module mounting."""


def test_root_gateway_lists_every_module(api):
    body = api.get("/api").json()
    assert body["system"] == "CyberLogix AI Master Engine"
    assert body["status"] == "fully_operational_stealth_mode"
    # Asserted against the app's own list rather than a hard-coded count,
    # so mounting a new subsystem does not fail an unrelated test.
    from main import MODULES_ACTIVE

    assert body["modules_active"] == MODULES_ACTIVE
    for module in (
        "universal_iot_telemetry",
        "byod_hardware_bridge",
        "site_management",
        "unattended_autopilot_scheduler",
    ):
        assert module in body["modules_active"]


def test_health_reports_subsystem_state(api):
    body = api.get("/api/health").json()
    assert body["status"] == "online"
    from store import INDUSTRY_PROFILES

    assert body["active_profiles"] == len(INDUSTRY_PROFILES)
    from main import MODULES_ACTIVE

    assert body["modules_active"] == len(MODULES_ACTIVE)
    assert body["plan_tiers"] == ["trial", "growth", "enterprise"]


def test_every_router_is_actually_mounted(api):
    paths = api.get("/openapi.json").json()["paths"]
    for expected in (
        "/api/sensor-pulse",
        "/api/licenses/tenants",
        "/api/autopilot/sweep",
        "/api/voice/pending",
        "/api/forecast/fleet",
        "/api/v1/bridge/sensor-webhook-ingest",
        "/api/v1/bridge/summarize-transcript",
        "/api/console/overview",
        "/api/accounts/login",
        "/api/costs",
        "/api/contacts",
        "/api/billing/pricing",
        "/api/v1/enterprise-billing/provision-cluster",
    ):
        assert expected in paths, f"{expected} is not mounted"


def test_industry_catalogue_covers_every_vertical(api):
    from store import INDUSTRY_PROFILES

    body = api.get("/api/industries").json()
    assert body["count"] == len(INDUSTRY_PROFILES)
    medical = next(
        i for i in body["industries"] if i["vertical"] == "medical_lab"
    )
    assert medical["danger_above"] == 46.0
    assert medical["danger_below"] == 36.0


def test_the_front_door_explains_the_product_before_asking_for_a_password(api):
    """The root used to be the console, so every arrival met a login box.

    A prospect, a journalist, somebody following a link off an invoice —
    all of them were shown a password field and nothing that said what
    the password was for. The landing page is the fix, and it is only a
    fix if it actually says what the product does and where to sign in.
    """
    resp = api.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "signin-form" not in resp.text, "the root is the login box again"
    assert 'href="/console"' in resp.text, "no way through to the console"
    for promise in ("escalation", "sector", "claim"):
        assert promise in resp.text.lower(), f"the page never mentions {promise}"


def test_the_console_is_served_at_console(api):
    resp = api.get("/console")
    assert resp.status_code == 200
    assert "Operations Console" in resp.text
    assert "signin-form" in resp.text


def test_the_partner_portal_is_served(api):
    resp = api.get("/partners")
    assert resp.status_code == 200
    assert "Partner Portal" in resp.text


def test_the_landing_page_quotes_no_prices_of_its_own(api):
    """A marketing page with its own copy of the price list will drift.

    Sooner or later it quotes a number the product does not charge, and
    somebody signs up expecting it. Every figure on the page is fetched
    from the same endpoints the console uses.
    """
    import re

    from pricing import PRICE_BOOK

    page = api.get("/").text
    for entry in PRICE_BOOK.values():
        price = f"{entry['monthly_usd']:.0f}"
        assert f"${price}" not in page, (
            f"${price} is hard-coded into the landing page instead of "
            "being read from /api/industries"
        )
    assert "/api/industries" in page
    assert "/api/licenses/plans" in page

    # And the plan seat counts are not transcribed either.
    assert not re.search(r"Up to 1,?000 sensors", page)


def test_every_surface_shares_one_stylesheet(api):
    """Three pages that must look like one product cannot each keep a copy."""
    import re

    theme = api.get("/static/theme.css")
    assert theme.status_code == 200
    assert "--accent:    #4FA8F0" in theme.text

    for path in ("/", "/console", "/partners"):
        page = api.get(path).text
        assert "/static/theme.css" in page

        # No page carries a colour of its own. Checked as a class rather
        # than by naming one hex, so the next palette change cannot leave
        # a stale literal behind on one surface — the favicon's data-URI
        # is the one exemption, since it must be self-contained.
        body = re.sub(r'<link rel="icon"[^>]*>', "", page)

        # The second exemption, and it is not a copy: `theme-color` is a
        # meta tag that will not read a CSS variable, so the value is
        # substituted out of theme.css when the page is served. Asserted
        # against the stylesheet rather than merely stripped, so a palette
        # change cannot leave one behind.
        for colour in re.findall(
            r'<meta name="theme-color" content="(#[0-9A-Fa-f]{6})">', body
        ):
            assert colour.upper() in theme.text.upper(), (
                f"{path} sets a browser-chrome colour that theme.css does "
                f"not define: {colour}"
            )
        body = re.sub(r'<meta name="theme-color"[^>]*>', "", body)

        strays = set(re.findall(r"#[0-9A-Fa-f]{6}\b", body))
        assert not strays, f"{path} defines its own colours: {sorted(strays)}"


def test_every_surface_carries_the_same_background(api):
    """The board is the house motif; a page without it is a stranger."""
    for path in ("/", "/console", "/partners"):
        page = api.get(path).text
        assert '/static/circuit.js' in page, f"{path} has no board behind it"
        assert 'id="circuit"' in page, f"{path} loads the board but never draws it"
        assert 'aria-hidden="true"' in page

    served = api.get("/static/circuit.js")
    assert served.status_code == 200
    # It must not be able to swallow a click, and it must stop moving for
    # anyone who asked for less motion.
    assert "pointer-events: none" in api.get("/static/theme.css").text
    assert "prefers-reduced-motion" in served.text
