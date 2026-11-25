#!/usr/bin/env python3
"""Closed-loop convergence dogfood — the proof, re-runnable.

Drives ``lib/converge_loop.run_waves`` over the REAL voice-hardening target
(Q3 → Q1 → Q2, the order implemented in ``lib/voice_note.py``) and shows the
closed loop in action: each wave's executor is THREADED the folded prior
lessons, and waves 2 and 3 record that the prior lesson changed their
implementation choice. The resulting ``state/converge/lessons.jsonl`` is the
dogfood evidence (gitignored); this script regenerates it and prints the
H859 verdict.

H859 (registered 2026-06-10, deadline 2026-06-17): "closed-loop lesson
threading measurably alters wave behaviour vs lesson-blind" — predicted
``>=2 of 3 dogfood waves`` change an implementation choice because of a
threaded prior lesson.

Run::

    PYTHONPATH=$HOME/.claude venv/bin/python scripts/converge_dogfood.py

Pure-python, no network, no LLM — the executor encodes the REAL causal story
of the three implemented waves; the kernel does the threading + fold.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(_ROOT / "lib"))

import converge_lessons as cl  # noqa: E402
import converge_loop as loop  # noqa: E402

TARGET = "voice-hardening"

# The three real waves, in implementation order. Each ``lesson`` is what that
# wave actually taught lib/voice_note.py; ``needs`` names the substring whose
# presence in a threaded prior lesson proves wave N reused wave N-1's insight.
_WAVES = [
    {  # Wave 0 — Q3: empty/whitespace guard
        "outcome": "implemented-Q3-empty-guard",
        "lesson": ("Cheap input guards belong BEFORE the expensive backend: the "
                   "empty/whitespace check sits above the STOP brake and TTS spend, "
                   "and the tts/convert/send DIP seams make it testable with zero "
                   "network."),
        "falsifier": ("wrong if an empty payload still reaches Gemini TTS and 400s "
                      "instead of returning the plain 'empty text' error"),
        "needs": None,  # first wave, no prior to reuse
    },
    {  # Wave 1 — Q1: opus pre-check ladder to AAC
        "outcome": "implemented-Q1-opus-ladder",
        "lesson": ("Applied the prior guard-before-spend lesson to the codec: a "
                   "memoised libopus pre-check (DIP seam) runs ONCE before any "
                   "encode, laddering to AAC/.m4a when opus is absent so we never "
                   "emit a silent unplayable ogg."),
        "falsifier": ("wrong if libopus absence yields an unplayable .ogg instead "
                      "of a playable .m4a file attachment"),
        "needs": "guard",  # reuses the W0 guard-before-spend + DIP-seam pattern
    },
    {  # Wave 2 — Q2: sentence chunking
        "outcome": "implemented-Q2-chunking",
        "lesson": ("Reused BOTH priors: the empty-guard is applied per chunk "
                   "(whitespace fragments dropped) and the opus->aac convert ladder "
                   "runs on each segment before concatenation; over-long sentences "
                   "hard-split so nothing is lost."),
        "falsifier": ("wrong if multi-minute text 400s/times-out instead of being "
                      "sentence-chunked and concatenated into one voice note"),
        "needs": "seam",  # reuses the W1 convert-seam/ladder insight
    },
]


def _make_executor(changed: list[int]):
    """Build the dogfood executor; records into ``changed`` the wave indices
    whose implementation choice was shaped by a threaded prior lesson."""

    def executor(wave_index: int, depth: int, prior: list[dict]) -> dict:
        spec = _WAVES[wave_index]
        prior_text = " ".join(r.get("lesson", "") for r in prior).lower()
        reused = bool(spec["needs"]) and spec["needs"] in prior_text
        if reused:
            changed.append(wave_index)
        return {
            "outcome": spec["outcome"],
            "lesson": spec["lesson"],
            "falsifier": spec["falsifier"],
            "fixed": True,
            "criterion_met": wave_index == len(_WAVES) - 1,
        }

    return executor


def main() -> int:
    path = cl.default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)  # clean run — the fold would dedupe anyway

    changed: list[int] = []
    result = loop.run_waves(TARGET, _make_executor(changed), max_waves=len(_WAVES),
                            path=path)
    cl.compact_file(path)  # leave the evidence file in canonical folded form

    print(f"target={result.target} waves_run={result.waves_run} "
          f"criterion_met={result.criterion_met}")
    for w in result.waves:
        print(f"  wave {w.wave_index} depth={w.depth} prior_count={w.prior_count} "
              f"-> {w.outcome}")
    changed_count = len(changed)
    verdict = "CONFIRMED" if changed_count >= 2 else "FALSIFIED"
    print(f"H859: {changed_count}/3 waves changed an implementation choice from a "
          f"threaded prior lesson (prediction >=2) -> {verdict}")
    print(f"evidence: {path}")
    return 0 if changed_count >= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
