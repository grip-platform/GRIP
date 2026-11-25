#!/usr/bin/env python3
"""tick_cost -- what a recurring prompt costs inside a Claude Code session.

A "tick" is every turn a session starts from the same prompt: a ``/loop`` prompt,
a cron prompt, a ``/goal`` continuation. Each one replays the session's whole
context, so the price of a tick is mostly the cached input it reads, not the
output it writes. This module measures that from the transcript, which is the
only place the numbers exist.

Every assistant record belongs to the prompt its ``parentUuid`` chain reaches,
unless that chain first passes through a record that ended a turn
(``stop_reason == "end_turn"``). A tick is therefore the segment of the chain
from the tick prompt to the first record that ends the turn. File order plays
no part: a cron prompt the harness queues while a long turn is still writing
lands in the transcript before that turn's remaining records, and those
records chain to the older prompt, not to the tick (a file-order scan charged
one "tick" 152,223 output tokens that way, measured 2026-10-28). Records after
the tick's end_turn, such as a Stop-hook continuation, chain through it and
are not the tick's either.

One API request that answers with several content blocks (thinking, text, a
tool call) is written as several rows with the same ``requestId``, each
repeating the request's usage. Usage is counted once per request, on its first
row, the way ``lib/orchestrator_budget.py`` counts it; ``messages`` therefore
counts requests, not rows (measured 2026-10-28: 747 rows for 341 requests in
one session, and the all-rows sum of cache reads was about twice the truth).
Rows whose ``message.model`` is ``<synthetic>`` never reached a model and stay
out of every total, the contract ``lib/model_attribution.py`` keeps. A tick
with no end_turn record yet (the transcript was read mid-turn, or the session
stopped) is counted as unfinished and kept out of the statistics. Dollar
figures are given only when one named model carries all the usage, because one
rate vector cannot price two models, and a row with no model name cannot be
shown to belong to the one that is named.

A turn that dispatches a subagent spends tokens the root transcript never
records: they live in ``<session>/subagents/agent-*.jsonl`` beside it. Those
files are discovered and their usage joins the session totals, so the ticks'
share is of the whole session. No field in a child record names the turn that
launched it (every record carries only the parent's ``sessionId``), so child
usage is reported on its own line and charged to no tick; a tick that used
agents is understated by that amount, and the report says so.

CLI::

    grip-py lib/tick_cost.py <transcript.jsonl> --prompt "<the recurring prompt>" [--json]
        [--no-subagents] [--price-cache-read USD_PER_M] [--price-input USD_PER_M]
        [--price-cache-write USD_PER_M] [--price-output USD_PER_M]

Prices are optional and come from the caller; the module ships none, because a
price typed from memory is a claim and the vendor's page is the source.

Exit status: 0 when at least one finished tick was found, 1 when none was (not
an error: the prompt never ran to the end of a turn in that transcript), 2 when
the transcript file is missing.

Two clocks per tick: ``seconds`` runs from the prompt's write time to the end of
the turn, which includes any wait behind other work, and
``seconds_to_first_reply`` runs from the prompt's write time to the first reply
record, which bounds that wait from above.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional

USAGE_KEYS = ("cache_read_input_tokens", "input_tokens", "cache_creation_input_tokens", "output_tokens")
SHORT = {"cache_read_input_tokens": "cache_read", "input_tokens": "input",
         "cache_creation_input_tokens": "cache_write", "output_tokens": "output"}
SYNTHETIC_MODEL = "<synthetic>"
UNKNOWN_MODEL = "unknown"  # a usage row with no message.model; its identity cannot be established


@dataclass
class Tick:
    started: str
    ended: str = ""
    first_reply: str = ""
    finished: bool = False
    messages: int = 0
    cache_read: int = 0
    input: int = 0
    cache_write: int = 0
    output: int = 0

    def add(self, usage: dict) -> None:
        self.messages += 1
        for key in USAGE_KEYS:
            setattr(self, SHORT[key], getattr(self, SHORT[key]) + int(usage.get(key) or 0))

    @property
    def seconds(self) -> Optional[float]:
        """Prompt write time to the end of the turn, waits included."""
        return _elapsed(self.started, self.ended)

    @property
    def seconds_to_first_reply(self) -> Optional[float]:
        """Prompt write time to the first reply record: an upper bound on the wait behind other work."""
        return _elapsed(self.started, self.first_reply)


def _elapsed(start: str, end: str) -> Optional[float]:
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return (b - a).total_seconds()
    except (ValueError, AttributeError):
        return None


@dataclass
class Totals:
    messages: int = 0
    cache_read: int = 0
    input: int = 0
    cache_write: int = 0
    output: int = 0

    def add(self, usage: dict) -> None:
        self.messages += 1
        for key in USAGE_KEYS:
            setattr(self, SHORT[key], getattr(self, SHORT[key]) + int(usage.get(key) or 0))


@dataclass
class Prices:
    """USD per million tokens, each optional; a missing price yields no dollar figure."""
    cache_read: Optional[float] = None
    input: Optional[float] = None
    cache_write: Optional[float] = None
    output: Optional[float] = None

    def usd(self, t) -> Optional[float]:
        parts = ((self.cache_read, t.cache_read), (self.input, t.input),
                 (self.cache_write, t.cache_write), (self.output, t.output))
        if any(p is None for p, _ in parts):
            return None
        return sum(p * n / 1_000_000 for p, n in parts)


@dataclass
class Report:
    prompt: str
    ticks: List[Tick]
    totals: Totals
    duplicates_dropped: int = 0
    prices: Prices = field(default_factory=Prices)
    unfinished: int = 0
    models: Dict[str, int] = field(default_factory=dict)
    subagents: Totals = field(default_factory=Totals)
    subagent_transcripts: int = 0

    def column(self, name: str) -> List[int]:
        return [getattr(t, name) for t in self.ticks]

    @property
    def priceable(self) -> bool:
        """True when one named model carries every token, so one rate vector can price them."""
        return len(self.models) == 1 and UNKNOWN_MODEL not in self.models

    def usd(self, t) -> Optional[float]:
        return self.prices.usd(t) if self.priceable else None

    def to_dict(self) -> dict:
        return {"prompt": self.prompt, "ticks": len(self.ticks), "unfinished_ticks": self.unfinished,
                "duplicates_dropped": self.duplicates_dropped, "models": dict(self.models),
                "subagent_transcripts": self.subagent_transcripts, "subagent_totals": asdict(self.subagents),
                "per_tick": {n: _stats(self.column(n)) for n in ("cache_read", "input", "cache_write", "output", "messages")},
                "seconds_median": _median([t.seconds for t in self.ticks]),
                "seconds_to_first_reply_median": _median([t.seconds_to_first_reply for t in self.ticks]),
                "session_totals": asdict(self.totals),
                "ticks_share": {n: _share(sum(self.column(n)), getattr(self.totals, n))
                                for n in ("cache_read", "input", "cache_write", "output")},
                "usd_per_tick_median": _median([self.usd(t) for t in self.ticks]),
                "usd_ticks_total": _total([self.usd(t) for t in self.ticks]),
                "usd_session_total": self.usd(self.totals)}


def _median(xs: List) -> Optional[float]:
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _stats(xs: List[int]) -> dict:
    if not xs:
        return {"mean": None, "median": None, "min": None, "max": None, "sum": 0}
    return {"mean": statistics.mean(xs), "median": statistics.median(xs), "min": min(xs), "max": max(xs), "sum": sum(xs)}


def _share(part: int, whole: int) -> Optional[float]:
    return None if not whole else round(100.0 * part / whole, 1)


def _total(vals: List[Optional[float]]) -> Optional[float]:
    return None if not vals or any(v is None for v in vals) else sum(vals)


def records(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("{"):
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def subagent_transcripts(path: Path) -> List[Path]:
    """The session's subagent transcripts beside the root one: <stem>/subagents/*.jsonl, sorted."""
    return sorted((path.parent / path.stem / "subagents").glob("*.jsonl"))


