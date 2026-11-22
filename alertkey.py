"""A business's own AI key, for the one line of an alert the AI words.

Every alert text and alert call has one line saying what to check first
("Check the door and the food now."). The model writes that line when it
can, and the template writes it when it cannot (gemini.safe_generate).
Normally the service's own key pays for it. A business may save its own
Google AI Studio key under Setup, "Your AI key for alert wording", and then
that line is written on its key instead, for its alerts only.

The rules this file keeps:

* The key is encrypted at rest with AES-GCM under a key derived from
  CYBERLOGIX_KEY_ENCRYPTION_SECRET, and bound to its business (the
  business's id is the associated data), so no other row can open it.
  Unset, nothing can be saved, and the console says why.
* It is never shown again: only its last four characters.
* It is never logged. A failure is logged by its kind only, with the key
  scrubbed out of anything the provider said.
* It is used only for that business's alert lines, and it can be removed.
* A call on it gets the same deadline as every other alert line
  (CYBERLOGIX_AI_TIMEOUT_SECONDS, 6 s by default). If it fails, the alert
  goes out with the template line, on time. It never falls back to the
  service's own key: the business asked for its own.

None of this touches the alarm path's timing: an alert is composed and
sent whether or not any AI answers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import threading
from types import SimpleNamespace
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from accounts import require_role, tenant_of
from auth import write_audit
from store import STORE, Tenant, User, iso, utc_now

logger = logging.getLogger("cyberlogix.alertkey")

router = APIRouter(prefix="/api/ai-key", tags=["AI key for alert wording"])

KIND = "alert_ai_key"
SETTING = "CYBERLOGIX_KEY_ENCRYPTION_SECRET"
PROVIDER = "Google AI Studio"

# What a pasted key may look like: one word of the characters API keys use.
# Not a particular provider's prefix, which can change.
KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]{20,200}$")


def _secret() -> str:
    # Read when used, so a test (or an operator restarting with it set) is
    # not stuck with what the process saw at import.
    return os.environ.get(SETTING, "").strip()


def configured() -> bool:
    """Whether this server can keep a key safely."""
    return bool(_secret())


def why_not() -> str:
    # Said to a customer: what to do, not the name of a server setting
    # (3.26.0). The setting is in docs/PILOT.md and the operator's log.
    return ("Not switched on for this server yet. "
            "Ask your provider to switch this on.")


def _cipher():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    secret = _secret()
    if not secret:
        raise RuntimeError(f"{SETTING} is not set.")
    # HKDF-SHA256 with a fixed salt and a label of its own, so this key is
    # never the same as one derived from the same secret for anything else.
    prk = hmac.new(b"cyberlogix-key-salt-v1", secret.encode("utf-8"), hashlib.sha256).digest()
    okm = hmac.new(prk, b"cyberlogix alert ai key v1\x01", hashlib.sha256).digest()
    return AESGCM(okm)


def seal(tenant_id: str, key: str) -> str:
    """The key encrypted, bound to its business."""
    nonce = os.urandom(12)
    sealed = _cipher().encrypt(nonce, key.encode("utf-8"), tenant_id.encode("utf-8"))
    return "v1." + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")


def unseal(tenant_id: str, token: str) -> str:
    if not token.startswith("v1."):
        raise ValueError("Not a sealed key.")
    raw = base64.urlsafe_b64decode(token[3:].encode("ascii"))
    return _cipher().decrypt(raw[:12], raw[12:], tenant_id.encode("utf-8")).decode("utf-8")


def hint(key: str) -> str:
    """All anybody is ever shown again: the last four characters."""
    return key[-4:]


def scrub(text: str, key: Optional[str]) -> str:
    """Anything a provider said, with the key taken out of it."""
    out = str(text or "")
    if key:
        out = out.replace(key, "[key]")
    return out[:200]


def saved(tenant_id: str) -> Optional[Dict[str, Any]]:
    return STORE._db.get(KIND, tenant_id)


def key_for(tenant_id: Optional[str]) -> Optional[str]:
    """The business's own key, or None. Never raises: this is on the
    alarm path, and an alert goes out whatever happens here."""
    if not tenant_id:
        return None
    try:
        row = saved(tenant_id)
        if not row or not row.get("key_ciphertext") or not configured():
            return None
        return unseal(tenant_id, row["key_ciphertext"])
    except Exception as exc:  # noqa: BLE001 - a broken key never stops an alert
        # The kind of failure only: a wrong secret makes every saved key
        # unreadable, and that is worth knowing. Never the key or its text.
        logger.warning("A saved AI key for %s could not be opened (%s); "
                       "its alerts use the template line.", tenant_id, type(exc).__name__)
        return None


# How the last line on each business's key went, kept in memory only. It is
# for the console card ("worked" or "did not work, plain wording used"),
# not a record: nothing on the alarm path waits on a database write for it.
_last: Dict[str, Dict[str, Any]] = {}
_last_lock = threading.Lock()


def note(tenant_id: str, worked: bool, why: str = "") -> None:
    with _last_lock:
        _last[tenant_id] = {"worked": worked, "at": iso(utc_now()), "why": why[:120]}


def last_result(tenant_id: str) -> Optional[Dict[str, Any]]:
    with _last_lock:
        found = _last.get(tenant_id)
        return dict(found) if found else None


def forget(tenant_id: str) -> None:
    with _last_lock:
        _last.pop(tenant_id, None)


def view(tenant: Tenant) -> Dict[str, Any]:
    """The card: whether it can be used here, and what is saved. Never the key."""
    from gemini import GEMINI_MODEL

    row = saved(tenant.tenant_id) if configured() else None
    has = bool(row and row.get("key_ciphertext"))
    return {
        "available": configured(),
        "why_not": "" if configured() else why_not(),
        "provider": PROVIDER,
        "saved": has,
        "key_hint": row.get("key_hint") if has and row else None,
        "saved_at": row.get("saved_at") if has and row else None,
        # The line is written by the model this service is set to use. With
        # none named, every line is the template, with or without a key.
        "model_set": bool(GEMINI_MODEL),
        "last_result": last_result(tenant.tenant_id) if has else None,
        "note": ("Used only for the one line in your alerts that says what to "
                 "check first. Your provider bills your key for it. If a call "
                 "fails or is slow, the alert goes out on time with the plain "
                 "wording instead."),
    }


# ---- routes ------------------------------------------------------------------


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., min_length=1, max_length=300)


@router.get("")
def read_key(owner: User = Depends(require_role("owner"))):
    """Whether this business has its own AI key saved: only its last four."""
    return view(tenant_of(owner))


@router.put("")
def save_key(payload: KeyIn, owner: User = Depends(require_role("owner"))):
    """Save (or replace) this business's own AI key. Owner only."""
    tenant = tenant_of(owner)
    if not configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=why_not())
    key = payload.key.strip()
    if not KEY_RE.match(key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=("That does not look like an API key. Paste the whole key "
                    f"from {PROVIDER}, with no spaces."))
    existing = saved(tenant.tenant_id)
    STORE._db.put(KIND, tenant.tenant_id, {
        "tenant_id": tenant.tenant_id,
        "provider": "gemini",
        "key_ciphertext": seal(tenant.tenant_id, key),
        "key_hint": hint(key),
        "saved_at": iso(utc_now()),
    })
    forget(tenant.tenant_id)
    write_audit(tenant, owner, "ai_key.replaced" if existing else "ai_key.saved",
                f"Own AI key for alert wording {'replaced' if existing else 'saved'} "
                f"(ends {hint(key)}).")
    return {"message": "Saved. It is stored encrypted; only its last four are shown.",
            **view(tenant)}


