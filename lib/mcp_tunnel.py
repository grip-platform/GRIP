#!/usr/bin/env python3
"""lib/mcp_tunnel.py -- the GRIP MCP tunnel kit (``bin/grip-tunnel``).

Builds and checks a local Docker Compose deployment of Anthropic's MCP tunnel
stack (the proxy + cloudflared), per the vendor's quickstart and hardened
Compose deploy guide (``vendor-docs/platform-quickstart.md``,
``vendor-docs/platform-deploy-compose.md``, read 2026-11-05). Manual
credential provisioning only (no Workload Identity Federation, no ``setup``
service): the tunnel token is a file the operator pastes once, never an
argument and never a tracked path.

Subcommands, each a thin CLI wrapper over a pure function below:

- ``scaffold --dir DIR --domain D``   write the compose file, proxy config and
  the nonce-probe server; refuses to overwrite an existing deployment.
- ``certs --dir DIR``                 a CA and a server certificate via
  ``openssl``, meeting the vendor's certificate requirements.
- ``doctor --dir DIR``                offline checks, each with a rule id
  (``DoctorCheck``); exit 0 clean, 1 findings, 3 could not check. The rule
  table and the count live in ``docs/mcp-tunnel-first-contact.md``.
- ``nonce new`` / ``nonce verify``    a fresh nonce signed by the deployed
  probe's ``first_contact`` tool, verified locally against the key in
  ``data/``. Proves the path works to the operator who holds that key; proves
  nothing to a stranger. ``verify`` accepts the reply as bare JSON or inside
  the prose and code fence a session wraps it in.
- ``up`` / ``down`` / ``status`` / ``stop``   thin ``docker compose``
  wrappers. ``stop`` is the breach brake (``down --timeout 0``); ``down`` is
  the graceful teardown. ``stop --seal-home H`` adds the seal count of a
  ``grasp-mcp-seal`` home to the brake's card.
- ``weather --dir DIR [--seal-home H]``   one plain line: tunnel up or down,
  the certificate's days left, and the seal count when a home is given.
- ``cert-status --dir DIR`` / ``cert-renew --dir DIR``   the manual
  certificate-expiry check and renewal, also wired into ``lib/grip_tick.py``
  for every *registered* deployment (the directories ``scaffold`` has run
  against), reported once when a certificate is within 30 days of expiry.
  ``cert-status`` exits 0 with time left, 1 near or past expiry or unreadable,
  and 3 when there is no certificate to check.
- ``vendor-check``                    diffs the vendor's published
  ``status``/``zdr.eligibility`` against ``config/mcp-tunnel/vendor-baseline.json``.
  Exit 0 unchanged, 1 changed, 3 unreachable.

Capability lock (``rules/untrusted-content-capability-lock.md``): the only
route ``scaffold`` ever wires is the nonce probe. Routing a real MCP server
with personal or client data through this kit is a documented operator
decision, never this module's default -- MCP tunnels carry no zero-data-
retention cover (``vendor-docs/platform-overview.md``).

The generated probe container shares the proxy container's network namespace
(``network_mode: service:mcp-proxy``, the same trick the vendor's own compose
samples use for cloudflared) so the proxy reaches it over ``localhost`` and
the probe binds ``127.0.0.1`` -- never a wildcard bind, and never reachable
from the compose bridge network, the host, or the internet.

Every function below is a plain data transform or a single ``subprocess``/
``openssl`` call; nothing here is wired to a record writer, so this module's
top-level imports are deliberately stdlib-only -- ``yaml`` and
``cryptography`` are imported INSIDE the functions that use them, so a module
that imports this one (``lib/grip_tick.py``) stays safe to import under any
interpreter, pre-reexec (``rules/never-system-python-for-grip.md``).
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
from atomic_write import atomic_write  # noqa: E402

CLAUDE_DIR = get_claude_dir()

# --- vendor-pinned image digests, verbatim from vendor-docs/platform-quickstart.md -----
MCP_PROXY_IMAGE = (
    "us-docker.pkg.dev/anthropic-public-registry/images/mcp-proxy"
    "@sha256:d243d30642d39857a0579e465cb3b9bf3ae7c1141e83143bb6856777a31467a5"
)
CLOUDFLARED_IMAGE = (
    "cloudflare/cloudflared"
    "@sha256:0e56c13c7058fcef9c4f0e7d83372f82330548dbf8f8b366f3d8b506526d93b2"
)
# The probe's own base image -- not a vendor artefact like the two above, but a
# floating tag has the identical exposure: whatever registry-1.docker.io serves
# for this tag at `up` time is the probe's whole runtime, sharing the proxy's
# network namespace. Pinned to the manifest-list digest for `python:3.13-slim`,
# fetched from registry-1.docker.io (library/python) on 2026-11-05.
PROBE_BASE_IMAGE = (
    "python"
    "@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc"
)
# The two packages the probe imports. Pinning only these left every package they
# pull in to a fresh resolver at each container start (Codex,
# lib/mcp_tunnel.py:317), so the probe installs from PROBE_LOCK_SOURCE instead: every
# package in the set, by version and by SHA-256, with pip's --require-hashes
# refusing anything else. The lock's own header holds the command that rebuilds it
# when either pin below changes; tests/test_mcp_tunnel.py checks that it pins these.
PROBE_PIP_MCP = "mcp==2.3.0"
PROBE_PIP_CRYPTOGRAPHY = "cryptography==50.0.2"
PROBE_LOCK_SOURCE = _LIB.parent / "config" / "mcp-tunnel" / "probe-requirements.txt"
COMPOSE_TIMEOUT_S = 60
PROXY_PORT = 8080
PROBE_PORT = 9100
PROBE_ROUTE_NAME = "probe"  # resolves to https://probe.<tunnel-domain>/mcp (docs/mcp-tunnel-first-contact.md)
NONCE_TTL_S = 600
CERT_WARN_DAYS = 30
RFC1918_NETS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
CGNAT_NET = ipaddress.ip_network("100.64.0.10/10")  # RFC 6598 -- NOT RFC1918; e.g. Tailscale's own address range
VENDOR_OVERVIEW_URL = "https://platform.claude.com/docs/en/agents-and-tools/mcp-tunnels/overview.md"


class TunnelKitError(RuntimeError):
    """Base for every error this module raises on purpose (caught at the CLI layer)."""


class ScaffoldExists(TunnelKitError):
    """``scaffold`` refuses to overwrite an existing deployment."""


class CertsExist(TunnelKitError):
    """``certs`` refuses to replace an existing CA (use ``cert-renew`` for the server cert)."""


# ----------------------------------------------------------------------------- paths

def _config_path(dir_: Path) -> Path:
    return dir_ / "config" / "mcp-proxy.yaml"


def _compose_path(dir_: Path) -> Path:
    return dir_ / "docker-compose.yaml"


def _data_dir(dir_: Path) -> Path:
    return dir_ / "data"


def _ca_key(dir_: Path) -> Path:
    return _data_dir(dir_) / "ca.key"


def _ca_crt(dir_: Path) -> Path:
    return _data_dir(dir_) / "ca.crt"


def _tls_key(dir_: Path) -> Path:
    return _data_dir(dir_) / "tls.key"


def _tls_crt(dir_: Path) -> Path:
    return _data_dir(dir_) / "tls.crt"


def _token_path(dir_: Path) -> Path:
    return _data_dir(dir_) / "tunnel-token"


def _probe_key_path(dir_: Path) -> Path:
    return _data_dir(dir_) / "probe-ed25519-private.key"


def _nonce_pending_path(dir_: Path) -> Path:
    return _data_dir(dir_) / "nonce-pending.json"


def _probe_script_path(dir_: Path) -> Path:
    return dir_ / "mcp_tunnel_probe.py"


def _probe_lock_path(dir_: Path) -> Path:
    return dir_ / "probe-requirements.txt"


def registry_path() -> Path:
    """Every directory ``scaffold`` has written a deployment into; read by ``lib/grip_tick.py``."""
    return CLAUDE_DIR / "state" / "mcp-tunnel" / "deployments.json"


def vendor_baseline_path() -> Path:
    return CLAUDE_DIR / "config" / "mcp-tunnel" / "vendor-baseline.json"


# ----------------------------------------------------------------------------- small IO helpers

def _read_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_secure_text(path: Path, text: str) -> None:
    """Atomic, mode-0600 write; used for the probe's private key.

    Delegates to the shared ``atomic_write`` (``lib/atomic_write.py``) rather
    than hand-rolling the identical temp+rename dance a second time:
    ``tempfile.mkstemp`` already creates at 0600 independent of umask (the
    same guarantee this function's own docstring promises -- verified
    empirically, not merely asserted), and ``atomic_write`` additionally
    ``fsync``s before the rename, a durability property this hand-rolled
    version lacked."""
    atomic_write(path, text)


def _load_yaml(path: Path) -> dict:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _load_yaml_safe(path: Path) -> Tuple[dict, Optional[str]]:
    import yaml

    try:
        return _load_yaml(path), None
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return {}, f"cannot read {path}: {type(exc).__name__}: {exc}"


# ----------------------------------------------------------------------------- scaffold

# RFC 1123 hostname label: 1-63 chars, alphanumeric, interior hyphens only --
# excludes by construction every character openssl's ``-subj`` or a SAN
# extfile line would read as a field separator (``/``, ``=``, a comma) or a
# new line (``\n``). At least two labels (a dot), since the vendor always
# hands out a multi-label subdomain (``<id>.tunnel.anthropic.com``).
_DNS_LABEL = r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_DNS_NAME_RE = re.compile(rf"^{_DNS_LABEL}(\.{_DNS_LABEL})+$")


def _validate_tunnel_domain(domain: str) -> str:
    """The CLI boundary for the operator-typed tunnel domain: a DNS name and
    nothing else. ``certs`` later writes this verbatim into openssl's
    ``-subj`` and a SAN extfile (``_server_ext_file``), so anything that is
    not a plain hostname -- ``/CN=``, a newline, a comma -- is refused here,
    before ``scaffold`` creates anything."""
    if len(domain) > 253 or not _DNS_NAME_RE.match(domain):
        raise TunnelKitError(f"tunnel domain {domain!r} is not a valid DNS name")
    return domain


def _proxy_config(domain: str) -> dict:
    return {
        "listen_addr": f":{PROXY_PORT}",
        "log_level": "info",
        "shutdown_timeout": "30s",
        "tunnel_domain": domain,
        "tls": {"cert_file": "/data/tls.crt", "key_file": "/data/tls.key"},
        # The probe shares the proxy's network namespace (see _compose_services), so the
        # proxy reaches it over its own loopback -- never a service DNS name. ``localhost``
        # resolves to 127.0.0.1, which the proxy's RFC1918-only default `allowed_ips` does
        # NOT cover (vendor-docs/platform-troubleshooting.md "Upstream IP validation"): first
        # contact would otherwise fail with "IP validation failed". Setting `allowed_ips`
        # REPLACES that default rather than extending it, so the vendor's own documented fix
        # restates the three RFC1918 ranges alongside the loopback range it adds.
        "routes": {PROBE_ROUTE_NAME: f"http://localhost:{PROBE_PORT}"},
        "upstream": {"allowed_ips": list(RFC1918_NETS) + ["127.0.0.0/8"]},
    }


def _hardened_defaults() -> dict:
    """The non-root/read-only/no-capability shape every vendor container gets
    (``vendor-docs/platform-deploy-compose.md``)."""
    return {
        "restart": "unless-stopped",
        "user": "65532:65532",
        "read_only": True,
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": ["ALL"],
        "stop_grace_period": "30s",
        "logging": {"options": {"max-size": "10m", "max-file": "3"}},
    }


def _proxy_service() -> dict:
    return {
        "image": MCP_PROXY_IMAGE,
        "volumes": ["./config/mcp-proxy.yaml:/etc/mcp-gateway/config.yaml:ro", "./data:/data:ro"],
        **_hardened_defaults(),
    }


def _cloudflared_service() -> dict:
    return {
        "image": CLOUDFLARED_IMAGE,
        "command": f"tunnel --no-autoupdate run --url http://localhost:{PROXY_PORT}",
        "environment": ["TUNNEL_TOKEN"],
        "network_mode": "service:mcp-proxy",
        **_hardened_defaults(),
    }


def _probe_service() -> dict:
    """The nonce probe is this kit's own addition -- the vendor's hardened shape
    above has no opinion on it, so nothing hardens it by inheritance. Not
    ``user``/``read_only`` like its siblings: it still writes its pip-installed
    packages into its own root filesystem at boot. Everything it runs is pinned:
    the base image by digest (PROBE_BASE_IMAGE, the same mechanism as the two
    vendor images above), and every Python package by version and SHA-256 through
    the hash lock (PROBE_LOCK_SOURCE), because an unpinned pull or install at
    every boot is live supply-chain exposure inside a container sharing the
    proxy's network namespace."""
    return {
        "image": PROBE_BASE_IMAGE,
        "working_dir": "/app",
        # Only the ONE file the probe's own script reads (_KEY_PATH below), never the
        # whole data/ directory: that folder also holds tunnel-token, ca.key and
        # tls.key, and a compromised probe (or a compromised runtime pip dependency)
        # mounted at ``./data:/data:ro`` could still READ and exfiltrate all three --
        # ``:ro`` stops writes, not reads (Codex, lib/mcp_tunnel.py:302).
        "volumes": [
            "./mcp_tunnel_probe.py:/app/mcp_tunnel_probe.py:ro",
            "./probe-requirements.txt:/app/probe-requirements.txt:ro",
            "./data/probe-ed25519-private.key:/data/probe-ed25519-private.key:ro",
        ],
        # --no-deps: the lock already lists every package, so pip resolves nothing.
        "command": ("sh -c \"pip install --quiet --no-deps --require-hashes -r probe-requirements.txt"
                    " && python mcp_tunnel_probe.py\""),
        # Shares the proxy's netns: reachable at localhost:9100 from inside it, from
        # nowhere else -- not the bridge network, not the host, not the internet.
        "network_mode": "service:mcp-proxy",
        "restart": "unless-stopped",
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": ["ALL"],
    }


