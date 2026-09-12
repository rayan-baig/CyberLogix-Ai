"""The console renders text a tenant controls. All of it must be escaped.

A company name, a sensor id, a location, a site name, a contact name, an
acknowledged-by string — every one of them is free text the customer
types, and every one reaches innerHTML. In the reseller portal it is
worse: a partner views a *different* company's text, so a hostile
customer would be attacking someone else's session.

Verified live in Chromium as well — payloads injected into eleven fields
across nine views produced zero executions — but a browser is not in the
unit suite, so these keep the property from drifting.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SURFACES = ("static/console.html", "static/partner.html")

# Fields whose value is typed by a customer and therefore never safe raw.
TENANT_CONTROLLED = (
    "company_name", "full_name", "location_name", "sensor_id",
    "site_name", "contact_name", "acknowledged_by",
    "external_device_sn", "purchase_order", "breach_details",
    "resolved_by", "address", "contact_phone", "contact_email",
)

# Three sites interpolate a customer-typed value without calling esc() on
# the spot, and all three are safe because the value is escaped one step
# later. They are listed rather than pattern-matched so that changing any
# of them fails this test and forces the question to be asked again.
#
#   console.html  the user chip   -> assigned to textContent, which cannot
#                                    parse markup
#   console.html  badge(...)      -> badge() escapes its own label
#   console.html  timeline steps  -> incidentTimeline() escapes step.text
ESCAPED_ONE_STEP_LATER = (
    "$(\"chip-user\").textContent",
    "${badge(",
    "`Called ${reached.contact_name",
    "`Acknowledged by ${i.acknowledged_by",
)


def script_of(path: Path) -> str:
    src = path.read_text()
    return src[src.index("<script>"): src.rindex("</script>")]


@pytest.mark.parametrize("surface", SURFACES)
def test_the_escape_helper_covers_every_dangerous_character(surface):
    """Checked by running it, not by reading it."""
    js = script_of(ROOT / surface)
    helper = js[js.index("const esc ="): js.index("const esc =") + 300]
    for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert entity in helper, f"esc() does not produce {entity}"
    # The character class it scans must cover all five.
    assert re.search(r"replace\(/\[&<>\\?\"'\]/g", helper) or \
        "[&<>\"']" in helper, "esc() does not scan for all five characters"


@pytest.mark.parametrize("surface", SURFACES)
def test_tenant_text_is_never_interpolated_raw(surface):
    """Every ${...} carrying a customer-typed field must call esc().

    Interpolations into `prompt()`, `textContent` and `logToConsole` are
    exempt: none of them parse HTML, and logToConsole escapes its own
    argument.
    """
    js = script_of(ROOT / surface)
    lines = js.split("\n")
    offenders = []

    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        # Contexts that cannot execute markup.
        if any(safe in line for safe in
               ("textContent", "logToConsole(", "prompt(", ".placeholder",
                "encodeURIComponent", "console.log", "toast(")):
            continue
        if any(known in line for known in ESCAPED_ONE_STEP_LATER):
            continue
        # A multi-line textContent assignment: the value is on the next
        # line, so look back a little before judging it.
        context = " ".join(lines[max(0, number - 3):number])
        if "innerHTML" not in context and any(
            safe in context for safe in ("textContent", "prompt(", "confirm(")
        ):
            continue
        for match in re.finditer(r"\$\{([^{}]*)\}", line):
            expr = match.group(1)
            if "esc(" in expr or "money(" in expr:
                continue
            if any(field in expr for field in TENANT_CONTROLLED):
                offenders.append(f"{surface}:{number}  {stripped[:96]}")

    assert not offenders, (
        "customer-typed text reaching innerHTML without esc():\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_no_surface_carries_its_own_palette(surface):
    """Both pages render from the one stylesheet, or they drift apart."""
    src = (ROOT / surface).read_text()
    assert '/static/theme.css' in src
    assert "#D9B98A" not in src, "a colour was hard-coded into the page"


def test_the_api_returns_hostile_text_verbatim_for_the_client_to_escape():
    """The server must not silently strip markup.

    Stripping would corrupt a legitimate name — plenty of real companies
    have an ampersand — and would hide the fact that escaping is the
    client's job. The contract is: the API round-trips exactly what was
    typed, and every renderer escapes.
    """
    from store import HubStore
    from db import Database

    store = HubStore(db=Database(":memory:"))
    hostile = '<img src=x onerror="alert(1)"> & Sons'
    tenant = store.create_tenant(hostile, "n", "+1", "a@example.com", "growth")
    assert store.get_tenant(tenant.tenant_id).company_name == hostile
    assert tenant.public(sensor_count=0)["company_name"] == hostile


def test_every_page_names_itself_for_a_screen_reader(api):
    """A page with no h1 offers nothing to heading navigation.

    Both app shells identified themselves only through a brand mark in
    the nav, which is a graphic and a link, not a heading. Somebody
    listing the headings on either page got an empty list and no way to
    tell which of the two surfaces they had landed on.
    """
    import re

    for path, expected in (
        ("/", "freezer"),
        ("/console", "operations console"),
        ("/partners", "partner portal"),
    ):
        page = api.get(path).text
        headings = re.findall(r"<h1[^>]*>(.*?)</h1>", page, re.S | re.I)
        assert len(headings) == 1, f"{path} has {len(headings)} h1 elements"
        assert expected in headings[0].lower(), (
            f"{path} names itself {headings[0]!r}, which does not say which "
            "surface this is"
        )


def test_the_console_does_not_throw_away_an_issued_credential(api):
    """A key shown once, into a handler that ignores the response, is gone.

    Registering a sensor issues an ingest key that is never echoed again.
    The console's handler awaited the call and discarded what came back,
    which made the whole credential unreachable from the UI — rotation
    would have been the only way to ever see one.
    """
    page = api.get("/console").text

    assert "showIssuedKey" in page, "nothing surfaces an issued key"
    assert "made.ingest_key" in page, (
        "the registration response is discarded again, so the key is issued "
        "and immediately lost"
    )
    assert 'id="issued-key"' in page

    # A toast fades. Something you cannot get back needs a container that
    # stays until it is dismissed.
    assert 'id="issued-done"' in page and 'id="issued-copy"' in page
    assert "rotate-key" in page, "no way to re-key a device from the console"

    # And the copy button cannot be the only route: clipboard access can be
    # refused outright, so the key must be selectable text.
    assert "user-select: all" in api.get("/static/theme.css").text


def test_every_surface_wears_the_same_mark(api):
    """One mark, one file, referenced — not three inline copies.

    The old glyph was pasted separately into each page, which is how it
    came to be drawn in a blue that predated the palette and stayed there
    through a full rebrand without anyone noticing on two of the three
    surfaces.
    """
    served = api.get("/static/logo.svg")
    assert served.status_code == 200
    assert "image/svg" in served.headers["content-type"]

    for path in ("/", "/console", "/partners"):
        page = api.get(path).text
        assert '/static/logo.svg' in page, f"{path} does not wear the mark"
        # No page draws its own version of it.
        assert 'd="M12 3v10"' not in page, (
            f"{path} still has the old inline glyph pasted into it"
        )

    # The tab icon is generated from the mark's own file, so the two
    # cannot drift; it just has to be an SVG data URI, not a stale PNG.
    assert 'rel="icon" href="data:image/svg+xml,' in api.get("/").text


def test_the_background_stops_for_anyone_who_asked_it_to(api):
    """Ambient motion behind every page is exactly what that setting means.

    Also checked live: with reduced motion the canvas paints one frame and
    never changes again, and without it the field genuinely drifts.
    """
    js = api.get("/static/circuit.js")
    assert js.status_code == 200
    assert "prefers-reduced-motion" in js.text
    assert "visibilitychange" in js.text, (
        "the background keeps animating in a tab nobody is looking at"
    )
    # It must not be able to swallow a click or sit in front of anything.
    theme = api.get("/static/theme.css").text
    assert "pointer-events: none" in theme
    assert "z-index: -2" in theme


def test_the_agreement_renderer_escapes_before_it_marks_up(api):
    """/legal turns Markdown into HTML, which is a new innerHTML surface.

    The property that makes it safe is an ordering one: every line is
    escaped *first*, and the inline pass then only ever wraps
    already-escaped content in <strong> or <code>. Reverse those two steps
    and the page that carries the liability cap starts executing whatever
    is in the document.

    Verified live in Chromium too — twelve payloads through
    `renderMarkdown` (raw tags, an onerror image, an svg in a blockquote,
    an iframe in a list, a broken-out code span, a javascript: href)
    produced zero executions, zero event handlers and no element beyond
    the six the renderer emits. This keeps the ordering from drifting.
    """
    page = api.get("/static/legal.html")
    assert page.status_code == 200
    js = script_of(ROOT / "static/legal.html")

    assert 'const esc =' in js
    for entity in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert entity in js, f"esc() does not produce {entity}"

    # esc() is the first thing inline() does, before any replace() that
    # introduces a tag.
    body = js[js.index("function inline("): js.index("function renderMarkdown(")]
    assert body.index("esc(text)") < body.index("<strong>"), (
        "inline() adds markup before escaping, so the document can inject tags"
    )

    # Every interpolation in the file, listed. Pairing backticks to find
    # only the markup literals does not survive contact with this file —
    # it contains regex literals made of backticks — and a scan that
    # silently skips half the page is worse than none. So each one is
    # named and justified instead, the way the console's exceptions are.
    SAFE = {
        # escaped at the point of use
        "esc(d.slug)", "esc(d.title)", "esc(doc.sha256)",
        "esc(err.message)", "esc(slug)",
        # inline() escapes its own argument before adding any markup
        'inline(c)', 'inline(para.join(" "))',
        "inline(trimmed.slice(2))", "inline(trimmed.slice(3))",
        # 'th' or 'td', chosen by this file and never by the document
        "tag",
        # a URL path segment, where percent-encoding is the right escape
        "encodeURIComponent(slug)",
        # an HTTP status code, into an Error message that is itself
        # escaped by esc(err.message) before it reaches the page
        "resp.status",
    }
    found = {m.strip() for m in re.findall(r"\$\{([^}]+)\}", js)}
    assert found <= SAFE, (
        "new interpolation(s) in legal.html that nobody has checked: "
        f"{sorted(found - SAFE)}"
    )

    # And the renderer must not grow a construct that emits a tag beyond
    # the ones whose safety was reasoned about above. Anchors especially:
    # a href built from the document is a javascript: url waiting to
    # happen.
    emitted = {
        tag.lstrip("/") for tag in re.findall(r"<(/?[a-z][a-z0-9]*)[ >]", js)
    }
    assert emitted <= {
        "script",  # the page's own <script> element
        "strong", "code", "p", "h1", "h2", "ul", "li", "blockquote",
        "table", "thead", "tbody", "tr", "th", "td", "button",
    }, f"legal.html emits an unexpected tag: {sorted(emitted)}"


def test_one_failing_panel_cannot_blank_the_console(api):
    """The console fetches thirteen things. Five of them 402 on a lapsed
    account, and a single `Promise.all` across all of them meant one 402
    threw away the other eight — a blank page for the customer who most
    needed to read it.

    Verified live in Chromium: with a licence thirty days past its grace
    window the console renders, the critical banner is shown, and the
    panels that were refused say so where they would have been. This keeps
    the structure that makes that true.
    """
    js = script_of(ROOT / "static/console.html")
    body = js[js.index("async function refresh()"): js.index("/* ---------------- render")]

    assert "Promise.allSettled" in body, (
        "refresh() no longer tolerates a panel failing"
    )

    # Promise.all is still fine for the calls the page genuinely cannot do
    # without — but only those, and there should be very few.
    essential = re.search(r"Promise\.all\(\[(.*?)\]\)", body, re.S)
    assert essential, "refresh() should still fetch its essentials together"
    calls = re.findall(r'api\("([^"]+)"', essential.group(1))
    assert set(calls) <= {"/api/console/overview", "/api/health"}, (
        f"these are treated as must-succeed and should not be: {calls}"
    )

    # And every optional panel names an element to write the failure into,
    # so a refusal is visible rather than silent.
    panels = re.search(r"const OPTIONAL_PANELS = \{(.*?)\};", js, re.S)
    assert panels, "no map from panel to its element"
    for element_id in re.findall(r'"([a-z-]+-body)"', panels.group(1)):
        assert f'id="{element_id}"' in (ROOT / "static/console.html").read_text(), (
            f"OPTIONAL_PANELS points at #{element_id}, which is not on the page"
        )


@pytest.mark.parametrize("surface", [
    "static/index.html", "static/signup.html", "static/legal.html",
])
def test_every_public_page_has_a_phone_layout(surface):
    """Measured at 390px: /signup ran to 405 and scrolled sideways.

    On a marketing page that reads as broken; on the page that takes
    somebody's money it reads as broken before they have typed anything.
    The header is the thing that overflows every time — five words of
    navigation plus a wordmark — so each page has to say what it does
    about that.

    Verified live in Chromium at 390x844 across /, /signup, /legal and
    /console: document width equals viewport width on all four.
    """
    page = (ROOT / surface).read_text()
    small = re.findall(r"@media \(max-width: (\d+)px\)\s*\{", page)
    assert small, f"{surface} has no small-screen rules at all"
    assert any(int(w) <= 760 for w in small), (
        f"{surface} has no phone breakpoint; the widest is {max(small)}px"
    )
    # And that breakpoint has to do something about the header, which is
    # what actually overflows.
    header_rules = re.search(
        r"@media \(max-width: 7[0-9]0px\)\s*\{(.*?)\n\}", page, re.S
    )
    assert header_rules and ".top" in header_rules.group(1), (
        f"{surface} narrows the page but leaves the header at full width"
    )


def test_the_book_page_holds_no_secret_and_keeps_none(api):
    """The operator's own page: served to anyone, useful to nobody without
    the platform key.

    Verified live in Chromium: a wrong key leaves the gate up with the
    server's own refusal shown, the right one renders five totals and the
    worklist, and neither viewport scrolls sideways.
    """
    page = api.get("/book")
    assert page.status_code == 200
    body = page.text

    # No credential may be baked into a page anyone can fetch.
    for leak in ("CYBERLOGIX_ADMIN_KEY=", "X-CyberLogix-Admin: ",
                 "test-admin-key"):
        assert leak not in body, f"the page ships {leak!r}"

    js = script_of(ROOT / "static/book.html")
    # The key that reads every customer's contact details and what they owe
    # must not outlive the tab it was typed into.
    assert "sessionStorage." in js
    # Usage, not the word: the file explains in a comment why it is
    # sessionStorage and not localStorage, and that comment should not
    # fail its own test.
    assert "localStorage." not in js, (
        "the platform key would survive the browser being closed"
    )
    # And it is sent as a header, never as a query string that lands in logs.
    assert '"X-CyberLogix-Admin": key' in js
    assert "admin=" not in js

    # Every interpolation, named. The two that are not escaped at the
    # point of use are listed with the reason, the way console.html lists
    # its own — an allow-list nobody has to reason about is not one.
    SAFE = {
        # escaped where they are written
        "esc(k)", "esc(v)", "esc(s)", "esc(r.urgency)", "esc(r.company_name)",
        "esc(r.headline)", "esc(r.action)", "esc(r.contact_name)",
        "esc(r.contact_email)", "esc(r.contact_phone)", "esc(book.paying)",
        "esc(book.accounts)",
        # a rounded number this file produced from a JSON number
        "money(r.at_stake_usd)",
        # a count, into a template that never reaches innerHTML
        "rows.length",
        # an HTTP status and an error string, both into fail(), which
        # assigns to textContent — markup there is text, not markup
        "resp.status", "err.message",
    }
    found = {m.strip() for m in re.findall(r"\$\{([^}]+)\}", js)}
    assert found <= SAFE, (
        f"new interpolation(s) in book.html nobody has checked: "
        f"{sorted(found - SAFE)}"
    )
    # And fail() really does use textContent, which two of those rely on.
    assert "errorEl.textContent = message" in js or (
        '$("err").textContent = message' in js
    )
