#!/usr/bin/env python3
"""
grip-remember — durably remember a rule / fact / lesson FOREVER.

The deterministic backend of the /remember skill. Named for Mufasa's charge to
Simba: "Remember who you are." A thing GRIP /remember-s is written to the tier
that guarantees it is re-loaded every session, so it can never be silently
forgotten:

  tier=paramount  ->  rules/<slug>.md, routed by --channel, plus ONE line in
                      CLAUDE.md under "Remembered laws" (loaded every session).
  tier=rule       ->  rules/<slug>.md, routed by --channel.
  tier=memory     ->  <memory-dir>/<slug>.md  +  a one-line pointer prepended
                      into MEMORY.md (the session-inherited index).

A rule is ROUTED (KERN v2, phase 0): --channel says which readers get it, and
when, and the backend lists it in config/rules-scope.json to match, so a rule
can no longer merge unclassified (GRIP#6957 and GRIP#7191 both did):

  floor  loads natively in every session and every subagent (safety-core).
  main   reaches the main session only. Until the main-session tier lands it
         arrives there as one line of the SessionStart index (extended), and
         settings.json claudeMdExcludes keeps it out of native loading.
  file   loads when a file matching --paths is read or written, in any session
         or subagent (``paths:`` frontmatter).

A rule's narrative -- its origin, incidents and measurements -- goes to its
annex, docs/doctrine/<slug>.md, which nothing loads at startup; the rule keeps
the operative text and one line pointing at the annex. After writing a rule the
backend prints how the always-loaded total changed: the figure Claude Code warns
about at startup, which grew back 26,000 characters in six days after KERN's
Option C because nothing reported it.

Idempotent: re-remembering the same slug overwrites its files, moves it between
classification lists if its channel changed, and never double-appends the
CLAUDE.md pointer or duplicates the MEMORY.md line.

Stdlib plus in-tree modules. Path-portable via lib/claude_dir.get_claude_dir().
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import rules_scope  # noqa: E402
from asset_counter import count_inventory, fix_capabilities_line, fix_marked_count_lines  # noqa: E402
from claude_dir import get_claude_dir  # noqa: E402
from lexicon import measure as kern_measure  # noqa: E402
from lexicon import surfaces as kern_surfaces  # noqa: E402


# MEMORY.md is truncated on session load past ~24.4 KiB of characters, and the
# tail is what vanishes. Measured 2026-09-19: the index stood at 29,804 bytes and
# its last 20 pointers reached no session at all. This writer is operator-paced
# rather than per-session, so it is a slower growth surface than the auto-capture
# one -- but an uncapped line is an uncapped line, and /remember exists precisely
# so the record is NOT silently lost.
INDEX_ENTRY_CAP = 200

SCOPE_REL = "config/rules-scope.json"
ANNEX_DIR = "docs/doctrine"

# The channels that exist today. The task-brief and command channels arrive with
# their hooks in phase 2; offering them before then would route a rule nowhere.
CHANNELS = {
    "floor": "loads in every session and every subagent",
    "main": "reaches the main session only, as one line of its SessionStart index until "
            "the main-session tier lands",
    "file": "loads when a file matching --paths is read or written",
}
SCOPE_LIST = {"floor": "safety_core_explicit", "main": "extended", "file": "safety_core_explicit"}

# A PARAMOUNT rule gets one line in CLAUDE.md, every session's first read. The
# old pointer was a heading plus a paragraph; eight of them sat at the end of the
# global CLAUDE.md when KERN v2 began. One sentence is what an index of laws needs.
LAWS_HEADING = "## Remembered laws"
LAWS_INTRO = "One line per law added by /remember; each rule file is authoritative."
POINTER_DESC_CAP = 200


def _index_pointer(title: str, slug: str, desc: str) -> str:
    """One capped, single-line index pointer.

    Whitespace is collapsed first: a newline in the description would split the
    pointer across two lines and leave the remainder as an orphan bullet.
    """
    title = " ".join(title.split())
    desc = " ".join(desc.split())
    prefix = f"- [{title}]({slug}.md)"
    if not desc:
        return prefix + "\n"
    room = INDEX_ENTRY_CAP - len(prefix) - len(" — ") - 1
    if room <= 0:
        return prefix + "\n"
    if len(desc) > room:
        desc = desc[: max(room - 1, 0)].rstrip() + "…"
    return f"{prefix} — {desc}\n"


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _resolve_memory_dir(explicit: str | None) -> Path:
    """The GRIP project memory dir, resolved DETERMINISTICALLY.

    Order: explicit --memory-dir > the canonical project-dir slug for THIS install
    (the harness cwd-slug encoding: the CLAUDE_DIR path with '/' and '.' mapped to
    '-', per rules/bash-safety.md) > the sole memory dir if exactly one exists >
    loud fail requiring --memory-dir. This never uses the old 'largest MEMORY.md'
    heuristic, which a stale large index in an unrelated project would silently win —
    breaking the every-session guarantee for the intended project (AGORA #4122)."""
    if explicit:
        return Path(explicit)
    claude_dir = get_claude_dir()
    slug = str(claude_dir).replace("/", "-").replace(".", "-")
    canonical = claude_dir / "projects" / slug / "memory"
    if (canonical / "MEMORY.md").exists():
        return canonical
    candidates = list((claude_dir / "projects").glob("*/memory/MEMORY.md"))
    if len(candidates) == 1:
        return candidates[0].parent
    if not candidates:
        raise SystemExit("remember: no projects/*/memory/MEMORY.md found; pass --memory-dir")
    raise SystemExit(
        "remember: multiple projects/*/memory/ and no canonical slug match "
        f"({slug!r}); pass --memory-dir explicitly."
    )


def annex_path(repo: Path, slug: str) -> Path:
    """Where a rule's narrative lives: the same path lib/lexicon/ledger.py uses."""
    return repo / ANNEX_DIR / f"{slug}.md"


def write_annex(repo: Path, slug: str, title: str, narrative: str) -> Path:
    path = annex_path(repo, slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}: annex\n\n"
                    f"The narrative behind `rules/{slug}.md`: its origin, incidents and "
                    "measurements. The rule holds the operative text; nothing here loads "
                    f"at startup.\n\n{narrative.strip()}\n", encoding="utf-8")
    return path


