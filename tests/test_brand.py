"""The names in one place (brand.py), and the old name kept working (3.34.0).

The product is Kryozyn (it was CyberLogix AI); the checklist app its
Kitchen Safety Bundle fills in is Aegizyn (it was OmniArmor); the company
is MTRX Horizyn until its new name is chosen. Three promises:

* Every page, email, text, agreement and Pilot answer takes the names
  from brand.py, and nothing else spells them, so the company's rename is
  a change to one default. A page template, a page script or a Python
  string that spells a name fails here.
* Nothing a person reads carries the old names.
* Everything set up against the old names keeps working: settings, the
  old request headers, and the old address.

Old names are spelled out of pieces so this file does not carry them.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import brand
import formerly
import legal
import mail
import main

ROOT = Path(__file__).resolve().parent.parent

OLD_PRODUCT = "Cyber" + "Logix"
OLD_CHECKLIST = "Omni" + "Armor"
OLD_HOST = "cyber" + "logix." + "mtrx" + "horizyn.com"
OLD_STAGING = "staging." + OLD_HOST


def _word(name: str) -> str:
    # A name as a word: not inside an identifier or a header (X-Name-Key).
    return r"(?<![A-Za-z-])" + re.escape(name) + r"(?![A-Za-z-])"


# What only brand.py may spell: today's names and the old ones.
NAMES = re.compile("|".join(
    [_word(brand.APP_NAME), _word(brand.APP_SHORT), _word(brand.CHECKLIST_APP),
     re.escape(brand.COMPANY_NAME).replace(r"\ ", r"\s+"), re.escape(brand.COMPANY_DOMAIN),
     _word(OLD_PRODUCT), _word(OLD_CHECKLIST)]))
# What nobody reads any more, anywhere.
OLD_NAMES = re.compile("|".join([OLD_PRODUCT, OLD_CHECKLIST]))

# brand.py holds the names; formerly.py names the old address it redirects.
NAME_HOLDERS = {"brand.py", "formerly.py"}


def _tracked(*suffixes):
    names = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).split()
    names += subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"],
                                     cwd=ROOT, text=True).split()
    return sorted({n for n in names if n.endswith(suffixes) and (ROOT / n).is_file()})


def _python_strings(path: Path):
    """Every string literal in a module except docstrings, with its line."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.lineno, node.value


def _without_comments(text: str, kind: str) -> str:
    """A script or stylesheet with its comments taken out (not its strings)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    if kind == "js":
        text = re.sub(r"(?m)^\s*//.*$", "", text)
        text = re.sub(r"(?m)(?<=[;,{}()\]\s])//\s.*$", "", text)
    return text


# ---- one place ---------------------------------------------------------------


def test_no_python_string_spells_a_name_outside_brand():
    offenders = []
    for name in _tracked(".py"):
        if name.startswith("tests/") or name in NAME_HOLDERS:
            continue
        for line, value in _python_strings(ROOT / name):
            if NAMES.search(value):
                offenders.append(f"{name}:{line}: {value[:60]!r}")
    assert not offenders, offenders


def test_no_page_template_or_script_spells_a_name():
    """Templates use %%APP_NAME%%, %%COMPANY_NAME%% and the rest; scripts
    read the names from the page (<meta name="brand">) or, for Pilot, from
    its script tag. The shared Pilot core is not this app's to edit."""
    offenders = {}
    for path in sorted((ROOT / "static").rglob("*")):
        rel = path.relative_to(ROOT / "static")
        if not path.is_file() or rel.parts[0] == "pilot":
            continue
        if path.suffix in (".html", ".webmanifest"):
            text = path.read_text(encoding="utf-8")
        elif path.suffix in (".js", ".css"):
            text = _without_comments(path.read_text(encoding="utf-8"), path.suffix[1:])
        else:
            continue
        found = sorted({m.group(0) for m in NAMES.finditer(text)})
        if found:
            offenders[str(rel)] = found
    assert not offenders, offenders


