"""Hypothesis-verdict delight card for GRIP's falsification engine.

GRIP is a falsification machine: every loop is meant to exit on a hypothesis
resolving against reality. This renders that scientific MOMENT as deterministic
delight -- and it celebrates a FALSIFIED hypothesis exactly as much as a
CONFIRMED one. A refuted prediction is not a failure; it is the experiment
WORKING. (Popper: good tests kill flawed theories; we remain alive to guess
again.) No existing GRIP card marks this moment -- aurora marks the night's
aggregate work, ship-card the ship, rave a milestone; this marks the verdict.

Design (mirrors lib/rave_card.py):
- Same (hypothesis_id, outcome, claim) always renders the same card -- the
  aphorism is SHA-256-seeded, never random. Bit-identical, unit-testable.
- Pure stdlib, no network. The render core takes explicit data; `from_engine`
  is the SINGLE I/O seam that reads the real ledger (learning/hypotheses.jsonl)
  so `/verdict H027` works end-to-end without the caller restating the claim.
- Evidence tokens (metric / prediction / actual) are rendered VERBATIM -- never
  rounded, glossed, or truncated (the output-tier precision invariant: a claim's
  evidence survives at every intensity).

Used by:
- the /verdict slash command (skills/verdict/SKILL.md)
- ad-hoc from the ledger:  python3 -m lib.verdict_card H027 --from-engine
- explicit:  python3 -m lib.verdict_card H027 "claim" CONFIRMED \
                 --metric latency --prediction "<200ms" --actual "171ms"
"""

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

# --- claude_dir shim (path-portability.md) -------------------------------------
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
try:
    from claude_dir import get_claude_dir  # noqa: E402
except Exception:  # pragma: no cover - fallback only if shim missing
    def get_claude_dir() -> Path:
        env = os.environ.get("CLAUDE_DIR", "").strip()
        return Path(env) if env else Path.home() / ".claude"


# Canonical outcomes. Inputs are normalised onto these three (a hypothesis can
# only ever land CONFIRMED / REFUTED / INCONCLUSIVE -- PENDING is not a verdict).
CONFIRMED = "CONFIRMED"
REFUTED = "REFUTED"
INCONCLUSIVE = "INCONCLUSIVE"

# Input aliases -> canonical. "FALSIFIED" is the Popperian synonym for REFUTED.
_OUTCOME_ALIASES: Dict[str, str] = {
    "CONFIRMED": CONFIRMED, "CONFIRM": CONFIRMED, "PASS": CONFIRMED,
    "CORROBORATED": CONFIRMED, "HELD": CONFIRMED,
    "REFUTED": REFUTED, "FALSIFIED": REFUTED, "FALSIFY": REFUTED,
    "FAIL": REFUTED, "FAILED": REFUTED, "DISCONFIRMED": REFUTED,
    "INCONCLUSIVE": INCONCLUSIVE, "INDETERMINATE": INCONCLUSIVE,
    "UNKNOWN": INCONCLUSIVE, "UNVERIFIABLE": INCONCLUSIVE,
}

# Per-outcome presentation. glyph + banner word + ANSI colour role + the framing
# coda line. REFUTED is GOLD, never red -- a refutation is honoured, not mourned.
# Tuple shape: (glyph, ansi_role, coda)
_OUTCOMES: Dict[str, Tuple[str, str, str]] = {
    CONFIRMED: ("✓", "green",
                "Corroborated, not proven. Keep the falsifier loaded."),
    REFUTED: ("✗", "gold",
              "Falsified is not failed. The experiment WORKED."),
    INCONCLUSIVE: ("~", "cyan",
                   "Not yet wrong, not yet right. Sharpen the test, run again."),
}

# Aphorism pools, one per outcome. GRIP-voiced (deliberately unattributed to
# avoid mis-attributing famous lines -- R0). Selected deterministically by seed
# so the same verdict always speaks the same line.
_APHORISMS: Dict[str, Tuple[str, ...]] = {
    CONFIRMED: (
        "The prediction held against a test that could have killed it.",
        "Reality agreed this time. Stay suspicious of the next claim.",
        "Survived falsification -- which is the only kind of true there is.",
        "It passed the gauntlet you built to break it.",
    ),
    REFUTED: (
        "The prediction died so the truth could live.",
        "We guessed; reality answered; we are wiser. Guess again.",
        "A theory that cannot fail cannot teach. This one taught.",
        "The map was wrong. Now you know where the territory isn't.",
    ),
    INCONCLUSIVE: (
        "The test was too blunt to bite. Sharpen the blade.",
        "Reality shrugged. Design a question it cannot dodge.",
        "No signal is itself a finding: the experiment needs more teeth.",
    ),
}

