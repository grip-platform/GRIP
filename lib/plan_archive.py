"""Keep every Claude Code plan: copy a plan aside before a Write replaces it.

Claude Code keeps one plan file per session in its plans folder and, when the
task changes, asks for that file to be overwritten. The earlier plan then
survives only in the harness's hash-named file history, where nobody looks
for it. On 2026-11-25 one session lost two plans that way.

hooks/plan-archive-hook.py calls archive_before_overwrite() before every
Write. When the Write would replace a non-empty plan in the plans folder, this
module copies the old text to <plans>/archive/ first.

A copy is named <stem>--<UTC>--<slug>.md: the plan file's own name, the moment
a Write was about to replace its text, and the plan's first heading in plain
words, so a lost plan can be found by what it was about (rules/clear-naming.md).
A Write that another hook then refuses still leaves its copy; the copy is
harmless, and the same text is never kept twice.

Only a Write is watched. An Edit changes a plan in place and keeps most of it,
and a shell command that overwrites a plan is not seen at all.

Only the configured plans folder counts: GRIP_PLANS_DIR, else
CLAUDE_CONFIG_DIR/plans, else ~/.claude/plans. A plans/ folder inside some
project is never touched, so the hook cannot leave stray files in another
repository.

The archive is never pruned automatically. Deleting an old plan is the loss
this module exists to prevent, and nothing counts the archive as current work.
An archived plan has no open flag, so rules/lifecycle-closed-or-expired.md does
not govern it.
"""

from __future__ import annotations

import glob
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ARCHIVE_DIRNAME = "archive"
SLUG_LIMIT = 60
_MAX_SAME_SECOND = 100
_NON_WORD = re.compile(r"[^a-z0-9]+")


def plans_dir(env: dict[str, str] | None = None) -> Path:
    """The Claude Code plans folder this machine writes plans into."""
    env = os.environ if env is None else env
    override = env.get("GRIP_PLANS_DIR")
    if override:
        return Path(override).expanduser()
    config = env.get("CLAUDE_CONFIG_DIR")
    base = Path(config).expanduser() if config else Path.home() / ".claude"
    return base / "plans"


def is_plan_file(target: Path, plans: Path) -> bool:
    """True when ``target`` is a markdown file directly inside ``plans``.

    The folder is resolved, not the file, so a plan that is itself a symlink
    still counts as a plan in this folder.
    """
    if target.suffix != ".md":
        return False
    try:
        return target.parent.resolve() == plans.resolve()
    except OSError:
        return False


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:]
    return ""


def title_slug(text: str, limit: int = SLUG_LIMIT) -> str:
    """The first level-one heading of ``text`` as lowercase words joined by hyphens.

    The slug is cut at a word boundary to at most ``limit`` characters. It is
    empty when the text has no level-one heading.
    """
    words = _NON_WORD.sub("-", _first_heading(text).lower()).strip("-")
    if len(words) <= limit:
        return words
    cut = words[: limit + 1]
    boundary = cut.rfind("-")
    return cut[:boundary] if boundary > 0 else words[:limit]


def archive_name(stem: str, when: datetime, slug: str) -> str:
    """``<stem>--<UTC>--<slug>.md``, or ``<stem>--<UTC>.md`` with no slug."""
    stamp = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    return f"{stem}--{stamp}--{slug}.md" if slug else f"{stem}--{stamp}.md"


def _already_kept(archive: Path, stem: str, old: bytes) -> bool:
    """True when an archived copy of this plan file already holds ``old``."""
    if not archive.is_dir():
        return False
    for copy in archive.glob(f"{glob.escape(stem)}--*.md"):
        try:
            # The size check first, so only a copy of the same length is read.
            if copy.stat().st_size == len(old) and copy.read_bytes() == old:
                return True
        except OSError:
            continue
    return False


def _write_new(archive: Path, name: str, data: bytes) -> Path:
    """Write ``data`` under ``name``, or a numbered variant when that name is taken.

    A write that fails part-way removes its partial file before the error
    propagates, so a truncated copy never sits in the archive looking whole.
    Raises FileExistsError when every numbered variant is taken.
    """
    for number in range(1, _MAX_SAME_SECOND + 1):
        candidate = archive / (name if number == 1 else f"{name[:-3]}-{number}.md")
        try:
            handle = candidate.open("xb")
        except FileExistsError:
            continue
        # The exclusive create succeeded, so this file is ours alone: removing
        # it after a failed write can never delete a copy someone else made.
        try:
            with handle:
                handle.write(data)
        except OSError:
            candidate.unlink(missing_ok=True)
            raise
        return candidate
    raise FileExistsError(f"no free archive name for {name} after {_MAX_SAME_SECOND} tries")


def archive_before_overwrite(
    target: str | os.PathLike,
    new_content: str,
    *,
    plans: Path | None = None,
    now: datetime | None = None,
) -> Path | None:
    """Copy the plan at ``target`` aside before ``new_content`` replaces it.

    Returns the archive path written. Returns None when nothing needs keeping:
    the target is not a plan in the plans folder, does not exist yet, is empty,
    would not change, or its text is already in the archive.
    """
    path = Path(target).expanduser()
    plans = plans_dir() if plans is None else plans
    if not is_plan_file(path, plans) or not path.is_file():
        return None
    old = path.read_bytes()
    if not old.strip() or old == new_content.encode("utf-8"):
        return None
    archive = path.parent / ARCHIVE_DIRNAME
    if _already_kept(archive, path.stem, old):
        return None
    archive.mkdir(parents=True, exist_ok=True)
    when = now or datetime.now(timezone.utc)
    slug = title_slug(old.decode("utf-8", "replace"))
    return _write_new(archive, archive_name(path.stem, when, slug), old)
