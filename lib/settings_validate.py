#!/usr/bin/env python3
"""Settings integrity validator — the bullet-proof guard for settings.json.

`lib/settings_merge.py` re-applies the GRIP-managed block from the canonical
`config/settings.reference.json` on every install/update. But BETWEEN updates,
`settings.json` can be silently clobbered — a stray overwrite by another tool,
a bad write, a concurrent process. That is exactly what happened on 2026-06-25:
the whole GRIP-managed block (env, permissions, marketplaces, the
canonical-output-style keys) was stripped, leaving a near-empty file. The only
guard at the time was a hook-EVENT-COUNT check, and the hooks happened to
survive — so the clobber went undetected.

This validator closes that hole. It NEVER mutates anything — it reports.
Healing is `bash scripts/install.sh --update`, which re-runs the merge.

Checks, against the canonical reference:
  1. both files are valid JSON objects;
  2. every GRIP-managed key is PRESENT in settings.json;
  3. settings.json has >= the reference's hook-event count (a drop == the GRIP
     hook block was clobbered);
  4. every SCALAR GRIP-managed value MATCHES the reference (a flip/drift of
     skipDangerousModePermissionPrompt, alwaysThinkingEnabled, outputStyle, ...
     is caught — settings.json may hold EXTRA keys, never WRONG managed ones).

DRY: the managed-key set is imported from settings_merge — one source of truth,
no second list to drift out of sync.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from settings_merge import GRIP_MANAGED_KEYS  # noqa: E402  (DRY: single source of truth)
from claude_dir import get_claude_dir  # noqa: E402
from hooks_registry import (  # noqa: E402
    compile_claude_hooks,
    group_is_externally_installed,
    load_registry,
)

# Managed keys that are dicts or cosmetic — checked for PRESENCE only, never
# value-compared (settings.json legitimately holds a larger env/hooks/statusLine
# than the reference, and _comment is free text).
_PRESENCE_ONLY = frozenset({"_comment", "env", "hooks", "statusLine"})

# Scalar managed keys whose value MUST equal the canonical reference.
VALUE_KEYS = frozenset(GRIP_MANAGED_KEYS) - _PRESENCE_ONLY


def _hook_event_count(obj: dict) -> int:
    """Number of hook event types (PreToolUse, PostToolUse, ...) declared."""
    hooks = obj.get("hooks")
    return len(hooks) if isinstance(hooks, dict) else 0


def _check_presence(settings: dict) -> list[str]:
    return [
        f"settings.json is missing GRIP-managed key '{k}' "
        "(the canonical block looks clobbered)"
        for k in sorted(GRIP_MANAGED_KEYS)
        if k not in settings
    ]


def _check_hook_floor(settings: dict, reference: dict) -> list[str]:
    actual, expected = _hook_event_count(settings), _hook_event_count(reference)
    if actual < expected:
        return [
            f"settings.json has {actual} hook event types but the canonical "
            f"reference expects at least {expected} (GRIP hook block clobbered)"
        ]
    return []


def _handler_schema_problems(hp: str, handler: object) -> list[str]:
    """Schema problems for ONE handler entry, named by its path prefix.

    Split out of _hook_binding_counter, which was doing two jobs -- validating
    shape and counting bindings -- and had reached CC 29 against a ceiling of
    25. Counting is the caller's job; deciding whether an entry is well formed
    is this one's.
    """
    if not isinstance(handler, dict):
        return [f"{hp} must be an object"]
    problems: list[str] = []
    if handler.get("type") != "command":
        problems.append(f"{hp}.type must be 'command'")
    command = handler.get("command")
    if not isinstance(command, str) or not command.strip():
        problems.append(f"{hp}.command must be a non-empty string")
    timeout = handler.get("timeout")
    if "timeout" in handler and (
        not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or timeout <= 0
    ):
        problems.append(f"{hp}.timeout must be a positive number")
    if "async" in handler and not isinstance(handler["async"], bool):
        problems.append(f"{hp}.async must be boolean")
    return problems


def _hook_binding_counter(hooks: object) -> tuple[Counter, list[str]]:
    """Return exact per-handler binding counts plus schema problems."""
    counter: Counter = Counter()
    problems: list[str] = []
    if not isinstance(hooks, dict):
        return counter, ["settings.json 'hooks' must be an object"]
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            problems.append(f"hooks.{event} must be a list")
            continue
        for group_index, group in enumerate(groups):
            prefix = f"hooks.{event}[{group_index}]"
            if not isinstance(group, dict):
                problems.append(f"{prefix} must be an object")
                continue
            handlers = group.get("hooks")
            # A group whose handlers all belong to another product is not ours
            # to judge. settings.json is a shared file: a third-party tool may
            # write a matcher in its own dialect -- a bare "*" meaning "any",
            # which is not a valid regex -- and GRIP cannot fix that hook and
            # must not fail its own integrity check over it. This is the same
            # ownership rule partition_external() applies when it drops
            # all-foreign groups from the tree the registry has to reproduce;
            # applying it here too keeps the two checks consistent. A group
            # with even one handler this repo ships is still fully validated,
            # so a GRIP matcher can never hide behind a foreign neighbour.
            foreign_group = group_is_externally_installed(group)
            matcher = group.get("matcher")
            if matcher is not None and not foreign_group:
                if not isinstance(matcher, str):
                    problems.append(f"{prefix}.matcher must be a string")
                else:
                    try:
                        re.compile(matcher)
                    except re.error as exc:
                        problems.append(f"{prefix}.matcher is invalid: {exc}")
            if not isinstance(handlers, list) or not handlers:
                problems.append(f"{prefix}.hooks must be a non-empty list")
                continue
            group_fields = {key: value for key, value in group.items() if key != "hooks"}
            group_key = json.dumps(group_fields, sort_keys=True, separators=(",", ":"))
            for handler_index, handler in enumerate(handlers):
                hp = f"{prefix}.hooks[{handler_index}]"
                problems.extend(_handler_schema_problems(hp, handler))
                if not isinstance(handler, dict):
                    continue
                handler_key = json.dumps(handler, sort_keys=True, separators=(",", ":"))
                counter[(event, group_key, handler_key)] += 1
    return counter, problems


def _check_hook_bindings(settings: dict, expected_hooks: dict) -> list[str]:
    actual, problems = _hook_binding_counter(settings.get("hooks"))
    expected, expected_problems = _hook_binding_counter(expected_hooks)
    if expected_problems:
        return [
            "canonical expected hook tree is invalid: " + problem
            for problem in expected_problems
        ]
    for binding, count in expected.items():
        if actual[binding] >= count:
            continue
        event, _group_key, handler_key = binding
        handler = json.loads(handler_key)
        problems.append(
            f"settings.json is missing canonical {event} hook binding "
            f"{handler.get('command', '<unknown>')!r} "
            f"(expected {count}, found {actual[binding]})"
        )
    return problems


def _check_scalar_values(settings: dict, reference: dict) -> list[str]:
    return [
        f"settings.json '{k}' is {settings.get(k)!r} but canonical is "
        f"{reference[k]!r} (GRIP-managed value drifted)"
        for k in sorted(VALUE_KEYS)
        if k in reference and settings.get(k) != reference[k]
    ]


def validate(
    settings: dict,
    reference: dict,
    *,
    expected_hooks: dict | None = None,
) -> list[str]:
    """Return a list of plain-English problems. Empty list == healthy."""
    return (
        _check_presence(settings)
        + _check_hook_floor(settings, reference)
        + _check_hook_bindings(
            settings,
            reference.get("hooks", {}) if expected_hooks is None else expected_hooks,
        )
        + _check_scalar_values(settings, reference)
    )


def _load_object(path: Path) -> dict:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("top-level JSON is not an object")
    return data


def run(
    settings_path: Path,
    reference_path: Path,
    *,
    registry_path: Path | None = None,
    profile: str = "install",
) -> list[str]:
    """Load both files and validate; any read/parse error becomes a problem."""
    try:
        reference = _load_object(reference_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"cannot read canonical reference {reference_path}: {exc}"]
    try:
        settings = _load_object(settings_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"settings.json is unreadable / invalid JSON ({settings_path}): {exc}"]
    expected_hooks = None
    if registry_path is not None:
        try:
            expected_hooks = compile_claude_hooks(
                load_registry(registry_path), profile
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return [f"cannot read canonical hook registry {registry_path}: {exc}"]
    return validate(settings, reference, expected_hooks=expected_hooks)


def main() -> int:
    claude_dir = get_claude_dir()
    parser = argparse.ArgumentParser(
        description="Validate settings.json against the canonical reference."
    )
    parser.add_argument("--settings", default=str(claude_dir / "settings.json"))
    parser.add_argument(
        "--reference",
        default=str(claude_dir / "config" / "settings.reference.json"),
    )
    parser.add_argument(
        "--registry",
        default=str(claude_dir / "config" / "hooks.registry.json"),
    )
    parser.add_argument(
        "--profile",
        choices=("production", "install"),
        default="install",
    )
    args = parser.parse_args()

    problems = run(
        Path(args.settings),
        Path(args.reference),
        registry_path=Path(args.registry),
        profile=args.profile,
    )
    if problems:
        print("settings.json FAILED integrity check:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "  heal: bash scripts/install.sh --update  "
            "(re-applies the canonical GRIP block)",
            file=sys.stderr,
        )
        return 1
    print("settings.json OK — canonical GRIP block intact, no drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
