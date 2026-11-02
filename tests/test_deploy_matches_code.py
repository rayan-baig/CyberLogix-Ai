"""DEPLOY.md and render.yaml, step by step, against the code they describe.

test_deploy_runbook.py checks that each setting the runbook names is read
somewhere. This goes further: every route quoted is a real route (not just
a real first segment), the health check Render is told to use answers 200
on a fresh start, the disk is where the database is, the scheduler notes
say what the scheduler does, and the external-scheduler route really does
keep billing, backups and the mail queue running.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = (ROOT / "DEPLOY.md").read_text()
FLAT = " ".join(RUNBOOK.split())
BLUEPRINT = (ROOT / "render.yaml").read_text()
DOCKERFILE = (ROOT / "Dockerfile").read_text()
ENTRYPOINT = (ROOT / "docker-entrypoint.sh").read_text()
ENV_EXAMPLE = (ROOT / ".env.example").read_text()

def _routes():
    from main import app

    def walk(routes):
        for route in routes:
            inner = getattr(route, "original_router", None)
            if inner is not None:
                yield from walk(inner.routes)
            elif hasattr(route, "routes"):
                yield from walk(route.routes)
            else:
                for method in getattr(route, "methods", None) or ():
                    yield method, route.path

    return set(walk(app.routes))


def _matches(path, routes):
    for _, real in routes:
        pattern = "^" + re.sub(r"\{[^}]+\}", "[^/]+", real) + "$"
        if re.match(pattern, path):
            return True
    return False


def _env_values():
    """key -> value for the plain `- key: X / value: Y` pairs in render.yaml."""
    found = {}
    for key, rest in re.findall(r"- key: (\S+)\n((?:\s+\w+: .*\n)*)", BLUEPRINT):
        value = re.search(r"value: \"?([^\"\n]*)\"?", rest)
        found[key] = value.group(1) if value else None
    return found


def test_every_path_the_runbook_and_blueprint_quote_is_a_route():
    routes = _routes()
    quoted = set(re.findall(r"(/api/[a-z0-9/_.-]+[a-z0-9_])", RUNBOOK + BLUEPRINT))
    # The pages it sends people to: `/signup`, and "/book → Health".
    quoted |= set(re.findall(r"`(/[a-z]+)`", RUNBOOK))
    quoted |= set(re.findall(r"(/[a-z]+) →", RUNBOOK))
    assert {"/signup", "/book"} <= quoted
    missing = sorted(p for p in quoted if not _matches(p, routes))
    assert not missing, f"quoted but not served: {missing}"


def test_the_health_check_answers_on_a_fresh_start_and_is_not_the_watchdog(api):
    path = re.search(r"^\s+healthCheckPath: (\S+)$", BLUEPRINT, re.M).group(1)
    assert path == "/api/health"
    assert api.get(path).status_code == 200
    assert "/api/health" in FLAT and "never at `/api/watchdog`" in FLAT


def test_the_disk_is_where_the_database_and_backups_are():
    env = _env_values()
    mount = re.search(r"^\s+mountPath: (\S+)$", BLUEPRINT, re.M).group(1)
    assert env["CYBERLOGIX_DB_PATH"].startswith(mount + "/")
    assert env["CYBERLOGIX_BACKUP_DIR"].startswith(mount + "/")
    # The image's own default, and the volume the runbook mounts, agree.
    assert f"ENV CYBERLOGIX_DB_PATH={env['CYBERLOGIX_DB_PATH']}" in DOCKERFILE
    assert f":{mount} " in RUNBOOK or f":{mount}\n" in RUNBOOK
    assert f"CYBERLOGIX_DB_PATH={env['CYBERLOGIX_DB_PATH']}" in RUNBOOK
    # The backup directory the runbook sets is a volume it mounts, and the
    # entrypoint hands it to the app's user so a root-owned disk works.
    backup_dir = re.search(r"CYBERLOGIX_BACKUP_DIR=(\S+)", RUNBOOK).group(1)
    assert f":{backup_dir} " in RUNBOOK
    assert "CYBERLOGIX_BACKUP_DIR" in ENTRYPOINT


def test_every_setting_the_blueprint_asks_for_is_read_by_the_code():
    sources = "".join(p.read_text() for p in ROOT.glob("*.py"))
    names = set(_env_values())
    # Read by the Google SDK itself rather than by name in this code.
    read_elsewhere = {"GEMINI_API_KEY"}
    unread = sorted(n for n in names - read_elsewhere if n not in sources)
    assert not unread, f"render.yaml sets what nothing reads: {unread}"
    # And every one is documented for people not using Render.
    undocumented = sorted(n for n in names if n not in ENV_EXAMPLE)
    assert not undocumented, undocumented


def test_the_port_and_single_worker_agree():
    assert "PORT=8080" in DOCKERFILE and "EXPOSE 8080" in DOCKERFILE
    assert "--port ${PORT}" in DOCKERFILE and "--workers 1" in DOCKERFILE
    assert "-p 8080:8080" in RUNBOOK
    assert "numInstances: 1" in BLUEPRINT


def test_no_document_says_the_sweep_setting_makes_a_second_process_safe():
    """It does not: every process keeps its own copy of every record."""
    for name, text in (("DEPLOY.md", FLAT), ("render.yaml", BLUEPRINT),
                       ("Dockerfile", DOCKERFILE), (".env.example", ENV_EXAMPLE)):
        flat = " ".join(text.replace("#", " ").split()).lower()
        assert "to scale out, set cyberlogix_sweep_seconds=0" not in flat, name
        assert "scale out by setting cyberlogix_sweep_seconds=0" not in flat, name
        assert "or when running more than one replica" not in flat, name


def test_the_runbook_quotes_the_secrets_it_generates():
    generated = set(re.findall(r'print\("([A-Z_]+)=', RUNBOOK))
    assert generated == {"CYBERLOGIX_ADMIN_KEY", "CYBERLOGIX_MAIL_SECRET"}
    for name in generated:
        assert f"{name}=<generated above>" in RUNBOOK
        assert f"key: {name}\n        generateValue: true" in BLUEPRINT


def test_the_provisioning_key_is_described_the_same_way_everywhere():
    """Unset does not mean trials-only; .env.example used to say it did."""
    assert "There is no switch that makes a deployment trials-only" in FLAT
    for name, text in (("render.yaml", BLUEPRINT), (".env.example", ENV_EXAMPLE)):
        flat = " ".join(text.replace("#", " ").split())
        assert "no paid account can be created at all" not in flat, name
        assert "does NOT make" in flat, name


# ---- what the scheduler notes promise -------------------------------------


@pytest.fixture()
def external(monkeypatch):
    import scheduler

    monkeypatch.setattr(scheduler, "_interval", lambda: 0)
    yield scheduler
    if scheduler._money_thread is not None:
        scheduler._money_thread.join(10)


def test_the_external_scheduler_route_keeps_the_money_pass_running(
        api, admin_headers, external, monkeypatch):
    """With the loop off, sweep-all was the only thing anything called, and
    it never invoiced, backed up, sent a digest or flushed the mail queue."""
    ran = []
    monkeypatch.setattr(external, "run_money_pass",
                        lambda: (ran.append(1), external.watchdog.record_money_pass()))

    first = api.post("/api/autopilot/sweep-all", headers=admin_headers)
    assert first.status_code == 200, first.text
    assert first.json()["money_pass_started"] is True
    external._money_thread.join(10)
    assert ran == [1]

    # Every few minutes after that: not again until an hour has passed.
    again = api.post("/api/autopilot/sweep-all", headers=admin_headers).json()
    assert again["money_pass_started"] is False
    assert ran == [1]

    from datetime import timedelta

    import watchdog
    from store import STORE, iso, utc_now

    state = watchdog._read()
    state["last_money_pass_at"] = iso(utc_now() - timedelta(hours=1, minutes=1))
    STORE._db.put(watchdog._KIND, watchdog._ID, state)
    later = api.post("/api/autopilot/sweep-all", headers=admin_headers).json()
    assert later["money_pass_started"] is True
    external._money_thread.join(10)
    assert ran == [1, 1]


def test_with_the_loop_on_sweep_all_leaves_billing_to_the_loop(
        api, admin_headers, monkeypatch):
    import scheduler

    monkeypatch.setattr(scheduler, "_interval", lambda: 60)
    started = []
    monkeypatch.setattr(scheduler, "run_money_pass", lambda: started.append(1))
    body = api.post("/api/autopilot/sweep-all", headers=admin_headers).json()
    assert body["money_pass_started"] is False
    assert started == []


def test_the_external_scheduler_settings_the_runbook_names_are_honoured(monkeypatch):
    import watchdog

    assert "X-CyberLogix-Admin" in FLAT
    monkeypatch.setenv("CYBERLOGIX_EXTERNAL_SWEEP_SECONDS", "120")
    monkeypatch.setattr("scheduler._interval", lambda: 0)
    assert watchdog.stale_after_seconds() == max(
        120 * watchdog.STALE_AFTER_MULTIPLE, watchdog.STALE_FLOOR_SECONDS)
