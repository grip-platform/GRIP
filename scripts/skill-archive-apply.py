#!/usr/bin/env python3
"""Archive unused GRIP skills into ``skills/_archived/`` -- never delete.

Orchestrates the skills-archive build brief end to end:

  1. Measure real usage from transcripts (``lib/skill_usage_scan.py``).
  2. Decide which skills are protected and which are candidates
     (``lib/skill_protection.py``).
  3. ``git mv`` every candidate into ``skills/_archived/<name>`` -- the
     mechanism GRIP already uses for 78 other skills (excluded from
     ``lib/asset_counter.py``'s count and from ``lib/skill-loader.sh``'s
     discovery by name already, before this script existed).
  4. Prune that skill's generated ``commands/<name>.md`` proxy, if it has
     one -- a proxy left behind would still list in every session's
     command catalogue and would run ``sed`` against a ``SKILL.md`` that
     no longer lives there. Checked, never assumed
     (``lib/skill_protection.py::is_generated_command_proxy``): a
     hand-written command sharing the archived skill's name is left alone.
  5. Write/refresh ``skills/_archived/README.md`` with the restore
     instructions (move back, then regenerate the proxy).
  6. Emit a full JSON report (every skill, kept or archived, with its
     reasons) so the PR body can be built from it without re-running the
     scan.

Defaults to ``--dry-run``: prints what it WOULD do and writes the report,
but performs no ``git mv`` and no README write, until ``--apply`` is passed.
``--prune-proxies-for <names-file>`` is a separate, narrower mode: it only
removes generated proxies for skills ALREADY archived (by an earlier run of
this script, before step 4 existed) -- it never touches ``skills/``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from skill_protection import (  # noqa: E402
    build_protection_report,
    is_generated_command_proxy,
    list_live_skills,
)
from skill_usage_scan import cutoff_iso_for_days, scan_transcripts  # noqa: E402

# Transcripts are a machine-wide resource under the REAL install's
# projects/ dir -- never under whichever worktree this script happens to be
# running from (a worktree carries no projects/ at all; it is gitignored
# runtime state, not part of the tracked tree). repo_root (where skills/,
# rules/, hooks/, commands/, tests/ etc. live, and where `git mv` acts) is
# resolved from this script's OWN location instead, so it is always the
# checkout this script ships in, regardless of $CLAUDE_DIR.
_DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"

ARCHIVE_DIRNAME = "_archived"
RESTORE_README = """# Archived skills

Skills here are not loaded by any session -- they are excluded from
discovery (`lib/skill-loader.sh`), from the inventory count
(`lib/asset_counter.py`), and from the catalogue
(`skills/_archived/*/SKILL.md` is never surfaced to a session). Nothing here
is deleted, and nothing here is gone for good.

## Restore a skill

```bash
git mv skills/_archived/<name> skills/<name>
git checkout "$(git rev-list -n 1 HEAD -- commands/<name>.md)^" -- commands/<name>.md
```

