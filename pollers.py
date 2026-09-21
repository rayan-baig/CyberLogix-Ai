"""Fetching readings from a sensor cloud that will not push them to us.

The bridge assumes a sensor can be told where to send its readings. Many
can. The one this product most often recommends cannot: a SensorPush G1
gateway posts to SensorPush, and the way to get the data is to ask their
API for it. Same for a good deal of cheap hardware, which speaks only to
the vendor's own cloud and offers a read API as the way out.

Without this the recommendation was hollow. You could buy the sensor,
point it at nothing, and the console would sit empty.

**Generic on purpose.** Not "a SensorPush adapter", because an adapter
written against documentation nobody here can reach is a guess that
looks like support, and it goes stale the first time the vendor ships a
new endpoint. Instead: the customer gives a URL, whatever headers it
needs, and how often to ask. What comes back is handed to the same
translator the webhook path uses -- which already knows Monnit's field
names, SensorPush's, and how to be told about anything else by pointing
at one real payload.

So this module knows about HTTP and nothing about any vendor, and adding
a vendor is configuration rather than code.

**It never raises into the scheduler.** A vendor being down, slow, or
returning HTML instead of JSON is a Tuesday. Each failure is recorded on
the source so the console can say which one is broken and since when,
and the next pass tries again.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accounts import require_role
from auth import write_audit
from licenses import require_tenant
from store import STORE, Tenant, User, iso, utc_now

logger = logging.getLogger("cyberlogix.pollers")

router = APIRouter(prefix="/api/pollers", tags=["Vendor polling"])

SOURCE_KIND = "poll_source"

# How often a source may be asked. Below the floor is somebody's rate
# limit being burned through; above the ceiling is a freezer failing
# unnoticed for a morning.
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 120
DEFAULT_INTERVAL_MINUTES = 10

# Cheap hardware clouds are slow. Long enough to be patient, short enough
# that one stuck vendor cannot hold up the whole pass.
TIMEOUT_SECONDS = 20

# A vendor returning a megabyte of history is a vendor whose response is
# not being read into memory unbounded.
MAX_RESPONSE_BYTES = 2_000_000

# Consecutive failures before the console calls a source broken rather
# than merely quiet. Two is a blip; five is a Tuesday that is not ending.
FAILURES_BEFORE_BROKEN = 5


def sources_for(tenant_id: str) -> List[Dict[str, Any]]:
    rows = [r for r in STORE._db.all(SOURCE_KIND)
            if r.get("tenant_id") == tenant_id]
    return sorted(rows, key=lambda r: r.get("added_at") or "")


def public(row: Dict[str, Any]) -> Dict[str, Any]:
    """A source as the console may see it.

    Header values are the credential: a token, a password, a signed key.
    The names are shown because a customer needs to see what is being
    sent; the values never are, because a screen is shared and a console
    is logged into by an operator who should not be able to read the
    owner's vendor password back out of it.
    """
    headers = row.get("headers") or {}
    failures = row.get("consecutive_failures", 0)
    return {
        "source_id": row["source_id"],
        "name": row["name"],
        "url": row["url"],
        "header_names": sorted(headers),
        "interval_minutes": row["interval_minutes"],
        "enabled": row.get("enabled", True),
        "last_polled_at": row.get("last_polled_at"),
        "last_status": row.get("last_status"),
        "last_detail": row.get("last_detail", ""),
        "readings_ingested": row.get("readings_ingested", 0),
        "consecutive_failures": failures,
        "broken": failures >= FAILURES_BEFORE_BROKEN,
        "added_at": row.get("added_at"),
    }


def due(row: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Whether this source is owed a poll.

    A source that has never been polled is always due, so adding one
    produces data on the next pass rather than after a wait nobody
    understands.
    """
    if not row.get("enabled", True):
        return False
    last = row.get("last_polled_at")
    if not last:
        return True
    try:
        when = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return True
    gap = timedelta(minutes=row.get("interval_minutes",
                                    DEFAULT_INTERVAL_MINUTES))
    return (now or utc_now()) - when >= gap


# Hostnames that are a way back inside whatever this is running on, and
# are names rather than addresses, so no amount of address arithmetic
# catches them.
BLOCKED_NAMES = frozenset({
    "localhost", "metadata", "metadata.google.internal",
    "instance-data", "metadata.goog",
})


# IPv6 ranges that carry an IPv4 address in their low 32 bits and that
# `ipaddress` still calls global, so the embedded address is never
# looked at unless something goes looking. ::127.0.0.1 is the plain case.
CARRIES_AN_IPV4 = (
    ipaddress.ip_network("::/96"),        # IPv4-compatible, deprecated
    ipaddress.ip_network("64:ff9b::/96"), # NAT64 well-known prefix
)