def _paths_frontmatter(paths: list[str]) -> str:
    """The ``paths:`` block Claude Code reads, in the form rules/cc-enforcement.md uses."""
    return "---\npaths:\n" + "".join(f"  - {json.dumps(p)}\n" for p in paths) + "---\n\n"


def write_rule(repo: Path, slug: str, title: str, body: str,
               paths: list[str] | None = None, annexed: bool = False) -> Path:
    rp = repo / "rules" / f"{slug}.md"
    rp.parent.mkdir(parents=True, exist_ok=True)
    doc = body if body.lstrip().startswith("#") else f"# {title}\n\n{body.rstrip()}\n"
    if annexed:
        doc = (doc.rstrip() + "\n\nNarrative (origin, incidents, measurements): "
               f"`{ANNEX_DIR}/{slug}.md`.\n")
    if paths:
        doc = _paths_frontmatter(paths) + doc
    rp.write_text(doc)
    return rp


def _with_readers(cfg: dict, name: str, tags: list[str]) -> dict:
    """``cfg`` with rules/<name> tagged, the table kept in name order after its _doc."""
    table = dict(cfg.get("readers") or {})
    table[name] = sorted(set(tags))
    docs = {k: v for k, v in table.items() if k.startswith("_")}
    rules = sorted(((k, v) for k, v in table.items() if not k.startswith("_")),
                   key=lambda kv: kv[0].lower())
    return {**cfg, "readers": {**docs, **dict(rules)}}


