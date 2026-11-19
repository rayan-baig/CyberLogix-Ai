"""CyberLogix AI Master Enterprise Hub.

Mounts every subsystem of the suite behind one FastAPI application:
universal IoT telemetry, corporate license control, the autonomous
compliance clerk, AI outbound voice escalation, and predictive breakdown
forecasting.
"""

import html
import json
import logging
import math
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.middleware.gzip import GZipMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

# Import all modular system routers
from accounts import router as accounts_router
from automation import router as autopilot_router
from benchmarks import router as benchmarks_router
from businesses import router as businesses_router
from erase import router as erase_router
from claims import router as claims_router
from console_api import router as console_router
from contracts import router as contracts_router
from enterprise_billing import router as enterprise_router
from contacts import router as contacts_router
from costs import router as costs_router
from forecaster import router as forecaster_router
from gemini import dispatch_ready, dispatch_status
from hardware_bridge import router as bridge_router
from helpdesk import router as help_router
from invoicing import router as invoicing_router
from backup import router as backup_router
from certificates import router as certificates_router
from disposition import router as disposition_router
from doorstep import router as doorstep_router
from industries import router as industries_router
from pollers import router as pollers_router
from people import router as people_router
from tasks import router as tasks_router
from faults import router as faults_router
from usage import router as usage_router
from firstday import router as firstday_router
from readiness import router as readiness_router
from conversion import router as conversion_router
from digest import router as digest_router
from monthly import router as monthly_router
from payments import router as payments_router
from books import router as books_router
from margin import router as margin_router
from inspector import router as inspector_router
from watchdog import public_router as checks_router
from watchdog import router as watchdog_router
import watchdog
from legal import router as legal_router
from mail import router as mail_router
from mail import status as mail_status
from notifications import delivery_ready
from partners import router as partners_router
from insurers import router as insurers_router
from repairs import business_router as repair_quotes_router
from repairs import router as repairs_router
from retention import router as retention_router
from founders import router as founders_router
from proofreports import router as proofreports_router
import scheduler
import usage
from licenses import router as license_router
from pricing import router as billing_router
from referrals import router as referrals_router
from omniarmor import router as omniarmor_router
from shortcuts import router as shortcuts_router
from signup import router as signup_router
from simulator import router as simulator_router
from demo import router as demo_router
from sites import router as sites_router
from store import INDUSTRY_PROFILES, PLAN_TIERS, iso, utc_now
from telemetry import router as telemetry_router
from vault import router as vault_router
from voice_dispatch import router as voice_router
from webhooks import router as webhooks_router
from version import __version__

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run the unattended watchdog for as long as the API is up.

    Escalation is the product's promise: a text nobody reads becomes a
    phone call. Something has to run the sweep for that to be true at 3am,
    and an in-process loop is the cheapest thing that can.
    """
    scheduler.start()
    # A thread of its own, so a sweep that hangs is noticed too: tells the
    # operator when readings, the alert job or a channel stops.
    watchdog.start_watching()
    try:
        yield
    finally:
        watchdog.stop_watching()
        await scheduler.stop()


app = FastAPI(
    lifespan=lifespan,
    title="CyberLogix AI Master Enterprise Hub",
    description=(
        "Universal IoT Telemetry, License Control, Autonomous Operations, "
        "Voice Escalation, and Predictive Forecasting Suite."
    ),
    # From the VERSION file, so the health check, the API docs and the
    # console's About card all report the release CHANGELOG.md describes.
    version=__version__,
    # Served by the two routes near the page routes below, so each carries
    # no inline script (see API_DOCS_POLICY).
    docs_url=None,
    redoc_url=None,
)

# CYBERLOGIX_ALLOWED_ORIGINS names, comma-separated and exactly, the other
# sites whose pages may call this API from a visitor's browser. Unset means
# none. The console, the sign-up, proof and legal pages are all served from
# this same origin, which needs no CORS at all, so nothing is lost.
#
# CyberLogix runs on a subdomain of MTRX Horizyn's domain, beside the other
# MTRX Horizyn products on their own subdomains. A wildcard default would
# let any of those siblings -- and every other site -- read what this API
# answers. Each subdomain is its own origin on purpose; keep it that way.
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CYBERLOGIX_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    # Browsers reject credentialed requests against a wildcard origin, so
    # credentials switch on only once real origins are configured -- and
    # not at all while there are none, so no response advertises them.
    allow_credentials=bool(_ALLOWED_ORIGINS) and "*" not in _ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The most any request body may be. The largest legitimate one is the
# public vault check at its 20,000-reading limit, about 3 MB; nothing else
# comes near. Without a ceiling, sign-up, sign-in and the sensor webhook,
# all open to anybody, read whatever arrived into memory before looking at
# a field of it.
MAX_REQUEST_BYTES = 6_000_000


class _BodyTooLarge(HTTPException):
    """An HTTPException, so a route reading its body answers 413, not 400."""

    def __init__(self, limit: int):
        super().__init__(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(f"That request is too large. The most this service "
                    f"accepts is {limit // 1_000_000} MB."))


class RequestSizeLimit:
    """Refuse a body over MAX_REQUEST_BYTES, whether or not it says its size.

    A declared Content-Length over the limit is refused before a byte is
    read. A body sent in chunks with no length is counted as it arrives and
    cut off at the limit.
    """

    def __init__(self, app, limit: int):
        self.app = app
        self.limit = limit

    async def _refuse(self, send) -> None:
        body = json.dumps({"detail": _BodyTooLarge(self.limit).detail}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers") or ():
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > self.limit:
                    await self._refuse(send)
                    return
        seen = 0
        started = False

        async def counted_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body") or b"")
                if seen > self.limit:
                    raise _BodyTooLarge(self.limit)
            return message

        async def watched_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, watched_send)
        except _BodyTooLarge:
            if not started:
                await self._refuse(send)


app.add_middleware(RequestSizeLimit, limit=MAX_REQUEST_BYTES)

# Text answers are compressed for any browser that asks. The console's own
# script, stylesheet and page were 388 KB on every first open, and each
# 20-second poll about 70 KB of JSON; on a phone in a basement kitchen that
# is the difference between a page that opens and one that does not.
# Level 6, not the default 9: nearly the same size for a fraction of the
# CPU, which matters on a one-worker server. Images are already compressed
# and are left alone.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)

# Mount all microservice routers under the central app
app.include_router(license_router)
app.include_router(telemetry_router)
app.include_router(autopilot_router)
app.include_router(voice_router)
app.include_router(forecaster_router)
app.include_router(bridge_router)
app.include_router(console_router)
app.include_router(accounts_router)
app.include_router(businesses_router)
app.include_router(erase_router)
app.include_router(costs_router)
app.include_router(firstday_router)
app.include_router(contacts_router)
app.include_router(billing_router)
app.include_router(shortcuts_router)
app.include_router(sites_router)
app.include_router(webhooks_router)
app.include_router(vault_router)
app.include_router(claims_router)
app.include_router(benchmarks_router)
app.include_router(partners_router)
app.include_router(insurers_router)
app.include_router(repairs_router)
app.include_router(repair_quotes_router)
app.include_router(retention_router)
app.include_router(founders_router)
app.include_router(proofreports_router)
app.include_router(invoicing_router)
app.include_router(enterprise_router)
app.include_router(contracts_router)
app.include_router(legal_router)
app.include_router(signup_router)
app.include_router(mail_router)
app.include_router(conversion_router)
app.include_router(digest_router)
app.include_router(monthly_router)
app.include_router(backup_router)
app.include_router(readiness_router)
app.include_router(faults_router)
app.include_router(usage_router)
app.include_router(certificates_router)
app.include_router(disposition_router)
app.include_router(doorstep_router)
app.include_router(industries_router)
app.include_router(pollers_router)
app.include_router(people_router)
app.include_router(tasks_router)
app.include_router(watchdog_router)
app.include_router(checks_router)
app.include_router(inspector_router)
app.include_router(payments_router)
app.include_router(referrals_router)
app.include_router(omniarmor_router)
app.include_router(margin_router)
app.include_router(books_router)
app.include_router(simulator_router)
app.include_router(demo_router)
app.include_router(help_router)

MODULES_ACTIVE = [
    "universal_iot_telemetry",
    "corporate_license_management",
    "autonomous_compliance_clerk",
    "ai_outbound_voice_escalator",
    "predictive_breakdown_forecaster",
    "byod_hardware_bridge",
    "sector_meeting_intelligence",
    "operations_console",
    "operator_accounts_and_audit",
    "spend_controls",
    "on_call_roster",
    "per_unit_billing",
    "sector_shortcuts",
    "site_management",
    "unattended_autopilot_scheduler",
    "outbound_alert_webhooks",
    "tamper_evident_compliance_vault",
    "insurance_claim_packets",
    "anonymised_sector_benchmarks",
    "reseller_channel",
    "invoicing",
    "enterprise_cluster_billing",
    "self_billing_contracts",
    "collections_and_dunning",
    "generated_legal_terms",
    "self_serve_signup",
    "revenue_worklist",
    "outbound_mail",
    "trial_conversion_sequence",
    "operator_digest_and_customer_reports",
    "verified_database_snapshots",
    "dead_mans_switch",
    "payment_reconciliation",
    "margin_analysis",
    "books_and_ledger_export",
    "starter_plan",
    "bring_a_friend_referrals",
    "kitchen_safety_bundle_omniarmor",
    "proof_of_monitoring_reports",
    "insurance_partners",
    "repair_partners",
    "pause_instead_of_cancel",
    "trade_pages",
]


STATIC_DIR = Path(__file__).parent / "static"
LANDING_HTML = STATIC_DIR / "index.html"
CONSOLE_HTML = STATIC_DIR / "console.html"
PARTNER_HTML = STATIC_DIR / "partner.html"
LEGAL_HTML = STATIC_DIR / "legal.html"
SIGNUP_HTML = STATIC_DIR / "signup.html"
DEMO_HTML = STATIC_DIR / "demo.html"
BOOK_HTML = STATIC_DIR / "book.html"
SERVICE_WORKER = STATIC_DIR / "sw.js"
WEB_MANIFEST = STATIC_DIR / "manifest.webmanifest"
OFFLINE_HTML = STATIC_DIR / "offline.html"
PROOF_HTML = STATIC_DIR / "proof.html"
PROOF_REPORT_HTML = STATIC_DIR / "proof-report.html"
VERIFY_HTML = STATIC_DIR / "verify.html"
INSURERS_HTML = STATIC_DIR / "insurance-partners.html"
INSURER_DASHBOARD_HTML = STATIC_DIR / "insurance-dashboard.html"
REPAIRS_HTML = STATIC_DIR / "repair-partners.html"
REPAIR_DASHBOARD_HTML = STATIC_DIR / "repair-dashboard.html"
TRADE_HTML = STATIC_DIR / "trade.html"
SIMPLE_HTML = STATIC_DIR / "simple.html"
INDUSTRY_HTML = STATIC_DIR / "industry.html"
RESET_HTML = STATIC_DIR / "reset.html"
PRICING_HTML = STATIC_DIR / "pricing.html"
HELP_HTML = STATIC_DIR / "help.html"
SENSORS_HTML = STATIC_DIR / "help-sensors.html"
SHEET_HTML = STATIC_DIR / "sheet.html"
DEVELOPERS_HTML = STATIC_DIR / "developers.html"
ERROR_HTML = STATIC_DIR / "error.html"
FAVICON = STATIC_DIR / "favicon.ico"

# The console and the partner portal render from one stylesheet, so it is
# served rather than inlined twice.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --- security headers ----------------------------------------------------
#
# None of these was set. Each closes something specific:
#
# * `frame-ancestors` and X-Frame-Options stop the console being loaded
#   invisibly inside somebody else's page — the attack where a customer
#   thinks they are clicking one thing and are clicking "suspend this
#   licence" on ours.
# * `Referrer-Policy` is the other half of the password-reset fix. The
#   reset page scrubs the token out of the address bar, and this stops it
#   reaching a third party in a Referer header before the scrub — a
#   one-time credential handed to whatever the page loads next.
# * `nosniff` stops a browser deciding for itself that an uploaded blob
#   or a JSON error is really HTML and running it.
# * `base-uri 'none'` stops an injected `<base>` tag repointing every
#   relative URL on the page, which turns one stray tag into a rewrite of
#   the whole application's fetches.
#
# The stylesheet pulls its typefaces from Google Fonts, so the font and
# style origins are named rather than pretending everything is same-origin
# — a policy that blocks the product's own assets gets removed the first
# time somebody notices, and then nothing is protecting anything.
_FONT_CSS = "https://fonts.googleapis.com"
_FONT_FILES = "https://fonts.gstatic.com"

CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    # No 'unsafe-inline', for script or style. Every page's script and
    # stylesheet is a file under /static (console.js, landing.css, ...), no
    # element carries an onclick or a style="..." attribute, and markup a
    # script builds uses classes (utilities.css) rather than style
    # attributes. So text that gets into a page -- a sensor name, a company
    # name, a note -- cannot run as script or restyle the page even if an
    # escape is missed somewhere: the browser refuses anything inline.
    # tests/test_csp.py holds every served page to that.
    "script-src 'self'",
    f"style-src 'self' {_FONT_CSS}",
    f"font-src 'self' {_FONT_FILES}",
    "img-src 'self' data:",
    "connect-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "object-src 'none'",
])


# The two API reference pages run third-party code from a CDN (Swagger UI
# and ReDoc), so they get a policy of their own that names that origin,
# and only they do. Their own setup is a file (static/api-docs.js), so
# script stays strict here too. Style does not: ReDoc writes its styles
# into <style> elements at run time (styled-components) and Swagger UI's
# bundle is not ours to change, so style-src keeps 'unsafe-inline' on
# these two pages only. A page with nothing of the customer's on it, and
# no script it does not name.
_DOCS_CDN = "https://cdn.jsdelivr.net"
API_DOCS_POLICY = "; ".join([
    "default-src 'self'",
    f"script-src 'self' {_DOCS_CDN}",
    f"style-src 'self' 'unsafe-inline' {_DOCS_CDN} {_FONT_CSS}",
    f"font-src 'self' {_FONT_FILES}",
    "img-src 'self' data:",
    "worker-src 'self' blob:",
    "connect-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "object-src 'none'",
])


# The pages a search engine may list. Everything else -- the console, the
# plain view, the partner portal, the book, the reset and offline pages,
# a shared proof link, the API and the demo simulator behind it -- is
# answered with `X-Robots-Tag: noindex`, and robots.txt asks crawlers to
# stay out as well. The sitemap lists exactly these (plus one page per
# industry), and a test holds the two together.
PUBLIC_PAGES = ("/", "/signup", "/pricing", "/help", "/help/sensors", "/for", "/legal",
                "/insurance-partners", "/repair-partners")

# A copy that is not the live product -- staging, a demo box -- asks to be
# left out of search results entirely: every page noindex, robots.txt
# closed, the sitemap empty. Otherwise a stranger searching for the
# product could find staging's sign-up page and start a trial on a copy
# that texts nobody (docs/STAGING.md).
NOINDEX = os.environ.get("CYBERLOGIX_NOINDEX", "").strip().lower() in ("1", "true", "yes")

# Paths whose errors are always JSON: the API and its documentation, and
# the static files a page loads. A browser that mistypes a page address
# gets the friendly page; a script calling the API gets what it can parse.
_MACHINE_PREFIXES = ("/api", "/docs", "/redoc", "/openapi.json", "/static")


def _indexable(path: str) -> bool:
    """A public page a search engine may list, or a file such pages use."""
    if NOINDEX:
        return False
    if path in PUBLIC_PAGES or path in ("/robots.txt", "/sitemap.xml", "/favicon.ico"):
        return True
    if path.startswith("/static/"):
        return True
    if path.startswith("/for/"):
        from industries import known
        from trades import known as trades

        return path[len("/for/"):] in set(known()) | set(trades())
    return False


def _apply_security_headers(response: Response, request: Request) -> Response:
    """The headers a browser enforces on our behalf, on any response.

    Called by the middleware below and, separately, by the handler for
    unhandled errors: Starlette answers those from outside the middleware
    stack, so a 500 would otherwise go out with none of them.
    """
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Referrer-Policy", "strict-origin-when-cross-origin"
    )
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    # A page is checked with the server every time it is opened. Without
    # this the browser kept /console fresh for a while on its own guess,
    # so an installed app with no network opened yesterday's console --
    # a sign-in form and a raw fetch error -- instead of the offline page
    # the service worker falls back to.
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Cache-Control", "no-cache")

    # HSTS only over HTTPS. Sent on a plain-http response it is either
    # ignored or, worse, honoured by a browser that then cannot reach a
    # development server on localhost at all — a header that makes the
    # product unrunnable locally is one somebody deletes.
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    # Private pages, and any error, are kept out of search results.
    if response.status_code >= 400 or not _indexable(request.url.path):
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    return response


def _cache_headers(response: Response, request: Request) -> Response:
    """How long a browser may keep a static file, and the 304 for a page.

    A /static/ file asked for by its versioned address never changes, so
    it is kept for a year. Without the version it is left as it always
    was: the browser's own judgement, checked with its ETag.
    """
    path = request.url.path
    if path.startswith("/static/") and response.status_code == 200:
        if request.query_params.get("v"):
            response.headers.setdefault("Cache-Control", STATIC_IMMUTABLE)
        return response
    etag = response.headers.get("etag")
    if (etag and response.status_code == 200 and request.method == "GET"
            and request.headers.get("if-none-match") == etag):
        kept = {k: v for k, v in response.headers.items()
                if k.lower() in ("etag", "cache-control", "vary")}
        return Response(status_code=304, headers=kept)
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Set the headers that a browser can enforce on our behalf."""
    response = await call_next(request)
    return _apply_security_headers(_cache_headers(response, request), request)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    """Write down anything that got all the way out, then answer cleanly.

    Without this the traceback went to stdout, and in a container stdout
    is gone at the next restart. So the sequence was: something breaks at
    2am, the process logs, the container restarts, and the only evidence
    is a customer asking why. Nothing in the product could answer.

    The response says nothing about the internals -- a stack trace handed
    to whoever triggered it is a gift to somebody probing the API -- but
    it carries the fault id, so a customer quoting it is quoting
    something that can be looked up.
    """
    import faults

    row = faults.record(
        f"{request.method} {request.url.path}", exc,
        # Parameter names and lengths, not values: some routes take a key in
        # the query (?k= on the bridge), and the fault list is kept for good.
        context="query=" + str({k: f"<{len(v)} chars>" for k, v in request.query_params.items()}),
    )
    fault_id = str(row.get("fault_id", ""))
    response: Response
    if _wants_page(request):
        try:
            response = _error_page(request, 500, fault_id)
        except Exception:  # pragma: no cover - the page itself failed to render
            logging.getLogger("cyberlogix").exception("error page failed")
            response = PlainTextResponse(
                "Something went wrong on our side. It has been recorded."
                + (f" Reference: {fault_id}" if fault_id else ""),
                status_code=500)
    else:
        response = JSONResponse(
            status_code=500,
            content={
                "detail": (
                    "Something went wrong on our side. It has been recorded."
                ),
                "fault_id": fault_id,
            },
        )
    # Mailed to CYBERLOGIX_ERRORS_EMAIL when that is set, after the answer
    # has gone, so the customer is not kept waiting on a mail host. The
    # route as written ("/api/x/{token}") rather than the address it was
    # called at, which can carry a key or an address in its path.
    if row and faults.errors_address():
        route = request.scope.get("route")
        where = getattr(route, "path", None) or request.url.path
        response.background = BackgroundTask(
            faults.mail_report, row, exc, request.method, where)
    # This handler runs outside the middleware stack, so the headers the
    # middleware would have added are added here.
    return _apply_security_headers(response, request)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    """A missing page gets a page; everything else answers as it always did.

    Only a 404, only for a browser asking for a page, and never under the
    API: a script calling /api/... must keep getting JSON it can parse.
    """
    if exc.status_code == 404 and _wants_page(request):
        return _error_page(request, 404)
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Return a clean 422 for a malformed body, including a non-finite one.

    FastAPI's default handler echoes the offending value back inside the
    error, and `json.dumps` refuses NaN and infinity — so a device sending
    a bare `NaN` or `1e400` token produced an unhandled 500 with a stack
    trace instead of a rejection it could act on. Both are exactly what a
    failing sensor emits, so the ingestion path had a crash reachable by
    the hardware it exists to listen to.

    The offending value is scrubbed to its text form rather than dropped:
    whoever is debugging the device needs to see what it sent.
    """
    def scrub(value: Any) -> Any:
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return repr(value)
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [scrub(v) for v in value]
        return value

    # jsonable_encoder first, which turns the exception objects Pydantic
    # attaches into strings; then scrub, because the encoder passes floats
    # through untouched and NaN is what json.dumps refuses.
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": scrub(jsonable_encoder(exc.errors()))},
    )


# --- public pages, with their own absolute URL in them -------------------
#
# A link preview needs absolute URLs: og:image and og:url are fetched by a
# scraper that has no page to resolve a relative path against, so a
# relative one is silently dropped and the card renders as a bare link.
# The deployment's own address is the one thing a static file cannot know,
# so it is substituted at serve time.
#
# The request's own base URL is used when PUBLIC_BASE_URL is unset, which
# means previews work on a laptop and on a preview deployment without
# anybody configuring anything — and the configured value still wins,
# because behind a proxy the request's idea of its own host is whatever
# the proxy passed on.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
_BASE_TOKEN = "%%BASE_URL%%"

# The browser-chrome colour is the one value a page cannot take from the
# stylesheet: `theme-color` is a meta tag and will not read a CSS
# variable. Rather than let two pages keep their own copy of the
# background — which is exactly the drift the shared stylesheet exists to
# prevent — it is read out of theme.css at startup and substituted in.
_THEME_TOKEN = "%%THEME_COLOR%%"
_page_cache: Dict[str, str] = {}


def _background_colour() -> str:
    """The --bg token from the one stylesheet that defines the palette."""
    css = (STATIC_DIR / "theme.css").read_text(encoding="utf-8")
    found = re.search(r"--bg:\s*(#[0-9A-Fa-f]{6})", css)
    if not found:  # pragma: no cover - the token has been there since day one
        raise RuntimeError(
            "theme.css no longer defines --bg, so no page can know what "
            "colour the browser chrome should be."
        )
    return found.group(1)


# Every /static/ script and stylesheet a page names gets ?v=<a hash of the file> added when
# the page is served, so a browser can keep the file for a year and still
# never run yesterday's script against today's page: a changed file is a
# new address. Asked for without ?v= (an old page, the service worker's
# shell list), a file is served as it always was.
#
# Scripts and stylesheets only: they are the weight, and the ones that must
# match the page. Icons and images keep their plain address, which an
# installed app's manifest and a link preview name too.
_STATIC_REF = re.compile(r'((?:src|href)=")(/static/[^"?#]+\.(?:js|css))(")')
_asset_tags: Dict[str, str] = {}
STATIC_IMMUTABLE = "public, max-age=31536000, immutable"


def _asset_tag(url_path: str) -> Optional[str]:
    tag = _asset_tags.get(url_path)
    if tag is None:
        target = (STATIC_DIR / url_path[len("/static/"):]).resolve()
        if STATIC_DIR.resolve() not in target.parents or not target.is_file():
            return None
        import hashlib

        tag = hashlib.sha256(target.read_bytes()).hexdigest()[:12]
        _asset_tags[url_path] = tag
    return tag


def versioned(page: str) -> str:
    """The page with ?v=<hash> on every /static/ file it names."""
    def tagged(found):
        tag = _asset_tag(found.group(2))
        if tag is None:
            return found.group(0)
        return f"{found.group(1)}{found.group(2)}?v={tag}{found.group(3)}"

    return _STATIC_REF.sub(tagged, page)


_html_files: Dict[str, tuple] = {}


def _html_file(path: Path) -> HTMLResponse:
    """A page served as it is on disk, with its assets versioned.

    Carries an ETag, so opening it again costs a 304 rather than the page.
    """
    cached = _html_files.get(str(path))
    if cached is None:
        import hashlib

        page = versioned(path.read_text(encoding="utf-8"))
        etag = '"' + hashlib.sha256(page.encode()).hexdigest()[:16] + '"'
        cached = (page, etag)
        _html_files[str(path)] = cached
    return HTMLResponse(cached[0], headers={"ETag": cached[1]})


def _serve_page(
    path: Path,
    request: Request,
    extra: Optional[Dict[str, str]] = None,
    markup: Optional[Dict[str, str]] = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Read a public page, substitute the deployment's own URL, serve it.

    `extra` values are text and are escaped. `markup` values are HTML that
    the caller has already built safely (helpdesk.py, the pricing tables)
    and go in as they are.
    """
    source = _page_cache.get(str(path))
    if source is None:
        source = versioned(path.read_text(encoding="utf-8"))
        _page_cache[str(path)] = source
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    page = (source.replace(_BASE_TOKEN, base)
                  .replace(_THEME_TOKEN, _background_colour()))
    for token, value in (markup or {}).items():
        page = page.replace(token, value)
    for token, value in (extra or {}).items():
        page = page.replace(token, html.escape(value))
    return HTMLResponse(page, status_code=status_code)


