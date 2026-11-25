#!/usr/bin/env python3
"""scripts/ste-baseline.py -- measure how close GRIP's own turns are to ASD-STE100.

Reads the local Claude Code transcripts of GRIP checkouts, takes the most recent
substantive assistant turns, scores each one with lib/ste_lint.py, and prints the
distribution. It prints NUMBERS ONLY: no turn text ever reaches stdout, a file, or
the network. That is the property that lets it run over real sessions.

Why it exists. The plan to make STE the canonical voice (plans/implement-asd-ste100-
as-grip-s-wobbly-cook.md, T1) needs a number B to measure H-STE-2 against. A target
with no baseline is a decree. The first B (0.706, 2026-10-05 04:51 UTC) was taken
under scorer version 1 and by file time, and the scorer changed later that day. So
this script now records the scorer version it ran under and picks turns by the
ENTRY time inside each transcript, which lets a window be re-scored under a newer
scorer (plans/ULTRAPLAN-2026-10-10-ste-improver-loop.md, stage S1).

One instrument. A turn's row is lib/ste_lint.py's own score(): the same sentence
rules, the same formula and the same scorer version as the Stop hook's log row
(lib/ste_hook.py), so the two never disagree on what a pass rate is. The hook
scores a turn's final message only; this script scores every assistant entry of
MIN_WORDS prose words or more, and its output says so.

Which transcripts. Only project directories whose slug encodes a GRIP checkout:
~/.grip, ~/.claude and ~/.grip-worktrees/*. Sessions run in a client or personal
folder have different slugs and are never opened. The slug rule is Claude Code's,
and lib/path_encoding.py holds it: a slash of either kind, ":", "." and a space each
become "-". On Windows the names are compared without regard to letter case.

Which turns. An assistant entry whose text blocks hold at least MIN_WORDS words of
prose after fenced code is set aside. Shorter turns are acknowledgements and tool
chatter; they would flatter the number. Entries are ordered by their own
``timestamp`` field; an entry without one takes its file's modification time.
--since and --until bound that time: a date (its first second, or its last second
for --until) or a full ISO time such as 2026-10-05T04:50:00Z.

Usage:
    bin/grip-py scripts/ste-baseline.py                 # last 200 substantive turns
    bin/grip-py scripts/ste-baseline.py --last 500 --json
    bin/grip-py scripts/ste-baseline.py --since 2026-10-01 --until 2026-10-05T04:50:00Z
"""
from __future__ import annotations

import argparse
import heapq
import json
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from itertools import chain
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(_ROOT / "lib"))

import ste_lint  # noqa: E402
from path_encoding import encode_project_path  # noqa: E402

MIN_WORDS = 60
#: How the rows were chosen; printed so a reader can tell this instrument from the
#: file-time selection the first baseline used.
SELECTION = "entry-time"
POPULATION = f"every assistant entry of at least {MIN_WORDS} prose words"


def _slug(path: Path) -> str:
    """Claude Code's name for a project directory, in the form this system compares names.

    The same rule as lib/ste_hook.py's _slug, which tests/test_ste_lint_hook.py holds to
    this one. On Windows one folder is reached under "C:" and under "c:", so the name is
    case-folded there. os.path.normcase changes nothing on POSIX.
    """
    return os.path.normcase(encode_project_path(path))


def grip_project_dirs(projects_root: Path, home: Path) -> list[Path]:
    """Project directories that encode a GRIP checkout, and nothing else."""
    exact = {_slug(home / ".grip"), _slug(home / ".claude")}
    worktree_prefix = _slug(home / ".grip-worktrees") + "-"
    if not projects_root.is_dir():
        return []
    named = ((d, os.path.normcase(d.name)) for d in projects_root.iterdir() if d.is_dir())
    return [d for d, name in named if name in exact or name.startswith(worktree_prefix)]


def _text_of(entry: dict) -> str:
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    return "\n\n".join(str(b.get("text") or "") for b in content or []
                       if isinstance(b, dict) and b.get("type") == "text")