def test_changing_the_settings_changes_every_page_email_and_agreement(tmp_path):
    """The proof that it is one place: other names, no code change."""
    script = r"""
import json
from fastapi.testclient import TestClient
import main, legal, mail
c = TestClient(main.app)
out = {
  "home": c.get("/").text, "pricing": c.get("/pricing").text,
  "legal": legal.terms_of_service() + legal.privacy_statement(),
  "mail": mail.compose(to_address="a@example.com", subject="Hi", body="B.",
                       klass="commercial").as_string(),
  "manifest": c.get("/manifest.webmanifest").text,
  "pilot": main.pilot_tag(),
}
print(json.dumps(out))
"""
    env = dict(os.environ, CYBERLOGIX_COMPANY_NAME="Example Holdings",
               KRYOZYN_COMPANY_DOMAIN="example.org", CYBERLOGIX_APP_NAME="Coldwell",
               CYBERLOGIX_DB_PATH=str(tmp_path / "x.db"), CYBERLOGIX_SWEEP_SECONDS="0")
    done = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    for where, text in out.items():
        assert brand.COMPANY_NAME not in text, where
        assert not re.search(_word(brand.APP_NAME), text), where
    assert "Example Holdings" in out["home"] and "Coldwell" in out["home"]
    assert "Coldwell is a product of Example Holdings" in " ".join(out["legal"].split())
    assert "Coldwell is a product of Example Holdings." in out["mail"]
    assert '"short_name": "Coldwell"' in out["manifest"]
    assert 'data-app-short="Coldwell"' in out["pilot"]


def test_the_defaults():
    assert (brand.APP_NAME, brand.COMPANY_NAME, brand.CHECKLIST_APP) == (
        "Kryozyn", "MTRX Horizyn", "Aegizyn")
    assert brand.APP_URL == "https://kryozyn.mtrxhorizyn.com"
    assert brand.STAGING_HOST == "staging.kryozyn.mtrxhorizyn.com"
    assert brand.fill("<head></head>%%APP_NAME%%|%%COMPANY_NAME%%").endswith(
        "Kryozyn|MTRX Horizyn")
    assert 'name="brand"' in brand.fill("<head></head>")


# ---- nothing a person reads carries the old names ----------------------------


def _page_paths(api):
    from domain import INDUSTRY_PROFILES

    paths = ["/", "/console", "/today", "/signup", "/pricing", "/help",
             "/help/sensors", "/for", "/legal", "/partners", "/demo", "/book",
             "/reset", "/offline", "/developers", "/docs", "/redoc",
             "/insurance-partners", "/insurance-partners/dashboard",
             "/repair-partners", "/repair-partners/dashboard", "/verify",
             "/robots.txt", "/sitemap.xml", "/manifest.webmanifest", "/sw.js",
             "/openapi.json", "/api", "/api/health", "/api/sensor-guides/wizard",
             "/a-page-that-is-not-there"]
    for loc in re.findall(r"<loc>[^<]*?(/for/[^<]+)</loc>", api.get("/sitemap.xml").text):
        paths.append(loc)
    for vertical in INDUSTRY_PROFILES:
        paths += [f"/for/{vertical}", f"/for/{vertical}/sheet"]
    paths += [f"/api/legal/{slug}" for slug in ("terms", "sla", "privacy", "aup")]
    return sorted(set(paths))


def test_no_page_says_an_old_name(api):
    offenders = {}
    for path in _page_paths(api):
        resp = api.get(path)
        text = resp.text
        # The old product name in any case (CYBERLOGIX_ setting names
        # included: a page names the KRYOZYN_ ones); the old checklist name
        # as a word (its lower-case form is in internal ids such as
        # kitchen_safety_bundle_omniarmor).
        found = sorted({m.group(0) for m in re.finditer(OLD_PRODUCT, text, re.I)}
                       | {m.group(0) for m in re.finditer(OLD_CHECKLIST, text)})
        if found:
            offenders[path] = found
        assert "%%" not in re.sub(r"<script>.*?</script>", "", text, flags=re.S) or path.startswith(
            ("/openapi", "/docs", "/redoc", "/api")), (path, "an unfilled token")
    assert not offenders, offenders


def test_no_static_file_or_customer_document_says_an_old_name():
    offenders = []
    for path in sorted((ROOT / "static").rglob("*")):
        rel = path.relative_to(ROOT / "static")
        if path.is_file() and rel.parts[0] != "pilot" and path.suffix in (
                ".html", ".js", ".css", ".svg", ".webmanifest"):
            if OLD_NAMES.search(path.read_text(encoding="utf-8")):
                offenders.append(str(rel))
    for path in sorted((ROOT / "docs").glob("*")):
        if path.suffix in (".md", ".html") and OLD_NAMES.search(path.read_text(encoding="utf-8")):
            offenders.append(f"docs/{path.name}")
    assert not offenders, offenders


def test_the_agreements_and_mail_use_the_names(mailbox):
    for text in (legal.terms_of_service(), legal.service_level(),
                 legal.privacy_statement(), legal.acceptable_use()):
        assert not OLD_NAMES.search(text)
    assert "Kryozyn is a product of MTRX Horizyn" in " ".join(legal.terms_of_service().split())
    message = mail.compose(to_address="ops@example.com", subject="Hello",
                           body="Body.", klass="commercial")
    assert not OLD_NAMES.search(message.as_string())
    assert mail.MAIL_FROM_NAME == "Kryozyn"
    major, minor = (int(x) for x in legal.TERMS_VERSION.split("."))
    assert (major, minor) >= (2, 10), "a changed name is a new version of the agreements"


