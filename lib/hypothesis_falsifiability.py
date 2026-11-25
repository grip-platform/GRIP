"""Jev rates how easily a new hypothesis could be proven wrong, shown once at registration.

The Jev integration plan, Phase 5.2 (V>>'s D-6), first part only: when a hypothesis
is registered from the command line, a decision model (TypeSafe's Jev, through HAL
decide) answers one Score question about its claim, metric, prediction and measure
plan -- an ordered reading of how falsifiable it is, from "nothing could prove it
wrong" to "one named measurement with a threshold and a date would prove it wrong".
The card is advisory only: it is shown AFTER ``hypothesis_engine.register()`` has
already saved the row, and nothing it does can stop, change, or delay-fail a
registration. ``register()`` itself gains no network behaviour -- only the CLI
path calls this module, and only when ``enabled()`` says yes.

Score, not Choice: the vendor's jaggedness page documents a first-listed-option
bias for Choice questions, which is why the sibling modules ask a Choice both ways
and average. A Score's levels are not arbitrary candidates to reorder -- the order
IS the information, weakest to strongest -- so there is nothing to reverse here.

Privacy: the text sent is the hypothesis's own claim, metric, prediction and
measure plan -- usually a claim about GRIP/HAL code or a test, which is internal.
The decision-model privacy floor (lib/decision_privacy.may_send) still runs on it,
because a hypothesis can in principle be registered about anything typed into the
CLI; a text the floor would not pass stays home, same as every other Jev caller.

Persistence: every reading (and the reason for having none) is appended to
logs/hypothesis-falsifiability-shadow.jsonl -- the same directory and naming shape
as the other Jev shadows (logs/prompt-routing-shadow.jsonl,
logs/skill-rerank-shadow.jsonl). Nothing is added to the Hypothesis dataclass or to
learning/hypotheses.jsonl: the join between "what Jev read" and "how the
hypothesis settled" happens at report time, by hypothesis id, across the two
files.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB.parent) not in sys.path:
    sys.path.insert(0, str(_LIB.parent))

#: Ordered weakest-to-strongest; each level names what is missing from the last,
#: so a literal reader can tell them apart without inferring a scale.
LEVELS = (
    "nothing could prove it wrong",
    "only a vague impression could count against it -- no measurement is named",
    "a future measurement is described, but it is not named",
    "a named measurement is described, but it has no threshold or no date",
    "one named measurement with a threshold and a date would prove it wrong",
)
_QUESTION = "How easily could a named measurement prove this hypothesis wrong?"
#: A confidence in this band is reported as "uncertain", never as the level Jev
#: happened to rate highest (the Jev integration plan's Phase 5 common rule).
_UNCERTAIN_LOW, _UNCERTAIN_HIGH = 0.30, 0.70
#: The value scale is 0..len(LEVELS)-1; this threshold is the midpoint, so "high"
#: means at least "a future measurement is described" and "low" means at most a
#: vague impression. Stated here because the forward hypothesis (Phase 5.2 item 6)
#: is pre-registered against exactly this split.
HIGH_THRESHOLD = (len(LEVELS) - 1) / 2
#: Either switch turns it off: the operator's, and the test suite's fence for
#: every decision-model call (tests/conftest.py).
_OFF = (("GRIP_FALSIFIABILITY_CARD", "0"), ("GRIP_CLASSIFY_NO_DECIDE", "1"))
#: Logged beside the other Jev shadows -- same directory, same "<feature>-shadow"
#: naming as prompt-routing-shadow.jsonl and skill-rerank-shadow.jsonl.
_LOG_NAME = "hypothesis-falsifiability-shadow.jsonl"

Decide = Callable[[str, dict], Optional[dict]]


@dataclass(frozen=True)
class Reading:
    """Jev's rating of one hypothesis, or why there is none. Uncalibrated and advisory."""
    level: str = ""
    value: Optional[float] = None
    confidence: Optional[float] = None
    source: str = ""
    unavailable: str = ""

    @property
    def uncertain(self) -> bool:
        return self.confidence is not None and _UNCERTAIN_LOW <= self.confidence <= _UNCERTAIN_HIGH


