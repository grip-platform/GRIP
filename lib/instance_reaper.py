"""instance_reaper — bounded retention for the session instance store.

WHY THIS EXISTS (measured 2026-09-14 on the live store, not asserted):

    projects/<slug>/grip/ held 13,079 files / 12 GB in ONE scope, growing at
    roughly 145 files/day, with NO reaper anywhere in the estate — context_gc
    (IDR chain), mesh_gc (mesh), task_pruner (harness tasks) and
    grip-kono-prune (embeddings) each cover a DIFFERENT substrate, and none
    covers this one. 10,050 of those files exceed 30 days and hold 8.56 GB.

    GRIP#5677 stopped the hook READING all of it. Nothing stops it GROWING.

WHAT REAPING COSTS, precisely — so the trade is stated rather than implied:

    Each file is a session RESURRECTION record: 15 keys including lineage,
    conversation, mental_state and a ~28,000-character resurrection_prompt.

    index.json separately holds a LIGHTWEIGHT entry per instance
    (instance_id, session_uuid, slug, serialized_at, content_hash,
    message_count, summary, lineage), and lib/session_resolver.py consults
    that index FIRST, falling back to the files only on a miss.

    So reaping a file PRESERVES the instance's identity, lineage, summary and
    content hash, and COSTS the ability to RESURRECT that session. index.json
    is never touched, and never selected.

    That is not the WHOLE cost, and an earlier version of this docstring said
    it was. ``--apply`` writes the selection to a .tar.gz first, and NOTHING
    prunes that directory — so the first run trades one copy for another.
    Measured on the FULL first run, 2026-09-14: 10,061 files holding 8.59 GiB
    were archived to 4,168,760,524 bytes (3.88 GiB) — a ratio of 2.21x, so the
    archive is 45.2% of what was reaped and roughly 4.71 GiB was actually
    freed. Every later run adds another archive nobody reaps; prune the
    backup directory yourself.

    These numbers REPLACE an earlier estimate of 2.4x / ~42%, taken from a
    40-file sample (33,818,143 bytes raw -> 14,276,730 gzipped). The full run
    is 250x larger and refuted it. The error was small and in the flattering
    direction, which is the direction worth correcting loudest: the tool had
    been printing the optimistic figure to the operator at runtime.

SAFETY POSTURE, mirroring bin/grip-kono-prune.py:
    Dry-run by default. ``--apply`` commits, and only after an archive that has
    been read back and verified — a backup that cannot be read is not a backup.

Selection is a CONSERVATIVE INTERSECTION: a file is reapable only if it is
BOTH outside the newest-N AND older than the age floor. Either guard alone
would be enough to spare a file.
"""

import json
import os
import tarfile
import time
from pathlib import Path

#: Keep this many newest files regardless of age. Deliberately far above the
#: read cap in next_step_block (200) — that cap governs what the hook READS on
#: a 60s tick, whereas this governs what remains RESURRECTABLE. Different
#: question, so it gets its own, more generous number.
DEFAULT_KEEP_NEWEST = 500

#: Age floor. Nothing younger than this is ever reaped, whatever the count.
DEFAULT_MIN_AGE_DAYS = 30

_INDEX = "index.json"


def _install_dir() -> Path:
    """The RESOLVED GRIP install directory — never a guessed home path.

    Two literals used to stand in for this, and the worse of the pair named
    the directory holding the ONLY copy of files this tool then UNLINKS:
    ``Path.home() / ".grip" / "backups"`` hardcodes one machine's
    shared-tree name. Here ``~/.claude`` is a symlink to ``~/.grip``, so it
    happened to work; on an install without that symlink the backup lands
    somewhere unrelated to the install while the originals still go. The
    other, ``Path.home() / ".claude" / "projects"``, silently ignores
    ``$CLAUDE_DIR``.

    ``rules/path-portability.md`` (PARAMOUNT) forbids the literal and names
    ``lib/claude_dir.py::get_claude_dir()`` as the canonical resolver.

    AGORA downgraded this concern as "unquotable" for citing no source
    line. A downgrade judges citation FORM, not substance — the defect is
    real, and it is the second one this module shipped in that class.
    """
    import sys
    lib = str(Path(__file__).resolve().parent)
    if lib not in sys.path:          # idempotent: reap() may call this often
        sys.path.insert(0, lib)
    from claude_dir import get_claude_dir  # noqa: E402
    return get_claude_dir()