def classify_rule(repo: Path, name: str, channel: str, tags: list[str]) -> list[tuple[Path, str]]:
    """List rules/<name> in config/rules-scope.json for its channel, tagged with who it
    is for, and keep the claudeMdExcludes of every settings file present in step with
    the extended list: settings.json and the reference pair a fresh install is built from.

    Returns each file changed, with what changed in it. A repo without the
    config returns nothing: such a rule loads natively everywhere until it is
    classified, and the caller says so.
    """
    path = repo / SCOPE_REL
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    cfg = json.loads(text)
    target = SCOPE_LIST[channel]
    other = "extended" if target == "safety_core_explicit" else "safety_core_explicit"
    cfg[other] = [n for n in cfg.get(other, []) if n != name]
    if name not in cfg.setdefault(target, []):
        cfg[target].append(name)
    cfg = _with_readers(cfg, name, tags)
    changed = []
    new_text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
        changed.append((path, f"rule listed in {target}, for {', '.join(sorted(set(tags)))}"))
    changed += [(p, "claudeMdExcludes resynced") for p in rules_scope.sync_settings_files(repo, cfg)]
    return changed


def refresh_inventory_counts(repo: Path, new_rule: Path) -> list[Path]:
    """Regenerate the inventory counts so they include new_rule.

    The doc-drift hook does this only for Write and Edit tool calls, and this
    script writes files directly, so a rule /remember wrote left the stats line
    one short until someone noticed (GRIP#7487, GRIP#7528). The counter counts
    only files git tracks, so the new rule is staged first; /remember commits it
    next anyway. Outside a git checkout the staging fails harmlessly and every
    file counts. Returns the files that changed, for the caller to commit.
    """
    try:
        subprocess.run(["git", "-C", str(repo), "add", "--", str(new_rule)],
                       capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        pass
    changed = []
    if fix_capabilities_line(repo):
        changed.append(repo / "rules" / "system-capabilities.md")
    if fix_marked_count_lines(repo / "README.md", count_inventory(repo)):
        changed.append(repo / "README.md")
    return changed


def claude_pointer_line(slug: str, title: str, desc: str) -> str:
    """The one line a PARAMOUNT rule gets in CLAUDE.md. The marker is metadata, not prose."""
    return (f"- **{' '.join(title.split())}**: {' '.join(desc.split())} "
            f"Rule: `rules/{slug}.md`. <!-- remember:paramount:{slug} -->")


def _insert_in_section(text: str, heading: str, line: str) -> str:
    """``text`` with ``line`` added as the last line of the ``heading`` section,
    which is created at the end of the file when it is absent."""
    lines = text.rstrip("\n").split("\n")
    if heading not in lines:
        return "\n".join(lines) + f"\n\n{heading}\n\n{LAWS_INTRO}\n\n{line}\n"
    start = lines.index(heading)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[:end] + [line] + lines[end:]) + "\n"


def append_claude_pointer(repo: Path, slug: str, title: str, desc: str) -> Path | None:
    cm = repo / "CLAUDE.md"
    if not cm.exists():
        return None
    text = cm.read_text()
    marker = f"<!-- remember:paramount:{slug} -->"
    # Idempotent on the exact marker (robust to refactors that move the rules/<slug>.md
    # mention) OR a pre-existing rules/<slug>.md reference (back-compat with pointer
    # blocks written before the marker existed).
    if marker in text or f"rules/{slug}.md" in text:
        return None
    cm.write_text(_insert_in_section(text, LAWS_HEADING, claude_pointer_line(slug, title, desc)))
    return cm