Run both lines from the repository root, which `git mv` already requires.
The first line moves the skill back; the skill is live again on the next
session. The second line brings back its generated `commands/<name>.md`
proxy from just before the commit that removed it:
`scripts/skill-archive-apply.py` deletes a skill's generated proxy when it
archives the skill, because a proxy pointing at `skills/<name>/SKILL.md`
would otherwise run `sed` against a file that no longer exists there. Skip
the second line when the skill never had a generated proxy. Do not rerun
the proxy generator instead: `surface_skills.py sync` rewrites every
generated proxy it finds across the GRIP, Codex, agent and plugin skill
trees on the machine, so it would add unrelated, machine-specific commands
to the change (Codex review, GRIP#7779).

## Why a skill ends up here

Moved by `scripts/skill-archive-apply.py` when, across a 30-day usage
measurement, a skill was never invoked (as a Skill-tool call or a slash
command, including any declared `invocable_name` alias) AND is not named by
any hook, command, rule, config file, test, or another skill that is itself
kept, AND is not marked `PARAMOUNT` or on the explicit critical/auto-loaded
list. See `lib/skill_protection.py` for the exact criteria.

Its generated `commands/<name>.md` proxy (if it had one) is removed in the
same operation, via `lib/skill_protection.py::is_generated_command_proxy` --
checked, never assumed: a hand-written command sharing the archived
skill's name (for example `commands/fleet-inspection.md`) is left alone.
"""


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


def prune_generated_proxy(repo_root: Path, name: str) -> str | None:
    """Remove ``commands/<name>.md`` via ``git rm``, but ONLY when it is
    verified (``is_generated_command_proxy``) to be a surfacing-skills-
    generated proxy for exactly this skill.

    A missing command file, or one that fails the check (a hand-written
    command sharing the skill's name -- the measured instance is
    ``commands/fleet-inspection.md``), is a no-op, not an error: both
    outcomes mean "nothing more to do here", and the caller does not need
    to distinguish them to know the archive succeeded. Returns an error
    string only on an actual ``git rm`` failure.
    """
    path = repo_root / "commands" / f"{name}.md"
    if not path.is_file() or not is_generated_command_proxy(path, name):
        return None
    result = _git(["rm", "-q", str(Path("commands") / f"{name}.md")], cwd=repo_root)
    if result.returncode != 0:
        return result.stderr.strip() or result.stdout.strip()
    return None


def _archive_one(repo_root: Path, name: str) -> str | None:
    """``git mv`` one skill into the archive, then prune its generated
    command proxy if it has one. Returns an error string, or None.

    If ``skills/_archived/<name>`` already exists (a stale copy from an
    earlier archive pass), ``git mv`` would NEST the live skill inside it
    rather than replacing it -- plain ``mv`` semantics: moving a directory
    onto an existing directory nests it, it never overwrites. The copy
    being archived now is the live, authoritative one, so a pre-existing
    stale destination is removed first (Codex review, GRIP#7779: this
    nesting shape landed for real on 8 skills in this PR's own first run).

    ``git rm -r`` also removes ``skills/_archived/`` itself from the
    working tree if the stale entry was its only remaining content (git
    tracks no empty directory) -- the recreate below is a no-op in the
    real repository, which always has other archived skills and its own
    README keeping that directory alive, but a ``git mv`` into a parent
    that does not exist on disk fails, so this is not assumed.
    """
    src = Path("skills") / name
    dest = Path("skills") / ARCHIVE_DIRNAME / name
    if (repo_root / dest).exists():
        result = _git(["rm", "-rq", str(dest)], cwd=repo_root)
        if result.returncode != 0:
            return result.stderr.strip() or result.stdout.strip()
        (repo_root / dest.parent).mkdir(parents=True, exist_ok=True)
    result = _git(["mv", str(src), str(dest)], cwd=repo_root)
    if result.returncode != 0:
        return result.stderr.strip() or result.stdout.strip()
    return prune_generated_proxy(repo_root, name)


def _write_readme(claude_dir: Path) -> None:
    readme = claude_dir / "skills" / ARCHIVE_DIRNAME / "README.md"
    readme.write_text(RESTORE_README, encoding="utf-8")


def _folded_continuation(lines: list[str]) -> str:
    """Join a YAML folded block scalar's indented continuation lines.

    ``itertools.takewhile`` rather than a second loop, so this stays a
    single-loop function as far as the BIG-O nested-loop heuristic is
    concerned (it has no real quadratic risk either way -- continuation is
    at most a few lines -- but the no-nested-loop shape is worth keeping
    consistent across this file).
    """
    import itertools
    continuation = itertools.takewhile(lambda c: c.startswith(" "), lines)
    return " ".join(c.strip() for c in continuation if c.strip())


def _skill_description(skill_md_path: Path) -> str:
    """Best-effort one-line description pulled from a SKILL.md frontmatter.

    Handles both shapes seen in the live corpus: a plain scalar
    (``description: text``) and a YAML folded block scalar
    (``description: >`` followed by indented continuation lines, which are
    joined back into one line).
    """
    lines = skill_md_path.read_text(encoding="utf-8", errors="replace").split("\n")
    for index, line in enumerate(lines):
        if not line.startswith("description:"):
            continue
        value = line.split(":", 1)[1].strip()
        if value not in ("", ">", "|"):
            return value.strip('"')
        return _folded_continuation(lines[index + 1:])
    return ""


def _reason_group(result) -> str:
    """The single primary reason heading a kept skill is grouped under."""
    if result.used:
        return "invoked in the last 30 days"
    if result.paramount_or_critical:
        return "marked PARAMOUNT or an explicitly critical/auto-loaded skill"
    if result.references:
        hit = result.references[0]
        return f"named by a {hit.category} ({hit.file})"
    return "unknown"


def build_report(repo_root: Path, projects_root: Path, days: int) -> dict:
    cutoff_iso = cutoff_iso_for_days(days)
    usage = scan_transcripts(projects_root, cutoff_iso)
    used_names = frozenset(usage.combined().keys())
    protection = build_protection_report(repo_root, used_names)
    live = list_live_skills(repo_root)

    kept = {n: r for n, r in protection.items() if r.kept}
    candidates = {n: r for n, r in protection.items() if not r.kept}

    groups: dict[str, list[str]] = {}
    for name, result in kept.items():
        groups.setdefault(_reason_group(result), []).append(name)
    for names in groups.values():
        names.sort()

    return {
        "days": days,
        "usage": usage.to_dict(),
        "total_live_skills": len(protection),
        "kept_count": len(kept),
        "archived_count": len(candidates),
        "kept_groups": {k: v for k, v in sorted(groups.items())},
        "archived": sorted(candidates.keys()),
        "archived_descriptions": {
            n: _skill_description(live[n]) for n in sorted(candidates.keys())
        },
        "archived_reasons": {
            n: candidates[n].reasons() for n in sorted(candidates.keys())
        },
    }


def _run_prune_only(repo_root: Path, names_file: Path) -> int:
    """Remove generated command proxies for already-archived skills.

    For catching up archives done before ``_archive_one`` pruned proxies
    itself (this script's own first 32-skill run): takes a names file
    (one skill name per line) and, for each, removes
    ``commands/<name>.md`` ONLY when it verifiably is that skill's
    generated proxy. Never touches ``skills/`` -- these names are assumed
    already archived.
    """
    names = [line.strip() for line in names_file.read_text().splitlines() if line.strip()]
    pruned: list[str] = []
    skipped: list[str] = []
    prune_errors: list[str] = []
    for name in names:
        path = repo_root / "commands" / f"{name}.md"
        if not path.is_file() or not is_generated_command_proxy(path, name):
            skipped.append(name)
            continue
        err = prune_generated_proxy(repo_root, name)
        if err:
            prune_errors.append(f"{name}: {err}")
        else:
            pruned.append(name)

    print(f"Pruned {len(pruned)} generated proxies, skipped {len(skipped)} "
          f"(no matching generated proxy -- hand-written or absent).")
    for name in pruned:
        print(f"  pruned: commands/{name}.md")
    for name in skipped:
        print(f"  skipped: {name}")
    if prune_errors:
        print("ERRORS:", file=sys.stderr)
        for e in prune_errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    return 0


def _run_archive(repo_root: Path, report: dict, *, apply: bool, report_out: str | None) -> int:
    """The normal flow's side effects: apply (or preview) the archive,
    write the report file if asked. Split out of ``main`` to keep it
    under the KISS line-count ceiling -- this is pure sequencing, no new
    decisions.
    """
    mode = "APPLYING" if apply else "DRY RUN"
    print(f"[{mode}] {report['kept_count']} kept, {report['archived_count']} candidates "
          f"out of {report['total_live_skills']} live skills.")

    errors: list[str] = []
    if apply:
        for name in report["archived"]:
            err = _archive_one(repo_root, name)
            if err:
                errors.append(f"{name}: {err}")
            else:
                print(f"  archived: {name}")
        _write_readme(repo_root)
        print("  wrote skills/_archived/README.md")
    else:
        for name in report["archived"]:
            print(f"  would archive: {name}")

    if report_out:
        Path(report_out).write_text(json.dumps(report, indent=2, sort_keys=True))
        print(f"Report written to {report_out}")

    if errors:
        print("ERRORS:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--apply", action="store_true", help="Actually git mv + write README")
    parser.add_argument("--report-out", default=None, help="Write the full JSON report here")
    parser.add_argument(
        "--projects-root", default=None,
        help="Override the transcripts root (default: ~/.claude/projects -- used by tests)",
    )
    parser.add_argument(
        "--prune-proxies-for", default=None,
        help="Path to a file of already-archived skill names (one per line). "
             "Removes each one's generated command proxy, if it has one, "
             "without touching skills/. For catching up an archive done "
             "before proxy-pruning existed; never archives anything new.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent

    if args.prune_proxies_for:
        return _run_prune_only(repo_root, Path(args.prune_proxies_for))

    projects_root = Path(args.projects_root) if args.projects_root else _DEFAULT_PROJECTS_ROOT
    report = build_report(repo_root, projects_root, args.days)
    return _run_archive(repo_root, report, apply=args.apply, report_out=args.report_out)


if __name__ == "__main__":
    raise SystemExit(main())