def enabled() -> bool:
    return not any(os.environ.get(name, "") == value for name, value in _OFF)


def _state_text(claim: str, metric: str, prediction: str, measure_plan: str) -> str:
    lines = [f"Claim: {claim}", f"Metric: {metric}", f"Prediction: {prediction}"]
    if measure_plan:
        lines.append(f"Measure plan: {measure_plan}")
    return "\n".join(lines)


def _number(value) -> Optional[float]:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value) if ok else None


def _read(answer) -> Optional[tuple]:
    """``(level, value, confidence, probabilities)`` from a Score answer, or None."""
    if not isinstance(answer, dict) or not isinstance(answer.get("probabilities"), dict):
        return None
    probs = {k: _number(answer["probabilities"].get(str(i))) for i, k in enumerate(LEVELS)}
    value, confidence = _number(answer.get("score")), _number(answer.get("confidence"))
    numbers = list(probs.values()) + [confidence]
    if value is None or not 0 <= value <= len(LEVELS) - 1 or any(n is None or not 0 <= n <= 1 for n in numbers):
        return None
    level = max(LEVELS, key=lambda k: (probs[k], -LEVELS.index(k)))
    return level, value, confidence, probs


def _default_decide(state: str, questions: dict) -> Optional[dict]:
    from lib.hal_llm_adapter import _query_grip_decide
    return _query_grip_decide(state, questions, timeout_s=10.0)


def _floor(text: str, cwd: Optional[str]) -> tuple:
    try:
        from lib.decision_privacy import may_send
        return may_send(text, cwd=cwd)
    except Exception:  # noqa: BLE001 -- no floor, no send
        return False, "privacy_floor_unavailable"


def score_falsifiability(claim: str, metric: str, prediction: str, measure_plan: str = "",
                         *, cwd: Optional[str] = None, decide: Decide = _default_decide) -> Reading:
    """Jev's Score of how easily a named measurement could prove this hypothesis wrong."""
    if not enabled():
        return Reading(unavailable="off")
    state = _state_text(claim, metric, prediction, measure_plan)
    allowed, why = _floor(state, cwd)
    if not allowed:
        return Reading(unavailable=f"privacy_floor:{why}")
    question = {"type": "score", "instructions": _QUESTION, "criteria": list(LEVELS)}
    try:
        result = decide(state, {"falsifiability": question})
    except Exception:  # noqa: BLE001 -- advisory; never raises
        result = None
    read = _read((result.get("answers") or {}).get("falsifiability")) \
        if isinstance(result, dict) and result.get("ok") else None
    if read is None:
        return Reading(unavailable="no_answer")
    level, value, confidence, _probs = read
    return Reading(level, value, confidence, str(result.get("model") or ""))


def advisory_line(reading: Reading) -> str:
    """One quiet terminal line for ``reading``, or "" when there is nothing worth printing.

    Never names the hypothesis good or bad -- only how falsifiable it reads as, or
    that the reading is uncertain.
    """
    if reading.unavailable or not reading.level:
        return ""
    verdict = "uncertain" if reading.uncertain else reading.level
    return f"  Jev's reading (machine, unmeasured precision): {verdict}."


def falsifiability_row(hid: str, reading: Reading, *, ts: float) -> dict:
    """The log row: the hypothesis id and Jev's reading, never the claim text."""
    return {"ts": ts, "id": hid, "machine_reading": True, "calibrated": False,
            "level": reading.level, "value": reading.value, "confidence": reading.confidence,
            "model": reading.source, "unavailable": reading.unavailable}


def _default_log_path() -> Path:
    from lib.claude_dir import get_claude_dir
    return get_claude_dir() / "logs" / _LOG_NAME


