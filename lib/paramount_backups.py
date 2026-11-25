"""Resolve the PARAMOUNT_BACKUPS root and copy important things into it.

Some artefacts must survive things that routinely destroy files inside a working
repo: a branch switch, a worktree removal, a `/clear`, a reset, a machine rebuild.
A signed contract, a rendered deliverable, a handover document, an invoice. The
answer is a folder that is deliberately **outside every repo**, and one obvious
place to put such things.

That folder already existed by convention and was hardcoded three separate times as
``Path.home() / "CodeTonight" / "PARAMOUNT_BACKUPS"`` (a whitepaper build script,
``bin/maister_sankey_v5.py``, ``bin/agora-replay-discover``). Three copies of a user
path is the reason this module exists: the folder lives in a **different physical
place on each machine** -- the older backups sit on a second Mac -- so a literal path
is wrong everywhere except the machine it was written on, and is unshippable in a
repo heading for public release.

Resolution ladder, first hit wins:

1. ``$GRIP_PARAMOUNT_BACKUPS`` -- per-machine, set in the shell profile. Primary,
   because hostnames are not stable (this machine reports ``192-168-0-25``).
2. ``config/paramount-backups.local.json`` -> ``{"root": "..."}`` -- gitignored, so an
   operator's real path never enters the repo.
3. ``$HOME/PARAMOUNT_BACKUPS`` -- portable default that works on a fresh clone.

**A root inside a git repository is refused.** The whole point is to survive repo
operations, and the artefacts most worth backing up are exactly the ones
``rules/no-sensitive-info-in-grip.md`` forbids committing -- contracts, financial
records, client material. Silently accepting a root inside the repo would turn this
helper into the leak it exists to prevent, so it raises instead.

Copies only ever go **outward**, into the backup root. There is deliberately no
"restore into the repo" function: pulling a backed-up contract back into a
public-bound tree is the failure mode, not a feature.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ENV_VAR = "GRIP_PARAMOUNT_BACKUPS"
CONFIG_RELATIVE = Path("config") / "paramount-backups.local.json"
DEFAULT_DIR_NAME = "PARAMOUNT_BACKUPS"

# Which rung of the ladder answered. These strings are what ``--where`` prints,
# so the human-readable report and the machine-readable provenance are the same
# value and cannot drift apart.
SOURCE_ENV = f"${ENV_VAR}"
SOURCE_CONFIG = str(CONFIG_RELATIVE)
SOURCE_DEFAULT = f"$HOME/{DEFAULT_DIR_NAME}"


class ParamountBackupError(Exception):
    """The backup root is unusable, or the copy could not be made safely."""


def _from_env() -> Optional[Path]:
    raw = (os.environ.get(ENV_VAR) or "").strip()
    return Path(raw).expanduser() if raw else None


def _from_config(claude_dir: Path) -> Optional[Path]:
    """Read the operator's local config, tolerating every kind of absence.

    A missing, empty, malformed or wrong-shaped config is not an error -- it just
    means this rung of the ladder does not apply and the next one is tried.
    """
    path = claude_dir / CONFIG_RELATIVE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    root = data.get("root")
    return Path(str(root)).expanduser() if isinstance(root, str) and root.strip() else None


def _require_absolute(path: Path, source: str) -> Path:
    """A configured root must be absolute, or it is not a location at all.

    A relative root resolves against the working directory, so the SAME config
    names a different destination per run -- and a scheduled job is exactly the
    caller that does not control its cwd. Measured before this check existed,
    with one config of {"root": "backups"} and require_configured=True: refused
    from inside a git repo (incidentally, by the git check), ACCEPTED from
    outside one, writing to <cwd>/backups.

    Raised rather than skipped: falling through to the next rung would be the
    silent demotion this module's provenance reporting exists to remove, and it
    would hide a misconfiguration the operator can fix in one line.
    """
    if path.is_absolute():
        return path
    raise ParamountBackupError(
        f"the backup root from {source} is {str(path)!r}, which is a relative path. "
        f"A relative root resolves against the working directory, so it names a "
        f"different destination on every run. Set {source} to an absolute path."
    )


def _inside_git_repo(path: Path) -> bool:
    """True if ``path`` or any ancestor contains a .git entry.

    Checked against the path as written rather than by invoking git, so it works
    for a root that does not exist yet -- which is the common case on first use.
    """
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return True
    return False


def resolve_root(claude_dir: Optional[Path] = None) -> "tuple[Path, str]":
    """The backup root AND which rung of the ladder supplied it.

    Provenance is the load-bearing half, and it cannot be recovered from the
    path alone. Rung 2 is gitignored, so it is absent *by construction* on a
    rebuilt machine, and rung 3 then answers without erroring because
    ``$HOME/PARAMOUNT_BACKUPS`` plausibly exists. A scheduled job pointed at it
    mirrors the wrong tree and reports success.

    A file-count floor cannot detect this: a first install legitimately has an
    empty root, so "few files" and "wrong root" are indistinguishable by count.
    Only the rung separates them.

    Measured 2026-09-15 on the operator's machine: the rung-3 default held 1
    file / 420K while the configured root held 4,326 files / 9.4 GB.
    """
    base = claude_dir or Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
    from_env = _from_env()
    if from_env is not None:
        return _require_absolute(from_env, SOURCE_ENV), SOURCE_ENV
    from_config = _from_config(base)
    if from_config is not None:
        return _require_absolute(from_config, SOURCE_CONFIG), SOURCE_CONFIG
    return Path.home() / DEFAULT_DIR_NAME, SOURCE_DEFAULT


def backups_root(
    claude_dir: Optional[Path] = None, *, require_configured: bool = False
) -> Path:
    """Where important things get backed up on THIS machine.

    Never returns a path inside a git repository; raises instead, because a backup
    that lands in a repo is not a backup.

    ``require_configured`` is for SCHEDULED callers: it refuses a root that
    nobody configured, rather than silently backing up whatever happens to sit
    at the portable default. It is off by default so ad-hoc use on a fresh
    install -- where an unconfigured root is entirely legitimate -- is unchanged.
    """
    root, source = resolve_root(claude_dir)
    if require_configured and source == SOURCE_DEFAULT:
        raise ParamountBackupError(
            f"the backup root fell through to {SOURCE_DEFAULT}, which nobody "
            f"configured. A scheduled backup must not run against a defaulted "
            f"root: {CONFIG_RELATIVE} is gitignored, so it is absent on a rebuilt "
            f"machine and the default answers silently. Set {ENV_VAR} to the real "
            f"path, or restore {CONFIG_RELATIVE}."
        )
    if _inside_git_repo(root):
        raise ParamountBackupError(
            f"backup root {root} is inside a git repository. Backups must survive "
            f"branch switches and worktree removal, and must never carry sensitive "
            f"files into a tracked tree. Set {ENV_VAR} to a path outside any repo."
        )
    return root


def _extension(name: str) -> str:
    """The file's extension, with ``.tar.gz`` counted as one."""
    suffixes = Path(name).suffixes
    two = "".join(suffixes[-2:])
    return two if two.lower().startswith(".tar.") else "".join(suffixes[-1:])