# Print-only delight (2026-11-01, D9). None of these ever changes an outcome.
_TOMBSTONE = "The tower fell."
_NO_EVIDENCE_STAMP = "[ BECAUSE I SAID SO ]  No evidence at the end of the why ladder. Marked for deletion."
_FIVES = "Somebody count the fives."
_CITES_THE_NUMBER = re.compile(r"\b67\b")

_ANSI = {
    "green": "\033[32m", "gold": "\033[33m", "cyan": "\033[36m",
    "bold": "\033[1m", "dim": "\033[2m", "reset": "\033[0m",
}

_WIDTH = 60  # inner box width; fixed for deterministic rendering


@dataclass(frozen=True)
class VerdictCard:
    """Immutable hypothesis-verdict card. Render via `render()`/`format_card`."""

    hypothesis_id: str
    claim: str
    outcome: str          # canonical: CONFIRMED / REFUTED / INCONCLUSIVE
    aphorism: str
    metric: Optional[str] = None
    prediction: Optional[str] = None
    actual: Optional[str] = None
    no_evidence: bool = False

    def render(self, color: bool = False) -> str:
        return format_card(self, color=color)


def normalise_outcome(outcome: str) -> str:
    """Map any accepted spelling onto a canonical outcome.

    Raises ValueError on an unrecognised outcome -- a verdict card must never
    silently invent a verdict (a wrong outcome is worse than a loud error).
    """
    key = (outcome or "").strip().upper()
    if key not in _OUTCOME_ALIASES:
        raise ValueError(
            "unknown outcome %r -- expected one of CONFIRMED / REFUTED / "
            "INCONCLUSIVE (or an alias e.g. FALSIFIED)" % outcome
        )
    return _OUTCOME_ALIASES[key]


def _seed(hypothesis_id: str, outcome: str) -> int:
    """Deterministic seed from id+outcome -- pure SHA-256, no randomness."""
    payload = ("%s|%s" % (hypothesis_id, outcome)).encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest(), 16)


def _select_aphorism(hypothesis_id: str, outcome: str) -> str:
    """Pick one aphorism from the outcome's pool, seeded by id+outcome.

    Same verdict -> same line. Different ids spread across the pool because the
    digest differs. Never crosses pools (a CONFIRMED card never speaks a REFUTED
    line) -- the pool is keyed strictly on the canonical outcome.
    """
    pool = _APHORISMS[outcome]
    return pool[_seed(hypothesis_id, outcome) % len(pool)]


def card_for_verdict(
    hypothesis_id: str,
    claim: str,
    outcome: str,
    metric: Optional[str] = None,
    prediction: Optional[str] = None,
    actual: Optional[str] = None,
    no_evidence: bool = False,
) -> VerdictCard:
    """Build a deterministic verdict card.

    Args:
        hypothesis_id: e.g. "H027" -- seeds the aphorism, shown in the banner.
        claim:         the hypothesis claim (one line).
        outcome:       CONFIRMED / REFUTED / INCONCLUSIVE (or an alias).
        metric/prediction/actual: optional evidence, rendered VERBATIM.
        no_evidence:   a why ladder ended with no evidence, which stamps the card.

    Raises:
        ValueError: empty id/claim, or unrecognised outcome.
    """
    if not (hypothesis_id and hypothesis_id.strip()):
        raise ValueError("hypothesis_id required")
    if not (claim and claim.strip()):
        raise ValueError("claim required")
    canonical = normalise_outcome(outcome)
    return VerdictCard(
        hypothesis_id=hypothesis_id.strip(),
        claim=claim.strip(),
        outcome=canonical,
        aphorism=_select_aphorism(hypothesis_id.strip(), canonical),
        metric=metric,
        prediction=prediction,
        actual=actual,
        no_evidence=no_evidence,
    )


def _delight_lines(card: VerdictCard) -> list:
    """The print-only lines this card has earned, in order. They never touch the outcome."""
    lines = []
    if card.outcome == REFUTED:
        lines.append(_TOMBSTONE)
    if card.no_evidence:
        lines.append(_NO_EVIDENCE_STAMP)
    if _CITES_THE_NUMBER.search(card.claim):
        lines.append(_FIVES)
    return lines


def _c(text: str, role: str, color: bool) -> str:
    if not color:
        return text
    return "%s%s%s" % (_ANSI.get(role, ""), text, _ANSI["reset"])