def _prose_words(text: str) -> int:
    return sum(len(block.split()) for _, block in ste_lint.blocks_of(text))


def _entry(line: str) -> dict:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {}


def parse_time(value: str | None, *, end: bool = False) -> float | None:
    """Seconds since the epoch for a date or an ISO time, or None for no value.

    A bare date is its first instant, or its last microsecond with ``end=True``, so
    ``--until 2026-10-05`` keeps the whole of that day, fractional-second entries
    included (a Codex finding on GRIP#7920). A time without a zone is UTC.
    """
    if not value:
        return None
    text = value.strip()
    if len(text) == 10:
        day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (day + timedelta(days=1)).timestamp() - 1e-6 if end else day.timestamp()
    stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def _entry_time(entry: dict, fallback: float) -> float:
    """The entry's own timestamp, or the file's modification time without one."""
    try:
        return parse_time(entry.get("timestamp")) or fallback
    except (TypeError, ValueError):
        return fallback


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _assistant_entries(path: Path) -> list[tuple[float, str]]:
    """(entry time, text) for each assistant entry of one transcript, in file order;
    unreadable input is empty."""
    mtime = _mtime(path)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    entries = (_entry(line) for line in lines)
    stamped = ((_entry_time(e, mtime or 0.0), _text_of(e)) for e in entries if e.get("type") == "assistant")
    return [(ts, text) for ts, text in stamped if text]


def _in_window(ts: float, since_ts: float | None, until_ts: float | None) -> bool:
    return (since_ts is None or ts >= since_ts) and (until_ts is None or ts <= until_ts)


def _entries_in_window(path: Path, since_ts: float | None, until_ts: float | None) -> list[tuple[float, str]]:
    """The substantive entries of one transcript inside the window, later entries
    first, so two entries with the same time keep the newest-first order."""
    return [(ts, text) for ts, text in reversed(_assistant_entries(path))
            if _in_window(ts, since_ts, until_ts) and _prose_words(text) >= MIN_WORDS]


def _keep_newest(heap: list[float], stamps: list[float], last: int) -> None:
    """Push each stamp and keep only the newest ``last``, so heap[0] is the oldest of them."""
    for ts in stamps:
        heapq.heappush(heap, ts)
        if len(heap) > last:
            heapq.heappop(heap)


def score_turn(text: str) -> dict:
    """Numbers for one turn, from lib/ste_lint.py's own score(): the hook's instrument."""
    result = ste_lint.score(text)
    errors = sum(1 for f in result.findings if f.severity == "error")
    return {
        "sentences": result.sentences,
        "instructions": result.instructions,
        "words": _prose_words(text),
        "sentence_errors": result.structural_errors,   # sentences that fail a sentence rule
        "errors": errors,
        "warnings": len(result.findings) - errors,
        "per_rule": {rule: count for rule, count in result.per_rule.items() if isinstance(count, int)},
        "pass_rate": round(result.structural_pass_rate, 4),
        "scorer_version": result.scorer_version,
    }


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    return vals[min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))]


def summarise(rows: list[dict]) -> dict:
    rates = [r["pass_rate"] for r in rows]
    per_rule: Counter = Counter()
    for r in rows:
        per_rule.update(r["per_rule"])
    sentences = sum(r["sentences"] for r in rows)
    words = sum(r["words"] for r in rows)
    failing = sum(r["sentence_errors"] for r in rows)
    return {
        "turns": len(rows),
        "sentences": sentences,
        "words": words,
        "words_per_sentence": round(words / sentences, 2) if sentences else 0.0,
        "pass_rate_median": round(statistics.median(rates), 4) if rates else 0.0,
        "pass_rate_p10": round(_quantile(rates, 0.10), 4),
        "pass_rate_p90": round(_quantile(rates, 0.90), 4),
        # every sentence weighs the same: a two-sentence turn no longer counts as much as a forty-sentence one
        "pass_rate_pooled": round(1.0 - failing / sentences, 4) if sentences else 1.0,
        "turns_fully_passing": sum(1 for x in rates if x >= 0.999),
        "turns_with_any_error": sum(1 for r in rows if r["errors"]),
        "per_rule": dict(sorted(per_rule.items())),
        "rules_in_rate": list(ste_lint.SENTENCE_ERROR_RULES),
        "scorer_version": ste_lint.SCORER_VERSION,
        "selection": SELECTION,
        "population": POPULATION,
        "min_words": MIN_WORDS,
    }


