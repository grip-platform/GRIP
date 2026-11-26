"""/mental-model -- one up-to-date picture of GRIP, composed from the readers it has.

Designed in GRIP#9967. V>> settled what it promises on 2026-11-26: "Contract -- as it
has to fit in with LSP." So /mental-model promises a CONTRACT, not coverage. Every
report keeps the rules below, whatever it covers, and so any report can stand in for
another (the Liskov rule). This version covers 11 sources on 6 axes and names every
source it leaves out, which makes it a valid sub-type of every later, fuller version.

The contract:

1. Every source reports one status from a closed set: OK, EMPTY, STALE, UNREADABLE,
   FORBIDDEN or NOT_RUN. Any status but OK prints a named gap line, never a bare 0.
2. Every age carries its kind: heartbeat, mtime, ledger-lag or fetched-now.
3. Session ids are joined on their first eight hex characters. No source in this
   version joins session ids, so there is no join to fail.
4. A list asks for LIST_LIMIT + 1 rows and says "at least LIST_LIMIT" when cut.
5. Privacy floor: every source's text passes ``lib/redaction_shield.py`` (profile
   ``public``) before it is composed, and all text is withheld when the shield cannot
   load. No reader opens a transcript body, ``state/whatsapp-history/`` or the body of
   a memory record; the memory reader measures the index's size only.
6. Every git fact names its tree or ref.
7. ``--json`` carries a manifest: the axes attempted, each source's status, and each
   source left out with its reason.
8. It never runs ``grip-anthropic-limits`` (it spends a little of every account) or
   ``grip-chain-anchor`` (it is not read-only).

It composes and re-implements no reader. It makes no model call. With the same reader
answers and the same clock, it prints the same bytes.

Each reader runs in its own thread with its own time limit (30 s, or 10 s for HAL's
gateway), so one slow source delays the report by at most that limit and is then
NOT_RUN. Every command a reader runs must finish within 90% of the reader's limit, and
none starts with less than 0.05 s of that time left (SlotSpent), so no command
outlives the report. A reader written in pure Python has no such stop, and Python
waits for its thread before the process exits; each such reader in this version
reads only local files.

CLI: ``bin/grip-mental-model [--json] [--axis AXIS ...]``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:  # run as a script, lib/ is sys.path[0] and the repo root is not
    sys.path.insert(0, str(_REPO))

from lib.claude_dir import get_claude_dir  # noqa: E402

VERSION = 1
STATUSES = ("OK", "EMPTY", "STALE", "UNREADABLE", "FORBIDDEN", "NOT_RUN")
AGE_KINDS = ("heartbeat", "mtime", "ledger-lag", "fetched-now")
AXES = ("sessions", "memory", "canonical", "harnesses", "decisions", "spend")
LIST_LIMIT = 50
_MAX_LIMIT_S = 86400  # a reader's time limit is at most one day
WITHHELD = "[withheld: the redaction shield could not load]"
HAL_GATEWAY_HEALTH = "http://127.0.0.1:4010/health"  # rules/port-registry.md: HAL is 4010

#: Sources GRIP#9967 names that this version does not compose, each with its reason.
LEFT_OUT: tuple[tuple[str, str], ...] = (
    ("sessions/roster", "its task field can carry transcript text, and no test yet pins its redaction"),
    ("memory/anamnesis", "it prints memory record text, and no test yet pins its redaction"),
    ("memory/recall", "grip-recall verify is not composed by this version"),
    ("memory/lint-stats", "grip-memory-lint --stats is not composed by this version"),
    ("memory/kono", "KONO stats take about a second and are not composed by this version"),
    ("canonical/public-mains", "the public mirrors' heads are not composed by this version"),
    ("canonical/newcomer-check", "the public newcomer-check runs are not composed by this version"),
    ("harnesses/accounts", "grip-account list is not composed by this version"),
    ("harnesses/launchd", "launchctl list for com.grip.* is not composed by this version"),
    ("harnesses/tailscale", "tailscale status is not composed by this version"),
    ("decisions/briefs", "the unexecuted-brief list is not composed by this version"),
)

#: Sources the contract forbids this report to run (item 8).
NEVER_RUN: tuple[tuple[str, str], ...] = (
    ("spend/anthropic-limits", "grip-anthropic-limits spends a little of every account"),
    ("decisions/chain-anchor", "grip-chain-anchor is not read-only"),
)


# --- the data -----------------------------------------------------------------

@dataclass(frozen=True)
class Source:
    """One source's answer, held to the contract by ``_checked``."""

    axis: str
    name: str
    status: str
    facts: tuple[tuple[str, str], ...] = ()
    age_kind: str = "fetched-now"
    age_s: int | None = None
    detail: str = ""

    @property
    def key(self) -> str:
        return f"{self.axis}/{self.name}"


