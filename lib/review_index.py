"""review_index -- one place to find what GRIP actually worked on, newest first.

The operator's complaint, verbatim (2026-09-19): *"you have to make it easy for me
to find the most recent files you've worked on. It takes really long for me to just
find it to humanly review. It's all over the show: /plans /drafts/ whatever."*

He is right, and the scatter is measurable. On the day he said it: 14 files in
``plans/``, 19 in ``drafts/``, 255 in ``docs/``, 6 in ``prep/``, 2 in ``exports/``
-- with the SAME artefact class (an ultraprompt continuation brief) living in both
``plans/`` and ``drafts/``, and 10 of 22 plan/draft files carrying no date in their
filename at all. Nothing answers "what changed this week".

**Why this reads git and never the filesystem.** The obvious implementation sorts by
``stat`` mtime. That is wrong here, and silently so: a git worktree is checked out in
one operation, so every file in it shares the checkout's mtime. Measured on
``grip-session-99344`` the same day -- all 22 plan/draft files reported
``2026-09-19``, including one last genuinely edited in May. An mtime-sorted index
would have been uniformly, confidently useless, in the shape
``shared-tree-is-not-canonical.md`` describes: a valid answer to the wrong question.
Recency therefore comes from ``git log``, the canonical record of when a file was
last touched, plus ``git status`` for work not yet committed.

The commit subject doubles as the description: a human sentence about why the file
changed, already written and already reviewed. No summarisation required.

**The fast path.** ``build(..., read_titles=False)`` skips reading the first heading
out of every prose file, which is one open-and-scan per entry. Use it whenever only
paths and dates are wanted -- a count, a sort, a caller that renders its own labels.
The default stays ``True`` because the human-facing index is the main caller and a
title is most of what makes a row scannable.
"""

from __future__ import annotations

import datetime as _dt
import re
import subprocess
import time as _time
from dataclasses import dataclass, field
from itertools import repeat
from pathlib import Path

# Ordered: prose artefacts a human reviews come first, code last. The operator asked
# to "humanly review", and a wall of lib/*.py churn buries the three plans he wants.
CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Plans & briefs", ("plans/",)),
    ("Drafts & research", ("drafts/",)),
    ("Deliverables", ("exports/", "prep/", "artifacts/")),
    ("Rules & doctrine", ("rules/",)),
    ("Docs & measurements", ("docs/",)),
    ("Memory", ("memory/", "projects/")),
    ("Capabilities", ("skills/", "commands/", "agents/", "modes/")),
    ("Code", ("lib/", "bin/", "hooks/", "scripts/", "mcp-servers/", "apps/", "oss/")),
    ("Config & CI", ("config/", ".github/")),
    ("Tests", ("tests/",)),
)

# Prose = rendered in full, one block each, because a human reads these. Everything
# else is summarised as a count plus the few most recent paths. Without the split,
# 1,870 commands/ changes in a fortnight bury the three plans he actually wants.
PROSE_CATEGORIES = frozenset(
    {
        "Plans & briefs",
        "Drafts & research",
        "Deliverables",
        "Rules & doctrine",
        "Docs & measurements",
        "Memory",
    }
)

# Never listed: machine-written runtime state and things already retired. Surfacing
# runtime logs would also drag absorbed URLs and channel identifiers into a file
# built for sharing -- the class rules/operator-anonymity.md exists to keep out.
EXCLUDE_PREFIXES: tuple[str, ...] = (
    "_archived/",
    "state/",
    "logs/",
    "cache/",
    "node_modules/",
    "venv/",
    "data/absorbed",
)

OTHER = "Other"


def _build_prefix_map() -> tuple[tuple[str, str], ...]:
    """Flatten CATEGORIES to (prefix, name) pairs, longest prefix first.

    Computed once at import so categorise() is a single scan that stops at the first
    hit, instead of walking every category's prefix tuple on every call.
    """
    pairs: list[tuple[str, str]] = []
    for name, prefixes in CATEGORIES:
        pairs += list(zip(prefixes, repeat(name)))
    return tuple(sorted(pairs, key=lambda pair: len(pair[0]), reverse=True))


_PREFIX_MAP: tuple[tuple[str, str], ...] = _build_prefix_map()


