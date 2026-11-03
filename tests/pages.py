"""A page's source as a reader of it sees it: markup, script and style.

Each page's own script and stylesheet are files under static/ (the
Content-Security-Policy refuses inline ones), but most of what the tests
here check is about a page as a whole -- that the console calls an
endpoint, that the sign-up form keeps the session, that a card is wired
up. This puts the page's own files back where its <script src> and
<link rel="stylesheet"> tags name them, so those checks read the same
text they always did. Shared files (theme.css, circuit.js, motion.js,
utilities.css, pricing.js) are left as links: they are not the page's.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"

# Files that more than one page loads, which belong to no page in particular.
SHARED = {"theme.css", "pages.css", "motion.css", "utilities.css",
          "fonts-embedded.css", "circuit.js", "motion.js", "pricing.js",
          "api-docs.js", "sw.js"}


def own_files(source: str) -> list:
    """The /static files a page's markup loads that belong to it alone."""
    found = re.findall(r'(?:src|href)="/static/([a-z0-9-]+\.(?:js|css))"', source)
    return [name for name in found if name not in SHARED]


def page_source(path) -> str:
    source = Path(path).read_text(encoding="utf-8")
    for name in own_files(source):
        body = (STATIC / name).read_text(encoding="utf-8")
        if name.endswith(".js"):
            source = source.replace(
                f'<script src="/static/{name}"></script>', f"<script>\n{body}</script>")
        else:
            source = source.replace(
                f'<link rel="stylesheet" href="/static/{name}">', f"<style>\n{body}</style>")
    return source


def served_page(api, path: str) -> str:
    """A page as the server sends it, with its own files put back in.

    The same as page_source, but through the app: for the pages whose
    markup is filled in when served (the landing figures, an industry's
    words), and to prove the files the page names are really served.
    """
    source = api.get(path).text
    for name in own_files(source):
        resp = api.get(f"/static/{name}")
        assert resp.status_code == 200, f"{path} loads /static/{name}, which is not served"
        if name.endswith(".js"):
            source = source.replace(
                f'<script src="/static/{name}"></script>', f"<script>\n{resp.text}</script>")
        else:
            source = source.replace(
                f'<link rel="stylesheet" href="/static/{name}">', f"<style>\n{resp.text}</style>")
    return source
