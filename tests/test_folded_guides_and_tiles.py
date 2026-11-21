"""3.24.0: /help/sensors as folds, and the summary tiles compact on a phone.

/help/sensors was about 13,700px on a phone: every guide's steps and
example, one after the other. Each guide is now a fold carrying the
guide's id, so /help/sensors#monnit still lands on Monnit, and
help-sensors.js opens the fold the address names. The seven summary tiles
were four tall rows on a phone; they are a compact two-column grid.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from sensor_guides import GUIDES

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "static" / "help-sensors.js"


def test_each_guide_is_a_fold_with_its_own_id(api):
    page = api.get("/help/sensors").text
    for guide in GUIDES:
        fold = re.search(rf'<details class="fold guide" id="{re.escape(guide["id"])}">'
                         rf'<summary><h2>([^<]+)</h2></summary>(.*?)</details>', page, re.S)
        assert fold, guide["id"]
        assert "<ol class=\"steps\">" in fold.group(2) and "<pre>" in fold.group(2)
        # Closed until somebody opens it (or a link names it).
        assert " open" not in fold.group(0)[:80]
    # Each id once: the link and the fold are the same element.
    for guide in GUIDES:
        assert page.count(f'id="{guide["id"]}"') == 1, guide["id"]
    # The row of buttons to each guide was the folds again; it is gone.
    assert 'class="help-links"' not in page
    # The help desk's link to "What you need" still lands on its section.
    assert 'id="what-you-need"' in page


def test_the_page_loads_the_script_that_opens_a_linked_guide(api):
    page = api.get("/help/sensors").text
    assert re.search(r'<script src="/static/help-sensors\.js\?v=[0-9a-f]+" defer></script>', page)
    assert "<script>" not in page and 'style="' not in page
    js = SCRIPT.read_text()
    assert 'window.addEventListener("hashchange", openFor);' in js
    assert "d.open = true" in js and "scrollIntoView" in js
    css = (ROOT / "static" / "pages.css").read_text()
    assert "details.guide, #what-you-need, #tech { scroll-margin-top: 84px; }" in css


def test_the_script_opens_the_fold_the_address_names_in_node(tmp_path):
    """Runs the real file in Node with a stand-in page: arriving at
    #monnit opens Monnit (and nothing else); changing the address to
    #sensorpush opens that one too."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed here (CI has it)")
    harness = tmp_path / "run.js"
    harness.write_text(r"""
const fs = require("node:fs");
const vm = require("node:vm");
function el(tag, id, parent) {
  return { tagName: tag, id, parentElement: parent || null, open: false, scrolled: 0, on: {},
    closest(sel) { let n = this; while (n) { if (sel === "details" && n.tagName === "DETAILS") return n; n = n.parentElement; } return null; },
    addEventListener(t, f) { this.on[t] = f; }, scrollIntoView() { this.scrolled += 1; } };
}
const folds = ["native", "monnit", "sensorpush"].map((id) => el("DETAILS", id));
const wide = el("DETAILS", "");
const inside = el("H3", "inner", el("DIV", "", folds[2]));
const byId = Object.fromEntries([...folds, inside].map((e) => [e.id, e]));
const listeners = {};
const location = { hash: "#monnit" };
const replaced = [];
const window = {
  location, scrollY: 0,
  matchMedia: () => ({ matches: false }),
  addEventListener: (t, f) => { listeners[t] = f; },
  history: { replaceState: (a, b, url) => { replaced.push(url); location.hash = url; } },
};
const document = {
  getElementById: (id) => byId[id] || null,
  querySelectorAll: (sel) => (sel === "details.guide[id]" ? folds : sel === "details[data-open-wide]" ? [wide] : []),
  body: {},
};
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), { window, document, setTimeout, decodeURIComponent });
const out = { arrived: folds.map((f) => f.open), scrolled: byId.monnit.scrolled > 0, wideOnPhone: wide.open };
location.hash = "#inner";
listeners.hashchange();
out.inner = folds[2].open;
// Opening one by hand names it in the address.
folds[0].open = true;
folds[0].on.toggle();
out.replaced = replaced;
console.log(JSON.stringify(out));
""")
    result = subprocess.run([node, str(harness), str(SCRIPT)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip())
    assert out["arrived"] == [False, True, False]
    assert out["scrolled"] is True
    assert out["wideOnPhone"] is False
    assert out["inner"] is True
    assert out["replaced"] == ["#native"]


def test_the_summary_tiles_are_a_compact_two_column_grid_on_a_phone():
    css = (ROOT / "static" / "theme.css").read_text()
    phone = css[css.index("/* ---------- narrow screens ---------- */"):]
    phone = phone[:phone.index("\n}\n")]
    assert ".tiles { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px;" in phone
    assert ".tiles > .tile:last-child:nth-child(odd) { grid-column: 1 / -1; }" in phone
    assert "display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: baseline;" in phone
    assert ".tile-label { grid-column: 1 / -1; font-size: 9.5px;" in phone
    assert ".tiles > .tile { flex: 1 1 132px; }" not in phone
