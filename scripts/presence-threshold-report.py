#!/usr/bin/env python3
"""Rung 3 of the presence ladder, measured: can its window be fitted to data?

`lib/operator_presence.py` ships `PRESENT_WITHIN_SECONDS` labelled as an
UNMEASURED placeholder. This is the measurement its comment defers to. It reads
the Claude Code transcripts, rebuilds every turn end with what rung 3 would have
known at that moment, and asks whether any window beats the trivial
majority-class baseline by a margin the data can actually support.

WHAT THE CONSTANT THRESHOLDS. The stamp is written on UserPromptSubmit
(`hooks/time-awareness-hook.py`) and read at Stop (`hooks/next-step-recorder.py`),
so rung 3 compares exactly one quantity against the window:

    age  =  (the turn end)  -  (the last human prompt before it)

That is not the wait until the next prompt. The continuation brief worded the
claim as "gap < T implies present", with the gap measured from the turn end to
the next prompt. But the next prompt is what DECIDES whether he was present, so
thresholding it would score a label against itself. Here the wait defines the
label and the age is the predictor, as in the module.

THE LABEL. PRESENT is worth something only because it buys a hold: the Stop hook
blocks and waits up to `RELEASE_SECONDS` for an answer, and a PRESENT nobody
answers costs the whole ceiling -- the expensive error the module names. So a
turn end is PRESENT when a human prompt arrived in the same session within
RELEASE_SECONDS of it, and ABSENT when none did. It is CENSORED, and left out,
when the ceiling has not yet elapsed and nothing has arrived: an open session is
not yet a no.

WHAT COUNTS AS HUMAN. Claude Code stamps user records with `origin.kind` --
"human", "task-notification", "auto-continuation", "peer". Only "human" counts.
Records with no origin at all are kept apart as UNLABELLED. They occur in every
Claude Code version in the corpus beside labelled records, so they are not an
older format of human prompt, and telling what they are would take reading
their text, which this script never does. A sensitivity variant counts them as
human instead.

PRIVACY. Lines are parsed as JSON, but only structural fields are used: record
type, timestamp, uuid, the sidechain / meta / compaction flags, entrypoint,
`origin.kind`, and the TYPES of content blocks (to tell a tool result from a
prompt). No message text is kept, compared or emitted. Every output is an
aggregate; not even a session id leaves this process.

THE DECISION RULE, fixed before the corpus was run. A window is adopted only if,
on a held-out split of LATER sessions, it clears all three:

  1. the precision of its PRESENT verdicts beats the base rate -- the precision
     of an uninformative guess -- with non-overlapping Wilson 95% intervals;
  2. its accuracy beats always predicting the majority class, again with
     non-overlapping Wilson 95% intervals;
  3. both margins stay above zero at the 2.5th percentile of a session-level
     bootstrap, because turns inside one session are not independent, and a
     Wilson interval that assumes they are is too narrow.

The window is chosen on the EARLIER split -- among windows clearing 1 and 2
there, the highest accuracy lower bound, a tie going to the smaller window --
and judged only on the later one. If none qualifies, nothing is fitted and the
constant stays exactly as it is.

    bin/grip-py scripts/presence-threshold-report.py
    bin/grip-py scripts/presence-threshold-report.py --json-out x.json --markdown-out x.md
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.claude_dir import get_claude_dir  # noqa: E402
from lib.continuity_precision import wilson  # noqa: E402
from lib.next_step_router import RELEASE_SECONDS, SILENT_WAIT_SECONDS  # noqa: E402
from lib.operator_presence import PRESENT_WITHIN_SECONDS  # noqa: E402

_GRID = (10, 20, 30, 45, 60, 90, 120, 180, 240, 300, 420, 600, 900,
         1200, 1800, 2700, 3600, 7200, 14400)
THRESHOLDS = tuple(sorted({float(t) for t in _GRID} | {float(PRESENT_WITHIN_SECONDS)}))
FIT_FRACTION = 0.5
DRAWS = 2000
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)

Row = tuple  # (age or None, label 0/1)


# ------------------------------------------------------------------ records

@dataclass(frozen=True)
class Record:
    """One structural event from a transcript -- never its text."""

    kind: str          # "assistant", or "prompt" for a user record that starts a turn
    ts: float          # epoch seconds
    uuid: str
    origin: str = ""   # prompts only: origin.kind, "" when the record carries none


def _epoch(stamp: object) -> float | None:
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def _is_tool_result(obj: dict) -> bool:
    if "toolUseResult" in obj:
        return True
    content = (obj.get("message") or {}).get("content")
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    )


def _prompt_origin(obj: dict) -> str | None:
    """The origin of a user record that STARTS a turn, or None if it does not.

    Tool results sit inside a turn. Compaction summaries restate the past. Meta
    records without an origin are expansions and caveats riding on a prompt
    that is itself recorded; meta records WITH an origin (auto-continuation,
    peer) do start a turn, so they are kept.
    """
    if _is_tool_result(obj) or obj.get("isCompactSummary") or obj.get("isVisibleInTranscriptOnly"):
        return None
    origin = obj.get("origin")
    kind = origin.get("kind") if isinstance(origin, dict) else None
    if obj.get("isMeta") and not kind:
        return None
    return str(kind or "")


def classify(obj: object) -> Record | None:
    """Reduce one transcript record to the structure this report needs, or None.

    Only main-chain records of interactive sessions survive: a sidechain is a
    subagent talking to its parent, and a headless run (`entrypoint` other than
    "cli") is one where rung 2 already answers ABSENT, so rung 3 never speaks.
    """
    if not isinstance(obj, dict) or obj.get("isSidechain") or obj.get("entrypoint") != "cli":
        return None
    ts, uuid = _epoch(obj.get("timestamp")), obj.get("uuid")
    if ts is None or not isinstance(uuid, str):
        return None
    if obj.get("type") == "assistant":
        return Record("assistant", ts, uuid)
    if obj.get("type") != "user":
        return None
    origin = _prompt_origin(obj)
    return None if origin is None else Record("prompt", ts, uuid, origin)


def parse_line(raw: bytes | str) -> Record | None:
    """One transcript line to a Record. A torn last line of a live session is None."""
    try:
        return classify(json.loads(raw))
    except (ValueError, TypeError, RecursionError):
        return None


# ------------------------------------------------------------ origin classes

Origin = Callable[[str], bool]


def human(origin: str) -> bool:
    """The module's own claim: a person typed it."""
    return origin == "human"


