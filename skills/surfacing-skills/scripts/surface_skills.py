#!/usr/bin/env python3
"""Build a slash-command friendly catalogue of GRIP and Codex skills."""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

HOME = Path.home()
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", HOME / ".claude"))
CODEX_HOME = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
AGENTS_HOME = Path(os.environ.get("AGENTS_HOME", HOME / ".agents"))

CATALOGUE_JSON = CLAUDE_DIR / "cache" / "skill-catalogue.json"
CATALOGUE_MD = CLAUDE_DIR / "cache" / "skill-catalogue.md"
COMMANDS_DIR = CLAUDE_DIR / "commands"
GENERATED_BY = "surfacing-skills"

# Sources that stay in the catalogue (`build`, `list`, /skills) but get no
# /command of their own. Every commands/*.md rides into every session's context
# (Claude Code's /context: 1,306 skills = 93.1K tokens), and measured across
# 4,399 session transcripts on 2026-09-22 not one of the 792 codex-plugin proxies
# was ever invoked from Claude Code, whereas the agent-skill proxies were.
PROXY_SKIPPED_SOURCES = frozenset({"codex-plugin"})

# The sed load line windows to the first N lines of the skill body; the
# description truncates to N chars. Kept as two named values because they must
# never drift together silently — the sed window is about the skill body, the
# char limit is about the frontmatter description (GRIP#5621).
SED_WINDOW_LINES = 220
DESC_CHAR_LIMIT = 220


@dataclass(frozen=True)
class SkillEntry:
    source: str
    name: str
    description: str
    skill_md: str
    root: str
    command: str


@dataclass(frozen=True)
class RootSpec:
    source: str
    root: Path
    recursive: bool = True


def _normalise_command_part(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", out) or "skill"


def _unquote(value: str) -> str:
    """Decode a quoted YAML scalar as a YAML loader would.

    lib/skill_lint.py's fixer (quote_scalar) writes an unsafe value single-quoted,
    or double-quoted with \\" and \\\\ escapes when it holds a single quote; those
    are the forms decoded here. Anything else keeps the old lenient strip.
    """
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r'\\(["\\])', r"\1", value[1:-1])
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value.strip("\"'")


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    data: dict[str, str] = {}
    for raw in text[4:end].splitlines():
        if ":" not in raw or raw.startswith(" "):
            continue
        key, _, value = raw.partition(":")
        value = _unquote(value.strip())
        if value:
            data[key.strip()] = value
    return data