def _safe_mtime(p: Path) -> float:
    """mtime, or +inf when the file cannot be stat'ed — FAIL-SAFE.

    The sentinel MUST mean "maximally new", not "maximally old". An earlier
    version returned 0.0 (the epoch), which inverted the age guard: any stat
    failure that is not a vanish — EACCES on a readable-but-not-searchable
    directory, EIO, a transient network mount — read as older than every
    floor AND sorted to the oldest end, so it failed BOTH guards at once and
    was handed straight to the unlink loop. That falsified this module's own
    claim that either guard alone spares a file.

    Reproduced before the fix (AGORA review of GRIP#5901, concern 1): a file
    created seconds earlier, with stat raising EACCES, was selected for
    reaping. +inf spares it twice over — newest by sort, youngest by age.
    """
    try:
        return p.stat().st_mtime
    except OSError:
        return float("inf")


def _safe_size(p: Path) -> int:
    """Size, or 0 when unstattable.

    0 is correct HERE only because unstattable files can no longer be
    selected (see _safe_mtime): a 0 that used to under-report the dry run's
    byte total while still counting the file as reapable is now unreachable
    from the selection path.
    """
    try:
        return p.stat().st_size
    except OSError:
        return 0


def unstattable(paths: list) -> list:
    """Paths that cannot be stat'ed. Surfaced so sparing them is VISIBLE.

    Silently sparing a file is still a silent behaviour change; the operator
    should see that the store contains entries this tool refuses to judge.
    """
    out = []
    for p in paths:
        try:
            p.stat()
        except OSError:
            out.append(p)
    return out


def candidates(grip_dir: Path) -> list:
    """Every instance file in the store, newest-first. index.json excluded."""
    if not grip_dir.exists():
        return []
    try:
        entries = [Path(e.path) for e in os.scandir(grip_dir)
                   if e.is_file() and e.name.endswith(".json") and e.name != _INDEX]
    except OSError:
        return []
    entries.sort(key=_safe_mtime, reverse=True)
    return entries


def _check_limits(keep_newest: int, min_age_days: int) -> None:
    """Refuse negative retention limits — they BROADEN a destructive op.

    Both safeguards used to vanish silently on a stray minus sign, and they
    vanished together: ``keep_newest < 0`` skipped the newest-N slice
    entirely (the guard was ``if keep_newest > 0``), while
    ``min_age_days < 0`` was clamped to 0 by ``max(0, ...)``. So
    ``--keep-newest=-1 --min-age-days=-1 --apply`` read as "keep nothing,
    spare nothing" and selected the WHOLE store, while _render still printed
    the negative numbers back as though they were the policy in force.

    Raising is the only reading of a negative that cannot widen the blast
    radius. (Codex P2 on GRIP#5901.)
    """
    if keep_newest < 0:
        raise ValueError(f"--keep-newest must be >= 0, got {keep_newest}")
    if min_age_days < 0:
        raise ValueError(f"--min-age-days must be >= 0, got {min_age_days}")


def select_reapable(grip_dir: Path, *,
                    keep_newest: int = DEFAULT_KEEP_NEWEST,
                    min_age_days: int = DEFAULT_MIN_AGE_DAYS,
                    now: float | None = None) -> list:
    """Files that are BOTH outside the newest-N AND older than the age floor.

    The intersection is deliberate: either guard alone spares a file, so a
    mistaken cap cannot reap something recent, and a clock skew cannot reap
    something the operator just wrote.
    """
    _check_limits(keep_newest, min_age_days)
    now = time.time() if now is None else now
    files = candidates(grip_dir)
    if keep_newest > 0:
        files = files[keep_newest:]
    floor = min_age_days * 86400
    return [p for p in files if (now - _safe_mtime(p)) > floor]


