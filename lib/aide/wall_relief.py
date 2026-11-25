"""Context-wall relief consumer — wire the merged FailurePreventionEngine (⑥) to
the ultraclear relief marker (②). Predict the wall, fire relief BEFORE it.

Self-contained: keeps its OWN bounded trajectory ring-buffer of context-token
samples, so it needs **no change to the PARAMOUNT context gate**. Each tick
appends the current sample, asks the engine whether the wall is within the
(learned, floored) margin, and — if so — writes the ultraclear-pending marker.

The marker is inert without the ② watcher that injects ``/clear`` out-of-band
(``/clear`` cannot self-invoke mid-tool-call). That is deliberate: this PRODUCER
ships and is testable independently of the watcher CONSUMER. Until the watcher
lands, the marker is an honest, auditable "relief recommended" signal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import jsonl_io, paths
from .failure_prevention import FailurePreventionEngine, WallPrediction

DEFAULT_CEILING = 1_000_000.0          # 1M-token plan; overridable per construction
DEFAULT_MAX_SAMPLES = 24               # ring-buffer depth — recent trend, not history

TRAJECTORY = paths.STATE_DIR / "context_trajectory.jsonl"
ULTRACLEAR_MARKER = paths.STATE_DIR.parent / "ultraclear-pending.json"  # state/…


def _clean_tokens(tokens: float) -> float:
    """Sanitise a context-token sample at the ingress boundary.

    Context tokens are physically finite and non-negative. A non-finite value
    (NaN/inf — a caller bug) would propagate silently through the EMA derivative
    into a nonsensical ``WallPrediction``; a negative value would mask the wall.
    Reject the former loudly, clamp the latter, so one bad sample cannot poison
    the ring buffer downstream.
    """
    t = float(tokens)
    if not math.isfinite(t):
        raise ValueError(f"context tokens must be finite, got {tokens!r}")
    return max(0.0, t)


class TrajectoryStore:
    """Bounded ring-buffer of context-token samples for the wall predictor.

    Append-and-trim: keeps the most recent ``max_samples`` so the EMA derivative
    tracks the *current* trend, not ancient history. Persisted via the shared
    ``jsonl_io`` shim (atomic rewrite). Turns are re-indexed 0..n-1 on read so the
    predictor sees a uniform step axis regardless of how many samples were trimmed.

    Re-index invariant: the uniform 0..n-1 axis is sound because the consumer
    appends exactly one sample per turn and trimming only drops the *oldest*
    rows — the retained samples therefore stay consecutive, with no mid-stream
    gaps for the uniform axis to mask. (A skipped tick is out of scope for this
    producer; the watcher CONSUMER ticks every turn.)
    """

    def __init__(self, path: Path = TRAJECTORY,
                 max_samples: int = DEFAULT_MAX_SAMPLES) -> None:
        self._path = path
        self._max = max(2, int(max_samples))

    def append(self, tokens: float) -> List[Tuple[float, float]]:
        rows = jsonl_io.read(self._path)
        rows.append({"tokens": _clean_tokens(tokens)})
        rows = rows[-self._max:]
        jsonl_io.write_all(self._path, rows)
        return self._as_samples(rows)

    def read(self) -> List[Tuple[float, float]]:
        return self._as_samples(jsonl_io.read(self._path))

    @staticmethod
    def _as_samples(rows) -> List[Tuple[float, float]]:
        return [(float(i), float(r.get("tokens", 0.0))) for i, r in enumerate(rows)]


def write_ultraclear_marker(prediction: WallPrediction, *,
                            path: Path = ULTRACLEAR_MARKER,
                            now_iso: Optional[str] = None) -> Path:
    """Emit the ② ultraclear-pending marker — the relief signal the watcher acts on.

    Records WHY relief fired (the prediction's evidence) so the watcher and the
    operator can audit the trigger. Pure JSON write via the shared shim.

    ``now_iso`` is injectable for deterministic tests; when omitted it is
    generated internally (UTC ISO-8601) so the audit marker ALWAYS carries a real
    timestamp — an empty ``predicted_at`` would defeat the marker's audit purpose.
    """
    jsonl_io.write_json(path, {
        "status": "pending",
        "reason": "precog: context wall predicted within margin — fire relief before it",
        "context_fraction": round(prediction.fraction, 4),
        "steps_to_ceiling": prediction.steps_to_ceiling,
        "rate_per_turn": prediction.rate,
        "margin": prediction.margin_steps,
        "confidence": prediction.confidence,
        "evidence": prediction.evidence,
        "predicted_at": now_iso or datetime.now(timezone.utc).isoformat(),
        "pid": prediction.pid,
    })
    return path


ReliefWriter = Callable[[WallPrediction], None]


@dataclass
class ReliefOutcome:
    prediction: WallPrediction
    fired: bool


class ContextWallRelief:
    """Tick-driven consumer: sample → predict → fire relief if the wall is near.

    DIP: ``store`` (trajectory), ``engine`` (the merged FailurePreventionEngine),
    and ``relief_writer`` (the marker emit) are all injectable, so tests run with
    zero IO. With ``relief_writer=None`` it is a pure signal (predicts, never
    writes) — an observe / dry-run mode.
    """

    def __init__(self, *, ceiling: float = DEFAULT_CEILING,
                 store: Optional[TrajectoryStore] = None,
                 engine: Optional[FailurePreventionEngine] = None,
                 relief_writer: Optional[ReliefWriter] = None,
                 work_type: str = "session") -> None:
        self.ceiling = float(ceiling)
        self._store = store if store is not None else TrajectoryStore()
        self._engine = engine if engine is not None else FailurePreventionEngine(
            ceiling=self.ceiling, margin_steps=3.0,
        )
        self._relief_writer = relief_writer
        self._work_type = work_type

    def tick(self, current_tokens: float, *, register: bool = True) -> ReliefOutcome:
        samples = self._store.append(current_tokens)
        prediction = self._engine.assess(samples, work_type=self._work_type,
                                          register=register)
        fired = False
        if prediction.will_fire and self._relief_writer is not None:
            self._relief_writer(prediction)
            fired = True
        return ReliefOutcome(prediction=prediction, fired=fired)


def _main(argv=None) -> int:  # pragma: no cover - thin CLI
    import argparse
    ap = argparse.ArgumentParser(description="Context-wall relief tick (⑥ → ② marker).")
    ap.add_argument("--tokens", type=float, required=True, help="current context tokens")
    ap.add_argument("--ceiling", type=float, default=DEFAULT_CEILING)
    ap.add_argument("--marker", type=str, default=str(ULTRACLEAR_MARKER))
    ap.add_argument("--dry-run", action="store_true",
                    help="predict only, do not write the marker")
    args = ap.parse_args(argv)
    marker_path = Path(args.marker)
    writer = None if args.dry_run else (
        lambda p: write_ultraclear_marker(p, path=marker_path)
    )
    out = ContextWallRelief(ceiling=args.ceiling, relief_writer=writer).tick(
        args.tokens, register=False)
    p = out.prediction
    print(f"[wall-relief] frac={p.fraction:.0%} rate={p.rate:.0f}/turn "
          f"steps_to_ceiling={p.steps_to_ceiling} will_fire={p.will_fire} "
          f"fired={out.fired}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
