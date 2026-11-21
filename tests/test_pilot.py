"""Pilot, the MTRX Horizyn assistant, on CyberLogix (static/pilot/, docs/PILOT.md).

These check the promises Pilot makes here: it is on the public pages, the
console and the Simple view, and on none of the others (a /verify proof
page, an error page, the operator's /book); the security policy lets the
browser reach exactly the AI providers' addresses, the same list as the
MTRX Horizyn site, and nothing more; the chat window's code loads only
when it is opened; the core files are the shared core, unchanged; no model
name is written anywhere; the knowledge pack's figures are the ones the
product runs on; and nothing about Pilot passes through this server. The
core's own tests and this app's tool tests run in Node (tests/js/).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import main

ROOT = Path(__file__).resolve().parent.parent
PILOT = ROOT / "static" / "pilot"
CORE = ("pilot.js", "pilot-store.js", "pilot-mascot.js", "pilot-engine.js", "pilot-providers.js", "pilot-media.js",
        "pilot-render.js", "pilot-panel.js", "pilot.css", "pilot-panel.css", "pilot-motion.js", "pilot-actor.js",
        "pilot-actor.css")
LAZY = ("pilot-panel.js", "pilot-engine.js", "pilot-providers.js", "pilot-media.js", "pilot-render.js",
        "pilot-panel.css", "pilot-cyberlogix-pack.js", "pilot-motion.js", "pilot-actor.js", "pilot-actor.css")
MOUNTED = ("/", "/pricing", "/help", "/help/sensors", "/for", "/for/restaurant", "/for/florists", "/demo",
           "/console", "/today")
# Pilot core 1.1.1 as it ships on the MTRX Horizyn site (commit 8b0ecd7).
# A newer core is copied into every app together, and these change with it.
CORE_SHA256 = {
    "pilot-actor.css": "f8cd10a058138d5c",
    "pilot-actor.js": "ba210dfb444d86df",
    "pilot-engine.js": "a57ffece42dcf1a3",
    "pilot-mascot.js": "526c0bcb4bdeee8d",
    "pilot-media.js": "d94af02b571c4531",
    "pilot-motion.js": "d8097cf57a82b706",
    "pilot-panel.css": "04aac3d9f703259a",
    "pilot-panel.js": "d81c9ce70ae55dc6",
    "pilot-providers.js": "aaa2286a833b8ec9",
    "pilot-render.js": "9c1a1057b3fa8dac",
    "pilot-rest-body.webp": "121ef0b2da9fd734",
    "pilot-rest-eyes.webp": "e7d5c7013b9d82dd",
    "pilot-rest-head.webp": "5eb064f177cffcb3",
    "pilot-robot-128.webp": "05a92f32d99b8076",
    "pilot-robot-256.webp": "7f4c60546b37759e",
    "pilot-store.js": "4a4c8a34d8ed9a59",
    "pilot.css": "6c817bdc1a3af9fb",
    "pilot.js": "651e86c8964cfb88",
}
# The moving robot's pose pictures (static/pilot/poses/), copied with the core.
POSES_SHA256 = {
    "arms-crossed.webp": "d97c356154f8cd89",
    "celebrate.webp": "ce62dadd1576964b",
    "climb1.webp": "edb6710eb1e4bb55",
    "flip.webp": "04300f4d67d6cfc4",
    "fly.webp": "1e429f636bcd7a01",
    "kick.webp": "10929d8c415dbd82",
    "land.webp": "27e2c2e7c4ed521d",
    "love.webp": "9c3a2e67ee4da3b1",
    "point.webp": "265e2cf3deeb5ddf",
    "power.webp": "12460c2a579edeff",
    "run1.webp": "69531e3afc79f654",
    "sitting.webp": "25880d4fad611832",
    "slide.webp": "85433b9284a38e76",
    "standing.webp": "523b8e7f6126eee8",
    "tennis.webp": "d82fdcd4b7b5e5d1",
    "throw.webp": "246444d9edb1f385",
    "walk1.webp": "374a948be8aded8c",
    "wave.webp": "a20b54e78d27654a",
}


def _page(api, path, **headers):
    return api.get(path, headers={"Accept": "text/html", **headers})


def _srcs(page: str) -> list:
    return re.findall(r'<script src="([^"]+)"', page)


def test_pilot_is_on_the_public_pages_the_console_and_the_simple_view(api):
    for path in MOUNTED:
        resp = _page(api, path)
        assert resp.status_code == 200, path
        page = resp.text
        pilot = [s for s in _srcs(page) if "pilot" in s]
        assert len(pilot) == 1 and re.fullmatch(r"/static/pilot/pilot\.js\?v=[0-9a-f]{12}", pilot[0]), (path, pilot)
        for name in ("pilot/pilot-store.js", "pilot/pilot-mascot.js", "pilot-cyberlogix.js", "pilot/pilot.css",
                     "pilot/pilot-robot-128.webp", "pilot/pilot-robot-256.webp"):
            assert re.search(rf'data-[a-z0-9-]+="/static/{re.escape(name)}\?v=[0-9a-f]{{12}}"', page), (path, name)
        assert page.count('id="pilot-core"') == 1, path
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", page), path
        assert 'style="' not in page, path


def test_no_pilot_on_proof_pages_error_pages_or_the_operators_book(api):
    operator = {"Authorization": "Basic " + base64.b64encode(b"op:test-admin-key").decode()}
    book = _page(api, "/book", **operator)
    assert book.status_code == 200 and "pilot" not in book.text.lower()
    for path in ("/verify", "/verify/AAAA-BBBB-CCCC-DDDD", "/no-such-page", "/book", "/signup", "/legal", "/reset",
                 "/proof/not-a-real-token", "/proof-report/PRF-000001", "/partners", "/offline",
                 "/for/restaurant/sheet", "/insurance-partners", "/repair-partners"):
        assert "pilot" not in _page(api, path).text.lower(), path
    # The 500 page is the same file as the 404 page, and neither carries the mark.
    for name in ("error.html", "verify.html", "book.html", "proof.html", "proof-report.html", "reset.html", "sheet.html"):
        assert main.PILOT_MARK not in (ROOT / "static" / name).read_text(), name
    for name in ("index.html", "pricing.html", "help.html", "help-sensors.html", "industry.html", "trade.html",
                 "demo.html", "console.html", "simple.html"):
        assert (ROOT / "static" / name).read_text().count(main.PILOT_MARK) == 1, name


def test_a_server_error_page_has_no_pilot(api, monkeypatch):
    from fastapi.testclient import TestClient

    def boom(*_a, **_k):
        raise RuntimeError("simulated")

    monkeypatch.setattr(main, "_landing_facts", boom)
    monkeypatch.setattr(main, "_page_cache", {})
    resp = TestClient(main.app, raise_server_exceptions=False).get("/", headers={"Accept": "text/html"})
    assert resp.status_code == 500
    assert "pilot" not in resp.text.lower()


def test_the_window_loads_only_when_opened(api):
    page = _page(api, "/").text
    for name in LAZY:
        assert not re.search(rf'(?:src|href)="[^"]*{re.escape(name)}', page), name
        assert re.search(rf'data-[a-z-]+="/static/(?:pilot/)?{re.escape(name)}\?v=[0-9a-f]{{12}}"', page), name
    sent = len(gzip.compress((PILOT / "pilot.js").read_bytes()))
    assert sent < 6_000, sent
    loader = (PILOT / "pilot.js").read_text()
    assert 'window.addEventListener("load", () => setTimeout(boot, 0), { once: true });' in loader
    assert re.search(r'<script src="/static/pilot/pilot\.js\?v=[0-9a-f]+" defer id="pilot-core"', page)


def test_the_security_policy_adds_exactly_the_provider_hosts(api):
    resp = _page(api, "/")
    csp = resp.headers["content-security-policy"]
    assert csp == main.CONTENT_SECURITY_POLICY
    rules = dict(rule.strip().split(" ", 1) for rule in csp.split(";"))
    connect = rules["connect-src"].split()
    assert connect == (["'self'", "https://api.anthropic.com", "https://api.openai.com",
                        "https://generativelanguage.googleapis.com"]
                       + [f"https://{h}" for h in main.PILOT_COMPATIBLE_HOSTS]
                       + ["http://localhost:*", "http://127.0.0.1:*"])
    # The same compatible hosts as the MTRX Horizyn site.
    assert main.PILOT_COMPATIBLE_HOSTS == ("openrouter.ai", "api.groq.com", "api.mistral.ai", "api.deepseek.com",
                                           "api.together.xyz", "api.fireworks.ai", "api.x.ai")
    for host in connect:
        assert "*." not in host and host not in ("https:", "http:", "*", "data:", "blob:"), host
    # Nothing else was widened.
    assert rules["script-src"] == "'self'" and rules["default-src"] == "'self'"
    assert rules["style-src"] == "'self' https://fonts.googleapis.com"
    assert rules["frame-ancestors"] == "'none'" and rules["form-action"] == "'self'"
    assert rules["media-src"] == "'self' blob:"
    assert rules["img-src"] == "'self' data:"
    assert "unsafe" not in csp
    hosts = re.search(r'data-hosts="([^"]*)"', resp.text).group(1).split()
    assert hosts == list(main.PILOT_COMPATIBLE_HOSTS + main.PILOT_LOCAL_HOSTS)
    policy = resp.headers["permissions-policy"]
    assert "microphone=(self)" in policy and "camera=()" in policy and "geolocation=()" in policy
    # The API docs pages keep their own policy, untouched.
    assert "anthropic" not in main.API_DOCS_POLICY


def test_keys_and_ai_calls_never_touch_this_server():
    """Pilot talks only to the provider the person chose. No core file asks
    this server for anything, and the server has no Pilot address at all."""
    for name in CORE:
        text = (PILOT / name).read_text()
        assert "/api/" not in text, name
        for unsafe in (".innerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert unsafe not in text, (name, unsafe)
    for route in main.app.routes:
        assert "pilot" not in getattr(route, "path", "").lower()
    providers = (PILOT / "pilot-providers.js").read_text()
    assert '"anthropic-dangerous-direct-browser-access": "true"' in providers
    assert 'credentials: "omit"' in providers


def test_the_core_is_the_shared_core_unchanged():
    for name, digest in CORE_SHA256.items():
        assert hashlib.sha256((PILOT / name).read_bytes()).hexdigest().startswith(digest), name
    for name, digest in POSES_SHA256.items():
        assert hashlib.sha256((PILOT / "poses" / name).read_bytes()).hexdigest().startswith(digest), name
    assert sorted(p.name for p in PILOT.iterdir() if p.is_file()) == sorted(CORE_SHA256)
    assert sorted(p.name for p in PILOT.iterdir() if p.is_dir()) == ["poses"]
    assert sorted(p.name for p in (PILOT / "poses").iterdir()) == sorted(POSES_SHA256)
    versions = set()
    for name in CORE:
        first = (PILOT / name).read_text().splitlines()[0]
        match = re.fullmatch(r"(?://|/\*) Pilot core (\d+\.\d+\.\d+) · " + re.escape(name) + r"(?: \*/)?", first)
        assert match, (name, first)
        versions.add(match.group(1))
    assert versions == {"1.1.1"}
    for name in CORE:
        if name.endswith(".js"):
            assert 'const VERSION = "1.1.1";' in (PILOT / name).read_text(), name
    assert "core 1.1.1" in (ROOT / "docs/PILOT.md").read_text()


def test_the_robot_is_the_owners_picture_small_enough():
    for size, most in ((128, 15_000), (256, 40_000)):
        data = (PILOT / f"pilot-robot-{size}.webp").read_bytes()
        assert data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) < most, size


MODEL_NAMES = re.compile(r"claude-|gpt-?\d|gemini-\d|\bo[1-9]-|sonnet|opus|haiku|llama|dall-?e|\bsora\b|whisper|imagen|\bveo\b",
                         re.IGNORECASE)


def test_no_model_name_is_written_anywhere():
    files = [PILOT / n for n in CORE] + [
        ROOT / "static" / n for n in ("pilot-cyberlogix.js", "pilot-cyberlogix-pack.js")] + [
        ROOT / "docs/PILOT.md", ROOT / "alertkey.py", ROOT / "tests/js/pilot.test.js",
        ROOT / "tests/js/cyberlogix-pilot.test.js", ROOT / "tests/test_alert_ai_key.py"]
    for f in files:
        hits = MODEL_NAMES.findall(f.read_text())
        assert not hits, (f.name, hits)


def test_the_window_matches_the_design_words():
    panel = (PILOT / "pilot-panel.js").read_text() + (ROOT / "static/pilot-cyberlogix.js").read_text()
    for words in ("Your AI copilot. Powered by you.", "Hello! I'm Pilot.", "How can I help you today?", "Ask anything…",
                  "Get things done…", "Help with code…", "Analyze & explain…", "Customize with your API key",
                  "Type a message…", "Bring Your Own API Key", "Image generation", "Video generation"):
        assert words in panel, words
    config = (ROOT / "static/pilot-cyberlogix.js").read_text()
    assert 'tagline: "Your AI copilot. Powered by you."' in config
    assert "themeClass" not in config, "the robot and window look exactly as on the MTRX site"
    assert "kidView: () => false" in config and 'storagePrefix: "cyberlogix-pilot"' in config


def test_the_packs_figures_are_the_ones_the_product_runs_on():
    """The figures come from the server on Pilot's tag; the few the pack
    writes itself are checked against the code here."""
    from auth import LICENCE_GRACE_DAYS
    from costs import TRIAL_CALLS_TOTAL
    from domain import (DISPATCH_SLA_SECONDS, INDUSTRY_PROFILES, PLAN_TIERS, SENSOR_OFFLINE_AFTER_MINUTES,
                        SMS_RETRY_AFTER_MINUTES, UNIT_KINDS, VOICE_ESCALATION_GRACE_MINUTES)
    from pricing import headline, yearly_label

    facts = main.pilot_facts()
    assert facts["fridge-limit"] == "41" and facts["freezer-limit"] == "10"
    assert f"${facts['from-price']}" == "$59" == headline()["starter_label"]
    # Bare numbers: a page that quotes no price of its own still quotes none.
    assert not any("$" in value for value in facts.values())
    assert facts["trial-calls"] == str(TRIAL_CALLS_TOTAL) == "5"
    assert facts["offline-minutes"] == str(SENSOR_OFFLINE_AFTER_MINUTES)
    assert facts["call-after-minutes"] == str(VOICE_ESCALATION_GRACE_MINUTES)
    assert facts["text-within-seconds"] == str(DISPATCH_SLA_SECONDS)
    assert facts["trial-days"] == str(PLAN_TIERS["trial"]["term_days"])

    pack = (ROOT / "static/pilot-cyberlogix-pack.js").read_text()
    clinical = {k["kind"]: k for k in UNIT_KINDS["pharmacy"]}
    assert (f"a fridge between {clinical['fridge']['danger_below']:g}°F and {clinical['fridge']['danger_above']:g}°F, "
            f"a freezer between {clinical['freezer']['danger_below']:g}°F and {clinical['freezer']['danger_above']:g}°F") in pack
    assert f"still watched for {LICENCE_GRACE_DAYS} more days" in pack
    assert "tried again " + ", ".join(str(m) for m in SMS_RETRY_AFTER_MINUTES[:-1]) + \
        f" and {SMS_RETRY_AFTER_MINUTES[-1]} minutes" in pack
    assert f"Growth (up to {PLAN_TIERS['growth']['max_sensors']} sensors" in pack
    assert f"Enterprise (up to {PLAN_TIERS['enterprise']['max_sensors']:,}" in pack
    assert f"{len(INDUSTRY_PROFILES)} industries in all" in pack
    assert f"Paying yearly gives {yearly_label()}" in pack
    for words in ("What you need", "If the power or the internet goes off", "proof of monitoring report",
                  "Simple view", "Advanced", "not a ruling", "STRENGTHS"):
        assert words in pack, words


def test_the_console_keeps_keys_away_from_explain_this_screen():
    """"Analyze & explain" never reads a part marked data-pilot-private: the
    box a new sensor or business key is shown in, and the AI key card."""
    console = (ROOT / "static/console.html").read_text()
    assert '<div id="issued-key" class="banner warn" data-pilot-private hidden' in console
    assert re.search(r'<section class="card" id="find-ai-key"[^>]*data-pilot-private>', console)
    # The console hands Pilot nothing that changes the plan, the on-call
    # list, or sends a text or a call.
    js = (ROOT / "static/console.js").read_text()
    bridge = js[js.index("window.CyberLogixConsole = Object.freeze({"):js.index("/* ---------------- boot")]
    code = re.sub(r"(?m)//.*$", "", re.sub(r"/\*.*?\*/", "", bridge, flags=re.S))
    for word in ("plan", "contacts", "escalate", "test-alert", "DELETE", "erase"):
        assert word not in code, word


def test_pilot_in_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed here (CI has it)")
    scripts = [PILOT / n for n in CORE if n.endswith(".js")] + [
        ROOT / "static" / n for n in ("pilot-cyberlogix.js", "pilot-cyberlogix-pack.js", "console.js")]
    for script in scripts:
        subprocess.run([node, "--check", str(script)], check=True)
    for suite in ("pilot.test.js", "cyberlogix-pilot.test.js"):
        result = subprocess.run([node, "--test", "--test-reporter=tap", str(ROOT / "tests/js" / suite)],
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "# fail 0" in result.stdout, suite


# --- the moving robot (core 1.1.0) -----------------------------------------


def test_the_moving_robot_loads_late_and_never_under_reduce_motion(api):
    """Fetched only once the page is idle, and not at all when the visitor
    asks for less motion; small pictures, and pose pictures only when a move
    needs them: the page names them and loads none."""
    loader = (PILOT / "pilot.js").read_text()
    assert 'window.matchMedia("(prefers-reduced-motion: reduce)").matches' in loader
    assert "requestIdleCallback" in loader
    actor = (PILOT / "pilot-actor.js").read_text()
    assert 'win.matchMedia("(prefers-reduced-motion: reduce)").matches) return null;' in actor
    for part in ("body", "head", "eyes"):
        data = (PILOT / f"pilot-rest-{part}.webp").read_bytes()
        assert data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) < 12_000, part
    total = 0
    for pose in (PILOT / "poses").iterdir():
        data = pose.read_bytes()
        assert data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) < 25_000, pose.name
        total += len(data)
    assert total < 420_000, total
    page = _page(api, "/").text
    poses = re.search(r'data-poses="([^"]*)"', page).group(1).split()
    assert len(poses) == 18
    for pair in poses:
        name, url = pair.split("=", 1)
        assert re.fullmatch(rf"/static/pilot/poses/{re.escape(name)}\.webp\?v=[0-9a-f]{{12}}", url), pair
        assert not re.search(rf'(?:src|href)="[^"]*poses/{re.escape(name)}', page), name
    for name in ("pilot-rest-body.webp", "pilot-rest-head.webp", "pilot-rest-eyes.webp"):
        assert re.search(rf'data-rest-[a-z]+="/static/pilot/{re.escape(name)}\?v=[0-9a-f]{{12}}"', page), name


def test_a_new_pose_picture_is_picked_up(tmp_path):
    """Adding a pose is just putting its picture in static/pilot/poses/."""
    assert main.pilot_pose_files(tmp_path) == {}
    (tmp_path / "kick.png").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    (tmp_path / "dance.webp").write_bytes(b"x")
    assert main.pilot_pose_files(tmp_path) == {"kick": "kick.png"}
    (tmp_path / "kick.webp").write_bytes(b"x")
    assert main.pilot_pose_files(tmp_path) == {"kick": "kick.webp"}
    motion = (PILOT / "pilot-motion.js").read_text()
    names = re.search(r"const POSES = \[([^\]]+)\]", motion).group(1)
    assert tuple(re.findall(r'"([a-z0-9-]+)"', names)) == main.PILOT_POSES


def test_the_robot_keeps_clear_of_alerts_and_the_page_keeps_clear_of_it():
    """avoid: the console's alert banners and the buttons that answer an
    alert; and on a phone the page's last lines sit above the robot."""
    config = (ROOT / "static/pilot-cyberlogix.js").read_text()
    for selector in ("#site-wide-banner", "#account-banner", "#needs-wrap", ".incident-actions"):
        assert f'"{selector}"' in config, selector
    theme = (ROOT / "static/theme.css").read_text()
    assert ":root.pilot-on body { padding-bottom: var(--pilot-gutter, 0px); }" in theme


