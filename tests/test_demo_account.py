"""The demo account: believable, into an empty database only, and its
backups kept well away from the live ones.

Most of this seeds the suite's in-memory store directly. The command
itself, and what its database does with backups, run in separate
processes against throwaway files, because that is the only way to see
which folder a snapshot really lands in.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

import demo_account
import oncall
from store import STORE, utc_now

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "demo-password-for-tests"


@pytest.fixture()
def demo():
    return demo_account.seed(password=PASSWORD)


def _sign_in(api):
    resp = api.post("/api/accounts/login", json={
        "email": demo_account.OWNER[1], "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


# ---- what it seeds -----------------------------------------------------------


def test_a_restaurant_group_with_three_sites_and_twenty_units(demo):
    (tenant,) = STORE.list_tenants()
    assert tenant.company_name == "Juniper & Vine Kitchens"
    assert tenant.industry_vertical == "restaurant"
    assert len(STORE.sites_for(tenant.tenant_id)) == 3
    sensors = STORE.sensors_for(tenant.tenant_id)
    assert len(sensors) == 20
    assert all(s.site_id for s in sensors)
    # A fridge is held to a fridge's limit, not a freezer's.
    fridge = STORE.get_sensor("HBR-PREP-1")
    assert fridge.bounds() == (41.0, 32.0)


def test_a_week_of_readings_mostly_in_range(demo):
    for sensor in STORE.sensors_for(demo["tenant_id"]):
        readings = STORE.readings_for(sensor.sensor_id)
        assert len(readings) == demo_account.DAYS * 48
        span = readings[-1].recorded_at - readings[0].recorded_at
        assert timedelta(days=6, hours=23) <= span <= timedelta(days=7)
        assert sensor.last_seen is not None and not sensor.offline(utc_now())
        if sensor.sensor_id not in (demo_account.HANDLED, demo_account.STILL_OPEN):
            assert not any(r.breached for r in readings), sensor.sensor_id


def test_one_breach_handled_and_one_problem_open(demo):
    incidents = STORE.incidents_for(demo["tenant_id"])
    assert len(incidents) == 2
    handled = next(i for i in incidents if i.sensor_id == demo_account.HANDLED)
    still = next(i for i in incidents if i.sensor_id == demo_account.STILL_OPEN)

    assert handled.resolved_at is not None
    assert handled.acknowledged_by.startswith("Priya Lund")
    assert handled.acknowledged_at - handled.opened_at == timedelta(minutes=7)
    assert "gasket" in handled.corrective_action
    assert utc_now() - handled.opened_at > timedelta(days=2)

    assert still.resolved_at is None and still.acknowledged_at is None
    assert STORE.get_sensor(demo_account.STILL_OPEN).last_temperature > 41.0


def test_an_on_call_rotation_with_the_first_person_on_now(demo):
    (rotation,) = oncall.rotations_for(STORE._db, demo["tenant_id"])
    assert len(rotation.members) == 3 and rotation.shift_days == 7
    assert rotation.on_shift(utc_now()) == rotation.members[0]


def test_a_signed_contract_and_a_paid_invoice(demo):
    sub = STORE.active_subscription(demo["tenant_id"])
    assert sub is not None and sub.periods_billed == 1
    (invoice,) = STORE.invoices_for(demo["tenant_id"])
    assert invoice.state == "paid" and invoice.balance_usd == 0
    assert demo["invoice_state"] == "paid"
    # Nothing more falls due on the first pass after it is served.
    from contracts import run_billing

    assert run_billing()["invoices_issued"] == 0


def test_the_owner_signs_in_and_the_console_has_it_all(api, demo):
    headers = _sign_in(api)
    overview = api.get("/api/console/overview", headers=headers).json()
    assert overview["tenant"]["company_name"] == "Juniper & Vine Kitchens"
    assert overview["summary"]["sensors_total"] == 20
    assert overview["summary"]["open_incidents"] == 1
    assert overview["account"]["severity"] == "ok"
    assert api.get("/api/autopilot/inspector-log", headers=headers).status_code == 200
    rota = api.get("/api/contacts/rotations", headers=headers).json()
    assert rota["rotations"][0]["on_shift_now"]["full_name"] == "Morgan Hale"


def test_a_password_is_made_when_none_is_given():
    made = demo_account.seed()
    assert len(made["owner_password"]) >= 12
    assert made["owner_password"] != made["operator_password"]


# ---- only made-up details --------------------------------------------------------


def test_every_address_is_on_the_reserved_domain_and_every_number_is_fictional(demo):
    rows = []
    for kind in ("tenant", "user", "contact", "site"):
        rows += [data for _, data in STORE._db.rows(kind)]
    text = str(rows)
    emails = set(re.findall(r"[\w.+-]+@[\w.-]+", text))
    assert emails and all(e.endswith(".example") for e in emails), emails
    phones = set(re.findall(r"\+1555\d{4}", text))
    assert phones and all(p[5:7] == "01" for p in phones), phones


def test_nothing_is_ever_mailed_to_the_demo(demo, mailbox):
    from mail import flush_queue
    from scheduler import run_money_pass

    run_money_pass()
    flush_queue()
    assert [m for m in mailbox if "juniper-vine.example" in m["To"]] == []


# ---- only into an empty database ------------------------------------------------


def test_a_database_with_a_business_in_it_is_refused(tenant_factory):
    tenant_factory(company_name="A Real Customer")
    before = STORE._db.rows("tenant")

    with pytest.raises(demo_account.RefusedDatabase) as refused:
        demo_account.seed(password=PASSWORD)

    assert "1 business" in str(refused.value)
    assert STORE._db.rows("tenant") == before
    assert STORE._db.get(*demo_account.DEMO_MARK) is None
    assert STORE.user_by_email(demo_account.OWNER[1]) is None


@pytest.mark.parametrize("kind", ["user", "invoice", "reading"])
def test_any_real_record_is_enough_to_refuse(kind):
    STORE._db.put(kind, "X-1", {"tenant_id": "TEN-OTHER"})
    with pytest.raises(demo_account.RefusedDatabase):
        demo_account.seed(password=PASSWORD)


def test_it_will_not_seed_twice(demo):
    with pytest.raises(demo_account.RefusedDatabase) as refused:
        demo_account.seed(password=PASSWORD)
    assert "the demo already" in str(refused.value)
    assert len(STORE.list_tenants()) == 1


# ---- the command, and where its backups go ------------------------------------------


def _run(args, env):
    full = {**os.environ, **env}
    full.pop("CYBERLOGIX_DB_PATH", None)
    full.update({k: v for k, v in env.items() if k == "CYBERLOGIX_DB_PATH"})
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=full,
                          capture_output=True, text=True, timeout=120)


def _snapshot(db, live, tmp_path):
    """Take a backup the way the daily pass does, for the database at `db`."""
    script = tmp_path / "take.py"
    script.write_text("import backup\nprint(backup.take(label='test'))\n")
    return _run([str(script)], {
        "CYBERLOGIX_DB_PATH": str(db), "CYBERLOGIX_BACKUP_DIR": str(live),
        "PYTHONPATH": str(ROOT), "CYBERLOGIX_SWEEP_SECONDS": "0"})


def test_the_command_seeds_a_new_file_and_prints_how_to_sign_in(tmp_path):
    db = tmp_path / "demo.db"
    done = _run(["demo_account.py", "--db", str(db), "--password", PASSWORD],
                {"CYBERLOGIX_BACKUP_DIR": str(tmp_path / "live")})
    assert done.returncode == 0, done.stderr
    assert "Juniper & Vine Kitchens" in done.stdout
    assert f"Owner:    {demo_account.OWNER[1]}  password: {PASSWORD}" in done.stdout
    assert "/console" in done.stdout
    assert db.exists()

    again = _run(["demo_account.py", "--db", str(db)], {})
    assert again.returncode == 2
    assert "Refused" in again.stderr and "not empty" in again.stderr


def test_the_command_refuses_a_live_database(tmp_path):
    db = tmp_path / "live.db"
    script = tmp_path / "make_live.py"
    script.write_text(
        "from store import STORE\n"
        "STORE.create_tenant(company_name='Live Customer', contact_name='A', "
        "contact_phone='+15550100', contact_email='a@live.example', plan='growth')\n")
    made = _run([str(script)], {"CYBERLOGIX_DB_PATH": str(db), "PYTHONPATH": str(ROOT)})
    assert made.returncode == 0, made.stderr

    refused = _run(["demo_account.py", "--db", str(db)], {})
    assert refused.returncode == 2
    assert "1 business" in refused.stderr


def test_a_demo_backs_up_beside_itself_never_into_the_live_folder(tmp_path):
    live = tmp_path / "live-backups"
    live.mkdir()
    (live / "cyberlogix-20261101T000000Z.sqlite3").write_text("a real snapshot")
    db = tmp_path / "demo" / "demo.db"
    db.parent.mkdir()
    seeded = _run(["demo_account.py", "--db", str(db), "--password", PASSWORD],
                  {"CYBERLOGIX_BACKUP_DIR": str(live)})
    assert seeded.returncode == 0, seeded.stderr

    taken = _snapshot(db, live, tmp_path)
    assert taken.returncode == 0, taken.stderr
    assert "'taken': True" in taken.stdout, taken.stdout
    assert sorted(p.name for p in live.iterdir()) == ["cyberlogix-20261101T000000Z.sqlite3"]
    assert list((db.parent / "demo-backups").glob("*.sqlite3"))


def test_a_live_database_still_backs_up_into_the_live_folder(tmp_path):
    """The control: the rule is about demo databases only."""
    live = tmp_path / "live-backups"
    db = tmp_path / "live.db"
    script = tmp_path / "make_live.py"
    script.write_text(
        "from store import STORE\n"
        "STORE.create_tenant(company_name='Live Customer', contact_name='A', "
        "contact_phone='+15550100', contact_email='a@live.example', plan='growth')\n")
    assert _run([str(script)], {"CYBERLOGIX_DB_PATH": str(db),
                                "PYTHONPATH": str(ROOT)}).returncode == 0
    taken = _snapshot(db, live, tmp_path)
    assert taken.returncode == 0, taken.stderr
    assert list(live.glob("*.sqlite3"))
    assert not (tmp_path / "demo-backups").exists()


def test_a_demo_whose_own_folder_is_the_live_one_takes_none(monkeypatch, tmp_path):
    import backup

    STORE._db.put(*demo_account.DEMO_MARK, {"seeded_at": "now"})
    db = tmp_path / "demo.db"
    monkeypatch.setattr(STORE._db, "path", str(db), raising=False)
    monkeypatch.setattr(backup, "BACKUP_DIR", str(tmp_path / "demo-backups"))
    assert backup._directory() is None
    monkeypatch.setattr(backup, "BACKUP_DIR", str(tmp_path / "elsewhere"))
    assert backup._directory() == (tmp_path / "demo-backups").resolve()


def test_the_seed_never_touches_the_repository():
    """Like the public demo builder, the command needs a path; no default
    can drop a database into the working directory."""
    source = (ROOT / "demo_account.py").read_text()
    assert '"--db", required=True' in source