def collect_rows(transcripts: list[Path], last: int, since_ts: float | None = None,
                 until_ts: float | None = None) -> tuple[list[dict], dict]:
    """Score the newest ``last`` substantive turns by entry time; return rows and the window.

    ``transcripts`` come newest-modified first. A file cannot hold an entry newer than
    its own modification time, so the walk stops once ``last`` candidates are in hand
    and the next file is older than the oldest of the newest ``last``; it also stops at
    the first file older than ``since_ts``.
    """
    candidates: list[tuple[float, str]] = []
    newest: list[float] = []
    for path in transcripts:
        mtime = _mtime(path)
        if mtime is None:
            continue
        if (since_ts is not None and mtime < since_ts) or (len(newest) >= last and mtime < newest[0]):
            break
        found = _entries_in_window(path, since_ts, until_ts)
        candidates.extend(found)
        _keep_newest(newest, [ts for ts, _ in found], last)
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    chosen = candidates[:last]
    return [score_turn(text) for _, text in chosen], _window([ts for ts, _ in chosen])


def _window(stamps: list[float]) -> dict:
    def iso(t: float) -> str:
        return datetime.fromtimestamp(t, timezone.utc).isoformat()
    return {"newest": iso(max(stamps)) if stamps else None,
            "oldest": iso(min(stamps)) if stamps else None}


def _print_text(s: dict) -> None:
    print(f"STE baseline over {s['turns']} substantive turns "
          f"({s['project_dirs']} GRIP project dirs, {s['transcripts_seen']} transcripts)")
    print(f"  scorer version {s['scorer_version']}; rules in the rate: {', '.join(s['rules_in_rate'])}")
    print(f"  selection: {s['selection']}; population: {s['population']}")
    print(f"  window: {s['window']['oldest']} .. {s['window']['newest']}")
    print(f"  sentences: {s['sentences']}  words: {s['words']}  words/sentence: {s['words_per_sentence']}")
    print(f"  pass rate (sentence-level): median {s['pass_rate_median']}  "
          f"p10 {s['pass_rate_p10']}  p90 {s['pass_rate_p90']}  pooled {s['pass_rate_pooled']}")
    print(f"  turns fully passing: {s['turns_fully_passing']}  turns with any error: {s['turns_with_any_error']}")
    for rule, n in s["per_rule"].items():
        print(f"  {rule:<20} {n}")


def _parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--last", type=int, default=200, help="substantive turns to score (newest first)")
    ap.add_argument("--since", help="only entries from this time on (YYYY-MM-DD, or an ISO time such as "
                                    "2026-10-01T00:00:00Z)")
    ap.add_argument("--until", help="only entries up to this time (a date keeps its whole day)")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    ap.add_argument("--projects-root", default=os.environ.get("CLAUDE_PROJECTS_ROOT")
                    or str(Path.home() / ".claude" / "projects"))
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    dirs = grip_project_dirs(Path(args.projects_root), Path.home())
    transcripts = sorted(chain.from_iterable(d.glob("*.jsonl") for d in dirs),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    rows, window = collect_rows(transcripts, args.last, parse_time(args.since), parse_time(args.until, end=True))
    summary = summarise(rows)
    summary.update({"project_dirs": len(dirs), "transcripts_seen": len(transcripts),
                    "window": window, "since": args.since, "until": args.until,
                    "measured_at": datetime.now(timezone.utc).isoformat()})
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        _print_text(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
