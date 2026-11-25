"""Decide which GRIP skills are protected from archiving, and record why.

A skill is PROTECTED (kept live under ``skills/<name>/``) when any of these
holds:

  * it was invoked in the measurement window (Skill-tool call or slash
    command -- see ``lib/skill_usage_scan.py``);
  * its name appears ANYWHERE in the tracked repository (``git ls-files``,
    text files), outside three things that name nothing beyond themselves:
    its own ``skills/<name>/`` directory; its own auto-generated
    ``commands/<name>.md`` proxy, VERIFIED generated rather than merely
    same-named (``is_generated_command_proxy`` -- a hand-written command
    that happens to share its skill's filename, such as
    ``commands/fleet-inspection.md`` calling
    ``Skill(skill="fleet-inspection")``, is a real dependency and still
    protects the skill); and the generated listings that enumerate every
    skill (``_GENERATED_LISTING_FILES``), which would otherwise protect
    everything by construction;
  * it is marked ``PARAMOUNT``, or is one of the explicitly named
    auto-loaded/critical skills (``EXPLICITLY_CRITICAL_SKILLS``).

Everything else is a candidate for archiving.

Narrowed 2026-10-08 (GRIP#7779, Codex review): the original version of this
module scanned a hardcoded, incomplete list of directories (rules/, commands/,
hooks/, config/, tests/, modes/definitions/) and missed every OTHER consumer
-- ``lib/onboarding_interview.py`` surfacing ``competitive-battlecard``, a
fixture bank under ``tests/fixtures/`` naming four other skills by their
``"target"`` field, and anything in ``agents/``, ``bin/`` (beyond one
hand-picked file) or ``scripts/``. A real reference living in a directory
nobody thought to list is invisible to a category enumeration by
construction; scanning the repository's own tracked-file list instead means
there is no list to fall behind. This also retires the separate
skill-mentions-a-kept-skill transitive closure: under a flat "anywhere
outside its own directory" rule, one skill's body mentioning another IS
already a reference like any other -- no second pass, and no requirement
that the mentioning skill be itself kept (an archived skill's body can still
protect a sibling it names; the coordinator's own stated bias is that an
archived skill something still uses is worse than no saving).

The function that decides this, ``build_protection_report``, is pure given
its inputs (a Path to scan and a set of used names) so it is unit-testable
against a small fixture tree without depending on the real corpus or a live
usage scan -- ``git ls-files`` returns nothing for a plain (non-git) fixture
directory, so the file-listing helper falls back to a full directory walk in
that case (see ``_tracked_files``), and the real corpus, always a git
repository, never takes that path.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# The explicitly named auto-loaded/critical skills, plus the two that do not
# already carry "PARAMOUNT" in their one-line descriptions (hitl,
# creating-wizards -- both say PARAMOUNT in their full SKILL.md body, so the
# generic marker check below would also catch them; this set is kept anyway
# as an explicit, reviewable floor).
EXPLICITLY_CRITICAL_SKILLS = frozenset({
    "asking-users",
    "hitl",
    "creating-wizards",
    "ultrathink",
    "recall",
    "forging-skills",
    "context-refresh",
    "dependency-guardian",
})

# Files that enumerate every skill by construction (a catalogue, an index, a
# generated stats line) -- scanning them would protect everything, which is
# why the 326/326-kept measurement noted below (where this module's own
# history already proved it) existed before this exclusion did.
#
# The bar is coverage, not a "generated" label: measured directly against
# the real 326-skill corpus, skills/INDEX.md names 295 of them and
# docs/COMMANDS.md names 291 -- both functionally complete. Two OTHER files
# that also self-describe as generated did NOT clear this bar when
# measured the same way -- docs/skill-graph-index.json's own ``_meta`` block
# says ``"generated_from": "skills/*/SKILL.md frontmatter"``, yet it names
# only 116 of 326; COWORK_SKILLS.md calls itself an "Auto-generated
# registry", yet it names only 84 of 326. Both are curated SUBSETS, the
# same shape as docs/SKILLS.md (77 of 326, and explicitly NOT canonical per
# its own header) -- excluding a subset would silently drop real evidence
# for whichever skills it happens to omit, so neither is excluded here. A
# label claiming "generated" is a claim, not the measurement; the
# measurement is what earned these four a place in this set.
_GENERATED_LISTING_FILES = frozenset({
    "skills/INDEX.md",
    "docs/COMMANDS.md",
    "rules/system-capabilities.md",
    "skills/_archived/README.md",
})

# Extensions that are never meaningfully "text" for a skill-name mention --
# skipped so the scan does not spend time UTF-8-decoding images/binaries
# that cannot contain a matching word-boundaried token anyway.
_BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff", ".woff2",
    ".ttf", ".otf", ".eot", ".zip", ".gz", ".tar", ".db", ".sqlite",
    ".pyc", ".so", ".dylib", ".dll", ".exe", ".wav", ".mp3", ".mp4",
})


def _tracked_files(repo_root: Path) -> list[str]:
    """Every file ``git ls-files`` reports, relative to ``repo_root``.

    This is the canonical membership test for "is this part of the
    repository" -- an untracked file (gitignored build output, a local
    scratch file) never appears, with no separate ignore-pattern logic
    needed here.

    Falls back to a full directory walk when ``git ls-files`` returns
    nothing -- not a git repository at all, or a real one with nothing
    staged yet (a plain fixture tree in a test). The real corpus is always
    a git repository with thousands of tracked files, so it never takes
    this path; the fallback exists purely so this module stays usable
    against a small synthetic tree without requiring every test to
    maintain a real git index.
    """
    result = subprocess.run(
        ["git", "ls-files"], cwd=repo_root, capture_output=True, text=True, check=False
    )
    tracked = (
        [line for line in result.stdout.splitlines() if line]
        if result.returncode == 0
        else []
    )
    if tracked:
        return tracked
    return sorted(
        str(p.relative_to(repo_root))
        for p in repo_root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(repo_root).parts
    )


def _category_for(rel_path: str) -> str:
    """A human-readable provenance label for a reference -- the file's
    top-level tracked directory, or ``CLAUDE.md`` for either of the two
    real ``CLAUDE.md`` files (root, and ``.claude/``)."""
    if rel_path in {"CLAUDE.md", ".claude/CLAUDE.md"}:
        return "CLAUDE.md"
    return rel_path.split("/", 1)[0]


def _is_scannable_text_file(rel_path: str) -> bool:
    """False for a generated listing (would protect every skill by
    construction) or a binary extension (cannot carry a matching word)."""
    if rel_path in _GENERATED_LISTING_FILES:
        return False
    return Path(rel_path).suffix.lower() not in _BINARY_EXTENSIONS


def list_live_skills(claude_dir: Path) -> dict[str, Path]:
    """Top-level ``skills/<name>/SKILL.md`` paths, excluding archive dirs.

    A symlinked entry, or one that resolves outside ``claude_dir``, is not a
    GRIP skill: GRIP tracks links into ``~/.agents/skills`` (agents-sdk, the
    cloudflare skills), and moving such a link under ``_archived`` would break
    its relative target (Codex review, GRIP#7779). ``lib/asset_counter.py``
    counts skills the same way.
    """
    skills_dir = claude_dir / "skills"
    live: dict[str, Path] = {}
    if not skills_dir.is_dir():
        return live
    root = claude_dir.resolve()
    for entry in sorted(skills_dir.iterdir()):
        if entry.is_symlink() or not entry.is_dir() or entry.name in {"_archived", ".archived", ".archive"}:
            continue
        if root not in entry.resolve().parents:
            continue
        skill_md = entry / "SKILL.md"
        if skill_md.is_file():
            live[entry.name] = skill_md
    return live


def _name_alternation(names: frozenset[str]) -> re.Pattern:
    """One compiled regex matching ANY of ``names`` as a whole token.

    Longest-first alternation so a longer name is never shadowed by a
    shorter one that happens to be its prefix (regex alternation matches the
    first alternative that fits at a given position).
    """
    ordered = sorted(names, key=len, reverse=True)
    body = "|".join(re.escape(n) for n in ordered)
    return re.compile(r"(?<![\w-])(" + body + r")(?![\w-])")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


@dataclass(frozen=True)
class ReferenceHit:
    category: str
    file: Path
    line_no: int


def _hits_in_text(text: str, pattern: re.Pattern, category: str, file: Path) -> list[tuple[str, ReferenceHit]]:
    """O(1): one regex pass over ``text`` (bounded by one file's length)."""
    found = []
    for match in pattern.finditer(text):
        line_no = text.count("\n", 0, match.start()) + 1
        found.append((match.group(1), ReferenceHit(category, file, line_no)))
    return found


def _is_self_reference(name: str, rel_path: str, hit: ReferenceHit) -> bool:
    """True when ``hit`` is evidence of nothing but ``name`` naming itself.

    Two shapes, both satisfied by construction for every skill and so
    never real evidence a consumer needs it:

      * the hit lives inside ``name``'s OWN ``skills/<name>/`` directory
        (its SKILL.md stating its own frontmatter ``name:``, its own
        scripts mentioning it in a comment, and so on);
      * the hit is ``name``'s own auto-generated ``commands/<name>.md``
        proxy, VERIFIED generated rather than merely same-named
        (``is_generated_command_proxy`` -- the filename-stem comparison
        here is a cheap pre-filter that skips reading file content for
        every command that obviously isn't this skill's own).

    Narrowed 2026-10-08 (GRIP#7779 follow-up): the proxy check used to
    exclude ANY same-named command by filename ALONE, which wrongly
    excluded ``commands/fleet-inspection.md`` -- a HAND-WRITTEN command
    whose body genuinely calls ``Skill(skill="fleet-inspection")``, a real
    dependency, not an artefact of the proxy generator. A command that
    mentions a DIFFERENT skill, or any hand-written command -- same-named
    or not -- still counts as a real reference. Without the own-directory
    exclusion, scanning the whole repository would make every skill
    "named by" its own SKILL.md frontmatter by construction (measured:
    326/326 kept before it existed).
    """
    if rel_path == f"skills/{name}" or rel_path.startswith(f"skills/{name}/"):
        return True
    if hit.category == "commands" and hit.file.stem == name:
        return is_generated_command_proxy(hit.file, name)
    return False


_GENERATED_BY_MARKER = 'generated-by: "surfacing-skills"'
_SOURCE_PATH_RE = re.compile(r'^source-path:\s*(.+?)\s*$', re.MULTILINE)


def is_generated_command_proxy(command_path: Path, skill_name: str) -> bool:
    """True iff ``command_path`` is a ``surfacing-skills``-generated proxy
    for ``skill_name`` SPECIFICALLY -- never a hand-written command that
    merely happens to share the skill's filename.

    This is the authoritative check -- ``_is_self_reference`` now
    delegates to it rather than matching by filename alone, after a
    filename-only match wrongly excluded a hand-written command
    (``commands/fleet-inspection.md``) from protecting its own skill.
    Both the ``generated-by`` marker AND an exact ``source-path`` match
    are required, because the failure mode of a loose check here is
    deleting -- or failing to protect -- a real, hand-written command.
    Matches the literal shape
    ``skills/surfacing-skills/scripts/surface_skills.py::_proxy_command``
    writes: ``generated-by: "surfacing-skills"`` and a ``source-path``
    value that resolves, after the ``${CLAUDE_DIR:-$HOME/.claude}/``-style
    prefix and any quoting are stripped, to ``skills/<skill_name>/SKILL.md``.

    The discriminating instance: ``commands/fleet-inspection.md`` is named
    after the archived skill ``fleet-inspection`` but carries no
    ``generated-by`` line at all -- a hand-written command with its own
    frontmatter and prose, which this function correctly refuses to match.
    """
    text = _read_text(command_path)
    if _GENERATED_BY_MARKER not in text:
        return False
    match = _SOURCE_PATH_RE.search(text)
    if not match:
        return False
    source_path = match.group(1)
    if len(source_path) >= 2 and source_path[0] == source_path[-1] == '"':
        source_path = source_path[1:-1]
    return source_path.endswith(f"skills/{skill_name}/SKILL.md")


def _record_file_hits(
    index: dict[str, list[ReferenceHit]],
    hits: list[tuple[str, ReferenceHit]],
    rel_path: str,
) -> None:
    """Append one file's hits into ``index``, dropping self-references."""
    keep = ((name, hit) for name, hit in hits if not _is_self_reference(name, rel_path, hit))
    for name, hit in keep:
        index[name].append(hit)


def build_reference_index(
    claude_dir: Path, names: frozenset[str]
) -> dict[str, list[ReferenceHit]]:
    """name -> every tracked-repository location naming it.

    Scans ``git ls-files`` (falling back to a full walk -- see
    ``_tracked_files``), skipping generated listings and binary
    extensions. Reads each candidate file exactly once; the alternation
    regex is compiled once and tested against each file's whole text (not
    line by line), so this is linear in total corpus size regardless of
    ``len(names)``. ``_record_file_hits`` owns the per-file bucketing so
    this function's own loop has no loop nested inside it.
    """
    index: dict[str, list[ReferenceHit]] = {name: [] for name in names}
    if not names:
        return index
    pattern = _name_alternation(names)
    for rel_path in _tracked_files(claude_dir):
        if not _is_scannable_text_file(rel_path):
            continue
        abs_path = claude_dir / rel_path
        category = _category_for(rel_path)
        hits = _hits_in_text(_read_text(abs_path), pattern, category, abs_path)
        _record_file_hits(index, hits, rel_path)
    return index


def _is_marked_paramount(skill_md_text: str) -> bool:
    return "PARAMOUNT" in skill_md_text


_INVOCABLE_NAME_RE = re.compile(r"^invocable_name:\s*(\S+)", re.MULTILINE)


def _declared_alias(skill_md_text: str) -> str | None:
    """The ``invocable_name:`` frontmatter value, if a skill declares one.

    A handful of skills answer to a shorter or different public name than
    their own directory -- ``skills/saving-state`` declares
    ``invocable_name: save``, so a transcript recording a Skill-tool call
    with ``skill: "save"`` is usage of ``saving-state``, not of a
    non-existent ``save`` directory. Checking the directory name alone
    under-counts real usage for every skill that declares one (measured:
    12 of 326, half of them a no-op self-alias and the rest a genuine
    rename -- ``sync``, ``holistic``, ``save``, ``recall``, ``catch-me-up``,
    ``transfer``).
    """
    match = _INVOCABLE_NAME_RE.search(skill_md_text)
    return match.group(1).strip() if match else None


@dataclass
class ProtectionResult:
    name: str
    used: bool = False
    invoked_as: list[str] = field(default_factory=list)
    paramount_or_critical: bool = False
    references: list[ReferenceHit] = field(default_factory=list)

    @property
    def kept(self) -> bool:
        return self.used or self.paramount_or_critical or bool(self.references)

    def reasons(self) -> list[str]:
        out: list[str] = []
        if self.used:
            names = ", ".join(f"'{n}'" for n in self.invoked_as)
            out.append(f"invoked in the measurement window (as {names})")
        if self.paramount_or_critical:
            out.append("marked PARAMOUNT or an explicitly critical/auto-loaded skill")
        for hit in self.references:
            out.append(f"named by {hit.category} {hit.file}:{hit.line_no}")
        return out or ["no usage, reference, or criticality signal found"]


def _invoked_names(name: str, skill_md_text: dict[str, str]) -> frozenset[str]:
    """A skill's directory name plus its declared ``invocable_name``, if any."""
    alias = _declared_alias(skill_md_text.get(name, ""))
    return frozenset({name, alias}) if alias else frozenset({name})


def _mark_usage(
    results: dict[str, ProtectionResult],
    names: frozenset[str],
    skill_md_text: dict[str, str],
    used_names: frozenset[str],
) -> None:
    """O(1) per skill: test its directory name and alias against ``used_names``."""
    for name in names:
        matched = _invoked_names(name, skill_md_text) & used_names
        if matched:
            results[name].used = True
            results[name].invoked_as = sorted(matched)


def build_protection_report(
    claude_dir: Path, used_names: frozenset[str]
) -> dict[str, ProtectionResult]:
    """The full per-skill protection verdict for every live skill under ``claude_dir``.

    ``used_names`` is the set of Skill-tool/slash-command names seen in the
    measurement window (``lib/skill_usage_scan.py``'s report, unioned over
    tool and slash counts) -- the only external input besides the directory
    tree, which keeps this function easy to exercise against a small fixture
    in a test. A name is matched against each skill's directory name AND its
    declared ``invocable_name`` alias (see ``_declared_alias``), because the
    transcript records whatever literal name was actually invoked.
    """
    live = list_live_skills(claude_dir)
    names = frozenset(live.keys())
    results = {name: ProtectionResult(name=name) for name in names}
    skill_md_text = {name: _read_text(path) for name, path in live.items()}

    _mark_usage(results, names, skill_md_text, used_names)

    reference_index = build_reference_index(claude_dir, names)
    for name, hits in reference_index.items():
        results[name].references = hits

    for name, text in skill_md_text.items():
        if _is_marked_paramount(text) or name in EXPLICITLY_CRITICAL_SKILLS:
            results[name].paramount_or_critical = True

    return results