def test_the_old_names_stay_only_where_they_explain_the_rename():
    allowed = {"CHANGELOG.md", "README.md", "DEPLOY.md", ".env.example"}
    offenders = [name for name in _tracked(".md", ".yaml", ".yml", ".example")
                 if name not in allowed and OLD_NAMES.search((ROOT / name).read_text(encoding="utf-8"))]
    assert not offenders, offenders


def test_old_machine_labels_read_with_the_new_name():
    from claims import full_name
    from records import AuditEntry, person_name, renamed
    from store import utc_now

    assert renamed(OLD_PRODUCT + " (automatic)") == "Kryozyn (automatic)"
    assert person_name(OLD_PRODUCT + " (back to normal)") == "Kryozyn"
    assert full_name(OLD_PRODUCT + " (automatic)") == "Kryozyn (automatic)"
    assert person_name("Rosa Diaz <rosa@example.com>") == "Rosa"
    entry = AuditEntry(entry_id="a1", tenant_id="t1", actor=OLD_PRODUCT,
                       actor_role="system", action="x", detail="y", at=utc_now())
    assert entry.public()["actor"] == "Kryozyn"
    assert entry.to_row()["actor"] == OLD_PRODUCT, "the record is kept as written"


# ---- settings: the old names work, the new ones win --------------------------


def test_a_kryozyn_setting_fills_the_old_name_and_wins():
    env = {"CYBERLOGIX_ADMIN_KEY": "old", "KRYOZYN_ADMIN_KEY": "new",
           "KRYOZYN_NOINDEX": "1", "CYBERLOGIX_SWEEP_SECONDS": "60", "KRYOZYN_": "x"}
    written = formerly.apply_env_aliases(env)
    assert env["CYBERLOGIX_ADMIN_KEY"] == "new"
    assert env["CYBERLOGIX_NOINDEX"] == "1"
    assert env["CYBERLOGIX_SWEEP_SECONDS"] == "60"
    assert sorted(written) == ["CYBERLOGIX_ADMIN_KEY", "CYBERLOGIX_NOINDEX"]


def test_main_reads_the_new_names_before_any_other_module():
    source = (ROOT / "main.py").read_text()
    assert re.search(r"^(?:import|from) (\S+)", source, re.M).group(1) == "formerly"


def test_the_entrypoint_honours_the_new_names_too():
    entry = (ROOT / "docker-entrypoint.sh").read_text()
    assert "${KRYOZYN_DB_PATH:-${CYBERLOGIX_DB_PATH:-" in entry
    assert "${KRYOZYN_BACKUP_DIR:-${CYBERLOGIX_BACKUP_DIR:-" in entry


# ---- headers: the old names work, the new ones win ---------------------------

OLD_KEY_HEADER = "X-" + OLD_PRODUCT + "-Key"


def test_the_new_and_the_old_key_header_both_open_the_account(api, tenant_factory):
    headers, _ = tenant_factory()
    key = headers["X-Kryozyn-Key"]
    assert api.get("/api/console/overview", headers={"X-Kryozyn-Key": key}).status_code == 200
    assert api.get("/api/console/overview", headers={OLD_KEY_HEADER: key}).status_code == 200
    assert api.get("/api/console/overview").status_code == 401


def test_when_both_are_sent_the_new_header_wins(api, tenant_factory):
    headers, _ = tenant_factory()
    key = headers["X-Kryozyn-Key"]
    good = {"X-Kryozyn-Key": key, OLD_KEY_HEADER: "clx_not-a-real-key"}
    assert api.get("/api/console/overview", headers=good).status_code == 200
    bad = {"X-Kryozyn-Key": "clx_not-a-real-key", OLD_KEY_HEADER: key}
    assert api.get("/api/console/overview", headers=bad).status_code == 401


def test_an_old_admin_header_still_reaches_the_operator_routes(api):
    for name in ("X-" + OLD_PRODUCT + "-Admin", "X-Kryozyn-Admin"):
        assert api.get("/api/repair-partners", headers={name: "test-admin-key"}).status_code == 200
    assert api.get("/api/repair-partners").status_code in (401, 403)


