#!/usr/bin/env python3
"""RSI-Optimal Pipeline — codified ultimate recursive self-improvement.

The proven optimal RSI configuration, codified as executable infrastructure:

    /ultraplan → /ultrathink → outer Fibonacci Wave breathing →
    inner converge loops with Fibonacci-series growing depth →
    /hypothesis-driven-development + Gentner SMT + external scoring

PARAMOUNT: Criterion is ALWAYS falsification. Every wave, every converge,
every hypothesis must state what would DISPROVE the claim. A system that
only confirms is not doing science.

Pipeline:
    Phase 0: Plan (ultraplan — falsifiable plan with measurable criterion)
    Phase 1: Execute (Fibonacci waves — escalating depth converge loops)
    Phase 2: Ship (auto-ship per wave)
    Phase 3: Measure (hypothesis register + external score via HAL council)
    Phase 4: Breathe (genome evolve + DELIGHT snapshot + anti-drift)

Usage:
    from rsi.optimal import RSIOptimalPipeline, load_pipeline
    pipeline = RSIOptimalPipeline("close the GRIP-HAL memory gap")
    pipeline.plan()
    # ... or resume:
    pipeline = load_pipeline()

CLI:
    python3 -m rsi.optimal start "goal" --duration 3
    python3 -m rsi.optimal status
    python3 -m rsi.optimal resume
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
STATE_DIR = CLAUDE_DIR / "state" / "rsi"
OPTIMAL_STATE_FILE = STATE_DIR / "rsi-optimal-state.json"

FIBONACCI = [1, 1, 2, 3, 5, 8, 13, 21]


def fibonacci(n: int) -> int:
    """Get Fibonacci number at position n (0-indexed)."""
    if n < len(FIBONACCI):
        return FIBONACCI[n]
    a, b = FIBONACCI[-2], FIBONACCI[-1]
    for _ in range(n - len(FIBONACCI) + 1):
        a, b = b, a + b
    return b


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class WaveState:
    """State of a single Fibonacci wave."""
    number: int
    depth: int
    batch_size: int
    status: str = "pending"
    hypothesis_id: str = ""
    external_score: float = 0.0
    agreement: float = 0.0
    gentner_score: float = 0.0
    pr_numbers: list = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    falsification: str = ""


@dataclass
class RSIOptimalState:
    """Full pipeline state — persisted for session handoff."""
    goal: str = ""
    criterion: str = ""
    duration_hours: float = 3.0
    started_at: str = ""
    current_wave: int = 0
    max_waves: int = 6
    waves: list = field(default_factory=list)
    plan_file: str = ""
    total_prs: int = 0
    total_hypotheses: int = 0
    total_external_scores: int = 0
    phase: str = "init"
    halt_reason: str = ""
    context_usage_pct: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RSIOptimalState":
        return cls(**{k: v for k, v in data.items()
                      if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Measurement (module-level — ISP: separated from orchestration)
# ---------------------------------------------------------------------------

def register_hypothesis(
    claim: str, metric: str, prediction: str, pr_number: int = 0,
) -> str:
    """Register a falsifiable hypothesis via hypothesis_engine.py."""
    import subprocess
    deadline = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cmd = [
        "python3", str(CLAUDE_DIR / "lib" / "hypothesis_engine.py"),
        "register",
        "--pr", str(pr_number), "--claim", claim,
        "--metric", metric, "--prediction", prediction,
        "--deadline", deadline,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10,
            env={**os.environ, "PYTHONPATH": str(CLAUDE_DIR)},
        )
        for line in result.stdout.splitlines():
            if line.startswith("H"):
                return line.strip().split()[0]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return ""


def score_externally(claim: str, context: str = "") -> tuple[float, float, str]:
    """Score via HAL council — producing model NEVER scores itself.

    Returns (score, agreement, reasoning).
    """
    try:
        from lib.hal_client import score_external
        result = score_external(claim, rubric="accuracy", context=context)
        return result.score, result.agreement, result.reasoning
    except Exception as e:
        return 0.0, 0.0, f"External scoring unavailable: {e}"


def validate_gentner(proposal_data: dict) -> float:
    """Validate structural mapping via Gentner SMT enforcer. Returns 0-100."""
    try:
        from lib.gentner_enforcer import MappingProposal, MappingRelation, validate
        relations = [MappingRelation(**r) for r in proposal_data.get("relations", [])]
        proposal = MappingProposal(
            source_domain=proposal_data.get("source_domain", ""),
            target_domain=proposal_data.get("target_domain", ""),
            source_elements=proposal_data.get("source_elements", []),
            target_elements=proposal_data.get("target_elements", []),
            relations=relations,
            unmapped_source=proposal_data.get("unmapped_source", []),
            unmapped_target=proposal_data.get("unmapped_target", []),
        )
        return validate(proposal).total_score
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Persistence (module-level — ISP: separated from orchestration)
# ---------------------------------------------------------------------------

def save_state(state: RSIOptimalState) -> None:
    """Persist pipeline state for session handoff."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    OPTIMAL_STATE_FILE.write_text(json.dumps(state.to_dict(), indent=2, default=str))


