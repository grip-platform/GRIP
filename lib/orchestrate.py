"""Harness-agnostic orchestration shim — ``run(prompt)`` routes one reasoning
request to the right backend for the active environment.

Origin: 2026-06-24, converge ``wf_985cf7b0`` (deferred → V>>-ratified). The
syscall-doctrine (``rules/syscall-doctrine.md``: "everything is a shim, AI is a
syscall") says every LLM interaction passes through ONE typed contract. This is
that shim for the *agentic-task* boundary — the level above ``hal_llm_adapter``:

* **NATIVE** — when an agentic harness is active (Claude Code, Codex, …), a
  reasoning task is best executed by *that harness's own agent loop* (it has the
  tool surface, the repo context, the permission gates). Python cannot call the
  harness's task primitive from inside a tool call — that is an HONEST boundary,
  not a stub (``rules/no-stubs.md`` carve-out: a runtime-dependency boundary).
  So ``run`` first emits a PRECOG IDR routing receipt, then returns a
  fully-populated :class:`TaskSpec` describing the work; the MODEL driving the
  harness executes it. The TaskSpec IS the complete return value for this route
  — there is no missing capability it merely names.
* **PORTABLE** — standalone (no harness) or when the caller forces it, the task
  is executed in-process via the canonical AI syscall
  (``lib.hal_llm_adapter.HALLLMAdapter.generate`` — RAILLM-over-happi,
  provider-agnostic, fail-soft to a neutral sentinel, never raises).

Routing is read off ``lib.hal.harness_detect.detect_harness`` (the real shipped
detector). The first and only consumer is ``lib.learning_proposer``.

Design invariants (all enforced by ``tests/test_orchestrate.py``):

* **Pure dispatch + fail-open audit.** ``decide_route`` is a pure function of
  ``(harness, force_portable)`` — table-driven, mockable. The PRECOG write is
  an additive audit side effect and degrades to ``None`` without blocking work.
* **No stubs.** Both routes are fully realised: NATIVE returns a complete,
  executable TaskSpec; PORTABLE returns real model text (or the documented
  neutral sentinel — a complete "provider unavailable" answer, never a hollow
  placeholder).
* **Path-portable.** Zero hardcoded operator paths; the only filesystem touch is
  delegated to ``hal_llm_adapter`` / ``claude_dir`` (``rules/path-portability.md``).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

# --- path-portable imports (rules/path-portability.md) ---------------------
_LIB = Path(__file__).resolve().parent
_ROOT = _LIB.parent
for _p in (str(_ROOT), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

__all__ = ["Route", "TaskSpec", "OrchestrateResult", "decide_route", "run"]


class Route(str, Enum):
    """Where a reasoning task is executed."""

    NATIVE = "native"      # the active agentic harness drives its own agent loop
    PORTABLE = "portable"  # in-process via the HAL AI syscall


@dataclass(frozen=True)
class TaskSpec:
    """A complete, executable description of a reasoning task for the NATIVE route.

    Returned (never executed in-Python) when an agentic harness is active: the
    MODEL reads this and runs the task with the harness's own tools. Every field
    is populated — this is the whole deliverable for the NATIVE route, not a
    placeholder that names work left undone (``rules/no-stubs.md``).
    """

    prompt: str
    harness: str
    instructions: str
    context: Mapping[str, Any] = field(default_factory=dict)
    precog_idr: Optional[str] = None


@dataclass(frozen=True)
class OrchestrateResult:
    """The result of ``run`` — exactly one of ``task_spec`` / ``text`` is set.

    * NATIVE  → ``task_spec`` populated, ``text is None`` (the harness executes).
    * PORTABLE → ``text`` populated (model output / neutral sentinel),
      ``task_spec is None``.
    ``route`` records which path was taken so callers can branch deterministically.
    """

    route: Route
    task_spec: Optional[TaskSpec] = None
    text: Optional[str] = None
    precog_idr: Optional[str] = None


# Harness value -> route. STANDALONE/UNKNOWN have no agent loop to delegate to,
# so they execute in-process (PORTABLE). Every other detected harness owns a
# richer agent loop than an in-process single-shot call, so it drives NATIVE.
_NATIVE_HARNESSES: frozenset[str] = frozenset(
    {"claude-code", "gemini-cli", "codex", "cursor", "opencode"}
)


def decide_route(harness: str, *, force_portable: bool = False) -> Route:
    """Pure routing decision. ``force_portable`` overrides to PORTABLE always.

    A standalone/unknown harness routes PORTABLE (nothing to delegate to); a
    known agentic harness routes NATIVE. Table-driven so a new harness is one
    frozenset entry, not a new branch (CC discipline).
    """
    if force_portable:
        return Route.PORTABLE
    return Route.NATIVE if harness in _NATIVE_HARNESSES else Route.PORTABLE


def _native_task_spec(prompt: str, harness: str, instructions: str,
                      context: Mapping[str, Any],
                      precog_idr: Optional[str]) -> TaskSpec:
    """Build the executable TaskSpec for the NATIVE route (complete deliverable)."""
    return TaskSpec(
        prompt=prompt,
        harness=harness,
        instructions=instructions,
        context=dict(context),
        precog_idr=precog_idr,
    )


def _portable_text(prompt: str, *, json_mode: bool,
                  generate: Optional[Callable[..., str]]) -> str:
    """Execute PORTABLE in-process via the HAL AI syscall (fail-soft, never raises).

    ``generate`` is dependency-injected for tests (DIP, ``rules/syscall-doctrine.md``
    §"trivially mockable"); production resolves the real adapter lazily so this
    module stays import-light and free of an import cycle.
    """
    if generate is None:
        from hal_llm_adapter import HALLLMAdapter  # lazy: import-light shim
        generate = HALLLMAdapter().generate
    return generate(prompt, json_mode=json_mode)


def _provider_addresses(route: Route, harness: str) -> list[str]:
    """Provider address recorded in PRECOG for this orchestration boundary."""
    if route is Route.NATIVE:
        return [f"harness/{harness}"]
    return ["hal/raillm"]


def _record_precog(
    prompt: str,
    *,
    route: Route,
    harness: str,
    instructions: str,
    context: Mapping[str, Any],
    json_mode: bool,
    recorder: Optional[Callable[..., Any]],
) -> Optional[str]:
    """Emit the orchestration-head PRECOG IDR before delegation.

    Additive + fail-open: audit recording must not make the active harness
    unusable. The recorded body stores route metadata and hashes inside the
    PRECOG layer; it does not persist raw context values here.
    """
    try:
        if recorder is None:
            from lib.precog.orchestrator import orchestrate as recorder
        idr = recorder(
            prompt,
            kernel_text=instructions,
            routing_hint={
                "recommended_pattern": "NativeHarness" if route is Route.NATIVE else "PortableHAL",
                "harness": harness,
                "route": route.value,
                "json_mode": bool(json_mode),
                "context_keys": sorted(str(k) for k in context.keys()),
            },
            provider_addresses=_provider_addresses(route, harness),
        )
        return getattr(idr, "id", None)
    except Exception:
        return None


def run(
    prompt: str,
    *,
    instructions: str = "",
    context: Optional[Mapping[str, Any]] = None,
    force_portable: bool = False,
    json_mode: bool = False,
    harness: Optional[str] = None,
    generate: Optional[Callable[..., str]] = None,
    precog_recorder: Optional[Callable[..., Any]] = None,
) -> OrchestrateResult:
    """Route one reasoning task and return an :class:`OrchestrateResult`.

    Parameters
    ----------
    prompt:
        The reasoning request.
    instructions:
        Extra guidance carried into the NATIVE TaskSpec (ignored on PORTABLE).
    context:
        Structured context dict carried into the NATIVE TaskSpec.
    force_portable:
        Force in-process execution even under a harness (e.g. a caller that
        must have text back synchronously).
    json_mode:
        Passed to the PORTABLE generator so JSON callers never hit a parse miss.
    harness:
        Override the detected harness (testing seam). ``None`` ⇒ detect live.
    generate:
        Inject the PORTABLE text generator (testing seam). ``None`` ⇒ real HAL.
    precog_recorder:
        Inject the PRECOG IDR recorder (testing seam). ``None`` ⇒ real PRECOG
        orchestrator. Exceptions are swallowed by design.

    Returns
    -------
    OrchestrateResult
        NATIVE ⇒ ``task_spec`` set; PORTABLE ⇒ ``text`` set. Exactly one.
    """
    if harness is None:
        from hal.harness_detect import detect_harness  # lazy: real detector
        harness = detect_harness().value
    route = decide_route(harness, force_portable=force_portable)
    precog_idr = _record_precog(
        prompt,
        route=route,
        harness=harness,
        instructions=instructions,
        context=context or {},
        json_mode=json_mode,
        recorder=precog_recorder,
    )
    if route is Route.NATIVE:
        spec = _native_task_spec(prompt, harness, instructions, context or {}, precog_idr)
        return OrchestrateResult(route=Route.NATIVE, task_spec=spec, precog_idr=precog_idr)
    text = _portable_text(prompt, json_mode=json_mode, generate=generate)
    return OrchestrateResult(route=Route.PORTABLE, text=text, precog_idr=precog_idr)