def show_card(h, *, cwd: Optional[str] = None, log_path: Optional[Path] = None,
              decide: Decide = _default_decide) -> None:
    """Score ``h`` (a Hypothesis), print one advisory line (or nothing), and log the
    reading. Never raises -- called only after ``register()`` has already saved the
    row, so nothing here can stop, change, or delay-fail a registration.
    """
    try:
        reading = score_falsifiability(h.claim, h.metric, h.prediction, h.measure_plan,
                                       cwd=cwd, decide=decide)
        if reading.unavailable == "off":
            return
        path = log_path or _default_log_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(falsifiability_row(h.id, reading, ts=time.time())) + "\n")
        line = advisory_line(reading)
        if line:
            print(line)
    except Exception:  # noqa: BLE001 -- advisory only; registration already completed
        return


# =========================================================================
# report -- join the sidecar log with the registry
# =========================================================================

_SETTLED = frozenset({"confirmed", "refuted"})
_ENDED_OTHER = frozenset({"inconclusive", "expired"})


def _read_log(path: Path) -> list:
    """Every JSON row in ``path``, or [] when it is missing, empty, or unreadable."""
    if not Path(path).exists():
        return []
    rows = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows


def _latest_readings(rows: list) -> Dict[str, float]:
    """Each hypothesis id's most recent scored ``value``; unscored/unavailable rows
    (no ``value``) are skipped, and a later row for the same id overwrites an
    earlier one, since the log is append-only in time order.

    A row that is valid JSON but not an object -- ``null``, a bare number, a
    list, a string -- is skipped too, before either field is read. AGORA
    (DeepSeek V4 Flash, PR #8084) found the previous guard checked
    ``isinstance(row, dict)`` only inside the ternary on the SECOND field
    (``value``); ``row.get("id")`` on the first field ran unconditionally, so
    any such line crashed the read with ``AttributeError`` instead of being
    skipped.
    """
    out: Dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        hid, value = row.get("id"), _number(row.get("value"))
        if hid and value is not None:
            out[hid] = value
    return out


def _settlement_counts(values: Dict[str, float], hypotheses: list, *, threshold: float) -> dict:
    """``{"high": {"settled": n, "ended_other": n}, "low": {...}}`` by score value.

    A hypothesis still "pending" is neither settled nor ended, so it is left out of
    both counts -- it has not yet produced an answer for either bucket.
    """
    buckets = {"high": {"settled": 0, "ended_other": 0}, "low": {"settled": 0, "ended_other": 0}}
    by_id = {h.id: h for h in hypotheses}
    for hid, value in values.items():
        h = by_id.get(hid)
        if h is None:
            continue
        bucket = "high" if value >= threshold else "low"
        if h.status in _SETTLED:
            buckets[bucket]["settled"] += 1
        elif h.status in _ENDED_OTHER:
            buckets[bucket]["ended_other"] += 1
    return buckets


def report(*, log_path: Optional[Path] = None) -> dict:
    """Join the sidecar log with the registry: settlement counts for high- vs
    low-scored hypotheses. Never raises on an empty or missing log."""
    from lib.hypothesis_engine import _load_hypotheses
    values = _latest_readings(_read_log(log_path or _default_log_path()))
    return _settlement_counts(values, _load_hypotheses(), threshold=HIGH_THRESHOLD)


USAGE = "Usage: python -m lib.hypothesis_falsifiability report [--json]"


def _cmd_report(*, as_json: bool, log_path: Optional[Path] = None) -> None:
    result = report(log_path=log_path)
    if as_json:
        print(json.dumps(result, separators=(",", ":")))
        return
    labels = (("high", f"high-scored (value >= {HIGH_THRESHOLD})"),
              ("low", f"low-scored (value < {HIGH_THRESHOLD})"))
    for bucket, label in labels:
        counts = result[bucket]
        print(f"{label}: {counts['settled']} settled, {counts['ended_other']} ended inconclusive/expired")


def main(argv: Optional[list] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(USAGE)
        return 1
    if argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if argv[0] != "report":
        print(f"Unknown command: {argv[0]}")
        return 1
    _cmd_report(as_json="--json" in argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
