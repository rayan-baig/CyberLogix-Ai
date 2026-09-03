"""Shared Google GenAI client with fail-open generation.

Every AI-authored message in the suite is safety-critical: an alert that is
worded poorly still saves the asset, but an alert that never fires does not.
`safe_generate` therefore never raises. It returns the model's text when the
call succeeds and a caller-supplied deterministic fallback when it does not,
alongside the source so the API response can say which was used.
"""

from __future__ import annotations

import logging
import os
from typing import Tuple

from google import genai

logger = logging.getLogger("cyberlogix.gemini")

GEMINI_MODEL = os.environ.get("CYBERLOGIX_GEMINI_MODEL", "gemini-2.5-flash")

try:
    client = genai.Client()
    logger.info("Google GenAI client initialized (model=%s).", GEMINI_MODEL)
except Exception as exc:  # noqa: BLE001 - startup must never hard-fail
    client = None
    logger.warning(
        "Google GenAI client unavailable (%s). All generated copy will use "
        "deterministic fallback templates.",
        exc,
    )


def dispatch_ready() -> bool:
    """True when the Gemini client initialized successfully."""
    return client is not None


def safe_generate(prompt: str, fallback: str, purpose: str) -> Tuple[str, str]:
    """Generate text, degrading to `fallback` instead of raising.

    Returns a (text, source) pair where source is "gemini" or
    "fallback_template".
    """
    if client is None:
        logger.error(
            "Gemini unavailable for %s; using deterministic template. "
            "Verify the GEMINI_API_KEY environment variable.",
            purpose,
        )
        return fallback, "fallback_template"

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = (response.text or "").strip()
        if not text:
            raise ValueError("Gemini returned an empty body.")
        return text, "gemini"
    except Exception as exc:  # noqa: BLE001 - an alert must always go out
        logger.exception("Gemini %s failed (%s); falling back.", purpose, exc)
        return fallback, "fallback_template"
