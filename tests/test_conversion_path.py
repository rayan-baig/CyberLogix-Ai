"""The route from a trial to a paying customer, end to end.

The acquisition half of this product -- land, read an industry page, sign
up, run a trial, receive a six-stage nurture email -- was complete and
needed nobody. The conversion half was a set of dead ends, each one
invisible from the code on either side of it because each side was
correct on its own:

- the emails promised a one-click upgrade, and no page had the click;
- the one money control shown to trials was one that refuses trials;
- signing up said "you are signed in" and did not sign you in.

Every trial that wanted to pay landed in a human's inbox. These tests
pin each join, because each break was at a join.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = (ROOT / "static" / "console.html").read_text()
CONVERSION = (ROOT / "conversion.py").read_text()


# --- 1a: the button the emails promise ------------------------------------


def test_the_emails_promise_a_one_click_upgrade():
    """The premise. If the emails ever stop promising it, the next test is
    guarding a promise nobody makes and can be revisited."""
    assert "one click" in CONVERSION.lower() or "one-click" in CONVERSION.lower()


def test_the_console_has_the_click_the_emails_promise():
    """The bug: /api/licenses/me/plan existed and nothing called it.

    Customers were emailed "move the account onto Growth -- it takes one
    click" and there was no click. Verified in Chromium after the fix: a
    real trial, two buttons, one press, the plan read back as growth.
    """
    # A call, not a mention. The first version of this checked for the path
    # anywhere in the file and passed with the call deleted, because the
    # comment explaining the fix names the endpoint.
    assert re.search(r'api\(\s*"/api/licenses/me/plan"', CONSOLE), (
        "the conversion emails promise a one-click upgrade and the console "
        "has no caller of the endpoint that performs it")


def test_the_upgrade_is_only_offered_to_an_owner_on_a_trial():
    """The endpoint requires an owner. Offering anyone else a button is
    offering them a 403; they are told who can do it instead."""
    block = CONSOLE[CONSOLE.index("function upgradeBlock"):]
    block = block[:block.index("\n}\n")]

    assert 'tenant.plan !== "trial"' in block, "shown to accounts already paying"
    assert 'role === "owner"' in block, "shown to people the endpoint refuses"
    assert "data-upgrade" in block


def test_the_confirmation_survives_the_refresh_it_triggers():
    """A success refreshes the console and rebuilds the card, and the trial
    block is no longer drawn -- so a message written into it vanished with
    it. Measured: the plan changed and the owner saw the section disappear.
    The confirmation is drawn from state that outlives the redraw."""
    block = CONSOLE[CONSOLE.index("function upgradeBlock"):]
    block = block[:block.index("\n}\n")]

    assert "state.justUpgradedTo" in block
    assert "state.justUpgradedTo = plan" in CONSOLE
