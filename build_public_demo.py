"""Package the console as one HTML file anybody can open.

Not a mockup and not a screenshot: this is `static/console.html`,
`static/theme.css` and `static/circuit.js` exactly as the product ships
them, with `fetch` answering out of responses captured from a real
seeded run instead of going to a server. Every figure on the page is one
the application actually produced.

It exists because the product needs somewhere to be *seen*. A private
link that asks people to sign in first is not a demo, it is a meeting
request, and the whole point is to be forwardable to somebody who has
never heard of any of this.

    python build_public_demo.py

writes `docs/index.html`, which GitHub Pages serves at
https://<user>.github.io/<repo>/ once Pages is switched on for the repo.

Self-contained on purpose. It seeds a throwaway database in a temporary
directory, drives the real application through Starlette's test client,
and throws the database away again, so the page can be rebuilt from a
clean checkout with no server running and nothing left behind.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
# Overridable, so a test can build for real without rewriting the two
# tracked files in docs/. A test that dirties the working tree is one
# that eventually gets blamed for a change nobody made.
DOCS = pathlib.Path(os.environ.get("DEMO_OUT_DIR") or (ROOT / "docs"))

# What the console asks for on load. Anything missing here renders as
# "Not captured in this demo" on the card that wanted it, which is honest
# but looks broken, so the list is checked against the page below.
PATHS = [
    "/api/console/overview", "/api/health", "/api/costs?days=30",
    "/api/accounts/audit?limit=8", "/api/accounts/me", "/api/contacts",
    "/api/accounts/users", "/api/shortcuts", "/api/webhooks",
    "/api/contracts/pipeline", "/api/forecast/fleet", "/api/claims/eligible",
    "/api/contacts/preview", "/api/legal/acceptance/status",
    "/api/benchmarks", "/api/vault/attestation",
    "/api/people/alerts", "/api/people/calendar?days=180",
    "/api/people/departures", "/api/people", "/api/v1/bridge/samples",
]

BANNER = """
<div class="demo-bar" role="note">
  <strong>Live demo.</strong> The real console on a simulated estate.
  The temperatures move, the back-bar freezer runs its defrost and comes
  back, and the clubhouse walk-in keeps climbing because it is broken.
  Both sign-in boxes are already filled in &mdash; just press Sign in.
  <strong>Never type a real password into a link somebody sent you</strong>,
  here or anywhere. This page has no server behind it and sends nothing
  anywhere, but that is not something you can tell by looking, which is
  the whole reason the rule exists. Every figure is one the application
  produced, frozen, so nothing is live.
</div>
"""

# Raw, because every backslash in here belongs to JavaScript rather than
# to Python: `\.` and `\d` in a regex, `\/` in a path pattern. Python
# deprecates those as escape sequences and will make them an error, and
# the failure is silent until it is not -- the string still built, the
# demo still worked, and a warning appeared in the test run.
STUB = r"""<script>
"use strict";
/* The demo harness. Nothing below is part of the product: it stands in
   for the server so the real page can run with no backend. */
const DEMO = %s;

// Start signed out, at the gate, with the email already filled in. The
// way into a product is part of the product, and a demo that skips it is
// showing you the half that was never the hard part.
try {
  localStorage.removeItem("cyberlogix.session");
  localStorage.setItem("cyberlogix.email", "dana@blueharbor.example");
} catch (e) {}

// Fill the password in too, so that nobody ever types a real one here out
// of habit. A demo that shows a password box and waits is training people
// to do the exact thing that gets them phished, and the fact that this
// particular box is harmless is invisible to the person looking at it.
document.addEventListener("DOMContentLoaded", () => {
  const field = document.getElementById("si-password");
  if (field) field.value = "harbor-demo-2026";
});

// No service worker here: there is no /sw.js to register, and caching a
// frozen estate would be doubly wrong.
try {
  // `"serviceWorker" in navigator` stays true even when a getter returns
  // undefined, so the page walked straight into `.register`. Give it a
  // real object that does nothing instead.
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    get: () => ({
      register: () => Promise.resolve(null),
      getRegistration: () => Promise.resolve(null),
      ready: new Promise(() => {}),
      addEventListener: () => {},
    }),
  });
} catch (e) {}

function reply(body, status) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status: status || 200,
    headers: { "Content-Type": "application/json" },
  }));
}

