"""Harness abstraction — agentic runtimes as substitutable types.

A Harness is anything that can execute a Task and return a HarnessResult:
Claude Code, Codex, OpenCode, Gemini CLI, a bare HAL provider call, or a
COMPOSITION of harnesses (orchestrator / hosted / fallback). The design
lifts lib/agora/speaker.py one level: Speaker composes conversational
actors, Harness composes agentic RUNTIMES — same recursive-composition
discipline, one principled extension: the base class enforces the
substitution contract mechanically (template method), so a violating
subtype fails loudly at run() time instead of silently downstream.

Contract (every subtype — Liskov, enforced by Harness.run):
    - result.id == task.id                 (echo invariant)
    - result.kind == harness.kind          (type-discriminator round-trip)
    - result.ok  => result.content != ""   (structural, in HarnessResult)
    - not result.ok => result.error != ""  (structural, in HarnessResult)
    - preconditions never strengthened: every subtype accepts any valid Task

Unified dispatch log (one line per run, ALL harnesses, ALL models — the
cross-model session log): <claude_dir>/logs/harness-dispatch.jsonl.
Content is hashed, never logged raw (redaction discipline).
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402

# Confabulation gates at the dispatch seam (task #474): the pure detectors
# from the confab program consulted once here, so EVERY leaf — claude, codex,
# opencode, gemini, grok, HAL, and every future kind — inherits them through
# run(). Fail-open by construction: a missing or broken detector never
# affects dispatch — but never SILENTLY (one boot-time warning per process
# makes a vanished seam audible to ops; AGORA #4038). Advisory-only contract
# (monotone add-only, same discipline as the Claude Code hook layer): the
# signal lands in the dispatch log; the HarnessResult is never altered and
# nothing ever blocks.
import logging  # noqa: E402

_logger = logging.getLogger(__name__)
try:
    from premise_reproduce_gate import classify_dispatch_prompt as _classify_premise  # noqa: E402
except Exception as _exc:  # noqa: BLE001 — seam is optional equipment, never a dependency
    _classify_premise = None
    _logger.warning("confab premise gate unavailable at dispatch seam: %s", _exc)
try:
    from tool_error_self_check import scan_command as _scan_command  # noqa: E402
    from tool_error_self_check import scan_output as _scan_output  # noqa: E402
except Exception as _exc:  # noqa: BLE001
    _scan_command = None
    _scan_output = None
    _logger.warning("tool-error self-check unavailable at dispatch seam: %s", _exc)


class HarnessContractViolation(RuntimeError):
    """A subtype broke the substitution contract — loud, never silent."""


@dataclass(frozen=True)
class Task:
    """One unit of work handed to any harness."""

    id: str
    prompt: str
    timeout_s: float = 300.0

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str):
            raise TypeError("Task.prompt must be a string")
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Task.id must be a non-empty string")


@dataclass(frozen=True)
class HarnessResult:
    """Outcome of one dispatch. Honest by construction: an ok result cannot
    carry empty content, and a failed result cannot carry an empty error."""

    id: str
    kind: str
    harness_id: str
    ok: bool
    content: str = ""
    error: str = ""
    sub_results: tuple["HarnessResult", ...] = ()

    def __post_init__(self) -> None:
        if self.ok and not self.content:
            raise ValueError("ok result requires non-empty content")
        if not self.ok and not self.error:
            raise ValueError("failed result requires non-empty error")


class Harness(ABC):
    """Polymorphic root. Subclasses implement _execute(); callers use run().

    run() is a template method: validates the task, walks the composition
    graph for cycles, executes, converts unexpected exceptions into honest
    failed results, enforces the echo + kind invariants (raising
    HarnessContractViolation on a lying subtype), and appends one line to
    the unified dispatch log.
    """

    KIND: str = ""  # concrete subtypes declare their canonical kind

    def __init__(self, harness_id: Optional[str] = None,
                 log_path: Optional[Path] = None) -> None:
        if not self.kind:
            raise ValueError(
                f"{type(self).__name__} must declare a non-empty KIND")
        self._harness_id = harness_id or f"{self.kind}-{id(self):x}"
        self._log_path = Path(log_path) if log_path else (
            get_claude_dir() / "logs" / "harness-dispatch.jsonl")

    @property
    def kind(self) -> str:
        return type(self).KIND

    @property
    def harness_id(self) -> str:
        return self._harness_id

    @abstractmethod
    def _execute(self, task: Task) -> HarnessResult:
        """Subtype hook. Must echo task.id and self.kind in the result."""

    def _members(self) -> tuple["Harness", ...]:
        """Composite children (empty for leaves); used by the cycle guard."""
        return ()

    def _guard_no_cycle(self, node: "Harness", seen: set) -> None:
        if id(node) in seen:
            raise ValueError(
                f"cycle detected via harness '{node.harness_id}'")
        branch_seen = seen | {id(node)}
        for child in node._members():
            self._guard_no_cycle(child, branch_seen)

    def fail(self, task: Task, error: str) -> HarnessResult:
        """Honest failure result for this harness (keeps subtypes DRY)."""
        return HarnessResult(id=task.id, kind=self.kind,
                             harness_id=self.harness_id, ok=False,
                             error=error or "unknown error")

    def _confab_advisory(self, task: Task, result: HarnessResult) -> dict:
        """Advisory-only confabulation signals for the dispatch log (task #474).

        Three consults at the one seam every leaf inherits: an unreproduced
        failure premise asserted in the task prompt (premise-reproduce gate),
        a known footgun command embedded in the prompt (tool-error
        self-check), and the ``--replace`` mangling signature in successful
        result content. Never raises, never mutates the result, never blocks
        — a signal only ever ADDS a key to the log line (monotone add-only,
        ``rules/llm-augmentation-pattern.md``).
        """
        advisory: dict = {}
        try:
            if _classify_premise is not None:
                premise = _classify_premise(task.prompt)
                if premise["verdict"] == "warn":
                    advisory["premise_warn"] = premise["reason"][:200]
            if _scan_command is not None:
                footgun = _scan_command(task.prompt)
                if footgun["flagged"]:
                    advisory["prompt_footgun"] = footgun["message"][:200]
            if _scan_output is not None and result.ok:
                mangling = _scan_output(result.content)
                if mangling["flagged"]:
                    advisory["output_mangling"] = mangling["message"][:200]
        except Exception:  # noqa: BLE001 — advisory must never break dispatch
            pass
        return advisory

    def run(self, task: Task) -> HarnessResult:
        if not isinstance(task, Task):
            raise TypeError("run() requires a Task")
        self._guard_no_cycle(self, set())
        t0 = time.monotonic()
        try:
            result = self._execute(task)
        except Exception as exc:  # honest failure, never a silent crash
            result = self.fail(task, f"{type(exc).__name__}: {exc}")
        if result.id != task.id:
            raise HarnessContractViolation(
                f"{type(self).__name__} broke the id-echo invariant "
                f"({result.id!r} != {task.id!r})")
        if result.kind != self.kind:
            raise HarnessContractViolation(
                f"{type(self).__name__} broke the kind invariant "
                f"({result.kind!r} != {self.kind!r})")
        self._log(task, result, time.monotonic() - t0,
                  self._confab_advisory(task, result))
        return result

    def _log(self, task: Task, result: HarnessResult,
             duration_s: float, confab: dict | None = None) -> None:
        line = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "kind": result.kind,
            "harness_id": result.harness_id,
            "task_id": task.id,
            "ok": result.ok,
            "duration_ms": round(duration_s * 1000),
            "content_sha12": hashlib.sha256(
                result.content.encode("utf-8")).hexdigest()[:12],
            "error_head": result.error[:120],
        }
        if confab:
            line["confab"] = confab
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
        except OSError:
            pass  # logging must never break dispatch


HARNESS_KINDS: dict[str, type] = {}


def register(cls: type) -> type:
    """Class decorator adding a concrete Harness subtype to the registry.

    Open for extension: a new runtime (Hermes, whatever ships next) lands
    as one subclass + this decorator — zero caller changes.
    """
    kind = getattr(cls, "KIND", "")
    if not kind:
        raise ValueError(f"{cls.__name__} must declare a non-empty KIND")
    existing = HARNESS_KINDS.get(kind)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"kind {kind!r} already registered to {existing.__name__}")
    HARNESS_KINDS[kind] = cls
    return cls


def make(kind: str, **kwargs) -> "Harness":
    """Factory over the registry — callers stay closed to modification."""
    try:
        cls = HARNESS_KINDS[kind]
    except KeyError:
        raise ValueError(
            f"unknown harness kind {kind!r}; "
            f"known: {sorted(HARNESS_KINDS)}") from None
    return cls(**kwargs)
