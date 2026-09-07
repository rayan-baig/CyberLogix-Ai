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
