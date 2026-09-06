"""The deployment artefacts, kept honest by something that fails.

The Dockerfile used to list its modules by hand and had fallen thirteen
behind the application: the image would have crashed on import the first
time anybody deployed it, and nothing in the repository would have said
so. An explicit manifest is only safe when something breaks as it drifts.
These are that something.
"""

import ast
import fnmatch
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def repo_modules():
    """Every top-level module the application is made of."""
    return {
        path.stem
        for path in ROOT.glob("*.py")
        if path.stem not in {"seed_demo", "conftest"}
    }


def dockerignore_patterns():
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def excluded_from_image(relative: str) -> bool:
    """Whether .dockerignore keeps a path out of the build context."""
    excluded = False
    for pattern in dockerignore_patterns():
        negate = pattern.startswith("!")
        body = pattern[1:] if negate else pattern
        body = body.rstrip("/")
        if fnmatch.fnmatch(relative, body) or relative.startswith(body + "/"):
            excluded = not negate
    return excluded


def test_every_module_the_app_imports_reaches_the_image():
    """The bug this exists for: a module missing from the image."""
    modules = repo_modules()
    for name in sorted(modules):
        assert not excluded_from_image(f"{name}.py"), (
            f"{name}.py is excluded from the build context but the "
            "application imports it — the container would crash on import."
        )


def test_the_dockerfile_does_not_list_modules_by_hand():
    """A hand-written manifest is what fell behind last time."""
    dockerfile = (ROOT / "Dockerfile").read_text()
    copied_by_name = re.findall(r"^COPY\s+(.*\.py)", dockerfile, re.M)
    assert not copied_by_name, (
        "The Dockerfile names Python files individually again: "
        f"{copied_by_name}. Copy the tree and exclude with .dockerignore."
    )


def test_the_image_excludes_what_must_never_ship():
    for path in ("tests/test_deployment.py", ".env", "cyberlogix.db",
                 ".venv/lib/python3.11/site-packages/x.py"):
        assert excluded_from_image(path), f"{path} would be baked into the image."


def test_the_image_runs_as_an_unprivileged_user():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^USER\s+cyberlogix", dockerfile, re.M)
    # And the USER line comes after the last RUN that needs root.
    user_at = dockerfile.index("\nUSER ")
    assert "useradd" in dockerfile[:user_at]


def environment_variables_read_by_the_code():
    """Every setting the application actually reads.

    Two passes, because one is not enough. The AST walk catches the direct
    os.environ lookups; the literal scan catches the ones passed through a
    helper — costs.py reads its caps via `_int(name, default)`, and an
    AST-only check reported those as documented when they were not.
    """
    found = set()
    for path in ROOT.glob("*.py"):
        if path.stem == "seed_demo":
            continue
        source = path.read_text()
        found |= set(re.findall(r'["\'](CYBERLOGIX_[A-Z0-9_]+)["\']', source))
        found |= set(re.findall(r'["\'](TWILIO_[A-Z0-9_]+)["\']', source))
        tree = ast.parse(source)
        for node in ast.walk(tree):
            # os.environ.get("X") and os.environ.get("X", default)
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "environ"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)):
                found.add(node.args[0].value)
            # os.environ["X"]
            if (isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "environ"
                    and isinstance(node.slice, ast.Constant)):
                found.add(node.slice.value)
    return found


def test_every_setting_is_documented():
    """Undocumented configuration is configuration nobody sets correctly."""
    documented = {
        line.split("=", 1)[0].strip()
        for line in (ROOT / ".env.example").read_text().splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    read = environment_variables_read_by_the_code()

    # PORT is set by the platform rather than the operator, but it is
    # documented anyway; GEMINI_API_KEY is read by the vendor SDK.
    missing = sorted(read - documented)
    assert not missing, (
        f"These settings are read by the code but absent from .env.example: "
        f"{missing}"
    )


def test_nothing_documented_is_dead():
    """A setting nobody reads is a setting somebody will waste an hour on."""
    documented = {
        line.split("=", 1)[0].strip()
        for line in (ROOT / ".env.example").read_text().splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    read = environment_variables_read_by_the_code()
    # The SDK reads this one itself rather than through our code.
    read |= {"GEMINI_API_KEY"}

    stale = sorted(documented - read)
    assert not stale, (
        f"These settings are documented but nothing reads them: {stale}"
    )