def _wants_page(request: Request) -> bool:
    """A browser asking for a page, rather than a script asking the API."""
    path = request.url.path
    for prefix in _MACHINE_PREFIXES:
        if path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "."):
            return False
    return "text/html" in request.headers.get("accept", "")


_ERROR_TEXT = {
    404: ("Page not found",
          "There is nothing at this address. The link may be old, or the "
          "address may have a typing mistake in it."),
    500: ("Something went wrong",
          "Something went wrong on our side and it has been recorded. "
          "Please try again in a minute. Your sensors are still being "
          "watched."),
}


def _error_page(request: Request, status_code: int, fault_id: str = "") -> HTMLResponse:
    """The friendly error page, in the site's own look.

    A 500 names a reference code that can be looked up in the fault list,
    and nothing else: never the exception, its message or a traceback.
    """
    title, message = _ERROR_TEXT[status_code]
    detail = ""
    if fault_id:
        detail = ('<p class="muted">If you contact us about this, quote '
                  f'<span class="fault-ref">{html.escape(fault_id)}</span>.</p>')
    return _serve_page(
        ERROR_HTML, request,
        extra={"%%STATUS%%": str(status_code), "%%TITLE%%": title,
               "%%MESSAGE%%": message},
        markup={"%%DETAIL%%": detail},
        status_code=status_code,
    )


