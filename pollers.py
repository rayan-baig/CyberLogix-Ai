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

import http.client
import ipaddress
import json
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, wait
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

# The whole of one fetch, however the time is spent. The socket timeout
# above is per read, so a vendor trickling a byte every 19 seconds could
# hold one fetch open for as long as it liked.
FETCH_DEADLINE_SECONDS = 30

# The poll pass runs just ahead of the sweep that phones people about
# breaches. It used to fetch every due source one after another, so ten
# stuck vendors at 20 seconds each held every escalation in the fleet for
# over three minutes. Now sources are fetched side by side, and the sweep
# waits for them at most this long; anything slower finishes in the
# background and is scored on the next pass.
POLL_WORKERS = 8
POLL_PASS_SECONDS = 25

# Each source is a fetch our server makes on the customer's behalf. A
# ceiling keeps one account from turning that into a crawler.
MAX_SOURCES_PER_TENANT = 10

# A vendor returning a megabyte of history is a vendor whose response is
# not being read into memory unbounded.
MAX_RESPONSE_BYTES = 2_000_000

# Consecutive failures before the console calls a source broken rather
# than merely quiet. Two is a blip; five is a Tuesday that is not ending.
# At that point the source also stops being asked, and the owner is told:
# asking a vendor every ten minutes, all night, with a credential it has
# stopped accepting is how the vendor ends up locking the account, and
# a source that fails in silence is a freezer nobody is watching. "Try
# now" in the console asks again, and one success starts it up again.
FAILURES_BEFORE_BROKEN = 5

# Outcomes that are the source working. "unchanged" is a vendor answering
# with readings already taken: the device is quiet, not the source broken.
OK_STATUSES = frozenset({"ok", "unchanged"})


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
        "stopped": bool(row.get("stopped_at")),
        "stopped_at": row.get("stopped_at"),
        "added_at": row.get("added_at"),
    }


def due(row: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Whether this source is owed a poll.

    A source that has never been polled is always due, so adding one
    produces data on the next pass rather than after a wait nobody
    understands.
    """
    if not row.get("enabled", True) or row.get("stopped_at"):
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


def _resolve_to_public(host: str) -> Optional[List[str]]:
    """Refuse unless every address this name resolves to is public.

    The save-time check reads the URL; this one asks a resolver, and it
    is the one that matters. A name the customer controls can be public
    in spelling and point at 169.254.169.254 -- which on a cloud host is
    the credentials of the machine this runs on.

    Returns the addresses it checked, and the connection is made to one
    of those, never to the name again (see `_connect_checked`). A name
    that answers with a public address for this check and a private one
    a moment later, for the connect -- DNS rebinding -- therefore gets
    nowhere: the second answer is never asked for.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"'{host}' does not resolve to anything.") from exc
    if not infos:
        raise ValueError(f"'{host}' does not resolve to anything.")
    checked: List[str] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _reaches_the_public_internet(ip):
            raise ValueError(
                f"'{host}' resolves to {ip}, which is inside a private "
                "network. This fetches from the public internet only.")
        if str(ip) not in checked:
            checked.append(str(ip))
    return checked


def _connect_checked(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                     source_address=None):
    """Open the socket to an address that was checked, not to the name.

    Takes the place of `socket.create_connection` inside the HTTP(S)
    connection, so the name is resolved once, here, every address it gave
    is checked, and the socket goes to one of those. TLS still verifies
    the certificate against the name: http.client wraps this socket with
    the hostname, not the address.
    """
    host, port = address
    checked = _resolve_to_public(host)
    if checked is None:  # the checks have been switched off (tests only)
        return socket.create_connection(address, timeout, source_address)
    last: Optional[OSError] = None
    for ip in checked:
        try:
            return socket.create_connection((ip, port), timeout, source_address)
        except OSError as exc:
            last = exc
    raise last or OSError(f"Could not connect to '{host}'.")


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


_deadline = threading.local()


