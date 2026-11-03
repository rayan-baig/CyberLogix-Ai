"""The sensor setup guides say what the code does.

Every route a guide sends somebody to exists with that method; every
header and query parameter is one that route reads; every field is one
the model behind it has; every preset field is the one byod.py matches
on. The words of each step are scanned too, so a path or a header
written into a step without being declared fails here. And each guide's
example message is put through the real route, end to end, and lands as
a reading on the unit the guide set up.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

import byod
import sensor_guides
from main import app
from store import STORE

ROOT = Path(__file__).resolve().parent.parent
GUIDES = sensor_guides.guides()


def _routes():
    found = {}

    def walk(routes):
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                walk(inner.routes)
            elif hasattr(route, "routes"):
                walk(route.routes)
            else:
                for method in getattr(route, "methods", None) or ():
                    found[(method, route.path)] = route

    walk(app.routes)
    return found


ROUTES = _routes()


def _params(route, kind):
    """Every header or query name a route reads, its dependencies included."""
    names = set()

    def walk(dependant):
        for param in getattr(dependant, f"{kind}_params"):
            alias = param.alias or param.name
            names.add(alias.lower().replace("_", "-"))
        for sub in dependant.dependencies:
            walk(sub)

    walk(route.dependant)
    return names


def _all_text(guide):
    return " ".join([guide["title"], guide["for"], *guide["steps"]])


@pytest.mark.parametrize("guide", GUIDES, ids=[g["id"] for g in GUIDES])
def test_every_route_named_exists(guide):
    for method, path in guide["routes"]:
        assert (method, path) in ROUTES, (guide["id"], method, path)


@pytest.mark.parametrize("guide", GUIDES, ids=[g["id"] for g in GUIDES])
def test_every_header_and_query_parameter_is_read_by_its_route(guide):
    for path, headers in guide["headers"].items():
        route = ROUTES[("POST", path)]
        reads = _params(route, "header")
        for header in headers:
            assert header.lower() in reads, (guide["id"], path, header, reads)
    for path, names in guide["query"].items():
        route = ROUTES[("POST", path)]
        reads = _params(route, "query")
        for name in names:
            assert name in reads, (guide["id"], path, name, reads)


@pytest.mark.parametrize("guide", GUIDES, ids=[g["id"] for g in GUIDES])
def test_every_field_is_on_the_model(guide):
    for dotted, fields in guide["fields"].items():
        module, name = dotted.rsplit(".", 1)
        model = getattr(importlib.import_module(module), name)
        for field in fields:
            assert field in model.model_fields, (guide["id"], dotted, field)


@pytest.mark.parametrize("guide", GUIDES, ids=[g["id"] for g in GUIDES])
def test_every_preset_field_is_the_one_matched_on(guide):
    for preset, roles in guide["presets"].items():
        for role in roles:
            name = byod.PRESETS[preset][role]
            assert name and name in _all_text(guide), (guide["id"], role, name)


@pytest.mark.parametrize("guide", GUIDES, ids=[g["id"] for g in GUIDES])
def test_nothing_is_named_in_the_words_that_was_not_declared(guide):
    text = _all_text(guide).replace(sensor_guides.BASE, "")
    paths = set(re.findall(r"(/api/[A-Za-z0-9_/{}.-]*[A-Za-z0-9_}])", text))
    declared = {path for _, path in guide["routes"]}
    assert paths <= declared, (guide["id"], paths - declared)
    headers = set(re.findall(r"\b(X-CyberLogix-[A-Za-z-]+)", text))
    named = {h for hs in guide["headers"].values() for h in hs}
    assert headers <= named, (guide["id"], headers - named)
    for field in re.findall(r"\b([a-z]+(?:_[a-z]+)+)\b", text):
        declared_fields = {f for fs in guide["fields"].values() for f in fs}
        known = (declared_fields | set(byod.SERIAL_KEYS) | set(byod.VALUE_KEYS)
                 | {"temp_c", "temperature_c", "temperature_f", "humidity_pct",
                    "battery_pct", "signal_pct", "sensor_id"})
        assert field in known, (guide["id"], field)


@pytest.mark.parametrize("guide", GUIDES, ids=[g["id"] for g in GUIDES])
def test_each_example_is_read_the_way_the_guide_says(guide):
    example = guide["example"]
    if guide["id"] in ("native", "webhook"):
        return  # typed models, checked against the route below
    rows = example.get("readings") or [example]
    for row in rows:
        verdict = byod.detect(row)
        assert verdict["understood"], (guide["id"], verdict["missing"])
        assert verdict["unit"] is not None, guide["id"]
    if guide["presets"]:
        assert byod.detect(example)["preset"] == byod.PRESETS[
            next(iter(guide["presets"]))]["name"]


# ---- end to end: each way in, as the guide describes it ---------------------


@pytest.fixture()
def unit(api, tenant_factory):
    """A unit set up as step 1 says, and its sensor key from step 2."""
    headers, _ = tenant_factory(plan="enterprise")

    def _make(sensor_id, serial=None):
        body = {"sensor_id": sensor_id, "industry_vertical": "restaurant",
                "location_name": "Walk-in"}
        if serial:
            body["external_device_sn"] = serial
        made = api.post("/api/licenses/me/sensors", headers=headers, json=body)
        assert made.status_code == 201, made.text
        return made.json()["ingest_key"], headers

    return _make


def _guide(key):
    return next(g for g in GUIDES if g["id"] == key)


def test_the_native_guide_end_to_end(api, unit):
    key, _ = unit("WALKIN-1")
    resp = api.post("/api/sensor-pulse", json=_guide("native")["example"],
                    headers={"X-CyberLogix-Sensor-Key": key,
                             "Content-Type": "application/json"})
    assert resp.status_code == 200, resp.text
    assert STORE.get_sensor("WALKIN-1").last_temperature == 2.5
    # The answers the last step lists.
    assert api.post("/api/sensor-pulse", json=_guide("native")["example"],
                    headers={"X-CyberLogix-Sensor-Key": "nope"}).status_code == 401
    other, _ = unit("WALKIN-2")
    assert api.post("/api/sensor-pulse", json=_guide("native")["example"],
                    headers={"X-CyberLogix-Sensor-Key": other}).status_code == 403


def test_the_webhook_guide_end_to_end(api, unit):
    example = dict(_guide("webhook")["example"])
    key, _ = unit("COOLER-1", serial=example["device_sn"])
    example["api_key_token"] = key
    resp = api.post("/api/v1/bridge/sensor-webhook-ingest", json=example)
    assert resp.status_code == 200, resp.text
    assert STORE.get_sensor("COOLER-1").last_seen is not None
    stranger = dict(example, device_sn="NOT-SET-UP")
    _, headers = unit("COOLER-2")
    stranger["api_key_token"] = headers["X-CyberLogix-Key"]
    assert api.post("/api/v1/bridge/sensor-webhook-ingest",
                    json=stranger).status_code == 202


@pytest.mark.parametrize("how", ["query", "sensor header"])
def test_the_monnit_guide_end_to_end(api, unit, how):
    example = _guide("monnit")["example"]
    key, _ = unit("MONNIT-1", serial=example["SensorID"])
    if how == "query":
        resp = api.post(f"/api/v1/bridge/any?k={key}", json=example)
    else:
        resp = api.post("/api/v1/bridge/any", json=example,
                        headers={"X-CyberLogix-Sensor-Key": key})
    assert resp.status_code == 200, resp.text
    assert resp.json()["understood_as"]["unit"] == "temperature_c"


def test_the_sensor_key_header_works_on_the_send_anything_route(api, unit):
    """The key's own notice says to send it as X-CyberLogix-Sensor-Key, and
    this route did not read that header: a device set up as told got 401."""
    example = _guide("anything")["example"]
    key, _ = unit("DICKSON-1", serial=example["Device ID"])
    resp = api.post("/api/v1/bridge/any", json=example,
                    headers={"X-CyberLogix-Sensor-Key": key})
    assert resp.status_code == 200, resp.text
    # And it is still only good for its own unit.
    other = dict(example, **{"Device ID": "SOMEBODY-ELSE"})
    unit("OTHER-1", serial="SOMEBODY-ELSE")
    assert api.post("/api/v1/bridge/any", json=other,
                    headers={"X-CyberLogix-Sensor-Key": key}).status_code == 403


def test_the_sensorpush_guide_end_to_end(api, unit):
    example = _guide("sensorpush")["example"]
    key, _ = unit("PUSH-1", serial=example["sensor"])
    resp = api.post("/api/v1/bridge/any", json=example,
                    headers={"X-CyberLogix-Sensor-Key": key})
    assert resp.status_code == 200, resp.text
    assert resp.json()["understood_as"]["unit"] == "temperature_f"
    # The Celsius form the guide gives.
    celsius = {"sensor_id": example["sensor"], "temp_c": 3.0}
    resp = api.post("/api/v1/bridge/any", json=celsius,
                    headers={"X-CyberLogix-Sensor-Key": key})
    assert resp.status_code == 200, resp.text
    assert resp.json()["understood_as"]["unit"] == "temperature_c"


def test_the_vendor_cloud_guide_end_to_end(api, operator_factory, sensor_factory,
                                           monkeypatch):
    import pollers

    headers, _, _ = operator_factory()
    serial = _guide("vendor-cloud")["example"]["readings"][0]["device_id"]
    api.post("/api/licenses/me/sensors", headers=headers, json={
        "sensor_id": "CLOUD-1", "industry_vertical": "restaurant",
        "location_name": "Walk-in", "external_device_sn": serial})
    added = api.post("/api/pollers", headers=headers, json={
        "name": "Vendor", "url": "https://api.vendor.example/latest",
        "headers": {"Authorization": "Bearer t"}, "interval_minutes":
            sensor_guides.DEFAULT_INTERVAL_MINUTES})
    assert added.status_code == 201, added.text
    monkeypatch.setattr(pollers, "_fetch",
                        lambda url, hdrs: _guide("vendor-cloud")["example"])
    source_id = added.json()["source"]["source_id"]
    tried = api.post(f"/api/pollers/{source_id}/test", headers=headers)
    assert tried.status_code == 200, tried.text
    assert STORE.get_sensor("CLOUD-1").last_seen is not None


def test_the_preview_the_guide_sends_people_to_reads_the_example(api, tenant_factory):
    headers, _ = tenant_factory()
    resp = api.post("/api/v1/bridge/preview", headers=headers,
                    json={"payload": _guide("anything")["example"]})
    assert resp.status_code == 200
    assert resp.json()["understood"] and resp.json()["unit"] == "temperature_c"


# ---- where the guides are ---------------------------------------------------


def test_the_page_shows_every_guide_with_this_address(api):
    resp = api.get("/help/sensors")
    assert resp.status_code == 200
    page = resp.text
    for guide in GUIDES:
        assert f'id="{guide["id"]}"' in page
    assert "http://testserver/api/sensor-pulse" in page
    assert "{base}" not in page and "%%" not in page
    assert "style=" not in page and "<script>" not in page


def test_the_help_page_links_to_it():
    assert 'href="/help/sensors"' in (ROOT / "static" / "help.html").read_text()


def test_it_is_listed_for_search_engines(api):
    assert "/help/sensors" in api.get("/sitemap.xml").text


def test_the_docs_file_is_what_the_module_renders():
    on_disk = (ROOT / "docs" / "SENSOR-SETUP.md").read_text()
    assert on_disk == sensor_guides.render_markdown(), (
        "docs/SENSOR-SETUP.md is out of date: run "
        "python sensor_guides.py > docs/SENSOR-SETUP.md")


def test_the_docs_use_a_reserved_domain_only():
    text = (ROOT / "docs" / "SENSOR-SETUP.md").read_text()
    hosts = set(re.findall(r"https?://([a-z0-9.-]+)", text))
    assert hosts and all(h.endswith(".example") or h.endswith("example.com")
                         for h in hosts), hosts


def test_examples_are_valid_json():
    for guide in GUIDES:
        json.loads(json.dumps(guide["example"]))