def _landing_facts() -> Dict[str, str]:
    """Who it is for, what it costs and the trial, for the first screen.

    Substituted rather than written into the page, for the same reason the
    sector cards are fetched: a front page with its own copy of a price is
    a front page that will one day quote one the product does not charge.
    These come off the rate card and the plan tiers as they are now.
    """
    from helpdesk import talk_to_us
    from pricing import headline

    facts = headline()
    talk = talk_to_us()
    named = 4  # restaurants, pharmacies, labs and data centres, by name
    return {
        # Where a sector card priced by quote sends "Talk to us".
        "%%TALK_HREF%%": talk["href"],
        "%%TALK_ADDRESS%%": talk["address"],
        "%%FROM_PRICE%%": facts["from_label"],
        "%%FROM_UNIT%%": facts["from_unit"],
        "%%TRIAL_DAYS%%": str(facts["trial_days"]),
        "%%TRIAL_UNITS%%": str(facts["trial_units"]),
        "%%INDUSTRIES_MORE%%": str(max(0, facts["industries"] - named)),
    }


@app.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    """What a crawler may index, and where the map is.

    The console, the reseller portal and the operator's book are not
    secret — every one of them refuses to render without a credential —
    but they are not pages anybody should arrive at from a search result
    either. A password field is a bad first impression of a product, and
    it is what the front door used to be.
    """
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    if NOINDEX:
        return PlainTextResponse("User-agent: *\nDisallow: /\n")
    body = "\n".join([
        "User-agent: *",
        "Allow: /$",
        "Allow: /signup",
        "Allow: /pricing",
        "Allow: /help",
        "Allow: /for",
        "Allow: /legal",
        "Allow: /insurance-partners$",
        "Disallow: /insurance-partners/",
        "Allow: /repair-partners$",
        "Disallow: /repair-partners/",
        "Disallow: /console",
        "Disallow: /today",
        "Disallow: /partners",
        "Disallow: /book",
        "Disallow: /reset",
        "Disallow: /offline",
        "Disallow: /proof/",
        "Disallow: /proof-report/",
        "Disallow: /verify",
        "Disallow: /api/",
        "Disallow: /docs",
        "Disallow: /redoc",
        "Disallow: /openapi.json",
        "",
        f"Sitemap: {base}/sitemap.xml",
        "",
    ])
    return PlainTextResponse(body)


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    """The pages worth finding: what it is, how to buy, the terms -- and
    one page per industry, in that industry's own words.

    The industry pages were missing. They are the most findable thing in
    the product -- "walk-in freezer alarm for restaurants" is what a
    restaurant owner actually searches, and /for/restaurant answers it --
    and they were crawlable but absent from the map.
    """
    from industries import known
    from trades import known as trades

    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    pages = [("/", "1.0"), ("/pricing", "0.9"), ("/signup", "0.9"),
             ("/for", "0.8")]
    pages += [(f"/for/{vertical}", "0.8") for vertical in known()]
    # The shorter pages for more trades with cold stock (trades.py): a
    # florist searches for "flower cooler alarm", not for our verticals.
    pages += [(f"/for/{slug}", "0.7") for slug in trades()]
    pages += [("/help", "0.6"), ("/help/sensors", "0.6"),
              ("/insurance-partners", "0.5"), ("/repair-partners", "0.5"),
              ("/legal", "0.4")]
    if NOINDEX:
        pages = []
    entries = "".join(
        f"<url><loc>{base}{path}</loc><priority>{weight}</priority></url>"
        for path, weight in pages
    )
    return Response(
        content=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{entries}</urlset>"
        ),
        media_type="application/xml",
    )