def prompt_text(record: dict) -> Optional[str]:
    """The operator-facing text of a user record, or None for a tool result."""
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return None
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def _usage(record: dict) -> dict:
    return (record.get("message") or {}).get("usage") or {}


def _ends_turn(record: dict) -> bool:
    return (record.get("message") or {}).get("stop_reason") == "end_turn"


def _model(record: dict) -> Optional[str]:
    return (record.get("message") or {}).get("model")


def _kind(record: dict) -> str:
    """prompt: a user record with text. ended: an assistant record that ended its turn. through: anything else."""
    if record.get("type") == "user" and prompt_text(record) is not None:
        return "prompt"
    if record.get("type") == "assistant" and _ends_turn(record):
        return "ended"
    return "through"


class _Chain:
    """Roots a record at the prompt its parent chain reaches, or at nothing once the chain crosses an ended turn."""

    def __init__(self, recs: List[dict]):
        self.by_uuid = {r["uuid"]: r for r in recs if r.get("uuid")}
        self._root: Dict[str, Optional[str]] = {}

    def root(self, uuid: Optional[str]) -> Optional[str]:
        path: List[str] = []
        result: Optional[str] = None
        while uuid is not None:
            if uuid in self._root:
                result = self._root[uuid]
                break
            rec = self.by_uuid.get(uuid)
            if rec is None:
                break
            path.append(uuid)
            kind = _kind(rec)
            if kind == "prompt":
                result = uuid
                break
            if kind == "ended":
                break
            uuid = rec.get("parentUuid")
        for u in path:
            self._root[u] = result
        return result


