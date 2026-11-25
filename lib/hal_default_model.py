"""HAL default model for the activation wizard (Step 5.95, GRIP#7791).

Writes the operator's chosen HAL default, for example ``xai/grok-4.6``, so that
Codex, OpenCode and every other harness that routes through HAL serves it.

Two keys in HAL's ``config/providers.yaml`` decide what HAL serves, and both
are written. ``routing.default_model`` is what the direct strategy reads. The
head of ``routing.fallback_chain`` is what the shipped strategy,
``routing.default: fallback``, dispatches: ``lib/hal/router.py``
``_route_fallback`` walks the chain and never consults ``default_model``.
Writing only the first line leaves the old head in charge, which is the
surprise this step exists to close ("I chose Grok but Codex still used Groq").

HAL's home is resolved the way ``lib/hal/home.py`` resolves it: the
``HAL_HOME`` environment variable, else ``~/.hal``. GRIP's own install
directory says nothing about where HAL lives.

Design (CBC / KISS / no-stubs):
- The edit is targeted. Only the ``default_model`` line and the
  ``fallback_chain`` entry inside the top-level ``routing:`` block change, so
  every comment and every other key survives byte for byte. A same-named key
  under a provider or in another section is never touched.
- The result is re-parsed with PyYAML and checked before anything is written:
  ``default_model`` equals the choice, the chain head equals the choice, and
  the choice appears in the chain exactly once. A failed check raises and
  leaves the file alone.
- An address whose provider the file does not declare raises ``ValueError``
  rather than writing a route HAL cannot serve (Rule 7 made mechanical, the
  same posture as ``lib/grip_model.py``).
- ``state/hal-default-model.json`` is the wizard's own record of the choice
  (``set_by``, ``set_at``). HAL does not read it. The two YAML lines are what
  HAL reads.
- Writes are atomic (temp file + ``os.replace``) and keep the file's mode.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

#: The wizard's "leave HAL as it is" choice. Touches neither YAML key.
KEEP_EXISTING = "keep existing default_model"

_TOP_KEY_RE = re.compile(r"^routing:\s*(#.*)?$")
_DEFAULT_MODEL_RE = re.compile(r"^(\s*default_model:\s*)(\S.*?)\s*$")
_FLOW_CHAIN_RE = re.compile(r"^(\s*fallback_chain:\s*)\[(.*)\](\s*#.*)?$")
_BLOCK_CHAIN_RE = re.compile(r"^(\s*fallback_chain:\s*)(#.*)?$")
_BLOCK_ITEM_RE = re.compile(r"^(\s*)-\s*([^#\s][^#]*?)\s*(#.*)?$")


def resolve_hal_home(hal_home: Path | None = None) -> Path:
    """HAL's home: an explicit argument, else ``HAL_HOME``, else ``~/.hal``."""
    if hal_home is not None:
        return Path(hal_home)
    env = os.environ.get("HAL_HOME")
    return Path(env).expanduser() if env else Path.home() / ".hal"


def _routing_span(lines: list[str]) -> tuple[int, int]:
    """Index range [start, end) of the lines under the top-level ``routing:`` key."""
    start = next((i for i, line in enumerate(lines) if _TOP_KEY_RE.match(line)), None)
    if start is None:
        raise ValueError("providers.yaml has no top-level routing: block")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i] and not lines[i][0].isspace() and not lines[i].startswith("#"):
            end = i
            break
    return start + 1, end


def _reorder(entries: list[tuple[str, str]], choice: str) -> list[tuple[str, str]]:
    """``choice`` first (keeping its comment if it was listed), any later copy dropped."""
    chosen = next((entry for entry in entries if entry[0] == choice), (choice, ""))
    return [chosen] + [entry for entry in entries if entry[0] != choice]


def _rewrite_flow_chain(line: str, choice: str) -> str:
    """``fallback_chain: [a, b, c]`` with ``choice`` moved to the head."""
    match = _FLOW_CHAIN_RE.match(line)
    entries = [(item.strip(), "") for item in match.group(2).split(",") if item.strip()]
    items = ", ".join(bare for bare, _ in _reorder(entries, choice))
    return f"{match.group(1)}[{items}]{match.group(3) or ''}"


def _rewrite_block_chain(lines: list[str], at: int, choice: str) -> None:
    """Rewrite in place a block-style chain: the ``- item`` lines after ``at``."""
    first = at + 1
    last = first
    while last < len(lines) and _BLOCK_ITEM_RE.match(lines[last]):
        last += 1
    if last == first:
        raise ValueError("routing.fallback_chain is empty")
    matches = [_BLOCK_ITEM_RE.match(line) for line in lines[first:last]]
    indent = matches[0].group(1)
    entries = [(m.group(2), m.group(3) or "") for m in matches]
    lines[first:last] = [f"{indent}- {bare}" + (f"  {note}" if note else "")
                         for bare, note in _reorder(entries, choice)]


def rewrite_routing(text: str, choice: str) -> str:
    """Return ``text`` with ``routing.default_model`` set to ``choice`` and
    ``choice`` at the head of ``routing.fallback_chain``. Pure; touches
    nothing outside the ``routing:`` block."""
    lines = text.split("\n")
    start, end = _routing_span(lines)
    found_default = found_chain = False
    for i in range(start, end):
        if found_default and found_chain:
            break
        if not found_default and _DEFAULT_MODEL_RE.match(lines[i]):
            lines[i] = _DEFAULT_MODEL_RE.match(lines[i]).group(1) + choice
            found_default = True
        elif not found_chain and _FLOW_CHAIN_RE.match(lines[i]):
            lines[i] = _rewrite_flow_chain(lines[i], choice)
            found_chain = True
        elif not found_chain and _BLOCK_CHAIN_RE.match(lines[i]):
            _rewrite_block_chain(lines, i, choice)
            found_chain = True
    if not (found_default and found_chain):
        raise ValueError("routing: block lacks default_model or fallback_chain")
    return "\n".join(lines)


def _require_known_provider(data: dict, choice: str) -> None:
    """Refuse an address whose provider the catalogue does not declare."""
    providers = (data or {}).get("providers")
    if not isinstance(providers, list):
        return
    names = sorted(str(p.get("name")) for p in providers if isinstance(p, dict) and p.get("name"))
    provider = choice.split("/", 1)[0]
    if provider not in names:
        raise ValueError(f"unknown HAL provider {provider!r} in {choice!r}; "
                         f"declared: {', '.join(names)}")


def _check(text: str, choice: str) -> None:
    """Re-parse the rewritten text and confirm both keys say ``choice``."""
    routing = (yaml.safe_load(text) or {}).get("routing") or {}
    chain = list(routing.get("fallback_chain") or [])
    if routing.get("default_model") != choice or not chain or chain[0] != choice \
            or chain.count(choice) != 1:
        raise ValueError(f"rewrite check failed for {choice!r}: "
                         f"default_model={routing.get('default_model')!r} chain={chain!r}")


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a temp file, keeping the mode bits."""
    mode = path.stat().st_mode & 0o777 if path.exists() else None
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_state(grip_home: Path, choice: str | None) -> Path:
    """The wizard's record of the choice. HAL does not read this file."""
    state = grip_home / "state"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "hal-default-model.json"
    payload = {"default_model": choice, "set_by": "wizard",
               "set_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    _atomic_write(path, json.dumps(payload, indent=2) + "\n")
    return path


def _apply_to_providers(providers: Path, choice: str, result: dict) -> dict:
    """Edit the two routing lines of ``providers`` and report what changed."""
    text = providers.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    _require_known_provider(data, choice)
    routing = data.get("routing") or {}
    result["previous"] = {"default_model": routing.get("default_model"),
                          "chain_head": (list(routing.get("fallback_chain") or []) or [None])[0]}
    new_text = rewrite_routing(text, choice)
    _check(new_text, choice)
    if new_text != text:
        _atomic_write(providers, new_text)
        result["changed"] = True
    return result


def set_hal_default(choice: str, grip_home: Path | None = None,
                    hal_home: Path | None = None) -> dict:
    """Persist the operator's HAL default and return what was written.

    ``choice`` is a ``provider/model`` address or :data:`KEEP_EXISTING`.
    The result names the state file, HAL's ``providers.yaml`` and whether it
    was present, whether it changed, and the values it held before.
    """
    choice = (choice or "").strip()
    keep = choice == KEEP_EXISTING
    if not keep and "/" not in choice:
        raise ValueError(f"{choice!r} is not a provider/model address, "
                         f"and not {KEEP_EXISTING!r}")
    state = _write_state(Path(grip_home) if grip_home else Path(get_claude_dir()),
                         None if keep else choice)
    result = {"choice": None if keep else choice, "state_file": str(state),
              "providers_yaml": None, "providers_yaml_present": None,
              "changed": False, "previous": None}
    if keep:
        return result
    providers = resolve_hal_home(hal_home) / "config" / "providers.yaml"
    result["providers_yaml"] = str(providers)
    result["providers_yaml_present"] = providers.exists()
    if not providers.exists():
        return result
    return _apply_to_providers(providers, choice, result)