def _embedded_ipv4(ip: ipaddress._BaseAddress):
    """The IPv4 address riding inside an IPv6 one, if there is one.

    Six ways exist to write an IPv4 address as an IPv6 address, and
    `is_private` only knows about one of them. `::127.0.0.1` and
    `64:ff9b::7f00:1` both reach 127.0.0.1 on a stack that routes them,
    and both are is_global=True.
    """
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    for attr in ("ipv4_mapped", "sixtofour"):
        found = getattr(ip, attr, None)
        if found is not None:
            return found
    teredo = getattr(ip, "teredo", None)
    if teredo:
        return teredo[1]
    if any(ip in net for net in CARRIES_AN_IPV4):
        return ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
    return None


def _reaches_the_public_internet(ip: ipaddress._BaseAddress) -> bool:
    """Is this an address out on the internet, rather than a way back in?

    `is_global` is very nearly the whole answer; the rest is spelled out
    because a future Python widening any one of them should not quietly
    widen this.
    """

    def plainly_public(addr) -> bool:
        return addr.is_global and not (
            addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_multicast or addr.is_reserved or addr.is_unspecified
        )

    inner = _embedded_ipv4(ip)
    if inner is not None and not plainly_public(inner):
        return False
    return plainly_public(ip)


def _as_address(host: str) -> Optional[ipaddress._BaseAddress]:
    """The address this host already IS, or None if it is a name.

    Handles the encodings that are an address to every resolver and not
    an address to a string comparison: 2130706433 and 0x7f000001 both
    reach 127.0.0.1.
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        packed = int(host, 0)
    except ValueError:
        return None
    if 0 <= packed <= 0xFFFFFFFF:
        return ipaddress.ip_address(packed)
    return None


def host_of(url: str) -> str:
    """The hostname, brackets stripped and lowercased.

    Through urlsplit rather than by hand. Splitting on ':' is what let
    `https://[::1]:8080/` past: it yields '[', which matches none of the
    blocked prefixes and is not an address anybody checked.
    """
    host = (urlsplit(url).hostname or "").strip().rstrip(".")
    if not host:
        raise ValueError("That URL has no host in it.")
    return host


def refuse_a_host_pointing_inward(host: str) -> None:
    """Refuse a host that is plainly local, without asking a resolver.

    Save-time half: instant, offline, and wrong about a public NAME that
    resolves somewhere private -- which is why `_resolve_to_public` runs
    again at fetch time and is the half that actually enforces this.
    """
    if host in BLOCKED_NAMES or host.endswith(".localhost"):
        raise ValueError(
            f"'{host}' is this machine. This fetches from the public "
            "internet only.")
    literal = _as_address(host)
    if literal is not None and not _reaches_the_public_internet(literal):
        raise ValueError(
            f"'{host}' is {literal}, a private, loopback or link-local "
            "address. This fetches from the public internet only.")


def _resolve_to_public(host: str) -> None:
    """Refuse unless every address this name resolves to is public.

    The save-time check reads the URL; this one asks a resolver, and it
    is the one that matters. A name the customer controls can be public
    in spelling and point at 169.254.169.254 -- which on a cloud host is
    the credentials of the machine this runs on.

    A name that changes answer between this call and the connect is not
    closed by this. Closing that means connecting to the address checked
    rather than the name, which costs the certificate check; refusing
    redirects and re-checking every fetch is where that trade is drawn.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"'{host}' does not resolve to anything.") from exc
    if not infos:
        raise ValueError(f"'{host}' does not resolve to anything.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _reaches_the_public_internet(ip):
            raise ValueError(
                f"'{host}' resolves to {ip}, which is inside a private "
                "network. This fetches from the public internet only.")


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Follow nothing.

    Every address check above reads the URL that was saved. A redirect
    is a second URL, chosen by the vendor after all of them have run,
    and urllib follows it by default -- carrying the customer's vendor
    credential to wherever it points. One 302 to 169.254.169.254 turns
    all of this back into the hole it was.

    Refusing outright rather than re-checking the new URL: a data API
    that redirects a GET is rare, and 'we did not follow that' is a
    better answer to debug than a redirect chain that was silently
    walked.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError(
            f"The vendor answered {code} and redirected to '{newurl}'. "
            "Redirects are not followed, because the address checks ran "
            "against the URL you saved and cannot run against one the "
            "vendor picks afterwards. Save the final URL instead.")


_OPENER = urllib.request.build_opener(_RefuseRedirects)


def _fetch(url: str, headers: Dict[str, str]) -> Any:
    """One GET, decoded as JSON. Raises; the caller is what must not."""
    _resolve_to_public(host_of(url))
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "application/json")
    for name, value in headers.items():
        request.add_header(name, value)
    with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError(
            f"Response is over {MAX_RESPONSE_BYTES // 1000}kB. This is "
            "meant to fetch the latest readings, not a history export."
        )
    return json.loads(raw.decode("utf-8", errors="replace"))


