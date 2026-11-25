"""Extract a fixed, exogenous judgement benchmark from GRIP's own post-mortems.

Every rule in ``rules/`` and every ``feedback_*`` memory record was written AFTER
a real failure: a situation arose, GRIP did something, that something was wrong.
Flip one and it becomes an eval item — present the SITUATION without the
governing rule and observe whether the DOCUMENTED FAILURE recurs.

Why this is a benchmark rather than a self-report
-------------------------------------------------
* **Fixed** — history does not change, so the item set is stable across runs.
* **Exogenous** — the failure is a matter of record, not a judgement GRIP makes
  about itself now.
* **Uncontaminated** — the corpus was authored as doctrine, never as an eval.
* **Capability, not activity** — it measures whether the rules actually BIND. A
  rule whose scenario still fails is a rule that is not working.

The split, and why it is shaped this way
----------------------------------------
The hard part is producing a ``situation`` that does not leak its own answer.
GRIP's Origin blocks are **diagnosis-shaped**: they were authored after the
fact and state the failure inline ("I rendered it as a 1-bit audio waveform").
No deterministic splitter can rewrite diagnosis into a clean task prompt —
that is generative work, and an LLM in the extractor would destroy
re-runnability.

So the extractor does not try. It **partitions** instead of rewriting:

    sentence contains a leak marker  ->  documented_failure   (council sees it)
    sentence contains none           ->  situation            (agent sees it)

The sentences dropped from ``situation`` because they give the answer away ARE
the documented failure, so one deterministic pass yields both fields and the
partition is principled rather than arbitrary.

An item survives only if what is left is still a usable scenario (word floor +
a concrete anchor). Everything else is SKIPPED with a recorded reason, per the
brief: a malformed eval item is worse than a missing one. Expect low yield.
That is the corpus being honest about its own shape, not a bug to tune away.

Determinism
-----------
No LLM, no network, no clock, no randomness. Same tree in, byte-identical
corpus out. File lists are sorted; the dev/holdout assignment is a stable hash
of the item id (never a shuffle, which would reshuffle the holdout every run
and destroy the guarantee); each item carries the sha256 of its source file so
a corpus can be re-verified against the tree it came from.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from claude_dir import get_claude_dir  # noqa: E402
from holistic_synthesiser import _is_private  # noqa: E402

__all__ = [
    "SCHEMA_VERSION",
    "HOLDOUT_PERCENT",
    "LEAK_MARKERS",
    "EvalItem",
    "Skip",
    "sentences",
    "split_on_leak",
    "assign_split",
    "extract_rule",
    "extract_memory",
    "rule_paths",
    "memory_paths",
    "build_corpus",
    "write_corpus",
]

SCHEMA_VERSION = "judgement-v1"
HOLDOUT_PERCENT = 20
MIN_SITUATION_WORDS = 25
MIN_FAILURE_WORDS = 4
MAX_FIELD_CHARS = 1800

# --------------------------------------------------------------------------
# Leak markers.
#
# A sentence carrying any of these is a sentence that tells the reader what
# went wrong or what the corrective rule is. It is therefore disqualified from
# `situation` and belongs in `documented_failure`.
#
# `\bI\b` is deliberately included and is the single strongest signal: GRIP's
# post-mortems switch to first person precisely to own the mistake.
# --------------------------------------------------------------------------
LEAK_MARKERS = (
    r"\bI\b",
    r"\bmy own\b",
    r"\bwrongly\b",
    r"\bfalsely\b",
    r"\bincorrectly\b",
    r"\bmistake\b",
    r"\bmis-?read\b",
    r"\bthe failure\b",
    r"\bfailed\b",
    r"\bdefect\b",
    r"\bshould have\b",
    r"\binstead of\b",
    r"\bnever\b",
    r"\balways\b",
    r"\bmust\b",
    r"\bverdict\b",
    r"\bdirective\b",
    r"\brejected\b",
    r"\bRCA\b",
    r"\broot cause\b",
    r"\bfixed by\b",
    r"\bratified\b",
    r"\brefuted\b",
    r"\bthe lesson\b",
    r"\bthe rule\b",
    r"\bthe law\b",
    r"\banti-pattern\b",
    r"\bshould not\b",
    r"\bwas false\b",
    r"\bis false\b",
    # Diagnosis tier. Evaluative vocabulary that only appears once the author
    # has stopped describing and started judging. Measured 2026-09-13: without
    # these, `rule:gh-repo-resolution` kept "bare `gh` silently targets HAL —
    # the wrong repo. Worst failure: ..." inside `situation`, handing the agent
    # under test the exact answer it was supposed to be tested on.
    r"\bfailure\b",
    r"\bwrong\b",
    r"\bsilently\b",
    r"\bwast(ing|ed|e)\b",
    r"\bbug\b",
    r"\bbroke(n)?\b",
    r"\bincident\b",
    r"\bviolat\w*\b",
    r"\bregression\b",
    r"\bleak\w*\b",
    r"\bcorrupt\w*\b",
    r"\bunsafe\b",
    r"\brecur\w*\b",
    r"\boversight\b",
    r"\bmissed\b",
    r"\boverlooked\b",
    r"\bhidden\b",
    r"\bmisl\w+\b",
    r"\bdeadlock\w*\b",
    r"\bstale\b",
    r"\bexhaust\w*\b",
    r"\bblind\b",
    r"\bnear-miss\b",
    r"\bdid not\b",
)
LEAK_RE = re.compile("|".join(LEAK_MARKERS), re.IGNORECASE)

# A usable scenario names something concrete: a number, a code token, or a
# quotation. A sentence with none of those is atmosphere, not a setup.
ANCHOR_RE = re.compile(r"[0-9`\"“‘']")

ORIGIN_RE = re.compile(r"^Origin:\s*(.+?)(?=\n\s*\n)", re.MULTILINE | re.DOTALL)
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)

# Split on sentence-final punctuation only when the next token starts a new
# sentence (capital, quote, backtick, emphasis). Keeps `e.g.` and `N=3.` intact.
SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*\"“])")

LAW_HEADINGS = frozenset({
    "the law",
    "the rule",
    "the doctrine",
    "the principle",
    "the class",
    "the problem",
    "the failure shape",
    "mandate",
})


class EvalItem(NamedTuple):
    """One benchmark item. ``situation`` is shown to the agent under test;
    ``documented_failure`` is shown only to the exogenous scorer."""

    id: str
    situation: str
    documented_failure: str
    source_path: str
    source_kind: str
    rule_name: str
    source_sha: str
    split: str

    def as_dict(self) -> dict:
        return dict(self._asdict())


class Skip(NamedTuple):
    """A source that could not yield a well-formed item, and why."""

    id: str
    source_path: str
    source_kind: str
    reason: str


def _norm(text: str) -> str:
    """Collapse all whitespace runs to single spaces."""
    return " ".join(text.split())


def _clip(text: str, limit: int = MAX_FIELD_CHARS) -> str:
    """Truncate at a word boundary, deterministically."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip() + " ..."