def _http_status(url: str, timeout_s: float) -> int:
    with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310 -- a fixed local URL
        return int(response.status)


def _engine_status() -> tuple[dict, Path]:
    """The hypothesis engine's totals and the ledger it read them from."""
    from lib import hypothesis_engine

    return hypothesis_engine.status(), Path(hypothesis_engine.HYPOTHESIS_FILE)


def _default_memory_index() -> Path:
    from lib.holistic_synthesiser import default_substrate

    return Path(default_substrate()[1])


@dataclass(frozen=True)
class Context:
    """Everything a reader may use. Tests replace each callable with a fake.

    ``deadline`` is the monotonic time by which a reader's commands must stop;
    ``_run_one`` sets it from the reader's own time limit.
    """

    root: Path
    now: datetime
    run: Callable[..., Any] = subprocess.run
    http: Callable[[str, float], int] = _http_status
    hal_home: Path = field(default_factory=lambda: Path(os.environ.get("HAL_HOME") or Path.home() / ".hal"))
    hypothesis_status: Callable[[], tuple[dict, Path]] = _engine_status
    memory_index: Callable[[], Path] = _default_memory_index
    deadline: float | None = None


@dataclass(frozen=True)
class Reader:
    axis: str
    name: str
    read: Callable[[Context], Source]
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        limit = self.timeout_s
        numeric = isinstance(limit, (int, float)) and not isinstance(limit, bool)
        if not (numeric and 0 < limit <= _MAX_LIMIT_S):  # also refuses NaN, infinity and huge integers
            raise ValueError(f"{self.axis}/{self.name}: a time limit must be a number above 0 s and at most "
                             f"{_MAX_LIMIT_S} s, not {limit!r}")

    @property
    def key(self) -> str:
        return f"{self.axis}/{self.name}"


@dataclass(frozen=True)
class Report:
    generated_at: str
    axes: tuple[str, ...]
    sources: tuple[Source, ...]
    left_out: tuple[tuple[str, str], ...]
    never_run: tuple[tuple[str, str], ...]


# --- small helpers ------------------------------------------------------------

def _ok(axis: str, name: str, facts: Sequence[tuple[str, Any]], *, age_kind: str = "fetched-now",
        age_s: int | None = None) -> Source:
    return Source(axis, name, "OK", tuple((k, str(v)) for k, v in facts), age_kind, age_s)


def _gap(axis: str, name: str, status: str, detail: str, *, age_kind: str = "fetched-now",
         age_s: int | None = None) -> Source:
    return Source(axis, name, status, (), age_kind, age_s, detail)


def _age(ctx: Context, path: Path) -> int | None:
    try:
        return max(0, int(ctx.now.timestamp() - path.stat().st_mtime))
    except OSError:
        return None


def _tree_label(root: Path) -> str:
    home = str(Path.home())
    text = str(root)
    return "~" + text[len(home):] if text.startswith(home) else text


_SLOT_SHARE = 0.9  # a reader's commands must finish within this share of its time limit
_MIN_START_S = 0.05  # a command is not started with less time than this left in its slot