def write_memory(mem_dir: Path, slug: str, title: str, desc: str, body: str,
                 kind: str = "reference") -> tuple[Path, Path]:
    mem_dir.mkdir(parents=True, exist_ok=True)
    mf = mem_dir / f"{slug}.md"
    # `desc` is arbitrary prose, so it is emitted as a JSON string rather than
    # interpolated bare. YAML 1.2 is a superset of JSON, so json.dumps() yields a
    # valid double-quoted scalar with every quote, backslash, colon and newline
    # already escaped.
    #
    # Bare interpolation produced UNPARSEABLE frontmatter whenever the prose held
    # ": " (YAML reads a nested mapping) or opened with a quote or backtick (YAML
    # starts a quoted scalar and never finds its end). Measured 2026-09-17: 8 of
    # the corpus's 42 invalid-frontmatter records came from THIS writer, the rest
    # from the same bug in lib/auto_memory_writer.py, fixed in GRIP#6096.
    #
    # `slug` is a hyphenated identifier and cannot contain a colon, so it is left
    # bare -- quoting it would churn every existing record for no gain.
    fm = (
        "---\n"
        f"name: {slug}\n"
        f"description: {json.dumps(desc)}\n"
        "metadata:\n"
        f"  type: {kind}\n"
        "---\n\n"
        f"{body.rstrip()}\n"
    )
    mf.write_text(fm)
    index = mem_dir / "MEMORY.md"
    pointer = _index_pointer(title, slug, desc)
    if index.exists():
        lines = index.read_text()
        if f"]({slug}.md)" not in lines:  # idempotent
            marker = "<!-- GRIP-BRIEF-END -->\n"
            if marker in lines:
                head, _, tail = lines.partition(marker)
                index.write_text(head + marker + "\n" + pointer + tail.lstrip("\n"))
            else:
                index.write_text(pointer + lines)
    else:
        index.write_text(pointer)
    return mf, index


def _measured(read) -> int | None:
    """One figure, or None when it cannot be read: measuring reports on a write
    and is never a condition of it."""
    try:
        return read()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return None


def _sizes(repo: Path) -> dict[str, int | None]:
    """The always-loaded total and the SessionStart index for ``repo``.

    Each is read on its own, so a SessionStart hook that fails here hides only
    the index figure, never the always-loaded change or the ratchet warning
    (the falsifier AGORA's technical speaker named on GRIP#7669).
    """
    return {"always_loaded": _measured(lambda: sum(s.chars for s in kern_surfaces.always_loaded(repo))),
            "index": _measured(lambda: kern_measure.injected_chars(repo))}


def _warning_chars() -> int:
    return next(b.limit for b in kern_measure.BUDGETS
                if b.metric == "always_loaded_chars" and b.level == "ceiling")


def _ratchet(repo: Path) -> int | None:
    """The always-loaded ratchet, or None when there is none or it cannot be read. It
    only colours the size report, which runs after the rule is written, so a malformed
    value must not crash the command at that point (int() on it did)."""
    path = repo / SCOPE_REL
    if not path.is_file():
        return None
    try:
        return kern_measure.ratchet_limits(json.loads(path.read_text(encoding="utf-8"))).get(
            "always_loaded_chars")
    except ValueError:
        return None


def _always_loaded_lines(was: int | None, now: int | None, ratchet: int | None) -> list[str]:
    if was is None or now is None:
        return ["always-loaded instruction files: not measured; this checkout could not be read"]
    lines = [f"always-loaded instruction files: {was:,} -> {now:,} characters "
             f"({now - was:+,}); Claude Code warns above {_warning_chars():,}."]
    if ratchet and now > ratchet:
        lines.append(f"That is {now - ratchet:,} over the ratchet of {ratchet:,} in {SCOPE_REL}, "
                     "so the pull request will carry a size warning.")
    return lines


def size_lines(before: dict, after: dict, ratchet: int | None) -> list[str]:
    """How the write changed what sessions load, in plain sentences."""
    lines = _always_loaded_lines(before["always_loaded"], after["always_loaded"], ratchet)
    was, now = before["index"], after["index"]
    if was is None or now is None:
        lines.append("main-session index: not measured; its SessionStart hook failed here")
    elif now != was:
        lines.append(f"main-session index: {was:,} -> {now:,} characters ({now - was:+,}).")
    return lines


