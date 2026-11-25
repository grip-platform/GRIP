"""Headless GRIP, slice 2 -- the harness-neutral gate-equivalence layer.

An interactive GRIP session is guarded by HARD gates: PreToolUse hooks that
refuse a dangerous tool call before it runs (``classification: "hard"`` in
``config/hooks.registry.json``). A headless run must be refused the same
things. This module decides, BEFORE any harness runs, whether a given harness
with a given tool allowlist would be -- and refuses the run when it would not.

For every hard PreToolUse policy the registry binds, it reaches one verdict:

* ``locked`` -- no tool the run can call matches the policy's matcher, so the
  operation the policy guards is unreachable; the capability lock refuses it.
* ``hooked`` -- the harness runs GRIP's PreToolUse hooks, and the settings the
  child will load bind this policy's hook under the registry's own matcher; the
  same gate an interactive session has refuses it.
* ``uncovered`` -- the run could reach the operation and nothing would refuse it.

One ``uncovered`` policy refuses the whole run. That is the slice-2 invariant in
``~/.grip/drafts/headless-grip/ultrareason.md`` (sections 4 and 6): for every
(harness, denied operation) pair in the gate corpus, the headless run refuses
it, and "any harness kind for which the test cannot be written is not allowed
in the ladder". ``tests/test_headless_gate_equivalence.py`` is that test.

Which harness enforces what, each row traceable to a measurement or a file:

* ``claude-code`` -- GRIP's PreToolUse hooks fire under ``claude -p
  --permission-mode dontAsk``; slice 0 measured a hook deny holding there
  (``~/.grip/drafts/headless-grip/slice0-measurement.md``). Its tool surface is
  exactly the ``--tools`` allowlist, because ``--strict-mcp-config`` starts it
  with zero MCP servers (``lib/headless_run.py``).
* ``hal`` -- ``HALHarness`` is a bare provider call with no tool loop
  (``lib/harness/hal.py``), so it has no tool surface and every policy is locked.
* every other kind (``codex``, ``opencode``, ``gemini``, and any kind registered
  later) -- GRIP's gates are wired to Claude Code's hook events and nothing else
  invokes them (``bin/grip-gate``, module docstring). Codex can call
  ``bin/grip-gate`` from ``~/.codex/hooks.json``, but only after a human
  ``/hooks`` trust review, and grip-gate judges written content only, which is
  not this registry's whole hard set. OpenCode's permission deny-set is a
  capability lock, not GRIP's gates. Each runs its own shell, so no allowlist
  here bounds what it can reach: every policy is reachable and none is enforced.
  Such a kind is refused until it gains a mechanism a test can prove.

Fails closed. An unreadable registry or settings file, a registry with no hard
PreToolUse policy, or a settings file that disables all hooks refuses the run,
because a gate that cannot read its policy cannot call the run equivalent. This
cannot deadlock its own repair (``rules/gate-resolution-escape-hatch.md``,
Invariant 1): it refuses headless runs only, and fixing a settings file is not a
headless run.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, Sequence

__all__ = [
    "ENFORCEMENT",
    "REGISTRY_PATH",
    "Admission",
    "HardPolicy",
    "admit",
    "admit_from_disk",
    "hard_pretooluse_policies",
    "hooks_disabled",
    "live_pretooluse_bindings",
    "matcher_matches",
    "tool_surface",
    "user_settings_path",
]

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "config" / "hooks.registry.json"

# How each harness kind enforces GRIP's hard policies (see the module docstring).
# A kind absent from this table enforces nothing: adding one here is the step
# that admits it, and the forward anchor must prove the row before it lands.
ENFORCEMENT: dict[str, str] = {
    "claude-code": "pretooluse-hooks",
    "hal": "no-tools",
}


@dataclass(frozen=True)
class HardPolicy:
    """One hard PreToolUse binding: the policy, its matcher, its hook command."""

    policy_id: str
    matcher: str
    command: str


@dataclass(frozen=True)
class Admission:
    """The verdict for one harness kind and tool allowlist."""

    harness_kind: str
    verdicts: tuple = ()  # ((policy_id, "locked" | "hooked" | "uncovered"), ...)
    error: Optional[str] = None

    @property
    def uncovered(self) -> tuple:
        ids = (policy_id for policy_id, verdict in self.verdicts if verdict == "uncovered")
        return tuple(dict.fromkeys(ids))

    @property
    def ok(self) -> bool:
        return self.error is None and not self.uncovered

    def reason(self) -> str:
        if self.error:
            return f"gate-equivalence: {self.error}"
        if self.uncovered:
            return ("gate-equivalence: nothing would refuse what these hard "
                    f"policies guard under {self.harness_kind}: "
                    + ", ".join(self.uncovered))
        return "gate-equivalence: every hard policy is locked or hooked"


def _compiles(matcher: str) -> bool:
    try:
        re.compile(matcher)
    except re.error:
        return False
    return True


def matcher_matches(matcher: str, tool: str) -> bool:
    """Whether a Claude Code hook ``matcher`` selects ``tool``.

    Empty and ``*`` select every tool; anything else is a regex matched against
    the WHOLE tool name. Anchoring is the safe reading both ways: every name a
    full match selects is also selected by an unanchored search, so ``hooked``
    never claims a hook fires when it does not; and a ``locked`` policy guards
    only tools outside the allowlist, so a hook that also fires on an allowed
    tool can only add refusals. An invalid regex selects every tool, which can
    only push a verdict toward refusal.
    """
    if matcher in ("", "*"):
        return True
    if not _compiles(matcher):
        return True
    return re.fullmatch(matcher, tool) is not None


def tool_surface(harness_kind: str, tools: Sequence[str]) -> Optional[tuple]:
    """The tool names a run of this harness can call; ``None`` means unbounded."""
    enforcement = ENFORCEMENT.get(harness_kind, "none")
    if enforcement == "pretooluse-hooks":
        return tuple(tools)
    if enforcement == "no-tools":
        return ()
    return None


def _registry_matcher(group: Mapping) -> str:
    return (group.get("group") or {}).get("matcher", "")


def _settings_matcher(group: Mapping) -> str:
    return group.get("matcher", "")


def _flatten(groups: Iterable[Mapping], items_key: str,
             matcher_of: Callable[[Mapping], str]) -> Iterable[tuple]:
    """``(matcher, item)`` for every item of every hook group, each visited once.

    Hook groups form a two-level tree (group -> its bindings or hooks), so this
    is one linear pass over all items, never a cross product.
    """
    return chain.from_iterable(
        [(matcher_of(group), item) for item in group.get(items_key) or []]
        for group in groups
    )


def hard_pretooluse_policies(registry: Mapping) -> tuple:
    """Every hard policy the registry binds on PreToolUse, once per binding."""
    policies = registry.get("policies") or {}
    groups = ((registry.get("claude") or {}).get("events") or {}).get("PreToolUse") or []
    found: dict = {}
    for matcher, binding in _flatten(groups, "bindings", _registry_matcher):
        policy_id = binding.get("policy_id", "")
        if (policies.get(policy_id) or {}).get("classification") == "hard":
            command = (binding.get("hook") or {}).get("command", "")
            found.setdefault((policy_id, matcher, command), None)
    return tuple(HardPolicy(*key) for key in found)


def _refuses_every_matched_call(hook: Mapping) -> bool:
    """Whether a hook binding runs, and can block, on every call its matcher selects.

    Two binding fields break that, both quoted from code.claude.com/docs/en/hooks
    (read 2026-10-26). An ``if`` filter narrows the handler: "without it, every
    handler in the matched group runs", so with it some matched calls never reach
    the hook. ``async: true`` runs it in the background, and "Async hooks can't
    block or control Claude's behavior". Either way the gate would be credited for
    calls it cannot refuse, so such a binding does not count (Codex, GRIP#8668).
    """
    return "if" not in hook and hook.get("async") is not True


def live_pretooluse_bindings(settings_docs: Iterable[Mapping]) -> frozenset:
    """``(matcher, command)`` for every PreToolUse hook the settings files bind
    that refuses every call its matcher selects."""
    groups = chain.from_iterable(
        (doc.get("hooks") or {}).get("PreToolUse") or [] for doc in settings_docs)
    return frozenset((matcher, hook.get("command", ""))
                     for matcher, hook in _flatten(groups, "hooks", _settings_matcher)
                     if _refuses_every_matched_call(hook))


def hooks_disabled(settings_docs: Iterable[Mapping]) -> bool:
    """True when any settings file the child loads switches every hook off."""
    return any(doc.get("disableAllHooks") is True for doc in settings_docs)


def _verdict(policy: HardPolicy, surface: Optional[tuple], enforcement: str,
             bindings: frozenset, hooks_off: bool) -> str:
    if surface is not None and not any(matcher_matches(policy.matcher, t) for t in surface):
        return "locked"
    hooked = (enforcement == "pretooluse-hooks" and not hooks_off
              and _compiles(policy.matcher)
              and (policy.matcher, policy.command) in bindings)
    return "hooked" if hooked else "uncovered"


def admit(harness_kind: str, tools: Sequence[str], *, policies: Sequence[HardPolicy],
          bindings: frozenset, hooks_off: bool = False) -> Admission:
    """Admit or refuse one harness kind running with ``tools``.

    An empty corpus refuses: a registry that names no hard policy means the
    gate has nothing to compare, and admitting then would be a vacuous pass.
    """
    if not policies:
        return Admission(harness_kind, error="the registry names no hard PreToolUse policy")
    surface = tool_surface(harness_kind, tools)
    enforcement = ENFORCEMENT.get(harness_kind, "none")
    verdicts = tuple((p.policy_id, _verdict(p, surface, enforcement, bindings, hooks_off))
                     for p in policies)
    return Admission(harness_kind, verdicts)


def user_settings_path(claude_dir: Path) -> Path:
    """The user settings file a ``claude -p`` child loads.

    Claude Code reads ``$CLAUDE_CONFIG_DIR/settings.json`` when that variable is
    set, else ``~/.claude/settings.json``. GRIP's install directory is that same
    default, so ``claude_dir`` stands in for it; the child inherits this
    process's environment, so a ``CLAUDE_CONFIG_DIR`` set here is the one it uses.
    """
    override = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return (Path(override) if override else Path(claude_dir)) / "settings.json"


def _read_json(path: Path) -> dict:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    return doc


def _optional_json(path: Path) -> list:
    return [_read_json(path)] if path.is_file() else []


def _origin_main_project_settings(project_dir: Path) -> list:
    """The ``.claude/settings.json`` a fresh worktree from ``origin/main`` will
    carry, when git can say. Being unable to ask is not a refusal on its own:
    the working copy is read beside it. A file git returns that does not parse
    still refuses, through ``_read_json``'s rule, because the child would load it.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_dir), "show", "origin/main:.claude/settings.json"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    doc = json.loads(proc.stdout)
    if not isinstance(doc, dict):
        raise ValueError("origin/main:.claude/settings.json does not hold a JSON object")
    return [doc]


def _settings_sources(claude_dir: Path, project_dir: Optional[Path]) -> tuple:
    """``(firing, off)``: the settings whose hooks the child certainly runs, and
    every settings file that could switch the child's hooks off.

    The user file must exist. The child starts in a worktree, and Claude Code
    reads a worktree's ``settings.local.json`` "at the main checkout's root"
    (code.claude.com/docs/en/settings, read 2026-10-26), so that file counts
    for both. The shared ``.claude/settings.json`` the child loads is the
    worktree's own, born from ``origin/main``; both that copy and the working
    copy are checked for ``disableAllHooks``. Its hook bindings are not credited,
    which can only refuse more: no hard gate is bound there today.
    """
    firing = [_read_json(user_settings_path(claude_dir))]
    off = list(firing)
    if project_dir is not None:
        project = Path(project_dir) / ".claude"
        local = _optional_json(project / "settings.local.json")
        firing += local
        off += local + _optional_json(project / "settings.json")
        off += _origin_main_project_settings(Path(project_dir))
    return firing, off


def admit_from_disk(harness_kind: str, tools: Sequence[str], *, claude_dir: Path,
                    project_dir: Optional[Path] = None,
                    registry_path: Path = REGISTRY_PATH) -> Admission:
    """``admit`` against the registry and the settings the child will load
    (see ``_settings_sources``). Any read or parse failure refuses the run."""
    try:
        registry = _read_json(registry_path)
        firing, off = _settings_sources(claude_dir, project_dir)
    except (OSError, ValueError) as exc:
        return Admission(harness_kind,
                         error=f"cannot read the gate policy ({type(exc).__name__}: {exc})")
    return admit(harness_kind, tools, policies=hard_pretooluse_policies(registry),
                 bindings=live_pretooluse_bindings(firing), hooks_off=hooks_disabled(off))