class SlotSpent(subprocess.TimeoutExpired):
    """A command was never started, because its reader's slot had too little time left."""


def _left(ctx: Context, wanted: float | None, cmd: Any) -> float | None:
    """A command's time limit, cut to what is left of its reader's slot. ``wanted`` None
    asks for no limit of its own. A slot with less than _MIN_START_S left starts nothing:
    it raises SlotSpent for ``cmd``, which the reader reports as NOT_RUN."""
    if ctx.deadline is None:
        return wanted
    left = ctx.deadline - time.monotonic()
    if left < _MIN_START_S:
        raise SlotSpent(cmd, 0)
    return left if wanted is None else min(wanted, left)


def _clamped(ctx: Context) -> Callable[..., Any]:
    """A runner that stops every command inside its reader's slot, so no command
    outlives the slot and none starts once the slot is spent."""
    def run(argv: list[str], **kwargs: Any):
        kwargs["timeout"] = _left(ctx, kwargs.get("timeout", 20.0), argv)
        return ctx.run(argv, **kwargs)
    return run


def _run(ctx: Context, argv: list[str], timeout_s: float = 20.0, **kwargs: Any):
    return _clamped(ctx)(argv, capture_output=True, text=True, timeout=timeout_s, **kwargs)


def _first_line(text: str) -> str:
    return (text or "").strip().splitlines()[0][:160] if (text or "").strip() else ""


def count_label(rows: int, limit: int = LIST_LIMIT) -> str:
    """Contract item 4: a list asked for ``limit + 1`` rows, so more than ``limit`` means cut."""
    return f"at least {limit}" if rows > limit else str(rows)


# --- the readers --------------------------------------------------------------

_TREE_STATUS = {"OK": "OK", "BEHIND": "STALE", "OFF_CANONICAL": "STALE"}


def read_tree(ctx: Context) -> Source:
    """Is the GRIP tree on main and level with origin/main? (``bin/grip-tree-check``)"""
    res = _run(ctx, [str(ctx.root / "bin" / "grip-tree-check"), "--tree", str(ctx.root), "--json"])
    data = json.loads(res.stdout or "{}")
    status = _TREE_STATUS.get(str(data.get("verdict")), "UNREADABLE")
    age = _age(ctx, ctx.root / ".git" / "FETCH_HEAD")
    if status != "OK":
        return _gap("canonical", "tree", status, f"{_tree_label(ctx.root)}: {data.get('detail') or 'no verdict'}",
                    age_kind="ledger-lag", age_s=age)
    facts = (("tree", _tree_label(ctx.root)), ("branch", data.get("branch", "")),
             ("head", data.get("head", "")), ("origin/main", data.get("canonical_head", "")))
    return _ok("canonical", "tree", facts, age_kind="ledger-lag", age_s=age)


def read_origin_main(ctx: Context) -> Source:
    """Does the local origin/main ref match the remote's main right now?"""
    remote = _run(ctx, ["git", "-C", str(ctx.root), "ls-remote", "origin", "refs/heads/main"])
    if remote.returncode != 0:
        return _gap("canonical", "origin-main", "UNREADABLE", f"git ls-remote: {_first_line(remote.stderr)}")
    remote_sha = (remote.stdout.split() or [""])[0]
    if not remote_sha:
        return _gap("canonical", "origin-main", "EMPTY", "the remote has no refs/heads/main")
    local = _run(ctx, ["git", "-C", str(ctx.root), "rev-parse", "origin/main"]).stdout.strip()
    facts = (("ref", "origin/main"), ("remote", remote_sha[:9]), ("local", local[:9]))
    if local != remote_sha:
        return _gap("canonical", "origin-main", "STALE",
                    f"origin/main is {local[:9] or 'missing'} here and {remote_sha[:9]} on the remote; git fetch")
    return _ok("canonical", "origin-main", facts, age_s=0)


