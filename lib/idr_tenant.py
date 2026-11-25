"""Which tenant is THIS GRIP installation? The single IDR tenant resolution point.

The tenant *namespace* already existed and is canonical: ``state/audit/keys/
<tenant_id>/`` is shared by ``lib.audit_chain`` (Ed25519 chain signing),
``lib.aide.key_provisioning`` (key material), ``lib.aide.decision_logger``
(per-tenant decision logs) and ``lib.pq_dual_sign`` (the dual seal). One
``tenant_id`` keys all four.

What did NOT exist was anything that *answers* the question. Every one of the
~17 ``build_idr`` call sites would have had to name a tenant itself, so none
did, so the product signing path was never reached and every record took the
forgeable demo seal. This module is the one place that answers it, so wiring a
seventeenth call site costs nothing and the answer cannot drift between them.

Resolution order, first non-empty wins::

    1. $GRIP_IDR_TENANT           per-machine override (mirrors $CLAUDE_DIR)
    2. config/grip.json  idr.tenant_id   declarative, tracked, reviewable
    3. None                       UNCONFIGURED -> caller behaves exactly as before

Rung 3 is the load-bearing one. On a machine where the operator has configured
nothing, ``resolve_tenant_id()`` returns ``None`` and every existing caller
signs exactly as it did before this module existed — same scheme, same warning,
same bytes. Turning dual custody on is a deliberate operator act, never a
side-effect of upgrading.

Nothing here is a secret. A ``tenant_id`` is a PUBLIC identifier: it is written
into the audit block in clear, and it names the published verifying-key file at
``.well-known/<product>-verifying-keys/<tenant_id>.pub``. Keeping it in tracked
config is correct; the private keys it names never leave ``state/audit/keys/``.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Mapping, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

#: Env var carrying the tenant for this machine. Highest priority.
TENANT_ENV = "GRIP_IDR_TENANT"

#: Config file and the dotted key inside it.
CONFIG_RELPATH = ("config", "grip.json")
CONFIG_SECTION = "idr"
CONFIG_KEY = "tenant_id"

#: A tenant_id becomes a DIRECTORY NAME under ``state/audit/keys/`` and a
#: published filename under a web docroot, so it is constrained to a shape that
#: is safe in both. Must OPEN with an alphanumeric — that single anchor is what
#: makes ``..`` and ``.hidden`` and ``-flag`` unrepresentable, which is the
#: path-traversal guard. Separators are excluded by the character class, so
#: ``../../etc/shadow`` cannot be spelled at all.
_VALID_TENANT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class InvalidTenantId(ValueError):
    """A configured tenant_id is not a usable identifier.

    Raised rather than ignored, deliberately. Silently discarding a bad value
    would fall back to the shared-key demo seal — a forgeable record produced
    because of a typo, reported by nothing. That silent-downgrade shape is the
    exact failure this whole subsystem exists to end, so a misconfiguration
    fails loudly on the first record instead.
    """


def _validate(tenant_id: str, source: str) -> str:
    """Return ``tenant_id`` if it is a safe identifier, else raise.

    ``source`` names where the value came from so the operator is told which
    of the two rungs to go and fix.
    """
    if not _VALID_TENANT.match(tenant_id):
        raise InvalidTenantId(
            f"{source} gave tenant_id {tenant_id!r}, which is not a usable "
            f"identifier. A tenant_id names a key DIRECTORY and a published "
            f"key FILE, so it must start with a letter or digit and contain "
            f"only letters, digits, dot, dash or underscore (max 64 chars). "
            f"Try something like 'grip-prod' or 'codetonight-grip'."
        )
    return tenant_id


def _from_env(env: Mapping[str, str]) -> Optional[str]:
    """Rung 1 — ``$GRIP_IDR_TENANT``, whitespace-stripped."""
    raw = (env.get(TENANT_ENV) or "").strip()
    return _validate(raw, f"${TENANT_ENV}") if raw else None


def _from_config(claude_dir: Path) -> Optional[str]:
    """Rung 2 — ``config/grip.json`` -> ``idr.tenant_id``.

    Fail-open on an unreadable or malformed config file: a corrupt
    ``grip.json`` must not make every IDR write raise, because the file also
    carries unrelated settings and is edited by other tooling. A MISSING key is
    simply "unconfigured"; only a PRESENT but malformed tenant_id raises, since
    that is an operator intent that cannot be honoured.
    """
    path = claude_dir.joinpath(*CONFIG_RELPATH)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    section = data.get(CONFIG_SECTION)
    if not isinstance(section, dict):
        return None
    raw = section.get(CONFIG_KEY)
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _validate(raw.strip(), f"config/grip.json {CONFIG_SECTION}.{CONFIG_KEY}")


def resolve_tenant_id(
    env: Optional[Mapping[str, str]] = None,
    claude_dir: Optional[Path] = None,
) -> Optional[str]:
    """The tenant this installation signs as, or ``None`` when unconfigured.

    Reads the environment at CALL time (never cached at import) so a test — or
    a process that sets the variable after import — sees the current value.

    Returns ``None`` when nothing is configured. Callers MUST treat ``None`` as
    "carry on exactly as before", not as an error: an unconfigured machine is
    the normal state until the operator provisions a tenant.

    Raises ``InvalidTenantId`` when a value IS configured but is unusable.
    """
    environ = os.environ if env is None else env
    from_env = _from_env(environ)
    if from_env:
        return from_env
    return _from_config(get_claude_dir() if claude_dir is None else Path(claude_dir))
