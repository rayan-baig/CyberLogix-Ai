"""Run the README's quick start, and check the story it tells.

Same family as the onboarding guide: instructions we publish that nobody
executes. This one was wrong in a quieter way — every command returned
2xx, so any test that only checked status codes would have passed.

The README's step 3 said "Pulse it" with 47°F and step 4 said "Now break
it" with 61°F. The country-club limit is 32°F, so step 3 broke it too:
the reader is told the fourth command is the one that raises an alarm,
follows along, and has already opened an incident and (with Twilio
configured) texted somebody by the third. A monitoring product whose
own tutorial cries wolf has taught the wrong lesson on the first page.

So the assertion here is not that the commands run. It is that the
narrative around them is true.
"""

import json
import re
import shlex
from pathlib import Path

import pytest

from store import STORE

README = Path(__file__).resolve().parent.parent / "README.md"


def quickstart_commands():
    """The curl invocations from the README's quick-start block."""
    block = re.search(
        r"^## Quick start\n\n```bash\n(.*?)^```", README.read_text(),
        re.MULTILINE | re.DOTALL,
    )
    assert block, "the README has no quick-start block"
    # Comments first — they contain prose that would otherwise be parsed
    # as arguments — then the shell line continuations, then split at each
    # line that starts a new invocation. The JSON bodies span lines of
    # their own, which is why the split is anchored to the line start.
    lines = [
        line for line in block.group(1).splitlines()
        if not line.lstrip().startswith("#")
    ]
    joined = "\n".join(lines).replace("\\\n", " ")
    return [
        "curl " + chunk.strip()
        for chunk in re.split(r"(?m)^curl ", joined)[1:]
    ]


def _run(api, command):
    parts = shlex.split(command)
    method, url, headers, body = "GET", None, {}, None
    index = 1
    while index < len(parts):
        token = parts[index]
        if token == "-X":
            method, index = parts[index + 1], index + 2
        elif token == "-H":
            name, _, value = parts[index + 1].partition(":")
            headers[name.strip()] = value.strip()
            index += 2
        elif token == "-d":
            body, index = json.loads(parts[index + 1]), index + 2
        elif token.startswith("localhost:8080"):
            url, index = token[len("localhost:8080"):], index + 1
        else:
            raise AssertionError(f"unparsed token {token!r} in: {command}")
    assert url, command
    return api.request(method, url, headers=headers, json=body)


@pytest.fixture()
def walkthrough(api, mailbox):
    """Every quick-start command, in order, with $KEY threaded through."""
    responses = []
    key = None
    for command in quickstart_commands():
        if key:
            command = command.replace("$KEY", key)
        resp = _run(api, command)
        responses.append(resp)
        if key is None and resp.status_code < 400:
            # Only the sign-up step issues one, and it is the first.
            # Sniffing every response for the string would pick up the
            # sensor's own ingest credential and thread the wrong one on.
            key = resp.json().get("api_key")
    return responses


def test_every_command_runs(walkthrough):
    for index, resp in enumerate(walkthrough, start=1):
        assert resp.status_code < 400, (
            f"quick-start step {index} failed with {resp.status_code}: "
            f"{resp.text}"
        )


def test_the_signup_step_returns_a_working_session_as_it_claims(walkthrough):
    """"it returns a working session as well as the api_key"."""
    body = walkthrough[0].json()
    assert body["api_key"].startswith("clx_")
    assert body["token"].startswith("cls_")


def test_the_safe_pulse_is_actually_safe(api, walkthrough):
    """Step 3 says nothing happens. It has to be true.

    It was not: 47°F against a 32°F limit opened an incident two steps
    before the tutorial says anything is wrong.
    """
    third = walkthrough[2].json()
    assert "incident_id" not in third, (
        f"the README's 'pulse it' step opens an incident: {third}"
    )

    # And across the whole walkthrough there is exactly one incident: the
    # one step 4 is supposed to cause. Counting after all four steps is
    # deliberate — it catches a step 3 that breaches whether or not the
    # response happens to show it.
    tenant_id = STORE.list_tenants()[0].tenant_id
    incidents = STORE.incidents_for(tenant_id)
    assert len(incidents) == 1, (
        f"the tutorial opens {len(incidents)} incidents; only step 4 should"
    )
    assert incidents[0].temperature_fahrenheit == 61.0


def test_the_breaking_pulse_actually_breaks(walkthrough):
    """Step 4 says it opens an incident and texts the roster."""
    fourth = walkthrough[3].json()
    assert "incident_id" in fourth, fourth
    assert fourth["temperature_fahrenheit"] == 61.0
    assert "32.0" in fourth["breach_details"]
    # "and texts the roster" — composed and attempted, whatever Twilio says.
    assert fourth["dispatched_sms_text"]
    assert fourth["sms_delivery"]["to"]