def sentences(block: str) -> list[str]:
    """Split a normalised prose block into sentences."""
    norm = _norm(block)
    return [s for s in SENT_SPLIT_RE.split(norm) if s.strip()] if norm else []


def split_on_leak(block: str) -> tuple[str, str]:
    """Partition prose at the first sentence that names the mistake.

    Returns ``(situation, failure_account)`` where ``situation`` is the LEADING
    CONTIGUOUS run of clean sentences and ``failure_account`` is everything
    from the first leaking sentence onward.

    The prefix rule is what keeps a situation readable. Collecting every clean
    sentence wherever it occurred would splice non-adjacent fragments from
    across a long document into sentence-salad that reads like a scenario but
    describes none — malformed in exactly the way the brief warns against. A
    post-mortem also narrates in order, setup first and diagnosis after, so the
    prefix is both the coherent choice and the faithful one.
    """
    sents = sentences(block)
    for index, sentence in enumerate(sents):
        if LEAK_RE.search(sentence):
            return " ".join(sents[:index]), " ".join(sents[index:])
    return " ".join(sents), ""


def _section_lead(section: str) -> str:
    """First blockquote of a section, else its first paragraph."""
    quoted = [ln.lstrip("> ").strip() for ln in section.splitlines()
              if ln.lstrip().startswith(">")]
    if quoted:
        return _norm(" ".join(quoted))
    paragraphs = [p for p in section.split("\n\n") if p.strip()]
    return _norm(paragraphs[0]) if paragraphs else ""