def read_open_prs(ctx: Context) -> Source:
    """How many pull requests are open on the tree's GitHub origin?"""
    from lib import review_pass_rate

    repo = review_pass_rate.origin_repo(ctx.root, _clamped(ctx))
    res = _run(ctx, ["gh", "pr", "list", "--repo", repo, "--state", "open",
                     "--limit", str(LIST_LIMIT + 1), "--json", "number"], timeout_s=30.0)
    if res.returncode != 0:
        return _gap("canonical", "open-prs", "UNREADABLE", f"gh pr list: {_first_line(res.stderr)}")
    rows = json.loads(res.stdout or "[]")
    if not rows:
        return _gap("canonical", "open-prs", "EMPTY", f"{repo} has no open pull requests")
    return _ok("canonical", "open-prs", (("repo", repo), ("open", count_label(len(rows)))), age_s=0)


#: The per-session file suffix ``lib/context_head._session_path`` inserts: the
#: session's first four hex characters. It names files, so it is not a session-id join
#: (contract item 3 covers joins). A family whose own name ends in four hex letters,
#: such as ``-beef``, would be grouped with the sessions; none did on 2026-11-26.
_SESSION_SUFFIX = re.compile(r"-[0-9a-f]{4}$")


def _newest(ages: Sequence[int | None]) -> int | None:
    """The smallest known age, or None when no age could be read (never a made-up 0)."""
    known = [a for a in ages if a is not None]
    return min(known) if known else None


def read_chains(ctx: Context) -> Source:
    """Every causal-chain ledger family under state/precog/, by file name only."""
    files = sorted((ctx.root / "state" / "precog").glob("*.jsonl"))
    if not files:
        return _gap("decisions", "chains", "EMPTY", "no ledger under state/precog/", age_kind="mtime")
    families: dict[str, list[int | None]] = {}
    for path in files:
        families.setdefault(_SESSION_SUFFIX.sub("-<session>", path.stem), []).append(_age(ctx, path))
    facts = [(family, f"{len(ages)} file(s), newest {_fmt_age(_newest(ages))} old")
             for family, ages in sorted(families.items())]
    return _ok("decisions", "chains", facts, age_kind="mtime",
               age_s=_newest([_newest(ages) for ages in families.values()]))


def read_hypotheses(ctx: Context) -> Source:
    """The hypothesis ledger's totals (``hypothesis_engine.status``)."""
    summary, ledger = ctx.hypothesis_status()
    if not summary.get("total"):
        return _gap("decisions", "hypotheses", "EMPTY", "the hypothesis ledger holds none", age_kind="mtime")
    by_status = summary.get("by_status") or {}
    facts = [("total", summary["total"]), *sorted(by_status.items()),
             ("due now", summary.get("due_for_verification", 0))]
    return _ok("decisions", "hypotheses", facts, age_kind="mtime", age_s=_age(ctx, ledger))


def read_kernel(ctx: Context) -> Source:
    """Does the memory kernel name every PARAMOUNT and feedback record? (``bin/holistic verify``)"""
    res = _run(ctx, [str(ctx.root / "bin" / "holistic"), "verify"],
               env={**os.environ, "CLAUDE_DIR": str(ctx.root)})
    said = f"{res.stdout or ''}{res.stderr or ''}"
    age = _age(ctx, ctx.root / "rules" / "HOLISTIC.md")
    if res.returncode != 0 or "VERIFY OK" not in said:
        return _gap("memory", "kernel", "STALE", f"holistic verify: {_first_line(said) or 'no answer'}",
                    age_kind="mtime", age_s=age)
    return _ok("memory", "kernel", (("kernel", "rules/HOLISTIC.md"), ("ledger", "complete")),
               age_kind="mtime", age_s=age)


def read_memory_index(ctx: Context) -> Source:
    """The memory index's size against the length Claude Code reads. Size only, never text."""
    from lib.auto_memory_writer import _HARNESS_INDEX_CHAR_BUDGET as budget

    index = ctx.memory_index()
    chars = len(index.read_text(encoding="utf-8"))
    facts = (("characters", chars), ("read limit", budget), ("share", f"{chars / budget:.0%}"))
    return _ok("memory", "index", facts, age_kind="mtime", age_s=_age(ctx, index))


