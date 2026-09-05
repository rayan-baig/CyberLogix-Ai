"""CyberLogix AI Master Enterprise Hub.

Mounts every subsystem of the suite behind one FastAPI application:
universal IoT telemetry, corporate license control, the autonomous
compliance clerk, AI outbound voice escalation, and predictive breakdown
forecasting.
"""

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# Import all modular system routers
from accounts import router as accounts_router
from automation import router as autopilot_router
from console_api import router as console_router
from enterprise_billing import router as enterprise_router
from contacts import router as contacts_router
from costs import router as costs_router
from forecaster import router as forecaster_router
from gemini import GEMINI_MODEL, dispatch_ready
from hardware_bridge import router as bridge_router
from notifications import delivery_ready
from licenses import router as license_router
from pricing import router as billing_router
from shortcuts import router as shortcuts_router
from sites import router as sites_router
from store import INDUSTRY_PROFILES, PLAN_TIERS, iso, utc_now
from telemetry import router as telemetry_router
from voice_dispatch import router as voice_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

app = FastAPI(
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
app.include_router(enterprise_router)

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
    "enterprise_cluster_billing",
]


CONSOLE_HTML = Path(__file__).parent / "static" / "console.html"


@app.get("/", include_in_schema=False)
def operations_console():
    """Serve the operations console to a browser."""
    return FileResponse(CONSOLE_HTML, media_type="text/html")


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
        "timestamp": iso(utc_now()),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )
