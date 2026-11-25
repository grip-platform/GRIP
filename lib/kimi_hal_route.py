#!/usr/bin/env python3
"""Install GRIP's HAL gateway as a Kimi Code CLI provider (``[providers.hal]``).

Kimi Code CLI reads providers and model aliases from ``~/.kimi-code/config.toml``
(getting-started.html:66; ``$KIMI_CODE_HOME/config.toml`` when that variable is
set, :94). This module writes ONE fenced block into that file — a ``hal``
provider of type ``openai_responses`` pointed at the loopback gateway, plus one
model alias per routed Kimi id — so the CLI reaches Moonshot through HAL's
witness, cost rows and failover instead of a bare vendor key. Harness in a
harness in both directions: GRIP dispatches ``kimi -p`` (lib/harness/kimi.py),
and ``kimi`` uses GRIP's gateway as its provider (this file).

Three properties, each load-bearing and each pinned by tests/test_kimi_hal_route.py.

**No secret is ever written.** The CLI states its credential rule plainly:
"Credential priority: api_key direct field > [providers.<name>.env] sub-table
key > if both are absent, startup fails with an error. The CLI does not fall
back to shell environment variables for credentials." (providers.html:42), and
its ``[providers.<name>.env]`` sub-table carries literal VALUES — the vertexai
example at :111-117 writes the project id there, "Simply export
GOOGLE_CLOUD_PROJECT in the shell will not be read by the CLI". A
reference-only credential, DSH-style ``apiKeyEnv``, is therefore not a
mechanism this CLI has. What goes into the documented first-priority
``api_key`` field is the gateway DOORSTOP — the literal ``grip-local-doorstop``
that ``bin/grip-codex`` already uses. It is not a credential: HAL discards the
client's Authorization header and attaches its own per-provider keychain key
(``~/.hal/lib/hal/gateway_egress.py``), so the value only has to be non-empty.
Keeping it a placeholder is deliberate safety: an id HAL's router does not
recognise falls through to the verbatim Anthropic relay, and a placeholder makes
that path fail loudly with 401 instead of quietly spending the weekly limit
(``bin/grip-dsh`` verified this on 2026-08-19).

**The merge cannot clobber.** ``config.toml`` is the operator's file — their
``[providers.kimi]`` from ``/login``, their own aliases, their comments. The
block lives between GRIP's fence markers (reusing ``lib/grip_harness.py``'s
``merge_managed_toml``, which places a TOML fence before the first table header
and is idempotent); everything outside is preserved byte-for-byte, and the
merge REFUSES — raises, writes nothing — when the file does not parse, when a
fence opens without closing, or when ``[providers.hal]`` or one of our alias
tables already exists outside the fence. A re-run on a current file changes
nothing, which is what keeps comments intact (a text merge, never a re-dump).

**Every id was served through the gateway, and every window is catalogued.**
Probed twice on 2026-09-07 — once when this was written, once before it landed:
``POST /v1/responses`` on 127.0.0.1:4010 returned HTTP 200 for bare ``kimi-k3``
(4.56 s, then 4.24 s; ``status: completed``) and ``kimi-k2.7-code`` (1.05 s, then
1.03 s), and ``/v1/chat/completions`` returned 200 for ``moonshot/kimi-k3``
(3.12 s, then 2.75 s) and ``moonshot/kimi-k2.7-code`` (0.91 s, then 0.80 s).
``max_context_size`` values come from ``config/model_catalog.yaml``
(``moonshot/kimi-k3`` :376-380 = 1048576; ``moonshot/kimi-k2.7-code`` :393-397 =
262144); HAL's catalog on its ``origin/main`` agrees on both (:286-290 and
:366-370 — the ``~/.hal`` checkout on this machine was two commits behind and
still lacked the k2.7-code row when this was first written). Nothing is invented.

Why bare vendor ids (``kimi-k3``) rather than DSH's explicit ``moonshot/kimi-k3``:
the CLI matches thinking, vision and tool use "automatically by model name
prefix" (providers.html:41), and a ``moonshot/`` prefix would hide that name.
HAL routes the bare prefix through its alias table (``model_route.py:69``
``("kimi", "moonshot")``) — the path ``gg++ codex --kimi`` already relies on —
so both readings of the CLI's matcher (alias or model field) see ``kimi-``.
The aliases end in ``-hal`` so they can never claim an operator's own
``[models."kimi-k3"]`` table.

The provider type is ``openai_responses`` (providers.html:89-96 — "Configuration
is the same as openai", streaming always on), matching HAL's proven Responses
same-wire path to Moonshot. Whether the CLI's Responses client and HAL's
Responses façade agree on every field is UNVERIFIED-until-installed;
``bin/grip-kimi-verify`` is the probe.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Mapping

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from grip_harness import (  # noqa: E402
    MANAGED_BEGIN,
    MANAGED_END,
    _excise_managed_block,
    atomic_write_text,
    has_truncated_fence,
    merge_managed_toml,
)

#: Provider name inside ``[providers.<name>]``. Not a name the CLI reserves —
#: its own managed provider is ``kimi`` (providers.html:57-65).
PROVIDER = "hal"
#: One of the six documented provider types (providers.html:34-40).
PROVIDER_TYPE = "openai_responses"
#: HAL gateway, loopback only. Port per rules/port-registry.md.
BASE_URL = "http://127.0.0.1:4010/v1"
#: Non-secret doorstop (bin/grip-codex:70). Never a key; see the module docstring.
DOORSTOP = "grip-local-doorstop"

#: (alias shown in /model, vendor model id sent on the wire, max_context_size)
MODELS: tuple[tuple[str, str, int], ...] = (
    ("kimi-k3-hal", "kimi-k3", 1048576),
    ("kimi-k2.7-code-hal", "kimi-k2.7-code", 262144),
)


def default_config_path(env: Mapping[str, str] | None = None) -> Path:
    """``$KIMI_CODE_HOME/config.toml``, defaulting to ``~/.kimi-code`` as the CLI does."""
    values = os.environ if env is None else env
    home = values.get("KIMI_CODE_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".kimi-code") / "config.toml"


def provider_table() -> dict:
    """The ``[providers.hal]`` table. Pure — no I/O, no environment reads."""
    return {"type": PROVIDER_TYPE, "base_url": BASE_URL, "api_key": DOORSTOP}


def model_tables() -> dict[str, dict]:
    """``alias -> [models."<alias>"]`` table, in the CLI's documented shape
    (providers.html:75-78: ``provider`` / ``model`` / ``max_context_size``)."""
    return {alias: {"provider": PROVIDER, "model": model_id, "max_context_size": ctx}
            for alias, model_id, ctx in MODELS}


def _toml_value(value) -> str:
    # Integers stay bare; strings become TOML basic strings — JSON escaping is a
    # strict subset of TOML's for the ASCII values rendered here.
    return str(value) if isinstance(value, int) else json.dumps(value)


def _render_table(header: str, table: dict) -> str:
    lines = [f"[{header}]"] + [f"{key} = {_toml_value(value)}" for key, value in table.items()]
    return "\n".join(lines) + "\n"


def managed_block() -> str:
    """The full fence, MANAGED_BEGIN … MANAGED_END, tables only (no root keys)."""
    head = "\n".join((
        MANAGED_BEGIN,
        "# Written by GRIP (lib/kimi_hal_route.py, run by bin/grip-kimi). Routes the",
        "# aliases below through the HAL gateway on 127.0.0.1:4010. api_key is a",
        "# non-secret doorstop: the gateway discards it and uses its own keychain keys.",
        "# Keep your own providers OUTSIDE this block; a re-run rewrites only this span.",
    )) + "\n"
    tables = [_render_table(f"providers.{PROVIDER}", provider_table())]
    tables += [_render_table(f'models."{alias}"', table) for alias, table in model_tables().items()]
    return head + "\n".join(tables) + MANAGED_END + "\n"


def _parse(text: str, what: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{what} does not parse as TOML: {exc}") from exc


def _refuse_if_claimed_outside_fence(outside: dict) -> None:
    providers = outside.get("providers")
    if isinstance(providers, dict) and PROVIDER in providers:
        raise ValueError(
            f"[providers.{PROVIDER}] is already declared outside the GRIP-managed block; "
            "rename or remove yours before letting GRIP manage the hal route")
    models = outside.get("models")
    taken = sorted(model_tables()) if not isinstance(models, dict) else sorted(
        alias for alias in model_tables() if alias in models)
    if isinstance(models, dict) and taken:
        raise ValueError(f"model alias table(s) {taken} already declared outside the GRIP-managed block")


def merge_config(existing: str) -> tuple[str, bool]:
    """Fold the managed block into ``existing``; returns ``(merged, changed)``.

    :raises ValueError: instead of returning anything writable when the file is
        corrupt, a fence is torn, or the operator already owns ``[providers.hal]``
        or one of our alias tables outside the fence. Nothing is repaired by guess.
    """
    if has_truncated_fence(existing):
        raise ValueError("a GRIP-MANAGED block opens but never closes; repair the file by hand first")
    outside = _parse(_excise_managed_block(existing), "config.toml outside the GRIP-managed block")
    _refuse_if_claimed_outside_fence(outside)
    merged = merge_managed_toml(existing, managed_block())
    _parse(merged, "the merged config.toml")
    return merged, merged != existing


def install(path: Path | None = None) -> tuple[Path, bool]:
    """Write the route into ``path``, only when it would actually change.

    :returns: ``(path, wrote)``. The write is atomic (temp file + rename).
    """
    target = path or default_config_path()
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    merged, changed = merge_config(existing)
    if not changed:
        return target, False
    atomic_write_text(target, merged)
    return target, True


def main(argv: list[str]) -> int:
    """CLI: ``python3 lib/kimi_hal_route.py [<config.toml>]``."""
    target = Path(argv[1]).expanduser() if len(argv) > 1 else None
    try:
        written, wrote = install(target)
    except (ValueError, OSError) as exc:
        print(f"[grip-kimi] refusing to write: {exc}", file=sys.stderr)
        return 1
    print(f"[grip-kimi] {'wrote' if wrote else 'already current'}: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