# Where the thing now lives, said truthfully per route. The old confirmation said
# every remembered rule is "re-read at the start of every session", which is
# false for main (one index line) and file (loads on a matching file): Codex on
# GRIP#7669.
REACH = {
    "floor": "It loads at the start of every session and in every subagent.",
    "main": "The main session sees it at every start, as one line of its index, "
            "until the main-session tier lands; subagents do not.",
    "file": "It loads whenever a file matching its paths is read or written.",
    "memory": "Its pointer sits at the top of MEMORY.md, the index every session loads.",
}


def confirmation(tier: str, channel: str | None, tags=()) -> str:
    """The closing Mufasa lines, naming where the thing is now read, and who needs it
    but will not receive it yet."""
    lines = ['\n  "Remember who you are." — Mufasa', "  It is written into GRIP now.",
             "  " + REACH[channel or "memory"]]
    unserved = sorted(set(tags) & rules_scope.SUBAGENT_READERS) if channel == "main" else []
    if unserved:
        lines.append(f"  Subagents that need it ({', '.join(unserved)}) do not receive it until "
                     "the task brief lands (KERN v2 phase 2).")
    if tier == "paramount":
        lines.append("  Its one line in CLAUDE.md is read at the start of every session.")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Remember a rule/fact/lesson forever.")
    ap.add_argument("--tier", choices=["paramount", "rule", "memory"], required=True)
    ap.add_argument("--slug", required=True, help="short kebab-case identifier")
    ap.add_argument("--title", required=True)
    ap.add_argument("--desc", required=True, help="one-line summary")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--body", help="inline body text")
    src.add_argument("--body-file", help="path to body text")
    ap.add_argument("--channel", choices=sorted(CHANNELS),
                    help="a rule's route: " + "; ".join(f"{k} {v}" for k, v in CHANNELS.items()))
    ap.add_argument("--paths", action="append",
                    help="with --channel file: a glob of the files that load the rule (repeatable)")
    ap.add_argument("--readers", action="append", choices=sorted(rules_scope.READERS),
                    help="who a rule is for (repeatable): " + ", ".join(sorted(rules_scope.READERS)))
    narrative = ap.add_mutually_exclusive_group()
    narrative.add_argument("--narrative", help="a rule's origin, incidents and measurements")
    narrative.add_argument("--narrative-file", help="path to the narrative text")
    ap.add_argument("--repo", default=None, help="repo root for rules/CLAUDE.md (default: CLAUDE_DIR)")
    ap.add_argument("--memory-dir", default=None, help="tier=memory target dir (default: auto)")
    ap.add_argument("--kind", default="reference",
                    choices=["user", "feedback", "project", "reference"])
    return ap


def _refuse(message: str) -> None:
    raise SystemExit(f"remember: {message}")


def _pinned_route_allowed(channel: str, name: str, repo: Path) -> bool:
    """A pinned rule loads at every start, or at the file moment when the repo's config lists
    it in `file_moment` (KERN v2 ruling 2(a), 2026-10-18). /remember follows that list and
    never adds to it, so it cannot grant a pinned rule a route the operator did not rule."""
    if channel == "floor":
        return True
    path = repo / SCOPE_REL
    if channel != "file" or not path.is_file():
        return False
    cfg = rules_scope.load_config(path)
    return bool(cfg) and name in rules_scope.file_moment(cfg)


