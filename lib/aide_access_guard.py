"""Who may call an aide API: this machine and the tailnet, and never another site.

Both aide apps install this guard: ``apps/aide-api/main.py``, which the Mac's
launch agent runs on 0.0.0.0:8080 (always, unless DEMO_MODE=true), and
``scripts/aide-api.py``, which ``scripts/start-aide.sh`` runs. It answers 403 to:

- a caller that is not loopback or a Tailscale address. While Tailscale is up a
  LAN device cannot pose as a tailnet address: the reply to a forged
  100.64.0.10/10 source goes into the WireGuard tunnel;
- a Host header other than an IP literal, localhost or a *.ts.net name, which
  is how a DNS-rebinding page reaches loopback;
- a writing request whose Origin is not one of the app's own pages. CORS does
  not stop a plain HTML form, so this check is what keeps another site's page
  from triggering a write through the browser.

It lives in lib/ because every install ships lib/ and none ships apps/ (see
``scripts/install.sh`` CORE_DIRS and ``lib/grip_starter_kit.py`` SHIP_DIRS).
The Railway demo deploys apps/aide-api alone, so there main.py finds no guard.
It then serves only because DEMO_MODE=true switches the guard off anyway.
"""
from __future__ import annotations

import ipaddress
from typing import Callable, Iterable, Optional

TRUSTED_CLIENT_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    "127.0.0.0/8", "::1/128", "100.64.0.10/10", "fd7a:115c:a1e0::/48"))
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def client_is_trusted(host: Optional[str]) -> bool:
    """True iff ``host`` is a loopback or Tailscale address."""
    try:
        addr = ipaddress.ip_address(host or "")
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return any(addr in net for net in TRUSTED_CLIENT_NETWORKS)


def host_header_is_local(host_header: Optional[str]) -> bool:
    """True iff the Host header names this machine by a name nobody else controls.

    A DNS-rebinding page reaches loopback through a name its author controls,
    and on a LAN that includes any *.local name (anyone can answer mDNS) and
    short names the router resolves. So only IP literals, localhost and *.ts.net
    names (which Tailscale resolves) pass.
    """
    name = (host_header or "").strip().lower()
    if name.startswith("["):
        name = name[1:].split("]", 1)[0]
    elif name.count(":") == 1:
        name = name.split(":", 1)[0]
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return name == "localhost" or name.endswith(".ts.net")


def same_origin(origin: str, host_header: Optional[str]) -> bool:
    """True iff ``origin`` is the API's own scheme://host, such as its /api/docs page."""
    host = (host_header or "").strip().lower()
    return bool(host) and origin.strip().lower() in (f"http://{host}", f"https://{host}")


def refusal(method: str, client: Optional[str], host_header: Optional[str],
            origin: Optional[str], allowed_origins: Iterable[str]) -> Optional[str]:
    """Why a request is refused, or None to serve it.

    A request without an Origin header comes from outside a browser (curl, a
    script), so its address and Host header decide alone.
    """
    if not client_is_trusted(client):
        return "the caller is not this machine or the tailnet"
    if not host_header_is_local(host_header):
        return "the Host header does not name this machine"
    foreign = origin is not None and origin not in set(allowed_origins)
    if method in UNSAFE_METHODS and foreign and not same_origin(origin, host_header):
        return "a write from another site's page"
    return None


def install(app, allowed_origins: Iterable[str], enabled: Callable[[], bool] = lambda: True):
    """Add the guard to a FastAPI ``app``; ``enabled()`` is read on every request."""
    from starlette.responses import JSONResponse

    origins = tuple(allowed_origins)

    @app.middleware("http")
    async def aide_access_guard(request, call_next):
        if enabled():
            client = request.client.host if request.client else None
            reason = refusal(request.method, client, request.headers.get("host"),
                             request.headers.get("origin"), origins)
            if reason:
                return JSONResponse({"detail": f"aide-api refused: {reason}"}, status_code=403)
        return await call_next(request)

    return aide_access_guard