def _law_text(body: str) -> str:
    """Normative core of the document: the lead of its first law-ish section."""
    for match in HEADING_RE.finditer(body):
        if match.group(1).strip().lower().lstrip("# ") not in LAW_HEADINGS:
            continue
        section = body[match.end():]
        nxt = re.search(r"^##\s", section, re.MULTILINE)
        return _section_lead(section[: nxt.start()] if nxt else section)
    return ""


def _frontmatter(text: str) -> dict[str, str]:
    """Parse the flat scalar keys of a YAML frontmatter block. Nested keys are
    ignored on purpose — only ``name`` and ``description`` are consumed."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    out: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip().strip("\"'")
    return out


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assign_split(item_id: str, holdout_percent: int = HOLDOUT_PERCENT) -> str:
    """Stable dev/holdout assignment from a hash of the id.

    Deliberately NOT a shuffle: a shuffle reassigns rows on every run, so a
    holdout row could silently migrate into dev between runs. Hashing the id
    makes the assignment a property of the item, reproducible forever.
    """
    bucket = int(_sha(item_id)[:8], 16) % 100
    return "holdout" if bucket < holdout_percent else "dev"


def _assemble(item_id: str, situation: str, failure: str, path: Path,
              kind: str, rule_name: str, raw: str) -> EvalItem | Skip:
    """Apply the quality floors, then build the item or explain the skip."""
    situation, failure = _norm(situation), _norm(failure)
    if len(situation.split()) < MIN_SITUATION_WORDS:
        return Skip(item_id, str(path), kind, "situation_too_thin")
    if not ANCHOR_RE.search(situation):
        return Skip(item_id, str(path), kind, "situation_has_no_concrete_anchor")
    if len(failure.split()) < MIN_FAILURE_WORDS:
        return Skip(item_id, str(path), kind, "no_documented_failure")
    return EvalItem(
        id=item_id,
        situation=_clip(situation),
        documented_failure=_clip(failure),
        source_path=str(path),
        source_kind=kind,
        rule_name=rule_name,
        source_sha=_sha(raw)[:16],
        split=assign_split(item_id),
    )


def extract_rule(path: Path) -> EvalItem | Skip:
    """Build an item from a ``rules/*.md`` post-mortem."""
    raw = path.read_text(encoding="utf-8")
    item_id = f"rule:{path.stem}"
    origin = ORIGIN_RE.search(raw)
    if not origin:
        return Skip(item_id, str(path), "rule", "no_origin_block")
    situation, leaked = split_on_leak(origin.group(1))
    failure = " ".join(p for p in (leaked, _law_text(raw)) if p)
    return _assemble(item_id, situation, failure, path, "rule", path.stem, raw)


def extract_memory(path: Path) -> EvalItem | Skip:
    """Build an item from a ``feedback_*.md`` memory record."""
    raw = path.read_text(encoding="utf-8")
    item_id = f"mem:{path.stem}"
    meta = _frontmatter(raw)
    body = FRONTMATTER_RE.sub("", raw, count=1)
    body = re.sub(r"^#\s+.*$", "", body, count=1, flags=re.MULTILINE)
    situation, leaked = split_on_leak(body)
    failure = " ".join(p for p in (meta.get("description", ""), leaked) if p)
    return _assemble(item_id, situation, failure, path, "memory",
                     meta.get("name", path.stem), raw)


def rule_paths(root: Path) -> list[Path]:
    """Every rule document in this worktree, sorted."""
    return sorted((root / "rules").glob("*.md"))


def _canonical_slug(claude_dir: Path) -> str:
    """Harness project-dir slug for the install path.

    ``~/.grip`` -> ``-Users-x--grip``. Derived from the resolved install
    path rather than hardcoded, per ``rules/path-portability.md``.
    """
    real = os.path.realpath(str(claude_dir))
    return real.replace("/", "-").replace(".", "-")


def memory_paths(claude_dir: Path | None = None) -> list[Path]:
    """Feedback records from the canonical project-memory directory.

    The corpus is REPLICATED across every project directory the harness has
    ever opened (measured 2026-09-13: 323 directories holding the same 234
    basenames, 68,328 files). Globbing ``projects/*/memory`` would therefore
    multiply the corpus ~323x, and which copy won would depend on which
    worktrees happen to exist — so the corpus would not be reproducible.
    Pinning the canonical directory makes it stable; a sorted first-seen
    fallback keeps this working on installs that lack it.
    """
    claude_dir = claude_dir or get_claude_dir()
    base = claude_dir / "projects"
    canonical = base / _canonical_slug(claude_dir) / "memory"
    if canonical.is_dir():
        return _public(sorted(canonical.glob("feedback_*.md")))
    seen: dict[str, Path] = {}
    for path in sorted(base.glob("*/memory/feedback_*.md")):
        seen.setdefault(path.name, path)
    return _public([seen[name] for name in sorted(seen)])


def _public(paths: list[Path]) -> list[Path]:
    """Drop records marked ``private: true``, which stay out of this tracked corpus as they stay out of the kernel."""
    return [p for p in paths if not _is_private(p.read_text(encoding="utf-8", errors="replace"))]


def build_corpus(root: Path, claude_dir: Path | None = None
                 ) -> tuple[list[EvalItem], list[Skip]]:
    """Extract every item from both corpora. Deterministic and re-runnable."""
    sources = ([(p, extract_rule) for p in rule_paths(root)]
               + [(p, extract_memory) for p in memory_paths(claude_dir)])
    results = [extractor(path) for path, extractor in sources]
    items = sorted((r for r in results if isinstance(r, EvalItem)),
                   key=lambda i: i.id)
    skipped = sorted((r for r in results if isinstance(r, Skip)),
                     key=lambda s: s.id)
    return items, skipped


def _tree_sha(root: Path) -> str:
    """Commit SHA the corpus was built from, or ``unknown`` outside a repo."""
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 - provenance is best-effort, never fatal
        return "unknown"


def _skip_reason_counts(skipped: list[Skip]) -> dict[str, int]:
    reasons: dict[str, int] = {}
    for skip in skipped:
        reasons[skip.reason] = reasons.get(skip.reason, 0) + 1
    return dict(sorted(reasons.items()))


def write_corpus(root: Path, out_path: Path, claude_dir: Path | None = None) -> dict:
    """Write the frozen corpus, its skip report, and its sibling meta file."""
    items, skipped = build_corpus(root, claude_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "".join(json.dumps(i.as_dict(), sort_keys=True, ensure_ascii=False) + "\n"
                for i in items), encoding="utf-8")
    (out_path.parent / "judgement-v1.skipped.jsonl").write_text(
        "".join(json.dumps(dict(s._asdict()), sort_keys=True, ensure_ascii=False) + "\n"
                for s in skipped), encoding="utf-8")

    meta = {
        "schema": SCHEMA_VERSION,
        "grip_sha": _tree_sha(root),
        "counts": {
            "extracted": len(items),
            "skipped": len(skipped),
            "dev": sum(1 for i in items if i.split == "dev"),
            "holdout": sum(1 for i in items if i.split == "holdout"),
            "rule": sum(1 for i in items if i.source_kind == "rule"),
            "memory": sum(1 for i in items if i.source_kind == "memory"),
        },
        "holdout_percent": HOLDOUT_PERCENT,
        "skip_reasons": _skip_reason_counts(skipped),
        "corpus_sha256": _sha(out_path.read_text(encoding="utf-8")),
    }
    out_path.with_suffix(".meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the judgement eval corpus.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                        help="worktree root holding rules/")
    parser.add_argument("--out", default=None, help="output .jsonl path")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    out = Path(args.out) if args.out else root / "eval" / "judgement-v1.jsonl"
    print(json.dumps(write_corpus(root, out), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