def _description_from_body(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    for line in lines:
        if not line or line.startswith("#") or line == "---":
            continue
        if line.startswith("[TODO:"):
            continue
        return line[:300]
    return ""


def _read_skill(skill_md: Path) -> tuple[str, str]:
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    fm = _frontmatter(text)
    name = fm.get("name") or skill_md.parent.name
    desc = fm.get("description") or _description_from_body(text)
    return name.strip(), desc.strip()


def _default_roots() -> list[RootSpec]:
    roots = [
        RootSpec("grip", CLAUDE_DIR / "skills", recursive=False),
        RootSpec("codex", CODEX_HOME / "skills", recursive=True),
        RootSpec("agent-skill", AGENTS_HOME / "skills", recursive=True),
    ]
    plugin_cache = CODEX_HOME / "plugins" / "cache"
    if plugin_cache.exists():
        roots.extend(
            RootSpec("codex-plugin", path, recursive=True)
            for path in sorted(plugin_cache.glob("*/*/*/skills"))
            if path.is_dir()
        )
    return roots


def _iter_skill_files(root: RootSpec) -> Iterable[Path]:
    if not root.root.is_dir():
        return []
    if root.recursive:
        return sorted(root.root.glob("**/SKILL.md"))
    return sorted(path / "SKILL.md" for path in root.root.iterdir() if path.is_dir())


def _entries_for_root(root: RootSpec, seen: set[tuple[str, str, str]]) -> list[SkillEntry]:
    entries: list[SkillEntry] = []
    for skill_md in _iter_skill_files(root):
        # as_posix(): on Windows str() of this path has backslashes, so the
        # forward-slash text below never matched and archived skills were listed.
        if not skill_md.is_file() or "/_archived/" in skill_md.as_posix():
            continue
        name, desc = _read_skill(skill_md)
        key = (root.source, name, str(skill_md))
        if key in seen:
            continue
        seen.add(key)
        # Strip org prefixes: `source-command-queue-list` → `queue-list`
        slug = _normalise_command_part(name).removeprefix("source-command-")
        entries.append(SkillEntry(
            source=root.source,
            name=name,
            description=desc,
            skill_md=str(skill_md),
            root=str(root.root),
            command=slug,
        ))
    return entries


def collect_entries(roots: Iterable[RootSpec] | None = None) -> list[SkillEntry]:
    entries: list[SkillEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for root in roots or _default_roots():
        entries.extend(_entries_for_root(root, seen))
    return sorted(entries, key=lambda item: (item.source, item.name, item.skill_md))


def write_catalogue(entries: list[SkillEntry]) -> None:
    CATALOGUE_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {"total": len(entries), "entries": [asdict(entry) for entry in entries]}
    CATALOGUE_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    CATALOGUE_MD.write_text(render_markdown(entries), encoding="utf-8")


def render_markdown(entries: list[SkillEntry]) -> str:
    lines = [
        "# Skill Catalogue",
        "",
        f"Total skills: {len(entries)}",
        "",
        "| Source | Skill | Slash command | Load command |",
        "|---|---|---|---|",
    ]
    for entry in entries:
        load = f"sed -n '1,{SED_WINDOW_LINES}p' {_portable_skill_path(entry)}"
        lines.append(
            f"| {entry.source} | `{entry.name}` | `/{entry.command}` | `{load}` |"
        )
    return "\n".join(lines) + "\n"


def _yaml_string(value: str) -> str:
    return json.dumps(value.replace("\n", " ")[:900])


_SOURCE_ENV_PREFIX = {
    "grip": "${CLAUDE_DIR:-$HOME/.claude}/skills",
    "codex": "${CODEX_HOME:-$HOME/.codex}/skills",
    "agent-skill": "${AGENTS_HOME:-$HOME/.agents}/skills",
}


def _portable_skill_path(entry: SkillEntry) -> str:
    """Rewrite the absolute skill_md against its source's home env var, so a
    generated proxy loads on any machine rather than only the one that built it
    (GRIP#5515 defect 1: ``~/`` hardcoded into all 1133 proxies)."""
    prefix = _SOURCE_ENV_PREFIX.get(entry.source)
    if prefix:
        try:
            rel = Path(entry.skill_md).relative_to(Path(entry.root))
        except ValueError:
            return entry.skill_md
        # Forward slashes on every system: the result is written into tracked
        # command files that every machine loads, and built on Windows it held
        # backslashes.
        return f"{prefix}/{rel.as_posix()}"
    # codex-plugin has no entry in the table because its roots vary per plugin
    # and version (CODEX_HOME/plugins/cache/<plugin>/<version>/skills), so no
    # single STATIC prefix fits. They are still all under CODEX_HOME, so anchor
    # on that instead of on entry.root. Without this, every codex-plugin proxy
    # keeps the building machine's absolute home path and loads nowhere else --
    # 793 of 1251 generated commands, GRIP#5515 defect 1.
    try:
        rel = Path(entry.skill_md).relative_to(CODEX_HOME)
    except ValueError:
        return entry.skill_md
    return f"${{CODEX_HOME:-$HOME/.codex}}/{rel.as_posix()}"


def _short_desc(entry: SkillEntry) -> str:
    desc = entry.description or f"Load {entry.source} skill {entry.name}."
    if len(desc) <= DESC_CHAR_LIMIT:
        return desc
    clipped = desc[:DESC_CHAR_LIMIT]
    # Cut at the last sentence boundary inside the window, else a word boundary —
    # never mid-word (GRIP#5515 defect 2: a description dangling at "Use when a ").
    for sep in (". ", "! ", "? "):
        idx = clipped.rfind(sep)
        if idx >= 120:
            return clipped[: idx + 1]
    idx = clipped.rfind(" ")
    if idx >= 120:
        return clipped[:idx] + "…"
    return clipped + "…"


def _proxy_command(entry: SkillEntry) -> str:
    load = f"sed -n '1,{SED_WINDOW_LINES}p' {_portable_skill_path(entry)}"
    return f"""---
description: {_yaml_string(f"Load {entry.source} skill {entry.name}: {_short_desc(entry)}")}
argument-hint: "Optional task context"
generated-by: "{GENERATED_BY}"
source: "{entry.source}"
source-path: {_yaml_string(_portable_skill_path(entry))}
---

# /{entry.command}

Load `{entry.name}` from `{entry.source}`.

```bash
{load}
```

Use the loaded skill protocol to handle the user's task. Do not bulk-load other
skills unless their frontmatter or the user request requires them.
"""


def _skills_command(entries: list[SkillEntry]) -> str:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.source] = counts.get(entry.source, 0) + 1
    summary = ", ".join(f"{source}: {count}" for source, count in sorted(counts.items()))
    return f"""---
description: "Show the generated catalogue of GRIP, Codex, plugin, and agent skills."
argument-hint: "Optional search term"
generated-by: "{GENERATED_BY}"
---

# /skills

Generated skill catalogue.

- Total: {len(entries)}
- Sources: {summary}
- JSON: `{CATALOGUE_JSON}`
- Markdown: `{CATALOGUE_MD}`

## Usage

```bash
python3 ~/.claude/skills/surfacing-skills/scripts/surface_skills.py list
python3 ~/.claude/skills/surfacing-skills/scripts/surface_skills.py list gemini
python3 ~/.claude/skills/surfacing-skills/scripts/surface_skills.py sync --proxy-commands
```

To load a skill, run the command shown in the catalogue, for example:

```bash
sed -n '1,220p' skills/<skill-name>/SKILL.md
```
"""


def _write_command(path: Path, content: str, *, overwrite_generated: bool = True) -> bool:
    if path.exists():
        old = path.read_text(encoding="utf-8", errors="replace")
        if not overwrite_generated or f'generated-by: "{GENERATED_BY}"' not in old:
            return False
    path.write_text(content, encoding="utf-8")
    return True


def sync_commands(entries: list[SkillEntry], *, proxy_commands: bool) -> tuple[int, int]:
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0
    if _write_command(COMMANDS_DIR / "skills.md", _skills_command(entries)):
        written += 1
    else:
        skipped += 1
    if not proxy_commands:
        return written, skipped
    for entry in entries:
        if entry.source in PROXY_SKIPPED_SOURCES:
            continue
        path = COMMANDS_DIR / f"{entry.command}.md"
        if _write_command(path, _proxy_command(entry)):
            written += 1
        else:
            skipped += 1
    return written, skipped


def print_list(entries: list[SkillEntry], query: str = "") -> None:
    needle = query.lower().strip()
    filtered = [
        entry for entry in entries
        if not needle or needle in entry.name.lower() or needle in entry.description.lower()
    ]
    for entry in filtered:
        print(f"/{entry.command}\t{entry.source}\t{entry.name}\t{entry.skill_md}")
    print(f"\n{len(filtered)} of {len(entries)} skills")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="write cache/skill-catalogue.{json,md}")
    list_parser = sub.add_parser("list", help="print the skill catalogue")
    list_parser.add_argument("query", nargs="?", default="")
    sync_parser = sub.add_parser("sync", help="write /skills and optional /skill-* commands")
    sync_parser.add_argument("--proxy-commands", action="store_true")
    sync_parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    entries = collect_entries()
    if args.command == "list":
        print_list(entries, args.query)
        return 0
    write_catalogue(entries)
    if args.command == "build":
        print(f"wrote {len(entries)} skills to {CATALOGUE_JSON}")
        return 0
    written, skipped = sync_commands(entries, proxy_commands=args.proxy_commands)
    result = {"skills": len(entries), "commands_written": written, "commands_skipped": skipped}
    print(json.dumps(result, indent=2) if args.json else result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