@app.get("/", include_in_schema=False)
def landing_page(request: Request):
    """The front door.

    This used to be the console, which meant everybody who arrived at the
    product — a prospect, a journalist, somebody following a link from an
    invoice — was shown a password field and nothing that said what the
    password was for. The console has moved to /console, where the people
    who have one will look for it.
    """
    return _serve_page(LANDING_HTML, request, _landing_facts())


@app.get("/console", include_in_schema=False)
def operations_console():
    """Serve the operations console to a browser."""
    return _html_file(CONSOLE_HTML)


@app.get("/for", include_in_schema=False)
@app.get("/for/{vertical}", include_in_schema=False)
def industry_page(request: Request, vertical: str = ""):
    """What the product does, in one industry's own words.

    One template and twelve sets of words. A restaurant owner and an IVF
    clinic director both need exactly this product and neither will read
    the other's page: "walk-in" means nothing in a hangar, and "straw"
    means nothing in a kitchen.

    Public and unauthenticated on purpose. This is the front of the
    product, and a page that asks a stranger to sign in before it will
    say what the thing does is a page nobody reads.

    An industry with no page is a 404, so a mistyped address gets the
    friendly page rather than a template that never fills in.
    """
    from industries import known
    from trades import TRADES, render

    if vertical in TRADES:
        # A shorter page for a trade that is not a vertical of its own
        # (trades.py), written out on the server with its own title.
        page = render(vertical)
        return _serve_page(TRADE_HTML, request, {
            "%%TITLE%%": page["title"],
            "%%PAGE_PATH%%": f"/for/{vertical}",
            "%%OG_TITLE%%": page["title"],
            "%%OG_DESCRIPTION%%": page["description"],
        }, markup={"%%BODY%%": page["body"]})
    if vertical and vertical not in known():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No page for that industry.")
    if vertical:
        name = INDUSTRY_PROFILES[vertical]["name"]
        title = f"CyberLogix AI for {name}"
        description = (f"Temperature alarms for {name}: a text to everyone "
                       "on call the moment something goes out of range, and "
                       "the record afterwards.")
    else:
        title = "CyberLogix AI for your industry"
        description = ("Temperature alarms for every trade with something "
                       "that must stay cold: find yours.")
    return _serve_page(INDUSTRY_HTML, request, {
        "%%PAGE_PATH%%": f"/for/{vertical}" if vertical else "/for",
        "%%OG_TITLE%%": title,
        "%%OG_DESCRIPTION%%": description,
    })