def _tick_prompts(recs: List[dict], prompt: str) -> "tuple[Dict[str, Tick], int]":
    """Tick prompts in file order, keyed by uuid; a prompt record written twice counts once."""
    ticks: Dict[str, Tick] = {}
    dropped = 0
    for rec in recs:
        if rec.get("type") != "user" or (prompt_text(rec) or "").strip() != prompt.strip():
            continue
        uid = rec.get("uuid")
        if uid in ticks:
            dropped += 1
            continue
        ticks[uid] = Tick(started=rec.get("timestamp") or "")
    return ticks, dropped


def _charge(tick: Optional[Tick], rec: dict, usage: Optional[dict]) -> None:
    if tick is None:
        return
    if usage is not None:
        tick.add(usage)
    tick.ended = rec.get("timestamp") or tick.ended
    tick.first_reply = tick.first_reply or rec.get("timestamp") or ""
    if _ends_turn(rec):
        tick.finished = True


def _usage_rows(recs: Iterable[dict], seen: set) -> Iterator["tuple[dict, Optional[dict]]"]:
    """Each assistant row that reached a model, with its usage on the first row of its request and None after."""
    for rec in recs:
        if rec.get("type") != "assistant" or _model(rec) == SYNTHETIC_MODEL:
            continue
        req = rec.get("requestId")
        if req in seen:
            yield rec, None
            continue
        if req:
            seen.add(req)
        yield rec, _usage(rec)


def _tally(rec: dict, usage: Optional[dict], totals: Totals, models: Counter) -> None:
    """Count a request's usage once, in the session totals and the model tally."""
    if usage is None:
        return
    totals.add(usage)
    models[_model(rec) or UNKNOWN_MODEL] += sum(int(usage.get(k) or 0) for k in USAGE_KEYS)


def _add_children(subagents: "Iterable[Iterable[dict]]", totals: Totals, models: Counter, seen: set) -> "tuple[Totals, int]":
    """Add every child transcript's usage to the session totals and the model tally; return the children's own totals and count."""
    children, count = Totals(), 0
    for stream in subagents:
        count += 1
        for rec, usage in _usage_rows(stream, seen):
            _tally(rec, usage, totals, models)
            if usage is not None:
                children.add(usage)
    return children, count


