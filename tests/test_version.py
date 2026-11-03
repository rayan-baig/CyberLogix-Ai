"""One release number, and a changelog entry that says what is in it.

The number lives in VERSION. The health check, the API docs and the
console's About card read it from there, CHANGELOG.md must describe it,
and the image must carry both files.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = (ROOT / "VERSION").read_text().strip()
CHANGELOG = (ROOT / "CHANGELOG.md").read_text()
HEADING = re.compile(r"^## (\d+\.\d+\.\d+)\b", re.M)


def test_the_version_is_a_plain_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION), VERSION


def test_the_version_has_a_changelog_entry():
    assert VERSION in HEADING.findall(CHANGELOG), (
        f"VERSION says {VERSION} but CHANGELOG.md has no '## {VERSION}' "
        "entry. Add one saying what changed.")


def test_the_newest_changelog_entry_is_the_current_version():
    """Newest first: the top entry is the release that is running."""
    assert HEADING.findall(CHANGELOG)[0] == VERSION


def test_changelog_versions_are_newest_first_and_never_repeat():
    found = HEADING.findall(CHANGELOG)
    as_tuples = [tuple(int(n) for n in v.split(".")) for v in found]
    assert as_tuples == sorted(as_tuples, reverse=True)
    assert len(set(found)) == len(found)


def test_the_health_check_reports_the_version(api):
    body = api.get("/api/health").json()
    assert body["version"] == VERSION
    assert api.get("/api").json()["version"] == VERSION


def test_the_api_docs_carry_the_version(api):
    assert api.get("/openapi.json").json()["info"]["version"] == VERSION


def test_a_missing_version_file_does_not_stop_the_service(tmp_path):
    from version import read_version

    assert read_version(tmp_path / "VERSION") == "unknown"
    (tmp_path / "VERSION").write_text("  9.8.7\n")
    assert read_version(tmp_path / "VERSION") == "9.8.7"


def test_the_console_shows_the_version_in_its_about_card():
    page = (ROOT / "static" / "console.html").read_text()
    assert 'id="find-about"' in page
    assert 'data-group="setup"' in page.split('id="find-about"')[1][:80]
    assert '$("about-version").textContent' in page
    assert "health.version" in page


def test_the_image_ships_the_version_and_the_changelog():
    """.dockerignore drops every *.md but the README; these must survive."""
    ignored = [line.strip() for line in
               (ROOT / ".dockerignore").read_text().splitlines()
               if line.strip() and not line.startswith("#")]
    assert "VERSION" not in ignored
    assert "*.md" in ignored and "!CHANGELOG.md" in ignored
    # A later rule wins, so the exception must come after the exclusion.
    assert ignored.index("!CHANGELOG.md") > ignored.index("*.md")

    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY . ." in dockerfile
    assert "test -s /app/VERSION" in dockerfile
    assert "test -s /app/CHANGELOG.md" in dockerfile


def test_render_builds_from_that_dockerfile():
    blueprint = (ROOT / "render.yaml").read_text()
    assert "runtime: docker" in blueprint
    assert "dockerfilePath: ./Dockerfile" in blueprint
