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

writes `docs/index.html` (with `docs/console.html` and `docs/help.html`
beside it), which GitHub Pages serves at
https://<user>.github.io/<repo>/ once Pages is switched on for the repo.

Self-contained on purpose. It seeds a throwaway database in a temporary
directory, drives the real application through Starlette's test client,
and throws the database away again, so the page can be rebuilt from a
clean checkout with no server running and nothing left behind.
"""

from __future__ import annotations

import brand
from brand import APP_SHORT, APP_URL
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
    "/api/contacts/rotations",
    "/api/accounts/users", "/api/shortcuts", "/api/webhooks",
    "/api/contracts/pipeline", "/api/forecast/fleet", "/api/claims/eligible",
    "/api/contacts/preview", "/api/legal/acceptance/status",
    "/api/benchmarks", "/api/vault/attestation",
    "/api/people/alerts", "/api/people/calendar?days=180",
    "/api/people/departures", "/api/people", "/api/v1/bridge/samples",
    "/api/doorstep", "/api/tasks", "/api/pollers", "/api/industries",
    "/api/sites", "/api/invoices", "/api/contracts",
    "/api/billing/pricing",
    "/api/autopilot/compliance?days=7",
    # Asked for with the dates picked on the card; the stub falls back to
    # the path alone, so this one answer serves whatever dates are chosen.
    "/api/autopilot/inspector-log",
    "/api/accounts/businesses",
    "/api/simulator",
    "/api/help/sensor-offline",
    "/api/referrals/me",
    "/api/connections/aegizyn",
    "/api/ai-key",
    "/api/proof-reports",
    "/api/leaving",
    # "How to connect it": the brands and their steps (sensor_guides.WIZARD).
    "/api/sensor-guides/wizard",
    # What Kryozyn did for you (3.32.0), on /today; the stub falls back
    # to the path alone, so this one answers the week and the month.
    "/api/console/did-for-you",
]

# Paths the console names but never GETs: things it posts, deletes, or
# builds a link to. They are listed rather than guessed at, so that a
# card added tomorrow with a new GET is a build failure instead of a
# panel reading "Not captured in this demo".

NOT_FETCHED = {
    "/api/accounts/bootstrap", "/api/accounts/login", "/api/accounts/logout",
    "/api/accounts/me/password", "/api/accounts/me/prefs", "/api/autopilot/sweep",
    "/api/accounts/businesses/join", "/api/accounts/businesses/join-codes",
    "/api/accounts/business/export", "/api/accounts/business/erase",
    "/api/licenses/me/rotate-key",
    "/api/autopilot/compliance.csv", "/api/claims", "/api/contracts/billing-run",
    "/api/contracts/renew", "/api/contracts/add-ons", "/api/contracts/billing-interval",
    "/api/doorstep/adopt", "/api/legal/accept",
    "/api/licenses/me/plan", "/api/licenses/me/plan/terms", "/api/licenses/me/sensors",
    "/api/licenses/me/temperature-unit",
    "/api/licenses/tenants", "/api/sensor-pulse", "/api/signup",
    "/api/contacts/test-alert", "/api/simulator/units",
    "/api/tasks/from-meeting",
    "/api/connections/aegizyn/test", "/api/ai-key/test",
    "/api/v1/bridge/preview", "/api/v1/bridge/sensor-webhook-ingest",
    "/api/v1/bridge/summarize-transcript",
    # Pause or cancel, a founding member code and a repair quote: pressed,
    # never loaded with the page.
    "/api/contracts/cancel", "/api/leaving/pause", "/api/leaving/resume",
    "/api/leaving/stay", "/api/founder-code", "/api/repairs/quote",
}


def paths_the_page_asks_for(page: str) -> set:
    """Every /api/... the page names, cut back to its fixed prefix.

    Backticks included. The first version of this read only quoted
    strings, so it did not see `/api/v1/enterprise-billing/quote?...`,
    which is built with a template literal -- and the price card shipped
    in a demo reading "Not captured in this demo", which is exactly the
    failure the caller exists to prevent.
    """
    return {url.split("?")[0].split("${")[0].rstrip("/")
            for url in re.findall(r"""["'`](/api/[A-Za-z0-9_./-]+[^"'`]*)["'`?]""",
                                  page)}


def check_every_panel_has_data(page_name: str = "console.html") -> None:
    """Fail the build for a card whose endpoint nobody captured.

    The comment above PATHS claimed this happened for a long time before
    it did, and the cost of the gap was exactly what it predicted: a
    Devices-knocking card shipped in a demo reading "Not captured in
    this demo", which does not look like an uncaptured panel to anybody
    looking at it. It looks like the feature is broken.
    """
    page = (ROOT / "static" / page_name).read_text()
    # The calls are made by the page's own script, which is a file of its own.
    for name in re.findall(r'<script src="/static/([a-z0-9-]+\.js)"></script>', page):
        page += (ROOT / "static" / name).read_text()
    asked = paths_the_page_asks_for(page)
    captured = {p.split("?")[0] for p in PATHS}
    # Paths with a value substituted into them are captured per-item by
    # the loops in capture(), not by name.
    dynamic = ("/api/console/sensor", "/api/benchmarks", "/api/voice/",
               "/api/disposition", "/api/vault/verify")
    missing = sorted(
        path for path in asked
        if path not in captured and path not in NOT_FETCHED
        and not path.startswith(dynamic))
    if missing:
        raise SystemExit(
            f"{page_name} asks for {missing} and the demo captures no "
            "answer, so those cards would render as 'Not captured in "
            "this demo'. Add them to PATHS, or to NOT_FETCHED if the "
            "page only writes to them.")

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

  // The business switcher. The demo holds one business's figures, so it
  // says what the live app would do rather than pretending to go there.
  if (method === "POST" && /^[/]api[/]accounts[/]businesses/.test(url.pathname)) {
    return reply({ detail: url.pathname.endsWith("/switch")
      ? "In the live app this opens that business, with its own units and alerts. "
        + "The demo holds one business, so it stays here."
      : "In the live app this adds the business on a free trial and opens it. "
        + "The demo is read-only, so it does not save." }, 403);
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
        token = signed_in.json()["token"]
        home = signed_in.json()["tenant"]["tenant_id"]

        # A second business for the same owner, so the business switcher
        # has something to show: a bakery with one freezer, running fine.
        # Adding it opens it, so the session is then switched back home.
        added = client.post(
            "/api/accounts/businesses",
            headers={"Authorization": f"Bearer {token}"},
            json={"company_name": "Harbor Bakery", "industry_vertical": "restaurant"},
        )
        added.raise_for_status()
        bakery_key = {"X-Kryozyn-Key": added.json()["api_key"]}
        client.post("/api/licenses/me/sensors", headers=bakery_key, json={
            "sensor_id": "BAKERY-FREEZER", "industry_vertical": "restaurant",
            "location_name": "Pastry freezer"}).raise_for_status()
        client.post("/api/sensor-pulse", headers=bakery_key, json={
            "sensor_id": "BAKERY-FREEZER", "temperature_fahrenheit": 2.0,
        }).raise_for_status()
        back = client.post(
            f"/api/accounts/businesses/{home}/switch",
            headers={"Authorization": f"Bearer {added.json()['token']}"},
        )
        back.raise_for_status()
        head = {"Authorization": f"Bearer {back.json()['token']}"}

        # Taking turns, so the On call now card shows a rotation rather
        # than its empty state: the two estate-wide managers a week each,
        # and one of them with quiet hours.
        people = {c["full_name"]: c["contact_id"] for c in
                  client.get("/api/contacts", headers=head).json()["contacts"]}
        client.put("/api/contacts/rotations", headers=head, json={
            "timezone": "America/New_York", "handover": "08:00", "shift_days": 7,
            "members": [people["Dana Reyes"], people["Marco Diaz"]],
        }).raise_for_status()
        client.patch(f"/api/contacts/{people['Marco Diaz']}", headers=head, json={
            "quiet_hours": {"start": "23:00", "end": "06:00",
                            "timezone": "America/New_York"},
        }).raise_for_status()

        _bundle_and_referrals(client, head, home)

        # The AI key card as a business sees it on a server that can keep
        # one: a throwaway secret for this capture only, put back after.
        import alertkey
        import secrets

        had = os.environ.get(alertkey.SETTING)
        os.environ[alertkey.SETTING] = had or secrets.token_urlsafe(32)
        try:
            for path in PATHS:
                got = client.get(path, headers=head)
                out[path] = {"status": got.status_code, "body": got.json()}
        finally:
            if had is None:
                os.environ.pop(alertkey.SETTING, None)

        overview = out["/api/console/overview"]["body"]
        # The drawer for each sensor, and the peer comparison for every
        # vertical in the estate -- whichever one dominates is the one the
        # standing card will ask for, and it is cheaper to capture all of
        # them than to reimplement the page's choice here.
        for sensor in overview.get("sensors", []):
            # The drawer, the food-discard guidance and the tamper-proof
            # record: three cards that are asked for one sensor at a
            # time, with the query the page itself sends.
            for path in (
                f"/api/console/sensor/{sensor['sensor_id']}",
                f"/api/disposition/{sensor['sensor_id']}?hours=24",
                f"/api/vault/verify/{sensor['sensor_id']}?days=30",
            ):
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
    refuse_model_names(out)
    return out


# The model family this codebase calls (gemini.py), followed by a version
# or a key, as an id or the health answer's old field would be.
MODEL_NAME = re.compile(r"gemini[-_][0-9a-z]")


def refuse_model_names(data) -> None:
    """Stop the build if a captured answer names the AI model.

    /api/health once carried the model's id, and this page carried it to
    anybody with the link. Health no longer says it; this makes sure no
    other answer starts to, in the one place a leak is public for good.
    """
    import gemini

    text = data if isinstance(data, str) else json.dumps(data)
    named = set(MODEL_NAME.findall(text.lower()))
    if gemini.GEMINI_MODEL and gemini.GEMINI_MODEL.lower() in text.lower():
        named.add(gemini.GEMINI_MODEL)
    if named:
        raise SystemExit(f"the public demo would name the AI model: {sorted(named)}")


def _bundle_and_referrals(client, head, home: str) -> None:
    """The two money cards with something on them.

    The Aegizyn connection, with last night's report sent to a stand-in
    that answers the way Aegizyn's contract says it does (nothing leaves
    this build), and two businesses that joined with the referral code,
    one of which has paid its first invoice.
    """
    import secrets

    import omniarmor
    import pricing
    import referrals
    from store import STORE

    if not omniarmor.CREDENTIAL_KEY:
        # A throwaway, for this build's throwaway database only.
        omniarmor.CREDENTIAL_KEY = secrets.token_urlsafe(32)
    client.put("/api/connections/aegizyn", headers=head, json={
        "address": "https://aegizyn.example.com",
        "key": "clx_" + secrets.token_urlsafe(32)[:43].ljust(43, "x"),
        "site_label": "Main kitchen", "timezone": "America/New_York",
    }).raise_for_status()

    def stand_in(url, key, body, headers=None):
        return 200, {}, json.dumps({
            "ok": True, "matched_checks": 1, "ticked": 1, "noted": 0, "unchanged": 0,
            "checks": [{"industry": "RestoArmor", "check": "cold_holding",
                        "title": "Cold holding at 41\u00b0F or below",
                        "result": "ticked"}]}).encode()

    # The seed puts a week of readings on units it registers today; date
    # the units from before their readings, as real ones would be, so last
    # night's report has them in it.
    from datetime import timedelta

    for sensor in STORE.sensors_for(home):
        first = min((r.recorded_at for r in STORE.readings_for(sensor.sensor_id)),
                    default=None)
        if first is not None and (sensor.registered_at is None or sensor.registered_at > first):
            sensor.registered_at = first - timedelta(hours=1)
            STORE._db.put("sensor", sensor.sensor_id, sensor.to_row())
    omniarmor.run_daily(transport=stand_in, sleep=lambda seconds: None)

    tenant = STORE.get_tenant(home)
    code = referrals.code_for(tenant)
    for name, pays in (("Corner Cafe", True), ("Pier Street Deli", False)):
        friend = STORE.create_tenant(
            company_name=name, contact_name="Sam Lee", contact_phone="+1-555-0170",
            contact_email=f"hello@{name.split()[0].lower()}.example", plan="trial")
        referrals.record_signup(friend, tenant, code)
        if pays:
            STORE.change_plan(friend, "starter", extend_term=False)
            invoice = STORE.create_invoice(tenant=friend, lines=[{
                "kind": "subscription", "description": "Starter plan: 1 location",
                "quantity": 1, "unit_price_usd": pricing.STARTER_MONTHLY_USD,
                "amount_usd": pricing.STARTER_MONTHLY_USD}])
            invoice, _ = STORE.settle_invoice(invoice, "DEMO-WIRE-0002")
            referrals.after_settlement(friend, invoice)


def refuse_outbound(page: str) -> None:
    """The guarantee each page makes: open it, and nobody is told you did.

    Checked at the places a browser actually loads from, not at every
    http:// in the file. The console prints an example Slack webhook as
    grey hint text in an input box; matching on bare text flagged that
    and would have taught whoever hit it to loosen the check until it
    went quiet, which is how a guard stops guarding.
    """
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


def offline_theme() -> str:
    """theme.css with its Google Fonts import swapped for the embedded copy."""
    theme = (ROOT / "static" / "theme.css").read_text()
    fonts = (ROOT / "static" / "fonts-embedded.css").read_text()
    imports = [line for line in theme.splitlines()
               if line.startswith("@import url('https://fonts.googleapis.com")]
    if len(imports) != 1:
        raise SystemExit(
            f"expected one Google Fonts import in theme.css, found "
            f"{len(imports)}. The public page must not fetch anything.")
    return theme.replace(imports[0], fonts)


def build(data: dict, page_name: str = "console.html",
          extra_stub: str = "", banner: str = "") -> str:
    console = brand.fill((ROOT / page_name).read_text() if "/" in page_name else (
        ROOT / "static" / page_name).read_text())
    # The shipped stylesheet pulls its webfonts from fonts.googleapis.com.
    # On a page whose whole job is to be forwarded to strangers, that
    # means every person who opens it tells Google they opened it, from
    # their IP address, before a word renders. offline_theme() swaps the
    # import for the embedded copy so the page reaches nothing at all.
    theme = offline_theme()
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

    # A page's own stylesheet is linked from its <head>, and only the
    # <body> is carried over, so each one is read in here: the page's own
    # (simple.css) and the utility classes every page uses for small
    # adjustments (utilities.css), in the order the page links them. The
    # first build of the plain view silently shipped an unstyled page that
    # still looked plausible -- default list numbering, no card, and no
    # sign anything was missing.
    head = console[:console.index("<body>")]
    own_styles = "\n".join(
        (ROOT / "static" / name).read_text()
        for name in re.findall(r'<link rel="stylesheet" href="/static/([a-z0-9-]+\.css)">', head)
        if name != "theme.css")

    body = console[console.index("<body>") + len("<body>"):
                   console.rindex("</body>")]
    # No Pilot in the public copy: it is the live site's assistant, loaded
    # from the live site's own files, and this page has neither.
    body = body.replace("<!--pilot-->", "")
    # The page's own script has to run after the stub is installed. It is
    # a file now (the policy refuses inline script on the real server), so
    # it is read and written in here, where no policy applies.
    own_script = re.search(r'<script src="/static/([a-z0-9-]+\.js)"></script>\s*$', body)
    if own_script is None:
        raise SystemExit(f"{page_name} does not end by loading its own script.")
    markup = body[:own_script.start()]
    # The plain view pulls circuit.js with a <script src>, which is one
    # more file that does not exist beside a single inlined page; it is
    # written in below instead.
    markup = markup.replace(
        '<script src="/static/circuit.js"></script>', "")
    # The shared words (how long, when, who) every page says them with:
    # one more file, written in ahead of the page's own script.
    shared = ""
    for name in ("words.js", "answer.js"):
        tag = f'<script src="/static/{name}"></script>'
        if tag in markup:
            markup = markup.replace(tag, "")
            shared += ("<script>\n" + (ROOT / "static" / name).read_text().replace(
                "</script", "<\\/script") + "</script>\n")
    own_js = (ROOT / "static" / own_script.group(1)).read_text()
    # The demo is a tour of the console: signing in to it stays there, even
    # with its broken walk-in alerting (the real app goes to /today first).
    own_js = own_js.replace("state.fromSignIn = true;", "state.fromSignIn = false;")
    app_js = shared + ("<script>\n"
              + own_js.replace("</script", "<\\/script")
              + "</script>\n")

    page = (
        # Said in the page, not left to the host: served without a charset
        # header, every degree sign reads as mojibake.
        '<meta charset="utf-8">\n'
        f"<title>{APP_SHORT} Console</title>\n"
        # A demo is for whoever is handed the link, not for a search
        # engine: it is the console's private surface, on a public host.
        '<meta name="robots" content="noindex">\n'
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

    # The help page is a file beside this one in the demo (help.html), so
    # its links, and the ones the scripts build, point there.
    page = (page.replace('href="/help', 'href="help.html')
                .replace('"/help#', '"help.html#')
                .replace('location.href = "/demo"', 'location.href = "demo.html"'))
    refuse_outbound(page)
    # And nothing loaded from the server's own root, which on GitHub Pages
    # is somebody else's site: a stray <script src="/static/..."> is a 404
    # at best. Markup only; the inlined scripts' comments name such tags.
    bare = re.sub(r"<script>.*?</script>", "", page, flags=re.S)
    rooted = re.findall(r'src="/[^"]*"', bare)
    if rooted:
        raise SystemExit(f"the demo page still loads from the server: {rooted}")
    return page


# Pages the demo has a copy of, by the route the real app serves them at.
DEMO_PAGES = {"/": "index.html", "/console": "console.html", "/help": "help.html",
              "/help/sensors": "sensors.html", "/demo": "demo.html"}

# Where "Start your free trial" goes from the copy on GitHub Pages, which
# has no sign-up of its own: the live product. Overridable for a staging
# copy. It travels inside the story (JSON), never as a link the browser
# loads, so opening the page still reaches nothing.
LIVE_ADDRESS = os.environ.get("DEMO_LIVE_ADDRESS", APP_URL)


def build_kitchen(story: dict) -> str:
    """See it live (static/demo.html) as one file, with its story inside.

    The served page asks /api/demo/story for the story; this copy carries
    it in a JSON block that demo.js reads first, so it needs no server.
    Everything else is the shipped page: its markup, demo.css, demo.js.
    """
    from pricing import headline

    story = dict(story, signup_href=f"{LIVE_ADDRESS}/signup?sector=restaurant")
    page = brand.fill((ROOT / "static" / "demo.html").read_text())
    page = (page.replace("%%TRIAL_DAYS%%", str(headline()["trial_days"]))
                .replace("%%THEME_COLOR%%", "#05070D")
                .replace("<!--pilot-->\n", ""))
    page = re.sub(r'\n<link rel="icon"[^>]*>', "", page)
    styles = "\n".join([offline_theme()] + [
        (ROOT / "static" / name).read_text() for name in ("demo.css", "utilities.css")])
    page = re.sub(r'<link rel="stylesheet" href="/static/theme.css">\n'
                  r'<link rel="stylesheet" href="/static/demo.css">\n'
                  r'<link rel="stylesheet" href="/static/utilities.css">',
                  lambda m: f"<style>\n{styles}\n</style>", page)
    logo = (ROOT / "static" / "logo.svg").read_text()
    page = page.replace("/static/logo.svg", "data:image/svg+xml;base64,"
                        + base64.b64encode(logo.encode()).decode())
    page = page.replace('href="/"', 'href="index.html"')
    # demo.js points these at the live sign-up from the story.
    page = page.replace('href="/signup?sector=restaurant"', 'href="#"')
    data = json.dumps(story).replace("</", "<\\/")
    scripts = "".join(
        "<script>\n" + (ROOT / "static" / name).read_text().replace("</script", "<\\/script")
        + "\n</script>\n" for name in ("circuit.js", "demo.js"))
    page = page.replace(
        '<script src="/static/circuit.js"></script>\n<script src="/static/demo.js"></script>\n',
        f'<script type="application/json" id="demo-story">{data}</script>\n{scripts}')
    page = page.replace("<head>", '<head>\n<meta name="robots" content="noindex">', 1)
    refuse_outbound(page)
    refuse_model_names(page)
    markup = re.sub(r"<script>.*?</script>", "", page, flags=re.S)
    leftover = re.findall(r'(?:href|src)="/[^"]*"', markup)
    if leftover:
        raise SystemExit(f"the demo kitchen still points at the server: {leftover}")
    return page

# The address the sensor guides show in their steps. The page is rendered
# on a test client, whose address means nothing to a reader; this one is
# on a reserved domain and plainly stands for "yours".
EXAMPLE_ADDRESS = "https://kryozyn.example.com"


def capture_help(route: str = "/help") -> str:
    """A help page exactly as the application serves it."""
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as client:
        got = client.get(route)
        got.raise_for_status()
        # The server names its files with ?v=<hash> so browsers keep them;
        # here they are inlined, so the plain names are what is wanted.
        page = re.sub(r'(/static/[^"?#]+)\?v=[0-9a-f]+"', r'\1"', got.text)
        return page.replace("http://testserver", EXAMPLE_ADDRESS)


def build_help(served: str) -> str:
    """The real help page, as one file beside the demo's other two.

    The console and the plain view both link to /help, and on GitHub
    Pages there is nothing there: the link from "A sensor is offline"
    went to a 404, which reads as a broken product. This is the page the
    server renders -- every answer from helpdesk.py -- with its
    stylesheets written in, nothing fetched from anywhere, and every link
    pointing at a page the demo actually has. A link to a page it has no
    copy of (pricing, the agreements) is left as its words, not a 404.
    """
    page = served
    # No Pilot in the public copy: it loads the live site's own files.
    page = re.sub(r'<script src="/static/pilot/pilot\.js"[^>]*></script>\n?', "", page)
    # Previews, the canonical address and the installed-app links all name
    # the server the copy was rendered on, which is not where it is read.
    page = re.sub(r'\n<link rel="(?:canonical|icon|apple-touch-icon|manifest)"[^>]*>', "", page)
    page = re.sub(r'\n<meta (?:property="og:|name="twitter:)[^>]*>', "", page)
    page = page.replace("<head>", '<head>\n<meta name="robots" content="noindex">', 1)

    pages_css = (ROOT / "static" / "pages.css").read_text()
    page = page.replace('<link rel="stylesheet" href="/static/theme.css">',
                        f"<style>\n{offline_theme()}\n</style>")
    page = page.replace('<link rel="stylesheet" href="/static/pages.css">',
                        f"<style>\n{pages_css}\n</style>")
    circuit = (ROOT / "static" / "circuit.js").read_text().replace("</script", "<\\/script")
    page = page.replace('<script src="/static/circuit.js" defer></script>\n', "")
    page = page.replace("</body>", f"<script>\n{circuit}\n</script>\n</body>", 1)
    # "Sign in" or "Your console" (signedin.js): the copy has neither a
    # session nor a console to sign in to, and its nav drops the link.
    page = re.sub(r'<script src="/static/signedin\.js[^"]*" defer></script>\n?', "", page)
    # The sensor guides' folds: the script that opens the one a link names,
    # written in like circuit.js (/help/sensors only).
    folds = '<script src="/static/help-sensors.js" defer></script>\n'
    if folds in page:
        script = (ROOT / "static" / "help-sensors.js").read_text().replace("</script", "<\\/script")
        page = page.replace(folds, "")
        page = page.replace("</body>", f"<script>\n{script}\n</script>\n</body>", 1)
    logo = (ROOT / "static" / "logo.svg").read_text()
    page = page.replace("/static/logo.svg", "data:image/svg+xml;base64,"
                        + base64.b64encode(logo.encode()).decode())

    def relink(match: "re.Match[str]") -> str:
        route, anchor, text = match.group(2), match.group(3) or "", match.group(5)
        if route in DEMO_PAGES:
            return (f'{match.group(1)}href="{DEMO_PAGES[route]}{anchor}"'
                    f'{match.group(4)}>{text}</a>')
        # Inside the nav a dead link is dropped, and so is a button (the help
        # page's row of them: a button's words alone are not a sentence); in
        # a sentence its words stay.
        if 'class="btn' in match.group(1) + match.group(4):
            return ""
        return "" if "<nav" in page[:match.start()].rsplit("</nav>", 1)[-1] else text

    page = re.sub(r'(<a\b[^>]*?)href="(/[a-z/]*)(#[a-z-]+)?"([^>]*)>(.*?)</a>',
                  relink, page, flags=re.S)
    refuse_outbound(page)
    # Markup only: circuit.js's own usage comment names its <script src>.
    markup = re.sub(r"<script>.*?</script>", "", page, flags=re.S)
    leftover = re.findall(r'(?:href|src)="/[^"]*"', markup)
    if leftover:
        raise SystemExit(f"the demo help page still points at the server: {leftover}")
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
        check_every_panel_has_data("console.html")
        check_every_panel_has_data("simple.html")
        data = capture()
        help_page = build_help(capture_help())
        sensors_page = build_help(capture_help("/help/sensors"))
        import demo

        kitchen = build_kitchen(demo.story())
        # index.html is the plain view, because the front door belongs to
        # whoever was handed the link and has never seen the product. The
        # console is one click away for anyone who wants the instruments.
        pages = {
            "index.html": build(data, "simple.html",
                                extra_stub=SIMPLE_STUB_EXTRA,
                                banner=SIMPLE_BANNER),
            "console.html": build(data, "console.html"),
            "help.html": help_page,
            "sensors.html": sensors_page,
            "demo.html": kitchen,
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
