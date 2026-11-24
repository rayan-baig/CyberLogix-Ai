"""What the app was called before, kept working.

Kryozyn was called CyberLogix AI, at cyberlogix.mtrxhorizyn.com, until
3.34.0. The rename
changed every word a person reads (brand.py holds the names). It did not
change anything a running deployment, a sensor or a partner's integration
already depends on. Three things keep the old names working:

* **Settings.** Every setting keeps its `CYBERLOGIX_` name; the code reads
  those. Each may also be given as `KRYOZYN_` plus the same rest
  (`KRYOZYN_ADMIN_KEY` for `CYBERLOGIX_ADMIN_KEY`). When both are set, the
  `KRYOZYN_` one wins. Applied once, when this module is imported, which
  main.py does before anything else reads a setting.
* **Request headers.** The API's headers are now `X-Kryozyn-Key`,
  `X-Kryozyn-Sensor-Key` and so on. A device or integration set up with
  the old `X-CyberLogix-` names keeps working: each header is seen under
  both names, and when a request sends both, the `X-Kryozyn-` one wins.
* **The old address.** cyberlogix.mtrxhorizyn.com (and its staging copy)
  sends a browser on to the same page at the new address with a 301. Only
  page visits move: anything under `/api/`, and every request that is not
  a GET or HEAD (sensor readings, Twilio's callbacks, Stripe's webhooks),
  is answered where it arrived, so nothing set up against the old address
  breaks. `KRYOZYN_OLD_HOSTS` changes the list (`old=new,old2=new2`); set
  it empty to switch the redirect off.
"""

from __future__ import annotations

import os
from typing import Dict, Iterable, List, Mapping, MutableMapping, Tuple
from urllib.parse import quote

OLD_PREFIX = "CYBERLOGIX_"
NEW_PREFIX = "KRYOZYN_"


def apply_env_aliases(env: MutableMapping[str, str] = os.environ) -> List[str]:
    """Copy each KRYOZYN_* setting onto its CYBERLOGIX_* name.

    The new name wins when both are set. Returns the old names written.
    """
    written = []
    for name, value in list(env.items()):
        if name.startswith(NEW_PREFIX) and len(name) > len(NEW_PREFIX):
            old = OLD_PREFIX + name[len(NEW_PREFIX):]
            env[old] = value
            written.append(old)
    return written


# Before brand is imported, so a KRYOZYN_ name for one of its settings
# counts too.
apply_env_aliases()

import brand  # noqa: E402  (after the settings above, on purpose)


# ---- request headers -------------------------------------------------------

_OLD_HEADER = b"x-cyberlogix-"
_NEW_HEADER = b"x-kryozyn-"


def alias_headers(headers: Iterable[Tuple[bytes, bytes]]) -> List[Tuple[bytes, bytes]]:
    """Every X-CyberLogix-*/X-Kryozyn-* header under both names, new winning."""
    kept: List[Tuple[bytes, bytes]] = []
    old: Dict[bytes, bytes] = {}
    new: Dict[bytes, bytes] = {}
    for name, value in headers:
        if name.startswith(_NEW_HEADER):
            new.setdefault(name[len(_NEW_HEADER):], value)
        elif name.startswith(_OLD_HEADER):
            old.setdefault(name[len(_OLD_HEADER):], value)
        else:
            kept.append((name, value))
    for rest in list(old) + [r for r in new if r not in old]:
        value = new.get(rest, old.get(rest, b""))
        kept.append((_NEW_HEADER + rest, value))
        kept.append((_OLD_HEADER + rest, value))
    return kept


class HeaderAliases:
    """ASGI middleware: the old X-CyberLogix-* request headers keep working."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            names = [name for name, _ in scope.get("headers", ())]
            if any(n.startswith(_OLD_HEADER) or n.startswith(_NEW_HEADER) for n in names):
                # In place, not a copy: what the router records on the scope
                # (the matched route) stays visible to the layers outside.
                scope["headers"] = alias_headers(scope["headers"])
        await self.app(scope, receive, send)


# ---- the old address -------------------------------------------------------

OLD_HOST = "cyberlogix.mtrxhorizyn.com"
OLD_STAGING_HOST = "staging." + OLD_HOST
DEFAULT_OLD_HOSTS = {OLD_HOST: brand.APP_HOST, OLD_STAGING_HOST: brand.STAGING_HOST}


def parse_old_hosts(raw: str | None) -> Dict[str, str]:
    """`old=new,old2=new2` -> {old: new}. None (unset) gives the defaults."""
    if raw is None:
        return dict(DEFAULT_OLD_HOSTS)
    found: Dict[str, str] = {}
    for pair in raw.split(","):
        old, sep, new = pair.partition("=")
        old, new = old.strip().lower().rstrip("."), new.strip().lower().rstrip(".")
        if sep and old and new and old != new:
            found[old] = new
    return found


def old_hosts(env: Mapping[str, str] = os.environ) -> Dict[str, str]:
    raw = env.get(NEW_PREFIX + "OLD_HOSTS")
    if raw is None:
        raw = env.get(OLD_PREFIX + "OLD_HOSTS")
    return parse_old_hosts(raw)


def _host_of(scope) -> str:
    for name, value in scope.get("headers", ()):
        if name == b"host":
            host = value.decode("latin-1").strip().lower()
            if host.startswith("["):  # an IPv6 literal, kept whole
                return host.split("]")[0] + "]"
            return host.split(":")[0].rstrip(".")
    return ""


def moves(method: str, path: str) -> bool:
    """Whether a request to an old host is sent on (True) or answered (False)."""
    if method not in ("GET", "HEAD"):
        return False
    return not (path == "/api" or path.startswith("/api/"))


class OldHostRedirect:
    """ASGI middleware: a page asked for at an old host moves, with a 301."""

    def __init__(self, app, hosts: Dict[str, str] | None = None):
        self.app = app
        self.hosts = old_hosts() if hosts is None else hosts

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.hosts:
            target = self.hosts.get(_host_of(scope))
            path = scope.get("path") or "/"
            if target and moves(scope.get("method", "GET"), path):
                location = "https://" + target + quote(path, safe="/%:@!$&'()*+,;=-._~")
                query = scope.get("query_string") or b""
                if query:
                    location += "?" + query.decode("latin-1")
                await send({
                    "type": "http.response.start",
                    "status": 301,
                    "headers": [
                        (b"location", location.encode("latin-1")),
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"cache-control", b"public, max-age=86400"),
                        (b"content-length", b"0"),
                    ],
                })
                await send({"type": "http.response.body", "body": b""})
                return
        await self.app(scope, receive, send)
