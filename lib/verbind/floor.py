"""Verbind meaning floor -- evidence about what a coined verb means. Never a verdict.

The MAC half is CF-IOF (Scheepers 2011, eq. 2.3.6), reused rather than
re-implemented: ``lib/semantic_keyword_floor.SemanticKeywordFloor`` is fitted
with one document per skill, the skill being the class, so each word in the
catalogue is weighted by how uniquely it belongs to one skill's text. The words
scored are the ones around the verb: the verb's own hyphen-separated parts and
the bounded argument span.

What this module adds on top of the engine:

  ranking    every skill sorted by its CF-IOF score, best first;
  margin     how far the best stands clear of the runner-up, normalised by
             the best's distance from the floor of the ranking (the lowest
             score, a skill sharing no word with the prompt):
                 margin = (s1 - s2) / (s1 - s_min), in [0, 1];
             0 is a tie, 1 is a candidate no other skill comes near;
  uncertain  the margin is below ``uncertain_below`` (a parameter), or no
             word of the prompt was known, so no recommendation is made;
  recommend  OFF by default, because the measurement said so: on the blind
             bank 5 of the 6 recommendations it would have made were wrong
             (``RECOMMEND_DEFAULT`` says where the numbers are);
  FAC        an optional structural re-rank of the top window through
             ``lib/smt/macfac.fac_rerank``. It engages only when a reviewed
             structure exists for the prompt and for the candidates. None
             exists yet (docs/VERBIND.md section 5 measured why the lexicon
             bridge cannot supply them), so in production the MAC ranking
             stands alone; the path is exercised by its tests.

The output is ``Evidence``, which has no verdict field. It pre-fills a Koppel
round; a human decides. CF-IOF's last outing on the operator-close corpus was
killed (docs/measurements/2026-10-02-semantic-keyword-floor-verdict.md), so
nothing here is assumed to work: ``tests/test_verbind_floor.py`` measures it on
a blind bank written before this module existed.
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional, Sequence

from lib.semantic_keyword_floor import SemanticKeywordFloor  # reuse, not a copy

from .gate import Candidate, Evidence

__all__ = [
    "DEFAULT_FAC_WINDOW",
    "DEFAULT_TOP_K",
    "DEFAULT_UNCERTAIN_BELOW",
    "MeaningFloor",
    "RECOMMEND_DEFAULT",
    "class_text",
    "normalised_margin",
]

#: Pre-registered before the floor was ever run on the blind bank (commit
#: history: the bank commit precedes this file). A runner-up holding three
#: quarters of the leader's evidence is treated as a near tie.
DEFAULT_UNCERTAIN_BELOW = 0.25
DEFAULT_TOP_K = 3
DEFAULT_FAC_WINDOW = 10

#: OFF, because of the measurement. On the blind bank, at the pre-registered
#: threshold, the floor would have labelled 6 of 20 prompts "(Recommended)" and
#: 5 of those labels were wrong; recall at 1 was 3 of 20
#: (docs/measurements/2026-10-03-verbind-floor-blind-bank.md). So a floor built
#: with the default ranks candidates but recommends none. Turning this on needs
#: a floor that passes a FRESH blind bank -- retuning the threshold on this one
#: would make it no longer held out.
RECOMMEND_DEFAULT = False


def class_text(entry) -> str:
    """One skill's training text: slug words, name words, description, triggers."""
    words = [entry.slug.replace("-", " ")]
    if entry.name and entry.name != entry.slug:
        words.append(entry.name.replace("-", " "))
    words.append(entry.description or "")
    words.extend(t for t in (entry.triggers or ()) if isinstance(t, str))
    return " ".join(words)


def normalised_margin(scores: Sequence[float]) -> float:
    """(s1 - s2) / (s1 - s_min) over scores sorted high to low; 0.0 when flat."""
    if len(scores) < 2:
        return 1.0 if scores else 0.0
    spread = scores[0] - scores[-1]
    if spread <= 0:
        return 0.0
    return max(0.0, min(1.0, (scores[0] - scores[1]) / spread))


class MeaningFloor:
    """CF-IOF ranking of the skill catalogue, with an optional FAC re-rank."""

    def __init__(self, entries: Sequence, *, uncertain_below: float = DEFAULT_UNCERTAIN_BELOW,
                 top_k: int = DEFAULT_TOP_K, recommend: bool = RECOMMEND_DEFAULT,
                 structures: Optional[Mapping] = None,
                 prompt_structure: Optional[Callable[[str, str], object]] = None,
                 fac_alpha: float = 0.6) -> None:
        self._entries = {(e.kind, e.slug): e for e in entries}
        if len(self._entries) < 2:
            raise ValueError("the meaning floor needs at least two catalogue entries")
        labels = list(self._entries)
        self._engine = SemanticKeywordFloor().fit([class_text(self._entries[k]) for k in labels],
                                                  labels)
        self.uncertain_below = float(uncertain_below)
        self.top_k = int(top_k)
        self.recommend = bool(recommend)
        self._structures = dict(structures or {})
        self._prompt_structure = prompt_structure
        self._fac_alpha = float(fac_alpha)

    def evidence(self, verb: str, args: str) -> Evidence:
        """Ranked candidates, a margin and an uncertainty flag. Never a verdict."""
        query = f"{verb.replace('-', ' ')} {args}".strip()
        verdict = self._engine.classify(query)
        if verdict.active == 0:
            return Evidence(gaps=("no-known-words",))
        ranked = sorted(verdict.scores.items(), key=lambda kv: (-kv[1], repr(kv[0])))
        ranked = self._fac(verb, args, ranked)
        margin = normalised_margin([s for _, s in ranked])
        uncertain = margin < self.uncertain_below
        gaps = (f"near-tie:{ranked[0][0][1]}~{ranked[1][0][1]}",) if uncertain else ()
        if not self.recommend:
            uncertain, gaps = True, gaps + ("floor-not-calibrated",)
        return Evidence(self._candidates(ranked), margin, uncertain, gaps)

    def _candidates(self, ranked: list) -> tuple:
        out = []
        for (kind, slug), score in ranked[: self.top_k]:
            entry = self._entries[(kind, slug)]
            summary = " ".join((entry.description or "").split())[:160]
            out.append(Candidate(kind, slug, float(score), summary))
        return tuple(out)

    def _fac(self, verb: str, args: str, ranked: list) -> list:
        """Structural re-rank of the top window, only when structures exist."""
        query = self._prompt_structure(verb, args) if self._prompt_structure else None
        window = ranked[:DEFAULT_FAC_WINDOW]
        if query is None or not any(k in self._structures for k, _ in window):
            return ranked
        return _fac_window(query, window, self._structures, self._fac_alpha) + ranked[len(window):]


def _fac_window(query, window: list, structures: Mapping, alpha: float) -> list:
    """Fuse the normalised MAC score with SME alignment (``fac_rerank``)."""
    from lib.smt.macfac import fac_rerank  # noqa: PLC0415 -- only when structures exist
    from lib.smt.sme import Structure  # noqa: PLC0415

    top, low = window[0][1], window[-1][1]
    spread = (top - low) or 1.0
    empty = Structure("none", (), ())
    rows = [(repr(k), (s - low) / spread, structures.get(k, empty)) for k, s in window]
    order = {repr(k): (k, s) for k, s in window}
    fused = fac_rerank(query, rows, top_n=len(rows), alpha=alpha)
    return [(order[r.item_id][0], low + r.fused_score * spread) for r in fused]