def _build_scan_dirs() -> tuple[tuple[str, str], ...]:
    """Prose directories to sweep on the filesystem, as (dirname, category) pairs.

    Only prose categories: the point of the sweep is artefacts a human reviews, and
    a gitignored build directory is noise.
    """
    pairs: list[tuple[str, str]] = []
    for prefix, name in _PREFIX_MAP:
        if name in PROSE_CATEGORIES:
            pairs.append((prefix.rstrip("/"), name))
    return tuple(pairs)


_SCAN_DIRS: tuple[tuple[str, str], ...] = _build_scan_dirs()

_CATEGORY_ORDER: dict[str, int] = {
    name: position for position, (name, _) in enumerate(CATEGORIES)
}

# git log -z gives NUL-separated paths; %x1e separates each commit record.
_LOG_FORMAT = "%x1e%at%x1f%h%x1f%s"

_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)
_H1 = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_DATE_IN_NAME = re.compile(r"20\d{2}-\d{2}-\d{2}")

# A title is the first real line of an arbitrary file, and some carry terminal
# colour codes -- three such lines reached the first published index, which made
# `file` classify the whole markdown document as containing escape sequences.
# Rendering an ANSI code into markdown shows the raw bytes to the reader.
# CSI (colour, cursor) and OSC (window title) forms. OSC needs its own branch:
# stripping the bare ESC and BEL leaves the payload behind as visible text --
# "\x1b]0;title\x07" became a literal "]0;title" until this was added.
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_CODE_PREVIEW = 8
_LATEST_LINKS = 20
_UNDATED_SCOPE = ("Plans & briefs", "Drafts & research")


@dataclass(frozen=True)
class Entry:
    """One artefact, carrying the evidence for why it is listed where it is."""

    path: str
    timestamp: int
    sha: str
    subject: str
    category: str
    title: str = ""
    uncommitted: bool = False
    untracked: bool = False

    @property
    def date(self) -> str:
        moment = _dt.datetime.fromtimestamp(self.timestamp, _dt.timezone.utc)
        return moment.strftime("%Y-%m-%d")

    @property
    def dated_in_name(self) -> bool:
        """Does the filename itself carry a date? 10 of 22 did not, on day one."""
        return bool(_DATE_IN_NAME.search(Path(self.path).name))


@dataclass
class Index:
    entries: list[Entry] = field(default_factory=list)
    days: int = 14
    truncated: dict[str, int] = field(default_factory=dict)

    def by_category(self) -> dict[str, list[Entry]]:
        out: dict[str, list[Entry]] = {}
        for entry in self.entries:
            out.setdefault(entry.category, []).append(entry)
        return out

    def ordered_categories(self) -> list[str]:
        last = len(CATEGORIES)
        return sorted(
            self.by_category(), key=lambda n: (_CATEGORY_ORDER.get(n, last), n)
        )


def categorise(path: str) -> str:
    """Map a repo-relative path to its review category; longest prefix wins."""
    for prefix, name in _PREFIX_MAP:
        if path.startswith(prefix):
            return name
    return OTHER


def excluded(path: str) -> bool:
    """Is this path machine-written state or already-retired work?"""
    return path.startswith(EXCLUDE_PREFIXES)


def extract_title(text: str) -> str:
    """First markdown H1, else first real line. Frontmatter is stripped first.

    Returns "" rather than guessing when the file carries neither -- an invented
    title is worse than an absent one, because the reader cannot tell it is invented.
    """
    body = _FRONTMATTER.sub("", text, count=1)
    match = _H1.search(body)
    if match:
        return _as_plain_line(match.group(1))
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("<!--", "|", "```")):
            return _as_plain_line(stripped[:120])
    return ""


def _as_plain_line(value: str) -> str:
    """Neutralise leading markdown so file content cannot forge a heading.

    The title is echoed into INDEX.md verbatim. A file whose first real line is
    "## Origin" then renders as a top-level section of the index itself -- observed
    on the first generated index, which grew a spurious "## Origin" heading between
    two legitimate ones.
    """
    value = _ANSI.sub("", value)
    value = _CONTROL.sub("", value)
    return value.lstrip("#>*-= \t").strip()


