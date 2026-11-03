"""Retuning a limit from the console asks in the unit the card shows.

The sensor card shows a Celsius account its limits in Celsius. Retune
then asked for the new limit "in °F", pre-filled with the Fahrenheit
figure, so somebody reading "5°C" on the card who typed 5 set the limit
to 5°F, and every reading from that fridge alarmed.

The handler is run for real under Node, with the browser pieces stubbed,
and the body it would post is checked.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from pages import page_source

PAGE = Path(__file__).resolve().parent.parent / "static" / "console.html"


def _tune_handler() -> str:
    src = page_source(PAGE)
    start = src.index("  if (btn.dataset.tune) {")
    end = src.index("  if (btn.dataset.shortcut) {", start)
    return src[start:end]


def _run(sensor, answer):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    harness = f"""
    const sensor = {json.dumps(sensor)};
    const out = {{}};
    const state = {{ unit: sensor.temperature_unit, data: {{ sensors: [sensor] }} }};
    const btn = {{ dataset: {{ tune: sensor.sensor_id }} }};
    const prompt = (text, current) => {{ out.asked = text; out.prefilled = current; return {json.dumps(answer)}; }};
    const toast = (text) => {{ out.toast = text; }};
    const api = (url, opts) => {{ out.body = JSON.parse(opts.body); return Promise.resolve({{}}); }};
    const act = (fn, message) => {{ fn(); out.message = message; }};
    (function () {{
    {_tune_handler()}
    }})();
    console.log(JSON.stringify(out));
    """
    done = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


SENSOR = {
    "sensor_id": "FRZ-1", "industry_name": "Franchise Restaurants",
    "danger_above": 41.0, "danger_above_display": 5.0,
    "override_below": None,
}


def test_a_celsius_account_is_asked_in_celsius_and_the_api_gets_fahrenheit():
    out = _run({**SENSOR, "temperature_unit": "C"}, "4")

    assert "°C" in out["asked"] and "°F" not in out["asked"]
    assert out["prefilled"] == "5"
    assert out["body"]["danger_above"] == pytest.approx(39.2)
    assert "4°C" in out["message"]


def test_a_fahrenheit_account_is_unchanged():
    out = _run({**SENSOR, "temperature_unit": "F", "danger_above_display": 41.0}, "38")

    assert "°F" in out["asked"]
    assert out["prefilled"] == "41"
    assert out["body"]["danger_above"] == 38


def _trend(trend_f, unit):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    src = page_source(PAGE)
    start = src.index("    const perHour = f.trend_f_per_hour === null")
    end = src.index("    const shape = f.pattern", start)
    harness = f"""
    const state = {{ unit: {json.dumps(unit)} }};
    const f = {{ trend_f_per_hour: {json.dumps(trend_f)} }};
    {src[start:end]}
    console.log(JSON.stringify(trend));
    """
    done = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_forecast_drift_is_written_in_the_account_unit():
    """1.8°F an hour is 1°C an hour; shown as "+1.8°/h" to a Celsius
    account it read as nearly twice as fast as it is."""
    assert _trend(1.8, "C") == "+1°C/h"
    assert _trend(1.8, "F") == "+1.8°F/h"
    assert _trend(None, "C") == ""