def _compose_services() -> dict:
    """The three deployed containers: the two vendor images, hardened per
    ``vendor-docs/platform-deploy-compose.md``, and this kit's own nonce probe,
    which joins the proxy's network namespace exactly as cloudflared does (the
    vendor's own ``network_mode: "service:mcp-proxy"`` pattern), so it is reachable
    only from inside that shared namespace -- never from the compose bridge
    network, the host, or the internet."""
    return {"services": {
        "mcp-proxy": _proxy_service(),
        "cloudflared": _cloudflared_service(),
        "mcp-tunnel-probe": _probe_service(),
    }}


def _probe_script_source() -> str:
    """The nonce probe, generated verbatim into every deployment. Runs in its own
    container with its own ``pip install``; its imports are irrelevant to this
    module's own interpreter-safety.

    Written for the ``mcp`` 2.x API the lock pins. Until 2026-11-05 it imported
    ``mcp.server.fastmcp``, which 2.x removed, so the probe could not have started;
    ``tests/test_mcp_tunnel.py`` now runs this source end to end."""
    return f'''#!/usr/bin/env python3
"""Generated by ``grip-tunnel scaffold`` -- the MCP tunnel kit's nonce probe.

The ONLY route this kit wires by default (rules/untrusted-content-capability-lock.md):
one tool, first_contact(nonce), that signs a nonce with the Ed25519 private key at
/data/probe-ed25519-private.key, so "grip-tunnel nonce verify" can confirm the path
works end to end. It proves the path works to the operator who holds that key; it
proves nothing to a stranger, and carries no personal or client data.

This container shares the proxy container's network namespace (docker-compose.yaml:
network_mode "service:mcp-proxy", the same pattern the proxy's own cloudflared
service uses). Binding the loopback address is therefore correct, not permissive:
only the proxy (and cloudflared, which shares that same namespace) can reach this
port. Nothing on the compose bridge network, the host, or the internet can.
"""
from datetime import datetime, timezone

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

mcp = MCPServer("grip-tunnel-probe")

_KEY_PATH = "/data/probe-ed25519-private.key"

# The SDK checks the Host header of any server bound to a loopback address, to stop
# a browser's DNS-rebinding attack. No browser can reach this namespace, and the
# vendor does not document whether the proxy forwards the public Host header
# (probe.<tunnel-domain>) or rewrites it; the check would refuse the first. So it is
# off here, where the bind already limits who can connect.
_NO_HOST_CHECK = TransportSecuritySettings(enable_dns_rebinding_protection=False)


@mcp.tool()
def first_contact(nonce: str) -> dict:
    """Sign a nonce printed by `grip-tunnel nonce new`, proving this path is live."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    with open(_KEY_PATH, "r", encoding="utf-8") as fh:
        priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(fh.read().strip()))
    signed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = f"{{nonce}}|{{signed_at}}".encode("utf-8")
    signature = priv.sign(payload).hex()
    public_key = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    ).hex()
    return {{"nonce": nonce, "signed_at": signed_at, "signature": signature, "public_key": public_key}}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port={PROBE_PORT},
            transport_security=_NO_HOST_CHECK)
'''


def _ensure_probe_keypair(dir_: Path) -> None:
    path = _probe_key_path(dir_)
    if path.exists():
        return
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    _write_secure_text(path, raw.hex())


def _register_deployment(dir_: Path) -> None:
    """Adds ``dir_`` and drops every entry whose directory no longer exists: a deleted
    deployment has no certificate left to warn about, and without the drop the list
    only grew while the tick read all of it every five minutes (AGORA, 2026-11-05)."""
    path = registry_path()
    entries = _read_json(path)
    entries = entries if isinstance(entries, list) else []
    resolved = str(Path(dir_).resolve())
    kept = [e for e in entries if isinstance(e, str) and (e == resolved or Path(e).is_dir())]
    if resolved not in kept:
        kept.append(resolved)
    if kept != entries:
        # A plain write_text can leave this file 0-byte or truncated if the
        # process dies mid-write; atomic_write's temp-file+rename never can.
        atomic_write(path, json.dumps(kept))


def registered_deployments(registry: Optional[Path] = None) -> List[Path]:
    """The deployment directories listed in ``registry``, by default registry_path()."""
    entries = _read_json(registry or registry_path())
    return [Path(e) for e in (entries or []) if isinstance(e, str)]


