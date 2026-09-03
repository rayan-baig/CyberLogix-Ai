"""BYOD webhook ingestion and sector meeting intelligence."""

import json

from store import STORE


def bind_device(api, headers, sensor_id="FRZ-1", serial="ELITECH-AB12", vertical="restaurant"):
    resp = api.post(
        "/api/licenses/me/sensors",
        headers=headers,
        json={
            "sensor_id": sensor_id,
            "industry_vertical": vertical,
            "location_name": "Store 118 / Walk-In",
            "external_device_sn": serial,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["sensor"]


def webhook(api, token, serial="ELITECH-AB12", value=28.0, metric="temperature_f", label=None):
    payload = {
        "device_sn": serial,
        "api_key_token": token,
        "reading_value": value,
        "metric_type": metric,
    }
    if label is not None:
        payload["location_label"] = label
    return api.post("/api/v1/bridge/sensor-webhook-ingest", json=payload)


# ---- Part 1: BYOD hardware webhook ------------------------------------


def test_nominal_webhook_reading(api, tenant_factory):
    headers, _ = tenant_factory()
    bind_device(api, headers)
    body = webhook(api, headers["X-CyberLogix-Key"], value=28.0).json()

    assert body["status"] == "INGESTION_SUCCESS"
    assert body["alert_triggered"] is False
    assert body["bound_sensor_id"] == "FRZ-1"
    assert body["telemetry_result"]["status"] == "nominal"


def test_webhook_breach_opens_a_real_incident(api, tenant_factory):
    headers, _ = tenant_factory()
    bind_device(api, headers)
    body = webhook(api, headers["X-CyberLogix-Key"], value=45.0).json()

    assert body["alert_triggered"] is True
    incident_id = body["telemetry_result"]["incident_id"]
    assert incident_id.startswith("INC-")

    # The BYOD reading reaches the rest of the platform, not a dead end.
    incidents = api.get("/api/voice/incidents", headers=headers).json()
    assert incidents["count"] == 1
    assert incidents["incidents"][0]["incident_id"] == incident_id

    compliance = api.get("/api/autopilot/compliance", headers=headers).json()
    assert compliance["total_readings_logged"] == 1
    assert compliance["total_readings_breached"] == 1


def test_thresholds_follow_the_industry_not_a_flat_number(api, tenant_factory):
    """45F is a catastrophe in a freezer and unremarkable in a hangar."""
    headers, _ = tenant_factory()
    bind_device(api, headers, sensor_id="FRZ-1", serial="SN-FREEZER", vertical="restaurant")
    bind_device(
        api, headers, sensor_id="HANGAR-1", serial="SN-HANGAR", vertical="private_aviation"
    )
    token = headers["X-CyberLogix-Key"]

    assert webhook(api, token, serial="SN-FREEZER", value=45.0).json()["alert_triggered"] is True
    assert webhook(api, token, serial="SN-HANGAR", value=45.0).json()["alert_triggered"] is False


def test_celsius_is_normalised(api, tenant_factory):
    headers, _ = tenant_factory()
    bind_device(api, headers)
    body = webhook(
        api, headers["X-CyberLogix-Key"], value=7.0, metric="temperature_c"
    ).json()

    assert body["normalised_temperature_f"] == 44.6
    assert body["alert_triggered"] is True


def test_humidity_is_stored_as_context(api, tenant_factory):
    headers, _ = tenant_factory()
    bind_device(api, headers)
    body = webhook(
        api, headers["X-CyberLogix-Key"], value=61.5, metric="humidity_pct"
    ).json()

    assert body["alert_triggered"] is False
    assert STORE.get_sensor("FRZ-1").last_humidity == 61.5


def test_out_of_range_humidity_rejected(api, tenant_factory):
    headers, _ = tenant_factory()
    bind_device(api, headers)
    resp = webhook(
        api, headers["X-CyberLogix-Key"], value=140.0, metric="humidity_pct"
    )
    assert resp.status_code == 400


def test_unsupported_metric_rejected(api, tenant_factory):
    headers, _ = tenant_factory()
    bind_device(api, headers)
    resp = webhook(api, headers["X-CyberLogix-Key"], metric="pressure_psi")
    assert resp.status_code == 400
    assert "Unsupported metric_type" in resp.json()["detail"]


def test_webhook_rejects_a_bad_token(api, tenant_factory):
    headers, _ = tenant_factory()
    bind_device(api, headers)
    resp = webhook(api, "clx_not_a_real_key")
    assert resp.status_code == 401


def test_webhook_rejects_a_suspended_license(api, tenant_factory):
    headers, _ = tenant_factory()
    bind_device(api, headers)
    api.post("/api/licenses/me/suspend", headers=headers)

    resp = webhook(api, headers["X-CyberLogix-Key"])
    assert resp.status_code == 402


def test_unbound_device_is_rejected(api, tenant_factory):
    headers, _ = tenant_factory()
    resp = webhook(api, headers["X-CyberLogix-Key"], serial="GHOST-SN")
    assert resp.status_code == 404
    assert "not bound to a licensed sensor" in resp.json()["detail"]


def test_device_cannot_report_into_another_tenant(api, tenant_factory):
    alice, _ = tenant_factory(company_name="Alice Foods")
    bob, _ = tenant_factory(company_name="Bob Labs")
    bind_device(api, alice, sensor_id="ALICE-1", serial="ALICE-SN")

    resp = webhook(api, bob["X-CyberLogix-Key"], serial="ALICE-SN")
    assert resp.status_code == 404


def test_duplicate_device_serial_rejected(api, tenant_factory):
    headers, _ = tenant_factory()
    bind_device(api, headers, sensor_id="FRZ-1", serial="DUP-SN")
    resp = api.post(
        "/api/licenses/me/sensors",
        headers=headers,
        json={
            "sensor_id": "FRZ-2",
            "industry_vertical": "restaurant",
            "location_name": "Store 119",
            "external_device_sn": "DUP-SN",
        },
    )
    assert resp.status_code == 409
    assert "already bound" in resp.json()["detail"]


def test_serial_falls_back_to_sensor_id(api, tenant_factory, sensor_factory):
    """Hardware registered without an explicit serial still resolves."""
    headers, _ = tenant_factory()
    sensor_factory(headers, sensor_id="RACK-01")
    body = webhook(api, headers["X-CyberLogix-Key"], serial="RACK-01", value=68.0).json()
    assert body["bound_sensor_id"] == "RACK-01"


def test_device_label_updates_the_site_tag(api, tenant_factory):
    headers, _ = tenant_factory()
    bind_device(api, headers)
    webhook(api, headers["X-CyberLogix-Key"], label="Store 118 / Prep Cooler")
    assert STORE.get_sensor("FRZ-1").location_name == "Store 118 / Prep Cooler"


def test_decommission_releases_the_serial(api, tenant_factory):
    headers, _ = tenant_factory()
    bind_device(api, headers, sensor_id="FRZ-1", serial="REUSE-SN")
    api.delete("/api/licenses/me/sensors/FRZ-1", headers=headers)

    resp = api.post(
        "/api/licenses/me/sensors",
        headers=headers,
        json={
            "sensor_id": "FRZ-2",
            "industry_vertical": "restaurant",
            "location_name": "Store 119",
            "external_device_sn": "REUSE-SN",
        },
    )
    assert resp.status_code == 201


# ---- Part 2: meeting intelligence -------------------------------------


REPORT = {
    "executive_summary": "Walk-in cooler flagged for gasket replacement.",
    "extracted_operational_decisions": ["Replace the gasket before Friday service."],
    "action_items_assigned": [
        {"task": "Order gasket", "owner": "Marco", "priority": "High"}
    ],
    "industry_compliance_impact": "Reduces spoilage liability at inspection.",
}


def summarize(api, headers, transcript="Marco will order the gasket.", vertical="restaurant"):
    return api.post(
        "/api/v1/bridge/summarize-transcript",
        headers=headers,
        json={"industry_vertical": vertical, "raw_transcript": transcript},
    )


def test_transcript_is_structured(api, tenant_factory, stub_gemini):
    headers, _ = tenant_factory()
    stub_gemini._text = json.dumps(REPORT)

    body = summarize(api, headers).json()
    assert body["status"] == "TRANSCRIPT_PROCESSED_SUCCESSFULLY"
    assert body["report_source"] == "gemini"
    assert body["intelligence_report"]["action_items_assigned"][0]["owner"] == "Marco"
    assert "food spoilage liability" in body["sector_directive"]


def test_markdown_fenced_json_is_parsed(api, tenant_factory, stub_gemini):
    headers, _ = tenant_factory()
    stub_gemini._text = f"```json\n{json.dumps(REPORT)}\n```"

    body = summarize(api, headers).json()
    assert body["status"] == "TRANSCRIPT_PROCESSED_SUCCESSFULLY"
    assert body["intelligence_report"]["executive_summary"].startswith("Walk-in")


def test_prose_wrapped_json_is_salvaged(api, tenant_factory, stub_gemini):
    headers, _ = tenant_factory()
    stub_gemini._text = f"Sure, here you go:\n{json.dumps(REPORT)}\nHope that helps!"

    body = summarize(api, headers).json()
    assert body["status"] == "TRANSCRIPT_PROCESSED_SUCCESSFULLY"


def test_action_item_shapes_are_normalised(api, tenant_factory, stub_gemini):
    headers, _ = tenant_factory()
    ragged = dict(REPORT)
    ragged["action_items_assigned"] = ["Call the engineer", {"task": "Log it"}]
    ragged["extracted_operational_decisions"] = "Single decision as a string"
    stub_gemini._text = json.dumps(ragged)

    report = summarize(api, headers).json()["intelligence_report"]
    assert report["action_items_assigned"][0] == {
        "task": "Call the engineer",
        "owner": "Unassigned",
        "priority": "Med",
    }
    assert report["action_items_assigned"][1]["owner"] == "Unassigned"
    assert report["extracted_operational_decisions"] == ["Single decision as a string"]


def test_unparseable_reply_is_reported_not_invented(
    api, tenant_factory, stub_gemini
):
    headers, _ = tenant_factory()
    stub_gemini._text = "I'm afraid I can't do that."

    body = summarize(api, headers).json()
    assert body["status"] == "TRANSCRIPT_PROCESSING_DEGRADED"
    assert body["intelligence_report"] is None
    assert "unparseable" in body["degraded_reason"]


def test_reply_missing_required_keys_is_degraded(
    api, tenant_factory, stub_gemini
):
    headers, _ = tenant_factory()
    stub_gemini._text = json.dumps({"executive_summary": "Only this one key."})

    body = summarize(api, headers).json()
    assert body["status"] == "TRANSCRIPT_PROCESSING_DEGRADED"
    assert body["intelligence_report"] is None


def test_gemini_outage_is_degraded_not_fabricated(
    api, tenant_factory, break_gemini
):
    headers, _ = tenant_factory()
    break_gemini("outage")

    body = summarize(api, headers).json()
    assert body["status"] == "TRANSCRIPT_PROCESSING_DEGRADED"
    assert body["intelligence_report"] is None
    assert body["report_source"] == "fallback_template"


def test_summarizer_requires_authentication(api):
    resp = api.post(
        "/api/v1/bridge/summarize-transcript",
        json={"industry_vertical": "restaurant", "raw_transcript": "hello"},
    )
    assert resp.status_code == 401


def test_unknown_vertical_rejected(api, tenant_factory):
    headers, _ = tenant_factory()
    resp = summarize(api, headers, vertical="casino")
    assert resp.status_code == 400


def test_empty_transcript_rejected(api, tenant_factory):
    headers, _ = tenant_factory()
    resp = summarize(api, headers, transcript="")
    assert resp.status_code == 422


def test_sector_directives_are_listed(api):
    body = api.get("/api/v1/bridge/sectors").json()
    assert body["count"] == 8
    club = next(s for s in body["sectors"] if s["vertical"] == "country_club")
    assert club["name"] == "High-End Country Clubs"