def stats(paths: list) -> dict:
    """Count and bytes for a selection. Pure."""
    total = sum(_safe_size(p) for p in paths)
    return {"files": len(paths), "bytes": total,
            "gb": round(total / 1073741824, 2)}


def _archive(paths: list, archive_path: Path) -> dict:
    """Tar the selection, then READ IT BACK and verify every member is present.

    A backup that cannot be read is not a backup (bin/grip-kono-prune.py).
    Raises on any shortfall, so the caller never proceeds to delete.
    """
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tar:
        for p in paths:
            try:
                tar.add(p, arcname=p.name)
            except OSError:
                continue  # vanished mid-run; verified below
    with tarfile.open(archive_path, "r:gz") as tar:
        stored = {m.name: m.size for m in tar.getmembers()}
    # Names AND sizes. A name-only check passes a member whose header is
    # present but whose payload is short or empty — the archive would look
    # complete while holding nothing (AGORA review of GRIP#5901, concern 3).
    missing = []
    for p in paths:
        if not p.exists():
            continue                       # genuine vanish; nothing to protect
        if p.name not in stored:
            missing.append(f"{p.name} (absent)")
        elif stored[p.name] != _safe_size(p):
            missing.append(f"{p.name} (size {stored[p.name]} != {_safe_size(p)})")
    if missing:
        raise RuntimeError(
            f"archive incomplete: {len(missing)} file(s) unverified in "
            f"{archive_path} (first: {missing[0]}) — refusing to delete")
    return {"path": str(archive_path), "members": len(stored),
            "bytes": _safe_size(archive_path)}


def reap(grip_dir: Path, *,
         keep_newest: int = DEFAULT_KEEP_NEWEST,
         min_age_days: int = DEFAULT_MIN_AGE_DAYS,
         apply: bool = False,
         archive_dir: Path | None = None,
         now: float | None = None) -> dict:
    """One reap pass. DRY-RUN unless ``apply=True``.

    Dry run touches nothing and returns what WOULD go. With ``apply=True`` the
    selection is archived and the archive verified BEFORE a single unlink; any
    archive shortfall raises and nothing is deleted.

    Returns a summary dict; ``deleted`` is 0 on a dry run by construction.
    """
    selected = select_reapable(grip_dir, keep_newest=keep_newest,
                               min_age_days=min_age_days, now=now)
    summary = {
        "grip_dir": str(grip_dir),
        "kept_newest": keep_newest,
        "min_age_days": min_age_days,
        "selected": stats(selected),
        "unstattable": len(unstattable(candidates(grip_dir))),
        "applied": bool(apply),
        "deleted": 0,
        "archive": None,
    }
    if not apply or not selected:
        return summary
    archive, deleted = _archive_then_delete(selected, archive_dir)
    summary["archive"] = archive
    summary["deleted"] = deleted
    return summary