def scaffold(dir_: Path, domain: str) -> dict:
    """Write the compose file, proxy config and probe script; refuses to overwrite.

    The tunnel token is never written here -- the operator pastes it into
    ``data/tunnel-token`` themselves, and ``up`` reads it from that file.

    Not transactional at the filesystem level (no single operation covers a
    whole directory tree), so a failure partway -- most plausibly keypair
    generation, if ``cryptography`` is missing -- removes every FILE this
    call wrote (never the ``config``/``data`` directories themselves: an
    empty leftover directory is harmless, the next ``mkdir(exist_ok=True)``
    is a no-op over it, mirroring ``certs``'s own rollback below). Without
    this, a half-written dir would make ``ScaffoldExists`` refuse a retry
    forever, over a deployment that never actually finished.
    """
    import yaml

    _validate_tunnel_domain(domain)
    dir_ = Path(dir_)
    compose_path, config_path = _compose_path(dir_), _config_path(dir_)
    outputs = [config_path, compose_path, _probe_script_path(dir_), _probe_key_path(dir_), _probe_lock_path(dir_)]
    # Every file this call writes, not two of them: a leftover probe script or key used
    # to be overwritten or reused, then deleted by the rollback below (Codex,
    # lib/mcp_tunnel.py:446). With all of them refused here, the rollback can only
    # ever remove files this call created.
    existing = [str(p) for p in outputs if p.exists()]
    if existing:
        raise ScaffoldExists(f"refusing to overwrite an existing deployment at {dir_} (found {', '.join(existing)})")
    try:
        _data_dir(dir_).mkdir(parents=True, exist_ok=True)
        _allow_proxy_traversal(_data_dir(dir_))
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.safe_dump(_proxy_config(domain), sort_keys=False), encoding="utf-8")
        os.chmod(config_path, 0o644)  # read by the proxy's UID 65532 through a file bind mount, whatever the umask
        compose_path.write_text(yaml.safe_dump(_compose_services(), sort_keys=False), encoding="utf-8")
        _probe_script_path(dir_).write_text(_probe_script_source(), encoding="utf-8")
        _probe_lock_path(dir_).write_text(PROBE_LOCK_SOURCE.read_text(encoding="utf-8"), encoding="utf-8")
        _ensure_probe_keypair(dir_)
        _register_deployment(dir_)
    except Exception:
        _remove_if_exists(outputs)
        raise
    return {"dir": str(dir_), "domain": domain, "config": str(config_path), "compose": str(compose_path)}


def _allow_proxy_traversal(path: Path) -> None:
    """The proxy runs as UID 65532 and opens ``/data/tls.*`` through the ``./data``
    bind mount, so it needs search (x) permission on the directory. ``mkdir`` takes
    its mode from the umask, and under ``umask 077`` the directory is 0700, which the
    proxy cannot enter (Codex, lib/mcp_tunnel.py:450). This adds x for other users
    and nothing else: nobody gains the right to list the directory, and the secrets
    inside stay 0600."""
    mode = stat.S_IMODE(path.stat().st_mode)
    if not mode & 0o001:
        os.chmod(path, mode | 0o001)


# ----------------------------------------------------------------------------- certs

def _read_domain(dir_: Path) -> str:
    try:
        cfg = _load_yaml(_config_path(dir_))
    except OSError as exc:
        raise TunnelKitError(f"{_config_path(dir_)} is not readable ({exc}); run scaffold first") from exc
    domain = cfg.get("tunnel_domain")
    if not domain:
        raise TunnelKitError(f"{_config_path(dir_)} has no tunnel_domain; run scaffold first")
    return str(domain)


def _run_openssl(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["openssl"] + args, capture_output=True, text=True, timeout=30)


def _check_openssl(proc: subprocess.CompletedProcess, step: str) -> None:
    if proc.returncode != 0:
        raise TunnelKitError(f"openssl {step} failed: {proc.stderr.strip()[:500]}")


def _gen_ca(ca_key: Path, ca_crt: Path) -> None:
    """A self-signed CA: RSA 2048, CA:TRUE critical, keyCertSign+cRLSign, SKI -- the
    certificate-requirements shape from vendor-docs/platform-reference.md."""
    proc = _run_openssl([
        "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(ca_key), "-out", str(ca_crt),
        "-days", "3650", "-subj", "/CN=mcp-tunnel-ca",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-addext", "subjectKeyIdentifier=hash",
    ])
    _check_openssl(proc, "CA generation")
    os.chmod(ca_key, 0o600)


def _server_ext_file(domain: str, tmp_dir: Path) -> Path:
    ext = tmp_dir / "tls.ext"
    ext.write_text(
        f"subjectAltName = DNS:{domain},DNS:*.{domain}\n"
        "authorityKeyIdentifier = keyid,issuer\n"
        "extendedKeyUsage = serverAuth\n",
        encoding="utf-8",
    )
    return ext


def _sign_server_cert(domain: str, ca_key: Path, ca_crt: Path, csr: Path, tls_crt: Path, ext: Path, days: int) -> None:
    proc = _run_openssl([
        "x509", "-req", "-in", str(csr),
        "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial",
        "-out", str(tls_crt), "-days", str(days), "-extfile", str(ext),
    ])
    _check_openssl(proc, "server certificate signing")


def _remove_if_exists(paths: List[Path]) -> None:
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass  # best-effort cleanup; the original error is still the one that propagates


def _generate_ca_and_server_cert(domain: str, ca_key: Path, ca_crt: Path, tls_key: Path, tls_crt: Path, days: int) -> None:
    _gen_ca(ca_key, ca_crt)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        ext = _server_ext_file(domain, tmp_dir)
        csr = tmp_dir / "server.csr"
        proc = _run_openssl(["req", "-newkey", "rsa:2048", "-nodes",
                             "-keyout", str(tls_key), "-out", str(csr), "-subj", f"/CN={domain}"])
        _check_openssl(proc, "server key/CSR generation")
        _sign_server_cert(domain, ca_key, ca_crt, csr, tls_crt, ext, days)


def certs(dir_: Path, *, days: int = 90) -> dict:
    """A CA and a fresh server keypair+cert, SAN'd for ``*.<tunnel_domain>``.

    Refuses to replace an existing CA -- a new CA must be re-registered on the
    tunnel in the Console, which ``cert-renew`` (same key, new cert) avoids.

    A failure partway (CA written, server cert not yet signed, or vice versa)
    removes every file this call could have created, so a retry is not blocked by
    ``CertsExist`` seeing a CA the operator never asked it to keep.
    """
    dir_ = Path(dir_)
    domain = _read_domain(dir_)
    ca_key, ca_crt = _ca_key(dir_), _ca_crt(dir_)
    if ca_key.exists() or ca_crt.exists():
        raise CertsExist(f"a CA already exists at {_data_dir(dir_)}; use cert-renew to reissue the server certificate")
    _data_dir(dir_).mkdir(parents=True, exist_ok=True)
    tls_key, tls_crt = _tls_key(dir_), _tls_crt(dir_)
    try:
        _generate_ca_and_server_cert(domain, ca_key, ca_crt, tls_key, tls_crt, days)
    except Exception:
        # ANY exception, not only TunnelKitError (Codex, lib/mcp_tunnel.py:545):
        # a hung `openssl` raises subprocess.TimeoutExpired, a filesystem problem
        # raises OSError, and neither is a TunnelKitError -- so the CA this call
        # already wrote was left behind with no server cert to match it, and
        # every retry then hit CertsExist with no way to tell a half-finished
        # attempt from a real one. Mirrors scaffold()'s own `except Exception`
        # cleanup above, for the identical reason.
        _remove_if_exists([ca_key, ca_crt, tls_key, tls_crt])
        raise
    # Both files are read by the SAME non-root proxy container, through the SAME
    # read-only bind mount, for the SAME reason -- but openssl's `-out` only gets
    # 0644 by accident of the CALLING process's umask. Measured: under `umask 077`
    # openssl writes 0600, which UID 65532 cannot read, and no doctor check (not
    # even `key-world-readable`, which only ever looks at `ca.key`) would catch
    # it before `up` fails to serve TLS. Explicit, not umask-dependent, for both.
    os.chmod(tls_key, 0o644)
    os.chmod(tls_crt, 0o644)
    return {"ca_key": str(ca_key), "ca_crt": str(ca_crt), "tls_key": str(tls_key), "tls_crt": str(tls_crt), "days": days}


def cert_renew(dir_: Path, *, days: int = 90) -> dict:
    """A new server certificate from the SAME ``tls.key`` and the stored CA (no API call).

    openssl writes to a staged file beside ``tls.crt``, which replaces it only once
    signing has succeeded. Signing straight into ``tls.crt`` left it EMPTY when the CA
    key was unusable (measured: openssl opens its ``-out`` file before it fails), which
    broke a running deployment on a failed maintenance step (Codex, lib/mcp_tunnel.py:592).
    The staged file sits in ``data/`` itself, so the replace is a same-volume rename."""
    dir_ = Path(dir_)
    domain = _read_domain(dir_)
    ca_key, ca_crt, tls_key, tls_crt = _ca_key(dir_), _ca_crt(dir_), _tls_key(dir_), _tls_crt(dir_)
    for required in (ca_key, ca_crt, tls_key):
        if not required.exists():
            raise TunnelKitError(f"{required} is missing; run certs first")
    staged = tls_crt.with_name(tls_crt.name + ".renewing")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            ext = _server_ext_file(domain, tmp_dir)
            csr = tmp_dir / "server.csr"
            proc = _run_openssl(["req", "-new", "-key", str(tls_key), "-out", str(csr), "-subj", f"/CN={domain}"])
            _check_openssl(proc, "renewal CSR generation")
            _sign_server_cert(domain, ca_key, ca_crt, csr, staged, ext, days)
        os.chmod(staged, 0o644)  # same umask-independence as `certs` above; a renewal must not regress it
        os.replace(staged, tls_crt)
    finally:
        _remove_if_exists([staged])
    return {"tls_crt": str(tls_crt), "days": days}


# ----------------------------------------------------------------------------- doctor

@dataclass
class DoctorCheck:
    rule: str
    # "ok" | "finding" | "unknown" | "not-checked". "not-checked" is distinct
    # from both: unlike "ok" it does not claim the thing was verified, and
    # unlike "unknown" (a file that should be readable and is not) it does
    # not raise the exit code -- a hostname route is the normal case, not a
    # failure to check. See ``_check_ip_coverage``.
    status: str
    message: str


@dataclass
class DoctorReport:
    checks: List[DoctorCheck] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        statuses = {c.status for c in self.checks}
        if "unknown" in statuses:
            return 3  # unknown is never reported as clean
        if "finding" in statuses:
            return 1
        return 0  # "ok" and "not-checked" both exit clean; only a real finding or an unknown raises it


