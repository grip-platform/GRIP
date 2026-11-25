#!/usr/bin/env python3
"""Attribute real token spend by model across Claude Code session transcripts.

WHY THIS EXISTS, and what it deliberately is NOT.

GRIP's goal of "RSI in a mostly non-Anthropic way" has no scoreboard today.
``lib/llm_usage_ledger`` records **Pool B** -- the ``grip_infer`` fan-out to
groq, gemini, deepseek and xai -- and is structurally blind to **Pool A**: the
Claude Code REPL, the subagents it dispatches, and ``claude -p``. Measured
2026-09-17: 99.4% of that ledger's callers are ``grip_infer``, so it *is* Pool B
by construction. An Anthropic fraction computed from it measures leakage inside
the already-non-Anthropic pool, never the loop's actual split.

The missing half is already on disk. Session transcripts carry ``message.model``
and a full ``message.usage`` block per request, so Pool A can be counted from
**real API-reported figures** and set beside the Pool B ledger. That comparison
is the scoreboard; neither half alone is one.

This is intentionally NOT part of ``lib/orchestrator_budget.py``. That module
answers a different question -- where a session's *context window* went -- using
a chars/4 estimator, and it deliberately SKIPS sidechain (subagent) records
because a subagent does not consume the orchestrator's window. Here the opposite
holds on both counts: an estimate is not admissible when the API reports exact
figures, and a subagent's tokens are real Anthropic spend that MUST be counted.
Merging the two would force one file to hold two incompatible notions of a token.

Honest limits, stated here rather than left to be discovered:

* This measures **Pool A only**. It cannot see Pool B, exactly as the ledger
  cannot see Pool A. The split requires both, which is why :func:`split` takes
  the ledger's totals as an argument rather than trying to compute them.
* ``<synthetic>`` is the harness's own marker for a record that never went to a
  model. It is excluded from every count; including it would inflate Pool A with
  calls that never happened.
* Cache-read tokens are tracked separately from fresh input because they are
  priced differently. Folding them together would overstate the cost of a
  cache-warm session.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

# The harness writes this in place of a model id for records that never went to
# a model (tool plumbing, injected reminders). Counting them inflates Pool A.
SYNTHETIC = "<synthetic>"

# Prefix -> vendor. Order matters only in that the first match wins, so no
# prefix here may be a prefix of an earlier one.
_VENDOR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("claude", "anthropic"),
    ("anthropic", "anthropic"),
    ("gpt", "openai"),
    ("o1-", "openai"),
    ("o3-", "openai"),
    ("gemini", "google"),
    ("deepseek", "deepseek"),
    ("grok", "xai"),
    ("qwen", "alibaba"),
    ("llama", "meta"),
    ("kimi", "moonshot"),
    ("mistral", "mistral"),
    ("glm", "zhipu"),
)

VENDOR_UNKNOWN = "unknown"
ANTHROPIC = "anthropic"


def is_real_call(model_id: str | None) -> bool:
    """False for the synthetic marker, an empty id, or a non-string.

    A record that never reached a model is not spend. This is the single
    guard that keeps Pool A totals honest.
    """
    if not isinstance(model_id, str):
        return False
    stripped = model_id.strip()
    return bool(stripped) and stripped != SYNTHETIC


def vendor_of(model_id: str | None) -> str:
    """Map a model id to its vendor, case-insensitively.

    Returns :data:`VENDOR_UNKNOWN` for anything unrecognised rather than
    guessing -- an unknown model silently counted as non-Anthropic would
    flatter the very metric this module exists to report.
    """
    if not is_real_call(model_id):
        return VENDOR_UNKNOWN
    lowered = str(model_id).strip().lower()
    for prefix, vendor in _VENDOR_PREFIXES:
        if lowered.startswith(prefix):
            return vendor
    return VENDOR_UNKNOWN


_PROVIDER_ALIASES: tuple[tuple[str, str], ...] = (
    ("anthropic", ANTHROPIC),
    ("cch", ANTHROPIC),          # Claude Code Handshake — bills a subscription, still Anthropic
    ("gemini", "google"),
    ("google", "google"),
    ("groq", "groq"),
    ("xai", "xai"),
    ("grok", "xai"),
    ("deepseek", "deepseek"),
    ("moonshot", "moonshot"),
    ("kimi", "moonshot"),
    ("openai", "openai"),
    ("mistral", "mistral"),
    ("ollama", "local"),
    ("lmstudio", "local"),
)


def normalise_provider(name: str | None) -> str:
    """Map a ledger ``provider`` string to the same vendor space as :func:`vendor_of`.

    ``anthropic-cch`` and bare ``cch`` normalise to Anthropic: the Claude Code
    Handshake bills a subscription rather than per-token credits, but the model
    answering is still Anthropic's. Counting it as non-Anthropic would flatter
    the metric.
    """
    if not isinstance(name, str) or not name.strip():
        return VENDOR_UNKNOWN
    lowered = name.strip().lower()
    for alias, vendor in _PROVIDER_ALIASES:
        if lowered.startswith(alias):
            return vendor
    return VENDOR_UNKNOWN


@dataclass
class LedgerTotals:
    """Pool B, as recorded by ``lib/llm_usage_ledger``.

    ``tokens_populated`` is the load-bearing field. Measured 2026-09-17 on the
    live ledger: all 2,163 records carry ``input_tokens``/``output_tokens``
    keys, and they sum to 2,922 across the entire corpus -- groq's 1,784 calls
    report zero, deepseek's 7 report zero. The fields exist and are not filled.
    A split computed on that basis would report Pool B as costing nothing,
    which is a claim about the instrument, not about the world. So the token
    basis is marked unusable and the calls basis is used instead.
    """

    calls_by_vendor: dict[str, int] = field(default_factory=dict)
    tokens_by_vendor: dict[str, int] = field(default_factory=dict)
    records: int = 0
    records_with_tokens: int = 0
    malformed_lines: int = 0

    #: A ledger is treated as populated only if MOST records carry a count.
    POPULATED_COVERAGE_FLOOR = 0.5

    @property
    def total_tokens(self) -> int:
        return sum(self.tokens_by_vendor.values())

    @property
    def total_calls(self) -> int:
        return sum(self.calls_by_vendor.values())

    @property
    def token_coverage(self) -> float:
        """Fraction of records that carry a nonzero token count."""
        return (self.records_with_tokens / self.records) if self.records else 0.0

    @property
    def tokens_populated(self) -> bool:
        """True only if MOST records carry a count -- coverage, never a total.

        An earlier version of this tested ``total_tokens >= records``, and it
        FAILED on the live ledger, silently, in exactly the case it exists to
        catch. Measured 2026-09-17: 2,165 records, of which **12 (0.55%)**
        carry any token count -- yet those twelve sum to 2,922, which clears a
        one-token-per-record floor and reported the ledger as populated.

        A handful of real rows will always drag a mean over a low bar. The
        share of rows carrying data cannot be dragged that way, which is why
        this measures coverage instead.
        """
        return self.records > 0 and self.token_coverage >= self.POPULATED_COVERAGE_FLOOR


def read_ledger(path: Path) -> LedgerTotals:
    """Read Pool B totals from the usage ledger, by vendor.

    Counts calls and tokens separately because, on the live ledger, only one of
    those two is actually populated.
    """
    totals = LedgerTotals()
    for rec in _iter_records(path):
        if rec is None:
            totals.malformed_lines += 1
            continue
        totals.records += 1
        vendor = normalise_provider(rec.get("provider") or rec.get("vendor"))
        totals.calls_by_vendor[vendor] = totals.calls_by_vendor.get(vendor, 0) + 1
        tokens = _int(rec.get("input_tokens")) + _int(rec.get("output_tokens"))
        totals.tokens_by_vendor[vendor] = totals.tokens_by_vendor.get(vendor, 0) + tokens
        if tokens > 0:
            totals.records_with_tokens += 1
    return totals


@dataclass
class ModelUsage:
    """Real, API-reported token counts for one model id. Never an estimate."""

    model: str
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def vendor(self) -> str:
        return vendor_of(self.model)

    @property
    def total_tokens(self) -> int:
        """Every token the API reported moving, cache included."""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )

    def add(self, usage: dict) -> None:
        self.requests += 1
        self.input_tokens += _int(usage.get("input_tokens"))
        self.output_tokens += _int(usage.get("output_tokens"))
        self.cache_creation_tokens += _int(usage.get("cache_creation_input_tokens"))
        self.cache_read_tokens += _int(usage.get("cache_read_input_tokens"))

    def merge(self, other: "ModelUsage") -> None:
        self.requests += other.requests
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_creation_tokens += other.cache_creation_tokens
        self.cache_read_tokens += other.cache_read_tokens

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "vendor": self.vendor,
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "total_tokens": self.total_tokens,
        }


def _int(value) -> int:
    """Coerce a usage field to int, treating anything unusable as zero."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