def _reserve_archive_path(adir: Path, ts: str) -> Path:
    """Claim an archive name that can NEVER clobber an existing one.

    ``tarfile.open(..., "w:gz")`` TRUNCATES. The name carries a one-second
    timestamp, so two --apply runs starting in the same second — concurrent
    cleanups of two project stores, say — resolved to the same path, and the
    second run would overwrite the only backup of files the FIRST run had
    already unlinked. Verified-then-deleted is worth nothing if a later run
    can silently empty the archive that made the delete safe.

    O_CREAT|O_EXCL makes that unrepresentable: the loser of the race takes
    the next free name instead of the winner's bytes. (Codex P2 on
    GRIP#5901; AGORA speaker A raised the same collision.)
    """
    adir.mkdir(parents=True, exist_ok=True)
    base = f"instance-store-{ts}"
    for n in range(1000):
        cand = adir / (f"{base}.tar.gz" if n == 0 else f"{base}-{n}.tar.gz")
        try:
            fd = os.open(cand, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(fd)
        return cand
    raise RuntimeError(f"could not reserve a free archive name under {adir}")


def _archive_then_delete(selected: list, archive_dir: Path | None) -> tuple:
    """Archive the selection, verify it, THEN unlink. Returns (archive, count).

    Split out of reap() to keep each function under the 30-line KISS limit —
    and the split is the honest shape anyway: reap() decides WHAT goes, this
    decides HOW it goes, and the verified archive is the gate between them.
    _archive raises on any shortfall, so an unverified archive never reaches
    the unlink loop.
    """
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    adir = archive_dir or (_install_dir() / "backups")
    archive = _archive(selected, _reserve_archive_path(adir, ts))
    deleted = 0
    for p in selected:
        try:
            p.unlink()
            deleted += 1
        except OSError:
            continue
    return archive, deleted


def _render(s: dict) -> str:
    sel = s["selected"]
    head = "APPLIED" if s["applied"] else "DRY RUN — nothing was changed"
    lines = [
        f"instance-store reaper — {head}",
        f"  store        : {s['grip_dir']}",
        f"  policy       : keep newest {s['kept_newest']}, "
        f"and everything under {s['min_age_days']} days",
        f"  reapable     : {sel['files']} files, {sel['gb']} GB",
    ]
    if s["archive"]:
        a = s["archive"]
        lines.append(f"  archived     : {a['members']} members -> {a['path']}")
    if s["applied"]:
        lines.append(f"  deleted      : {s['deleted']} files")
    else:
        lines.append("  re-run with --apply to archive and delete")
    if s.get("unstattable"):
        lines.append(f"  unstattable  : {s['unstattable']} file(s) SPARED "
                     "(cannot be stat'ed, so never judged)")
    lines.append("  NOTE: index.json is never touched — identity, lineage,")
    lines.append("        summary and content_hash survive; what is lost is")
    lines.append("        the ability to RESURRECT those sessions.")
    lines.append("  NOTE: --apply leaves a SECOND COPY of the selection as a")
    lines.append("        .tar.gz archive, and NOTHING prunes that directory.")
    lines.append("        Measured on a full 10,061-file run, gzip gives 2.2x,")
    lines.append("        so the archive is ~45% of what was reaped and each")
    lines.append("        run adds another one. Prune it yourself.")
    return "\n".join(lines)


def main(argv: list | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="grip-instance-reaper",
        description="Bounded retention for the session instance store. "
                    "Dry-run by default.")
    ap.add_argument("grip_dir", nargs="?",
                    help="path to projects/<slug>/grip (default: this project's)")
    ap.add_argument("--apply", action="store_true",
                    help="archive then delete (default: dry run)")
    ap.add_argument("--keep-newest", type=int, default=DEFAULT_KEEP_NEWEST)
    ap.add_argument("--min-age-days", type=int, default=DEFAULT_MIN_AGE_DAYS)
    ap.add_argument("--archive-dir", default=None)
    ap.add_argument("--json", action="store_true", help="emit the summary as JSON")
    args = ap.parse_args(argv)

    if args.grip_dir:
        gd = Path(args.grip_dir)
    else:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from path_encoding import encode_project_path  # noqa: E402
        gd = (_install_dir() / "projects"
              / encode_project_path(Path.cwd()) / "grip")

    try:
        summary = reap(gd, keep_newest=args.keep_newest,
                       min_age_days=args.min_age_days, apply=args.apply,
                       archive_dir=Path(args.archive_dir) if args.archive_dir else None)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2
    print(json.dumps(summary, indent=2) if args.json else _render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