def human_or_unlabelled(origin: str) -> bool:
    """Sensitivity: every prompt without an origin counted as a person's."""
    return origin in ("human", "")


def any_prompt(origin: str) -> bool:
    """Sensitivity: what the shipped stamp records, which is more than typing.

    UserPromptSubmit also fires for injected prompts -- the report's findings
    give the measurement -- so in production a background task's notification
    refreshes the stamp exactly as a keystroke would. Every origin counts.
    """
    return True


# -------------------------------------------------------------------- turns

@dataclass(frozen=True)
class Turn:
    """One turn end, with what rung 3 would have known and what happened next."""

    uuid: str            # the turn-ending assistant record
    session: str
    end: float
    age: float | None    # seconds since the last anchoring prompt -- the predictor
    wait: float | None   # seconds until the next answering prompt -- the label's basis


def _next_answers(records: Sequence[Record], answer: Origin) -> list[float | None]:
    """For each index, the timestamp of the first answering prompt after it."""
    out: list[float | None] = [None] * len(records)
    upcoming: float | None = None
    for i in range(len(records) - 1, -1, -1):
        out[i] = upcoming
        rec = records[i]
        if rec.kind == "prompt" and answer(rec.origin):
            upcoming = rec.ts
    return out


def extract_turns(
    session: str,
    records: Sequence[Record],
    *,
    anchor: Origin = human,
    answer: Origin = human,
) -> list[Turn]:
    """Every turn end in one session, with its predictor and its wait.

    A turn ends at the last assistant record before the next prompt of ANY
    origin: a task notification starts a turn just as a keystroke does, and the
    Stop hook consults presence at the end of both. Tool results were dropped by
    `classify`, so two assistant records in a row belong to one turn.

    A prompt stamped later than the turn end it precedes reads as no evidence,
    mirroring `seconds_since_prompt`, which returns None for a negative age.
    """
    upcoming = _next_answers(records, answer)
    turns: list[Turn] = []
    last: float | None = None
    for i, rec in enumerate(records):
        if rec.kind == "prompt":
            last = rec.ts if anchor(rec.origin) else last
            continue
        if i + 1 < len(records) and records[i + 1].kind != "prompt":
            continue
        age = None if last is None or rec.ts < last else rec.ts - last
        nxt = upcoming[i]
        turns.append(Turn(rec.uuid, session, rec.ts, age, None if nxt is None else nxt - rec.ts))
    return turns


