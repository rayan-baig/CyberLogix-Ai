"""A leaked partner key can be cut off, and is never kept in the clear.

Before: no route suspended a partner or replaced their key, so a leaked key
could only be stopped by detaching every account one by one, and the key sat
in the database as plain text.
"""

import json
import sqlite3

from db import Database
from store import HubStore, hash_secret
from test_partners import admin, make_partner  # noqa: F401  (fixture)


def test_suspending_a_partner_closes_the_portal(api, admin):  # noqa: F811
    body, key = make_partner(api, admin)
    assert api.get("/api/partners/me", headers=key).status_code == 200
    resp = api.post(f"/api/partners/{body['partner_id']}/status", headers=admin, json={"active": False})
    assert resp.status_code == 200
    assert api.get("/api/partners/me", headers=key).status_code == 403
    api.post(f"/api/partners/{body['partner_id']}/status", headers=admin, json={"active": True})
    assert api.get("/api/partners/me", headers=key).status_code == 200


def test_rotating_a_key_retires_the_old_one(api, admin):  # noqa: F811
    body, old = make_partner(api, admin)
    resp = api.post(f"/api/partners/{body['partner_id']}/rotate-key", headers=admin)
    assert resp.status_code == 200
    new_key = resp.json()["api_key"]
    assert new_key.startswith("clx_ptr_") and new_key != old["X-CyberLogix-Partner"]
    assert api.get("/api/partners/me", headers=old).status_code == 401
    assert api.get("/api/partners/me", headers={"X-CyberLogix-Partner": new_key}).status_code == 200
    assert new_key not in api.get("/api/partners", headers=admin).text


def test_only_the_platform_can_suspend_or_rotate(api, admin):  # noqa: F811
    body, key = make_partner(api, admin)
    for path, payload in ((f"/api/partners/{body['partner_id']}/status", {"active": False}),
                          (f"/api/partners/{body['partner_id']}/rotate-key", None)):
        assert api.post(path, json=payload).status_code == 401
        assert api.post(path, headers=key, json=payload).status_code == 401
    assert api.post("/api/partners/PTR-nope/rotate-key", headers=admin).status_code == 404


def test_the_key_is_not_stored(tmp_path):
    path = str(tmp_path / "hub.sqlite3")
    store = HubStore(db=Database(path))
    partner = store.create_partner("Acme", "Ray", "ray@acme.example", 20.0)
    raw = partner.api_key
    con = sqlite3.connect(path)
    everything = ""
    for (table,) in con.execute("select name from sqlite_master where type='table'"):
        everything += json.dumps(con.execute(f"select * from {table}").fetchall())
    assert raw not in everything
    assert hash_secret(raw) in everything
    again = HubStore(db=Database(path))
    assert again.partner_by_key(raw).partner_id == partner.partner_id
    assert again.partner_by_key("clx_ptr_wrong") is None
    assert again.partner_by_key("") is None


def test_a_key_stored_before_hashing_still_works_and_is_rewritten(tmp_path):
    path = str(tmp_path / "hub.sqlite3")
    db = Database(path)
    db.put("partner", "PTR-1", {"partner_id": "PTR-1", "company_name": "Old", "contact_name": "R",
                                "contact_email": "r@x.example", "api_key": "clx_ptr_legacy_plain_key",
                                "commission_percent": 20.0, "active": True, "created_at": None})
    store = HubStore(db=Database(path))
    assert store.partner_by_key("clx_ptr_legacy_plain_key").partner_id == "PTR-1"
    con = sqlite3.connect(path)
    everything = ""
    for (table,) in con.execute("select name from sqlite_master where type='table'"):
        everything += json.dumps(con.execute(f"select * from {table}").fetchall())
    assert "clx_ptr_legacy_plain_key" not in everything


def test_business_and_sensor_keys_are_not_stored(tmp_path):
    """A backup used to hold every business's master key and every sensor's key."""
    path = str(tmp_path / "hub.sqlite3")
    store = HubStore(db=Database(path))
    tenant = store.create_tenant("A", "n", "+1", "a@example.com", "growth")
    sensor = store.register_sensor("FRZ-1", tenant.tenant_id, "restaurant", "Walk-in")
    con = sqlite3.connect(path)
    everything = ""
    for (table,) in con.execute("select name from sqlite_master where type='table'"):
        everything += json.dumps(con.execute(f"select * from {table}").fetchall())
    assert tenant.api_key.startswith("clx_") and tenant.api_key not in everything
    assert sensor.ingest_key.startswith("clx_snr_") and sensor.ingest_key not in everything
    again = HubStore(db=Database(path))
    assert again.tenant_by_key(tenant.api_key).tenant_id == tenant.tenant_id
    assert again.sensor_by_ingest_key(sensor.ingest_key).sensor_id == "FRZ-1"
    assert again.tenant_by_key(tenant.key_hash) is None


def test_keys_stored_before_hashing_keep_working(tmp_path):
    path = str(tmp_path / "hub.sqlite3")
    store = HubStore(db=Database(path))
    tenant = store.create_tenant("A", "n", "+1", "a@example.com", "growth")
    sensor = store.register_sensor("FRZ-1", tenant.tenant_id, "restaurant", "Walk-in")
    # Rewrite both rows the way an older version stored them.
    trow = {**tenant.to_row(), "api_key": "clx_legacy_tenant"}
    trow.pop("key_hash")
    srow = {**sensor.to_row(), "ingest_key": "clx_snr_legacy_sensor"}
    srow.pop("ingest_key_hash")
    store._db.put("tenant", tenant.tenant_id, trow)
    store._db.put("sensor", sensor.sensor_id, srow)
    again = HubStore(db=Database(path))
    assert again.tenant_by_key("clx_legacy_tenant").tenant_id == tenant.tenant_id
    assert again.sensor_by_ingest_key("clx_snr_legacy_sensor").sensor_id == "FRZ-1"
    raw = json.dumps(sqlite3.connect(path).execute("select * from sqlite_master").fetchall())
    third = HubStore(db=Database(path))
    assert "clx_legacy_tenant" not in json.dumps(third._db.get("tenant", tenant.tenant_id)) and raw
