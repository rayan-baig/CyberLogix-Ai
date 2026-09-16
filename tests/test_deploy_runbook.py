"""The runbook, checked against the application it describes.

Documentation drifts silently, and a deploy runbook that drifts is read
once, on the day it matters, by somebody who then does the wrong thing.
Every setting it names has to be one the code reads, and every claim it
makes about what runs on its own has to be a thing that runs on its own.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = (ROOT / "DEPLOY.md").read_text()
# Prose wraps, so a phrase split across two lines is still the phrase.
FLAT = " ".join(RUNBOOK.split())


def test_every_setting_it_names_is_one_the_code_reads():
    """A runbook that tells you to set something nothing reads is worse
    than one that omits it: you believe you have configured it."""
    named = set(re.findall(r"\b(CYBERLOGIX_[A-Z_]+|TWILIO_[A-Z_]+|SMTP_[A-Z_]+"
                           r"|MAIL_[A-Z_]+|STRIPE_[A-Z_]+|PUBLIC_BASE_URL)\b",
                           RUNBOOK))
    sources = "".join(
        path.read_text() for path in ROOT.glob("*.py")
    ) + (ROOT / ".env.example").read_text()

    unknown = sorted(n for n in named if n not in sources)

    assert not unknown, f"the runbook names settings nothing reads: {unknown}"


def test_it_is_linked_from_the_readme():
    """Nobody finds a runbook that only exists as a filename."""
    readme = (ROOT / "README.md").read_text()

    assert "DEPLOY.md" in readme


def test_the_commands_it_gives_exist():
    """readiness.py is the one it tells you to run at every step."""
    assert (ROOT / "readiness.py").exists()
    assert "python readiness.py" in RUNBOOK
    assert (ROOT / "Dockerfile").exists()


def test_the_endpoints_it_quotes_are_real():
    """Every path in a curl example has to be a route."""
    sources = "".join(path.read_text() for path in ROOT.glob("*.py"))
    quoted = set(re.findall(r"/api/[a-z0-9/_-]+", RUNBOOK))

    missing = sorted(
        path for path in quoted
        if path.split("/api/")[1].split("/")[0] not in sources
    )

    assert not missing, f"the runbook quotes routes that do not exist: {missing}"


def test_it_does_not_promise_self_repair():
    """The one claim that would be a lie. Nothing here fixes a bug."""
    assert "No program repairs its own logic" in FLAT


def test_it_says_the_backups_must_leave_the_machine():
    """The single most common way a backup strategy is not one."""
    assert "off the machine" in FLAT


def test_it_still_says_a_lawyer_has_to_read_the_agreements():
    """Every document says so itself. The runbook must not be the place
    that quietly stops saying it."""
    assert "lawyer" in FLAT

    import legal

    if legal.DISCLAIMER.startswith("Draft"):
        assert "draft disclaimer" in FLAT
