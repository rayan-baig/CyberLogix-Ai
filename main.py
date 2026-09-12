"""CyberLogix AI Master Enterprise Hub.

Mounts every subsystem of the suite behind one FastAPI application:
universal IoT telemetry, corporate license control, the autonomous
compliance clerk, AI outbound voice escalation, and predictive breakdown
forecasting.
"""

import logging
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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
from legal import router as legal_router
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
]


STATIC_DIR = Path(__file__).parent / "static"
LANDING_HTML = STATIC_DIR / "index.html"
CONSOLE_HTML = STATIC_DIR / "console.html"
PARTNER_HTML = STATIC_DIR / "partner.html"
LEGAL_HTML = STATIC_DIR / "legal.html"
SIGNUP_HTML = STATIC_DIR / "signup.html"

# The console and the partner portal render from one stylesheet, so it is
# served rather than inlined twice.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


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


@app.get("/", include_in_schema=False)
def landing_page():
    """The front door.

    This used to be the console, which meant everybody who arrived at the
    product — a prospect, a journalist, somebody following a link from an
    invoice — was shown a password field and nothing that said what the
    password was for. The console has moved to /console, where the people
    who have one will look for it.
    """
    return FileResponse(LANDING_HTML, media_type="text/html")


@app.get("/console", include_in_schema=False)
def operations_console():
    """Serve the operations console to a browser."""
    return FileResponse(CONSOLE_HTML, media_type="text/html")


@app.get("/partners", include_in_schema=False)
def partner_portal():
    """Serve the reseller portal, which is a different principal entirely."""
    return FileResponse(PARTNER_HTML, media_type="text/html")


@app.get("/signup", include_in_schema=False)
def signup_page():
    """Where a prospect becomes a customer.

    Until this existed every call to action on the landing page pointed at
    /console, which is a password field. Somebody who had read the whole
    page and wanted to buy had nowhere to go.
    """
    return FileResponse(SIGNUP_HTML, media_type="text/html")


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
