"""CyberLogix on a subdomain of MTRX Horizyn's domain, beside its siblings.

MTRX Horizyn owns the one domain. CyberLogix, OmniArmor and RankUp are its
products, each on a subdomain of it, and the company's own site is on the
apex. Separate subdomains are separate origins, and that is the point: one
app's sign-in, storage and service worker never reach another.

Two things would quietly undo it. A cookie set with `Domain=` the parent
domain is sent to every sibling, so one app's sign-in would arrive at all of
them. A CORS policy that trusts the siblings (or everybody) lets a page on
one subdomain read what this API answers. Neither may happen by default.

And the company: the agreements, invoices and mail say who is behind the
product, which is MTRX Horizyn.
"""

import re
from pathlib import Path

import pytest

import invoicing
import legal
import mail
import main

ROOT = Path(__file__).resolve().parent.parent

# The parent domain and the siblings that share it, plus a stranger.
OTHER_ORIGINS = (
    "https://example.com",
    "https://www.example.com",
    "https://rankup.example.com",
    "https://omniarmor.example.com",
    "https://evil.example",
)

SIGNUP = {
    "company_name": "Blue Harbor Club",
    "full_name": "Dana Reyes",
    "email": "dana@blueharbor.example",
    "password": "correct-horse-battery",
    "contact_phone": "+15550100",
    "industry_vertical": "country_club",
}


# ---- cookies stay on this host ---------------------------------------------


def _set_cookies(response):
    return response.headers.get_list("set-cookie")


def test_signing_up_and_in_sets_no_cookie_another_subdomain_could_receive(api):
    """Sign-in travels as a bearer token, never as a parent-domain cookie."""
    seen = []
    for path in ("/", "/console", "/signup", "/legal", "/reset", "/book"):
        seen += _set_cookies(api.get(path))
    made = api.post("/api/signup", json=SIGNUP)
    assert made.status_code == 201, made.text
    seen += _set_cookies(made)
    signed_in = api.post(
        "/api/accounts/login",
        json={"email": SIGNUP["email"], "password": SIGNUP["password"]},
    )
    assert signed_in.status_code == 200, signed_in.text
    seen += _set_cookies(signed_in)

    for cookie in seen:
        assert not re.search(r";\s*domain\s*=", cookie, re.I), (
            f"{cookie!r} is scoped to a parent domain, so every sibling "
            "subdomain would receive it."
        )


def test_no_code_scopes_a_cookie_to_a_domain():
    """Whatever sets a cookie later, server-side or in a page, sets it host-only."""
    offenders = []
    for path in list(ROOT.glob("*.py")) + list((ROOT / "static").glob("*")):
        if not path.is_file() or path.suffix not in (".py", ".html", ".js"):
            continue
        text = path.read_text(errors="ignore")
        if re.search(r"set_cookie\([^)]*\bdomain\s*=", text, re.S) or re.search(
            r"cookie[^\n]*;\s*domain\s*=", text, re.I
        ):
            offenders.append(path.name)
    assert not offenders, f"cookies scoped to a domain in: {offenders}"


# ---- no sibling may read this API -------------------------------------------


def test_the_default_trusts_no_other_origin():
    assert "*" not in main._ALLOWED_ORIGINS
    for origin in OTHER_ORIGINS:
        assert origin not in main._ALLOWED_ORIGINS


@pytest.mark.parametrize("origin", OTHER_ORIGINS)
def test_a_sibling_page_cannot_read_a_response(api, origin):
    response = api.get("/api/health", headers={"Origin": origin})
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


