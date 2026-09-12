"""Run the instructions we actually send people.

The sign-up response and the welcome email both hand a new customer a
short list of commands, with a credential printed in them. Step one did
not work: it sent only the tenant API key at `/api/contacts`, which
requires an operator role, so the very first instruction we gave every
new customer answered

    401 Missing bearer token. Sign in at /api/accounts/login.

Nothing caught it, because every test asserted on the *shape* of the
guide — that it had four steps, that the key was in them — and no test
had ever run one. The gate itself is right: deciding who gets phoned at
3am is a person's decision, not something a machine key should do. The
bug was printing a command the credential could not run.

So this file parses each command out of the guide and executes it. It is
the only kind of test that could have found this, and the only kind that
will find the next one.
"""

import json
import re
import shlex

import pytest

from signup import first_steps
from store import STORE


def _run(api, command):
    """Execute one generated curl line against the app. Returns a response.

    A general curl parser would be a bad idea. This one only has to read
    the lines this codebase generates, and it fails loudly on anything it
    does not recognise rather than quietly skipping it — a parser that
    silently ignores a step it cannot read would reintroduce exactly the
    blind spot this file exists to remove.
    """
    parts = shlex.split(command)
    assert parts[0] == "curl", command

    method = "GET"
    url = None
    headers = {}
    body = None

    index = 1
    while index < len(parts):
        token = parts[index]
        if token == "-X":
            method = parts[index + 1]
            index += 2
        elif token == "-H":
            name, _, value = parts[index + 1].partition(":")
            headers[name.strip()] = value.strip()
            index += 2
        elif token == "-d":
            body = json.loads(parts[index + 1])
            index += 2
        elif token.startswith("$HOST"):
            url = token[len("$HOST"):]
            index += 1
        else:
            raise AssertionError(f"unparsed token {token!r} in: {command}")

    assert url, f"no URL in: {command}"
    return api.request(method, url, headers=headers, json=body)


@pytest.fixture()
def signed_up(api, mailbox):
    resp = api.post(
        "/api/signup",
        json={
            "company_name": "Northgate Foods",
            "full_name": "Dana Reyes",
            "email": "dana@northgate.example",
            "password": "correct-horse-battery",
            "contact_phone": "+1-555-0100",
            "industry_vertical": "restaurant",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_every_command_in_the_signup_response_actually_works(api, signed_up):
    """The whole point. Four commands, run in order, all must succeed."""
    steps = signed_up["next_steps"]["steps"]
    assert steps, "the guide is empty"

    for number, step in enumerate(steps, start=1):
        command = step.get("command")
        if command is None:
            continue
        resp = _run(api, command)
        assert resp.status_code < 400, (
            f"step {number} ({step['title']}) fails with "
            f"{resp.status_code}: {resp.text}\n  {command}"
        )


def test_the_four_steps_do_what_they_say(api, signed_up):
    """Not just 2xx: the estate has to end up in the promised state."""
    tenant_id = signed_up["tenant"]["tenant_id"]
    for step in signed_up["next_steps"]["steps"]:
        if step.get("command"):
            _run(api, step["command"])

    assert STORE.contacts_for(tenant_id), "step 1 added nobody to the roster"
    assert STORE.sensors_for(tenant_id), "step 2 registered no sensor"
    # "This opens an incident and texts the roster" — so it must have.
    assert STORE.incidents_for(tenant_id), "step 4 opened no incident"


def test_a_step_with_no_command_says_where_to_go_instead(api):
    """The emailed guide has no session to print, so it cannot show the
    roster command. It must point somewhere real rather than print one
    that would 401."""
    guide = first_steps("clx_whatever", "restaurant", bearer_token=None)
    roster = guide["steps"][0]

    assert roster["command"] is None
    assert roster["where"] == "$HOST/console"
    assert "needs a signed-in person" in roster["detail"]


def test_the_welcome_email_prints_no_command_that_would_fail(
    api, mailbox, signed_up
):
    """Every curl line in the email, run against the app."""
    body = mailbox[0].get_content()
    commands = re.findall(r"^\s*(curl -X POST \S+ .+)$", body, re.MULTILINE)
    assert commands, f"no commands in the welcome email:\n{body}"

    for command in commands:
        # The email has the real host substituted in; put the token back
        # so the parser can aim it at the test client.
        resp = _run(api, command.replace("https://hub.example", "$HOST")
                    .replace("http://testserver", "$HOST"))
        assert resp.status_code < 400, (
            f"the welcome email prints a command that fails with "
            f"{resp.status_code}: {resp.text}\n  {command}"
        )


def test_the_email_sends_them_to_the_console_for_the_roster(
    api, mailbox, signed_up
):
    body = mailbox[0].get_content()
    assert "/console" in body
    # And never a contacts command it cannot authorise.
    assert "api/contacts" not in body