def _check_one_route(name: str, value: str) -> List[DoctorCheck]:
    """Three rule ids from one route value: ``scheme://host:port`` and nothing after it."""
    if "://" not in value:
        return [DoctorCheck("route-format", "finding", f"route {name!r} ({value!r}) is not scheme://host:port")]
    scheme, _, rest = value.partition("://")
    out = []
    hostport, _, path = rest.partition("/")
    host = hostport.rpartition(":")[0] if ":" in hostport else hostport
    if not scheme or not rest or not host:
        out.append(DoctorCheck("route-format", "finding", f"route {name!r} ({value!r}) is missing a scheme or host"))
    else:
        out.append(DoctorCheck("route-format", "ok", f"route {name!r} has a scheme and host"))
    if path:
        out.append(DoctorCheck("route-no-path", "finding", f"route {name!r} includes a path ({value!r}); the proxy rejects this at load"))
    else:
        out.append(DoctorCheck("route-no-path", "ok", f"route {name!r} has no path"))
    _, colon, port = hostport.rpartition(":")
    if not colon or not port.isdigit():
        out.append(DoctorCheck("route-port", "finding", f"route {name!r} ({value!r}) has no numeric port"))
    else:
        out.append(DoctorCheck("route-port", "ok", f"route {name!r} has a port"))
    return out


def _check_routes(cfg: dict) -> List[DoctorCheck]:
    routes = cfg.get("routes")
    if not isinstance(routes, dict):
        return [DoctorCheck("routes-is-map", "finding", "routes must be a map of subdomain to upstream URL, not a list")]
    out = [DoctorCheck("routes-is-map", "ok", "routes is a map")]
    for name, value in routes.items():
        out += _check_one_route(str(name), str(value))
    return out


def _check_tunnel_domain(cfg: dict) -> List[DoctorCheck]:
    domain = cfg.get("tunnel_domain")
    if domain:
        return [DoctorCheck("tunnel-domain-present", "ok", f"tunnel_domain is {domain!r}")]
    return [DoctorCheck("tunnel-domain-present", "finding", "tunnel_domain is missing or empty")]


def _route_hosts(cfg: dict) -> List[str]:
    hosts = []
    routes = cfg.get("routes")
    if not isinstance(routes, dict):
        return hosts  # ``_check_routes`` already reports ``routes-is-map``; nothing sensible to check here
    for value in routes.values():
        _, _, rest = str(value).partition("://")
        hostport, _, _ = rest.partition("/")
        host, _, _ = hostport.rpartition(":")
        if host:
            hosts.append(host)
    return hosts


