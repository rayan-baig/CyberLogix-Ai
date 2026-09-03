"""CyberLogix AI Master Enterprise Hub.

Mounts every subsystem of the suite behind one FastAPI application:
universal IoT telemetry, corporate license control, the autonomous
compliance clerk, AI outbound voice escalation, and predictive breakdown
forecasting.
"""

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Import all modular system routers
from automation import router as autopilot_router
from forecaster import router as forecaster_router
from gemini import GEMINI_MODEL, dispatch_ready
from licenses import router as license_router
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

MODULES_ACTIVE = [
    "universal_iot_telemetry",
    "corporate_license_management",
    "autonomous_compliance_clerk",
    "ai_outbound_voice_escalator",
    "predictive_breakdown_forecaster",
]


@app.get("/", status_code=200, tags=["Gateway"])
def root_gateway():
    return {
        "system": "CyberLogix AI Master Engine",
        "status": "fully_operational_stealth_mode",
        "version": app.version,
        "modules_active": MODULES_ACTIVE,
        "docs": "/docs",
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
        "timestamp": iso(utc_now()),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )
