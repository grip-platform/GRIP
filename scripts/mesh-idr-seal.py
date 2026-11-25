#!/usr/bin/env python3
"""Seal a GRIP session-mesh setup as a signed PrecogIDR (tamper-evident record).

Records THIS machine's mesh-tailnet setup decision — which layers are up, which
peers are bridged, and that the setup converged (the Banach fixed point proven by
lib/mesh_setup_idempotent.py) — into the PRECOG IDR chain (HMAC-SHA256 signed via
lib/precog/idr.py). It extends the existing chain (predecessor = last record) so
the attestation is anchored, not orphaned.

Run once per machine after scripts/mesh-tailnet-setup.sh, or via the
mesh-tailnet-setup workflow's Seal phase. The chain is append-only by design;
each invocation adds one timestamped, signed attestation.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

_CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
# Both the root (for `lib.precog.*` package imports) AND lib/ itself (GRIP's lib
# modules use flat sibling imports, e.g. audit_chain -> `from file_lock import`).
for _p in (str(_CLAUDE_DIR), str(_CLAUDE_DIR / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.precog.idr import append_idr, build_idr, read_idr_chain  # noqa: E402
from lib.precog.fingerprint import compute_fingerprint  # noqa: E402

MESH_HOME = Path.home() / ".grip-session-mesh"


def _port_open(port: int) -> bool:
    """True iff a local TCP listener answers on 127.0.0.1:port (0.5s timeout)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _peers() -> list[str]:
    """Configured relay peers from peers.json (empty on any read error)."""
    pf = MESH_HOME / "peers.json"
    if not pf.exists():
        return []
    try:
        return json.loads(pf.read_text()).get("peers", [])
    except (json.JSONDecodeError, OSError):
        return []


def _layers() -> dict[str, bool]:
    """Observed mesh layer state for the attestation body."""
    presence = _CLAUDE_DIR / "mcp-servers/grip-session-mesh/presence/dist/src/index.js"
    return {
        "bus_9474": _port_open(9474),
        "relay_9475": _port_open(9475),
        "presence_mcp": presence.exists(),
        "token": (MESH_HOME / "token").exists(),
    }


def main() -> int:
    machine = socket.gethostname()
    layers = _layers()
    peers = _peers()
    # Derive convergence from the REAL probes — never hardcode True. A signed
    # attestation that always read converged:True (even on a half-up mesh) would
    # weaken the tamper-evidence the IDR sells.
    converged = all(layers.values()) and len(peers) >= 1
    decision = {
        "action": "mesh-tailnet-setup",
        "machine": machine,
        "peers": peers,
        "layers": layers,
        "converged": converged,
        "idempotent_proof": "lib/mesh_setup_idempotent.py (Banach fixed point)",
    }
    prompt = f"mesh-tailnet-setup on {machine}"
    fingerprint = compute_fingerprint(prompt, "mesh-tailnet-setup")

    chain = read_idr_chain()
    predecessor = chain[-1].id if chain else None
    depth = (chain[-1].depth + 1) if chain else 0

    idr = build_idr(
        prompt=prompt,
        fingerprint=fingerprint,
        decision=decision,
        predecessor_idr=predecessor,
        depth=depth,
        kind="mesh-tailnet-setup",
    )
    append_idr(idr)

    print(json.dumps({
        "idr_id": idr.id,
        "scheme": idr.audit.get("scheme"),
        "signed": idr.audit.get("scheme") == "hmac-sha256",
        "predecessor": predecessor,
        "depth": depth,
        "peers": decision["peers"],
        "layers": decision["layers"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    # Dual-sign custody guard (GRIP#5628). This entrypoint has a
    # `#!/usr/bin/env python3` shebang and reaches a signed-record write, so under
    # an interpreter without `cryptography>=48` it would silently emit forgeable
    # `demo-shared-key` records. Re-exec under a capable interpreter FIRST, before
    # main() reaches any signing path.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "lib"))
    from pq_reexec import reexec_if_incapable as _reexec
    _reexec(_Path(__file__))

    raise SystemExit(main())