@app.get("/for/{vertical}/sheet", include_in_schema=False)
def industry_sheet(request: Request, vertical: str):
    """One industry on one printed page: the problem, the fix, where the
    alarm is set, what an alert says, the prices and the trial.

    Rendered from salessheet.py, which reads every figure from the code
    that charges, alarms and alerts. Not listed for search engines: it is
    the industry page again, made for paper.
    """
    from salessheet import known_sheet, render_html, sheet_title

    if not known_sheet(vertical):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No sheet for that industry.")
    name = INDUSTRY_PROFILES[vertical]["name"]
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    return _serve_page(SHEET_HTML, request, {
        "%%TITLE%%": sheet_title(vertical),
        "%%DESCRIPTION%%": (f"Temperature alarms for {name} on one page: the "
                            "problem, the fix, the alarm limits, the alerts, "
                            "the prices and the free trial."),
        "%%PAGE_PATH%%": f"/for/{vertical}/sheet",
        "%%BACK_PATH%%": f"/for/{vertical}",
    }, markup={"%%SHEET%%": render_html(vertical, base, __version__)})


@app.get("/today", include_in_schema=False)
def plain_view():
    """The console, asked one question: is there anything I have to do.

    The operations console answers everything, and is written for
    somebody who already knows what "breaching", "escalation" and
    "estate" mean. The person who owns the restaurant does not. They
    have about four seconds and they are holding a phone in a kitchen.

    Same data, same session, same design -- fewer words, and none of
    them jargon. Not a cut-down edition to be upsold out of: a different
    question, asked by a different person on a different day.
    """
    return _html_file(SIMPLE_HTML)


# --- the installed app ------------------------------------------------------
#
# Installable rather than shipped through a store. The product is a web
# application either way, and going through the App Store or Play Store
# would add a 15-30% cut on every subscription and a review queue between
# a fix and the customer -- for a wrapper around the same pages.
#
# The worker is served from the root rather than /static because a
# service worker can only control the scope it is served from, and this
# one has to cover /console, /book and the shell together.


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(
        SERVICE_WORKER,
        media_type="text/javascript",
        headers={
            # A stale worker cannot be updated by the page that it is
            # serving, so this one file is never cached.
            "Cache-Control": "no-cache, max-age=0",
            "Service-Worker-Allowed": "/",
        },
    )


@app.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest():
    return FileResponse(WEB_MANIFEST, media_type="application/manifest+json")


@app.get("/proof/{token}", include_in_schema=False)
def public_proof(token: str):
    """A customer's cold-chain record, for anyone they hand the link to.

    No credential: the whole point is that a health inspector, an
    adjuster or a diner can check it without an account and without
    taking either party's word for it. The token is the only secret, and
    the owner can revoke it.
    """
    return _html_file(PROOF_HTML)


@app.get("/proof-report/{report_id}", include_in_schema=False)
def proof_report_page(report_id: str):
    """A proof of monitoring report, to read and print.

    The page holds nothing: it asks /api/proof-reports/{id} with the
    console's own sign-in, and only an owner of that business gets an
    answer.
    """
    return _html_file(PROOF_REPORT_HTML)


@app.get("/verify", include_in_schema=False)
def verify_entry(request: Request, code: str = ""):
    """Where somebody types the code printed on a report."""
    from proofreports import normalise_code, pretty_code

    typed = normalise_code(code)
    if typed:
        return RedirectResponse(f"/verify/{pretty_code(typed[:16])}", status_code=303)
    return _serve_page(VERIFY_HTML, request, markup={"%%RESULT%%": ""})


@app.get("/verify/{code}", include_in_schema=False)
def verify_page(request: Request, code: str):
    """Is this proof of monitoring report genuine and unchanged?

    Public and rate limited. Shows the business name and the headline
    numbers, never a reading, a person or a location's name.
    """
    from proofreports import render_verify_html, verify

    try:
        result = verify(code, request)
    except HTTPException as refused:
        if refused.status_code != 429:
            raise
        message = ('<div class="verdict v-unknown"><p class="verdict-word">Slow down'
                   f'</p><p>{html.escape(str(refused.detail))}</p></div>')
        page = _serve_page(VERIFY_HTML, request, markup={"%%RESULT%%": message},
                           status_code=429)
        page.headers.update(refused.headers or {})
        return page
    codes = {"genuine": 200, "unchecked": 200, "changed": 409,
             "revoked": 410, "unknown": 404}
    return _serve_page(VERIFY_HTML, request,
                       markup={"%%RESULT%%": render_verify_html(result, code)},
                       status_code=codes[result["state"]])


@app.get("/insurance-partners", include_in_schema=False)
def insurance_partners_page(request: Request):
    """For insurers and brokers: what we do, and the partner application."""
    from insurers import DEFAULT_SHARE_MONTHS, DEFAULT_SHARE_PERCENT
    from pricing import REFERRAL_TRIAL_DAYS

    return _serve_page(INSURERS_HTML, request, {
        "%%SHARE_PERCENT%%": f"{DEFAULT_SHARE_PERCENT:g}",
        "%%SHARE_MONTHS%%": str(DEFAULT_SHARE_MONTHS),
        "%%REFERRAL_DAYS%%": str(REFERRAL_TRIAL_DAYS),
    })


@app.get("/insurance-partners/dashboard", include_in_schema=False)
def insurance_partner_dashboard():
    """An approved partner's dashboard. Renders nothing without their key."""
    return _html_file(INSURER_DASHBOARD_HTML)


@app.get("/repair-partners", include_in_schema=False)
def repair_partners_page(request: Request):
    """For refrigeration technicians: how leads work, and the application."""
    from repairs import BAD_LEAD_DAYS, DEFAULT_LEAD_FEE_USD

    return _serve_page(REPAIRS_HTML, request, {
        "%%LEAD_FEE%%": f"${DEFAULT_LEAD_FEE_USD:,.0f}" if DEFAULT_LEAD_FEE_USD == int(
            DEFAULT_LEAD_FEE_USD) else f"${DEFAULT_LEAD_FEE_USD:,.2f}",
        "%%BAD_DAYS%%": str(BAD_LEAD_DAYS),
    })


@app.get("/repair-partners/dashboard", include_in_schema=False)
def repair_partner_dashboard():
    """An approved repair partner's dashboard. Renders nothing without their key."""
    return _html_file(REPAIR_DASHBOARD_HTML)


@app.get("/offline", include_in_schema=False)
def offline_page():
    """What the installed app shows with no network.

    Deliberately not a copy of the dashboard. Showing the last known
    temperatures to somebody who has no connection is how an app tells
    a restaurant its freezer is fine while the freezer is failing.
    """
    return _html_file(OFFLINE_HTML)


@app.get("/partners", include_in_schema=False)
def partner_portal():
    """Serve the reseller portal, which is a different principal entirely."""
    return _html_file(PARTNER_HTML)


@app.get("/signup", include_in_schema=False)
def signup_page(request: Request):
    """Where a prospect becomes a customer.

    Until this existed every call to action on the landing page pointed at
    /console, which is a password field. Somebody who had read the whole
    page and wanted to buy had nowhere to go.
    """
    return _serve_page(SIGNUP_HTML, request)


@app.get("/demo", include_in_schema=False)
def demo_page(request: Request):
    """See it live, with no account: a pretend kitchen (demo.py).

    The page is files and nothing else. It asks the server for one thing,
    the story at /api/demo/story, which is rate limited and keeps nothing.
    """
    from pricing import headline

    return _serve_page(DEMO_HTML, request,
                       {"%%TRIAL_DAYS%%": str(headline()["trial_days"])})


@app.get("/book", include_in_schema=False)
def book_page():
    """The operator's own view of the whole customer book.

    Served to anyone; it renders nothing until the platform key is
    supplied, and every figure on it comes from an endpoint that checks
    that key. The page itself holds no secret.
    """
    return _html_file(BOOK_HTML)


@app.get("/reset", include_in_schema=False)
def reset_page():
    """Where a password reset link lands.

    Its own page rather than a panel on the console, because the person
    following the link is by definition the person who cannot get past
    the console's sign-in form, and putting the way out behind the thing
    they are locked out of is how an account becomes unreachable forever.
    """
    return _html_file(RESET_HTML)


@app.get("/legal", include_in_schema=False)
def legal_page(request: Request):
    """The agreements, where a prospect can read them before signing.

    Terms nobody can find until after they have bought are a surprise, not
    terms — and in several jurisdictions an unenforceable one.
    """
    return _serve_page(LEGAL_HTML, request)


@app.get("/pricing", include_in_schema=False)
def pricing_page(request: Request):
    """Every rate, the plans, and a calculator, rendered from pricing.py.

    The calculator's numbers are the ones rendered here, read back out of
    the markup by static/pricing.js, so the page cannot quote a price the
    invoice will not charge.
    """
    return _serve_page(PRICING_HTML, request, _pricing_text(),
                       markup=_pricing_markup())


@app.get("/help", include_in_schema=False)
def help_page(request: Request):
    """The customer help page: the questions, and what to do when a sensor
    goes quiet. Answers are built from the constants the product runs on
    (helpdesk.py)."""
    from helpdesk import render_faq, render_offline

    usage.count("help_views")
    return _serve_page(HELP_HTML, request, markup={
        "%%FAQ%%": render_faq(),
        "%%OFFLINE_STEPS%%": render_offline(),
    })


@app.get("/help/sensors", include_in_schema=False)
def sensor_setup_page(request: Request):
    """Step-by-step setup for every way a sensor can report, from
    sensor_guides.py, with this deployment's own address in every step."""
    from sensor_guides import render_html

    usage.count("help_views")
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    return _serve_page(SENSORS_HTML, request, markup={"%%GUIDES%%": render_html(base)})


