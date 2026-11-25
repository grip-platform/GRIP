"""Failure-prevention engine — predict the wall, fire relief before it.

The **third pole** of V>>'s 2026-06-08 dialectic. Pole 1 (runtime modification)
and pole 2 (RSI engineering) both act AT or AFTER a failure — they are *cure*.
This module is *prevention*: it predicts the failure trajectory and fires relief
BEFORE the wall is hit. "Prevention is better than cure."

It **wires the existing copilot precognition substrate** — it does not rebuild it
(GRIP-First / DRY; V>>'s standing correction "WIRE, not CREATE"):

* :class:`~lib.aide.adaptive_engine.PredictionRegistry` — the Popperian
  register-and-grade log (every prediction carries a falsifier + a check time).
* :class:`~lib.aide.adaptive.AdaptiveLearning` — predicted-vs-actual
  calibration (the relief trigger learns its optimal point per work-type instead
  of a fixed 85%).

**The rehabilitated derivative.** ``gentner-enforcer`` rightly killed the
integral/derivative analogy for memory *folding* — there it was surface
decoration. Its *sound* structural home is PREDICTION: ``d(proximity)/d(step)``
is a systematic relational mapping (rate -> extrapolate -> pre-empt), one-to-one
and relational, not surface. That is the only place the derivative earns its keep.

**Domain (MVP): the context wall.** Feed the trajectory of cumulative tokens vs
turn; the predictor extrapolates turns-to-ceiling and fires the ultraclear relief
valve a learned safety-margin BEFORE the ceiling — never the blunt fixed 85%.
The :class:`ProximityPredictor` core is domain-agnostic: any monotone
failure-proximity signal (queue saturation, step-budget overrun, time-to-timeout)
plugs into the same maths.

R0 guard (built in, CBC): a predictor that fires too early is *precognition
theatre* (wasteful relief). The falsifier is registered with every prediction and
the engine grades itself against reality — the trigger is only kept if it
measurably beats the reactive 85% baseline. See ``tests/test_failure_prevention``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

# Wire the existing substrate — import, do not reimplement.
from .adaptive_engine import PredictionRegistry, Prediction  # noqa: F401  (re-exported)

Sample = Tuple[float, float]  # (step, cumulative_value)


# --------------------------------------------------------------------------
# Pure predictor core — the rehabilitated derivative. No I/O, fully testable.
# --------------------------------------------------------------------------

class ProximityPredictor:
    """Extrapolate steps-to-ceiling from a monotone proximity signal.

    Pure and hermetic: ``observe`` accumulates ``(step, value)`` samples;
    ``rate`` is the EMA of the per-segment derivative; ``steps_to_ceiling``
    linearly extrapolates; ``should_fire_relief`` is true iff the wall is within
    ``margin_steps``. A non-positive rate (flat or shrinking) never fires — the
    CBC guarantee that relief is never wasted on a trajectory that is not
    approaching the ceiling.
    """

    def __init__(self, ceiling: float, margin_steps: float,
                 *, ema_alpha: float = 0.5) -> None:
        if ceiling <= 0:
            raise ValueError("ceiling must be positive")
        if margin_steps < 0:
            raise ValueError("margin_steps must be non-negative")
        if not 0.0 < ema_alpha <= 1.0:
            raise ValueError("ema_alpha must be in (0, 1]")
        self.ceiling = float(ceiling)
        self.margin_steps = float(margin_steps)
        self.ema_alpha = float(ema_alpha)
        self._samples: List[Sample] = []

    def observe(self, step: float, value: float) -> "ProximityPredictor":
        """Add one trajectory sample. Returns self for fluent chaining."""
        self._samples.append((float(step), float(value)))
        return self

    def observe_many(self, samples: Sequence[Sample]) -> "ProximityPredictor":
        for step, value in samples:
            self.observe(step, value)
        return self

    @property
    def latest_value(self) -> Optional[float]:
        return self._samples[-1][1] if self._samples else None

    def rate(self) -> float:
        """EMA of d(value)/d(step) across consecutive samples.

        Returns 0.0 when fewer than two samples exist, or when no segment has a
        positive step delta (cannot infer a rate). EMA weights recent segments
        more, so an accelerating trajectory is tracked, not averaged away.
        """
        if len(self._samples) < 2:
            return 0.0
        ema: Optional[float] = None
        for (s0, v0), (s1, v1) in zip(self._samples, self._samples[1:]):
            ds = s1 - s0
            if ds <= 0:
                continue
            slope = (v1 - v0) / ds
            ema = slope if ema is None else (
                self.ema_alpha * slope + (1.0 - self.ema_alpha) * ema
            )
        return ema if ema is not None else 0.0

    def steps_to_ceiling(self) -> Optional[float]:
        """Linear extrapolation: steps until ``latest_value`` reaches ``ceiling``.

        ``None`` when the rate is non-positive (the wall is never reached at the
        current trajectory) or there is no data. ``0.0`` when already at/over it.
        """
        latest = self.latest_value
        if latest is None:
            return None
        if latest >= self.ceiling:
            return 0.0
        r = self.rate()
        if r <= 0:
            return None
        return (self.ceiling - latest) / r

    def should_fire_relief(self) -> bool:
        """True iff the ceiling is within ``margin_steps`` of the current point."""
        stc = self.steps_to_ceiling()
        return stc is not None and stc <= self.margin_steps


# --------------------------------------------------------------------------
# Result record
# --------------------------------------------------------------------------

@dataclass
class WallPrediction:
    """The assessment of a single trajectory against a ceiling."""
    work_type: str
    current_value: float
    ceiling: float
    rate: float
    steps_to_ceiling: Optional[float]
    margin_steps: float
    will_fire: bool
    confidence: float
    pid: Optional[str] = None          # PredictionRegistry id once registered
    evidence: List[str] = field(default_factory=list)

    @property
    def fraction(self) -> float:
        """Current position as a fraction of the ceiling, clamped to [0, 1]."""
        if self.ceiling <= 0:
            return 0.0
        return max(0.0, min(1.0, self.current_value / self.ceiling))


# --------------------------------------------------------------------------
# Engine — wires PredictionRegistry + AdaptiveLearning. DIP: every collaborator
# is injectable so tests run with zero I/O and zero network.
# --------------------------------------------------------------------------

ReliefFn = Callable[[WallPrediction], None]


class FailurePreventionEngine:
    """Predict the wall, register the falsifiable prediction, fire relief early.

    Collaborators are injected (DIP): ``registry`` (defaults to the real
    :class:`PredictionRegistry`), ``learner`` (optional
    :class:`AdaptiveLearning` for calibration), and ``relief_fn`` (the side
    effect that actually fires relief — e.g. emit the ultraclear marker). With
    all three left ``None`` the engine is a pure signal generator.
    """

    CATEGORY = "failure_prevention.margin"
    MIN_MARGIN = 1.0  # prevention floor: a learned margin may shrink but can
                      # never reach 0 (firing only AT the ceiling) — AGORA #3432.

    def __init__(self, *, ceiling: float, margin_steps: float,
                 registry: Optional[PredictionRegistry] = None,
                 learner=None,
                 relief_fn: Optional[ReliefFn] = None) -> None:
        self.ceiling = float(ceiling)
        self.default_margin = float(margin_steps)
        self._registry = registry if registry is not None else PredictionRegistry()
        self._learner = learner
        self._relief_fn = relief_fn

    # ---- assessment -----------------------------------------------------

    def assess(self, samples: Sequence[Sample], *, work_type: str = "default",
               register: bool = True) -> WallPrediction:
        """Build a prediction from a trajectory; optionally register it."""
        margin = self.calibrated_margin(work_type)
        predictor = ProximityPredictor(self.ceiling, margin).observe_many(samples)
        rate = predictor.rate()
        stc = predictor.steps_to_ceiling()
        current = predictor.latest_value or 0.0
        confidence = self._confidence(samples, rate)
        evidence = [
            f"n={len(samples)}", f"rate={rate:.4g}/step",
            f"current={current:.0f}/{self.ceiling:.0f}",
            f"steps_to_ceiling={'inf' if stc is None else f'{stc:.2f}'}",
            f"margin={margin:.2f}",
        ]
        pred = WallPrediction(
            work_type=work_type, current_value=current, ceiling=self.ceiling,
            rate=rate, steps_to_ceiling=stc, margin_steps=margin,
            will_fire=predictor.should_fire_relief(), confidence=confidence,
            evidence=evidence,
        )
        if register:
            stc_txt = "never" if stc is None else f"~{stc:.1f} steps"
            registered = self._registry.register(
                claim=(f"[{work_type}] context wall reached in {stc_txt} "
                       f"at rate {rate:.4g}/step (relief "
                       f"{'WILL' if pred.will_fire else 'will NOT'} fire now)"),
                falsifier=(f"wall NOT reached within {2 * margin:.0f} steps, OR "
                           f"rate falls below {0.5 * rate:.4g}/step "
                           f"(prediction was premature / theatre)"),
                confidence=confidence,
            )
            pred.pid = registered.pid
        return pred

    def maybe_fire_relief(self, prediction: WallPrediction) -> bool:
        """Fire the injected relief side effect iff the prediction says so."""
        if prediction.will_fire and self._relief_fn is not None:
            self._relief_fn(prediction)
        return prediction.will_fire

    # ---- self-grading: close the Popperian loop -------------------------

    def grade(self, pid: str, *, hit_wall: bool, at_step: Optional[float] = None,
              predicted_steps: Optional[float] = None) -> Optional[str]:
        """Grade a past prediction against reality + calibrate the margin.

        ``confirmed`` if the wall was hit (the prediction was right to worry);
        ``falsified`` if it was not (the relief was premature — theatre). Feeds
        :class:`AdaptiveLearning` so the margin self-tunes: chronic premature
        firing shrinks the margin, a missed wall grows it.
        """
        outcome = "confirmed" if hit_wall else "falsified"
        evidence = [f"hit_wall={hit_wall}"]
        if at_step is not None:
            evidence.append(f"at_step={at_step:.2f}")
        if predicted_steps is not None:
            evidence.append(f"predicted_steps={predicted_steps:.2f}")
        self._registry.grade(pid, outcome=outcome, evidence=evidence)
        if self._learner is not None:
            self._learner.observe(self.CATEGORY, {
                "pid": pid, "outcome": outcome, "hit_wall": hit_wall,
                "at_step": at_step, "predicted_steps": predicted_steps,
            })
        return outcome

    def calibrated_margin(self, work_type: str) -> float:
        """The margin to use for ``work_type`` — learned if available, else default.

        Reads a learned preference ``margin:<work_type>`` from
        :class:`AdaptiveLearning` when present; otherwise the constructor default.
        A learned value is floored at :attr:`MIN_MARGIN`, so calibration may
        SHRINK the margin (fire a little later) but can NEVER drive it to 0 —
        which would silently disable prevention by firing only AT the ceiling
        (AGORA #3432: a clamped-0 margin defeats the whole point). The floor is
        the CBC guarantee that prevention always keeps >= MIN_MARGIN headroom.
        """
        if self._learner is None:
            return self.default_margin
        pref = self._learner.get_preference(f"margin:{work_type}")
        if pref is None:
            return self.default_margin
        try:
            return max(self.MIN_MARGIN, float(pref.value))
        except (TypeError, ValueError):
            return self.default_margin

    @staticmethod
    def _confidence(samples: Sequence[Sample], rate: float) -> float:
        """Confidence rises with sample count + monotonicity; 0 on a flat rate."""
        n = len(samples)
        if n < 2 or rate <= 0:
            return 0.0
        # Monotone-increasing values => trustworthy trajectory.
        values = [v for _, v in samples]
        monotone = all(b >= a for a, b in zip(values, values[1:]))
        base = min(0.9, 0.4 + 0.1 * (n - 1))
        return base if monotone else base * 0.6


# --------------------------------------------------------------------------
# CLI demo — runnable proof. `python -m lib.aide.failure_prevention`
# --------------------------------------------------------------------------

def _demo() -> int:
    """Show the engine firing relief BEFORE a fixed 85% gate on a steep climb."""
    ceiling = 1_000_000.0
    engine = FailurePreventionEngine(ceiling=ceiling, margin_steps=3.0)
    # A steep trajectory: +120k tokens/turn, currently at 700k (70%).
    samples = [(0, 340_000), (1, 460_000), (2, 580_000), (3, 700_000)]
    pred = engine.assess(samples, work_type="demo", register=False)
    fired_early = pred.will_fire and pred.fraction < 0.85
    print(f"[precog] current={pred.fraction:.0%} rate={pred.rate:.0f}/turn "
          f"steps_to_ceiling={pred.steps_to_ceiling:.2f} "
          f"will_fire={pred.will_fire} confidence={pred.confidence:.2f}")
    print(f"[precog] fires BEFORE the fixed-85% gate: {fired_early} "
          f"(prevention, not cure)")
    return 0 if fired_early else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_demo())