def _unique_destination(directory: Path, name: str) -> Path:
    """A path in ``directory`` that does not exist yet, never overwriting."""
    candidate = directory / name
    if not candidate.exists():
        return candidate
    suffix = _extension(name)
    stem = name[:-len(suffix)] if suffix else name
    for n in range(2, 1000):
        candidate = directory / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
    raise ParamountBackupError(f"could not find a free name for {name} in {directory}")


def _slugify(text: str) -> str:
    """A folder-safe, readable slug: lowercase, non-alphanumerics become dashes.

    A dated folder is only a timestamp; the slug is what tells a later reader
    (or the operator six months on) *what* is in it without opening it. Collapsed
    and trimmed so "EasyEquities breach response" becomes "easyequities-breach-response".
    """
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def _labelled_name(label: str, src: Path) -> str:
    """The label names the copy, and the copy keeps the source's extension.

    A label with no extension made a PDF that no application offered to open and
    no search for "*.pdf" could find (V>>, 2026-11-01). A label that already ends
    in the extension is used as written."""
    suffix = _extension(src.name)
    return label if label.lower().endswith(suffix.lower()) else f"{label}{suffix}"


def back_up(
    source: Path,
    *,
    label: Optional[str] = None,
    summary: Optional[str] = None,
    now: Optional[datetime] = None,
    claude_dir: Optional[Path] = None,
) -> Path:
    """Copy ``source`` into a dated folder under the backup root; return the copy.

    Dated so successive versions of the same document sit side by side rather than
    replacing one another, and never overwriting, because the entire value of this
    folder is that nothing in it is silently lost. A ``summary`` is folded into the
    folder name (``YYYY-MM-DD--<slug>``) so the folder says what it holds, not just
    when it was made.

    ``now`` is injectable so the destination is deterministic under test.
    """
    src = Path(source).expanduser()
    if not src.is_file():
        raise ParamountBackupError(f"not a file: {src}")

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    folder = f"{stamp}--{_slugify(summary)}" if summary else stamp
    directory = backups_root(claude_dir) / folder
    directory.mkdir(parents=True, exist_ok=True)

    destination = _unique_destination(directory, _labelled_name(label, src) if label else src.name)
    shutil.copy2(src, destination)
    return destination