window.fetch = function (input, init) {
  const url = new URL(typeof input === "string" ? input : input.url,
                      location.href);
  const path = url.pathname + (url.search || "");
  const method = ((init && init.method) || "GET").toUpperCase();

  // Signing in and out are the two writes the demo allows, because the
  // way in is part of what there is to look at. Any password works:
  // there is no account here to get wrong.
  if (method === "POST" && url.pathname === "/api/accounts/login") {
    const me = DEMO["/api/accounts/me"];
    return reply({ token: "demo-session", user: me && me.body.user });
  }
  if (method === "POST" && url.pathname === "/api/accounts/logout") {
    return reply({ ok: true });
  }

  // The writes somebody will actually press, answered from the running
  // simulation rather than refused. A console where every button says
  // 403 teaches the visitor that nothing works.
  // The real paths, taken from the console rather than guessed: it
  // calls /api/voice/acknowledge/<id>, not /api/incidents/<id>/...
  // The first version of this guessed, matched nothing, and every click
  // fell through to the 403 while looking like it had been handled.
  if (method === "POST"
      && /^[/]api[/]voice[/](acknowledge|resolve)[/][^/]+$/
           .test(url.pathname)) {
    const [, , , what, id] = url.pathname.split("/");
    const found = (SIM.incidents || []).find((i) => i.incident_id === id);
    if (found) {
      const stamp = new Date().toISOString().replace(/\.\d+Z$/, "Z");
      if (what === "acknowledge") {
        found.state = "acknowledged";
        found.acknowledged_at = stamp;
        found.acknowledged_by = "Dana Reyes";
      } else {
        found.state = "resolved";
        found.resolved_at = stamp;
      }
      DEMO["/api/console/overview"] = { status: 200, body: SIM };
      return reply({ incident: found });
    }
  }

  if (method !== "GET") {
    // Everything else is read-only on purpose. A demo that looked like
    // it saved something and did not would be worse than one that says
    // so.
    return reply({ detail: "This part of the demo is read-only, so this "
                           + "one does not save." }, 403);
  }

  const hit = DEMO[path] || DEMO[url.pathname];
  if (hit) return reply(hit.body, hit.status);
  return reply({ detail: "Not captured in this demo." }, 404);
};
%s
</script>
"""


def capture() -> dict:
    """Seed a throwaway estate and record what the application answers."""
    from fastapi.testclient import TestClient

    import seed_demo
    from main import app

    credentials = seed_demo.seed()
    out: dict = {}
    with TestClient(app) as client:
        signed_in = client.post("/api/accounts/login", json={
            "email": credentials["email"],
            "password": credentials["password"],
        })
        signed_in.raise_for_status()
        head = {"Authorization": f"Bearer {signed_in.json()['token']}"}

        for path in PATHS:
            got = client.get(path, headers=head)
            out[path] = {"status": got.status_code, "body": got.json()}

        overview = out["/api/console/overview"]["body"]
        # The drawer for each sensor, and the peer comparison for every
        # vertical in the estate -- whichever one dominates is the one the
        # standing card will ask for, and it is cheaper to capture all of
        # them than to reimplement the page's choice here.
        for sensor in overview.get("sensors", []):
            path = f"/api/console/sensor/{sensor['sensor_id']}"
            got = client.get(path, headers=head)
            out[path] = {"status": got.status_code, "body": got.json()}
        for vertical in sorted({s.get("industry_vertical")
                                for s in overview.get("sensors", [])
                                if s.get("industry_vertical")}):
            path = f"/api/benchmarks/{vertical}"
            got = client.get(path, headers=head)
            out[path] = {"status": got.status_code, "body": got.json()}

    bad = {p: r["status"] for p, r in out.items() if r["status"] != 200}
    if bad:
        raise SystemExit(f"Refusing to build a demo with broken panels: {bad}")
    return out


def build(data: dict, page_name: str = "console.html",
          extra_stub: str = "", banner: str = "") -> str:
    console = (ROOT / page_name).read_text() if "/" in page_name else (
        ROOT / "static" / page_name).read_text()
    theme = (ROOT / "static" / "theme.css").read_text()

    # The shipped stylesheet pulls its webfonts from fonts.googleapis.com.
    # On a page whose whole job is to be forwarded to strangers, that
    # means every person who opens it tells Google they opened it, from
    # their IP address, before a word renders. Swap the import for the
    # embedded copy so the page reaches nothing at all.
    fonts = (ROOT / "static" / "fonts-embedded.css").read_text()
    imports = [line for line in theme.splitlines()
               if line.startswith("@import url('https://fonts.googleapis.com")]
    if len(imports) != 1:
        raise SystemExit(
            f"expected one Google Fonts import in theme.css, found "
            f"{len(imports)}. The public page must not fetch anything.")
    theme = theme.replace(imports[0], fonts)
    circuit = (ROOT / "static" / "circuit.js").read_text()
    # Its own usage comment contains a literal </script>, which closes the
    # tag early when the file is inlined rather than linked. Escaping the
    # slash is inert in a comment and in a string alike.
    circuit = circuit.replace("</script", "<\\/script")

    # The mark is an <img src="/static/logo.svg">, and there is no /static
    # in a single file. A demo of the product with a broken image where
    # the logo should be is the first thing anybody looks at.
    logo = (ROOT / "static" / "logo.svg").read_text()
    console = console.replace("/static/logo.svg",
                              "data:image/svg+xml;base64,"
                              + base64.b64encode(logo.encode()).decode())

    # Both pages link to the other by its real route; inside a folder of
    # flat files those have to become filenames.
    console = (console.replace('href="/console"', 'href="console.html"')
                      .replace('href="/today"', 'href="index.html"'))

    # A page's own <style> lives in its <head>, and only the <body> is
    # carried over. The console keeps everything in theme.css so nothing
    # was lost and nobody noticed; the plain view keeps its own block,
    # and the first build of it silently shipped an unstyled page that
    # still looked plausible -- default list numbering, no card, and no
    # sign anything was missing.
    head = console[:console.index("<body>")]
    own_styles = "\n".join(
        re.findall(r"<style>(.*?)</style>", head, re.S))

    body = console[console.index("<body>") + len("<body>"):
                   console.rindex("</body>")]
    # The page's own script has to run after the stub is installed.
    split = body.index('<script>\n"use strict";')
    markup, app_js = body[:split], body[split:]

    # The plain view pulls circuit.js with a <script src>, which is one
    # more file that does not exist beside a single inlined page.
    markup = markup.replace(
        '<script src="/static/circuit.js"></script>', "")

    page = (
        "<title>CyberLogix Console</title>\n"
        f"<style>\n{theme}\n\n"
        "/* --- the one thing this page adds to the product --------- */\n"
        ".demo-bar {\n"
        "  position: sticky; top: env(safe-area-inset-top, 0px); z-index: 90;\n"
        "  padding: 10px 16px; font-size: 13px; line-height: 1.55;\n"
        "  color: var(--ink); text-align: center;\n"
        "  background: color-mix(in srgb, var(--accent) 14%, var(--surface));\n"
        "  border-bottom: 1px solid var(--border-lit);\n"
        "}\n"
        ".demo-bar strong { color: var(--accent); }\n"
        f"{own_styles}\n"
        "</style>\n"
        f"{banner or BANNER}\n{markup}\n"
        f"<script>\n{circuit}\n</script>\n"
        + (STUB % (json.dumps(data).replace("</", "<\\/"),
                   (ROOT / "tools" / "demo_sim.js").read_text())).replace(
            "</script>", extra_stub + "</script>", 1)
        + app_js
    )

    # The guarantee this page makes: open it, and nobody is told you did.
    #
    # Checked at the places a browser actually loads from, not at every
    # http:// in the file. The console prints an example Slack webhook as
    # grey hint text in an input box; matching on bare text flagged that
    # and would have taught whoever hit it to loosen the check until it
    # went quiet, which is how a guard stops guarding.
    loads_from = re.findall(
        r"""(?:src|href)\s*=\s*["']\s*(https?://[^"']+)"""
        r"""|@import\s+url\(\s*['"]?\s*(https?://[^'")]+)"""
        r"""|url\(\s*['"]?\s*(https?://[^'")]+)""",
        page)
    outbound = sorted({hit for group in loads_from for hit in group if hit})
    if outbound:
        raise SystemExit(
            "the public page would fetch from the network, so opening it "
            f"would be observable: {outbound}")
    return page