def _as_map(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _upstream_section(cfg: dict) -> dict:
    """``upstream`` when it is a map, else an empty one; ``upstream-is-map`` reports
    any other shape. Every reader goes through this, because ``.get`` on a string or
    a list raised AttributeError inside doctor (Codex, lib/mcp_tunnel.py:735)."""
    return _as_map(cfg.get("upstream"))


def _check_upstream_is_map(cfg: dict) -> List[DoctorCheck]:
    upstream = cfg.get("upstream")
    if upstream is None or isinstance(upstream, dict):
        return [DoctorCheck("upstream-is-map", "ok", "upstream is a map, or absent so the proxy's defaults apply")]
    return [DoctorCheck(
        "upstream-is-map", "finding",
        f"upstream must be a map (allowed_ips, disable_ip_validation, tls), not "
        f"{type(upstream).__name__} ({upstream!r}); doctor checked the routes against the proxy's defaults instead",
    )]


def _parse_cidrs(cidrs: List[Any]) -> Tuple[List[ipaddress.IPv4Network], List[str]]:
    """Every value that fails to parse is reported back, never just dropped. IPv4
    only: the proxy takes IPv4 CIDRs or addresses and connects upstream over IPv4
    alone (vendor-docs/platform-reference.md), so an IPv6 value that parses cleanly
    is still one the proxy never enforces (Codex, lib/mcp_tunnel.py:687)."""
    nets, bad = [], []
    for cidr in cidrs:
        try:
            net = ipaddress.ip_network(str(cidr), strict=False)
        except ValueError:
            bad.append(str(cidr))
            continue
        if net.version == 4:
            nets.append(net)
        else:
            bad.append(str(cidr))
    return nets, bad


def _effective_allowed_nets(cfg: dict) -> List[ipaddress.IPv4Network]:
    raw = _upstream_section(cfg).get("allowed_ips")
    cidrs = raw if isinstance(raw, list) and raw else list(RFC1918_NETS)
    nets, _ = _parse_cidrs(cidrs)
    return nets


# "localhost" is not an arbitrary hostname needing a runtime DNS lookup like any
# other route host -- RFC 6761 reserves it to always mean the loopback address,
# so it is checkable OFFLINE, same as a literal IP. Without this, the doctor
# reported the default scaffolded route (``http://localhost:<port>``) as
# honestly "not-checked" right up until `up` actually started the stack and the
# proxy rejected its own probe with "IP validation failed" -- the exact trap
# Codex named at lib/mcp_tunnel.py:252.
_LOOPBACK_HOSTNAME_ADDRESS = {"localhost": "127.0.0.1"}


def _check_ip_coverage(host: str, nets: List[ipaddress.IPv4Network]) -> DoctorCheck:
    try:
        addr = ipaddress.ip_address(_LOOPBACK_HOSTNAME_ADDRESS.get(host.lower(), host))
    except ValueError:
        # A hostname route is the normal case, not an error, so this must not
        # read as "ok" (nothing was checked) or as "unknown" (exit 3 for
        # every ordinary hostname route would make the doctor useless).
        # "not-checked" says so plainly and leaves the exit code alone --
        # DoctorReport.exit_code only elevates on "unknown" or "finding".
        return DoctorCheck(
            "allowed-ips-coverage", "not-checked",
            f"{host} is a hostname, not a literal IP; this route was not checked offline "
            "(the address is only known at runtime, after DNS resolution)",
        )
    if any(addr in net for net in nets):
        return DoctorCheck("allowed-ips-coverage", "ok", f"{host} is covered by allowed_ips")
    if addr in CGNAT_NET:
        return DoctorCheck(
            "allowed-ips-coverage", "finding",
            f"{host} is a CGNAT address (RFC 6598, e.g. a Tailscale IP) -- it is NOT in the RFC1918 "
            "private ranges the default allowed_ips covers; add it to upstream.allowed_ips explicitly",
        )
    return DoctorCheck("allowed-ips-coverage", "finding", f"{host} is not covered by allowed_ips")


def _check_allowed_ips(cfg: dict) -> List[DoctorCheck]:
    upstream = _upstream_section(cfg)
    both_set = bool(upstream.get("allowed_ips")) and bool(upstream.get("disable_ip_validation"))
    out = [DoctorCheck(
        "allowed-ips-exclusive",
        "finding" if both_set else "ok",
        "allowed_ips and disable_ip_validation are mutually exclusive but both are set" if both_set
        else "allowed_ips and disable_ip_validation are not both set",
    )]
    if upstream.get("disable_ip_validation") and not upstream.get("allowed_ips"):
        return out  # validation is off by choice; there is nothing to check coverage against
    raw = upstream.get("allowed_ips")
    if raw and not isinstance(raw, list):
        out.append(DoctorCheck(
            "allowed-ips-type", "finding",
            f"allowed_ips must be a list of CIDR strings, not {type(raw).__name__} "
            f"({raw!r}); it is being silently ignored and the RFC1918 default is in effect",
        ))
    elif isinstance(raw, list) and raw:
        _, bad = _parse_cidrs(raw)
        if bad:
            out.append(DoctorCheck(
                "allowed-ips-cidr", "finding",
                f"allowed_ips has value(s) that are not IPv4 CIDRs or addresses {bad}; each is ignored, not enforced",
            ))
    nets = _effective_allowed_nets(cfg)
    out += [_check_ip_coverage(host, nets) for host in _route_hosts(cfg)]
    return out


def _check_https_anchor(cfg: dict) -> List[DoctorCheck]:
    routes = cfg.get("routes")
    if not isinstance(routes, dict):
        # ``_check_routes`` already reports ``routes-is-map`` for this config
        # (the same guard ``_route_hosts`` carries, for the same reason) --
        # nothing sensible to check here, and ``.values()`` on a list raises.
        # "not-checked", never "ok": nothing was verified (AGORA, 2026-11-05).
        return [DoctorCheck("https-trust-anchor", "not-checked",
                             "routes is not a map, so no route was checked; routes-is-map reports this")]
    if not any(str(v).startswith("https://") for v in routes.values()):
        return [DoctorCheck("https-trust-anchor", "ok", "no https:// upstream route is configured")]
    upstream_tls = _as_map(_upstream_section(cfg).get("tls"))
    if upstream_tls.get("ca_file") or upstream_tls.get("include_system_cas"):
        return [DoctorCheck("https-trust-anchor", "ok", "an https upstream route has a trust anchor configured")]
    return [DoctorCheck(
        "https-trust-anchor", "finding",
        "an https:// upstream route is configured but neither upstream.tls.ca_file nor "
        "upstream.tls.include_system_cas is set; the proxy has no trust anchor for it",
    )]


def _safe_load_cert(path: Path):
    if not path.exists():
        return None, "missing"
    try:
        from cryptography import x509
    except ImportError:
        # Reported, never raised: the always-on tick reads certificates through here
        # (AGORA, 2026-11-05), and so does every doctor rule, which then says unknown.
        return None, "the cryptography package is not installed for this interpreter"
    try:
        return x509.load_pem_x509_certificate(path.read_bytes()), None
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _ca_constraints(cert, err: Optional[str]) -> DoctorCheck:
    if cert is None:
        return DoctorCheck("ca-constraints", "unknown", f"cannot read the CA certificate ({err})")
    from cryptography import x509

    try:
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
    except x509.ExtensionNotFound:
        return DoctorCheck("ca-constraints", "finding", "the CA certificate has no BasicConstraints extension")
    if not bc.value.ca or not bc.critical:
        return DoctorCheck("ca-constraints", "finding", "the CA certificate's BasicConstraints must be CA:TRUE and critical")
    try:
        ku = cert.extensions.get_extension_for_class(x509.KeyUsage)
    except x509.ExtensionNotFound:
        return DoctorCheck("ca-constraints", "finding", "the CA certificate has no KeyUsage extension")
    if not ku.value.key_cert_sign:
        return DoctorCheck("ca-constraints", "finding", "the CA certificate's KeyUsage does not include keyCertSign")
    return DoctorCheck("ca-constraints", "ok", "the CA certificate has CA:TRUE (critical) and keyCertSign")


def _route_hostnames(cfg: dict) -> List[str]:
    """The full hostname Claude dials for each route: a bare subdomain key gains the
    tunnel domain, and a key already ending in it stays as it is (the proxy's own
    lookup, vendor-docs/platform-reference.md "Route matching")."""
    routes, domain = cfg.get("routes"), cfg.get("tunnel_domain")
    if not isinstance(routes, dict) or not domain:
        return []
    suffix = f".{domain}"
    return [k if k == domain or k.endswith(suffix) else f"{k}{suffix}" for k in map(str, routes)]


def _san_covers(names: set, domain: str, host: str) -> bool:
    """An exact SAN entry, or the wildcard, which covers one label below the domain only."""
    return host in names or (f"*.{domain}" in names and host.partition(".")[2] == domain)


def _cert_san(cert, err: Optional[str], domain: Optional[str], hostnames: Sequence[str] = ()) -> DoctorCheck:
    """The vendor's rule: the SAN includes ``<route>.<tunnel-domain>``, and a wildcard
    ``*.<tunnel-domain>`` covers every route. An exact name per route is enough on its
    own; this used to demand the wildcard (Codex, lib/mcp_tunnel.py:828)."""
    if cert is None:
        return DoctorCheck("cert-san", "unknown", f"cannot read the server certificate ({err})")
    if not domain:
        return DoctorCheck("cert-san", "unknown", "tunnel_domain is not configured; cannot check the SAN")
    from cryptography import x509

    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return DoctorCheck("cert-san", "finding", "the server certificate has no Subject Alternative Name")
    names = set(san.get_values_for_type(x509.DNSName))
    wildcard = f"*.{domain}"
    uncovered = [h for h in hostnames if not _san_covers(names, domain, h)]
    if wildcard in names and not uncovered:
        return DoctorCheck("cert-san", "ok", f"the server certificate's SAN covers {wildcard}")
    if hostnames and not uncovered:
        return DoctorCheck("cert-san", "ok", f"the server certificate's SAN names every route exactly ({', '.join(hostnames)})")
    gap = f"route host(s) {uncovered} are not covered" if uncovered else f"it has no {wildcard} and no route to match"
    return DoctorCheck(
        "cert-san", "finding",
        f"the server certificate's SAN {sorted(names)} falls short: {gap}. It needs {wildcard}, or an "
        f"exact name per route (e.g. {PROBE_ROUTE_NAME}.{domain}); a SAN for {domain} alone covers no route",
    )


def _cert_eku(cert, err: Optional[str]) -> DoctorCheck:
    """The vendor's rule: if the server certificate carries ExtendedKeyUsage, it includes
    serverAuth. A clientAuth-only certificate passed every other check (Codex,
    lib/mcp_tunnel.py:1005)."""
    if cert is None:
        return DoctorCheck("cert-eku", "unknown", f"cannot read the server certificate ({err})")
    from cryptography import x509
    from cryptography.x509.oid import ExtendedKeyUsageOID

    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound:
        return DoctorCheck("cert-eku", "ok", "the server certificate has no ExtendedKeyUsage, so none is required")
    if ExtendedKeyUsageOID.SERVER_AUTH in eku:
        return DoctorCheck("cert-eku", "ok", "the server certificate's ExtendedKeyUsage includes serverAuth")
    return DoctorCheck("cert-eku", "finding",
                       "the server certificate's ExtendedKeyUsage omits serverAuth, which the vendor "
                       "requires whenever the extension is present")


def _aki_ski(ca_cert, tls_cert, ca_err: Optional[str], tls_err: Optional[str]) -> DoctorCheck:
    if ca_cert is None:
        return DoctorCheck("cert-aki-ski", "unknown", f"cannot read the CA certificate ({ca_err})")
    if tls_cert is None:
        return DoctorCheck("cert-aki-ski", "unknown", f"cannot read the server certificate ({tls_err})")
    from cryptography import x509

    try:
        ski = ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
        aki = tls_cert.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value.key_identifier
    except x509.ExtensionNotFound:
        return DoctorCheck("cert-aki-ski", "finding", "the CA's SubjectKeyIdentifier or the server's AuthorityKeyIdentifier is missing")
    if aki == ski:
        return DoctorCheck("cert-aki-ski", "ok", "the server certificate's AuthorityKeyIdentifier matches the CA's SubjectKeyIdentifier")
    return DoctorCheck("cert-aki-ski", "finding", "the server certificate's AuthorityKeyIdentifier does not match the CA's SubjectKeyIdentifier")


def _cert_ca_signature(ca_cert, tls_cert, ca_err: Optional[str], tls_err: Optional[str]) -> DoctorCheck:
    """``cert-aki-ski`` (above) compares two UNSIGNED extension values -- a
    certificate can carry an AuthorityKeyIdentifier copied from this CA while
    having actually been signed by an entirely different key (Codex,
    lib/mcp_tunnel.py:819). The vendor's own certificate requirements
    (vendor-docs/platform-reference.md) say the server certificate must be
    "signed directly by a registered CA (no intermediates)" -- this verifies
    the real cryptographic signature, which identifier matching alone does
    not prove."""
    if ca_cert is None:
        return DoctorCheck("cert-ca-signature", "unknown", f"cannot read the CA certificate ({ca_err})")
    if tls_cert is None:
        return DoctorCheck("cert-ca-signature", "unknown", f"cannot read the server certificate ({tls_err})")
    from cryptography.exceptions import InvalidSignature

    try:
        tls_cert.verify_directly_issued_by(ca_cert)
    except (InvalidSignature, ValueError, TypeError) as exc:
        return DoctorCheck(
            "cert-ca-signature", "finding",
            f"the server certificate's signature does not verify against this CA "
            f"({type(exc).__name__}: {exc})",
        )
    return DoctorCheck("cert-ca-signature", "ok", "the server certificate's signature verifies against the CA")


def _key_size_ok(cert) -> bool:
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    pub = cert.public_key()
    if isinstance(pub, rsa.RSAPublicKey):
        return pub.key_size >= 2048
    if isinstance(pub, ec.EllipticCurvePublicKey):
        return pub.curve.key_size >= 256
    return False


def _key_size_checks(ca_cert, tls_cert, ca_err: Optional[str], tls_err: Optional[str]) -> List[DoctorCheck]:
    out = []
    for label, cert, err in (("CA", ca_cert, ca_err), ("server", tls_cert, tls_err)):
        if cert is None:
            out.append(DoctorCheck("key-size", "unknown", f"cannot read the {label} certificate ({err})"))
        elif _key_size_ok(cert):
            out.append(DoctorCheck("key-size", "ok", f"the {label} certificate's key is RSA>=2048 or ECDSA P-256+"))
        else:
            out.append(DoctorCheck("key-size", "finding", f"the {label} certificate's key is smaller than RSA 2048 / ECDSA P-256"))
    return out


def _signature_hash_ok(cert) -> bool:
    """SHA-256 or stronger: a digest of at least 32 bytes. SHA-1, SHA-224 and MD5 fail,
    and so does a signature with no separate hash (Ed25519, which key-size refuses too)."""
    from cryptography.exceptions import UnsupportedAlgorithm

    try:
        alg = cert.signature_hash_algorithm
    except UnsupportedAlgorithm:
        return False
    return alg is not None and alg.digest_size >= 32


def _signature_hash_checks(ca_cert, tls_cert, ca_err: Optional[str], tls_err: Optional[str]) -> List[DoctorCheck]:
    """The vendor requires a SHA-256 or stronger signature on BOTH certificates; key
    size alone was treated as the whole requirement (Codex, lib/mcp_tunnel.py:899)."""
    out = []
    for label, cert, err in (("CA", ca_cert, ca_err), ("server", tls_cert, tls_err)):
        if cert is None:
            out.append(DoctorCheck("cert-signature-hash", "unknown", f"cannot read the {label} certificate ({err})"))
        elif _signature_hash_ok(cert):
            out.append(DoctorCheck("cert-signature-hash", "ok", f"the {label} certificate is signed with SHA-256 or stronger"))
        else:
            out.append(DoctorCheck("cert-signature-hash", "finding",
                                   f"the {label} certificate's signature hash is weaker than SHA-256, "
                                   "which the vendor does not accept"))
    return out


def _expiry_checks(ca_cert, tls_cert, ca_err: Optional[str], tls_err: Optional[str], now: Optional[datetime] = None) -> List[DoctorCheck]:
    """Both ends of the validity period: the vendor requires each certificate to be
    within it, and a certificate valid only from a later date used to pass (Codex,
    lib/mcp_tunnel.py:916)."""
    now = now or datetime.now(timezone.utc)
    out = []
    for label, cert, err in (("CA", ca_cert, ca_err), ("server", tls_cert, tls_err)):
        if cert is None:
            out.append(DoctorCheck("cert-days-left", "unknown", f"cannot read the {label} certificate ({err})"))
            continue
        if cert.not_valid_before_utc > now:
            out.append(DoctorCheck("cert-days-left", "finding",
                                   f"the {label} certificate is not valid until "
                                   f"{cert.not_valid_before_utc.strftime('%Y-%m-%d %H:%M')} UTC"))
            continue
        days = (cert.not_valid_after_utc - now).days
        if days < 0:
            out.append(DoctorCheck("cert-days-left", "finding", f"the {label} certificate expired {-days} day(s) ago"))
        elif days <= CERT_WARN_DAYS:
            out.append(DoctorCheck("cert-days-left", "finding", f"the {label} certificate has {days} day(s) left"))
        else:
            out.append(DoctorCheck("cert-days-left", "ok", f"the {label} certificate has {days} day(s) left"))
    return out


def _mode_restricted_check(path: Path, rule: str) -> DoctorCheck:
    """A file that must stay owner-only: a ``finding`` once it exists and carries
    a group/other read (or write) bit, ``unknown`` while it does not exist yet."""
    if not path.exists():
        return DoctorCheck(rule, "unknown", f"{path} does not exist")
    mode = path.stat().st_mode
    if mode & 0o077:
        return DoctorCheck(rule, "finding", f"{path} is readable by group or other (mode {oct(mode & 0o777)}); chmod 600 it")
    return DoctorCheck(rule, "ok", f"{path} is private (mode {oct(mode & 0o777)})")


def _ca_key_permission(dir_: Path) -> DoctorCheck:
    """``ca.key`` only, among the certificate material -- the vendor's own quickstart
    requires ``chmod 644 data/tls.key`` so the non-root proxy container can read it,
    so flagging ``tls.key`` would be a false positive against the vendor's own
    documented step. ``ca.key`` is read only on this host (certs/cert-renew), never
    by a container, so it should stay owner-only."""
    return _mode_restricted_check(_ca_key(dir_), "key-world-readable")


def _token_permission(dir_: Path) -> DoctorCheck:
    """The tunnel token: the one secret the plan's own risk section names -- it lets
    a reader replay every call through the tunnel. ``scaffold``/``certs`` never
    write it (the operator pastes it in before ``up``), so a MISSING file is not yet
    a problem; a PRESENT, group/other-readable one is."""
    path = _token_path(dir_)
    if not path.exists():
        return DoctorCheck("token-world-readable", "ok", f"{path} does not exist yet; paste the Console token in before up")
    mode = path.stat().st_mode
    if mode & 0o077:
        return DoctorCheck("token-world-readable", "finding", f"{path} is readable by group or other (mode {oct(mode & 0o777)}); chmod 600 it")
    return DoctorCheck("token-world-readable", "ok", f"{path} is private (mode {oct(mode & 0o777)})")


def _probe_key_permission(dir_: Path) -> DoctorCheck:
    """The probe's Ed25519 signing key: ``scaffold`` always writes it at mode 0600
    (``_write_secure_text``), so a group/other-readable key here means something
    else changed it after the fact."""
    return _mode_restricted_check(_probe_key_path(dir_), "probe-key-world-readable")


def _public_key_der(pub) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return pub.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _tls_key_matches_cert(tls_key_path: Path, tls_cert, tls_cert_err: Optional[str]) -> DoctorCheck:
    """Codex (lib/mcp_tunnel.py:911): the generated proxy configuration requires
    ``/data/tls.key`` to serve TLS, but no doctor check named it at all -- only
    ``ca.key``'s PERMISSIONS were checked (``_ca_key_permission`` above). A
    deployment missing ``tls.key``, or whose ``tls.key`` is corrupt or does not
    match ``tls.crt``'s public key, reported a clean doctor right up until the
    proxy failed to serve TLS. Compares DER-encoded public-key bytes rather
    than key-type-specific numbers, so this works for any key type the
    certificate requirements accept (RSA or ECDSA)."""
    if tls_cert is None:
        return DoctorCheck("tls-key-matches-cert", "unknown", f"cannot read the server certificate ({tls_cert_err})")
    if not tls_key_path.exists():
        return DoctorCheck("tls-key-matches-cert", "finding",
                            f"{tls_key_path} does not exist; the proxy cannot serve TLS without it")
    from cryptography.hazmat.primitives import serialization

    try:
        key_bytes = tls_key_path.read_bytes()
        priv = serialization.load_pem_private_key(key_bytes, password=None)
    except (OSError, ValueError, TypeError) as exc:
        return DoctorCheck("tls-key-matches-cert", "finding",
                            f"{tls_key_path} is not a readable private key ({type(exc).__name__}: {exc})")
    if _public_key_der(priv.public_key()) != _public_key_der(tls_cert.public_key()):
        return DoctorCheck("tls-key-matches-cert", "finding",
                            f"{tls_key_path} does not match the server certificate's public key")
    return DoctorCheck("tls-key-matches-cert", "ok", f"{tls_key_path} matches the server certificate's public key")


def _check_certs(dir_: Path, cfg: dict) -> List[DoctorCheck]:
    ca_cert, ca_err = _safe_load_cert(_ca_crt(dir_))
    tls_cert, tls_err = _safe_load_cert(_tls_crt(dir_))
    domain = cfg.get("tunnel_domain")
    out = [
        _ca_constraints(ca_cert, ca_err), _cert_san(tls_cert, tls_err, domain, _route_hostnames(cfg)),
        _aki_ski(ca_cert, tls_cert, ca_err, tls_err),
        _cert_ca_signature(ca_cert, tls_cert, ca_err, tls_err),
        _cert_eku(tls_cert, tls_err),
    ]
    out += _key_size_checks(ca_cert, tls_cert, ca_err, tls_err)
    out += _signature_hash_checks(ca_cert, tls_cert, ca_err, tls_err)
    out += _expiry_checks(ca_cert, tls_cert, ca_err, tls_err)
    out.append(_tls_key_matches_cert(_tls_key(dir_), tls_cert, tls_err))
    out.append(_ca_key_permission(dir_))
    return out


def _check_secret_permissions(dir_: Path) -> List[DoctorCheck]:
    """The two bearer secrets this kit never writes with a relaxed mode on purpose
    (unlike ``tls.key``): a world-readable one is a finding, not a silent gap in a
    report that otherwise says ``ok``."""
    return [_token_permission(dir_), _probe_key_permission(dir_)]


def doctor(dir_: Path) -> DoctorReport:
    """Offline checks, each with a rule id; ``docs/mcp-tunnel-first-contact.md`` lists
    them. A config that cannot be read at all yields one ``unknown`` check and nothing
    else -- there is nothing else to check."""
    dir_ = Path(dir_)
    cfg, cfg_err = _load_yaml_safe(_config_path(dir_))
    if cfg_err:
        return DoctorReport([DoctorCheck("config-readable", "unknown", cfg_err)])
    checks = _check_routes(cfg) + _check_tunnel_domain(cfg) + _check_upstream_is_map(cfg)
    checks += _check_allowed_ips(cfg) + _check_https_anchor(cfg)
    checks += _check_certs(dir_, cfg)
    checks += _check_secret_permissions(dir_)
    return DoctorReport(checks)


# ----------------------------------------------------------------------------- nonce / first contact

def _signing_payload(nonce: str, signed_at: str) -> bytes:
    """Byte-identical to the generated probe script's own ``f"{nonce}|{signed_at}"`` line."""
    return f"{nonce}|{signed_at}".encode("utf-8")


def _nonce_sentence(nonce: str) -> str:
    return f'Use the first_contact tool with nonce "{nonce}" and return its signature.'


def _pbcopy(text: str) -> None:
    try:
        subprocess.run(["pbcopy"], input=text, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        pass  # the clipboard is a convenience; the sentence is still printed and returned


def nonce_new(dir_: Path, *, clipboard: Optional[Callable[[str], None]] = None, now: Optional[datetime] = None) -> dict:
    dir_ = Path(dir_)
    _ensure_probe_keypair(dir_)
    nonce = secrets.token_hex(16)
    issued_at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pending = {"nonce": nonce, "issued_at": issued_at, "ttl_s": NONCE_TTL_S}
    _nonce_pending_path(dir_).write_text(json.dumps(pending), encoding="utf-8")
    sentence = _nonce_sentence(nonce)
    (clipboard or _pbcopy)(sentence)
    return {"nonce": nonce, "sentence": sentence, "issued_at": issued_at, "ttl_s": NONCE_TTL_S}


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _verify_probe_signature(dir_: Path, reply: dict) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_probe_key_path(dir_).read_text().strip()))
        payload = _signing_payload(str(reply["nonce"]), str(reply["signed_at"]))
        priv.public_key().verify(bytes.fromhex(str(reply.get("signature", ""))), payload)
        return True
    except (InvalidSignature, ValueError, KeyError, OSError):
        return False