@pytest.mark.parametrize("origin", OTHER_ORIGINS)
def test_a_sibling_page_is_refused_at_preflight(api, origin):
    response = api.options(
        "/api/accounts/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.headers.get("access-control-allow-origin") not in (origin, "*")


def test_readiness_counts_a_wildcard_as_wide_open(monkeypatch):
    import readiness

    monkeypatch.setattr(main, "_ALLOWED_ORIGINS", ["*"])
    check = next(c for c in readiness.checks() if c["key"] == "origins")
    assert check["state"] == readiness.WARNS

    monkeypatch.setattr(main, "_ALLOWED_ORIGINS", [])
    check = next(c for c in readiness.checks() if c["key"] == "origins")
    assert check["state"] == readiness.OK


def test_pages_still_refuse_to_be_framed_by_a_sibling(api):
    headers = api.get("/console").headers
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


# ---- who is behind it --------------------------------------------------------


def test_the_issuer_defaults_to_mtrx_horizyn(monkeypatch):
    monkeypatch.delitem(invoicing.ISSUER, "legal_name", raising=False)
    assert invoicing.issuer_block()["legal_name"] == "MTRX Horizyn"


def test_the_agreements_name_mtrx_horizyn():
    for text in (legal.terms_of_service(), legal.privacy_statement()):
        assert "CyberLogix is a product of MTRX Horizyn" in " ".join(text.split())


def test_the_registered_name_still_reaches_the_agreements(monkeypatch):
    monkeypatch.setitem(invoicing.ISSUER, "legal_name", "MTRX Horizyn Ltd")
    assert "MTRX Horizyn Ltd" in legal.terms_of_service()
    assert "MTRX Horizyn Ltd" in legal.privacy_statement()


@pytest.mark.parametrize("klass", ["transactional", "commercial"])
def test_every_mail_says_whose_product_it_is(mailbox, klass):
    message = mail.compose(
        to_address="ops@example.com", subject="Hello", body="Body.", klass=klass
    )
    assert "CyberLogix is a product of MTRX Horizyn." in message.get_body(preferencelist=("plain",)).get_content()


def test_the_public_pages_name_the_parent_company():
    for page in ("index.html", "signup.html", "legal.html"):
        text = (ROOT / "static" / page).read_text()
        assert "MTRX Horizyn" in text, page
        assert "Cybersalus" not in text, page


# The company was renamed: MTRX Horizyn, so it is not confused with other
# companies of the earlier name. Spelled out of pieces so this file does
# not itself carry the old spelling it is looking for.
OLD_NAME = re.compile("MTRX" + r"\s+(?:#\s*)?" + "Horiz" + "on", re.IGNORECASE)


def test_the_old_spelling_is_gone_from_everything_a_person_reads(api, mailbox):
    import subprocess

    seen = {}
    for path in ("/", "/signup", "/legal", "/console", "/partners", "/for",
                 "/for/restaurant", "/today", "/reset", "/book", "/offline"):
        seen[path] = api.get(path).text
    for slug in ("terms", "sla", "privacy", "aup"):
        resp = api.get(f"/api/legal/{slug}")
        if resp.status_code == 200:
            seen[f"/api/legal/{slug}"] = resp.text
    for name, text in (("terms", legal.terms_of_service()),
                       ("sla", legal.service_level()),
                       ("privacy", legal.privacy_statement()),
                       ("aup", legal.acceptable_use())):
        seen[f"legal.{name}"] = text
    seen["issuer"] = str(invoicing.issuer_block())
    seen["mail"] = mail.compose(to_address="ops@example.com", subject="Hi",
                                body="Body.", klass="commercial").get_body(preferencelist=("plain",)).get_content()
    # And every file in the repository a person might read.
    for name in subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).split():
        if name.endswith((".py", ".md", ".html", ".yaml", ".yml", ".example", ".txt", ".js", ".css")):
            try:
                seen[name] = (ROOT / name).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

    stale = sorted(where for where, text in seen.items() if OLD_NAME.search(text))
    assert not stale, f"the old spelling of the company is still in: {stale}"
    assert "MTRX Horizyn" in seen["/"] and "MTRX Horizyn" in seen["legal.terms"]


def test_the_rename_moved_the_agreements_to_a_new_version():
    """A changed name in the agreements is a changed agreement: everybody
    who accepted the old text is asked again."""
    major, minor = (int(x) for x in legal.TERMS_VERSION.split("."))
    assert (major, minor) >= (2, 3)