def _min_known(a: float | None, b: float | None) -> float | None:
    known = [v for v in (a, b) if v is not None]
    return min(known) if known else None


def dedupe(turns: Iterable[Turn]) -> list[Turn]:
    """One row per turn end, however many forked transcripts copied it.

    A resumed or forked session copies earlier records verbatim -- same uuid,
    same timestamp, a new session id -- so one turn end can sit in several
    files. The copies differ only in how much of what followed they saw: the
    fork holds a reply that the original, ending first, never recorded. So the
    smallest known age and the smallest known wait win, and the turn stays with
    the session it was first seen in.
    """
    best: dict[str, Turn] = {}
    for t in turns:
        cur = best.get(t.uuid)
        best[t.uuid] = t if cur is None else Turn(
            cur.uuid, cur.session, cur.end,
            _min_known(cur.age, t.age), _min_known(cur.wait, t.wait),
        )
    return list(best.values())


def label(turn: Turn, horizon: float, now: float) -> int | None:
    """1 when he answered inside the ceiling, 0 when he did not, None when unknowable.

    The hold releases once `waited >= RELEASE_SECONDS`, so an answer landing
    exactly on the ceiling missed it: the comparison is strict. A negative wait
    is a clock anomaly and is left out rather than guessed at.
    """
    if turn.wait is None:
        return 0 if now - turn.end >= horizon else None
    if turn.wait < 0:
        return None
    return 1 if turn.wait < horizon else 0


# ------------------------------------------------------------------ scoring

@dataclass(frozen=True)
class Confusion:
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.fn + self.tn


def confusion(rows: Sequence[Row], threshold: float) -> Confusion:
    """Score rung 3 at one window: PRESENT iff `age < threshold`, as the module has it."""
    tp = fp = fn = tn = 0
    for age, y in rows:
        said_present = age is not None and threshold > 0 and age < threshold
        if said_present and y:
            tp += 1
        elif said_present:
            fp += 1
        elif y:
            fn += 1
        else:
            tn += 1
    return Confusion(tp, fp, fn, tn)


def _total(parts: Sequence[Confusion]) -> Confusion:
    return Confusion(sum(c.tp for c in parts), sum(c.fp for c in parts),
                     sum(c.fn for c in parts), sum(c.tn for c in parts))


def _ratio(k: int, n: int) -> float | None:
    return k / n if n else None


def score(c: Confusion) -> dict:
    """Precision, recall and accuracy, each with its Wilson 95% interval."""
    said, positives, right = c.tp + c.fp, c.tp + c.fn, c.tp + c.tn
    return {
        "said_present": said,
        "precision": _ratio(c.tp, said), "precision_ci": wilson(c.tp, said),
        "recall": _ratio(c.tp, positives), "recall_ci": wilson(c.tp, positives),
        "accuracy": _ratio(right, c.n), "accuracy_ci": wilson(right, c.n),
    }


def baseline(rows: Sequence[Row]) -> dict:
    """The trivial comparator: the base rate, and always predicting the majority.

    A tie goes to ABSENT, the module's own default.
    """
    n = len(rows)
    k = sum(1 for _, y in rows if y)
    majority_present = 2 * k > n
    hits = k if majority_present else n - k
    return {
        "n": n, "present": k,
        "base_rate": _ratio(k, n), "base_rate_ci": wilson(k, n),
        "majority": "PRESENT" if majority_present else "ABSENT",
        "majority_accuracy": _ratio(hits, n), "majority_accuracy_ci": wilson(hits, n),
    }


def beats(s: dict, base: dict) -> dict:
    """The rule's two interval comparisons: each needs non-overlapping 95% intervals."""
    return {
        "precision": s["precision_ci"][0] > base["base_rate_ci"][1],
        "accuracy": s["accuracy_ci"][0] > base["majority_accuracy_ci"][1],
    }


