"""gate_runner -- run GRIP's mechanical gates from any harness, not just Claude Code.

The operator (2026-09-19): *"Remember we are moving away from requiring Claude Code."*

Measured the same day, and it reframes what that costs. GRIP's ~119 hook scripts hang
off the eight event names Claude Code defines -- PreToolUse, PostToolUse, Stop,
SessionStart, UserPromptSubmit, SubagentStop, Notification, PreCompact -- and a search
of ``bin/``, ``lib/`` and ``scripts/`` finds no other runtime invoker; every hit is an
installer or a health check. ``~/.codex/config.toml`` exists on this machine, so Codex
is installed and configured, and it references none of it.

So switching harness today leaves every mechanical gate GRIP has inert, and **nothing
says so**. The system runs, reports healthy, and is unguarded. That silence is the
defect, not the coupling: an inert gate is indistinguishable from a passing one.

The coupling is shallow, which is what makes this fixable. The detectors in ``lib/``
are already pure functions over (content, path). Only two things are Claude-Code-shaped:
the stdin payload (``{tool_name, tool_input}``) and the decision JSON that
``lib/hook_response.py`` emits. This module is the neutral middle -- detectors in, a
harness-independent verdict out -- so ``hooks/*.py`` stay Claude Code's adapter and any
other harness writes its own thin one.

**A gate that could not run is reported, never dropped in silence.** A runner that
quietly omits a gate it failed to import reproduces the exact failure it exists to
remove.

Each gate's own semantics live in its rule, and are cited rather than paraphrased here
-- a second-hand description of a detector drifts from the detector.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

ALLOW = 0
DENY = 2


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict. ``available`` false means it could not run, not that it passed."""

    name: str
    allowed: bool
    available: bool = True
    signal: str = ""
    reason: str = ""
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def status(self) -> str:
        if not self.available:
            return "COULD-NOT-RUN"
        return "allow" if self.allowed else "DENY"


def _unavailable(name: str, why: str) -> GateResult:
    return GateResult(name=name, allowed=True, available=False, reason=why)


def _gate_guardrail_evasion(content: str, path: str) -> GateResult:
    """Applies the two signals defined in ``rules/no-guardrail-evasion.md``."""
    name = "guardrail-evasion"
    try:
        from guardrail_evasion_llm import combined_evaluate
    except ImportError as exc:
        return _unavailable(name, f"import failed: {exc}")
    verdict = combined_evaluate(content, path)
    return GateResult(
        name=name,
        allowed=bool(getattr(verdict, "allowed", True)),
        signal=str(getattr(verdict, "signal", "")),
        reason=str(getattr(verdict, "reason", "")),
        evidence=tuple(getattr(verdict, "evidence", ()) or ()),
    )


def _findings_result(name: str, findings: list, path: str) -> GateResult:
    """Shared shape for detectors that return a list of findings."""
    if not findings:
        return GateResult(name=name, allowed=True)
    evidence = tuple(str(f)[:200] for f in findings[:5])
    return GateResult(
        name=name,
        allowed=False,
        signal=f"{len(findings)} finding(s)",
        reason=f"{len(findings)} finding(s) in {path}",
        evidence=evidence,
    )


def _gate_no_stubs(content: str, path: str) -> GateResult:
    """Applies ``rules/no-stubs.md`` -- the Completeness principle."""
    name = "no-stubs"
    try:
        from no_stubs_detector import analyze_source
    except ImportError as exc:
        return _unavailable(name, f"import failed: {exc}")
    try:
        findings = analyze_source(content, path=path)
    except SyntaxError:
        # Not valid Python. Saying nothing is correct; denying would be a false
        # positive on, say, a Markdown edit routed here by a caller's own matcher.
        return GateResult(name=name, allowed=True, signal="not python")
    return _findings_result(name, list(findings), path)


