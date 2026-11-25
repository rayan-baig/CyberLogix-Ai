"""Every dialog fits a phone (3.36.0), checked in a real browser.

"Thrown away. What next?" at 375px had "Make the insurance pack" running
into "Add what was thrown away" and off the edge of the screen: it shared
the food dialog's button rules, made for two short answers, and a button
never wraps its words. Nothing caught it because nothing opened the
dialog at a phone's width. This does, for every dialog and modal the app
has, at 320px and 375px: the alert's three (not back in range yet, with
"Close it anyway" open; "Closed. What about the food?"; "Thrown away.
What next?" and its note box) on /today and on the console, the plan
confirmation, "Connect your sensor" (the brands, then one brand's steps)
and the insurance pack drawer. Each must sit inside the screen, every
button's words inside the button, no two buttons on top of each other,
and the page must not scroll sideways.

A real server on a free port, a database in a temporary folder, and a
headless Chromium (Playwright). Skipped where either is missing, unless
KRYOZYN_BROWSER_TESTS=required (CI installs both and sets it), so a
missing browser can never quietly pass for a passing test.
"""

from __future__ import annotations

import os
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = os.environ.get("KRYOZYN_BROWSER_TESTS") == "required"

try:  # pragma: no cover - depends on the machine
    from playwright.sync_api import sync_playwright
except Exception:  # noqa: BLE001
    sync_playwright = None

WIDTHS = [(320, 640), (375, 812)]