def sweep(rows: Sequence[Row], thresholds: Sequence[float] = THRESHOLDS) -> list[dict]:
    base = baseline(rows)
    out = []
    for t in thresholds:
        s = score(confusion(rows, t))
        out.append({"threshold": t, **s, "beats": beats(s, base)})
    return out


def quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


# ----------------------------------------------------------------- deciding

def split_by_session(pairs: Sequence[tuple], fraction: float = FIT_FRACTION) -> tuple[list, list]:
    """Earlier sessions to fit, later sessions to judge. A session never straddles.

    `pairs` are (Turn, label). Sessions are ordered by their first turn end and
    taken whole until the fit side holds `fraction` of the turns: turns inside
    one session are correlated, so a session feeding both sides would leak.
    """
    groups: dict[str, list] = {}
    for pair in pairs:
        groups.setdefault(pair[0].session, []).append(pair)
    order = sorted(groups, key=lambda s: (min(p[0].end for p in groups[s]), s))
    target = fraction * len(pairs)
    fit: list = []
    hold: list = []
    for s in order:
        (fit if len(fit) < target else hold).extend(groups[s])
    return fit, hold


def by_session(pairs: Sequence[tuple]) -> list[list[Row]]:
    groups: dict[str, list[Row]] = {}
    for t, y in pairs:
        groups.setdefault(t.session, []).append((t.age, y))
    return [groups[s] for s in sorted(groups)]


def fit_threshold(rows: Sequence[Row], thresholds: Sequence[float] = THRESHOLDS) -> float | None:
    """The window this split would choose, or None when no window qualifies.

    Qualifying means clearing both comparisons in `beats`. Among those the
    highest accuracy lower bound wins, and a tie goes to the SMALLER window --
    the one that calls him present less often.
    """
    best: tuple | None = None
    for row in sweep(rows, thresholds):
        if not all(row["beats"].values()):
            continue
        key = (row["accuracy_ci"][0], -row["threshold"])
        if best is None or key > best[0]:
            best = (key, row["threshold"])
    return None if best is None else best[1]


def _margins(c: Confusion) -> tuple[float, float]:
    """Precision minus base rate, accuracy minus majority accuracy; -1 when undefined.

    A draw in which the window calls nobody present has no precision. It scores
    the worst margin rather than being skipped, because skipping it would
    flatter exactly the windows too tight to matter.
    """
    if c.n == 0:
        return -1.0, -1.0
    base = (c.tp + c.fn) / c.n
    said = c.tp + c.fp
    precision_margin = c.tp / said - base if said else -1.0
    return precision_margin, (c.tp + c.tn) / c.n - max(base, 1.0 - base)


def cluster_margins(groups: Sequence[Sequence[Row]], threshold: float, *,
                    draws: int = DRAWS, seed: int = 0) -> dict:
    """Session-level bootstrap of the two margins the decision rule tests.

    Each draw takes as many sessions as there are, with replacement, and
    totals their counts. Cost is O(draws x sessions): linear in the corpus,
    with `draws` a fixed constant.
    """
    per = [confusion(g, threshold) for g in groups]
    if not per or draws <= 0:
        return {"precision_margin_lo": None, "accuracy_margin_lo": None, "draws": 0}
    rng = random.Random(seed)
    precision, accuracy = [], []
    for _ in range(draws):
        p_margin, a_margin = _margins(_total(rng.choices(per, k=len(per))))
        precision.append(p_margin)
        accuracy.append(a_margin)
    return {"precision_margin_lo": quantile(precision, 0.025),
            "accuracy_margin_lo": quantile(accuracy, 0.025), "draws": draws}


def judge(groups: Sequence[Sequence[Row]], threshold: float, *,
          draws: int = DRAWS, seed: int = 0) -> dict:
    """One window on the held-out sessions, against all three conditions."""
    rows = [r for g in groups for r in g]
    s = score(confusion(rows, threshold))
    b = beats(s, baseline(rows))
    boot = cluster_margins(groups, threshold, draws=draws, seed=seed)
    lows = (boot["precision_margin_lo"], boot["accuracy_margin_lo"])
    robust = all(v is not None and v > 0 for v in lows)
    return {"threshold": threshold, **s, "beats": b, "bootstrap": boot,
            "passes": b["precision"] and b["accuracy"] and robust}


