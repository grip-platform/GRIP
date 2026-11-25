"""The lossless gate for a rewrite: nothing a reader needed may go missing (plan S4).

An STE rewrite earns its score by shortening sentences. The cheapest way to shorten
a sentence is to drop something from it, and the 2026-10-10 plan measured a telegram
rewrite, a table rewrite and a reason-free rewrite all scoring a perfect 1.00
(plans/ULTRAPLAN-2026-10-10-ste-improver-loop.md, D2). This gate reads the source and
the rewrite side by side and refuses a rewrite that lost a fact-bearing token:

  error    a technical token (a path, an id, a flag, a constant, a version) in the
           source and not in the rewrite; a bare number; a quoted span (backticks or
           double quotes); a modal (must, should, may ...) used fewer times
  warning  a negation (not, never, without ...) used fewer times

Counts, never positions: a rewrite may reorder freely. The technical-token shapes
are lib/cos_metric.py's, which is also the W0 edge-B check, so one definition serves
both. The number, modal and negation patterns are lib/ste_counters.py's. Pure
functions, no I/O; scripts/ste-lossless-check.py is the command line.

Hypothesis H-S4 (registered with the PR): the gate refuses every planted drop and
refuses at most 1 of the 20 faithful H778 rewrites.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

try:
    from . import cos_metric, ste_counters  # type: ignore[attr-defined]  # noqa: PLC0415
except ImportError:
    import cos_metric  # type: ignore[no-redef]  # noqa: PLC0415
    import ste_counters  # type: ignore[no-redef]  # noqa: PLC0415

GATE_VERSION = "1"

#: A span the writer set apart on purpose: inline code, or a double-quoted phrase of
#: three characters or more. Its text must survive, quoted or not.
QUOTED = re.compile(r"`([^`\n]+)`|\"([^\"\n]{3,})\"|“([^”\n]{3,})”")


@dataclass(frozen=True)
class LosslessResult:
    ok: bool
    errors: tuple = ()
    warnings: tuple = ()
    dropped: dict = field(default_factory=dict)

    def lines(self) -> list:
        """One line per finding, errors first, for a report or a refusal message."""
        return [f"error: {e}" for e in self.errors] + [f"warning: {w}" for w in self.warnings]


def technical_tokens(text: str) -> set:
    return set(cos_metric._TECH.findall(text or ""))


def numbers(text: str) -> Counter:
    return Counter(ste_counters.NUMBER.findall(text or ""))


def quoted_spans(text: str) -> set:
    return {next(g for g in m.groups() if g) for m in QUOTED.finditer(text or "")}


def word_counts(pattern: re.Pattern, text: str) -> Counter:
    return Counter(w.lower() for w in pattern.findall(text or ""))


def _fewer(before: Counter, after: Counter) -> list:
    """The keys ``after`` has fewer of than ``before``, as "key (before -> after)"."""
    return [f"{key} ({before[key]} -> {after[key]})" for key in sorted(before) if after[key] < before[key]]


def check(source: str, rewrite: str) -> LosslessResult:
    """Refuse ``rewrite`` when a fact-bearing token of ``source`` is missing from it."""
    missing_tech = sorted(technical_tokens(source) - technical_tokens(rewrite))
    missing_numbers = _fewer(numbers(source), numbers(rewrite))
    missing_quoted = sorted(q for q in quoted_spans(source) if q not in (rewrite or ""))
    fewer_modals = _fewer(word_counts(ste_counters.MODAL, source), word_counts(ste_counters.MODAL, rewrite))
    fewer_negations = _fewer(word_counts(ste_counters.NEGATION, source),
                             word_counts(ste_counters.NEGATION, rewrite))
    errors = ([f"technical token dropped: {t}" for t in missing_tech]
              + [f"number dropped: {n}" for n in missing_numbers]
              + [f"quoted span dropped: {q}" for q in missing_quoted]
              + [f"modal dropped: {m}" for m in fewer_modals])
    warnings = [f"negation dropped: {n}" for n in fewer_negations]
    dropped = {"technical": missing_tech, "numbers": missing_numbers, "quoted": missing_quoted,
               "modals": fewer_modals, "negations": fewer_negations}
    return LosslessResult(ok=not errors, errors=tuple(errors), warnings=tuple(warnings), dropped=dropped)


# ---- planted drops: the gate's own control ------------------------------------------

def planted_drops(source: str) -> list:
    """Variants of ``source`` with one fact-bearing item removed, one per class the
    source carries: its first technical token, its first bare number, its first
    quoted span, its first modal. A token or a span is removed everywhere it occurs,
    because the gate counts facts, not repetitions: a source that names a path twice
    and a variant that still names it once has lost nothing. Each variant must be
    refused; a gate that passes one is blind to that class."""
    out = []
    tech = sorted(technical_tokens(source), key=lambda t: source.find(t))
    if tech:
        out.append(("technical", source.replace(tech[0], "")))
    number = ste_counters.NUMBER.search(source or "")
    if number:
        out.append(("number", source[: number.start()] + source[number.end():]))
    quoted = sorted(quoted_spans(source), key=lambda q: source.find(q))
    if quoted:
        out.append(("quoted", source.replace(quoted[0], "")))
    modal = ste_counters.MODAL.search(source or "")
    if modal:
        out.append(("modal", source[: modal.start()] + source[modal.end():]))
    return out


def verdict_on_pairs(pairs: list, max_refused: int = 1) -> dict:
    """The H-S4 reading over (source, rewrite) pairs: how many faithful rewrites the
    gate refuses, and how many planted drops it catches. CONFIRMED when every
    planted drop is refused and at most ``max_refused`` faithful rewrites are."""
    refused = [p["id"] for p in pairs if not check(p["source"], p["rewrite"]).ok]
    planted = [(p["id"], kind, check(p["source"], variant).ok)
               for p in pairs for kind, variant in planted_drops(p["source"])]
    missed = [(pid, kind) for pid, kind, ok in planted if ok]
    word = "CONFIRMED" if not missed and len(refused) <= max_refused else "REFUTED"
    return {"gate_version": GATE_VERSION, "pairs": len(pairs), "refused": refused,
            "planted": len(planted), "planted_missed": missed, "max_refused": max_refused, "verdict": word}