def _nonce_reason(dir_: Path, pending: dict, reply: dict, now: datetime) -> Optional[str]:
    if str(reply.get("nonce")) != str(pending.get("nonce")):
        return "nonce_mismatch"
    issued = _parse_iso(pending.get("issued_at"))
    if issued is None or now > issued + timedelta(seconds=int(pending.get("ttl_s", NONCE_TTL_S))):
        return "nonce_expired"
    signed_at = _parse_iso(reply.get("signed_at"))
    if signed_at is None or signed_at < issued:
        return "bad_timestamp"
    if not _verify_probe_signature(dir_, reply):
        return "bad_signature"
    return None


def nonce_verify(dir_: Path, reply: dict, *, now: Optional[datetime] = None) -> dict:
    """Checks a probe reply against the pending nonce; consumes it on success.

    Proves to the operator that the path works and the reply is fresh. Proves
    nothing to a stranger: the signing key never leaves ``data/``.
    """
    dir_ = Path(dir_)
    now = now or datetime.now(timezone.utc)
    pending_path = _nonce_pending_path(dir_)
    pending = _read_json(pending_path)
    if pending is None:
        return {"ok": False, "reason": "no_pending_nonce"}
    reason = _nonce_reason(dir_, pending, reply, now)
    if reason:
        return {"ok": False, "reason": reason}
    pending_path.unlink(missing_ok=True)
    _first_contact_record(dir_, reply, now=now)
    return {
        "ok": True, "reason": "verified",
        "note": "This proves the tunnel path works and the reply is fresh, to you. It proves "
                "nothing to a stranger: the signing key never leaves data/.",
        "card": CEREMONY_CARD,
    }


# ----------------------------------------------------------------------------- first-contact ceremony

CEREMONY_CARD = "Claude spoke.\nYour host replied, and you checked."
CEREMONY_PROMPT = "I saw it"
CEREMONY_REPLY = "Then I will seal it."


def _first_contact_path(dir_: Path) -> Path:
    return dir_ / "first-contact.txt"


def _first_contact_record(dir_: Path, reply: dict, *, now: Optional[datetime] = None) -> None:
    """Appends the first-contact file: the date, the nonce, and a short
    signature crumb -- never the whole signature. Plainly not a proof for
    anyone else: the signing key never leaves ``data/``, so only the operator
    who holds it can make anything of this file."""
    now = now or datetime.now(timezone.utc)
    crumb = str(reply.get("signature", ""))[:12]
    text = (
        f"First contact: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"Nonce: {reply.get('nonce')}\n"
        f"Signature crumb: {crumb}\n"
        "This is not a proof for anyone else: the signing key never leaves "
        "data/, and this file only means something to you.\n\n"
    )
    with open(_first_contact_path(dir_), "a", encoding="utf-8") as fh:
        fh.write(text)


def first_contact_confirm(dir_: Path, text: str) -> dict:
    """The operator's own "I saw it", appended to the first-contact log with
    the kit's reply. Anything else is left unrecorded and gets no reply --
    this is a ceremony between the operator and the file, never a check."""
    dir_ = Path(dir_)
    if text.strip() != CEREMONY_PROMPT:
        return {"ok": False, "reply": None}
    path = _first_contact_path(dir_)
    if not path.exists():
        return {"ok": False, "reply": None, "reason": "no first contact recorded yet"}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{CEREMONY_PROMPT}\n{CEREMONY_REPLY}\n\n")
    return {"ok": True, "reply": CEREMONY_REPLY}


# ----------------------------------------------------------------------------- docker compose wrappers

def _docker_env(dir_: Path) -> dict:
    """The caller's environment without any ``TUNNEL_TOKEN`` it carries, plus this
    deployment's own token when ``data/tunnel-token`` exists. A token exported for
    another tunnel must never start this one (Codex, lib/mcp_tunnel.py:1173)."""
    env = dict(os.environ)
    env.pop("TUNNEL_TOKEN", None)
    token = _token_path(dir_)
    if token.exists():
        env["TUNNEL_TOKEN"] = token.read_text(encoding="utf-8").strip()
    return env


def _compose(dir_: Path, args: List[str]) -> subprocess.CompletedProcess:
    """``docker compose ARGS``; a missing executable or a hang becomes a failed result,
    never an exception. A hung Docker CLI or daemon is exactly when the brake is
    needed, and a traceback there skipped the stop card (Codex, lib/mcp_tunnel.py:1183)."""
    try:
        return subprocess.run(
            ["docker", "compose"] + args, cwd=str(dir_), env=_docker_env(dir_),
            capture_output=True, text=True, timeout=COMPOSE_TIMEOUT_S,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(args, 127, "", f"{type(exc).__name__}: {exc}")
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args, 124, "", f"docker compose {' '.join(args)} timed out after {COMPOSE_TIMEOUT_S}s")