def _check_readers(a: argparse.Namespace, name: str, repo: Path) -> None:
    """A rule says who it is for, and a pinned rule keeps its pinned readers and its route."""
    if not a.readers:
        _refuse("a rule needs --readers, who it is for (repeatable): "
                + ", ".join(sorted(rules_scope.READERS)))
    # One check for every pinned rule: MUST_BE_SAFETY_CORE is a tested subset of
    # MUST_REACH, so its own channel refusal was a duplicate (AGORA on GRIP#7687).
    if name in rules_scope.MUST_REACH and not _pinned_route_allowed(a.channel, name, repo):
        _refuse(f"rules/{name} is pinned to reach its readers at every start, so it stays on "
                "--channel floor: main reaches no subagent, and file loads only on a matching "
                "file (Codex on GRIP#7687). --channel file is allowed only when "
                f"{SCOPE_REL} `file_moment` lists the rule (KERN v2 ruling 2(a))")
    missing = sorted(rules_scope.MUST_REACH.get(name, frozenset()) - set(a.readers))
    if missing:
        _refuse(f"rules/{name} must keep the readers lib/rules_scope.MUST_REACH pins: {missing}")


def _check_rule_args(a: argparse.Namespace, slug: str, repo: Path) -> None:
    """Refuse a rule that would be routed wrongly, before anything is written."""
    if not a.channel:
        _refuse("a rule needs --channel, its route: "
                + "; ".join(f"{k} {v}" for k, v in CHANNELS.items()))
    if a.channel == "file" and not a.paths:
        _refuse("--channel file needs --paths: the globs of the files that load the rule")
    if a.paths and a.channel != "file":
        _refuse("--paths goes with --channel file")
    _check_readers(a, f"{slug}.md", repo)
    summary = " ".join(a.desc.split())
    if a.tier == "paramount" and len(summary) > POINTER_DESC_CAP:
        _refuse(f"the one-line summary is {len(summary)} characters; its CLAUDE.md line carries "
                f"one sentence of at most {POINTER_DESC_CAP}")


def _check_memory_args(a: argparse.Namespace) -> None:
    if a.channel or a.paths or a.readers or a.narrative or a.narrative_file:
        _refuse("--channel, --paths, --readers and --narrative route a rule; "
                "a memory record is indexed in MEMORY.md instead")


def _remember_rule(a: argparse.Namespace, repo: Path, slug: str, body: str) -> list[str]:
    narrative = Path(a.narrative_file).read_text() if a.narrative_file else a.narrative
    written = []
    if narrative:
        written.append(f"{write_annex(repo, slug, a.title, narrative)} (narrative annex)")
    annexed = bool(narrative) or annex_path(repo, slug).is_file()
    rule = write_rule(repo, slug, a.title, body, paths=a.paths, annexed=annexed)
    written.append(f"{rule} (channel {a.channel}: {CHANNELS[a.channel]})")
    written += [f"{path} ({what})" for path, what in
                classify_rule(repo, f"{slug}.md", a.channel, a.readers)]
    if not (repo / SCOPE_REL).is_file():
        written.append(f"{SCOPE_REL} not found: the rule loads everywhere until it is classified")
    written += [f"{p} (inventory count refreshed)" for p in refresh_inventory_counts(repo, rule)]
    if a.tier == "paramount":
        cm = append_claude_pointer(repo, slug, a.title, a.desc)
        written.append(f"{cm} (one line under '{LAWS_HEADING[3:]}')" if cm
                       else "CLAUDE.md (pointer already present: idempotent)")
    return written


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    repo = Path(a.repo) if a.repo else get_claude_dir()
    slug = slugify(a.slug)
    if a.tier == "memory":
        _check_memory_args(a)
    else:
        _check_rule_args(a, slug, repo)
    body = Path(a.body_file).read_text() if a.body_file else a.body
    sizes: list[str] = []
    if a.tier == "memory":
        mf, idx = write_memory(_resolve_memory_dir(a.memory_dir), slug, a.title,
                               a.desc, body, a.kind)
        written = [str(mf), f"{idx} (pointer added)"]
    else:
        before = _sizes(repo)
        written = _remember_rule(a, repo, slug, body)
        sizes = size_lines(before, _sizes(repo), _ratchet(repo))

    print(f"REMEMBERED [{a.tier}] {a.title!r}:")
    for w in written:
        print("  +", w)
    for line in sizes:
        print("  ", line)
    print(confirmation(a.tier, a.channel, a.readers or ()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