SIMPLE_BANNER = """
<div class="demo-bar" role="note">
  <strong>Live demo.</strong> A simulated estate: the temperatures move
  while you watch, and a unit that crosses its limit opens a problem in
  front of you. Nothing here is a real fridge.
</div>
"""

SIMPLE_STUB_EXTRA = """
// The plain view checks for a session before it will render, and sends
// you to the console if there is none. Give it one: the demo's front
// door is this page, and bouncing a visitor to a sign-in form they have
// already been told they can skip is the opposite of the point.
try { localStorage.setItem("cyberlogix.session", "demo-session"); } catch (e) {}
"""


def main() -> None:
    # A throwaway database, so building the page never touches a real one
    # and leaves nothing behind to be served by accident.
    #
    # The name matters and is easy to get wrong: db.py reads
    # CYBERLOGIX_DB_PATH, and setting anything else silently does nothing
    # at all -- the seed then lands in ./cyberlogix.db in the working
    # directory, which is how the first version of this wrote a four
    # megabyte database into the repository. It has to be set before the
    # import below, because db.py freezes the path at import time.
    with tempfile.TemporaryDirectory() as tmp:
        # Spelled out rather than taken from `db.ENV_DB_PATH`, because
        # importing db to read that name is already too late: db.py
        # resolves the path at import time, and an import here would
        # freeze the default before this line could change it. A test
        # ties this literal to the constant so the two cannot drift.
        os.environ["CYBERLOGIX_DB_PATH"] = str(pathlib.Path(tmp) / "demo.db")
        data = capture()
        # index.html is the plain view, because the front door belongs to
        # whoever was handed the link and has never seen the product. The
        # console is one click away for anyone who wants the instruments.
        pages = {
            "index.html": build(data, "simple.html",
                                extra_stub=SIMPLE_STUB_EXTRA,
                                banner=SIMPLE_BANNER),
            "console.html": build(data, "console.html"),
        }

    DOCS.mkdir(exist_ok=True)
    # Without this GitHub Pages runs the output through Jekyll, which
    # silently drops anything it decides is a draft.
    (DOCS / ".nojekyll").write_text("")
    for name, page in pages.items():
        (DOCS / name).write_text(page)
        print(f"wrote {DOCS / name} — {len(page) // 1024} KB")


if __name__ == "__main__":
    main()