def read_fleet(ctx: Context) -> Source:
    """How many session transcripts the fleet watch sees, and how many are hot or stuck. Counts only."""
    res = _run(ctx, [str(ctx.root / "bin" / "grip-fleet-watch"), "--json"])
    rows = json.loads(res.stdout or "{}").get("transcripts") or []
    if not rows:
        return _gap("sessions", "fleet", "EMPTY", "the fleet watch saw no transcript")
    facts = (("transcripts", len(rows)), ("hot", sum(1 for r in rows if r.get("hot"))),
             ("stuck", sum(1 for r in rows if r.get("stuck"))))
    return _ok("sessions", "fleet", facts, age_s=0)


_HARNESS_ROW = re.compile(r"^\s{2}(\S+)\s+\[([a-z-]+)\]", re.MULTILINE)


def read_harnesses(ctx: Context) -> Source:
    """Which harnesses GRIP knows, by tag (``bin/grip-harness list``)."""
    res = _run(ctx, [str(ctx.root / "bin" / "grip-harness"), "list"])
    rows = _HARNESS_ROW.findall(res.stdout or "")
    if not rows:
        return _gap("harnesses", "list", "EMPTY", f"grip-harness list: {_first_line(res.stderr) or 'no rows'}")
    tags: dict[str, int] = {}
    for _name, tag in rows:
        tags[tag] = tags.get(tag, 0) + 1
    return _ok("harnesses", "list", [("harnesses", len(rows)), *sorted(tags.items())], age_s=0)


def read_hal_gateway(ctx: Context) -> Source:
    """Does HAL's gateway answer its health check on port 4010?"""
    try:
        code = ctx.http(HAL_GATEWAY_HEALTH, _left(ctx, 3.0, HAL_GATEWAY_HEALTH))
    except (OSError, urllib.error.URLError) as exc:
        return _gap("harnesses", "hal-gateway", "UNREADABLE", f"no answer on port 4010: {exc}")
    if code != 200:
        return _gap("harnesses", "hal-gateway", "UNREADABLE", f"port 4010 answered HTTP {code}")
    return _ok("harnesses", "hal-gateway", (("port", 4010), ("health", "HTTP 200")), age_s=0)


def read_spend(ctx: Context) -> Source:
    """This machine's weekly and five-hour Claude use, from HAL's cache-loss ledger."""
    from lib.workflow_budget_gate import latest_utilisation

    ledger = ctx.hal_home / "logs" / "cache-loss-ledger.jsonl"
    age = _age(ctx, ledger)
    if age is None:
        return _gap("spend", "weekly", "EMPTY", "HAL's cache-loss ledger is missing", age_kind="ledger-lag")
    recent, status = latest_utilisation(ledger, within_minutes=60), "OK"
    if not recent:
        recent, status = latest_utilisation(ledger, within_minutes=24 * 60), "STALE"
    if not recent:
        return _gap("spend", "weekly", "EMPTY", "no reading in the last day", age_kind="ledger-lag", age_s=age)
    if status == "STALE":
        return _gap("spend", "weekly", "STALE", "the newest reading is more than an hour old",
                    age_kind="ledger-lag", age_s=age)
    facts = [(label, _pct(recent.get(key))) for label, key in
             (("seven-day", "seven_day_utilization"), ("five-hour", "five_hour_utilization"))]
    return _ok("spend", "weekly", facts, age_kind="ledger-lag", age_s=age)


def _pct(value: Any) -> str:
    return f"{float(value):.0%}" if isinstance(value, (int, float)) else "no reading"


