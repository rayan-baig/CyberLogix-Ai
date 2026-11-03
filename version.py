"""The release number, read from the VERSION file at the repository root.

One file holds it so the health check, the console's About card, the API
docs and CHANGELOG.md can only ever disagree in a way a test notices
(tests/test_version.py). Bump VERSION and add a CHANGELOG.md entry
together.
"""

from __future__ import annotations

import logging
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent / "VERSION"
CHANGELOG_FILE = Path(__file__).resolve().parent / "CHANGELOG.md"


def read_version(path: Path = VERSION_FILE) -> str:
    """The version string, or "unknown" if the file did not ship.

    Not a crash: a missing file is a packaging mistake, and the service
    that watches freezers should still start. The Docker build fails if
    the file is missing, so this only happens outside the image.
    """
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        logging.getLogger("cyberlogix").warning(
            "VERSION file not found at %s; reporting 'unknown'.", path)
        return "unknown"
    return text or "unknown"


__version__ = read_version()