def _rows_in(payload: Any) -> List[Any]:
    """The readings inside whatever shape came back.

    A vendor returns one reading, a list of them, or a dict keyed by
    device. All three are common and none is wrong, so all three are
    handled rather than one being declared the format.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("samples", "readings", "data", "results", "sensors"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return inner
            if isinstance(inner, dict):
                # Keyed by device id: keep the key, because it is very
                # often the serial and the row inside does not repeat it.
                return [{"device_id": k, **v} if isinstance(v, dict) else v
                        for k, v in inner.items()]
        return [payload]
    return []


def poll_once(row: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Ask one source, ingest what it gives, and record how it went.

    Never raises. A vendor being down, slow, or returning an HTML error
    page is normal, and the scheduler must not care.
    """
    now = now or utc_now()
    tenant = STORE.get_tenant(row["tenant_id"])
    outcome = {"source_id": row["source_id"], "ingested": 0,
               "status": "failed", "detail": ""}

    if tenant is None:
        outcome["detail"] = "The account this source belongs to is gone."
    else:
        try:
            payload = _fetch(row["url"], row.get("headers") or {})
            outcome.update(_ingest(tenant, row, payload))
        except urllib.error.HTTPError as exc:
            outcome["detail"] = f"The vendor answered {exc.code}."
        except urllib.error.URLError as exc:
            outcome["detail"] = f"Could not reach the vendor: {exc.reason}"
        except json.JSONDecodeError:
            outcome["detail"] = (
                "The vendor did not return JSON. That is usually a login "
                "page, which means the credential is wrong or expired."
            )
        except Exception as exc:  # noqa: BLE001 - one source must not stop the pass
            logger.exception("Poll of %s failed.", row["source_id"])
            outcome["detail"] = f"{type(exc).__name__}: {exc}"[:200]

    failed = outcome["status"] != "ok"
    STORE._db.put(SOURCE_KIND, row["source_id"], {
        **row,
        "last_polled_at": iso(now),
        "last_status": outcome["status"],
        "last_detail": outcome["detail"],
        "readings_ingested": row.get("readings_ingested", 0)
                             + outcome["ingested"],
        "consecutive_failures": (row.get("consecutive_failures", 0) + 1
                                 if failed else 0),
    })
    return outcome


def _ingest(tenant: Tenant, row: Dict[str, Any], payload: Any) -> Dict[str, Any]:
    """Hand each row to the translator the webhook path already uses."""
    import byod
    from hardware_bridge import GenericWebhookPayload, \
        ingest_third_party_hardware_webhook

    rows = _rows_in(payload)
    if not rows:
        return {"status": "empty", "ingested": 0,
                "detail": "The vendor answered, with no readings in it."}

    taken, skipped = 0, []
    for entry in rows:
        verdict = byod.detect(entry)
        if not verdict["understood"] or verdict["unit"] is None:
            # Kept, so "why is my sensor not showing up" has an answer
            # that is a payload rather than a theory.
            byod.remember(tenant.tenant_id, entry, verdict,
                          "not_understood" if not verdict["understood"]
                          else "unit_unknown")
            skipped.append(verdict.get("missing") or ["unit"])
            continue
        try:
            # It authenticates from the token on the payload, the same
            # way the webhook path does, so the account's own key is
            # what is passed. No separate trust path for polled
            # readings: they land under exactly the checks a pushed one
            # gets, including the serial having to be a registered
            # device on this account.
            ingest_third_party_hardware_webhook(
                GenericWebhookPayload(
                    device_sn=verdict["serial"],
                    api_key_token=tenant.api_key,
                    reading_value=verdict["value"],
                    metric_type=verdict["unit"],
                )
            )
            byod.remember(tenant.tenant_id, entry, verdict, "ingested")
            taken += 1
        except HTTPException as exc:
            # An unregistered serial or an implausible reading. Both are
            # the customer's to fix and neither is this poll failing.
            skipped.append(str(exc.detail)[:80])

    if taken:
        return {"status": "ok", "ingested": taken,
                "detail": (f"{taken} reading(s) taken"
                           + (f", {len(skipped)} skipped" if skipped else ""))}
    return {"status": "nothing_usable", "ingested": 0,
            "detail": (f"{len(rows)} row(s) came back and none could be "
                       f"used. First reason: {skipped[0] if skipped else '?'}")}