def _clean(value: str) -> str:
    """Strip whitespace AND NUL bytes.

    ``git log -z`` NUL-terminates the format string as well as the path list, so a
    subject or path can carry a trailing ``\\x00``. ``str.strip()`` does not remove
    it, and one NUL is enough to make the rendered INDEX.md a *binary* file as far
    as ripgrep, grep and some editors are concerned -- which would make the one
    place the operator is told to look unsearchable. Caught 2026-09-19 at offset 524
    of the first generated index.
    """
    return value.replace("\x00", "").strip()


def _run(args: list[str], cwd: Path) -> str:
    try:
        proc = subprocess.run(
            args, cwd=str(cwd), capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def _record_rows(
    record: str, seen: set[str]
) -> list[tuple[int, str, str, str]]:
    """Rows for one commit record, skipping paths already claimed by a newer commit."""
    if not record.strip():
        return []
    header, _, body = record.partition("\n")
    parts = header.split("\x1f")
    if len(parts) != 3 or not parts[0].isdigit():
        return []
    timestamp, sha, subject = int(parts[0]), _clean(parts[1]), _clean(parts[2])
    rows: list[tuple[int, str, str, str]] = []
    for line in body.split("\x00"):
        path = _clean(line)
        if path and path not in seen:
            seen.add(path)
            rows.append((timestamp, sha, subject, path))
    return rows


def parse_log(raw: str) -> list[tuple[int, str, str, str]]:
    """Parse ``git log`` output into (timestamp, sha, subject, path) rows.

    Only the FIRST sighting of a path is kept: git log is newest-first, so the first
    commit touching a file is the one the reviewer wants.
    """
    rows: list[tuple[int, str, str, str]] = []
    seen: set[str] = set()
    for record in raw.split("\x1e"):
        rows += _record_rows(record, seen)
    return rows


def tracked_paths(repo: Path) -> set[str]:
    """Every path git knows about, so the filesystem sweep can skip them."""
    return {line for line in _run(["git", "ls-files"], repo).splitlines() if line}


def _scan_dir(
    repo: Path, dirname: str, category: str, cutoff: float, tracked: set[str]
) -> list[Entry]:
    """Recent GITIGNORED artefacts under one directory, dated by mtime.

    mtime is the right signal here and only here. The module docstring warns that
    mtime is meaningless for TRACKED files, because a worktree checkout stamps them
    all at once -- but a gitignored file is never part of a checkout. It is written
    once, in place, and keeps the time it was actually written. Verified: the
    EasyEquities brief reports 2026-09-17 03:23, its real authoring time.
    """
    base = repo / dirname
    if not base.is_dir():
        return []
    out: list[Entry] = []
    for path in base.rglob("*.md"):
        rel = path.relative_to(repo).as_posix()
        if rel in tracked or excluded(rel):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        out.append(
            Entry(
                path=rel, timestamp=int(mtime), sha="",
                subject="not in git — local file only",
                category=category, untracked=True,
            )
        )
    return out


def _ignored_entries(repo: Path, days: int, tracked: set[str]) -> list[Entry]:
    """Sweep the prose directories for artefacts git cannot see.

    ``drafts/**`` is gitignored wholesale (.gitignore:587), which hides 254 markdown
    files including every pending continuation brief. Anything that consults git --
    ``rg``, ``fd``, and this tool before the sweep existed -- reports them as absent
    rather than as hidden, which is the worst possible failure for an index whose
    entire purpose is "help me find these files".
    """
    cutoff = _time.time() - (days * 86400)
    out: list[Entry] = []
    for dirname, category in _SCAN_DIRS:
        out += _scan_dir(repo, dirname, category, cutoff, tracked)
    return out


def parse_status(raw: str) -> list[str]:
    """Repo-relative paths with uncommitted changes, from ``git status --porcelain``."""
    paths: list[str] = []
    for line in raw.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        # Renames read "old -> new"; the new name is what the reviewer opens.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip('"')
        if path and not path.endswith("/"):
            paths.append(path)
    return paths


def _committed_entries(repo: Path, days: int) -> list[Entry]:
    raw = _run(
        [
            "git", "log", f"--since={days} days ago", "--name-only", "-z",
            f"--format={_LOG_FORMAT}",
        ],
        repo,
    )
    return [
        Entry(path=p, timestamp=ts, sha=sha, subject=subj, category=categorise(p))
        for ts, sha, subj, p in parse_log(raw)
        if not excluded(p)
    ]


def _uncommitted_entries(repo: Path, known: set[str], newest: int) -> list[Entry]:
    out: list[Entry] = []
    # -uall expands untracked DIRECTORIES into their files. Without it a brand-new
    # drafts/ folder reports as the single entry "drafts/", and parse_status drops
    # it for ending in a slash -- so a whole session's new work would be invisible
    # in the one place the operator is told to look. Caught by test 2026-09-19.
    raw = _run(["git", "status", "--porcelain", "-uall"], repo)
    for path in parse_status(raw):
        if path in known or excluded(path):
            continue
        known.add(path)
        out.append(
            Entry(
                path=path, timestamp=newest, sha="",
                subject="uncommitted working change",
                category=categorise(path), uncommitted=True,
            )
        )
    return out


def _cap_per_category(
    entries: list[Entry], limit: int
) -> tuple[list[Entry], dict[str, int]]:
    kept: list[Entry] = []
    counts: dict[str, int] = {}
    truncated: dict[str, int] = {}
    for entry in entries:
        seen = counts.get(entry.category, 0)
        if seen >= limit:
            truncated[entry.category] = truncated.get(entry.category, 0) + 1
            continue
        counts[entry.category] = seen + 1
        kept.append(entry)
    return kept, truncated


def _with_title(repo: Path, entry: Entry) -> Entry:
    if not entry.path.endswith((".md", ".markdown")):
        return entry
    try:
        text = (repo / entry.path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return entry
    title = extract_title(text)
    if not title:
        return entry
    return Entry(
        path=entry.path, timestamp=entry.timestamp, sha=entry.sha,
        subject=entry.subject, category=entry.category, title=title,
        uncommitted=entry.uncommitted, untracked=entry.untracked,
    )


def build(
    repo: Path,
    days: int = 14,
    limit_per_category: int = 25,
    read_titles: bool = True,
) -> Index:
    """Collect recent artefacts from git history plus the uncommitted working set."""
    entries = _committed_entries(repo, days)
    known = {e.path for e in entries}
    # Uncommitted work is the newest thing there is, so it sorts above every commit
    # -- but "one past the newest commit" is wrong when NO commit falls in the
    # window: max(..., default=0) + 1 yields 1, i.e. 1970-01-01, which sorts
    # uncommitted work to the BOTTOM and renders it with an epoch date. Clamping to
    # now fixes both. Raised by xAI Grok 4.3 on GRIP#6252 as an UNQUOTABLE concern,
    # downgraded on citation form, and true on the code: reproduced on a repo whose
    # only commit sat outside the window.
    newest = max(max((e.timestamp for e in entries), default=0) + 1, int(_time.time()))
    entries += _uncommitted_entries(repo, known, newest)
    # Then the files git cannot see at all. Without this the index reports the
    # operator's own continuation briefs as absent rather than as hidden.
    #
    # `known` is passed, not just the tracked set: an untracked-but-NOT-ignored file
    # is absent from git ls-files yet already collected by _uncommitted_entries, so
    # skipping only tracked paths listed it twice.
    entries += _ignored_entries(repo, days, tracked_paths(repo) | known)
    # An index is a list of things to OPEN, so a path that no longer exists has no
    # business in it. A file renamed or deleted inside the window stays in git log
    # under its OLD name -- the commit that created it falls in the window too --
    # so without this the reviewer is handed a dead path and finds nothing there.
    # Found by the falsifier AGORA filed on GRIP#6253: a merge commit carrying a
    # rename listed the same artefact under BOTH names. `is_file()`, not
    # `exists()`: a file later replaced by a FOLDER at the same path still
    # "exists", and the reviewer would be handed the folder (Codex on GRIP#6364).
    entries = [e for e in entries if (repo / e.path).is_file()]
    entries.sort(key=lambda e: (-e.timestamp, e.path))
    if read_titles:
        entries = [_with_title(repo, e) for e in entries]
    kept, truncated = _cap_per_category(entries, limit_per_category)
    return Index(entries=kept, days=days, truncated=truncated)


def _render_header(index: Index, repo_name: str) -> list[str]:
    listed = len(index.entries)
    # The section counts are true totals, so the headline must be too -- reporting
    # the post-cap figure here understated the fortnight by over a thousand files.
    total = listed + sum(index.truncated.values())
    scope = f"{total} files changed" + (f", {listed} listed" if total != listed else "")
    return [
        f"# {repo_name} — what changed in the last {index.days} days",
        "",
        f"{scope}, newest first. Dates and descriptions come from git history, not "
        "file timestamps — in a worktree every file shares the checkout's mtime, so "
        "filesystem dates are meaningless here.",
        "",
        "Regenerate with `bin/grip-review`.",
        "",
    ]


def _flag(entry: Entry) -> str:
    """Mark how durable the artefact is, because that decides whether to act on it."""
    if entry.untracked:
        return " **[not in git — this machine only]**"
    if entry.uncommitted:
        return " **[uncommitted]**"
    return ""


def _render_prose(name: str, rows: list[Entry], extra: int) -> list[str]:
    lines = [f"## {name} ({len(rows) + extra})", ""]
    for entry in rows:
        flag = _flag(entry)
        suffix = f" (`{entry.sha}`)" if entry.sha else ""
        lines.append(f"### {entry.date} — `{entry.path}`{flag}")
        if entry.title:
            lines.append(entry.title)
        lines.append("")
        lines.append(f"_{entry.subject}_{suffix}")
        lines.append("")
    if extra:
        lines += [
            f"…and {extra} more in this category — raise the cap to see them: "
            f"`bin/grip-review --limit {len(rows) + extra}`",
            "",
        ]
    return lines


def _render_code(name: str, rows: list[Entry], extra: int) -> list[str]:
    total = len(rows) + extra
    lines = [f"## {name} ({total})", "", f"{total} files changed. Most recent:", ""]
    for entry in rows[:_CODE_PREVIEW]:
        lines.append(f"- `{entry.path}` — {entry.date} — {entry.subject}")
    remainder = total - min(len(rows), _CODE_PREVIEW)
    if remainder > 0:
        lines.append(f"- …and {remainder} more")
    lines.append("")
    return lines


def _undated_entries(index: Index) -> list[Entry]:
    return [
        e for e in index.entries
        if e.category in _UNDATED_SCOPE and not e.dated_in_name
    ]


def _render_undated(index: Index) -> list[str]:
    undated = _undated_entries(index)
    if not undated:
        return []
    lines = [
        "## Missing a date in the filename",
        "",
        "Findable here, but not from a directory listing. New artefacts should be "
        "named `<topic>-YYYY-MM-DD.md`.",
        "",
    ]
    lines += [f"- `{e.path}` — last touched {e.date}" for e in undated]
    lines.append("")
    return lines


def render(index: Index, repo_name: str = "GRIP") -> str:
    """Render the index as the single markdown file the operator opens."""
    grouped = index.by_category()
    lines = _render_header(index, repo_name)
    for name in index.ordered_categories():
        is_prose = name in PROSE_CATEGORIES
        writer = _render_prose if is_prose else _render_code
        lines += writer(name, grouped[name], index.truncated.get(name, 0))
    lines += _render_undated(index)
    return "\n".join(lines).rstrip() + "\n"


def _clear_latest(latest: Path) -> None:
    """Empty the symlink folder so a removed artefact does not linger as a dead link."""
    if latest.is_dir():
        for child in latest.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink()
    latest.mkdir(parents=True, exist_ok=True)


def _link_prose(repo: Path, entries: list[Entry], latest: Path) -> int:
    linked = 0
    for entry in entries[:_LATEST_LINKS]:
        source = repo / entry.path
        # Same test as build()'s filter: a folder at this path must not become a
        # `latest/` symlink.
        if not source.is_file():
            continue
        link = latest / f"{entry.date}__{Path(entry.path).name}"
        if link.is_symlink() or link.exists():
            link.unlink()
        try:
            link.symlink_to(source)
        except OSError:
            continue
        linked += 1
    return linked


def write_index(
    repo: Path, index: Index, out_dir: Path, repo_name: str | None = None
) -> Path:
    """Write INDEX.md and refresh the ``latest/`` symlink folder.

    ``repo_name`` is passed in rather than taken from ``repo.name`` because sessions
    run inside ``grip-session-<n>`` worktrees, whose directory name is throwaway.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "INDEX.md"
    name = repo_name or repo.name or "GRIP"
    target.write_text(render(index, repo_name=name), encoding="utf-8")

    latest = out_dir / "latest"
    _clear_latest(latest)
    _link_prose(repo, [e for e in index.entries if e.category in PROSE_CATEGORIES], latest)
    return target