def _skip(why: str):
    if REQUIRED:
        pytest.fail(why)
    pytest.skip(why)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    if sync_playwright is None:
        _skip("Playwright is not installed")
    port = _free_port()
    folder = tmp_path_factory.mktemp("browser-db")
    env = {
        **os.environ,
        "CYBERLOGIX_DB_PATH": str(folder / "app.db"),
        "CYBERLOGIX_SWEEP_SECONDS": "0",
        "CYBERLOGIX_ALARM_WATCH_SECONDS": "0",
        "CYBERLOGIX_TRUST_PROXY_HEADER": "1",
        "CYBERLOGIX_ADMIN_KEY": "browser-test-admin",
    }
    log = open(folder / "server.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(160):
            try:
                if httpx.get(base + "/api/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if proc.poll() is not None:
                break
            time.sleep(0.25)
        else:
            pytest.fail("the server did not start")
        if proc.poll() is not None:
            pytest.fail("the server stopped: " + (folder / "server.log").read_text()[-2000:])
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


@pytest.fixture(scope="module")
def browser(server):
    with sync_playwright() as p:
        try:
            # KRYOZYN_CHROMIUM: a Chromium already on the machine, where
            # Playwright's own build of it is not installed.
            b = p.chromium.launch(executable_path=os.environ.get("KRYOZYN_CHROMIUM") or None)
        except Exception as err:  # noqa: BLE001 - no browser on this machine
            _skip(f"no Chromium for Playwright: {err}")
        yield b
        b.close()


def _ip() -> str:
    return "10.%d.%d.%d" % tuple(random.randint(1, 250) for _ in range(3))


class Cafe:
    """A café signed up through the API, with one walk-in fridge."""

    def __init__(self, base: str):
        self.base = base
        email = f"maya{time.time_ns()}{random.randint(0, 9999)}@cafe.example"
        made = self.post("/api/signup", {
            "company_name": "Maya's Cafe", "full_name": "Maya Okafor", "email": email,
            "password": "espresso-shots-44", "contact_phone": "+15550103322",
            "industry_vertical": "restaurant"})
        assert made.status_code == 201, made.text
        self.token = made.json()["token"]
        self.auth = {"Authorization": "Bearer " + self.token}
        self.sensor_id = f"WALKIN-{random.randint(100000, 999999)}"
        added = self.post("/api/licenses/me/sensors", {
            "sensor_id": self.sensor_id, "industry_vertical": "restaurant",
            "location_name": "Walk-in fridge", "unit_kind": "fridge"}, self.auth)
        assert added.status_code == 201, added.text
        self.key = added.json()["ingest_key"]

    def post(self, path, body, headers=None):
        return httpx.post(self.base + path, json=body, timeout=20,
                          headers={"X-Forwarded-For": _ip(), **(headers or {})})

    def get(self, path):
        return httpx.get(self.base + path, timeout=20, headers={"X-Forwarded-For": _ip(), **self.auth})

    def pulse(self, temp: float):
        r = self.post("/api/sensor-pulse", {"sensor_id": self.sensor_id, "temperature_fahrenheit": temp},
                      {"X-CyberLogix-Sensor-Key": self.key})
        assert r.status_code < 300, r.text

    def warm(self) -> str:
        """The walk-in too warm, past alerting at once: the alert's id."""
        self.pulse(37.4)
        time.sleep(0.2)
        self.pulse(62.0)
        incidents = self.get("/api/console/overview").json().get("incidents") or []
        assert incidents, "no alert opened"
        return incidents[0]["incident_id"]


def _page(browser, base, width, height, token):
    ctx = browser.new_context(viewport={"width": width, "height": height}, is_mobile=True,
                              has_touch=True, device_scale_factor=2)
    ctx.add_init_script(f"try {{ localStorage.setItem('cyberlogix.session', {token!r}); }} catch (_) {{}}")
    page = ctx.new_page()
    ip = _ip()
    page.route("**/*", lambda route: route.continue_(headers={**route.request.headers, "x-forwarded-for": ip})
               if route.request.url.startswith(base) else route.abort())
    return ctx, page


# What is wrong with the open dialog (or modal) at `sel`, in words: [] when
# it fits. Inside the screen; nothing inside it wider than it; every
# button's words inside the button; no two buttons overlapping; the page
# itself not scrolling sideways.
FIT = """(sel) => {
  const vw = document.documentElement.clientWidth;
  const d = document.querySelector(sel);
  if (!d) return ['not open: ' + sel];
  const shown = (el) => { const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0; };
  const name = (el) => (el.id ? '#' + el.id : el.tagName.toLowerCase()) + ' "' + (el.textContent || '').trim().slice(0, 30) + '"';
  const bad = [];
  const r = d.getBoundingClientRect();
  if (r.left < -0.5 || r.right > vw + 0.5) bad.push(`${sel} spans ${Math.round(r.left)}..${Math.round(r.right)} of ${vw}`);
  if (d.scrollWidth > d.clientWidth + 1) bad.push(`${sel} scrolls sideways (${d.scrollWidth} > ${d.clientWidth})`);
  if (document.documentElement.scrollWidth > vw + 1) bad.push(`the page scrolls sideways (${document.documentElement.scrollWidth} > ${vw})`);
  const els = [...d.querySelectorAll('*')].filter(shown);
  for (const el of els) {
    const e = el.getBoundingClientRect();
    if (e.left < Math.max(0, r.left) - 0.5 || e.right > Math.min(vw, r.right) + 0.5) {
      bad.push(`${name(el)} spans ${Math.round(e.left)}..${Math.round(e.right)}, outside ${Math.round(r.left)}..${Math.round(r.right)}`);
    }
  }
  const buttons = els.filter((el) => el.matches('button, .btn, a.btn, summary'));
  for (const b of buttons) {
    if (b.scrollWidth > b.clientWidth + 1) bad.push(`${name(b)}: its words run out of it (${b.scrollWidth} > ${b.clientWidth})`);
  }
  for (let i = 0; i < buttons.length; i++) for (let j = i + 1; j < buttons.length; j++) {
    if (buttons[i].contains(buttons[j]) || buttons[j].contains(buttons[i])) continue;
    const a = buttons[i].getBoundingClientRect(), b = buttons[j].getBoundingClientRect();
    const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    if (w > 1 && h > 1) bad.push(`${name(buttons[i])} overlaps ${name(buttons[j])}`);
  }
  return [...new Set(bad)];
}"""


def _fits(page, sel, what):
    page.wait_for_selector(sel, state="visible", timeout=8000)
    page.wait_for_timeout(250)
    bad = page.evaluate(FIT, sel)
    assert bad == [], f"{what} at {page.viewport_size['width']}px: " + "; ".join(bad)


def _alert_dialogs(browser, server, width, height, where):
    """The alert's three dialogs, in the order a night brings them."""
    # Still too warm: "Not back in range yet", then "Close it anyway".
    cafe = Cafe(server)
    cafe.warm()
    ctx, page = _page(browser, server, width, height, cafe.token)
    try:
        page.goto(server + where)
        page.locator("[data-fixed], [data-resolve]").first.click(timeout=15000)
        _fits(page, "#fix-dialog[open]", f"'Not back in range yet' on {where}")
        page.click("#fix-anyway > summary")
        _fits(page, "#fix-dialog[open]", f"'Close it anyway' on {where}")
    finally:
        ctx.close()
    # Somebody on it and cold again: the food, then what next, then the note.
    cafe = Cafe(server)
    incident = cafe.warm()
    assert cafe.post(f"/api/voice/acknowledge/{incident}", {}, cafe.auth).status_code == 200
    cafe.pulse(38.0)
    ctx, page = _page(browser, server, width, height, cafe.token)
    try:
        page.goto(server + where)
        page.locator("[data-fixed], [data-resolve]").first.click(timeout=15000)
        _fits(page, "#food-dialog[open]", f"'Closed. What about the food?' on {where}")
        page.click("#food-thrown")
        _fits(page, "#loss-dialog[open]", f"'Thrown away. What next?' on {where}")
        page.click("#loss-note")
        _fits(page, "#loss-dialog[open]", f"'Add what was thrown away' on {where}")
    finally:
        ctx.close()


@pytest.mark.parametrize("width,height", WIDTHS)
def test_the_alert_dialogs_fit_a_phone_on_today(browser, server, width, height):
    _alert_dialogs(browser, server, width, height, "/today")


@pytest.mark.parametrize("width,height", WIDTHS)
def test_the_alert_dialogs_fit_a_phone_on_the_console(browser, server, width, height):
    _alert_dialogs(browser, server, width, height, "/console?view=full")


@pytest.mark.parametrize("width,height", WIDTHS)
def test_the_console_dialogs_and_the_pack_fit_a_phone(browser, server, width, height):
    cafe = Cafe(server)
    incident = cafe.warm()
    ctx, page = _page(browser, server, width, height, cafe.token)
    try:
        # The plan confirmation, from a "Move to" button as the Money tab draws it.
        page.goto(server + "/console?view=full")
        page.wait_for_selector("#needs-bar, .needs-bar", timeout=15000)
        page.evaluate("""() => { const b = document.createElement('button'); b.className = 'btn';
          b.setAttribute('data-upgrade', 'growth'); b.textContent = 'Move'; b.id = 'test-upgrade';
          document.body.appendChild(b); }""")
        page.click("#test-upgrade")
        _fits(page, "#plan-confirm[open]", "the plan confirmation")
        page.click("#plan-confirm-no")
        # Connect your sensor: the brands, then one brand's steps.
        page.goto(server + "/console?view=full#connect")
        _fits(page, "#connect[open]", "Connect your sensor")
        page.locator("#connect-brands button").first.click()
        _fits(page, "#connect[open]", "Connect your sensor, a brand's steps")
        # The insurance pack drawer.
        page.goto(server + f"/console?view=full#packet={incident}")
        page.wait_for_selector("#drawer:not([hidden]) .doc-section", timeout=15000)
        _fits(page, "#drawer", "the insurance pack")
    finally:
        ctx.close()
