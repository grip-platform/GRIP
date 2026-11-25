#!/usr/bin/env python3
"""Compile GRIP hook configuration from one canonical policy registry.

Wave 0 preserves two explicit Claude Code profiles:

``production``
    The tracked, currently exercised ``settings.json`` hook tree.

``install``
    The hook tree installed from ``config/settings.reference.json``.

Bindings retain their native dictionaries verbatim. Policy metadata is shared
by stable policy ID, so later harness adapters can migrate one policy at a time
without another configuration inventory.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from settings_merge import extract_hook_identity


SCHEMA_VERSION = "grip-hooks/1.0"
PROFILES = ("production", "install")
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = REPO_ROOT / "config" / "hooks.registry.json"
DEFAULT_REFERENCE_BASE = REPO_ROOT / "config" / "settings.reference.base.json"
DEFAULT_REFERENCE = REPO_ROOT / "config" / "settings.reference.json"
DEFAULT_CLASSIFICATION = REPO_ROOT / "config" / "hook-classification.json"
DEFAULT_MANIFEST = REPO_ROOT / "hooks" / "manifest.json"
DEFAULT_DOCUMENTATION = REPO_ROOT / "hooks" / "GENERATED-HOOKS.md"

CLASSIFICATIONS = frozenset({"hard", "soft", "advisory"})
FAILURE_MODES = frozenset({"closed", "open"})
DISPOSITIONS = frozenset(
    {"migrate", "legacy-adapter", "intentionally-inactive", "retire"}
)

# These active blocking policies predate complete classification metadata.
# The list is used only by the one-time importer. The committed registry is
# authoritative after bootstrap.
HARD_POLICY_IDENTITIES = frozenset(
    {
        "agent-dispatch-validator-hook.py",
        "cc-gate-hook.py",
        "channel-scope-gate.py",
        "closed-loop-guard.py",
        "concurrent-commit-guard-hook.py",
        "context-gate-hook.py",
        "cross-org-comms-hook.py",
        "dispatch-economics-pretool-hook.py",
        "elicitation-result-hook.py",
        "gh-default-repo-gate.py",
        "gitcrypt-staged-guard.py",
        "grip-first-retrieval-hook.py",
        "grip-first-web-hook.py",
        "grip-hal-repo-protection.py",
        "hitl-quality-hook.py",
        "memory-guard.sh",
        "no-stubs-gate.py",
        "phantom-chase-breaker.py",
        "pre-check-hook.py",
        "premise-reproduce-gate.py",
        "quality-pretool-hook.py",
        "recall-preassert-gate.py",
        "recall-toolresult-gate.py",
        "relay-context-firewall.py",
        "relay-listener-principal-classifier.py",
        "relay-write-scope-gate.py",
        "sensitive-info-guard.py",
        "user-prompt-secrets-hook.py",
    }
)

TEST_OVERRIDES = {
    "agent-dispatch-validator-hook.py": "tests/test_agent_dispatch_validator_hook.py",
    "cc-gate-hook.py": "tests/test_cc_gate.py",
    "channel-scope-gate.py": "tests/test_channel_scope_gate.py",
    "closed-loop-guard.py": "tests/test_closed_loop_guard.py",
    "context-gate-hook.py": "tests/test_context_gate_coordinator.py",
    "elicitation-hook.py": "tests/test_elicitation_hooks.py",
    "elicitation-result-hook.py": "tests/test_elicitation_hooks.py",
    "gh-default-repo-gate.py": "tests/test_gh_default_repo_gate.py",
    "gitcrypt-staged-guard.py": "tests/test_gitcrypt_guards.py",
    "grip-first-retrieval-hook.py": "tests/test_grip_first_retrieval_hook.py",
    "grip-first-web-hook.py": "tests/test_grip_first_web_hook.py",
    "grip-hal-repo-protection.py": "tests/test_grip_hal_repo_protection.py",
    "no-stubs-gate.py": "tests/test_no_stubs_gate.py",
    "pre-check-hook.py": "tests/test_pre_check_hook.py",
    "quality-pretool-hook.py": "tests/test_quality_pretool_differential.py",
    "relay-context-firewall.py": "tests/test_relay_context_firewall.py",
    "sensitive-info-guard.py": "tests/test_sensitive_info_guard.py",
    "user-prompt-secrets-hook.py": "tests/test_user_prompt_secrets_hook.py",
}


class RegistryError(ValueError):
    """The canonical registry is malformed or internally inconsistent."""


def _load_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RegistryError(f"{path}: top-level JSON must be an object")
    return data


def _json_text(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _content_id(prefix: str, value: object, length: int = 16) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}.{digest}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "hook"


def hook_identity(hook: dict[str, Any]) -> str:
    command = hook.get("command", "")
    if isinstance(command, str) and command.strip():
        identity = extract_hook_identity(command)
        if identity and identity not in {"true", "false"}:
            return identity
    return _content_id("inline", hook, 12)


def policy_id_for(identity: str) -> str:
    return f"policy.{_slug(identity)}"


def repo_script_for(identity: str) -> Path | None:
    """The tracked script backing a hook identity, or ``None`` when this repo
    does not ship one (an inline handler, or a hook installed by another
    product)."""
    if identity.startswith("inline."):
        return None
    # bin/ joins hooks/ and lib/: it is tracked, it ships with GRIP, and a
    # hook may legitimately invoke an operator-facing command there
    # (bin/grip-chime is the first). Omitting it classified a repo-owned
    # script as EXTERNALLY INSTALLED, which put it in the same bucket as
    # another product's subspace hooks and failed the externals contract.
    for directory in ("hooks", "lib", "bin"):
        candidate = REPO_ROOT / directory / identity
        if candidate.is_file():
            return candidate
    return None


def owns_hook(hook: dict[str, Any]) -> bool:
    """True when this repo ships the handler, or it is inline in the wiring.

    An operator's machine may run hooks GRIP does not ship — another product's
    installer appends its own handlers to ``settings.json``. Those are real and
    must keep running, but they are not GRIP's to declare: the registry would be
    claiming a script this repo cannot verify, and their commands carry the
    installing machine's absolute path, which must never be committed
    (``rules/path-portability.md``, and the repo mirrors to a client org).
    """
    identity = hook_identity(hook)
    return identity.startswith("inline.") or repo_script_for(identity) is not None


# The directories this repo ships handlers from. The test below is "is the
# script under one of THESE", not "is it under REPO_ROOT" -- the difference is
# not academic on a real machine.
#
# Another product installed its hooks at ~/.claude/subspace-hooks/, and
# ~/.claude is a symlink to the GRIP checkout. Path.resolve() follows it, so
# those scripts resolve INSIDE REPO_ROOT whenever this module is loaded from
# the shared tree -- and a root-relative test then called six foreign handlers
# ours, applying GRIP's matcher and script-existence contract to them. The same
# module loaded from a worktree got it right, so the defect appeared only in a
# full-suite run and looked like test pollution.
#
# hooks/, lib/ and bin/ are exactly where repo_script_for() looks, so the two
# ownership questions agree on which directories count. bin/ was added when
# a hook first needed to invoke an operator-facing command (bin/grip-chime).
_HANDLER_DIRS = (REPO_ROOT / "hooks", REPO_ROOT / "lib", REPO_ROOT / "bin")


def is_externally_installed(hook: dict[str, Any]) -> bool:
    """True when the command runs a script by ABSOLUTE path outside this checkout.

    This complements owns_hook() rather than duplicating it, because the two
    answer questions that only look alike. owns_hook asks "does this repo ship
    the handler", and answers it by the file being present -- which is exactly
    the WRONG question for a caller checking whether a shipped script has gone
    MISSING: a deleted GRIP hook would classify as not-ours and be skipped by
    the very check meant to catch its deletion.

    This asks something independent of file existence. GRIP writes its own hook
    commands ${CLAUDE_DIR}-relative, so a command naming an absolute path that
    resolves outside this checkout was installed by another product. A GRIP
    hook whose script was deleted still reads as ours, and still fails.
    """
    command = hook.get("command", "")
    if not isinstance(command, str) or "${CLAUDE_DIR" in command:
        return False
    for candidate in _absolute_scripts(command):
        try:
            resolved = Path(candidate).resolve()
        except OSError:  # pragma: no cover - unresolvable path is not evidence
            continue
        if not any(resolved.is_relative_to(d) for d in _HANDLER_DIRS):
            return True
    return False


def _absolute_scripts(command: str):
    """Yield absolute script paths named anywhere in a hook command."""
    cleaned = command.replace("'", " ").replace('"', " ")
    for token in cleaned.split():
        if token.startswith("/") and token.endswith((".sh", ".py")):
            yield token


def group_is_externally_installed(group: dict) -> bool:
    """True when EVERY handler in a hook group belongs to another product.

    One definition, because two callers need this exact question and had
    written it twice with DIFFERENT predicates: the validator asked
    ``not owns_hook`` and the status-message tests asked
    ``is_externally_installed``. Those agree on today's settings.json (measured:
    0 groups disagree) and diverge on precisely the case that matters -- a GRIP
    hook whose script was deleted, which owns_hook calls foreign and this does
    not. A latent disagreement that only appears once something is already
    broken is worse than a visible one, so the narrower predicate wins and both
    callers share it.

    An empty group is not "all foreign": there is nothing to own.
    """
    handlers = group.get("hooks") if isinstance(group, dict) else None
    if not isinstance(handlers, list) or not handlers:
        return False
    return all(isinstance(h, dict) and is_externally_installed(h) for h in handlers)


def partition_external(hooks: dict) -> tuple[dict[str, list[dict]], list[tuple[str, str]]]:
    """Split a native hook tree into the wiring this repo owns and the handlers
    it does not ship.

    Returns ``(owned_tree, [(event, identity), ...])``. A group whose handlers
    are all external drops out of the owned tree entirely rather than remaining
    as an empty group, so the result is a tree the compiler can reproduce.
    """
    owned: dict[str, list[dict]] = {}
    external: list[tuple[str, str]] = []
    for event, groups in hooks.items():
        kept_groups = []
        for group in groups:
            kept = [hook for hook in group.get("hooks", []) if owns_hook(hook)]
            external += [
                (event, hook_identity(hook))
                for hook in group.get("hooks", [])
                if not owns_hook(hook)
            ]
            if kept:
                kept_group = {k: v for k, v in group.items() if k != "hooks"}
                kept_group["hooks"] = kept
                kept_groups.append(kept_group)
        if kept_groups:
            owned[event] = kept_groups
    return owned, external


def iter_bindings(registry: dict[str, Any]):
    for event, groups in registry.get("claude", {}).get("events", {}).items():
        for group in groups:
            for binding in group.get("bindings", []):
                yield event, group, binding


def _validate_policy(policy_id: str, policy: object) -> list[str]:
    prefix = f"policies.{policy_id}"
    if not isinstance(policy, dict):
        return [f"{prefix} must be an object"]
    problems = []
    checks = (
        (policy.get("owner") == "GRIP", f"{prefix}.owner must be 'GRIP'"),
        (
            policy.get("classification") in CLASSIFICATIONS,
            f"{prefix}.classification is invalid",
        ),
        (
            policy.get("failure_mode") in FAILURE_MODES,
            f"{prefix}.failure_mode is invalid",
        ),
        (policy.get("status") in DISPOSITIONS, f"{prefix}.status is invalid"),
        (
            isinstance(policy.get("purpose"), str) and bool(policy["purpose"]),
            f"{prefix}.purpose must be non-empty",
        ),
        (
            isinstance(policy.get("legacy_hal_dispatch"), bool),
            f"{prefix}.legacy_hal_dispatch must be boolean",
        ),
    )
    problems.extend(message for valid, message in checks if not valid)
    verification = policy.get("verification")
    if not isinstance(verification, list) or not verification:
        problems.append(f"{prefix}.verification must be non-empty")
    elif (REPO_ROOT / "tests").is_dir():
        problems.extend(
            f"{prefix}.verification target missing: {test}"
            for test in verification
            if not (REPO_ROOT / test).is_file()
        )
    return problems


def _validate_unique_id(
    value: object, prefix: str, seen: set[str]
) -> list[str]:
    if not isinstance(value, str) or not value:
        return [f"{prefix}.id must be non-empty"]
    if value in seen:
        return [f"{prefix}.id duplicates {value}"]
    seen.add(value)
    return []


def _validate_binding(
    binding: object,
    prefix: str,
    policies: dict[str, Any],
    seen_binding_ids: set[str],
) -> list[str]:
    if not isinstance(binding, dict):
        return [f"{prefix} must be an object"]
    problems = _validate_unique_id(binding.get("id"), prefix, seen_binding_ids)
    if binding.get("policy_id") not in policies:
        problems.append(f"{prefix}.policy_id is unknown")
    profiles = binding.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        problems.append(f"{prefix}.profiles must be non-empty")
    elif any(profile not in PROFILES for profile in profiles):
        problems.append(f"{prefix}.profiles has unknown profile")
    if binding.get("disposition") not in DISPOSITIONS:
        problems.append(f"{prefix}.disposition is invalid")
    if not isinstance(binding.get("profile_order"), dict):
        problems.append(f"{prefix}.profile_order must be an object")
    hook = binding.get("hook")
    if not isinstance(hook, dict):
        problems.append(f"{prefix}.hook must be an object")
    elif hook.get("type") != "command" or not isinstance(hook.get("command"), str):
        problems.append(f"{prefix}.hook must be a command hook")
    if not isinstance(binding.get("required", {}), dict):
        problems.append(f"{prefix}.required must be an object")
    return problems


def _validate_group(
    event: str,
    index: int,
    group: object,
    policies: dict[str, Any],
    seen_group_ids: set[str],
    seen_binding_ids: set[str],
) -> list[str]:
    prefix = f"{event}[{index}]"
    if not isinstance(group, dict):
        return [f"{prefix} must be an object"]
    problems = _validate_unique_id(group.get("id"), prefix, seen_group_ids)
    if not isinstance(group.get("group"), dict):
        problems.append(f"{prefix}.group must be an object")
    profile_order = group.get("profile_order")
    if not isinstance(profile_order, dict) or not profile_order:
        problems.append(f"{prefix}.profile_order must be non-empty")
    elif any(profile not in PROFILES for profile in profile_order):
        problems.append(f"{prefix}.profile_order has unknown profile")
    bindings = group.get("bindings")
    if not isinstance(bindings, list) or not bindings:
        problems.append(f"{prefix}.bindings must be non-empty")
        return problems
    for binding_index, binding in enumerate(bindings):
        problems.extend(
            _validate_binding(
                binding,
                f"{prefix}.bindings[{binding_index}]",
                policies,
                seen_binding_ids,
            )
        )
    return problems


def validate_registry(registry: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if registry.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION!r}")
    policies = registry.get("policies")
    events = registry.get("claude", {}).get("events")
    orders = registry.get("claude", {}).get("profile_event_order")
    if not isinstance(policies, dict) or not policies:
        problems.append("policies must be a non-empty object")
        return problems
    if not isinstance(events, dict) or not events:
        problems.append("claude.events must be a non-empty object")
        return problems
    if not isinstance(orders, dict):
        problems.append("claude.profile_event_order must be an object")

    for policy_id, policy in policies.items():
        problems.extend(_validate_policy(policy_id, policy))

    seen_group_ids: set[str] = set()
    seen_binding_ids: set[str] = set()
    for event, groups in events.items():
        if not isinstance(groups, list):
            problems.append(f"{event}: groups must be a list")
            continue
        for index, group in enumerate(groups):
            problems.extend(
                _validate_group(
                    event,
                    index,
                    group,
                    policies,
                    seen_group_ids,
                    seen_binding_ids,
                )
            )

    for profile in PROFILES:
        if not isinstance(orders.get(profile) if isinstance(orders, dict) else None, list):
            problems.append(f"profile_event_order.{profile} must be a list")
    return problems


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    registry = _load_object(path)
    problems = validate_registry(registry)
    if problems:
        raise RegistryError("\n".join(problems))
    return registry


def compile_claude_hooks(
    registry: dict[str, Any], profile: str
) -> dict[str, list[dict]]:
    if profile not in PROFILES:
        raise RegistryError(f"unknown profile: {profile}")
    events = registry["claude"]["events"]
    result: dict[str, list[dict]] = {}
    for event in registry["claude"]["profile_event_order"][profile]:
        groups = [
            group for group in events.get(event, []) if profile in group["profile_order"]
        ]
        groups.sort(key=lambda group: group["profile_order"][profile])
        compiled_groups = []
        for group in groups:
            bindings = [
                binding
                for binding in group["bindings"]
                if profile in binding["profiles"]
                and binding["disposition"] not in {"intentionally-inactive", "retire"}
            ]
            bindings.sort(key=lambda binding: binding["profile_order"][profile])
            if not bindings:
                continue
            native_group = copy.deepcopy(group["group"])
            native_group["hooks"] = [copy.deepcopy(binding["hook"]) for binding in bindings]
            compiled_groups.append(native_group)
        if compiled_groups:
            result[event] = compiled_groups
    return result


def render_settings_reference(
    registry: dict[str, Any], reference_base: dict[str, Any]
) -> dict[str, Any]:
    rendered = copy.deepcopy(reference_base)
    rendered["hooks"] = compile_claude_hooks(registry, "install")
    return rendered


def render_classification(registry: dict[str, Any]) -> dict[str, Any]:
    hard: dict[str, dict] = {}
    soft: dict[str, dict] = {}
    for policy_id, policy in registry["policies"].items():
        if not policy["legacy_hal_dispatch"]:
            continue
        detail = copy.deepcopy(policy.get("legacy_classification_detail", {}))
        detail["policy_id"] = policy_id
        detail["failure_mode"] = policy["failure_mode"]
        detail["owner"] = policy["owner"]
        target = hard if policy["classification"] == "hard" else soft
        target[policy["identity"]] = detail
    return {
        "_comment": (
            "GENERATED from config/hooks.registry.json. This compatibility "
            "projection retains only policies used by the legacy HAL dispatcher."
        ),
        "version": "2.0.0",
        "hard": {
            "description": "Blocking/security policies consumed by legacy HAL dispatch.",
            "hooks": dict(sorted(hard.items())),
        },
        "soft": {
            "description": "Advisory policies consumed by legacy HAL dispatch.",
            "hooks": dict(sorted(soft.items())),
        },
    }


def render_manifest(registry: dict[str, Any]) -> dict[str, Any]:
    required = []
    for event, _group, binding in iter_bindings(registry):
        metadata = binding.get("required", {})
        if not metadata or "production" not in binding["profiles"]:
            continue
        required.append(
            {
                "event": event,
                "hook_file": registry["policies"][binding["policy_id"]]["identity"],
                "description": metadata["description"],
                "policy_id": binding["policy_id"],
            }
        )
    return {
        "_comment": (
            "GENERATED from config/hooks.registry.json. Every required policy "
            "must be present in both production and install profiles."
        ),
        "required": required,
    }


def render_documentation(registry: dict[str, Any]) -> str:
    policy_rows = []
    for policy_id, policy in sorted(registry["policies"].items()):
        policy_rows.append(
            f"| `{policy_id}` | `{policy['identity']}` | "
            f"`{policy['classification']}` | `{policy['failure_mode']}` | "
            f"`{policy['status']}` |"
        )
    binding_rows = []
    for event, group, binding in iter_bindings(registry):
        matcher = group["group"].get("matcher", "<absent>")
        binding_rows.append(
            f"| `{binding['id']}` | `{event}` | `{matcher}` | "
            f"`{','.join(binding['profiles'])}` | `{binding['disposition']}` |"
        )
    return "\n".join(
        [
            "# Generated hook inventory",
            "",
            "<!-- GENERATED by lib/hooks_registry.py; do not edit. -->",
            "",
            "## Policies",
            "",
            "| Policy | Entrypoint | Class | Failure | Status |",
            "|---|---|---|---|---|",
            *policy_rows,
            "",
            "## Claude Code bindings",
            "",
            "| Binding | Event | Matcher | Profiles | Disposition |",
            "|---|---|---|---|---|",
            *binding_rows,
            "",
        ]
    )


def rendered_outputs(
    registry: dict[str, Any], reference_base: dict[str, Any]
) -> dict[Path, str]:
    return {
        DEFAULT_REFERENCE: _json_text(
            render_settings_reference(registry, reference_base)
        ),
        DEFAULT_CLASSIFICATION: _json_text(render_classification(registry)),
        DEFAULT_MANIFEST: _json_text(render_manifest(registry)),
        DEFAULT_DOCUMENTATION: render_documentation(registry),
    }


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.replace(path)


def write_outputs(outputs: dict[Path, str], check: bool) -> list[str]:
    drift = []
    for path, expected in outputs.items():
        actual = path.read_text(encoding="utf-8") if path.exists() else None
        if actual == expected:
            continue
        drift.append(str(path.relative_to(REPO_ROOT)))
        if not check:
            _write_atomic(path, expected)
    return drift


def _classification_source(path: Path) -> dict[str, tuple[str, dict]]:
    result = {}
    if not path.exists():
        return result
    source = _load_object(path)
    for section in ("hard", "soft"):
        for identity, detail in source.get(section, {}).get("hooks", {}).items():
            result[identity] = (section, detail)
    return result


def _manifest_source(path: Path) -> dict[tuple[str, str], str]:
    if not path.exists():
        return {}
    return {
        (item["event"], item["hook_file"]): item["description"]
        for item in _load_object(path).get("required", [])
    }


def _verification(identity: str) -> list[str]:
    tests = ["tests/test_hooks_registry.py"]
    override = TEST_OVERRIDES.get(identity)
    if override and (REPO_ROOT / override).is_file():
        tests.append(override)
    else:
        candidate = f"tests/test_{Path(identity).stem.replace('-', '_')}.py"
        if (REPO_ROOT / candidate).is_file():
            tests.append(candidate)
    return tests


def _policy(
    identity: str,
    classifications: dict[str, tuple[str, dict]],
    *,
    status: str,
) -> dict[str, Any]:
    legacy = classifications.get(identity)
    if legacy:
        section, detail = legacy
        classification = "hard" if section == "hard" else "advisory"
    else:
        detail = {}
        classification = "hard" if identity in HARD_POLICY_IDENTITIES else "advisory"
    failure_mode = "closed" if classification == "hard" else "open"
    purpose = (
        detail.get("purpose")
        or ", ".join(detail.get("gates", []))
        or f"Canonical policy for {identity}"
    )
    return {
        "identity": identity,
        "owner": "GRIP",
        "classification": classification,
        "failure_mode": failure_mode,
        "purpose": purpose,
        "verification": _verification(identity),
        "status": status,
        "legacy_hal_dispatch": legacy is not None,
        "legacy_classification_detail": detail,
    }


def _profile_groups(hooks: dict, profile: str):
    for event_index, (event, groups) in enumerate(hooks.items()):
        for group_index, group in enumerate(groups):
            yield event, event_index, group_index, group


def _native_group_key(event: str, native_group: dict) -> str:
    group_body = {key: value for key, value in native_group.items() if key != "hooks"}
    return json.dumps(
        {"event": event, "group": group_body, "hooks": native_group["hooks"]},
        sort_keys=True,
        separators=(",", ":"),
    )


def _make_binding(
    event: str,
    group_id: str,
    hook: dict,
    duplicate: int,
) -> dict[str, Any]:
    return {
        "id": _content_id(
            f"binding.{_slug(event)}",
            {"group": group_id, "hook": hook, "duplicate": duplicate},
        ),
        "policy_id": policy_id_for(hook_identity(hook)),
        "profiles": [],
        "profile_order": {},
        "disposition": "legacy-adapter",
        "required": {},
        "hook": copy.deepcopy(hook),
    }


def _make_group(
    event: str,
    native_group: dict,
    policies: dict[str, dict],
    classifications: dict[str, tuple[str, dict]],
) -> dict[str, Any]:
    group_body = {key: value for key, value in native_group.items() if key != "hooks"}
    group_id = _content_id(
        f"group.{_slug(event)}",
        {"event": event, "group": group_body, "hooks": native_group["hooks"]},
    )
    group = {
        "id": group_id,
        "group": copy.deepcopy(group_body),
        "profile_order": {},
        "bindings": [],
    }
    duplicate_counts: defaultdict[str, int] = defaultdict(int)
    for hook in native_group["hooks"]:
        serialised = json.dumps(hook, sort_keys=True)
        duplicate_counts[serialised] += 1
        binding = _make_binding(
            event, group_id, hook, duplicate_counts[serialised]
        )
        group["bindings"].append(binding)
        identity = hook_identity(hook)
        policies.setdefault(
            binding["policy_id"],
            _policy(identity, classifications, status="legacy-adapter"),
        )
    return group


def _bind_group_to_profile(group: dict, profile: str, group_index: int) -> None:
    group["profile_order"][profile] = group_index
    for hook_index, binding in enumerate(group["bindings"]):
        binding["profiles"].append(profile)
        binding["profile_order"][profile] = hook_index


def _import_profile(
    profile: str,
    hooks: dict,
    policies: dict[str, dict],
    classifications: dict[str, tuple[str, dict]],
    groups_by_key: dict[str, dict],
    events: defaultdict[str, list[dict]],
) -> None:
    for event, _event_index, group_index, native_group in _profile_groups(
        hooks, profile
    ):
        group_key = _native_group_key(event, native_group)
        group = groups_by_key.get(group_key)
        if group is None:
            group = _make_group(event, native_group, policies, classifications)
            groups_by_key[group_key] = group
            events[event].append(group)
        _bind_group_to_profile(group, profile, group_index)


def _retain_classification_only_policies(
    policies: dict[str, dict],
    classifications: dict[str, tuple[str, dict]],
) -> None:
    for identity in classifications:
        policies.setdefault(
            policy_id_for(identity),
            _policy(
                identity,
                classifications,
                status="intentionally-inactive",
            ),
        )


def _matching_bindings(
    events: dict,
    policies: dict[str, dict],
    event: str,
    identity: str,
    profile: str,
) -> list[tuple[dict, dict]]:
    return [
        (group, binding)
        for candidate_event, group, binding in _iter_event_bindings(events)
        if candidate_event == event
        and policies[binding["policy_id"]]["identity"] == identity
        and profile in binding["profiles"]
    ]


def _promoted_install_group(
    event: str,
    identity: str,
    description: str,
    production_group: dict,
    production_binding: dict,
    events: dict,
) -> dict[str, Any]:
    next_order = 1 + max(
        (
            group["profile_order"]["install"]
            for group in events[event]
            if "install" in group["profile_order"]
        ),
        default=-1,
    )
    native_group = copy.deepcopy(production_group["group"])
    group_id = _content_id(
        f"group.{_slug(event)}.required-install",
        {"event": event, "group": native_group, "identity": identity},
    )
    binding = copy.deepcopy(production_binding)
    binding.update(
        {
            "id": _content_id(
                f"binding.{_slug(event)}.required-install",
                {"group": group_id, "hook": production_binding["hook"]},
            ),
            "profiles": ["install"],
            "profile_order": {"install": 0},
            "required": {"description": description},
        }
    )
    return {
        "id": group_id,
        "group": native_group,
        "profile_order": {"install": next_order},
        "bindings": [binding],
    }


def _apply_required_policies(
    events: dict,
    policies: dict[str, dict],
    required: dict[tuple[str, str], str],
) -> list[dict[str, str]]:
    promotions = []
    for (event, identity), description in required.items():
        production = _matching_bindings(
            events, policies, event, identity, "production"
        )
        if not production:
            raise RegistryError(
                f"required handler absent from production: {event}/{identity}"
            )
        production_group, production_binding = production[0]
        production_binding["required"] = {"description": description}
        if _matching_bindings(events, policies, event, identity, "install"):
            continue
        events[event].append(
            _promoted_install_group(
                event,
                identity,
                description,
                production_group,
                production_binding,
                events,
            )
        )
        promotions.append(
            {
                "event": event,
                "identity": identity,
                "reason": "required manifest policy absent from install profile",
            }
        )
    return promotions


def _event_orders(profile_hooks: dict, promotions: list[dict]) -> dict[str, list[str]]:
    orders = {profile: list(profile_hooks[profile]) for profile in PROFILES}
    for promotion in promotions:
        event = promotion["event"]
        if event not in orders["install"]:
            orders["install"].append(event)
    return orders


def bootstrap_registry(
    production_settings: dict[str, Any],
    install_reference: dict[str, Any],
    classifications: dict[str, tuple[str, dict]],
    required: dict[tuple[str, str], str],
) -> dict[str, Any]:
    """Import the union of all hook sources with explicit output membership."""
    # Only the wiring this repo ships is imported; a handler installed by
    # another product keeps running from settings.json but is never declared
    # here (see ``partition_external``).
    profile_hooks = {
        "production": partition_external(production_settings.get("hooks", {}))[0],
        "install": partition_external(install_reference.get("hooks", {}))[0],
    }
    policies: dict[str, dict] = {}
    groups_by_key: dict[str, dict] = {}
    events: defaultdict[str, list[dict]] = defaultdict(list)

    for profile in PROFILES:
        _import_profile(
            profile,
            profile_hooks[profile],
            policies,
            classifications,
            groups_by_key,
            events,
        )
    _retain_classification_only_policies(policies, classifications)
    promotions = _apply_required_policies(events, policies, required)
    profile_event_order = _event_orders(profile_hooks, promotions)

    registry = {
        "_comment": (
            "Canonical hook policy and binding registry. Generated views must "
            "be refreshed with python3 lib/hooks_registry.py generate."
        ),
        "schema_version": SCHEMA_VERSION,
        "profiles": {
            "production": "tracked settings.json hook tree",
            "install": "fresh-install settings.reference.json hook tree",
        },
        "migration": {
            "baseline_counts": {
                profile: _counts(profile_hooks[profile]) for profile in PROFILES
            },
            "install_promotions": promotions,
        },
        "policies": dict(sorted(policies.items())),
        "claude": {
            "profile_event_order": profile_event_order,
            "events": dict(events),
        },
    }
    problems = validate_registry(registry)
    if problems:
        raise RegistryError("\n".join(problems))
    return registry


def _iter_event_bindings(events):
    for event, groups in events.items():
        for group in groups:
            for binding in group["bindings"]:
                yield event, group, binding


def _counts(hooks: dict) -> dict[str, int]:
    return {
        "events": len(hooks),
        "groups": sum(len(groups) for groups in hooks.values()),
        "handlers": sum(
            len(group.get("hooks", []))
            for groups in hooks.values()
            for group in groups
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--check", action="store_true")
    generate.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    generate.add_argument("--base", type=Path, default=DEFAULT_REFERENCE_BASE)
    validate = commands.add_parser("validate")
    validate.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    bootstrap = commands.add_parser("bootstrap")
    bootstrap.add_argument("--settings", type=Path, default=REPO_ROOT / "settings.json")
    bootstrap.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    bootstrap.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION)
    bootstrap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    bootstrap.add_argument("--output", type=Path, default=DEFAULT_REGISTRY)
    bootstrap.add_argument("--base-output", type=Path, default=DEFAULT_REFERENCE_BASE)
    bootstrap.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "validate":
            load_registry(args.registry)
            print(f"{args.registry}: valid {SCHEMA_VERSION} registry")
            return 0
        if args.command == "bootstrap":
            if args.output.exists() and not args.force:
                raise RegistryError(
                    f"{args.output} exists; bootstrap is one-time (use --force explicitly)"
                )
            reference = _load_object(args.reference)
            registry = bootstrap_registry(
                _load_object(args.settings),
                reference,
                _classification_source(args.classification),
                _manifest_source(args.manifest),
            )
            base = {key: value for key, value in reference.items() if key != "hooks"}
            _write_atomic(args.output, _json_text(registry))
            _write_atomic(args.base_output, _json_text(base))
            print(f"bootstrapped {args.output} and {args.base_output}")
            return 0

        registry = load_registry(args.registry)
        outputs = rendered_outputs(registry, _load_object(args.base))
        drift = write_outputs(outputs, args.check)
        if drift and args.check:
            print("generated hook files are stale: " + ", ".join(drift), file=sys.stderr)
            return 1
        print("hook registry outputs already current" if not drift else "generated: " + ", ".join(drift))
        return 0
    except (OSError, json.JSONDecodeError, RegistryError) as exc:
        print(f"hook registry error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