def decide(fit_rows: Sequence[Row], hold_groups: Sequence[Sequence[Row]], *,
           thresholds: Sequence[float] = THRESHOLDS, draws: int = DRAWS, seed: int = 0) -> dict:
    """Fit on the earlier split, judge on the later one, and say whether to adopt."""
    chosen = fit_threshold(fit_rows, thresholds)
    hold_rows = [r for g in hold_groups for r in g]
    judged = None if chosen is None else judge(hold_groups, chosen, draws=draws, seed=seed)
    return {
        "chosen": chosen,
        "fit_baseline": baseline(fit_rows),
        "holdout_baseline": baseline(hold_rows),
        "judged": judged,
        "current": judge(hold_groups, float(PRESENT_WITHIN_SECONDS), draws=draws, seed=seed),
        "adopt": bool(judged and judged["passes"]),
    }


# ------------------------------------------------------------------ corpus

def transcript_paths(root: Path) -> tuple[list[Path], int]:
    """Main transcripts under `root`, sorted; and how many subagent files were skipped.

    Files under a `subagents` directory hold only sidechain records (measured:
    every user and assistant record in them carries isSidechain), which
    `classify` would discard line by line anyway. Skipping them whole saves
    most of the bytes, and `classify` still refuses a sidechain record
    wherever one turns up.
    """
    found: list[Path] = []
    skipped = 0
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        jsonl = [f for f in files if f.endswith(".jsonl")]
        if "subagents" in Path(dirpath).relative_to(root).parts:
            skipped += len(jsonl)
        else:
            found.extend(Path(dirpath) / f for f in jsonl)
    return sorted(found), skipped


def read_session(path: Path, cutoff: float = float("inf")) -> list[Record]:
    """One transcript's structural events up to `cutoff`, in file order.

    Records stamped after `cutoff` are dropped, so a scan is a snapshot of the
    corpus at that instant. Transcripts only grow, so a later re-run with the
    same --now reads exactly what the first one read. Without the cutoff a
    re-run picked up prompts that arrived after the recorded scan time and
    labelled turn ends the original scan had censored.
    """
    with open(path, "rb") as fh:
        return [rec for rec in map(parse_line, fh) if rec is not None and rec.ts <= cutoff]


def load_corpus(root: Path, cutoff: float = float("inf")) -> tuple[list[tuple[str, list[Record]]], dict]:
    """Every interactive session as (session id, records) up to `cutoff`, plus corpus counts."""
    paths, skipped = transcript_paths(root)
    sessions: list[tuple[str, list[Record]]] = []
    size = 0
    for path in paths:
        try:
            size += path.stat().st_size
            records = read_session(path, cutoff)
        except OSError:
            continue
        if records:
            sessions.append((path.stem, records))
    seen = {r.uuid: r.origin for _, records in sessions for r in records if r.kind == "prompt"}
    origins = Counter(seen.values())
    return sessions, {
        "files": len(paths), "subagent_files_skipped": skipped, "bytes": size,
        "sessions": len(sessions),
        "prompts_by_origin": {k or "(none)": v for k, v in sorted(origins.items())},
    }


# ---------------------------------------------------------------- analysis

@dataclass(frozen=True)
class Variant:
    name: str
    anchor: Origin
    answer: Origin
    horizon: float


PRIMARY = Variant(
    f"human prompts, {int(RELEASE_SECONDS) // 60}-minute hold ceiling",
    human, human, float(RELEASE_SECONDS),
)
SENSITIVITY = (
    Variant("prompts with no origin counted as human",
            human_or_unlabelled, human_or_unlabelled, float(RELEASE_SECONDS)),
    Variant("any prompt refreshes the stamp, as shipped",
            any_prompt, human, float(RELEASE_SECONDS)),
    Variant(f"human prompts, {int(SILENT_WAIT_SECONDS) // 60}-minute horizon",
            human, human, float(SILENT_WAIT_SECONDS)),
)


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _labelled(turns: Sequence[Turn], horizon: float, now: float) -> tuple[list, int, int]:
    """(Turn, label) pairs, plus how many were censored and how many anomalous."""
    kept: list = []
    censored = anomalies = 0
    for t in turns:
        y = label(t, horizon, now)
        if y is not None:
            kept.append((t, y))
        elif t.wait is not None:
            anomalies += 1
        else:
            censored += 1
    return kept, censored, anomalies