@router.delete("")
def remove_key(owner: User = Depends(require_role("owner"))):
    """Remove this business's own AI key. Alert lines go back to the service's."""
    tenant = tenant_of(owner)
    if saved(tenant.tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="There is no saved AI key to remove.")
    STORE._db.delete(KIND, tenant.tenant_id)
    forget(tenant.tenant_id)
    write_audit(tenant, owner, "ai_key.removed", "Own AI key for alert wording removed.")
    return {"message": "Removed. The key is deleted.", **view(tenant)}


@router.post("/test")
def test_key(owner: User = Depends(require_role("owner"))):
    """Word one sample alert line on the saved key, to see that it works.

    The same call an alert makes, with the same deadline and the same
    fallback, about a pretend unit of this business's trade. Nothing is
    sent to anyone; the provider bills the key for the one call.
    """
    import wording
    from gemini import safe_generate

    tenant = tenant_of(owner)
    if key_for(tenant.tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="There is no saved AI key to try.")
    from store import INDUSTRY_PROFILES

    vertical = tenant.industry_vertical if tenant.industry_vertical in INDUSTRY_PROFILES else "restaurant"
    sample = SimpleNamespace(industry_vertical=vertical)
    template = wording.action_for(sample)
    line, source = safe_generate(
        wording.action_prompt(sample, {"direction": "above"}), template,
        purpose="alert line test", tenant_id=tenant.tenant_id,
        business_key=True, use_cache=False)
    worked = source == "gemini"
    return {
        "worked": worked,
        "line": (wording.clean_action(line) or template) if worked else template,
        "message": ("It worked. Your alerts' what-to-check line is written on your key."
                    if worked else
                    "That did not work, so alerts would use the plain wording shown. "
                    "Check the key, and that it may use the model this service is set to."),
        **view(tenant),
    }
