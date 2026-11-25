"""Run a divergent descent and persist the WakeReport the launch card reads.

``lib/inception.py`` builds and sweeps a :class:`~inception.WakeReport`, and
``lib/grip_launch_card.py`` knows how to render one, but nothing ever wrote one
to disk. The card's ``dreamt overnight`` band has therefore always shown its
honest empty state — "no run recorded yet, nothing to show, and nothing
invented". This module is the missing writer, and only the writer: it owns
generation and persistence, and delegates every judgement about what survives
to ``inception.limbo_sweep``.

Three properties are deliberate.

**Generation is injected.** ``descend`` takes a ``generate`` callable, so the
Pool-B council used in production is one implementation among others and the
whole cycle runs in tests as a pure function, with no provider call and no
network.

**The parser fails closed on shape, and only on shape.** A voice that returns
prose instead of the requested JSON yields no ideas at all rather than a partial
rescue. But an idea that parses and then fails the gate — no falsifier, an
anchor pointing at nothing — is passed through intact for ``limbo_sweep`` to
reject, because a gate cannot measure what the reader in front of it has already
thrown away. Filtering admission conditions in the parser was measured turning a
real night's survival rate into a constant 100%.

**Cost is bounded mechanically.** ``descend`` calls ``generate`` once per
frontier idea per level, which compounds fast: two seeds over three levels at a
fanout of three is twenty-six council dispatches. The generator therefore
carries a hard call budget and returns nothing once it is spent, which ends the
descent through ``descend``'s own empty-level break rather than through a second
competing stop mechanism.

Remember the success criterion is inverted. A healthy night is one where MOST of
what was dreamt is thrown away; a run that admits nearly everything is not
diverging, it is anchoring trivially. See ``WakeReport.is_healthy``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from inception import (  # noqa: E402
    INTERNAL,
    USER_FACING,
    AnchorResolver,
    Idea,
    WakeReport,
    default_anchor_resolver,
    descend,
)

__all__ = [
    "WAKE_PATH",
    "LIMBO_PATH",
    "wake_path",
    "limbo_path",
    "parse_ideas",
    "discover_seed_topics",
    "build_seeds",
    "PoolBGenerator",
    "run_wake",
    "write_wake",
    "record_limbo",
    "main",
]

# Resolved exactly as lib/grip_launch_card.py resolves it, so the writer and the
# reader can never disagree about where the report lives.
_GRIP_DEFAULT = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
WAKE_PATH = _GRIP_DEFAULT / "state/inception/wake-latest.json"
# The limbo ledger lives beside the wake report: one is what survived the night,
# the other is what the sweep threw away and is still worth remembering.
LIMBO_PATH = _GRIP_DEFAULT / "state/inception/limbo-ledger.jsonl"

_DOMAINS = {USER_FACING, INTERNAL}

# The generation contract. Stated as a schema rather than a description because
# the parser below rejects anything that is not this shape, and a voice that is
# told the shape loosely will supply it loosely.
#
# `measured:` is deliberately NOT advertised here even though the resolver
# accepts it. That anchor passes on SHAPE alone, so `measured:<any words>` is
# self-certifying: a voice told it is available can satisfy grounding by
# asserting a measurement nobody took. It was measured being used that way — two
# of the first four ideas a real night produced reached for it. File and test
# anchors are the only ones this resolver actually confirms against a checkout,
# so those are what the contract asks for.
_IDEA_CONTRACT = """\
Return ONLY a JSON array, no prose and no code fence. Each element must be:

  {"text": "<one concrete idea, <=200 chars>",
   "evidence": ["<anchor>", ...],
   "falsifier": "<what observation would prove this idea wrong>",
   "domain": "user-facing" | "internal"}

An anchor must be one of these EXACT shapes, and must name something that really
exists in the repository. Prefer the file and test shapes: those are checked
against the actual checkout, so a path you invent will be caught.
  path/to/file.py          a real file
  path/to/file.py:120      a real file with at least that many lines
  test:tests/test_x.py     a real test file
  #4238                    a real GitHub issue or pull request number