class _Tracked:
    """Remembers each socket a fetch opens, so a timer can close it.

    And opens it with `_connect_checked`, so it reaches only an address
    the checks have passed.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_checked

    def connect(self):
        super().connect()
        socks = getattr(_deadline, "socks", None)
        if socks is not None:
            socks.append(self.sock)


class _TrackedHTTPS(_Tracked, http.client.HTTPSConnection):
    pass


class _TrackedHTTP(_Tracked, http.client.HTTPConnection):
    pass


class _DeadlineHTTPS(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_TrackedHTTPS, req, context=self._context)


class _DeadlineHTTP(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_TrackedHTTP, req)


# No proxy, whatever the environment says. A proxy resolves the name
# itself, so every address check here would be about a connection that
# is never made.
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _RefuseRedirects, _DeadlineHTTPS, _DeadlineHTTP)


def _fetch(url: str, headers: Dict[str, str]) -> Any:
    """One GET, decoded as JSON. Raises; the caller is what must not."""
    _resolve_to_public(host_of(url))
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "application/json")
    for name, value in headers.items():
        request.add_header(name, value)
    deadline = time.monotonic() + FETCH_DEADLINE_SECONDS
    late = TimeoutError(f"The vendor took over {FETCH_DEADLINE_SECONDS} seconds to answer.")
    # A hard stop for the whole fetch, headers included: at the deadline the
    # connection is closed under whatever is reading it. Checking the clock
    # only between body chunks let a vendor trickling its headers a byte at a
    # time hold a worker, and the source, for as long as it liked.
    _deadline.socks = socks = []
    fired = threading.Event()

    def _cut():
        fired.set()
        for sock in list(socks):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    timer = threading.Timer(FETCH_DEADLINE_SECONDS, _cut)
    timer.daemon = True
    timer.start()
    chunks, size = [], 0
    try:
        with _OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            while size <= MAX_RESPONSE_BYTES:
                if time.monotonic() > deadline:
                    raise late
                chunk = response.read(min(65536, MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
    except (OSError, http.client.HTTPException) as exc:
        if fired.is_set():
            raise late from exc
        raise
    finally:
        timer.cancel()
        _deadline.socks = None
    if fired.is_set():
        raise late
    raw = b"".join(chunks)
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
            # Checked and ingested under the source's own lock, which
            # removal takes too: a source removed while its fetch was out
            # writes nothing, and once removal has returned nothing it
            # fetched can be written afterwards.
            with _source_lock(row["source_id"]):
                if STORE._db.get(SOURCE_KIND, row["source_id"]) is None:
                    outcome["detail"] = "The source was removed while it was being fetched."
                    return outcome
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
        except (ValueError, TimeoutError) as exc:
            # Ours, written to be read by the customer: the address checks,
            # the size limit, the deadline.
            outcome["detail"] = str(exc)[:200]
        except Exception:  # noqa: BLE001 - one source must not stop the pass
            # Anything else is a fault on our side or deep in a library, and
            # its text (paths, internal names) is not for the customer.
            logger.exception("Poll of %s failed.", row["source_id"])
            outcome["detail"] = "Something unexpected went wrong reading this vendor. It has been logged."

    observed = outcome.pop("observed", None) or {}
    failed = outcome["status"] not in OK_STATUSES
    stopped_now: Optional[Dict[str, Any]] = None
    with STORE._lock:
        # The row as it is now, not as it was when the fetch began: writing
        # the old copy back brought a source the owner had just removed back
        # to life, credential and all, and undid any edit made meanwhile.
        current = STORE._db.get(SOURCE_KIND, row["source_id"])
        if current is not None:
            failures = current.get("consecutive_failures", 0) + 1 if failed else 0
            stopped_at = current.get("stopped_at") if failed else None
            if failures >= FAILURES_BEFORE_BROKEN and not stopped_at:
                stopped_at = iso(now)
            updated = {
                **current,
                "last_polled_at": iso(now),
                "last_status": outcome["status"],
                "last_detail": outcome["detail"],
                "readings_ingested": current.get("readings_ingested", 0)
                                     + outcome["ingested"],
                "consecutive_failures": failures,
                "stopped_at": stopped_at,
                "last_observed": {**(current.get("last_observed") or {}), **observed},
            }
            STORE._db.put(SOURCE_KIND, row["source_id"], updated)
            if stopped_at and not current.get("stopped_at"):
                stopped_now = updated
    if stopped_now is not None and tenant is not None:
        _tell_the_owner_it_stopped(tenant, stopped_now)
    return outcome


def _tell_the_owner_it_stopped(tenant: Tenant, row: Dict[str, Any]) -> None:
    """Write it down and email the account, once per stop. Never raises."""
    try:
        detail = (row.get("last_detail") or "").strip() or "no reason was given"
        write_audit(
            tenant, None, "poller.stopped",
            f"{row['name']}: stopped after {row['consecutive_failures']} failures "
            f"in a row. Last: {detail}",
            fallback_actor="Vendor polling",
        )
        import mail

        mail.send(
            to_address=tenant.contact_email,
            subject=f"{tenant.company_name}: we have stopped fetching from {row['name']}",
            body=(
                f"We could not get readings from {row['name']} "
                f"{row['consecutive_failures']} times in a row, so we have "
                "stopped asking it.\n\n"
                f"The last answer was: {detail}\n\n"
                "Until this is fixed, nothing from the sensors behind it is "
                "arriving, and nobody will be alerted about them. That is "
                "usually an expired password or API token at the vendor.\n\n"
                "Fix it, then open the console and press Try now on this "
                "source. One good answer starts it again."
            ),
            dedupe_key=f"poller-stopped:{row['source_id']}:{row['stopped_at']}",
            tenant_id=tenant.tenant_id,
        )
    except Exception:  # noqa: BLE001 - telling the owner must not stop the pass
        logger.exception("Could not tell the owner that %s stopped.", row.get("source_id"))


_MS_DATE = re.compile(r"/Date\((-?\d+)")


def observed_time(value: Any) -> Optional[datetime]:
    """When the vendor says a reading was taken, or None if it cannot say.

    Vendors write it every way there is: ISO text with or without a zone,
    Unix seconds, Unix milliseconds, and Microsoft's "/Date(ms)/". A time
    with no zone is taken as UTC, which is what every API in this market
    means by one.
    """
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    number: Optional[float] = None
    found = _MS_DATE.search(text)
    if found:
        number = int(found.group(1)) / 1000.0
    else:
        try:
            number = float(text)
        except ValueError:
            number = None
        else:
            # Milliseconds since 1970 run to thirteen digits; seconds to ten.
            if abs(number) > 1e11:
                number /= 1000.0
    try:
        if number is not None:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        when = datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def _ingest(tenant: Tenant, row: Dict[str, Any], payload: Any) -> Dict[str, Any]:
    """Hand each row to the translator the webhook path already uses.

    A reading the vendor timestamps is taken once. A vendor's "latest"
    endpoint keeps answering with the last reading it holds, however old:
    taken again on every poll, a sensor whose battery had died looked alive
    for ever, so nobody was told it had gone quiet, and one old warm reading
    re-opened its incident every time somebody resolved it. So the newest
    time taken for each device is remembered on the source, anything not
    newer is passed over, and what is new is taken oldest first, so the
    newest reading is the one left showing.
    """
    import byod
    from hardware_bridge import GenericWebhookPayload, ingest_for_tenant

    rows = _rows_in(payload)
    if not rows:
        return {"status": "empty", "ingested": 0,
                "detail": "The vendor answered, with no readings in it."}

    taken, skipped = 0, []
    already = 0
    seen_before = row.get("last_observed") or {}
    newest: Dict[str, str] = {}
    usable = []
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
        when = observed_time(verdict.get("observed_at"))
        if when is not None:
            last = observed_time(seen_before.get(verdict["serial"]))
            if last is not None and when <= last:
                already += 1
                continue
        usable.append((when, entry, verdict))
    # Oldest first; a row with no time keeps its place after the timed ones.
    usable.sort(key=lambda item: (item[0] is None,
                                  item[0] or datetime.min.replace(tzinfo=timezone.utc)))

    for when, entry, verdict in usable:
        try:
            # Exactly the checks a pushed reading gets, including the
            # serial having to be a registered device on this account
            # and the licence being current; only the credential is
            # skipped, because this source is already known to be theirs.
            ingest_for_tenant(
                tenant,
                GenericWebhookPayload(
                    device_sn=verdict["serial"],
                    reading_value=verdict["value"],
                    metric_type=verdict["unit"],
                ),
            )
            byod.remember(tenant.tenant_id, entry, verdict, "ingested")
            taken += 1
            if when is not None:
                # Full precision: a vendor timestamping in milliseconds
                # would otherwise look newer than itself next time.
                newest[verdict["serial"]] = when.isoformat()
        except HTTPException as exc:
            # An unregistered serial or an implausible reading. Both are
            # the customer's to fix and neither is this poll failing.
            skipped.append(str(exc.detail)[:80])

    if taken:
        return {"status": "ok", "ingested": taken, "observed": newest,
                "detail": (f"{taken} reading(s) taken"
                           + (f", {len(skipped)} skipped" if skipped else ""))}
    if already:
        # The vendor is answering; the device has not sent anything since.
        # Not a failure of the source, so it is not counted towards broken,
        # and the sensor is left to go offline the way a quiet one should.
        return {"status": "unchanged", "ingested": 0,
                "detail": (f"Nothing new since the last fetch: the vendor sent "
                           f"{already} reading(s) already taken"
                           + (f", {len(skipped)} skipped" if skipped else "") + ".")}
    return {"status": "nothing_usable", "ingested": 0,
            "detail": (f"{len(rows)} row(s) came back and none could be "
                       f"used. First reason: {skipped[0] if skipped else '?'}")}


_in_flight: set = set()
_in_flight_lock = threading.Lock()

# One lock per source, held while a fetched answer is written and while the
# source is removed. See poll_once.
_source_locks: Dict[str, threading.Lock] = {}


def _source_lock(source_id: str) -> threading.Lock:
    with _in_flight_lock:
        return _source_locks.setdefault(source_id, threading.Lock())


def _poll_and_release(row: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    try:
        return poll_once(row, now)
    finally:
        with _in_flight_lock:
            _in_flight.discard(row["source_id"])


def run_poll_pass(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Every source that is due, in one pass. Called by the scheduler.

    Side by side, and bounded: see POLL_PASS_SECONDS. A source still being
    fetched from an earlier pass is not started a second time.
    """
    now = now or utc_now()
    with _in_flight_lock:
        rows = [r for r in STORE._db.all(SOURCE_KIND)
                if due(r, now) and r["source_id"] not in _in_flight]
        _in_flight.update(r["source_id"] for r in rows)
    if not rows:
        return {"polled": 0, "readings": 0, "failed": 0, "still_running": 0}

    pool = ThreadPoolExecutor(max_workers=POLL_WORKERS, thread_name_prefix="vendor-poll")
    futures = {}
    for row in rows:
        try:
            futures[pool.submit(_poll_and_release, row, now)] = row
        except RuntimeError:  # no thread to be had: try this one next pass
            with _in_flight_lock:
                _in_flight.discard(row["source_id"])
    done, waiting = wait(futures, timeout=POLL_PASS_SECONDS)
    # Not waited for: a stuck vendor finishes, or times out, on its own.
    pool.shutdown(wait=False, cancel_futures=True)
    for future in waiting:
        if future.cancelled():
            with _in_flight_lock:
                _in_flight.discard(futures[future]["source_id"])

    polled, ingested, failed = 0, 0, 0
    for future in done:
        try:
            outcome = future.result()
        except Exception:  # noqa: BLE001 - poll_once never raises, but a pass must not die
            logger.exception("A vendor poll failed unexpectedly.")
            failed += 1
            continue
        polled += 1
        ingested += outcome["ingested"]
        if outcome["status"] not in OK_STATUSES:
            failed += 1
    if waiting:
        logger.warning("%d vendor poll(s) still running after %ss; the sweep goes ahead without them.",
                       len(waiting), POLL_PASS_SECONDS)
    return {"polled": polled, "readings": ingested, "failed": failed, "still_running": len(waiting)}


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
            "times in a row. Nothing is arriving from them, and they are no "
            "longer being asked: fix the credential, then press Try now."
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
    if len(sources_for(tenant.tenant_id)) >= MAX_SOURCES_PER_TENANT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"An account can fetch from {MAX_SOURCES_PER_TENANT} vendor clouds. "
                    "Remove one you no longer use, or talk to us."),
        )
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
    with _in_flight_lock:
        if source_id in _in_flight:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="This source is being fetched right now. Try again in a minute.")
        _in_flight.add(source_id)
    try:
        outcome = poll_once(row)
    finally:
        with _in_flight_lock:
            _in_flight.discard(source_id)
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
    # Waits for an answer being written right now, so that once this has
    # returned nothing this source fetched is ever written again. The
    # delete also holds the store's lock: poll_once records how a fetch
    # went by reading the row and writing it back under that lock, and a
    # delete landing between its read and its write was undone by the
    # write -- the source came back, credential and all, about one run
    # in eight of the test that checks it.
    with _source_lock(source_id):
        with STORE._lock:
            STORE._db.delete(SOURCE_KIND, source_id)
    with _in_flight_lock:
        _source_locks.pop(source_id, None)
    write_audit(tenant, operator, "poller.removed", row["name"])
    return None