def run_poll_pass(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Every source that is due, in one pass. Called by the scheduler."""
    now = now or utc_now()
    polled, ingested, failed = 0, 0, 0
    for row in list(STORE._db.all(SOURCE_KIND)):
        if not due(row, now):
            continue
        outcome = poll_once(row, now)
        polled += 1
        ingested += outcome["ingested"]
        if outcome["status"] != "ok":
            failed += 1
    return {"polled": polled, "readings": ingested, "failed": failed}


# --- routes ----------------------------------------------------------------


class NewSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=2, max_length=120)
    url: str = Field(..., min_length=8, max_length=2000)
    headers: Dict[str, str] = Field(default_factory=dict)
    interval_minutes: int = Field(
        DEFAULT_INTERVAL_MINUTES,
        ge=MIN_INTERVAL_MINUTES, le=MAX_INTERVAL_MINUTES)
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def a_public_https_url(cls, value: str) -> str:
        """HTTPS, and not pointed back inside our own network.

        A URL the customer controls is fetched by our server, which is
        the shape of a server-side request forgery: left open, somebody
        stores http://169.254.169.254/ and reads the cloud metadata
        service through us.
        """
        url = value.strip()
        if not url.lower().startswith("https://"):
            raise ValueError(
                "The URL must be https. A credential sent over plain http "
                "is readable by anything between us and the vendor.")
        # Both halves deliberately. This one is instant and offline and
        # gives the person typing it an immediate answer; the one inside
        # _fetch asks a resolver and is what actually holds, because a
        # perfectly public-looking name can point anywhere.
        refuse_a_host_pointing_inward(host_of(url))
        return url

    @field_validator("headers")
    @classmethod
    def sane_headers(cls, value: Dict[str, str]) -> Dict[str, str]:
        if len(value) > 12:
            raise ValueError("That is more headers than any vendor needs.")
        for name, header in value.items():
            if not name.strip() or len(name) > 120 or len(header) > 4000:
                raise ValueError(f"Header '{name[:40]}' is not usable.")
            if "\n" in name or "\r" in name or "\n" in header or "\r" in header:
                raise ValueError(
                    "A newline in a header is how one request is turned "
                    "into two.")
        return value


@router.get("")
def list_sources(tenant: Tenant = Depends(require_tenant)):
    """Every vendor cloud this account fetches from."""
    rows = [public(r) for r in sources_for(tenant.tenant_id)]
    broken = [r for r in rows if r["broken"]]
    return {
        "count": len(rows),
        "sources": rows,
        "broken": len(broken),
        "note": (
            f"{len(broken)} source(s) have failed {FAILURES_BEFORE_BROKEN} "
            "times in a row. Nothing is arriving from them."
            if broken else
            "Some sensors only talk to their own vendor's cloud. Point us "
            "at that cloud's API and we fetch from it on a timer."
        ),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def add_source(
    payload: NewSource,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("owner")),
):
    """Add a vendor cloud to fetch from.

    Owner only: the headers are a credential, and this makes our server
    fetch a URL somebody chose.
    """
    source_id = STORE._next_id("PLL")
    row = {
        "source_id": source_id,
        "tenant_id": tenant.tenant_id,
        "name": payload.name.strip(),
        "url": payload.url,
        "headers": payload.headers,
        "interval_minutes": payload.interval_minutes,
        "enabled": payload.enabled,
        "added_at": iso(utc_now()),
        "last_polled_at": None,
        "last_status": None,
        "last_detail": "",
        "readings_ingested": 0,
        "consecutive_failures": 0,
    }
    STORE._db.put(SOURCE_KIND, source_id, row)
    write_audit(tenant, operator, "poller.added",
                f"{row['name']} every {row['interval_minutes']}m")
    return {"source": public(row)}


@router.post("/{source_id}/test")
def test_source(
    source_id: str,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("owner")),
):
    """Ask it now, and say plainly what came back.

    The whole reason this exists: a source added and not tested is a
    source nobody finds out about until the freezer fails and the
    console was empty the whole time.
    """
    row = STORE._db.get(SOURCE_KIND, source_id)
    if row is None or row.get("tenant_id") != tenant.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such source on this account.")
    outcome = poll_once(row)
    write_audit(tenant, operator, "poller.tested",
                f"{row['name']}: {outcome['status']}")
    return {"result": outcome,
            "source": public(STORE._db.get(SOURCE_KIND, source_id))}


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_source(
    source_id: str,
    tenant: Tenant = Depends(require_tenant),
    operator: User = Depends(require_role("owner")),
):
    """Stop fetching, and forget the credential."""
    row = STORE._db.get(SOURCE_KIND, source_id)
    if row is None or row.get("tenant_id") != tenant.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No such source on this account.")
    STORE._db.delete(SOURCE_KIND, source_id)
    write_audit(tenant, operator, "poller.removed", row["name"])
    return None