READERS: tuple[Reader, ...] = (
    Reader("sessions", "fleet", read_fleet),
    Reader("memory", "kernel", read_kernel),
    Reader("memory", "index", read_memory_index),
    Reader("canonical", "tree", read_tree),
    Reader("canonical", "origin-main", read_origin_main),
    Reader("canonical", "open-prs", read_open_prs),
    Reader("harnesses", "list", read_harnesses),
    Reader("harnesses", "hal-gateway", read_hal_gateway, timeout_s=10.0),
    Reader("decisions", "chains", read_chains),
    Reader("decisions", "hypotheses", read_hypotheses),
    Reader("spend", "weekly", read_spend),
)


# --- composing ----------------------------------------------------------------

def _checked(source: Source, reader: Reader) -> Source:
    """Hold one answer to the contract: a known status and age kind, and a reason for every gap."""
    if (source.axis, source.name) != (reader.axis, reader.name):
        source = replace(source, axis=reader.axis, name=reader.name)
    if source.status not in STATUSES or source.age_kind not in AGE_KINDS:
        return _gap(reader.axis, reader.name, "UNREADABLE",
                    f"the reader answered outside the contract ({source.status}, {source.age_kind})")
    if source.status != "OK" and not source.detail.strip():
        return replace(source, detail="the reader gave no reason")
    return source


def _timeout_detail(reader: Reader, exc: subprocess.TimeoutExpired) -> str:
    """Name the limit that really stopped the reader: the one its command ran under,
    which is the command's own or what was left of the slot, whichever was shorter."""
    if isinstance(exc, SlotSpent):
        return f"no answer within {reader.timeout_s:g} s (too little time was left to start a command)"
    if not isinstance(exc.timeout, (int, float)):  # a raiser gave no number, so name no limit
        return f"no answer within {reader.timeout_s:g} s (a command timed out)"
    return f"no answer within {reader.timeout_s:g} s (a command stopped at {round(exc.timeout, 2):g} s)"


def _run_one(reader: Reader, ctx: Context) -> Source:
    budget = reader.timeout_s * _SLOT_SHARE  # inside the slot, so the reader reports before compose stops waiting
    ctx = replace(ctx, deadline=time.monotonic() + budget)
    try:
        return _checked(reader.read(ctx), reader)
    except subprocess.TimeoutExpired as exc:
        return _gap(reader.axis, reader.name, "NOT_RUN", _timeout_detail(reader, exc))
    except Exception as exc:  # noqa: BLE001 -- any failure of one reader is that reader's named gap
        return _gap(reader.axis, reader.name, "UNREADABLE", f"{type(exc).__name__}: {exc}")


def _shield() -> Callable[[str], str] | None:
    """Contract item 5. None means the shield could not load, so all text is withheld."""
    try:
        from lib.redaction_shield import Shield, load_denylist

        shield = Shield(load_denylist(), profile="public")
    except Exception:  # noqa: BLE001 -- fail closed: no shield, no text
        return None
    return lambda text: shield.redact(text).text if text else text


def _scrubbed(source: Source, redact: Callable[[str], str] | None) -> Source:
    if redact is None:
        return replace(source, facts=tuple((k, WITHHELD) for k, _ in source.facts),
                       detail=WITHHELD if source.detail else "")
    return replace(source, facts=tuple((redact(k), redact(v)) for k, v in source.facts),
                   detail=redact(source.detail))


def _gather(readers: Sequence[Reader], ctx: Context) -> list[Source]:
    """Run every reader at once; one past its time limit is NOT_RUN, never a wait."""
    started = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=max(1, len(readers)))  # all start at once, so no slot is spent queued
    futures = [(reader, pool.submit(_run_one, reader, ctx)) for reader in readers]
    sources = []
    for reader, future in futures:
        left = max(0.0, started + reader.timeout_s - time.monotonic())
        try:
            sources.append(future.result(timeout=left))
        except FutureTimeout:
            sources.append(_gap(reader.axis, reader.name, "NOT_RUN",
                                f"no answer within {reader.timeout_s:g} s"))
    pool.shutdown(wait=False, cancel_futures=True)
    return sources


_LOAD_SHIELD = object()  # compose's default: load the real redaction shield


