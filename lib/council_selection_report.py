"""Council selection leaderboard — who does the judge actually choose?

Reads the sealed sovereign-council corpus (``state/sovereign-council/*.json``)
and answers, with receipts, the question behind the 2026-09-24 LinkedIn post:
which model is most often chosen as the best answer when it actually sits?

Method (pinned by tests/test_council_selection_report.py):

- Only MULTI-speaker sittings count — a single-speaker sitting says nothing
  about selection.
- A speaker "sat" iff it answered ok in that sitting; errored seats never sat.
- A win = the judge's ``best`` naming a model that sat — exact lowercase match
  first (dict lookup), substring either way as the fallback, because judges
  write ``astra``, ``gpt-6-astra`` or ``codex / gpt-6-astra`` interchangeably.
- Rates are conditioned on sitting, with Wilson 95% intervals, because raw win
  counts reward showing up often rather than being chosen.
- The report names its window, its N, and the judge split — the single-judge
  confound stays visible instead of laundered ("most chosen" on today's corpus
  means "most chosen BY Gemini 3.1 Pro", and the report says so).

First live run (2026-09-24, 402 records): 389 multi-speaker sittings,
gpt-6-astra 42/64 = 65.6% [53.4, 76.1] — highest rate on the panel;
grok-4.5 136/226 = 60.2% [53.7, 66.3] — most total wins; the judge's own
vendor's speaker chosen 3.8% of the time.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from claude_dir import get_claude_dir  # noqa: E402


def default_corpus_dir() -> pathlib.Path:
    """The live sealed-record store, resolved at call time (worktree-safe)."""
    return get_claude_dir() / "state" / "sovereign-council"


def wilson95(wins: int, sat: int) -> tuple[float, float]:
    """Wilson 95% interval for wins/sat, in percent."""
    z = 1.96
    p = wins / sat
    denom = 1 + z * z / sat
    centre = (p + z * z / (2 * sat)) / denom
    half = z * math.sqrt(p * (1 - p) / sat + z * z / (4 * sat * sat)) / denom
    return (centre - half) * 100, (centre + half) * 100


def _sat_models(record: dict) -> dict:
    """lowercased-model -> original-model for speakers that answered ok."""
    out = {}
    for s in record.get("speakers") or []:
        if s.get("ok", True):
            m = str(s.get("model", "?"))
            out[m.lower()] = m
    return out


def _winner(best: str, sat: dict) -> "str | None":
    """Exact dict hit first; substring scan only when the judge paraphrased."""
    b = best.lower()
    hit = sat.get(b)
    if hit:
        return hit
    for ml, orig in sat.items():
        if b in ml or ml in b:
            return orig
    return None


class _Tally:
    """Mutable accumulation across the corpus scan."""

    def __init__(self) -> None:
        self.wins: dict = {}
        self.sat: dict = {}
        self.judges: dict = {}
        self.sittings = 0
        self.first_ts: "str | None" = None
        self.last_ts: "str | None" = None

    def add(self, record: dict) -> None:
        best = str((record.get("judge") or {}).get("best") or "").strip()
        sat = _sat_models(record)
        if not best or len(sat) < 2:
            return
        self.sittings += 1
        ts = record.get("sealed_at", "")
        if ts:
            self.first_ts = min(self.first_ts or ts, ts)
            self.last_ts = max(self.last_ts or ts, ts)
        jm = str((record.get("judge") or {}).get("model") or "?")
        self.judges[jm] = self.judges.get(jm, 0) + 1
        for m in sat.values():
            self.sat[m] = self.sat.get(m, 0) + 1
        w = _winner(best, sat)
        if w:
            self.wins[w] = self.wins.get(w, 0) + 1


def _row(model: str, wins: int, sat: int) -> dict:
    lo, hi = wilson95(wins, sat) if wins else (0.0, wilson95(1, sat)[1])
    return {"model": model, "wins": wins, "sat": sat,
            "rate_pct": round(wins / sat * 100, 1),
            "wilson95": [round(lo, 1), round(hi, 1)]}


def selection_report(corpus_dir: "pathlib.Path | None" = None) -> dict:
    """Tally the corpus into {sittings, window, judges, rows}."""
    corpus = pathlib.Path(corpus_dir) if corpus_dir else default_corpus_dir()
    tally = _Tally()
    for f in sorted(corpus.glob("*.json")):
        try:
            tally.add(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue  # a torn or foreign file is skipped, never fatal
    rows = [_row(m, tally.wins.get(m, 0), n) for m, n in tally.sat.items()]
    rows.sort(key=lambda r: -r["rate_pct"])
    return {"sittings": tally.sittings,
            "window": (tally.first_ts, tally.last_ts),
            "judges": tally.judges, "rows": rows}


def render_markdown(rep: dict) -> str:
    """The leaderboard as prose-honest markdown."""
    lines = [
        "# Council selection leaderboard",
        "",
        f"{rep['sittings']} multi-speaker judged sittings, "
        f"window {rep['window'][0]} -> {rep['window'][1]}.",
        "",
        "Rates are conditioned on sitting (chosen best | sat and answered), "
        "with Wilson 95% intervals — raw win counts reward showing up, not "
        "being chosen.",
        "",
        "| model | wins | sat | rate | Wilson 95% |",
        "|---|---:|---:|---:|---|",
    ]
    for r in rep["rows"]:
        lines.append(f"| {r['model']} | {r['wins']} | {r['sat']} | "
                     f"{r['rate_pct']}% | [{r['wilson95'][0]}%, {r['wilson95'][1]}%] |")
    judges = ", ".join(f"{k}: {v}" for k, v in
                       sorted(rep["judges"].items(), key=lambda kv: -kv[1]))
    lines += ["",
              f"Judge split (the confound, named): {judges}. A leaderboard "
              "judged by one model family measures that family's preferences; "
              "read it as such."]
    return "\n".join(lines)


def main(argv: "list | None" = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    corpus = pathlib.Path(args[0]) if args else None
    rep = selection_report(corpus)
    if not rep["sittings"]:
        print("no multi-speaker judged sittings found", file=sys.stderr)
        return 1
    print(render_markdown(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