@app.get("/developers", include_in_schema=False)
def developer_reference(request: Request):
    """The API reference as one plain page, rendered here from the schema.

    /docs and /redoc need a CDN and a looser policy; this needs neither,
    so it works wherever the product does. Routes only the platform
    operator can call are counted, not listed (apiref.py).
    """
    from apiref import render

    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    markup = render(app.openapi(), base)
    count = markup.pop("%%ROUTE_COUNT%%")
    return _serve_page(DEVELOPERS_HTML, request,
                       {"%%VERSION%%": __version__, "%%ROUTE_COUNT%%": count},
                       markup=markup)


_SWAGGER_VERSION = "5"
_REDOC_VERSION = "2"


@app.get("/docs", include_in_schema=False)
def api_reference():
    """Interactive API docs (Swagger UI), with no inline script.

    FastAPI's own page configures Swagger UI in an inline <script>, which
    the policy refuses. The same configuration is static/api-docs.js.
    """
    page = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(app.title)} - API</title>"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<link rel=\"icon\" href=\"/favicon.ico\">"
        f"<link rel=\"stylesheet\" href=\"{_DOCS_CDN}/npm/swagger-ui-dist@"
        f"{_SWAGGER_VERSION}/swagger-ui.css\">"
        "</head><body><div id=\"swagger-ui\"></div>"
        f"<script src=\"{_DOCS_CDN}/npm/swagger-ui-dist@{_SWAGGER_VERSION}/"
        "swagger-ui-bundle.js\"></script>"
        "<script src=\"/static/api-docs.js\"></script>"
        "</body></html>"
    )
    return HTMLResponse(page, headers={"Content-Security-Policy": API_DOCS_POLICY})


