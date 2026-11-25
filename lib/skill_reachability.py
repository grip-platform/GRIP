"""Catalog and reachability analyser for GRIP skills, modes and agents.

Surfaces every skill, mode and agent as a browsable directory so each is
discoverable, regardless of whether it is referenced in CLAUDE.md or rules/*.md.

An entry is "referenced" if its slug, name, or any trigger phrase appears
verbatim (case-insensitive) in CLAUDE.md or any rules/*.md file. Entries with
zero matches are "dark" -- valid and invokable, just not mentioned in the
auto-loaded docs.

Three KINDS share one matcher and one renderer:

  skill   skills/*/SKILL.md            slug = directory name; candidates are the
                                       slug, the frontmatter name, the triggers.
  mode    modes/definitions/*.yaml     slug = file stem (``decision-mode``);
                                       candidates are the slug, ``/mode <name>``
                                       and only the SLASH-shaped triggers. The
                                       bare ``name`` and the word triggers are
                                       deliberately NOT candidates: a mode's name
                                       is a dictionary word by design (``code``,
                                       ``decision``) and its triggers are
                                       activation vocabulary (``review``,
                                       ``write``), so their presence in the corpus
                                       is not evidence that the MODE is referenced
                                       (see ``SkillInfo.candidates``).
  agent   asset_counter.AGENT_GLOB (agents/**/*.md, subfolders included) minus
          NON_AGENT_FILES (CLAUDE.md and _base-protocol.md); slug = file stem;
          candidates as for skills.

What counts as inventory is decided ONCE, in ``lib/asset_counter.py``: a match
must exist, must resolve inside the install root (the eleven tracked symlinks
under ``skills/`` point at another project), and inside a git checkout must be
tracked. This module applies the same filter so its per-kind totals equal
``asset_counter.count_inventory()`` for ``skills``, ``modes`` and ``agents``;
``tests/test_skill_reachability_kinds.py`` pins that equality on the real tree.
The one known divergence is historical and kept: a skill directory whose name
starts with ``_`` is skipped here (an existing contract) while the counter
would count ``skills/_x/SKILL.md``; no such directory exists on main.

Usage:
    python3 skill_reachability.py              # full catalog, all kinds
    python3 skill_reachability.py --dark-only  # unreferenced entries only
    python3 skill_reachability.py --kind mode  # one kind: skill | mode | agent
    python3 skill_reachability.py --search "voice"  # filter by keyword
    python3 skill_reachability.py --json       # machine-readable
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from asset_counter import AGENT_GLOB, NON_AGENT_FILES, _is_inside, _tracked_paths  # noqa: E402
from claude_dir import get_claude_dir  # noqa: E402

KINDS: tuple[str, ...] = ("skill", "mode", "agent")

# The asset_counter category key each kind's total must equal.
_COUNTER_KEY: dict[str, str] = {"skill": "skills", "mode": "modes", "agent": "agents"}


# ── YAML frontmatter parser (stdlib only, no PyYAML dep) ─────────────────────

_FM_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_LIST_ITEM = re.compile(r"^\s*-\s+(.+)$")
_TOP_KEY = re.compile(r"^(\w[\w-]*):\s*(.*)")


def _split_flow_list(val: str) -> list[str]:
    """Split a YAML flow sequence ``[a, "b, c", d]`` into its items.

    Commas inside quotes do not split; surrounding quotes are stripped. Agent
    frontmatter writes ``triggers: ["/x", "y z"]`` in this style, where skills
    write block lists -- both shapes must yield a list.
    """
    inner = val.strip()[1:-1]
    items: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in inner:
        if quote:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
        elif ch in "\"'":
            quote = ch
        elif ch == ",":
            items.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return [i for i in items if i]


def _parse_keyed_block(block: str) -> dict:
    """Parse top-level ``key: value`` lines plus their block or flow lists.

    Indented sub-keys are ignored, so a mode YAML's nested ``skills:`` map or a
    quoted multi-line ``prompt_injection`` never leaks into the result.
    """
    fm: dict = {}
    current_list_key: str | None = None
    for line in block.splitlines():
        kv = _TOP_KEY.match(line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                fm[key] = _split_flow_list(val)
                current_list_key = None
            else:
                fm[key] = val if val else []
                current_list_key = key if not val else None
        elif current_list_key and _LIST_ITEM.match(line):
            if not isinstance(fm[current_list_key], list):
                fm[current_list_key] = []
            fm[current_list_key].append(_LIST_ITEM.match(line).group(1).strip())
    return fm


def _parse_frontmatter(text: str) -> dict:
    m = _FM_PATTERN.match(text)
    if not m:
        return {}
    return _parse_keyed_block(m.group(1))


def _strip_quotes(val: str) -> str:
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        return val[1:-1]
    return val


# ── Data types ────────────────────────────────────────────────────────────────


@dataclass
class SkillInfo:
    """One catalog entry. The class name predates modes and agents and is kept
    for the callers that import it; ``kind`` says which inventory it came from."""

    slug: str
    name: str
    description: str
    triggers: list[str]
    path: Path
    referenced: bool = False
    matched_by: list[str] = field(default_factory=list)
    kind: str = "skill"

    def candidates(self) -> set[str]:
        """The strings whose presence in the corpus counts as a reference.

        A mode's triggers are activation VOCABULARY, not names -- ``review``,
        ``write``, ``demo``, ``analyze`` -- and measured 2026-10-03 on main, 20
        of the 21 modes that matched did so through such a word alone, which
        says nothing about whether the MODE is mentioned anywhere. So for a mode
        only slash-shaped triggers (``/plan``, ``/mode decision``) count, beside
        the slug and ``/mode <name>``; for skills and agents every trigger is a
        phrase chosen to be distinctive and all of them count, as before.
        """
        if self.kind == "mode":
            slash_triggers = {t for t in self.triggers if t.startswith("/")}
            return {self.slug, f"/mode {self.name}"} | slash_triggers
        return {self.slug, self.name} | set(self.triggers)


@dataclass
class Catalog:
    """``total``/``referenced``/``dark``/``all_skills`` describe SKILLS only, as
    they always have; ``all_entries`` carries every kind."""

    total: int
    referenced: int
    dark: int
    all_skills: list[SkillInfo]
    all_entries: list[SkillInfo] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.all_entries:
            self.all_entries = list(self.all_skills)

    @property
    def dark_pct(self) -> float:
        return round(100.0 * self.dark / self.total, 1) if self.total else 0.0

    @property
    def dark_skills(self) -> list[SkillInfo]:
        return [s for s in self.all_skills if not s.referenced]

    @property
    def referenced_skills(self) -> list[SkillInfo]:
        return [s for s in self.all_skills if s.referenced]

    def of_kind(self, kind: str) -> list[SkillInfo]:
        return [e for e in self.all_entries if e.kind == kind]

    def kind_summary(self, kind: str) -> dict[str, int]:
        entries = self.of_kind(kind)
        referenced = sum(1 for e in entries if e.referenced)
        return {"total": len(entries), "referenced": referenced, "dark": len(entries) - referenced}

    @property
    def counts(self) -> dict[str, int]:
        """Per-kind totals, keyed like ``asset_counter.count_inventory()``."""
        return {_COUNTER_KEY[k]: len(self.of_kind(k)) for k in KINDS}


# ── Core helpers ──────────────────────────────────────────────────────────────


def _build_corpus(claude_dir: Path) -> str:
    parts: list[str] = []
    for p in (claude_dir / "CLAUDE.md", claude_dir / ".claude" / "CLAUDE.md"):
        if p.is_file():
            parts.append(p.read_text(encoding="utf-8", errors="replace"))
    for p in sorted((claude_dir / "rules").glob("*.md")):
        parts.append(p.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts).lower()


def _scan_root_of(path: Path) -> Path:
    """The directory an entry was found under, resolved WITHOUT following the
    entry's own symlink: ``skills/<slug>/SKILL.md`` -> ``skills``;
    ``modes/definitions/x.yaml`` -> ``modes/definitions``; ``agents/x.md`` ->
    ``agents``."""
    parent = path.parent
    if path.name == "SKILL.md":
        parent = parent.parent
    return parent.resolve()


def _inventory_filter(claude_dir: Path) -> Callable[[Path], bool]:
    """The predicate ``asset_counter`` applies, as a function of one path.

    Exists; resolves inside the install root; inside a checkout, is tracked.
    Membership is tested on RESOLVED paths so a caller may spell the scan
    directory any way it likes. Outside a checkout the tracked filter is inert
    and only existence and containment remain, which is the counter's own
    behaviour on a tree that is not a checkout.

    A scan directory handed in from OUTSIDE the install root (a fixture
    ``skills/`` beside a fixture ``claude_dir``) is still inventory; there, only
    a symlink that leaves its own scan directory is dropped.
    """
    root_resolved = claude_dir.resolve()
    tracked = _tracked_paths(claude_dir)
    tracked_resolved = None if tracked is None else {p.resolve() for p in tracked}

    def accept(path: Path) -> bool:
        if not path.exists():
            return False
        if not _is_inside(path, root_resolved):
            return _is_inside(path, _scan_root_of(path))
        return tracked_resolved is None or path.resolve() in tracked_resolved

    return accept


def _skill_entries(skills_dir: Path) -> list[tuple[str, Path]]:
    """(slug, SKILL.md path) for every top-level skill directory.

    Directories starting with ``_`` are skipped -- an existing contract."""
    if not skills_dir.is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for skill_path in sorted(skills_dir.iterdir()):
        if not skill_path.is_dir() or skill_path.name.startswith("_"):
            continue
        skill_md = skill_path / "SKILL.md"
        if skill_md.is_file():
            out.append((skill_path.name, skill_md))
    return out


def _info_from_fields(kind: str, slug: str, path: Path, fm: dict) -> SkillInfo:
    name = fm.get("name", slug)
    desc = fm.get("description", "")
    triggers = fm.get("triggers", [])
    return SkillInfo(
        slug=slug,
        name=_strip_quotes(str(name)) if not isinstance(name, list) else slug,
        description=_strip_quotes(str(desc)) if not isinstance(desc, list) else "",
        triggers=[_strip_quotes(str(t)) for t in triggers] if isinstance(triggers, list) else [],
        path=path,
        kind=kind,
    )


def _extract_skills(
    skills_dir: Path, accept: Callable[[Path], bool] | None = None
) -> list[SkillInfo]:
    skills: list[SkillInfo] = []
    for slug, skill_md in _skill_entries(skills_dir):
        if accept is not None and not accept(skill_md):
            continue
        fm = _parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
        skills.append(_info_from_fields("skill", slug, skill_md, fm))
    return skills


def _extract_modes(
    modes_dir: Path, accept: Callable[[Path], bool] | None = None
) -> list[SkillInfo]:
    """One entry per ``modes/definitions/*.yaml``. The YAML has no ``---``
    fence; its top-level keys are read directly."""
    if not modes_dir.is_dir():
        return []
    modes: list[SkillInfo] = []
    for yaml_path in sorted(modes_dir.glob("*.yaml")):
        if accept is not None and not accept(yaml_path):
            continue
        fields = _parse_keyed_block(yaml_path.read_text(encoding="utf-8", errors="replace"))
        modes.append(_info_from_fields("mode", yaml_path.stem, yaml_path, fields))
    return modes


def _extract_agents(
    agents_dir: Path, accept: Callable[[Path], bool] | None = None
) -> list[SkillInfo]:
    """One entry per file ``asset_counter.AGENT_GLOB`` matches, subfolders
    included, except ``asset_counter.NON_AGENT_FILES``: the directory's own
    ``CLAUDE.md`` index and the shared ``_base-protocol.md``, mirroring
    ``asset_counter``'s rule."""
    if not agents_dir.is_dir():
        return []
    agents: list[SkillInfo] = []
    for md_path in sorted(agents_dir.glob(AGENT_GLOB)):
        if md_path.name in NON_AGENT_FILES:
            continue
        if accept is not None and not accept(md_path):
            continue
        fm = _parse_frontmatter(md_path.read_text(encoding="utf-8", errors="replace"))
        agents.append(_info_from_fields("agent", md_path.stem, md_path, fm))
    return agents


def _match_skill(skill: SkillInfo, corpus: str) -> list[str]:
    """Return list of matching candidates for an entry against corpus (hash lookup, O(k))."""
    return [c for c in skill.candidates() if c and c.lower() in corpus]


# ── Public API ────────────────────────────────────────────────────────────────


def build_catalog(
    skills_dir: Path | None = None,
    claude_dir: Path | None = None,
    *,
    modes_dir: Path | None = None,
    agents_dir: Path | None = None,
    kinds: tuple[str, ...] = KINDS,
) -> Catalog:
    """Build the full catalog with reachability annotations.

    ``kinds`` selects which inventories to read; the skill-only fields of the
    returned ``Catalog`` are populated whether or not ``"skill"`` is selected,
    so an existing caller sees the same shape it always did.
    """
    claude_dir = claude_dir or get_claude_dir()
    skills_dir = skills_dir or (claude_dir / "skills")
    modes_dir = modes_dir or (claude_dir / "modes" / "definitions")
    agents_dir = agents_dir or (claude_dir / "agents")

    corpus = _build_corpus(claude_dir)
    accept = _inventory_filter(claude_dir)

    entries: list[SkillInfo] = []
    if "skill" in kinds:
        entries.extend(_extract_skills(skills_dir, accept))
    if "mode" in kinds:
        entries.extend(_extract_modes(modes_dir, accept))
    if "agent" in kinds:
        entries.extend(_extract_agents(agents_dir, accept))

    for entry in entries:
        matches = _match_skill(entry, corpus)
        if matches:
            entry.referenced = True
            entry.matched_by = matches

    skills = [e for e in entries if e.kind == "skill"]
    referenced_count = sum(1 for s in skills if s.referenced)
    return Catalog(
        total=len(skills),
        referenced=referenced_count,
        dark=len(skills) - referenced_count,
        all_skills=skills,
        all_entries=entries,
    )


# Keep old name as alias for backward compatibility
def analyse(
    skills_dir: Path | None = None,
    claude_dir: Path | None = None,
) -> Catalog:
    return build_catalog(skills_dir=skills_dir, claude_dir=claude_dir)


# ── Rendering ─────────────────────────────────────────────────────────────────

_KIND_LABEL = {"skill": "skills", "mode": "modes", "agent": "agents"}
_KIND_PREFIX = {"skill": "/", "mode": "/mode ", "agent": "agent:"}


def _summary_clause(summary: dict[str, int]) -> str:
    return f"({summary['referenced']} referenced in docs, {summary['dark']} surfaced here for the first time)"


def _header_lines(catalog: Catalog, kind: str | None = None) -> list[str]:
    """The catalog header. With a ``kind`` filter the header is that kind's
    line alone, so ``--kind mode`` never opens with a skills total (AGORA on
    GRIP#7145 caught the unfiltered header surviving the filter)."""
    if kind is not None:
        s = catalog.kind_summary(kind)
        return [f"GRIP {_KIND_LABEL[kind].capitalize()} Catalog — {s['total']} {_KIND_LABEL[kind]} {_summary_clause(s)}"]
    lines = [
        f"GRIP Skill Catalog — {catalog.total} skills "
        f"({catalog.referenced} referenced in docs, {catalog.dark} surfaced here for the first time)"
    ]
    for other in ("mode", "agent"):
        summary = catalog.kind_summary(other)
        if summary["total"]:
            lines.append(f"  + {summary['total']} {_KIND_LABEL[other]} {_summary_clause(summary)}")
    return lines


def _matches_search(entry: SkillInfo, q: str) -> bool:
    return (
        q in entry.slug
        or q in entry.name.lower()
        or q in entry.description.lower()
        or any(q in t.lower() for t in entry.triggers)
    )


def _entry_lines(e: SkillInfo) -> list[str]:
    tag = "" if e.referenced else "  [surfaced]"
    desc_short = (e.description[:72] + "...") if len(e.description) > 75 else e.description
    label = _KIND_PREFIX[e.kind] + (e.name if e.kind == "mode" else e.slug)
    lines = [f"  {label:<40}{tag}"]
    if desc_short:
        lines.append(f"    {desc_short}")
    if e.triggers:
        lines.append(f"    Invoke when: {e.triggers[0]}")
    lines.append("")
    return lines


def _select_entries(
    catalog: Catalog, *, dark_only: bool, search: str, kind: str | None
) -> list[SkillInfo]:
    """Filter, then order by kind (stable, so each kind keeps its own order)."""
    entries = catalog.all_entries
    if kind is not None:
        entries = [e for e in entries if e.kind == kind]
    if dark_only:
        entries = [e for e in entries if not e.referenced]
    if search:
        q = search.lower()
        entries = [e for e in entries if _matches_search(e, q)]
    return sorted(entries, key=lambda e: KINDS.index(e.kind))


def _render_catalog(
    catalog: Catalog,
    *,
    dark_only: bool = False,
    search: str = "",
    kind: str | None = None,
) -> str:
    lines: list[str] = _header_lines(catalog, kind)
    lines.append("")

    entries = _select_entries(catalog, dark_only=dark_only, search=search, kind=kind)
    if not entries:
        lines.append(f"  (no {_KIND_LABEL[kind] if kind else 'skills'} match)")
        return "\n".join(lines)

    # One pass: a kind header wherever the kind changes, only when >1 kind shows.
    multi_kind = len({e.kind for e in entries}) > 1
    last_kind: str | None = None
    for e in entries:
        if multi_kind and e.kind != last_kind:
            lines.append(f"── {_KIND_LABEL[e.kind]} ──")
            last_kind = e.kind
        lines.extend(_entry_lines(e))

    if dark_only:
        lines.append(
            f"--- {len(entries)} entries shown above are valid and invokable "
            "but not referenced in CLAUDE.md or rules/*.md ---"
        )

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────


def _entry_dict(e: SkillInfo) -> dict:
    return {
        "slug": e.slug,
        "name": e.name,
        "kind": e.kind,
        "description": e.description,
        "triggers": e.triggers,
        "referenced": e.referenced,
        "matched_by": e.matched_by,
    }


def catalog_to_dict(catalog: Catalog) -> dict:
    """The ``--json`` payload. ``total``/``referenced``/``dark``/``skills`` keep
    their skill-only meaning; ``counts`` and ``by_kind`` carry every kind."""
    return {
        "total": catalog.total,
        "referenced": catalog.referenced,
        "dark": catalog.dark,
        "dark_pct": catalog.dark_pct,
        "counts": catalog.counts,
        "by_kind": {k: catalog.kind_summary(k) for k in KINDS},
        "skills": [_entry_dict(s) for s in catalog.all_skills],
        "entries": [_entry_dict(e) for e in catalog.all_entries],
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Browse all GRIP skills, modes and agents — surface dark entries and search the catalog"
    )
    parser.add_argument("--skills-dir", type=Path, default=None)
    parser.add_argument("--modes-dir", type=Path, default=None)
    parser.add_argument("--agents-dir", type=Path, default=None)
    parser.add_argument("--claude-dir", type=Path, default=None)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--dark-only", action="store_true", help="show only unreferenced entries")
    parser.add_argument("--kind", choices=KINDS, default=None, help="show one kind only")
    parser.add_argument("--search", default="", help="filter entries by keyword")
    args = parser.parse_args(argv)

    catalog = build_catalog(
        skills_dir=args.skills_dir,
        claude_dir=args.claude_dir,
        modes_dir=args.modes_dir,
        agents_dir=args.agents_dir,
    )

    if args.as_json:
        print(json.dumps(catalog_to_dict(catalog), indent=2))
    else:
        print(_render_catalog(catalog, dark_only=args.dark_only, search=args.search, kind=args.kind))


if __name__ == "__main__":
    main()
