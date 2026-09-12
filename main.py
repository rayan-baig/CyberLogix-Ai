"""CyberLogix AI Master Enterprise Hub.

Mounts every subsystem of the suite behind one FastAPI application:
universal IoT telemetry, corporate license control, the autonomous
compliance clerk, AI outbound voice escalation, and predictive breakdown
forecasting.
"""

import logging
import math
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles

# Import all modular system routers
from accounts import router as accounts_router
from assurance import router as assurance_router
from automation import router as autopilot_router
from benchmarks import router as benchmarks_router
from claims import router as claims_router
from console_api import router as console_router
from contracts import router as contracts_router
from enterprise_billing import router as enterprise_router
from contacts import router as contacts_router
from costs import router as costs_router
from forecaster import router as forecaster_router
from gemini import GEMINI_MODEL, dispatch_ready
from hardware_bridge import router as bridge_router
from invoicing import router as invoicing_router
from backup import router as backup_router
from conversion import router as conversion_router
from digest import router as digest_router
from legal import router as legal_router
from mail import router as mail_router
from mail import status as mail_status
from notifications import delivery_ready
from partners import router as partners_router
import scheduler
from licenses import router as license_router
from pricing import router as billing_router
from shortcuts import router as shortcuts_router
from signup import router as signup_router
from sites import router as sites_router
from store import INDUSTRY_PROFILES, PLAN_TIERS, iso, utc_now
from telemetry import router as telemetry_router
from vault import router as vault_router
from voice_dispatch import router as voice_router
from webhooks import router as webhooks_router

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
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(
    lifespan=lifespan,
    title="CyberLogix AI Master Enterprise Hub",
    description=(
        "Universal IoT Telemetry, License Control, Autonomous Operations, "
        "Voice Escalation, and Predictive Forecasting Suite."
    ),
    version="3.0.0",
)

# CYBERLOGIX_ALLOWED_ORIGINS accepts a comma-separated origin list in
# production; the permissive default keeps local development frictionless.
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CYBERLOGIX_ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    # Browsers reject credentialed requests against a wildcard origin, so
    # credentials switch on only once real origins are configured.
    allow_credentials=_ALLOWED_ORIGINS != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all microservice routers under the central app
app.include_router(license_router)
app.include_router(telemetry_router)
app.include_router(autopilot_router)
app.include_router(voice_router)
app.include_router(forecaster_router)
app.include_router(bridge_router)
app.include_router(console_router)
app.include_router(accounts_router)
app.include_router(costs_router)
app.include_router(contacts_router)
app.include_router(billing_router)
app.include_router(shortcuts_router)
app.include_router(sites_router)
app.include_router(webhooks_router)
app.include_router(vault_router)
app.include_router(claims_router)
app.include_router(assurance_router)
app.include_router(benchmarks_router)
app.include_router(partners_router)
app.include_router(invoicing_router)
app.include_router(enterprise_router)
app.include_router(contracts_router)
app.include_router(legal_router)
app.include_router(signup_router)
app.include_router(mail_router)
app.include_router(conversion_router)
app.include_router(digest_router)
app.include_router(backup_router)

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
    "loss_assurance",
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
]


STATIC_DIR = Path(__file__).parent / "static"
LANDING_HTML = STATIC_DIR / "index.html"
CONSOLE_HTML = STATIC_DIR / "console.html"
PARTNER_HTML = STATIC_DIR / "partner.html"
LEGAL_HTML = STATIC_DIR / "legal.html"
SIGNUP_HTML = STATIC_DIR / "signup.html"
BOOK_HTML = STATIC_DIR / "book.html"
RESET_HTML = STATIC_DIR / "reset.html"

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
    # 'unsafe-inline' is honest rather than aspirational: every page here
    # carries its own <script> and <style> block, and a policy written as
    # though they did not would be a policy that has to be switched off.
    # It still refuses script from any other origin, which is what stops
    # an injected <script src> from reaching anywhere useful.
    "script-src 'self' 'unsafe-inline'",
    f"style-src 'self' 'unsafe-inline' {_FONT_CSS}",
    f"font-src 'self' {_FONT_FILES}",
    "img-src 'self' data:",
    "connect-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "base-uri 'none'",
    "object-src 'none'",
])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Set the headers that a browser can enforce on our behalf."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Referrer-Policy", "strict-origin-when-cross-origin"
    )
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)

    # HSTS only over HTTPS. Sent on a plain-http response it is either
    # ignored or, worse, honoured by a browser that then cannot reach a
    # development server on localhost at all — a header that makes the
    # product unrunnable locally is one somebody deletes.
    if request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


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
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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


def _serve_page(path: Path, request: Request) -> HTMLResponse:
    """Read a public page, substitute the deployment's own URL, serve it."""
    source = _page_cache.get(str(path))
    if source is None:
        source = path.read_text(encoding="utf-8")
        _page_cache[str(path)] = source
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    return HTMLResponse(
        source.replace(_BASE_TOKEN, base)
              .replace(_THEME_TOKEN, _background_colour())
    )


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
    body = "\n".join([
        "User-agent: *",
        "Allow: /$",
        "Allow: /signup",
        "Allow: /legal",
        "Disallow: /console",
        "Disallow: /partners",
        "Disallow: /book",
        "Disallow: /api/",
        "",
        f"Sitemap: {base}/sitemap.xml",
        "",
    ])
    return PlainTextResponse(body)


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    """The three pages worth finding: what it is, how to buy, and the terms."""
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    pages = [("/", "1.0"), ("/signup", "0.9"), ("/legal", "0.4")]
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
    return _serve_page(LANDING_HTML, request)


@app.get("/console", include_in_schema=False)
def operations_console():
    """Serve the operations console to a browser."""
    return FileResponse(CONSOLE_HTML, media_type="text/html")


@app.get("/partners", include_in_schema=False)
def partner_portal():
    """Serve the reseller portal, which is a different principal entirely."""
    return FileResponse(PARTNER_HTML, media_type="text/html")


@app.get("/signup", include_in_schema=False)
def signup_page(request: Request):
    """Where a prospect becomes a customer.

    Until this existed every call to action on the landing page pointed at
    /console, which is a password field. Somebody who had read the whole
    page and wanted to buy had nowhere to go.
    """
    return _serve_page(SIGNUP_HTML, request)


@app.get("/book", include_in_schema=False)
def book_page():
    """The operator's own view of the whole customer book.

    Served to anyone; it renders nothing until the platform key is
    supplied, and every figure on it comes from an endpoint that checks
    that key. The page itself holds no secret.
    """
    return FileResponse(BOOK_HTML, media_type="text/html")


@app.get("/reset", include_in_schema=False)
def reset_page():
    """Where a password reset link lands.

    Its own page rather than a panel on the console, because the person
    following the link is by definition the person who cannot get past
    the console's sign-in form, and putting the way out behind the thing
    they are locked out of is how an account becomes unreachable forever.
    """
    return FileResponse(RESET_HTML, media_type="text/html")


@app.get("/legal", include_in_schema=False)
def legal_page():
    """The agreements, where a prospect can read them before signing.

    Terms nobody can find until after they have bought are a surprise, not
    terms — and in several jurisdictions an unenforceable one.
    """
    return FileResponse(LEGAL_HTML, media_type="text/html")


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
        "gemini_model": GEMINI_MODEL,
        "gemini_dispatch": "ready" if dispatch_ready() else "fallback_template",
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