@app.get("/redoc", include_in_schema=False)
def api_reference_redoc():
    """The same API reference, as ReDoc reads it. No script of ours at all."""
    page = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(app.title)} - API</title>"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<link rel=\"icon\" href=\"/favicon.ico\">"
        "</head><body><redoc spec-url=\"/openapi.json\"></redoc>"
        f"<script src=\"{_DOCS_CDN}/npm/redoc@{_REDOC_VERSION}/bundles/"
        "redoc.standalone.js\"></script>"
        "</body></html>"
    )
    return HTMLResponse(page, headers={"Content-Security-Policy": API_DOCS_POLICY})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Browsers and crawlers ask for this whatever the page says."""
    return FileResponse(FAVICON, media_type="image/x-icon")


def _pricing_text() -> Dict[str, str]:
    from pricing import (
        ANNUAL_ESCALATOR_PERCENT,
        CANCELLATION_RULE,
        CHAIN_DISCOUNT_TIERS,
        EXTRA_UNIT_MONTHLY_USD,
        PRICE_BOOK,
        UNITS_PER_LOCATION,
        YEARLY_MONTHS_CHARGED,
        YEARLY_MONTHS_COVERED,
        YEARLY_RENEWAL_REMINDER_DAYS,
        chain_example,
        headline,
        yearly_label,
        yearly_price,
    )

    from pricing import (REFERRAL_TRIAL_DAYS, bundle_includes_label,
                         bundle_price_label, starter_terms)

    facts = headline()
    starter = starter_terms()
    example = chain_example("restaurant")

    def money(amount: float) -> str:
        return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"

    first, top = CHAIN_DISCOUNT_TIERS[0], CHAIN_DISCOUNT_TIERS[-1]
    return {
        "%%STARTER_PRICE%%": starter["monthly_label"],
        "%%STARTER_RATE%%": repr(float(starter["monthly_usd"])),
        "%%STARTER_UNITS%%": str(starter["max_units"]),
        "%%BUNDLE_PRICE%%": bundle_price_label(),
        "%%BUNDLE_INCLUDES%%": bundle_includes_label(),
        "%%REFERRAL_DAYS%%": str(REFERRAL_TRIAL_DAYS),
        "%%TRIAL_DAYS%%": str(facts["trial_days"]),
        "%%TRIAL_UNITS%%": str(facts["trial_units"]),
        "%%YEARLY_LABEL%%": yearly_label(),
        "%%MONTHS_CHARGED%%": str(YEARLY_MONTHS_CHARGED),
        "%%MONTHS_COVERED%%": str(YEARLY_MONTHS_COVERED),
        "%%STARTER_YEARLY%%": money(yearly_price(starter["monthly_usd"])),
        "%%RESTAURANT_YEARLY%%": money(yearly_price(PRICE_BOOK["restaurant"]["monthly_usd"])),
        "%%CHAIN_DATA%%": ",".join(f"{start}:{off:g}" for start, off in CHAIN_DISCOUNT_TIERS),
        "%%CHAIN_FIRST%%": str(first[0]),
        "%%CHAIN_FIRST_OFF%%": f"{first[1]:g}",
        "%%CHAIN_TOP%%": str(top[0]),
        "%%CHAIN_TOP_OFF%%": f"{top[1]:g}",
        "%%CHAIN_EXAMPLE%%": (
            f"{example['locations']} restaurants: {money(example['list_usd'])} → "
            f"{money(example['each_usd'])} each, {money(example['monthly_usd'])} a "
            f"month, or {money(example['yearly_usd'])} a year"),
        "%%CANCELLATION%%": CANCELLATION_RULE,
        "%%REMINDER_DAYS%%": " and ".join(str(d) for d in YEARLY_RENEWAL_REMINDER_DAYS),
        "%%RESTAURANT_PRICE%%": f"${PRICE_BOOK['restaurant']['monthly_usd']:,.0f}",
        "%%PHARMACY_PRICE%%": f"${PRICE_BOOK['pharmacy']['monthly_usd']:,.0f}",
        "%%UNITS_PER_LOCATION%%": str(UNITS_PER_LOCATION),
        "%%EXTRA_UNIT_PRICE%%": f"${EXTRA_UNIT_MONTHLY_USD:,.0f}",
        "%%EXTRA_UNIT_RATE%%": repr(float(EXTRA_UNIT_MONTHLY_USD)),
        "%%ESCALATOR%%": f"{ANNUAL_ESCALATOR_PERCENT:g}",
    }


def _pricing_markup() -> Dict[str, str]:
    """The rate card, plans and extras as HTML, every value escaped.

    A trade priced by quote goes into the page with no figure anywhere in
    its markup (no data-rate either): the calculator says "custom" for it
    and offers the Talk to us link.
    """
    from helpdesk import talk_to_us_html
    from pricing import (EXTRA_UNIT_MONTHLY_USD, calculator_rates, chain_tiers,
                         headline, yearly_price)

    e = html.escape
    data = calculator_rates()
    cheapest = headline()["from_vertical"]
    talk = talk_to_us_html()

    def money(amount: float) -> str:
        return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"

    def option(r) -> str:
        price = ('data-custom="yes"' if r["custom"]
                 else f'data-rate="{r["monthly_usd"]!r}"')
        allowance = (f' data-included="{r["units_per_location"]}"'
                     if r["units_per_location"] else "")
        return (f'<option value="{e(r["vertical"])}" {price} '
                f'data-unit="{e(r["unit"])}" data-plural="{e(r["unit_plural"])}"'
                f'{allowance}{" selected" if r["vertical"] == cheapest else ""}>'
                f'{e(r["industry"])}{" (custom pricing)" if r["custom"] else ""}'
                '</option>')

    def unit_cell(r) -> str:
        if r["units_per_location"]:
            return (f'{e(r["unit"])}, up to {r["units_per_location"]} units; '
                    f'{money(EXTRA_UNIT_MONTHLY_USD)} a month for each unit past '
                    f'{r["units_per_location"]}')
        return e(r["unit"])

    def price_cell(r) -> str:
        if r["custom"]:
            return (f'<td class="num custom-price" colspan="2">'
                    f'{e(data["custom_label"])}</td>')
        return (f'<td class="num">{money(r["monthly_usd"])}</td>'
                f'<td class="num">{money(yearly_price(r["monthly_usd"]))}</td>')

    options = "".join(option(r) for r in data["rates"])
    custom_attr = ' data-custom="yes"'
    rate_rows = "".join(
        f'<tr data-vertical="{e(r["vertical"])}"'
        f'{custom_attr if r["custom"] else ""}>'
        f'<td>{e(r["industry"])}</td><td>{unit_cell(r)}</td>{price_cell(r)}</tr>'
        for r in data["rates"])
    def per(a) -> str:
        return " per location" if a["basis"] == "per location" else ""

    addon_boxes = "".join(
        f'<label><input type="checkbox" class="calc-addon" value="{e(a["key"])}" '
        f'data-rate="{a["monthly_usd"]!r}" data-name="{e(a["name"])}" '
        f'data-basis="{e(a["basis"])}" data-starter="{"yes" if a["on_starter"] else "no"}"> '
        f'{e(a["name"])} · {money(a["monthly_usd"])} a month{per(a)}'
        f'{" (free on the trial)" if a["included_in_trial"] else ""}</label>'
        for a in data["add_ons"])
    addon_rows = "".join(
        f'<tr data-add-on="{e(a["key"])}"><td>{e(a["name"])}</td>'
        f'<td>{e(a["description"])}'
        f'{"" if a["on_starter"] else " Not on Starter."}</td>'
        f'<td class="num">{money(a["monthly_usd"])}{per(a)}</td></tr>'
        for a in data["add_ons"])
    starter = data["starter"]
    starter_points = "".join(f"<li>{e(item)}</li>" for item in starter["includes"])

    def term(days: int) -> str:
        if days % 365 == 0:
            years = days // 365
            return f"{years} year" + ("" if years == 1 else "s")
        return f"{days} days, free"

    def plan_price(key: str) -> str:
        if key == "trial":
            return "Free"
        if key == starter["plan"]:
            return (f"{money(starter['monthly_usd'])} a month, or "
                    f"{money(yearly_price(starter['monthly_usd']))} a year; "
                    "one location")
        return "The rate card"

    # The chain discount table: each tier, what a restaurant and a
    # pharmacy cost there each month, and paid yearly.
    restaurant, pharmacy = chain_tiers("restaurant"), chain_tiers("pharmacy")

    def span(row) -> str:
        return (f'{row["from"]}+' if row["to"] is None
                else f'{row["from"]}&ndash;{row["to"]}')

    def off(row) -> str:
        return f'{row["percent"]:g}% off' if row["percent"] else "List price"

    chain_rows = "".join(
        f'<tr data-chain-from="{r["from"]}"><td>{span(r)}</td>'
        f'<td class="num">{off(r)}</td>'
        f'<td class="num">{money(r["each_usd"])}'
        f'<span class="per-year">{money(r["each_yearly_usd"])} a&nbsp;year</span></td>'
        f'<td class="num">{money(ph["each_usd"])}'
        f'<span class="per-year">{money(ph["each_yearly_usd"])} a&nbsp;year</span></td></tr>'
        for r, ph in zip(restaurant, pharmacy))

    plan_rows = "".join(
        f'<tr><td>{e(t["name"])}</td><td class="num">{t["max_sensors"]:,}</td>'
        f'<td>{"Yes" if t["voice_escalation"] else "No, texts only"}</td>'
        f'<td>{e(term(int(t["term_days"])))}</td>'
        f'<td>{e(plan_price(key))}</td></tr>'
        for key, t in PLAN_TIERS.items())
    return {
        "%%RATE_OPTIONS%%": options,
        "%%RATE_ROWS%%": rate_rows,
        "%%ADDON_CHECKBOXES%%": addon_boxes,
        "%%ADDON_ROWS%%": addon_rows,
        "%%PLAN_ROWS%%": plan_rows,
        "%%CHAIN_ROWS%%": chain_rows,
        "%%STARTER_POINTS%%": starter_points,
        "%%TALK_LINK%%": talk,
        "%%CUSTOM_LABEL%%": e(data["custom_label"]),
    }


@app.get("/api", status_code=200, tags=["Gateway"])
def root_gateway():
    return {
        "system": "CyberLogix AI Master Engine",
        "status": "fully_operational_stealth_mode",
        "version": app.version,
        "modules_active": MODULES_ACTIVE,
        "docs": "/docs",
        "console": "/",
    }


@app.get("/api/stats", include_in_schema=False)
def usage_stats(request: Request):
    """Counts for the owner's MTRX Horizyn console, and nothing else.

    How many people have a sign-in, and the last 30 days of daily totals
    of what they do (usage.py): numbers only, never who. Off unless
    STATS_KEY is set (the same value as the console's MTRX_STATS_KEY),
    and answered only to that key.
    """
    import hmac as _hmac

    from store import STORE

    key = os.environ.get("STATS_KEY", "").strip()
    if not key:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    sent = request.headers.get("authorization", "")
    if not _hmac.compare_digest(sent.encode(), f"Bearer {key}".encode()):
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    # The daily totals of what people do (usage.py): numbers per day,
    # nothing about who.
    return {**STORE.usage_counts(), "usage": usage.last_days(30)}


def public_ai_status() -> dict:
    """The model layer's state, with nothing that names a model."""
    full = dispatch_status()
    return {key: full[key] for key in
            ("configured", "degraded", "consecutive_failures", "last_success_at")}


@app.get("/api/health", status_code=200, tags=["Gateway"])
def health_check():
    """Liveness probe covering every mounted subsystem."""
    return {
        "status": "online",
        "engine": "CyberLogix Universal Common Catastrophe IoT Engine",
        "version": app.version,
        "modules_active": len(MODULES_ACTIVE),
        "active_profiles": len(INDUSTRY_PROFILES),
        "plan_tiers": list(PLAN_TIERS),
        "ai_dispatch": "ready" if dispatch_ready() else "fallback_template",
        # The line above says a client was constructed. This one says
        # whether calls through it are working, which is a different
        # question once a named model is retired.
        #
        # Which model is not said here. This answer is public -- anyone
        # can ask for it, and the public demo once carried it to the
        # world -- so it gives the state and never a model's name or id,
        # nor the last error, which can carry one. The operator's
        # readiness check (readiness.py, platform key only) names the model.
        "ai": public_ai_status(),
        "message_delivery": "twilio" if delivery_ready() else "dry_run",
        "email_delivery": "smtp" if mail_status()["configured"] else "queued",
        "autopilot_scheduler": scheduler.status(),
        "timestamp": iso(utc_now()),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )
