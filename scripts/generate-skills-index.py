#!/usr/bin/env python3
"""
Generate skills/INDEX.md from skills/*/SKILL.md frontmatter.

Reads YAML frontmatter from all SKILL.md files, groups skills by category
(using docs/SKILLS.md as the category source of truth), and outputs a
navigable INDEX.md with prose descriptions and see_also links.

Usage:
    python3 scripts/generate-skills-index.py

Output:
    skills/INDEX.md

Run via /update-docs or CI.
"""

import re
import sys
from pathlib import Path

# Resolve the install dir via the canonical shim so $CLAUDE_DIR is honoured
# (rules/path-portability.md). A hardcoded Path.home()/".claude" made this
# script read AND write the shared tree even when run from a worktree --
# which is the shared-tree-is-not-canonical.md hazard, since launchd agents
# execute from that tree.
_SCRIPTS = Path(__file__).resolve().parent
_REPO = _SCRIPTS.parent
if str(_REPO / "lib") not in sys.path:
    sys.path.insert(0, str(_REPO / "lib"))
from claude_dir import get_claude_dir  # noqa: E402

CLAUDE_DIR = get_claude_dir()
SKILLS_DIR = CLAUDE_DIR / "skills"
SKILLS_MD = CLAUDE_DIR / "docs" / "SKILLS.md"
INDEX_OUT = SKILLS_DIR / "INDEX.md"

_LIB = CLAUDE_DIR / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from asset_counter import _is_inside, refresh_readme_markers  # noqa: E402


def _apply_yaml_line(line: str, data: dict, state: dict) -> None:
    """Mutate data/state for one YAML line (list item or key:value)."""
    if line.startswith("  - ") and state["key"] is not None:
        item = line.strip()[2:].strip()
        if state["list"] is not None:
            state["list"].append(item)
        else:
            state["list"] = [item]
            data[state["key"]] = state["list"]
        return
    if ":" in line and not line.startswith(" "):
        state["list"] = None
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        state["key"] = key
        data[key] = value if value else None


def parse_frontmatter(skill_md: Path) -> dict:
    """Extract YAML frontmatter from a SKILL.md file."""
    try:
        content = skill_md.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not content.startswith("---"):
        return {}
    end = content.find("---", 3)
    if end == -1:
        return {}
    data: dict = {}
    state: dict = {"key": None, "list": None}
    for line in content[3:end].strip().splitlines():
        if line.strip() and not line.strip().startswith("#"):
            _apply_yaml_line(line, data, state)
    return data


def parse_skills_grip_categories() -> dict:
    """Parse docs/SKILLS.md to build category -> [skill_id] mapping.

    Markdown format: `## category` headings, then list items `- ` + a
    (backticked) skill id, e.g. ``- `preplan` — intent injection``. Function name
    kept for caller stability; it now reads the renamed .md file.
    """
    categories: dict[str, list[str]] = {}
    current_cat = "uncategorized"

    if not SKILLS_MD.exists():
        return categories

    for raw in SKILLS_MD.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        cat_match = re.match(r"##\s+(\S+)", line)
        if cat_match:
            current_cat = cat_match.group(1).rstrip(":")
            categories.setdefault(current_cat, [])
            continue
        item = re.match(r"-\s+`?([A-Za-z0-9][\w.:-]*)`?", line)
        if item:
            skill_id = item.group(1).strip()
            categories.setdefault(current_cat, [])
            categories[current_cat].append(skill_id)

    return categories


def collect_skills() -> dict:
    """Scan skills/ directory and return {skill_id: frontmatter_dict}.

    An entry that resolves OUTSIDE the install root is skipped. Installed
    plugins symlink their skills into ``skills/`` (ten ``bailian-*`` links on
    the shared tree point at ``~/.bailian/skills/``), and listing them made the
    tracked ``skills/INDEX.md`` say 335 skills where ``lib/asset_counter.py``
    counts 326. The same ``_is_inside`` check keeps the two artefacts in
    agreement, so a regeneration on a machine with plugins no longer collides
    with main on every skill PR.
    """
    skills = {}
    root_resolved = CLAUDE_DIR.resolve()
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        if not _is_inside(skill_dir, root_resolved):
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        fm = parse_frontmatter(skill_md)
        skills[skill_dir.name] = fm
    return skills