def _gate_wiring_perimeter(content: str, path: str) -> GateResult:
    """Applies ``rules/declared-input-must-reach-the-actor.md``."""
    name = "wiring-perimeter"
    try:
        from wiring_perimeter_detector import analyze_source
    except ImportError as exc:
        return _unavailable(name, f"import failed: {exc}")
    try:
        findings = analyze_source(content, path)
    except SyntaxError:
        return GateResult(name=name, allowed=True, signal="not python")
    return _findings_result(name, list(findings), path)


GateFn = Callable[[str, str], GateResult]

GATES: tuple[tuple[str, str, GateFn], ...] = (
    ("guardrail-evasion", "rules/no-guardrail-evasion.md", _gate_guardrail_evasion),
    ("no-stubs", "rules/no-stubs.md", _gate_no_stubs),
    ("wiring-perimeter", "rules/declared-input-must-reach-the-actor.md",
     _gate_wiring_perimeter),
)

_BY_NAME: dict[str, GateFn] = {name: fn for name, _, fn in GATES}
_RULES: dict[str, str] = {name: rule for name, rule, _ in GATES}


def gate_names() -> tuple[str, ...]:
    return tuple(name for name, _, _ in GATES)


def rule_for(name: str) -> str:
    return _RULES.get(name, "")


def run_gates(
    content: str, path: str, only: tuple[str, ...] | None = None
) -> list[GateResult]:
    """Run every gate (or a named subset) over one candidate edit."""
    selected = only or gate_names()
    results: list[GateResult] = []
    for name in selected:
        fn = _BY_NAME.get(name)
        if fn is None:
            results.append(_unavailable(name, "no such gate"))
            continue
        results.append(fn(content, path))
    return results


def verdict(results: list[GateResult]) -> int:
    """ALLOW unless a gate that actually RAN says deny.

    A gate that could not run has no opinion, so it cannot deny. It is surfaced in
    the output rather than folded into the exit code, so an integrator can tell
    "nothing objected" apart from "nothing ran".
    """
    denied = [r for r in results if r.available and not r.allowed]
    return DENY if denied else ALLOW


def unavailable(results: list[GateResult]) -> list[GateResult]:
    return [r for r in results if not r.available]


def deny_summary(results: list[GateResult]) -> str:
    """One line per denying gate, for the channel a harness actually surfaces.

    Codex's PreToolUse contract is "exit with code 2 and write the reason to
    stderr" (developers.openai.com/codex/hooks), and Claude Code shows a hook's
    stderr to the model. Neither reads stdout, so a denial explained only there is
    a denial with no explanation attached.
    """
    denied = [r for r in results if r.available and not r.allowed]
    if not denied:
        return ""
    lines = ["grip-gate DENY:"]
    for result in denied:
        rule = rule_for(result.name)
        suffix = f" ({rule})" if rule else ""
        lines.append(f"  {result.name}{suffix}: {result.reason or result.signal}")
        lines.extend(f"    - {item}" for item in result.evidence[:3])
    return "\n".join(lines)


def render_text(results: list[GateResult], path: str) -> str:
    """Human-readable report; the machine form is JSON via the CLI's --json."""
    lines = [f"grip-gate: {path}", ""]
    for result in results:
        lines.append(f"  [{result.status:>13}] {result.name}")
        if result.reason:
            lines.append(f"                  {result.reason}")
        lines.extend(f"                  - {item}" for item in result.evidence)
    missing = unavailable(results)
    if missing:
        lines.append("")
        lines.append(
            f"WARNING: {len(missing)} gate(s) could not run. Silence from a gate that "
            "never executed is not a pass."
        )
    return "\n".join(lines)


def as_dict(results: list[GateResult], path: str) -> dict:
    return {
        "path": path,
        "verdict": "deny" if verdict(results) == DENY else "allow",
        "exit_code": verdict(results),
        "could_not_run": [r.name for r in unavailable(results)],
        "gates": [
            {
                "name": r.name,
                "rule": rule_for(r.name),
                "status": r.status,
                "allowed": r.allowed,
                "available": r.available,
                "signal": r.signal,
                "reason": r.reason,
                "evidence": list(r.evidence),
            }
            for r in results
        ],
    }