def _distribution(values: Sequence[float]) -> dict:
    return {"n": len(values), **{f"p{int(q * 100)}": quantile(values, q) for q in QUANTILES}}


def _describe(kept: Sequence[tuple]) -> dict:
    """Aggregates only: span, sessions, and the predictor and wait distributions."""
    ends = [t.end for t, _ in kept]
    ages = [t.age for t, _ in kept if t.age is not None]
    waits = [t.wait for t, _ in kept if t.wait is not None]
    return {
        "sessions": len({t.session for t, _ in kept}),
        "first_turn": _iso(min(ends)) if ends else None,
        "last_turn": _iso(max(ends)) if ends else None,
        "age": _distribution(ages),
        "wait": _distribution(waits),
        "no_earlier_anchor": _ratio(len(kept) - len(ages), len(kept)),
        "no_later_answer": _ratio(len(kept) - len(waits), len(kept)),
    }


def _split_counts(fit: Sequence[tuple], hold: Sequence[tuple]) -> dict:
    """How big each side is in sessions, which is what the bootstrap resamples."""
    return {
        "fit_turns": len(fit), "holdout_turns": len(hold),
        "fit_sessions": len({t.session for t, _ in fit}),
        "holdout_sessions": len({t.session for t, _ in hold}),
        "holdout_from": _iso(min((t.end for t, _ in hold), default=None)),
    }


def evaluate(sessions: Sequence[tuple[str, Sequence[Record]]], variant: Variant, *,
             now: float, draws: int = DRAWS, seed: int = 0) -> dict:
    """The whole pipeline for one definition of 'human' and one horizon."""
    raw = [t for sid, recs in sessions
           for t in extract_turns(sid, recs, anchor=variant.anchor, answer=variant.answer)]
    turns = dedupe(raw)
    kept, censored, anomalies = _labelled(turns, variant.horizon, now)
    rows = [(t.age, y) for t, y in kept]
    fit, hold = split_by_session(kept)
    return {
        "variant": variant.name, "horizon_s": variant.horizon,
        "turns_found": len(raw), "turns": len(turns), "labelled": len(kept),
        "censored": censored, "clock_anomalies": anomalies,
        **_describe(kept),
        "baseline": baseline(rows),
        "sweep": sweep(rows),
        **_split_counts(fit, hold),
        "decision": decide([(t.age, y) for t, y in fit], by_session(hold), draws=draws, seed=seed),
    }


def analyse(sessions: Sequence[tuple[str, Sequence[Record]]], *, now: float,
            draws: int = DRAWS, seed: int = 0) -> dict:
    return {
        "primary": evaluate(sessions, PRIMARY, now=now, draws=draws, seed=seed),
        "sensitivity": [evaluate(sessions, v, now=now, draws=draws, seed=seed)
                        for v in SENSITIVITY],
    }


# --------------------------------------------------------------- rendering