def test_the_loader_stays_small_with_the_placement_work_fetched_later():
    """Core 1.1.1's placement (the page's words, a box being typed in, the
    side gutter) lives in pilot-mascot.js, fetched after the page has
    loaded; the one script every page loads stays under its budget."""
    loader = (PILOT / "pilot.js").read_text()
    mascot = (PILOT / "pilot-mascot.js").read_text()
    for name in ("function contentIn", "function placeNow", "function sideGutter", "function isEditable"):
        assert name in mascot and name not in loader, name
    assert len(gzip.compress(loader.encode())) < 6_000


def test_the_robot_keeps_clear_of_the_tabs_the_alert_bar_and_the_first_screen():
    """On a phone it sat on "Everything" and on "5 alert calls"; on a desktop
    on "Advanced". And the key settings say what an AI key is."""
    config = (ROOT / "static/pilot-cyberlogix.js").read_text()
    avoid = config[config.index("avoid: ["):config.index("],", config.index("avoid: ["))]
    for sel in (".finder", ".f-tab", "#needs-bar", ".needs-bar-acts", "#facts", ".fix-dialog",
                "#first-day"):
        assert f'"{sel}"' in avoid, sel
    assert "An AI key is a private code you make on" in config
    assert "usually under API keys" in config


def test_the_robot_is_never_tucked_past_the_screen_edge():
    """Round 5: tucked away, it hung off the screen (355-411px on a 375px
    phone, 1330-1418px on a 1366px desktop). The app's own stylesheet keeps
    it inside a 16px margin, shrinking in its corner; no core file changes."""
    theme = (ROOT / "static/theme.css").read_text()
    tail = theme[theme.index("Pilot's robot stays wholly on the screen"):]
    assert ":root .pilot-root { --pilot-gap: 16px; --pilot-peek-show: 71px; }" in tail
    assert "--pilot-gap: 16px; --pilot-peek-show: 51px;" in tail
    assert "transform: scale(.62) !important" in tail
    assert "translateX" not in tail
    # The core still slides it out; the app's rule wins on specificity.
    core = (PILOT / "pilot.css").read_text()
    assert "translateX(var(--pilot-tuck))" in core
