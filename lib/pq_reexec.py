"""Re-exec a tool under an interpreter that can actually dual-sign a record.

GRIP signs decision records with Ed25519 AND ML-DSA-65. Both limbs live in
`cryptography`, which is installed in the GRIP venv and nowhere else. 89 of the
170 tools in `bin/` start with `#!/usr/bin/env python3`, which on this machine
resolves to Homebrew's python — so they run without it.

That combination is quiet and wrong. `key_provisioning.pq_backend_available`
returns False, `_custody_gap` reports `crypto-backend-unavailable`, and the
record is written with the DEMO seal: an HMAC under a shared secret that, on
this installation, is world-readable and committed. The record is honestly
LABELLED `custody: demo-shared-key`, so nothing lies — but the tool silently
produced a forgeable record when a real one was available one interpreter away.
Since 2026-10-18 the signer refuses that record by default (product plan item
A3); this module stays the way to make the real seal reachable instead.

Observed 2026-09-13: `grip-sovereign-council` wrote
`precog-1789260622-193659af` this way with the tenant correctly configured. The
tenant switch is necessary and not sufficient.

WHY THE PROBE TESTS `mldsa` AND NOT JUST `cryptography`. The dual seal is an
AND. A `cryptography` too old to carry ML-DSA-65 — the `mldsa` module landed
in `cryptography` 47.0.0 but only for AWS-LC/BoringSSL builds, and reached
the OpenSSL wheels pip installs in 48.0.0, 2026-05-04 — is as unable to sign
as none,
and must be treated the same. `bin/grip-moat` probes for `cryptography` alone,
which is right for its own job (verifying an Ed25519 signature) and would be
wrong here: it would re-exec into an interpreter that still cannot dual-sign.

TWO BUGS THIS INHERITS FIXES FOR, both found the hard way in `bin/grip-moat`:

* `Path.resolve()` on a venv python follows the symlink back to the base
  interpreter, so a perfectly good venv compares EQUAL to the current one and
  gets skipped. Compare the UNRESOLVED path.
* Existing on disk does not mean a venv carries the library. Ask the candidate
  itself, in a subprocess. A probe cannot be wrong; a guess can.

DEGRADES, NEVER CRASHES. If no capable interpreter exists, the caller continues
on the current one and the signing path does what it already did — announce the
downgrade once and write an honestly-labelled demo record. Making this fatal
would take a tool that works today and break it on every machine without the
venv.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: Set before any exec so an interpreter that ALSO lacks the backend cannot
#: loop. One re-exec attempt, ever, per process tree.
SENTINEL = "GRIP_PQ_REEXEC"

#: The import pair the dual seal actually needs. Mirrors
#: `key_provisioning.pq_backend_available` — if that changes, this must too.
_PROBE = (
    "from cryptography.hazmat.primitives.asymmetric import mldsa;"
    "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey"
)


def can_dual_sign() -> bool:
    """True iff THIS interpreter can perform both limbs of the dual seal."""
    try:
        from cryptography.hazmat.primitives.asymmetric import mldsa  # noqa: F401
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: F401
            Ed25519PrivateKey,
        )
    except Exception:  # noqa: BLE001 — any import failure means "cannot sign"
        return False
    return True


def _claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))


def _venv_layout() -> tuple[str, tuple[str, ...]]:
    """Where a venv keeps its interpreter on this platform, and what it calls it.

    Windows puts it at `venv/Scripts/python.exe` with no `python*` level at all,
    so a candidate list built only from the POSIX shape is entirely nonexistent
    there — every candidate misses, the guard falls through, and the tool writes
    the forgeable record this module exists to prevent. Silent, by construction.

    Same knowledge as `lib/_compat.py:166-172` (`grip_python`), which cannot be
    reused directly: it returns ONE path and falls back to `sys.executable`,
    where this needs the full ordered candidate list across two roots and both
    venv spellings.
    """
    if sys.platform == "win32":
        return "Scripts", ("python.exe", "python3.exe")
    return "bin", ("python3", "python")


def candidate_pythons(code_root: Path) -> list[Path]:
    """Interpreters that might carry the backend, nearest checkout first.

    A worktree's own venv is preferred over the install's, so a tool run from a
    worktree tests THAT checkout rather than silently borrowing the installed
    one. Both venv spellings and both interpreter names are tried, because venvs
    differ and this machine carries `venv` and `.venv` under one root.
    """
    roots, seen = [], set()
    for root in (code_root, _claude_dir()):
        key = str(root)
        if key not in seen:
            seen.add(key)
            roots.append(root)
    bindir, names = _venv_layout()
    return [
        root / venv / bindir / name
        for root in roots
        for venv in ("venv", ".venv")
        for name in names
    ]


def _probe(python: Path) -> bool:
    """Ask the candidate itself. Never raises — an unusable candidate is False."""
    try:
        return subprocess.run(
            [str(python), "-c", _PROBE],
            capture_output=True,
            timeout=20,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _usable(candidate: Path) -> bool:
    """A candidate worth probing: a real executable that is not this process.

    Compared UNRESOLVED on purpose — see the module docstring. Resolving a venv
    python follows the symlink to the base interpreter and makes a good venv
    look like the one already running.
    """
    if str(candidate) == sys.executable:
        return False
    return candidate.is_file() and os.access(candidate, os.X_OK)


def reexec_if_incapable(script: Path, *, extra_paths: list[str] | None = None) -> None:
    """Re-exec `script` under a dual-sign-capable interpreter, if one exists.

    Call it FIRST, before importing anything that signs. Returns normally when
    this interpreter is already capable, when the sentinel says an attempt was
    already made, or when no capable interpreter was found — the caller then
    proceeds and the signing path reports its own downgrade.

    Never returns after a successful exec: the process is replaced.
    """
    if os.environ.get(SENTINEL) or can_dual_sign():
        return
    os.environ[SENTINEL] = "1"  # BEFORE any exec — bounds the loop at one

    script = Path(script).resolve()
    code_root = script.parent.parent
    for candidate in candidate_pythons(code_root):
        if not _usable(candidate) or not _probe(candidate):
            continue
        paths = [str(code_root), str(code_root / "lib"), *(extra_paths or [])]
        os.environ["PYTHONPATH"] = os.pathsep.join(
            paths + [os.environ.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        # PYTHONPATH and the sentinel are already in os.environ above, and
        # execv passes the whole environment on. This call does not return.
        os.execv(str(candidate), [str(candidate), str(script), *sys.argv[1:]])
    # No capable interpreter. Continue here. Since 2026-10-18 the signer REFUSES
    # a record that needs the dual seal (lib/precog/idr.py, ALLOW_DEMO_WITHOUT_
    # CRYPTO_ENV); only under GRIP_IDR_ALLOW_DEMO_WITHOUT_CRYPTO=1 is it written,
    # honestly labelled `custody: demo-shared-key`, with the downgrade announced once.