def up(dir_: Path) -> dict:
    proc = _compose(Path(dir_), ["up", "-d"])
    return {"ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr}


def down(dir_: Path) -> dict:
    """Graceful teardown (vendor-docs/platform-security.md "Tear down a tunnel")."""
    proc = _compose(Path(dir_), ["down"])
    return {"ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr}


def stop(dir_: Path) -> dict:
    """The brake: no grace period (vendor-docs/platform-security.md "Respond to a suspected breach")."""
    proc = _compose(Path(dir_), ["down", "--timeout", "0"])
    return {"ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr}


def _count_seal_records(seal_home: Path) -> int:
    """Every well-formed JSON line in a ``grasp-mcp-seal`` home's ``seal.jsonl``
    -- a content count, not a claim about what happened: this kit reads the
    sidecar's ledger file directly rather than importing the ``grasp``
    package, which may not even be installed alongside GRIP."""
    path = Path(seal_home) / "seal.jsonl"
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            json.loads(line)
        except ValueError:
            continue
        count += 1
    return count


def stop_card(dir_: Path, *, seal_home: Optional[Path] = None, now: Optional[datetime] = None) -> dict:
    """The brake's own record: ``stop()``'s result plus the time and the seal
    count frozen at this instant. Says the stack is lowered ONLY when
    ``stop()`` actually reports ``ok`` -- this is presented as the breach
    brake, so a failed teardown (the Docker daemon unavailable, say) must
    never be mistaken for successful containment (Codex, lib/mcp_tunnel.py:1134).
    On success, never says that proves nothing left the machine before now --
    a seal count only tells you what was recorded, never what was not."""
    now = now or datetime.now(timezone.utc)
    result = stop(dir_)
    seals = _count_seal_records(Path(seal_home)) if seal_home is not None else None
    if result.get("ok"):
        lines = ["The stack is lowered.", f"Time: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}"]
        if seals is not None:
            lines.append(f"Seals at this instant: {seals}")
        lines.append("This does not prove nothing left the machine before now.")
    else:
        lines = ["The stack is NOT lowered -- the stop command failed.",
                 f"Time: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}"]
        if seals is not None:
            lines.append(f"Seals at this instant: {seals}")
        stderr = (result.get("stderr") or "").strip()
        if stderr:
            lines.append(f"Error: {stderr[:300]}")
    return {**result, "card": "\n".join(lines), "seals": seals,
            "stopped_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}


_STATUS_SERVICES = ("mcp-proxy", "cloudflared")


def _running_services(dir_: Path) -> List[str]:
    """Services ``docker compose ps`` currently reports as running -- NOT a
    replay of history the way ``compose logs`` is. A nonzero exit (compose
    itself missing, the project not up at all) is honestly zero running
    services, never an exception."""
    proc = _compose(Path(dir_), ["ps", "--status", "running", "--services"])
    if proc.returncode != 0:
        return []
    return sorted(ln.strip() for ln in proc.stdout.splitlines() if ln.strip())


def tunnel_status(dir_: Path) -> dict:
    """Counts the two lines the vendor's quickstart verifies by (route configured /
    Registered tunnel connection) AND confirms the proxy and connector containers are
    CURRENTLY running. ``docker compose logs`` replays a container's recorded output
    for as long as it ever existed, even after it stopped or crashed without being
    removed -- so the log-line counts alone cannot tell "the tunnel is up" from "the
    tunnel was up once" (Codex, lib/mcp_tunnel.py:1149)."""
    dir_ = Path(dir_)
    proxy_log = _compose(dir_, ["logs", "mcp-proxy"])
    tunnel_log = _compose(dir_, ["logs", "cloudflared"])
    routes = proxy_log.stdout.count("route configured")
    connections = tunnel_log.stdout.count("Registered tunnel connection")
    running = _running_services(dir_)
    containers_up = set(_STATUS_SERVICES) <= set(running)
    return {
        "routes_configured": routes, "tunnel_connections": connections,
        "containers_running": running,
        "ok": routes > 0 and connections > 0 and containers_up,
    }


# ----------------------------------------------------------------------------- weather

def _cert_weather_word(days: int) -> str:
    """clear (over 30 days), thin (8-30), short (1-7), or last (0 or expired)."""
    if days > CERT_WARN_DAYS:
        return "clear"
    if days >= 8:
        return "thin"
    if days >= 1:
        return "short"
    return "last"


def _days_label(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


def _cert_weather_phrase(dir_: Path, *, now: Optional[datetime] = None) -> str:
    cert, _err = _safe_load_cert(_tls_crt(Path(dir_)))
    if cert is None:
        return "missing"
    now = now or datetime.now(timezone.utc)
    days = (cert.not_valid_after_utc - now).days
    word = _cert_weather_word(days)
    if word != "last":
        return f"{word}, {_days_label(days)}"
    if days < 0:
        return f"last, expired {_days_label(-days)} ago"
    return "last, expires today"


def weather(dir_: Path, *, seal_home: Optional[Path] = None, now: Optional[datetime] = None) -> dict:
    """One honest line: whether the tunnel looks up, the certificate's
    weather word, and -- only when a sidecar home is given -- how many seal
    records exist. Never claims proof of any event: a seal record proves what
    its own host recorded, never that the recorded set is complete."""
    dir_ = Path(dir_)
    status = tunnel_status(dir_)
    tunnel_word = "Tunnel up" if status.get("ok") else "Tunnel down"
    cert_phrase = _cert_weather_phrase(dir_, now=now)
    parts = [f"{tunnel_word}.", f"Cert {cert_phrase}."]
    seals = None
    if seal_home is not None:
        seals = _count_seal_records(Path(seal_home))
        parts.append(f"Seals {seals}.")
    parts.append("Proof: not of event.")
    return {"line": " ".join(parts), "tunnel_up": bool(status.get("ok")), "cert": cert_phrase, "seals": seals}


# ----------------------------------------------------------------------------- cert-status (lib/grip_tick.py integration)

def cert_status_one(dir_: Path, *, now: Optional[datetime] = None) -> Optional[str]:
    """One line when ``dir_``'s server certificate is within 30 days of expiry or
    expired, OR when the certificate exists but cannot be read at all; else None.

    ``_safe_load_cert`` distinguishes "missing" (legitimately silent -- nothing
    deployed here yet) from a real parse error (truncated, malformed, or
    otherwise unreadable). Collapsing both into the same silent ``None`` let a
    corrupt certificate read as "ok: no certificate within 30 days of expiry"
    -- cert-status and the always-on tick both stayed silent about exactly the
    failure this check exists to catch (Codex, lib/mcp_tunnel.py:1207). The CLI
    reports "missing" itself (``_cmd_cert_status``), as not-checked."""
    cert, err = _safe_load_cert(_tls_crt(Path(dir_)))
    if cert is None:
        if err == "missing":
            return None  # nothing deployed here yet, or no certificate yet -- not this check's concern
        return f"the MCP tunnel certificate at {dir_} is unreadable ({err}); bin/grip-tunnel doctor --dir {dir_} names the cause"
    now = now or datetime.now(timezone.utc)
    days = (cert.not_valid_after_utc - now).days
    if days < 0:
        return f"the MCP tunnel certificate at {dir_} expired {-days} day(s) ago"
    if days <= CERT_WARN_DAYS:
        return f"the MCP tunnel certificate at {dir_} has {days} day(s) left"
    return None


def cert_status_findings(*, now: Optional[datetime] = None) -> List[str]:
    """One line per registered deployment whose certificate is within 30 days of expiry, or expired.

    Silent (an empty list) when no deployment is registered.
    """
    out = []
    for dir_ in registered_deployments():
        line = cert_status_one(dir_, now=now)
        if line:
            out.append(line)
    return out


# ----------------------------------------------------------------------------- vendor-check

def _default_fetch(url: str) -> str:
    import urllib.request

    with urllib.request.urlopen(url, timeout=10) as resp:  # the fixed vendor URL, not caller input
        return resp.read().decode("utf-8", errors="replace")


def parse_vendor_frontmatter(text: str) -> dict:
    """``status`` and ``zdr.eligibility`` from the vendor doc's YAML frontmatter (the raw ``.md`` form)."""
    import yaml

    if not text.startswith("---"):
        raise TunnelKitError("vendor page has no YAML frontmatter")
    _, _, rest = text.partition("---")
    frontmatter, sep, _ = rest.partition("\n---")
    if not sep:
        raise TunnelKitError("vendor page's frontmatter is not terminated")
    data = yaml.safe_load(frontmatter) or {}
    meta = data.get("featureMetadata") or {}
    return {"status": meta.get("status"), "zdr_eligibility": (meta.get("zdr") or {}).get("eligibility")}


def vendor_check(*, fetch: Optional[Callable[[str], str]] = None, baseline_path: Optional[Path] = None) -> dict:
    baseline_path = baseline_path or vendor_baseline_path()
    baseline = _read_json(baseline_path) or {}
    try:
        text = (fetch or _default_fetch)(VENDOR_OVERVIEW_URL)
        current = parse_vendor_frontmatter(text)
    except Exception as exc:  # noqa: BLE001 -- any network/parse failure is "unreachable", never a crash
        return {"reachable": False, "changed": None, "error": f"{type(exc).__name__}: {exc}"}
    changed = {k: {"was": baseline.get(k), "now": v} for k, v in current.items() if baseline.get(k) != v}
    return {"reachable": True, "changed": bool(changed), "diff": changed, "current": current, "baseline": baseline}


# ----------------------------------------------------------------------------- CLI

def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _cmd_scaffold(args: argparse.Namespace) -> int:
    _print(scaffold(Path(args.dir), args.domain))
    return 0


def _cmd_certs(args: argparse.Namespace) -> int:
    _print(certs(Path(args.dir), days=args.days))
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    report = doctor(Path(args.dir))
    for c in report.checks:
        print(f"[{c.status:7s}] {c.rule}: {c.message}")
    return report.exit_code


def _cmd_nonce_new(args: argparse.Namespace) -> int:
    result = nonce_new(Path(args.dir))
    print(result["sentence"])
    _print(result)
    return 0


def _read_reply_text(reply_file: Optional[str]) -> str:
    try:
        return Path(reply_file).read_text(encoding="utf-8") if reply_file else sys.stdin.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise TunnelKitError(f"could not read the reply ({exc})") from exc


def _parse_reply_text(text: str) -> dict:
    """The probe's reply as a dict, from bare JSON or from JSON inside the prose and
    code fence a session usually wraps it in. Finding the object inside other text
    loosens nothing: the signature check that follows is what makes a reply count.
    A reply that cannot be read is one plain line on stderr, never a traceback (AGORA,
    2026-11-05: the runbook's own "paste the JSON" step was the likeliest to fail)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise TunnelKitError("the reply holds no JSON object; paste the probe's whole reply")
    try:
        return json.loads(text[start:end + 1])
    except ValueError as exc:
        raise TunnelKitError(f"the reply's JSON could not be parsed ({exc})") from exc


CEREMONY_SKIPPED = "Not confirmed. The first contact itself is recorded."


def _ceremony_answer() -> Optional[str]:
    """The operator's answer at the "I saw it" prompt, or None when Ctrl-D or Ctrl-C
    skips the ceremony. By then the reply has verified and ``nonce_verify`` has
    already appended the first-contact record, so neither key may end in a traceback
    and a failing exit (GRIP#8978, GRIP#8980). A skip prints one plain line, and no
    confirmation is written."""
    try:
        return input(f'Type "{CEREMONY_PROMPT}" to confirm: ')
    except (EOFError, KeyboardInterrupt):
        print(f"\n{CEREMONY_SKIPPED}")
        return None


def _cmd_nonce_verify(args: argparse.Namespace) -> int:
    result = nonce_verify(Path(args.dir), _parse_reply_text(_read_reply_text(args.reply_file)))
    _print(result)
    if result["ok"]:
        print(result["card"])
        answer = _ceremony_answer() if sys.stdin.isatty() else None
        if answer is not None:
            confirm = first_contact_confirm(Path(args.dir), answer)
            if confirm["ok"]:
                print(confirm["reply"])
    return 0 if result["ok"] else 1


def _cmd_nonce(args: argparse.Namespace) -> int:
    return _cmd_nonce_new(args) if args.nonce_cmd == "new" else _cmd_nonce_verify(args)


def _cmd_simple(fn: Callable[[Path], dict], args: argparse.Namespace) -> int:
    result = fn(Path(args.dir))
    _print(result)
    return 0 if result.get("ok") else 1


def _cmd_stop(args: argparse.Namespace) -> int:
    seal_home = Path(args.seal_home) if getattr(args, "seal_home", None) else None
    result = stop_card(Path(args.dir), seal_home=seal_home)
    sys.stdout.write("\a")
    print(result["card"])
    _print({k: v for k, v in result.items() if k != "card"})
    return 0 if result.get("ok") else 1


def _cmd_weather(args: argparse.Namespace) -> int:
    seal_home = Path(args.seal_home) if getattr(args, "seal_home", None) else None
    print(weather(Path(args.dir), seal_home=seal_home)["line"])
    return 0


def _cmd_cert_status(args: argparse.Namespace) -> int:
    """Exit 0 with time left, 1 near or past expiry or unreadable, 3 with no
    certificate at all. That last case printed "ok:" and exited 0 -- a clean bill of
    health from a check that read nothing (AGORA, 2026-11-05)."""
    dir_ = Path(args.dir)
    if not _tls_crt(dir_).exists():
        print(f"not-checked: there is no certificate at {_tls_crt(dir_)}; run certs first")
        return 3
    line = cert_status_one(dir_)
    if line is None:
        print(f"ok: the certificate has more than {CERT_WARN_DAYS} days left")
        return 0
    print(line)
    return 1


def _cmd_cert_renew(args: argparse.Namespace) -> int:
    _print(cert_renew(Path(args.dir), days=args.days))
    return 0


def _cmd_vendor_check(_args: argparse.Namespace) -> int:
    result = vendor_check()
    _print(result)
    if not result["reachable"]:
        return 3
    return 1 if result["changed"] else 0


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="grip-tunnel", description="The GRIP MCP tunnel kit.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scaffold")
    p.add_argument("--dir", required=True)
    p.add_argument("--domain", required=True)

    p = sub.add_parser("certs")
    p.add_argument("--dir", required=True)
    p.add_argument("--days", type=int, default=90)

    p = sub.add_parser("doctor")
    p.add_argument("--dir", required=True)

    non = sub.add_parser("nonce")
    non_sub = non.add_subparsers(dest="nonce_cmd", required=True)
    n1 = non_sub.add_parser("new")
    n1.add_argument("--dir", required=True)
    n2 = non_sub.add_parser("verify")
    n2.add_argument("--dir", required=True)
    n2.add_argument("--reply-file")

    for name in ("up", "down", "status"):
        p = sub.add_parser(name)
        p.add_argument("--dir", required=True)

    p = sub.add_parser("stop")
    p.add_argument("--dir", required=True)
    p.add_argument("--seal-home", help="a grasp-mcp-seal home; freezes its seal count into the stop card")

    p = sub.add_parser("weather")
    p.add_argument("--dir", required=True)
    p.add_argument("--seal-home", help="a grasp-mcp-seal home; adds its seal count to the line")

    p = sub.add_parser("cert-status")
    p.add_argument("--dir", required=True)

    p = sub.add_parser("cert-renew")
    p.add_argument("--dir", required=True)
    p.add_argument("--days", type=int, default=90)

    sub.add_parser("vendor-check")
    return ap


_HANDLERS: Dict[str, Callable[[argparse.Namespace], int]] = {
    "scaffold": _cmd_scaffold,
    "certs": _cmd_certs,
    "doctor": _cmd_doctor,
    "nonce": _cmd_nonce,
    "up": lambda a: _cmd_simple(up, a),
    "down": lambda a: _cmd_simple(down, a),
    "status": lambda a: _cmd_simple(tunnel_status, a),
    "stop": _cmd_stop,
    "weather": _cmd_weather,
    "cert-status": _cmd_cert_status,
    "cert-renew": _cmd_cert_renew,
    "vendor-check": _cmd_vendor_check,
}


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return _HANDLERS[args.cmd](args)
    except TunnelKitError as exc:
        print(f"grip-tunnel: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    # certs/nonce sign with Ed25519; this must run under an interpreter that can
    # (rules/never-system-python-for-grip.md).
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(Path(__file__))
    raise SystemExit(main())