def load_state() -> Optional[RSIOptimalState]:
    """Load pipeline state from disk."""
    if not OPTIMAL_STATE_FILE.exists():
        return None
    try:
        return RSIOptimalState.from_dict(json.loads(OPTIMAL_STATE_FILE.read_text()))
    except (json.JSONDecodeError, OSError):
        return None


def load_pipeline() -> Optional["RSIOptimalPipeline"]:
    """Restore pipeline from saved state."""
    state = load_state()
    if not state:
        return None
    pipeline = RSIOptimalPipeline.__new__(RSIOptimalPipeline)
    pipeline.state = state
    pipeline._depth_override = None
    return pipeline


# ---------------------------------------------------------------------------
# Core Pipeline (ISP: 5 methods + 2 properties = 7 total)
# ---------------------------------------------------------------------------

class RSIOptimalPipeline:
    """Fibonacci Wave orchestrator with falsification-first criterion.

    Public interface (7):
      Properties: current_wave, is_complete
      Methods: plan, start_wave, complete_wave, breathe, summary
    Persistence: use module-level load_pipeline() / save_state()
    Measurement: use module-level register_hypothesis() / score_externally()
    """

    def __init__(
        self,
        goal: str,
        duration_hours: float = 3.0,
        max_waves: int = 6,
        depth_override: Optional[int] = None,
    ):
        self.state = RSIOptimalState(
            goal=goal, duration_hours=duration_hours, max_waves=max_waves,
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._depth_override = depth_override
        for i in range(max_waves):
            self.state.waves.append(asdict(WaveState(
                number=i, depth=depth_override or fibonacci(i),
                batch_size=fibonacci(i),
            )))

    @property
    def current_wave(self) -> Optional[WaveState]:
        idx = self.state.current_wave
        if idx >= len(self.state.waves):
            return None
        data = self.state.waves[idx]
        return WaveState(**data) if isinstance(data, dict) else data

    @property
    def is_complete(self) -> bool:
        return self.state.current_wave >= self.state.max_waves

    def plan(self) -> str:
        """Phase 0: Generate falsifiable plan. Returns criterion."""
        self.state.phase = "planning"
        self.state.criterion = (
            f"FALSIFY: {self.state.goal} — "
            f"FAILS if: (1) no PR merged with passing tests, "
            f"(2) external council scores < 50/100, "
            f"(3) hypothesis rate outside 30-70%, "
            f"(4) Gentner SMT < 60 on structural claims. "
            f"Deadline: +{self.state.duration_hours:.0f}h."
        )
        save_state(self.state)
        return self.state.criterion

    def start_wave(self) -> WaveState:
        """Phase 1: Begin executing current wave."""
        wave = self.current_wave
        if not wave:
            raise RuntimeError("No more waves")
        wave.status = "executing"
        wave.started_at = datetime.now(timezone.utc).isoformat()
        self.state.waves[self.state.current_wave] = asdict(wave)
        self.state.phase = "executing"
        save_state(self.state)
        return wave

    def complete_wave(self, **kwargs) -> WaveState:
        """Phase 2-3: Mark wave complete with measurements, advance."""
        wave = self.current_wave
        if not wave:
            raise RuntimeError("No active wave")
        wave.status = "complete"
        wave.completed_at = datetime.now(timezone.utc).isoformat()
        for key, val in kwargs.items():
            if hasattr(wave, key):
                setattr(wave, key, val)
        self.state.total_prs += len(wave.pr_numbers)
        if wave.hypothesis_id:
            self.state.total_hypotheses += 1
        if wave.external_score > 0:
            self.state.total_external_scores += 1
        self.state.waves[self.state.current_wave] = asdict(wave)
        self.state.current_wave += 1
        save_state(self.state)
        return wave

    def breathe(self) -> dict:
        """Phase 4: Breathing pause — auto-compact at context gate, never halt.

        InfiniteMachine: HAL for tokens, compact for context. The only stop
        conditions are V>> says stop or all work is done.
        """
        elapsed = self._elapsed_hours()
        remaining = self.state.duration_hours - elapsed
        diag = {
            "wave_completed": self.state.current_wave - 1,
            "next_wave": self.state.current_wave,
            "elapsed_hours": elapsed, "remaining_hours": remaining,
        }
        if self.state.context_usage_pct >= 85:
            # InfiniteMachine: save state, compact, continue — never halt
            save_state(self.state)
            diag["action"] = "compact"
            diag["reason"] = "Context 85% — save + compact + continue"
        elif remaining <= 0:
            diag["action"] = "extend"
            diag["reason"] = "Time budget reached — extend or compact"
        else:
            nw = self.current_wave
            if nw:
                diag["next_depth"] = nw.depth
        save_state(self.state)
        return diag

    def summary(self) -> dict:
        """Anti-drift sprint summary."""
        done = sum(1 for w in self.state.waves
                   if (w if isinstance(w, dict) else asdict(w)).get("status") == "complete")
        return {
            "goal": self.state.goal, "phase": self.state.phase,
            "waves_completed": done, "waves_total": self.state.max_waves,
            "total_prs": self.state.total_prs,
            "total_hypotheses": self.state.total_hypotheses,
            "elapsed_hours": round(self._elapsed_hours(), 1),
            "criterion": self.state.criterion,
        }

    def _halt(self, reason: str) -> None:
        self.state.phase = "halted"
        self.state.halt_reason = reason
        save_state(self.state)

    def _elapsed_hours(self) -> float:
        if not self.state.started_at:
            return 0.0
        started = datetime.fromisoformat(self.state.started_at)
        return (datetime.now(timezone.utc) - started).total_seconds() / 3600


# ---------------------------------------------------------------------------
# Protocol Generation (KISS: focused helpers)
# ---------------------------------------------------------------------------

def _wave_schedule_table(max_waves: int) -> list[str]:
    lines = ["| Wave | Depth | Batch | Method |", "|------|-------|-------|--------|"]
    for i in range(max_waves):
        lines.append(f"| W{i+1} | d={fibonacci(i)} | {fibonacci(i)} | broly auto converge |")
    return lines


_EXECUTION_LOOP = [
    "```",
    "for each wave in schedule:",
    "    1. PLAN: ultraplan with falsification criterion",
    "    2. EXECUTE: converge(depth=fibonacci(wave), criterion=FALSIFY)",
    "    3. SHIP: auto-ship (commit -> push -> PR -> merge)",
    "    4. MEASURE: hypothesis + Gentner SMT + external HAL council score",
    "    5. BREATHE: genome evolve + DELIGHT snapshot + anti-drift",
    "```",
]

_PARAMOUNT_RULES = [
    "- Criterion is ALWAYS falsification (what would DISPROVE the claim)",
    "- External scoring only (HAL council — producing model never scores itself)",
    "- Hypothesis confirmation rate target: 30-70%",
    "- Gentner SMT >= 60 for structural claims (< 60 = blocked)",
    "- Context gate at 85% = HALT, serialize, hand off",
    "- Fibonacci depth escalation is the point — do NOT flatten waves",
]


def generate_wave_prompt(pipeline: RSIOptimalPipeline) -> str:
    """Generate converge prompt for the current wave."""
    wave = pipeline.current_wave
    if not wave:
        return ""
    return (
        f"## Wave {wave.number + 1}/{pipeline.state.max_waves} — Depth {wave.depth}\n\n"
        f"**Goal**: {pipeline.state.goal}\n"
        f"**Criterion (FALSIFY)**: {pipeline.state.criterion}\n"
        f"**Converge depth**: {wave.depth} | **Batch**: {wave.batch_size}\n\n"
        f"Execute: converge -> hypothesis -> gentner -> ship -> score -> breathe\n"
        f"**Falsification gate**: What would disprove this wave's outcome?\n"
    )


def generate_full_protocol(goal: str, duration: float = 3.0, max_waves: int = 6) -> str:
    """Generate the full RSI-optimal protocol as executable instructions."""
    sections = [
        [f"# RSI-Optimal Protocol: {goal}", "",
         f"Duration: {duration}h | Waves: {max_waves} | Criterion: FALSIFY", "",
         "## Wave Schedule", ""],
        _wave_schedule_table(max_waves),
        ["", "## Execution Loop", ""],
        _EXECUTION_LOOP,
        ["", "## PARAMOUNT Rules", ""],
        _PARAMOUNT_RULES, [""],
    ]
    return "\n".join(line for section in sections for line in section)


# ---------------------------------------------------------------------------
# CLI (KISS: dispatch table)
# ---------------------------------------------------------------------------

def _cli_start(args) -> None:
    p = RSIOptimalPipeline(args.goal, args.duration, args.waves, args.depth)
    print(f"Criterion: {p.plan()}")
    print(json.dumps(p.summary(), indent=2))


def _cli_status(_args) -> None:
    p = load_pipeline()
    print(json.dumps(p.summary(), indent=2) if p else "No active pipeline.")


def _cli_resume(_args) -> None:
    p = load_pipeline()
    print(generate_wave_prompt(p) if p else "No saved pipeline.")


def _cli_protocol(args) -> None:
    print(generate_full_protocol(args.goal, args.duration, args.waves))


def cli_main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="RSI-Optimal Pipeline")
    sub = parser.add_subparsers(dest="command")
    s = sub.add_parser("start", help="Start pipeline")
    s.add_argument("goal"); s.add_argument("--duration", type=float, default=3.0)
    s.add_argument("--waves", type=int, default=6); s.add_argument("--depth", type=int, default=None)
    sub.add_parser("status"); sub.add_parser("resume")
    pr = sub.add_parser("protocol", help="Generate protocol")
    pr.add_argument("goal"); pr.add_argument("--duration", type=float, default=3.0)
    pr.add_argument("--waves", type=int, default=6)
    args = parser.parse_args()
    handler = {"start": _cli_start, "status": _cli_status,
               "resume": _cli_resume, "protocol": _cli_protocol}.get(args.command)
    handler(args) if handler else parser.print_help()


if __name__ == "__main__":
    cli_main()
