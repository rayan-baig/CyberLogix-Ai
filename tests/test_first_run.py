"""What a customer sees the first time, and the number that has to be right.

A restaurant signed up, told us so on the form, and the audit trail
recorded it — and then every sector picker in their console opened on
"CyberTech Data Centers", because the options were rendered from a list
and nothing ever selected one, so the browser took the first.

That is not a cosmetic default. Registering a walk-in freezer under the
data-centre sector monitors it against a 78F limit instead of 32F: it
alarms long after the food is gone, and the product's one promise fails
silently in a way the customer cannot see. The worst version of this bug
is the one where the customer never notices they picked wrong.

The sector is on the tenant from sign-up. The console just had to use it.
"""

import re
from pathlib import Path


from store import INDUSTRY_PROFILES

CONSOLE = Path(__file__).resolve().parent.parent / "static" / "console.html"


def test_the_gap_between_the_two_sectors_is_the_size_of_the_bug():
    """Why this matters, asserted rather than claimed.

    If these limits ever converge, the rest of this file is about a
    cosmetic default. While they do not, it is about a freezer.
    """
    restaurant = INDUSTRY_PROFILES["restaurant"]["danger_above"]
    datacentre = INDUSTRY_PROFILES["cybersecurity"]["danger_above"]

    assert datacentre - restaurant > 40, (
        "the wrong sector no longer means a wildly wrong alarm limit; "
        "this test file needs rewriting rather than deleting"
    )


def test_the_sign_up_sector_reaches_the_console_payload(api, mailbox):
    """The console cannot use what the API does not send."""
    resp = api.post("/api/signup", json={
        "company_name": "Harbour Street Kitchen", "full_name": "Sam Reyes",
        "email": "sam@harbour.example", "password": "correct-horse-battery",
        "contact_phone": "+1-555-0142", "industry_vertical": "restaurant",
    })
    assert resp.status_code == 201, resp.text
    key = {"X-CyberLogix-Key": resp.json()["api_key"]}

    overview = api.get("/api/console/overview", headers=key).json()

    assert overview["tenant"]["industry_vertical"] == "restaurant"


def test_the_industry_list_carries_what_the_placeholders_need(api):
    """The example unit shown in the form comes from the sector, so a
    cryostorage customer is not told to type a restaurant's walk-in."""
    industries = api.get("/api/industries").json()["industries"]

    assert industries, "no industries at all"
    for entry in industries:
        assert entry["asset_noun"], entry["vertical"]
        assert entry["vertical"] in INDUSTRY_PROFILES


# ---- the console's own logic -------------------------------------------
#
# Asserted against the source because the behaviour lives in the browser.
# Playwright drives it end to end during development; these keep the
# pieces that made it work from being quietly removed.


def script():
    return CONSOLE.read_text()


def test_the_console_selects_a_sector_rather_than_taking_the_first():
    source = script()

    assert "function preferSector" in source, (
        "the console no longer chooses a sector, so the browser will take "
        "whichever option happens to be first in the list"
    )
    assert "industry_vertical" in source


def test_it_is_wired_into_both_of_the_calls_that_race():
    """The bug behind the bug.

    The first overview lands before the industry list does. A version
    wired only into the overview saw zero industries, matched nothing,
    and left the picker exactly where it started — which looked
    identical to not having written it at all.
    """
    source = script()
    calls = len(re.findall(r"preferSector\(", source))

    assert calls >= 3, (
        f"preferSector appears {calls} times; it needs its definition and "
        "both callers, or it silently depends on which fetch wins"
    )
    assert "preferSector(state.data)" in source, "not called after industries load"
    assert "preferSector(overview)" in source, "not called after the overview lands"


def test_it_gives_up_quietly_when_it_has_nothing_to_choose_from():
    source = script()
    body = source.split("function preferSector")[1].split("\n}")[0]

    assert "!state.industries" in body, (
        "without this guard the loser of the race throws or picks wrongly"
    )
    assert "if (!overview) return;" in body


def test_it_never_overrides_a_choice_the_customer_has_made():
    """refresh() runs on a timer. Re-selecting every few seconds would
    yank the dropdown back from somebody halfway through registering a
    unit in a second sector."""
    source = script()
    body = source.split("function preferSector")[1].split("\n}")[0]

    assert "if (state.sectorChosen) return;" in body
    assert "state.sectorChosen = true;" in body


def test_it_prefers_what_they_said_then_what_they_run():
    """An estate that grew past its sign-up answer should open on itself."""
    body = script().split("function preferSector")[1].split("\n}")[0]

    assert "[stated, dominant]" in body, (
        "the order matters: what they told us beats what we inferred"
    )


def test_the_example_unit_follows_a_manual_sector_change():
    """Once somebody picks a different sector by hand the form has to
    stay coherent with what they chose."""
    source = script()

    assert "function showSectorExample" in source
    assert '$("add-vertical").addEventListener("change"' in source


def test_the_example_is_a_placeholder_and_never_a_value():
    """So it can never overwrite something already typed."""
    body = script().split("function showSectorExample")[1].split("\n}")[0]

    assert ".placeholder =" in body
    assert ".value =" not in body, (
        "this writes a value into the form and will clobber typed input"
    )