@dataclass
class Attribution:
    """Pool A totals, plus the residues that make the number falsifiable."""

    models: dict[str, ModelUsage] = field(default_factory=dict)
    transcripts: int = 0
    malformed_lines: int = 0
    records_without_timestamp: int = 0
    synthetic_records: int = 0

    def record(self, model_id: str, usage: dict) -> None:
        bucket = self.models.get(model_id)
        if bucket is None:
            bucket = ModelUsage(model=model_id)
            self.models[model_id] = bucket
        bucket.add(usage)

    def merge(self, other: "Attribution") -> None:
        for model_id, usage in other.models.items():
            mine = self.models.get(model_id)
            if mine is None:
                self.models[model_id] = ModelUsage(model=model_id)
                mine = self.models[model_id]
            mine.merge(usage)
        self.transcripts += other.transcripts
        self.malformed_lines += other.malformed_lines
        self.records_without_timestamp += other.records_without_timestamp
        self.synthetic_records += other.synthetic_records

    @property
    def total_tokens(self) -> int:
        return sum(m.total_tokens for m in self.models.values())

    def tokens_by_vendor(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for usage in self.models.values():
            out[usage.vendor] = out.get(usage.vendor, 0) + usage.total_tokens
        return out

    @property
    def total_requests(self) -> int:
        return sum(m.requests for m in self.models.values())

    def calls_by_vendor(self) -> dict[str, int]:
        """Request counts per vendor — the basis that is actually recorded on
        BOTH sides of the pool boundary, unlike tokens."""
        out: dict[str, int] = {}
        for usage in self.models.values():
            out[usage.vendor] = out.get(usage.vendor, 0) + usage.requests
        return out


def _iter_records(path: Path) -> Iterator[dict | None]:
    """Yield each JSON record; ``None`` for a line that will not parse.

    A malformed line never aborts the walk -- a partially written transcript
    from a live session is normal, and refusing the whole file over one bad
    line would silently drop a session's entire spend.
    """
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                yield None
                continue
            yield obj if isinstance(obj, dict) else None


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def attribute_transcript(path: Path, since: datetime | None = None) -> Attribution:
    """Count real token spend per model in one transcript.

    Sidechain (subagent) records are COUNTED, unlike in
    ``lib/orchestrator_budget.py``. A subagent's tokens do not touch the
    orchestrator's context window but are real spend, and excluding them
    would under-report Pool A by exactly the fan-out this project relies on.
    """
    result = Attribution(transcripts=1)
    for rec in _iter_records(path):
        if rec is None:
            result.malformed_lines += 1
            continue
        message = rec.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        model_id = message.get("model")
        if not is_real_call(model_id):
            if isinstance(model_id, str) and model_id.strip() == SYNTHETIC:
                result.synthetic_records += 1
            continue
        stamp = _parse_ts(rec.get("timestamp"))
        if stamp is None:
            result.records_without_timestamp += 1
        elif since is not None and stamp < since:
            continue
        result.record(str(model_id).strip(), usage)
    return result


def attribute_tree(
    root: Path,
    since: datetime | None = None,
    paths: Iterable[Path] | None = None,
) -> Attribution:
    """Aggregate every transcript beneath ``root`` (or an explicit path list)."""
    total = Attribution()
    candidates = sorted(paths) if paths is not None else sorted(root.rglob("*.jsonl"))
    for path in candidates:
        try:
            total.merge(attribute_transcript(path, since=since))
        except OSError:
            continue
    return total


def split(
    pool_a: Attribution,
    pool_b_tokens: dict[str, int] | None = None,
    pool_b_calls: dict[str, int] | None = None,
) -> dict:
    """Combine Pool A (this module) with Pool B (the usage ledger).

    ``pool_b_tokens`` and ``pool_b_calls`` map vendor -> count, as reported by
    ``lib/llm_usage_ledger``. They are ARGUMENTS rather than something computed
    here because this module cannot see Pool B, and a number invented for the
    half you cannot observe is worse than no number.

    Both bases are returned because only one of them is trustworthy on the live
    data. Tokens are recorded on the Pool A side and effectively absent on the
    Pool B side; calls are recorded on both. The caller decides which to quote,
    and :func:`render` refuses to quote the token basis when Pool B's token
    field is unpopulated.

    Returns zero fractions rather than raising when nothing has been observed,
    so an empty corpus reports as empty instead of as a division error.
    """
    tokens = dict(pool_a.tokens_by_vendor())
    for vendor, count in (pool_b_tokens or {}).items():
        tokens[vendor] = tokens.get(vendor, 0) + int(count)
    calls = dict(pool_a.calls_by_vendor())
    for vendor, count in (pool_b_calls or {}).items():
        calls[vendor] = calls.get(vendor, 0) + int(count)

    tok_total = sum(tokens.values())
    tok_anthropic = tokens.get(ANTHROPIC, 0)
    call_total = sum(calls.values())
    call_anthropic = calls.get(ANTHROPIC, 0)

    return {
        "tokens_by_vendor": tokens,
        "total_tokens": tok_total,
        "anthropic_tokens": tok_anthropic,
        "non_anthropic_tokens": tok_total - tok_anthropic,
        "anthropic_fraction": (tok_anthropic / tok_total) if tok_total else 0.0,
        "non_anthropic_fraction": ((tok_total - tok_anthropic) / tok_total) if tok_total else 0.0,
        "calls_by_vendor": calls,
        "total_calls": call_total,
        "anthropic_calls": call_anthropic,
        "non_anthropic_calls": call_total - call_anthropic,
        "non_anthropic_call_fraction": ((call_total - call_anthropic) / call_total) if call_total else 0.0,
        # Reported separately because "not recognised" is not evidence of
        # "not Anthropic". These sit inside non_anthropic_calls by arithmetic,
        # so the reader needs to see how much of that total is merely
        # unclassified — an unrecognised Anthropic model would otherwise
        # flatter this metric, which the module docstring forbids.
        "unclassified_calls": calls.get(VENDOR_UNKNOWN, 0),
        "unclassified_call_fraction": (calls.get(VENDOR_UNKNOWN, 0) / call_total) if call_total else 0.0,
        "pool_b_supplied": bool(pool_b_tokens or pool_b_calls),
    }


def render(
    attribution: Attribution,
    combined: dict,
    ledger: LedgerTotals | None = None,
) -> str:
    """A plain-text report that states its own coverage, never just a number.

    Quotes the CALLS basis as the headline and refuses to quote the token
    basis whenever Pool B's token field is unpopulated, because a token split
    computed against an unfilled field reports Pool B as free.
    """
    lines: list[str] = []
    lines.append(f"Pool A (Claude Code transcripts): {attribution.transcripts} transcripts")
    lines.append("")
    lines.append(f"  {'model':<26} {'requests':>9} {'total tokens':>16}")
    for usage in sorted(
        attribution.models.values(), key=lambda m: m.total_tokens, reverse=True
    ):
        lines.append(f"  {usage.model:<26} {usage.requests:>9,} {usage.total_tokens:>16,}")
    lines.append(f"  {'':<26} {attribution.total_requests:>9,} {attribution.total_tokens:>16,}")
    lines.append("")

    if not combined["pool_b_supplied"]:
        lines.append("  Pool B NOT SUPPLIED - this is Pool A only, not a split.")
        lines.append("  Pass --ledger to read the usage ledger and compute the split.")
    else:
        lines.append(f"Pool B (usage ledger): {ledger.records if ledger else 0} records")
        for vendor, calls in sorted(
            (combined.get("calls_by_vendor") or {}).items(),
            key=lambda kv: kv[1], reverse=True,
        ):
            lines.append(f"  {vendor:<26} {calls:>9,} calls")
        lines.append("")
        pct = combined["non_anthropic_call_fraction"] * 100.0
        lines.append(f"  NON-ANTHROPIC SHARE OF ALL MODEL CALLS: {pct:.2f}%")
        lines.append(
            f"  ({combined['non_anthropic_calls']:,} of {combined['total_calls']:,} calls)"
        )
        if combined.get("unclassified_calls"):
            lines.append(
                f"  of which UNCLASSIFIED: {combined['unclassified_calls']:,} calls "
                f"({combined['unclassified_call_fraction']:.2%}) — model ids not"
            )
            lines.append(
                "  recognised by vendor_of. Not recognised is not evidence of not Anthropic."
            )
        if ledger is not None and not ledger.tokens_populated:
            lines.append("")
            lines.append("  TOKEN BASIS UNUSABLE - the ledger declares input_tokens and")
            lines.append(f"  output_tokens, but only {ledger.records_with_tokens:,} of "
                         f"{ledger.records:,} records carry a count "
                         f"({ledger.token_coverage:.2%} coverage).")
            lines.append("  A token split against an unfilled field reports Pool B as free.")
            lines.append("  The calls figure above is the only basis recorded on both sides.")

    lines.append("")
    lines.append(
        f"  residues: {attribution.malformed_lines} malformed lines, "
        f"{attribution.synthetic_records} synthetic records excluded, "
        f"{attribution.records_without_timestamp} records without a timestamp"
    )
    return "\n".join(lines)


def _default_root() -> Path:
    import os

    base = os.environ.get("CLAUDE_DIR") or str(Path.home() / ".claude")
    return Path(base) / "projects"


def _build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog="model_attribution",
        description="Real token spend per model across Claude Code transcripts (Pool A).",
    )
    parser.add_argument("--root", type=Path, default=None,
                        help="transcript root (default: $CLAUDE_DIR/projects)")
    parser.add_argument("--since", default=None,
                        help="ISO date/datetime; skip records older than this")
    parser.add_argument("--ledger", type=Path, default=None,
                        help="Pool B usage-ledger JSONL; without it this is Pool A only")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def as_json(attribution: Attribution, combined: dict, ledger: LedgerTotals | None) -> str:
    """The machine-readable form, carrying the same residues as the text form."""
    return json.dumps({
        "pool_a": {m: u.as_dict() for m, u in attribution.models.items()},
        "transcripts": attribution.transcripts,
        "malformed_lines": attribution.malformed_lines,
        "synthetic_records": attribution.synthetic_records,
        "pool_b": None if ledger is None else {
            "records": ledger.records,
            "records_with_tokens": ledger.records_with_tokens,
            "token_coverage": ledger.token_coverage,
            "calls_by_vendor": ledger.calls_by_vendor,
            "tokens_by_vendor": ledger.tokens_by_vendor,
            "tokens_populated": ledger.tokens_populated,
        },
        "combined": combined,
    }, indent=2)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    since = _parse_ts(args.since) if args.since else None
    if args.since and since is None:
        print(f"unparseable --since value: {args.since!r}", file=sys.stderr)
        return 2

    root = args.root or _default_root()
    if not root.exists():
        print(f"transcript root does not exist: {root}", file=sys.stderr)
        return 2
    if args.ledger is not None and not args.ledger.exists():
        print(f"ledger does not exist: {args.ledger}", file=sys.stderr)
        return 2

    ledger = read_ledger(args.ledger) if args.ledger is not None else None
    attribution = attribute_tree(root, since=since)
    combined = split(
        attribution,
        pool_b_tokens=ledger.tokens_by_vendor if ledger else None,
        pool_b_calls=ledger.calls_by_vendor if ledger else None,
    )
    print(as_json(attribution, combined, ledger) if args.json
          else render(attribution, combined, ledger))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
