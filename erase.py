"""A business takes its data and leaves.

Two buttons for the owner, on the Setup tab:

* Download everything: one JSON file with every record stored for this
  business. Passwords, keys and tokens are left out.
* Delete everything: the business, its sensors and readings, people,
  incidents, mail, settings, all of it, straight away. The only thing
  kept is the bills already sent, because tax law makes a seller keep
  those, and each bill already names the company it went to.

Deleting asks for the business's name typed out and the owner's
password, so neither a slip of the finger nor a borrowed phone with a
signed-in session can do it. It also waits until nothing is owed, so
"delete" is never a way to make a bill disappear.

Daily backups still hold a copy until they age out; the privacy page
says how long.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from accounts import require_role, tenant_of
from store import STORE, User, iso, utc_now, verify_password

logger = logging.getLogger("cyberlogix.erase")

router = APIRouter(prefix="/api/accounts/business", tags=["Leaving"])


class EraseRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    company_name: str = Field(..., min_length=1, max_length=200,
                              description="The business's name, typed out.")
    password: str = Field(..., min_length=1, max_length=500)


@router.get("/export")
def export_business(user: User = Depends(require_role("owner"))):
    tenant = tenant_of(user)
    body = {
        "business": tenant.company_name,
        "tenant_id": tenant.tenant_id,
        "exported_at": iso(utc_now()),
        "note": "Everything stored for this business. Passwords, keys and tokens are left out.",
        "records": STORE.export_tenant(tenant.tenant_id),
    }
    return Response(
        content=json.dumps(body, indent=2, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="cyberlogix-{tenant.tenant_id}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/erase")
def erase_business(payload: EraseRequest, user: User = Depends(require_role("owner"))):
    tenant = tenant_of(user)
    if payload.company_name.casefold() != tenant.company_name.strip().casefold():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That is not this business's name. Nothing was deleted.",
        )
    person = STORE.person_of(user)
    if not verify_password(payload.password, person.password_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Wrong password. Nothing was deleted.",
        )
    owed = round(sum(i.balance_usd for i in STORE.open_invoices(tenant.tenant_id)), 2)
    if owed > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"This business still owes ${owed:,.2f}. Pay it first, "
                    "then you can delete everything. Nothing was deleted."),
        )
    removed = STORE.erase_tenant(tenant.tenant_id)
    logger.warning("Business %s deleted itself: %d records removed.",
                   tenant.tenant_id, sum(removed.values()))
    return {
        "deleted": True,
        "records_removed": sum(removed.values()),
        "kept": "Bills already sent, because tax law requires it.",
    }