def _redactor(redact: Any) -> Callable[[str], str] | None:
    """What compose applies to all text: the real shield by default, else the caller's
    function, or None to withhold all text. True, the first version's spelling of the
    default, still loads the shield. Anything else is refused before a reader runs."""
    if redact is _LOAD_SHIELD or redact is True:
        return _shield()
    if redact is None or callable(redact):
        return redact
    raise TypeError(f"redact must be a function, None or True, not {redact!r}")


def compose(readers: Sequence[Reader], ctx: Context, axes: Sequence[str] | None = None,
            redact: Any = _LOAD_SHIELD) -> Report:
    """One report. ``redact`` is a function applied to all text, None to withhold all
    text, or by default the real redaction shield, loaded here."""
    scrub = _redactor(redact)
    chosen = tuple(a for a in AXES if axes is None or a in axes)
    selected = [r for r in readers if r.axis in chosen]
    sources = sorted((_scrubbed(s, scrub) for s in _gather(selected, ctx)),
                     key=lambda s: (AXES.index(s.axis), s.name))
    keep = lambda pairs: tuple(p for p in pairs if p[0].split("/")[0] in chosen)  # noqa: E731
    return Report(ctx.now.strftime("%Y-%m-%d %H:%M UTC"), chosen, tuple(sources),
                  keep(LEFT_OUT), keep(NEVER_RUN))


# --- rendering ----------------------------------------------------------------

def _fmt_age(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def _age_label(source: Source) -> str:
    if source.age_kind == "fetched-now":
        return "fetched now"
    return f"{source.age_kind} {_fmt_age(source.age_s)}"


def _row(source: Source) -> str:
    if source.status != "OK":
        return f"  {source.name:<12} {source.status:<10} see GAPS  [{_age_label(source)}]"
    facts = "; ".join(f"{k}: {v}" for k, v in source.facts)
    return f"  {source.name:<12} OK         {facts}  [{_age_label(source)}]"


def render_text(report: Report) -> str:
    gaps = [s for s in report.sources if s.status != "OK"]
    lines = [f"/mental-model v{VERSION}  {report.generated_at}  "
             f"{len(report.sources)} sources, {len(gaps)} gap(s), {len(report.left_out)} left out"]
    for axis in report.axes:
        lines.append(axis.upper())
        lines.extend(_row(s) for s in report.sources if s.axis == axis)
    if gaps:
        lines.append("GAPS")
        lines.extend(f"  {s.key}: {s.status} -- {s.detail}" for s in gaps)
    if report.left_out:
        lines.append("LEFT OUT")
        lines.extend(f"  {key}: {reason}" for key, reason in report.left_out)
    if report.never_run:
        lines.append("NEVER RUN")
        lines.extend(f"  {key}: {reason}" for key, reason in report.never_run)
    return "\n".join(lines)


def render_json(report: Report) -> str:
    payload = {
        "version": VERSION,
        "generated_at": report.generated_at,
        "sources": [{"source": s.key, "status": s.status, "facts": dict(s.facts),
                     "age_kind": s.age_kind, "age_s": s.age_s, "detail": s.detail}
                    for s in report.sources],
        "manifest": {
            "axes_attempted": list(report.axes),
            "statuses": {s.key: s.status for s in report.sources},
            "left_out": [{"source": k, "reason": r} for k, r in report.left_out],
            "never_run": [{"source": k, "reason": r} for k, r in report.never_run],
        },
    }
    return json.dumps(payload, sort_keys=True, indent=1)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grip-mental-model", description="One up-to-date picture of GRIP, from the readers it has.")
    parser.add_argument("--json", action="store_true", help="print the report and its manifest as JSON")
    parser.add_argument("--axis", action="append", choices=AXES, help="limit the report to one axis; repeat it")
    ns = parser.parse_args(argv)
    ctx = Context(root=get_claude_dir(), now=datetime.now(timezone.utc))
    report = compose(READERS, ctx, axes=ns.axis)
    print(render_json(report) if ns.json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