def _f(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def _ci(ci: Sequence[float]) -> str:
    return f"[{ci[0]:.3f}, {ci[1]:.3f}]"


def _secs(x: float | None) -> str:
    return "n/a" if x is None else f"{x:,.0f}"


def _yes(flag: bool) -> str:
    return "yes" if flag else "no"


def _corpus_lines(meta: dict, primary: dict) -> list[str]:
    origins = ", ".join(f"{k} {v:,}" for k, v in meta["prompts_by_origin"].items())
    return [
        "## Corpus", "",
        "| Measure | Value |", "|---|---|",
        f"| Transcripts read (main sessions) | {meta['files']:,} |",
        f"| Subagent transcripts skipped (sidechain only) | {meta['subagent_files_skipped']:,} |",
        f"| Bytes read | {meta['bytes'] / 1e9:.2f} GB |",
        f"| Interactive sessions with at least one kept record | {meta['sessions']:,} |",
        f"| Turn-starting prompts by origin (unique) | {origins} |",
        f"| Turn ends found / after de-duplicating forks | {primary['turns_found']:,} / {primary['turns']:,} |",
        f"| Labelled / censored / clock anomalies | {primary['labelled']:,} / {primary['censored']:,} / {primary['clock_anomalies']:,} |",
        f"| Sessions contributing a labelled turn end | {primary['sessions']:,} |",
        f"| Labelled turn ends span (UTC) | {primary['first_turn']} to {primary['last_turn']} |",
        f"| Scan time (UTC) | {meta['scan_time']} |",
        "",
    ]


def _sweep_lines(primary: dict) -> list[str]:
    b = primary["baseline"]
    lines = [
        "## Every window on the whole corpus (descriptive)", "",
        f"Base rate, the share of turn ends answered within {_secs(primary['horizon_s'])} s: "
        f"{_f(b['base_rate'])} {_ci(b['base_rate_ci'])} of {b['n']:,}. Majority class: "
        f"{b['majority']}, accuracy {_f(b['majority_accuracy'])} {_ci(b['majority_accuracy_ci'])}.",
        "",
        "| Window T (s) | Said PRESENT | Precision [95% CI] | Recall [95% CI] | Accuracy [95% CI] "
        "| Precision beats base rate | Accuracy beats majority |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in primary["sweep"]:
        mark = " (current)" if row["threshold"] == float(PRESENT_WITHIN_SECONDS) else ""
        lines.append(
            f"| {_secs(row['threshold'])}{mark} | {row['said_present']:,} "
            f"| {_f(row['precision'])} {_ci(row['precision_ci'])} "
            f"| {_f(row['recall'])} {_ci(row['recall_ci'])} "
            f"| {_f(row['accuracy'])} {_ci(row['accuracy_ci'])} "
            f"| {_yes(row['beats']['precision'])} | {_yes(row['beats']['accuracy'])} |"
        )
    return lines + [""]


def _judged_line(name: str, j: dict, base: dict) -> str:
    boot = j["bootstrap"]
    return (
        f"| {name} | {_f(j['precision'])} {_ci(j['precision_ci'])} vs {_f(base['base_rate_ci'][1])} "
        f"| {_f(j['accuracy'])} {_ci(j['accuracy_ci'])} vs {_f(base['majority_accuracy_ci'][1])} "
        f"| {_f(boot['precision_margin_lo'])} | {_f(boot['accuracy_margin_lo'])} | {_yes(j['passes'])} |"
    )


def _decision_lines(primary: dict) -> list[str]:
    d = primary["decision"]
    fb, hb = d["fit_baseline"], d["holdout_baseline"]
    chosen = "none qualified" if d["chosen"] is None else f"{_secs(d['chosen'])} s"
    lines = [
        "## The decision: fit on earlier sessions, judge on later ones", "",
        "| | Fit (earlier sessions) | Holdout (later sessions) |", "|---|---|---|",
        f"| Sessions | {primary['fit_sessions']:,} | {primary['holdout_sessions']:,} "
        f"(first turn end {primary['holdout_from']}) |",
        f"| Labelled turn ends | {fb['n']:,} | {hb['n']:,} |",
        f"| Base rate [95% CI] | {_f(fb['base_rate'])} {_ci(fb['base_rate_ci'])} "
        f"| {_f(hb['base_rate'])} {_ci(hb['base_rate_ci'])} |",
        f"| Majority class, accuracy [95% CI] | {fb['majority']}, {_f(fb['majority_accuracy'])} "
        f"{_ci(fb['majority_accuracy_ci'])} | {hb['majority']}, {_f(hb['majority_accuracy'])} "
        f"{_ci(hb['majority_accuracy_ci'])} |",
        "",
        f"Window chosen on the fit split: {chosen}.",
        "",
        "| Window on the holdout | Precision [CI] vs base-rate upper bound "
        "| Accuracy [CI] vs majority upper bound | Bootstrap 2.5% precision margin "
        "| Bootstrap 2.5% accuracy margin | Clears all three |",
        "|---|---|---|---|---|---|",
    ]
    if d["judged"] is not None:
        lines.append(_judged_line(f"{_secs(d['chosen'])} s (fitted)", d["judged"], hb))
    lines.append(_judged_line(f"{_secs(PRESENT_WITHIN_SECONDS)} s (current)", d["current"], hb))
    verdict = f"ADOPT {_secs(d['chosen'])} s" if d["adopt"] else "NO FIT: the constant stays as it is"
    return lines + ["", f"Mechanical verdict: **{verdict}**.", ""]


def _sensitivity_lines(result: dict) -> list[str]:
    lines = [
        "## Sensitivity (reported, not decided on)", "",
        "| Variant | Labelled turn ends | Base rate | Window chosen on fit "
        "| Current window clears all three | Would adopt |",
        "|---|---|---|---|---|---|",
    ]
    for v in [result["primary"], *result["sensitivity"]]:
        d = v["decision"]
        chosen = "none" if d["chosen"] is None else f"{_secs(d['chosen'])} s"
        lines.append(
            f"| {v['variant']} | {v['labelled']:,} | {_f(v['baseline']['base_rate'])} "
            f"| {chosen} | {_yes(d['current']['passes'])} | {_yes(d['adopt'])} |"
        )
    return lines + [""]


def _distribution_lines(primary: dict) -> list[str]:
    head = "| Quantity | n | " + " | ".join(f"p{int(q * 100)}" for q in QUANTILES) + " |"
    names = (("age", "Age of the last human prompt at a turn end (the predictor), s"),
             ("wait", "Wait from a turn end to the next human prompt (the label's basis), s"))
    rows = [
        f"| {name} | {primary[key]['n']:,} | "
        + " | ".join(_secs(primary[key][f"p{int(q * 100)}"]) for q in QUANTILES) + " |"
        for key, name in names
    ]
    return [
        "## Distributions", "", head, "|---|---|" + "---|" * len(QUANTILES), *rows, "",
        f"Turn ends with no earlier human prompt in the session: {_f(primary['no_earlier_anchor'])}. "
        f"With no later human prompt: {_f(primary['no_later_answer'])}.",
        "",
    ]


def render_markdown(meta: dict, result: dict) -> str:
    primary = result["primary"]
    lines = (_corpus_lines(meta, primary) + _sweep_lines(primary) + _decision_lines(primary)
             + _sensitivity_lines(result) + _distribution_lines(primary))
    return "\n".join(lines)


# --------------------------------------------------------------------- main

def _display(path: Path) -> str:
    home, text = str(Path.home()), str(path)
    return "~" + text[len(home):] if text.startswith(home) else text


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--projects", type=Path, default=None,
                    help="transcript root (default: <claude dir>/projects)")
    ap.add_argument("--now", type=float, default=None,
                    help="epoch seconds the scan is taken as of: later records are ignored and "
                         "younger turn ends are censored (default: scan start)")
    ap.add_argument("--draws", type=int, default=DRAWS, help="bootstrap draws")
    ap.add_argument("--seed", type=int, default=0, help="bootstrap seed")
    ap.add_argument("--json-out", type=Path, default=None)
    ap.add_argument("--markdown-out", type=Path, default=None)
    args = ap.parse_args(argv)

    root = args.projects or (get_claude_dir() / "projects")
    now = time.time() if args.now is None else args.now
    started = time.time()
    sessions, meta = load_corpus(root, now)
    result = analyse(sessions, now=now, draws=args.draws, seed=args.seed)
    meta.update(projects=_display(root), scan_time=_iso(now), draws=args.draws, seed=args.seed,
                thresholds=list(THRESHOLDS), elapsed_s=round(time.time() - started, 1))

    if args.json_out:
        args.json_out.write_text(json.dumps({"meta": meta, **result}, indent=2, default=str))
    if args.markdown_out:
        args.markdown_out.write_text(render_markdown(meta, result))

    p = result["primary"]
    d = p["decision"]
    chosen = "none" if d["chosen"] is None else f"{_secs(d['chosen'])} s"
    print(f"transcripts {meta['files']} | labelled turn ends {p['labelled']} "
          f"| base rate {_f(p['baseline']['base_rate'])} | elapsed {meta['elapsed_s']}s")
    print(f"fitted window: {chosen} | adopt: {_yes(d['adopt'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