def measure(recs: Iterable[dict], prompt: str, prices: Optional[Prices] = None,
            subagents: "Iterable[Iterable[dict]]" = ()) -> Report:
    """Attribute every assistant record to the tick its parent chain reaches, or to the rest.

    ``subagents`` holds one record stream per child transcript; their usage joins
    the session totals and the per-model tally, and is charged to no tick. Usage
    is counted once per ``requestId`` across every stream.
    """
    recs = list(recs)
    chain = _Chain(recs)
    ticks, dropped = _tick_prompts(recs, prompt)
    totals, models, seen = Totals(), Counter(), set()
    for rec, usage in _usage_rows(recs, seen):
        _tally(rec, usage, totals, models)
        _charge(ticks.get(chain.root(rec.get("parentUuid"))), rec, usage)
    children, child_count = _add_children(subagents, totals, models, seen)
    finished = [t for t in ticks.values() if t.finished]
    return Report(prompt, finished, totals, dropped, prices or Prices(),
                  unfinished=len(ticks) - len(finished), models={m: n for m, n in models.items() if n},
                  subagents=children, subagent_transcripts=child_count)


def _usd_line(report: Report, d: dict) -> str:
    if not report.priceable:
        if not report.models:
            return "USD: not priced, no model carries usage"
        if UNKNOWN_MODEL in report.models:
            return "USD: not priced, usage rows without a model name cannot be shown to belong to one model"
        names = ", ".join(sorted(report.models))
        return f"USD: not priced, {len(report.models)} models carry usage ({names}) and one rate vector cannot price them"
    if d["usd_per_tick_median"] is None:
        return "USD: no prices given (pass --price-* in USD per million tokens from the vendor's page)"
    return (f"USD per tick (median) {d['usd_per_tick_median']:.4f}; all ticks {d['usd_ticks_total']:.2f}; "
            f"whole session {d['usd_session_total']:.2f}")


def render(report: Report) -> str:
    """The measurement as text a reader can act on."""
    d = report.to_dict()
    counts = f"(unfinished: {d['unfinished_ticks']}, duplicate prompt records dropped: {d['duplicates_dropped']})"
    if not report.ticks:
        return f"ticks: 0 finished for prompt {report.prompt!r} {counts}"
    lines = [f"ticks: {d['ticks']} finished {counts}", "per tick (tokens):"]
    for name in ("cache_read", "input", "cache_write", "output"):
        s = d["per_tick"][name]
        lines.append(f"  {name:12s} median {s['median']:>12,.0f}  mean {s['mean']:>12,.0f}  min {s['min']:>10,}  max {s['max']:>12,}  sum {s['sum']:>14,}")
    lines.append(f"  requests     mean {d['per_tick']['messages']['mean']:.2f}   wall seconds median {d['seconds_median'] or 0:.1f}"
                 f"   seconds to first reply median {d['seconds_to_first_reply_median'] or 0:.1f}")
    lines.append("ticks' share of the session: " + ", ".join(f"{k} {v}%" for k, v in d["ticks_share"].items() if v is not None))
    if report.subagent_transcripts:
        s = report.subagents
        lines.append(f"subagent transcripts: {report.subagent_transcripts}, their usage is in the session totals and in no tick "
                     f"(cache_read {s.cache_read:,}, input {s.input:,}, cache_write {s.cache_write:,}, output {s.output:,})")
    lines.append(_usd_line(report, d))
    return "\n".join(lines)


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="What a recurring prompt costs inside a Claude Code session.")
    ap.add_argument("transcript", type=Path)
    ap.add_argument("--prompt", required=True, help="the recurring prompt, matched exactly after trimming")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-subagents", action="store_true", help="leave <stem>/subagents/*.jsonl out of the session totals")
    for name in ("cache-read", "input", "cache-write", "output"):
        ap.add_argument(f"--price-{name}", type=float, default=None, help=f"USD per million {name} tokens")
    args = ap.parse_args(list(argv) if argv is not None else None)
    if not args.transcript.is_file():
        print(f"tick cost: transcript not found: {args.transcript}", file=sys.stderr)
        return 2
    prices = Prices(args.price_cache_read, args.price_input, args.price_cache_write, args.price_output)
    children = [] if args.no_subagents else [records(p) for p in subagent_transcripts(args.transcript)]
    report = measure(records(args.transcript), args.prompt, prices, subagents=children)
    print(json.dumps(report.to_dict(), indent=1) if args.json else render(report))
    return 0 if report.ticks else 1


if __name__ == "__main__":
    raise SystemExit(main())