def format_card(card: VerdictCard, color: bool = False) -> str:
    """Render a VerdictCard as a string ready to print/post.

    color=False (default) emits pure text with zero ANSI escapes -- stable and
    byte-identical, which is what the tests assert on. color=True wraps the
    glyph/banner/coda in ANSI for an interactive terminal.
    """
    glyph, role, coda = _OUTCOMES[card.outcome]
    top = "+" + "-" * _WIDTH + "+"
    title = "%s  VERDICT  %s  %s  %s" % (glyph, card.hypothesis_id, glyph, card.outcome)
    title_line = "|" + _c(title.center(_WIDTH), role, color) + "|"

    lines = [top, title_line, top, ""]
    lines.append("  Hypothesis:  %s" % card.claim)
    # Evidence -- VERBATIM, only the lines that exist (never fabricate evidence).
    if card.metric is not None:
        lines.append("  Metric:      %s" % card.metric)
    if card.prediction is not None:
        lines.append("  Predicted:   %s" % card.prediction)
    if card.actual is not None:
        lines.append("  Observed:    %s" % card.actual)
    lines.append("")
    lines.append("  " + _c('"%s"' % card.aphorism, "bold", color))
    lines.extend("  " + _c(line, role, color) for line in _delight_lines(card))
    lines.append("")
    lines.append("  " + _c(coda, "dim", color))
    lines.append("  " + _c("-- confirmation and falsification are both science --", "dim", color))
    return "\n".join(lines)


def from_engine(
    hypothesis_id: str,
    ledger_path: Optional[Path] = None,
) -> VerdictCard:
    """Build a card from the real hypothesis ledger (learning/hypotheses.jsonl).

    The single I/O seam -- kept out of the pure render core. Reads the ledger,
    finds the record by id, maps its fields onto a card. A still-PENDING
    hypothesis has no verdict yet, so this raises (you cannot celebrate a
    verdict that has not happened -- verify-canonical: status is the record
    self-reporting; a PENDING one has nothing to render).

    Raises:
        FileNotFoundError: ledger absent.
        KeyError:          id not found, or found but still PENDING.
    """
    path = ledger_path or (get_claude_dir() / "learning" / "hypotheses.jsonl")
    if not path.exists():
        raise FileNotFoundError("hypothesis ledger not found: %s" % path)
    record = None
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue  # tolerate a malformed line, keep scanning
            if str(row.get("id", "")) == hypothesis_id:
                record = row  # last write wins (jsonl is append-only)
    if record is None:
        raise KeyError("hypothesis %s not found in %s" % (hypothesis_id, path))
    status = str(record.get("status", "")).strip().upper()
    if status in ("", "PENDING"):
        raise KeyError(
            "hypothesis %s is still %s -- no verdict to render yet"
            % (hypothesis_id, status or "PENDING")
        )
    return card_for_verdict(
        hypothesis_id=hypothesis_id,
        claim=str(record.get("claim", "(claim unavailable)")),
        outcome=status,
        metric=_opt(record.get("metric")),
        prediction=_opt(record.get("prediction")),
        actual=_opt(record.get("actual")),
    )


def _opt(value) -> Optional[str]:
    """Normalise a ledger field to an optional verbatim string."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a GRIP hypothesis-verdict delight card."
    )
    parser.add_argument("hypothesis_id", help="e.g. H027")
    parser.add_argument("claim", nargs="?", default="",
                        help="hypothesis claim (omit when using --from-engine)")
    parser.add_argument("outcome", nargs="?", default="",
                        help="CONFIRMED / REFUTED / INCONCLUSIVE (or --from-engine)")
    parser.add_argument("--metric", default=None)
    parser.add_argument("--prediction", default=None)
    parser.add_argument("--actual", default=None)
    parser.add_argument("--because-i-said-so", action="store_true",
                        help="stamp a card whose why ladder ended with no evidence")
    parser.add_argument("--from-engine", action="store_true",
                        help="read the claim+outcome from learning/hypotheses.jsonl")
    parser.add_argument("--no-color", action="store_true",
                        help="force plain output even on a tty")
    args = parser.parse_args(argv)

    color = (not args.no_color) and sys.stdout.isatty()
    try:
        if args.from_engine:
            card = from_engine(args.hypothesis_id)
        else:
            if not args.claim or not args.outcome:
                parser.error("claim and outcome required unless --from-engine")
            card = card_for_verdict(
                args.hypothesis_id, args.claim, args.outcome,
                metric=args.metric, prediction=args.prediction, actual=args.actual,
                no_evidence=args.because_i_said_so,
            )
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print("verdict: %s" % exc, file=sys.stderr)
        return 1
    print(format_card(card, color=color))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