def test_header_aliasing_keeps_every_other_header():
    out = dict(formerly.alias_headers([
        (b"host", b"x"), (b"x-cyberlogix-sensor-key", b"s1"),
        (b"x-kryozyn-key", b"k2"), (b"x-cyberlogix-key", b"k1")]))
    assert out[b"host"] == b"x"
    assert out[b"x-kryozyn-sensor-key"] == out[b"x-cyberlogix-sensor-key"] == b"s1"
    assert out[b"x-kryozyn-key"] == out[b"x-cyberlogix-key"] == b"k2"


def test_the_api_reference_documents_only_the_new_headers(api):
    schema = api.get("/openapi.json").text
    assert "x-kryozyn-key" in schema.lower()
    assert OLD_PRODUCT.lower() not in schema.lower()


# ---- the old address ----------------------------------------------------------


@pytest.mark.parametrize("old, new", [
    (OLD_HOST, "kryozyn.mtrxhorizyn.com"),
    (OLD_STAGING, "staging.kryozyn.mtrxhorizyn.com"),
])
def test_a_page_at_the_old_address_moves_to_the_same_page_at_the_new_one(api, old, new):
    for path in ("/", "/console", "/proof/abc123?print=1", "/for/restaurant"):
        resp = api.get(path, headers={"Host": old}, follow_redirects=False)
        assert resp.status_code == 301, (path, resp.status_code)
        assert resp.headers["location"] == f"https://{new}{path}"
    head = api.head("/pricing", headers={"Host": old.upper() + ":443"}, follow_redirects=False)
    assert head.status_code == 301 and head.headers["location"] == f"https://{new}/pricing"


def test_the_api_at_the_old_address_is_answered_where_it_arrives(api, tenant_factory):
    old = {"Host": OLD_HOST}
    assert api.get("/api/health", headers=old, follow_redirects=False).status_code == 200
    assert api.get("/api", headers=old, follow_redirects=False).status_code == 200
    headers, _ = tenant_factory()
    assert api.get("/api/console/overview", headers={**old, **headers},
                   follow_redirects=False).status_code == 200
    posted = api.post("/api/accounts/login", headers=old, follow_redirects=False,
                      json={"email": "nobody@example.com", "password": "wrong-password-1"})
    assert posted.status_code not in (301, 302, 307, 308), "a POST is never redirected"


def test_the_new_address_and_other_hosts_are_never_redirected(api):
    for host in ("kryozyn.mtrxhorizyn.com", "testserver", "localhost:8000",
                 "rankup.mtrxhorizyn.com"):
        assert api.get("/", headers={"Host": host}, follow_redirects=False).status_code == 200


def test_the_old_hosts_setting():
    assert formerly.parse_old_hosts(None) == {OLD_HOST: brand.APP_HOST,
                                              OLD_STAGING: brand.STAGING_HOST}
    assert formerly.parse_old_hosts("") == {}
    assert formerly.parse_old_hosts(" A.example. = b.example ,junk,c.example=c.example") == {
        "a.example": "b.example"}
    assert formerly.old_hosts({"KRYOZYN_OLD_HOSTS": ""}) == {}
    assert formerly.old_hosts({"CYBERLOGIX_OLD_HOSTS": "x.example=y.example"}) == {"x.example": "y.example"}


def test_the_app_is_wired_with_both_middlewares():
    kinds = [m.cls for m in main.app.user_middleware]
    assert formerly.HeaderAliases in kinds and formerly.OldHostRedirect in kinds


def test_the_connection_answers_at_its_old_address_too(api, tenant_factory, owner_headers):
    """The console calls /api/connections/aegizyn; an installed console
    that has not reloaded since the rename still calls the old address."""
    owner_headers = owner_headers(tenant_factory()[0])
    old = "/api/connections/" + "omni" + "armor"
    new = "/api/connections/aegizyn"
    assert api.get(new, headers=owner_headers).json() == api.get(old, headers=owner_headers).json()
    for method in ("put", "delete"):
        assert getattr(api, method)(old).status_code == 401
    assert api.post(old + "/test").status_code == 401
    schema = api.get("/openapi.json").json()["paths"]
    assert new in schema and old not in schema


def test_header_aliasing_keeps_the_scope_the_router_writes_to():
    """The headers are rewritten in the same scope, so what the router
    records there (the matched route) is seen by every layer outside."""
    import asyncio

    seen = {}

    async def inner(scope, receive, send):
        scope["route"] = "matched"

    scope = {"type": "http", "headers": [(b"x-cyberlogix-key", b"k")]}
    asyncio.run(formerly.HeaderAliases(inner)(scope, None, None))
    seen.update(scope)
    assert seen["route"] == "matched"
    assert (b"x-kryozyn-key", b"k") in seen["headers"]