Rules that decide admission, so do not treat them as style notes:
  - An idea with no falsifier is discarded. Say what would prove it wrong.
  - An idea whose evidence names nothing real is discarded. Do not invent paths.
  - Cite what you are actually improving, not the issue you were handed.
  - Prefer "user-facing" when the operator would SEE or FEEL the change.
  - Diverge. Obvious safe suggestions are worth less than grounded odd ones."""


def wake_path(root: Optional[Path] = None) -> Path:
    """Where the wake report lives for a given GRIP root."""
    if root is None:
        return WAKE_PATH
    return Path(root) / "state/inception/wake-latest.json"


def limbo_path(root: Optional[Path] = None) -> Path:
    """Where the limbo ledger lives for a given GRIP root."""
    if root is None:
        return LIMBO_PATH
    return Path(root) / "state/inception/limbo-ledger.jsonl"


# ----------------------------------------------------------------- parsing

def _strip_fence(text: str) -> str:
    """Drop a surrounding markdown code fence if a voice added one anyway."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    end = body.rfind("```")
    return (body[:end] if end != -1 else body).strip()


def _extract_json_array(text: str) -> list:
    """Return the first JSON array in ``text``, or [] when there is not one.

    Fails closed on every error path. Unparseable output is not a crash and not
    a partial rescue; it simply produced no ideas.
    """
    candidate = _strip_fence(text or "")
    start = candidate.find("[")
    end = candidate.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        parsed = json.loads(candidate[start:end + 1])
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _clean_evidence(raw) -> tuple[str, ...]:
    """Normalise an evidence list to a tuple of non-empty strings."""
    if not isinstance(raw, list):
        return ()
    return tuple(
        item.strip() for item in raw
        if isinstance(item, str) and item.strip()
    )


def _normalise_domain(raw) -> str:
    """Read a stated domain through whitespace and casing drift.

    ``"User-Facing "`` means user-facing. Matching the raw string against the
    constants sent that idea to INTERNAL instead, which is not a stricter gate,
    just a misread: it silently corrupts ``domain_mix`` and ``is_balanced()``,
    the checks that exist to notice a night dreaming only about the machine.

    This does not weaken the INTERNAL default. That default exists so
    USER_FACING cannot be acquired by ACCIDENT — a voice that says nothing, or
    says something unrecognisable, still lands on INTERNAL. Trimming a trailing
    space is reading what was said, not being generous about what was meant,
    and it matches how ``text`` and ``falsifier`` are already handled.
    """
    if not isinstance(raw, str):
        return INTERNAL
    cleaned = raw.strip().lower()
    return cleaned if cleaned in _DOMAINS else INTERNAL


def parse_ideas(
    payload: str, parent: Idea, level: int, *, limit: int = 3,
) -> list[Idea]:
    """Turn one voice's answer into Ideas, refusing only what it cannot read.

    An entry needs a non-empty ``text``, because an idea with no text is not an
    idea. Nothing else is filtered here — a missing falsifier and an anchor that
    resolves to nothing are both passed straight through as they were produced.

    That restraint is load-bearing rather than lazy. ``limbo_sweep`` is the gate,
    and a gate cannot measure anything if the reader in front of it has already
    thrown away every failing case. Filtering on the falsifier here was measured
    doing exactly that: with grounding inherited from a grounded seed, and the
    falsifier pre-enforced, both admission conditions were satisfied before the
    sweep ran and a real Pool-B night reported 100% survival — a survival rate
    that describes this parser, not the ideas.
    """
    out: list[Idea] = []
    for n, entry in enumerate(_extract_json_array(payload)):
        if len(out) >= limit:
            break
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        raw_falsifier = entry.get("falsifier")
        falsifier = (raw_falsifier.strip()
                     if isinstance(raw_falsifier, str) and raw_falsifier.strip()
                     else None)
        out.append(Idea(
            id=f"{parent.id}.{level}.{n}",
            text=text.strip(),
            level=level,
            evidence=_clean_evidence(entry.get("evidence")),
            parent_id=parent.id,
            falsifier=falsifier,
            domain=_normalise_domain(entry.get("domain")),
        ))
    return out


# ------------------------------------------------------------------- seeds

_ISSUE_TITLE_MAX = 160


def _gh_open_issues(repo_root: Path, limit: int) -> list[tuple[int, str]]:
    """Open issues as (number, title), or [] when gh is unavailable.

    Fail-soft on purpose. A missing or unauthenticated ``gh`` degrades the seed
    quality; it must never sink the run.
    """
    try:
        proc = subprocess.run(
            ["gh", "issue", "list", "--state", "open", "--limit", str(limit),
             "--json", "number,title"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=25,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    try:
        rows = json.loads(proc.stdout or "[]")
    except ValueError:
        return []
    return [
        (int(r["number"]), str(r.get("title", ""))[:_ISSUE_TITLE_MAX])
        for r in rows
        if isinstance(r, dict) and isinstance(r.get("number"), int)
    ]


def _fallback_seed_anchor(repo_root: Path) -> Optional[str]:
    """A real in-repo path to anchor seeds on when gh gives us nothing."""
    for candidate in ("lib/inception.py", "lib/grip_launch_card.py", "CLAUDE.md"):
        if (Path(repo_root) / candidate).is_file():
            return candidate
    return None


def discover_seed_topics(
    repo_root: Path, *, limit: int = 2,
) -> list[tuple[str, tuple[str, ...]]]:
    """Real, currently-open work to start the descent from.

    Seeds come from live repository state rather than a hardcoded list, so a
    night reflects what is actually open. Each returned topic carries evidence
    that genuinely resolves, which is what lets a seed survive its own sweep.
    """
    topics: list[tuple[str, tuple[str, ...]]] = []
    for number, title in _gh_open_issues(repo_root, limit)[:limit]:
        topics.append((
            f"Open issue #{number}: {title}".strip(),
            (f"#{number}",),
        ))
    if topics:
        return topics
    anchor = _fallback_seed_anchor(repo_root)
    if anchor is None:
        return []
    return [(
        f"Improve what {anchor} already does, using only what it demonstrably does today",
        (anchor,),
    )]


def build_seeds(
    topics: Sequence[tuple[str, tuple[str, ...]]],
) -> list[Idea]:
    """Level-0 Ideas for a descent.

    Seeds are swept alongside everything they generate, so they are given real
    falsifiers rather than being exempted. A seed that cannot state how it would
    be proven wrong belongs in limbo exactly as much as a dreamt one does.
    """
    return [
        Idea(
            id=f"seed-{n}",
            text=text,
            level=0,
            evidence=evidence,
            parent_id=None,
            falsifier=(
                "No change to this area survives review, or the work it names "
                "turns out to be already done on origin/main"
            ),
            domain=INTERNAL,
        )
        for n, (text, evidence) in enumerate(topics)
    ]


# --------------------------------------------------------------- generation

class PoolBGenerator:
    """A ``generate`` callable backed by a Pool-B multi-provider council.

    Pool-B (non-Anthropic providers through the HAL seam) costs nothing and does
    not consume the weekly Anthropic limit, which is what makes an overnight run
    affordable in the first place.

    The call budget is the whole cost-control story. ``descend`` fans out per
    frontier idea per level, so an unbounded generator compounds into dozens of
    council dispatches. Once ``budget`` is spent this returns an empty list,
    which ``descend`` reads as an exhausted level and stops on.
    """

    def __init__(
        self,
        *,
        budget: int = 3,
        fanout: int = 3,
        roster_name: str = "open",
        timeout: int = 120,
        context: str = "",
        ideate: Optional[Callable[..., list]] = None,
        rosters: Optional[dict] = None,
    ) -> None:
        self.budget = int(budget)
        self.fanout = int(fanout)
        self.roster_name = roster_name
        self.timeout = int(timeout)
        self.context = context
        self.calls = 0
        self.voices_ok = 0
        self.voices_failed = 0
        self._ideate = ideate
        self._rosters = rosters

    def _load_council(self) -> tuple[Callable[..., list], dict]:
        """Resolve the ideation seam lazily so importing this module is cheap.

        The failure is named rather than left as a bare traceback. This import
        only runs on the un-injected production path, so when it fails the
        operator is running the CLI on an install where the Pool-B seam is not
        present — and a raw ImportError several frames deep reads as a bug in
        the descent rather than as a missing dependency with an obvious remedy.
        """
        if self._ideate is not None and self._rosters is not None:
            return self._ideate, self._rosters
        try:
            from hal_council_ideate import ROSTERS, ideate  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "Pool-B generation needs lib/hal_council_ideate.py (and the HAL "
                f"seam it dispatches through), which is not importable: {exc}. "
                "Install or restore HAL, or supply your own generator — "
                "PoolBGenerator(ideate=..., rosters=...) and run_wake(generate=...) "
                "both accept one, which is how the tests run without a provider."
            ) from exc
        return ideate, ROSTERS

    def _question(self, idea: Idea, depth: int) -> str:
        return (
            f"Dream {self.fanout} concrete, grounded next ideas that descend from "
            f"this one (level {depth}).\n\nIDEA: {idea.text}\n"
            f"ITS EVIDENCE: {', '.join(idea.evidence) or '(none)'}\n\n"
            f"{_IDEA_CONTRACT}"
        )

    def __call__(self, idea: Idea, depth: int) -> list[Idea]:
        if self.calls >= self.budget:
            return []
        self.calls += 1
        ideate, rosters = self._load_council()
        voices = ideate(
            self._question(idea, depth),
            context=self.context,
            roster=rosters.get(self.roster_name),
            timeout=self.timeout,
        )
        produced: list[Idea] = []
        for voice in voices:
            if not getattr(voice, "ok", False):
                self.voices_failed += 1
                continue
            self.voices_ok += 1
            produced.extend(parse_ideas(
                voice.answer, idea, depth, limit=self.fanout,
            ))
        return produced[:self.fanout]


# ---------------------------------------------------------------- the cycle

def run_wake(
    generate: Callable[[Idea, int], list[Idea]],
    *,
    repo_root: Path,
    seeds: Optional[Iterable[Idea]] = None,
    resolver: Optional[AnchorResolver] = None,
    levels: int = 2,
) -> WakeReport:
    """Descend, sweep, and hand back the report. Persistence is separate."""
    if seeds is None:
        seeds = build_seeds(discover_seed_topics(repo_root))
    resolver = resolver or default_anchor_resolver(repo_root)
    return descend(list(seeds), generate, resolver, levels=levels)


def write_wake(
    report: WakeReport,
    path: Optional[Path] = None,
    *,
    meta: Optional[dict] = None,
) -> Path:
    """Persist a report where the launch card reads it, atomically.

    The payload is ``WakeReport.to_dict()`` verbatim plus provenance, because
    the card spreads the file straight into its band and inventing keys here
    would put words in the report's mouth.

    The temp file gets a UNIQUE name per call. A fixed ``wake-latest.json.tmp``
    looks atomic and is not: two runs overlapping — a scheduled night and a
    manual one, or two sessions — write into the same scratch path, so their
    bytes interleave and whichever ``os.replace`` lands second publishes a
    corrupted mixture rather than either report. ``mkstemp`` creates the file
    atomically under a name no other process can hold, which reduces the worst
    case to last-writer-wins on a whole, valid document. Only the rename races,
    and rename is atomic.
    """
    target = Path(path) if path is not None else WAKE_PATH
    payload = dict(report.to_dict())
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    if meta:
        payload["run"] = meta
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f"{target.name}.", suffix=".tmp",
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        # mkstemp opens 0600; the card is read by ordinary tooling, so restore
        # the permissions a plain write would have produced.
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return target


def _root_seed(idea: Idea, by_id: dict) -> Idea:
    """The root seed an idea descends from, walking parent links to level 0.

    Grounding is the sweep's reachability test; attribution is the ledger's.
    Each limbo idea is charged to the independent seed that produced it — the
    open issue or fallback topic at the top of its chain — so recurrence counts
    origins, not sightings. A parent loop terminates on itself, so attribution
    can never recurse forever.
    """
    seen: set[str] = set()
    cur = idea
    while cur.parent_id and cur.parent_id in by_id and cur.parent_id not in seen:
        seen.add(cur.id)
        cur = by_id[cur.parent_id]
    return cur


def record_limbo(
    report: WakeReport,
    *,
    ledger_path: Optional[Path] = None,
    when: Optional[str] = None,
) -> "LimboLedger":
    """Record every limbo idea into the persistent ledger, keyed by its seed.

    This is the half that makes limbo survive instead of evaporate. The sweep
    throws unanchored ideas away; the ledger is where they accumulate, so a
    recurring-but-early idea can be told apart from a worthless one — the
    distinction a single night cannot make and recurrence across nights can.

    The seed attributed to an idea is its root seed's TEXT (the open issue or
    fallback topic it descends from), never its own text and never a raw
    sighting count: ten repeats from one seed is determinism, not corroboration.

    DENY_ALWAYS is respected here — a class the operator refused is silently
    suppressed by the ledger, never re-surfaced.
    """
    from inception_limbo import LimboLedger

    path = Path(ledger_path) if ledger_path is not None else LIMBO_PATH
    ledger = LimboLedger.load(path)
    by_id = {i.id: i for i in (*report.admitted, *report.limbo)}
    stamp = when or datetime.now(timezone.utc).isoformat()
    for idea in report.limbo:
        root = _root_seed(idea, by_id)
        ledger.record(idea.id, idea.text, root.text, when=stamp)
    ledger.save(path)
    return ledger


# ----------------------------------------------------------------- the CLI

def _verdict(report: WakeReport) -> str:
    """Name which end of the health band the run missed, not just that it did.

    The gate became two-sided in #4251, so "not healthy" now has two opposite
    meanings and one message cannot serve both. A run that admits everything is
    not diverging; a run that admits nothing produced no usable evidence. Those
    call for opposite responses, and collapsing them into TOO MUCH ADMITTED
    would have reported the 0/11 night as over-admitting.
    """
    if report.is_healthy():
        return "healthy - most of it was thrown away"
    if report.generated and not report.admitted:
        return "NOTHING SURVIVED - no idea reached a checkable anchor"
    if report.survival_rate < 0.05:
        return "ALMOST NOTHING SURVIVED - evidence quality is the problem"
    return "TOO MUCH ADMITTED - not really diverging"


def _summarise(report: WakeReport, target: Optional[Path]) -> str:
    rate = report.survival_rate * 100
    verdict = _verdict(report)
    where = f"\nwritten  {target}" if target else "\n(dry run, nothing written)"
    return (
        f"generated {report.generated} - admitted {len(report.admitted)} - "
        f"limbo {len(report.limbo)} - {rate:.0f}% survived - {verdict}"
        f"\nlevels   {report.levels}   balanced: {report.is_balanced()}"
        f"{where}"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a divergent descent and persist the WakeReport the launch card reads.",
    )
    parser.add_argument("--levels", type=int, default=2,
                        help="descent depth (default 2)")
    parser.add_argument("--budget", type=int, default=3,
                        help="hard cap on council dispatches (default 3)")
    parser.add_argument("--fanout", type=int, default=3,
                        help="ideas kept per dispatch (default 3)")
    parser.add_argument("--roster", default="open",
                        help="Pool-B roster name (default 'open', non-Anthropic)")
    parser.add_argument("--timeout", type=int, default=120,
                        help="per-provider timeout in seconds (default 120)")
    parser.add_argument("--repo-root", default=str(_GRIP_DEFAULT),
                        help="repository the anchors resolve against")
    parser.add_argument("--out", default=None,
                        help="output path (default state/inception/wake-latest.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="run the descent but write nothing")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).expanduser()
    generator = PoolBGenerator(
        budget=args.budget, fanout=args.fanout,
        roster_name=args.roster, timeout=args.timeout,
        context=f"Repository: {repo_root}. This is GRIP, a self-improving AI harness.",
    )
    report = run_wake(generator, repo_root=repo_root, levels=args.levels)

    target = None
    if not args.dry_run:
        target = write_wake(report, Path(args.out) if args.out else None, meta={
            "roster": args.roster,
            "dispatches": generator.calls,
            "voices_ok": generator.voices_ok,
            "voices_failed": generator.voices_failed,
            "pool": "B",
        })
        ledger = record_limbo(report)
        due = ledger.due_for_review()
        print(f"limbo    {len(ledger.entries)} entries recorded - "
              f"{len(due)} due for operator review")
    print(_summarise(report, target))
    print(f"council  {generator.calls} dispatches - "
          f"{generator.voices_ok} voices answered, {generator.voices_failed} failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