def category_display_name(cat: str) -> str:
    """Convert category ID to display name."""
    return cat.replace("-", " ").title()


def _build_reverse_lookup(categories: dict) -> dict:
    """Return {skill_id: category} from the category map (O(n) single pass)."""
    return {s: cat for cat, skill_list in categories.items() for s in skill_list}


def _render_skill_entry(sid: str, fm: dict) -> list:
    """Return Markdown lines for one skill entry."""
    desc = fm.get("description", "")
    if isinstance(desc, str):
        desc = desc.replace(">", "").strip()
    priority = fm.get("priority", "")
    priority_tag = " **[PARAMOUNT]**" if priority == "PARAMOUNT" else ""

    see_also = fm.get("see_also", [])
    if isinstance(see_also, list) and see_also:
        links = ", ".join(f"`{s}`" for s in see_also)
        see_also_str = f" See also: {links}."
    else:
        see_also_str = ""

    entry = [f"### `{sid}`{priority_tag}"]
    if desc:
        entry += ["", desc]
    if see_also_str:
        entry += ["", see_also_str]
    entry.append("")
    return entry


def _render_category_block(cat: str, cat_skills: list, skills: dict) -> list:
    """Return Markdown lines for one category section."""
    out = [f"## {category_display_name(cat)}", ""]
    for sid in cat_skills:
        out += _render_skill_entry(sid, skills[sid])
    return out


def _render_uncategorized(uncategorized: list, skills: dict) -> list:
    """Return Markdown lines for the Uncategorized section."""
    if not uncategorized:
        return []
    out = ["## Uncategorized", ""]
    for sid in sorted(uncategorized):
        fm = skills[sid]
        desc = fm.get("description", "")
        if isinstance(desc, str):
            desc = desc.replace(">", "").strip()
        out.append(f"### `{sid}`")
        if desc:
            out += ["", desc]
        out.append("")
    return out


def generate_index(skills: dict, categories: dict) -> str:
    """Generate INDEX.md content."""
    lines = [
        "# Skills Index",
        "",
        "Auto-generated from `skills/*/SKILL.md` frontmatter. "
        "Run `python3 scripts/generate-skills-index.py` or `/update-docs` to regenerate.",
        "",
        f"Total skills: {len(skills)}",
        "",
        "---",
        "",
    ]

    skill_to_cat = _build_reverse_lookup(categories)
    cat_order = list(dict.fromkeys(categories.keys()))
    uncategorized = [sid for sid in skills if sid not in skill_to_cat]

    for cat in cat_order:
        cat_skills = [s for s in categories.get(cat, []) if s in skills]
        if cat_skills:
            lines += _render_category_block(cat, cat_skills, skills)

    lines += _render_uncategorized(uncategorized, skills)
    lines += ["---", "", "*Generated by `scripts/generate-skills-index.py`*", ""]

    return "\n".join(lines)


def main():
    print("Scanning skills directory...")
    skills = collect_skills()
    print(f"Found {len(skills)} skills.")

    print("Parsing category map from SKILLS.md...")
    categories = parse_skills_grip_categories()
    print(f"Found {len(categories)} categories.")

    print("Generating INDEX.md...")
    content = generate_index(skills, categories)

    INDEX_OUT.write_text(content, encoding="utf-8")
    print(f"Written to {INDEX_OUT}")

    # Keep the README's grip-stats:skills marker in sync with the counter.
    if refresh_readme_markers():
        print("Refreshed README.md inventory markers")

    # Quick validation
    skill_count = content.count("### `")
    print(f"Index contains {skill_count} skill entries.")


if __name__ == "__main__":
    main()
